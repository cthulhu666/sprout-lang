from __future__ import annotations

import sys
from typing import TextIO

from .analysis_adapter import run_analysis_adapter_session

__all__ = ["cmd_analysis_service"]


def cmd_analysis_service(
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
) -> int:
    stdin = sys.stdin if stdin is None else stdin
    stdout = sys.stdout if stdout is None else stdout
    return run_analysis_adapter_session(stdin=stdin, stdout=stdout)


if __name__ == "__main__":
    raise SystemExit(cmd_analysis_service())
