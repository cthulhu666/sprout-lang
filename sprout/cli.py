from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

from .ast import to_dict
from .codegen_llvm import CodegenError, compile_to_llvm
from .interpreter import RuntimeError, run_program
from .parser import ParseError, parse
from .stdlib import with_prelude
from .tokenizer import TokenizeError
from .typechecker import TypeCheckError, typecheck_program


def cmd_parse(path: Path) -> int:
    source = path.read_text(encoding="utf-8")
    tree = parse(source)
    print(json.dumps(to_dict(tree), indent=2))
    return 0


def cmd_check(path: Path, with_stdlib: bool = False) -> int:
    source = path.read_text(encoding="utf-8")
    tree = parse(with_prelude(source) if with_stdlib else source)
    typed = typecheck_program(tree)
    print("ok")
    for name in sorted(typed.keys()):
        print(f"{name}: {typed[name]}")
    return 0


def cmd_run(path: Path, with_stdlib: bool = False) -> int:
    source = path.read_text(encoding="utf-8")
    tree = parse(with_prelude(source) if with_stdlib else source)
    typecheck_program(tree)
    run_program(tree)
    return 0


def cmd_compile(path: Path, out: Path, with_stdlib: bool = False, native: bool = False) -> int:
    source = path.read_text(encoding="utf-8")
    tree = parse(with_prelude(source) if with_stdlib else source)
    typecheck_program(tree)
    llvm_ir = compile_to_llvm(tree)

    if not native:
        out.write_text(llvm_ir, encoding="utf-8")
        return 0

    clang = shutil.which("clang")
    if clang is None:
        raise CodegenError("clang not found; install clang or compile with --emit-llvm only")

    with tempfile.NamedTemporaryFile("w", suffix=".ll", delete=False, encoding="utf-8") as tmp:
        tmp.write(llvm_ir)
        ll_path = Path(tmp.name)
    runtime_c = """#include <stdio.h>
long long print_int(long long x) {
  printf("%lld\\n", x);
  return x;
}
"""
    with tempfile.NamedTemporaryFile("w", suffix=".c", delete=False, encoding="utf-8") as tmp_c:
        tmp_c.write(runtime_c)
        c_path = Path(tmp_c.name)
    try:
        subprocess.run([clang, str(ll_path), str(c_path), "-O2", "-o", str(out)], check=True)
    finally:
        ll_path.unlink(missing_ok=True)
        c_path.unlink(missing_ok=True)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sprout")
    sub = parser.add_subparsers(dest="command", required=True)

    p_parse = sub.add_parser("parse", help="parse a Sprout file and print AST as JSON")
    p_parse.add_argument("file", type=Path)
    p_check = sub.add_parser("check", help="typecheck a Sprout file")
    p_check.add_argument("file", type=Path)
    p_check.add_argument("--with-stdlib", action="store_true", help="load stdlib prelude")
    p_run = sub.add_parser("run", help="typecheck and run a Sprout file")
    p_run.add_argument("file", type=Path)
    p_run.add_argument("--with-stdlib", action="store_true", help="load stdlib prelude")
    p_compile = sub.add_parser("compile", help="typecheck and compile a Sprout file")
    p_compile.add_argument("file", type=Path)
    p_compile.add_argument("-o", "--output", type=Path, required=True, help="output file")
    p_compile.add_argument("--with-stdlib", action="store_true", help="load stdlib prelude")
    p_compile.add_argument(
        "--native",
        action="store_true",
        help="emit native binary with clang (default writes LLVM .ll text)",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "parse":
            return cmd_parse(args.file)
        if args.command == "check":
            return cmd_check(args.file, with_stdlib=args.with_stdlib)
        if args.command == "run":
            return cmd_run(args.file, with_stdlib=args.with_stdlib)
        if args.command == "compile":
            return cmd_compile(
                args.file,
                out=args.output,
                with_stdlib=args.with_stdlib,
                native=args.native,
            )
    except (ParseError, TokenizeError, TypeCheckError, RuntimeError, CodegenError, subprocess.CalledProcessError) as exc:
        print(f"error: {exc}")
        return 1

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
