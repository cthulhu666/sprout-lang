from __future__ import annotations

from dataclasses import dataclass

from . import ast


class CodegenError(ValueError):
    pass


@dataclass(frozen=True)
class LLType:
    text: str


I64 = LLType("i64")
I1 = LLType("i1")
I32 = LLType("i32")
I8_PTR = LLType("ptr")


def _tuple_lltype(items: list[LLType]) -> LLType:
    return LLType("{ " + ", ".join(item.text for item in items) + " }")


@dataclass
class FnSig:
    name: str
    params: list[LLType]
    ret: LLType
    ret_callable_sig: "CallSig | None" = None


@dataclass
class CtorSig:
    name: str
    tag: int
    arg_types: list[LLType]


EXTERN_SIGS: dict[str, FnSig] = {
    "malloc": FnSig(name="malloc", params=[I64], ret=I8_PTR),
    "print_int": FnSig(name="print_int", params=[I64], ret=I64),
    "print_str": FnSig(name="print_str", params=[I8_PTR], ret=I64),
    "print_text": FnSig(name="print_text", params=[I8_PTR], ret=I64),
    "print_value": FnSig(name="print_value", params=[I64], ret=I64),
    "print_value_part": FnSig(name="print_value_part", params=[I64], ret=I64),
    "print_newline": FnSig(name="print_newline", params=[], ret=I64),
    "read_file": FnSig(name="read_file", params=[I8_PTR], ret=I8_PTR),
    "env_get": FnSig(name="env_get", params=[I8_PTR], ret=I64),
    "argv_get": FnSig(name="argv_get", params=[I64], ret=I64),
    "read_int_lines": FnSig(name="read_int_lines", params=[I8_PTR], ret=I64),
    "parse_int": FnSig(name="parse_int", params=[I8_PTR], ret=I64),
    "str_concat": FnSig(name="str_concat", params=[I8_PTR, I8_PTR], ret=I8_PTR),
    "str_len": FnSig(name="str_len", params=[I8_PTR], ret=I64),
    "str_slice": FnSig(name="str_slice", params=[I8_PTR, I64, I64], ret=I8_PTR),
    "str_eq": FnSig(name="str_eq", params=[I8_PTR, I8_PTR], ret=I1),
    "str_find": FnSig(name="str_find", params=[I8_PTR, I8_PTR], ret=I64),
    "str_starts_with": FnSig(name="str_starts_with", params=[I8_PTR, I8_PTR], ret=I1),
    "bytes_empty": FnSig(name="bytes_empty", params=[], ret=I64),
    "bytes_length": FnSig(name="bytes_length", params=[I64], ret=I64),
    "bytes_get": FnSig(name="bytes_get", params=[I64, I64], ret=I64),
    "bytes_slice": FnSig(name="bytes_slice", params=[I64, I64, I64], ret=I64),
    "bytes_append": FnSig(name="bytes_append", params=[I64, I64], ret=I64),
    "bytes_singleton": FnSig(name="bytes_singleton", params=[I64], ret=I64),
    "bytes_from_utf8": FnSig(name="bytes_from_utf8", params=[I8_PTR], ret=I64),
    "bytes_to_utf8": FnSig(name="bytes_to_utf8", params=[I64], ret=I64),
    "bytes_builder_empty": FnSig(name="bytes_builder_empty", params=[], ret=I64),
    "bytes_builder_bytes": FnSig(name="bytes_builder_bytes", params=[I64], ret=I64),
    "bytes_builder_byte": FnSig(name="bytes_builder_byte", params=[I64], ret=I64),
    "bytes_builder_u16_be": FnSig(name="bytes_builder_u16_be", params=[I64], ret=I64),
    "bytes_builder_u32_be": FnSig(name="bytes_builder_u32_be", params=[I64], ret=I64),
    "bytes_builder_append": FnSig(name="bytes_builder_append", params=[I64, I64], ret=I64),
    "bytes_builder_build": FnSig(name="bytes_builder_build", params=[I64], ret=I64),
    "crypto_sha256": FnSig(name="crypto_sha256", params=[I64], ret=I64),
    "crypto_hmac_sha256": FnSig(name="crypto_hmac_sha256", params=[I64, I64], ret=I64),
    "crypto_base64_encode": FnSig(name="crypto_base64_encode", params=[I64], ret=I8_PTR),
    "crypto_base64_decode": FnSig(name="crypto_base64_decode", params=[I8_PTR], ret=I64),
    "crypto_bytes_xor": FnSig(name="crypto_bytes_xor", params=[I64, I64], ret=I64),
    "crypto_random_bytes": FnSig(name="crypto_random_bytes", params=[I64], ret=I64),
    "vector_empty": FnSig(name="vector_empty", params=[], ret=I64),
    "vector_length": FnSig(name="vector_length", params=[I64], ret=I64),
    "vector_get": FnSig(name="vector_get", params=[I64, I64], ret=I64),
    "vector_set": FnSig(name="vector_set", params=[I64, I64, I64], ret=I64),
    "vector_append": FnSig(name="vector_append", params=[I64, I64], ret=I64),
    "map_empty": FnSig(name="map_empty", params=[], ret=I64),
    "map_get": FnSig(name="map_get", params=[I64, I8_PTR], ret=I64),
    "map_set": FnSig(name="map_set", params=[I64, I8_PTR, I64], ret=I64),
    "map_remove": FnSig(name="map_remove", params=[I64, I8_PTR], ret=I64),
    "map_size": FnSig(name="map_size", params=[I64], ret=I64),
    "map_nth_key": FnSig(name="map_nth_key", params=[I64, I64], ret=I64),
    "map_nth_value": FnSig(name="map_nth_value", params=[I64, I64], ret=I64),
    "tcp_listen": FnSig(name="tcp_listen", params=[I64], ret=I64),
    "tcp_accept": FnSig(name="tcp_accept", params=[I64], ret=I64),
    "tcp_read": FnSig(name="tcp_read", params=[I64], ret=I8_PTR),
    "tcp_write": FnSig(name="tcp_write", params=[I64, I8_PTR], ret=I64),
    "tcp_connect": FnSig(name="tcp_connect", params=[I8_PTR, I64], ret=I64),
    "tcp_read_exact": FnSig(name="tcp_read_exact", params=[I64, I64], ret=I64),
    "tcp_write_all": FnSig(name="tcp_write_all", params=[I64, I64], ret=I64),
    "tcp_close": FnSig(name="tcp_close", params=[I64], ret=I64),
    "tcp_close_listener": FnSig(name="tcp_close_listener", params=[I64], ret=I64),
    "tcp_echo_serve": FnSig(name="tcp_echo_serve", params=[I64, I64], ret=I64),
    "http_request": FnSig(name="http_request", params=[I8_PTR, I8_PTR, I8_PTR, I8_PTR, I64], ret=I64),
    "json_stringify": FnSig(name="json_stringify", params=[I64], ret=I8_PTR),
    "sprout_set_argv": FnSig(name="sprout_set_argv", params=[I32, I8_PTR], ret=I64),
    "sprout_register_ctor": FnSig(name="sprout_register_ctor", params=[I64, I8_PTR, I64], ret=I64),
    "sprout_make0": FnSig(name="sprout_make0", params=[I64], ret=I64),
    "sprout_make1": FnSig(name="sprout_make1", params=[I64, I64], ret=I64),
    "sprout_make2": FnSig(name="sprout_make2", params=[I64, I64, I64], ret=I64),
    "sprout_make3": FnSig(name="sprout_make3", params=[I64, I64, I64, I64], ret=I64),
    "sprout_tag": FnSig(name="sprout_tag", params=[I64], ret=I64),
    "sprout_field": FnSig(name="sprout_field", params=[I64, I64], ret=I64),
}


QUALIFIED_LEAF_CTORS = {
    "Ok": "stdlib.http.Ok",
    "Err": "stdlib.http.Err",
    "HttpResponse": "stdlib.http.HttpResponse",
    "HttpTimeout": "stdlib.http.HttpTimeout",
    "HttpNetwork": "stdlib.http.HttpNetwork",
    "HttpBadStatus": "stdlib.http.HttpBadStatus",
    "HttpDecode": "stdlib.http.HttpDecode",
    "JsonDecode": "stdlib.json.JsonDecode",
    "JsonNull": "stdlib.json.JsonNull",
    "JsonBool": "stdlib.json.JsonBool",
    "JsonInt": "stdlib.json.JsonInt",
    "JsonString": "stdlib.json.JsonString",
    "JsonArray": "stdlib.json.JsonArray",
    "JsonObject": "stdlib.json.JsonObject",
    "JsonArrayNil": "stdlib.json.JsonArrayNil",
    "JsonArrayCons": "stdlib.json.JsonArrayCons",
    "JsonObjectNil": "stdlib.json.JsonObjectNil",
    "JsonObjectCons": "stdlib.json.JsonObjectCons",
}


@dataclass
class Value:
    typ: LLType
    ir: str
    callable_sig: "CallSig | None" = None
    tuple_items: list[LLType] | None = None


@dataclass
class GlobalConst:
    typ: LLType
    value_ir: str


@dataclass
class GlobalInfo:
    typ: LLType
    is_const: bool
    const_value_ir: str | None = None
    callable_sig: "CallSig | None" = None


@dataclass(frozen=True)
class CallSig:
    params: list[LLType]
    ret: LLType
    ret_callable_sig: "CallSig | None" = None


@dataclass
class LambdaInfo:
    expr: ast.LambdaExpr
    name: str
    captures: list[str]
    call_sig: CallSig


class Emitter:
    def __init__(self) -> None:
        self.lines: list[str] = []
        self.next_tmp = 0
        self.next_block = 0
        self.current_block: str | None = None
        self.next_str = 0
        self.next_lambda = 0
        self.string_globals: list[str] = []
        self.global_defs: list[str] = []
        self.lifted_defs: list[str] = []
        self.fn_wrappers: dict[str, str] = {}

    def emit(self, line: str) -> None:
        self.lines.append(line)

    def label(self, name: str) -> None:
        self.lines.append(f"{name}:")
        self.current_block = name

    def tmp(self) -> str:
        name = f"%t{self.next_tmp}"
        self.next_tmp += 1
        return name

    def block(self, prefix: str) -> str:
        name = f"{prefix}{self.next_block}"
        self.next_block += 1
        return name

    def string_const(self, value: str) -> tuple[str, int]:
        data = value.encode("utf-8") + b"\x00"
        name = f"@.str.{self.next_str}"
        self.next_str += 1
        body = "".join(f"\\{b:02X}" for b in data)
        self.string_globals.append(
            f"{name} = private unnamed_addr constant [{len(data)} x i8] c\"{body}\""
        )
        return name, len(data)


