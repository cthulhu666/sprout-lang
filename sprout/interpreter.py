from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
import json
import hashlib
import hmac
import os
from pathlib import Path
import re
import selectors
import socket
import sys
from typing import Callable, TextIO
import urllib.error
import urllib.request

from . import ast
from .elaborate import elaborate_program
from .module_loader import ModuleLoadError
from .parser import ParseError
from .surface_checks import SurfaceCheckError
from .tokenizer import TokenizeError
from .typeclass_lowering import TypeclassLoweringError
from .typechecker import TypeCheckError


class RuntimeError(ValueError):
    pass


def rt_error(message: str, node: object | None = None) -> RuntimeError:
    return RuntimeError(f"{message}{ast.loc_str(node)}")


def _builtin_runtime_message(name: str, detail: str) -> str:
    return f"runtime error: builtin `{name}`: {detail}"


def _normalize_builtin_runtime_error(name: str, message: str) -> str:
    if message.startswith("runtime error:"):
        return message
    detail = message
    if message.startswith(f"{name}:"):
        detail = message[len(name) + 1 :].strip()
    elif message.startswith(f"{name} "):
        detail = message[len(name) + 1 :].strip()
    return _builtin_runtime_message(name, detail)


@dataclass(frozen=True)
class ADTValue:
    constructor: str
    args: tuple[object, ...]


@dataclass(frozen=True)
class VectorValue:
    items: tuple[object, ...]


@dataclass(frozen=True)
class MapValue:
    items: dict[str, object]


@dataclass(frozen=True)
class SetValue:
    items: frozenset[str]


@dataclass(frozen=True)
class BytesValue:
    items: bytes


@dataclass(frozen=True)
class BuilderValue:
    chunks: tuple[bytes, ...]


@dataclass(frozen=True)
class IntRangeValue:
    start: int
    end: int


_REGEX_SHORTHAND_ESCAPES = {
    "d": "[0-9]",
    "w": "[A-Za-z0-9_]",
    "s": "[ \t\r\n]",
}

_REGEX_SHORTHAND_CLASS_ESCAPES = {
    "d": "0-9",
    "w": "A-Za-z0-9_",
    "s": " \t\r\n",
}


def _translate_regex_pattern(pattern: str) -> str:
    out: list[str] = []
    in_class = False
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "\\":
            if i + 1 >= len(pattern):
                raise ValueError("invalid regex pattern: trailing escape")
            esc = pattern[i + 1]
            if esc.isdigit():
                raise ValueError("unsupported regex feature: backreferences")
            if esc in _REGEX_SHORTHAND_ESCAPES:
                out.append(
                    _REGEX_SHORTHAND_CLASS_ESCAPES[esc]
                    if in_class
                    else _REGEX_SHORTHAND_ESCAPES[esc]
                )
            elif esc in "\\.^$*+?()[]|{}-":
                out.append("\\" + esc)
            else:
                raise ValueError(f"unsupported regex feature: escape \\{esc}")
            i += 2
            continue
        if not in_class:
            if ch == "[":
                in_class = True
                out.append(ch)
            elif ch in "{}":
                raise ValueError("unsupported regex feature: counted repetition")
            elif ch in "*+?" and i + 1 < len(pattern) and pattern[i + 1] == "?":
                raise ValueError("unsupported regex feature: non-greedy quantifiers")
            elif ch == "(" and i + 1 < len(pattern) and pattern[i + 1] == "?":
                raise ValueError("unsupported regex feature: extended group syntax")
            else:
                out.append(ch)
        else:
            if ch == "]":
                in_class = False
            out.append(ch)
        i += 1
    return "".join(out)


def _compile_regex_pattern(pattern: str) -> re.Pattern[str]:
    translated = _translate_regex_pattern(pattern)
    try:
        return re.compile(translated)
    except re.error as exc:
        raise ValueError(f"invalid regex pattern: {exc}") from exc


@dataclass(frozen=True)
class TupleValue:
    items: tuple[object, ...]


@dataclass(frozen=True)
class RecordValue:
    type_name: str
    fields: dict[str, object]


@dataclass
class RefValue:
    """Mutable reference cell — backing store for ref_new/ref_read/ref_write."""

    value: object


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
class PartialFunction:
    callee: object
    args: list[object]
    remaining_arity: int


@dataclass
class ComposedFunction:
    outer: object
    inner: object


@dataclass
class TailCall:
    callee: object
    args: list[object]


class EchoServerBackend:
    def serve_echo(self, port: int, max_connections: int) -> None:
        raise NotImplementedError


class BlockingEchoServerBackend(EchoServerBackend):
    def serve_echo(self, port: int, max_connections: int) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("127.0.0.1", port))
            listener.listen(128)
            served = 0
            while served < max_connections:
                conn, _ = listener.accept()
                with conn:
                    payload = conn.recv(65536)
                    if payload:
                        conn.sendall(payload)
                served += 1


class ReactorEchoServerBackend(EchoServerBackend):
    def serve_echo(self, port: int, max_connections: int) -> None:
        selector = selectors.DefaultSelector()
        pending: dict[socket.socket, bytes] = {}
        served = 0
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", port))
        listener.listen(1024)
        listener.setblocking(False)
        selector.register(listener, selectors.EVENT_READ, data="listener")

        try:
            while served < max_connections:
                events = selector.select(timeout=1.0)
                for key, mask in events:
                    if key.data == "listener":
                        conn, _ = listener.accept()
                        conn.setblocking(False)
                        selector.register(conn, selectors.EVENT_READ, data="conn")
                        continue

                    conn = key.fileobj
                    assert isinstance(conn, socket.socket)
                    if mask & selectors.EVENT_READ:
                        payload = conn.recv(65536)
                        pending[conn] = payload
                        selector.modify(conn, selectors.EVENT_WRITE, data="conn")
                        continue

                    if mask & selectors.EVENT_WRITE:
                        payload = pending.pop(conn, b"")
                        if payload:
                            conn.sendall(payload)
                        selector.unregister(conn)
                        conn.close()
                        served += 1
        finally:
            for key in list(selector.get_map().values()):
                obj = key.fileobj
                if isinstance(obj, socket.socket):
                    selector.unregister(obj)
                    obj.close()
            selector.close()


def _build_echo_backend() -> EchoServerBackend:
    backend_name = os.environ.get("SPROUT_NET_MODEL", "reactor").strip().lower()
    if backend_name == "reactor":
        return ReactorEchoServerBackend()
    if backend_name == "blocking":
        return BlockingEchoServerBackend()
    raise RuntimeError(f"Unknown SPROUT_NET_MODEL {backend_name!r}, expected 'reactor' or 'blocking'")


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
        ctor_name = value.constructor.rsplit(".", 1)[-1]
        if not value.args:
            return ctor_name
        return f"{ctor_name}({', '.join(format_value(arg) for arg in value.args)})"
    if isinstance(value, VectorValue):
        return "[" + ", ".join(format_value(item) for item in value.items) + "]"
    if isinstance(value, MapValue):
        rendered = ", ".join(f"{k}: {format_value(v)}" for k, v in value.items.items())
        return "{" + rendered + "}"
    if isinstance(value, BytesValue):
        return "Bytes[" + ", ".join(str(item) for item in value.items) + "]"
    if isinstance(value, BuilderValue):
        return "Builder(" + ", ".join(repr(chunk) for chunk in value.chunks) + ")"
    if isinstance(value, IntRangeValue):
        return f"{value.start}..{value.end}"
    if isinstance(value, TupleValue):
        return "(" + ", ".join(format_value(item) for item in value.items) + ")"
    if isinstance(value, RecordValue):
        rendered = ", ".join(f"{name} = {format_value(item)}" for name, item in value.fields.items())
        return f"{value.type_name} " + "{" + rendered + "}"
    if value is None:
        return "()"
    return str(value)


def py_to_adt_list(items: list[object]) -> ADTValue:
    cursor: ADTValue = ADTValue(constructor="Nil", args=())
    for item in reversed(items):
        cursor = ADTValue(constructor="Cons", args=(item, cursor))
    return cursor


def _vector_sort_by_int_from_decorated_list(decorated: object) -> VectorValue:
    items: list[tuple[int, int, object]] = []
    cursor = decorated
    while True:
        if not isinstance(cursor, ADTValue):
            raise RuntimeError("vector_sort_by_int expects List")
        ctor = cursor.constructor.rsplit(".", 1)[-1]
        if ctor == "Nil":
            break
        if ctor != "Cons" or len(cursor.args) != 2:
            raise RuntimeError("vector_sort_by_int expects List")
        pair = cursor.args[0]
        if not isinstance(pair, TupleValue) or len(pair.items) != 3:
            raise RuntimeError("vector_sort_by_int expects decorated (Int, Int, a) tuples")
        key, index, value = pair.items
        if not isinstance(key, int) or not isinstance(index, int):
            raise RuntimeError("vector_sort_by_int expects decorated (Int, Int, a) tuples")
        items.append((key, index, value))
        cursor = cursor.args[1]

    items.sort(key=lambda item: (item[0], item[1]))
    return VectorValue(items=tuple(value for _, _, value in items))


