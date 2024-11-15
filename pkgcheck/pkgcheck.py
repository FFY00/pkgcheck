#!/usr/bin/env python
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

from __future__ import absolute_import

import argparse
import fileinput
import os
import re
import shlex
import subprocess
import sys

from pkgcheck import messages

MESSAGES = messages.MESSAGES


def is_continuation(line):
    return re.search(r'\\\s*$', line)


def check_for_do(line, report):
    if not is_continuation(line):
        match = re.match(r'^\s*(for|while|until)\s', line)
        if match:
            operator = match.group(1).strip()
            if operator == "for":
                # "for i in ..." and "for ((" is bash, but
                # "for (" is likely from an embedded awk script,
                # so skip it
                if re.search(r'for \([^\(]', line):
                    return
            if not re.search(r';\s*do$', line):
                report.print_error((MESSAGES['E010'].msg % operator), line)


def check_if_then(line, report):
    if not is_continuation(line):
        if re.search(r'^\s*(el)?if \[', line):
            if not re.search(r';\s*then$', line):
                report.print_error(MESSAGES['E011'].msg, line)


def check_no_trailing_whitespace(line, report):
    if re.search('[ \t]+$', line):
        report.print_error(MESSAGES['E001'].msg, line)


def check_indents(logical_line, report, continuing, skip_indent):
    # this is rather complex to handle argument offset indenting;
    # primarily done by emacs.  If there is an argument, it will try
    # to line up the following arguments underneath it, e.g.
    #   foobar_cmd bar baz \
    #              moo boo
    # Thus the offset in this case might not be a strict multiple of 4

    # Find the offset of the first argument of the command (if it has
    # one)
    m = re.search(r'^(?P<indent>[ \t]+)?(?P<cmd>\S+)(?P<ws>\s+)(?P<arg>\S+)',
                  logical_line[0])
    arg_offset = None
    if m:
        arg_offset = len(m.group('indent')) if m.group('indent') else 0
        arg_offset += len(m.group('cmd')) + len(m.group('ws'))

    # go through each line
    for lineno, line in enumerate(logical_line):
        m = re.search('^(?P<indent>[ \t]+)', line)
        if m:
            indent = m.group('indent')
            offset = len(indent.replace('\t', ''))

            # the first line and lines without an argument should be
            # offset by 2 spaces
            if not skip_indent:
                if (lineno == 0) or (arg_offset is None):
                    if (offset % 2) != 0:
                        report.print_error(MESSAGES['E003'].msg, line)
                else:
                    # other lines are allowed to line up with the first
                    # argument, or be multiple-of 2 spaces
                    if offset != arg_offset and (offset % 2) != 0:
                        report.print_error(MESSAGES['E003'].msg, line)

            # no tabs, only spaces
            if re.search('\t', indent) and not continuing:
                if not indent.startswith('  ') or not line.endswith(' \\\n'):
                    report.print_error(MESSAGES['E002'].msg, line)


def check_function_decl(line, report):
    failed = False
    if line.startswith("function"):
        if not re.search(r'^function [\w-]* \{$', line):
            failed = True
    else:
        # catch the case without "function", e.g.
        # things like '^foo() {'
        if re.search(r'^\s*?\(\)\s*?\{', line):
            failed = True

    if failed:
        report.print_error(MESSAGES['E020'].msg, line)


def starts_heredoc(line):
    # note, watch out for <<EOF and <<'EOF' ; quotes in the
    # deliminator are part of syntax
    m = re.search(r"[^<]<<\s*([\'\"]?)(?P<token>\w+)([\'\"]?)", line)
    return m.group('token') if m else False


def end_of_heredoc(line, token):
    return token and re.search(r"^%s\s*$" % token, line)


def check_arithmetic(line, report):
    if "$[" in line:
        report.print_error(MESSAGES['E041'].msg, line)


def check_bare_arithmetic(line, report):
    if line.lstrip().startswith("(("):
        report.print_error(MESSAGES['W043'].msg, line)


