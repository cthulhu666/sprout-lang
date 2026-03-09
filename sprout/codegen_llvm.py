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


@dataclass
class GlobalConst:
    typ: LLType
    value_ir: str


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
    if node is None:
        raise CodegenError("Function return type annotation is required for native codegen")
    if isinstance(node, ast.TypeName):
        if node.name == "Int":
            return I64
        if node.name == "Bool":
            return I1
        if node.name == "String":
            return I8_PTR
        if node.name in adt_names:
            return I64
    if isinstance(node, ast.TypeApply):
        if isinstance(node.base, ast.TypeName) and node.base.name == "IO":
            if isinstance(node.arg, ast.TypeName) and node.arg.name == "Unit":
                return I64
        base_name = _type_base_name(node)
        if base_name in adt_names:
            return I64
    raise CodegenError(f"Unsupported type for LLVM backend: {node}")


def _type_base_name(node: ast.TypeExpr) -> str | None:
    if isinstance(node, ast.TypeName):
        return node.name
    if isinstance(node, ast.TypeApply):
        return _type_base_name(node.base)
    return None


def _check_param_type(node: ast.TypeExpr, adt_names: set[str]) -> LLType:
    return _type_from_ast(node, adt_names)


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
    ]
    if other:
        raise CodegenError("LLVM backend encountered unsupported top-level declaration")

    adt_names = {t.name for t in type_decls}

    ctor_sigs: dict[str, CtorSig] = {}
    for tdecl in type_decls:
        for tag, ctor in enumerate(tdecl.constructors):
            if ctor.name in ctor_sigs:
                raise CodegenError(f"Duplicate constructor name in backend: {ctor.name}")
            arg_types = [_type_from_ast(arg, adt_names) for arg in ctor.args]
            if len(arg_types) > 2:
                raise CodegenError(
                    f"Constructor {ctor.name} has {len(arg_types)} args; backend currently supports up to 2"
                )
            ctor_sigs[ctor.name] = CtorSig(name=ctor.name, tag=tag, arg_types=arg_types)

    sigs: dict[str, FnSig] = {}
    for fn in fn_decls:
        if fn.name in sigs:
            raise CodegenError(f"Duplicate function {fn.name}")
        params = [_check_param_type(p.type_expr, adt_names) for p in fn.params]
        ret = _type_from_ast(fn.return_type, adt_names)
        sigs[fn.name] = FnSig(fn.name, params, ret)

    globals_: dict[str, GlobalConst] = {}
    for let_decl in let_decls:
        if let_decl.name in globals_:
            raise CodegenError(f"Duplicate global let {let_decl.name}")
        globals_[let_decl.name] = _eval_const_expr(let_decl.value, globals_)

    emitter = Emitter()
    emitter.emit("; Generated by sprout LLVM backend (v0)")
    emitter.emit("target triple = \"unknown-unknown-unknown\"")
    emitter.emit("")
    for ext in EXTERN_SIGS.values():
        params = ", ".join(t.text for t in ext.params)
        emitter.emit(f"declare {ext.ret.text} @{ext.name}({params})")
    emitter.emit("")

    for fn in fn_decls:
        _emit_fn(fn, sigs, ctor_sigs, globals_, emitter)
        emitter.emit("")

    module_lines = [emitter.lines[0], emitter.lines[1], ""]
    for name, glob in globals_.items():
        module_lines.append(f"@{name} = private constant {glob.typ.text} {glob.value_ir}")
    module_lines.extend(emitter.string_globals)
    if globals_ or emitter.string_globals:
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
    globals_: dict[str, GlobalConst],
    emitter: Emitter,
) -> None:
    sig = sigs[fn.name]
    params = []
    locals_: dict[str, Value] = {}
    for p, typ in zip(fn.params, sig.params):
        pname = f"%{p.name}"
        params.append(f"{typ.text} {pname}")
        locals_[p.name] = Value(typ=typ, ir=pname)

    emitter.emit(f"define {sig.ret.text} @{fn.name}({', '.join(params)}) {{")
    emitter.label("entry")
    ret = _emit_expr(fn.body, locals_, globals_, sigs, ctor_sigs, emitter)
    if ret.typ != sig.ret:
        raise CodegenError(f"Function {fn.name} body type mismatch in backend: {ret.typ.text} vs {sig.ret.text}")
    emitter.emit(f"  ret {ret.typ.text} {ret.ir}")
    emitter.emit("}")


