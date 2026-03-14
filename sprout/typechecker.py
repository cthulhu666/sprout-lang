from __future__ import annotations

"""Hindley-Milner style type inference/checking for Sprout v0.

This module implements:
- unification-based inference,
- let-polymorphism via schemes (forall),
- ADT constructor typing,
- basic match exhaustiveness for ADTs.
"""

from dataclasses import dataclass
from typing import Dict

from . import ast


class TypeCheckError(ValueError):
    pass


def tc_error(message: str, node: object | None = None) -> TypeCheckError:
    return TypeCheckError(f"{message}{ast.loc_str(node)}")


def unify_at(state: "InferState", left: "Type", right: "Type", node: object | None = None) -> None:
    try:
        unify(state, left, right)
    except TypeCheckError as exc:
        raise tc_error(str(exc), node) from exc


class Type:
    pass


@dataclass(frozen=True)
class TVar(Type):
    name: str


@dataclass(frozen=True)
class TConst(Type):
    name: str


@dataclass(frozen=True)
class TFunc(Type):
    arg: Type
    ret: Type


@dataclass(frozen=True)
class TApp(Type):
    base: Type
    arg: Type


@dataclass(frozen=True)
class Scheme:
    vars: tuple[str, ...]
    type: Type


@dataclass
class TypeDeclInfo:
    params: list[str]
    constructors: dict[str, Type]


@dataclass
class ClassDeclInfo:
    arity: int
    type_param_vars: tuple[str, ...]
    methods: dict[str, Type]


@dataclass(frozen=True)
class GlobalMethodInfo:
    class_name: str
    class_info: ClassDeclInfo
    method_type: Type


INT = TConst("Int")
BOOL = TConst("Bool")
STRING = TConst("String")
UNIT = TConst("Unit")


class InferState:
    def __init__(self) -> None:
        self.next_id = 0
        self.subst: dict[str, Type] = {}

    def fresh(self) -> TVar:
        t = TVar(f"t{self.next_id}")
        self.next_id += 1
        return t


def apply(subst: dict[str, Type], typ: Type) -> Type:
    if isinstance(typ, TVar):
        if typ.name in subst:
            return apply(subst, subst[typ.name])
        return typ
    if isinstance(typ, TFunc):
        return TFunc(apply(subst, typ.arg), apply(subst, typ.ret))
    if isinstance(typ, TApp):
        return TApp(apply(subst, typ.base), apply(subst, typ.arg))
    return typ


def ftv(typ: Type) -> set[str]:
    if isinstance(typ, TVar):
        return {typ.name}
    if isinstance(typ, TFunc):
        return ftv(typ.arg) | ftv(typ.ret)
    if isinstance(typ, TApp):
        return ftv(typ.base) | ftv(typ.arg)
    return set()


def ftv_scheme(s: Scheme) -> set[str]:
    return ftv(s.type) - set(s.vars)


def ftv_env(env: dict[str, Scheme]) -> set[str]:
    out: set[str] = set()
    for scheme in env.values():
        out |= ftv_scheme(scheme)
    return out


def bind_var(state: InferState, name: str, typ: Type) -> None:
    typ = apply(state.subst, typ)
    if typ == TVar(name):
        return
    if name in ftv(typ):
        raise tc_error(f"Occurs check failed: {name} appears in {type_to_string(typ)}")
    state.subst[name] = typ


def unify(state: InferState, left: Type, right: Type) -> None:
    left = apply(state.subst, left)
    right = apply(state.subst, right)

    if isinstance(left, TVar):
        bind_var(state, left.name, right)
        return
    if isinstance(right, TVar):
        bind_var(state, right.name, left)
        return

    if isinstance(left, TConst) and isinstance(right, TConst):
        if left.name != right.name and left.name.rsplit(".", 1)[-1] != right.name.rsplit(".", 1)[-1]:
            raise TypeCheckError(f"Type mismatch: {left.name} vs {right.name}")
        return

    if isinstance(left, TFunc) and isinstance(right, TFunc):
        unify(state, left.arg, right.arg)
        unify(state, left.ret, right.ret)
        return

    if isinstance(left, TApp) and isinstance(right, TApp):
        unify(state, left.base, right.base)
        unify(state, left.arg, right.arg)
        return

    raise TypeCheckError(f"Type mismatch: {type_to_string(left)} vs {type_to_string(right)}")