def check_local_subshell(line, report):
    # XXX: should we increase the string checking to see if the $( is
    # anywhere with a string being set?  Risk of false positives?x
    if line.lstrip().startswith('local ') and \
       any(s in line for s in ('=$(', '=`', '="$(', '="`')):
        report.print_error(MESSAGES['W042'].msg, line)


def check_conditional_expression(line, report):
    # We're really starting to push the limits of what we can do without
    # a complete bash syntax parser here.  For example
    # > [[ $foo =~ " [ " ]] && [[ $bar =~ " ] " ]]
    # would be valid but mess up a simple regex matcher for "[.*]".
    # Let alone dealing with multiple-line-spanning etc...
    #
    # So we'll KISS and just look for simple, one line,
    # > if [ $foo =~ "bar" ]; then
    # type statements, which are the vast majority of typo errors.
    #
    # shlex is pretty helpful in getting us something we can walk to
    # find this pattern.  It does however have issues with
    # unterminated quotes on multi-line strings (e.g.)
    #
    # foo="bar   <-- we only see this bit in "line"
    #  baz"
    #
    # So we're just going to ignore parser failures here and move on.
    # Possibly in the future we could pull such multi-line strings
    # into "logical_line" below, and pass that here and have shlex
    # break that up.
    try:
        toks = shlex.shlex(line)
        toks.wordchars = "[]=~"
        toks = list(toks)
    except ValueError:
        return

    in_single_bracket = False
    for tok in toks:
        if tok == '[':
            in_single_bracket = True
        elif tok in ('=~', '<', '>') and in_single_bracket:
            report.print_error(MESSAGES['E044'].msg, line)
        elif tok == ']':
            in_single_bracket = False


def check_unquoted(line, report):
    quoted = False
    for m in re.compile(r'"|\$(|{).*(pkgdir|srcdir)|"').finditer(line):
        if m.group() == '"':
            quoted = not quoted
        elif not quoted:
            report.print_error(MESSAGES['E005'].msg, line)
            return


def check_unneeded_quotes(line, report):
    if re.search('(pkgname|pkgver|pkgrel|epoch)=(\'|")', line):
        report.print_error(MESSAGES['W062'].msg, line)

def fetch_base_devel_packages():
    try:
        result = subprocess.run(['pacman', '-Si', 'base-devel'], capture_output=True, text=True, check=True)
        
        dependencies = []
        for line in result.stdout.splitlines():
            if line.startswith("Depends On"):
                dependencies = line.split(":")[1].strip().split()
                break
        return dependencies
    except subprocess.CalledProcessError as e:
        print("Failed to fetch base-devel dependencies:", e)
        return []

def check_base_devel(line, report):
    base_devel_packages = fetch_base_devel_packages()
    base_devel_packages.append('base-devel')
    
    if re.search(r'^makedepends=', line):
        found_packages = [pkg for pkg in base_devel_packages if re.search(rf'\b{pkg}\b', line)]
        if found_packages:
            found_packages_str = ', '.join(found_packages)
            error_message = MESSAGES['W063'].msg.format(packages=found_packages_str)
            report.print_error(error_message, line)


def check_syntax(filename, report):
    # run the file through "bash -n" to catch basic syntax errors and
    # other warnings
    matches = []

    # sample lines we want to match:
    # foo.sh: line 4: warning: \
    #    here-document at line 1 delimited by end-of-file (wanted `EOF')
    # foo.sh: line 9: syntax error: unexpected end of file
    # foo.sh: line 7: syntax error near unexpected token `}'
    #
    # i.e. consistency with ":"'s isn't constant, so just do our
    # best...
    r = re.compile(
        '^(?P<file>.*): line (?P<lineno>[0-9]+): (?P<error>.*)')
    # we are parsing the error message, so force it to ignore the
    # system locale so we don't get messages in another language
    bash_environment = os.environ
    bash_environment['LC_ALL'] = 'C'
    proc = subprocess.Popen(
        ['bash', '-n', filename], stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, env=bash_environment,
        universal_newlines=True)
    outputs = proc.communicate()

    for line in outputs[1].split('\n'):
        m = r.match(line)
        if m:
            matches.append(m)

    for m in matches:
        if 'syntax error' in m.group('error'):
            msg = '%s: %s' % (MESSAGES['E040'].msg, m.group('error'))
            report.print_error(msg, filename=filename,
                               filelineno=int(m.group('lineno')))

        # Matching output from bash warning about here-documents not
        # ending.
        # FIXME: are there other warnings that might come out
        # with "bash -n"?  A quick scan of the source code suggests
        # no, but there might be other interesting things we could
        # catch.
        if 'warning:' in m.group('error'):
            if 'delimited by end-of-file' in m.group('error'):
                start = re.match('^.*line (?P<start>[0-9]+).*$',
                                 m.group('error'))
                report.print_error(
                    MESSAGES['E012'].msg % int(start.group('start')),
                    filename=filename,
                    filelineno=int(m.group('lineno')))


