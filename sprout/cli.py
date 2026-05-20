from __future__ import annotations

import argparse
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile

from .analysis_cli import cmd_analysis_cli
from . import ast
from .ast import to_dict
from .codegen_llvm import CodegenError, compile_to_llvm
from .elaborate import ElaborateError
from .formatter import format_source, lint_source
from .interpreter import RuntimeError, run_program
from .module_loader import CompilerWarning, ModuleLoadError, load_module_bundle, resolve_program_names
from .parser import ParseError, parse
from .repl import cmd_repl
from .surface_checks import SurfaceCheckError, validate_public_surface
from .stdlib import with_prelude
from .tokenizer import TokenizeError
from .typeclass_lowering import TypeclassLoweringError, lower_typeclasses
from .typechecker import TypeCheckError, typecheck_program


def _bundle_has_implicit_prelude(bundle: object | None) -> bool:
    if bundle is None:
        return False
    return any(path.name == "prelude.sprout" and "stdlib" in path.parts for path in bundle.modules)


def _print_warnings(warnings: list[CompilerWarning]) -> None:
    for warning in warnings:
        print(
            f"warning: {warning.path}:{warning.line}:{warning.column}: {warning.message}",
            file=sys.stderr,
        )


def _missing_entrypoint_message(entry_main_name: str) -> str:
    return f"Executable entrypoint `{entry_main_name}` is missing"


def _entrypoint_arity_message(entry_main_name: str) -> str:
    return f"Executable entrypoint `{entry_main_name}` must take zero arguments"


def _entrypoint_type_message(entry_main_name: str, actual_type: str) -> str:
    return f"Executable entrypoint `{entry_main_name}` must have type Unit !{{IO}} or Int !{{IO}}, got {actual_type}"


def _validate_executable_entrypoint(program: ast.Program, typed: dict[str, str], entry_main_name: str) -> None:
    main_type = typed.get(entry_main_name)
    if main_type is None:
        raise TypeCheckError(_missing_entrypoint_message(entry_main_name))
    main_decl = next(
        (
            decl
            for decl in program.declarations
            if isinstance(decl, ast.FnDecl) and decl.name == entry_main_name
        ),
        None,
    )
    if main_decl is None:
        raise TypeCheckError(_missing_entrypoint_message(entry_main_name))
    if len(main_decl.params) != 0:
        raise TypeCheckError(_entrypoint_arity_message(entry_main_name))
    if main_type not in ("Unit !{IO}", "Int !{IO}"):
        raise TypeCheckError(_entrypoint_type_message(entry_main_name, main_type))


def _validate_typed_program_expr(expr: "ast.Expr", decl_name: str) -> None:
    """Walk an expression tree and assert that no list-typed field is None.

    Catches bugs where a field that must be a list (params, args, steps,
    branches, items) is accidentally None — these produce cryptic SIGSEGV
    crashes in the native collect_free_vars rather than a Python traceback.
    """
    if isinstance(expr, ast.LambdaExpr):
        if expr.params is None:
            raise RuntimeError(
                f"validate_typed_program: LambdaExpr.params is None in decl {decl_name!r}"
            )
        _validate_typed_program_expr(expr.body, decl_name)
    elif isinstance(expr, ast.CallExpr):
        if expr.args is None:
            raise RuntimeError(
                f"validate_typed_program: CallExpr.args is None in decl {decl_name!r}"
            )
        _validate_typed_program_expr(expr.callee, decl_name)
        for arg in expr.args:
            _validate_typed_program_expr(arg, decl_name)
    elif isinstance(expr, ast.DoExpr):
        if expr.steps is None:
            raise RuntimeError(
                f"validate_typed_program: DoExpr.steps is None in decl {decl_name!r}"
            )
        for step in expr.steps:
            if isinstance(step, (ast.DoBindStep, ast.DoLetStep, ast.DoExprStep)):
                _validate_typed_program_expr(step.value, decl_name)
    elif isinstance(expr, ast.MatchExpr):
        if expr.branches is None:
            raise RuntimeError(
                f"validate_typed_program: MatchExpr.branches is None in decl {decl_name!r}"
            )
        _validate_typed_program_expr(expr.scrutinee, decl_name)
        for branch in expr.branches:
            _validate_typed_program_expr(branch.value, decl_name)
    elif isinstance(expr, ast.TupleExpr):
        if expr.items is None:
            raise RuntimeError(
                f"validate_typed_program: TupleExpr.items is None in decl {decl_name!r}"
            )
        for item in expr.items:
            _validate_typed_program_expr(item, decl_name)
    elif isinstance(expr, ast.IfExpr):
        _validate_typed_program_expr(expr.condition, decl_name)
        _validate_typed_program_expr(expr.then_branch, decl_name)
        _validate_typed_program_expr(expr.else_branch, decl_name)
    elif isinstance(expr, ast.BinaryExpr):
        _validate_typed_program_expr(expr.left, decl_name)
        _validate_typed_program_expr(expr.right, decl_name)
    elif isinstance(expr, ast.UnaryExpr):
        _validate_typed_program_expr(expr.operand, decl_name)
    elif isinstance(expr, ast.IntRangeExpr):
        _validate_typed_program_expr(expr.start, decl_name)
        _validate_typed_program_expr(expr.end, decl_name)
    elif isinstance(expr, ast.RecordExpr):
        for field in expr.fields:
            _validate_typed_program_expr(field.value, decl_name)
    elif isinstance(expr, ast.GetFieldExpr):
        _validate_typed_program_expr(expr.record, decl_name)


