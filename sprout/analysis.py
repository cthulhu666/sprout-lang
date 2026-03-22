from __future__ import annotations

import io
import re
from dataclasses import dataclass
from pathlib import Path
import tempfile
from typing import Literal

from . import ast
from .interpreter import RuntimeError, run_program
from .module_loader import (
    MODULE_COMPAT_TYPES,
    ModuleBundle,
    ModuleLoadError,
    _build_module_symbols,
    _module_for_line,
    _namespace_alias_for_import,
    _resolve_module,
    _source_location_for_bundle_line,
    load_module_bundle,
    resolve_program_names,
)
from .parser import ParseError, parse
from .surface_checks import SurfaceCheckError, validate_public_surface
from .tokenizer import TokenizeError
from .typeclass_lowering import TypeclassLoweringError, lower_typeclasses
from .typechecker import InferState, TypeCheckError, parse_type_expr, typecheck_program, unify

__all__ = [
    "check_source",
    "declared_names_in_source",
    "diagnostics_in_source",
    "eval_expression_lines_in_source",
    "exported_names_in_source",
    "infer_type_in_source",
    "instances_in_source",
    "SourceLocation",
    "SymbolMetadata",
    "symbol_metadata_in_source",
    "symbol_locations_in_source",
    "symbol_inventory_in_source",
]


@dataclass(frozen=True)
class SourceLocation:
    path: Path
    line: int
    column: int


@dataclass(frozen=True)
class SymbolMetadata:
    visible_name: str
    kind: Literal["value", "type", "constructor", "class", "module_alias"]
    canonical_name: str | None
    origin_module: str | None
    location: SourceLocation
    introduced_via: Literal["declared", "imported", "namespace"]
    exported: bool
    imported_from_module: str | None = None


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


def _leaf_name(name: str) -> str:
    return name.rsplit(".", 1)[-1]


def _canonical_name(name: str | None, module_name: str | None) -> str | None:
    if name is None or module_name is None:
        return name
    repeated = f"{module_name}.{module_name}."
    if name.startswith(repeated):
        return name[len(module_name) + 1 :]
    return name


def _module_declared_symbol_locations(
    tree: ast.Program,
    bundle: ModuleBundle,
    module_path: Path,
) -> list[tuple[str, str, int, int]]:
    entries: list[tuple[str, str, int, int]] = []
    for decl in tree.declarations:
        line = getattr(decl, "line", None)
        if line is None:
            continue
        module_info = _module_for_line(bundle, line)
        if module_info is None or module_info.path != module_path:
            continue
        if isinstance(decl, (ast.FnDecl, ast.LetDecl)):
            location = _source_location_for_bundle_line(bundle, line, getattr(decl, "column", 1))
            if location is not None:
                _, source_line, source_column = location
                entries.append(("value", _leaf_name(decl.name), source_line, source_column))
        elif isinstance(decl, ast.TypeDecl):
            type_location = _source_location_for_bundle_line(bundle, line, getattr(decl, "column", 1))
            if type_location is not None:
                _, source_line, source_column = type_location
                entries.append(("type", _leaf_name(decl.name), source_line, source_column))
            for ctor in decl.constructors:
                ctor_line = getattr(ctor, "line", line)
                ctor_column = getattr(ctor, "column", getattr(decl, "column", 1))
                ctor_location = _source_location_for_bundle_line(bundle, ctor_line, ctor_column)
                if ctor_location is not None:
                    _, source_line, source_column = ctor_location
                    entries.append(("constructor", _leaf_name(ctor.name), source_line, source_column))
        elif isinstance(decl, ast.ClassDecl):
            location = _source_location_for_bundle_line(bundle, line, getattr(decl, "column", 1))
            if location is not None:
                _, source_line, source_column = location
                entries.append(("class", _leaf_name(decl.name), source_line, source_column))
    return sorted(entries, key=lambda entry: (entry[2], entry[3], entry[0], entry[1]))


