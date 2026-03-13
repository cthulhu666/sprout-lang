from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from . import ast


MODULE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")
DECL_RE = re.compile(r"^\s*(fn|type|let)\s+([A-Za-z_][A-Za-z0-9_]*)\b")
EXPORT_DECL_RE = re.compile(r"^\s*export\s+(fn|type|let)\s+([A-Za-z_][A-Za-z0-9_]*)\b")


class ModuleLoadError(ValueError):
    pass


@dataclass(frozen=True)
class ImportSpec:
    module: str
    alias: str | None = None
    imported_names: tuple[str, ...] | None = None


@dataclass(frozen=True)
class HeaderInfo:
    module: str | None
    imports: list[ImportSpec]
    body: str


@dataclass(frozen=True)
class ModuleInfo:
    path: Path
    header: HeaderInfo
    declared: set[str]
    exported: set[str]


@dataclass(frozen=True)
class ModuleSegment:
    path: Path
    start_line: int
    end_line: int


@dataclass(frozen=True)
class ModuleBundle:
    source: str
    modules: dict[Path, ModuleInfo]
    segments: list[ModuleSegment]


def _parse_import_line(trimmed: str, path: Path, line_no: int) -> ImportSpec:
    rest = trimmed[len("import ") :].strip()
    alias: str | None = None
    imported_names: tuple[str, ...] | None = None
    clause_part = rest

    if "(" in rest:
        if not rest.endswith(")"):
            raise ModuleLoadError(
                f"Invalid import clause in {path}:{line_no}; expected import x.y (a, b)"
            )
        open_idx = rest.rfind("(")
        clause_part = rest[:open_idx].strip()
        names_txt = rest[open_idx + 1 : -1].strip()
        if names_txt:
            names = [name.strip() for name in names_txt.split(",")]
            for name in names:
                if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
                    raise ModuleLoadError(f"Invalid exposed symbol {name!r} in {path}:{line_no}")
            imported_names = tuple(names)
        else:
            imported_names = tuple()

    module_part = clause_part.strip()
    as_key = " as "
    if as_key in module_part:
        module_part, alias_part = module_part.split(as_key, 1)
        alias = alias_part.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", alias):
            raise ModuleLoadError(f"Invalid import alias {alias!r} in {path}:{line_no}")

    module_name = module_part.strip()
    if not MODULE_RE.fullmatch(module_name):
        raise ModuleLoadError(f"Invalid module name {module_name!r} in import at {path}:{line_no}")

    return ImportSpec(module=module_name, alias=alias, imported_names=imported_names)


def parse_header(source: str, path: Path) -> HeaderInfo:
    module_name: str | None = None
    imports: list[ImportSpec] = []
    body_lines: list[str] = []

    in_header = True
    lines = source.splitlines()
    for idx, line in enumerate(lines, start=1):
        trimmed = line.strip()
        if in_header:
            if trimmed == "" or trimmed.startswith("#"):
                body_lines.append(line)
                continue
            if trimmed.startswith("module "):
                candidate = trimmed[len("module ") :].strip()
                if module_name is not None:
                    raise ModuleLoadError(f"Duplicate module declaration in {path}:{idx}")
                if not MODULE_RE.fullmatch(candidate):
                    raise ModuleLoadError(f"Invalid module name {candidate!r} at {path}:{idx}")
                module_name = candidate
                continue
            if trimmed.startswith("import "):
                imports.append(_parse_import_line(trimmed, path, idx))
                continue
            in_header = False

        body_lines.append(line)

    body = "\n".join(body_lines)
    if source.endswith("\n"):
        body += "\n"
    return HeaderInfo(module=module_name, imports=imports, body=body)


def _extract_decl_and_export_names(body: str) -> tuple[set[str], set[str], str]:
    declared: set[str] = set()
    explicit_exports: set[str] = set()
    out_lines: list[str] = []
    for line in body.splitlines():
        m_export = EXPORT_DECL_RE.match(line)
        if m_export is not None:
            name = m_export.group(2)
            declared.add(name)
            explicit_exports.add(name)
            out_lines.append(line.replace("export ", "", 1))
            continue
        m = DECL_RE.match(line)
        if m is not None:
            declared.add(m.group(2))
        out_lines.append(line)
    sanitized = "\n".join(out_lines)
    if body.endswith("\n"):
        sanitized += "\n"
    return declared, explicit_exports, sanitized


def _module_path(module_name: str) -> Path:
    return Path(*module_name.split(".")).with_suffix(".sprout")


def _resolve_module(module_name: str, importer: Path) -> Path:
    rel = _module_path(module_name)
    candidates = [importer.parent / rel, Path.cwd() / rel]
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    raise ModuleLoadError(
        f"Cannot resolve module {module_name!r} imported from {importer}; checked {[str(c) for c in candidates]}"
    )


