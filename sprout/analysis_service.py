from __future__ import annotations

from typing import TextIO

from .analysis_stdio import cmd_analysis_stdio

__all__ = ["cmd_analysis_service"]


def cmd_analysis_service(
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
) -> int:
    return cmd_analysis_stdio(stdin=stdin, stdout=stdout)


if __name__ == "__main__":
    raise SystemExit(cmd_analysis_service())