def instantiate(state: InferState, scheme: Scheme) -> Type:
    repl: dict[str, Type] = {v: state.fresh() for v in scheme.vars}

    def go(typ: Type) -> Type:
        if isinstance(typ, TVar) and typ.name in repl:
            return repl[typ.name]
        if isinstance(typ, TFunc):
            return TFunc(go(typ.arg), go(typ.ret))
        if isinstance(typ, TApp):
            return TApp(go(typ.base), go(typ.arg))
        return typ

    return go(scheme.type)


def generalize(env: dict[str, Scheme], typ: Type, state: InferState) -> Scheme:
    resolved = apply(state.subst, typ)
    vars_ = tuple(sorted(ftv(resolved) - ftv_env(env)))
    return Scheme(vars=vars_, type=resolved)


def type_to_string(typ: Type) -> str:
    typ = apply({}, typ)
    if isinstance(typ, TVar):
        return typ.name
    if isinstance(typ, TConst):
        return typ.name
    if isinstance(typ, TApp):
        return f"{type_to_string(typ.base)} {type_to_string(typ.arg)}"
    if isinstance(typ, TFunc):
        left = type_to_string(typ.arg)
        right = type_to_string(typ.ret)
        if isinstance(typ.arg, TFunc):
            left = f"({left})"
        return f"{left} -> {right}"
    return repr(typ)


def scheme_to_string(scheme: Scheme, subst: dict[str, Type]) -> str:
    masked = {k: v for k, v in subst.items() if k not in set(scheme.vars)}
    solved = apply(masked, scheme.type)
    txt = type_to_string(solved)
    if scheme.vars:
        return f"forall {' '.join(scheme.vars)}. {txt}"
    return txt


def parse_type_expr(
    node: ast.TypeExpr,
    local_vars: dict[str, TVar] | None = None,
    allow_implicit_type_vars: bool = False,
    state: InferState | None = None,
) -> Type:
    local_vars = local_vars or {}
    if isinstance(node, ast.TypeName):
        if node.name in local_vars:
            return local_vars[node.name]
        leaf = node.name.rsplit(".", 1)[-1]
        if allow_implicit_type_vars and leaf and leaf[0].islower():
            local_vars[node.name] = state.fresh() if state is not None else TVar(f"v_{node.name}")
            return local_vars[node.name]
        return TConst(node.name)
    if isinstance(node, ast.TypeApply):
        return TApp(
            parse_type_expr(node.base, local_vars, allow_implicit_type_vars, state),
            parse_type_expr(node.arg, local_vars, allow_implicit_type_vars, state),
        )
    if isinstance(node, ast.TypeArrow):
        return TFunc(
            parse_type_expr(node.left, local_vars, allow_implicit_type_vars, state),
            parse_type_expr(node.right, local_vars, allow_implicit_type_vars, state),
        )
    raise TypeCheckError(f"Unsupported type expression {node}")


def fn_type_from_decl(decl: ast.FnDecl, state: InferState) -> Type:
    local_vars: dict[str, TVar] = {}
    param_types = [
        parse_type_expr(param.type_expr, local_vars, allow_implicit_type_vars=True, state=state)
        for param in decl.params
    ]
    ret = (
        parse_type_expr(decl.return_type, local_vars, allow_implicit_type_vars=True, state=state)
        if decl.return_type
        else state.fresh()
    )
    typ = ret
    for p in reversed(param_types):
        typ = TFunc(p, typ)
    return typ


def method_type_from_parts(
    params: list[ast.Param],
    return_type: ast.TypeExpr | None,
    local_vars: dict[str, TVar],
) -> Type:
    param_types = [
        parse_type_expr(param.type_expr, local_vars, allow_implicit_type_vars=True)
        for param in params
    ]
    ret = parse_type_expr(return_type, local_vars, allow_implicit_type_vars=True) if return_type else UNIT
    typ = ret
    for p in reversed(param_types):
        typ = TFunc(p, typ)
    return typ


def _type_expr_key(node: ast.TypeExpr) -> tuple:
    if isinstance(node, ast.TypeName):
        return ("name", node.name)
    if isinstance(node, ast.TypeApply):
        return ("apply", _type_expr_key(node.base), _type_expr_key(node.arg))
    if isinstance(node, ast.TypeArrow):
        return ("arrow", _type_expr_key(node.left), _type_expr_key(node.right))
    raise TypeCheckError(f"Unsupported type expression {node}")