def load_module_bundle(entry_path: Path) -> ModuleBundle:
    entry = entry_path.resolve()
    ordered: list[Path] = []
    modules: dict[Path, ModuleInfo] = {}
    visiting: list[Path] = []
    seen: set[Path] = set()

    def visit(path: Path) -> None:
        if path in seen:
            return
        if path in visiting:
            cycle = " -> ".join(str(p) for p in visiting + [path])
            raise ModuleLoadError(f"Import cycle detected: {cycle}")

        visiting.append(path)
        source = path.read_text(encoding="utf-8")
        header = parse_header(source, path)
        for imp in header.imports:
            imp_path = _resolve_module(imp.module, path)
            visit(imp_path)
        visiting.pop()

        seen.add(path)
        ordered.append(path)
        declared, exported, sanitized_body = _extract_decl_and_export_names(header.body)
        modules[path] = ModuleInfo(
            path=path,
            header=HeaderInfo(module=header.module, imports=header.imports, body=sanitized_body),
            declared=declared,
            exported=exported,
        )

    def validate_module(path: Path) -> None:
        info = modules[path]
        local_names = info.declared
        introduced: dict[str, str] = {}
        aliases: dict[str, str] = {}
        for imp in info.header.imports:
            imp_path = _resolve_module(imp.module, path)
            imp_info = modules[imp_path]
            imported_names = list(imp.imported_names or ())
            for name in imported_names:
                if name not in imp_info.exported:
                    raise ModuleLoadError(
                        f"Module {imp.module!r} does not export {name!r} "
                        f"(imported from {path})"
                    )

            if imp.alias is not None:
                prev_alias = aliases.get(imp.alias)
                if prev_alias is not None and prev_alias != imp.module:
                    raise ModuleLoadError(
                        f"Duplicate import alias {imp.alias!r} in {path}: "
                        f"used for both {prev_alias!r} and {imp.module!r}"
                    )
                aliases[imp.alias] = imp.module

            if imp.imported_names is not None:
                names = imported_names
            elif imp.alias is None:
                names = sorted(imp_info.exported)
            else:
                names = []

            for name in names:
                prev = introduced.get(name)
                if prev is not None and prev != imp.module:
                    raise ModuleLoadError(
                        f"Ambiguous import {name!r} in {path}: provided by both {prev!r} and {imp.module!r}"
                    )
                introduced[name] = imp.module

        for name, provider in introduced.items():
            if name in local_names:
                raise ModuleLoadError(
                    f"Module {path} declares {name!r} which conflicts with imported symbol from {provider!r}"
                )

    visit(entry)
    for path in ordered:
        validate_module(path)

    chunks: list[str] = []
    segments: list[ModuleSegment] = []
    line_cursor = 1
    for path in ordered:
        header = f"# module: {path}\n"
        chunks.append(header)
        line_cursor += 1
        body = modules[path].header.body
        body_lines = body.count("\n") + (0 if body.endswith("\n") else 1 if body else 0)
        if body_lines > 0:
            segments.append(ModuleSegment(path=path, start_line=line_cursor, end_line=line_cursor + body_lines - 1))
        chunks.append(body)
        line_cursor += body_lines
        if not body.endswith("\n"):
            chunks.append("\n")
            line_cursor += 1
        chunks.append("\n")
        line_cursor += 1

    return ModuleBundle(source="".join(chunks), modules=modules, segments=segments)


def load_module_source(entry_path: Path) -> str:
    return load_module_bundle(entry_path).source


def _module_for_line(bundle: ModuleBundle, line: int) -> ModuleInfo | None:
    for segment in bundle.segments:
        if segment.start_line <= line <= segment.end_line:
            return bundle.modules[segment.path]
    return None


def _imports_for_module(module_info: ModuleInfo, bundle: ModuleBundle) -> tuple[dict[str, str], dict[str, str]]:
    aliases: dict[str, str] = {}
    unqualified: dict[str, str] = {}
    for imp in module_info.header.imports:
        imp_path = _resolve_module(imp.module, module_info.path)
        imp_info = bundle.modules[imp_path]
        if imp.alias is not None:
            aliases[imp.alias] = imp.module
        if imp.imported_names is not None:
            names = list(imp.imported_names)
        elif imp.alias is None:
            names = sorted(imp_info.exported)
        else:
            names = []
        for name in names:
            unqualified[name] = imp.module
    return aliases, unqualified