class pkgcheckRun(object):

    def __init__(self):
        self.error_count = 0
        self.error_list = None
        self.ignore_list = None
        self.warning_count = 0
        self.warning_list = None

    def register_ignores(self, ignores):
        if ignores:
            self.ignore_list = '^(' + '|'.join(ignores.split(',')) + ')'

    def register_warnings(self, warnings):
        if warnings:
            self.warning_list = '^(' + '|'.join(warnings.split(',')) + ')'

    def register_errors(self, errors):
        if errors:
            self.error_list = '^(' + '|'.join(errors.split(',')) + ')'

    def should_ignore(self, error):
        return self.ignore_list and re.search(self.ignore_list, error)

    def should_warn(self, error):
        # if in the errors list, overrides warning level
        if self.error_list and re.search(self.error_list, error):
            return False
        if messages.is_default_warning(error):
            return True
        return self.warning_list and re.search(self.warning_list, error)

    def print_error(self, error, line='',
                    filename=None, filelineno=None):
        if self.should_ignore(error):
            return

        warn = self.should_warn(error)

        if not filename:
            filename = fileinput.filename()
        if not filelineno:
            filelineno = fileinput.filelineno()
        if warn:
            self.warning_count = self.warning_count + 1
        else:
            self.error_count = self.error_count + 1

        self.log_error(error, line, filename, filelineno, warn)

    def log_error(self, error, line, filename, filelineno, warn=False):
        # following pycodestyle/pep8 default output format
        # https://github.com/PyCQA/pycodestyle/blob/master/pycodestyle.py#L108
        print("%(filename)s:%(filelineno)s:1: %(error)s" %
              {'filename': filename,
               'filelineno': filelineno,
               'warn': "W" if warn else "E",
               'error': error.replace(":", "", 1),
               'line': line.rstrip('\n')})

    def check_files(self, files, verbose):
        logical_line = ""
        token = False

        # NOTE(mrodden): magic; replace with proper
        # report class when necessary
        report = self
        skip_indent = continuing = False
        global nl_count
        nl_count = 0

        for fname in files:

            # reset world
            in_heredoc = False
            in_continuation = False

            # simple syntax checking, as files can pass style but still cause
            # syntax errors when you try to run them.
            check_syntax(fname, report)

            global prev_line
            prev_line = ''

            def finish(line):
                global nl_count
                global prev_line
                prev_line = line
                if line == '\n':
                    nl_count += 1
                else:
                    nl_count = 0

            for line in fileinput.input(fname):
                if fileinput.isfirstline() and verbose:
                        print("Running pkgcheck on %s" % fileinput.filename())

                # Don't run any tests on comment lines (but remember
                # inside a heredoc this might be part of the syntax of
                # an embedded script, just ignore that)
                if line.lstrip().startswith('#') and not in_heredoc:
                    finish(line)
                    continue

                # Strip trailing comments. From bash:
                #
                #   a word beginning with # causes that word and all
                #   remaining characters on that line to be ignored.
                #   ...
                #   A character that, when unquoted, separates
                #   words. One of the following: | & ; ( ) < > space
                #   tab
                #
                # for simplicity, we strip inline comments by
                # matching just '<space>#'.
                if not in_heredoc:
                    ll_split = line.split(' #', 1)
                    if len(ll_split) > 1:
                        line = ll_split[0].rstrip()

                # see if this starts a heredoc
                if not in_heredoc:
                    token = starts_heredoc(line)
                    if token:
                        in_heredoc = True
                        logical_line = [line]
                        finish(line)
                        continue

                # see if this starts a continuation
                if not in_continuation:
                    if is_continuation(line):
                        in_continuation = True
                        logical_line = [line]
                        finish(line)
                        continue

                # if we are in a heredoc or continuation, just loop
                # back and keep buffering the lines into
                # "logical_line" until the end of the
                # heredoc/continuation.
                if in_heredoc:
                    logical_line.append(line)
                    if not end_of_heredoc(line, token):
                        finish(line)
                        continue
                    else:
                        in_heredoc = False
                        # FIXME: if we want to do something with
                        # heredocs in the future, then the whole thing
                        # is now stored in logical_line.  for now,
                        # skip
                        continue
                elif in_continuation:
                    logical_line.append(line)
                    if is_continuation(line):
                        finish(line)
                        continue
                    else:
                        in_continuation = False
                else:
                    logical_line = [line]

                # skip identation when declaring variables
                if re.search(re.escape('=('), line) and not re.search(r'\)', line):
                    skip_indent = True
                elif re.search(r'\)', prev_line):
                    skip_indent = False

                # allow tabs when continuing a command
                continuing = prev_line.endswith(' \\\n')

                check_indents(logical_line, report, continuing, skip_indent)
                finish(line)

                # at this point, logical_line is an array that holds
                # the whole continuation.  XXX : historically, we've
                # just handled every line in a continuation
                # separatley.  Stick with what works...
                for line in logical_line:
                    check_no_trailing_whitespace(line, report)
                    check_for_do(line, report)
                    check_if_then(line, report)
                    check_unquoted(line, report)
                    check_function_decl(line, report)
                    check_arithmetic(line, report)
                    check_local_subshell(line, report)
                    check_bare_arithmetic(line, report)
                    check_unneeded_quotes(line, report)
                    check_conditional_expression(line, report)
                    check_base_devel(line, report)

        # finished processing the file

        # no multiple final newlines
        if nl_count > 1:
            report.print_error(MESSAGES['E061'].msg, line)

        # last line should always end with a newline
        if line != '\n':
            report.print_error(MESSAGES['E060'].msg, line)
        elif not line.endswith('\n'):
            report.print_error(MESSAGES['E004'].msg, line)