def _validate_typed_program(program: "ast.Program") -> None:
    """Assert no list-typed field in the typed+lowered AST is None."""
    for decl in program.declarations:
        if isinstance(decl, ast.FnDecl):
            _validate_typed_program_expr(decl.body, decl.name)
        elif isinstance(decl, ast.LetDecl):
            _validate_typed_program_expr(decl.value, decl.name)


def cmd_parse(path: Path) -> int:
    bundle = load_module_bundle(path)
    source = bundle.source
    tree = parse(source)
    _print_warnings(resolve_program_names(tree, bundle))
    print(json.dumps(to_dict(tree), indent=2))
    return 0


def _strip_sprout_headers(source: str) -> str:
    """Strip module/import/export-type(..) lines so the bootstrap parser can handle the body."""
    import re
    out = []
    for line in source.splitlines():
        s = line.strip()
        if s.startswith("module ") or s.startswith("import "):
            continue
        line = re.sub(r"\bexport\s+type\s+(\w+)\s*\(\.\.\)", r"type \1", line)
        line = re.sub(r"^\s*export\s+", lambda m: m.group(0).replace("export ", ""), line)
        out.append(line)
    return "\n".join(out)


def cmd_bootstrap_parse(path: Path) -> int:
    driver = Path(__file__).parent.parent / "stdlib" / "compiler" / "driver.sprout"
    bundle = load_module_bundle(driver)
    source = bundle.source
    tree = parse(source)
    _print_warnings(resolve_program_names(tree, bundle))
    validate_public_surface(tree, bundle)
    typecheck_program(tree)
    validate_public_surface(tree, bundle)
    lowered = lower_typeclasses(tree)
    stripped = _strip_sprout_headers(path.read_text(encoding="utf-8"))
    with tempfile.NamedTemporaryFile(mode="w", suffix=".spr", delete=False, encoding="utf-8") as f:
        f.write(stripped)
        tmp_path = f.name
    try:
        run_program(lowered, argv=[tmp_path])
    finally:
        Path(tmp_path).unlink(missing_ok=True)
    return 0


def cmd_bootstrap_check(path: Path) -> int:
    """Typecheck a Sprout file using the bootstrap (self-hosted) full pipeline.

    Routes through stdlib/compiler/full_driver.sprout, which runs the
    complete Sprout-native pipeline on the target file:
      bundle → typecheck → lower
    Prints "OK" and a decl count on success, or "ERROR: <stage>: <msg>" on
    the first failing stage.
    """
    driver = Path(__file__).parent.parent / "stdlib" / "compiler" / "full_driver.sprout"
    bundle = load_module_bundle(driver)
    source = bundle.source
    tree = parse(source)
    _print_warnings(resolve_program_names(tree, bundle))
    validate_public_surface(tree, bundle)
    typecheck_program(tree)
    validate_public_surface(tree, bundle)
    lowered = lower_typeclasses(tree)
    stdlib_root = str(Path(__file__).parent.parent / "stdlib")
    run_program(lowered, argv=[stdlib_root, str(path.resolve())])
    return 0


