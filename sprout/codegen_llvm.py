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
I8_PTR = LLType("ptr")


@dataclass
class FnSig:
    name: str
    params: list[LLType]
    ret: LLType


@dataclass
class CtorSig:
    name: str
    tag: int
    arg_types: list[LLType]


EXTERN_SIGS: dict[str, FnSig] = {
    "print_int": FnSig(name="print_int", params=[I64], ret=I64),
    "print_str": FnSig(name="print_str", params=[I8_PTR], ret=I64),
    "print_value": FnSig(name="print_value", params=[I64], ret=I64),
    "read_file": FnSig(name="read_file", params=[I8_PTR], ret=I8_PTR),
    "read_int_lines": FnSig(name="read_int_lines", params=[I8_PTR], ret=I64),
    "parse_int": FnSig(name="parse_int", params=[I8_PTR], ret=I64),
    "str_concat": FnSig(name="str_concat", params=[I8_PTR, I8_PTR], ret=I8_PTR),
    "str_len": FnSig(name="str_len", params=[I8_PTR], ret=I64),
    "str_slice": FnSig(name="str_slice", params=[I8_PTR, I64, I64], ret=I8_PTR),
    "str_find": FnSig(name="str_find", params=[I8_PTR, I8_PTR], ret=I64),
    "str_starts_with": FnSig(name="str_starts_with", params=[I8_PTR, I8_PTR], ret=I1),
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
    "tcp_listen": FnSig(name="tcp_listen", params=[I64], ret=I64),
    "tcp_accept": FnSig(name="tcp_accept", params=[I64], ret=I64),
    "tcp_read": FnSig(name="tcp_read", params=[I64], ret=I8_PTR),
    "tcp_write": FnSig(name="tcp_write", params=[I64, I8_PTR], ret=I64),
    "tcp_close": FnSig(name="tcp_close", params=[I64], ret=I64),
    "tcp_close_listener": FnSig(name="tcp_close_listener", params=[I64], ret=I64),
    "tcp_echo_serve": FnSig(name="tcp_echo_serve", params=[I64, I64], ret=I64),
    "sprout_register_ctor": FnSig(name="sprout_register_ctor", params=[I64, I8_PTR, I64], ret=I64),
    "sprout_make0": FnSig(name="sprout_make0", params=[I64], ret=I64),
    "sprout_make1": FnSig(name="sprout_make1", params=[I64, I64], ret=I64),
    "sprout_make2": FnSig(name="sprout_make2", params=[I64, I64, I64], ret=I64),
    "sprout_tag": FnSig(name="sprout_tag", params=[I64], ret=I64),
    "sprout_field": FnSig(name="sprout_field", params=[I64, I64], ret=I64),
}


@dataclass
class Value:
    typ: LLType
    ir: str
    callable_sig: "CallSig | None" = None


@dataclass
class GlobalConst:
    typ: LLType
    value_ir: str


@dataclass
class GlobalInfo:
    typ: LLType
    is_const: bool
    const_value_ir: str | None = None


@dataclass(frozen=True)
class CallSig:
    params: list[LLType]
    ret: LLType


class Emitter:
    def __init__(self) -> None:
        self.lines: list[str] = []
        self.next_tmp = 0
        self.next_block = 0
        self.current_block: str | None = None
        self.next_str = 0
        self.string_globals: list[str] = []

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
        if base_name in {"Vector", "Map"}:
            return I64
        if base_leaf and base_leaf[0].islower():
            return I64
    if isinstance(node, ast.TypeArrow):
        # Function-typed values are lowered as opaque callable pointers.
        return I8_PTR
    raise CodegenError(f"Unsupported type for LLVM backend: {node}")


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
    if isinstance(expr, ast.MatchExpr):
        if not expr.branches:
            raise CodegenError("Cannot infer top-level let type for empty match")
        return _infer_expr_type(expr.branches[0].value, globals_info, sigs, ctor_sigs)
    raise CodegenError("Cannot infer top-level let type for expression")


