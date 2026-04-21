#!/usr/bin/env python3
"""
Dump typed names from a Sprout source file using the Python typechecker.

Output format (one line per user-declared name, in declaration order):

    name : scheme_string

Used for parity testing against stdlib/compiler/type_driver.sprout in
tests/test_checker_parity.py.

For files with import headers (import stdlib.X as alias), load_module_bundle
is used to resolve imports and build the full type env before typechecking.

Usage:
    python tools/dump_types.py <file.spr>
"""
from __future__ import annotations

import sys
import os
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sprout import parse
import sprout.ast as ast
from sprout.typechecker import typecheck_program, Scheme, TFunc, TApp, TConst, TVar, PURE_EFFECT
from sprout.module_loader import load_module_bundle, resolve_program_names

_a = TVar("a")
_b = TVar("b")
_e = TVar("e")
_str = TConst("String")
_int = TConst("Int")
_bool = TConst("Bool")


def _fn(*ts: object) -> object:
    """Build a curried function type from left to right."""
    result = ts[-1]
    for t in reversed(ts[:-1]):
        result = TFunc(t, result, PURE_EFFECT)  # type: ignore[arg-type]
    return result


def _list(t: object) -> object:
    return TApp(TConst("List"), t)  # type: ignore[arg-type]


def _maybe(t: object) -> object:
    return TApp(TConst("Maybe"), t)  # type: ignore[arg-type]


def _result(e: object, a: object) -> object:
    return TApp(TApp(TConst("Result"), e), a)  # type: ignore[arg-type]


def _poly(*vars_and_type: object) -> "Scheme":
    *vs, typ = vars_and_type
    return Scheme(vars=tuple(v.name for v in vs), type=typ)  # type: ignore[arg-type, union-attr]


# Prelude constructors and common functions mirroring checker.builtin_env() in
# stdlib/compiler/checker.sprout.  Injected via the seed_env parameter so that
# corpus files which call prelude helpers can be typechecked without a full
# module-load step.
PRELUDE_SEED_ENV: "dict[str, Scheme]" = {
    # ADT constructors
    "Just":    _poly(_a, _fn(_a, _maybe(_a))),
    "Nothing": _poly(_a, _maybe(_a)),
    "Cons":    _poly(_a, _fn(_a, _fn(_list(_a), _list(_a)))),
    "Nil":     _poly(_a, _list(_a)),
    "Ok":      _poly(_e, _a, _fn(_a, _result(_e, _a))),
    "Err":     _poly(_e, _a, _fn(_e, _result(_e, _a))),
    # List helpers
    "list_reverse": _poly(_a, _fn(_list(_a), _list(_a))),
    "list_append":  _poly(_a, _fn(_list(_a), _fn(_list(_a), _list(_a)))),
    "list_map":     _poly(_a, _b, _fn(_fn(_a, _b), _fn(_list(_a), _list(_b)))),
    "list_fold":    _poly(_a, _b, _fn(_fn(_b, _fn(_a, _b)), _fn(_b, _fn(_list(_a), _b)))),
    "filter":       _poly(_a, _fn(_fn(_a, _bool), _fn(_list(_a), _list(_a)))),
    # Simplified prelude aliases (class-constrained in source; list-specialised here)
    "map":          _poly(_a, _b, _fn(_fn(_a, _b), _fn(_list(_a), _list(_b)))),
    "fold":         _poly(_a, _b, _fn(_fn(_b, _fn(_a, _b)), _fn(_b, _fn(_list(_a), _b)))),
    # String/int helpers used in corpus files
    "append":       Scheme(vars=(), type=_fn(_str, _fn(_str, _str))),  # type: ignore[arg-type]
    "split_ints":   Scheme(vars=(), type=_fn(_str, _list(_int))),  # type: ignore[arg-type]
    # Dict helpers (high-level wrappers around map_* builtins)
    "dict_empty":  _poly(_a, TApp(TConst("Dict"), _a)),
    "dict_get":    _poly(_a, _fn(_str, _fn(TApp(TConst("Dict"), _a), _maybe(_a)))),
    "dict_set":    _poly(_a, _fn(_str, _fn(_a, _fn(TApp(TConst("Dict"), _a), TApp(TConst("Dict"), _a))))),
    "dict_remove": _poly(_a, _fn(_str, _fn(TApp(TConst("Dict"), _a), TApp(TConst("Dict"), _a)))),
}


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


def _strip_headers(src: str) -> str:
    """Strip leading module/import/blank lines (mirrors strip_headers in bootstrap compiler)."""
    lines = src.split("\n")
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if not stripped or stripped.startswith("module ") or stripped.startswith("import "):
            i += 1
        else:
            break
    return "\n".join(lines[i:])


def _has_import_headers(src: str) -> bool:
    """Return True if the source starts with import lines."""
    for line in src.split("\n"):
        stripped = line.strip()
        if not stripped or stripped.startswith("module "):
            continue
        if stripped.startswith("import "):
            return True
        break
    return False


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

    if _has_import_headers(src):
        # File has imports: use load_module_bundle for full type env.
        # Parse only the stripped body to get user-declared names.
        try:
            body = _strip_headers(src)
            prog = parse(body)
        except Exception as e:
            print(f"ERROR: parse: {e}")
            sys.exit(1)
        try:
            bundle = load_module_bundle(Path(path))
            bundled_tree = parse(bundle.source)
            resolve_program_names(bundled_tree, bundle)
            type_map = typecheck_program(bundled_tree)
        except Exception as e:
            print(f"ERROR: check: {e}")
            sys.exit(1)
    else:
        try:
            prog = parse(src)
        except Exception as e:
            print(f"ERROR: parse: {e}")
            sys.exit(1)
        try:
            type_map = typecheck_program(prog, seed_env=PRELUDE_SEED_ENV)
        except Exception as e:
            print(f"ERROR: check: {e}")
            sys.exit(1)

    for name in user_declared_names(prog):
        val = type_map.get(name, "<not found>")
        print(f"{name} : {val}")


if __name__ == "__main__":
    main()
