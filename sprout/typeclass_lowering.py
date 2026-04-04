from __future__ import annotations

from . import ast


class TypeclassLoweringError(ValueError):
    pass


def _clone_with_loc(node, src):
    return ast.attach_loc(node, getattr(src, "line", 0), getattr(src, "column", 0))


def _copy_type_expr(node: ast.TypeExpr) -> ast.TypeExpr:
    if isinstance(node, ast.TypeName):
        return ast.TypeName(node.name)
    if isinstance(node, ast.TypeApply):
        return ast.TypeApply(base=_copy_type_expr(node.base), arg=_copy_type_expr(node.arg))
    if isinstance(node, ast.TypeArrow):
        return ast.TypeArrow(left=_copy_type_expr(node.left), right=_copy_type_expr(node.right), effects=node.effects)
    if isinstance(node, ast.TypeEffect):
        return ast.TypeEffect(base=_copy_type_expr(node.base), effects=node.effects)
    if isinstance(node, ast.TupleType):
        return ast.TupleType(items=[_copy_type_expr(item) for item in node.items])
    raise TypeclassLoweringError(f"Unsupported type expression in lowering copy: {node}")


def _type_expr_is_concrete(node: ast.TypeExpr) -> bool:
    if isinstance(node, ast.TypeName):
        leaf = node.name.rsplit(".", 1)[-1]
        return not (leaf and leaf[0].islower())
    if isinstance(node, ast.TypeApply):
        return _type_expr_is_concrete(node.base) and _type_expr_is_concrete(node.arg)
    if isinstance(node, ast.TypeArrow):
        return _type_expr_is_concrete(node.left) and _type_expr_is_concrete(node.right)
    if isinstance(node, ast.TypeEffect):
        return _type_expr_is_concrete(node.base)
    if isinstance(node, ast.TupleType):
        return all(_type_expr_is_concrete(item) for item in node.items)
    return False


def _type_name_matches(left: str, right: str) -> bool:
    if left == right:
        return True
    left_qualified = "." in left
    right_qualified = "." in right
    if left_qualified and right_qualified:
        return False
    return left.rsplit(".", 1)[-1] == right.rsplit(".", 1)[-1]


def _class_name_matches(left: str, right: str) -> bool:
    return _type_name_matches(left, right)


def _lookup_matching_class(mapping: dict[str, object], class_name: str):
    direct = mapping.get(class_name)
    if direct is not None:
        return direct
    for candidate_name, value in mapping.items():
        if _class_name_matches(candidate_name, class_name):
            return value
    return None


def _type_expr_equal(left: ast.TypeExpr, right: ast.TypeExpr) -> bool:
    if isinstance(left, ast.TypeName) and isinstance(right, ast.TypeName):
        return _type_name_matches(left.name, right.name)
    if isinstance(left, ast.TypeApply) and isinstance(right, ast.TypeApply):
        return _type_expr_equal(left.base, right.base) and _type_expr_equal(left.arg, right.arg)
    if isinstance(left, ast.TypeArrow) and isinstance(right, ast.TypeArrow):
        return (
            left.effects == right.effects
            and _type_expr_equal(left.left, right.left)
            and _type_expr_equal(left.right, right.right)
        )
    if isinstance(left, ast.TypeEffect) and isinstance(right, ast.TypeEffect):
        return left.effects == right.effects and _type_expr_equal(left.base, right.base)
    if isinstance(left, ast.TupleType) and isinstance(right, ast.TupleType):
        return len(left.items) == len(right.items) and all(
            _type_expr_equal(left_item, right_item) for left_item, right_item in zip(left.items, right.items)
        )
    return False


def _type_expr_mangle(node: ast.TypeExpr) -> str:
    if isinstance(node, ast.TypeName):
        return node.name.replace(".", "_")
    if isinstance(node, ast.TypeApply):
        return f"{_type_expr_mangle(node.base)}_{_type_expr_mangle(node.arg)}"
    if isinstance(node, ast.TypeArrow):
        effects = ""
        if node.effects:
            effects = "_eff_" + "_".join(node.effects)
        return f"Fn_{_type_expr_mangle(node.left)}_{_type_expr_mangle(node.right)}{effects}"
    if isinstance(node, ast.TypeEffect):
        return f"{_type_expr_mangle(node.base)}_eff_{'_'.join(node.effects)}"
    if isinstance(node, ast.TupleType):
        return "Tuple_" + "_".join(_type_expr_mangle(item) for item in node.items)
    raise TypeclassLoweringError(f"Unsupported type expression in lowering: {node}")


def _constraint_key(constraint: ast.TypeConstraint) -> tuple[str, tuple[str, ...]]:
    return (constraint.class_name, tuple(_type_expr_mangle(arg) for arg in constraint.args))


def _instance_method_name(constraint: ast.TypeConstraint, method_name: str) -> str:
    args = "_".join(_type_expr_mangle(arg) for arg in constraint.args)
    return f"__tc_{constraint.class_name}_{args}_{method_name}"


def _substitute_type_expr(node: ast.TypeExpr, subs: dict[str, ast.TypeExpr]) -> ast.TypeExpr:
    if isinstance(node, ast.TypeName):
        replacement = subs.get(node.name)
        return _copy_type_expr(replacement) if replacement is not None else ast.TypeName(node.name)
    if isinstance(node, ast.TypeApply):
        return ast.TypeApply(
            base=_substitute_type_expr(node.base, subs),
            arg=_substitute_type_expr(node.arg, subs),
        )
    if isinstance(node, ast.TypeArrow):
        return ast.TypeArrow(
            left=_substitute_type_expr(node.left, subs),
            right=_substitute_type_expr(node.right, subs),
            effects=node.effects,
        )
    if isinstance(node, ast.TypeEffect):
        return ast.TypeEffect(base=_substitute_type_expr(node.base, subs), effects=node.effects)
    if isinstance(node, ast.TupleType):
        return ast.TupleType(items=[_substitute_type_expr(item, subs) for item in node.items])
    raise TypeclassLoweringError(f"Unsupported type expression in substitution: {node}")


