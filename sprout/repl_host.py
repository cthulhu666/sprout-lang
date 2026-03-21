from __future__ import annotations

from dataclasses import dataclass, field
import io
import re
from pathlib import Path
import tempfile
from typing import Callable, TextIO

from . import ast
from .interpreter import RuntimeError, run_program
from .module_loader import ModuleLoadError, load_module_bundle, resolve_program_names
from .parser import ParseError, parse
from .surface_checks import SurfaceCheckError, validate_public_surface
from .tokenizer import TokenizeError
from .typeclass_lowering import TypeclassLoweringError, lower_typeclasses
from .typechecker import InferState, TypeCheckError, parse_type_expr, typecheck_program, unify

__all__ = [
    "ReplSession",
    "hosted_repl_session",
    "reset_hosted_repl_session",
    "check_source",
    "eval_expression_lines_in_source",
    "infer_type_in_source",
    "instances_in_source",
    "completion_candidates_in_state",
]

_REPL_COMMANDS = (
    ":help",
    ":quit",
    ":q",
    ":exit",
    ":type",
    ":t",
    ":instances",
    ":i",
)
_REPL_PRELUDE_NAMES = frozenset(
    {
        "Bool",
        "Cons",
        "Dict",
        "Err",
        "False",
        "Foldable",
        "Functor",
        "Int",
        "IO",
        "Just",
        "List",
        "Maybe",
        "Nil",
        "Nothing",
        "Ok",
        "Result",
        "Semigroup",
        "String",
        "True",
        "Unit",
        "Vec",
        "filter",
        "fmap",
        "fold",
        "foldable_to_vec",
        "list_append",
        "list_fold",
        "list_map",
        "map",
        "print",
        "split_ints",
        "vec_append",
        "vec_empty",
        "vec_fold",
        "vec_get",
        "vec_get_or",
        "vec_length",
        "vec_map",
        "vec_prepend",
        "vec_reverse",
        "vec_set",
        "vec_slice",
        "vec_sum",
        "vec_sum_by",
    }
)
_REPL_STDLIB_EXTRA_NAMES = frozenset(
    {
        "bytes",
        "collections",
        "crypto",
        "dict_empty",
        "dict_get",
        "dict_keys",
        "dict_remove",
        "dict_set",
        "dict_values",
        "http",
        "http_client",
        "math",
        "net",
        "string",
        "terminal",
    }
)
_REPL_TOKEN_RE = re.compile(r"[A-Za-z_:][A-Za-z0-9_:.]*$")
_REPL_MODULE_NAME = "app.repl"
_HOSTED_SESSION: ReplSession | None = None


def _repl_compose_source(
    imports: list[str],
    declarations: list[str],
    tail: list[str] | None = None,
) -> str:
    chunks = [f"module {_REPL_MODULE_NAME}"]
    chunks.extend(imp for imp in imports if imp.strip())
    chunks.extend(chunk for chunk in declarations + (tail or []) if chunk.strip())
    return "\n\n".join(chunks)


def _repl_parse_and_check(
    imports: list[str],
    declarations: list[str],
    tail: list[str] | None = None,
) -> tuple[object, dict[str, str]]:
    source = _repl_compose_source(imports, declarations, tail)
    with tempfile.TemporaryDirectory() as tmpdir:
        temp_path = Path(tmpdir) / "repl_session.sprout"
        temp_path.write_text(source, encoding="utf-8")
        bundle = load_module_bundle(temp_path)
        tree = parse(bundle.source)
        resolve_program_names(tree, bundle)
        validate_public_surface(tree, bundle)
        types = typecheck_program(tree)
        return tree, types


def _repl_parse_and_check_source(
    source: str,
    tail: list[str] | None = None,
) -> tuple[object, dict[str, str]]:
    chunks = [source.strip()]
    chunks.extend(chunk for chunk in (tail or []) if chunk.strip())
    composed = "\n\n".join(chunk for chunk in chunks if chunk)
    with tempfile.TemporaryDirectory() as tmpdir:
        temp_path = Path(tmpdir) / "repl_session.sprout"
        temp_path.write_text(composed, encoding="utf-8")
        bundle = load_module_bundle(temp_path)
        tree = parse(bundle.source)
        resolve_program_names(tree, bundle)
        validate_public_surface(tree, bundle)
        types = typecheck_program(tree)
        return tree, types


