from __future__ import annotations

"""Hindley-Milner style type inference/checking for Sprout v0.

This module implements:
- unification-based inference,
- let-polymorphism via schemes (forall),
- ADT constructor typing,
- basic match exhaustiveness for ADTs.
"""

from dataclasses import dataclass, field
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


def _friendly_type_var_name(index: int) -> str:
    letter = chr(ord("a") + (index % 26))
    suffix = index // 26
    if suffix == 0:
        return letter
    return f"{letter}{suffix}"


def _display_type_var_mapping(vars_: list[str] | tuple[str, ...]) -> dict[str, str]:
    return {name: _friendly_type_var_name(idx) for idx, name in enumerate(vars_)}


def _collect_type_var_names_in_order(typ: "Type", out: list[str], seen: set[str]) -> None:
    typ = apply({}, typ)
    if isinstance(typ, TVar):
        if typ.name not in seen:
            seen.add(typ.name)
            out.append(typ.name)
        return
    if isinstance(typ, TFunc):
        _collect_type_var_names_in_order(typ.arg, out, seen)
        _collect_type_var_names_in_order(typ.ret, out, seen)
        return
    if isinstance(typ, TApp):
        _collect_type_var_names_in_order(typ.base, out, seen)
        _collect_type_var_names_in_order(typ.arg, out, seen)
        return
    if isinstance(typ, TTuple):
        for item in typ.items:
            _collect_type_var_names_in_order(item, out, seen)


class Type:
    pass


class Effect:
    pass


@dataclass(frozen=True)
class EClosed(Effect):
    labels: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class EVar(Effect):
    name: str


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
    effects: Effect = field(default_factory=EClosed)


@dataclass(frozen=True)
class TApp(Type):
    base: Type
    arg: Type


@dataclass(frozen=True)
class TTuple(Type):
    items: tuple[Type, ...]


@dataclass(frozen=True)
class Scheme:
    vars: tuple[str, ...]
    type: Type
    effect_vars: tuple[str, ...] = ()
    effects: Effect = field(default_factory=EClosed)


@dataclass(frozen=True)
class MethodTypeInfo:
    type: Type
    effect_vars: tuple[str, ...] = ()
    effects: Effect = field(default_factory=EClosed)


@dataclass
class TypeDeclInfo:
    params: list[str]
    constructors: dict[str, Type]
    fields: dict[str, Type] = field(default_factory=dict)


@dataclass
class ClassDeclInfo:
    arity: int
    type_param_vars: tuple[str, ...]
    methods: dict[str, MethodTypeInfo]


@dataclass(frozen=True)
class GlobalMethodInfo:
    class_name: str
    class_info: ClassDeclInfo
    method_type: Type
    effects: Effect = field(default_factory=EClosed)
    effect_vars: tuple[str, ...] = ()


@dataclass(frozen=True)
class DoSequenceInfo:
    family: str
    payload_type: Type
    error_type: Type | None = None


INT = TConst("Int")
BOOL = TConst("Bool")
STRING = TConst("String")
CHAR = TConst("Char")
UNIT = TConst("Unit")
INT_RANGE = TConst("IntRange")
PURE_EFFECT = EClosed()
IO_EFFECT = EClosed(frozenset({"IO"}))


class InferState:
    def __init__(self) -> None:
        self.next_id = 0
        self.next_effect_id = 0
        self.subst: dict[str, Type] = {}
        self.effect_subst: dict[str, Effect] = {}

    def fresh(self) -> TVar:
        t = TVar(f"t{self.next_id}")
        self.next_id += 1
        return t

    def fresh_effect(self) -> EVar:
        effect = EVar(f"e{self.next_effect_id}")
        self.next_effect_id += 1
        return effect


def apply(
    subst: dict[str, Type],
    typ: Type,
    effect_subst: dict[str, Effect] | None = None,
) -> Type:
    effect_subst = effect_subst or {}
    if isinstance(typ, TVar):
        if typ.name in subst:
            return apply(subst, subst[typ.name], effect_subst)
        return typ
    if isinstance(typ, TFunc):
        return TFunc(
            apply(subst, typ.arg, effect_subst),
            apply(subst, typ.ret, effect_subst),
            apply_effect(effect_subst, typ.effects),
        )
    if isinstance(typ, TApp):
        return TApp(apply(subst, typ.base, effect_subst), apply(subst, typ.arg, effect_subst))
    if isinstance(typ, TTuple):
        return TTuple(tuple(apply(subst, item, effect_subst) for item in typ.items))
    return typ


def ftv(typ: Type) -> set[str]:
    if isinstance(typ, TVar):
        return {typ.name}
    if isinstance(typ, TFunc):
        return ftv(typ.arg) | ftv(typ.ret)
    if isinstance(typ, TApp):
        return ftv(typ.base) | ftv(typ.arg)
    if isinstance(typ, TTuple):
        out: set[str] = set()
        for item in typ.items:
            out |= ftv(item)
        return out
    return set()


def ftv_type_effects(typ: Type) -> set[str]:
    typ = apply({}, typ)
    if isinstance(typ, TFunc):
        return ftv_type_effects(typ.arg) | ftv_type_effects(typ.ret) | ftv_effect(typ.effects)
    if isinstance(typ, TApp):
        return ftv_type_effects(typ.base) | ftv_type_effects(typ.arg)
    if isinstance(typ, TTuple):
        out: set[str] = set()
        for item in typ.items:
            out |= ftv_type_effects(item)
        return out
    return set()


def apply_effect(subst: dict[str, Effect], effect: Effect) -> Effect:
    if isinstance(effect, EVar):
        replacement = subst.get(effect.name)
        if replacement is None:
            return effect
        return apply_effect(subst, replacement)
    return effect


def ftv_effect(effect: Effect) -> set[str]:
    effect = apply_effect({}, effect)
    if isinstance(effect, EVar):
        return {effect.name}
    return set()


def ftv_scheme(s: Scheme) -> set[str]:
    return ((ftv(s.type) | ftv_type_effects(s.type)) - set(s.vars) - set(s.effect_vars)) | (
        ftv_effect(s.effects) - set(s.effect_vars)
    )


def effect_from_names(names: tuple[str, ...] | list[str] | set[str] | frozenset[str] | None) -> Effect:
    if not names:
        return PURE_EFFECT
    entries = tuple(names)
    if len(entries) > 1:
        raise TypeCheckError("Only singleton effect rows are supported in this milestone")
    name = entries[0]
    leaf = name.rsplit(".", 1)[-1]
    if leaf and leaf[0].islower():
        return EVar(name)
    return EClosed(frozenset(entries))


def effects_to_string(effects: Effect, effect_var_names: dict[str, str] | None = None) -> str:
    effects = apply_effect({}, effects)
    if isinstance(effects, EVar):
        label = (effect_var_names or {}).get(effects.name, effects.name)
        return f" !{{{label}}}"
    if not effects.labels:
        return ""
    return " !{" + ", ".join(sorted(effects.labels)) + "}"


def build_function_type(param_types: list[Type], ret: Type, effects: Effect) -> tuple[Type, Effect]:
    if not param_types:
        return ret, effects
    typ = ret
    call_effects = effects
    for param in reversed(param_types):
        typ = TFunc(param, typ, call_effects)
        call_effects = PURE_EFFECT
    return typ, PURE_EFFECT


def ftv_env(env: dict[str, Scheme]) -> set[str]:
    out: set[str] = set()
    for scheme in env.values():
        out |= ftv_scheme(scheme)
    return out


def bind_var(state: InferState, name: str, typ: Type) -> None:
    typ = apply(state.subst, typ, state.effect_subst)
    if typ == TVar(name):
        return
    if name in ftv(typ):
        names = [name]
        seen = {name}
        _collect_type_var_names_in_order(typ, names, seen)
        mapping = _display_type_var_mapping(names)
        raise tc_error(
            f"Occurs check failed: {mapping[name]} appears in {type_to_string(typ, mapping)}"
        )
    state.subst[name] = typ


def bind_effect_var(state: InferState, name: str, effect: Effect) -> None:
    effect = apply_effect(state.effect_subst, effect)
    if effect == EVar(name):
        return
    if name in ftv_effect(effect):
        raise tc_error(f"Occurs check failed: effect variable {name} appears in {effects_to_string(effect).strip()}")
    state.effect_subst[name] = effect


def unify_effects(state: InferState, left: Effect, right: Effect) -> None:
    left = apply_effect(state.effect_subst, left)
    right = apply_effect(state.effect_subst, right)
    if left == right:
        return
    if isinstance(left, EVar):
        bind_effect_var(state, left.name, right)
        return
    if isinstance(right, EVar):
        bind_effect_var(state, right.name, left)
        return
    raise TypeCheckError(
        f"Effect mismatch: {effects_to_string(left).strip()} vs {effects_to_string(right).strip()}"
    )