def build_class_decls(program: ast.Program) -> dict[str, ClassDeclInfo]:
    out: dict[str, ClassDeclInfo] = {}
    for decl in program.declarations:
        if not isinstance(decl, ast.ClassDecl):
            continue
        if decl.name in out:
            raise tc_error(f"Duplicate class declaration: {decl.name}", decl)
        if len(set(decl.type_params)) != len(decl.type_params):
            raise tc_error(f"Duplicate class type parameter in {decl.name}", decl)
        method_types: dict[str, Type] = {}
        class_local_vars: dict[str, TVar] = {
            p: TVar(f"class.{decl.name}.{p}") for p in decl.type_params
        }
        for method in decl.methods:
            if method.name in method_types:
                raise tc_error(f"Duplicate method {method.name} in class {decl.name}", method)
            method_types[method.name] = method_type_from_parts(
                method.params,
                method.return_type,
                dict(class_local_vars),
            )
        out[decl.name] = ClassDeclInfo(
            arity=len(decl.type_params),
            type_param_vars=tuple(f"class.{decl.name}.{p}" for p in decl.type_params),
            methods=method_types,
        )
    return out


def _validate_constraint(
    constraint: ast.TypeConstraint,
    class_decls: dict[str, ClassDeclInfo],
    node: object | None = None,
) -> None:
    class_info = class_decls.get(constraint.class_name)
    if class_info is None:
        raise tc_error(f"Unknown class {constraint.class_name}", node or constraint)
    if len(constraint.args) != class_info.arity:
        raise tc_error(
            f"Class {constraint.class_name} expects {class_info.arity} type arg(s), got {len(constraint.args)}",
            node or constraint,
        )


def validate_class_constraints(program: ast.Program, class_decls: dict[str, ClassDeclInfo]) -> None:
    seen_instances: set[tuple[str, tuple[tuple, ...]]] = set()
    for decl in program.declarations:
        if isinstance(decl, ast.FnDecl):
            for constraint in decl.constraints:
                _validate_constraint(constraint, class_decls, decl)
        elif isinstance(decl, ast.InstanceDecl):
            _validate_constraint(decl.constraint, class_decls, decl)
            key = (
                decl.constraint.class_name,
                tuple(_type_expr_key(arg) for arg in decl.constraint.args),
            )
            if key in seen_instances:
                raise tc_error(
                    f"Duplicate instance declaration for {decl.constraint.class_name}",
                    decl,
                )
            seen_instances.add(key)


def substitute_type_vars(typ: Type, subst: dict[str, Type]) -> Type:
    if isinstance(typ, TVar):
        return subst.get(typ.name, typ)
    if isinstance(typ, TFunc):
        return TFunc(substitute_type_vars(typ.arg, subst), substitute_type_vars(typ.ret, subst))
    if isinstance(typ, TApp):
        return TApp(substitute_type_vars(typ.base, subst), substitute_type_vars(typ.arg, subst))
    return typ


def type_to_ast_expr(typ: Type) -> ast.TypeExpr:
    typ = apply({}, typ)
    if isinstance(typ, TVar):
        return ast.TypeName(typ.name)
    if isinstance(typ, TConst):
        return ast.TypeName(typ.name)
    if isinstance(typ, TApp):
        return ast.TypeApply(base=type_to_ast_expr(typ.base), arg=type_to_ast_expr(typ.arg))
    if isinstance(typ, TFunc):
        return ast.TypeArrow(left=type_to_ast_expr(typ.arg), right=type_to_ast_expr(typ.ret))
    raise TypeCheckError(f"Unsupported type for AST conversion: {typ}")


def _mark_expr_type(expr: ast.Expr, typ: Type) -> Type:
    setattr(expr, "_inferred_type_raw", typ)
    return typ


def _finalize_inferred_expr_types(program: ast.Program, state: InferState) -> None:
    def visit_expr(expr: ast.Expr) -> None:
        raw = getattr(expr, "_inferred_type_raw", None)
        if raw is not None:
            setattr(expr, "inferred_type", type_to_ast_expr(apply(state.subst, raw)))
        if isinstance(expr, ast.IfExpr):
            visit_expr(expr.condition)
            visit_expr(expr.then_branch)
            visit_expr(expr.else_branch)
            return
        if isinstance(expr, ast.MatchExpr):
            visit_expr(expr.scrutinee)
            for branch in expr.branches:
                visit_expr(branch.value)
            return
        if isinstance(expr, ast.BinaryExpr):
            visit_expr(expr.left)
            visit_expr(expr.right)
            return
        if isinstance(expr, ast.UnaryExpr):
            visit_expr(expr.operand)
            return
        if isinstance(expr, ast.CallExpr):
            visit_expr(expr.callee)
            for arg in expr.args:
                visit_expr(arg)
            return

    for decl in program.declarations:
        if isinstance(decl, ast.FnDecl):
            visit_expr(decl.body)
        elif isinstance(decl, ast.LetDecl):
            visit_expr(decl.value)
        elif isinstance(decl, ast.InstanceDecl):
            for method in decl.methods:
                visit_expr(method.body)