def compile_to_llvm(program: ast.Program) -> str:
    type_decls = [d for d in program.declarations if isinstance(d, ast.TypeDecl)]
    fn_decls = [d for d in program.declarations if isinstance(d, ast.FnDecl)]
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
            if len(arg_types) > 2:
                raise CodegenError(
                    f"Constructor {ctor.name} has {len(arg_types)} args; backend currently supports up to 2"
                )
            ctor_sigs[ctor.name] = CtorSig(name=ctor.name, tag=next_tag, arg_types=arg_types)
            next_tag += 1

    sigs: dict[str, FnSig] = {}
    for fn in fn_decls:
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
            globals_info[let_decl.name] = GlobalInfo(typ=inferred, is_const=False)
            runtime_lets.append(let_decl)

    emitter = Emitter()
    emitter.emit("; Generated by sprout LLVM backend (v0)")
    emitter.emit("target triple = \"unknown-unknown-unknown\"")
    emitter.emit("")
    for ext in EXTERN_SIGS.values():
        params = ", ".join(t.text for t in ext.params)
        emitter.emit(f"declare {ext.ret.text} @{ext.name}({params})")
    emitter.emit("")

    ctor_reg_meta: dict[str, tuple[tuple[str, int], tuple[str, int] | None, int, int]] = {}
    for ctor in ctor_sigs.values():
        primary = emitter.string_const(ctor.name)
        leaf_name = ctor.name.rsplit(".", 1)[-1]
        leaf = emitter.string_const(leaf_name) if leaf_name != ctor.name else None
        ctor_reg_meta[ctor.name] = (primary, leaf, len(ctor.arg_types), ctor.tag)

    if runtime_lets:
        _emit_init_globals(runtime_lets, globals_info, sigs, ctor_sigs, emitter)
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
            init = "null" if info.typ == I8_PTR else "0"
            module_lines.append(f"@{name} = global {info.typ.text} {init}")
    module_lines.extend(emitter.string_globals)
    if globals_info or emitter.string_globals:
        module_lines.append("")
    module_lines.extend(emitter.lines[3:])

    return "\n".join(module_lines).rstrip() + "\n"


