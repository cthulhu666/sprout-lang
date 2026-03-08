from __future__ import annotations

import argparse
import json
from pathlib import Path

from .ast import to_dict
from .interpreter import RuntimeError, run_program
from .parser import ParseError, parse
from .tokenizer import TokenizeError
from .typechecker import TypeCheckError, typecheck_program


def cmd_parse(path: Path) -> int:
    source = path.read_text(encoding="utf-8")
    tree = parse(source)
    print(json.dumps(to_dict(tree), indent=2))
    return 0


def cmd_check(path: Path) -> int:
    source = path.read_text(encoding="utf-8")
    tree = parse(source)
    typed = typecheck_program(tree)
    print("ok")
    for name in sorted(typed.keys()):
        print(f"{name}: {typed[name]}")
    return 0


def cmd_run(path: Path) -> int:
    source = path.read_text(encoding="utf-8")
    tree = parse(source)
    typecheck_program(tree)
    run_program(tree)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sprout")
    sub = parser.add_subparsers(dest="command", required=True)

    p_parse = sub.add_parser("parse", help="parse a Sprout file and print AST as JSON")
    p_parse.add_argument("file", type=Path)
    p_check = sub.add_parser("check", help="typecheck a Sprout file")
    p_check.add_argument("file", type=Path)
    p_run = sub.add_parser("run", help="typecheck and run a Sprout file")
    p_run.add_argument("file", type=Path)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "parse":
            return cmd_parse(args.file)
        if args.command == "check":
            return cmd_check(args.file)
        if args.command == "run":
            return cmd_run(args.file)
    except (ParseError, TokenizeError, TypeCheckError, RuntimeError) as exc:
        print(f"error: {exc}")
        return 1

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
