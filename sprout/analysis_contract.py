from __future__ import annotations

from typing import Final

OP_CHECK_SOURCE: Final = "check_source"
OP_TYPE_OF_IN_SOURCE: Final = "type_of_in_source"
OP_DECLARED_NAMES_IN_SOURCE: Final = "declared_names_in_source"
OP_EXPORTED_NAMES_IN_SOURCE: Final = "exported_names_in_source"
OP_SYMBOL_INVENTORY_IN_SOURCE: Final = "symbol_inventory_in_source"
OP_DIAGNOSTICS_IN_SOURCE: Final = "diagnostics_in_source"
OP_SYMBOL_LOCATIONS_IN_SOURCE: Final = "symbol_locations_in_source"
OP_INSTANCES_IN_SOURCE: Final = "instances_in_source"
OP_EVAL_EXPR_IN_SOURCE: Final = "eval_expr_in_source"
OP_COMPLETE_IN_STATE: Final = "complete_in_state"

__all__ = [
    "OP_CHECK_SOURCE",
    "OP_COMPLETE_IN_STATE",
    "OP_DECLARED_NAMES_IN_SOURCE",
    "OP_DIAGNOSTICS_IN_SOURCE",
    "OP_EVAL_EXPR_IN_SOURCE",
    "OP_EXPORTED_NAMES_IN_SOURCE",
    "OP_INSTANCES_IN_SOURCE",
    "OP_SYMBOL_INVENTORY_IN_SOURCE",
    "OP_SYMBOL_LOCATIONS_IN_SOURCE",
    "OP_TYPE_OF_IN_SOURCE",
    "request_check_source",
    "request_complete_in_state",
    "request_eval_expr_in_source",
    "request_instances_in_source",
    "request_type_of_in_source",
]


def request_check_source(module_source: str) -> dict[str, object]:
    return {"op": OP_CHECK_SOURCE, "module_source": module_source}


def request_type_of_in_source(module_source: str, expr: str) -> dict[str, object]:
    return {"op": OP_TYPE_OF_IN_SOURCE, "module_source": module_source, "expr": expr}


def request_instances_in_source(module_source: str, query: str) -> dict[str, object]:
    return {"op": OP_INSTANCES_IN_SOURCE, "module_source": module_source, "query": query}


def request_eval_expr_in_source(module_source: str, expr: str) -> dict[str, object]:
    return {"op": OP_EVAL_EXPR_IN_SOURCE, "module_source": module_source, "expr": expr}


def request_complete_in_state(
    line_buffer: str,
    imports: list[str],
    declarations: list[str],
) -> dict[str, object]:
    return {
        "op": OP_COMPLETE_IN_STATE,
        "line_buffer": line_buffer,
        "imports": imports,
        "declarations": declarations,
    }
