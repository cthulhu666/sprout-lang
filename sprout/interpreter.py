from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import socket
from typing import Callable, TextIO

from . import ast


class RuntimeError(ValueError):
    pass


def rt_error(message: str, node: object | None = None) -> RuntimeError:
    return RuntimeError(f"{message}{ast.loc_str(node)}")


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
    line: int | None = None
    column: int | None = None


@dataclass
class ComposedFunction:
    left: object
    right: object


@dataclass
class TailCall:
    callee: object
    args: list[object]


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


def py_to_adt_list(items: list[object]) -> ADTValue:
    cursor: ADTValue = ADTValue(constructor="Nil", args=())
    for item in reversed(items):
        cursor = ADTValue(constructor="Cons", args=(item, cursor))
    return cursor


def eval_expr(expr: ast.Expr, env: Env, in_tail_position: bool = False) -> object:
    if isinstance(expr, ast.IntExpr):
        return expr.value
    if isinstance(expr, ast.BoolExpr):
        return expr.value
    if isinstance(expr, ast.StringExpr):
        return expr.value
    if isinstance(expr, ast.VarExpr):
        try:
            return env.get(expr.name)
        except RuntimeError as exc:
            raise rt_error(str(exc), expr) from exc

    if isinstance(expr, ast.UnaryExpr):
        operand = eval_expr(expr.operand, env)
        if expr.op == "-":
            if not isinstance(operand, int):
                raise rt_error("Unary '-' expects Int", expr)
            return -operand
        raise rt_error(f"Unsupported unary operator {expr.op}", expr)

    if isinstance(expr, ast.BinaryExpr):
        left = eval_expr(expr.left, env)

        if expr.op == "&&":
            if not isinstance(left, bool):
                raise rt_error("'&&' expects Bool on the left", expr.left)
            if not left:
                return False
            right = eval_expr(expr.right, env)
            if not isinstance(right, bool):
                raise rt_error("'&&' expects Bool on the right", expr.right)
            return right

        if expr.op == "||":
            if not isinstance(left, bool):
                raise rt_error("'||' expects Bool on the left", expr.left)
            if left:
                return True
            right = eval_expr(expr.right, env)
            if not isinstance(right, bool):
                raise rt_error("'||' expects Bool on the right", expr.right)
            return right

        if expr.op == ">>":
            right = eval_expr(expr.right, env)
            if not _is_callable(left):
                raise rt_error("Left side of '>>' must be a function", expr.left)
            if not _is_callable(right):
                raise rt_error("Right side of '>>' must be a function", expr.right)
            return ComposedFunction(left=left, right=right)

        right = eval_expr(expr.right, env)

        if expr.op in {"+", "-", "*", "/"}:
            if not isinstance(left, int) or not isinstance(right, int):
                raise rt_error(f"Operator '{expr.op}' expects Int operands", expr)
            if expr.op == "+":
                return left + right
            if expr.op == "-":
                return left - right
            if expr.op == "*":
                return left * right
            return left // right

        if expr.op in {"<", "<=", ">", ">="}:
            if not isinstance(left, int) or not isinstance(right, int):
                raise rt_error(f"Operator '{expr.op}' expects Int operands", expr)
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

        raise rt_error(f"Unsupported binary operator {expr.op}", expr)

    if isinstance(expr, ast.IfExpr):
        condition = eval_expr(expr.condition, env)
        if not isinstance(condition, bool):
            raise rt_error("if condition must be Bool", expr.condition)
        if condition:
            return eval_expr(expr.then_branch, env, in_tail_position=in_tail_position)
        return eval_expr(expr.else_branch, env, in_tail_position=in_tail_position)

    if isinstance(expr, ast.MatchExpr):
        scrutinee = eval_expr(expr.scrutinee, env)
        for branch in expr.branches:
            bindings = match_pattern(branch.pattern, scrutinee)
            if bindings is not None:
                branch_env = Env(parent=env)
                for name, value in bindings.items():
                    branch_env.set(name, value)
                return eval_expr(branch.value, branch_env, in_tail_position=in_tail_position)
        raise rt_error("Non-exhaustive match at runtime", expr)

    if isinstance(expr, ast.CallExpr):
        callee = eval_expr(expr.callee, env)
        args = [eval_expr(arg, env) for arg in expr.args]
        if in_tail_position and isinstance(callee, FunctionValue):
            return TailCall(callee=callee, args=args)
        try:
            return apply_callable(callee, args)
        except RuntimeError as exc:
            raise rt_error(str(exc), expr) from exc

    raise rt_error(f"Unsupported expression node: {expr}", expr)