def _ctor_registration_names(name: str) -> list[str]:
    names = [name]
    leaf = name.rsplit(".", 1)[-1]
    if leaf != name:
        names.append(leaf)
        return names
    qualified = QUALIFIED_LEAF_CTORS.get(leaf)
    if qualified is not None:
        names.append(qualified)
    return names


def _type_from_ast(node: ast.TypeExpr | None, adt_names: set[str]) -> LLType:
    adt_leaf_names = {name.rsplit(".", 1)[-1] for name in adt_names}
    if node is None:
        raise CodegenError("Function return type annotation is required for native codegen")
    if isinstance(node, ast.TypeName):
        leaf = node.name.rsplit(".", 1)[-1]
        if node.name == "Int":
            return I64
        if node.name == "Bool":
            return I1
        if node.name == "String":
            return I8_PTR
        if node.name == "Bytes":
            return I64
        if node.name == "Builder":
            return I64
        if node.name in adt_names or leaf in adt_leaf_names:
            return I64
        if leaf and leaf[0].islower():
            return I64
    if isinstance(node, ast.TypeApply):
        if isinstance(node.base, ast.TypeName) and node.base.name == "IO":
            if isinstance(node.arg, ast.TypeName) and node.arg.name == "Unit":
                return I64
        base_name = _type_base_name(node)
        base_leaf = base_name.rsplit(".", 1)[-1] if base_name is not None else None
        if base_name in adt_names or (base_leaf is not None and base_leaf in adt_leaf_names):
            return I64
        if base_name in {"Vector", "Map", "Bytes"}:
            return I64
        if base_leaf and base_leaf[0].islower():
            return I64
    if isinstance(node, ast.TypeArrow):
        # Function-typed values are lowered as opaque callable pointers.
        return I8_PTR
    if isinstance(node, ast.TupleType):
        return _tuple_lltype([_type_from_ast(item, adt_names) for item in node.items])
    raise CodegenError(f"Unsupported type for LLVM backend: {node}")


def _tuple_item_types_from_type_expr(node: ast.TypeExpr | None, adt_names: set[str]) -> list[LLType] | None:
    if not isinstance(node, ast.TupleType):
        return None
    return [_type_from_ast(item, adt_names) for item in node.items]


def _tuple_item_types_from_lltype(typ: LLType) -> list[LLType] | None:
    text = typ.text.strip()
    if not (text.startswith("{ ") and text.endswith(" }")):
        return None
    inner = text[2:-2].strip()
    if not inner:
        return []
    parts: list[str] = []
    depth = 0
    start = 0
    for idx, ch in enumerate(inner):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append(inner[start:idx].strip())
            start = idx + 1
    parts.append(inner[start:].strip())
    return [LLType(part) for part in parts]


def _zero_init_for_type(typ: LLType) -> str:
    if typ == I8_PTR:
        return "null"
    if _tuple_item_types_from_lltype(typ) is not None:
        return "zeroinitializer"
    return "0"


def _type_base_name(node: ast.TypeExpr) -> str | None:
    if isinstance(node, ast.TypeName):
        return node.name
    if isinstance(node, ast.TypeApply):
        return _type_base_name(node.base)
    return None


def _check_param_type(node: ast.TypeExpr, adt_names: set[str]) -> LLType:
    typ, _ = _lower_value_type(node, adt_names)
    return typ


def _is_io_unit(node: ast.TypeExpr | None) -> bool:
    if not isinstance(node, ast.TypeApply):
        return False
    if not isinstance(node.base, ast.TypeName) or node.base.name != "IO":
        return False
    return isinstance(node.arg, ast.TypeName) and node.arg.name == "Unit"


def _lower_value_type(node: ast.TypeExpr, adt_names: set[str]) -> tuple[LLType, CallSig | None]:
    if isinstance(node, ast.TypeArrow):
        params_ast: list[ast.TypeExpr] = []
        cursor: ast.TypeExpr = node
        while isinstance(cursor, ast.TypeArrow):
            params_ast.append(cursor.left)
            cursor = cursor.right
        params_ll = [_type_from_ast(param, adt_names) for param in params_ast]
        ret_ll = _type_from_ast(cursor, adt_names)
        return I8_PTR, CallSig(params=params_ll, ret=ret_ll)
    return _type_from_ast(node, adt_names), None


def _closure_struct_type() -> str:
    return "{ ptr, ptr }"


def _env_struct_type(fields: list[Value]) -> str:
    if not fields:
        return "{ }"
    return "{ " + ", ".join(field.typ.text for field in fields) + " }"


def _sizeof_struct(struct_text: str, emitter: Emitter) -> str:
    size_ptr = emitter.tmp()
    emitter.emit(f"  {size_ptr} = getelementptr {struct_text}, ptr null, i32 1")
    size = emitter.tmp()
    emitter.emit(f"  {size} = ptrtoint ptr {size_ptr} to i64")
    return size


def _clone_emitter(emitter: Emitter) -> Emitter:
    clone = Emitter()
    clone.next_tmp = emitter.next_tmp
    clone.next_block = emitter.next_block
    clone.next_str = emitter.next_str
    clone.next_lambda = emitter.next_lambda
    clone.fn_wrappers = dict(emitter.fn_wrappers)
    return clone


def _merge_emitter_state(target: Emitter, source: Emitter) -> None:
    target.next_tmp = source.next_tmp
    target.next_block = source.next_block
    target.next_str = source.next_str
    target.next_lambda = source.next_lambda
    target.string_globals.extend(source.string_globals)
    target.global_defs.extend(source.global_defs)
    target.lifted_defs.extend(source.lifted_defs)
    target.fn_wrappers.update(source.fn_wrappers)


def _call_sig_from_type_expr(node: ast.TypeExpr | None, adt_names: set[str]) -> CallSig | None:
    if node is None:
        return None
    _, call_sig = _lower_value_type(node, adt_names)
    return call_sig


def _call_sig_from_lambda_expr(
    expr: ast.LambdaExpr,
    sigs: dict[str, FnSig],
    globals_info: dict[str, GlobalInfo],
    adt_names: set[str],
) -> CallSig:
    params: list[LLType] = []
    for param in expr.params:
        if param.type_expr is None:
            raise CodegenError("Lambda parameter type was not finalized before codegen")
        params.append(_type_from_ast(param.type_expr, adt_names))
    body_type = getattr(expr.body, "inferred_type", None)
    if body_type is None:
        raise CodegenError("Lambda body is missing inferred type")
    ret = _type_from_ast(body_type, adt_names)
    return CallSig(
        params=params,
        ret=ret,
        ret_callable_sig=_expr_callable_sig(expr.body, sigs, globals_info, adt_names),
    )


def _expr_callable_sig(
    expr: ast.Expr,
    sigs: dict[str, FnSig],
    globals_info: dict[str, GlobalInfo],
    adt_names: set[str],
) -> CallSig | None:
    if isinstance(expr, ast.LambdaExpr):
        params: list[LLType] = []
        for param in expr.params:
            if param.type_expr is None:
                raise CodegenError("Lambda parameter type was not finalized before codegen")
            params.append(_type_from_ast(param.type_expr, adt_names))
        body_type = getattr(expr.body, "inferred_type", None)
        if body_type is None:
            raise CodegenError("Lambda body is missing inferred type")
        return CallSig(
            params=params,
            ret=_type_from_ast(body_type, adt_names),
            ret_callable_sig=_expr_callable_sig(expr.body, sigs, globals_info, adt_names),
        )
    if isinstance(expr, ast.VarExpr):
        fn_sig = sigs.get(expr.name)
        if fn_sig is not None:
            return CallSig(params=fn_sig.params, ret=fn_sig.ret, ret_callable_sig=fn_sig.ret_callable_sig)
        global_info = globals_info.get(expr.name)
        if global_info is not None:
            return global_info.callable_sig
        return None
    if isinstance(expr, ast.IfExpr):
        then_sig = _expr_callable_sig(expr.then_branch, sigs, globals_info, adt_names)
        else_sig = _expr_callable_sig(expr.else_branch, sigs, globals_info, adt_names)
        if then_sig == else_sig:
            return then_sig
        return None
    if isinstance(expr, ast.MatchExpr):
        branch_sigs = [_expr_callable_sig(branch.value, sigs, globals_info, adt_names) for branch in expr.branches]
        if branch_sigs and all(sig == branch_sigs[0] for sig in branch_sigs[1:]):
            return branch_sigs[0]
        return None
    if isinstance(expr, ast.CallExpr):
        callee_sig = _expr_callable_sig(expr.callee, sigs, globals_info, adt_names)
        if callee_sig is None:
            return None
        if len(expr.args) != len(callee_sig.params):
            return None
        return callee_sig.ret_callable_sig
    return None


def _value_call_sig(value: Value, inferred_type: ast.TypeExpr | None, adt_names: set[str]) -> CallSig | None:
    if value.callable_sig is not None:
        return value.callable_sig
    return _call_sig_from_type_expr(inferred_type, adt_names)


def _value_for_inferred_type(
    value: Value,
    inferred_type: ast.TypeExpr | None,
    adt_names: set[str],
    emitter: Emitter,
) -> Value:
    if inferred_type is None:
        return value
    ll_type, call_sig = _lower_value_type(inferred_type, adt_names)
    coerced = _coerce_value(value, ll_type, emitter)
    tuple_items = _tuple_item_types_from_type_expr(inferred_type, adt_names)
    return Value(
        typ=coerced.typ,
        ir=coerced.ir,
        callable_sig=value.callable_sig or call_sig,
        tuple_items=tuple_items if tuple_items is not None else coerced.tuple_items,
    )


def _pattern_bound_names(pattern: ast.Pattern) -> set[str]:
    if isinstance(pattern, ast.VarPattern):
        return {pattern.name}
    if isinstance(pattern, ast.TuplePattern):
        out: set[str] = set()
        for item in pattern.items:
            out |= _pattern_bound_names(item)
        return out
    if isinstance(pattern, ast.ConstructorPattern):
        out: set[str] = set()
        for arg in pattern.args:
            out |= _pattern_bound_names(arg)
        return out
    return set()


