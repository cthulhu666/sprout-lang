from __future__ import annotations

import shlex
import sys

__all__ = ["default_analysis_service_cmd"]


def default_analysis_service_cmd(python_executable: str | None = None) -> str:
    executable = sys.executable if python_executable is None else python_executable
    return f"{shlex.quote(executable)} -m sprout.analysis_adapter"