def _eval_const_expr(expr: ast.Expr, globals_: dict[str, GlobalConst]) -> GlobalConst:
    if isinstance(expr, ast.IntExpr):
        return GlobalConst(I64, str(expr.value))
    if isinstance(expr, ast.BoolExpr):
        return GlobalConst(I1, "1" if expr.value else "0")
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
        locals_[p.name] = Value(typ=typ, ir=pname, callable_sig=call_sig)

    is_entry_main = fn.name == "main" or fn.name.endswith(".main")
    emitted_name = "main" if is_entry_main else fn.name
    emitter.emit(f"define {sig.ret.text} @{emitted_name}({', '.join(params)}) {{")
    emitter.label("entry")
    if is_entry_main:
        if runtime_lets:
            emitter.emit("  call void @__sprout_init_globals()")
        for _, (primary, leaf, arity, tag) in sorted(ctor_reg_meta.items(), key=lambda x: x[1][3]):
            sname, slen = primary
            sptr = emitter.tmp()
            emitter.emit(f"  {sptr} = getelementptr inbounds [{slen} x i8], ptr {sname}, i64 0, i64 0")
            reg = emitter.tmp()
            emitter.emit(
                f"  {reg} = call i64 @sprout_register_ctor(i64 {tag}, ptr {sptr}, i64 {arity})"
            )
            if leaf is not None:
                leaf_name, leaf_len = leaf
                leaf_ptr = emitter.tmp()
                emitter.emit(
                    f"  {leaf_ptr} = getelementptr inbounds [{leaf_len} x i8], ptr {leaf_name}, i64 0, i64 0"
                )
                leaf_reg = emitter.tmp()
                emitter.emit(
                    f"  {leaf_reg} = call i64 @sprout_register_ctor(i64 {tag}, ptr {leaf_ptr}, i64 {arity})"
                )
    ret = _emit_expr(fn.body, locals_, globals_info, sigs, ctor_sigs, emitter)
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
    emitter: Emitter,
) -> None:
    emitter.emit("define void @__sprout_init_globals() {")
    emitter.label("entry")
    locals_: dict[str, Value] = {}
    for let_decl in runtime_lets:
        info = globals_info[let_decl.name]
        value = _emit_expr(let_decl.value, locals_, globals_info, sigs, ctor_sigs, emitter)
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
    if isinstance(expr, ast.VarExpr):
        val = locals_.get(expr.name)
        if val is not None:
            return val
        fn_ref = sigs.get(expr.name)
        if fn_ref is not None:
            return Value(
                typ=I8_PTR,
                ir=f"@{expr.name}",
                callable_sig=CallSig(params=fn_ref.params, ret=fn_ref.ret),
            )
        global_info = globals_info.get(expr.name)
        if global_info is not None:
            tmp = emitter.tmp()
            emitter.emit(f"  {tmp} = load {global_info.typ.text}, ptr @{expr.name}")
            return Value(global_info.typ, tmp)
        ctor = ctor_sigs.get(expr.name)
        if ctor is not None:
            if ctor.arg_types:
                raise CodegenError(f"Constructor {ctor.name} requires arguments")
            tmp = emitter.tmp()
            emitter.emit(f"  {tmp} = call i64 @sprout_make0(i64 {ctor.tag})")
            return Value(I64, tmp)
        raise CodegenError(f"Unknown variable in backend: {expr.name}")
    if isinstance(expr, ast.UnaryExpr):
        operand = _emit_expr(expr.operand, locals_, globals_info, sigs, ctor_sigs, emitter)
        if expr.op == "-":
            if operand.typ != I64:
                raise CodegenError("Unary '-' backend supports Int only")
            tmp = emitter.tmp()
            emitter.emit(f"  {tmp} = sub i64 0, {operand.ir}")
            return Value(I64, tmp)
        raise CodegenError(f"Unsupported unary op in backend: {expr.op}")
    if isinstance(expr, ast.BinaryExpr):
        return _emit_binary(expr, locals_, globals_info, sigs, ctor_sigs, emitter)
    if isinstance(expr, ast.IfExpr):
        return _emit_if(expr, locals_, globals_info, sigs, ctor_sigs, emitter)
    if isinstance(expr, ast.CallExpr):
        return _emit_call(expr, locals_, globals_info, sigs, ctor_sigs, emitter)
    if isinstance(expr, ast.MatchExpr):
        return _emit_match(expr, locals_, globals_info, sigs, ctor_sigs, emitter)

    raise CodegenError(f"Unsupported expression in LLVM backend: {expr.__class__.__name__}")


def _emit_binary(
    expr: ast.BinaryExpr,
    locals_: dict[str, Value],
    globals_info: dict[str, GlobalInfo],
    sigs: dict[str, FnSig],
    ctor_sigs: dict[str, CtorSig],
    emitter: Emitter,
) -> Value:
    if expr.op in {"&&", "||"}:
        return _emit_short_circuit(expr, locals_, globals_info, sigs, ctor_sigs, emitter)

    left = _emit_expr(expr.left, locals_, globals_info, sigs, ctor_sigs, emitter)
    right = _emit_expr(expr.right, locals_, globals_info, sigs, ctor_sigs, emitter)

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
    emitter: Emitter,
) -> Value:
    left = _emit_expr(expr.left, locals_, globals_info, sigs, ctor_sigs, emitter)
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
    right = _emit_expr(expr.right, locals_, globals_info, sigs, ctor_sigs, emitter)
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
    emitter: Emitter,
) -> Value:
    cond = _emit_expr(expr.condition, locals_, globals_info, sigs, ctor_sigs, emitter)
    if cond.typ != I1:
        raise CodegenError("if condition must be Bool")

    then_label = emitter.block("if_then")
    else_label = emitter.block("if_else")
    done_label = emitter.block("if_done")
    emitter.emit(f"  br i1 {cond.ir}, label %{then_label}, label %{else_label}")

    emitter.label(then_label)
    then_val = _emit_expr(expr.then_branch, locals_, globals_info, sigs, ctor_sigs, emitter)
    then_end = emitter.current_block
    if then_end is None:
        raise CodegenError("Internal backend error: missing then block")
    emitter.emit(f"  br label %{done_label}")

    emitter.label(else_label)
    else_val = _emit_expr(expr.else_branch, locals_, globals_info, sigs, ctor_sigs, emitter)
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
    return Value(then_val.typ, phi)