def _collect_free_vars(expr: ast.Expr, bound: set[str], out: list[str], seen: set[str]) -> None:
    if isinstance(expr, ast.VarExpr):
        if expr.name not in bound and expr.name not in seen:
            seen.add(expr.name)
            out.append(expr.name)
        return
    if isinstance(expr, ast.IfExpr):
        _collect_free_vars(expr.condition, bound, out, seen)
        _collect_free_vars(expr.then_branch, bound, out, seen)
        _collect_free_vars(expr.else_branch, bound, out, seen)
        return
    if isinstance(expr, ast.MatchExpr):
        _collect_free_vars(expr.scrutinee, bound, out, seen)
        for branch in expr.branches:
            branch_bound = bound | _pattern_bound_names(branch.pattern)
            _collect_free_vars(branch.value, branch_bound, out, seen)
        return
    if isinstance(expr, ast.TupleExpr):
        for item in expr.items:
            _collect_free_vars(item, bound, out, seen)
        return
    if isinstance(expr, ast.BinaryExpr):
        _collect_free_vars(expr.left, bound, out, seen)
        _collect_free_vars(expr.right, bound, out, seen)
        return
    if isinstance(expr, ast.UnaryExpr):
        _collect_free_vars(expr.operand, bound, out, seen)
        return
    if isinstance(expr, ast.CallExpr):
        _collect_free_vars(expr.callee, bound, out, seen)
        for arg in expr.args:
            _collect_free_vars(arg, bound, out, seen)
        return
    if isinstance(expr, ast.LambdaExpr):
        inner_bound = bound | {param.name for param in expr.params}
        _collect_free_vars(expr.body, inner_bound, out, seen)
        return


def _gather_lambda_infos(
    expr: ast.Expr,
    available_locals: list[str],
    sigs: dict[str, FnSig],
    globals_info: dict[str, GlobalInfo],
    adt_names: set[str],
    infos: dict[int, LambdaInfo],
    emitter: Emitter,
) -> None:
    if isinstance(expr, ast.LambdaExpr):
        key = id(expr)
        if key not in infos:
            call_sig = _call_sig_from_lambda_expr(expr, sigs, globals_info, adt_names)
            free_vars: list[str] = []
            _collect_free_vars(
                expr.body,
                {param.name for param in expr.params},
                free_vars,
                set(),
            )
            captures = [name for name in free_vars if name in available_locals]
            info = LambdaInfo(
                expr=expr,
                name=f"__sprout_lambda_{emitter.next_lambda}",
                captures=captures,
                call_sig=call_sig,
            )
            emitter.next_lambda += 1
            infos[key] = info
        next_locals = list(dict.fromkeys(available_locals + infos[key].captures + [param.name for param in expr.params]))
        _gather_lambda_infos(expr.body, next_locals, sigs, globals_info, adt_names, infos, emitter)
        return
    if isinstance(expr, ast.IfExpr):
        _gather_lambda_infos(expr.condition, available_locals, sigs, globals_info, adt_names, infos, emitter)
        _gather_lambda_infos(expr.then_branch, available_locals, sigs, globals_info, adt_names, infos, emitter)
        _gather_lambda_infos(expr.else_branch, available_locals, sigs, globals_info, adt_names, infos, emitter)
        return
    if isinstance(expr, ast.TupleExpr):
        for item in expr.items:
            _gather_lambda_infos(item, available_locals, sigs, globals_info, adt_names, infos, emitter)
        return
    if isinstance(expr, ast.MatchExpr):
        _gather_lambda_infos(expr.scrutinee, available_locals, sigs, globals_info, adt_names, infos, emitter)
        for branch in expr.branches:
            branch_locals = list(dict.fromkeys(available_locals + list(_pattern_bound_names(branch.pattern))))
            _gather_lambda_infos(branch.value, branch_locals, sigs, globals_info, adt_names, infos, emitter)
        return
    if isinstance(expr, ast.BinaryExpr):
        _gather_lambda_infos(expr.left, available_locals, sigs, globals_info, adt_names, infos, emitter)
        _gather_lambda_infos(expr.right, available_locals, sigs, globals_info, adt_names, infos, emitter)
        return
    if isinstance(expr, ast.UnaryExpr):
        _gather_lambda_infos(expr.operand, available_locals, sigs, globals_info, adt_names, infos, emitter)
        return
    if isinstance(expr, ast.CallExpr):
        _gather_lambda_infos(expr.callee, available_locals, sigs, globals_info, adt_names, infos, emitter)
        for arg in expr.args:
            _gather_lambda_infos(arg, available_locals, sigs, globals_info, adt_names, infos, emitter)


def _emit_make_closure(code_ir: str, captures: list[Value], emitter: Emitter) -> Value:
    size = emitter.tmp()
    emitter.emit(f"  {size} = add i64 {8 * (len(captures) + 1)}, 0")
    raw = emitter.tmp()
    emitter.emit(f"  {raw} = call ptr @malloc(i64 {size})")
    emitter.emit(f"  store ptr {code_ir}, ptr {raw}")
    for idx, capture in enumerate(captures, start=1):
        packed = _pack_to_i64(capture, emitter)
        slot = emitter.tmp()
        emitter.emit(f"  {slot} = getelementptr i64, ptr {raw}, i64 {idx}")
        emitter.emit(f"  store i64 {packed}, ptr {slot}")
    return Value(I8_PTR, raw)


def _emit_closure_call(callee: Value, call_sig: CallSig, args: list[Value], emitter: Emitter) -> Value:
    if len(args) != len(call_sig.params):
        raise CodegenError(f"Callable expects {len(call_sig.params)} args, got {len(args)}")
    code = emitter.tmp()
    emitter.emit(f"  {code} = load ptr, ptr {callee.ir}")
    args_ir = [f"ptr {callee.ir}"]
    for value, param_type in zip(args, call_sig.params):
        coerced = _coerce_value(value, param_type, emitter)
        args_ir.append(f"{param_type.text} {coerced.ir}")
    out = emitter.tmp()
    emitter.emit(f"  {out} = call {call_sig.ret.text} {code}({', '.join(args_ir)})")
    return Value(call_sig.ret, out, callable_sig=call_sig.ret_callable_sig)


def _emit_named_function_wrapper(wrapper_name: str, target_name: str, sig: FnSig) -> list[str]:
    lines = [f"define {sig.ret.text} @{wrapper_name}(ptr %env{''.join(f', {t.text} %a{i}' for i, t in enumerate(sig.params))}) {{", "entry:"]
    args = ", ".join(f"{t.text} %a{i}" for i, t in enumerate(sig.params))
    call = f"  %ret = call {sig.ret.text} @{target_name}({args})" if args else f"  %ret = call {sig.ret.text} @{target_name}()"
    lines.append(call)
    lines.append(f"  ret {sig.ret.text} %ret")
    lines.append("}")
    return lines


def _emit_lambda_helper(
    info: LambdaInfo,
    globals_info: dict[str, GlobalInfo],
    sigs: dict[str, FnSig],
    ctor_sigs: dict[str, CtorSig],
    adt_names: set[str],
    emitter: Emitter,
) -> None:
    helper = Emitter()
    helper.fn_wrappers = dict(emitter.fn_wrappers)
    params = ["ptr %env"] + [f"{typ.text} %a{i}" for i, typ in enumerate(info.call_sig.params)]
    helper.emit(f"define {info.call_sig.ret.text} @{info.name}({', '.join(params)}) {{")
    helper.label("entry")
    locals_: dict[str, Value] = {}
    for idx, capture in enumerate(info.captures, start=1):
        slot = helper.tmp()
        helper.emit(f"  {slot} = getelementptr i64, ptr %env, i64 {idx}")
        raw = helper.tmp()
        helper.emit(f"  {raw} = load i64, ptr {slot}")
        locals_[capture] = Value(I64, raw)
    for idx, param in enumerate(info.expr.params):
        locals_[param.name] = Value(info.call_sig.params[idx], f"%a{idx}", _call_sig_from_type_expr(param.type_expr, adt_names))
    ret = _emit_expr(info.expr.body, locals_, globals_info, sigs, ctor_sigs, adt_names, helper)
    if ret.typ != info.call_sig.ret:
        raise CodegenError(f"Lambda body type mismatch in backend: {ret.typ.text} vs {info.call_sig.ret.text}")
    helper.emit(f"  ret {ret.typ.text} {ret.ir}")
    helper.emit("}")
    _merge_emitter_state(emitter, helper)
    emitter.lifted_defs.extend(helper.lines)


def _infer_expr_type(
    expr: ast.Expr,
    globals_info: dict[str, GlobalInfo],
    sigs: dict[str, FnSig],
    ctor_sigs: dict[str, CtorSig],
) -> LLType:
    if isinstance(expr, ast.IntExpr):
        return I64
    if isinstance(expr, ast.BoolExpr):
        return I1
    if isinstance(expr, ast.StringExpr):
        return I8_PTR
    if isinstance(expr, ast.TupleExpr):
        return _tuple_lltype([_infer_expr_type(item, globals_info, sigs, ctor_sigs) for item in expr.items])
    if isinstance(expr, ast.VarExpr):
        if expr.name in globals_info:
            return globals_info[expr.name].typ
        if expr.name in sigs:
            return I8_PTR
        if expr.name in ctor_sigs:
            return I64
        raise CodegenError(f"Cannot infer top-level let type for unknown variable {expr.name}")
    if isinstance(expr, ast.UnaryExpr):
        return I64
    if isinstance(expr, ast.BinaryExpr):
        if expr.op in {"+", "-", "*", "/"}:
            return I64
        return I1
    if isinstance(expr, ast.IfExpr):
        return _infer_expr_type(expr.then_branch, globals_info, sigs, ctor_sigs)
    if isinstance(expr, ast.CallExpr):
        if not isinstance(expr.callee, ast.VarExpr):
            raise CodegenError("Cannot infer top-level let type for indirect call")
        name = expr.callee.name
        if name == "print":
            return I64
        if name in ctor_sigs:
            return I64
        if name in sigs:
            return sigs[name].ret
        if name in EXTERN_SIGS:
            return EXTERN_SIGS[name].ret
        raise CodegenError(f"Cannot infer top-level let call type for {name}")
    if isinstance(expr, ast.LambdaExpr):
        return I8_PTR
    if isinstance(expr, ast.MatchExpr):
        if not expr.branches:
            raise CodegenError("Cannot infer top-level let type for empty match")
        return _infer_expr_type(expr.branches[0].value, globals_info, sigs, ctor_sigs)
    raise CodegenError("Cannot infer top-level let type for expression")


