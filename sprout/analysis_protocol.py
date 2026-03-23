from __future__ import annotations

import json
from typing import Callable, TextIO

from .analysis_contract import KEY_OK, response_error

__all__ = [
    "error_response",
    "response_status",
    "run_json_service_session",
]


def error_response(message: str) -> dict[str, object]:
    return response_error(message)


def _write_response(stdout: TextIO, payload: object) -> int:
    json.dump(payload, stdout, sort_keys=True)
    stdout.write("\n")
    stdout.flush()
    return 0


def response_status(payload: object) -> int:
    return 0 if isinstance(payload, dict) and payload.get(KEY_OK) is True else 1


def run_json_service_session(
    stdin: TextIO,
    stdout: TextIO,
    dispatch: Callable[[object], dict[str, object]],
) -> int:
    status = 0
    saw_input = False
    for raw_line in stdin:
        line = raw_line.strip()
        if not line:
            continue
        saw_input = True
        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            response = error_response(f"invalid request json: {exc.msg}")
            _write_response(stdout, response)
            status = response_status(response) or status
            continue
        response = dispatch(request)
        _write_response(stdout, response)
        status = status or response_status(response)
    if saw_input:
        return status
    try:
        request = json.load(stdin)
    except json.JSONDecodeError as exc:
        response = error_response(f"invalid request json: {exc.msg}")
        _write_response(stdout, response)
        return response_status(response)
    response = dispatch(request)
    _write_response(stdout, response)
    return response_status(response)
