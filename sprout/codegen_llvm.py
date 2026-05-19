from __future__ import annotations

from dataclasses import dataclass

from . import ast
from .elaborate import elaborate_program


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
    "read_file": FnSig(name="read_file", params=[I64], ret=I64),
    "write_file": FnSig(name="write_file", params=[I64, I64], ret=I64),
    "env_get": FnSig(name="env_get", params=[I8_PTR], ret=I64),
    "argv_get": FnSig(name="argv_get", params=[I64], ret=I64),
    "read_int_lines": FnSig(name="read_int_lines", params=[I8_PTR], ret=I64),
    "parse_int": FnSig(name="parse_int", params=[I8_PTR], ret=I64),
    "int_range": FnSig(name="int_range", params=[I64, I64], ret=I64),
    "int_range_start": FnSig(name="int_range_start", params=[I64], ret=I64),
    "int_range_end": FnSig(name="int_range_end", params=[I64], ret=I64),
    "int_to_string": FnSig(name="int_to_string", params=[I64], ret=I64),
    "char_to_string": FnSig(name="char_to_string", params=[I64], ret=I64),
    "str_concat": FnSig(name="str_concat", params=[I64, I64], ret=I64),
    "string_concat_many":   FnSig(name="string_concat_many",   params=[I64], ret=I64),
    "string_join_newlines": FnSig(name="string_join_newlines", params=[I64], ret=I64),
    "str_len": FnSig(name="str_len", params=[I8_PTR], ret=I64),
    "str_slice": FnSig(name="str_slice", params=[I64, I64, I64], ret=I64),
    "str_char_at": FnSig(name="str_char_at", params=[I8_PTR, I64], ret=I64),
    "str_split_lines": FnSig(name="str_split_lines", params=[I8_PTR], ret=I64),
    "str_char_at_byte": FnSig(name="str_char_at_byte", params=[I8_PTR, I64], ret=I64),
    "str_char_width_at_byte": FnSig(name="str_char_width_at_byte", params=[I8_PTR, I64], ret=I64),
    "str_byte_len": FnSig(name="str_byte_len", params=[I8_PTR], ret=I64),
    "str_starts_with_at_byte": FnSig(name="str_starts_with_at_byte", params=[I8_PTR, I64, I8_PTR], ret=I1),
    "str_eq": FnSig(name="str_eq", params=[I8_PTR, I8_PTR], ret=I1),
    "str_find": FnSig(name="str_find", params=[I8_PTR, I8_PTR], ret=I64),
    "str_starts_with": FnSig(name="str_starts_with", params=[I8_PTR, I8_PTR], ret=I1),
    "str_compare": FnSig(name="str_compare", params=[I8_PTR, I8_PTR], ret=I64),
    "regex_validate": FnSig(name="regex_validate", params=[I8_PTR], ret=I64),
    "regex_is_match": FnSig(name="regex_is_match", params=[I8_PTR, I8_PTR], ret=I1),
    "regex_find_range": FnSig(name="regex_find_range", params=[I8_PTR, I8_PTR], ret=I64),
    "regex_replace_all_literal": FnSig(name="regex_replace_all_literal", params=[I64, I64, I64], ret=I64),
    "regex_escape": FnSig(name="regex_escape", params=[I64], ret=I64),
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
    "crypto_base64_encode": FnSig(name="crypto_base64_encode", params=[I64], ret=I64),
    "crypto_base64_decode": FnSig(name="crypto_base64_decode", params=[I8_PTR], ret=I64),
    "crypto_bytes_xor": FnSig(name="crypto_bytes_xor", params=[I64, I64], ret=I64),
    "crypto_random_bytes": FnSig(name="crypto_random_bytes", params=[I64], ret=I64),
    "vector_empty": FnSig(name="vector_empty", params=[], ret=I64),
    "vector_length": FnSig(name="vector_length", params=[I64], ret=I64),
    "vector_get": FnSig(name="vector_get", params=[I64, I64], ret=I64),
    "vector_set": FnSig(name="vector_set", params=[I64, I64, I64], ret=I64),
    "vector_append": FnSig(name="vector_append", params=[I64, I64], ret=I64),
    "vector_from_list": FnSig(name="vector_from_list", params=[I64], ret=I64),
    "vector_sort_by_int": FnSig(name="vector_sort_by_int", params=[I64], ret=I64),
    "map_empty": FnSig(name="map_empty", params=[], ret=I64),
    "map_get": FnSig(name="map_get", params=[I64, I8_PTR], ret=I64),
    "map_set": FnSig(name="map_set", params=[I64, I8_PTR, I64], ret=I64),
    "map_remove": FnSig(name="map_remove", params=[I64, I8_PTR], ret=I64),
    "map_size": FnSig(name="map_size", params=[I64], ret=I64),
    "map_nth_key": FnSig(name="map_nth_key", params=[I64, I64], ret=I64),
    "map_nth_value": FnSig(name="map_nth_value", params=[I64, I64], ret=I64),
    "native_set_empty": FnSig(name="native_set_empty", params=[], ret=I64),
    "native_set_insert": FnSig(name="native_set_insert", params=[I8_PTR, I64], ret=I64),
    "native_set_member": FnSig(name="native_set_member", params=[I8_PTR, I64], ret=I1),
    "native_set_to_list": FnSig(name="native_set_to_list", params=[I64], ret=I64),
    "native_set_size": FnSig(name="native_set_size", params=[I64], ret=I64),
    "tcp_listen": FnSig(name="tcp_listen", params=[I64], ret=I64),
    "tcp_accept": FnSig(name="tcp_accept", params=[I64], ret=I64),
    "tcp_read": FnSig(name="tcp_read", params=[I64], ret=I64),
    "tcp_write": FnSig(name="tcp_write", params=[I64, I8_PTR], ret=I64),
    "tcp_connect": FnSig(name="tcp_connect", params=[I8_PTR, I64], ret=I64),
    "tcp_read_exact": FnSig(name="tcp_read_exact", params=[I64, I64], ret=I64),
    "tcp_write_all": FnSig(name="tcp_write_all", params=[I64, I64], ret=I64),
    "tcp_close": FnSig(name="tcp_close", params=[I64], ret=I64),
    "tcp_close_listener": FnSig(name="tcp_close_listener", params=[I64], ret=I64),
    "tcp_echo_serve": FnSig(name="tcp_echo_serve", params=[I64, I64], ret=I64),
    "http_request": FnSig(name="http_request", params=[I8_PTR, I8_PTR, I8_PTR, I8_PTR, I64], ret=I64),
    "json_parse": FnSig(name="json_parse", params=[I8_PTR], ret=I64),
    "json_stringify": FnSig(name="json_stringify", params=[I64], ret=I64),
    "term_clear": FnSig(name="term_clear", params=[], ret=I64),
    "term_move": FnSig(name="term_move", params=[I64, I64], ret=I64),
    "term_hide_cursor": FnSig(name="term_hide_cursor", params=[], ret=I64),
    "term_show_cursor": FnSig(name="term_show_cursor", params=[], ret=I64),
    "term_read_key": FnSig(name="term_read_key", params=[], ret=I64),
    "term_read_line": FnSig(name="term_read_line", params=[], ret=I64),
    "term_is_interactive": FnSig(name="term_is_interactive", params=[], ret=I1),
    "term_write": FnSig(name="term_write", params=[I8_PTR], ret=I64),
    "repl_add_import": FnSig(name="repl_add_import", params=[I8_PTR], ret=I64),
    "repl_add_declaration": FnSig(name="repl_add_declaration", params=[I8_PTR], ret=I64),
    "repl_eval_expr": FnSig(name="repl_eval_expr", params=[I8_PTR], ret=I64),
    "repl_eval_expr_in_source": FnSig(name="repl_eval_expr_in_source", params=[I8_PTR, I8_PTR], ret=I64),
    "analysis_eval_expr_in_source": FnSig(name="analysis_eval_expr_in_source", params=[I8_PTR, I8_PTR], ret=I64),
    "repl_check_source": FnSig(name="repl_check_source", params=[I8_PTR], ret=I64),
    "analysis_check_source": FnSig(name="analysis_check_source", params=[I8_PTR], ret=I64),
    "repl_declared_names_in_source": FnSig(name="repl_declared_names_in_source", params=[I8_PTR], ret=I64),
    "analysis_declared_names_in_source": FnSig(name="analysis_declared_names_in_source", params=[I8_PTR], ret=I64),
    "repl_exported_names_in_source": FnSig(name="repl_exported_names_in_source", params=[I8_PTR], ret=I64),
    "analysis_exported_names_in_source": FnSig(name="analysis_exported_names_in_source", params=[I8_PTR], ret=I64),
    "repl_symbol_inventory_in_source": FnSig(name="repl_symbol_inventory_in_source", params=[I8_PTR], ret=I64),
    "analysis_symbol_inventory_in_source": FnSig(name="analysis_symbol_inventory_in_source", params=[I8_PTR], ret=I64),
    "analysis_symbol_locations_in_source": FnSig(name="analysis_symbol_locations_in_source", params=[I8_PTR], ret=I64),
    "repl_diagnostics_in_source": FnSig(name="repl_diagnostics_in_source", params=[I8_PTR], ret=I64),
    "analysis_diagnostics_in_source": FnSig(name="analysis_diagnostics_in_source", params=[I8_PTR], ret=I64),
    "repl_type_of": FnSig(name="repl_type_of", params=[I8_PTR], ret=I64),
    "repl_type_of_in_source": FnSig(name="repl_type_of_in_source", params=[I8_PTR, I8_PTR], ret=I64),
    "analysis_type_of_in_source": FnSig(name="analysis_type_of_in_source", params=[I8_PTR, I8_PTR], ret=I64),
    "repl_instances": FnSig(name="repl_instances", params=[I8_PTR], ret=I64),
    "repl_instances_in_source": FnSig(name="repl_instances_in_source", params=[I8_PTR, I8_PTR], ret=I64),
    "analysis_instances_in_source": FnSig(name="analysis_instances_in_source", params=[I8_PTR, I8_PTR], ret=I64),
    "repl_complete": FnSig(name="repl_complete", params=[I8_PTR], ret=I64),
    "repl_complete_in_state": FnSig(name="repl_complete_in_state", params=[I8_PTR, I8_PTR, I8_PTR], ret=I64),
    "analysis_complete_in_state": FnSig(name="analysis_complete_in_state", params=[I8_PTR, I8_PTR, I8_PTR], ret=I64),
    "repl_reset_session": FnSig(name="repl_reset_session", params=[], ret=I64),
    "sprout_set_argv": FnSig(name="sprout_set_argv", params=[I32, I8_PTR], ret=I64),
    "sprout_register_ctor": FnSig(name="sprout_register_ctor", params=[I64, I8_PTR, I64], ret=I64),
    "sprout_nothing": FnSig(name="sprout_nothing", params=[I64], ret=I64),
    "sprout_make0": FnSig(name="sprout_make0", params=[I64], ret=I64),
    "sprout_make1": FnSig(name="sprout_make1", params=[I64, I64], ret=I64),
    "sprout_make2": FnSig(name="sprout_make2", params=[I64, I64, I64], ret=I64),
    "sprout_make3": FnSig(name="sprout_make3", params=[I64, I64, I64, I64], ret=I64),
    "sprout_make4": FnSig(name="sprout_make4", params=[I64, I64, I64, I64, I64], ret=I64),
    "sprout_make5": FnSig(name="sprout_make5", params=[I64, I64, I64, I64, I64, I64], ret=I64),
    "sprout_make6": FnSig(name="sprout_make6", params=[I64, I64, I64, I64, I64, I64, I64], ret=I64),
    "sprout_make7": FnSig(name="sprout_make7", params=[I64, I64, I64, I64, I64, I64, I64, I64], ret=I64),
    "sprout_make8": FnSig(name="sprout_make8", params=[I64, I64, I64, I64, I64, I64, I64, I64, I64], ret=I64),
    "sprout_make9": FnSig(name="sprout_make9", params=[I64, I64, I64, I64, I64, I64, I64, I64, I64, I64], ret=I64),
    "sprout_alloc_closure_env": FnSig(name="sprout_alloc_closure_env", params=[I64], ret=I64),
    "sprout_alloc_tuple_blob": FnSig(name="sprout_alloc_tuple_blob", params=[I64], ret=I64),
    "sprout_gc_register_i64_root": FnSig(name="sprout_gc_register_i64_root", params=[I8_PTR], ret=I64),
    "sprout_gc_register_ptr_root": FnSig(name="sprout_gc_register_ptr_root", params=[I8_PTR], ret=I64),
    "sprout_gc_register_scan_root": FnSig(name="sprout_gc_register_scan_root", params=[I8_PTR, I64], ret=I64),
    "sprout_gc_push_i64_root": FnSig(name="sprout_gc_push_i64_root", params=[I8_PTR], ret=I64),
    "sprout_gc_push_ptr_root": FnSig(name="sprout_gc_push_ptr_root", params=[I8_PTR], ret=I64),
    "sprout_gc_push_scan_root": FnSig(name="sprout_gc_push_scan_root", params=[I8_PTR, I64], ret=I64),
    "sprout_gc_pop_roots": FnSig(name="sprout_gc_pop_roots", params=[I64], ret=I64),
    "sprout_tag": FnSig(name="sprout_tag", params=[I64], ret=I64),
    "sprout_field": FnSig(name="sprout_field", params=[I64, I64], ret=I64),
    "ref_new": FnSig(name="ref_new", params=[I64], ret=I64),
    "ref_read": FnSig(name="ref_read", params=[I64], ret=I64),
    "ref_write": FnSig(name="ref_write", params=[I64, I64], ret=I64),
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
    # Parallel to tuple_items: True for String/Char fields, or a nested list for tuple fields.
    tuple_item_is_string: list[object] | None = None
    # Non-None signals a pending TCO back-edge: list of coerced new argument values.
    tco_args: "list[Value] | None" = None