def build_global_method_info(class_decls: dict[str, ClassDeclInfo]) -> dict[str, GlobalMethodInfo]:
    methods: dict[str, GlobalMethodInfo | None] = {}
    for class_name, class_info in class_decls.items():
        for method_name, method_type in class_info.methods.items():
            if method_name in methods:
                methods[method_name] = None
                continue
            methods[method_name] = GlobalMethodInfo(
                class_name=class_name,
                class_info=class_info,
                method_type=method_type,
            )
    return {name: info for name, info in methods.items() if info is not None}


def validate_instance_methods(
    program: ast.Program,
    class_decls: dict[str, ClassDeclInfo],
    env: dict[str, Scheme],
    state: InferState,
    type_decls: dict[str, TypeDeclInfo],
) -> None:
    for decl in program.declarations:
        if not isinstance(decl, ast.InstanceDecl):
            continue
        class_info = class_decls.get(decl.constraint.class_name)
        if class_info is None:
            continue
        expected_names = set(class_info.methods.keys())
        provided_names = {m.name for m in decl.methods}
        if expected_names != provided_names:
            missing = sorted(expected_names - provided_names)
            extra = sorted(provided_names - expected_names)
            parts: list[str] = []
            if missing:
                parts.append(f"missing: {', '.join(missing)}")
            if extra:
                parts.append(f"extra: {', '.join(extra)}")
            detail = "; ".join(parts) if parts else "method set mismatch"
            raise tc_error(
                f"Instance for {decl.constraint.class_name} has incorrect methods ({detail})",
                decl,
            )

        method_impls: dict[str, ast.InstanceMethodImpl] = {}
        for method in decl.methods:
            if method.name in method_impls:
                raise tc_error(f"Duplicate method {method.name} in instance", method)
            method_impls[method.name] = method

        instance_args = [parse_type_expr(arg) for arg in decl.constraint.args]
        class_subst = {
            class_var_name: arg_type
            for class_var_name, arg_type in zip(class_info.type_param_vars, instance_args)
        }

        for method_name, expected_template in class_info.methods.items():
            impl = method_impls[method_name]
            expected_type = substitute_type_vars(expected_template, class_subst)
            actual_type = fn_type_from_decl(
                ast.FnDecl(
                    name=impl.name,
                    params=impl.params,
                    return_type=impl.return_type,
                    constraints=[],
                    body=impl.body,
                ),
                state,
            )

            local_state = InferState()
            unify_at(local_state, expected_type, actual_type, impl)

            working_env = dict(env)
            cursor = apply(state.subst, actual_type)
            for param in impl.params:
                cursor = apply(state.subst, cursor)
                if not isinstance(cursor, TFunc):
                    raise tc_error("Internal error for instance method params", impl)
                working_env[param.name] = Scheme(vars=(), type=cursor.arg)
                cursor = cursor.ret
            body_t = infer_expr(impl.body, working_env, state, type_decls, {})
            expected_return = apply(state.subst, cursor)
            unify_at(state, body_t, expected_return, impl.body)


