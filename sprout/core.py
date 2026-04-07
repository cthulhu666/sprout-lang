from __future__ import annotations

from dataclasses import dataclass


class Expr:
    pass


class Pattern:
    pass


@dataclass
class VarExpr(Expr):
    name: str
    src: object | None = None


@dataclass
class IntExpr(Expr):
    value: int
    src: object | None = None


@dataclass
class BoolExpr(Expr):
    value: bool
    src: object | None = None


@dataclass
class StringExpr(Expr):
    value: str
    src: object | None = None


@dataclass
class CharExpr(Expr):
    value: str
    src: object | None = None


@dataclass
class UnitExpr(Expr):
    src: object | None = None


@dataclass
class TupleExpr(Expr):
    items: list[Expr]
    src: object | None = None


@dataclass
class RecordFieldValue:
    name: str
    value: Expr
    src: object | None = None


@dataclass
class RecordExpr(Expr):
    type_name: str
    fields: list[RecordFieldValue]
    src: object | None = None


@dataclass
class GetFieldExpr(Expr):
    record: Expr
    field_name: str
    src: object | None = None


@dataclass
class BinaryExpr(Expr):
    op: str
    left: Expr
    right: Expr
    src: object | None = None


@dataclass
class IntRangeExpr(Expr):
    start: Expr
    end: Expr
    src: object | None = None


@dataclass
class UnaryExpr(Expr):
    op: str
    operand: Expr
    src: object | None = None


@dataclass
class CallExpr(Expr):
    callee: Expr
    args: list[Expr]
    src: object | None = None


@dataclass
class LambdaExpr(Expr):
    params: list[object]
    body: Expr
    src: object | None = None


@dataclass
class IfExpr(Expr):
    condition: Expr
    then_branch: Expr
    else_branch: Expr
    src: object | None = None


@dataclass
class MatchBranch:
    pattern: Pattern
    value: Expr
    src: object | None = None


@dataclass
class MatchExpr(Expr):
    scrutinee: Expr
    branches: list[MatchBranch]
    src: object | None = None


@dataclass
class WildcardPattern(Pattern):
    src: object | None = None


@dataclass
class VarPattern(Pattern):
    name: str
    src: object | None = None


@dataclass
class IntPattern(Pattern):
    value: int
    src: object | None = None


@dataclass
class BoolPattern(Pattern):
    value: bool
    src: object | None = None


@dataclass
class StringPattern(Pattern):
    value: str
    src: object | None = None


@dataclass
class CharPattern(Pattern):
    value: str
    src: object | None = None


@dataclass
class UnitPattern(Pattern):
    src: object | None = None


@dataclass
class TuplePattern(Pattern):
    items: list[Pattern]
    src: object | None = None


@dataclass
class ConstructorPattern(Pattern):
    name: str
    args: list[Pattern]
    src: object | None = None