def ensure_effect_allowed(state: InferState, actual: Effect, declared: Effect) -> None:
    actual = apply_effect(state.effect_subst, actual)
    declared = apply_effect(state.effect_subst, declared)
    if actual == PURE_EFFECT:
        return
    if declared == PURE_EFFECT:
        if isinstance(actual, EClosed) and "IO" in actual.labels:
            raise TypeCheckError(
                "This function is declared pure, but this call requires !{IO}. Add !{IO} to the function signature."
            )
        if isinstance(actual, EVar):
            raise TypeCheckError(
                "This helper calls an effect-polymorphic argument, so its type must also carry !{e}."
            )
    unify_effects(state, actual, declared)


def merge_effects(state: InferState, left: Effect, right: Effect) -> Effect:
    left = apply_effect(state.effect_subst, left)
    right = apply_effect(state.effect_subst, right)
    if left == PURE_EFFECT:
        return right
    if right == PURE_EFFECT:
        return left
    if left == right:
        return left
    if isinstance(left, EClosed) and isinstance(right, EClosed):
        return EClosed(left.labels | right.labels)
    raise TypeCheckError(
        "Only singleton closed effects or a single shared effect variable are supported in this milestone"
    )


def unify(state: InferState, left: Type, right: Type) -> None:
    left = apply(state.subst, left, state.effect_subst)
    right = apply(state.subst, right, state.effect_subst)

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
        unify_effects(state, left.effects, right.effects)
        return

    if isinstance(left, TApp) and isinstance(right, TApp):
        unify(state, left.base, right.base)
        unify(state, left.arg, right.arg)
        return
    if isinstance(left, TTuple) and isinstance(right, TTuple):
        if len(left.items) != len(right.items):
            raise TypeCheckError(
                f"Tuple arity mismatch: {len(left.items)} vs {len(right.items)}"
            )
        for left_item, right_item in zip(left.items, right.items):
            unify(state, left_item, right_item)
        return

    names: list[str] = []
    seen: set[str] = set()
    _collect_type_var_names_in_order(left, names, seen)
    _collect_type_var_names_in_order(right, names, seen)
    mapping = _display_type_var_mapping(names)
    raise TypeCheckError(f"Type mismatch: {type_to_string(left, mapping)} vs {type_to_string(right, mapping)}")


def instantiate_scheme(state: InferState, scheme: Scheme) -> tuple[Type, Effect]:
    repl: dict[str, Type] = {v: state.fresh() for v in scheme.vars}
    effect_repl: dict[str, Effect] = {v: state.fresh_effect() for v in scheme.effect_vars}

    def go(typ: Type) -> Type:
        if isinstance(typ, TVar) and typ.name in repl:
            return repl[typ.name]
        if isinstance(typ, TFunc):
            return TFunc(go(typ.arg), go(typ.ret), go_effect(typ.effects))
        if isinstance(typ, TApp):
            return TApp(go(typ.base), go(typ.arg))
        if isinstance(typ, TTuple):
            return TTuple(tuple(go(item) for item in typ.items))
        return typ

    def go_effect(effect: Effect) -> Effect:
        effect = apply_effect(state.effect_subst, effect)
        if isinstance(effect, EVar) and effect.name in effect_repl:
            return effect_repl[effect.name]
        return effect

    return go(scheme.type), go_effect(scheme.effects)


def instantiate(state: InferState, scheme: Scheme) -> Type:
    return instantiate_scheme(state, scheme)[0]


def generalize(
    env: dict[str, Scheme],
    typ: Type,
    state: InferState,
    effects: Effect = PURE_EFFECT,
) -> Scheme:
    resolved = apply(state.subst, typ, state.effect_subst)
    resolved_effects = apply_effect(state.effect_subst, effects)
    vars_ = tuple(sorted(ftv(resolved) - ftv_env(env)))
    effect_vars = tuple(sorted((ftv_type_effects(resolved) | ftv_effect(resolved_effects)) - ftv_env(env)))
    return Scheme(vars=vars_, effect_vars=effect_vars, type=resolved, effects=resolved_effects)


def type_to_string(
    typ: Type,
    type_var_names: dict[str, str] | None = None,
    effect_var_names: dict[str, str] | None = None,
) -> str:
    typ = apply({}, typ)
    if isinstance(typ, TVar):
        return (type_var_names or {}).get(typ.name, typ.name)
    if isinstance(typ, TConst):
        return typ.name
    if isinstance(typ, TApp):
        return f"{type_to_string(typ.base, type_var_names, effect_var_names)} {type_to_string(typ.arg, type_var_names, effect_var_names)}"
    if isinstance(typ, TTuple):
        return "(" + ", ".join(type_to_string(item, type_var_names, effect_var_names) for item in typ.items) + ")"
    if isinstance(typ, TFunc):
        left = type_to_string(typ.arg, type_var_names, effect_var_names)
        right = type_to_string(typ.ret, type_var_names, effect_var_names)
        if isinstance(typ.arg, TFunc):
            left = f"({left})"
        return f"{left} -> {right}{effects_to_string(typ.effects, effect_var_names)}"
    return repr(typ)


def scheme_to_string(
    scheme: Scheme,
    subst: dict[str, Type],
    effect_subst: dict[str, Effect] | None = None,
) -> str:
    masked = {k: v for k, v in subst.items() if k not in set(scheme.vars)}
    solved = apply(masked, scheme.type, effect_subst)
    solved_effects = apply_effect(effect_subst or {}, scheme.effects)
    ordered_quantified: list[str] = []
    _collect_type_var_names_in_order(solved, ordered_quantified, set())
    ordered_quantified = [name for name in ordered_quantified if name in set(scheme.vars)]
    for name in scheme.vars:
        if name not in ordered_quantified:
            ordered_quantified.append(name)
    mapping = _display_type_var_mapping(ordered_quantified)
    remaining: list[str] = []
    _collect_type_var_names_in_order(solved, remaining, set(mapping))
    if remaining:
        offset = len(mapping)
        mapping.update({name: _friendly_type_var_name(offset + idx) for idx, name in enumerate(remaining)})
    effect_mapping = {name: f"e{idx}" for idx, name in enumerate(scheme.effect_vars)}
    txt = type_to_string(solved, mapping, effect_mapping) + effects_to_string(solved_effects, effect_mapping)
    quantified = [mapping[name] for name in ordered_quantified] + [effect_mapping[name] for name in scheme.effect_vars]
    if quantified:
        return f"forall {' '.join(quantified)}. {txt}"
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
            effect_from_names(node.effects),
        )
    if isinstance(node, ast.TypeEffect):
        return parse_type_expr(node.base, local_vars, allow_implicit_type_vars, state)
    if isinstance(node, ast.TupleType):
        return TTuple(
            tuple(parse_type_expr(item, local_vars, allow_implicit_type_vars, state) for item in node.items)
        )
    raise TypeCheckError(f"Unsupported type expression {node}")


def parse_annotated_type_expr(
    node: ast.TypeExpr,
    local_vars: dict[str, TVar] | None = None,
    allow_implicit_type_vars: bool = False,
    state: InferState | None = None,
) -> tuple[Type, Effect]:
    if isinstance(node, ast.TypeEffect):
        return (
            parse_type_expr(node.base, local_vars, allow_implicit_type_vars, state),
            effect_from_names(node.effects),
        )
    return parse_type_expr(node, local_vars, allow_implicit_type_vars, state), PURE_EFFECT


def param_annotation_effects(param: ast.Param) -> Effect:
    if isinstance(param.type_expr, ast.TypeEffect):
        return effect_from_names(param.type_expr.effects)
    return PURE_EFFECT


def fn_type_from_decl(decl: ast.FnDecl, state: InferState) -> tuple[Type, Effect]:
    local_vars: dict[str, TVar] = {}
    param_types = []
    for param in decl.params:
        if param.type_expr is None:
            param_types.append(state.fresh())
        else:
            param_type, _ = parse_annotated_type_expr(
                param.type_expr, local_vars, allow_implicit_type_vars=True, state=state
            )
            param_types.append(param_type)
    ret = (
        parse_type_expr(decl.return_type, local_vars, allow_implicit_type_vars=True, state=state)
        if decl.return_type
        else state.fresh()
    )
    return build_function_type(param_types, ret, effect_from_names(decl.effects))


def method_type_from_parts(
    params: list[ast.Param],
    return_type: ast.TypeExpr | None,
    effects: tuple[str, ...] | None,
    local_vars: dict[str, TVar],
) -> MethodTypeInfo:
    param_types = []
    for param in params:
        if param.type_expr is None:
            param_types.append(TVar(f"missing.{param.name}"))
        else:
            param_type, _ = parse_annotated_type_expr(
                param.type_expr, local_vars, allow_implicit_type_vars=True
            )
            param_types.append(param_type)
    ret = parse_type_expr(return_type, local_vars, allow_implicit_type_vars=True) if return_type else UNIT
    typ, zero_arg_effects = build_function_type(param_types, ret, effect_from_names(effects))
    effect_vars = tuple(sorted(ftv_type_effects(typ) | ftv_effect(zero_arg_effects)))
    return MethodTypeInfo(type=typ, effect_vars=effect_vars, effects=zero_arg_effects)