@dataclass
class TcoCtx:
    """Context threaded through expression emission for tail-call optimisation."""
    fn_name: str
    loop_label: str
    param_slots: list[tuple[str, LLType]]  # (alloca_ir, slot_type)
    ret: LLType
    outer_roots: int = 0  # accumulated pattern-bind roots from enclosing scopes
    sp_save: str = ""    # llvm.stacksave result; restored at each TCO back-edge


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

    def is_block_terminated(self) -> bool:
        """Return True if the current block already ends with a terminator."""
        for line in reversed(self.lines):
            if not line.strip():
                continue
            # A label (no leading whitespace, ends with ':') starts a fresh block
            if not line.startswith("  ") and line.strip().endswith(":"):
                return False
            stripped = line.strip()
            return stripped.startswith("br ") or stripped.startswith("ret ") or stripped == "unreachable"
        return False

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
            return I64
        if node.name == "Char":
            return I64
        if node.name == "Bytes":
            return I64
        if node.name == "Builder":
            return I64
        if node.name == "IntRange":
            return I64
        if node.name == "Unit":
            return I64
        if node.name == "NativeSet":
            return I64
        if node.name in adt_names or leaf in adt_leaf_names:
            return I64
        if leaf and leaf[0].islower():
            return I64
    if isinstance(node, ast.TypeApply):
        base_name = _type_base_name(node)
        base_leaf = base_name.rsplit(".", 1)[-1] if base_name is not None else None
        if base_name in adt_names or (base_leaf is not None and base_leaf in adt_leaf_names):
            return I64
        if base_name in {"Vector", "Map", "Bytes", "Ref"}:
            return I64
        if base_leaf and base_leaf[0].islower():
            return I64
    if isinstance(node, ast.TypeEffect):
        return _type_from_ast(node.base, adt_names)
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


def _string_meta_from_type_expr(node: ast.TypeExpr | None, adt_names: set[str]) -> object:
    if isinstance(node, ast.TypeName) and node.name.rsplit(".", 1)[-1] in {"String", "Char"}:
        return True
    if isinstance(node, ast.TupleType):
        return [_string_meta_from_type_expr(item, adt_names) for item in node.items]
    return False


def _tuple_item_string_meta_from_type_expr(
    node: ast.TypeExpr | None,
    adt_names: set[str],
) -> list[object] | None:
    meta = _string_meta_from_type_expr(node, adt_names)
    return meta if isinstance(meta, list) else None


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
    if isinstance(node, ast.TypeEffect):
        return _type_base_name(node.base)
    return None


def _check_param_type(node: ast.TypeExpr, adt_names: set[str]) -> LLType:
    typ, _ = _lower_value_type(node, adt_names)
    return typ


def _is_effectful_unit(return_type: ast.TypeExpr | None, effects: tuple[str, ...] | None) -> bool:
    return isinstance(return_type, ast.TypeName) and return_type.name == "Unit" and effects == ("IO",)


def _lower_value_type(node: ast.TypeExpr, adt_names: set[str]) -> tuple[LLType, CallSig | None]:
    if isinstance(node, ast.TypeEffect):
        return _lower_value_type(node.base, adt_names)
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


def _emit_push_temp_root(value: Value, emitter: Emitter) -> int:
    tuple_items = value.tuple_items or _tuple_item_types_from_lltype(value.typ)
    if value.typ == I64:
        slot = emitter.tmp()
        emitter.emit(f"  {slot} = alloca i64")
        emitter.emit(f"  store i64 {value.ir}, ptr {slot}")
        reg = emitter.tmp()
        emitter.emit(f"  {reg} = call i64 @sprout_gc_push_i64_root(ptr {slot})")
        return 1
    if value.typ == I8_PTR:
        slot = emitter.tmp()
        emitter.emit(f"  {slot} = alloca ptr")
        emitter.emit(f"  store ptr {value.ir}, ptr {slot}")
        reg = emitter.tmp()
        emitter.emit(f"  {reg} = call i64 @sprout_gc_push_ptr_root(ptr {slot})")
        return 1
    if tuple_items is not None:
        slot = emitter.tmp()
        emitter.emit(f"  {slot} = alloca {value.typ.text}")
        emitter.emit(f"  store {value.typ.text} {value.ir}, ptr {slot}")
        size = _sizeof_struct(value.typ.text, emitter)
        reg = emitter.tmp()
        emitter.emit(f"  {reg} = call i64 @sprout_gc_push_scan_root(ptr {slot}, i64 {size})")
        return 1
    return 0


def _emit_pop_temp_roots(count: int, emitter: Emitter) -> None:
    if count <= 0:
        return
    reg = emitter.tmp()
    emitter.emit(f"  {reg} = call i64 @sprout_gc_pop_roots(i64 {count})")


def _sprout_type_is_plain_int(typ: "ast.TypeExpr | None") -> bool:
    """Return True for the Sprout Int type — a plain unboxed integer, never a GC heap pointer."""
    return isinstance(typ, ast.TypeName) and typ.name == "Int"


def _emit_exprs_with_temp_roots(
    exprs: list[ast.Expr],
    locals_: dict[str, Value],
    globals_info: dict[str, tuple[LLType, str]],
    sigs: dict[str, FnSig],
    ctor_sigs: dict[str, CtorInfo],
    adt_names: set[str],
    emitter: Emitter,
) -> tuple[list[Value], int]:
    rooted = 0
    values: list[Value] = []
    for expr in exprs:
        value = _emit_expr(expr, locals_, globals_info, sigs, ctor_sigs, adt_names, emitter)
        values.append(value)
        rooted += _emit_push_temp_root(value, emitter)
    return values, rooted


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