def _match_type_expr_pattern(
    pattern: ast.TypeExpr,
    candidate: ast.TypeExpr,
    env: dict[str, ast.TypeExpr],
) -> bool:
    if isinstance(pattern, ast.TypeName) and pattern.name and pattern.name.rsplit(".", 1)[-1][0].islower():
        existing = env.get(pattern.name)
        if existing is None:
            env[pattern.name] = candidate
            return True
        return _type_expr_equal(existing, candidate)

    if isinstance(pattern, ast.TypeName) and isinstance(candidate, ast.TypeName):
        return _type_name_matches(pattern.name, candidate.name)
    if isinstance(pattern, ast.TypeApply) and isinstance(candidate, ast.TypeApply):
        return _match_type_expr_pattern(pattern.base, candidate.base, env) and _match_type_expr_pattern(
            pattern.arg, candidate.arg, env
        )
    if isinstance(pattern, ast.TypeArrow) and isinstance(candidate, ast.TypeArrow):
        return (
            pattern.effects == candidate.effects
            and _match_type_expr_pattern(pattern.left, candidate.left, env)
            and _match_type_expr_pattern(pattern.right, candidate.right, env)
        )
    if isinstance(pattern, ast.TypeEffect) and isinstance(candidate, ast.TypeEffect):
        return pattern.effects == candidate.effects and _match_type_expr_pattern(pattern.base, candidate.base, env)
    if isinstance(pattern, ast.TupleType) and isinstance(candidate, ast.TupleType):
        return len(pattern.items) == len(candidate.items) and all(
            _match_type_expr_pattern(pattern_item, candidate_item, env)
            for pattern_item, candidate_item in zip(pattern.items, candidate.items)
        )
    return False


def _constraint_matches_pattern(pattern: ast.TypeConstraint, candidate: ast.TypeConstraint) -> bool:
    if not _class_name_matches(pattern.class_name, candidate.class_name):
        return False
    if len(pattern.args) != len(candidate.args):
        return False
    env: dict[str, ast.TypeExpr] = {}
    for p, c in zip(pattern.args, candidate.args):
        if not _match_type_expr_pattern(p, c, env):
            return False
    return True


def _collect_type_expr_substitution(
    pattern: ast.TypeExpr,
    actual: ast.TypeExpr,
    env: dict[str, ast.TypeExpr],
) -> bool:
    if isinstance(pattern, ast.TypeName) and pattern.name and pattern.name.rsplit(".", 1)[-1][0].islower():
        existing = env.get(pattern.name)
        if existing is None:
            env[pattern.name] = actual
            return True
        return _type_expr_equal(existing, actual)
    if isinstance(pattern, ast.TypeName) and isinstance(actual, ast.TypeName):
        return _type_name_matches(pattern.name, actual.name)
    if isinstance(pattern, ast.TypeApply) and isinstance(actual, ast.TypeApply):
        return _collect_type_expr_substitution(pattern.base, actual.base, env) and _collect_type_expr_substitution(
            pattern.arg,
            actual.arg,
            env,
        )
    if isinstance(pattern, ast.TypeArrow) and isinstance(actual, ast.TypeArrow):
        return _collect_type_expr_substitution(pattern.left, actual.left, env) and _collect_type_expr_substitution(
            pattern.right,
            actual.right,
            env,
        )
    if isinstance(pattern, ast.TupleType) and isinstance(actual, ast.TupleType):
        return len(pattern.items) == len(actual.items) and all(
            _collect_type_expr_substitution(pattern_item, actual_item, env)
            for pattern_item, actual_item in zip(pattern.items, actual.items)
        )
    return False


def _instantiate_constraint(
    constraint: ast.TypeConstraint,
    subs: dict[str, ast.TypeExpr],
) -> ast.TypeConstraint:
    return ast.TypeConstraint(
        class_name=constraint.class_name,
        args=[_substitute_type_expr(arg, subs) for arg in constraint.args],
    )


def _type_expr_outermost_base(node: ast.TypeExpr) -> ast.TypeExpr:
    current = node
    while isinstance(current, ast.TypeApply):
        current = current.base
    return current


def _constraint_resolution_candidates(constraint: ast.TypeConstraint) -> list[ast.TypeConstraint]:
    candidates = [constraint]
    head_args = [_type_expr_outermost_base(arg) for arg in constraint.args]
    head_constraint = ast.TypeConstraint(class_name=constraint.class_name, args=head_args)
    if any(not _type_expr_equal(left, right) for left, right in zip(constraint.args, head_args)):
        candidates.append(head_constraint)
    return candidates