def _emit_expr(
    expr: ast.Expr,
    locals_: dict[str, Value],
    globals_: dict[str, GlobalConst],
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
        global_const = globals_.get(expr.name)
        if global_const is not None:
            tmp = emitter.tmp()
            emitter.emit(f"  {tmp} = load {global_const.typ.text}, ptr @{expr.name}")
            return Value(global_const.typ, tmp)
        ctor = ctor_sigs.get(expr.name)
        if ctor is not None:
            if ctor.arg_types:
                raise CodegenError(f"Constructor {ctor.name} requires arguments")
            tmp = emitter.tmp()
            emitter.emit(f"  {tmp} = call i64 @sprout_make0(i64 {ctor.tag})")
            return Value(I64, tmp)
        raise CodegenError(f"Unknown variable in backend: {expr.name}")
    if isinstance(expr, ast.UnaryExpr):
        operand = _emit_expr(expr.operand, locals_, globals_, sigs, ctor_sigs, emitter)
        if expr.op == "-":
            if operand.typ != I64:
                raise CodegenError("Unary '-' backend supports Int only")
            tmp = emitter.tmp()
            emitter.emit(f"  {tmp} = sub i64 0, {operand.ir}")
            return Value(I64, tmp)
        raise CodegenError(f"Unsupported unary op in backend: {expr.op}")
    if isinstance(expr, ast.BinaryExpr):
        return _emit_binary(expr, locals_, globals_, sigs, ctor_sigs, emitter)
    if isinstance(expr, ast.IfExpr):
        return _emit_if(expr, locals_, globals_, sigs, ctor_sigs, emitter)
    if isinstance(expr, ast.CallExpr):
        return _emit_call(expr, locals_, globals_, sigs, ctor_sigs, emitter)
    if isinstance(expr, ast.MatchExpr):
        return _emit_match(expr, locals_, globals_, sigs, ctor_sigs, emitter)

    raise CodegenError(f"Unsupported expression in LLVM backend: {expr.__class__.__name__}")


def _emit_binary(
    expr: ast.BinaryExpr,
    locals_: dict[str, Value],
    globals_: dict[str, GlobalConst],
    sigs: dict[str, FnSig],
    ctor_sigs: dict[str, CtorSig],
    emitter: Emitter,
) -> Value:
    if expr.op in {"&&", "||"}:
        return _emit_short_circuit(expr, locals_, globals_, sigs, ctor_sigs, emitter)

    left = _emit_expr(expr.left, locals_, globals_, sigs, ctor_sigs, emitter)
    right = _emit_expr(expr.right, locals_, globals_, sigs, ctor_sigs, emitter)

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
    globals_: dict[str, GlobalConst],
    sigs: dict[str, FnSig],
    ctor_sigs: dict[str, CtorSig],
    emitter: Emitter,
) -> Value:
    left = _emit_expr(expr.left, locals_, globals_, sigs, ctor_sigs, emitter)
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
    right = _emit_expr(expr.right, locals_, globals_, sigs, ctor_sigs, emitter)
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
    globals_: dict[str, GlobalConst],
    sigs: dict[str, FnSig],
    ctor_sigs: dict[str, CtorSig],
    emitter: Emitter,
) -> Value:
    cond = _emit_expr(expr.condition, locals_, globals_, sigs, ctor_sigs, emitter)
    if cond.typ != I1:
        raise CodegenError("if condition must be Bool")

    then_label = emitter.block("if_then")
    else_label = emitter.block("if_else")
    done_label = emitter.block("if_done")
    emitter.emit(f"  br i1 {cond.ir}, label %{then_label}, label %{else_label}")

    emitter.label(then_label)
    then_val = _emit_expr(expr.then_branch, locals_, globals_, sigs, ctor_sigs, emitter)
    then_end = emitter.current_block
    if then_end is None:
        raise CodegenError("Internal backend error: missing then block")
    emitter.emit(f"  br label %{done_label}")

    emitter.label(else_label)
    else_val = _emit_expr(expr.else_branch, locals_, globals_, sigs, ctor_sigs, emitter)
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
    globals_: dict[str, GlobalConst],
    sigs: dict[str, FnSig],
    ctor_sigs: dict[str, CtorSig],
    emitter: Emitter,
) -> Value:
    scrut = _emit_expr(expr.scrutinee, locals_, globals_, sigs, ctor_sigs, emitter)
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
        value = _emit_expr(branch.value, branch_locals, globals_, sigs, ctor_sigs, emitter)
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
    globals_: dict[str, GlobalConst],
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
        arg = _emit_expr(expr.args[0], locals_, globals_, sigs, ctor_sigs, emitter)
        if arg.typ == I64:
            tmp = emitter.tmp()
            emitter.emit(f"  {tmp} = call i64 @print_int(i64 {arg.ir})")
            return Value(I64, tmp)
        if arg.typ == I1:
            widened = emitter.tmp()
            emitter.emit(f"  {widened} = zext i1 {arg.ir} to i64")
            tmp = emitter.tmp()
            emitter.emit(f"  {tmp} = call i64 @print_int(i64 {widened})")
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
            arg_val = _emit_expr(arg_expr, locals_, globals_, sigs, ctor_sigs, emitter)
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

    sig = sigs.get(fn_name)
    if sig is None:
        sig = EXTERN_SIGS.get(fn_name)
    if sig is None:
        raise CodegenError(f"Unknown function in backend call: {fn_name}")
    if len(expr.args) != len(sig.params):
        raise CodegenError(f"Function {fn_name} expects {len(sig.params)} args, got {len(expr.args)}")

    args_ir: list[str] = []
    for arg_expr, param_type in zip(expr.args, sig.params):
        arg_val = _emit_expr(arg_expr, locals_, globals_, sigs, ctor_sigs, emitter)
        if arg_val.typ != param_type:
            raise CodegenError(f"Call type mismatch for {fn_name}")
        args_ir.append(f"{param_type.text} {arg_val.ir}")

    tmp = emitter.tmp()
    emitter.emit(f"  {tmp} = call {sig.ret.text} @{fn_name}({', '.join(args_ir)})")
    return Value(sig.ret, tmp)
