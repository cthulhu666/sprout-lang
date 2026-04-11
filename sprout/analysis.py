from __future__ import annotations

from .analysis_completion_backend import (
    completion_candidates_in_state,
    completion_matches_in_state,
)
from .analysis_execution_backend import (
    check_source,
    eval_expression_lines_in_source,
    infer_type_in_source,
    instances_in_source,
)
from .analysis_snapshot_backend import (
    Diagnostic,
    SourceLocation,
    declared_names_in_source as snapshot_declared_names_in_source,
    diagnostics_in_source as snapshot_diagnostics_in_source,
    exported_names_in_source as snapshot_exported_names_in_source,
    SymbolMetadata,
    structured_diagnostics_in_source as snapshot_structured_diagnostics_in_source,
    symbol_inventory_in_source as snapshot_symbol_inventory_in_source,
    symbol_locations_in_source as snapshot_symbol_locations_in_source,
    symbol_metadata_in_source as snapshot_symbol_metadata_in_source,
)

__all__ = [
    "check_source",
    "completion_candidates_in_state",
    "completion_matches_in_state",
    "analysis_complete_in_state",
    "analysis_eval_expr_in_source",
    "declared_names_in_source",
    "Diagnostic",
    "diagnostics_in_source",
    "eval_expression_lines_in_source",
    "exported_names_in_source",
    "infer_type_in_source",
    "instances_in_source",
    "SourceLocation",
    "SymbolMetadata",
    "structured_diagnostics_in_source",
    "symbol_metadata_in_source",
    "symbol_locations_in_source",
    "symbol_inventory_in_source",
]


def analysis_eval_expr_in_source(source: str, expr: str) -> tuple[str, ...]:
    return eval_expression_lines_in_source(source, expr)


def declared_names_in_source(source: str) -> list[str]:
    return snapshot_declared_names_in_source(source)


def exported_names_in_source(source: str) -> list[str]:
    return snapshot_exported_names_in_source(source)


def symbol_inventory_in_source(source: str) -> tuple[list[str], list[str], list[str]]:
    return snapshot_symbol_inventory_in_source(source)


def symbol_locations_in_source(source: str) -> list[tuple[str, str, int, int]]:
    return snapshot_symbol_locations_in_source(source)


def symbol_metadata_in_source(source: str) -> list[SymbolMetadata]:
    return snapshot_symbol_metadata_in_source(source)


def structured_diagnostics_in_source(source: str) -> list[Diagnostic]:
    return snapshot_structured_diagnostics_in_source(source)


def diagnostics_in_source(source: str) -> list[tuple[str, int, int]]:
    return snapshot_diagnostics_in_source(source)


def analysis_complete_in_state(
    line_buffer: str,
    imports: list[str],
    declarations: list[str],
) -> tuple[str, list[str]]:
    return completion_candidates_in_state(line_buffer, imports, declarations)