def infer_expr(
    expr: ast.Expr,
    env: dict[str, Scheme],
    state: InferState,
    type_decls: dict[str, TypeDeclInfo],
    global_methods: dict[str, GlobalMethodInfo],
) -> Type:
    if isinstance(expr, ast.IntExpr):
        return _mark_expr_type(expr, INT)
    if isinstance(expr, ast.BoolExpr):
        return _mark_expr_type(expr, BOOL)
    if isinstance(expr, ast.StringExpr):
        return _mark_expr_type(expr, STRING)
    if isinstance(expr, ast.VarExpr):
        scheme = env.get(expr.name)
        if scheme is None:
            raise tc_error(f"Unknown variable {expr.name}", expr)
        return _mark_expr_type(expr, instantiate(state, scheme))
    if isinstance(expr, ast.UnaryExpr):
        operand_t = infer_expr(expr.operand, env, state, type_decls, global_methods)
        if expr.op == "-":
            unify_at(state, operand_t, INT, expr)
            return _mark_expr_type(expr, INT)
        raise tc_error(f"Unsupported unary operator {expr.op}", expr)
    if isinstance(expr, ast.BinaryExpr):
        left = infer_expr(expr.left, env, state, type_decls, global_methods)
        right = infer_expr(expr.right, env, state, type_decls, global_methods)
        if expr.op == ">>":
            input_t = state.fresh()
            middle_t = state.fresh()
            output_t = state.fresh()
            unify_at(state, right, TFunc(input_t, middle_t), expr.right)
            unify_at(state, left, TFunc(middle_t, output_t), expr.left)
            return _mark_expr_type(expr, TFunc(input_t, output_t))
        if expr.op in {"+", "-", "*", "/"}:
            unify_at(state, left, INT, expr.left)
            unify_at(state, right, INT, expr.right)
            return _mark_expr_type(expr, INT)
        if expr.op in {"<", "<=", ">", ">="}:
            unify_at(state, left, INT, expr.left)
            unify_at(state, right, INT, expr.right)
            return _mark_expr_type(expr, BOOL)
        if expr.op in {"==", "!="}:
            unify_at(state, left, right, expr)
            return _mark_expr_type(expr, BOOL)
        if expr.op in {"&&", "||"}:
            unify_at(state, left, BOOL, expr.left)
            unify_at(state, right, BOOL, expr.right)
            return _mark_expr_type(expr, BOOL)
        raise tc_error(f"Unsupported binary operator {expr.op}", expr)
    if isinstance(expr, ast.CallExpr):
        direct_method_info: GlobalMethodInfo | None = None
        direct_method_args: list[Type] = []
        if isinstance(expr.callee, ast.VarExpr) and expr.callee.name not in env:
            direct_method_info = global_methods.get(expr.callee.name)
        if direct_method_info is not None:
            replacements = {name: state.fresh() for name in ftv(direct_method_info.method_type)}
            direct_method_args = [replacements[name] for name in direct_method_info.class_info.type_param_vars]

            def go(typ: Type) -> Type:
                if isinstance(typ, TVar) and typ.name in replacements:
                    return replacements[typ.name]
                if isinstance(typ, TFunc):
                    return TFunc(go(typ.arg), go(typ.ret))
                if isinstance(typ, TApp):
                    return TApp(go(typ.base), go(typ.arg))
                return typ

            callee = go(direct_method_info.method_type)
        else:
            callee = infer_expr(expr.callee, env, state, type_decls, global_methods)
        result = state.fresh()
        typ = callee
        for arg_expr in expr.args:
            arg_t = infer_expr(arg_expr, env, state, type_decls, global_methods)
            next_t = state.fresh()
            unify_at(state, typ, TFunc(arg_t, next_t), arg_expr)
            typ = next_t
        unify_at(state, typ, result, expr)
        if direct_method_info is not None:
            resolved_args = [apply(state.subst, arg) for arg in direct_method_args]
            setattr(
                expr,
                "resolved_constraint",
                ast.TypeConstraint(
                    class_name=direct_method_info.class_name,
                    args=[type_to_ast_expr(arg) for arg in resolved_args],
                ),
            )
        return _mark_expr_type(expr, result)
    if isinstance(expr, ast.IfExpr):
        cond = infer_expr(expr.condition, env, state, type_decls, global_methods)
        unify_at(state, cond, BOOL, expr.condition)
        then_t = infer_expr(expr.then_branch, env, state, type_decls, global_methods)
        else_t = infer_expr(expr.else_branch, env, state, type_decls, global_methods)
        unify_at(state, then_t, else_t, expr)
        return _mark_expr_type(expr, then_t)
    if isinstance(expr, ast.MatchExpr):
        scrutinee_t = infer_expr(expr.scrutinee, env, state, type_decls, global_methods)
        out_t = state.fresh()
        branch_ctors: list[str] = []
        has_catchall = False

        for branch in expr.branches:
            branch_env = dict(env)
            ctor_name = infer_pattern(
                branch.pattern,
                scrutinee_t,
                branch_env,
                state,
                type_decls,
            )
            if ctor_name is not None:
                branch_ctors.append(ctor_name)
            if isinstance(branch.pattern, (ast.WildcardPattern, ast.VarPattern)):
                has_catchall = True
            value_t = infer_expr(branch.value, branch_env, state, type_decls, global_methods)
            unify_at(state, out_t, value_t, branch.value)

        ensure_exhaustive_match(scrutinee_t, branch_ctors, has_catchall, state, type_decls, expr)
        return _mark_expr_type(expr, out_t)

    raise tc_error(f"Unsupported expression node: {expr}", expr)


