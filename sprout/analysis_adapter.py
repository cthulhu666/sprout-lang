from __future__ import annotations

import sys
from typing import TextIO

from .analysis_dispatch import dispatch_request
from .analysis_protocol import run_json_service_session

__all__ = ["cmd_analysis_adapter", "run_analysis_adapter_session", "run_analysis_stdio_session"]


def run_analysis_adapter_session(stdin: TextIO, stdout: TextIO) -> int:
    return run_json_service_session(stdin=stdin, stdout=stdout, dispatch=dispatch_request)


def run_analysis_stdio_session(stdin: TextIO, stdout: TextIO) -> int:
    return run_analysis_adapter_session(stdin=stdin, stdout=stdout)


def cmd_analysis_adapter(
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
) -> int:
    stdin = sys.stdin if stdin is None else stdin
    stdout = sys.stdout if stdout is None else stdout
    return run_analysis_adapter_session(stdin=stdin, stdout=stdout)


if __name__ == "__main__":
    raise SystemExit(cmd_analysis_adapter())