def _concretize_call_constraints(
    expr: ast.CallExpr,
    fn_decl: ast.FnDecl | None,
    constraints: list[ast.TypeConstraint],
) -> list[ast.TypeConstraint]:
    if fn_decl is None or not constraints:
        return constraints

    subs: dict[str, ast.TypeExpr] = {}
    matched_any = False
    for param, arg in zip(fn_decl.params, expr.args):
        if param.type_expr is None:
            continue
        actual = getattr(arg, "inferred_type", None)
        if not isinstance(actual, ast.TypeExpr):
            continue
        if _collect_type_expr_substitution(param.type_expr, actual, subs):
            matched_any = True

    if not matched_any:
        return constraints

    concrete_constraints: list[ast.TypeConstraint] = []
    for constraint in constraints:
        concrete = _instantiate_constraint(constraint, subs)
        if any(
            isinstance(arg, ast.TypeName) and arg.name and arg.name.rsplit(".", 1)[-1][0].islower()
            for arg in concrete.args
        ):
            return constraints
        concrete_constraints.append(concrete)
    return concrete_constraints


def _pattern_bindings(pat: ast.Pattern) -> set[str]:
    if isinstance(pat, ast.VarPattern):
        return {pat.name}
    if isinstance(pat, ast.TuplePattern):
        out: set[str] = set()
        for item in pat.items:
            out |= _pattern_bindings(item)
        return out
    if isinstance(pat, ast.ConstructorPattern):
        out: set[str] = set()
        for arg in pat.args:
            out |= _pattern_bindings(arg)
        return out
    return set()


def _rewrite_expr_with_specialization(
    expr: ast.Expr,
    scope: set[str],
    hidden_count_by_fn: dict[str, int],
    fn_decls_by_name: dict[str, ast.FnDecl],
    specializations: dict[tuple[str, tuple[str, ...]], str],
    generated_wrappers: list[ast.FnDecl],
    taken_names: set[str],
) -> ast.Expr:
    if isinstance(expr, (ast.IntExpr, ast.BoolExpr, ast.StringExpr, ast.CharExpr, ast.VarExpr)):
        return expr
    if isinstance(expr, ast.LambdaExpr):
        return _clone_with_loc(
            ast.LambdaExpr(
                params=expr.params,
                body=_rewrite_expr_with_specialization(
                    expr.body,
                    scope | {param.name for param in expr.params},
                    hidden_count_by_fn,
                    fn_decls_by_name,
                    specializations,
                    generated_wrappers,
                    taken_names,
                ),
            ),
            expr,
        )
    if isinstance(expr, ast.UnaryExpr):
        return _clone_with_loc(
            ast.UnaryExpr(
                op=expr.op,
                operand=_rewrite_expr_with_specialization(
                    expr.operand,
                    scope,
                    hidden_count_by_fn,
                    fn_decls_by_name,
                    specializations,
                    generated_wrappers,
                    taken_names,
                ),
            ),
            expr,
        )
    if isinstance(expr, ast.BinaryExpr):
        return _clone_with_loc(
            ast.BinaryExpr(
                op=expr.op,
                left=_rewrite_expr_with_specialization(
                    expr.left,
                    scope,
                    hidden_count_by_fn,
                    fn_decls_by_name,
                    specializations,
                    generated_wrappers,
                    taken_names,
                ),
                right=_rewrite_expr_with_specialization(
                    expr.right,
                    scope,
                    hidden_count_by_fn,
                    fn_decls_by_name,
                    specializations,
                    generated_wrappers,
                    taken_names,
                ),
            ),
            expr,
        )
    if isinstance(expr, ast.CallExpr):
        callee = _rewrite_expr_with_specialization(
            expr.callee,
            scope,
            hidden_count_by_fn,
            fn_decls_by_name,
            specializations,
            generated_wrappers,
            taken_names,
        )
        args = [
            _rewrite_expr_with_specialization(
                arg,
                scope,
                hidden_count_by_fn,
                fn_decls_by_name,
                specializations,
                generated_wrappers,
                taken_names,
            )
            for arg in expr.args
        ]

        if isinstance(expr.callee, ast.VarExpr) and expr.callee.name not in scope:
            callee_name = expr.callee.name
            hidden_count = hidden_count_by_fn.get(callee_name, 0)
            if hidden_count > 0 and len(args) >= hidden_count:
                extra = args[-hidden_count:]
                if all(
                    isinstance(x, ast.VarExpr)
                    and x.name in fn_decls_by_name
                    and hidden_count_by_fn.get(x.name, 0) == 0
                    for x in extra
                ):
                    extra_names = tuple(x.name for x in extra if isinstance(x, ast.VarExpr))
                    key = (callee_name, extra_names)
                    spec_name = specializations.get(key)
                    if spec_name is None:
                        target = fn_decls_by_name.get(callee_name)
                        if target is None:
                            raise TypeclassLoweringError(f"Cannot specialize unknown function {callee_name}")
                        user_param_count = len(target.params) - hidden_count
                        if user_param_count < 0:
                            raise TypeclassLoweringError(f"Internal specialization error for {callee_name}")
                        wrapper_name = f"__spec_{callee_name}_{len(specializations)}"
                        while wrapper_name in taken_names:
                            wrapper_name += "_"
                        taken_names.add(wrapper_name)
                        specializations[key] = wrapper_name

                        user_params = target.params[:user_param_count]
                        wrapper_call_args = [ast.VarExpr(p.name) for p in user_params] + [
                            ast.VarExpr(name) for name in extra_names
                        ]
                        generated_wrappers.append(
                            _clone_with_loc(
                                ast.FnDecl(
                                    name=wrapper_name,
                                    params=user_params,
                                    return_type=target.return_type,
                                    effects=target.effects,
                                    constraints=[],
                                    body=ast.CallExpr(callee=ast.VarExpr(callee_name), args=wrapper_call_args),
                                ),
                                expr,
                            )
                        )
                        spec_name = wrapper_name

                    return _clone_with_loc(
                        ast.CallExpr(callee=ast.VarExpr(spec_name), args=args[:-hidden_count]),
                        expr,
                    )

        return _clone_with_loc(ast.CallExpr(callee=callee, args=args), expr)
    if isinstance(expr, ast.IfExpr):
        return _clone_with_loc(
            ast.IfExpr(
                condition=_rewrite_expr_with_specialization(
                    expr.condition,
                    scope,
                    hidden_count_by_fn,
                    fn_decls_by_name,
                    specializations,
                    generated_wrappers,
                    taken_names,
                ),
                then_branch=_rewrite_expr_with_specialization(
                    expr.then_branch,
                    scope,
                    hidden_count_by_fn,
                    fn_decls_by_name,
                    specializations,
                    generated_wrappers,
                    taken_names,
                ),
                else_branch=_rewrite_expr_with_specialization(
                    expr.else_branch,
                    scope,
                    hidden_count_by_fn,
                    fn_decls_by_name,
                    specializations,
                    generated_wrappers,
                    taken_names,
                ),
            ),
            expr,
        )
    if isinstance(expr, ast.MatchExpr):
        return _clone_with_loc(
            ast.MatchExpr(
                scrutinee=_rewrite_expr_with_specialization(
                    expr.scrutinee,
                    scope,
                    hidden_count_by_fn,
                    fn_decls_by_name,
                    specializations,
                    generated_wrappers,
                    taken_names,
                ),
                branches=[
                    ast.MatchBranch(
                        pattern=b.pattern,
                        value=_rewrite_expr_with_specialization(
                            b.value,
                            scope | _pattern_bindings(b.pattern),
                            hidden_count_by_fn,
                            fn_decls_by_name,
                            specializations,
                            generated_wrappers,
                            taken_names,
                        ),
                    )
                    for b in expr.branches
                ],
            ),
            expr,
        )
    if isinstance(expr, ast.DoExpr):
        rewritten_steps: list[ast.DoStep] = []
        step_scope = set(scope)
        for step in expr.steps:
            if isinstance(step, ast.DoBindStep):
                rewritten_steps.append(
                    _clone_with_loc(
                        ast.DoBindStep(
                            pattern=step.pattern,
                            value=_rewrite_expr_with_specialization(
                                step.value,
                                step_scope,
                                hidden_count_by_fn,
                                fn_decls_by_name,
                                specializations,
                                generated_wrappers,
                                taken_names,
                            ),
                        ),
                        step,
                    )
                )
                step_scope |= _pattern_bindings(step.pattern)
                if hasattr(step, "_do_family"):
                    setattr(rewritten_steps[-1], "_do_family", getattr(step, "_do_family"))
                continue
            if isinstance(step, ast.DoLetStep):
                rewritten_steps.append(
                    _clone_with_loc(
                        ast.DoLetStep(
                            name=step.name,
                            value=_rewrite_expr_with_specialization(
                                step.value,
                                step_scope,
                                hidden_count_by_fn,
                                fn_decls_by_name,
                                specializations,
                                generated_wrappers,
                                taken_names,
                            ),
                        ),
                        step,
                    )
                )
                step_scope.add(step.name)
                continue
            if isinstance(step, ast.DoExprStep):
                rewritten_steps.append(
                    _clone_with_loc(
                        ast.DoExprStep(
                            value=_rewrite_expr_with_specialization(
                                step.value,
                                step_scope,
                                hidden_count_by_fn,
                                fn_decls_by_name,
                                specializations,
                                generated_wrappers,
                                taken_names,
                            ),
                        ),
                        step,
                    )
                )
                continue
            raise TypeclassLoweringError("Unsupported do step in specialization rewrite")
        return _clone_with_loc(ast.DoExpr(steps=rewritten_steps), expr)
    return expr