def resolve_program_names(program: ast.Program, bundle: ModuleBundle) -> None:
    builtin_values = {
        "print",
        "print_int",
        "read_lines",
        "parse_int",
        "split_words",
        "str_concat",
        "str_len",
        "str_slice",
        "str_find",
        "str_starts_with",
        "vector_empty",
        "vector_length",
        "vector_get",
        "vector_set",
        "vector_append",
        "map_empty",
        "map_get",
        "map_set",
        "map_remove",
        "map_size",
        "tcp_listen",
        "tcp_accept",
        "tcp_read",
        "tcp_write",
        "tcp_close",
        "tcp_close_listener",
        "tcp_echo_serve",
        "http_request",
        "json_parse",
        "term_clear",
        "term_move",
        "term_hide_cursor",
        "term_show_cursor",
        "term_read_key",
        "term_write",
    }
    all_exported: set[str] = set()
    all_declared: set[str] = set()
    declared_by_name: dict[str, set[Path]] = {}
    for path, info in bundle.modules.items():
        all_exported |= info.exported
        all_declared |= info.declared
        for name in info.declared:
            declared_by_name.setdefault(name, set()).add(path)

    def resolve_name(name: str, node: object | None = None) -> str:
        line = getattr(node, "line", None)
        if line is None:
            return name
        module_info = _module_for_line(bundle, line)
        if module_info is None:
            return name
        aliases, unqualified_imports = _imports_for_module(module_info, bundle)
        local = module_info.declared

        if "." in name:
            alias, symbol = name.split(".", 1)
            target = aliases.get(alias)
            if target is None:
                raise ModuleLoadError(f"Unknown import alias {alias!r} at {module_info.path}:{line}")
            target_path = _resolve_module(target, module_info.path)
            if symbol not in bundle.modules[target_path].exported:
                raise ModuleLoadError(
                    f"Module {target!r} does not export {symbol!r} (referenced at {module_info.path}:{line})"
                )
            return symbol

        if name in builtin_values or name in local or name in unqualified_imports:
            return name
        if name in all_exported:
            raise ModuleLoadError(
                f"Symbol {name!r} at {module_info.path}:{line} requires explicit import or qualification"
            )
        providers = declared_by_name.get(name, set())
        if providers and module_info.path not in providers and name in all_declared:
            raise ModuleLoadError(
                f"Symbol {name!r} at {module_info.path}:{line} is not exported by any imported module"
            )
        return name

    def walk_type(t: ast.TypeExpr, node: object | None = None) -> None:
        if isinstance(t, ast.TypeName):
            t.name = resolve_name(t.name, node)
            return
        if isinstance(t, ast.TypeApply):
            walk_type(t.base, node)
            walk_type(t.arg, node)
            return
        if isinstance(t, ast.TypeArrow):
            walk_type(t.left, node)
            walk_type(t.right, node)

    def walk_pattern(p: ast.Pattern, node: object | None = None) -> None:
        if isinstance(p, ast.ConstructorPattern):
            p.name = resolve_name(p.name, node)
            for arg in p.args:
                walk_pattern(arg, node)

    def _pattern_bindings(p: ast.Pattern) -> set[str]:
        if isinstance(p, ast.VarPattern):
            return {p.name}
        if isinstance(p, ast.ConstructorPattern):
            out: set[str] = set()
            for arg in p.args:
                out |= _pattern_bindings(arg)
            return out
        return set()

    def walk_expr(e: ast.Expr, node: object | None = None, scope: set[str] | None = None) -> None:
        current_scope = scope or set()
        if isinstance(e, ast.VarExpr):
            if e.name not in current_scope:
                e.name = resolve_name(e.name, e)
            return
        if isinstance(e, ast.UnaryExpr):
            walk_expr(e.operand, e, current_scope)
            return
        if isinstance(e, ast.BinaryExpr):
            walk_expr(e.left, e, current_scope)
            walk_expr(e.right, e, current_scope)
            return
        if isinstance(e, ast.CallExpr):
            walk_expr(e.callee, e, current_scope)
            for arg in e.args:
                walk_expr(arg, e, current_scope)
            return
        if isinstance(e, ast.IfExpr):
            walk_expr(e.condition, e, current_scope)
            walk_expr(e.then_branch, e, current_scope)
            walk_expr(e.else_branch, e, current_scope)
            return
        if isinstance(e, ast.MatchExpr):
            walk_expr(e.scrutinee, e, current_scope)
            for branch in e.branches:
                walk_pattern(branch.pattern, e)
                branch_scope = set(current_scope)
                branch_scope |= _pattern_bindings(branch.pattern)
                walk_expr(branch.value, e, branch_scope)

    for decl in program.declarations:
        if isinstance(decl, ast.TypeDecl):
            for ctor in decl.constructors:
                for arg in ctor.args:
                    walk_type(arg, decl)
        elif isinstance(decl, ast.FnDecl):
            for param in decl.params:
                walk_type(param.type_expr, decl)
            if decl.return_type is not None:
                walk_type(decl.return_type, decl)
            scope = {p.name for p in decl.params}
            walk_expr(decl.body, decl, scope)
        elif isinstance(decl, ast.LetDecl):
            walk_expr(decl.value, decl)