def _mapped_source_location(bundle: ModuleBundle, line: int, column: int = 1) -> SourceLocation | None:
    location = _source_location_for_bundle_line(bundle, line, column)
    if location is None:
        return None
    path, source_line, source_column = location
    return SourceLocation(path=path, line=source_line, column=source_column)


def _module_symbol_metadata(
    tree: ast.Program,
    bundle: ModuleBundle,
    module_path: Path,
) -> list[SymbolMetadata]:
    module_info = bundle.modules[module_path]
    module_name = module_info.header.module
    module_symbols = _build_module_symbols(tree, bundle)
    local_symbols = module_symbols[module_path]
    exported_names = {_leaf_name(name) for name in module_info.exported}
    exported_ctor_types = {_leaf_name(name) for name in module_info.exported_type_constructors}
    entries: list[SymbolMetadata] = []

    for decl in tree.declarations:
        line = getattr(decl, "line", None)
        if line is None:
            continue
        owner = _module_for_line(bundle, line)
        if owner is None or owner.path != module_path:
            continue
        decl_location = _mapped_source_location(bundle, line, getattr(decl, "column", 1))
        if decl_location is None:
            continue
        if isinstance(decl, ast.FnDecl) or isinstance(decl, ast.LetDecl):
            leaf = _leaf_name(decl.name)
            entries.append(
                SymbolMetadata(
                    visible_name=leaf,
                    kind="value",
                    canonical_name=_canonical_name(local_symbols.value_locals.get(decl.name), module_name),
                    origin_module=module_name,
                    location=decl_location,
                    introduced_via="declared",
                    exported=leaf in exported_names,
                )
            )
        elif isinstance(decl, ast.TypeDecl):
            leaf = _leaf_name(decl.name)
            entries.append(
                SymbolMetadata(
                    visible_name=leaf,
                    kind="type",
                    canonical_name=_canonical_name(local_symbols.type_locals.get(decl.name), module_name),
                    origin_module=module_name,
                    location=decl_location,
                    introduced_via="declared",
                    exported=leaf in exported_names,
                )
            )
            for ctor in decl.constructors:
                ctor_leaf = _leaf_name(ctor.name)
                ctor_location = _mapped_source_location(
                    bundle,
                    getattr(ctor, "line", line),
                    getattr(ctor, "column", getattr(decl, "column", 1)),
                )
                if ctor_location is None:
                    continue
                entries.append(
                    SymbolMetadata(
                        visible_name=ctor_leaf,
                        kind="constructor",
                        canonical_name=_canonical_name(local_symbols.value_locals.get(ctor.name), module_name),
                        origin_module=module_name,
                        location=ctor_location,
                        introduced_via="declared",
                        exported=leaf in exported_ctor_types,
                    )
                )
        elif isinstance(decl, ast.ClassDecl):
            leaf = _leaf_name(decl.name)
            entries.append(
                SymbolMetadata(
                    visible_name=leaf,
                    kind="class",
                    canonical_name=_canonical_name(local_symbols.class_locals.get(decl.name), module_name),
                    origin_module=module_name,
                    location=decl_location,
                    introduced_via="declared",
                    exported=leaf in exported_names,
                )
            )

    for imp in module_info.header.imports:
        import_location = SourceLocation(path=module_path, line=imp.line, column=imp.column)
        imp_path = _resolve_module(imp.module, module_path)
        imp_symbols = module_symbols[imp_path]
        imported_values = {_leaf_name(name): canonical for name, canonical in imp_symbols.exported_values.items()}
        imported_types = {_leaf_name(name): canonical for name, canonical in imp_symbols.exported_types.items()}
        imported_classes = {_leaf_name(name): canonical for name, canonical in imp_symbols.exported_classes.items()}
        imported_type_ctors = {
            _leaf_name(type_name): {_leaf_name(ctor_name): ctor_target for ctor_name, ctor_target in ctors.items()}
            for type_name, ctors in imp_symbols.exported_type_constructors.items()
        }
        namespace_alias = _namespace_alias_for_import(imp)
        if namespace_alias is not None:
            entries.append(
                SymbolMetadata(
                    visible_name=namespace_alias,
                    kind="module_alias",
                    canonical_name=None,
                    origin_module=imp.module,
                    location=import_location,
                    introduced_via="namespace",
                    exported=False,
                    imported_from_module=imp.module,
                )
            )
        if imp.imported_names is None:
            continue
        for name in imp.imported_names:
            matched = False
            if name in imported_values:
                matched = True
                entries.append(
                    SymbolMetadata(
                        visible_name=name,
                        kind="value",
                        canonical_name=_canonical_name(imported_values[name], imp.module),
                        origin_module=imp.module,
                        location=import_location,
                        introduced_via="imported",
                        exported=False,
                        imported_from_module=imp.module,
                    )
                )
            if name in imported_types:
                matched = True
                entries.append(
                    SymbolMetadata(
                        visible_name=name,
                        kind="type",
                        canonical_name=_canonical_name(imported_types[name], imp.module),
                        origin_module=imp.module,
                        location=import_location,
                        introduced_via="imported",
                        exported=False,
                        imported_from_module=imp.module,
                    )
                )
                for ctor_name, ctor_target in imported_type_ctors.get(name, {}).items():
                    entries.append(
                        SymbolMetadata(
                            visible_name=ctor_name,
                            kind="constructor",
                            canonical_name=_canonical_name(ctor_target, imp.module),
                            origin_module=imp.module,
                            location=import_location,
                            introduced_via="imported",
                            exported=False,
                            imported_from_module=imp.module,
                        )
                    )
            if name in imported_classes:
                matched = True
                entries.append(
                    SymbolMetadata(
                        visible_name=name,
                        kind="class",
                        canonical_name=_canonical_name(imported_classes[name], imp.module),
                        origin_module=imp.module,
                        location=import_location,
                        introduced_via="imported",
                        exported=False,
                        imported_from_module=imp.module,
                    )
                )
            if not matched:
                entries.append(
                    SymbolMetadata(
                        visible_name=name,
                        kind="value",
                        canonical_name=None,
                        origin_module=imp.module,
                        location=import_location,
                        introduced_via="imported",
                        exported=False,
                        imported_from_module=imp.module,
                    )
                )

    return sorted(
        entries,
        key=lambda item: (
            item.location.line,
            item.location.column,
            item.introduced_via,
            item.kind,
            item.visible_name,
        ),
    )


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