def _rewrite_expr(
    expr: ast.Expr,
    scope: set[str],
    method_aliases: dict[str, str],
    current_constraints: list[ast.TypeConstraint],
    current_binding_by_constraint: dict[tuple[str, tuple[str, ...]], dict[str, str]],
    fn_decls: dict[str, ast.FnDecl],
    fn_constraints: dict[str, list[ast.TypeConstraint]],
    class_method_order: dict[str, list[str]],
    instance_constraints: list[tuple[ast.TypeConstraint, dict[str, str]]],
) -> ast.Expr:
    if isinstance(expr, ast.VarExpr):
        if expr.name in scope:
            return expr
        alias = method_aliases.get(expr.name)
        if alias is None:
            return expr
        return _clone_with_loc(ast.VarExpr(alias), expr)

    if isinstance(expr, ast.IntExpr) or isinstance(expr, ast.BoolExpr) or isinstance(expr, ast.StringExpr) or isinstance(expr, ast.CharExpr):
        return expr

    if isinstance(expr, ast.LambdaExpr):
        return _clone_with_loc(
            ast.LambdaExpr(
                params=expr.params,
                body=_rewrite_expr(
                    expr.body,
                    scope | {param.name for param in expr.params},
                    method_aliases,
                    current_constraints,
                    current_binding_by_constraint,
                    fn_decls,
                    fn_constraints,
                    class_method_order,
                    instance_constraints,
                ),
            ),
            expr,
        )

    if isinstance(expr, ast.UnaryExpr):
        return _clone_with_loc(ast.UnaryExpr(op=expr.op, operand=_rewrite_expr(
            expr.operand,
            scope,
            method_aliases,
            current_constraints,
            current_binding_by_constraint,
            fn_decls,
            fn_constraints,
            class_method_order,
            instance_constraints,
        )), expr)

    if isinstance(expr, ast.BinaryExpr):
        return _clone_with_loc(
            ast.BinaryExpr(
                op=expr.op,
                left=_rewrite_expr(
                    expr.left,
                    scope,
                    method_aliases,
                    current_constraints,
                    current_binding_by_constraint,
                    fn_decls,
                    fn_constraints,
                    class_method_order,
                    instance_constraints,
                ),
                right=_rewrite_expr(
                    expr.right,
                    scope,
                    method_aliases,
                    current_constraints,
                    current_binding_by_constraint,
                    fn_decls,
                    fn_constraints,
                    class_method_order,
                    instance_constraints,
                ),
            ),
            expr,
        )

    if isinstance(expr, ast.CallExpr):
        rewritten_callee = _rewrite_expr(
            expr.callee,
            scope,
            method_aliases,
            current_constraints,
            current_binding_by_constraint,
            fn_decls,
            fn_constraints,
            class_method_order,
            instance_constraints,
        )
        rewritten_args = [
            _rewrite_expr(
                arg,
                scope,
                method_aliases,
                current_constraints,
                current_binding_by_constraint,
                fn_decls,
                fn_constraints,
                class_method_order,
                instance_constraints,
            )
            for arg in expr.args
        ]

        if (
            isinstance(expr.callee, ast.VarExpr)
            and expr.callee.name not in scope
            and expr.callee.name in method_aliases
        ):
            return _clone_with_loc(ast.CallExpr(callee=rewritten_callee, args=rewritten_args), expr)

        if isinstance(expr.callee, ast.VarExpr) and expr.callee.name not in scope:
            resolved_constraint = getattr(expr, "resolved_constraint", None)
            if isinstance(resolved_constraint, ast.TypeConstraint):
                method_source: dict[str, str] | None = None

                matches = [
                    binding
                    for have in current_constraints
                    for key, binding in current_binding_by_constraint.items()
                    if _constraint_matches_pattern(have, resolved_constraint) and key == _constraint_key(have)
                ]
                if len(matches) > 1:
                    raise TypeclassLoweringError(
                        f"Ambiguous constraint forwarding for {resolved_constraint.class_name} in direct method call"
                    )
                if len(matches) == 1:
                    method_source = matches[0]

                if method_source is None:
                    inst_matches: list[dict[str, str]] = []
                    seen_method_sources: set[tuple[tuple[str, str], ...]] = set()
                    for candidate in _constraint_resolution_candidates(resolved_constraint):
                        for inst_constraint, methods in instance_constraints:
                            if not _constraint_matches_pattern(inst_constraint, candidate):
                                continue
                            key = tuple(sorted(methods.items()))
                            if key in seen_method_sources:
                                continue
                            seen_method_sources.add(key)
                            inst_matches.append(methods)
                    if len(inst_matches) > 1:
                        raise TypeclassLoweringError(
                            f"Ambiguous instance resolution for {resolved_constraint.class_name} in direct method call"
                        )
                    if len(inst_matches) == 1:
                        method_source = inst_matches[0]

                if method_source is None:
                    raise TypeclassLoweringError(
                        f"Cannot resolve direct method call for constraint {resolved_constraint.class_name}"
                    )
                target = method_source.get(expr.callee.name)
                if target is None:
                    raise TypeclassLoweringError(
                        f"Resolved direct method call missing method {expr.callee.name}"
                    )
                return _clone_with_loc(ast.CallExpr(callee=ast.VarExpr(target), args=rewritten_args), expr)

            callee_constraints = _concretize_call_constraints(
                expr,
                fn_decls.get(expr.callee.name),
                fn_constraints.get(expr.callee.name, []),
            )
            if callee_constraints:
                extra_args: list[ast.Expr] = []
                for needed in callee_constraints:
                    method_source: dict[str, str] | None = None

                    # First, try forwarding from current function constraints.
                    matches = [
                        binding
                        for have in current_constraints
                        for key, binding in current_binding_by_constraint.items()
                        if _constraint_matches_pattern(needed, have) and key == _constraint_key(have)
                    ]
                    if len(matches) > 1:
                        raise TypeclassLoweringError(
                            f"Ambiguous constraint forwarding for {needed.class_name} in call to {expr.callee.name}"
                        )
                    if len(matches) == 1:
                        method_source = matches[0]

                    # Otherwise, try concrete instance methods.
                    if method_source is None:
                        inst_matches: list[dict[str, str]] = []
                        seen_method_sources: set[tuple[tuple[str, str], ...]] = set()
                        for candidate in _constraint_resolution_candidates(needed):
                            for inst_constraint, methods in instance_constraints:
                                if not _constraint_matches_pattern(inst_constraint, candidate):
                                    continue
                                key = tuple(sorted(methods.items()))
                                if key in seen_method_sources:
                                    continue
                                seen_method_sources.add(key)
                                inst_matches.append(methods)
                        if len(inst_matches) > 1:
                            raise TypeclassLoweringError(
                                f"Ambiguous instance resolution for {needed.class_name} in call to {expr.callee.name}"
                            )
                        if len(inst_matches) == 1:
                            method_source = inst_matches[0]

                    if method_source is None:
                        raise TypeclassLoweringError(
                            f"Cannot resolve constraint {needed.class_name} for call to {expr.callee.name}"
                        )

                    for method_name in _lookup_matching_class(class_method_order, needed.class_name) or []:
                        target = method_source.get(method_name)
                        if target is None:
                            raise TypeclassLoweringError(
                                f"Resolved constraint for {needed.class_name} missing method {method_name}"
                            )
                        extra_args.append(_clone_with_loc(ast.VarExpr(target), expr))

                rewritten_args = rewritten_args + extra_args

        return _clone_with_loc(ast.CallExpr(callee=rewritten_callee, args=rewritten_args), expr)

    if isinstance(expr, ast.IfExpr):
        return _clone_with_loc(
            ast.IfExpr(
                condition=_rewrite_expr(
                    expr.condition,
                    scope,
                    method_aliases,
                    current_constraints,
                    current_binding_by_constraint,
                    fn_decls,
                    fn_constraints,
                    class_method_order,
                    instance_constraints,
                ),
                then_branch=_rewrite_expr(
                    expr.then_branch,
                    scope,
                    method_aliases,
                    current_constraints,
                    current_binding_by_constraint,
                    fn_decls,
                    fn_constraints,
                    class_method_order,
                    instance_constraints,
                ),
                else_branch=_rewrite_expr(
                    expr.else_branch,
                    scope,
                    method_aliases,
                    current_constraints,
                    current_binding_by_constraint,
                    fn_decls,
                    fn_constraints,
                    class_method_order,
                    instance_constraints,
                ),
            ),
            expr,
        )

    if isinstance(expr, ast.MatchExpr):
        rewritten_scrutinee = _rewrite_expr(
            expr.scrutinee,
            scope,
            method_aliases,
            current_constraints,
            current_binding_by_constraint,
            fn_decls,
            fn_constraints,
            class_method_order,
            instance_constraints,
        )
        rewritten_branches: list[ast.MatchBranch] = []
        for branch in expr.branches:
            branch_scope = set(scope) | _pattern_bindings(branch.pattern)
            rewritten_branches.append(
                ast.MatchBranch(
                    pattern=branch.pattern,
                    value=_rewrite_expr(
                        branch.value,
                        branch_scope,
                        method_aliases,
                        current_constraints,
                        current_binding_by_constraint,
                        fn_decls,
                        fn_constraints,
                        class_method_order,
                        instance_constraints,
                    ),
                )
            )
        return _clone_with_loc(ast.MatchExpr(scrutinee=rewritten_scrutinee, branches=rewritten_branches), expr)

    if isinstance(expr, ast.DoExpr):
        rewritten_steps: list[ast.DoStep] = []
        step_scope = set(scope)
        for step in expr.steps:
            if isinstance(step, ast.DoBindStep):
                rewritten_step = _clone_with_loc(
                    ast.DoBindStep(
                        pattern=step.pattern,
                        value=_rewrite_expr(
                            step.value,
                            step_scope,
                            method_aliases,
                            current_constraints,
                            current_binding_by_constraint,
                            fn_decls,
                            fn_constraints,
                            class_method_order,
                            instance_constraints,
                        ),
                    ),
                    step,
                )
                if hasattr(step, "_do_family"):
                    setattr(rewritten_step, "_do_family", getattr(step, "_do_family"))
                rewritten_steps.append(rewritten_step)
                step_scope |= _pattern_bindings(step.pattern)
                continue
            if isinstance(step, ast.DoLetStep):
                rewritten_steps.append(
                    _clone_with_loc(
                        ast.DoLetStep(
                            name=step.name,
                            value=_rewrite_expr(
                                step.value,
                                step_scope,
                                method_aliases,
                                current_constraints,
                                current_binding_by_constraint,
                                fn_decls,
                                fn_constraints,
                                class_method_order,
                                instance_constraints,
                            ),
                        ),
                        step,
                    )
                )
                step_scope.add(step.name)
                continue
            if isinstance(step, ast.DoExprStep):
                rewritten_steps.append(
                    _clone_with_loc(
                        ast.DoExprStep(
                            value=_rewrite_expr(
                                step.value,
                                step_scope,
                                method_aliases,
                                current_constraints,
                                current_binding_by_constraint,
                                fn_decls,
                                fn_constraints,
                                class_method_order,
                                instance_constraints,
                            ),
                        ),
                        step,
                    )
                )
                continue
            raise TypeclassLoweringError("Unsupported do step in typeclass lowering")
        return _clone_with_loc(ast.DoExpr(steps=rewritten_steps), expr)

    return expr