def infer_pattern(
    pattern: ast.Pattern,
    expected_type: Type,
    env: dict[str, Scheme],
    state: InferState,
    type_decls: dict[str, TypeDeclInfo],
) -> str | None:
    if isinstance(pattern, ast.WildcardPattern):
        return None
    if isinstance(pattern, ast.VarPattern):
        env[pattern.name] = Scheme(vars=(), type=apply(state.subst, expected_type))
        return None
    if isinstance(pattern, ast.IntPattern):
        unify_at(state, expected_type, INT, pattern)
        return None
    if isinstance(pattern, ast.BoolPattern):
        unify_at(state, expected_type, BOOL, pattern)
        return None
    if isinstance(pattern, ast.StringPattern):
        unify_at(state, expected_type, STRING, pattern)
        return None
    if isinstance(pattern, ast.ConstructorPattern):
        ctor_scheme = env.get(pattern.name)
        if ctor_scheme is None:
            raise tc_error(f"Unknown constructor {pattern.name}", pattern)

        ctor_t = instantiate(state, ctor_scheme)
        arg_types: list[Type] = []
        current = ctor_t
        while isinstance(apply(state.subst, current), TFunc):
            current = apply(state.subst, current)
            assert isinstance(current, TFunc)
            arg_types.append(current.arg)
            current = current.ret
        if len(arg_types) != len(pattern.args):
            raise tc_error(
                f"Constructor {pattern.name} expects {len(arg_types)} args, got {len(pattern.args)}",
                pattern,
            )

        unify_at(state, current, expected_type, pattern)
        for arg_pat, arg_t in zip(pattern.args, arg_types):
            infer_pattern(arg_pat, arg_t, env, state, type_decls)
        return pattern.name

    raise tc_error(f"Unsupported pattern node: {pattern}", pattern)


def ensure_exhaustive_match(
    scrutinee_t: Type,
    seen_ctors: list[str],
    has_catchall: bool,
    state: InferState,
    type_decls: dict[str, TypeDeclInfo],
    node: ast.MatchExpr | None = None,
) -> None:
    if has_catchall:
        return

    resolved = apply(state.subst, scrutinee_t)
    adt_name = None
    if isinstance(resolved, TConst):
        adt_name = resolved.name
    elif isinstance(resolved, TApp):
        base = resolved.base
        while isinstance(base, TApp):
            base = base.base
        if isinstance(base, TConst):
            adt_name = base.name

    if adt_name is None or adt_name not in type_decls:
        return

    all_ctors = set(type_decls[adt_name].constructors.keys())
    missing = sorted(all_ctors - set(seen_ctors))
    if missing:
        miss = ", ".join(missing)
        raise tc_error(f"Non-exhaustive match, missing constructor(s): {miss}", node)


def build_type_decls(program: ast.Program) -> dict[str, TypeDeclInfo]:
    out: dict[str, TypeDeclInfo] = {}
    for decl in program.declarations:
        if not isinstance(decl, ast.TypeDecl):
            continue
        if decl.name in out:
            raise TypeCheckError(f"Duplicate type declaration: {decl.name}")

        local_vars = {name: TVar(f"{decl.name}.{name}") for name in decl.type_params}
        data_t: Type = TConst(decl.name)
        for param in decl.type_params:
            data_t = TApp(data_t, local_vars[param])

        ctors: dict[str, Type] = {}
        for ctor in decl.constructors:
            ctor_t = data_t
            args = [parse_type_expr(arg, local_vars) for arg in ctor.args]
            for arg in reversed(args):
                ctor_t = TFunc(arg, ctor_t)
            ctors[ctor.name] = ctor_t

        out[decl.name] = TypeDeclInfo(params=decl.type_params, constructors=ctors)
    return out