def _lambda_body_type(expr: ast.LambdaExpr) -> ast.TypeExpr | None:
    """Return the inferred return type of a lambda, using the body's annotation if present,
    falling back to peeling the lambda's own TypeArrow inferred_type."""
    body_type = getattr(expr.body, "inferred_type", None)
    if body_type is not None:
        return body_type
    lam_type = getattr(expr, "inferred_type", None)
    if lam_type is None:
        return None
    # Peel one TypeArrow per parameter to reach the return type.
    t = lam_type
    for _ in expr.params:
        if isinstance(t, ast.TypeArrow):
            t = t.right
        elif isinstance(t, ast.TypeEffect):
            inner = t.base
            if isinstance(inner, ast.TypeArrow):
                t = inner.right
            else:
                return None
        else:
            return None
    # Strip a trailing TypeEffect wrapper if present (effects annotation on the return).
    if isinstance(t, ast.TypeEffect):
        t = t.base
    return t


def _lambda_param_ll_type(type_expr: "ast.TypeExpr", adt_names: set[str]) -> LLType:
    """Return the LLVM calling-convention type for a lambda parameter.

    Lambdas use the universal closure convention: all arguments are passed as
    i64 (or ptr for ptr-typed values).  Tuple-typed parameters are no exception
    — the caller packs the tuple as an i64 blob pointer; the lambda body then
    loads the struct from that pointer when it needs field access.
    """
    ll = _type_from_ast(type_expr, adt_names)
    # Tuple types are stack-allocated structs in named-function signatures, but
    # closures receive them as i64 (packed blob pointer), so normalise here.
    if _tuple_item_types_from_lltype(ll) is not None:
        return I64
    return ll


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
        params.append(_lambda_param_ll_type(param.type_expr, adt_names))
    body_type = _lambda_body_type(expr)
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
            params.append(_lambda_param_ll_type(param.type_expr, adt_names))
        body_type = _lambda_body_type(expr)
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
        if len(expr.args) > len(callee_sig.params):
            return None
        remaining = callee_sig.params[len(expr.args) :]
        if remaining:
            return CallSig(params=remaining, ret=callee_sig.ret, ret_callable_sig=callee_sig.ret_callable_sig)
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
    tuple_item_is_string = _tuple_item_string_meta_from_type_expr(inferred_type, adt_names)
    return Value(
        typ=coerced.typ,
        ir=coerced.ir,
        callable_sig=value.callable_sig or call_sig,
        tuple_items=tuple_items if tuple_items is not None else coerced.tuple_items,
        tuple_item_is_string=tuple_item_is_string
        if tuple_item_is_string is not None
        else coerced.tuple_item_is_string,
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
    if isinstance(expr, ast.IntRangeExpr):
        _collect_free_vars(expr.start, bound, out, seen)
        _collect_free_vars(expr.end, bound, out, seen)
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
    if isinstance(expr, ast.IntRangeExpr):
        _gather_lambda_infos(expr.start, available_locals, sigs, globals_info, adt_names, infos, emitter)
        _gather_lambda_infos(expr.end, available_locals, sigs, globals_info, adt_names, infos, emitter)
        return
    if isinstance(expr, ast.UnaryExpr):
        _gather_lambda_infos(expr.operand, available_locals, sigs, globals_info, adt_names, infos, emitter)
        return
    if isinstance(expr, ast.CallExpr):
        _gather_lambda_infos(expr.callee, available_locals, sigs, globals_info, adt_names, infos, emitter)
        for arg in expr.args:
            _gather_lambda_infos(arg, available_locals, sigs, globals_info, adt_names, infos, emitter)


def _emit_make_closure(code_ir: str, captures: list[Value], emitter: Emitter) -> Value:
    rooted = 0
    for capture in captures:
        rooted += _emit_push_temp_root(capture, emitter)
    size = emitter.tmp()
    emitter.emit(f"  {size} = add i64 {8 * (len(captures) + 1)}, 0")
    raw_i64 = emitter.tmp()
    emitter.emit(f"  {raw_i64} = call i64 @sprout_alloc_closure_env(i64 {size})")
    raw = emitter.tmp()
    emitter.emit(f"  {raw} = inttoptr i64 {raw_i64} to ptr")
    emitter.emit(f"  store ptr {code_ir}, ptr {raw}")
    for idx, capture in enumerate(captures, start=1):
        packed = _pack_to_i64(capture, emitter)
        slot = emitter.tmp()
        emitter.emit(f"  {slot} = getelementptr i64, ptr {raw}, i64 {idx}")
        emitter.emit(f"  store i64 {packed}, ptr {slot}")
    _emit_pop_temp_roots(rooted, emitter)
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


def _emit_partial_direct_wrapper(wrapper_name: str, target_name: str, sig: FnSig, applied_count: int) -> list[str]:
    remaining = sig.params[applied_count:]
    lines = [f"define {sig.ret.text} @{wrapper_name}(ptr %env{''.join(f', {t.text} %a{i}' for i, t in enumerate(remaining))}) {{", "entry:"]
    arg_values: list[str] = []
    for idx, typ in enumerate(sig.params[:applied_count], start=1):
        slot = f"%slot{idx - 1}"
        raw = f"%raw{idx - 1}"
        val = f"%cap{idx - 1}"
        lines.append(f"  {slot} = getelementptr i64, ptr %env, i64 {idx}")
        lines.append(f"  {raw} = load i64, ptr {slot}")
        if typ == I64:
            arg_values.append(f"i64 {raw}")
        elif typ == I1:
            lines.append(f"  {val} = trunc i64 {raw} to i1")
            arg_values.append(f"i1 {val}")
        elif typ == I8_PTR:
            lines.append(f"  {val} = inttoptr i64 {raw} to ptr")
            arg_values.append(f"{typ.text} {val}")
        else:
            ptr = f"%ptr{idx - 1}"
            lines.append(f"  {ptr} = inttoptr i64 {raw} to ptr")
            lines.append(f"  {val} = load {typ.text}, ptr {ptr}")
            arg_values.append(f"{typ.text} {val}")
    arg_values.extend(f"{t.text} %a{i}" for i, t in enumerate(remaining))
    call = (
        f"  %ret = call {sig.ret.text} @{target_name}({', '.join(arg_values)})"
        if arg_values
        else f"  %ret = call {sig.ret.text} @{target_name}()"
    )
    lines.append(call)
    lines.append(f"  ret {sig.ret.text} %ret")
    lines.append("}")
    return lines


def _emit_partial_closure_wrapper(wrapper_name: str, call_sig: CallSig, applied_count: int) -> list[str]:
    remaining = call_sig.params[applied_count:]
    lines = [f"define {call_sig.ret.text} @{wrapper_name}(ptr %env{''.join(f', {t.text} %a{i}' for i, t in enumerate(remaining))}) {{", "entry:"]
    lines.append("  %callee_slot = getelementptr i64, ptr %env, i64 1")
    lines.append("  %callee_raw = load i64, ptr %callee_slot")
    lines.append("  %callee = inttoptr i64 %callee_raw to ptr")
    arg_values: list[str] = []
    for idx, typ in enumerate(call_sig.params[:applied_count], start=2):
        slot = f"%slot{idx - 1}"
        raw = f"%raw{idx - 1}"
        val = f"%cap{idx - 1}"
        lines.append(f"  {slot} = getelementptr i64, ptr %env, i64 {idx}")
        lines.append(f"  {raw} = load i64, ptr {slot}")
        if typ == I64:
            arg_values.append(f"i64 {raw}")
        elif typ == I1:
            lines.append(f"  {val} = trunc i64 {raw} to i1")
            arg_values.append(f"i1 {val}")
        elif typ == I8_PTR:
            lines.append(f"  {val} = inttoptr i64 {raw} to ptr")
            arg_values.append(f"{typ.text} {val}")
        else:
            ptr = f"%ptr{idx - 1}"
            lines.append(f"  {ptr} = inttoptr i64 {raw} to ptr")
            lines.append(f"  {val} = load {typ.text}, ptr {ptr}")
            arg_values.append(f"{typ.text} {val}")
    arg_values.extend(f"{t.text} %a{i}" for i, t in enumerate(remaining))
    lines.append(
        f"  %ret = call {call_sig.ret.text} %callee(ptr %callee, {', '.join(arg_values)})"
        if arg_values
        else f"  %ret = call {call_sig.ret.text} %callee(ptr %callee)"
    )
    lines.append(f"  ret {call_sig.ret.text} %ret")
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
    helper = _clone_emitter(emitter)
    helper.lines = []
    params = ["ptr %env"] + [f"{typ.text} %a{i}" for i, typ in enumerate(info.call_sig.params)]
    helper.emit(f"define {info.call_sig.ret.text} @{info.name}({', '.join(params)}) {{")
    helper.label("entry")
    locals_: dict[str, Value] = {}
    rooted = _emit_push_temp_root(Value(I8_PTR, "%env"), helper)
    for idx, capture in enumerate(info.captures, start=1):
        slot = helper.tmp()
        helper.emit(f"  {slot} = getelementptr i64, ptr %env, i64 {idx}")
        raw = helper.tmp()
        helper.emit(f"  {raw} = load i64, ptr {slot}")
        locals_[capture] = Value(I64, raw)
        rooted += _emit_push_temp_root(Value(I64, raw), helper)
    for idx, param in enumerate(info.expr.params):
        raw_ll = info.call_sig.params[idx]
        raw_value = Value(raw_ll, f"%a{idx}", _call_sig_from_type_expr(param.type_expr, adt_names))
        # If the logical type is a tuple but the closure convention passed it as
        # I64 (packed blob pointer), coerce it to the struct type now so that
        # pattern-bind code (extractvalue) works correctly inside the lambda body.
        logical_ll = _type_from_ast(param.type_expr, adt_names) if param.type_expr is not None else raw_ll
        if raw_ll == I64 and _tuple_item_types_from_lltype(logical_ll) is not None:
            value = _coerce_value(raw_value, logical_ll, helper)
            value = Value(logical_ll, value.ir,
                          callable_sig=raw_value.callable_sig,
                          tuple_items=_tuple_item_types_from_lltype(logical_ll))
        else:
            value = raw_value
        rooted += _emit_push_temp_root(value, helper)
        locals_[param.name] = value
    ret = _emit_expr(info.expr.body, locals_, globals_info, sigs, ctor_sigs, adt_names, helper)
    if ret.typ != info.call_sig.ret:
        raise CodegenError(f"Lambda body type mismatch in backend: {ret.typ.text} vs {info.call_sig.ret.text}")
    _emit_pop_temp_roots(rooted, helper)
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
        return I64
    if isinstance(expr, ast.CharExpr):
        return I64
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
    if isinstance(expr, ast.IntRangeExpr):
        return I64
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
            return I8_PTR if len(expr.args) < len(sigs[name].params) else sigs[name].ret
        if name in EXTERN_SIGS:
            return I8_PTR if len(expr.args) < len(EXTERN_SIGS[name].params) else EXTERN_SIGS[name].ret
        raise CodegenError(f"Cannot infer top-level let call type for {name}")
    if isinstance(expr, ast.LambdaExpr):
        return I8_PTR
    if isinstance(expr, ast.MatchExpr):
        if not expr.branches:
            raise CodegenError("Cannot infer top-level let type for empty match")
        return _infer_expr_type(expr.branches[0].value, globals_info, sigs, ctor_sigs)
    raise CodegenError("Cannot infer top-level let type for expression")


def compile_to_llvm(program: ast.Program, *, entry_main_name: str = "main") -> str:
    elaborate_program(program)
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
        and not isinstance(d, ast.AliasDecl)
        and not isinstance(d, ast.ExternFnDecl)
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
            if len(arg_types) > 9:
                raise CodegenError(
                    f"Constructor {ctor.name} has {len(arg_types)} args; backend currently supports up to 9"
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

    reachable_fn_names = {fn.name for fn in all_fn_decls if fn.name == entry_main_name}
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
        if fn.name == entry_main_name:
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
        _emit_fn(fn, sigs, ctor_sigs, ctor_reg_meta, globals_info, adt_names, runtime_lets, emitter, entry_main_name)
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
        if isinstance(node, ast.TupleExpr):
            for item in node.items:
                visit(item)
            return
        if isinstance(node, ast.BinaryExpr):
            visit(node.left)
            visit(node.right)
            return
        if isinstance(node, ast.IntRangeExpr):
            visit(node.start)
            visit(node.end)
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


def _has_self_tail_calls(expr: ast.Expr, fn_name: str) -> bool:
    """Return True if *expr* contains a direct self-tail-call to *fn_name*.

    Only descends into syntactic tail positions (if/match branches).
    Does NOT descend into lambda bodies or call arguments.
    """
    if isinstance(expr, ast.CallExpr):
        return isinstance(expr.callee, ast.VarExpr) and expr.callee.name == fn_name
    if isinstance(expr, ast.IfExpr):
        return _has_self_tail_calls(expr.then_branch, fn_name) or _has_self_tail_calls(expr.else_branch, fn_name)
    if isinstance(expr, ast.MatchExpr):
        return any(_has_self_tail_calls(branch.value, fn_name) for branch in expr.branches)
    return False


def _emit_fn(
    fn: ast.FnDecl,
    sigs: dict[str, FnSig],
    ctor_sigs: dict[str, CtorSig],
    ctor_reg_meta: dict[str, tuple[tuple[str, int], tuple[str, int] | None, int, int]],
    globals_info: dict[str, GlobalInfo],
    adt_names: set[str],
    runtime_lets: list[ast.LetDecl],
    emitter: Emitter,
    entry_main_name: str,
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

    is_entry_main = fn.name == entry_main_name
    # Apply TCO for non-entry self-recursive functions.
    if not is_entry_main and _has_self_tail_calls(fn.body, fn.name):
        _emit_fn_tco(fn, sigs, ctor_sigs, globals_info, adt_names, emitter)
        return

    emitted_name = "main" if is_entry_main else fn.name
    if is_entry_main:
        emitted_params = ["i32 %argc", "ptr %argv"]
    else:
        emitted_params = params
    define_ret = "i32" if is_entry_main else sig.ret.text
    emitter.emit(f"define {define_ret} @{emitted_name}({', '.join(emitted_params)}) {{")
    emitter.label("entry")
    rooted = 0
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
    else:
        for value in locals_.values():
            rooted += _emit_push_temp_root(value, emitter)
    ret = _emit_expr(fn.body, locals_, globals_info, sigs, ctor_sigs, adt_names, emitter)
    if ret.typ != sig.ret:
        raise CodegenError(f"Function {fn.name} body type mismatch in backend: {ret.typ.text} vs {sig.ret.text}")
    _emit_pop_temp_roots(rooted, emitter)
    if is_entry_main:
        if _is_effectful_unit(fn.return_type, fn.effects):
            emitter.emit("  ret i32 0")
        else:
            exit_reg = emitter.tmp()
            emitter.emit(f"  {exit_reg} = trunc i64 {ret.ir} to i32")
            emitter.emit(f"  ret i32 {exit_reg}")
    else:
        emitter.emit(f"  ret {ret.typ.text} {ret.ir}")
    emitter.emit("}")


def _emit_fn_tco(
    fn: ast.FnDecl,
    sigs: dict[str, FnSig],
    ctor_sigs: dict[str, CtorSig],
    globals_info: dict[str, GlobalInfo],
    adt_names: set[str],
    emitter: Emitter,
) -> None:
    """Emit a self-recursive function using an explicit loop (TCO Option C).

    The entry block allocates one alloca slot per parameter, stores the initial
    argument values, registers the slots as GC roots (once), then jumps to a loop
    header.  The loop header loads the current parameter values, emits the body
    normally.  Self-tail-calls store new argument values back into the slots and
    branch to the loop header instead of calling recursively.  Non-tail returns
    pop the GC roots and execute a normal ``ret``.
    """
    sig = sigs[fn.name]
    params_ir = ", ".join(f"{typ.text} %{p.name}" for p, typ in zip(fn.params, sig.params))
    emitter.emit(f"define {sig.ret.text} @{fn.name}({params_ir}) {{")
    emitter.label("entry")

    loop_label = emitter.block("tco_loop")
    param_slots: list[tuple[str, LLType]] = []
    n_roots = 0
    for p, typ in zip(fn.params, sig.params):
        slot = emitter.tmp()
        emitter.emit(f"  {slot} = alloca {typ.text}")
        emitter.emit(f"  store {typ.text} %{p.name}, ptr {slot}")
        if typ == I64:
            reg = emitter.tmp()
            emitter.emit(f"  {reg} = call i64 @sprout_gc_push_i64_root(ptr {slot})")
            n_roots += 1
        elif typ == I8_PTR:
            reg = emitter.tmp()
            emitter.emit(f"  {reg} = call i64 @sprout_gc_push_ptr_root(ptr {slot})")
            n_roots += 1
        elif _tuple_item_types_from_lltype(typ) is not None:
            size = _sizeof_struct(typ.text, emitter)
            reg = emitter.tmp()
            emitter.emit(f"  {reg} = call i64 @sprout_gc_push_scan_root(ptr {slot}, i64 {size})")
            n_roots += 1
        # I1 (Bool): alloca slot for loop state but no GC root needed
        param_slots.append((slot, typ))
    # Save the stack pointer so each TCO back-edge can restore it, freeing any
    # alloca-in-loop dynamic stack growth (see llvm.stacksave / llvm.stackrestore).
    sp_save = emitter.tmp()
    emitter.emit(f"  {sp_save} = call ptr @llvm.stacksave()")
    emitter.emit(f"  br label %{loop_label}")

    emitter.label(loop_label)
    locals_: dict[str, Value] = {}
    for (slot, typ), p in zip(param_slots, fn.params):
        loaded = emitter.tmp()
        emitter.emit(f"  {loaded} = load {typ.text}, ptr {slot}")
        _, call_sig = _lower_value_type(p.type_expr, adt_names)
        locals_[p.name] = Value(
            typ=typ,
            ir=loaded,
            callable_sig=call_sig,
            tuple_items=_tuple_item_types_from_type_expr(p.type_expr, adt_names),
        )

    tco_ctx = TcoCtx(
        fn_name=fn.name,
        loop_label=loop_label,
        param_slots=param_slots,
        ret=sig.ret,
        outer_roots=0,
        sp_save=sp_save,
    )
    ret = _emit_expr(fn.body, locals_, globals_info, sigs, ctor_sigs, adt_names, emitter, tco_ctx=tco_ctx)

    if ret.tco_args is not None:
        # Body was itself a direct self-tail-call (degenerate: fn f(n) = f(n-1))
        _emit_pop_temp_roots(n_roots, emitter)
        for (slot, typ), new_arg in zip(param_slots, ret.tco_args):
            emitter.emit(f"  store {typ.text} {new_arg.ir}, ptr {slot}")
        emitter.emit(f"  call void @llvm.stackrestore(ptr {sp_save})")
        emitter.emit(f"  br label %{loop_label}")
    elif emitter.is_block_terminated():
        # All branches ended with TCO back-edges; roots already balanced.
        pass
    else:
        if ret.typ != sig.ret:
            raise CodegenError(
                f"Function {fn.name} body type mismatch in TCO backend: {ret.typ.text} vs {sig.ret.text}"
            )
        _emit_pop_temp_roots(n_roots, emitter)
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
    def emit_root_registration(slot_ir: str, typ: LLType) -> None:
        if typ == I64:
            reg = emitter.tmp()
            emitter.emit(f"  {reg} = call i64 @sprout_gc_register_i64_root(ptr {slot_ir})")
            return
        if typ == I8_PTR:
            reg = emitter.tmp()
            emitter.emit(f"  {reg} = call i64 @sprout_gc_register_ptr_root(ptr {slot_ir})")
            return
        if _tuple_item_types_from_lltype(typ) is not None:
            size = _sizeof_struct(typ.text, emitter)
            reg = emitter.tmp()
            emitter.emit(f"  {reg} = call i64 @sprout_gc_register_scan_root(ptr {slot_ir}, i64 {size})")

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
        emit_root_registration(f"@{let_decl.name}", info.typ)
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
    tco_ctx: TcoCtx | None = None,
) -> Value:
    if isinstance(expr, ast.IntExpr):
        return Value(I64, str(expr.value))
    if isinstance(expr, ast.BoolExpr):
        return Value(I1, "1" if expr.value else "0")
    if isinstance(expr, ast.StringExpr):
        gname, length = emitter.string_const(expr.value)
        tmp = emitter.tmp()
        emitter.emit(f"  {tmp} = getelementptr inbounds [{length} x i8], ptr {gname}, i64 0, i64 0")
        i64_tmp = emitter.tmp()
        emitter.emit(f"  {i64_tmp} = ptrtoint ptr {tmp} to i64")
        return Value(I64, i64_tmp)
    if isinstance(expr, ast.CharExpr):
        gname, length = emitter.string_const(expr.value)
        tmp = emitter.tmp()
        emitter.emit(f"  {tmp} = getelementptr inbounds [{length} x i8], ptr {gname}, i64 0, i64 0")
        i64_tmp = emitter.tmp()
        emitter.emit(f"  {i64_tmp} = ptrtoint ptr {tmp} to i64")
        return Value(I64, i64_tmp)
    if isinstance(expr, ast.UnitExpr):
        return Value(I64, "0")
    if isinstance(expr, ast.TupleExpr):
        # Root each item after evaluation before computing the next — any item
        # may allocate (e.g. a constructor argument) which can trigger GC and
        # would collect unrooted earlier items.
        items: list[Value] = []
        total_rooted = 0
        for item_expr in expr.items:
            item = _emit_expr(item_expr, locals_, globals_info, sigs, ctor_sigs, adt_names, emitter)
            total_rooted += _emit_push_temp_root(item, emitter)
            items.append(item)
        tuple_items = [item.typ for item in items]
        tuple_item_is_string = [
            _string_meta_from_type_expr(getattr(item_expr, "inferred_type", None), adt_names)
            for item_expr in expr.items
        ]
        tuple_typ = _tuple_lltype(tuple_items)
        current = "undef"
        for idx, item in enumerate(items):
            next_val = emitter.tmp()
            emitter.emit(
                f"  {next_val} = insertvalue {tuple_typ.text} {current}, {item.typ.text} {item.ir}, {idx}"
            )
            current = next_val
        _emit_pop_temp_roots(total_rooted, emitter)
        return Value(tuple_typ, current, tuple_items=tuple_items, tuple_item_is_string=tuple_item_is_string)
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
            if ctor.name.rsplit(".", 1)[-1] == "Nothing":
                emitter.emit(f"  {tmp} = call i64 @sprout_nothing(i64 {ctor.tag})")
            else:
                emitter.emit(f"  {tmp} = call i64 @sprout_make0(i64 {ctor.tag})")
            return Value(I64, tmp)
        raise CodegenError(f"Unknown variable in backend: {expr.name}")
    if isinstance(expr, ast.LambdaExpr):
        info = getattr(expr, "_lambda_info", None)
        if info is None:
            free_vars: list[str] = []
            _collect_free_vars(
                expr.body,
                {param.name for param in expr.params},
                free_vars,
                set(),
            )
            captures = [name for name in free_vars if name in locals_]
            info = LambdaInfo(
                expr=expr,
                name=f"__sprout_lambda_{emitter.next_lambda}",
                captures=captures,
                call_sig=_call_sig_from_lambda_expr(expr, sigs, globals_info, adt_names),
            )
            emitter.next_lambda += 1
            setattr(expr, "_lambda_info", info)
            _emit_lambda_helper(info, globals_info, sigs, ctor_sigs, adt_names, emitter)
            emitter.lifted_defs.append("")
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
    if isinstance(expr, ast.IntRangeExpr):
        start = _emit_expr(expr.start, locals_, globals_info, sigs, ctor_sigs, adt_names, emitter)
        end = _emit_expr(expr.end, locals_, globals_info, sigs, ctor_sigs, adt_names, emitter)
        if start.typ != I64 or end.typ != I64:
            raise CodegenError("'..' backend supports Int only")
        tmp = emitter.tmp()
        emitter.emit(f"  {tmp} = call i64 @int_range(i64 {start.ir}, i64 {end.ir})")
        return Value(I64, tmp)
    if isinstance(expr, ast.BinaryExpr):
        return _emit_binary(expr, locals_, globals_info, sigs, ctor_sigs, adt_names, emitter)
    if isinstance(expr, ast.IfExpr):
        return _emit_if(expr, locals_, globals_info, sigs, ctor_sigs, adt_names, emitter, tco_ctx=tco_ctx)
    if isinstance(expr, ast.CallExpr):
        # IILE: beta-reduce immediately-invoked lambda so TCO propagates through where-bindings.
        # CallExpr(LambdaExpr([params...], body), [args...]) compiles exactly like match arm binds:
        # evaluate each arg, bind the corresponding param name inline, emit the body with tco_ctx
        # adjusted to account for the new roots.  This mirrors _emit_match's branch handling.
        if (
            isinstance(expr.callee, ast.LambdaExpr)
            and len(expr.callee.params) == len(expr.args)
            and len(expr.args) >= 1
        ):
            iile_locals = dict(locals_)
            rooted = 0
            for param, arg_expr in zip(expr.callee.params, expr.args):
                arg_val = _emit_expr(arg_expr, iile_locals, globals_info, sigs, ctor_sigs, adt_names, emitter)
                rooted += _emit_pattern_bind(ast.VarPattern(param.name), arg_val, iile_locals, ctor_sigs, emitter)
            iile_tco_ctx = (
                TcoCtx(
                    fn_name=tco_ctx.fn_name,
                    loop_label=tco_ctx.loop_label,
                    param_slots=tco_ctx.param_slots,
                    ret=tco_ctx.ret,
                    outer_roots=tco_ctx.outer_roots + rooted,
                    sp_save=tco_ctx.sp_save,
                )
                if tco_ctx is not None
                else None
            )
            result = _emit_expr(expr.callee.body, iile_locals, globals_info, sigs, ctor_sigs, adt_names, emitter, tco_ctx=iile_tco_ctx)
            if tco_ctx is not None and result.tco_args is not None:
                _emit_tco_back_edge(tco_ctx, result.tco_args, rooted, emitter)
                return Value(tco_ctx.ret, "undef")
            if not emitter.is_block_terminated():
                _emit_pop_temp_roots(rooted, emitter)
            return result
        return _emit_call(expr, locals_, globals_info, sigs, ctor_sigs, adt_names, emitter, tco_ctx=tco_ctx)
    if isinstance(expr, ast.MatchExpr):
        return _emit_match(expr, locals_, globals_info, sigs, ctor_sigs, adt_names, emitter, tco_ctx=tco_ctx)

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

    if expr.op == "++":
        left = _emit_expr(expr.left, locals_, globals_info, sigs, ctor_sigs, adt_names, emitter)
        rooted = _emit_push_temp_root(left, emitter)
        right = _emit_expr(expr.right, locals_, globals_info, sigs, ctor_sigs, adt_names, emitter)
        _emit_pop_temp_roots(rooted, emitter)
        li = _coerce_value(left, I64, emitter)
        ri = _coerce_value(right, I64, emitter)
        tmp = emitter.tmp()
        emitter.emit(f"  {tmp} = call i64 @str_concat(i64 {li.ir}, i64 {ri.ir})")
        return Value(I64, tmp)

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
        left_inferred = getattr(expr.left, "inferred_type", None)
        right_inferred = getattr(expr.right, "inferred_type", None)
        if (
            left.typ == I64
            and isinstance(left_inferred, ast.TypeName)
            and left_inferred.name == "IntRange"
            and isinstance(right_inferred, ast.TypeName)
            and right_inferred.name == "IntRange"
        ):
            if expr.op not in {"==", "!="}:
                raise CodegenError("IntRange comparison only supports == and !=")
            left_start = emitter.tmp()
            left_end = emitter.tmp()
            right_start = emitter.tmp()
            right_end = emitter.tmp()
            same_start = emitter.tmp()
            same_end = emitter.tmp()
            out = emitter.tmp()
            emitter.emit(f"  {left_start} = call i64 @int_range_start(i64 {left.ir})")
            emitter.emit(f"  {left_end} = call i64 @int_range_end(i64 {left.ir})")
            emitter.emit(f"  {right_start} = call i64 @int_range_start(i64 {right.ir})")
            emitter.emit(f"  {right_end} = call i64 @int_range_end(i64 {right.ir})")
            emitter.emit(f"  {same_start} = icmp eq i64 {left_start}, {right_start}")
            emitter.emit(f"  {same_end} = icmp eq i64 {left_end}, {right_end}")
            emitter.emit(f"  {out} = and i1 {same_start}, {same_end}")
            if expr.op == "==":
                return Value(I1, out)
            not_tmp = emitter.tmp()
            emitter.emit(f"  {not_tmp} = xor i1 {out}, true")
            return Value(I1, not_tmp)
        # Check if operands are String/Char values (I8_PTR or I64 with String/Char inferred type)
        # This check must come before the generic type-mismatch guard to allow mixed-rep comparisons.
        _left_inferred_str = isinstance(left_inferred, ast.TypeName) and left_inferred.name in {"String", "Char"}
        _is_string_cmp = left.typ == I8_PTR or (left.typ == I64 and _left_inferred_str)
        if _is_string_cmp and expr.op in {"==", "!="}:
            left_ptr = _coerce_value(left, I8_PTR, emitter)
            right_ptr = _coerce_value(right, I8_PTR, emitter)
            tmp = emitter.tmp()
            emitter.emit(f"  {tmp} = call i1 @str_eq(ptr {left_ptr.ir}, ptr {right_ptr.ir})")
            if expr.op == "==":
                return Value(I1, tmp)
            not_tmp = emitter.tmp()
            emitter.emit(f"  {not_tmp} = xor i1 {tmp}, true")
            return Value(I1, not_tmp)
        if _is_string_cmp:
            # Ordering comparison on String/Char via str_compare (returns i64 <0/0/>0)
            left_ptr = _coerce_value(left, I8_PTR, emitter)
            right_ptr = _coerce_value(right, I8_PTR, emitter)
            cmp_tmp = emitter.tmp()
            emitter.emit(f"  {cmp_tmp} = call i64 @str_compare(ptr {left_ptr.ir}, ptr {right_ptr.ir})")
            pred = {"<": "slt", "<=": "sle", ">": "sgt", ">=": "sge"}[expr.op]
            tmp = emitter.tmp()
            emitter.emit(f"  {tmp} = icmp {pred} i64 {cmp_tmp}, 0")
            return Value(I1, tmp)
        if left.typ != right.typ:
            raise CodegenError("Comparison operands must have same type")
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


def _emit_tco_back_edge(tco_ctx: TcoCtx, new_args: list[Value], extra_roots: int, emitter: Emitter) -> None:
    """Pop `extra_roots` + outer roots, store new args into param slots, branch to loop."""
    _emit_pop_temp_roots(extra_roots + tco_ctx.outer_roots, emitter)
    for (slot, typ), new_arg in zip(tco_ctx.param_slots, new_args):
        coerced = _coerce_value(new_arg, typ, emitter)
        emitter.emit(f"  store {typ.text} {coerced.ir}, ptr {slot}")
    # Restore the stack pointer to free any alloca-in-loop dynamic stack growth.
    # Without this, each loop iteration permanently grows the stack by the number
    # of bytes consumed by alloca instructions in the loop body.
    if tco_ctx.sp_save:
        emitter.emit(f"  call void @llvm.stackrestore(ptr {tco_ctx.sp_save})")
    emitter.emit(f"  br label %{tco_ctx.loop_label}")


def _emit_if(
    expr: ast.IfExpr,
    locals_: dict[str, Value],
    globals_info: dict[str, GlobalInfo],
    sigs: dict[str, FnSig],
    ctor_sigs: dict[str, CtorSig],
    adt_names: set[str],
    emitter: Emitter,
    tco_ctx: TcoCtx | None = None,
) -> Value:
    cond = _emit_expr(expr.condition, locals_, globals_info, sigs, ctor_sigs, adt_names, emitter)
    if cond.typ != I1:
        raise CodegenError("if condition must be Bool")

    then_label = emitter.block("if_then")
    else_label = emitter.block("if_else")
    done_label = emitter.block("if_done")
    emitter.emit(f"  br i1 {cond.ir}, label %{then_label}, label %{else_label}")

    emitter.label(then_label)
    then_val = _emit_expr(expr.then_branch, locals_, globals_info, sigs, ctor_sigs, adt_names, emitter, tco_ctx=tco_ctx)
    then_end = emitter.current_block
    if tco_ctx is not None and then_val.tco_args is not None:
        _emit_tco_back_edge(tco_ctx, then_val.tco_args, 0, emitter)
    then_reaches_done = not emitter.is_block_terminated()
    if then_reaches_done:
        if then_end is None:
            raise CodegenError("Internal backend error: missing then block")
        emitter.emit(f"  br label %{done_label}")

    emitter.label(else_label)
    else_val = _emit_expr(expr.else_branch, locals_, globals_info, sigs, ctor_sigs, adt_names, emitter, tco_ctx=tco_ctx)
    else_end = emitter.current_block
    if tco_ctx is not None and else_val.tco_args is not None:
        _emit_tco_back_edge(tco_ctx, else_val.tco_args, 0, emitter)
    else_reaches_done = not emitter.is_block_terminated()
    if else_reaches_done:
        if else_end is None:
            raise CodegenError("Internal backend error: missing else block")
        emitter.emit(f"  br label %{done_label}")

    # All branches terminated via TCO or deeply-nested all-TCO sub-expressions
    if not then_reaches_done and not else_reaches_done:
        return Value(tco_ctx.ret if tco_ctx is not None else I64, "undef")

    # Collect branches that reach done_label
    reaching: list[tuple[Value, str]] = []
    if then_reaches_done:
        assert then_end is not None
        reaching.append((then_val, then_end))
    if else_reaches_done:
        assert else_end is not None
        reaching.append((else_val, else_end))

    out_type = reaching[0][0].typ
    for v, _ in reaching[1:]:
        if v.typ != out_type:
            raise CodegenError("if branches must have same type")

    emitter.label(done_label)
    phi = emitter.tmp()
    parts = ", ".join(f"[ {v.ir}, %{blk} ]" for v, blk in reaching)
    emitter.emit(f"  {phi} = phi {out_type.text} {parts}")
    if len(reaching) == 2:
        v0, v1 = reaching[0][0], reaching[1][0]
        callable_sig = v0.callable_sig if v0.callable_sig == v1.callable_sig else None
        tuple_items = v0.tuple_items if v0.tuple_items == v1.tuple_items else None
    else:
        callable_sig = reaching[0][0].callable_sig
        tuple_items = reaching[0][0].tuple_items
    return Value(out_type, phi, callable_sig=callable_sig, tuple_items=tuple_items)


def _emit_match(
    expr: ast.MatchExpr,
    locals_: dict[str, Value],
    globals_info: dict[str, GlobalInfo],
    sigs: dict[str, FnSig],
    ctor_sigs: dict[str, CtorSig],
    adt_names: set[str],
    emitter: Emitter,
    tco_ctx: TcoCtx | None = None,
) -> Value:
    # Skip the direct-ctor optimisation when TCO is active to avoid having to
    # propagate tco_ctx through the entire direct-ctor match helper tree.
    if tco_ctx is None:
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
        rooted = _emit_pattern_bind(branch.pattern, scrut, branch_locals, ctor_sigs, emitter)

        # Build a tco_ctx with accumulated outer roots for the branch body.
        branch_tco_ctx = (
            TcoCtx(
                fn_name=tco_ctx.fn_name,
                loop_label=tco_ctx.loop_label,
                param_slots=tco_ctx.param_slots,
                ret=tco_ctx.ret,
                outer_roots=tco_ctx.outer_roots + rooted,
                sp_save=tco_ctx.sp_save,
            )
            if tco_ctx is not None
            else None
        )
        value = _emit_expr(branch.value, branch_locals, globals_info, sigs, ctor_sigs, adt_names, emitter, tco_ctx=branch_tco_ctx)
        end_block = emitter.current_block

        if tco_ctx is not None and value.tco_args is not None:
            # Direct self-tail-call: pop this branch's roots + all outer roots, loop back.
            _emit_tco_back_edge(tco_ctx, value.tco_args, rooted, emitter)
            current_fail = fail_label
            continue
        elif emitter.is_block_terminated():
            # All paths through the branch body were TCO back-edges (nested match/if).
            # Outer roots were already popped inside those back-edges.
            current_fail = fail_label
            continue

        if end_block is None:
            raise CodegenError("Internal backend error: missing match branch block")
        _emit_pop_temp_roots(rooted, emitter)
        branch_vals.append((value, end_block))
        emitter.emit(f"  br label %{done_label}")

        current_fail = fail_label

    emitter.label(current_fail)
    emitter.emit("  unreachable")

    if not branch_vals:
        # All branches were TCO back-edges; done_label is never reached.
        if tco_ctx is None:
            raise CodegenError("Match expression has no branches")
        return Value(tco_ctx.ret, "undef")

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
    return _is_direct_ctor_scrutinee(scrutinee, ctor_sigs)


def _is_direct_ctor_scrutinee(expr: ast.Expr, ctor_sigs: dict[str, CtorSig]) -> bool:
    if _direct_ctor_expr(expr, ctor_sigs) is not None:
        return True
    if isinstance(expr, ast.MatchExpr):
        return all(_is_direct_ctor_scrutinee(branch.value, ctor_sigs) for branch in expr.branches)
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

    if isinstance(scrutinee, ast.MatchExpr):
        _emit_direct_ctor_match_from_match_expr(
            scrutinee,
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


def _emit_direct_ctor_match_from_match_expr(
    scrutinee: ast.MatchExpr,
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
    inner_scrut = _emit_expr(scrutinee.scrutinee, locals_, globals_info, sigs, ctor_sigs, adt_names, emitter)
    next_label = emitter.block("match_ctor_inner_next")
    current_fail = next_label
    first = True
    for branch in scrutinee.branches:
        branch_label = emitter.block("match_ctor_inner_branch")
        fail_label = emitter.block("match_ctor_inner_next")

        if first:
            first = False
        else:
            emitter.label(current_fail)

        if isinstance(branch.pattern, (ast.WildcardPattern, ast.VarPattern)):
            emitter.emit(f"  br label %{branch_label}")
        else:
            cond = _emit_pattern_test(branch.pattern, inner_scrut, ctor_sigs, emitter)
            emitter.emit(f"  br i1 {cond.ir}, label %{branch_label}, label %{fail_label}")

        emitter.label(branch_label)
        branch_locals = dict(locals_)
        rooted = _emit_pattern_bind(branch.pattern, inner_scrut, branch_locals, ctor_sigs, emitter)
        _emit_direct_ctor_match_scrutinee(
            branch.value,
            branches,
            branch_locals,
            globals_info,
            sigs,
            ctor_sigs,
            adt_names,
            emitter,
            done_label,
            branch_vals,
        )
        _emit_pop_temp_roots(rooted, emitter)
        current_fail = fail_label

    emitter.label(current_fail)
    emitter.emit("  unreachable")


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
        rooted = _emit_direct_ctor_pattern_bind(branch.pattern, ctor, payloads, branch_locals, ctor_sigs, emitter)
        value = _emit_expr(branch.value, branch_locals, globals_info, sigs, ctor_sigs, adt_names, emitter)
        end_block = emitter.current_block
        if end_block is None:
            raise CodegenError("Internal backend error: missing direct constructor branch block")
        _emit_pop_temp_roots(rooted, emitter)
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
        return Value(I1, "1")
    if isinstance(pattern, (ast.IntPattern, ast.BoolPattern, ast.StringPattern, ast.CharPattern)):
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
) -> int:
    if isinstance(pattern, (ast.WildcardPattern, ast.IntPattern, ast.BoolPattern, ast.StringPattern, ast.CharPattern)):
        return 0
    if isinstance(pattern, ast.VarPattern):
        materialized = _emit_ctor_value(ctor, payloads, emitter)
        locals_[pattern.name] = materialized
        return _emit_push_temp_root(materialized, emitter)
    if isinstance(pattern, ast.ConstructorPattern):
        if pattern.name != ctor.name:
            return 0
        rooted = 0
        for sub, value in zip(pattern.args, payloads):
            rooted += _emit_pattern_bind(sub, value, locals_, ctor_sigs, emitter)
        return rooted
    raise CodegenError("Unsupported pattern form in direct constructor bind")


def _emit_ctor_value(ctor: CtorSig, payloads: list[Value], emitter: Emitter) -> Value:
    if len(payloads) != len(ctor.arg_types):
        raise CodegenError(
            f"Constructor {ctor.name} expects {len(ctor.arg_types)} args, got {len(payloads)} in backend materialization"
        )
    tmp = emitter.tmp()
    if len(payloads) == 0:
        if ctor.name.rsplit(".", 1)[-1] == "Nothing":
            emitter.emit(f"  {tmp} = call i64 @sprout_nothing(i64 {ctor.tag})")
        else:
            emitter.emit(f"  {tmp} = call i64 @sprout_make0(i64 {ctor.tag})")
        return Value(I64, tmp)

    rooted_payloads = 0
    packed_payloads: list[str] = []
    for payload in payloads:
        rooted_payloads += _emit_push_temp_root(payload, emitter)
        packed_payloads.append(_pack_to_i64(payload, emitter))
    _emit_pop_temp_roots(rooted_payloads, emitter)

    rooted_packed = 0
    for packed in packed_payloads:
        rooted_packed += _emit_push_temp_root(Value(I64, packed), emitter)
    n = len(packed_payloads)
    args_ir = ", ".join(f"i64 {p}" for p in packed_payloads)
    emitter.emit(f"  {tmp} = call i64 @sprout_make{n}(i64 {ctor.tag}, {args_ir})")
    _emit_pop_temp_roots(rooted_packed, emitter)
    return Value(I64, tmp)


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
    tuple_item_is_string = branch_vals[0][0].tuple_item_is_string
    if any(val.tuple_item_is_string != tuple_item_is_string for val, _ in branch_vals[1:]):
        tuple_item_is_string = None
    return Value(out_type, phi, callable_sig=callable_sig, tuple_items=tuple_items, tuple_item_is_string=tuple_item_is_string)


def _emit_tuple_field(value: Value, idx: int, emitter: Emitter) -> Value:
    tuple_items = value.tuple_items or _tuple_item_types_from_lltype(value.typ)
    if tuple_items is None:
        raise CodegenError("Tuple operation expects tuple metadata in backend")
    item_typ = tuple_items[idx]
    out = emitter.tmp()
    emitter.emit(f"  {out} = extractvalue {value.typ.text} {value.ir}, {idx}")
    field_meta = None
    if value.tuple_item_is_string is not None and idx < len(value.tuple_item_is_string):
        nested = value.tuple_item_is_string[idx]
        if isinstance(nested, list):
            field_meta = nested
    return Value(item_typ, out, tuple_items=_tuple_item_types_from_lltype(item_typ), tuple_item_is_string=field_meta)


def _emit_packed_tuple_field(packed_ir: str, idx: int, emitter: Emitter) -> Value:
    """Load field `idx` from an I64-packed tuple blob pointer.

    When a generic-typed constructor field (type variable → I64) holds a tuple,
    the I64 is a ptrtoint of an array of i64 slots.  Use GEP + load to extract.
    """
    ptr = emitter.tmp()
    emitter.emit(f"  {ptr} = inttoptr i64 {packed_ir} to ptr")
    gep = emitter.tmp()
    emitter.emit(f"  {gep} = getelementptr i64, ptr {ptr}, i64 {idx}")
    out = emitter.tmp()
    emitter.emit(f"  {out} = load i64, ptr {gep}")
    return Value(I64, out)


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
        value_ptr = _coerce_value(value, I8_PTR, emitter)
        literal_ptr, _ = emitter.string_const(pattern.value)
        tmp = emitter.tmp()
        emitter.emit(f"  {tmp} = call i1 @str_eq(ptr {value_ptr.ir}, ptr {literal_ptr})")
        return Value(I1, tmp)
    if isinstance(pattern, ast.CharPattern):
        value_ptr = _coerce_value(value, I8_PTR, emitter)
        literal_ptr, _ = emitter.string_const(pattern.value)
        tmp = emitter.tmp()
        emitter.emit(f"  {tmp} = call i1 @str_eq(ptr {value_ptr.ir}, ptr {literal_ptr})")
        return Value(I1, tmp)
    if isinstance(pattern, ast.UnitPattern):
        if value.typ != I64:
            raise CodegenError("Unit pattern expects Unit scrutinee")
        tmp = emitter.tmp()
        emitter.emit(f"  {tmp} = icmp eq i64 {value.ir}, 0")
        return Value(I1, tmp)
    if isinstance(pattern, ast.TuplePattern):
        tuple_items = value.tuple_items or _tuple_item_types_from_lltype(value.typ)
        if tuple_items is None and value.typ == I64:
            # Generic-typed constructor field: I64 is a packed blob pointer.
            # All sub-fields are treated as I64 (type variables carry no type info).
            acc = Value(I1, "1")
            for idx, item_pattern in enumerate(pattern.items):
                field = _emit_packed_tuple_field(value.ir, idx, emitter)
                test = _emit_pattern_test(item_pattern, field, ctor_sigs, emitter)
                and_tmp = emitter.tmp()
                emitter.emit(f"  {and_tmp} = and i1 {acc.ir}, {test.ir}")
                acc = Value(I1, and_tmp)
            return acc
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
        if not pattern.args:
            return Value(I1, eq_tag)
        return _emit_ctor_pattern_test(eq_tag, value.ir, ctor, pattern.args, ctor_sigs, emitter)

    raise CodegenError("Unsupported pattern form in backend")


def _emit_ctor_pattern_test(
    eq_tag_ir: str,
    handle_ir: str,
    ctor: CtorSig,
    arg_patterns: list[ast.Pattern],
    ctor_sigs: dict[str, CtorSig],
    emitter: Emitter,
) -> Value:
    check_label = emitter.block("match_ctor_check")
    mismatch_label = emitter.block("match_ctor_mismatch")
    done_label = emitter.block("match_ctor_done")
    emitter.emit(f"  br i1 {eq_tag_ir}, label %{check_label}, label %{mismatch_label}")

    emitter.label(check_label)
    for idx, (arg_pat, arg_typ) in enumerate(zip(arg_patterns, ctor.arg_types)):
        field = _emit_ctor_field(handle_ir, idx, arg_typ, emitter)
        test = _emit_pattern_test(arg_pat, field, ctor_sigs, emitter)
        next_label = emitter.block("match_ctor_next")
        emitter.emit(f"  br i1 {test.ir}, label %{next_label}, label %{mismatch_label}")
        emitter.label(next_label)

    matched_block = emitter.current_block
    if matched_block is None:
        raise CodegenError("Internal backend error: constructor pattern match lost current block")
    emitter.emit(f"  br label %{done_label}")

    emitter.label(mismatch_label)
    emitter.emit(f"  br label %{done_label}")

    emitter.label(done_label)
    out = emitter.tmp()
    emitter.emit(f"  {out} = phi i1 [ 1, %{matched_block} ], [ 0, %{mismatch_label} ]")
    return Value(I1, out)


def _emit_pattern_bind(
    pattern: ast.Pattern,
    value: Value,
    locals_: dict[str, Value],
    ctor_sigs: dict[str, CtorSig],
    emitter: Emitter,
) -> int:
    if isinstance(pattern, ast.WildcardPattern):
        return 0
    if isinstance(pattern, ast.VarPattern):
        locals_[pattern.name] = value
        return _emit_push_temp_root(value, emitter)
    if isinstance(pattern, (ast.IntPattern, ast.BoolPattern, ast.StringPattern, ast.CharPattern, ast.UnitPattern)):
        return 0
    if isinstance(pattern, ast.TuplePattern):
        tuple_items = value.tuple_items or _tuple_item_types_from_lltype(value.typ)
        if tuple_items is None and value.typ == I64:
            # Generic-typed constructor field: I64 is a packed blob pointer.
            rooted = 0
            for idx, sub in enumerate(pattern.items):
                field = _emit_packed_tuple_field(value.ir, idx, emitter)
                rooted += _emit_pattern_bind(sub, field, locals_, ctor_sigs, emitter)
            return rooted
        if tuple_items is None:
            raise CodegenError("Tuple pattern bind expects tuple scrutinee")
        rooted = 0
        for idx, sub in enumerate(pattern.items):
            rooted += _emit_pattern_bind(sub, _emit_tuple_field(value, idx, emitter), locals_, ctor_sigs, emitter)
        return rooted
    if isinstance(pattern, ast.ConstructorPattern):
        ctor = ctor_sigs.get(pattern.name)
        if ctor is None:
            raise CodegenError(f"Unknown constructor in backend bind: {pattern.name}")
        rooted = 0
        for idx, (sub, arg_typ) in enumerate(zip(pattern.args, ctor.arg_types)):
            field = _emit_ctor_field(value.ir, idx, arg_typ, emitter)
            rooted += _emit_pattern_bind(sub, field, locals_, ctor_sigs, emitter)
        return rooted
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
        rooted = _emit_push_temp_root(value, emitter)
        size = _sizeof_struct(value.typ.text, emitter)
        raw_i64 = emitter.tmp()
        emitter.emit(f"  {raw_i64} = call i64 @sprout_alloc_tuple_blob(i64 {size})")
        raw_ptr = emitter.tmp()
        emitter.emit(f"  {raw_ptr} = inttoptr i64 {raw_i64} to ptr")
        emitter.emit(f"  store {value.typ.text} {value.ir}, ptr {raw_ptr}")
        _emit_pop_temp_roots(rooted, emitter)
        return raw_i64
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
        item_meta = (
            value.tuple_item_is_string[idx]
            if value.tuple_item_is_string is not None and idx < len(value.tuple_item_is_string)
            else False
        )
        is_str = item_meta is True
        if is_str and field.typ == I64:
            ptr = emitter.tmp()
            emitter.emit(f"  {ptr} = inttoptr i64 {field.ir} to ptr")
            last = _emit_print_text(ptr, emitter)
        else:
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
    tco_ctx: TcoCtx | None = None,
) -> Value:
    if isinstance(expr.callee, ast.VarExpr) and expr.callee.name == "print":
        if len(expr.args) != 1:
            raise CodegenError("print expects 1 argument")
        arg = _emit_expr(expr.args[0], locals_, globals_info, sigs, ctor_sigs, adt_names, emitter)
        arg_inferred = getattr(expr.args[0], "inferred_type", None)
        _is_string_arg = arg.typ == I8_PTR or (
            arg.typ == I64
            and isinstance(arg_inferred, ast.TypeName)
            and arg_inferred.name in {"String", "Char"}
        )
        if _is_string_arg:
            coerced = _coerce_value(arg, I8_PTR, emitter)
            tmp = emitter.tmp()
            emitter.emit(f"  {tmp} = call i64 @print_str(ptr {coerced.ir})")
            return Value(I64, tmp)
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
            if fn_name.rsplit(".", 1)[-1] == "Nothing":
                emitter.emit(f"  {tmp} = call i64 @sprout_nothing(i64 {ctor.tag})")
            else:
                emitter.emit(f"  {tmp} = call i64 @sprout_make0(i64 {ctor.tag})")
            return Value(I64, tmp)
        rooted_args = 0
        coerced_args: list[Value] = []
        for arg_expr, typ in zip(expr.args, ctor.arg_types):
            arg_val = _emit_expr(arg_expr, locals_, globals_info, sigs, ctor_sigs, adt_names, emitter)
            coerced = _coerce_value(arg_val, typ, emitter)
            coerced_args.append(coerced)
            rooted_args += _emit_push_temp_root(coerced, emitter)
        packed_args: list[str] = []
        for coerced in coerced_args:
            packed_args.append(_pack_to_i64(coerced, emitter))
        _emit_pop_temp_roots(rooted_args, emitter)
        rooted_packed = 0
        for packed in packed_args:
            rooted_packed += _emit_push_temp_root(Value(I64, packed), emitter)
        tmp = emitter.tmp()
        n = len(packed_args)
        args_ir = ", ".join(f"i64 {p}" for p in packed_args)
        emitter.emit(f"  {tmp} = call i64 @sprout_make{n}(i64 {ctor.tag}, {args_ir})")
        _emit_pop_temp_roots(rooted_packed, emitter)
        return Value(I64, tmp)

    if fn_name is not None and fn_name in locals_:
        local_callee = _emit_expr(expr.callee, locals_, globals_info, sigs, ctor_sigs, adt_names, emitter)
        rooted_callee = _emit_push_temp_root(local_callee, emitter)
        call_sig = _value_call_sig(local_callee, getattr(expr.callee, "inferred_type", None), adt_names)
        if call_sig is None:
            raise CodegenError(f"Missing callable signature for {fn_name}")
        args, rooted_args = _emit_exprs_with_temp_roots(
            expr.args, locals_, globals_info, sigs, ctor_sigs, adt_names, emitter
        )
        if len(args) < len(call_sig.params):
            wrapper_name = f"__sprout_partial_{emitter.next_lambda}"
            emitter.next_lambda += 1
            emitter.lifted_defs.extend(_emit_partial_closure_wrapper(wrapper_name, call_sig, len(args)))
            emitter.lifted_defs.append("")
            closure = _emit_make_closure(f"@{wrapper_name}", [local_callee, *args], emitter)
            remaining_sig = CallSig(
                params=call_sig.params[len(args) :],
                ret=call_sig.ret,
                ret_callable_sig=call_sig.ret_callable_sig,
            )
            out = Value(closure.typ, closure.ir, callable_sig=remaining_sig)
        else:
            out = _emit_closure_call(local_callee, call_sig, args, emitter)
        _emit_pop_temp_roots(rooted_args, emitter)
        _emit_pop_temp_roots(rooted_callee, emitter)
    elif fn_name is not None and (fn_name in sigs or fn_name in EXTERN_SIGS):
        sig = sigs.get(fn_name) or EXTERN_SIGS.get(fn_name)
        assert sig is not None
        if len(expr.args) > len(sig.params):
            raise CodegenError(f"Function {fn_name} expects {len(sig.params)} args, got {len(expr.args)}")
        if tco_ctx is not None and fn_name == tco_ctx.fn_name and len(expr.args) == len(sig.params):
            # TCO self-call: evaluate args with selective temp roots.
            # After arg[i], push a temp root if the Sprout type could be a GC-managed
            # heap pointer AND there are subsequent args that might trigger collection.
            # Plain Int args (unboxed integers) are never heap pointers and are skipped
            # to avoid alloca-per-iteration stack growth on AArch64.
            raw_args: list[Value] = []
            tco_rooted = 0
            for i, tco_arg in enumerate(expr.args):
                val = _emit_expr(tco_arg, locals_, globals_info, sigs, ctor_sigs, adt_names, emitter, tco_ctx=None)
                raw_args.append(val)
                if i < len(expr.args) - 1:
                    arg_type = getattr(tco_arg, "inferred_type", None)
                    if not _sprout_type_is_plain_int(arg_type):
                        tco_rooted += _emit_push_temp_root(val, emitter)
            coerced_args = [_coerce_value(av, st, emitter) for av, (_, st) in zip(raw_args, tco_ctx.param_slots)]
            _emit_pop_temp_roots(tco_rooted, emitter)
            return Value(sig.ret, "undef", tco_args=coerced_args)
        args, rooted_args = _emit_exprs_with_temp_roots(
            expr.args, locals_, globals_info, sigs, ctor_sigs, adt_names, emitter
        )
        if len(args) < len(sig.params):
            wrapper_name = f"__sprout_partial_{emitter.next_lambda}"
            emitter.next_lambda += 1
            emitter.lifted_defs.extend(_emit_partial_direct_wrapper(wrapper_name, fn_name, sig, len(args)))
            emitter.lifted_defs.append("")
            closure = _emit_make_closure(f"@{wrapper_name}", args, emitter)
            remaining_sig = CallSig(
                params=sig.params[len(args) :],
                ret=sig.ret,
                ret_callable_sig=sig.ret_callable_sig,
            )
            out = Value(closure.typ, closure.ir, callable_sig=remaining_sig)
        else:
            args_ir: list[str] = []
            for arg_val, param_type in zip(args, sig.params):
                coerced = _coerce_value(arg_val, param_type, emitter)
                args_ir.append(f"{param_type.text} {coerced.ir}")
            tmp = emitter.tmp()
            emitter.emit(f"  {tmp} = call {sig.ret.text} @{fn_name}({', '.join(args_ir)})")
            out = Value(sig.ret, tmp, callable_sig=sig.ret_callable_sig)
        _emit_pop_temp_roots(rooted_args, emitter)
    else:
        callee = _emit_expr(expr.callee, locals_, globals_info, sigs, ctor_sigs, adt_names, emitter)
        rooted_callee = _emit_push_temp_root(callee, emitter)
        call_sig = _value_call_sig(callee, getattr(expr.callee, "inferred_type", None), adt_names)
        if call_sig is None:
            raise CodegenError("Backend expected function-typed callee")
        args, rooted_args = _emit_exprs_with_temp_roots(
            expr.args, locals_, globals_info, sigs, ctor_sigs, adt_names, emitter
        )
        if len(args) < len(call_sig.params):
            wrapper_name = f"__sprout_partial_{emitter.next_lambda}"
            emitter.next_lambda += 1
            emitter.lifted_defs.extend(_emit_partial_closure_wrapper(wrapper_name, call_sig, len(args)))
            emitter.lifted_defs.append("")
            closure = _emit_make_closure(f"@{wrapper_name}", [callee, *args], emitter)
            remaining_sig = CallSig(
                params=call_sig.params[len(args) :],
                ret=call_sig.ret,
                ret_callable_sig=call_sig.ret_callable_sig,
            )
            out = Value(closure.typ, closure.ir, callable_sig=remaining_sig)
        else:
            out = _emit_closure_call(callee, call_sig, args, emitter)
        _emit_pop_temp_roots(rooted_args, emitter)
        _emit_pop_temp_roots(rooted_callee, emitter)

    return _value_for_inferred_type(out, getattr(expr, "inferred_type", None), adt_names, emitter)
