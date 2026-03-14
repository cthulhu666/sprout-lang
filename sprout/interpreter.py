from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import selectors
import socket
import sys
from typing import Callable, TextIO
import urllib.error
import urllib.request

from . import ast


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


def run_program(program: ast.Program, stdout: TextIO | None = None) -> None:
    out = stdout
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
            return sys.stdin.read()
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
            return ADTValue(constructor="stdlib.collections.Nothing", args=())
        return ADTValue(constructor="stdlib.collections.Just", args=(vec.items[index],))

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
            return ADTValue(constructor="stdlib.collections.Nothing", args=())
        return ADTValue(constructor="stdlib.collections.Just", args=(map_value.items[key],))

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
            return ADTValue(constructor="stdlib.collections.Nothing", args=())
        return ADTValue(constructor="stdlib.collections.Just", args=(keys[index],))

    def builtin_map_nth_value(args: list[object]) -> object:
        map_value = args[0]
        index = args[1]
        if not isinstance(map_value, MapValue):
            raise RuntimeError("map_nth_value expects Map")
        if not isinstance(index, int):
            raise RuntimeError("map_nth_value expects Int index")
        values = list(map_value.items.values())
        if index < 0 or index >= len(values):
            return ADTValue(constructor="stdlib.collections.Nothing", args=())
        return ADTValue(constructor="stdlib.collections.Just", args=(values[index],))

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
        return ADTValue(constructor="stdlib.http.Err", args=(err,))

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
                return ADTValue(constructor="stdlib.http.Ok", args=(resp,))
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
            return ADTValue(constructor="stdlib.http.JsonNull", args=())
        if isinstance(value, bool):
            return ADTValue(constructor="stdlib.http.JsonBool", args=(value,))
        if isinstance(value, int):
            return ADTValue(constructor="stdlib.http.JsonInt", args=(value,))
        if isinstance(value, str):
            return ADTValue(constructor="stdlib.http.JsonString", args=(value,))
        if isinstance(value, list):
            cursor = ADTValue(constructor="stdlib.http.JsonArrayNil", args=())
            for item in reversed(value):
                cursor = ADTValue(
                    constructor="stdlib.http.JsonArrayCons",
                    args=(_json_to_adt(item), cursor),
                )
            return ADTValue(constructor="stdlib.http.JsonArray", args=(cursor,))
        if isinstance(value, dict):
            cursor = ADTValue(constructor="stdlib.http.JsonObjectNil", args=())
            for key, item in reversed(list(value.items())):
                cursor = ADTValue(
                    constructor="stdlib.http.JsonObjectCons",
                    args=(str(key), _json_to_adt(item), cursor),
                )
            return ADTValue(constructor="stdlib.http.JsonObject", args=(cursor,))
        raise RuntimeError(f"json_parse unsupported value kind: {type(value).__name__}")

    def builtin_json_parse(args: list[object]) -> object:
        raw = args[0]
        if not isinstance(raw, str):
            raise RuntimeError("json_parse expects String")
        try:
            parsed = json.loads(raw)
            return ADTValue(constructor="stdlib.http.Ok", args=(_json_to_adt(parsed),))
        except json.JSONDecodeError as exc:
            err = ADTValue(constructor="stdlib.http.JsonDecode", args=(str(exc),))
            return ADTValue(constructor="stdlib.http.Err", args=(err,))

    def _term_emit(text: str) -> None:
        if out is None:
            sys.stdout.write(text)
            sys.stdout.flush()
        else:
            out.write(text)

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
        return os.environ.get("SPROUT_TERM_KEY", "q")

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

    env.set("print", BuiltinFunction(name="print", arity=1, fn=builtin_print))
    env.set("print_int", BuiltinFunction(name="print_int", arity=1, fn=builtin_print_int))
    env.set("read_lines", BuiltinFunction(name="read_lines", arity=1, fn=builtin_read_lines))
    env.set("read_file", BuiltinFunction(name="read_file", arity=1, fn=builtin_read_file))
    env.set("read_int_lines", BuiltinFunction(name="read_int_lines", arity=1, fn=builtin_read_int_lines))
    env.set("parse_int", BuiltinFunction(name="parse_int", arity=1, fn=builtin_parse_int))
    env.set("split_words", BuiltinFunction(name="split_words", arity=1, fn=builtin_split_words))
    env.set("str_concat", BuiltinFunction(name="str_concat", arity=2, fn=builtin_str_concat))
    env.set("str_len", BuiltinFunction(name="str_len", arity=1, fn=builtin_str_len))
    env.set("str_slice", BuiltinFunction(name="str_slice", arity=3, fn=builtin_str_slice))
    env.set("str_find", BuiltinFunction(name="str_find", arity=2, fn=builtin_str_find))
    env.set(
        "str_starts_with",
        BuiltinFunction(name="str_starts_with", arity=2, fn=builtin_str_starts_with),
    )
    env.set("vector_empty", BuiltinFunction(name="vector_empty", arity=0, fn=builtin_vector_empty))
    env.set("vector_length", BuiltinFunction(name="vector_length", arity=1, fn=builtin_vector_length))
    env.set("vector_get", BuiltinFunction(name="vector_get", arity=2, fn=builtin_vector_get))
    env.set("vector_set", BuiltinFunction(name="vector_set", arity=3, fn=builtin_vector_set))
    env.set("vector_append", BuiltinFunction(name="vector_append", arity=2, fn=builtin_vector_append))
    env.set("map_empty", BuiltinFunction(name="map_empty", arity=0, fn=builtin_map_empty))
    env.set("map_get", BuiltinFunction(name="map_get", arity=2, fn=builtin_map_get))
    env.set("map_set", BuiltinFunction(name="map_set", arity=3, fn=builtin_map_set))
    env.set("map_remove", BuiltinFunction(name="map_remove", arity=2, fn=builtin_map_remove))
    env.set("map_size", BuiltinFunction(name="map_size", arity=1, fn=builtin_map_size))
    env.set("map_nth_key", BuiltinFunction(name="map_nth_key", arity=2, fn=builtin_map_nth_key))
    env.set("map_nth_value", BuiltinFunction(name="map_nth_value", arity=2, fn=builtin_map_nth_value))
    env.set("http_request", BuiltinFunction(name="http_request", arity=5, fn=builtin_http_request))
    env.set("json_parse", BuiltinFunction(name="json_parse", arity=1, fn=builtin_json_parse))
    env.set("term_clear", BuiltinFunction(name="term_clear", arity=0, fn=builtin_term_clear))
    env.set("term_move", BuiltinFunction(name="term_move", arity=2, fn=builtin_term_move))
    env.set("term_hide_cursor", BuiltinFunction(name="term_hide_cursor", arity=0, fn=builtin_term_hide_cursor))
    env.set("term_show_cursor", BuiltinFunction(name="term_show_cursor", arity=0, fn=builtin_term_show_cursor))
    env.set("term_read_key", BuiltinFunction(name="term_read_key", arity=0, fn=builtin_term_read_key))
    env.set("term_write", BuiltinFunction(name="term_write", arity=1, fn=builtin_term_write))
    env.set("tcp_listen", BuiltinFunction(name="tcp_listen", arity=1, fn=builtin_tcp_listen))
    env.set("tcp_accept", BuiltinFunction(name="tcp_accept", arity=1, fn=builtin_tcp_accept))
    env.set("tcp_read", BuiltinFunction(name="tcp_read", arity=1, fn=builtin_tcp_read))
    env.set("tcp_write", BuiltinFunction(name="tcp_write", arity=2, fn=builtin_tcp_write))
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
                apply_callable(main, [])
    finally:
        for conn in connections.values():
            conn.close()
        for listener in listeners.values():
            listener.close()
