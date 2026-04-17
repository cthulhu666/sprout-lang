#!/usr/bin/env python3
"""
Dump typed names from a Sprout source file using the Python typechecker.

Output format (one line per user-declared name, in declaration order):

    name : scheme_string

Used for parity testing against stdlib/compiler/type_driver.sprout in
tests/test_checker_parity.py.

Usage:
    python tools/dump_types.py <file.spr>
"""
from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sprout import parse
import sprout.ast as ast
from sprout.typechecker import typecheck_program


def user_declared_names(prog: ast.Program):
    """Yield names in source-declaration order, mirroring dump_typed_names in checker.sprout."""
    for decl in prog.declarations:
        if isinstance(decl, ast.FnDecl):
            yield decl.name
        elif isinstance(decl, ast.LetDecl):
            yield decl.name
        elif isinstance(decl, ast.TypeDecl):
            for ctor in decl.constructors:
                yield ctor.name
        # ClassDecl, InstanceDecl, RecordDecl: skipped (matches Sprout side)


def main() -> None:
    if len(sys.argv) != 2:
        print("usage: dump_types.py <file.spr>", file=sys.stderr)
        sys.exit(1)

    path = sys.argv[1]
    try:
        src = open(path).read()
    except OSError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        prog = parse(src)
    except Exception as e:
        print(f"ERROR: parse: {e}")
        sys.exit(1)

    try:
        type_map = typecheck_program(prog)
    except Exception as e:
        print(f"ERROR: check: {e}")
        sys.exit(1)

    for name in user_declared_names(prog):
        val = type_map.get(name, "<not found>")
        print(f"{name} : {val}")


if __name__ == "__main__":
    main()
