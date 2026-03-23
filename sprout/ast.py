from __future__ import annotations

from dataclasses import dataclass, field, is_dataclass
from typing import Any


@dataclass
class Program:
    declarations: list[Any]


@dataclass
class TypeDecl:
    name: str
    type_params: list[str]
    constructors: list["TypeConstructor"]


@dataclass
class RecordDecl:
    name: str
    type_params: list[str]
    fields: list["RecordFieldDecl"]


@dataclass
class TypeConstructor:
    name: str
    args: list["TypeExpr"]


@dataclass
class RecordFieldDecl:
    name: str
    type_expr: "TypeExpr"


@dataclass
class TypeConstraint:
    class_name: str
    args: list["TypeExpr"]


@dataclass
class FnDecl:
    name: str
    params: list["Param"]
    return_type: "TypeExpr | None"
    effects: tuple[str, ...] | None
    constraints: list["TypeConstraint"]
    body: "Expr"


@dataclass
class Param:
    name: str
    type_expr: "TypeExpr | None"


@dataclass
class LetDecl:
    name: str
    value: "Expr"


@dataclass
class ClassDecl:
    name: str
    type_params: list[str]
    methods: list["ClassMethodSig"] = field(default_factory=list)


@dataclass
class InstanceDecl:
    constraint: TypeConstraint
    methods: list["InstanceMethodImpl"] = field(default_factory=list)


@dataclass
class ClassMethodSig:
    name: str
    params: list["Param"]
    return_type: "TypeExpr"
    effects: tuple[str, ...] | None


@dataclass
class InstanceMethodImpl:
    name: str
    params: list["Param"]
    return_type: "TypeExpr | None"
    effects: tuple[str, ...] | None
    body: "Expr"


class Expr:
    pass


@dataclass
class IfExpr(Expr):
    condition: Expr
    then_branch: Expr
    else_branch: Expr


@dataclass
class MatchExpr(Expr):
    scrutinee: Expr
    branches: list["MatchBranch"]


@dataclass
class MatchBranch:
    pattern: "Pattern"
    value: Expr


@dataclass
class BinaryExpr(Expr):
    op: str
    left: Expr
    right: Expr


@dataclass
class UnaryExpr(Expr):
    op: str
    operand: Expr


@dataclass
class CallExpr(Expr):
    callee: Expr
    args: list[Expr]


@dataclass
class LambdaExpr(Expr):
    params: list["Param"]
    body: Expr


@dataclass
class VarExpr(Expr):
    name: str


@dataclass
class IntExpr(Expr):
    value: int


@dataclass
class BoolExpr(Expr):
    value: bool


@dataclass
class StringExpr(Expr):
    value: str


@dataclass
class TupleExpr(Expr):
    items: list[Expr]


@dataclass
class RecordExpr(Expr):
    type_name: str
    fields: list["RecordFieldValue"]


@dataclass
class RecordFieldValue:
    name: str
    value: Expr


@dataclass
class GetFieldExpr(Expr):
    record: Expr
    field_name: str


class Pattern:
    pass


@dataclass
class WildcardPattern(Pattern):
    pass


@dataclass
class VarPattern(Pattern):
    name: str


@dataclass
class IntPattern(Pattern):
    value: int


@dataclass
class BoolPattern(Pattern):
    value: bool


@dataclass
class StringPattern(Pattern):
    value: str


@dataclass
class TuplePattern(Pattern):
    items: list[Pattern]


@dataclass
class ConstructorPattern(Pattern):
    name: str
    args: list[Pattern] = field(default_factory=list)


class TypeExpr:
    pass


@dataclass
class TypeName(TypeExpr):
    name: str


@dataclass
class TypeApply(TypeExpr):
    base: TypeExpr
    arg: TypeExpr


@dataclass
class TypeArrow(TypeExpr):
    left: TypeExpr
    right: TypeExpr
    effects: tuple[str, ...] | None = None


@dataclass
class TypeEffect(TypeExpr):
    base: TypeExpr
    effects: tuple[str, ...]


@dataclass
class TupleType(TypeExpr):
    items: list[TypeExpr]


def attach_loc(node: Any, line: int, column: int) -> Any:
    setattr(node, "line", line)
    setattr(node, "column", column)
    return node


def loc_str(node: Any) -> str:
    line = getattr(node, "line", None)
    column = getattr(node, "column", None)
    if line is None or column is None:
        return ""
    return f" at {line}:{column}"


def to_dict(node: Any) -> Any:
    if is_dataclass(node):
        out = {"node": node.__class__.__name__}
        for key in node.__dataclass_fields__.keys():
            out[key] = to_dict(getattr(node, key))
        if hasattr(node, "line") and hasattr(node, "column"):
            out["line"] = getattr(node, "line")
            out["column"] = getattr(node, "column")
        return out
    if isinstance(node, list):
        return [to_dict(item) for item in node]
    return node
