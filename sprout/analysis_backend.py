from __future__ import annotations

from .analysis import (
    check_source,
    completion_candidates_in_state,
    declared_names_in_source,
    diagnostics_in_source,
    eval_expression_lines_in_source,
    exported_names_in_source,
    infer_type_in_source,
    instances_in_source,
    symbol_locations_in_source,
    symbol_inventory_in_source,
)

__all__ = [
    "backend_check_source",
    "backend_complete_in_state",
    "backend_declared_names_in_source",
    "backend_diagnostics_in_source",
    "backend_eval_expr_in_source",
    "backend_exported_names_in_source",
    "backend_instances_in_source",
    "backend_symbol_inventory_in_source",
    "backend_symbol_locations_in_source",
    "backend_type_of_in_source",
]


def backend_check_source(module_source: str) -> None:
    check_source(module_source)


def backend_type_of_in_source(module_source: str, expr: str) -> str:
    return infer_type_in_source(module_source, expr)


def backend_declared_names_in_source(module_source: str) -> list[str]:
    return declared_names_in_source(module_source)


def backend_exported_names_in_source(module_source: str) -> list[str]:
    return exported_names_in_source(module_source)


def backend_symbol_inventory_in_source(module_source: str) -> tuple[list[str], list[str], list[str]]:
    return symbol_inventory_in_source(module_source)


def backend_diagnostics_in_source(module_source: str) -> list[tuple[str, int, int]]:
    return diagnostics_in_source(module_source)


def backend_symbol_locations_in_source(module_source: str) -> list[tuple[str, str, int, int]]:
    return symbol_locations_in_source(module_source)


def backend_instances_in_source(module_source: str, query: str) -> tuple[str, list[str]]:
    return instances_in_source(module_source, query)


def backend_eval_expr_in_source(module_source: str, expr: str) -> tuple[str, ...]:
    return eval_expression_lines_in_source(module_source, expr)


def backend_complete_in_state(
    line_buffer: str,
    imports: list[str],
    declarations: list[str],
) -> tuple[str, list[str]]:
    return completion_candidates_in_state(line_buffer, imports, declarations)