def typecheck_program(program: ast.Program) -> dict[str, str]:
    state = InferState()
    type_decls = build_type_decls(program)
    class_decls = build_class_decls(program)
    global_methods = build_global_method_info(class_decls)
    validate_class_constraints(program, class_decls)

    p_var = TVar("prelude.print.a")
    vector_var = TVar("prelude.vector.a")
    maybe_collections = TConst("stdlib.collections.Maybe")
    maybe_vector_var = TApp(maybe_collections, vector_var)
    vector_t = TApp(TConst("Vector"), vector_var)
    map_var = TVar("prelude.map.a")
    maybe_map_var = TApp(maybe_collections, map_var)
    map_t = TApp(TConst("Map"), map_var)
    env: dict[str, Scheme] = {
        "print": Scheme(vars=(p_var.name,), type=TFunc(p_var, TApp(TConst("IO"), UNIT))),
        "print_int": Scheme(vars=(), type=TFunc(INT, INT)),
        "read_lines": Scheme(
            vars=(),
            type=TFunc(TConst("String"), TApp(TConst("List"), TConst("String"))),
        ),
        "read_file": Scheme(
            vars=(),
            type=TFunc(TConst("String"), TConst("String")),
        ),
        "read_int_lines": Scheme(
            vars=(),
            type=TFunc(TConst("String"), TApp(TConst("Vector"), INT)),
        ),
        "env_get": Scheme(
            vars=(),
            type=TFunc(TConst("String"), TApp(maybe_collections, STRING)),
        ),
        "parse_int": Scheme(vars=(), type=TFunc(TConst("String"), INT)),
        "split_words": Scheme(
            vars=(),
            type=TFunc(TConst("String"), TApp(TConst("List"), TConst("String"))),
        ),
        "str_concat": Scheme(vars=(), type=TFunc(STRING, TFunc(STRING, STRING))),
        "str_len": Scheme(vars=(), type=TFunc(STRING, INT)),
        "str_slice": Scheme(vars=(), type=TFunc(STRING, TFunc(INT, TFunc(INT, STRING)))),
        "str_find": Scheme(vars=(), type=TFunc(STRING, TFunc(STRING, INT))),
        "str_starts_with": Scheme(vars=(), type=TFunc(STRING, TFunc(STRING, BOOL))),
        "vector_empty": Scheme(vars=(vector_var.name,), type=vector_t),
        "vector_length": Scheme(vars=(vector_var.name,), type=TFunc(vector_t, INT)),
        "vector_get": Scheme(vars=(vector_var.name,), type=TFunc(vector_t, TFunc(INT, maybe_vector_var))),
        "vector_set": Scheme(vars=(vector_var.name,), type=TFunc(vector_t, TFunc(INT, TFunc(vector_var, vector_t)))),
        "vector_append": Scheme(vars=(vector_var.name,), type=TFunc(vector_t, TFunc(vector_var, vector_t))),
        "map_empty": Scheme(vars=(map_var.name,), type=map_t),
        "map_get": Scheme(vars=(map_var.name,), type=TFunc(map_t, TFunc(STRING, maybe_map_var))),
        "map_set": Scheme(vars=(map_var.name,), type=TFunc(map_t, TFunc(STRING, TFunc(map_var, map_t)))),
        "map_remove": Scheme(vars=(map_var.name,), type=TFunc(map_t, TFunc(STRING, map_t))),
        "map_size": Scheme(vars=(map_var.name,), type=TFunc(map_t, INT)),
        "map_nth_key": Scheme(vars=(map_var.name,), type=TFunc(map_t, TFunc(INT, TApp(maybe_collections, STRING)))),
        "map_nth_value": Scheme(vars=(map_var.name,), type=TFunc(map_t, TFunc(INT, maybe_map_var))),
        "tcp_listen": Scheme(vars=(), type=TFunc(INT, INT)),
        "tcp_accept": Scheme(vars=(), type=TFunc(INT, INT)),
        "tcp_read": Scheme(vars=(), type=TFunc(INT, STRING)),
        "tcp_write": Scheme(
            vars=(),
            type=TFunc(INT, TFunc(STRING, TApp(TConst("IO"), UNIT))),
        ),
        "tcp_close": Scheme(vars=(), type=TFunc(INT, TApp(TConst("IO"), UNIT))),
        "tcp_close_listener": Scheme(vars=(), type=TFunc(INT, TApp(TConst("IO"), UNIT))),
        "tcp_echo_serve": Scheme(vars=(), type=TFunc(INT, TFunc(INT, TApp(TConst("IO"), UNIT)))),
        "http_request": Scheme(
            vars=(),
            type=TFunc(
                STRING,
                TFunc(
                    STRING,
                    TFunc(
                        STRING,
                        TFunc(
                            STRING,
                            TFunc(
                                INT,
                                TApp(
                                    TApp(TConst("stdlib.http.Result"), TConst("stdlib.http.HttpError")),
                                    TConst("stdlib.http.HttpResponse"),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
        "json_parse": Scheme(
            vars=(),
            type=TFunc(
                STRING,
                TApp(
                    TApp(TConst("stdlib.http.Result"), TConst("stdlib.http.JsonError")),
                    TConst("stdlib.http.Json"),
                ),
            ),
        ),
        "term_clear": Scheme(vars=(), type=TApp(TConst("IO"), UNIT)),
        "term_move": Scheme(vars=(), type=TFunc(INT, TFunc(INT, TApp(TConst("IO"), UNIT)))),
        "term_hide_cursor": Scheme(vars=(), type=TApp(TConst("IO"), UNIT)),
        "term_show_cursor": Scheme(vars=(), type=TApp(TConst("IO"), UNIT)),
        "term_read_key": Scheme(vars=(), type=STRING),
        "term_write": Scheme(vars=(), type=TFunc(STRING, TApp(TConst("IO"), UNIT))),
    }

    for info in type_decls.values():
        for ctor_name, ctor_type in info.constructors.items():
            vars_ = tuple(sorted(ftv(ctor_type)))
            env[ctor_name] = Scheme(vars=vars_, type=ctor_type)

    fn_types: Dict[str, Type] = {}
    for decl in program.declarations:
        if not isinstance(decl, ast.FnDecl):
            continue
        fn_decl = decl
        if fn_decl.name in fn_types:
            raise TypeCheckError(f"Duplicate function {fn_decl.name}")
        fn_t = fn_type_from_decl(fn_decl, state)
        fn_types[fn_decl.name] = fn_t
        env[fn_decl.name] = Scheme(vars=(), type=fn_t)

    for decl in program.declarations:
        if isinstance(decl, ast.FnDecl):
            fn_decl = decl
            fn_t = fn_types[fn_decl.name]
            working_env = dict(env)
            cursor = fn_t
            for param in fn_decl.params:
                cursor = apply(state.subst, cursor)
                if not isinstance(cursor, TFunc):
                    raise TypeCheckError(f"Internal error for function params in {fn_decl.name}")
                working_env[param.name] = Scheme(vars=(), type=cursor.arg)
                cursor = cursor.ret

            for constraint in fn_decl.constraints:
                class_info = class_decls.get(constraint.class_name)
                if class_info is None:
                    continue
                instance_args = [
                    parse_type_expr(arg, allow_implicit_type_vars=True, state=state)
                    for arg in constraint.args
                ]
                class_subst = {
                    class_var_name: arg_type
                    for class_var_name, arg_type in zip(class_info.type_param_vars, instance_args)
                }
                for method_name, method_template in class_info.methods.items():
                    resolved_method_type = substitute_type_vars(method_template, class_subst)
                    method_scheme = Scheme(
                        vars=tuple(sorted(ftv(resolved_method_type))),
                        type=resolved_method_type,
                    )
                    existing = working_env.get(method_name)
                    if existing is not None and scheme_to_string(existing, state.subst) != scheme_to_string(
                        method_scheme, state.subst
                    ):
                        raise tc_error(
                            f"Ambiguous method {method_name} from constraints in function {fn_decl.name}",
                            fn_decl,
                        )
                    working_env[method_name] = method_scheme

            body_t = infer_expr(fn_decl.body, working_env, state, type_decls, global_methods)
            expected_return = apply(state.subst, cursor)
            unify_at(state, body_t, expected_return, fn_decl.body)

            solved_fn = apply(state.subst, fn_t)
            generalized_env = dict(env)
            generalized_env.pop(fn_decl.name, None)
            env[fn_decl.name] = generalize(generalized_env, solved_fn, state)

        elif isinstance(decl, ast.LetDecl):
            let_decl = decl
            value_t = infer_expr(let_decl.value, env, state, type_decls, global_methods)
            env[let_decl.name] = generalize(env, value_t, state)

    validate_instance_methods(program, class_decls, env, state, type_decls)
    _finalize_inferred_expr_types(program, state)

    return {name: scheme_to_string(sch, state.subst) for name, sch in env.items()}