def eval_expr(expr: ast.Expr, env: Env, in_tail_position: bool = False) -> object:
    if isinstance(expr, ast.IntExpr):
        return expr.value
    if isinstance(expr, ast.BoolExpr):
        return expr.value
    if isinstance(expr, ast.StringExpr):
        return expr.value
    if isinstance(expr, ast.CharExpr):
        return expr.value
    if isinstance(expr, ast.UnitExpr):
        return None
    if isinstance(expr, ast.IntRangeExpr):
        start = eval_expr(expr.start, env)
        end = eval_expr(expr.end, env)
        if not isinstance(start, int) or not isinstance(end, int):
            raise rt_error("'..' expects Int operands", expr)
        return IntRangeValue(start=start, end=end)
    if isinstance(expr, ast.TupleExpr):
        return TupleValue(items=tuple(eval_expr(item, env) for item in expr.items))
    if isinstance(expr, ast.RecordExpr):
        return RecordValue(
            type_name=expr.type_name,
            fields={field.name: eval_expr(field.value, env) for field in expr.fields},
        )
    if isinstance(expr, ast.GetFieldExpr):
        record = eval_expr(expr.record, env)
        if not isinstance(record, RecordValue):
            raise rt_error("get expects a record value", expr.record)
        if expr.field_name not in record.fields:
            raise rt_error(f"Record {record.type_name} has no field {expr.field_name}", expr)
        return record.fields[expr.field_name]
    if isinstance(expr, ast.VarExpr):
        try:
            return env.get(expr.name)
        except RuntimeError as exc:
            raise rt_error(str(exc), expr) from exc

    if isinstance(expr, ast.LambdaExpr):
        return FunctionValue(
            name="<lambda>",
            params=[param.name for param in expr.params],
            body=expr.body,
            closure=env,
            line=getattr(expr, "line", None),
            column=getattr(expr, "column", None),
        )

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

        if expr.op in {"<<", ">>"}:
            right = eval_expr(expr.right, env)
            if not _is_callable(left):
                raise rt_error(f"Left side of '{expr.op}' must be a function", expr.left)
            if not _is_callable(right):
                raise rt_error(f"Right side of '{expr.op}' must be a function", expr.right)
            if expr.op == ">>":
                return ComposedFunction(outer=right, inner=left)
            return ComposedFunction(outer=left, inner=right)

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
            if isinstance(left, str) and isinstance(right, str):
                # Char comparisons — compare by Unicode code point
                lv, rv = ord(left), ord(right)
            elif isinstance(left, int) and isinstance(right, int):
                lv, rv = left, right
            else:
                raise rt_error(f"Operator '{expr.op}' expects Int or Char operands", expr)
            if expr.op == "<":
                return lv < rv
            if expr.op == "<=":
                return lv <= rv
            if expr.op == ">":
                return lv > rv
            return lv >= rv

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
            intermediate = apply_callable(callee.inner, args)
            callee = callee.outer
            args = [intermediate]
            continue

        if isinstance(callee, PartialFunction):
            if len(args) > callee.remaining_arity:
                total_arity = len(callee.args) + callee.remaining_arity
                raise rt_error(f"Function expects at most {total_arity} args, got {len(callee.args) + len(args)}")
            partial = callee
            callee = partial.callee
            args = [*partial.args, *args]
            continue

        if isinstance(callee, FunctionValue):
            if len(args) > len(callee.params):
                raise rt_error(
                    f"Function {callee.name} expects at most {len(callee.params)} args, got {len(args)}",
                    callee,
                )
            if len(args) < len(callee.params):
                return PartialFunction(callee=callee, args=list(args), remaining_arity=len(callee.params) - len(args))
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
            if len(args) > callee.arity:
                raise RuntimeError(f"Builtin {callee.name} expects at most {callee.arity} args, got {len(args)}")
            if len(args) < callee.arity:
                return PartialFunction(callee=callee, args=list(args), remaining_arity=callee.arity - len(args))
            try:
                return callee.fn(args)
            except RuntimeError as exc:
                raise RuntimeError(_normalize_builtin_runtime_error(callee.name, str(exc))) from exc
            except OSError as exc:
                detail = exc.strerror or str(exc)
                if exc.filename is not None:
                    detail = f"{detail}: {exc.filename}"
                raise RuntimeError(_builtin_runtime_message(callee.name, detail)) from exc
            except ValueError as exc:
                raise RuntimeError(_builtin_runtime_message(callee.name, str(exc))) from exc

        if isinstance(callee, ConstructorValue):
            if len(args) != callee.arity:
                raise RuntimeError(
                    f"Constructor {callee.name} expects {callee.arity} args, got {len(args)}"
                )
            return ADTValue(constructor=callee.name, args=tuple(args))

        raise RuntimeError("Attempted to call a non-function value")


def _is_callable(value: object) -> bool:
    return isinstance(value, (FunctionValue, BuiltinFunction, ConstructorValue, PartialFunction, ComposedFunction))


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
    if isinstance(pattern, ast.CharPattern):
        return {} if value == pattern.value else None
    if isinstance(pattern, ast.UnitPattern):
        return {} if value is None else None
    if isinstance(pattern, ast.TuplePattern):
        if not isinstance(value, TupleValue):
            return None
        if len(value.items) != len(pattern.items):
            return None
        merged: dict[str, object] = {}
        for sub_pattern, sub_value in zip(pattern.items, value.items):
            sub = match_pattern(sub_pattern, sub_value)
            if sub is None:
                return None
            merged.update(sub)
        return merged

    if isinstance(pattern, ast.ConstructorPattern):
        if not isinstance(value, ADTValue):
            return None
        if value.constructor != pattern.name and value.constructor.rsplit(".", 1)[-1] != pattern.name.rsplit(".", 1)[-1]:
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