def compile_to_llvm(program: ast.Program) -> str:
    type_decls = [d for d in program.declarations if isinstance(d, ast.TypeDecl)]
    all_fn_decls = [d for d in program.declarations if isinstance(d, ast.FnDecl)]
    let_decls = [d for d in program.declarations if isinstance(d, ast.LetDecl)]
    other = [
        d
        for d in program.declarations
        if not isinstance(d, ast.TypeDecl)
        and not isinstance(d, ast.FnDecl)
        and not isinstance(d, ast.LetDecl)
        and not isinstance(d, ast.ClassDecl)
        and not isinstance(d, ast.InstanceDecl)
    ]
    if other:
        raise CodegenError("LLVM backend encountered unsupported top-level declaration")

    adt_names = {t.name for t in type_decls}

    ctor_sigs: dict[str, CtorSig] = {}
    next_tag = 0
    for tdecl in type_decls:
        for ctor in tdecl.constructors:
            if ctor.name in ctor_sigs:
                raise CodegenError(f"Duplicate constructor name in backend: {ctor.name}")
            arg_types = [_type_from_ast(arg, adt_names) for arg in ctor.args]
            if len(arg_types) > 3:
                raise CodegenError(
                    f"Constructor {ctor.name} has {len(arg_types)} args; backend currently supports up to 3"
                )
            ctor_sigs[ctor.name] = CtorSig(name=ctor.name, tag=next_tag, arg_types=arg_types)
            next_tag += 1

    sigs: dict[str, FnSig] = {}
    for fn in all_fn_decls:
        if fn.name in sigs:
            raise CodegenError(f"Duplicate function {fn.name}")
        params = [_check_param_type(p.type_expr, adt_names) for p in fn.params]
        ret = _type_from_ast(fn.return_type, adt_names)
        sigs[fn.name] = FnSig(fn.name, params, ret)

    globals_info: dict[str, GlobalInfo] = {}
    const_env: dict[str, GlobalConst] = {}
    runtime_lets: list[ast.LetDecl] = []
    for let_decl in let_decls:
        if let_decl.name in globals_info:
            raise CodegenError(f"Duplicate global let {let_decl.name}")
        try:
            const_val = _eval_const_expr(let_decl.value, const_env)
            const_env[let_decl.name] = const_val
            globals_info[let_decl.name] = GlobalInfo(
                typ=const_val.typ,
                is_const=True,
                const_value_ir=const_val.value_ir,
            )
        except CodegenError:
            inferred = _infer_expr_type(let_decl.value, globals_info, sigs, ctor_sigs)
            callable_sig: CallSig | None = None
            if isinstance(let_decl.value, ast.VarExpr) and let_decl.value.name in sigs:
                fn_sig = sigs[let_decl.value.name]
                callable_sig = CallSig(params=fn_sig.params, ret=fn_sig.ret, ret_callable_sig=fn_sig.ret_callable_sig)
            elif isinstance(let_decl.value, ast.LambdaExpr):
                callable_sig = _call_sig_from_lambda_expr(let_decl.value, sigs, globals_info, adt_names)
            else:
                callable_sig = _expr_callable_sig(let_decl.value, sigs, globals_info, adt_names)
            globals_info[let_decl.name] = GlobalInfo(typ=inferred, is_const=False, callable_sig=callable_sig)
            runtime_lets.append(let_decl)

    fn_by_name = {fn.name: fn for fn in all_fn_decls}
    changed = True
    while changed:
        changed = False
        for fn in all_fn_decls:
            ret_callable_sig = _expr_callable_sig(fn.body, sigs, globals_info, adt_names)
            if sigs[fn.name].ret_callable_sig != ret_callable_sig:
                sigs[fn.name].ret_callable_sig = ret_callable_sig
                changed = True
    for let_decl in runtime_lets:
        globals_info[let_decl.name].callable_sig = _expr_callable_sig(let_decl.value, sigs, globals_info, adt_names)

    reachable_fn_names = {
        fn.name for fn in all_fn_decls if fn.name == "main" or fn.name.endswith(".main")
    }
    for let_decl in runtime_lets:
        reachable_fn_names.update(_collect_called_functions(let_decl.value))
    worklist = list(reachable_fn_names)
    while worklist:
        name = worklist.pop()
        fn = fn_by_name.get(name)
        if fn is None:
            continue
        for callee in _collect_called_functions(fn.body):
            if callee not in reachable_fn_names:
                reachable_fn_names.add(callee)
                worklist.append(callee)
    fn_decls = [fn for fn in all_fn_decls if fn.name in reachable_fn_names]

    emitter = Emitter()
    lambda_infos: dict[int, LambdaInfo] = {}
    for let_decl in runtime_lets:
        _gather_lambda_infos(let_decl.value, [], sigs, globals_info, adt_names, lambda_infos, emitter)
    for fn in fn_decls:
        _gather_lambda_infos(fn.body, [param.name for param in fn.params], sigs, globals_info, adt_names, lambda_infos, emitter)

    emitter.emit("; Generated by sprout LLVM backend (v0)")
    emitter.emit("target triple = \"unknown-unknown-unknown\"")
    emitter.emit("")
    for ext in EXTERN_SIGS.values():
        params = ", ".join(t.text for t in ext.params)
        emitter.emit(f"declare {ext.ret.text} @{ext.name}({params})")
    emitter.emit("")

    ctor_reg_meta: dict[str, tuple[list[tuple[str, int]], int, int]] = {}
    for ctor in ctor_sigs.values():
        aliases: list[tuple[str, int]] = []
        seen_names: set[str] = set()
        for reg_name in _ctor_registration_names(ctor.name):
            if reg_name in seen_names:
                continue
            seen_names.add(reg_name)
            aliases.append(emitter.string_const(reg_name))
        ctor_reg_meta[ctor.name] = (aliases, len(ctor.arg_types), ctor.tag)

    for fn in fn_decls:
        if fn.name == "main" or fn.name.endswith(".main"):
            continue
        wrapper_name = f"__sprout_fn_closure_{len(emitter.lifted_defs)}"
        emitter.lifted_defs.extend(_emit_named_function_wrapper(wrapper_name, fn.name, sigs[fn.name]))
        emitter.lifted_defs.append("")
        emitter.fn_wrappers[fn.name] = wrapper_name
    for info in lambda_infos.values():
        setattr(info.expr, "_lambda_info", info)
        _emit_lambda_helper(info, globals_info, sigs, ctor_sigs, adt_names, emitter)
        emitter.lifted_defs.append("")

    if runtime_lets:
        _emit_init_globals(runtime_lets, globals_info, sigs, ctor_sigs, adt_names, emitter)
        emitter.emit("")

    for fn in fn_decls:
        _emit_fn(fn, sigs, ctor_sigs, ctor_reg_meta, globals_info, adt_names, runtime_lets, emitter)
        emitter.emit("")

    module_lines = [emitter.lines[0], emitter.lines[1], ""]
    for name, info in globals_info.items():
        if info.is_const:
            assert info.const_value_ir is not None
            module_lines.append(f"@{name} = private constant {info.typ.text} {info.const_value_ir}")
        else:
            init = _zero_init_for_type(info.typ)
            module_lines.append(f"@{name} = global {info.typ.text} {init}")
    module_lines.extend(emitter.string_globals)
    if globals_info or emitter.string_globals:
        module_lines.append("")
    module_lines.extend(emitter.global_defs)
    if emitter.global_defs:
        module_lines.append("")
    module_lines.extend(emitter.lifted_defs)
    if emitter.lifted_defs:
        module_lines.append("")
    module_lines.extend(emitter.lines[3:])

    return "\n".join(module_lines).rstrip() + "\n"


def _collect_called_functions(expr: ast.Expr) -> set[str]:
    out: set[str] = set()

    def visit(node: ast.Expr) -> None:
        if isinstance(node, ast.VarExpr):
            out.add(node.name)
            return
        if isinstance(node, ast.IfExpr):
            visit(node.condition)
            visit(node.then_branch)
            visit(node.else_branch)
            return
        if isinstance(node, ast.MatchExpr):
            visit(node.scrutinee)
            for branch in node.branches:
                visit(branch.value)
            return
        if isinstance(node, ast.BinaryExpr):
            visit(node.left)
            visit(node.right)
            return
        if isinstance(node, ast.UnaryExpr):
            visit(node.operand)
            return
        if isinstance(node, ast.CallExpr):
            if isinstance(node.callee, ast.VarExpr):
                out.add(node.callee.name)
            else:
                visit(node.callee)
            for arg in node.args:
                visit(arg)
            return
        if isinstance(node, ast.LambdaExpr):
            visit(node.body)
            return

    visit(expr)
    return out