def _diagnostic_entry(exc: Exception, bundle: ModuleBundle | None = None) -> tuple[str, int, int]:
    message = str(exc)
    line = 0
    column = 0
    context_match = re.search(r"--> [^:\n]+:(\d+):(\d+)", message)
    inline_match = re.search(r" at (\d+):(\d+)(?:\b|,)", message)
    if context_match is not None:
        line = int(context_match.group(1))
        column = int(context_match.group(2))
        message = message.splitlines()[0]
    elif inline_match is not None:
        line = int(inline_match.group(1))
        column = int(inline_match.group(2))
        first_line = message.splitlines()[0]
        message = re.sub(r" at \d+:\d+", "", first_line, count=1)
    else:
        message = message.splitlines()[0]
    if bundle is not None and line > 0:
        location = _source_location_for_bundle_line(bundle, line, column)
        if location is not None:
            _, line, column = location
    return (message, line, column)


def infer_type_in_source(source: str, expr: str) -> str:
    name = "__repl_source_value"
    _, types = _parse_and_check_source(source, [f"let {name} = {expr}"])
    return _lookup_type(types, name)


def check_source(source: str) -> None:
    _parse_and_check_source(source)


def declared_names_in_source(source: str) -> list[str]:
    tree, _ = _parse_and_check_source(source)
    return sorted(_declared_names_from_tree(tree))