def _repl_lookup_param_type(
    parse_and_check: Callable[[list[str]], tuple[object, dict[str, str]]],
    type_expr_source: str,
) -> ast.TypeExpr:
    probe_name = "__repl_instances_probe"
    tree, _ = parse_and_check([f"fn {probe_name}(__value: {type_expr_source}) -> Int = 0"])
    for decl in tree.declarations:
        if isinstance(decl, ast.FnDecl) and (decl.name == probe_name or decl.name.endswith(f".{probe_name}")):
            param = decl.params[0]
            if param.type_expr is None:
                raise TypeCheckError("Internal error: REPL instance query lost its type annotation")
            return param.type_expr
    raise TypeCheckError("Internal error: REPL instance query probe was not found")


def _repl_render_instances(tree: ast.Program, query_type: ast.TypeExpr) -> tuple[str, list[str]]:
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


@dataclass
class ReplSession:
    imports: list[str] = field(default_factory=list)
    declarations: list[str] = field(default_factory=list)
    repl_counter: int = 0

    def _compose_source(
        self,
        tail: list[str] | None = None,
        *,
        imports: list[str] | None = None,
        declarations: list[str] | None = None,
    ) -> str:
        session_imports = self.imports if imports is None else imports
        session_declarations = self.declarations if declarations is None else declarations
        chunks = [f"module {_REPL_MODULE_NAME}"]
        chunks.extend(imp for imp in session_imports if imp.strip())
        chunks.extend(chunk for chunk in session_declarations + (tail or []) if chunk.strip())
        return "\n\n".join(chunks)

    def _parse_and_check(
        self,
        tail: list[str] | None = None,
        *,
        imports: list[str] | None = None,
        declarations: list[str] | None = None,
    ) -> tuple[object, dict[str, str]]:
        source = self._compose_source(tail, imports=imports, declarations=declarations)
        with tempfile.TemporaryDirectory() as tmpdir:
            temp_path = Path(tmpdir) / "repl_session.sprout"
            temp_path.write_text(source, encoding="utf-8")
            bundle = load_module_bundle(temp_path)
            tree = parse(bundle.source)
            resolve_program_names(tree, bundle)
            validate_public_surface(tree, bundle)
            types = typecheck_program(tree)
            return tree, types

    def parse_and_check(self, tail: list[str] | None = None) -> tuple[object, dict[str, str]]:
        return self._parse_and_check(tail)

    def _next_temp_name(self) -> str:
        self.repl_counter += 1
        return f"__repl_value_{self.repl_counter}"

    def add_import(self, source: str) -> None:
        self._parse_and_check(imports=self.imports + [source])
        self.imports.append(source)

    def add_declaration(self, source: str) -> None:
        self._parse_and_check(declarations=self.declarations + [source])
        self.declarations.append(source)

    def infer_type(self, expr: str) -> str:
        name = self._next_temp_name()
        _, types = self._parse_and_check([f"let {name} = {expr}"])
        return _repl_lookup_type(types, name)

    def _lookup_param_type(self, type_expr_source: str) -> ast.TypeExpr:
        return _repl_lookup_param_type(self._parse_and_check, type_expr_source)

    def instances_for_type(self, type_expr_source: str) -> tuple[str, list[str]]:
        tree, _ = self.parse_and_check()
        query_type = self._lookup_param_type(type_expr_source)
        return _repl_render_instances(tree, query_type)

    def completion_matches(self, text: str, line_buffer: str) -> list[str]:
        return _repl_completion_matches(text, line_buffer, self.imports, self.declarations)

    def completion_candidates(self, line_buffer: str) -> tuple[str, list[str]]:
        return _repl_completion_candidates(line_buffer, self.imports, self.declarations)

    def run_expression(self, source: str, stdout: TextIO | None = None) -> None:
        name = self._next_temp_name()
        _, types = self._parse_and_check([f"let {name} = {source}"])
        inferred_type = _repl_lookup_type(types, name)
        if inferred_type.endswith(" !{IO}"):
            if inferred_type != "Unit !{IO}":
                raise TypeCheckError("repl cannot auto-print effectful non-Unit expressions yet")
            main_body = name
        else:
            main_body = f"print({name})"
        tree, _ = self._parse_and_check([f"let {name} = {source}", f"fn main() -> Unit !{{IO}} = {main_body}"])
        lowered = lower_typeclasses(tree)
        typecheck_program(lowered)
        run_program(lowered, stdout=stdout)

    def eval_expression_lines(self, source: str) -> tuple[str, ...]:
        capture = io.StringIO()
        self.run_expression(source, stdout=capture)
        return tuple(capture.getvalue().splitlines())


def hosted_repl_session() -> ReplSession:
    global _HOSTED_SESSION
    if _HOSTED_SESSION is None:
        _HOSTED_SESSION = ReplSession()
    return _HOSTED_SESSION


