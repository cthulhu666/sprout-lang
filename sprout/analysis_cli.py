from __future__ import annotations

from .analysis_service import cmd_analysis_service
from .analysis_stdio import cmd_analysis_stdio

__all__ = ["cmd_analysis_cli"]


def cmd_analysis_cli(command: str) -> int:
    if command == "analysis-service":
        return cmd_analysis_service()
    if command == "analysis-stdio":
        return cmd_analysis_stdio()
    raise ValueError(f"unknown analysis cli command: {command}")
