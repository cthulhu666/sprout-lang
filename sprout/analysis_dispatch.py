from __future__ import annotations

from .analysis_backend import (
    DEFAULT_ANALYSIS_BACKEND,
    AnalysisBackend,
)
from .analysis_contract import (
    KEY_CATEGORIES,
    KEY_COLUMNS,
    KEY_DECLARED,
    KEY_EXPORTED,
    KEY_IMPORTED,
    KEY_LINES,
    KEY_MATCHES,
    KEY_MESSAGES,
    KEY_NAMES,
    KEY_PREFIX,
    KEY_QUERY_TYPE,
    OP_CHECK_SOURCE,
    OP_COMPLETE_IN_STATE,
    OP_DECLARED_NAMES_IN_SOURCE,
    OP_DIAGNOSTICS_IN_SOURCE,
    OP_EVAL_EXPR_IN_SOURCE,
    OP_EXPORTED_NAMES_IN_SOURCE,
    OP_INSTANCES_IN_SOURCE,
    OP_SYMBOL_INVENTORY_IN_SOURCE,
    OP_SYMBOL_LOCATIONS_IN_SOURCE,
    OP_TYPE_OF_IN_SOURCE,
    response_error,
    response_ok,
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
    return response_error(message)


def _ok(value: object) -> dict[str, object]:
    return response_ok(value)


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


def dispatch_request(
    request: object,
    backend: AnalysisBackend = DEFAULT_ANALYSIS_BACKEND,
) -> dict[str, object]:
    if not isinstance(request, dict):
        return _error("analysis service request must be a JSON object")
    op = request.get("op")
    try:
        if op == OP_CHECK_SOURCE:
            backend.check_source(_require_string(request, "module_source"))
            return _ok(None)
        if op == OP_TYPE_OF_IN_SOURCE:
            module_source = _require_string(request, "module_source")
            expr = _require_string(request, "expr")
            return _ok(backend.type_of_in_source(module_source, expr))
        if op == OP_DECLARED_NAMES_IN_SOURCE:
            return _ok(backend.declared_names_in_source(_require_string(request, "module_source")))
        if op == OP_EXPORTED_NAMES_IN_SOURCE:
            return _ok(backend.exported_names_in_source(_require_string(request, "module_source")))
        if op == OP_SYMBOL_INVENTORY_IN_SOURCE:
            declared, imported, exported = backend.symbol_inventory_in_source(_require_string(request, "module_source"))
            return _ok({KEY_DECLARED: declared, KEY_IMPORTED: imported, KEY_EXPORTED: exported})
        if op == OP_DIAGNOSTICS_IN_SOURCE:
            diagnostics = backend.diagnostics_in_source(_require_string(request, "module_source"))
            return _ok(
                {
                    KEY_MESSAGES: [message for message, _, _ in diagnostics],
                    KEY_LINES: [line for _, line, _ in diagnostics],
                    KEY_COLUMNS: [column for _, _, column in diagnostics],
                }
            )
        if op == OP_SYMBOL_LOCATIONS_IN_SOURCE:
            locations = backend.symbol_locations_in_source(_require_string(request, "module_source"))
            return _ok(
                {
                    KEY_CATEGORIES: [category for category, _, _, _ in locations],
                    KEY_NAMES: [name for _, name, _, _ in locations],
                    KEY_LINES: [line for _, _, line, _ in locations],
                    KEY_COLUMNS: [column for _, _, _, column in locations],
                }
            )
        if op == OP_INSTANCES_IN_SOURCE:
            module_source = _require_string(request, "module_source")
            query = _require_string(request, "query")
            query_type, matches = backend.instances_in_source(module_source, query)
            return _ok({KEY_MATCHES: matches, KEY_QUERY_TYPE: query_type})
        if op == OP_EVAL_EXPR_IN_SOURCE:
            module_source = _require_string(request, "module_source")
            expr = _require_string(request, "expr")
            return _ok(list(backend.eval_expr_in_source(module_source, expr)))
        if op == OP_COMPLETE_IN_STATE:
            line_buffer = _require_string(request, "line_buffer")
            imports = _require_string_list(request, "imports")
            declarations = _require_string_list(request, "declarations")
            prefix, matches = backend.complete_in_state(line_buffer, imports, declarations)
            return _ok({KEY_MATCHES: matches, KEY_PREFIX: prefix})
    except ValueError as exc:
        return _error(str(exc))
    except _AnalysisError as exc:
        return _error(str(exc))
    return _error(f"unknown analysis service op `{op}`")
