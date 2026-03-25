from __future__ import annotations

from typing import Protocol

__all__ = [
    "AnalysisBackend",
    "AnalysisCompletionBackend",
    "AnalysisExecutionBackend",
    "AnalysisSnapshotBackend",
]


class AnalysisSnapshotBackend(Protocol):
    def declared_names_in_source(self, module_source: str) -> list[str]: ...
    def exported_names_in_source(self, module_source: str) -> list[str]: ...
    def symbol_inventory_in_source(self, module_source: str) -> tuple[list[str], list[str], list[str]]: ...
    def diagnostics_in_source(self, module_source: str) -> list[tuple[str, int, int]]: ...
    def symbol_locations_in_source(self, module_source: str) -> list[tuple[str, str, int, int]]: ...


class AnalysisExecutionBackend(Protocol):
    def check_source(self, module_source: str) -> None: ...
    def type_of_in_source(self, module_source: str, expr: str) -> str: ...
    def instances_in_source(self, module_source: str, query: str) -> tuple[str, list[str]]: ...
    def eval_expr_in_source(self, module_source: str, expr: str) -> tuple[str, ...]: ...


class AnalysisCompletionBackend(Protocol):
    def complete_in_state(self, line_buffer: str, imports: list[str], declarations: list[str]) -> tuple[str, list[str]]: ...


class AnalysisBackend(
    AnalysisSnapshotBackend,
    AnalysisExecutionBackend,
    AnalysisCompletionBackend,
    Protocol,
):
    pass