def _apply_inferred_fn_signature(
    params: list[ast.Param],
    return_type: ast.TypeExpr | None,
    solved_type: Type,
) -> ast.TypeExpr:
    cursor = solved_type
    for param in params:
        cursor = apply({}, cursor)
        if not isinstance(cursor, TFunc):
            raise TypeCheckError("Internal error while applying inferred parameter types")
        if param.type_expr is None:
            param.type_expr = type_to_ast_expr(cursor.arg)
        cursor = cursor.ret
    cursor = apply({}, cursor)
    inferred_return = type_to_ast_expr(cursor)
    return return_type if return_type is not None else inferred_return


def _apply_inferred_lambda_signature(params: list[ast.Param], solved_type: Type) -> None:
    cursor = solved_type
    for param in params:
        cursor = apply({}, cursor)
        if not isinstance(cursor, TFunc):
            raise TypeCheckError("Internal error while applying inferred lambda parameter types")
        if param.type_expr is None:
            param.type_expr = type_to_ast_expr(cursor.arg)
        cursor = cursor.ret


def _type_expr_key(node: ast.TypeExpr) -> tuple:
    if isinstance(node, ast.TypeName):
        return ("name", node.name)
    if isinstance(node, ast.TypeApply):
        return ("apply", _type_expr_key(node.base), _type_expr_key(node.arg))
    if isinstance(node, ast.TypeArrow):
        return ("arrow", _type_expr_key(node.left), _type_expr_key(node.right), node.effects)
    if isinstance(node, ast.TypeEffect):
        return ("effect", _type_expr_key(node.base), node.effects)
    if isinstance(node, ast.TupleType):
        return ("tuple", tuple(_type_expr_key(item) for item in node.items))
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
        method_types: dict[str, MethodTypeInfo] = {}
        class_local_vars: dict[str, TVar] = {
            p: TVar(f"class.{decl.name}.{p}") for p in decl.type_params
        }
        for method in decl.methods:
            if method.name in method_types:
                raise tc_error(f"Duplicate method {method.name} in class {decl.name}", method)
            method_types[method.name] = method_type_from_parts(
                method.params,
                method.return_type,
                method.effects,
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
        return TFunc(substitute_type_vars(typ.arg, subst), substitute_type_vars(typ.ret, subst), typ.effects)
    if isinstance(typ, TApp):
        return TApp(substitute_type_vars(typ.base, subst), substitute_type_vars(typ.arg, subst))
    if isinstance(typ, TTuple):
        return TTuple(tuple(substitute_type_vars(item, subst) for item in typ.items))
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
        effect = apply_effect({}, typ.effects)
        if isinstance(effect, EVar):
            effects = (effect.name,)
        else:
            effects = tuple(sorted(effect.labels)) or None
        return ast.TypeArrow(left=type_to_ast_expr(typ.arg), right=type_to_ast_expr(typ.ret), effects=effects)
    if isinstance(typ, TTuple):
        return ast.TupleType(items=[type_to_ast_expr(item) for item in typ.items])
    raise TypeCheckError(f"Unsupported type for AST conversion: {typ}")


def _mark_expr_type(expr: ast.Expr, typ: Type) -> Type:
    setattr(expr, "_inferred_type_raw", typ)
    return typ


def _finalize_inferred_expr_types(program: ast.Program, state: InferState) -> None:
    def visit_expr(expr: ast.Expr) -> None:
        raw = getattr(expr, "_inferred_type_raw", None)
        if raw is not None:
            solved = apply(state.subst, raw, state.effect_subst)
            setattr(expr, "inferred_type", type_to_ast_expr(solved))
            lambda_expr = getattr(ast, "LambdaExpr", None)
            if lambda_expr is not None and isinstance(expr, lambda_expr):
                _apply_inferred_lambda_signature(expr.params, solved)
        if isinstance(expr, ast.IfExpr):
            visit_expr(expr.condition)
            visit_expr(expr.then_branch)
            visit_expr(expr.else_branch)
            return
        if isinstance(expr, ast.TupleExpr):
            for item in expr.items:
                visit_expr(item)
            return
        if isinstance(expr, ast.RecordExpr):
            for field in expr.fields:
                visit_expr(field.value)
            return
        if isinstance(expr, ast.GetFieldExpr):
            visit_expr(expr.record)
            return
        if isinstance(expr, ast.MatchExpr):
            visit_expr(expr.scrutinee)
            for branch in expr.branches:
                visit_expr(branch.value)
            return
        do_expr = getattr(ast, "DoExpr", None)
        do_bind_step = getattr(ast, "DoBindStep", None)
        do_expr_step = getattr(ast, "DoExprStep", None)
        if do_expr is not None and isinstance(expr, do_expr):
            for step in expr.steps:
                if do_bind_step is not None and isinstance(step, do_bind_step):
                    visit_expr(step.value)
                elif do_expr_step is not None and isinstance(step, do_expr_step):
                    visit_expr(step.value)
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
        lambda_expr = getattr(ast, "LambdaExpr", None)
        if lambda_expr is not None and isinstance(expr, lambda_expr):
            visit_expr(expr.body)
            return

    for decl in program.declarations:
        if isinstance(decl, ast.FnDecl):
            visit_expr(decl.body)
        elif isinstance(decl, ast.LetDecl):
            visit_expr(decl.value)
        elif isinstance(decl, ast.InstanceDecl):
            for method in decl.methods:
                visit_expr(method.body)


def _type_const_matches(typ: Type, name: str, *, allow_qualified_leaf_match: bool = True) -> bool:
    resolved = apply({}, typ)
    if not isinstance(resolved, TConst):
        return False
    if resolved.name == name:
        return True
    if not allow_qualified_leaf_match:
        return False
    return resolved.name.rsplit(".", 1)[-1] == name.rsplit(".", 1)[-1]


def _do_sequence_info(state: InferState, typ: Type) -> DoSequenceInfo | None:
    resolved = apply(state.subst, typ, state.effect_subst)
    if isinstance(resolved, TApp) and _type_const_matches(resolved.base, "Maybe", allow_qualified_leaf_match=False):
        return DoSequenceInfo(family="Maybe", payload_type=resolved.arg)
    if (
        isinstance(resolved, TApp)
        and isinstance(resolved.base, TApp)
        and _type_const_matches(resolved.base.base, "Result", allow_qualified_leaf_match=False)
    ):
        return DoSequenceInfo(
            family="Result",
            error_type=resolved.base.arg,
            payload_type=resolved.arg,
        )
    return None


def _effect_includes_io(state: InferState, effect: Effect) -> bool:
    resolved = apply_effect(state.effect_subst, effect)
    return isinstance(resolved, EClosed) and "IO" in resolved.labels


def build_global_method_info(class_decls: dict[str, ClassDeclInfo]) -> dict[str, GlobalMethodInfo]:
    methods: dict[str, GlobalMethodInfo | None] = {}
    for class_name, class_info in class_decls.items():
        for method_name, method_info in class_info.methods.items():
            if method_name in methods:
                methods[method_name] = None
                continue
            methods[method_name] = GlobalMethodInfo(
                class_name=class_name,
                class_info=class_info,
                method_type=method_info.type,
                effect_vars=method_info.effect_vars,
                effects=method_info.effects,
            )
    return {name: info for name, info in methods.items() if info is not None}


def instantiate_global_method(
    state: InferState,
    method_info: GlobalMethodInfo,
) -> tuple[Type, list[Type], Effect]:
    replacements = {name: state.fresh() for name in ftv(method_info.method_type)}
    effect_replacements = {name: state.fresh_effect() for name in method_info.effect_vars}
    direct_method_args = [replacements[name] for name in method_info.class_info.type_param_vars]

    def go(typ: Type) -> Type:
        if isinstance(typ, TVar) and typ.name in replacements:
            return replacements[typ.name]
        if isinstance(typ, TFunc):
            return TFunc(go(typ.arg), go(typ.ret), go_effect(typ.effects))
        if isinstance(typ, TApp):
            return TApp(go(typ.base), go(typ.arg))
        if isinstance(typ, TTuple):
            return TTuple(tuple(go(item) for item in typ.items))
        return typ

    def go_effect(effect: Effect) -> Effect:
        effect = apply_effect(state.effect_subst, effect)
        if isinstance(effect, EVar) and effect.name in effect_replacements:
            return effect_replacements[effect.name]
        return effect

    return go(method_info.method_type), direct_method_args, go_effect(method_info.effects)


def validate_instance_methods(
    program: ast.Program,
    class_decls: dict[str, ClassDeclInfo],
    env: dict[str, Scheme],
    state: InferState,
    type_decls: dict[str, TypeDeclInfo],
    global_methods: dict[str, GlobalMethodInfo],
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
            expected_info = class_info.methods[method_name]
            expected_type = substitute_type_vars(expected_info.type, class_subst)
            expected_effects = expected_info.effects
            actual_type, actual_effects = fn_type_from_decl(
                ast.FnDecl(
                    name=impl.name,
                    params=impl.params,
                    return_type=impl.return_type,
                    effects=impl.effects,
                    constraints=[],
                    body=impl.body,
                ),
                state,
            )

            local_state = InferState()
            unify_at(local_state, expected_type, actual_type, impl)

            working_env = dict(env)
            cursor = apply(state.subst, actual_type, state.effect_subst)
            for param in impl.params:
                cursor = apply(state.subst, cursor, state.effect_subst)
                if not isinstance(cursor, TFunc):
                    raise tc_error("Internal error for instance method params", impl)
                working_env[param.name] = Scheme(vars=(), type=cursor.arg, effects=param_annotation_effects(param))
                cursor = cursor.ret
            body_t, body_effects = infer_expr(impl.body, working_env, state, type_decls, global_methods)
            expected_return = apply(state.subst, cursor, state.effect_subst)
            unify_at(state, body_t, expected_return, impl.body)
            try:
                ensure_effect_allowed(state, body_effects, expected_effects)
            except TypeCheckError as exc:
                raise tc_error(
                    f"Instance method {impl.name} requires undeclared effects: {exc}",
                    impl.body,
                ) from exc
            if apply_effect(state.effect_subst, actual_effects) != apply_effect(state.effect_subst, expected_effects):
                raise tc_error(
                    f"Instance method {impl.name} has effects{effects_to_string(actual_effects)} but class requires"
                    f"{effects_to_string(expected_effects)}",
                    impl,
                )
            solved_method = apply(state.subst, actual_type, state.effect_subst)
            impl.return_type = _apply_inferred_fn_signature(impl.params, impl.return_type, solved_method)


def infer_expr(
    expr: ast.Expr,
    env: dict[str, Scheme],
    state: InferState,
    type_decls: dict[str, TypeDeclInfo],
    global_methods: dict[str, GlobalMethodInfo],
) -> tuple[Type, Effect]:
    if isinstance(expr, ast.IntExpr):
        return _mark_expr_type(expr, INT), PURE_EFFECT
    if isinstance(expr, ast.BoolExpr):
        return _mark_expr_type(expr, BOOL), PURE_EFFECT
    if isinstance(expr, ast.StringExpr):
        return _mark_expr_type(expr, STRING), PURE_EFFECT
    if isinstance(expr, ast.CharExpr):
        return _mark_expr_type(expr, CHAR), PURE_EFFECT
    if isinstance(expr, ast.UnitExpr):
        return _mark_expr_type(expr, UNIT), PURE_EFFECT
    if isinstance(expr, ast.IntRangeExpr):
        start_t, start_effects = infer_expr(expr.start, env, state, type_decls, global_methods)
        end_t, end_effects = infer_expr(expr.end, env, state, type_decls, global_methods)
        unify_at(state, start_t, INT, expr.start)
        unify_at(state, end_t, INT, expr.end)
        return _mark_expr_type(expr, INT_RANGE), merge_effects(state, start_effects, end_effects)
    if isinstance(expr, ast.TupleExpr):
        item_results = [infer_expr(item, env, state, type_decls, global_methods) for item in expr.items]
        item_types = [typ for typ, _ in item_results]
        effects = PURE_EFFECT
        for _, item_effect in item_results:
            effects = merge_effects(state, effects, item_effect)
        return _mark_expr_type(expr, TTuple(tuple(item_types))), effects
    if isinstance(expr, ast.RecordExpr):
        info = type_decls.get(expr.type_name)
        if info is None or not info.fields:
            raise tc_error(f"Unknown record type {expr.type_name}", expr)
        field_replacements = {param: state.fresh() for param in info.params}

        def instantiate_record_field(field_t: Type) -> Type:
            return substitute_type_vars(field_t, {f"{expr.type_name}.{name}": repl for name, repl in field_replacements.items()})

        expected_field_names = set(info.fields)
        provided_field_names = {field.name for field in expr.fields}
        missing = sorted(expected_field_names - provided_field_names)
        extra = sorted(provided_field_names - expected_field_names)
        if missing:
            raise tc_error(f"Missing record field(s): {', '.join(missing)}", expr)
        if extra:
            raise tc_error(f"Unknown record field(s): {', '.join(extra)}", expr)
        effects = PURE_EFFECT
        for field in expr.fields:
            value_t, value_effects = infer_expr(field.value, env, state, type_decls, global_methods)
            unify_at(state, value_t, instantiate_record_field(info.fields[field.name]), field.value)
            effects = merge_effects(state, effects, value_effects)
        record_t: Type = TConst(expr.type_name)
        for param in info.params:
            record_t = TApp(record_t, field_replacements[param])
        return _mark_expr_type(expr, record_t), effects
    if isinstance(expr, ast.GetFieldExpr):
        record_t, record_effects = infer_expr(expr.record, env, state, type_decls, global_methods)
        resolved_record_t = apply(state.subst, record_t, state.effect_subst)
        record_name: str | None = None
        record_args: list[Type] = []
        if isinstance(resolved_record_t, TConst):
            record_name = resolved_record_t.name
        elif isinstance(resolved_record_t, TApp):
            cursor: Type = resolved_record_t
            args_reversed: list[Type] = []
            while isinstance(cursor, TApp):
                args_reversed.append(cursor.arg)
                cursor = cursor.base
            base = cursor
            record_args = list(reversed(args_reversed))
            while isinstance(base, TApp):
                base = base.base
            if isinstance(base, TConst):
                record_name = base.name
        if record_name is None:
            raise tc_error(f"get expects a record value, got {type_to_string(resolved_record_t)}", expr.record)
        info = type_decls.get(record_name)
        if info is None or not info.fields:
            raise tc_error(f"get expects a record value, got {type_to_string(resolved_record_t)}", expr.record)
        field_t = info.fields.get(expr.field_name)
        if field_t is None:
            raise tc_error(f"Record {record_name} has no field {expr.field_name}", expr)
        field_subst = {
            f"{record_name}.{param}": arg_t
            for param, arg_t in zip(info.params, record_args)
        }
        return _mark_expr_type(expr, substitute_type_vars(field_t, field_subst)), record_effects
    if isinstance(expr, ast.VarExpr):
        scheme = env.get(expr.name)
        if scheme is not None:
            inst_type, inst_effects = instantiate_scheme(state, scheme)
            return _mark_expr_type(expr, inst_type), inst_effects
        method_info = global_methods.get(expr.name)
        if method_info is None:
            raise tc_error(f"Unknown variable {expr.name}", expr)
        method_type, _, method_effects = instantiate_global_method(state, method_info)
        return _mark_expr_type(expr, method_type), method_effects
    if isinstance(expr, ast.UnaryExpr):
        operand_t, operand_effects = infer_expr(expr.operand, env, state, type_decls, global_methods)
        if expr.op == "-":
            unify_at(state, operand_t, INT, expr)
            return _mark_expr_type(expr, INT), operand_effects
        raise tc_error(f"Unsupported unary operator {expr.op}", expr)
    if isinstance(expr, ast.BinaryExpr):
        left, left_effects = infer_expr(expr.left, env, state, type_decls, global_methods)
        right, right_effects = infer_expr(expr.right, env, state, type_decls, global_methods)
        if expr.op in {"<<", ">>"}:
            input_t = state.fresh()
            middle_t = state.fresh()
            output_t = state.fresh()
            right_resolved = apply(state.subst, right, state.effect_subst)
            right_call_effects = right_resolved.effects if isinstance(right_resolved, TFunc) else PURE_EFFECT
            left_resolved = apply(state.subst, left, state.effect_subst)
            left_call_effects = left_resolved.effects if isinstance(left_resolved, TFunc) else PURE_EFFECT
            if expr.op == ">>":
                unify_at(state, left, TFunc(input_t, middle_t, left_call_effects), expr.left)
                unify_at(state, right, TFunc(middle_t, output_t, right_call_effects), expr.right)
            else:
                unify_at(state, right, TFunc(input_t, middle_t, right_call_effects), expr.right)
                unify_at(state, left, TFunc(middle_t, output_t, left_call_effects), expr.left)
            return _mark_expr_type(expr, TFunc(input_t, output_t, merge_effects(state, left_call_effects, right_call_effects))), (
                merge_effects(state, left_effects, right_effects)
            )
        if expr.op in {"+", "-", "*", "/"}:
            unify_at(state, left, INT, expr.left)
            unify_at(state, right, INT, expr.right)
            return _mark_expr_type(expr, INT), merge_effects(state, left_effects, right_effects)
        if expr.op in {"<", "<=", ">", ">="}:
            unify_at(state, left, INT, expr.left)
            unify_at(state, right, INT, expr.right)
            return _mark_expr_type(expr, BOOL), merge_effects(state, left_effects, right_effects)
        if expr.op in {"==", "!="}:
            unify_at(state, left, right, expr)
            return _mark_expr_type(expr, BOOL), merge_effects(state, left_effects, right_effects)
        if expr.op in {"&&", "||"}:
            unify_at(state, left, BOOL, expr.left)
            unify_at(state, right, BOOL, expr.right)
            return _mark_expr_type(expr, BOOL), merge_effects(state, left_effects, right_effects)
        raise tc_error(f"Unsupported binary operator {expr.op}", expr)
    if isinstance(expr, ast.CallExpr):
        direct_method_info: GlobalMethodInfo | None = None
        direct_method_args: list[Type] = []
        if isinstance(expr.callee, ast.VarExpr) and expr.callee.name not in env:
            direct_method_info = global_methods.get(expr.callee.name)
        if direct_method_info is not None:
            callee, direct_method_args, callee_effects = instantiate_global_method(state, direct_method_info)
        else:
            callee, callee_effects = infer_expr(expr.callee, env, state, type_decls, global_methods)
        typ = callee
        call_effects = callee_effects
        for arg_expr in expr.args:
            arg_t, arg_effects = infer_expr(arg_expr, env, state, type_decls, global_methods)
            resolved_typ = apply(state.subst, typ, state.effect_subst)
            expected_arg_t = resolved_typ.arg if isinstance(resolved_typ, TFunc) else None
            expected_effects = resolved_typ.effects if isinstance(resolved_typ, TFunc) else PURE_EFFECT
            next_t = state.fresh()
            try:
                unify_at(state, typ, TFunc(arg_t, next_t, expected_effects), arg_expr)
            except TypeCheckError as exc:
                if expected_arg_t is not None:
                    names: list[str] = []
                    seen: set[str] = set()
                    _collect_type_var_names_in_order(expected_arg_t, names, seen)
                    _collect_type_var_names_in_order(arg_t, names, seen)
                    mapping = _display_type_var_mapping(names)
                    raise tc_error(
                        "Argument type mismatch: "
                        f"expected {type_to_string(expected_arg_t, mapping)}, "
                        f"got {type_to_string(arg_t, mapping)}",
                        arg_expr,
                    ) from exc
                raise
            call_effects = merge_effects(state, call_effects, arg_effects)
            call_effects = merge_effects(state, call_effects, expected_effects)
            typ = next_t
        if direct_method_info is not None:
            resolved_args = [apply(state.subst, arg, state.effect_subst) for arg in direct_method_args]
            setattr(
                expr,
                "resolved_constraint",
                ast.TypeConstraint(
                    class_name=direct_method_info.class_name,
                    args=[type_to_ast_expr(arg) for arg in resolved_args],
                ),
            )
        return _mark_expr_type(expr, typ), call_effects
    if isinstance(expr, ast.IfExpr):
        cond, cond_effects = infer_expr(expr.condition, env, state, type_decls, global_methods)
        unify_at(state, cond, BOOL, expr.condition)
        then_t, then_effects = infer_expr(expr.then_branch, env, state, type_decls, global_methods)
        else_t, else_effects = infer_expr(expr.else_branch, env, state, type_decls, global_methods)
        unify_at(state, then_t, else_t, expr)
        return _mark_expr_type(expr, then_t), merge_effects(
            state, merge_effects(state, cond_effects, then_effects), else_effects
        )
    if isinstance(expr, ast.MatchExpr):
        scrutinee_t, scrutinee_effects = infer_expr(expr.scrutinee, env, state, type_decls, global_methods)
        out_t = state.fresh()
        out_effects = scrutinee_effects
        branch_ctors: list[str] = []
        covered_ctors: set[str] = set()
        has_catchall = False
        seen_literals: set[tuple[str, object]] = set()
        reachable_adt_ctors = _resolved_match_adt_constructors(scrutinee_t, state, type_decls)

        for branch in expr.branches:
            branch_env = dict(env)
            ctor_name = infer_pattern(
                branch.pattern,
                scrutinee_t,
                branch_env,
                state,
                type_decls,
            )
            literal_key = _top_level_literal_key(branch.pattern)
            if has_catchall:
                raise tc_error("Unreachable match branch", branch.pattern)
            if literal_key is not None and literal_key in seen_literals:
                raise tc_error(f"Unreachable match branch for literal {_format_literal_key(literal_key)}", branch.pattern)
            if ctor_name is not None and ctor_name in covered_ctors:
                raise tc_error(f"Unreachable match branch for constructor {ctor_name}", branch.pattern)
            if reachable_adt_ctors is not None and not reachable_adt_ctors:
                raise tc_error("Unreachable match branch", branch.pattern)
            if ctor_name is not None:
                branch_ctors.append(ctor_name)
                if _constructor_pattern_covers_all(branch.pattern):
                    covered_ctors.add(ctor_name)
                if reachable_adt_ctors is not None and _constructor_pattern_covers_all(branch.pattern):
                    reachable_adt_ctors.discard(ctor_name)
            if literal_key is not None:
                seen_literals.add(literal_key)
            if isinstance(branch.pattern, (ast.WildcardPattern, ast.VarPattern)):
                has_catchall = True
            value_t, value_effects = infer_expr(branch.value, branch_env, state, type_decls, global_methods)
            unify_at(state, out_t, value_t, branch.value)
            out_effects = merge_effects(state, out_effects, value_effects)

        ensure_exhaustive_match(scrutinee_t, branch_ctors, has_catchall, state, type_decls, expr)
        return _mark_expr_type(expr, out_t), out_effects
    do_expr = getattr(ast, "DoExpr", None)
    do_bind_step = getattr(ast, "DoBindStep", None)
    do_let_step = getattr(ast, "DoLetStep", None)
    do_expr_step = getattr(ast, "DoExprStep", None)
    if do_expr is not None and isinstance(expr, do_expr):
        if not expr.steps:
            raise tc_error("do block must contain at least one step", expr)
        if do_bind_step is None or do_expr_step is None or do_let_step is None:
            raise tc_error("Internal error: do-step nodes are unavailable", expr)

        working_env = dict(env)
        total_effects = PURE_EFFECT
        sequence_family: str | None = None
        sequence_error_type: Type | None = None

        for step in expr.steps[:-1]:
            if isinstance(step, do_let_step):
                step_t, step_effects = infer_expr(step.value, working_env, state, type_decls, global_methods)
                if apply_effect(state.effect_subst, step_effects) != PURE_EFFECT:
                    raise tc_error(
                        "do let bindings must be pure. Move the effectful call to its own step or bind it with <-.",
                        step.value,
                    )
                working_env[step.name] = Scheme(vars=(), type=step_t)
                continue
            if isinstance(step, do_bind_step):
                step_t, step_effects = infer_expr(step.value, working_env, state, type_decls, global_methods)
                total_effects = merge_effects(state, total_effects, step_effects)
                if not _pattern_is_irrefutable_within_value(step.pattern):
                    raise tc_error(
                        "do bind patterns must be irrefutable; use match for constructor or literal checks",
                        step.pattern,
                    )
                info = _do_sequence_info(state, step_t)
                if info is None:
                    if not _effect_includes_io(state, step_effects):
                        raise tc_error(
                            "This do bind must unwrap a Maybe/Result value, or require !{IO}. Use let for pure values, or match if you need the whole container.",
                            step.value,
                        )
                    setattr(step, "_do_family", "IO")
                    infer_pattern(step.pattern, step_t, working_env, state, type_decls)
                    continue
                if sequence_family is None:
                    sequence_family = info.family
                    sequence_error_type = info.error_type
                elif info.family != sequence_family:
                    raise tc_error(
                        f"This do block started with {sequence_family} bindings, but this step returns {info.family}. Keep one short-circuit family per do block.",
                        step.value,
                    )
                elif info.family == "Result" and sequence_error_type is not None and info.error_type is not None:
                    unify_at(state, sequence_error_type, info.error_type, step.value)
                setattr(step, "_do_family", info.family)
                infer_pattern(step.pattern, info.payload_type, working_env, state, type_decls)
                continue
            if isinstance(step, do_expr_step):
                step_t, step_effects = infer_expr(step.value, working_env, state, type_decls, global_methods)
                total_effects = merge_effects(state, total_effects, step_effects)
                if _effect_includes_io(state, step_effects):
                    continue
                if sequence_family is None:
                    raise tc_error(
                        "This non-final do step is pure. Non-final plain expression steps are only allowed when they require !{IO}; use let to name a pure value or make it the final expression.",
                        step.value,
                    )
                raise tc_error(
                    f"This do block already sequences {sequence_family}; a non-final plain expression step still requires !{{IO}}. Wrap the final value in {('Just' if sequence_family == 'Maybe' else 'Ok')}(...) or use let for a pure intermediate value.",
                    step.value,
                )
                continue
            raise tc_error("Unsupported do step", step)

        final_step = expr.steps[-1]
        if not isinstance(final_step, do_expr_step):
            raise tc_error("A do block must end with a final expression", final_step)
        final_t, final_effects = infer_expr(final_step.value, working_env, state, type_decls, global_methods)
        total_effects = merge_effects(state, total_effects, final_effects)
        if sequence_family is not None:
            final_info = _do_sequence_info(state, final_t)
            if final_info is None:
                raise tc_error(
                    f"This do block started with {sequence_family} bindings, so its final expression must also return {sequence_family}. Wrap the final value in {('Just' if sequence_family == 'Maybe' else 'Ok')}(...) or leave do for an explicit match.",
                    final_step.value,
                )
            if final_info.family != sequence_family:
                raise tc_error(
                    f"This do block started with {sequence_family} bindings, but its final expression returns {final_info.family}. Keep one short-circuit family per do block.",
                    final_step.value,
                )
            if sequence_family == "Result" and sequence_error_type is not None and final_info.error_type is not None:
                unify_at(state, sequence_error_type, final_info.error_type, final_step.value)
        return _mark_expr_type(expr, final_t), total_effects
    lambda_expr = getattr(ast, "LambdaExpr", None)
    if lambda_expr is not None and isinstance(expr, lambda_expr):
        if not expr.params:
            raise tc_error("Lambda parameter list cannot be empty", expr)
        local_vars: dict[str, TVar] = {}
        working_env = dict(env)
        param_types: list[Type] = []
        for param in expr.params:
            if param.type_expr is None:
                param_type = state.fresh()
            else:
                param_type = parse_type_expr(
                    param.type_expr,
                    local_vars,
                    allow_implicit_type_vars=True,
                    state=state,
                )
            working_env[param.name] = Scheme(vars=(), type=param_type, effects=param_annotation_effects(param))
            param_types.append(param_type)
        body_t, body_effects = infer_expr(expr.body, working_env, state, type_decls, global_methods)
        lambda_t, _ = build_function_type(param_types, body_t, body_effects)
        return _mark_expr_type(expr, lambda_t), PURE_EFFECT

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
        env[pattern.name] = Scheme(vars=(), type=apply(state.subst, expected_type, state.effect_subst))
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
    if isinstance(pattern, ast.CharPattern):
        unify_at(state, expected_type, CHAR, pattern)
        return None
    if isinstance(pattern, ast.UnitPattern):
        unify_at(state, expected_type, UNIT, pattern)
        return None
    if isinstance(pattern, ast.TuplePattern):
        expected = apply(state.subst, expected_type, state.effect_subst)
        if isinstance(expected, TVar):
            fresh_items = tuple(state.fresh() for _ in pattern.items)
            tuple_type = TTuple(fresh_items)
            unify_at(state, expected, tuple_type, pattern)
            expected = apply(state.subst, tuple_type, state.effect_subst)
        if not isinstance(expected, TTuple):
            raise tc_error(f"Tuple pattern expects tuple type, got {type_to_string(expected)}", pattern)
        if len(expected.items) != len(pattern.items):
            raise tc_error(
                f"Tuple pattern expects {len(expected.items)} items, got {len(pattern.items)}",
                pattern,
            )
        for sub_pattern, item_type in zip(pattern.items, expected.items):
            infer_pattern(sub_pattern, item_type, env, state, type_decls)
        return None
    if isinstance(pattern, ast.ConstructorPattern):
        ctor_scheme = env.get(pattern.name)
        if ctor_scheme is None:
            raise tc_error(f"Unknown constructor {pattern.name}", pattern)

        ctor_t = instantiate(state, ctor_scheme)
        arg_types: list[Type] = []
        current = ctor_t
        while isinstance(apply(state.subst, current, state.effect_subst), TFunc):
            current = apply(state.subst, current, state.effect_subst)
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

    resolved = apply(state.subst, scrutinee_t, state.effect_subst)
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


def _resolved_match_adt_constructors(
    scrutinee_t: Type,
    state: InferState,
    type_decls: dict[str, TypeDeclInfo],
) -> set[str] | None:
    resolved = apply(state.subst, scrutinee_t, state.effect_subst)
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
        return None
    return set(type_decls[adt_name].constructors.keys())


def _top_level_literal_key(pattern: ast.Pattern) -> tuple[str, object] | None:
    if isinstance(pattern, ast.IntPattern):
        return ("int", pattern.value)
    if isinstance(pattern, ast.BoolPattern):
        return ("bool", pattern.value)
    if isinstance(pattern, ast.StringPattern):
        return ("string", pattern.value)
    if isinstance(pattern, ast.CharPattern):
        return ("char", pattern.value)
    if isinstance(pattern, ast.UnitPattern):
        return ("unit", ())
    return None


def _format_literal_key(key: tuple[str, object]) -> str:
    kind, value = key
    if kind == "string":
        return repr(value)
    if kind == "char":
        return repr(value)
    if kind == "bool":
        return "true" if value else "false"
    if kind == "unit":
        return "()"
    return str(value)


def _pattern_is_irrefutable_within_value(pattern: ast.Pattern) -> bool:
    if isinstance(pattern, (ast.WildcardPattern, ast.VarPattern)):
        return True
    if isinstance(pattern, ast.TuplePattern):
        return all(_pattern_is_irrefutable_within_value(item) for item in pattern.items)
    return False


def _constructor_pattern_covers_all(pattern: ast.Pattern) -> bool:
    if not isinstance(pattern, ast.ConstructorPattern):
        return False
    return all(_pattern_is_irrefutable_within_value(arg) for arg in pattern.args)


def build_type_decls(program: ast.Program) -> dict[str, TypeDeclInfo]:
    out: dict[str, TypeDeclInfo] = {}
    for decl in program.declarations:
        if isinstance(decl, ast.TypeDecl):
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
            continue
        if isinstance(decl, ast.RecordDecl):
            if decl.name in out:
                raise TypeCheckError(f"Duplicate type declaration: {decl.name}")
            local_vars = {name: TVar(f"{decl.name}.{name}") for name in decl.type_params}
            fields: dict[str, Type] = {}
            for field_decl in decl.fields:
                if field_decl.name in fields:
                    raise tc_error(f"Duplicate record field {field_decl.name}", field_decl)
                fields[field_decl.name] = parse_type_expr(field_decl.type_expr, local_vars)
            out[decl.name] = TypeDeclInfo(params=decl.type_params, constructors={}, fields=fields)
    return out


def typecheck_program(program: ast.Program) -> dict[str, str]:
    state = InferState()
    type_decls = build_type_decls(program)
    class_decls = build_class_decls(program)
    global_methods = build_global_method_info(class_decls)
    validate_class_constraints(program, class_decls)

    p_var = TVar("prelude.print.a")
    vector_var = TVar("prelude.vector.a")
    sort_key_var = TVar("prelude.vector_sort_by_int.a")
    maybe_type = TConst("Maybe")
    result_type = TConst("Result")
    maybe_vector_var = TApp(maybe_type, vector_var)
    vector_t = TApp(TConst("Vector"), vector_var)
    sort_decorated_t = TApp(
        TConst("List"),
        TTuple((INT, INT, sort_key_var)),
    )
    sort_result_t = TApp(TConst("Vector"), sort_key_var)
    map_var = TVar("prelude.map.a")
    maybe_map_var = TApp(maybe_type, map_var)
    map_t = TApp(TConst("Map"), map_var)

    def builtin_scheme(
        params: list[Type],
        ret: Type,
        *,
        vars: tuple[str, ...] = (),
        effects: Effect = PURE_EFFECT,
    ) -> Scheme:
        typ, value_effects = build_function_type(params, ret, effects)
        return Scheme(vars=vars, type=typ, effects=value_effects)

    env: dict[str, Scheme] = {
        "print": builtin_scheme([p_var], UNIT, vars=(p_var.name,), effects=IO_EFFECT),
        "print_int": builtin_scheme([INT], INT, effects=IO_EFFECT),
        "read_lines": Scheme(
            vars=(),
            type=TFunc(TConst("String"), TApp(TConst("List"), TConst("String")), IO_EFFECT),
        ),
        "read_file": Scheme(
            vars=(),
            type=TFunc(TConst("String"), TConst("String"), IO_EFFECT),
        ),
        "read_int_lines": Scheme(
            vars=(),
            type=TFunc(TConst("String"), TApp(TConst("Vector"), INT), IO_EFFECT),
        ),
        "env_get": Scheme(
            vars=(),
            type=TFunc(TConst("String"), TApp(maybe_type, STRING), IO_EFFECT),
        ),
        "argv_get": Scheme(
            vars=(),
            type=TFunc(INT, TApp(maybe_type, STRING), IO_EFFECT),
        ),
        "parse_int": Scheme(vars=(), type=TFunc(TConst("String"), INT)),
        "char_to_string": Scheme(vars=(), type=TFunc(CHAR, STRING)),
        "split_words": Scheme(
            vars=(),
            type=TFunc(TConst("String"), TApp(TConst("List"), TConst("String"))),
        ),
        "int_range": Scheme(vars=(), type=TFunc(INT, TFunc(INT, INT_RANGE))),
        "int_range_start": Scheme(vars=(), type=TFunc(INT_RANGE, INT)),
        "int_range_end": Scheme(vars=(), type=TFunc(INT_RANGE, INT)),
        "int_to_string": Scheme(vars=(), type=TFunc(INT, STRING)),
        "str_concat": Scheme(vars=(), type=TFunc(STRING, TFunc(STRING, STRING))),
        "str_len": Scheme(vars=(), type=TFunc(STRING, INT)),
        "str_slice": Scheme(vars=(), type=TFunc(STRING, TFunc(INT, TFunc(INT, STRING)))),
        "str_char_at": Scheme(vars=(), type=TFunc(STRING, TFunc(INT, TApp(maybe_type, CHAR)))),
        "str_find": Scheme(vars=(), type=TFunc(STRING, TFunc(STRING, INT))),
        "str_starts_with": Scheme(vars=(), type=TFunc(STRING, TFunc(STRING, BOOL))),
        "str_compare": Scheme(vars=(), type=TFunc(STRING, TFunc(STRING, INT))),
        "regex_validate": Scheme(
            vars=(),
            type=TFunc(STRING, TApp(TApp(result_type, STRING), UNIT)),
        ),
        "regex_is_match": Scheme(vars=(), type=TFunc(STRING, TFunc(STRING, BOOL))),
        "regex_find_range": Scheme(vars=(), type=TFunc(STRING, TFunc(STRING, TApp(maybe_type, INT_RANGE)))),
        "regex_replace_all_literal": Scheme(
            vars=(),
            type=TFunc(STRING, TFunc(STRING, TFunc(STRING, STRING))),
        ),
        "regex_escape": Scheme(vars=(), type=TFunc(STRING, STRING)),
        "bytes_empty": Scheme(vars=(), type=TConst("Bytes")),
        "bytes_length": Scheme(vars=(), type=TFunc(TConst("Bytes"), INT)),
        "bytes_get": Scheme(vars=(), type=TFunc(TConst("Bytes"), TFunc(INT, TApp(maybe_type, INT)))),
        "bytes_slice": Scheme(vars=(), type=TFunc(TConst("Bytes"), TFunc(INT, TFunc(INT, TConst("Bytes"))))),
        "bytes_append": Scheme(vars=(), type=TFunc(TConst("Bytes"), TFunc(TConst("Bytes"), TConst("Bytes")))),
        "bytes_singleton": Scheme(vars=(), type=TFunc(INT, TConst("Bytes"))),
        "bytes_from_utf8": Scheme(vars=(), type=TFunc(STRING, TConst("Bytes"))),
        "bytes_to_utf8": Scheme(
            vars=(),
            type=TFunc(
                TConst("Bytes"),
                TApp(TApp(result_type, TConst("stdlib.bytes.Utf8Error")), STRING),
            ),
        ),
        "bytes_builder_empty": Scheme(vars=(), type=TConst("Builder")),
        "bytes_builder_bytes": Scheme(vars=(), type=TFunc(TConst("Bytes"), TConst("Builder"))),
        "bytes_builder_byte": Scheme(vars=(), type=TFunc(INT, TConst("Builder"))),
        "bytes_builder_u16_be": Scheme(vars=(), type=TFunc(INT, TConst("Builder"))),
        "bytes_builder_u32_be": Scheme(vars=(), type=TFunc(INT, TConst("Builder"))),
        "bytes_builder_append": Scheme(
            vars=(),
            type=TFunc(TConst("Builder"), TFunc(TConst("Builder"), TConst("Builder"))),
        ),
        "bytes_builder_build": Scheme(vars=(), type=TFunc(TConst("Builder"), TConst("Bytes"))),
        "crypto_sha256": Scheme(vars=(), type=TFunc(TConst("Bytes"), TConst("Bytes"))),
        "crypto_hmac_sha256": Scheme(vars=(), type=TFunc(TConst("Bytes"), TFunc(TConst("Bytes"), TConst("Bytes")))),
        "crypto_base64_encode": Scheme(vars=(), type=TFunc(TConst("Bytes"), STRING)),
        "crypto_base64_decode": Scheme(
            vars=(),
            type=TFunc(
                STRING,
                TApp(TApp(result_type, TConst("stdlib.crypto.Base64Error")), TConst("Bytes")),
            ),
        ),
        "crypto_bytes_xor": Scheme(
            vars=(),
            type=TFunc(
                TConst("Bytes"),
                TFunc(
                    TConst("Bytes"),
                    TApp(TApp(result_type, TConst("stdlib.crypto.BytesOpError")), TConst("Bytes")),
                ),
            ),
        ),
        "crypto_random_bytes": builtin_scheme(
            [INT],
            TApp(TApp(result_type, TConst("stdlib.crypto.CryptoError")), TConst("Bytes")),
            effects=IO_EFFECT,
        ),
        "vector_empty": Scheme(vars=(vector_var.name,), type=vector_t),
        "vector_length": Scheme(vars=(vector_var.name,), type=TFunc(vector_t, INT)),
        "vector_get": Scheme(vars=(vector_var.name,), type=TFunc(vector_t, TFunc(INT, maybe_vector_var))),
        "vector_set": Scheme(vars=(vector_var.name,), type=TFunc(vector_t, TFunc(INT, TFunc(vector_var, vector_t)))),
        "vector_append": Scheme(vars=(vector_var.name,), type=TFunc(vector_t, TFunc(vector_var, vector_t))),
        "vector_from_list": Scheme(
            vars=(vector_var.name,),
            type=TFunc(TApp(TConst("List"), vector_var), vector_t),
        ),
        "vector_sort_by_int": Scheme(
            vars=(sort_key_var.name,),
            type=TFunc(sort_decorated_t, sort_result_t),
        ),
        "map_empty": Scheme(vars=(map_var.name,), type=map_t),
        "map_get": Scheme(vars=(map_var.name,), type=TFunc(map_t, TFunc(STRING, maybe_map_var))),
        "map_set": Scheme(vars=(map_var.name,), type=TFunc(map_t, TFunc(STRING, TFunc(map_var, map_t)))),
        "map_remove": Scheme(vars=(map_var.name,), type=TFunc(map_t, TFunc(STRING, map_t))),
        "map_size": Scheme(vars=(map_var.name,), type=TFunc(map_t, INT)),
        "map_nth_key": Scheme(vars=(map_var.name,), type=TFunc(map_t, TFunc(INT, TApp(maybe_type, STRING)))),
        "map_nth_value": Scheme(vars=(map_var.name,), type=TFunc(map_t, TFunc(INT, maybe_map_var))),
        "tcp_listen": builtin_scheme([INT], INT, effects=IO_EFFECT),
        "tcp_accept": builtin_scheme([INT], INT, effects=IO_EFFECT),
        "tcp_read": builtin_scheme([INT], STRING, effects=IO_EFFECT),
        "tcp_write": builtin_scheme([INT, STRING], UNIT, effects=IO_EFFECT),
        "tcp_connect": builtin_scheme(
            [STRING, INT],
            TApp(TApp(result_type, TConst("stdlib.net.TcpError")), INT),
            effects=IO_EFFECT,
        ),
        "tcp_read_exact": builtin_scheme(
            [INT, INT],
            TApp(TApp(result_type, TConst("stdlib.net.TcpError")), TConst("Bytes")),
            effects=IO_EFFECT,
        ),
        "tcp_write_all": builtin_scheme(
            [INT, TConst("Bytes")],
            TApp(TApp(result_type, TConst("stdlib.net.TcpError")), INT),
            effects=IO_EFFECT,
        ),
        "tcp_close": builtin_scheme([INT], UNIT, effects=IO_EFFECT),
        "tcp_close_listener": builtin_scheme([INT], UNIT, effects=IO_EFFECT),
        "tcp_echo_serve": builtin_scheme([INT, INT], UNIT, effects=IO_EFFECT),
        "http_request": builtin_scheme(
            [STRING, STRING, STRING, STRING, INT],
            TApp(TApp(result_type, TConst("stdlib.http.HttpError")), TConst("stdlib.http.HttpResponse")),
            effects=IO_EFFECT,
        ),
        "json_parse": Scheme(
            vars=(),
            type=TFunc(
                STRING,
                TApp(TApp(result_type, TConst("stdlib.json.JsonError")), TConst("stdlib.json.Json")),
            ),
        ),
        "json_stringify": Scheme(vars=(), type=TFunc(TConst("stdlib.json.Json"), STRING)),
        "term_clear": builtin_scheme([], UNIT, effects=IO_EFFECT),
        "term_move": builtin_scheme([INT, INT], UNIT, effects=IO_EFFECT),
        "term_hide_cursor": builtin_scheme([], UNIT, effects=IO_EFFECT),
        "term_show_cursor": builtin_scheme([], UNIT, effects=IO_EFFECT),
        "term_read_key": builtin_scheme([], STRING, effects=IO_EFFECT),
        "term_read_line": builtin_scheme([], TApp(maybe_type, STRING), effects=IO_EFFECT),
        "term_is_interactive": builtin_scheme([], BOOL, effects=IO_EFFECT),
        "repl_add_import": builtin_scheme(
            [STRING],
            TApp(TApp(result_type, STRING), UNIT),
            effects=IO_EFFECT,
        ),
        "repl_add_declaration": builtin_scheme(
            [STRING],
            TApp(TApp(result_type, STRING), UNIT),
            effects=IO_EFFECT,
        ),
        "repl_eval_expr": builtin_scheme(
            [STRING],
            TApp(TApp(result_type, STRING), TApp(TConst("Vec"), STRING)),
            effects=IO_EFFECT,
        ),
        "repl_eval_expr_in_source": builtin_scheme(
            [STRING, STRING],
            TApp(TApp(result_type, STRING), TApp(TConst("Vec"), STRING)),
            effects=IO_EFFECT,
        ),
        "analysis_eval_expr_in_source": builtin_scheme(
            [STRING, STRING],
            TApp(TApp(result_type, STRING), TApp(TConst("Vec"), STRING)),
            effects=IO_EFFECT,
        ),
        "repl_check_source": builtin_scheme(
            [STRING],
            TApp(TApp(result_type, STRING), UNIT),
            effects=IO_EFFECT,
        ),
        "analysis_check_source": builtin_scheme(
            [STRING],
            TApp(TApp(result_type, STRING), UNIT),
            effects=IO_EFFECT,
        ),
        "repl_declared_names_in_source": builtin_scheme(
            [STRING],
            TApp(TApp(result_type, STRING), TApp(TConst("Vec"), STRING)),
            effects=IO_EFFECT,
        ),
        "analysis_declared_names_in_source": builtin_scheme(
            [STRING],
            TApp(TApp(result_type, STRING), TApp(TConst("Vec"), STRING)),
            effects=IO_EFFECT,
        ),
        "repl_exported_names_in_source": builtin_scheme(
            [STRING],
            TApp(TApp(result_type, STRING), TApp(TConst("Vec"), STRING)),
            effects=IO_EFFECT,
        ),
        "analysis_exported_names_in_source": builtin_scheme(
            [STRING],
            TApp(TApp(result_type, STRING), TApp(TConst("Vec"), STRING)),
            effects=IO_EFFECT,
        ),
        "repl_symbol_inventory_in_source": builtin_scheme(
            [STRING],
            TApp(
                TApp(result_type, STRING),
                TTuple(
                    (
                        TApp(TConst("Vec"), STRING),
                        TApp(TConst("Vec"), STRING),
                        TApp(TConst("Vec"), STRING),
                    )
                ),
            ),
            effects=IO_EFFECT,
        ),
        "analysis_symbol_inventory_in_source": builtin_scheme(
            [STRING],
            TApp(
                TApp(result_type, STRING),
                TTuple(
                    (
                        TApp(TConst("Vec"), STRING),
                        TApp(TConst("Vec"), STRING),
                        TApp(TConst("Vec"), STRING),
                    )
                ),
            ),
            effects=IO_EFFECT,
        ),
        "analysis_symbol_locations_in_source": builtin_scheme(
            [STRING],
            TApp(TApp(result_type, STRING), TApp(TConst("Vec"), TTuple((STRING, STRING, INT, INT)))),
            effects=IO_EFFECT,
        ),
        "repl_diagnostics_in_source": builtin_scheme(
            [STRING],
            TApp(TConst("Vec"), TTuple((STRING, INT, INT))),
            effects=IO_EFFECT,
        ),
        "analysis_diagnostics_in_source": builtin_scheme(
            [STRING],
            TApp(TConst("Vec"), TTuple((STRING, INT, INT))),
            effects=IO_EFFECT,
        ),
        "repl_type_of": builtin_scheme(
            [STRING],
            TApp(TApp(result_type, STRING), STRING),
            effects=IO_EFFECT,
        ),
        "repl_type_of_in_source": builtin_scheme(
            [STRING, STRING],
            TApp(TApp(result_type, STRING), STRING),
            effects=IO_EFFECT,
        ),
        "analysis_type_of_in_source": builtin_scheme(
            [STRING, STRING],
            TApp(TApp(result_type, STRING), STRING),
            effects=IO_EFFECT,
        ),
        "repl_instances": builtin_scheme(
            [STRING],
            TApp(TApp(result_type, STRING), TTuple((STRING, TApp(TConst("Vec"), STRING)))),
            effects=IO_EFFECT,
        ),
        "repl_instances_in_source": builtin_scheme(
            [STRING, STRING],
            TApp(TApp(result_type, STRING), TTuple((STRING, TApp(TConst("Vec"), STRING)))),
            effects=IO_EFFECT,
        ),
        "analysis_instances_in_source": builtin_scheme(
            [STRING, STRING],
            TApp(TApp(result_type, STRING), TTuple((STRING, TApp(TConst("Vec"), STRING)))),
            effects=IO_EFFECT,
        ),
        "repl_complete": builtin_scheme(
            [STRING],
            TTuple((STRING, TApp(TConst("Vec"), STRING))),
            effects=IO_EFFECT,
        ),
        "repl_complete_in_state": builtin_scheme(
            [STRING, TApp(TConst("Vec"), STRING), TApp(TConst("Vec"), STRING)],
            TTuple((STRING, TApp(TConst("Vec"), STRING))),
            effects=IO_EFFECT,
        ),
        "analysis_complete_in_state": builtin_scheme(
            [STRING, TApp(TConst("Vec"), STRING), TApp(TConst("Vec"), STRING)],
            TTuple((STRING, TApp(TConst("Vec"), STRING))),
            effects=IO_EFFECT,
        ),
        "repl_reset_session": builtin_scheme([], UNIT, effects=IO_EFFECT),
        "term_write": builtin_scheme([STRING], UNIT, effects=IO_EFFECT),
    }

    for info in type_decls.values():
        for ctor_name, ctor_type in info.constructors.items():
            vars_ = tuple(sorted(ftv(ctor_type)))
            env[ctor_name] = Scheme(vars=vars_, type=ctor_type)

    fn_types: Dict[str, Type] = {}
    fn_decl_effects: Dict[str, Effect] = {}
    for decl in program.declarations:
        if not isinstance(decl, ast.FnDecl):
            continue
        fn_decl = decl
        if fn_decl.name in fn_types:
            raise TypeCheckError(f"Duplicate function {fn_decl.name}")
        if (fn_decl.name == "main" or fn_decl.name.endswith(".main")) and isinstance(
            effect_from_names(fn_decl.effects), EVar
        ):
            raise tc_error("main must not be effect-polymorphic; use a concrete effect such as !{IO}", fn_decl)
        fn_t, fn_effects = fn_type_from_decl(fn_decl, state)
        fn_types[fn_decl.name] = fn_t
        fn_decl_effects[fn_decl.name] = fn_effects
        env[fn_decl.name] = Scheme(vars=(), type=fn_t, effects=fn_effects)

    for decl in program.declarations:
        if isinstance(decl, ast.FnDecl):
            fn_decl = decl
            fn_t = fn_types[fn_decl.name]
            working_env = dict(env)
            cursor = fn_t
            for param in fn_decl.params:
                cursor = apply(state.subst, cursor, state.effect_subst)
                if not isinstance(cursor, TFunc):
                    raise TypeCheckError(f"Internal error for function params in {fn_decl.name}")
                working_env[param.name] = Scheme(vars=(), type=cursor.arg, effects=param_annotation_effects(param))
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
                    resolved_method_type = substitute_type_vars(method_template.type, class_subst)
                    method_scheme = Scheme(
                        vars=tuple(sorted(ftv(resolved_method_type))),
                        effect_vars=method_template.effect_vars,
                        type=resolved_method_type,
                        effects=method_template.effects,
                    )
                    existing = working_env.get(method_name)
                    if existing is not None and scheme_to_string(
                        existing, state.subst, state.effect_subst
                    ) != scheme_to_string(
                        method_scheme, state.subst, state.effect_subst
                    ):
                        raise tc_error(
                            f"Ambiguous method {method_name} from constraints in function {fn_decl.name}",
                            fn_decl,
                        )
                    working_env[method_name] = method_scheme

            body_t, body_effects = infer_expr(fn_decl.body, working_env, state, type_decls, global_methods)
            expected_return = apply(state.subst, cursor, state.effect_subst)
            unify_at(state, body_t, expected_return, fn_decl.body)
            declared_effects = effect_from_names(fn_decl.effects)
            try:
                ensure_effect_allowed(state, body_effects, declared_effects)
            except TypeCheckError as exc:
                raise tc_error(
                    f"Function {fn_decl.name} requires undeclared effects: {exc}",
                    fn_decl.body,
                ) from exc

            solved_fn = apply(state.subst, fn_t, state.effect_subst)
            fn_decl.return_type = _apply_inferred_fn_signature(fn_decl.params, fn_decl.return_type, solved_fn)
            generalized_env = dict(env)
            generalized_env.pop(fn_decl.name, None)
            env[fn_decl.name] = generalize(generalized_env, solved_fn, state, fn_decl_effects[fn_decl.name])

        elif isinstance(decl, ast.LetDecl):
            let_decl = decl
            value_t, value_effects = infer_expr(let_decl.value, env, state, type_decls, global_methods)
            if apply_effect(state.effect_subst, value_effects) != PURE_EFFECT:
                raise tc_error(
                    "Top-level let bindings must not perform effects; move effectful work into a function such as main",
                    let_decl,
                )
            env[let_decl.name] = generalize(env, value_t, state)

    validate_instance_methods(program, class_decls, env, state, type_decls, global_methods)
    _finalize_inferred_expr_types(program, state)

    return {name: scheme_to_string(sch, state.subst, state.effect_subst) for name, sch in env.items()}