def lower_typeclasses(program: ast.Program) -> ast.Program:
    class_decls: dict[str, ast.ClassDecl] = {}
    class_method_order: dict[str, list[str]] = {}
    fn_decls: dict[str, ast.FnDecl] = {}
    fn_constraints: dict[str, list[ast.TypeConstraint]] = {}

    existing_top_names = {
        decl.name
        for decl in program.declarations
        if isinstance(decl, ast.FnDecl) or isinstance(decl, ast.LetDecl)
    }

    for decl in program.declarations:
        if isinstance(decl, ast.ClassDecl):
            class_decls[decl.name] = decl
            class_method_order[decl.name] = [m.name for m in decl.methods]
        elif isinstance(decl, ast.FnDecl):
            fn_decls[decl.name] = decl
            fn_constraints[decl.name] = list(decl.constraints)

    generated_class_method_wrappers: list[ast.FnDecl] = []
    for class_decl in class_decls.values():
        class_constraint = ast.TypeConstraint(
            class_name=class_decl.name,
            args=[ast.TypeName(name) for name in class_decl.type_params],
        )
        for method in class_decl.methods:
            if method.name in existing_top_names:
                raise TypeclassLoweringError(
                    f"Generated typeclass wrapper collides with existing name: {method.name}"
                )
            existing_top_names.add(method.name)
            wrapper = _clone_with_loc(
                ast.FnDecl(
                    name=method.name,
                    params=method.params,
                    return_type=method.return_type,
                    effects=method.effects,
                    constraints=[class_constraint],
                    body=ast.CallExpr(
                        callee=ast.VarExpr(method.name),
                        args=[ast.VarExpr(param.name) for param in method.params],
                    ),
                ),
                method,
            )
            generated_class_method_wrappers.append(wrapper)
            fn_decls[wrapper.name] = wrapper
            fn_constraints[wrapper.name] = list(wrapper.constraints)

    # Materialize instance methods as ordinary top-level functions.
    instance_method_table: dict[tuple[str, tuple[str, ...]], dict[str, str]] = {}
    instance_constraints: list[tuple[ast.TypeConstraint, dict[str, str]]] = []
    generated_instance_fns: list[ast.FnDecl] = []
    for decl in program.declarations:
        if not isinstance(decl, ast.InstanceDecl):
            continue
        key = _constraint_key(decl.constraint)
        method_map: dict[str, str] = {}
        for method in decl.methods:
            generated_name = _instance_method_name(decl.constraint, method.name)
            if generated_name in existing_top_names:
                raise TypeclassLoweringError(
                    f"Generated typeclass function collides with existing name: {generated_name}"
                )
            existing_top_names.add(generated_name)
            method_map[method.name] = generated_name
            generated_instance_fns.append(
                _clone_with_loc(
                    ast.FnDecl(
                        name=generated_name,
                        params=method.params,
                        return_type=method.return_type,
                        effects=method.effects,
                        constraints=[],
                        body=method.body,
                    ),
                    method,
                )
            )
        instance_method_table[key] = method_map
        instance_constraints.append((decl.constraint, method_map))

    decls_for_output: list[object] = []
    for decl in program.declarations:
        if isinstance(decl, ast.ClassDecl) or isinstance(decl, ast.InstanceDecl):
            continue

        if isinstance(decl, ast.FnDecl):
            hidden_params: list[ast.Param] = []
            method_aliases: dict[str, str] = {}
            binding_by_constraint: dict[tuple[str, tuple[str, ...]], dict[str, str]] = {}

            for idx, constraint in enumerate(decl.constraints):
                class_decl = _lookup_matching_class(class_decls, constraint.class_name)
                if class_decl is None:
                    continue
                subs = {
                    name: arg for name, arg in zip(class_decl.type_params, constraint.args)
                }
                methods_for_constraint: dict[str, str] = {}
                for method_sig in class_decl.methods:
                    hidden_name = f"__tc_{constraint.class_name}_{idx}_{method_sig.name}"
                    used = {p.name for p in decl.params} | set(method_aliases.values()) | {p.name for p in hidden_params}
                    while hidden_name in used:
                        hidden_name += "_"

                    hidden_param_type = ast.TypeArrow(
                        left=ast.TypeName("Unit"),
                        right=ast.TypeName("Unit"),
                        effects=method_sig.effects,
                    )
                    # Rebuild method type from its class signature and substituted args.
                    if method_sig.params:
                        t = _substitute_type_expr(method_sig.return_type, subs)
                        for p in reversed(method_sig.params):
                            t = ast.TypeArrow(
                                left=_substitute_type_expr(p.type_expr, subs),
                                right=t,
                                effects=None if p is not method_sig.params[-1] else method_sig.effects,
                            )
                        hidden_param_type = t
                    else:
                        hidden_param_type = _substitute_type_expr(method_sig.return_type, subs)

                    hidden_params.append(
                        _clone_with_loc(ast.Param(name=hidden_name, type_expr=hidden_param_type), method_sig)
                    )
                    methods_for_constraint[method_sig.name] = hidden_name
                    if method_sig.name in method_aliases and method_aliases[method_sig.name] != hidden_name:
                        raise TypeclassLoweringError(
                            f"Ambiguous method {method_sig.name} in constraints for function {decl.name}"
                        )
                    method_aliases[method_sig.name] = hidden_name

                binding_by_constraint[_constraint_key(constraint)] = methods_for_constraint

            rewritten_body = _rewrite_expr(
                decl.body,
                scope={p.name for p in decl.params} | {p.name for p in hidden_params},
                method_aliases=method_aliases,
                current_constraints=decl.constraints,
                current_binding_by_constraint=binding_by_constraint,
                fn_decls=fn_decls,
                fn_constraints=fn_constraints,
                class_method_order=class_method_order,
                instance_constraints=instance_constraints,
            )

            decls_for_output.append(
                _clone_with_loc(
                    ast.FnDecl(
                        name=decl.name,
                        params=decl.params + hidden_params,
                        return_type=decl.return_type,
                        effects=decl.effects,
                        constraints=[],
                        body=rewritten_body,
                    ),
                    decl,
                )
            )
            continue

        if isinstance(decl, ast.LetDecl):
            decls_for_output.append(
                _clone_with_loc(
                    ast.LetDecl(
                        name=decl.name,
                        value=_rewrite_expr(
                            decl.value,
                            scope=set(),
                            method_aliases={},
                            current_constraints=[],
                            current_binding_by_constraint={},
                            fn_decls=fn_decls,
                            fn_constraints=fn_constraints,
                            class_method_order=class_method_order,
                            instance_constraints=instance_constraints,
                        ),
                    ),
                    decl,
                )
            )
            continue

        decls_for_output.append(decl)

    for wrapper in generated_class_method_wrappers:
        hidden_params: list[ast.Param] = []
        method_aliases: dict[str, str] = {}
        binding_by_constraint: dict[tuple[str, tuple[str, ...]], dict[str, str]] = {}

        for idx, constraint in enumerate(wrapper.constraints):
            class_decl = _lookup_matching_class(class_decls, constraint.class_name)
            if class_decl is None:
                continue
            subs = {
                name: arg for name, arg in zip(class_decl.type_params, constraint.args)
            }
            methods_for_constraint: dict[str, str] = {}
            for method_sig in class_decl.methods:
                hidden_name = f"__tc_{constraint.class_name}_{idx}_{method_sig.name}"
                used = {p.name for p in wrapper.params} | set(method_aliases.values()) | {p.name for p in hidden_params}
                while hidden_name in used:
                    hidden_name += "_"

                hidden_param_type = ast.TypeArrow(
                    left=ast.TypeName("Unit"),
                    right=ast.TypeName("Unit"),
                    effects=method_sig.effects,
                )
                if method_sig.params:
                    t = _substitute_type_expr(method_sig.return_type, subs)
                    for p in reversed(method_sig.params):
                        t = ast.TypeArrow(
                            left=_substitute_type_expr(p.type_expr, subs),
                            right=t,
                            effects=None if p is not method_sig.params[-1] else method_sig.effects,
                        )
                    hidden_param_type = t
                else:
                    hidden_param_type = _substitute_type_expr(method_sig.return_type, subs)

                hidden_params.append(
                    _clone_with_loc(ast.Param(name=hidden_name, type_expr=hidden_param_type), method_sig)
                )
                methods_for_constraint[method_sig.name] = hidden_name
                if method_sig.name in method_aliases and method_aliases[method_sig.name] != hidden_name:
                    raise TypeclassLoweringError(
                        f"Ambiguous method {method_sig.name} in constraints for function {wrapper.name}"
                    )
                method_aliases[method_sig.name] = hidden_name

            binding_by_constraint[_constraint_key(constraint)] = methods_for_constraint

        rewritten_body = _rewrite_expr(
            wrapper.body,
            scope={p.name for p in wrapper.params} | {p.name for p in hidden_params},
            method_aliases=method_aliases,
            current_constraints=wrapper.constraints,
            current_binding_by_constraint=binding_by_constraint,
            fn_decls=fn_decls,
            fn_constraints=fn_constraints,
            class_method_order=class_method_order,
            instance_constraints=instance_constraints,
        )

        decls_for_output.append(
            _clone_with_loc(
                ast.FnDecl(
                    name=wrapper.name,
                    params=wrapper.params + hidden_params,
                    return_type=wrapper.return_type,
                    effects=wrapper.effects,
                    constraints=[],
                    body=rewritten_body,
                ),
                wrapper,
            )
        )

    # Rewrite generated instance fns too (they can call constrained functions).
    for fn in generated_instance_fns:
        rewritten_body = _rewrite_expr(
            fn.body,
            scope={p.name for p in fn.params},
            method_aliases={},
            current_constraints=[],
            current_binding_by_constraint={},
            fn_decls=fn_decls,
            fn_constraints=fn_constraints,
            class_method_order=class_method_order,
            instance_constraints=instance_constraints,
        )
        decls_for_output.append(
            _clone_with_loc(
                ast.FnDecl(
                    name=fn.name,
                    params=fn.params,
                    return_type=fn.return_type,
                    effects=fn.effects,
                    constraints=[],
                    body=rewritten_body,
                ),
                fn,
            )
        )

    out = ast.Program(declarations=decls_for_output)

    # Monomorphize concrete typeclass call sites into lightweight wrappers.
    hidden_count_by_fn: dict[str, int] = {}
    for fn_name, constraints in fn_constraints.items():
        hidden_count_by_fn[fn_name] = sum(
            len(class_method_order.get(c.class_name, [])) for c in constraints
        )

    fn_decls_by_name = {
        d.name: d for d in out.declarations if isinstance(d, ast.FnDecl)
    }
    taken_names = {
        d.name for d in out.declarations if isinstance(d, ast.FnDecl) or isinstance(d, ast.LetDecl) or isinstance(d, ast.TypeDecl)
    }
    specializations: dict[tuple[str, tuple[str, ...]], str] = {}
    generated_wrappers: list[ast.FnDecl] = []

    rewritten_decls: list[object] = []
    for decl in out.declarations:
        if isinstance(decl, ast.FnDecl):
            rewritten_decls.append(
                _clone_with_loc(
                    ast.FnDecl(
                        name=decl.name,
                        params=decl.params,
                        return_type=decl.return_type,
                        effects=decl.effects,
                        constraints=[],
                        body=_rewrite_expr_with_specialization(
                            decl.body,
                            scope={p.name for p in decl.params},
                            hidden_count_by_fn=hidden_count_by_fn,
                            fn_decls_by_name=fn_decls_by_name,
                            specializations=specializations,
                            generated_wrappers=generated_wrappers,
                            taken_names=taken_names,
                        ),
                    ),
                    decl,
                )
            )
        elif isinstance(decl, ast.LetDecl):
            rewritten_decls.append(
                _clone_with_loc(
                    ast.LetDecl(
                        name=decl.name,
                        value=_rewrite_expr_with_specialization(
                            decl.value,
                            scope=set(),
                            hidden_count_by_fn=hidden_count_by_fn,
                            fn_decls_by_name=fn_decls_by_name,
                            specializations=specializations,
                            generated_wrappers=generated_wrappers,
                            taken_names=taken_names,
                        ),
                    ),
                    decl,
                )
            )
        else:
            rewritten_decls.append(decl)

    rewritten_decls.extend(generated_wrappers)
    out2 = ast.Program(declarations=rewritten_decls)
    return _clone_with_loc(out2, program)
