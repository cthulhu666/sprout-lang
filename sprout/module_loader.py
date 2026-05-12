from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from . import ast
from .parser import extract_decl_annotations


MODULE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")
DECL_RE = re.compile(r"^\s*(fn|type|let|class)\s+([A-Za-z_][A-Za-z0-9_]*)\b")
EXPORT_DECL_RE = re.compile(r"^\s*export\s+(fn|type|let|class)\s+([A-Za-z_][A-Za-z0-9_]*)\b")
EXPORT_TYPE_ALL_CTORS_RE = re.compile(r"^(\s*)export\s+type\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(\.\.\)")
MODULE_COMPAT_VALUES: dict[str, dict[str, str]] = {
    "stdlib.collections": {
    "Just": "Just",
    "Nothing": "Nothing",
    "Cons": "Cons",
    "Nil": "Nil",
    "Vec": "Vec",
    "Dict": "Dict",
    "map": "map",
    "fold": "fold",
    "fmap": "fmap",
    "list_map": "list_map",
    "list_fold": "list_fold",
    "list_append": "list_append",
    "vec_empty": "vec_empty",
    "vec_prepend": "vec_prepend",
    "vec_append": "vec_append",
    "vec_length": "vec_length",
    "vec_get": "vec_get",
    "vec_get_or": "vec_get_or",
    "vec_set": "vec_set",
    "vec_map": "vec_map",
    "vec_fold": "vec_fold",
    "vec_filter": "vec_filter",
    "vec_filter_map": "vec_filter_map",
    "vec_any": "vec_any",
    "vec_all": "vec_all",
    "vec_count": "vec_count",
    "vec_slice": "vec_slice",
    "vec_reverse": "vec_reverse",
    "vec_sum": "vec_sum",
    "vec_sum_by": "vec_sum_by",
    "to_string": "to_string",
    "compare": "compare",
    "ord_lt": "ord_lt",
    "ord_lte": "ord_lte",
    "ord_gt": "ord_gt",
    "ord_gte": "ord_gte",
    "vec_sort": "vec_sort",
    "vec_sort_by": "vec_sort_by",
    "foldable_to_vec": "foldable_to_vec",
    "dict_empty": "dict_empty",
    "dict_get": "dict_get",
    "dict_set": "dict_set",
    "dict_remove": "dict_remove",
    "dict_keys": "dict_keys",
    "dict_values": "dict_values",
    "dict_entries": "dict_entries",
    "dict_from_list": "dict_from_list",
    "set_empty": "set_empty",
    "set_insert": "set_insert",
    "set_member": "set_member",
    "set_to_list": "set_to_list",
    "set_size": "set_size",
    },
    "stdlib.http": {
        "Just": "Just",
        "Nothing": "Nothing",
        "Ok": "Ok",
        "Err": "Err",
    },
    "stdlib.bytes": {
        "Ok": "Ok",
        "Err": "Err",
    },
    "stdlib.crypto": {
        "Ok": "Ok",
        "Err": "Err",
    },
    "stdlib.net": {
        "Ok": "Ok",
        "Err": "Err",
    },
}
MODULE_COMPAT_TYPES: dict[str, dict[str, dict[str, str]]] = {
    "stdlib.collections": {
    "Maybe": {"Just": "Just", "Nothing": "Nothing"},
    "List": {"Cons": "Cons", "Nil": "Nil"},
    "Vec": {"Vec": "Vec"},
    "Dict": {"Dict": "Dict"},
    "Set": {"Set": "Set"},
    },
    "stdlib.http": {
        "Maybe": {"Just": "Just", "Nothing": "Nothing"},
        "Result": {"Ok": "Ok", "Err": "Err"},
    },
    "stdlib.bytes": {
        "Result": {"Ok": "Ok", "Err": "Err"},
        "Builder": {},
    },
    "stdlib.crypto": {
        "Result": {"Ok": "Ok", "Err": "Err"},
        "Base64Error": {"Base64DecodeError": "Base64DecodeError"},
        "BytesOpError": {"BytesXorLengthMismatch": "BytesXorLengthMismatch"},
        "CryptoError": {
            "CryptoInvalidArgument": "CryptoInvalidArgument",
            "CryptoUnavailable": "CryptoUnavailable",
        },
    },
    "stdlib.net": {
        "Result": {"Ok": "Ok", "Err": "Err"},
    },
}
MODULE_COMPAT_CLASSES: dict[str, dict[str, str]] = {
    "stdlib.collections": {"ToString": "ToString", "Ord": "Ord", "Semigroup": "Semigroup", "Functor": "Functor", "Foldable": "Foldable"}
}


class ModuleLoadError(ValueError):
    pass


@dataclass(frozen=True)
class CompilerWarning:
    path: Path
    line: int
    column: int
    message: str


def _fmt_names(names: set[str] | list[str] | tuple[str, ...], limit: int = 8) -> str:
    ordered = sorted(set(names))
    if not ordered:
        return "<none>"
    if len(ordered) <= limit:
        return ", ".join(repr(name) for name in ordered)
    shown = ", ".join(repr(name) for name in ordered[:limit])
    return f"{shown}, ... ({len(ordered)} total)"


@dataclass(frozen=True)
class ImportSpec:
    module: str
    alias: str | None = None
    imported_names: tuple[str, ...] | None = None
    line: int = 1
    column: int = 1