def apply_callable(callee: object, args: list[object]) -> object:
    while True:
        if isinstance(callee, ComposedFunction):
            intermediate = apply_callable(callee.right, args)
            callee = callee.left
            args = [intermediate]
            continue

        if isinstance(callee, FunctionValue):
            if len(args) != len(callee.params):
                raise rt_error(
                    f"Function {callee.name} expects {len(callee.params)} args, got {len(args)}",
                    callee,
                )
            call_env = Env(parent=callee.closure)
            for name, value in zip(callee.params, args):
                call_env.set(name, value)
            result = eval_expr(callee.body, call_env, in_tail_position=True)
            if isinstance(result, TailCall):
                callee = result.callee
                args = result.args
                continue
            return result

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


def _is_callable(value: object) -> bool:
    return isinstance(value, (FunctionValue, BuiltinFunction, ConstructorValue, ComposedFunction))


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
    listeners: dict[int, socket.socket] = {}
    connections: dict[int, socket.socket] = {}
    next_handle = 1

    def alloc_handle() -> int:
        nonlocal next_handle
        handle = next_handle
        next_handle += 1
        return handle

    def builtin_print(args: list[object]) -> object:
        text = format_value(args[0])
        if out is None:
            print(text)
        else:
            print(text, file=out)
        return None

    def builtin_print_int(args: list[object]) -> object:
        value = args[0]
        if not isinstance(value, int):
            raise RuntimeError("print_int expects Int")
        if out is None:
            print(value)
        else:
            print(value, file=out)
        return value

    def builtin_read_lines(args: list[object]) -> object:
        raw_path = args[0]
        if not isinstance(raw_path, str):
            raise RuntimeError("read_lines expects String path")
        lines = Path(raw_path).read_text(encoding="utf-8").splitlines()
        return py_to_adt_list(lines)

    def builtin_parse_int(args: list[object]) -> object:
        raw = args[0]
        if not isinstance(raw, str):
            raise RuntimeError("parse_int expects String")
        return int(raw.strip())

    def builtin_split_words(args: list[object]) -> object:
        raw = args[0]
        if not isinstance(raw, str):
            raise RuntimeError("split_words expects String")
        tokens = raw.replace(",", " ").split()
        return py_to_adt_list(tokens)

    def builtin_tcp_listen(args: list[object]) -> object:
        port = args[0]
        if not isinstance(port, int):
            raise RuntimeError("tcp_listen expects Int port")
        if port < 1 or port > 65535:
            raise RuntimeError("tcp_listen port must be in 1..65535")
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", port))
        sock.listen(16)
        handle = alloc_handle()
        listeners[handle] = sock
        return handle

    def builtin_tcp_accept(args: list[object]) -> object:
        listener_handle = args[0]
        if not isinstance(listener_handle, int):
            raise RuntimeError("tcp_accept expects Int listener handle")
        listener = listeners.get(listener_handle)
        if listener is None:
            raise RuntimeError("tcp_accept got unknown listener handle")
        conn, _ = listener.accept()
        handle = alloc_handle()
        connections[handle] = conn
        return handle

    def builtin_tcp_read(args: list[object]) -> object:
        conn_handle = args[0]
        if not isinstance(conn_handle, int):
            raise RuntimeError("tcp_read expects Int connection handle")
        conn = connections.get(conn_handle)
        if conn is None:
            raise RuntimeError("tcp_read got unknown connection handle")
        data = conn.recv(65536)
        return data.decode("utf-8", errors="replace")

    def builtin_tcp_write(args: list[object]) -> object:
        conn_handle = args[0]
        payload = args[1]
        if not isinstance(conn_handle, int):
            raise RuntimeError("tcp_write expects Int connection handle")
        if not isinstance(payload, str):
            raise RuntimeError("tcp_write expects String payload")
        conn = connections.get(conn_handle)
        if conn is None:
            raise RuntimeError("tcp_write got unknown connection handle")
        conn.sendall(payload.encode("utf-8"))
        return None

    def builtin_tcp_close(args: list[object]) -> object:
        conn_handle = args[0]
        if not isinstance(conn_handle, int):
            raise RuntimeError("tcp_close expects Int connection handle")
        conn = connections.pop(conn_handle, None)
        if conn is None:
            raise RuntimeError("tcp_close got unknown connection handle")
        conn.close()
        return None

    def builtin_tcp_close_listener(args: list[object]) -> object:
        listener_handle = args[0]
        if not isinstance(listener_handle, int):
            raise RuntimeError("tcp_close_listener expects Int listener handle")
        listener = listeners.pop(listener_handle, None)
        if listener is None:
            raise RuntimeError("tcp_close_listener got unknown listener handle")
        listener.close()
        return None

    env.set("print", BuiltinFunction(name="print", arity=1, fn=builtin_print))
    env.set("print_int", BuiltinFunction(name="print_int", arity=1, fn=builtin_print_int))
    env.set("read_lines", BuiltinFunction(name="read_lines", arity=1, fn=builtin_read_lines))
    env.set("parse_int", BuiltinFunction(name="parse_int", arity=1, fn=builtin_parse_int))
    env.set("split_words", BuiltinFunction(name="split_words", arity=1, fn=builtin_split_words))
    env.set("tcp_listen", BuiltinFunction(name="tcp_listen", arity=1, fn=builtin_tcp_listen))
    env.set("tcp_accept", BuiltinFunction(name="tcp_accept", arity=1, fn=builtin_tcp_accept))
    env.set("tcp_read", BuiltinFunction(name="tcp_read", arity=1, fn=builtin_tcp_read))
    env.set("tcp_write", BuiltinFunction(name="tcp_write", arity=2, fn=builtin_tcp_write))
    env.set("tcp_close", BuiltinFunction(name="tcp_close", arity=1, fn=builtin_tcp_close))
    env.set(
        "tcp_close_listener",
        BuiltinFunction(name="tcp_close_listener", arity=1, fn=builtin_tcp_close_listener),
    )

    for decl in program.declarations:
        if isinstance(decl, ast.TypeDecl):
            for ctor in decl.constructors:
                arity = len(ctor.args)
                if arity == 0:
                    env.set(ctor.name, ADTValue(constructor=ctor.name, args=()))
                else:
                    env.set(ctor.name, ConstructorValue(name=ctor.name, arity=arity))

    for decl in program.declarations:
        if isinstance(decl, ast.FnDecl):
            env.set(
                decl.name,
                FunctionValue(
                    name=decl.name,
                    params=[param.name for param in decl.params],
                    body=decl.body,
                    closure=env,
                    line=getattr(decl, "line", None),
                    column=getattr(decl, "column", None),
                ),
            )
        elif isinstance(decl, ast.LetDecl):
            env.set(decl.name, eval_expr(decl.value, env))

    try:
        if "main" in env.values:
            main = env.get("main")
            if isinstance(main, FunctionValue):
                apply_callable(main, [])
    finally:
        for conn in connections.values():
            conn.close()
        for listener in listeners.values():
            listener.close()
