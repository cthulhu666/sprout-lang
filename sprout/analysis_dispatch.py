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
from .interpreter import RuntimeError
from .module_loader import ModuleLoadError
from .parser import ParseError
from .surface_checks import SurfaceCheckError
from .tokenizer import TokenizeError
from .typeclass_lowering import TypeclassLoweringError
from .typechecker import TypeCheckError

__all__ = ["dispatch_request"]


_AnalysisError = (
    ParseError,
    TokenizeError,
    TypeCheckError,
    RuntimeError,
    ModuleLoadError,
    SurfaceCheckError,
    TypeclassLoweringError,
)


def _error(message: str) -> dict[str, object]:
    return {"error": message, "ok": False}


def _ok(value: object) -> dict[str, object]:
    return {"ok": True, "value": value}


def _require_string(payload: object, field: str) -> str:
    if not isinstance(payload, dict):
        raise ValueError("analysis service request must be a JSON object")
    value = payload.get(field)
    if not isinstance(value, str):
        raise ValueError(f"analysis service field `{field}` must be a string")
    return value


def _require_string_list(payload: object, field: str) -> list[str]:
    if not isinstance(payload, dict):
        raise ValueError("analysis service request must be a JSON object")
    value = payload.get(field)
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"analysis service field `{field}` must be a list of strings")
    return value


def dispatch_request(request: object) -> dict[str, object]:
    if not isinstance(request, dict):
        return _error("analysis service request must be a JSON object")
    op = request.get("op")
    try:
        if op == "check_source":
            check_source(_require_string(request, "module_source"))
            return _ok(None)
        if op == "type_of_in_source":
            module_source = _require_string(request, "module_source")
            expr = _require_string(request, "expr")
            return _ok(infer_type_in_source(module_source, expr))
        if op == "declared_names_in_source":
            return _ok(declared_names_in_source(_require_string(request, "module_source")))
        if op == "exported_names_in_source":
            return _ok(exported_names_in_source(_require_string(request, "module_source")))
        if op == "symbol_inventory_in_source":
            declared, imported, exported = symbol_inventory_in_source(_require_string(request, "module_source"))
            return _ok({"declared": declared, "imported": imported, "exported": exported})
        if op == "diagnostics_in_source":
            diagnostics = diagnostics_in_source(_require_string(request, "module_source"))
            return _ok(
                {
                    "messages": [message for message, _, _ in diagnostics],
                    "lines": [line for _, line, _ in diagnostics],
                    "columns": [column for _, _, column in diagnostics],
                }
            )
        if op == "symbol_locations_in_source":
            locations = symbol_locations_in_source(_require_string(request, "module_source"))
            return _ok(
                {
                    "categories": [category for category, _, _, _ in locations],
                    "names": [name for _, name, _, _ in locations],
                    "lines": [line for _, _, line, _ in locations],
                    "columns": [column for _, _, _, column in locations],
                }
            )
        if op == "instances_in_source":
            module_source = _require_string(request, "module_source")
            query = _require_string(request, "query")
            query_type, matches = instances_in_source(module_source, query)
            return _ok({"matches": matches, "query_type": query_type})
        if op == "eval_expr_in_source":
            module_source = _require_string(request, "module_source")
            expr = _require_string(request, "expr")
            return _ok(list(eval_expression_lines_in_source(module_source, expr)))
        if op == "complete_in_state":
            line_buffer = _require_string(request, "line_buffer")
            imports = _require_string_list(request, "imports")
            declarations = _require_string_list(request, "declarations")
            prefix, matches = completion_candidates_in_state(line_buffer, imports, declarations)
            return _ok({"matches": matches, "prefix": prefix})
    except ValueError as exc:
        return _error(str(exc))
    except _AnalysisError as exc:
        return _error(str(exc))
    return _error(f"unknown analysis service op `{op}`")