@dataclass(frozen=True)
class HeaderInfo:
    module: str | None
    imports: list[ImportSpec]
    body: str
    body_line_numbers: tuple[int, ...]


@dataclass(frozen=True)
class ModuleInfo:
    path: Path
    header: HeaderInfo
    declared: set[str]
    exported: set[str]
    exported_type_constructors: set[str]
    annotations: dict[str, tuple[ast.DeclAnnotation, ...]]


@dataclass(frozen=True)
class ModuleSegment:
    path: Path
    start_line: int
    end_line: int
    source_lines: tuple[int, ...]


@dataclass(frozen=True)
class ModuleBundle:
    source: str
    modules: dict[Path, ModuleInfo]
    segments: list[ModuleSegment]


@dataclass
class ModuleSymbols:
    value_locals: dict[str, str]
    method_locals: set[str]
    type_locals: dict[str, str]
    class_locals: dict[str, str]
    exported_values: dict[str, str]
    exported_types: dict[str, str]
    exported_classes: dict[str, str]
    exported_type_constructors: dict[str, dict[str, str]]
    value_annotations: dict[str, tuple[ast.DeclAnnotation, ...]]
    type_annotations: dict[str, tuple[ast.DeclAnnotation, ...]]
    class_annotations: dict[str, tuple[ast.DeclAnnotation, ...]]


def _source_line(path: Path, line_no: int) -> str:
    lines = path.read_text(encoding="utf-8").splitlines()
    if 1 <= line_no <= len(lines):
        return lines[line_no - 1]
    return ""


def _format_source_context(path: Path, line_no: int, column: int = 1) -> str:
    source = _source_line(path, line_no)
    gutter = f"{line_no}"
    caret_pad = max(column - 1, 0)
    return (
        f"--> {path}:{line_no}:{column}\n"
        f"{gutter} | {source}\n"
        f"{' ' * len(gutter)} | {' ' * caret_pad}^"
    )


def _module_error(message: str, path: Path, line_no: int, column: int = 1) -> ModuleLoadError:
    return ModuleLoadError(f"{message}\n{_format_source_context(path, line_no, column)}")


def _parse_import_line(line: str, path: Path, line_no: int) -> ImportSpec:
    column = line.index("import ") + 1
    trimmed = line.strip()
    rest = trimmed[len("import ") :].strip()
    alias: str | None = None
    imported_names: tuple[str, ...] | None = None
    clause_part = rest

    if "(" in rest:
        if not rest.endswith(")"):
            raise _module_error(
                "Invalid import clause; expected `import x.y (a, b)`",
                path,
                line_no,
                column,
            )
        open_idx = rest.rfind("(")
        clause_part = rest[:open_idx].strip()
        names_txt = rest[open_idx + 1 : -1].strip()
        if names_txt:
            names = [name.strip() for name in names_txt.split(",")]
            for name in names:
                if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
                    raise _module_error(
                        f"Invalid exposed symbol {name!r}",
                        path,
                        line_no,
                        column,
                    )
            imported_names = tuple(names)
        else:
            imported_names = tuple()

    module_part = clause_part.strip()
    as_key = " as "
    if as_key in module_part:
        module_part, alias_part = module_part.split(as_key, 1)
        alias = alias_part.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", alias):
            raise _module_error(f"Invalid import alias {alias!r}", path, line_no, column)

    module_name = module_part.strip()
    if not MODULE_RE.fullmatch(module_name):
        raise _module_error(f"Invalid module name {module_name!r} in import", path, line_no, column)

    return ImportSpec(
        module=module_name,
        alias=alias,
        imported_names=imported_names,
        line=line_no,
        column=column,
    )


def parse_header(source: str, path: Path) -> HeaderInfo:
    module_name: str | None = None
    imports: list[ImportSpec] = []
    body_lines: list[str] = []
    body_line_numbers: list[int] = []

    in_header = True
    lines = source.splitlines()
    for idx, line in enumerate(lines, start=1):
        trimmed = line.strip()
        if in_header:
            if trimmed == "" or trimmed.startswith("#"):
                body_lines.append(line)
                body_line_numbers.append(idx)
                continue
            if trimmed.startswith("module "):
                candidate = trimmed[len("module ") :].strip()
                if module_name is not None:
                    raise _module_error("Duplicate module declaration", path, idx, line.index("module ") + 1)
                if not MODULE_RE.fullmatch(candidate):
                    raise _module_error(f"Invalid module name {candidate!r}", path, idx, line.index("module ") + 1)
                module_name = candidate
                continue
            if trimmed.startswith("import "):
                imports.append(_parse_import_line(line, path, idx))
                continue
            in_header = False

        body_lines.append(line)
        body_line_numbers.append(idx)

    body = "\n".join(body_lines)
    if source.endswith("\n"):
        body += "\n"
    return HeaderInfo(
        module=module_name,
        imports=imports,
        body=body,
        body_line_numbers=tuple(body_line_numbers),
    )