def reset_hosted_repl_session() -> ReplSession:
    global _HOSTED_SESSION
    _HOSTED_SESSION = ReplSession()
    return _HOSTED_SESSION


def infer_type_in_source(source: str, expr: str) -> str:
    name = "__repl_source_value"
    _, types = _repl_parse_and_check_source(source, [f"let {name} = {expr}"])
    return _repl_lookup_type(types, name)


def check_source(source: str) -> None:
    _repl_parse_and_check_source(source)


def eval_expression_lines_in_source(source: str, expr: str) -> tuple[str, ...]:
    name = "__repl_source_value"
    _, types = _repl_parse_and_check_source(source, [f"let {name} = {expr}"])
    inferred_type = _repl_lookup_type(types, name)
    if inferred_type.endswith(" !{IO}"):
        if inferred_type != "Unit !{IO}":
            raise TypeCheckError("repl cannot auto-print effectful non-Unit expressions yet")
        main_body = name
    else:
        main_body = f"print({name})"
    tree, _ = _repl_parse_and_check_source(source, [f"let {name} = {expr}", f"fn main() -> Unit !{{IO}} = {main_body}"])
    lowered = lower_typeclasses(tree)
    typecheck_program(lowered)
    capture = io.StringIO()
    run_program(lowered, stdout=capture)
    return tuple(capture.getvalue().splitlines())


def instances_in_source(source: str, type_expr_source: str) -> tuple[str, list[str]]:
    tree, _ = _repl_parse_and_check_source(source)
    query_type = _repl_lookup_param_type(lambda tail: _repl_parse_and_check_source(source, tail), type_expr_source)
    return _repl_render_instances(tree, query_type)


def completion_candidates_in_state(
    line_buffer: str,
    imports: list[str],
    declarations: list[str],
) -> tuple[str, list[str]]:
    return _repl_completion_candidates(line_buffer, imports, declarations)


def _repl_lookup_type(types: dict[str, str], name: str) -> str:
    direct = types.get(name)
    if direct is not None:
        return direct
    qualified = [typ for key, typ in types.items() if key.endswith(f".{name}")]
    if len(qualified) == 1:
        return qualified[0]
    raise KeyError(name)


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
    raise TypeError(f"Unsupported type expression: {node!r}")


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


def _repl_declared_names(declarations: list[str]) -> set[str]:
    names: set[str] = set()
    for source in declarations:
        tree = parse(source)
        for decl in tree.declarations:
            if isinstance(decl, ast.FnDecl | ast.LetDecl | ast.ClassDecl | ast.TypeDecl):
                names.add(decl.name)
            if isinstance(decl, ast.TypeDecl):
                names.update(ctor.name for ctor in decl.constructors)
            elif isinstance(decl, ast.ClassDecl):
                names.update(method.name for method in decl.methods)
            elif isinstance(decl, ast.InstanceDecl):
                names.update(method.name for method in decl.methods)
    return names


def _repl_imported_names(imports: list[str]) -> set[str]:
    names: set[str] = set()
    for source in imports:
        for line in source.splitlines():
            stripped = line.strip()
            if not stripped.startswith("import "):
                continue
            body = stripped[len("import ") :]
            if " as " in body:
                module_name, alias = body.split(" as ", 1)
                names.add(alias.strip())
                body = module_name.strip()
            if "(" in body and ")" in body:
                selected = body.split("(", 1)[1].rsplit(")", 1)[0]
                names.update(name.strip() for name in selected.split(",") if name.strip())
            else:
                names.add(body.rsplit(".", 1)[-1].strip())
    return names


def _repl_completion_matches(
    text: str,
    line_buffer: str,
    imports: list[str],
    declarations: list[str],
) -> list[str]:
    token_match = _REPL_TOKEN_RE.search(line_buffer)
    prefix = token_match.group(0) if token_match is not None else text
    return _repl_completion_from_prefix(prefix, imports, declarations)


def _repl_completion_candidates(
    line_buffer: str,
    imports: list[str],
    declarations: list[str],
) -> tuple[str, list[str]]:
    token_match = _REPL_TOKEN_RE.search(line_buffer)
    prefix = token_match.group(0) if token_match is not None else ""
    return prefix, _repl_completion_from_prefix(prefix, imports, declarations)


def _repl_completion_from_prefix(
    prefix: str,
    imports: list[str],
    declarations: list[str],
) -> list[str]:
    names = set(_REPL_COMMANDS)
    names.update(_REPL_PRELUDE_NAMES)
    names.update(_REPL_STDLIB_EXTRA_NAMES)
    names.update(_repl_declared_names(declarations))
    names.update(_repl_imported_names(imports))
    return sorted(name for name in names if name.startswith(prefix))
