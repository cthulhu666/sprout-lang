from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .analysis_backend import AnalysisBackend
from .analysis_completion_backend import python_completion_complete_in_state
from .analysis_execution_backend import (
    python_execution_check_source,
    python_execution_eval_expr_in_source,
    python_execution_instances_in_source,
    python_execution_type_of_in_source,
)
from .analysis_snapshot_backend import (
    python_snapshot_declared_names_in_source,
    python_snapshot_diagnostics_in_source,
    python_snapshot_exported_names_in_source,
    python_snapshot_symbol_inventory_in_source,
    python_snapshot_symbol_locations_in_source,
)

__all__ = [
    "DEFAULT_ANALYSIS_BACKEND",
    "default_analysis_backend",
    "python_backend_check_source",
    "python_backend_complete_in_state",
    "python_backend_declared_names_in_source",
    "python_backend_diagnostics_in_source",
    "python_backend_eval_expr_in_source",
    "python_backend_exported_names_in_source",
    "python_backend_instances_in_source",
    "python_backend_symbol_inventory_in_source",
    "python_backend_symbol_locations_in_source",
    "python_backend_type_of_in_source",
]


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


def python_backend_check_source(module_source: str) -> None:
    python_execution_check_source(module_source)


def python_backend_type_of_in_source(module_source: str, expr: str) -> str:
    return python_execution_type_of_in_source(module_source, expr)


def python_backend_declared_names_in_source(module_source: str) -> list[str]:
    return python_snapshot_declared_names_in_source(module_source)


def python_backend_exported_names_in_source(module_source: str) -> list[str]:
    return python_snapshot_exported_names_in_source(module_source)


def python_backend_symbol_inventory_in_source(module_source: str) -> tuple[list[str], list[str], list[str]]:
    return python_snapshot_symbol_inventory_in_source(module_source)


def python_backend_diagnostics_in_source(module_source: str) -> list[tuple[str, int, int]]:
    return python_snapshot_diagnostics_in_source(module_source)


def python_backend_symbol_locations_in_source(module_source: str) -> list[tuple[str, str, int, int]]:
    return python_snapshot_symbol_locations_in_source(module_source)


def python_backend_instances_in_source(module_source: str, query: str) -> tuple[str, list[str]]:
    return python_execution_instances_in_source(module_source, query)


def python_backend_eval_expr_in_source(module_source: str, expr: str) -> tuple[str, ...]:
    return python_execution_eval_expr_in_source(module_source, expr)


def python_backend_complete_in_state(
    line_buffer: str,
    imports: list[str],
    declarations: list[str],
) -> tuple[str, list[str]]:
    return python_completion_complete_in_state(line_buffer, imports, declarations)


DEFAULT_ANALYSIS_BACKEND: AnalysisBackend = _FunctionAnalysisBackend(
    check_source=python_backend_check_source,
    type_of_in_source=python_backend_type_of_in_source,
    declared_names_in_source=python_backend_declared_names_in_source,
    exported_names_in_source=python_backend_exported_names_in_source,
    symbol_inventory_in_source=python_backend_symbol_inventory_in_source,
    diagnostics_in_source=python_backend_diagnostics_in_source,
    symbol_locations_in_source=python_backend_symbol_locations_in_source,
    instances_in_source=python_backend_instances_in_source,
    eval_expr_in_source=python_backend_eval_expr_in_source,
    complete_in_state=python_backend_complete_in_state,
)


def default_analysis_backend() -> AnalysisBackend:
    return DEFAULT_ANALYSIS_BACKEND