def _extract_decl_and_export_names(
    body: str, body_line_numbers: tuple[int, ...]
) -> tuple[set[str], set[str], set[str], dict[str, tuple[ast.DeclAnnotation, ...]], str]:
    declared: set[str] = set()
    explicit_exports: set[str] = set()
    exported_type_constructors: set[str] = set()
    annotations_by_name: dict[str, tuple[ast.DeclAnnotation, ...]] = {}
    out_lines: list[str] = []
    decl_annotations_by_source_line: dict[int, tuple[ast.DeclAnnotation, ...]] = {}
    body_source = "\n".join(body.splitlines())
    if body.endswith("\n"):
        body_source += "\n"
    relative_annotations = extract_decl_annotations(body_source)
    for relative_line, annotations in relative_annotations.items():
        if 1 <= relative_line <= len(body_line_numbers):
            decl_annotations_by_source_line[body_line_numbers[relative_line - 1]] = annotations
    for line in body.splitlines():
        m_export_type = EXPORT_TYPE_ALL_CTORS_RE.match(line)
        if m_export_type is not None:
            indent, name = m_export_type.groups()
            declared.add(name)
            explicit_exports.add(name)
            exported_type_constructors.add(name)
            out_lines.append(f"{indent}type {name}{line[m_export_type.end():]}")
            continue
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
    for source_line_no, line in zip(body_line_numbers, body.splitlines()):
        m_export_type = EXPORT_TYPE_ALL_CTORS_RE.match(line)
        if m_export_type is not None:
            annotations_by_name[m_export_type.group(2)] = decl_annotations_by_source_line.get(source_line_no, ())
            continue
        m_export = EXPORT_DECL_RE.match(line)
        if m_export is not None:
            annotations_by_name[m_export.group(2)] = decl_annotations_by_source_line.get(source_line_no, ())
            continue
        m = DECL_RE.match(line)
        if m is not None:
            annotations_by_name[m.group(2)] = decl_annotations_by_source_line.get(source_line_no, ())
    sanitized = "\n".join(out_lines)
    if body.endswith("\n"):
        sanitized += "\n"
    return declared, explicit_exports, exported_type_constructors, annotations_by_name, sanitized


def _find_decl_line(body: str, body_line_numbers: tuple[int, ...], name: str) -> int:
    for source_line_no, line in zip(body_line_numbers, body.splitlines()):
        m_export_type = EXPORT_TYPE_ALL_CTORS_RE.match(line)
        if m_export_type is not None and m_export_type.group(2) == name:
            return source_line_no
        m_export = EXPORT_DECL_RE.match(line)
        if m_export is not None and m_export.group(2) == name:
            return source_line_no
        m = DECL_RE.match(line)
        if m is not None and m.group(2) == name:
            return source_line_no
    return body_line_numbers[0] if body_line_numbers else 1


def _module_path(module_name: str) -> Path:
    return Path(*module_name.split(".")).with_suffix(".sprout")


def _implicit_prelude_source() -> str:
    path = Path(__file__).resolve().parent.parent / "stdlib" / "prelude.sprout"
    return parse_header(path.read_text(encoding="utf-8"), path).body


def _implicit_prelude_path() -> Path:
    return (Path(__file__).resolve().parent.parent / "stdlib" / "prelude.sprout").resolve()


def _is_stdlib_module_path(bundle: ModuleBundle, path: Path) -> bool:
    info = bundle.modules.get(path)
    if info is not None and info.header.module is not None:
        mod = info.header.module
        if mod == "stdlib" or mod.startswith("stdlib."):
            return True
    return "stdlib" in path.parts


