from __future__ import annotations

import io
from pathlib import Path
import tempfile

from . import ast
from .interpreter import run_program
from .module_loader import load_module_bundle, resolve_program_names
from .parser import parse
from .surface_checks import validate_public_surface
from .typeclass_lowering import lower_typeclasses
from .typechecker import InferState, TypeCheckError, parse_type_expr, typecheck_program, unify

__all__ = [
    "check_source",
    "eval_expression_lines_in_source",
    "infer_type_in_source",
    "instances_in_source",
    "python_execution_check_source",
    "python_execution_eval_expr_in_source",
    "python_execution_instances_in_source",
    "python_execution_type_of_in_source",
]


def _compose_snapshot_source(source: str, tail: list[str] | None = None) -> str:
    chunks = [source.strip()]
    chunks.extend(chunk for chunk in (tail or []) if chunk.strip())
    return "\n\n".join(chunk for chunk in chunks if chunk)


def _parse_and_check_source(
    source: str,
    tail: list[str] | None = None,
) -> tuple[ast.Program, dict[str, str]]:
    composed = _compose_snapshot_source(source, tail)
    with tempfile.TemporaryDirectory() as tmpdir:
        temp_path = Path(tmpdir) / "repl_session.sprout"
        temp_path.write_text(composed, encoding="utf-8")
        bundle = load_module_bundle(temp_path)
        tree = parse(bundle.source)
        resolve_program_names(tree, bundle)
        validate_public_surface(tree, bundle)
        types = typecheck_program(tree)
        return tree, types


def _lookup_type(types: dict[str, str], name: str) -> str:
    direct = types.get(name)
    if direct is not None:
        return direct
    qualified = [typ for key, typ in types.items() if key.endswith(f".{name}")]
    if len(qualified) == 1:
        return qualified[0]
    raise KeyError(name)


def _lookup_param_type(source: str, type_expr_source: str) -> ast.TypeExpr:
    probe_name = "__repl_instances_probe"
    tree, _ = _parse_and_check_source(source, [f"fn {probe_name}(__value: {type_expr_source}) -> Int = 0"])
    for decl in tree.declarations:
        if isinstance(decl, ast.FnDecl) and (decl.name == probe_name or decl.name.endswith(f".{probe_name}")):
            param = decl.params[0]
            if param.type_expr is None:
                raise TypeCheckError("Internal error: REPL instance query lost its type annotation")
            return param.type_expr
    raise TypeCheckError("Internal error: REPL instance query probe was not found")


def _type_expr_to_string(node: ast.TypeExpr) -> str:
    if isinstance(node, ast.TypeName):
        return node.name
    if isinstance(node, ast.TypeApply):
        base = _type_expr_to_string(node.base)
        arg = _type_expr_to_string(node.arg)
        if isinstance(node.arg, (ast.TypeApply, ast.TypeArrow, ast.TypeEffect)):
            arg = f"({arg})"
        return f"{base} {arg}"
    if isinstance(node, ast.TypeArrow):
        left = _type_expr_to_string(node.left)
        right = _type_expr_to_string(node.right)
        if isinstance(node.left, ast.TypeArrow):
            left = f"({left})"
        suffix = ""
        if node.effects:
            suffix = " !{" + ", ".join(node.effects) + "}"
        return f"{left} -> {right}{suffix}"
    if isinstance(node, ast.TypeEffect):
        return _type_expr_to_string(node.base) + " !{" + ", ".join(node.effects) + "}"
    if isinstance(node, ast.TupleType):
        return "(" + ", ".join(_type_expr_to_string(item) for item in node.items) + ")"
    raise TypeCheckError(f"Unsupported REPL instance query type: {node}")


def _type_expr_outermost_base(node: ast.TypeExpr) -> ast.TypeExpr:
    current = node
    while isinstance(current, ast.TypeApply):
        current = current.base
    return current


def _type_expr_matches_query(pattern: ast.TypeExpr, query: ast.TypeExpr) -> bool:
    candidates = [query]
    query_base = _type_expr_outermost_base(query)
    if query_base is not query:
        candidates.append(query_base)
    for candidate in candidates:
        state = InferState()
        try:
            unify(
                state,
                parse_type_expr(pattern, allow_implicit_type_vars=True, state=state),
                parse_type_expr(candidate),
            )
        except TypeCheckError:
            continue
        return True
    return False


def _render_instances(tree: ast.Program, query_type: ast.TypeExpr) -> tuple[str, list[str]]:
    matches: list[str] = []
    for decl in tree.declarations:
        if not isinstance(decl, ast.InstanceDecl):
            continue
        if len(decl.constraint.args) != 1:
            continue
        if _type_expr_matches_query(decl.constraint.args[0], query_type):
            rendered_args = " ".join(
                _type_expr_to_string(arg)
                if not isinstance(arg, (ast.TypeApply, ast.TypeArrow, ast.TypeEffect))
                else f"({_type_expr_to_string(arg)})"
                for arg in decl.constraint.args
            )
            matches.append(f"{decl.constraint.class_name} {rendered_args}")
    matches.sort()
    return _type_expr_to_string(query_type), matches


def infer_type_in_source(source: str, expr: str) -> str:
    name = "__repl_source_value"
    _, types = _parse_and_check_source(source, [f"let {name} = {expr}"])
    return _lookup_type(types, name)


def check_source(source: str) -> None:
    _parse_and_check_source(source)


def eval_expression_lines_in_source(source: str, expr: str) -> tuple[str, ...]:
    name = "__repl_source_value"
    _, types = _parse_and_check_source(source, [f"let {name} = {expr}"])
    inferred_type = _lookup_type(types, name)
    if inferred_type.endswith(" !{IO}"):
        if inferred_type != "Unit !{IO}":
            raise TypeCheckError("repl cannot auto-print effectful non-Unit expressions yet")
        main_body = name
    else:
        main_body = f"print({name})"
    tree, _ = _parse_and_check_source(source, [f"let {name} = {expr}", f"fn main() -> Unit !{{IO}} = {main_body}"])
    lowered = lower_typeclasses(tree)
    typecheck_program(lowered)
    capture = io.StringIO()
    run_program(lowered, stdout=capture)
    return tuple(capture.getvalue().splitlines())


def instances_in_source(source: str, type_expr_source: str) -> tuple[str, list[str]]:
    tree, _ = _parse_and_check_source(source)
    query_type = _lookup_param_type(source, type_expr_source)
    return _render_instances(tree, query_type)


def python_execution_check_source(module_source: str) -> None:
    check_source(module_source)


def python_execution_type_of_in_source(module_source: str, expr: str) -> str:
    return infer_type_in_source(module_source, expr)


def python_execution_instances_in_source(module_source: str, query: str) -> tuple[str, list[str]]:
    return instances_in_source(module_source, query)


def python_execution_eval_expr_in_source(module_source: str, expr: str) -> tuple[str, ...]:
    return eval_expression_lines_in_source(module_source, expr)
