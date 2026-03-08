from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, TextIO

from . import ast


class RuntimeError(ValueError):
    pass


@dataclass(frozen=True)
class ADTValue:
    constructor: str
    args: tuple[object, ...]


@dataclass
class ConstructorValue:
    name: str
    arity: int


@dataclass
class BuiltinFunction:
    name: str
    arity: int
    fn: Callable[[list[object]], object]


@dataclass
class FunctionValue:
    name: str
    params: list[str]
    body: ast.Expr
    closure: "Env"


class Env:
    def __init__(self, parent: "Env | None" = None) -> None:
        self.parent = parent
        self.values: dict[str, object] = {}

    def get(self, name: str) -> object:
        if name in self.values:
            return self.values[name]
        if self.parent is not None:
            return self.parent.get(name)
        raise RuntimeError(f"Unknown variable {name}")

    def set(self, name: str, value: object) -> None:
        self.values[name] = value


def format_value(value: object) -> str:
    if isinstance(value, ADTValue):
        if not value.args:
            return value.constructor
        return f"{value.constructor}({', '.join(format_value(arg) for arg in value.args)})"
    if value is None:
        return "()"
    return str(value)


def eval_expr(expr: ast.Expr, env: Env) -> object:
    if isinstance(expr, ast.IntExpr):
        return expr.value
    if isinstance(expr, ast.BoolExpr):
        return expr.value
    if isinstance(expr, ast.StringExpr):
        return expr.value
    if isinstance(expr, ast.VarExpr):
        return env.get(expr.name)

    if isinstance(expr, ast.UnaryExpr):
        operand = eval_expr(expr.operand, env)
        if expr.op == "-":
            if not isinstance(operand, int):
                raise RuntimeError("Unary '-' expects Int")
            return -operand
        raise RuntimeError(f"Unsupported unary operator {expr.op}")

    if isinstance(expr, ast.BinaryExpr):
        left = eval_expr(expr.left, env)

        if expr.op == "&&":
            if not isinstance(left, bool):
                raise RuntimeError("'&&' expects Bool on the left")
            if not left:
                return False
            right = eval_expr(expr.right, env)
            if not isinstance(right, bool):
                raise RuntimeError("'&&' expects Bool on the right")
            return right

        if expr.op == "||":
            if not isinstance(left, bool):
                raise RuntimeError("'||' expects Bool on the left")
            if left:
                return True
            right = eval_expr(expr.right, env)
            if not isinstance(right, bool):
                raise RuntimeError("'||' expects Bool on the right")
            return right

        right = eval_expr(expr.right, env)

        if expr.op in {"+", "-", "*", "/"}:
            if not isinstance(left, int) or not isinstance(right, int):
                raise RuntimeError(f"Operator '{expr.op}' expects Int operands")
            if expr.op == "+":
                return left + right
            if expr.op == "-":
                return left - right
            if expr.op == "*":
                return left * right
            return left // right

        if expr.op in {"<", "<=", ">", ">="}:
            if not isinstance(left, int) or not isinstance(right, int):
                raise RuntimeError(f"Operator '{expr.op}' expects Int operands")
            if expr.op == "<":
                return left < right
            if expr.op == "<=":
                return left <= right
            if expr.op == ">":
                return left > right
            return left >= right

        if expr.op == "==":
            return left == right
        if expr.op == "!=":
            return left != right

        raise RuntimeError(f"Unsupported binary operator {expr.op}")

    if isinstance(expr, ast.IfExpr):
        condition = eval_expr(expr.condition, env)
        if not isinstance(condition, bool):
            raise RuntimeError("if condition must be Bool")
        if condition:
            return eval_expr(expr.then_branch, env)
        return eval_expr(expr.else_branch, env)

    if isinstance(expr, ast.MatchExpr):
        scrutinee = eval_expr(expr.scrutinee, env)
        for branch in expr.branches:
            bindings = match_pattern(branch.pattern, scrutinee)
            if bindings is not None:
                branch_env = Env(parent=env)
                for name, value in bindings.items():
                    branch_env.set(name, value)
                return eval_expr(branch.value, branch_env)
        raise RuntimeError("Non-exhaustive match at runtime")

    if isinstance(expr, ast.CallExpr):
        callee = eval_expr(expr.callee, env)
        args = [eval_expr(arg, env) for arg in expr.args]
        return apply_callable(callee, args)

    raise RuntimeError(f"Unsupported expression node: {expr}")


def apply_callable(callee: object, args: list[object]) -> object:
    if isinstance(callee, FunctionValue):
        if len(args) != len(callee.params):
            raise RuntimeError(
                f"Function {callee.name} expects {len(callee.params)} args, got {len(args)}"
            )
        call_env = Env(parent=callee.closure)
        for name, value in zip(callee.params, args):
            call_env.set(name, value)
        return eval_expr(callee.body, call_env)

    if isinstance(callee, BuiltinFunction):
        if len(args) != callee.arity:
            raise RuntimeError(f"Builtin {callee.name} expects {callee.arity} args, got {len(args)}")
        return callee.fn(args)

    if isinstance(callee, ConstructorValue):
        if len(args) != callee.arity:
            raise RuntimeError(
                f"Constructor {callee.name} expects {callee.arity} args, got {len(args)}"
            )
        return ADTValue(constructor=callee.name, args=tuple(args))

    raise RuntimeError("Attempted to call a non-function value")


def match_pattern(pattern: ast.Pattern, value: object) -> dict[str, object] | None:
    if isinstance(pattern, ast.WildcardPattern):
        return {}
    if isinstance(pattern, ast.VarPattern):
        return {pattern.name: value}
    if isinstance(pattern, ast.IntPattern):
        return {} if value == pattern.value else None
    if isinstance(pattern, ast.BoolPattern):
        return {} if value == pattern.value else None
    if isinstance(pattern, ast.StringPattern):
        return {} if value == pattern.value else None

    if isinstance(pattern, ast.ConstructorPattern):
        if not isinstance(value, ADTValue):
            return None
        if value.constructor != pattern.name:
            return None
        if len(value.args) != len(pattern.args):
            return None

        merged: dict[str, object] = {}
        for sub_pattern, sub_value in zip(pattern.args, value.args):
            sub = match_pattern(sub_pattern, sub_value)
            if sub is None:
                return None
            merged.update(sub)
        return merged

    return None


def run_program(program: ast.Program, stdout: TextIO | None = None) -> None:
    out = stdout
    env = Env()

    def builtin_print(args: list[object]) -> object:
        text = format_value(args[0])
        if out is None:
            print(text)
        else:
            print(text, file=out)
        return None

    env.set("print", BuiltinFunction(name="print", arity=1, fn=builtin_print))

    for decl in program.declarations:
        if isinstance(decl, ast.TypeDecl):
            for ctor in decl.constructors:
                env.set(ctor.name, ConstructorValue(name=ctor.name, arity=len(ctor.args)))

    for decl in program.declarations:
        if isinstance(decl, ast.FnDecl):
            env.set(
                decl.name,
                FunctionValue(
                    name=decl.name,
                    params=[param.name for param in decl.params],
                    body=decl.body,
                    closure=env,
                ),
            )
        elif isinstance(decl, ast.LetDecl):
            env.set(decl.name, eval_expr(decl.value, env))

    if "main" in env.values:
        main = env.get("main")
        if isinstance(main, FunctionValue):
            apply_callable(main, [])
