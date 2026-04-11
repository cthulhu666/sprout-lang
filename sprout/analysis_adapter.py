from __future__ import annotations

import sys
from typing import TextIO

from .analysis_backend import AnalysisBackend, AnalysisCompletionBackend, AnalysisExecutionBackend, AnalysisSnapshotBackend
from .analysis_backend_python import default_completion_backend, default_execution_backend, default_snapshot_backend
from .analysis_dispatch import dispatch_request
from .analysis_protocol import run_json_service_session

__all__ = ["cmd_analysis_adapter", "run_analysis_adapter_session", "run_analysis_stdio_session"]


def run_analysis_adapter_session(
    stdin: TextIO,
    stdout: TextIO,
    backend: AnalysisBackend | None = None,
    *,
    snapshot_backend: AnalysisSnapshotBackend | None = None,
    execution_backend: AnalysisExecutionBackend | None = None,
    completion_backend: AnalysisCompletionBackend | None = None,
) -> int:
    if backend is None:
        snapshot_backend = default_snapshot_backend() if snapshot_backend is None else snapshot_backend
        execution_backend = default_execution_backend() if execution_backend is None else execution_backend
        completion_backend = default_completion_backend() if completion_backend is None else completion_backend

    def dispatch(request: object) -> dict[str, object]:
        return dispatch_request(
            request,
            backend=backend,
            snapshot_backend=snapshot_backend,
            execution_backend=execution_backend,
            completion_backend=completion_backend,
        )

    return run_json_service_session(stdin=stdin, stdout=stdout, dispatch=dispatch)


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
