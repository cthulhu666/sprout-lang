from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol

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
    "AnalysisBackend",
    "DEFAULT_ANALYSIS_BACKEND",
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


class AnalysisBackend(Protocol):
    def check_source(self, module_source: str) -> None: ...
    def type_of_in_source(self, module_source: str, expr: str) -> str: ...
    def declared_names_in_source(self, module_source: str) -> list[str]: ...
    def exported_names_in_source(self, module_source: str) -> list[str]: ...
    def symbol_inventory_in_source(self, module_source: str) -> tuple[list[str], list[str], list[str]]: ...
    def diagnostics_in_source(self, module_source: str) -> list[tuple[str, int, int]]: ...
    def symbol_locations_in_source(self, module_source: str) -> list[tuple[str, str, int, int]]: ...
    def instances_in_source(self, module_source: str, query: str) -> tuple[str, list[str]]: ...
    def eval_expr_in_source(self, module_source: str, expr: str) -> tuple[str, ...]: ...
    def complete_in_state(self, line_buffer: str, imports: list[str], declarations: list[str]) -> tuple[str, list[str]]: ...


@dataclass(frozen=True)
class _FunctionAnalysisBackend:
    check_source: Callable[[str], None]
    type_of_in_source: Callable[[str, str], str]
    declared_names_in_source: Callable[[str], list[str]]
    exported_names_in_source: Callable[[str], list[str]]
    symbol_inventory_in_source: Callable[[str], tuple[list[str], list[str], list[str]]]
    diagnostics_in_source: Callable[[str], list[tuple[str, int, int]]]
    symbol_locations_in_source: Callable[[str], list[tuple[str, str, int, int]]]
    instances_in_source: Callable[[str, str], tuple[str, list[str]]]
    eval_expr_in_source: Callable[[str, str], tuple[str, ...]]
    complete_in_state: Callable[[str, list[str], list[str]], tuple[str, list[str]]]


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


DEFAULT_ANALYSIS_BACKEND: AnalysisBackend = _FunctionAnalysisBackend(
    check_source=backend_check_source,
    type_of_in_source=backend_type_of_in_source,
    declared_names_in_source=backend_declared_names_in_source,
    exported_names_in_source=backend_exported_names_in_source,
    symbol_inventory_in_source=backend_symbol_inventory_in_source,
    diagnostics_in_source=backend_diagnostics_in_source,
    symbol_locations_in_source=backend_symbol_locations_in_source,
    instances_in_source=backend_instances_in_source,
    eval_expr_in_source=backend_eval_expr_in_source,
    complete_in_state=backend_complete_in_state,
)
