from __future__ import annotations

from .analysis import (
    check_source,
    eval_expression_lines_in_source,
    infer_type_in_source,
    instances_in_source,
)

__all__ = [
    "python_execution_check_source",
    "python_execution_eval_expr_in_source",
    "python_execution_instances_in_source",
    "python_execution_type_of_in_source",
]


def python_execution_check_source(module_source: str) -> None:
    check_source(module_source)


def python_execution_type_of_in_source(module_source: str, expr: str) -> str:
    return infer_type_in_source(module_source, expr)


def python_execution_instances_in_source(module_source: str, query: str) -> tuple[str, list[str]]:
    return instances_in_source(module_source, query)


def python_execution_eval_expr_in_source(module_source: str, expr: str) -> tuple[str, ...]:
    return eval_expression_lines_in_source(module_source, expr)