def main(args=None):

    if args is None:
        args = sys.argv[1:]

    parser = argparse.ArgumentParser(
        description='A PKGBUILD style checker')
    parser.add_argument('files', metavar='file', nargs='*',
                        help='files to scan for errors')
    parser.add_argument('-i', '--ignore', help='Rules to ignore')
    parser.add_argument('-w', '--warn',
                        help='Rules to always warn (rather than error)')
    parser.add_argument('-e', '--error',
                        help='Rules to always error (rather than warn)')
    parser.add_argument('-v', '--verbose', action='store_true', default=False)
    parser.add_argument('-s', '--show', action='store_true', default=False)
    opts = parser.parse_args(args)

    if opts.show:
        messages.print_messages()
        sys.exit(0)

    files = opts.files
    if not files:
        parser.print_usage()
        return 1

    run = pkgcheckRun()
    run.register_ignores(opts.ignore)
    run.register_warnings(opts.warn)
    run.register_errors(opts.error)

    try:
        run.check_files(files, opts.verbose)
    except IOError as e:
        print("pkgcheck: %s" % e)
        return 1

    if run.warning_count > 0:
        print("%d pkgcheck warning(s) found" % run.warning_count)

    if run.error_count > 0:
        print("%d pkgcheck error(s) found" % run.error_count)
        return 1

    return 0

if __name__ == "__main__":
    sys.exit(main())