def cmd_fmt(path: Path, check: bool = False) -> int:
    source = path.read_text(encoding="utf-8")
    formatted = format_source(source)
    current = source if source.endswith("\n") else source + "\n"
    if check:
        if formatted != current:
            print(f"needs formatting: {path}")
            return 1
        print("ok")
        return 0
    if formatted != current:
        path.write_text(formatted, encoding="utf-8")
        print(f"formatted {path}")
        return 0
    print(f"already formatted {path}")
    return 0


def cmd_lint(path: Path) -> int:
    issues = lint_source(path.read_text(encoding="utf-8"))
    if not issues:
        print("ok")
        return 0
    for issue in issues:
        print(f"{path}:{issue.line}:{issue.column}: {issue.message}")
    return 1


def cmd_check(path: Path, with_stdlib: bool = False) -> int:
    bundle = load_module_bundle(path)
    source = bundle.source
    if with_stdlib and not _bundle_has_implicit_prelude(bundle):
        source = with_prelude(source)
        bundle = None
    tree = parse(source)
    if bundle is not None:
        _print_warnings(resolve_program_names(tree, bundle))
    validate_public_surface(tree, bundle)
    typed = typecheck_program(tree)
    validate_public_surface(tree, bundle)
    print("ok")
    for name in sorted(typed.keys()):
        print(f"{name}: {typed[name]}")
    return 0


def cmd_run(
    path: Path,
    with_stdlib: bool = False,
    program_args: list[str] | None = None,
) -> int:
    bundle = load_module_bundle(path)
    source = bundle.source
    if with_stdlib and not _bundle_has_implicit_prelude(bundle):
        source = with_prelude(source)
        bundle = None
    tree = parse(source)
    if bundle is not None:
        _print_warnings(resolve_program_names(tree, bundle))
    validate_public_surface(tree, bundle)
    typecheck_program(tree)
    validate_public_surface(tree, bundle)
    lowered = lower_typeclasses(tree)
    entry_main_name = "main"
    if bundle is not None:
        entry_info = bundle.modules[path.resolve()]
        if entry_info.header.module is not None:
            entry_main_name = f"{entry_info.header.module}.main"
    typed = typecheck_program(lowered)
    _validate_typed_program(lowered)
    _validate_executable_entrypoint(lowered, typed, entry_main_name)
    exit_code = run_program(lowered, argv=program_args)
    return exit_code if exit_code is not None else 0


def cmd_compile(
    path: Path,
    out: Path,
    with_stdlib: bool = False,
    native: bool = False,
    emit_runtime_c: "Path | None" = None,
) -> int:
    ll_path: Path | None = None
    clang: str | None = None
    if emit_runtime_c is None:
        bundle = load_module_bundle(path)
        source = bundle.source
        if with_stdlib and not _bundle_has_implicit_prelude(bundle):
            source = with_prelude(source)
            bundle = None
        tree = parse(source)
        if bundle is not None:
            _print_warnings(resolve_program_names(tree, bundle))
        validate_public_surface(tree, bundle)
        typecheck_program(tree)
        validate_public_surface(tree, bundle)
        lowered = lower_typeclasses(tree)
        entry_main_name = "main"
        if bundle is not None:
            entry_info = bundle.modules[path.resolve()]
            if entry_info.header.module is not None:
                entry_main_name = f"{entry_info.header.module}.main"
        typed = typecheck_program(lowered)
        _validate_typed_program(lowered)
        _validate_executable_entrypoint(lowered, typed, entry_main_name)
        llvm_ir = compile_to_llvm(lowered, entry_main_name=entry_main_name)

        if not native:
            out.write_text(llvm_ir, encoding="utf-8")
            return 0

        clang = shutil.which("clang")
        if clang is None:
            raise CodegenError("clang not found; install clang or compile with --emit-llvm only")

        with tempfile.NamedTemporaryFile("w", suffix=".ll", delete=False, encoding="utf-8") as tmp:
            tmp.write(llvm_ir)
            ll_path = Path(tmp.name)
    runtime_c = (Path(__file__).parent.parent / "runtime" / "sprout_runtime.c").read_text(encoding="utf-8")
    if emit_runtime_c is not None:
        emit_runtime_c.write_text(runtime_c, encoding="utf-8")
        return 0
    with tempfile.NamedTemporaryFile("w", suffix=".c", delete=False, encoding="utf-8") as tmp_c:
        tmp_c.write(runtime_c)
        c_path = Path(tmp_c.name)
    try:
        clang_cmd = [clang, str(ll_path), str(c_path), "-O2"]
        if sys.platform == "darwin":
            clang_cmd.extend(["-framework", "Security", "-framework", "CoreFoundation"])
        clang_cmd.extend(["-o", str(out)])
        subprocess.run(clang_cmd, check=True)
    finally:
        ll_path.unlink(missing_ok=True)
        c_path.unlink(missing_ok=True)
    return 0