def _eval_const_expr(expr: ast.Expr, globals_: dict[str, GlobalConst]) -> GlobalConst:
    if isinstance(expr, ast.IntExpr):
        return GlobalConst(I64, str(expr.value))
    if isinstance(expr, ast.BoolExpr):
        return GlobalConst(I1, "1" if expr.value else "0")
    if isinstance(expr, ast.TupleExpr):
        items = [_eval_const_expr(item, globals_) for item in expr.items]
        tuple_typ = _tuple_lltype([item.typ for item in items])
        value_ir = "{" + ", ".join(f"{item.typ.text} {item.value_ir}" for item in items) + "}"
        return GlobalConst(tuple_typ, value_ir)
    if isinstance(expr, ast.VarExpr):
        ref = globals_.get(expr.name)
        if ref is None:
            raise CodegenError(f"Global let constant references unknown name: {expr.name}")
        return ref
    if isinstance(expr, ast.UnaryExpr) and expr.op == "-":
        operand = _eval_const_expr(expr.operand, globals_)
        if operand.typ != I64:
            raise CodegenError("Global let unary '-' expects Int")
        return GlobalConst(I64, str(-int(operand.value_ir)))
    if isinstance(expr, ast.BinaryExpr):
        left = _eval_const_expr(expr.left, globals_)
        right = _eval_const_expr(expr.right, globals_)
        if expr.op in {"+", "-", "*", "/"}:
            if left.typ != I64 or right.typ != I64:
                raise CodegenError(f"Global let arithmetic op {expr.op} expects Int")
            a = int(left.value_ir)
            b = int(right.value_ir)
            if expr.op == "+":
                return GlobalConst(I64, str(a + b))
            if expr.op == "-":
                return GlobalConst(I64, str(a - b))
            if expr.op == "*":
                return GlobalConst(I64, str(a * b))
            return GlobalConst(I64, str(a // b))
        if expr.op in {"<", "<=", ">", ">=", "==", "!="}:
            if left.typ != right.typ:
                raise CodegenError("Global let comparison operands must have same type")
            if left.typ == I64:
                a = int(left.value_ir)
                b = int(right.value_ir)
            elif left.typ == I1:
                a = left.value_ir == "1"
                b = right.value_ir == "1"
            else:
                raise CodegenError("Unsupported global let comparison type")
            out = {
                "<": a < b,
                "<=": a <= b,
                ">": a > b,
                ">=": a >= b,
                "==": a == b,
                "!=": a != b,
            }[expr.op]
            return GlobalConst(I1, "1" if out else "0")
        if expr.op in {"&&", "||"}:
            if left.typ != I1 or right.typ != I1:
                raise CodegenError(f"Global let logical op {expr.op} expects Bool")
            a = left.value_ir == "1"
            b = right.value_ir == "1"
            out = (a and b) if expr.op == "&&" else (a or b)
            return GlobalConst(I1, "1" if out else "0")
    if isinstance(expr, ast.IfExpr):
        cond = _eval_const_expr(expr.condition, globals_)
        if cond.typ != I1:
            raise CodegenError("Global let if condition must be Bool")
        branch = expr.then_branch if cond.value_ir == "1" else expr.else_branch
        return _eval_const_expr(branch, globals_)
    raise CodegenError("Top-level let in LLVM backend must be compile-time constant expression")


def _emit_fn(
    fn: ast.FnDecl,
    sigs: dict[str, FnSig],
    ctor_sigs: dict[str, CtorSig],
    ctor_reg_meta: dict[str, tuple[tuple[str, int], tuple[str, int] | None, int, int]],
    globals_info: dict[str, GlobalInfo],
    adt_names: set[str],
    runtime_lets: list[ast.LetDecl],
    emitter: Emitter,
) -> None:
    sig = sigs[fn.name]
    params = []
    locals_: dict[str, Value] = {}
    for p, typ in zip(fn.params, sig.params):
        _, call_sig = _lower_value_type(p.type_expr, adt_names)
        pname = f"%{p.name}"
        params.append(f"{typ.text} {pname}")
        locals_[p.name] = Value(
            typ=typ,
            ir=pname,
            callable_sig=call_sig,
            tuple_items=_tuple_item_types_from_type_expr(p.type_expr, adt_names),
        )

    is_entry_main = fn.name == "main" or fn.name.endswith(".main")
    emitted_name = "main" if is_entry_main else fn.name
    if is_entry_main:
        emitted_params = ["i32 %argc", "ptr %argv"]
    else:
        emitted_params = params
    emitter.emit(f"define {sig.ret.text} @{emitted_name}({', '.join(emitted_params)}) {{")
    emitter.label("entry")
    if is_entry_main:
        init_argv = emitter.tmp()
        emitter.emit(f"  {init_argv} = call i64 @sprout_set_argv(i32 %argc, ptr %argv)")
        if runtime_lets:
            emitter.emit("  call void @__sprout_init_globals()")
        for _, (aliases, arity, tag) in sorted(ctor_reg_meta.items(), key=lambda x: x[1][2]):
            for reg_name, reg_len in aliases:
                reg_ptr = emitter.tmp()
                emitter.emit(
                    f"  {reg_ptr} = getelementptr inbounds [{reg_len} x i8], ptr {reg_name}, i64 0, i64 0"
                )
                reg = emitter.tmp()
                emitter.emit(
                    f"  {reg} = call i64 @sprout_register_ctor(i64 {tag}, ptr {reg_ptr}, i64 {arity})"
                )
    ret = _emit_expr(fn.body, locals_, globals_info, sigs, ctor_sigs, adt_names, emitter)
    if ret.typ != sig.ret:
        raise CodegenError(f"Function {fn.name} body type mismatch in backend: {ret.typ.text} vs {sig.ret.text}")
    if is_entry_main and _is_io_unit(fn.return_type):
        emitter.emit("  ret i64 0")
    else:
        emitter.emit(f"  ret {ret.typ.text} {ret.ir}")
    emitter.emit("}")


def _emit_init_globals(
    runtime_lets: list[ast.LetDecl],
    globals_info: dict[str, GlobalInfo],
    sigs: dict[str, FnSig],
    ctor_sigs: dict[str, CtorSig],
    adt_names: set[str],
    emitter: Emitter,
) -> None:
    emitter.emit("define void @__sprout_init_globals() {")
    emitter.label("entry")
    locals_: dict[str, Value] = {}
    for let_decl in runtime_lets:
        info = globals_info[let_decl.name]
        value = _emit_expr(let_decl.value, locals_, globals_info, sigs, ctor_sigs, adt_names, emitter)
        if value.typ != info.typ:
            raise CodegenError(
                f"Global init type mismatch for {let_decl.name}: {value.typ.text} vs {info.typ.text}"
            )
        emitter.emit(f"  store {value.typ.text} {value.ir}, ptr @{let_decl.name}")
    emitter.emit("  ret void")
    emitter.emit("}")


def _emit_expr(
    expr: ast.Expr,
    locals_: dict[str, Value],
    globals_info: dict[str, GlobalInfo],
    sigs: dict[str, FnSig],
    ctor_sigs: dict[str, CtorSig],
    adt_names: set[str],
    emitter: Emitter,
) -> Value:
    if isinstance(expr, ast.IntExpr):
        return Value(I64, str(expr.value))
    if isinstance(expr, ast.BoolExpr):
        return Value(I1, "1" if expr.value else "0")
    if isinstance(expr, ast.StringExpr):
        gname, length = emitter.string_const(expr.value)
        tmp = emitter.tmp()
        emitter.emit(f"  {tmp} = getelementptr inbounds [{length} x i8], ptr {gname}, i64 0, i64 0")
        return Value(I8_PTR, tmp)
    if isinstance(expr, ast.TupleExpr):
        items = [_emit_expr(item, locals_, globals_info, sigs, ctor_sigs, adt_names, emitter) for item in expr.items]
        tuple_items = [item.typ for item in items]
        tuple_typ = _tuple_lltype(tuple_items)
        current = "undef"
        for idx, item in enumerate(items):
            next_val = emitter.tmp()
            emitter.emit(
                f"  {next_val} = insertvalue {tuple_typ.text} {current}, {item.typ.text} {item.ir}, {idx}"
            )
            current = next_val
        return Value(tuple_typ, current, tuple_items=tuple_items)
    if isinstance(expr, ast.VarExpr):
        val = locals_.get(expr.name)
        if val is not None:
            return _value_for_inferred_type(val, getattr(expr, "inferred_type", None), adt_names, emitter)
        fn_ref = sigs.get(expr.name)
        if fn_ref is not None:
            wrapper_name = emitter.fn_wrappers.get(expr.name)
            if wrapper_name is None:
                raise CodegenError(f"Missing closure wrapper for function {expr.name}")
            closure = _emit_make_closure(f"@{wrapper_name}", [], emitter)
            return Value(
                closure.typ,
                closure.ir,
                callable_sig=CallSig(params=fn_ref.params, ret=fn_ref.ret, ret_callable_sig=fn_ref.ret_callable_sig),
            )
        global_info = globals_info.get(expr.name)
        if global_info is not None:
            tmp = emitter.tmp()
            emitter.emit(f"  {tmp} = load {global_info.typ.text}, ptr @{expr.name}")
            out = Value(global_info.typ, tmp, callable_sig=global_info.callable_sig)
            return _value_for_inferred_type(out, getattr(expr, "inferred_type", None), adt_names, emitter)
        ctor = ctor_sigs.get(expr.name)
        if ctor is not None:
            if ctor.arg_types:
                raise CodegenError(f"Constructor {ctor.name} requires arguments")
            tmp = emitter.tmp()
            emitter.emit(f"  {tmp} = call i64 @sprout_make0(i64 {ctor.tag})")
            return Value(I64, tmp)
        raise CodegenError(f"Unknown variable in backend: {expr.name}")
    if isinstance(expr, ast.LambdaExpr):
        info = getattr(expr, "_lambda_info", None)
        if info is None:
            raise CodegenError("Missing lambda lowering metadata in backend")
        captures = []
        for name in info.captures:
            value = locals_.get(name)
            if value is None:
                raise CodegenError(f"Unknown captured variable in backend: {name}")
            captures.append(value)
        closure = _emit_make_closure(f"@{info.name}", captures, emitter)
        return Value(closure.typ, closure.ir, callable_sig=info.call_sig)
    if isinstance(expr, ast.UnaryExpr):
        operand = _emit_expr(expr.operand, locals_, globals_info, sigs, ctor_sigs, adt_names, emitter)
        if expr.op == "-":
            if operand.typ != I64:
                raise CodegenError("Unary '-' backend supports Int only")
            tmp = emitter.tmp()
            emitter.emit(f"  {tmp} = sub i64 0, {operand.ir}")
            return Value(I64, tmp)
        raise CodegenError(f"Unsupported unary op in backend: {expr.op}")
    if isinstance(expr, ast.BinaryExpr):
        return _emit_binary(expr, locals_, globals_info, sigs, ctor_sigs, adt_names, emitter)
    if isinstance(expr, ast.IfExpr):
        return _emit_if(expr, locals_, globals_info, sigs, ctor_sigs, adt_names, emitter)
    if isinstance(expr, ast.CallExpr):
        return _emit_call(expr, locals_, globals_info, sigs, ctor_sigs, adt_names, emitter)
    if isinstance(expr, ast.MatchExpr):
        return _emit_match(expr, locals_, globals_info, sigs, ctor_sigs, adt_names, emitter)

    raise CodegenError(f"Unsupported expression in LLVM backend: {expr.__class__.__name__}")


def _emit_binary(
    expr: ast.BinaryExpr,
    locals_: dict[str, Value],
    globals_info: dict[str, GlobalInfo],
    sigs: dict[str, FnSig],
    ctor_sigs: dict[str, CtorSig],
    adt_names: set[str],
    emitter: Emitter,
) -> Value:
    if expr.op in {"&&", "||"}:
        return _emit_short_circuit(expr, locals_, globals_info, sigs, ctor_sigs, adt_names, emitter)

    left = _emit_expr(expr.left, locals_, globals_info, sigs, ctor_sigs, adt_names, emitter)
    right = _emit_expr(expr.right, locals_, globals_info, sigs, ctor_sigs, adt_names, emitter)

    if expr.op in {"+", "-", "*", "/"}:
        if left.typ != I64 or right.typ != I64:
            raise CodegenError(f"Arithmetic op {expr.op} expects Int")
        tmp = emitter.tmp()
        inst = {"+": "add", "-": "sub", "*": "mul", "/": "sdiv"}[expr.op]
        emitter.emit(f"  {tmp} = {inst} i64 {left.ir}, {right.ir}")
        return Value(I64, tmp)

    if expr.op in {"<", "<=", ">", ">=", "==", "!="}:
        if left.typ != right.typ:
            raise CodegenError("Comparison operands must have same type")
        if left.typ == I8_PTR and expr.op not in {"==", "!="}:
            raise CodegenError("String comparison only supports == and !=")
        if left.typ == I8_PTR:
            tmp = emitter.tmp()
            emitter.emit(f"  {tmp} = call i1 @str_eq(ptr {left.ir}, ptr {right.ir})")
            if expr.op == "==":
                return Value(I1, tmp)
            not_tmp = emitter.tmp()
            emitter.emit(f"  {not_tmp} = xor i1 {tmp}, true")
            return Value(I1, not_tmp)
        tmp = emitter.tmp()
        pred = {
            "<": "slt",
            "<=": "sle",
            ">": "sgt",
            ">=": "sge",
            "==": "eq",
            "!=": "ne",
        }[expr.op]
        emitter.emit(f"  {tmp} = icmp {pred} {left.typ.text} {left.ir}, {right.ir}")
        return Value(I1, tmp)

    raise CodegenError(f"Unsupported binary op in backend: {expr.op}")


def _emit_short_circuit(
    expr: ast.BinaryExpr,
    locals_: dict[str, Value],
    globals_info: dict[str, GlobalInfo],
    sigs: dict[str, FnSig],
    ctor_sigs: dict[str, CtorSig],
    adt_names: set[str],
    emitter: Emitter,
) -> Value:
    left = _emit_expr(expr.left, locals_, globals_info, sigs, ctor_sigs, adt_names, emitter)
    if left.typ != I1:
        raise CodegenError(f"Logical op {expr.op} expects Bool")
    left_block = emitter.current_block
    if left_block is None:
        raise CodegenError("Internal backend error: missing current block for logical op")

    rhs_label = emitter.block("logic_rhs")
    done_label = emitter.block("logic_done")
    if expr.op == "&&":
        emitter.emit(f"  br i1 {left.ir}, label %{rhs_label}, label %{done_label}")
        const_on_short = "0"
    else:
        emitter.emit(f"  br i1 {left.ir}, label %{done_label}, label %{rhs_label}")
        const_on_short = "1"

    emitter.label(rhs_label)
    right = _emit_expr(expr.right, locals_, globals_info, sigs, ctor_sigs, adt_names, emitter)
    if right.typ != I1:
        raise CodegenError(f"Logical op {expr.op} expects Bool")
    rhs_end = emitter.current_block
    if rhs_end is None:
        raise CodegenError("Internal backend error: missing rhs block for logical op")
    emitter.emit(f"  br label %{done_label}")

    emitter.label(done_label)
    phi = emitter.tmp()
    emitter.emit(
        f"  {phi} = phi i1 [ {const_on_short}, %{left_block} ], [ {right.ir}, %{rhs_end} ]"
    )
    return Value(I1, phi)


def _emit_if(
    expr: ast.IfExpr,
    locals_: dict[str, Value],
    globals_info: dict[str, GlobalInfo],
    sigs: dict[str, FnSig],
    ctor_sigs: dict[str, CtorSig],
    adt_names: set[str],
    emitter: Emitter,
) -> Value:
    cond = _emit_expr(expr.condition, locals_, globals_info, sigs, ctor_sigs, adt_names, emitter)
    if cond.typ != I1:
        raise CodegenError("if condition must be Bool")

    then_label = emitter.block("if_then")
    else_label = emitter.block("if_else")
    done_label = emitter.block("if_done")
    emitter.emit(f"  br i1 {cond.ir}, label %{then_label}, label %{else_label}")

    emitter.label(then_label)
    then_val = _emit_expr(expr.then_branch, locals_, globals_info, sigs, ctor_sigs, adt_names, emitter)
    then_end = emitter.current_block
    if then_end is None:
        raise CodegenError("Internal backend error: missing then block")
    emitter.emit(f"  br label %{done_label}")

    emitter.label(else_label)
    else_val = _emit_expr(expr.else_branch, locals_, globals_info, sigs, ctor_sigs, adt_names, emitter)
    else_end = emitter.current_block
    if else_end is None:
        raise CodegenError("Internal backend error: missing else block")
    emitter.emit(f"  br label %{done_label}")

    if then_val.typ != else_val.typ:
        raise CodegenError("if branches must have same type")

    emitter.label(done_label)
    phi = emitter.tmp()
    emitter.emit(
        f"  {phi} = phi {then_val.typ.text} [ {then_val.ir}, %{then_end} ], [ {else_val.ir}, %{else_end} ]"
    )
    callable_sig = then_val.callable_sig if then_val.callable_sig == else_val.callable_sig else None
    tuple_items = then_val.tuple_items if then_val.tuple_items == else_val.tuple_items else None
    return Value(then_val.typ, phi, callable_sig=callable_sig, tuple_items=tuple_items)


def _emit_match(
    expr: ast.MatchExpr,
    locals_: dict[str, Value],
    globals_info: dict[str, GlobalInfo],
    sigs: dict[str, FnSig],
    ctor_sigs: dict[str, CtorSig],
    adt_names: set[str],
    emitter: Emitter,
) -> Value:
    direct = _try_emit_direct_ctor_match(expr, locals_, globals_info, sigs, ctor_sigs, adt_names, emitter)
    if direct is not None:
        return direct
    scrut = _emit_expr(expr.scrutinee, locals_, globals_info, sigs, ctor_sigs, adt_names, emitter)
    done_label = emitter.block("match_done")
    next_label = emitter.block("match_next")
    branch_vals: list[tuple[Value, str]] = []

    current_fail = next_label
    first = True
    for branch in expr.branches:
        branch_label = emitter.block("match_branch")
        fail_label = emitter.block("match_next")

        if first:
            first = False
        else:
            emitter.label(current_fail)

        if isinstance(branch.pattern, (ast.WildcardPattern, ast.VarPattern)):
            emitter.emit(f"  br label %{branch_label}")
        else:
            cond = _emit_pattern_test(branch.pattern, scrut, ctor_sigs, emitter)
            emitter.emit(f"  br i1 {cond.ir}, label %{branch_label}, label %{fail_label}")

        emitter.label(branch_label)
        branch_locals = dict(locals_)
        _emit_pattern_bind(branch.pattern, scrut, branch_locals, ctor_sigs, emitter)
        value = _emit_expr(branch.value, branch_locals, globals_info, sigs, ctor_sigs, adt_names, emitter)
        end_block = emitter.current_block
        if end_block is None:
            raise CodegenError("Internal backend error: missing match branch block")
        branch_vals.append((value, end_block))
        emitter.emit(f"  br label %{done_label}")

        current_fail = fail_label

    emitter.label(current_fail)
    emitter.emit("  unreachable")

    if not branch_vals:
        raise CodegenError("Match expression has no branches")

    out_type = branch_vals[0][0].typ
    for val, _ in branch_vals[1:]:
        if val.typ != out_type:
            raise CodegenError("match branches must have same type in backend")

    emitter.label(done_label)
    phi = emitter.tmp()
    parts = ", ".join(f"[ {val.ir}, %{block} ]" for val, block in branch_vals)
    emitter.emit(f"  {phi} = phi {out_type.text} {parts}")
    callable_sig = branch_vals[0][0].callable_sig
    if any(val.callable_sig != callable_sig for val, _ in branch_vals[1:]):
        callable_sig = None
    return Value(out_type, phi, callable_sig=callable_sig)


def _try_emit_direct_ctor_match(
    expr: ast.MatchExpr,
    locals_: dict[str, Value],
    globals_info: dict[str, GlobalInfo],
    sigs: dict[str, FnSig],
    ctor_sigs: dict[str, CtorSig],
    adt_names: set[str],
    emitter: Emitter,
) -> Value | None:
    if not _supports_direct_ctor_match(expr.scrutinee, expr.branches, ctor_sigs):
        return None

    done_label = emitter.block("match_done")
    branch_vals: list[tuple[Value, str]] = []
    _emit_direct_ctor_match_scrutinee(
        expr.scrutinee,
        expr.branches,
        locals_,
        globals_info,
        sigs,
        ctor_sigs,
        adt_names,
        emitter,
        done_label,
        branch_vals,
    )
    return _finalize_match_result(branch_vals, done_label, emitter)


def _supports_direct_ctor_match(
    scrutinee: ast.Expr, branches: list[ast.MatchBranch], ctor_sigs: dict[str, CtorSig]
) -> bool:
    if any(isinstance(branch.pattern, ast.VarPattern) for branch in branches):
        return False
    return _is_direct_ctor_scrutinee(scrutinee, ctor_sigs)


def _is_direct_ctor_scrutinee(expr: ast.Expr, ctor_sigs: dict[str, CtorSig]) -> bool:
    if _direct_ctor_expr(expr, ctor_sigs) is not None:
        return True
    return (
        isinstance(expr, ast.IfExpr)
        and _is_direct_ctor_scrutinee(expr.then_branch, ctor_sigs)
        and _is_direct_ctor_scrutinee(expr.else_branch, ctor_sigs)
    )


def _direct_ctor_expr(expr: ast.Expr, ctor_sigs: dict[str, CtorSig]) -> tuple[CtorSig, list[ast.Expr]] | None:
    if isinstance(expr, ast.VarExpr):
        ctor = ctor_sigs.get(expr.name)
        if ctor is not None and not ctor.arg_types:
            return ctor, []
        return None
    if isinstance(expr, ast.CallExpr) and isinstance(expr.callee, ast.VarExpr):
        ctor = ctor_sigs.get(expr.callee.name)
        if ctor is not None and len(expr.args) == len(ctor.arg_types):
            return ctor, expr.args
    return None


def _emit_direct_ctor_match_scrutinee(
    scrutinee: ast.Expr,
    branches: list[ast.MatchBranch],
    locals_: dict[str, Value],
    globals_info: dict[str, GlobalInfo],
    sigs: dict[str, FnSig],
    ctor_sigs: dict[str, CtorSig],
    adt_names: set[str],
    emitter: Emitter,
    done_label: str,
    branch_vals: list[tuple[Value, str]],
) -> None:
    direct_ctor = _direct_ctor_expr(scrutinee, ctor_sigs)
    if direct_ctor is not None:
        ctor, arg_exprs = direct_ctor
        payloads: list[Value] = []
        for arg_expr, typ in zip(arg_exprs, ctor.arg_types):
            arg_val = _emit_expr(arg_expr, locals_, globals_info, sigs, ctor_sigs, adt_names, emitter)
            payloads.append(_coerce_value(arg_val, typ, emitter))
        _emit_direct_ctor_match_case(
            ctor,
            payloads,
            branches,
            locals_,
            globals_info,
            sigs,
            ctor_sigs,
            adt_names,
            emitter,
            done_label,
            branch_vals,
        )
        return

    if not isinstance(scrutinee, ast.IfExpr):
        raise CodegenError("Internal backend error: unsupported direct constructor scrutinee")

    cond = _emit_expr(scrutinee.condition, locals_, globals_info, sigs, ctor_sigs, adt_names, emitter)
    if cond.typ != I1:
        raise CodegenError("if condition in direct constructor match must be Bool")
    then_label = emitter.block("match_ctor_then")
    else_label = emitter.block("match_ctor_else")
    emitter.emit(f"  br i1 {cond.ir}, label %{then_label}, label %{else_label}")

    emitter.label(then_label)
    _emit_direct_ctor_match_scrutinee(
        scrutinee.then_branch,
        branches,
        locals_,
        globals_info,
        sigs,
        ctor_sigs,
        adt_names,
        emitter,
        done_label,
        branch_vals,
    )

    emitter.label(else_label)
    _emit_direct_ctor_match_scrutinee(
        scrutinee.else_branch,
        branches,
        locals_,
        globals_info,
        sigs,
        ctor_sigs,
        adt_names,
        emitter,
        done_label,
        branch_vals,
    )


def _emit_direct_ctor_match_case(
    ctor: CtorSig,
    payloads: list[Value],
    branches: list[ast.MatchBranch],
    locals_: dict[str, Value],
    globals_info: dict[str, GlobalInfo],
    sigs: dict[str, FnSig],
    ctor_sigs: dict[str, CtorSig],
    adt_names: set[str],
    emitter: Emitter,
    done_label: str,
    branch_vals: list[tuple[Value, str]],
) -> None:
    current_fail = emitter.block("match_ctor_next")
    first = True
    for branch in branches:
        branch_label = emitter.block("match_ctor_branch")
        fail_label = emitter.block("match_ctor_next")

        if first:
            first = False
        else:
            emitter.label(current_fail)

        test = _emit_direct_ctor_pattern_test(branch.pattern, ctor, payloads, ctor_sigs, emitter)
        if test is None:
            emitter.emit(f"  br label %{fail_label}")
            current_fail = fail_label
            continue
        if test.ir == "1":
            emitter.emit(f"  br label %{branch_label}")
        else:
            emitter.emit(f"  br i1 {test.ir}, label %{branch_label}, label %{fail_label}")

        emitter.label(branch_label)
        branch_locals = dict(locals_)
        _emit_direct_ctor_pattern_bind(branch.pattern, ctor, payloads, branch_locals, ctor_sigs, emitter)
        value = _emit_expr(branch.value, branch_locals, globals_info, sigs, ctor_sigs, adt_names, emitter)
        end_block = emitter.current_block
        if end_block is None:
            raise CodegenError("Internal backend error: missing direct constructor branch block")
        branch_vals.append((value, end_block))
        emitter.emit(f"  br label %{done_label}")
        current_fail = fail_label

    emitter.label(current_fail)
    emitter.emit("  unreachable")


def _emit_direct_ctor_pattern_test(
    pattern: ast.Pattern, ctor: CtorSig, payloads: list[Value], ctor_sigs: dict[str, CtorSig], emitter: Emitter
) -> Value | None:
    if isinstance(pattern, ast.WildcardPattern):
        return Value(I1, "1")
    if isinstance(pattern, ast.VarPattern):
        return None
    if isinstance(pattern, (ast.IntPattern, ast.BoolPattern, ast.StringPattern)):
        return None
    if isinstance(pattern, ast.ConstructorPattern):
        if pattern.name != ctor.name:
            return None
        if len(pattern.args) != len(ctor.arg_types):
            raise CodegenError(
                f"Constructor pattern {pattern.name} expects {len(ctor.arg_types)} args, got {len(pattern.args)}"
            )
        acc = Value(I1, "1")
        for arg_pat, arg_val in zip(pattern.args, payloads):
            test = _emit_pattern_test(arg_pat, arg_val, ctor_sigs, emitter)
            and_tmp = emitter.tmp()
            emitter.emit(f"  {and_tmp} = and i1 {acc.ir}, {test.ir}")
            acc = Value(I1, and_tmp)
        return acc
    raise CodegenError("Unsupported pattern form in direct constructor match")


def _emit_direct_ctor_pattern_bind(
    pattern: ast.Pattern,
    ctor: CtorSig,
    payloads: list[Value],
    locals_: dict[str, Value],
    ctor_sigs: dict[str, CtorSig],
    emitter: Emitter,
) -> None:
    if isinstance(pattern, (ast.WildcardPattern, ast.IntPattern, ast.BoolPattern, ast.StringPattern)):
        return
    if isinstance(pattern, ast.VarPattern):
        raise CodegenError("Direct constructor match does not support top-level variable patterns")
    if isinstance(pattern, ast.ConstructorPattern):
        if pattern.name != ctor.name:
            return
        for sub, value in zip(pattern.args, payloads):
            _emit_pattern_bind(sub, value, locals_, ctor_sigs, emitter)
        return
    raise CodegenError("Unsupported pattern form in direct constructor bind")


def _finalize_match_result(branch_vals: list[tuple[Value, str]], done_label: str, emitter: Emitter) -> Value:
    if not branch_vals:
        raise CodegenError("Match expression has no branches")

    out_type = branch_vals[0][0].typ
    for val, _ in branch_vals[1:]:
        if val.typ != out_type:
            raise CodegenError("match branches must have same type in backend")

    emitter.label(done_label)
    phi = emitter.tmp()
    parts = ", ".join(f"[ {val.ir}, %{block} ]" for val, block in branch_vals)
    emitter.emit(f"  {phi} = phi {out_type.text} {parts}")
    callable_sig = branch_vals[0][0].callable_sig
    if any(val.callable_sig != callable_sig for val, _ in branch_vals[1:]):
        callable_sig = None
    tuple_items = branch_vals[0][0].tuple_items
    if any(val.tuple_items != tuple_items for val, _ in branch_vals[1:]):
        tuple_items = None
    return Value(out_type, phi, callable_sig=callable_sig, tuple_items=tuple_items)


def _emit_tuple_field(value: Value, idx: int, emitter: Emitter) -> Value:
    tuple_items = value.tuple_items or _tuple_item_types_from_lltype(value.typ)
    if tuple_items is None:
        raise CodegenError("Tuple operation expects tuple metadata in backend")
    item_typ = tuple_items[idx]
    out = emitter.tmp()
    emitter.emit(f"  {out} = extractvalue {value.typ.text} {value.ir}, {idx}")
    return Value(item_typ, out, tuple_items=_tuple_item_types_from_lltype(item_typ))


def _emit_pattern_test(pattern: ast.Pattern, value: Value, ctor_sigs: dict[str, CtorSig], emitter: Emitter) -> Value:
    if isinstance(pattern, (ast.WildcardPattern, ast.VarPattern)):
        return Value(I1, "1")
    if isinstance(pattern, ast.IntPattern):
        if value.typ != I64:
            raise CodegenError("Int pattern expects Int scrutinee")
        tmp = emitter.tmp()
        emitter.emit(f"  {tmp} = icmp eq i64 {value.ir}, {pattern.value}")
        return Value(I1, tmp)
    if isinstance(pattern, ast.BoolPattern):
        if value.typ != I1:
            raise CodegenError("Bool pattern expects Bool scrutinee")
        lit = "1" if pattern.value else "0"
        tmp = emitter.tmp()
        emitter.emit(f"  {tmp} = icmp eq i1 {value.ir}, {lit}")
        return Value(I1, tmp)
    if isinstance(pattern, ast.StringPattern):
        if value.typ != I8_PTR:
            raise CodegenError("String pattern expects String scrutinee")
        literal_ptr, _ = emitter.string_const(pattern.value)
        tmp = emitter.tmp()
        emitter.emit(f"  {tmp} = call i1 @str_eq(ptr {value.ir}, ptr {literal_ptr})")
        return Value(I1, tmp)
    if isinstance(pattern, ast.TuplePattern):
        tuple_items = value.tuple_items or _tuple_item_types_from_lltype(value.typ)
        if tuple_items is None:
            raise CodegenError("Tuple pattern expects tuple scrutinee")
        if len(pattern.items) != len(tuple_items):
            raise CodegenError(
                f"Tuple pattern expects {len(tuple_items)} items, got {len(pattern.items)}"
            )
        acc = Value(I1, "1")
        for idx, item_pattern in enumerate(pattern.items):
            field = _emit_tuple_field(value, idx, emitter)
            test = _emit_pattern_test(item_pattern, field, ctor_sigs, emitter)
            and_tmp = emitter.tmp()
            emitter.emit(f"  {and_tmp} = and i1 {acc.ir}, {test.ir}")
            acc = Value(I1, and_tmp)
        return acc
    if isinstance(pattern, ast.ConstructorPattern):
        if value.typ != I64:
            raise CodegenError("Constructor pattern expects ADT handle scrutinee")
        ctor = ctor_sigs.get(pattern.name)
        if ctor is None:
            raise CodegenError(f"Unknown constructor in backend pattern: {pattern.name}")
        if len(pattern.args) != len(ctor.arg_types):
            raise CodegenError(
                f"Constructor pattern {pattern.name} expects {len(ctor.arg_types)} args, got {len(pattern.args)}"
            )

        tag_tmp = emitter.tmp()
        emitter.emit(f"  {tag_tmp} = call i64 @sprout_tag(i64 {value.ir})")
        eq_tag = emitter.tmp()
        emitter.emit(f"  {eq_tag} = icmp eq i64 {tag_tmp}, {ctor.tag}")
        acc = Value(I1, eq_tag)

        for idx, (arg_pat, arg_typ) in enumerate(zip(pattern.args, ctor.arg_types)):
            field = _emit_ctor_field(value.ir, idx, arg_typ, emitter)
            test = _emit_pattern_test(arg_pat, field, ctor_sigs, emitter)
            and_tmp = emitter.tmp()
            emitter.emit(f"  {and_tmp} = and i1 {acc.ir}, {test.ir}")
            acc = Value(I1, and_tmp)
        return acc

    raise CodegenError("Unsupported pattern form in backend")


def _emit_pattern_bind(
    pattern: ast.Pattern,
    value: Value,
    locals_: dict[str, Value],
    ctor_sigs: dict[str, CtorSig],
    emitter: Emitter,
) -> None:
    if isinstance(pattern, ast.WildcardPattern):
        return
    if isinstance(pattern, ast.VarPattern):
        locals_[pattern.name] = value
        return
    if isinstance(pattern, (ast.IntPattern, ast.BoolPattern, ast.StringPattern)):
        return
    if isinstance(pattern, ast.TuplePattern):
        tuple_items = value.tuple_items or _tuple_item_types_from_lltype(value.typ)
        if tuple_items is None:
            raise CodegenError("Tuple pattern bind expects tuple scrutinee")
        for idx, sub in enumerate(pattern.items):
            _emit_pattern_bind(sub, _emit_tuple_field(value, idx, emitter), locals_, ctor_sigs, emitter)
        return
    if isinstance(pattern, ast.ConstructorPattern):
        ctor = ctor_sigs.get(pattern.name)
        if ctor is None:
            raise CodegenError(f"Unknown constructor in backend bind: {pattern.name}")
        for idx, (sub, arg_typ) in enumerate(zip(pattern.args, ctor.arg_types)):
            field = _emit_ctor_field(value.ir, idx, arg_typ, emitter)
            _emit_pattern_bind(sub, field, locals_, ctor_sigs, emitter)
        return
    raise CodegenError("Unsupported pattern form in backend")


def _emit_ctor_field(handle_ir: str, idx: int, typ: LLType, emitter: Emitter) -> Value:
    raw = emitter.tmp()
    emitter.emit(f"  {raw} = call i64 @sprout_field(i64 {handle_ir}, i64 {idx})")
    if typ == I64:
        return Value(I64, raw)
    if typ == I1:
        out = emitter.tmp()
        emitter.emit(f"  {out} = trunc i64 {raw} to i1")
        return Value(I1, out)
    if typ == I8_PTR:
        out = emitter.tmp()
        emitter.emit(f"  {out} = inttoptr i64 {raw} to ptr")
        return Value(I8_PTR, out)
    raise CodegenError("Unsupported constructor field type")


def _pack_to_i64(value: Value, emitter: Emitter) -> str:
    if value.typ == I64:
        return value.ir
    if value.typ == I1:
        widened = emitter.tmp()
        emitter.emit(f"  {widened} = zext i1 {value.ir} to i64")
        return widened
    if value.typ == I8_PTR:
        out = emitter.tmp()
        emitter.emit(f"  {out} = ptrtoint ptr {value.ir} to i64")
        return out
    if (value.tuple_items or _tuple_item_types_from_lltype(value.typ)) is not None:
        size = _sizeof_struct(value.typ.text, emitter)
        raw = emitter.tmp()
        emitter.emit(f"  {raw} = call ptr @malloc(i64 {size})")
        emitter.emit(f"  store {value.typ.text} {value.ir}, ptr {raw}")
        out = emitter.tmp()
        emitter.emit(f"  {out} = ptrtoint ptr {raw} to i64")
        return out
    raise CodegenError("Cannot pack value to i64")


def _coerce_value(value: Value, target: LLType, emitter: Emitter) -> Value:
    if value.typ == target:
        return value
    if target == I64:
        return Value(I64, _pack_to_i64(value, emitter))
    if value.typ == I64 and _tuple_item_types_from_lltype(target) is not None:
        ptr = emitter.tmp()
        emitter.emit(f"  {ptr} = inttoptr i64 {value.ir} to ptr")
        out = emitter.tmp()
        emitter.emit(f"  {out} = load {target.text}, ptr {ptr}")
        return Value(target, out, tuple_items=_tuple_item_types_from_lltype(target))
    if value.typ == I64 and target == I1:
        out = emitter.tmp()
        emitter.emit(f"  {out} = trunc i64 {value.ir} to i1")
        return Value(I1, out)
    if value.typ == I64 and target == I8_PTR:
        out = emitter.tmp()
        emitter.emit(f"  {out} = inttoptr i64 {value.ir} to ptr")
        return Value(I8_PTR, out)
    raise CodegenError(f"Cannot coerce {value.typ.text} to {target.text} in backend")


def _emit_print_text(text_ir: str, emitter: Emitter) -> Value:
    tmp = emitter.tmp()
    emitter.emit(f"  {tmp} = call i64 @print_text(ptr {text_ir})")
    return Value(I64, tmp)


def _emit_print_literal(text: str, emitter: Emitter) -> Value:
    gname, length = emitter.string_const(text)
    ptr = emitter.tmp()
    emitter.emit(f"  {ptr} = getelementptr inbounds [{length} x i8], ptr {gname}, i64 0, i64 0")
    return _emit_print_text(ptr, emitter)


def _emit_print_newline(emitter: Emitter) -> Value:
    tmp = emitter.tmp()
    emitter.emit(f"  {tmp} = call i64 @print_newline()")
    return Value(I64, tmp)


def _emit_print_inline_value(value: Value, emitter: Emitter) -> Value:
    if value.typ == I64:
        tmp = emitter.tmp()
        emitter.emit(f"  {tmp} = call i64 @print_value_part(i64 {value.ir})")
        return Value(I64, tmp)
    if value.typ == I1:
        widened = emitter.tmp()
        emitter.emit(f"  {widened} = zext i1 {value.ir} to i64")
        tmp = emitter.tmp()
        emitter.emit(f"  {tmp} = call i64 @print_value_part(i64 {widened})")
        return Value(I64, tmp)
    if value.typ == I8_PTR:
        return _emit_print_text(value.ir, emitter)
    if (value.tuple_items or _tuple_item_types_from_lltype(value.typ)) is not None:
        return _emit_print_tuple_value(value, emitter)
    raise CodegenError("print backend supports Int/Bool/String/tuple only")


def _emit_print_tuple_value(value: Value, emitter: Emitter) -> Value:
    tuple_items = value.tuple_items or _tuple_item_types_from_lltype(value.typ)
    if tuple_items is None:
        raise CodegenError("Tuple print expects tuple metadata in backend")
    last = _emit_print_literal("(", emitter)
    for idx in range(len(tuple_items)):
        if idx > 0:
            last = _emit_print_literal(", ", emitter)
        field = _emit_tuple_field(value, idx, emitter)
        last = _emit_print_inline_value(field, emitter)
    last = _emit_print_literal(")", emitter)
    return last


def _emit_call(
    expr: ast.CallExpr,
    locals_: dict[str, Value],
    globals_info: dict[str, GlobalInfo],
    sigs: dict[str, FnSig],
    ctor_sigs: dict[str, CtorSig],
    adt_names: set[str],
    emitter: Emitter,
) -> Value:
    if isinstance(expr.callee, ast.VarExpr) and expr.callee.name == "print":
        if len(expr.args) != 1:
            raise CodegenError("print expects 1 argument")
        arg = _emit_expr(expr.args[0], locals_, globals_info, sigs, ctor_sigs, adt_names, emitter)
        if arg.typ == I64:
            tmp = emitter.tmp()
            emitter.emit(f"  {tmp} = call i64 @print_value(i64 {arg.ir})")
            return Value(I64, tmp)
        if arg.typ == I1:
            widened = emitter.tmp()
            emitter.emit(f"  {widened} = zext i1 {arg.ir} to i64")
            tmp = emitter.tmp()
            emitter.emit(f"  {tmp} = call i64 @print_value(i64 {widened})")
            return Value(I64, tmp)
        if arg.typ == I8_PTR:
            tmp = emitter.tmp()
            emitter.emit(f"  {tmp} = call i64 @print_str(ptr {arg.ir})")
            return Value(I64, tmp)
        if (arg.tuple_items or _tuple_item_types_from_lltype(arg.typ)) is not None:
            _emit_print_tuple_value(arg, emitter)
            return _emit_print_newline(emitter)
        raise CodegenError("print backend supports Int/Bool/String/tuple only")

    if isinstance(expr.callee, ast.VarExpr):
        fn_name = expr.callee.name
    else:
        fn_name = None

    ctor = ctor_sigs.get(fn_name) if fn_name is not None else None
    if fn_name is not None and ctor is not None:
        if len(expr.args) != len(ctor.arg_types):
            raise CodegenError(
                f"Constructor {fn_name} expects {len(ctor.arg_types)} args, got {len(expr.args)}"
            )
        if len(ctor.arg_types) == 0:
            tmp = emitter.tmp()
            emitter.emit(f"  {tmp} = call i64 @sprout_make0(i64 {ctor.tag})")
            return Value(I64, tmp)
        packed_args: list[str] = []
        for arg_expr, typ in zip(expr.args, ctor.arg_types):
            arg_val = _emit_expr(arg_expr, locals_, globals_info, sigs, ctor_sigs, adt_names, emitter)
            coerced = _coerce_value(arg_val, typ, emitter)
            packed_args.append(_pack_to_i64(coerced, emitter))
        tmp = emitter.tmp()
        if len(packed_args) == 1:
            emitter.emit(f"  {tmp} = call i64 @sprout_make1(i64 {ctor.tag}, i64 {packed_args[0]})")
        elif len(packed_args) == 2:
            emitter.emit(
                f"  {tmp} = call i64 @sprout_make2(i64 {ctor.tag}, i64 {packed_args[0]}, i64 {packed_args[1]})"
            )
        else:
            emitter.emit(
                "  "
                + f"{tmp} = call i64 @sprout_make3(i64 {ctor.tag}, i64 {packed_args[0]}, i64 {packed_args[1]}, i64 {packed_args[2]})"
            )
        return Value(I64, tmp)

    if fn_name is not None and fn_name in locals_:
        local_callee = _emit_expr(expr.callee, locals_, globals_info, sigs, ctor_sigs, adt_names, emitter)
        call_sig = _value_call_sig(local_callee, getattr(expr.callee, "inferred_type", None), adt_names)
        if call_sig is None:
            raise CodegenError(f"Missing callable signature for {fn_name}")
        args = [_emit_expr(arg_expr, locals_, globals_info, sigs, ctor_sigs, adt_names, emitter) for arg_expr in expr.args]
        out = _emit_closure_call(local_callee, call_sig, args, emitter)
    elif fn_name is not None and (fn_name in sigs or fn_name in EXTERN_SIGS):
        sig = sigs.get(fn_name) or EXTERN_SIGS.get(fn_name)
        assert sig is not None
        if len(expr.args) != len(sig.params):
            raise CodegenError(f"Function {fn_name} expects {len(sig.params)} args, got {len(expr.args)}")
        args_ir: list[str] = []
        for arg_expr, param_type in zip(expr.args, sig.params):
            arg_val = _emit_expr(arg_expr, locals_, globals_info, sigs, ctor_sigs, adt_names, emitter)
            coerced = _coerce_value(arg_val, param_type, emitter)
            args_ir.append(f"{param_type.text} {coerced.ir}")
        tmp = emitter.tmp()
        emitter.emit(f"  {tmp} = call {sig.ret.text} @{fn_name}({', '.join(args_ir)})")
        out = Value(sig.ret, tmp, callable_sig=sig.ret_callable_sig)
    else:
        callee = _emit_expr(expr.callee, locals_, globals_info, sigs, ctor_sigs, adt_names, emitter)
        call_sig = _value_call_sig(callee, getattr(expr.callee, "inferred_type", None), adt_names)
        if call_sig is None:
            raise CodegenError("Backend expected function-typed callee")
        args = [_emit_expr(arg_expr, locals_, globals_info, sigs, ctor_sigs, adt_names, emitter) for arg_expr in expr.args]
        out = _emit_closure_call(callee, call_sig, args, emitter)

    return _value_for_inferred_type(out, getattr(expr, "inferred_type", None), adt_names, emitter)
