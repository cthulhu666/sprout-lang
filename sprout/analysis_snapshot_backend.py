from __future__ import annotations

from .analysis import (
    declared_names_in_source,
    diagnostics_in_source,
    exported_names_in_source,
    symbol_locations_in_source,
    symbol_inventory_in_source,
)

__all__ = [
    "python_snapshot_declared_names_in_source",
    "python_snapshot_diagnostics_in_source",
    "python_snapshot_exported_names_in_source",
    "python_snapshot_symbol_inventory_in_source",
    "python_snapshot_symbol_locations_in_source",
]


def python_snapshot_declared_names_in_source(module_source: str) -> list[str]:
    return declared_names_in_source(module_source)


def python_snapshot_exported_names_in_source(module_source: str) -> list[str]:
    return exported_names_in_source(module_source)


def python_snapshot_symbol_inventory_in_source(module_source: str) -> tuple[list[str], list[str], list[str]]:
    return symbol_inventory_in_source(module_source)


def python_snapshot_diagnostics_in_source(module_source: str) -> list[tuple[str, int, int]]:
    return diagnostics_in_source(module_source)


def python_snapshot_symbol_locations_in_source(module_source: str) -> list[tuple[str, str, int, int]]:
    return symbol_locations_in_source(module_source)
