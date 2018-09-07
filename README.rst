===============================
pkgcheck
===============================

This is a bashate fork for Arch Linux's PKGBUILDs.

- Free software: Apache license

Currently Supported Checks
--------------------------

Errors
~~~~~~

Basic white space errors, for consistent indenting

- E001: check that lines do not end with trailing whitespace
- E002: ensure that indents are only spaces, and not hard tabs
- E003: ensure all indents are a multiple of 4 spaces
- E004: file did not end with a newline
- E005: unquoted $srcdir or $pkgdir

Structure Errors
~~~~~~~~~~~~~~~~

A set of rules that help keep things consistent in control blocks.
These are ignored on long lines that have a continuation, because
unrolling that is kind of "interesting"

- E010: *do* not on the same line as *for*
- E011: *then* not on the same line as *if* or *elif*
- E012: heredoc didn't end before EOF
- E020: Function declaration not in format ``^function name {$``

Obsolete, deprecated or unsafe syntax
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Rules to identify obsolete, deprecated or unsafe syntax that should
not be used

- E040: Syntax errors reported by `bash -n`
- E041: Usage of $[ for arithmetic is deprecated for $((
- W042: Local declaration hides errors
- W043: Arithmetic compound has inconsistent return semantics
- E044: Use [[ for =~,<,> comparisions

Style enforcer
~~~~~~~~~~~~~~

- E060: Last line isn't a newline
- E061: Multiple final newlines
- W062: Unsafe quotes
