from __future__ import annotations

import json
import sys
from typing import TextIO

from .analysis_dispatch import dispatch_request

__all__ = ["cmd_analysis_service"]


def _write_response(stdout: TextIO, payload: object) -> int:
    json.dump(payload, stdout, sort_keys=True)
    stdout.write("\n")
    stdout.flush()
    return 0


def _response_status(payload: object) -> int:
    return 0 if isinstance(payload, dict) and payload.get("ok") is True else 1


def cmd_analysis_service(
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
) -> int:
    stdin = sys.stdin if stdin is None else stdin
    stdout = sys.stdout if stdout is None else stdout
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
            response = {"error": f"invalid request json: {exc.msg}", "ok": False}
            _write_response(stdout, response)
            status = _response_status(response) or status
            continue
        response = dispatch_request(request)
        _write_response(stdout, response)
        status = status or _response_status(response)
    if saw_input:
        return status
    try:
        request = json.load(stdin)
    except json.JSONDecodeError as exc:
        response = {"error": f"invalid request json: {exc.msg}", "ok": False}
        _write_response(stdout, response)
        return _response_status(response)
    response = dispatch_request(request)
    _write_response(stdout, response)
    return _response_status(response)


if __name__ == "__main__":
    raise SystemExit(cmd_analysis_service())
