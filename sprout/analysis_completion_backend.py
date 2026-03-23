from __future__ import annotations

from .analysis import completion_candidates_in_state

__all__ = ["python_completion_complete_in_state"]


def python_completion_complete_in_state(
    line_buffer: str,
    imports: list[str],
    declarations: list[str],
) -> tuple[str, list[str]]:
    return completion_candidates_in_state(line_buffer, imports, declarations)