def _resolve_module(module_name: str, importer: Path) -> Path:
    rel = _module_path(module_name)
    toolchain_root = Path(__file__).resolve().parent.parent
    candidates = [importer.parent / rel, Path.cwd() / rel]
    if module_name.startswith("stdlib."):
        candidates.append(toolchain_root / rel)
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    raise ModuleLoadError(
        f"Cannot resolve module {module_name!r} imported from {importer}; "
        f"checked {', '.join(str(c) for c in candidates)}"
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
        declared, exported, exported_type_constructors, annotations, sanitized_body = _extract_decl_and_export_names(
            header.body, header.body_line_numbers
        )
        modules[path] = ModuleInfo(
            path=path,
            header=HeaderInfo(
                module=header.module,
                imports=header.imports,
                body=sanitized_body,
                body_line_numbers=header.body_line_numbers,
            ),
            declared=declared,
            exported=exported,
            exported_type_constructors=exported_type_constructors,
            annotations=annotations,
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
                    raise _module_error(
                        f"Module {imp.module!r} does not export {name!r}; "
                        f"exported names: {_fmt_names(imp_info.exported)}",
                        path,
                        imp.line,
                        imp.column,
                    )

            namespace_alias = _namespace_alias_for_import(imp)
            if namespace_alias is not None:
                prev_alias = aliases.get(namespace_alias)
                if prev_alias is not None and prev_alias != imp.module:
                    raise _module_error(
                        f"Duplicate import alias {namespace_alias!r}: "
                        f"used for both {prev_alias!r} and {imp.module!r}. "
                        f"Use an explicit `as ...` alias on one import.",
                        path,
                        imp.line,
                        imp.column,
                    )
                aliases[namespace_alias] = imp.module

            names = imported_names if imp.imported_names is not None else []

            for name in names:
                prev = introduced.get(name)
                if prev is not None and prev != imp.module:
                    raise _module_error(
                        f"Ambiguous import {name!r}: provided by both {prev!r} and {imp.module!r}",
                        path,
                        imp.line,
                        imp.column,
                    )
                introduced[name] = imp.module

        for name, provider in introduced.items():
            if name in local_names:
                local_line = _find_decl_line(info.header.body, info.header.body_line_numbers, name)
                raise _module_error(
                    f"Module {path} declares {name!r} which conflicts with selected import from {provider!r}. "
                    f"Rename the local declaration or qualify the imported module instead.",
                    path,
                    local_line,
                    1,
                )

    visit(entry)
    for path, info in list(modules.items()):
        compat_values = MODULE_COMPAT_VALUES.get(info.header.module or "")
        compat_types = MODULE_COMPAT_TYPES.get(info.header.module or "")
        compat_classes = MODULE_COMPAT_CLASSES.get(info.header.module or "")
        if compat_values is None and compat_types is None and compat_classes is None:
            continue
        compat_names = set(compat_values or {}) | set(compat_types or {}) | set(compat_classes or {})
        compat_ctor_exports = {name for name, ctors in (compat_types or {}).items() if ctors}
        modules[path] = ModuleInfo(
            path=info.path,
            header=info.header,
            declared=info.declared,
            exported=info.exported | compat_names,
            exported_type_constructors=info.exported_type_constructors | compat_ctor_exports,
            annotations=info.annotations,
        )
    needs_implicit_prelude = any(info.header.module is not None or info.header.imports for info in modules.values())
    if needs_implicit_prelude:
        prelude_path = _implicit_prelude_path()
        prelude_header = parse_header(prelude_path.read_text(encoding="utf-8"), prelude_path)
        prelude_declared, _, _, prelude_annotations, prelude_body = _extract_decl_and_export_names(
            prelude_header.body, prelude_header.body_line_numbers
        )
        modules[prelude_path] = ModuleInfo(
            path=prelude_path,
            header=HeaderInfo(
                module=None,
                imports=[],
                body=prelude_body,
                body_line_numbers=prelude_header.body_line_numbers,
            ),
            declared=prelude_declared,
            exported=set(),
            exported_type_constructors=set(),
            annotations=prelude_annotations,
        )
        ordered = [prelude_path] + ordered

    for path in ordered:
        validate_module(path)

    chunks: list[str] = []
    segments: list[ModuleSegment] = []
    line_cursor = 1
    for path in ordered:
        header = f"# module: {path}\n"
        chunks.append(header)
        line_cursor += len(header.splitlines())
        body = modules[path].header.body
        body_lines = len(body.splitlines())
        if body_lines > 0:
            segments.append(
                ModuleSegment(
                    path=path,
                    start_line=line_cursor,
                    end_line=line_cursor + body_lines - 1,
                    source_lines=modules[path].header.body_line_numbers,
                )
            )
        chunks.append(body)
        line_cursor += body_lines
        if not body.endswith("\n"):
            chunks.append("\n")
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


def _source_location_for_bundle_line(bundle: ModuleBundle, line: int, column: int = 1) -> tuple[Path, int, int] | None:
    for segment in bundle.segments:
        if segment.start_line <= line <= segment.end_line:
            offset = line - segment.start_line
            if 0 <= offset < len(segment.source_lines):
                return segment.path, segment.source_lines[offset], column
            return segment.path, line, column
    return None


def _node_module_error(bundle: ModuleBundle, node: object, message: str) -> ModuleLoadError:
    line = getattr(node, "line", None)
    column = getattr(node, "column", 1)
    if line is None:
        return ModuleLoadError(message)
    location = _source_location_for_bundle_line(bundle, line, column)
    if location is None:
        return ModuleLoadError(message)
    path, source_line_no, source_column = location
    return _module_error(message, path, source_line_no, source_column)


def _node_warning(bundle: ModuleBundle, node: object, message: str) -> CompilerWarning | None:
    line = getattr(node, "line", None)
    column = getattr(node, "column", 1)
    if line is None:
        return None
    location = _source_location_for_bundle_line(bundle, line, column)
    if location is None:
        return None
    path, source_line_no, source_column = location
    return CompilerWarning(path=path, line=source_line_no, column=source_column, message=message)


def _format_decl_annotation_warning(name: str, annotation: ast.DeclAnnotation) -> str:
    base = {
        "unstable": f"{name!r} is unstable and may change",
        "temporary": f"{name!r} is temporary and may be removed",
        "wip": f"{name!r} is work in progress",
        "deprecated": f"{name!r} is deprecated",
    }.get(annotation.kind, f"{name!r} uses declaration annotation {annotation.kind!r}")
    if annotation.message:
        return f"{base}: {annotation.message}"
    return base


def _namespace_alias_for_import(imp: ImportSpec) -> str | None:
    if imp.alias is not None:
        return imp.alias
    if imp.imported_names is None:
        return imp.module.rsplit(".", 1)[-1]
    return None


def _qualify_name(module_name: str | None, name: str) -> str:
    if module_name is None:
        return name
    return f"{module_name}.{name}"


def _build_module_symbols(program: ast.Program, bundle: ModuleBundle) -> dict[Path, ModuleSymbols]:
    out: dict[Path, ModuleSymbols] = {
        path: ModuleSymbols(
            value_locals={},
            method_locals=set(),
            type_locals={},
            class_locals={},
            exported_values={},
            exported_types={},
            exported_classes={},
            exported_type_constructors={},
            value_annotations={},
            type_annotations={},
            class_annotations={},
        )
        for path in bundle.modules
    }

    for decl in program.declarations:
        line = getattr(decl, "line", None)
        if line is None:
            continue
        module_info = _module_for_line(bundle, line)
        if module_info is None:
            continue
        symbols = out[module_info.path]
        exported = getattr(decl, "name", None) in module_info.exported
        module_name = module_info.header.module
        if isinstance(decl, ast.FnDecl) or isinstance(decl, ast.LetDecl):
            canonical = _qualify_name(module_name, decl.name)
            symbols.value_locals[decl.name] = canonical
            symbols.value_annotations[decl.name] = module_info.annotations.get(
                decl.name, getattr(decl, "annotations", ())
            )
            if exported:
                symbols.exported_values[decl.name] = canonical
        elif isinstance(decl, ast.TypeDecl):
            canonical = _qualify_name(module_name, decl.name)
            symbols.type_locals[decl.name] = canonical
            symbols.type_annotations[decl.name] = module_info.annotations.get(
                decl.name, getattr(decl, "annotations", ())
            )
            if exported:
                symbols.exported_types[decl.name] = canonical
            export_ctors = decl.name in module_info.exported_type_constructors
            ctor_exports: dict[str, str] = {}
            for ctor in decl.constructors:
                ctor_canonical = _qualify_name(module_name, ctor.name)
                symbols.value_locals[ctor.name] = ctor_canonical
                if export_ctors:
                    ctor_exports[ctor.name] = ctor_canonical
                    symbols.exported_values[ctor.name] = ctor_canonical
            if export_ctors:
                symbols.exported_type_constructors[decl.name] = ctor_exports
        elif isinstance(decl, ast.RecordDecl):
            canonical = _qualify_name(module_name, decl.name)
            symbols.type_locals[decl.name] = canonical
            symbols.type_annotations[decl.name] = module_info.annotations.get(
                decl.name, getattr(decl, "annotations", ())
            )
            if exported:
                symbols.exported_types[decl.name] = canonical
        elif isinstance(decl, ast.ClassDecl):
            canonical = _qualify_name(module_name, decl.name)
            symbols.class_locals[decl.name] = canonical
            symbols.class_annotations[decl.name] = module_info.annotations.get(
                decl.name, getattr(decl, "annotations", ())
            )
            symbols.method_locals.update(method.name for method in decl.methods)
            if exported:
                symbols.exported_classes[decl.name] = canonical
    for path, module_info in bundle.modules.items():
        compat_values = MODULE_COMPAT_VALUES.get(module_info.header.module or "")
        compat_types = MODULE_COMPAT_TYPES.get(module_info.header.module or "")
        compat_classes = MODULE_COMPAT_CLASSES.get(module_info.header.module or "")
        if compat_values is None and compat_types is None and compat_classes is None:
            continue
        symbols = out[path]
        for name, canonical in (compat_values or {}).items():
            symbols.exported_values.setdefault(name, canonical)
            target_name = canonical.rsplit(".", 1)[-1]
            annotations = module_info.annotations.get(target_name, ())
            if not annotations:
                for candidate_info in bundle.modules.values():
                    annotations = candidate_info.annotations.get(target_name, ())
                    if annotations:
                        break
            symbols.value_annotations.setdefault(name, annotations)
        for name, ctors in (compat_types or {}).items():
            symbols.exported_types.setdefault(name, name)
            symbols.exported_type_constructors.setdefault(name, {}).update(ctors)
            for ctor_name, ctor_canonical in ctors.items():
                symbols.exported_values.setdefault(ctor_name, ctor_canonical)
        for name, canonical in (compat_classes or {}).items():
            symbols.exported_classes.setdefault(name, canonical)
    return out


def _imports_for_module(
    module_info: ModuleInfo,
    bundle: ModuleBundle,
    module_symbols: dict[Path, ModuleSymbols],
) -> tuple[dict[str, Path], dict[str, str], dict[str, Path], dict[str, str], dict[str, str], set[str]]:
    aliases: dict[str, Path] = {}
    unqualified_values: dict[str, str] = {}
    unqualified_value_sources: dict[str, Path] = {}
    unqualified_types: dict[str, str] = {}
    unqualified_classes: dict[str, str] = {}
    method_names: set[str] = set()
    for imp in module_info.header.imports:
        imp_path = _resolve_module(imp.module, module_info.path)
        imp_symbols = module_symbols[imp_path]
        namespace_alias = _namespace_alias_for_import(imp)
        if namespace_alias is not None:
            aliases[namespace_alias] = imp_path
        if imp.imported_names is None:
            method_names |= imp_symbols.method_locals
            continue
        for name in imp.imported_names:
            if name in imp_symbols.exported_values:
                unqualified_values[name] = imp_symbols.exported_values[name]
                unqualified_value_sources[name] = imp_path
            if name in imp_symbols.exported_types:
                unqualified_types[name] = imp_symbols.exported_types[name]
                for ctor_name, ctor_target in imp_symbols.exported_type_constructors.get(name, {}).items():
                    unqualified_values[ctor_name] = ctor_target
                    unqualified_value_sources[ctor_name] = imp_path
            if name in imp_symbols.exported_classes:
                unqualified_classes[name] = imp_symbols.exported_classes[name]
            if name in imp_symbols.method_locals:
                method_names.add(name)
    return aliases, unqualified_values, unqualified_value_sources, unqualified_types, unqualified_classes, method_names


def resolve_program_names(program: ast.Program, bundle: ModuleBundle) -> list[CompilerWarning]:
    public_builtin_values = {
        "print",
        "print_int",
        "read_lines",
        "read_file",
        "read_int_lines",
        "env_get",
        "argv_get",
        "parse_int",
        "int_to_string",
        "char_to_string",
        "split_words",
        "str_concat",
        "string_concat_many",
        "string_join_newlines",
        "str_len",
        "str_slice",
        "str_char_at",
        "str_find",
        "str_starts_with",
        "str_compare",
        "regex_validate",
        "regex_is_match",
        "regex_find_range",
        "regex_replace_all_literal",
        "regex_escape",
        "bytes_empty",
        "bytes_length",
        "bytes_get",
        "bytes_slice",
        "bytes_append",
        "bytes_singleton",
        "bytes_from_utf8",
        "bytes_to_utf8",
        "bytes_builder_empty",
        "bytes_builder_bytes",
        "bytes_builder_byte",
        "bytes_builder_u16_be",
        "bytes_builder_u32_be",
        "bytes_builder_append",
        "bytes_builder_build",
        "crypto_sha256",
        "crypto_hmac_sha256",
        "crypto_base64_encode",
        "crypto_base64_decode",
        "crypto_bytes_xor",
        "crypto_random_bytes",
        "vector_empty",
        "vector_length",
        "vector_get",
        "vector_set",
        "vector_append",
        "vector_from_list",
        "vector_sort_by_int",
        "map_empty",
        "map_get",
        "map_set",
        "map_remove",
        "map_size",
        "map_nth_key",
        "map_nth_value",
        "native_set_empty",
        "native_set_insert",
        "native_set_member",
        "native_set_to_list",
        "native_set_size",
        "tcp_listen",
        "tcp_accept",
        "tcp_read",
        "tcp_write",
        "tcp_connect",
        "tcp_read_exact",
        "tcp_write_all",
        "tcp_close",
        "tcp_close_listener",
        "tcp_echo_serve",
        "http_request",
        "json_parse",
        "json_stringify",
    }
    stdlib_internal_builtin_values = {
        "term_clear",
        "term_move",
        "term_hide_cursor",
        "term_show_cursor",
        "term_read_key",
        "term_read_line",
        "term_is_interactive",
        "analysis_check_source",
        "analysis_declared_names_in_source",
        "analysis_exported_names_in_source",
        "analysis_symbol_inventory_in_source",
        "analysis_symbol_locations_in_source",
        "analysis_diagnostics_in_source",
        "analysis_type_of_in_source",
        "analysis_instances_in_source",
        "repl_add_import",
        "repl_add_declaration",
        "repl_eval_expr",
        "analysis_eval_expr_in_source",
        "repl_eval_expr_in_source",
        "repl_check_source",
        "analysis_complete_in_state",
        "repl_declared_names_in_source",
        "repl_exported_names_in_source",
        "repl_symbol_inventory_in_source",
        "repl_diagnostics_in_source",
        "repl_type_of",
        "repl_type_of_in_source",
        "repl_instances",
        "repl_instances_in_source",
        "repl_complete",
        "repl_complete_in_state",
        "repl_reset_session",
        "term_write",
    }
    builtin_types = {"Int", "Bool", "String", "Bytes", "Builder", "Unit", "List", "Vector", "Map"}
    module_symbols = _build_module_symbols(program, bundle)

    all_exported_values: set[str] = set()
    all_exported_types: set[str] = set()
    all_exported_classes: set[str] = set()
    declared_value_by_name: dict[str, set[Path]] = {}
    declared_type_by_name: dict[str, set[Path]] = {}
    declared_class_by_name: dict[str, set[Path]] = {}
    for path, symbols in module_symbols.items():
        all_exported_values |= set(symbols.exported_values)
        all_exported_types |= set(symbols.exported_types)
        all_exported_classes |= set(symbols.exported_classes)
        for name in symbols.value_locals:
            declared_value_by_name.setdefault(name, set()).add(path)
        for name in symbols.type_locals:
            declared_type_by_name.setdefault(name, set()).add(path)
        for name in symbols.class_locals:
            declared_class_by_name.setdefault(name, set()).add(path)

    for decl in program.declarations:
        line = getattr(decl, "line", None)
        if line is None:
            continue
        module_info = _module_for_line(bundle, line)
        if module_info is None:
            continue
        symbols = module_symbols[module_info.path]
        if isinstance(decl, ast.FnDecl) or isinstance(decl, ast.LetDecl):
            decl.name = symbols.value_locals[decl.name]
        elif isinstance(decl, ast.TypeDecl):
            decl.name = symbols.type_locals[decl.name]
            for ctor in decl.constructors:
                ctor.name = symbols.value_locals[ctor.name]
        elif isinstance(decl, ast.RecordDecl):
            decl.name = symbols.type_locals[decl.name]
        elif isinstance(decl, ast.ClassDecl):
            decl.name = symbols.class_locals[decl.name]

    implicit_prelude_path = _implicit_prelude_path()
    warnings: list[CompilerWarning] = []
    seen_warnings: set[tuple[Path, int, int, str]] = set()

    def _has_implicit_prelude_provider(paths: set[Path]) -> bool:
        return implicit_prelude_path in paths

    def _warn_for_value_use(
        target_path: Path,
        symbol: str,
        node: object | None,
        current_module_path: Path,
    ) -> None:
        if target_path == current_module_path:
            return
        annotations = module_symbols[target_path].value_annotations.get(symbol, ())
        for annotation in annotations:
            warning = _node_warning(bundle, node, _format_decl_annotation_warning(symbol, annotation))
            if warning is None:
                continue
            key = (warning.path, warning.line, warning.column, warning.message)
            if key in seen_warnings:
                continue
            seen_warnings.add(key)
            warnings.append(warning)

    def resolve_value_name(name: str, node: object | None = None) -> str:
        line = getattr(node, "line", None)
        if line is None:
            return name
        module_info = _module_for_line(bundle, line)
        if module_info is None:
            return name
        symbols = module_symbols[module_info.path]
        aliases, unqualified_values, unqualified_value_sources, _, _, imported_methods = _imports_for_module(
            module_info, bundle, module_symbols
        )

        if "." in name:
            alias, symbol = name.split(".", 1)
            target_path = aliases.get(alias)
            if target_path is None:
                raise _node_module_error(
                    bundle,
                    node,
                    f"Unknown import alias {alias!r}; available aliases: {_fmt_names(set(aliases))}",
                )
            target_symbols = module_symbols[target_path]
            target = target_symbols.exported_values.get(symbol)
            if target is not None:
                _warn_for_value_use(target_path, symbol, node, module_info.path)
                return target
            for ctors in target_symbols.exported_type_constructors.values():
                target = ctors.get(symbol)
                if target is not None:
                    return target
            raise _node_module_error(
                bundle,
                node,
                f"Module {bundle.modules[target_path].header.module or str(target_path)!r} "
                f"does not export value {symbol!r}; "
                f"exported values: {_fmt_names(set(target_symbols.exported_values))}",
            )

        if name in public_builtin_values:
            return name
        if _is_stdlib_module_path(bundle, module_info.path) and name in stdlib_internal_builtin_values:
            return name
        if name in stdlib_internal_builtin_values:
            raise _node_module_error(
                bundle,
                node,
                f"Value {name!r} is internal; use the corresponding stdlib package wrapper instead.",
            )
        if name in symbols.value_locals:
            return symbols.value_locals[name]
        if name in symbols.method_locals or name in imported_methods:
            return name
        if name in unqualified_values:
            source_path = unqualified_value_sources.get(name)
            if source_path is not None:
                _warn_for_value_use(source_path, name, node, module_info.path)
            return unqualified_values[name]
        providers = declared_value_by_name.get(name, set())
        implicit_prelude_symbols = module_symbols.get(implicit_prelude_path)
        if implicit_prelude_symbols is not None and name in implicit_prelude_symbols.method_locals:
            return name
        if _has_implicit_prelude_provider(providers):
            return name
        if name in all_exported_values:
            providers = sorted(bundle.modules[path].header.module or str(path) for path in declared_value_by_name.get(name, set()))
            raise _node_module_error(
                bundle,
                node,
                f"Value {name!r} requires explicit import or qualification; "
                f"available from: {_fmt_names(providers)}. "
                f"Use `import module ({name})` or `import module` and qualify it.",
            )
        if providers and module_info.path not in providers:
            raise _node_module_error(
                bundle,
                node,
                f"Value {name!r} is not exported by any imported module; "
                f"it exists in: {_fmt_names(bundle.modules[path].header.module or str(path) for path in providers)}",
            )
        return name

    def resolve_type_name(name: str, node: object | None = None) -> str:
        line = getattr(node, "line", None)
        if line is None:
            return name
        module_info = _module_for_line(bundle, line)
        if module_info is None:
            return name
        symbols = module_symbols[module_info.path]
        aliases, _, _, unqualified_types, unqualified_classes, _ = _imports_for_module(
            module_info, bundle, module_symbols
        )

        if "." in name:
            alias, symbol = name.split(".", 1)
            target_path = aliases.get(alias)
            if target_path is None:
                raise _node_module_error(
                    bundle,
                    node,
                    f"Unknown import alias {alias!r}; available aliases: {_fmt_names(set(aliases))}",
                )
            target_symbols = module_symbols[target_path]
            target = target_symbols.exported_types.get(symbol) or target_symbols.exported_classes.get(symbol)
            if target is not None:
                return target
            raise _node_module_error(
                bundle,
                node,
                f"Module {bundle.modules[target_path].header.module or str(target_path)!r} "
                f"does not export type/class {symbol!r}; "
                f"exported types/classes: {_fmt_names(set(target_symbols.exported_types) | set(target_symbols.exported_classes))}",
            )

        if name in builtin_types:
            return name
        if name in symbols.type_locals:
            return symbols.type_locals[name]
        if name in symbols.class_locals:
            return symbols.class_locals[name]
        if name in unqualified_types:
            return unqualified_types[name]
        if name in unqualified_classes:
            return unqualified_classes[name]
        providers = declared_type_by_name.get(name, set()) | declared_class_by_name.get(name, set())
        if _has_implicit_prelude_provider(providers):
            return name
        if name in all_exported_types or name in all_exported_classes:
            providers = sorted(
                bundle.modules[path].header.module or str(path)
                for path in (declared_type_by_name.get(name, set()) | declared_class_by_name.get(name, set()))
            )
            raise _node_module_error(
                bundle,
                node,
                f"Type/class {name!r} requires explicit import or qualification; "
                f"available from: {_fmt_names(providers)}",
            )
        if providers and module_info.path not in providers:
            raise _node_module_error(
                bundle,
                node,
                f"Type/class {name!r} is not exported by any imported module; "
                f"it exists in: {_fmt_names(bundle.modules[path].header.module or str(path) for path in providers)}",
            )
        return name

    def walk_type(t: ast.TypeExpr, node: object | None = None) -> None:
        if isinstance(t, ast.TypeName):
            t.name = resolve_type_name(t.name, node)
            return
        if isinstance(t, ast.TypeApply):
            walk_type(t.base, node)
            walk_type(t.arg, node)
            return
        if isinstance(t, ast.TypeArrow):
            walk_type(t.left, node)
            walk_type(t.right, node)
            return
        if isinstance(t, ast.TypeEffect):
            walk_type(t.base, node)
            return
        if isinstance(t, ast.TypeArrow):
            walk_type(t.left, node)
            walk_type(t.right, node)

    def walk_pattern(p: ast.Pattern, node: object | None = None) -> None:
        if isinstance(p, ast.TuplePattern):
            for item in p.items:
                walk_pattern(item, node)
            return
        if isinstance(p, ast.ConstructorPattern):
            p.name = resolve_value_name(p.name, node or p)
            for arg in p.args:
                walk_pattern(arg, node)

    def _pattern_bindings(p: ast.Pattern) -> set[str]:
        if isinstance(p, ast.VarPattern):
            return {p.name}
        if isinstance(p, ast.TuplePattern):
            out: set[str] = set()
            for item in p.items:
                out |= _pattern_bindings(item)
            return out
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
                e.name = resolve_value_name(e.name, e)
            return
        if isinstance(e, ast.RecordExpr):
            e.type_name = resolve_type_name(e.type_name, e)
            for field in e.fields:
                walk_expr(field.value, e, current_scope)
            return
        if isinstance(e, ast.GetFieldExpr):
            walk_expr(e.record, e, current_scope)
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
        string_template_expr = getattr(ast, "StringTemplateExpr", None)
        if string_template_expr is not None and isinstance(e, string_template_expr):
            interp_part = getattr(ast, "InterpPart", None)
            if interp_part is not None:
                for part in e.parts:
                    if isinstance(part, interp_part):
                        walk_expr(part.expr, e, current_scope)
            return
        if isinstance(e, ast.LambdaExpr):
            lambda_scope = set(current_scope)
            lambda_scope |= {param.name for param in e.params}
            walk_expr(e.body, e, lambda_scope)
            return
        if isinstance(e, ast.TupleExpr):
            for item in e.items:
                walk_expr(item, e, current_scope)
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
            return
        do_expr = getattr(ast, "DoExpr", None)
        do_bind_step = getattr(ast, "DoBindStep", None)
        do_let_step = getattr(ast, "DoLetStep", None)
        do_expr_step = getattr(ast, "DoExprStep", None)
        if do_expr is not None and isinstance(e, do_expr):
            do_scope = set(current_scope)
            for step in e.steps:
                if do_bind_step is not None and isinstance(step, do_bind_step):
                    walk_expr(step.value, e, do_scope)
                    walk_pattern(step.pattern, e)
                    do_scope |= _pattern_bindings(step.pattern)
                elif do_let_step is not None and isinstance(step, do_let_step):
                    walk_expr(step.value, e, do_scope)
                    do_scope.add(step.name)
                elif do_expr_step is not None and isinstance(step, do_expr_step):
                    walk_expr(step.value, e, do_scope)
            return

    for decl in program.declarations:
        if isinstance(decl, ast.TypeDecl):
            for ctor in decl.constructors:
                for arg in ctor.args:
                    walk_type(arg, decl)
        elif isinstance(decl, ast.RecordDecl):
            for field in decl.fields:
                walk_type(field.type_expr, decl)
        elif isinstance(decl, ast.ClassDecl):
            for method in decl.methods:
                for param in method.params:
                    if param.type_expr is not None:
                        walk_type(param.type_expr, decl)
                walk_type(method.return_type, decl)
        elif isinstance(decl, ast.FnDecl):
            for param in decl.params:
                if param.type_expr is not None:
                    walk_type(param.type_expr, decl)
            if decl.return_type is not None:
                walk_type(decl.return_type, decl)
            for constraint in decl.constraints:
                constraint.class_name = resolve_type_name(constraint.class_name, decl)
                for arg in constraint.args:
                    walk_type(arg, decl)
            scope = {p.name for p in decl.params}
            walk_expr(decl.body, decl, scope)
        elif isinstance(decl, ast.LetDecl):
            walk_expr(decl.value, decl)
        elif isinstance(decl, ast.InstanceDecl):
            decl.constraint.class_name = resolve_type_name(decl.constraint.class_name, decl)
            for arg in decl.constraint.args:
                walk_type(arg, decl)
            for method in decl.methods:
                for param in method.params:
                    if param.type_expr is not None:
                        walk_type(param.type_expr, decl)
                if method.return_type is not None:
                    walk_type(method.return_type, decl)
                scope = {p.name for p in method.params}
                walk_expr(method.body, decl, scope)
    return warnings
