from __future__ import annotations

from functools import lru_cache
import re

from . import ast
from .parser import parse
from .stdlib import load_prelude

__all__ = [
    "completion_candidates_in_state",
    "completion_matches_in_state",
    "python_completion_complete_in_state",
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


def _declared_names_from_tree(tree: ast.Program) -> set[str]:
    names: set[str] = set()
    for decl in tree.declarations:
        if isinstance(decl, (ast.FnDecl, ast.LetDecl, ast.ClassDecl)):
            names.add(decl.name.rsplit(".", 1)[-1])
        elif isinstance(decl, ast.TypeDecl):
            names.add(decl.name.rsplit(".", 1)[-1])
            for ctor in decl.constructors:
                names.add(ctor.name.rsplit(".", 1)[-1])
        elif isinstance(decl, ast.InstanceDecl):
            for method in decl.methods:
                names.add(method.name.rsplit(".", 1)[-1])
    return names


@lru_cache(maxsize=1)
def _prelude_completion_names() -> frozenset[str]:
    tree = parse(load_prelude())
    return frozenset(_declared_names_from_tree(tree))


def _declared_names_from_declarations(declarations: list[str]) -> set[str]:
    names: set[str] = set()
    for source in declarations:
        tree = parse(source)
        names.update(_declared_names_from_tree(tree))
    return names


def _imported_names_from_imports(imports: list[str]) -> set[str]:
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


def _completion_from_prefix(prefix: str, imports: list[str], declarations: list[str]) -> list[str]:
    names = set(_REPL_COMMANDS)
    names.update(_prelude_completion_names())
    names.update(_REPL_STDLIB_EXTRA_NAMES)
    names.update(_declared_names_from_declarations(declarations))
    names.update(_imported_names_from_imports(imports))
    return sorted(name for name in names if name.startswith(prefix))


def completion_matches_in_state(
    text: str,
    line_buffer: str,
    imports: list[str],
    declarations: list[str],
) -> list[str]:
    token_match = _REPL_TOKEN_RE.search(line_buffer)
    prefix = token_match.group(0) if token_match is not None else text
    return _completion_from_prefix(prefix, imports, declarations)


def completion_candidates_in_state(
    line_buffer: str,
    imports: list[str],
    declarations: list[str],
) -> tuple[str, list[str]]:
    token_match = _REPL_TOKEN_RE.search(line_buffer)
    prefix = token_match.group(0) if token_match is not None else ""
    return prefix, _completion_from_prefix(prefix, imports, declarations)


def python_completion_complete_in_state(
    line_buffer: str,
    imports: list[str],
    declarations: list[str],
) -> tuple[str, list[str]]:
    return completion_candidates_in_state(line_buffer, imports, declarations)