def exported_names_in_source(source: str) -> list[str]:
    composed = _compose_snapshot_source(source)
    with tempfile.TemporaryDirectory() as tmpdir:
        temp_path = (Path(tmpdir) / "repl_session.sprout").resolve()
        temp_path.write_text(composed, encoding="utf-8")
        bundle = load_module_bundle(temp_path)
        tree = parse(bundle.source)
        resolve_program_names(tree, bundle)
        validate_public_surface(tree, bundle)
        typecheck_program(tree)
        module_info = bundle.modules[temp_path]
        exported = set(module_info.exported)
        export_ctor_types = set(module_info.exported_type_constructors)
        module_name = module_info.header.module or ""
        for decl in tree.declarations:
            if _module_for_line(bundle, getattr(decl, "line", -1)) != module_info:
                continue
            if isinstance(decl, ast.TypeDecl) and decl.name.rsplit(".", 1)[-1] in export_ctor_types:
                for ctor in decl.constructors:
                    exported.add(ctor.name.rsplit(".", 1)[-1])
        for ctors in MODULE_COMPAT_TYPES.get(module_name, {}).values():
            exported.update(ctors)
        return sorted(exported)


def symbol_inventory_in_source(source: str) -> tuple[list[str], list[str], list[str]]:
    composed = _compose_snapshot_source(source)
    with tempfile.TemporaryDirectory() as tmpdir:
        temp_path = (Path(tmpdir) / "repl_session.sprout").resolve()
        temp_path.write_text(composed, encoding="utf-8")
        bundle = load_module_bundle(temp_path)
        tree = parse(bundle.source)
        resolve_program_names(tree, bundle)
        validate_public_surface(tree, bundle)
        typecheck_program(tree)
        module_info = bundle.modules[temp_path]
        declared = sorted(_declared_names_from_tree(tree))
        module_symbols = _build_module_symbols(tree, bundle)
        imported: set[str] = set()
        for imp in module_info.header.imports:
            namespace_alias = _namespace_alias_for_import(imp)
            if namespace_alias is not None:
                imported.add(namespace_alias)
            if imp.imported_names is not None:
                imported.update(imp.imported_names)
                imp_path = _resolve_module(imp.module, temp_path)
                imp_symbols = module_symbols[imp_path]
                for name in imp.imported_names:
                    imported.update(imp_symbols.exported_type_constructors.get(name, {}))
        exported = exported_names_in_source(source)
        return declared, sorted(imported), exported


def symbol_locations_in_source(source: str) -> list[tuple[str, str, int, int]]:
    composed = _compose_snapshot_source(source)
    with tempfile.TemporaryDirectory() as tmpdir:
        temp_path = (Path(tmpdir) / "repl_session.sprout").resolve()
        temp_path.write_text(composed, encoding="utf-8")
        bundle = load_module_bundle(temp_path)
        tree = parse(bundle.source)
        resolve_program_names(tree, bundle)
        validate_public_surface(tree, bundle)
        typecheck_program(tree)
        return _module_declared_symbol_locations(tree, bundle, temp_path)


def symbol_metadata_in_source(source: str) -> list[SymbolMetadata]:
    composed = _compose_snapshot_source(source)
    with tempfile.TemporaryDirectory() as tmpdir:
        temp_path = (Path(tmpdir) / "repl_session.sprout").resolve()
        temp_path.write_text(composed, encoding="utf-8")
        bundle = load_module_bundle(temp_path)
        tree = parse(bundle.source)
        resolve_program_names(tree, bundle)
        validate_public_surface(tree, bundle)
        typecheck_program(tree)
        return _module_symbol_metadata(tree, bundle, temp_path)


def diagnostics_in_source(source: str) -> list[tuple[str, int, int]]:
    composed = _compose_snapshot_source(source)
    with tempfile.TemporaryDirectory() as tmpdir:
        temp_path = Path(tmpdir) / "repl_session.sprout"
        temp_path.write_text(composed, encoding="utf-8")
        bundle = load_module_bundle(temp_path)
        try:
            tree = parse(bundle.source)
            resolve_program_names(tree, bundle)
            validate_public_surface(tree, bundle)
            typecheck_program(tree)
        except (
            ParseError,
            TokenizeError,
            TypeCheckError,
            ModuleLoadError,
            SurfaceCheckError,
            TypeclassLoweringError,
        ) as exc:
            return [_diagnostic_entry(exc, bundle)]
        return []


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
