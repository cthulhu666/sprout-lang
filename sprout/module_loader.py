from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from . import ast


MODULE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")
DECL_RE = re.compile(r"^\s*(fn|type|let)\s+([A-Za-z_][A-Za-z0-9_]*)\b")


class ModuleLoadError(ValueError):
    pass


@dataclass(frozen=True)
class ImportSpec:
    module: str
    alias: str | None = None
    exposing: tuple[str, ...] | None = None


@dataclass(frozen=True)
class HeaderInfo:
    module: str | None
    imports: list[ImportSpec]
    body: str


@dataclass(frozen=True)
class ModuleInfo:
    path: Path
    header: HeaderInfo
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
    exposing: tuple[str, ...] | None = None
    clause_part = rest
    exposing_key = " exposing "
    if exposing_key in rest:
        clause_part, exposing_part = rest.split(exposing_key, 1)
        exposing_part = exposing_part.strip()
        if not (exposing_part.startswith("(") and exposing_part.endswith(")")):
            raise ModuleLoadError(
                f"Invalid import exposing clause in {path}:{line_no}; expected import x.y exposing (a, b)"
            )
        names_txt = exposing_part[1:-1].strip()
        if names_txt:
            names = [name.strip() for name in names_txt.split(",")]
            for name in names:
                if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
                    raise ModuleLoadError(f"Invalid exposed symbol {name!r} in {path}:{line_no}")
            exposing = tuple(names)
        else:
            exposing = tuple()

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

    return ImportSpec(module=module_name, alias=alias, exposing=exposing)


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


def _extract_decl_names(body: str) -> set[str]:
    out: set[str] = set()
    for line in body.splitlines():
        m = DECL_RE.match(line)
        if m is not None:
            out.add(m.group(2))
    return out


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
        modules[path] = ModuleInfo(path=path, header=header, exported=_extract_decl_names(header.body))

    def validate_module(path: Path) -> None:
        info = modules[path]
        local_names = info.exported
        introduced: dict[str, str] = {}
        aliases: dict[str, str] = {}
        for imp in info.header.imports:
            imp_path = _resolve_module(imp.module, path)
            imp_info = modules[imp_path]
            exposed_names = list(imp.exposing or ())
            for name in exposed_names:
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

            if imp.exposing is not None:
                names = exposed_names
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
        if imp.exposing is not None:
            names = list(imp.exposing)
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
        "tcp_listen",
        "tcp_accept",
        "tcp_read",
        "tcp_write",
        "tcp_close",
        "tcp_close_listener",
        "tcp_echo_serve",
    }
    all_exported: set[str] = set()
    for info in bundle.modules.values():
        all_exported |= info.exported

    def resolve_name(name: str, node: object | None = None) -> str:
        line = getattr(node, "line", None)
        if line is None:
            return name
        module_info = _module_for_line(bundle, line)
        if module_info is None:
            return name
        aliases, unqualified_imports = _imports_for_module(module_info, bundle)
        local = module_info.exported

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

    def walk_expr(e: ast.Expr, node: object | None = None) -> None:
        if isinstance(e, ast.VarExpr):
            e.name = resolve_name(e.name, e)
            return
        if isinstance(e, ast.UnaryExpr):
            walk_expr(e.operand, e)
            return
        if isinstance(e, ast.BinaryExpr):
            walk_expr(e.left, e)
            walk_expr(e.right, e)
            return
        if isinstance(e, ast.CallExpr):
            walk_expr(e.callee, e)
            for arg in e.args:
                walk_expr(arg, e)
            return
        if isinstance(e, ast.IfExpr):
            walk_expr(e.condition, e)
            walk_expr(e.then_branch, e)
            walk_expr(e.else_branch, e)
            return
        if isinstance(e, ast.MatchExpr):
            walk_expr(e.scrutinee, e)
            for branch in e.branches:
                walk_pattern(branch.pattern, e)
                walk_expr(branch.value, e)

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
            walk_expr(decl.body, decl)
        elif isinstance(decl, ast.LetDecl):
            walk_expr(decl.value, decl)
