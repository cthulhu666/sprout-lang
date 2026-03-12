from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re


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


def load_module_source(entry_path: Path) -> str:
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
    for path in ordered:
        chunks.append(f"# module: {path}\n")
        body = modules[path].header.body
        for imp in modules[path].header.imports:
            if imp.alias is None:
                continue
            imp_path = _resolve_module(imp.module, path)
            imp_info = modules[imp_path]
            for name in sorted(imp_info.exported):
                chunks.append(f"let {imp.alias}.{name} = {name}\n")
        if modules[path].header.imports:
            chunks.append("\n")
        chunks.append(body)
        if not body.endswith("\n"):
            chunks.append("\n")
        chunks.append("\n")

    return "".join(chunks)
