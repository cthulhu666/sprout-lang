from __future__ import annotations

from pathlib import Path
import tempfile

from . import ast
from .module_loader import (
    MODULE_COMPAT_TYPES,
    _build_module_symbols,
    _module_for_line,
    _namespace_alias_for_import,
    _resolve_module,
    load_module_bundle,
    resolve_program_names,
)
from .parser import parse
from .surface_checks import validate_public_surface
from .typechecker import typecheck_program

__all__ = [
    "python_snapshot_declared_names_in_source",
    "python_snapshot_diagnostics_in_source",
    "python_snapshot_exported_names_in_source",
    "python_snapshot_symbol_inventory_in_source",
    "python_snapshot_symbol_locations_in_source",
]


def _compose_snapshot_source(source: str) -> str:
    return source.strip()


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


def python_snapshot_declared_names_in_source(module_source: str) -> list[str]:
    composed = _compose_snapshot_source(module_source)
    with tempfile.TemporaryDirectory() as tmpdir:
        temp_path = (Path(tmpdir) / "repl_session.sprout").resolve()
        temp_path.write_text(composed, encoding="utf-8")
        bundle = load_module_bundle(temp_path)
        tree = parse(bundle.source)
        resolve_program_names(tree, bundle)
        validate_public_surface(tree, bundle)
        typecheck_program(tree)
        return sorted(_declared_names_from_tree(tree))


def python_snapshot_exported_names_in_source(module_source: str) -> list[str]:
    composed = _compose_snapshot_source(module_source)
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


def python_snapshot_symbol_inventory_in_source(module_source: str) -> tuple[list[str], list[str], list[str]]:
    composed = _compose_snapshot_source(module_source)
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
        exported = python_snapshot_exported_names_in_source(module_source)
        return declared, sorted(imported), exported


def python_snapshot_diagnostics_in_source(module_source: str) -> list[tuple[str, int, int]]:
    from .analysis import diagnostics_in_source

    return diagnostics_in_source(module_source)


def python_snapshot_symbol_locations_in_source(module_source: str) -> list[tuple[str, str, int, int]]:
    from .analysis import symbol_locations_in_source

    return symbol_locations_in_source(module_source)