def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sprout")
    sub = parser.add_subparsers(dest="command", required=True)

    p_parse = sub.add_parser("parse", help="parse a Sprout file and print AST as JSON")
    p_parse.add_argument("file", type=Path)
    p_fmt = sub.add_parser("fmt", help="format a Sprout file")
    p_fmt.add_argument("file", type=Path)
    p_fmt.add_argument("--check", action="store_true", help="report whether formatting changes are needed")
    p_lint = sub.add_parser("lint", help="lint a Sprout file for baseline style issues")
    p_lint.add_argument("file", type=Path)
    p_check = sub.add_parser("check", help="typecheck a Sprout file")
    p_check.add_argument("file", type=Path)
    p_check.add_argument("--with-stdlib", action="store_true", help="load stdlib prelude")
    p_run = sub.add_parser("run", help="typecheck and run a Sprout file")
    p_run.add_argument("file", type=Path)
    p_run.add_argument("--with-stdlib", action="store_true", help="load stdlib prelude")
    p_run.add_argument("program_args", nargs="*", help="arguments exposed to the program via argv_get")
    p_compile = sub.add_parser("compile", help="typecheck and compile a Sprout file")
    p_compile.add_argument("file", type=Path)
    p_compile.add_argument("-o", "--output", type=Path, required=True, help="output file")
    p_compile.add_argument("--with-stdlib", action="store_true", help="load stdlib prelude")
    p_compile.add_argument(
        "--native",
        action="store_true",
        help="emit native binary with clang (default writes LLVM .ll text)",
    )
    p_compile.add_argument(
        "--emit-runtime-c",
        type=Path,
        default=None,
        metavar="FILE",
        dest="emit_runtime_c",
        help="write the Sprout C runtime to FILE and exit (for linking pre-generated LLVM IR)",
    )
    p_bootstrap_parse = sub.add_parser("bootstrap-parse", help="parse a Sprout file using the bootstrap (self-hosted) parser")
    p_bootstrap_parse.add_argument("file", type=Path)
    p_bootstrap_check = sub.add_parser("bootstrap-check", help="typecheck a Sprout file using the bootstrap (self-hosted) checker")
    p_bootstrap_check.add_argument("file", type=Path)
    sub.add_parser("analysis-service", help=argparse.SUPPRESS)
    sub.add_parser("analysis-stdio", help=argparse.SUPPRESS)
    p_repl = sub.add_parser("repl", help="start a simple interactive Sprout REPL")
    p_repl.add_argument(
        "--native",
        action="store_true",
        help="experimentally launch the Sprout REPL frontend as a native binary via the analysis-service bridge",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "parse":
            return cmd_parse(args.file)
        if args.command == "fmt":
            return cmd_fmt(args.file, check=args.check)
        if args.command == "lint":
            return cmd_lint(args.file)
        if args.command == "check":
            return cmd_check(
                args.file,
                with_stdlib=args.with_stdlib,
            )
        if args.command == "run":
            return cmd_run(
                args.file,
                with_stdlib=args.with_stdlib,
                program_args=args.program_args,
            )
        if args.command == "compile":
            return cmd_compile(
                args.file,
                out=args.output,
                with_stdlib=args.with_stdlib,
                native=args.native,
                emit_runtime_c=args.emit_runtime_c,
            )
        if args.command == "bootstrap-parse":
            return cmd_bootstrap_parse(args.file)
        if args.command == "bootstrap-check":
            return cmd_bootstrap_check(args.file)
        if args.command == "analysis-service":
            return cmd_analysis_cli(args.command)
        if args.command == "analysis-stdio":
            return cmd_analysis_cli(args.command)
        if args.command == "repl":
            return cmd_repl(native=args.native)
    except (
        ParseError,
        TokenizeError,
        TypeCheckError,
        RuntimeError,
        CodegenError,
        ElaborateError,
        ModuleLoadError,
        SurfaceCheckError,
        TypeclassLoweringError,
        subprocess.CalledProcessError,
    ) as exc:
        print(f"error: {exc}")
        return 1

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
