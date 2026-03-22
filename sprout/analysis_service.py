from __future__ import annotations

import json
import sys
from typing import TextIO

from .analysis import check_source, infer_type_in_source, instances_in_source
from .interpreter import RuntimeError
from .module_loader import ModuleLoadError
from .parser import ParseError
from .surface_checks import SurfaceCheckError
from .tokenizer import TokenizeError
from .typeclass_lowering import TypeclassLoweringError
from .typechecker import TypeCheckError

__all__ = ["cmd_analysis_service"]


_AnalysisError = (
    ParseError,
    TokenizeError,
    TypeCheckError,
    RuntimeError,
    ModuleLoadError,
    SurfaceCheckError,
    TypeclassLoweringError,
)


def _write_response(stdout: TextIO, payload: object) -> int:
    json.dump(payload, stdout, sort_keys=True)
    stdout.write("\n")
    stdout.flush()
    return 0


def _request_error(stdout: TextIO, message: str) -> int:
    return _write_response(stdout, {"error": message, "ok": False}) or 1


def _require_string(payload: object, field: str) -> str:
    if not isinstance(payload, dict):
        raise ValueError("analysis service request must be a JSON object")
    value = payload.get(field)
    if not isinstance(value, str):
        raise ValueError(f"analysis service field `{field}` must be a string")
    return value


def cmd_analysis_service(
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
) -> int:
    stdin = sys.stdin if stdin is None else stdin
    stdout = sys.stdout if stdout is None else stdout
    try:
        request = json.load(stdin)
    except json.JSONDecodeError as exc:
        return _request_error(stdout, f"invalid request json: {exc.msg}")
    if not isinstance(request, dict):
        return _request_error(stdout, "analysis service request must be a JSON object")
    op = request.get("op")
    if op == "check_source":
        try:
            module_source = _require_string(request, "module_source")
            check_source(module_source)
        except ValueError as exc:
            return _request_error(stdout, str(exc))
        except _AnalysisError as exc:
            return _request_error(stdout, str(exc))
        return _write_response(stdout, {"ok": True, "value": None})
    if op == "type_of_in_source":
        try:
            module_source = _require_string(request, "module_source")
            expr = _require_string(request, "expr")
            inferred = infer_type_in_source(module_source, expr)
        except ValueError as exc:
            return _request_error(stdout, str(exc))
        except _AnalysisError as exc:
            return _request_error(stdout, str(exc))
        return _write_response(stdout, {"ok": True, "value": inferred})
    if op == "instances_in_source":
        try:
            module_source = _require_string(request, "module_source")
            query = _require_string(request, "query")
            query_type, matches = instances_in_source(module_source, query)
        except ValueError as exc:
            return _request_error(stdout, str(exc))
        except _AnalysisError as exc:
            return _request_error(stdout, str(exc))
        return _write_response(stdout, {"ok": True, "value": {"matches": matches, "query_type": query_type}})
    return _request_error(stdout, f"unknown analysis service op `{op}`")
