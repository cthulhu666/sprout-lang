from __future__ import annotations

from typing import TextIO

from .analysis_dispatch import dispatch_request
from .analysis_protocol import run_json_service_session

__all__ = ["run_analysis_stdio_session"]


def run_analysis_stdio_session(stdin: TextIO, stdout: TextIO) -> int:
    return run_json_service_session(stdin=stdin, stdout=stdout, dispatch=dispatch_request)
