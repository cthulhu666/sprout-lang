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
        if left.name != right.name:
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
        if allow_implicit_type_vars and node.name and node.name[0].islower():
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


def infer_expr(
    expr: ast.Expr,
    env: dict[str, Scheme],
    state: InferState,
    type_decls: dict[str, TypeDeclInfo],
) -> Type:
    if isinstance(expr, ast.IntExpr):
        return INT
    if isinstance(expr, ast.BoolExpr):
        return BOOL
    if isinstance(expr, ast.StringExpr):
        return STRING
    if isinstance(expr, ast.VarExpr):
        scheme = env.get(expr.name)
        if scheme is None:
            raise tc_error(f"Unknown variable {expr.name}", expr)
        return instantiate(state, scheme)
    if isinstance(expr, ast.UnaryExpr):
        operand_t = infer_expr(expr.operand, env, state, type_decls)
        if expr.op == "-":
            unify_at(state, operand_t, INT, expr)
            return INT
        raise tc_error(f"Unsupported unary operator {expr.op}", expr)
    if isinstance(expr, ast.BinaryExpr):
        left = infer_expr(expr.left, env, state, type_decls)
        right = infer_expr(expr.right, env, state, type_decls)
        if expr.op == ">>":
            input_t = state.fresh()
            middle_t = state.fresh()
            output_t = state.fresh()
            unify_at(state, right, TFunc(input_t, middle_t), expr.right)
            unify_at(state, left, TFunc(middle_t, output_t), expr.left)
            return TFunc(input_t, output_t)
        if expr.op in {"+", "-", "*", "/"}:
            unify_at(state, left, INT, expr.left)
            unify_at(state, right, INT, expr.right)
            return INT
        if expr.op in {"<", "<=", ">", ">="}:
            unify_at(state, left, INT, expr.left)
            unify_at(state, right, INT, expr.right)
            return BOOL
        if expr.op in {"==", "!="}:
            unify_at(state, left, right, expr)
            return BOOL
        if expr.op in {"&&", "||"}:
            unify_at(state, left, BOOL, expr.left)
            unify_at(state, right, BOOL, expr.right)
            return BOOL
        raise tc_error(f"Unsupported binary operator {expr.op}", expr)
    if isinstance(expr, ast.CallExpr):
        callee = infer_expr(expr.callee, env, state, type_decls)
        result = state.fresh()
        typ = callee
        for arg_expr in expr.args:
            arg_t = infer_expr(arg_expr, env, state, type_decls)
            next_t = state.fresh()
            unify_at(state, typ, TFunc(arg_t, next_t), arg_expr)
            typ = next_t
        unify_at(state, typ, result, expr)
        return result
    if isinstance(expr, ast.IfExpr):
        cond = infer_expr(expr.condition, env, state, type_decls)
        unify_at(state, cond, BOOL, expr.condition)
        then_t = infer_expr(expr.then_branch, env, state, type_decls)
        else_t = infer_expr(expr.else_branch, env, state, type_decls)
        unify_at(state, then_t, else_t, expr)
        return then_t
    if isinstance(expr, ast.MatchExpr):
        scrutinee_t = infer_expr(expr.scrutinee, env, state, type_decls)
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
            value_t = infer_expr(branch.value, branch_env, state, type_decls)
            unify_at(state, out_t, value_t, branch.value)

        ensure_exhaustive_match(scrutinee_t, branch_ctors, has_catchall, state, type_decls, expr)
        return out_t

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

    p_var = TVar("prelude.print.a")
    vector_var = TVar("prelude.vector.a")
    maybe_vector_var = TApp(TConst("Maybe"), vector_var)
    vector_t = TApp(TConst("Vector"), vector_var)
    map_var = TVar("prelude.map.a")
    maybe_map_var = TApp(TConst("Maybe"), map_var)
    map_t = TApp(TConst("Map"), map_var)
    env: dict[str, Scheme] = {
        "print": Scheme(vars=(p_var.name,), type=TFunc(p_var, TApp(TConst("IO"), UNIT))),
        "print_int": Scheme(vars=(), type=TFunc(INT, INT)),
        "read_lines": Scheme(
            vars=(),
            type=TFunc(TConst("String"), TApp(TConst("List"), TConst("String"))),
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
                                    TApp(TConst("Result"), TConst("HttpError")),
                                    TConst("HttpResponse"),
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
                    TApp(TConst("Result"), TConst("JsonError")),
                    TConst("Json"),
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

            body_t = infer_expr(fn_decl.body, working_env, state, type_decls)
            expected_return = apply(state.subst, cursor)
            unify_at(state, body_t, expected_return, fn_decl.body)

            solved_fn = apply(state.subst, fn_t)
            generalized_env = dict(env)
            generalized_env.pop(fn_decl.name, None)
            env[fn_decl.name] = generalize(generalized_env, solved_fn, state)

        elif isinstance(decl, ast.LetDecl):
            let_decl = decl
            value_t = infer_expr(let_decl.value, env, state, type_decls)
            env[let_decl.name] = generalize(env, value_t, state)

    return {name: scheme_to_string(sch, state.subst) for name, sch in env.items()}