def _emit_match(
    expr: ast.MatchExpr,
    locals_: dict[str, Value],
    globals_info: dict[str, GlobalInfo],
    sigs: dict[str, FnSig],
    ctor_sigs: dict[str, CtorSig],
    emitter: Emitter,
) -> Value:
    scrut = _emit_expr(expr.scrutinee, locals_, globals_info, sigs, ctor_sigs, emitter)
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
        value = _emit_expr(branch.value, branch_locals, globals_info, sigs, ctor_sigs, emitter)
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
    return Value(out_type, phi)


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
    raise CodegenError("Cannot pack value to i64")


def _emit_call(
    expr: ast.CallExpr,
    locals_: dict[str, Value],
    globals_info: dict[str, GlobalInfo],
    sigs: dict[str, FnSig],
    ctor_sigs: dict[str, CtorSig],
    emitter: Emitter,
) -> Value:
    if not isinstance(expr.callee, ast.VarExpr):
        raise CodegenError("Backend supports direct function calls only")
    fn_name = expr.callee.name
    if fn_name == "print":
        if len(expr.args) != 1:
            raise CodegenError("print expects 1 argument")
        arg = _emit_expr(expr.args[0], locals_, globals_info, sigs, ctor_sigs, emitter)
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
        raise CodegenError("print backend supports Int/Bool/String only")

    ctor = ctor_sigs.get(fn_name)
    if ctor is not None:
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
            arg_val = _emit_expr(arg_expr, locals_, globals_info, sigs, ctor_sigs, emitter)
            if arg_val.typ != typ:
                raise CodegenError(f"Constructor call type mismatch for {fn_name}")
            packed_args.append(_pack_to_i64(arg_val, emitter))
        tmp = emitter.tmp()
        if len(packed_args) == 1:
            emitter.emit(f"  {tmp} = call i64 @sprout_make1(i64 {ctor.tag}, i64 {packed_args[0]})")
        else:
            emitter.emit(
                f"  {tmp} = call i64 @sprout_make2(i64 {ctor.tag}, i64 {packed_args[0]}, i64 {packed_args[1]})"
            )
        return Value(I64, tmp)

    local_callee = locals_.get(fn_name)
    if local_callee is not None and local_callee.callable_sig is not None:
        call_sig = local_callee.callable_sig
        if len(expr.args) != len(call_sig.params):
            raise CodegenError(
                f"Callable {fn_name} expects {len(call_sig.params)} args, got {len(expr.args)}"
            )
        args_ir: list[str] = []
        for arg_expr, param_type in zip(expr.args, call_sig.params):
            arg_val = _emit_expr(arg_expr, locals_, globals_info, sigs, ctor_sigs, emitter)
            if arg_val.typ != param_type:
                raise CodegenError(f"Call type mismatch for callable {fn_name}")
            args_ir.append(f"{param_type.text} {arg_val.ir}")
        tmp = emitter.tmp()
        emitter.emit(f"  {tmp} = call {call_sig.ret.text} {local_callee.ir}({', '.join(args_ir)})")
        return Value(call_sig.ret, tmp)

    sig = sigs.get(fn_name)
    if sig is None:
        sig = EXTERN_SIGS.get(fn_name)
    if sig is None:
        raise CodegenError(f"Unknown function in backend call: {fn_name}")
    if len(expr.args) != len(sig.params):
        raise CodegenError(f"Function {fn_name} expects {len(sig.params)} args, got {len(expr.args)}")

    args_ir: list[str] = []
    for arg_expr, param_type in zip(expr.args, sig.params):
        arg_val = _emit_expr(arg_expr, locals_, globals_info, sigs, ctor_sigs, emitter)
        if arg_val.typ != param_type:
            raise CodegenError(f"Call type mismatch for {fn_name}")
        args_ir.append(f"{param_type.text} {arg_val.ir}")

    tmp = emitter.tmp()
    emitter.emit(f"  {tmp} = call {sig.ret.text} @{fn_name}({', '.join(args_ir)})")
    return Value(sig.ret, tmp)