def run_program(program: ast.Program, stdout: TextIO | None = None, argv: list[str] | None = None) -> None:
    import sys as _sys
    # The bootstrap typechecker is deeply recursive when interpreted; give it
    # more headroom than the default 1000 frames.
    if _sys.getrecursionlimit() < 20000:
        _sys.setrecursionlimit(20000)
    elaborate_program(program)
    out = stdout
    runtime_in = sys.stdin
    runtime_out = sys.stdout if out is None else out
    program_argv = [] if argv is None else argv
    env = Env()
    echo_backend = _build_echo_backend()
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

    def builtin_read_file(args: list[object]) -> object:
        raw_path = args[0]
        if not isinstance(raw_path, str):
            raise RuntimeError("read_file expects String path")
        if raw_path == "-":
            return runtime_in.read()
        return Path(raw_path).read_text(encoding="utf-8")

    def builtin_read_int_lines(args: list[object]) -> object:
        raw_path = args[0]
        if not isinstance(raw_path, str):
            raise RuntimeError("read_int_lines expects String path")
        lines = Path(raw_path).read_text(encoding="utf-8").splitlines()
        items: list[object] = []
        for line in lines:
            txt = line.strip()
            if txt == "":
                continue
            try:
                items.append(int(txt))
            except ValueError as exc:
                raise RuntimeError(f"read_int_lines invalid integer line {txt!r}") from exc
        return VectorValue(items=tuple(items))

    def builtin_env_get(args: list[object]) -> object:
        name = args[0]
        if not isinstance(name, str):
            raise RuntimeError("env_get expects String name")
        value = os.environ.get(name)
        if value is None:
            return ADTValue(constructor="Nothing", args=())
        return ADTValue(constructor="Just", args=(value,))

    def builtin_argv_get(args: list[object]) -> object:
        index = args[0]
        if not isinstance(index, int):
            raise RuntimeError("argv_get expects Int index")
        if index < 0 or index >= len(program_argv):
            return ADTValue(constructor="Nothing", args=())
        return ADTValue(constructor="Just", args=(program_argv[index],))

    def builtin_parse_int(args: list[object]) -> object:
        raw = args[0]
        if not isinstance(raw, str):
            raise RuntimeError("parse_int expects String")
        txt = raw.strip()
        try:
            return int(txt)
        except ValueError as exc:
            raise RuntimeError(f"parse_int invalid integer {txt!r}") from exc

    def builtin_split_words(args: list[object]) -> object:
        raw = args[0]
        if not isinstance(raw, str):
            raise RuntimeError("split_words expects String")
        tokens = raw.replace(",", " ").split()
        return py_to_adt_list(tokens)

    def builtin_int_range(args: list[object]) -> object:
        start = args[0]
        end = args[1]
        if not isinstance(start, int) or not isinstance(end, int):
            raise RuntimeError("int_range expects Int start and Int end")
        return IntRangeValue(start=start, end=end)

    def builtin_int_range_start(args: list[object]) -> object:
        value = args[0]
        if not isinstance(value, IntRangeValue):
            raise RuntimeError("int_range_start expects IntRange")
        return value.start

    def builtin_int_range_end(args: list[object]) -> object:
        value = args[0]
        if not isinstance(value, IntRangeValue):
            raise RuntimeError("int_range_end expects IntRange")
        return value.end

    def builtin_str_concat(args: list[object]) -> object:
        left = args[0]
        right = args[1]
        if not isinstance(left, str) or not isinstance(right, str):
            raise RuntimeError("str_concat expects String, String")
        return left + right

    def builtin_str_len(args: list[object]) -> object:
        raw = args[0]
        if not isinstance(raw, str):
            raise RuntimeError("str_len expects String")
        return len(raw)

    def builtin_str_slice(args: list[object]) -> object:
        raw = args[0]
        start = args[1]
        length = args[2]
        if not isinstance(raw, str):
            raise RuntimeError("str_slice expects String as first argument")
        if not isinstance(start, int) or not isinstance(length, int):
            raise RuntimeError("str_slice expects Int start and Int length")
        if start < 0 or length < 0:
            raise RuntimeError("str_slice start/length must be >= 0")
        if start >= len(raw):
            return ""
        return raw[start : start + length]

    def builtin_str_char_at(args: list[object]) -> object:
        raw = args[0]
        index = args[1]
        if not isinstance(raw, str):
            raise RuntimeError("str_char_at expects String as first argument")
        if not isinstance(index, int):
            raise RuntimeError("str_char_at expects Int index")
        if index < 0 or index >= len(raw):
            return ADTValue(constructor="Nothing", args=())
        return ADTValue(constructor="Just", args=(raw[index],))

    def builtin_str_find(args: list[object]) -> object:
        haystack = args[0]
        needle = args[1]
        if not isinstance(haystack, str) or not isinstance(needle, str):
            raise RuntimeError("str_find expects String, String")
        return haystack.find(needle)

    def builtin_str_starts_with(args: list[object]) -> object:
        raw = args[0]
        prefix = args[1]
        if not isinstance(raw, str) or not isinstance(prefix, str):
            raise RuntimeError("str_starts_with expects String, String")
        return raw.startswith(prefix)

    def builtin_int_to_string(args: list[object]) -> object:
        value = args[0]
        if not isinstance(value, int) or isinstance(value, bool):
            raise RuntimeError("int_to_string expects Int")
        return str(value)

    def builtin_char_to_string(args: list[object]) -> object:
        value = args[0]
        if not isinstance(value, str) or len(value) != 1:
            raise RuntimeError("char_to_string expects Char")
        return value

    def builtin_str_compare(args: list[object]) -> object:
        left = args[0]
        right = args[1]
        if not isinstance(left, str) or not isinstance(right, str):
            raise RuntimeError("str_compare expects String, String")
        return (left > right) - (left < right)

    def builtin_regex_validate(args: list[object]) -> object:
        pattern = args[0]
        if not isinstance(pattern, str):
            raise RuntimeError("regex_validate expects String")
        try:
            _compile_regex_pattern(pattern)
        except ValueError as exc:
            return ADTValue(constructor="Err", args=(str(exc),))
        return ADTValue(constructor="Ok", args=(None,))

    def builtin_regex_is_match(args: list[object]) -> object:
        pattern = args[0]
        text = args[1]
        if not isinstance(pattern, str) or not isinstance(text, str):
            raise RuntimeError("regex_is_match expects String, String")
        try:
            compiled = _compile_regex_pattern(pattern)
        except ValueError as exc:
            raise RuntimeError(f"regex_is_match: {exc}") from exc
        return compiled.search(text) is not None

    def builtin_regex_find_range(args: list[object]) -> object:
        pattern = args[0]
        text = args[1]
        if not isinstance(pattern, str) or not isinstance(text, str):
            raise RuntimeError("regex_find_range expects String, String")
        try:
            compiled = _compile_regex_pattern(pattern)
        except ValueError as exc:
            raise RuntimeError(f"regex_find_range: {exc}") from exc
        match = compiled.search(text)
        if match is None:
            return ADTValue(constructor="Nothing", args=())
        return ADTValue(
            constructor="Just",
            args=(IntRangeValue(start=match.start(), end=match.end()),),
        )

    def builtin_regex_replace_all_literal(args: list[object]) -> object:
        pattern = args[0]
        replacement = args[1]
        text = args[2]
        if not isinstance(pattern, str) or not isinstance(replacement, str) or not isinstance(text, str):
            raise RuntimeError("regex_replace_all_literal expects String, String, String")
        try:
            compiled = _compile_regex_pattern(pattern)
        except ValueError as exc:
            raise RuntimeError(f"regex_replace_all_literal: {exc}") from exc
        return compiled.sub(lambda _: replacement, text)

    def builtin_regex_escape(args: list[object]) -> object:
        raw = args[0]
        if not isinstance(raw, str):
            raise RuntimeError("regex_escape expects String")
        escaped: list[str] = []
        for ch in raw:
            if ch in ".^$*+?()[]{}|\\":
                escaped.append("\\")
            escaped.append(ch)
        return "".join(escaped)

    def builtin_bytes_empty(args: list[object]) -> object:
        return BytesValue(items=b"")

    def builtin_bytes_length(args: list[object]) -> object:
        value = args[0]
        if not isinstance(value, BytesValue):
            raise RuntimeError("bytes_length expects Bytes")
        return len(value.items)

    def builtin_bytes_get(args: list[object]) -> object:
        value = args[0]
        index = args[1]
        if not isinstance(value, BytesValue):
            raise RuntimeError("bytes_get expects Bytes")
        if not isinstance(index, int):
            raise RuntimeError("bytes_get expects Int index")
        if index < 0 or index >= len(value.items):
            return ADTValue(constructor="Nothing", args=())
        return ADTValue(constructor="Just", args=(value.items[index],))

    def builtin_bytes_slice(args: list[object]) -> object:
        value = args[0]
        start = args[1]
        count = args[2]
        if not isinstance(value, BytesValue):
            raise RuntimeError("bytes_slice expects Bytes")
        if not isinstance(start, int) or not isinstance(count, int):
            raise RuntimeError("bytes_slice expects Int start and Int count")
        if start < 0 or count < 0:
            raise RuntimeError("bytes_slice start/count must be >= 0")
        return BytesValue(items=value.items[start : start + count])

    def builtin_bytes_append(args: list[object]) -> object:
        left = args[0]
        right = args[1]
        if not isinstance(left, BytesValue) or not isinstance(right, BytesValue):
            raise RuntimeError("bytes_append expects Bytes")
        return BytesValue(items=left.items + right.items)

    def builtin_bytes_singleton(args: list[object]) -> object:
        value = args[0]
        if not isinstance(value, int):
            raise RuntimeError("bytes_singleton expects Int")
        if value < 0 or value > 255:
            raise RuntimeError("bytes_singleton expects byte in 0..255")
        return BytesValue(items=bytes([value]))

    def builtin_bytes_from_utf8(args: list[object]) -> object:
        raw = args[0]
        if not isinstance(raw, str):
            raise RuntimeError("bytes_from_utf8 expects String")
        return BytesValue(items=raw.encode("utf-8"))

    def builtin_bytes_to_utf8(args: list[object]) -> object:
        value = args[0]
        if not isinstance(value, BytesValue):
            raise RuntimeError("bytes_to_utf8 expects Bytes")
        try:
            decoded = value.items.decode("utf-8")
        except UnicodeDecodeError as exc:
            err = ADTValue(constructor="stdlib.bytes.Utf8DecodeError", args=(str(exc),))
            return ADTValue(constructor="Err", args=(err,))
        if "\x00" in decoded:
            err = ADTValue(
                constructor="stdlib.bytes.Utf8DecodeError",
                args=("decoded string contains NUL byte",),
            )
            return ADTValue(constructor="Err", args=(err,))
        return ADTValue(constructor="Ok", args=(decoded,))

    def builtin_bytes_builder_empty(args: list[object]) -> object:
        return BuilderValue(chunks=())

    def _builder_bytes_from_value(value: object) -> bytes:
        if isinstance(value, BytesValue):
            return value.items
        raise RuntimeError("builder_bytes expects Bytes")

    def builtin_bytes_builder_bytes(args: list[object]) -> object:
        value = args[0]
        return BuilderValue(chunks=(_builder_bytes_from_value(value),))

    def builtin_bytes_builder_byte(args: list[object]) -> object:
        value = args[0]
        if not isinstance(value, int):
            raise RuntimeError("builder_byte expects Int")
        if value < 0 or value > 255:
            raise RuntimeError("builder_byte expects byte in 0..255")
        return BuilderValue(chunks=(bytes([value]),))

    def _builder_mod_256(value: int) -> int:
        return value - (value // 256) * 256

    def builtin_bytes_builder_u16_be(args: list[object]) -> object:
        value = args[0]
        if not isinstance(value, int):
            raise RuntimeError("builder_u16_be expects Int")
        return BuilderValue(
            chunks=(
                bytes([_builder_mod_256(value // 256)]),
                bytes([_builder_mod_256(value)]),
            )
        )

    def builtin_bytes_builder_u32_be(args: list[object]) -> object:
        value = args[0]
        if not isinstance(value, int):
            raise RuntimeError("builder_u32_be expects Int")
        return BuilderValue(
            chunks=(
                bytes([_builder_mod_256(value // 16777216)]),
                bytes([_builder_mod_256(value // 65536)]),
                bytes([_builder_mod_256(value // 256)]),
                bytes([_builder_mod_256(value)]),
            )
        )

    def builtin_bytes_builder_append(args: list[object]) -> object:
        left = args[0]
        right = args[1]
        if not isinstance(left, BuilderValue) or not isinstance(right, BuilderValue):
            raise RuntimeError("builder_append expects Builder")
        return BuilderValue(chunks=left.chunks + right.chunks)

    def builtin_bytes_builder_build(args: list[object]) -> object:
        value = args[0]
        if not isinstance(value, BuilderValue):
            raise RuntimeError("builder_build expects Builder")
        return BytesValue(items=b"".join(value.chunks))

    def _crypto_ok(value: object) -> ADTValue:
        return ADTValue(constructor="Ok", args=(value,))

    def _crypto_err(constructor: str, payload: object | None = None) -> ADTValue:
        err = ADTValue(
            constructor=f"stdlib.crypto.{constructor}",
            args=() if payload is None else (payload,),
        )
        return ADTValue(constructor="Err", args=(err,))

    def builtin_crypto_sha256(args: list[object]) -> object:
        value = args[0]
        if not isinstance(value, BytesValue):
            raise RuntimeError("crypto_sha256 expects Bytes")
        return BytesValue(items=hashlib.sha256(value.items).digest())

    def builtin_crypto_hmac_sha256(args: list[object]) -> object:
        key = args[0]
        message = args[1]
        if not isinstance(key, BytesValue) or not isinstance(message, BytesValue):
            raise RuntimeError("crypto_hmac_sha256 expects Bytes key and Bytes message")
        return BytesValue(items=hmac.new(key.items, message.items, hashlib.sha256).digest())

    def builtin_crypto_base64_encode(args: list[object]) -> object:
        value = args[0]
        if not isinstance(value, BytesValue):
            raise RuntimeError("crypto_base64_encode expects Bytes")
        return base64.b64encode(value.items).decode("ascii")

    def builtin_crypto_base64_decode(args: list[object]) -> object:
        raw = args[0]
        if not isinstance(raw, str):
            raise RuntimeError("crypto_base64_decode expects String")
        try:
            decoded = base64.b64decode(raw, validate=True)
        except (binascii.Error, ValueError) as exc:
            return _crypto_err("Base64DecodeError", str(exc))
        return _crypto_ok(BytesValue(items=decoded))

    def builtin_crypto_bytes_xor(args: list[object]) -> object:
        left = args[0]
        right = args[1]
        if not isinstance(left, BytesValue) or not isinstance(right, BytesValue):
            raise RuntimeError("crypto_bytes_xor expects Bytes")
        if len(left.items) != len(right.items):
            return _crypto_err("BytesXorLengthMismatch", len(left.items))
        return _crypto_ok(BytesValue(items=bytes(a ^ b for a, b in zip(left.items, right.items))))

    def builtin_crypto_random_bytes(args: list[object]) -> object:
        count = args[0]
        if not isinstance(count, int):
            raise RuntimeError("crypto_random_bytes expects Int")
        if count < 0:
            return _crypto_err("CryptoInvalidArgument", "count must be >= 0")
        try:
            return _crypto_ok(BytesValue(items=os.urandom(count)))
        except (NotImplementedError, OSError, ValueError) as exc:
            return _crypto_err("CryptoUnavailable", str(exc))


    def builtin_vector_empty(args: list[object]) -> object:
        return VectorValue(items=())

    def builtin_vector_length(args: list[object]) -> object:
        vec = args[0]
        if not isinstance(vec, VectorValue):
            raise RuntimeError("vector_length expects Vector")
        return len(vec.items)

    def builtin_vector_get(args: list[object]) -> object:
        vec = args[0]
        index = args[1]
        if not isinstance(vec, VectorValue):
            raise RuntimeError("vector_get expects Vector")
        if not isinstance(index, int):
            raise RuntimeError("vector_get expects Int index")
        if index < 0 or index >= len(vec.items):
            return ADTValue(constructor="Nothing", args=())
        return ADTValue(constructor="Just", args=(vec.items[index],))

    def builtin_vector_set(args: list[object]) -> object:
        vec = args[0]
        index = args[1]
        value = args[2]
        if not isinstance(vec, VectorValue):
            raise RuntimeError("vector_set expects Vector")
        if not isinstance(index, int):
            raise RuntimeError("vector_set expects Int index")
        if index < 0 or index >= len(vec.items):
            return vec
        updated = list(vec.items)
        updated[index] = value
        return VectorValue(items=tuple(updated))

    def builtin_vector_append(args: list[object]) -> object:
        vec = args[0]
        value = args[1]
        if not isinstance(vec, VectorValue):
            raise RuntimeError("vector_append expects Vector")
        return VectorValue(items=vec.items + (value,))

    def builtin_vector_from_list(args: list[object]) -> object:
        # Walk the Sprout List (ADTValue with Cons/Nil), collect elements in order
        items: list[object] = []
        cur = args[0]
        while isinstance(cur, ADTValue) and cur.constructor == "Cons":
            items.append(cur.args[0])
            cur = cur.args[1]
        return VectorValue(items=tuple(items))

    def builtin_vector_sort_by_int(args: list[object]) -> object:
        return _vector_sort_by_int_from_decorated_list(args[0])

    def builtin_map_empty(args: list[object]) -> object:
        return MapValue(items={})

    def builtin_map_get(args: list[object]) -> object:
        map_value = args[0]
        key = args[1]
        if not isinstance(map_value, MapValue):
            raise RuntimeError("map_get expects Map")
        if not isinstance(key, str):
            raise RuntimeError("map_get expects String key")
        if key not in map_value.items:
            return ADTValue(constructor="Nothing", args=())
        return ADTValue(constructor="Just", args=(map_value.items[key],))

    def builtin_map_set(args: list[object]) -> object:
        map_value = args[0]
        key = args[1]
        value = args[2]
        if not isinstance(map_value, MapValue):
            raise RuntimeError("map_set expects Map")
        if not isinstance(key, str):
            raise RuntimeError("map_set expects String key")
        updated = dict(map_value.items)
        updated[key] = value
        return MapValue(items=updated)

    def builtin_map_remove(args: list[object]) -> object:
        map_value = args[0]
        key = args[1]
        if not isinstance(map_value, MapValue):
            raise RuntimeError("map_remove expects Map")
        if not isinstance(key, str):
            raise RuntimeError("map_remove expects String key")
        if key not in map_value.items:
            return map_value
        updated = dict(map_value.items)
        del updated[key]
        return MapValue(items=updated)

    def builtin_map_size(args: list[object]) -> object:
        map_value = args[0]
        if not isinstance(map_value, MapValue):
            raise RuntimeError("map_size expects Map")
        return len(map_value.items)

    def builtin_map_nth_key(args: list[object]) -> object:
        map_value = args[0]
        index = args[1]
        if not isinstance(map_value, MapValue):
            raise RuntimeError("map_nth_key expects Map")
        if not isinstance(index, int):
            raise RuntimeError("map_nth_key expects Int index")
        keys = list(map_value.items.keys())
        if index < 0 or index >= len(keys):
            return ADTValue(constructor="Nothing", args=())
        return ADTValue(constructor="Just", args=(keys[index],))

    def builtin_map_nth_value(args: list[object]) -> object:
        map_value = args[0]
        index = args[1]
        if not isinstance(map_value, MapValue):
            raise RuntimeError("map_nth_value expects Map")
        if not isinstance(index, int):
            raise RuntimeError("map_nth_value expects Int index")
        values = list(map_value.items.values())
        if index < 0 or index >= len(values):
            return ADTValue(constructor="Nothing", args=())
        return ADTValue(constructor="Just", args=(values[index],))

    def builtin_native_set_empty(args: list[object]) -> object:
        return SetValue(items=frozenset())

    def builtin_native_set_insert(args: list[object]) -> object:
        item = args[0]
        set_value = args[1]
        if not isinstance(set_value, SetValue):
            raise RuntimeError("native_set_insert expects NativeSet")
        if not isinstance(item, str):
            raise RuntimeError("native_set_insert expects String item")
        return SetValue(items=set_value.items | {item})

    def builtin_native_set_member(args: list[object]) -> object:
        item = args[0]
        set_value = args[1]
        if not isinstance(set_value, SetValue):
            raise RuntimeError("native_set_member expects NativeSet")
        if not isinstance(item, str):
            raise RuntimeError("native_set_member expects String item")
        return item in set_value.items

    def builtin_native_set_to_list(args: list[object]) -> object:
        set_value = args[0]
        if not isinstance(set_value, SetValue):
            raise RuntimeError("native_set_to_list expects NativeSet")
        result: object = ADTValue(constructor="Nil", args=())
        for item in sorted(set_value.items):
            result = ADTValue(constructor="Cons", args=(item, result))
        return result

    def builtin_native_set_size(args: list[object]) -> object:
        set_value = args[0]
        if not isinstance(set_value, SetValue):
            raise RuntimeError("native_set_size expects NativeSet")
        return len(set_value.items)

    def _parse_header_block(raw: str) -> list[tuple[str, str]]:
        headers: list[tuple[str, str]] = []
        lines = raw.replace("\r\n", "\n").split("\n")
        for line in lines:
            txt = line.strip()
            if txt == "":
                continue
            if ":" not in txt:
                raise RuntimeError("http_request headers must be 'Name: Value' lines")
            key, value = txt.split(":", 1)
            key = key.strip()
            value = value.strip()
            if key == "":
                raise RuntimeError("http_request header name cannot be empty")
            headers.append((key, value))
        return headers

    def _http_err(constructor: str, payload: object | None = None) -> ADTValue:
        err = ADTValue(constructor=f"stdlib.http.{constructor}", args=() if payload is None else (payload,))
        return ADTValue(constructor="Err", args=(err,))

    def builtin_http_request(args: list[object]) -> object:
        method = args[0]
        url = args[1]
        headers_raw = args[2]
        body = args[3]
        timeout_ms = args[4]
        if not isinstance(method, str):
            raise RuntimeError("http_request expects String method")
        if not isinstance(url, str):
            raise RuntimeError("http_request expects String url")
        if not isinstance(headers_raw, str):
            raise RuntimeError("http_request expects String headers")
        if not isinstance(body, str):
            raise RuntimeError("http_request expects String body")
        if not isinstance(timeout_ms, int):
            raise RuntimeError("http_request expects Int timeout_ms")
        if timeout_ms < 1:
            raise RuntimeError("http_request timeout_ms must be >= 1")

        method_name = method.upper()
        payload: bytes | None = body.encode("utf-8")
        if method_name in {"GET", "HEAD"} and body == "":
            payload = None

        try:
            request = urllib.request.Request(url=url, data=payload, method=method_name)
            for key, value in _parse_header_block(headers_raw):
                request.add_header(key, value)
            with urllib.request.urlopen(request, timeout=timeout_ms / 1000.0) as response:
                status = int(response.getcode() or 0)
                response_headers = "".join(f"{k}: {v}\r\n" for k, v in response.headers.items())
                response_body = response.read().decode("utf-8", errors="replace")
                resp = ADTValue(
                    constructor="stdlib.http.HttpResponse",
                    args=(status, response_headers, response_body),
                )
                return ADTValue(constructor="Ok", args=(resp,))
        except TimeoutError:
            return _http_err("HttpTimeout")
        except urllib.error.HTTPError as exc:
            return _http_err("HttpBadStatus", int(exc.code))
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                return _http_err("HttpTimeout")
            return _http_err("HttpNetwork", str(exc.reason))
        except ValueError as exc:
            return _http_err("HttpDecode", str(exc))

    def _json_to_adt(value: object) -> ADTValue:
        if value is None:
            return ADTValue(constructor="stdlib.json.JsonNull", args=())
        if isinstance(value, bool):
            return ADTValue(constructor="stdlib.json.JsonBool", args=(value,))
        if isinstance(value, int):
            return ADTValue(constructor="stdlib.json.JsonInt", args=(value,))
        if isinstance(value, str):
            return ADTValue(constructor="stdlib.json.JsonString", args=(value,))
        if isinstance(value, list):
            cursor = ADTValue(constructor="stdlib.json.JsonArrayNil", args=())
            for item in reversed(value):
                cursor = ADTValue(
                    constructor="stdlib.json.JsonArrayCons",
                    args=(_json_to_adt(item), cursor),
                )
            return ADTValue(constructor="stdlib.json.JsonArray", args=(cursor,))
        if isinstance(value, dict):
            cursor = ADTValue(constructor="stdlib.json.JsonObjectNil", args=())
            for key, item in reversed(list(value.items())):
                cursor = ADTValue(
                    constructor="stdlib.json.JsonObjectCons",
                    args=(str(key), _json_to_adt(item), cursor),
                )
            return ADTValue(constructor="stdlib.json.JsonObject", args=(cursor,))
        raise RuntimeError(f"json_parse unsupported value kind: {type(value).__name__}")

    def _json_ctor_matches(value: ADTValue, name: str) -> bool:
        return value.constructor == name or value.constructor == f"stdlib.json.{name}"

    def _adt_to_json(value: object) -> object:
        if not isinstance(value, ADTValue):
            raise RuntimeError("json_stringify expects Json")
        if _json_ctor_matches(value, "JsonNull"):
            return None
        if _json_ctor_matches(value, "JsonBool"):
            return value.args[0]
        if _json_ctor_matches(value, "JsonInt"):
            return value.args[0]
        if _json_ctor_matches(value, "JsonString"):
            return value.args[0]
        if _json_ctor_matches(value, "JsonArray"):
            items: list[object] = []
            cursor = value.args[0]
            while True:
                if not isinstance(cursor, ADTValue):
                    raise RuntimeError("json_stringify expects JsonArray")
                if _json_ctor_matches(cursor, "JsonArrayNil"):
                    return items
                if not _json_ctor_matches(cursor, "JsonArrayCons"):
                    raise RuntimeError("json_stringify expects JsonArray")
                items.append(_adt_to_json(cursor.args[0]))
                cursor = cursor.args[1]
        if _json_ctor_matches(value, "JsonObject"):
            out: dict[str, object] = {}
            cursor = value.args[0]
            while True:
                if not isinstance(cursor, ADTValue):
                    raise RuntimeError("json_stringify expects JsonObject")
                if _json_ctor_matches(cursor, "JsonObjectNil"):
                    return out
                if not _json_ctor_matches(cursor, "JsonObjectCons"):
                    raise RuntimeError("json_stringify expects JsonObject")
                key = cursor.args[0]
                if not isinstance(key, str):
                    raise RuntimeError("json_stringify expects String object keys")
                out[key] = _adt_to_json(cursor.args[1])
                cursor = cursor.args[2]
        raise RuntimeError("json_stringify expects Json")

    def builtin_json_parse(args: list[object]) -> object:
        raw = args[0]
        if not isinstance(raw, str):
            raise RuntimeError("json_parse expects String")
        try:
            parsed = json.loads(raw)
            return ADTValue(constructor="Ok", args=(_json_to_adt(parsed),))
        except json.JSONDecodeError as exc:
            err = ADTValue(constructor="stdlib.json.JsonDecode", args=(str(exc),))
            return ADTValue(constructor="Err", args=(err,))

    def builtin_json_stringify(args: list[object]) -> object:
        return json.dumps(_adt_to_json(args[0]), ensure_ascii=False, separators=(",", ":"))

    def _term_emit(text: str) -> None:
        if out is None:
            runtime_out.write(text)
            runtime_out.flush()
        else:
            out.write(text)

    def _term_is_interactive() -> bool:
        return getattr(runtime_in, "isatty", lambda: False)() and getattr(runtime_out, "isatty", lambda: False)()

    def builtin_term_clear(args: list[object]) -> object:
        _term_emit("\x1b[2J\x1b[H")
        return None

    def builtin_term_move(args: list[object]) -> object:
        row = args[0]
        col = args[1]
        if not isinstance(row, int) or not isinstance(col, int):
            raise RuntimeError("term_move expects Int row and Int col")
        if row < 1 or col < 1:
            raise RuntimeError("term_move row and col must be >= 1")
        _term_emit(f"\x1b[{row};{col}H")
        return None

    def builtin_term_hide_cursor(args: list[object]) -> object:
        _term_emit("\x1b[?25l")
        return None

    def builtin_term_show_cursor(args: list[object]) -> object:
        _term_emit("\x1b[?25h")
        return None

    def builtin_term_read_key(args: list[object]) -> object:
        def _normalize_key(ch: str) -> str:
            if ch == "":
                return ""
            if ch == "\x01":
                return "ctrl-a"
            if ch == "\x02":
                return "ctrl-b"
            if ch == "\x04":
                return "ctrl-d"
            if ch == "\x05":
                return "ctrl-e"
            if ch == "\x06":
                return "ctrl-f"
            if ch in {"\x7f", "\b"}:
                return "backspace"
            if ch == "\x1b":
                return "escape"
            if ch in {"\n", "\r"}:
                return "enter"
            if ch == "\t":
                return "tab"
            return ch

        interactive_term = _term_is_interactive()
        if interactive_term:
            try:
                import termios
                import tty

                fd = runtime_in.fileno()
                old_settings = termios.tcgetattr(fd)
                try:
                    tty.setraw(fd)
                    ch = runtime_in.read(1)
                finally:
                    termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
                return _normalize_key(ch)
            except (ImportError, OSError, AttributeError):
                pass
        ch = runtime_in.read(1)
        return _normalize_key(ch)

    def builtin_term_read_line(args: list[object]) -> object:
        line = runtime_in.readline()
        if line == "":
            return ADTValue(constructor="Nothing", args=())
        if line.endswith("\n"):
            line = line[:-1]
        if line.endswith("\r"):
            line = line[:-1]
        return ADTValue(constructor="Just", args=(line,))

    def builtin_term_is_interactive(args: list[object]) -> object:
        return _term_is_interactive()

    def _repl_ok(value: object) -> ADTValue:
        return ADTValue(constructor="Ok", args=(value,))

    def _repl_err(message: str) -> ADTValue:
        return ADTValue(constructor="Err", args=(message,))

    def _repl_vec_string(items: tuple[str, ...] | list[str]) -> ADTValue:
        return ADTValue(constructor="Vec", args=(VectorValue(items=tuple(items)),))

    def _repl_vec_diagnostic(items: tuple[tuple[str, int, int], ...] | list[tuple[str, int, int]]) -> ADTValue:
        return ADTValue(
            constructor="Vec",
            args=(
                VectorValue(
                    items=tuple(
                        TupleValue(items=(message, line, column))
                        for message, line, column in items
                    )
                ),
            ),
        )

    def _repl_wrap(action: Callable[[], object]) -> ADTValue:
        try:
            return _repl_ok(action())
        except (
            ParseError,
            TokenizeError,
            TypeCheckError,
            RuntimeError,
            ModuleLoadError,
            SurfaceCheckError,
            TypeclassLoweringError,
        ) as exc:
            return _repl_err(str(exc))

    def _default_execution_backend():
        from .analysis_backend_python import default_execution_backend

        return default_execution_backend()

    def _default_snapshot_backend():
        from .analysis_backend_python import default_snapshot_backend

        return default_snapshot_backend()

    def _default_completion_backend():
        from .analysis_backend_python import default_completion_backend

        return default_completion_backend()

    def _to_string_list(value: object, label: str, *, builtin: str) -> list[str]:
        if not isinstance(value, ADTValue) or value.constructor != "Vec" or len(value.args) != 1 or not isinstance(value.args[0], VectorValue):
            raise RuntimeError(f"{builtin} expects {label} to be Vec String")
        items: list[str] = []
        for item in value.args[0].items:
            if not isinstance(item, str):
                raise RuntimeError(f"{builtin} expects {label} to contain only String values")
            items.append(item)
        return items

    def builtin_repl_add_import(args: list[object]) -> object:
        source = args[0]
        if not isinstance(source, str):
            raise RuntimeError("repl_add_import expects String")
        from .repl_host import hosted_repl_session

        return _repl_wrap(lambda: hosted_repl_session().add_import(source))

    def builtin_repl_add_declaration(args: list[object]) -> object:
        source = args[0]
        if not isinstance(source, str):
            raise RuntimeError("repl_add_declaration expects String")
        from .repl_host import hosted_repl_session

        return _repl_wrap(lambda: hosted_repl_session().add_declaration(source))

    def builtin_repl_eval_expr(args: list[object]) -> object:
        source = args[0]
        if not isinstance(source, str):
            raise RuntimeError("repl_eval_expr expects String")
        from .repl_host import hosted_repl_session

        return _repl_wrap(lambda: _repl_vec_string(hosted_repl_session().eval_expression_lines(source)))

    def builtin_repl_eval_expr_in_source(args: list[object]) -> object:
        module_source = args[0]
        expr = args[1]
        if not isinstance(module_source, str) or not isinstance(expr, str):
            raise RuntimeError("repl_eval_expr_in_source expects String module source and String expression")

        return _repl_wrap(lambda: _repl_vec_string(_default_execution_backend().eval_expr_in_source(module_source, expr)))

    def builtin_analysis_eval_expr_in_source(args: list[object]) -> object:
        module_source = args[0]
        expr = args[1]
        if not isinstance(module_source, str) or not isinstance(expr, str):
            raise RuntimeError("analysis_eval_expr_in_source expects String module source and String expression")

        return _repl_wrap(lambda: _repl_vec_string(_default_execution_backend().eval_expr_in_source(module_source, expr)))

    def builtin_repl_check_source(args: list[object]) -> object:
        source = args[0]
        if not isinstance(source, str):
            raise RuntimeError("repl_check_source expects String module source")

        return _repl_wrap(lambda: _default_execution_backend().check_source(source))

    def builtin_analysis_check_source(args: list[object]) -> object:
        source = args[0]
        if not isinstance(source, str):
            raise RuntimeError("analysis_check_source expects String module source")

        return _repl_wrap(lambda: _default_execution_backend().check_source(source))

    def builtin_repl_declared_names_in_source(args: list[object]) -> object:
        source = args[0]
        if not isinstance(source, str):
            raise RuntimeError("repl_declared_names_in_source expects String module source")

        return _repl_wrap(lambda: _repl_vec_string(_default_snapshot_backend().declared_names_in_source(source)))

    def builtin_analysis_declared_names_in_source(args: list[object]) -> object:
        source = args[0]
        if not isinstance(source, str):
            raise RuntimeError("analysis_declared_names_in_source expects String module source")

        return _repl_wrap(lambda: _repl_vec_string(_default_snapshot_backend().declared_names_in_source(source)))

    def builtin_repl_exported_names_in_source(args: list[object]) -> object:
        source = args[0]
        if not isinstance(source, str):
            raise RuntimeError("repl_exported_names_in_source expects String module source")

        return _repl_wrap(lambda: _repl_vec_string(_default_snapshot_backend().exported_names_in_source(source)))

    def builtin_analysis_exported_names_in_source(args: list[object]) -> object:
        source = args[0]
        if not isinstance(source, str):
            raise RuntimeError("analysis_exported_names_in_source expects String module source")

        return _repl_wrap(lambda: _repl_vec_string(_default_snapshot_backend().exported_names_in_source(source)))

    def builtin_repl_symbol_inventory_in_source(args: list[object]) -> object:
        source = args[0]
        if not isinstance(source, str):
            raise RuntimeError("repl_symbol_inventory_in_source expects String module source")

        def _inventory() -> TupleValue:
            declared, imported, exported = _default_snapshot_backend().symbol_inventory_in_source(source)
            return TupleValue(
                items=(
                    _repl_vec_string(declared),
                    _repl_vec_string(imported),
                    _repl_vec_string(exported),
                )
            )

        return _repl_wrap(_inventory)

    def builtin_analysis_symbol_inventory_in_source(args: list[object]) -> object:
        source = args[0]
        if not isinstance(source, str):
            raise RuntimeError("analysis_symbol_inventory_in_source expects String module source")

        def _inventory() -> TupleValue:
            declared, imported, exported = _default_snapshot_backend().symbol_inventory_in_source(source)
            return TupleValue(
                items=(
                    _repl_vec_string(declared),
                    _repl_vec_string(imported),
                    _repl_vec_string(exported),
                )
            )

        return _repl_wrap(_inventory)

    def builtin_analysis_symbol_locations_in_source(args: list[object]) -> object:
        source = args[0]
        if not isinstance(source, str):
            raise RuntimeError("analysis_symbol_locations_in_source expects String module source")

        def _locations() -> ADTValue:
            return ADTValue(
                constructor="Vec",
                args=(
                    VectorValue(
                        tuple(
                            TupleValue(items=(category, name, line, column))
                            for category, name, line, column in _default_snapshot_backend().symbol_locations_in_source(source)
                        )
                    ),
                ),
            )

        return _repl_wrap(_locations)

    def builtin_repl_diagnostics_in_source(args: list[object]) -> object:
        source = args[0]
        if not isinstance(source, str):
            raise RuntimeError("repl_diagnostics_in_source expects String module source")

        return _repl_vec_diagnostic(_default_snapshot_backend().diagnostics_in_source(source))

    def builtin_analysis_diagnostics_in_source(args: list[object]) -> object:
        source = args[0]
        if not isinstance(source, str):
            raise RuntimeError("analysis_diagnostics_in_source expects String module source")

        return _repl_vec_diagnostic(_default_snapshot_backend().diagnostics_in_source(source))

    def builtin_repl_type_of(args: list[object]) -> object:
        source = args[0]
        if not isinstance(source, str):
            raise RuntimeError("repl_type_of expects String")
        from .repl_host import hosted_repl_session

        return _repl_wrap(lambda: hosted_repl_session().infer_type(source))

    def builtin_repl_type_of_in_source(args: list[object]) -> object:
        module_source = args[0]
        expr = args[1]
        if not isinstance(module_source, str) or not isinstance(expr, str):
            raise RuntimeError("repl_type_of_in_source expects String module source and String expression")

        return _repl_wrap(lambda: _default_execution_backend().type_of_in_source(module_source, expr))

    def builtin_analysis_type_of_in_source(args: list[object]) -> object:
        module_source = args[0]
        expr = args[1]
        if not isinstance(module_source, str) or not isinstance(expr, str):
            raise RuntimeError("analysis_type_of_in_source expects String module source and String expression")

        return _repl_wrap(lambda: _default_execution_backend().type_of_in_source(module_source, expr))

    def builtin_repl_instances(args: list[object]) -> object:
        source = args[0]
        if not isinstance(source, str):
            raise RuntimeError("repl_instances expects String")
        from .repl_host import hosted_repl_session

        def _instances() -> TupleValue:
            query_type, matches = hosted_repl_session().instances_for_type(source)
            return TupleValue(items=(query_type, _repl_vec_string(matches)))

        return _repl_wrap(_instances)

    def builtin_repl_instances_in_source(args: list[object]) -> object:
        module_source = args[0]
        type_expr_source = args[1]
        if not isinstance(module_source, str) or not isinstance(type_expr_source, str):
            raise RuntimeError("repl_instances_in_source expects String module source and String type")

        def _instances() -> TupleValue:
            query_type, matches = _default_execution_backend().instances_in_source(module_source, type_expr_source)
            return TupleValue(items=(query_type, _repl_vec_string(matches)))

        return _repl_wrap(_instances)

    def builtin_analysis_instances_in_source(args: list[object]) -> object:
        module_source = args[0]
        type_expr_source = args[1]
        if not isinstance(module_source, str) or not isinstance(type_expr_source, str):
            raise RuntimeError("analysis_instances_in_source expects String module source and String type")

        def _instances() -> TupleValue:
            query_type, matches = _default_execution_backend().instances_in_source(module_source, type_expr_source)
            return TupleValue(items=(query_type, _repl_vec_string(matches)))

        return _repl_wrap(_instances)

    def builtin_repl_complete(args: list[object]) -> object:
        line_buffer = args[0]
        if not isinstance(line_buffer, str):
            raise RuntimeError("repl_complete expects String")
        from .repl_host import hosted_repl_session

        prefix, matches = hosted_repl_session().completion_candidates(line_buffer)
        return TupleValue(items=(prefix, _repl_vec_string(matches)))

    def builtin_repl_complete_in_state(args: list[object]) -> object:
        line_buffer = args[0]
        imports = args[1]
        declarations = args[2]
        if not isinstance(line_buffer, str):
            raise RuntimeError("repl_complete_in_state expects String line buffer and Vec String imports/declarations")
        prefix, matches = _default_completion_backend().complete_in_state(
            line_buffer,
            _to_string_list(imports, "imports", builtin="repl_complete_in_state"),
            _to_string_list(declarations, "declarations", builtin="repl_complete_in_state"),
        )
        return TupleValue(items=(prefix, _repl_vec_string(matches)))

    def builtin_analysis_complete_in_state(args: list[object]) -> object:
        line_buffer = args[0]
        imports = args[1]
        declarations = args[2]
        if not isinstance(line_buffer, str):
            raise RuntimeError("analysis_complete_in_state expects String line buffer and Vec String imports/declarations")
        prefix, matches = _default_completion_backend().complete_in_state(
            line_buffer,
            _to_string_list(imports, "imports", builtin="analysis_complete_in_state"),
            _to_string_list(declarations, "declarations", builtin="analysis_complete_in_state"),
        )
        return TupleValue(items=(prefix, _repl_vec_string(matches)))

    def builtin_repl_reset_session(args: list[object]) -> object:
        from .repl_host import reset_hosted_repl_session

        reset_hosted_repl_session()
        return None

    def builtin_term_write(args: list[object]) -> object:
        text = args[0]
        if not isinstance(text, str):
            raise RuntimeError("term_write expects String")
        _term_emit(text)
        return None

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

    def _tcp_ok(value: object) -> ADTValue:
        return ADTValue(constructor="Ok", args=(value,))

    def _tcp_err(constructor: str, payload: object | None = None) -> ADTValue:
        err = ADTValue(constructor=f"stdlib.net.{constructor}", args=() if payload is None else (payload,))
        return ADTValue(constructor="Err", args=(err,))

    def builtin_tcp_connect(args: list[object]) -> object:
        host = args[0]
        port = args[1]
        if not isinstance(host, str):
            raise RuntimeError("tcp_connect expects String host")
        if not isinstance(port, int):
            raise RuntimeError("tcp_connect expects Int port")
        if port < 1 or port > 65535:
            return _tcp_err("TcpInvalidArgument", "port must be in 1..65535")
        try:
            conn = socket.create_connection((host, port), timeout=1.0)
        except OSError as exc:
            return _tcp_err("TcpConnectFailed", str(exc))
        handle = alloc_handle()
        connections[handle] = conn
        return _tcp_ok(handle)

    def builtin_tcp_read_exact(args: list[object]) -> object:
        conn_handle = args[0]
        count = args[1]
        if not isinstance(conn_handle, int):
            raise RuntimeError("tcp_read_exact expects Int connection handle")
        if not isinstance(count, int):
            raise RuntimeError("tcp_read_exact expects Int count")
        if count < 0:
            return _tcp_err("TcpInvalidArgument", "count must be >= 0")
        conn = connections.get(conn_handle)
        if conn is None:
            return _tcp_err("TcpInvalidHandle")
        chunks: list[bytes] = []
        remaining = count
        try:
            while remaining > 0:
                chunk = conn.recv(remaining)
                if chunk == b"":
                    return _tcp_err("TcpEndOfStream")
                chunks.append(chunk)
                remaining -= len(chunk)
        except OSError as exc:
            return _tcp_err("TcpReadFailed", str(exc))
        return _tcp_ok(BytesValue(items=b"".join(chunks)))

    def builtin_tcp_write_all(args: list[object]) -> object:
        conn_handle = args[0]
        payload = args[1]
        if not isinstance(conn_handle, int):
            raise RuntimeError("tcp_write_all expects Int connection handle")
        if not isinstance(payload, BytesValue):
            raise RuntimeError("tcp_write_all expects Bytes payload")
        conn = connections.get(conn_handle)
        if conn is None:
            return _tcp_err("TcpInvalidHandle")
        raw = payload.items
        try:
            conn.sendall(raw)
        except OSError as exc:
            return _tcp_err("TcpWriteFailed", str(exc))
        return _tcp_ok(len(raw))

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

    def builtin_tcp_echo_serve(args: list[object]) -> object:
        port = args[0]
        max_connections = args[1]
        if not isinstance(port, int):
            raise RuntimeError("tcp_echo_serve expects Int port")
        if not isinstance(max_connections, int):
            raise RuntimeError("tcp_echo_serve expects Int max_connections")
        if port < 1 or port > 65535:
            raise RuntimeError("tcp_echo_serve port must be in 1..65535")
        if max_connections < 1:
            raise RuntimeError("tcp_echo_serve max_connections must be >= 1")
        echo_backend.serve_echo(port, max_connections)
        return None

    def builtin_ref_new(args: list[object]) -> object:
        return RefValue(value=args[0])

    def builtin_ref_read(args: list[object]) -> object:
        r = args[0]
        if not isinstance(r, RefValue):
            raise RuntimeError("ref_read: not a Ref")
        return r.value

    def builtin_ref_write(args: list[object]) -> object:
        r, val = args[0], args[1]
        if not isinstance(r, RefValue):
            raise RuntimeError("ref_write: not a Ref")
        r.value = val
        return None

    env.set("ref_new", BuiltinFunction(name="ref_new", arity=1, fn=builtin_ref_new))
    env.set("ref_read", BuiltinFunction(name="ref_read", arity=1, fn=builtin_ref_read))
    env.set("ref_write", BuiltinFunction(name="ref_write", arity=2, fn=builtin_ref_write))

    env.set("print", BuiltinFunction(name="print", arity=1, fn=builtin_print))
    env.set("print_int", BuiltinFunction(name="print_int", arity=1, fn=builtin_print_int))
    env.set("read_lines", BuiltinFunction(name="read_lines", arity=1, fn=builtin_read_lines))
    env.set("read_file", BuiltinFunction(name="read_file", arity=1, fn=builtin_read_file))
    env.set("read_int_lines", BuiltinFunction(name="read_int_lines", arity=1, fn=builtin_read_int_lines))
    env.set("env_get", BuiltinFunction(name="env_get", arity=1, fn=builtin_env_get))
    env.set("argv_get", BuiltinFunction(name="argv_get", arity=1, fn=builtin_argv_get))
    env.set("parse_int", BuiltinFunction(name="parse_int", arity=1, fn=builtin_parse_int))
    env.set("int_to_string", BuiltinFunction(name="int_to_string", arity=1, fn=builtin_int_to_string))
    env.set("char_to_string", BuiltinFunction(name="char_to_string", arity=1, fn=builtin_char_to_string))
    env.set("split_words", BuiltinFunction(name="split_words", arity=1, fn=builtin_split_words))
    env.set("int_range", BuiltinFunction(name="int_range", arity=2, fn=builtin_int_range))
    env.set("int_range_start", BuiltinFunction(name="int_range_start", arity=1, fn=builtin_int_range_start))
    env.set("int_range_end", BuiltinFunction(name="int_range_end", arity=1, fn=builtin_int_range_end))
    env.set("str_concat", BuiltinFunction(name="str_concat", arity=2, fn=builtin_str_concat))
    env.set("str_len", BuiltinFunction(name="str_len", arity=1, fn=builtin_str_len))
    env.set("str_slice", BuiltinFunction(name="str_slice", arity=3, fn=builtin_str_slice))
    env.set("str_char_at", BuiltinFunction(name="str_char_at", arity=2, fn=builtin_str_char_at))
    env.set("str_find", BuiltinFunction(name="str_find", arity=2, fn=builtin_str_find))
    env.set(
        "str_starts_with",
        BuiltinFunction(name="str_starts_with", arity=2, fn=builtin_str_starts_with),
    )
    env.set("str_compare", BuiltinFunction(name="str_compare", arity=2, fn=builtin_str_compare))
    env.set("regex_validate", BuiltinFunction(name="regex_validate", arity=1, fn=builtin_regex_validate))
    env.set("regex_is_match", BuiltinFunction(name="regex_is_match", arity=2, fn=builtin_regex_is_match))
    env.set("regex_find_range", BuiltinFunction(name="regex_find_range", arity=2, fn=builtin_regex_find_range))
    env.set(
        "regex_replace_all_literal",
        BuiltinFunction(name="regex_replace_all_literal", arity=3, fn=builtin_regex_replace_all_literal),
    )
    env.set("regex_escape", BuiltinFunction(name="regex_escape", arity=1, fn=builtin_regex_escape))
    env.set("bytes_empty", BuiltinFunction(name="bytes_empty", arity=0, fn=builtin_bytes_empty))
    env.set("bytes_length", BuiltinFunction(name="bytes_length", arity=1, fn=builtin_bytes_length))
    env.set("bytes_get", BuiltinFunction(name="bytes_get", arity=2, fn=builtin_bytes_get))
    env.set("bytes_slice", BuiltinFunction(name="bytes_slice", arity=3, fn=builtin_bytes_slice))
    env.set("bytes_append", BuiltinFunction(name="bytes_append", arity=2, fn=builtin_bytes_append))
    env.set("bytes_singleton", BuiltinFunction(name="bytes_singleton", arity=1, fn=builtin_bytes_singleton))
    env.set("bytes_from_utf8", BuiltinFunction(name="bytes_from_utf8", arity=1, fn=builtin_bytes_from_utf8))
    env.set("bytes_to_utf8", BuiltinFunction(name="bytes_to_utf8", arity=1, fn=builtin_bytes_to_utf8))
    env.set(
        "bytes_builder_empty",
        BuiltinFunction(name="bytes_builder_empty", arity=0, fn=builtin_bytes_builder_empty),
    )
    env.set(
        "bytes_builder_bytes",
        BuiltinFunction(name="bytes_builder_bytes", arity=1, fn=builtin_bytes_builder_bytes),
    )
    env.set(
        "bytes_builder_byte",
        BuiltinFunction(name="bytes_builder_byte", arity=1, fn=builtin_bytes_builder_byte),
    )
    env.set(
        "bytes_builder_u16_be",
        BuiltinFunction(name="bytes_builder_u16_be", arity=1, fn=builtin_bytes_builder_u16_be),
    )
    env.set(
        "bytes_builder_u32_be",
        BuiltinFunction(name="bytes_builder_u32_be", arity=1, fn=builtin_bytes_builder_u32_be),
    )
    env.set(
        "bytes_builder_append",
        BuiltinFunction(name="bytes_builder_append", arity=2, fn=builtin_bytes_builder_append),
    )
    env.set(
        "bytes_builder_build",
        BuiltinFunction(name="bytes_builder_build", arity=1, fn=builtin_bytes_builder_build),
    )
    env.set("crypto_sha256", BuiltinFunction(name="crypto_sha256", arity=1, fn=builtin_crypto_sha256))
    env.set(
        "crypto_hmac_sha256",
        BuiltinFunction(name="crypto_hmac_sha256", arity=2, fn=builtin_crypto_hmac_sha256),
    )
    env.set(
        "crypto_base64_encode",
        BuiltinFunction(name="crypto_base64_encode", arity=1, fn=builtin_crypto_base64_encode),
    )
    env.set(
        "crypto_base64_decode",
        BuiltinFunction(name="crypto_base64_decode", arity=1, fn=builtin_crypto_base64_decode),
    )
    env.set(
        "crypto_bytes_xor",
        BuiltinFunction(name="crypto_bytes_xor", arity=2, fn=builtin_crypto_bytes_xor),
    )
    env.set(
        "crypto_random_bytes",
        BuiltinFunction(name="crypto_random_bytes", arity=1, fn=builtin_crypto_random_bytes),
    )
    env.set("vector_empty", BuiltinFunction(name="vector_empty", arity=0, fn=builtin_vector_empty))
    env.set("vector_length", BuiltinFunction(name="vector_length", arity=1, fn=builtin_vector_length))
    env.set("vector_get", BuiltinFunction(name="vector_get", arity=2, fn=builtin_vector_get))
    env.set("vector_set", BuiltinFunction(name="vector_set", arity=3, fn=builtin_vector_set))
    env.set("vector_append", BuiltinFunction(name="vector_append", arity=2, fn=builtin_vector_append))
    env.set("vector_from_list", BuiltinFunction(name="vector_from_list", arity=1, fn=builtin_vector_from_list))
    env.set("vector_sort_by_int", BuiltinFunction(name="vector_sort_by_int", arity=1, fn=builtin_vector_sort_by_int))
    env.set("map_empty", BuiltinFunction(name="map_empty", arity=0, fn=builtin_map_empty))
    env.set("map_get", BuiltinFunction(name="map_get", arity=2, fn=builtin_map_get))
    env.set("map_set", BuiltinFunction(name="map_set", arity=3, fn=builtin_map_set))
    env.set("map_remove", BuiltinFunction(name="map_remove", arity=2, fn=builtin_map_remove))
    env.set("map_size", BuiltinFunction(name="map_size", arity=1, fn=builtin_map_size))
    env.set("map_nth_key", BuiltinFunction(name="map_nth_key", arity=2, fn=builtin_map_nth_key))
    env.set("map_nth_value", BuiltinFunction(name="map_nth_value", arity=2, fn=builtin_map_nth_value))
    env.set("native_set_empty", BuiltinFunction(name="native_set_empty", arity=0, fn=builtin_native_set_empty))
    env.set("native_set_insert", BuiltinFunction(name="native_set_insert", arity=2, fn=builtin_native_set_insert))
    env.set("native_set_member", BuiltinFunction(name="native_set_member", arity=2, fn=builtin_native_set_member))
    env.set("native_set_to_list", BuiltinFunction(name="native_set_to_list", arity=1, fn=builtin_native_set_to_list))
    env.set("native_set_size", BuiltinFunction(name="native_set_size", arity=1, fn=builtin_native_set_size))
    env.set("http_request", BuiltinFunction(name="http_request", arity=5, fn=builtin_http_request))
    env.set("json_parse", BuiltinFunction(name="json_parse", arity=1, fn=builtin_json_parse))
    env.set("json_stringify", BuiltinFunction(name="json_stringify", arity=1, fn=builtin_json_stringify))
    env.set("term_clear", BuiltinFunction(name="term_clear", arity=0, fn=builtin_term_clear))
    env.set("term_move", BuiltinFunction(name="term_move", arity=2, fn=builtin_term_move))
    env.set("term_hide_cursor", BuiltinFunction(name="term_hide_cursor", arity=0, fn=builtin_term_hide_cursor))
    env.set("term_show_cursor", BuiltinFunction(name="term_show_cursor", arity=0, fn=builtin_term_show_cursor))
    env.set("term_read_key", BuiltinFunction(name="term_read_key", arity=0, fn=builtin_term_read_key))
    env.set("term_read_line", BuiltinFunction(name="term_read_line", arity=0, fn=builtin_term_read_line))
    env.set("term_is_interactive", BuiltinFunction(name="term_is_interactive", arity=0, fn=builtin_term_is_interactive))
    env.set("repl_add_import", BuiltinFunction(name="repl_add_import", arity=1, fn=builtin_repl_add_import))
    env.set(
        "repl_add_declaration",
        BuiltinFunction(name="repl_add_declaration", arity=1, fn=builtin_repl_add_declaration),
    )
    env.set("repl_eval_expr", BuiltinFunction(name="repl_eval_expr", arity=1, fn=builtin_repl_eval_expr))
    env.set("repl_eval_expr_in_source", BuiltinFunction(name="repl_eval_expr_in_source", arity=2, fn=builtin_repl_eval_expr_in_source))
    env.set(
        "analysis_eval_expr_in_source",
        BuiltinFunction(name="analysis_eval_expr_in_source", arity=2, fn=builtin_analysis_eval_expr_in_source),
    )
    env.set("repl_check_source", BuiltinFunction(name="repl_check_source", arity=1, fn=builtin_repl_check_source))
    env.set("analysis_check_source", BuiltinFunction(name="analysis_check_source", arity=1, fn=builtin_analysis_check_source))
    env.set("repl_declared_names_in_source", BuiltinFunction(name="repl_declared_names_in_source", arity=1, fn=builtin_repl_declared_names_in_source))
    env.set("analysis_declared_names_in_source", BuiltinFunction(name="analysis_declared_names_in_source", arity=1, fn=builtin_analysis_declared_names_in_source))
    env.set("repl_exported_names_in_source", BuiltinFunction(name="repl_exported_names_in_source", arity=1, fn=builtin_repl_exported_names_in_source))
    env.set("analysis_exported_names_in_source", BuiltinFunction(name="analysis_exported_names_in_source", arity=1, fn=builtin_analysis_exported_names_in_source))
    env.set("repl_symbol_inventory_in_source", BuiltinFunction(name="repl_symbol_inventory_in_source", arity=1, fn=builtin_repl_symbol_inventory_in_source))
    env.set("analysis_symbol_inventory_in_source", BuiltinFunction(name="analysis_symbol_inventory_in_source", arity=1, fn=builtin_analysis_symbol_inventory_in_source))
    env.set("analysis_symbol_locations_in_source", BuiltinFunction(name="analysis_symbol_locations_in_source", arity=1, fn=builtin_analysis_symbol_locations_in_source))
    env.set("repl_diagnostics_in_source", BuiltinFunction(name="repl_diagnostics_in_source", arity=1, fn=builtin_repl_diagnostics_in_source))
    env.set("analysis_diagnostics_in_source", BuiltinFunction(name="analysis_diagnostics_in_source", arity=1, fn=builtin_analysis_diagnostics_in_source))
    env.set("repl_type_of", BuiltinFunction(name="repl_type_of", arity=1, fn=builtin_repl_type_of))
    env.set("repl_type_of_in_source", BuiltinFunction(name="repl_type_of_in_source", arity=2, fn=builtin_repl_type_of_in_source))
    env.set("analysis_type_of_in_source", BuiltinFunction(name="analysis_type_of_in_source", arity=2, fn=builtin_analysis_type_of_in_source))
    env.set("repl_instances", BuiltinFunction(name="repl_instances", arity=1, fn=builtin_repl_instances))
    env.set("repl_instances_in_source", BuiltinFunction(name="repl_instances_in_source", arity=2, fn=builtin_repl_instances_in_source))
    env.set("analysis_instances_in_source", BuiltinFunction(name="analysis_instances_in_source", arity=2, fn=builtin_analysis_instances_in_source))
    env.set("repl_complete", BuiltinFunction(name="repl_complete", arity=1, fn=builtin_repl_complete))
    env.set("repl_complete_in_state", BuiltinFunction(name="repl_complete_in_state", arity=3, fn=builtin_repl_complete_in_state))
    env.set(
        "analysis_complete_in_state",
        BuiltinFunction(name="analysis_complete_in_state", arity=3, fn=builtin_analysis_complete_in_state),
    )
    env.set("repl_reset_session", BuiltinFunction(name="repl_reset_session", arity=0, fn=builtin_repl_reset_session))
    env.set("term_write", BuiltinFunction(name="term_write", arity=1, fn=builtin_term_write))
    env.set("tcp_listen", BuiltinFunction(name="tcp_listen", arity=1, fn=builtin_tcp_listen))
    env.set("tcp_accept", BuiltinFunction(name="tcp_accept", arity=1, fn=builtin_tcp_accept))
    env.set("tcp_read", BuiltinFunction(name="tcp_read", arity=1, fn=builtin_tcp_read))
    env.set("tcp_write", BuiltinFunction(name="tcp_write", arity=2, fn=builtin_tcp_write))
    env.set("tcp_connect", BuiltinFunction(name="tcp_connect", arity=2, fn=builtin_tcp_connect))
    env.set("tcp_read_exact", BuiltinFunction(name="tcp_read_exact", arity=2, fn=builtin_tcp_read_exact))
    env.set("tcp_write_all", BuiltinFunction(name="tcp_write_all", arity=2, fn=builtin_tcp_write_all))
    env.set("tcp_close", BuiltinFunction(name="tcp_close", arity=1, fn=builtin_tcp_close))
    env.set(
        "tcp_close_listener",
        BuiltinFunction(name="tcp_close_listener", arity=1, fn=builtin_tcp_close_listener),
    )
    env.set("tcp_echo_serve", BuiltinFunction(name="tcp_echo_serve", arity=2, fn=builtin_tcp_echo_serve))

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

    for decl in program.declarations:
        if isinstance(decl, ast.LetDecl):
            env.set(decl.name, eval_expr(decl.value, env))

    try:
        entry_name = "main" if "main" in env.values else next(
            (name for name in env.values if name.endswith(".main")),
            None,
        )
        if entry_name is not None:
            main = env.get(entry_name)
            if isinstance(main, FunctionValue):
                result = apply_callable(main, [])
                if isinstance(result, PartialFunction):
                    total_arity = len(result.args) + result.remaining_arity
                    raise rt_error(f"Function {main.name} expects {total_arity} args, got 0", main)
    finally:
        for conn in connections.values():
            conn.close()
        for listener in listeners.values():
            listener.close()
