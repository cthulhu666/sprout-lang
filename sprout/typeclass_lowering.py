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
        return ast.TypeArrow(left=_copy_type_expr(node.left), right=_copy_type_expr(node.right))
    raise TypeclassLoweringError(f"Unsupported type expression in lowering copy: {node}")


def _type_expr_is_concrete(node: ast.TypeExpr) -> bool:
    if isinstance(node, ast.TypeName):
        return not (node.name and node.name[0].islower())
    if isinstance(node, ast.TypeApply):
        return _type_expr_is_concrete(node.base) and _type_expr_is_concrete(node.arg)
    if isinstance(node, ast.TypeArrow):
        return _type_expr_is_concrete(node.left) and _type_expr_is_concrete(node.right)
    return False


def _type_expr_equal(left: ast.TypeExpr, right: ast.TypeExpr) -> bool:
    if isinstance(left, ast.TypeName) and isinstance(right, ast.TypeName):
        return left.name == right.name
    if isinstance(left, ast.TypeApply) and isinstance(right, ast.TypeApply):
        return _type_expr_equal(left.base, right.base) and _type_expr_equal(left.arg, right.arg)
    if isinstance(left, ast.TypeArrow) and isinstance(right, ast.TypeArrow):
        return _type_expr_equal(left.left, right.left) and _type_expr_equal(left.right, right.right)
    return False


def _type_expr_mangle(node: ast.TypeExpr) -> str:
    if isinstance(node, ast.TypeName):
        return node.name.replace(".", "_")
    if isinstance(node, ast.TypeApply):
        return f"{_type_expr_mangle(node.base)}_{_type_expr_mangle(node.arg)}"
    if isinstance(node, ast.TypeArrow):
        return f"Fn_{_type_expr_mangle(node.left)}_{_type_expr_mangle(node.right)}"
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
        )
    raise TypeclassLoweringError(f"Unsupported type expression in substitution: {node}")


def _match_type_expr_pattern(
    pattern: ast.TypeExpr,
    candidate: ast.TypeExpr,
    env: dict[str, ast.TypeExpr],
) -> bool:
    if isinstance(pattern, ast.TypeName) and pattern.name and pattern.name[0].islower():
        existing = env.get(pattern.name)
        if existing is None:
            env[pattern.name] = candidate
            return True
        return _type_expr_equal(existing, candidate)

    if isinstance(pattern, ast.TypeName) and isinstance(candidate, ast.TypeName):
        return pattern.name == candidate.name
    if isinstance(pattern, ast.TypeApply) and isinstance(candidate, ast.TypeApply):
        return _match_type_expr_pattern(pattern.base, candidate.base, env) and _match_type_expr_pattern(
            pattern.arg, candidate.arg, env
        )
    if isinstance(pattern, ast.TypeArrow) and isinstance(candidate, ast.TypeArrow):
        return _match_type_expr_pattern(pattern.left, candidate.left, env) and _match_type_expr_pattern(
            pattern.right, candidate.right, env
        )
    return False


def _constraint_matches_pattern(pattern: ast.TypeConstraint, candidate: ast.TypeConstraint) -> bool:
    if pattern.class_name != candidate.class_name:
        return False
    if len(pattern.args) != len(candidate.args):
        return False
    env: dict[str, ast.TypeExpr] = {}
    for p, c in zip(pattern.args, candidate.args):
        if not _match_type_expr_pattern(p, c, env):
            return False
    return True


def _pattern_bindings(pat: ast.Pattern) -> set[str]:
    if isinstance(pat, ast.VarPattern):
        return {pat.name}
    if isinstance(pat, ast.ConstructorPattern):
        out: set[str] = set()
        for arg in pat.args:
            out |= _pattern_bindings(arg)
        return out
    return set()


def _rewrite_expr(
    expr: ast.Expr,
    scope: set[str],
    method_aliases: dict[str, str],
    current_constraints: list[ast.TypeConstraint],
    current_binding_by_constraint: dict[tuple[str, tuple[str, ...]], dict[str, str]],
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

    if isinstance(expr, ast.IntExpr) or isinstance(expr, ast.BoolExpr) or isinstance(expr, ast.StringExpr):
        return expr

    if isinstance(expr, ast.UnaryExpr):
        return _clone_with_loc(ast.UnaryExpr(op=expr.op, operand=_rewrite_expr(
            expr.operand,
            scope,
            method_aliases,
            current_constraints,
            current_binding_by_constraint,
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
                fn_constraints,
                class_method_order,
                instance_constraints,
            )
            for arg in expr.args
        ]

        if isinstance(expr.callee, ast.VarExpr) and expr.callee.name not in scope:
            callee_constraints = fn_constraints.get(expr.callee.name, [])
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
                        inst_matches = [
                            methods
                            for inst_constraint, methods in instance_constraints
                            if _constraint_matches_pattern(needed, inst_constraint)
                        ]
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

                    for method_name in class_method_order.get(needed.class_name, []):
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
                        fn_constraints,
                        class_method_order,
                        instance_constraints,
                    ),
                )
            )
        return _clone_with_loc(ast.MatchExpr(scrutinee=rewritten_scrutinee, branches=rewritten_branches), expr)

    return expr


def lower_typeclasses(program: ast.Program) -> ast.Program:
    class_decls: dict[str, ast.ClassDecl] = {}
    class_method_order: dict[str, list[str]] = {}
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
            fn_constraints[decl.name] = list(decl.constraints)

    # Materialize instance methods as ordinary top-level functions.
    instance_method_table: dict[tuple[str, tuple[str, ...]], dict[str, str]] = {}
    instance_constraints: list[tuple[ast.TypeConstraint, dict[str, str]]] = []
    generated_instance_fns: list[ast.FnDecl] = []
    for decl in program.declarations:
        if not isinstance(decl, ast.InstanceDecl):
            continue
        if not all(_type_expr_is_concrete(arg) for arg in decl.constraint.args):
            raise TypeclassLoweringError(
                f"Instance for {decl.constraint.class_name} must use concrete types for lowering"
            )
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
                class_decl = class_decls.get(constraint.class_name)
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
                    )
                    # Rebuild method type from its class signature and substituted args.
                    if method_sig.params:
                        t = _substitute_type_expr(method_sig.return_type, subs)
                        for p in reversed(method_sig.params):
                            t = ast.TypeArrow(
                                left=_substitute_type_expr(p.type_expr, subs),
                                right=t,
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
                        constraints=[],
                        body=rewritten_body,
                    ),
                    decl,
                )
            )
            continue

        decls_for_output.append(decl)

    # Rewrite generated instance fns too (they can call constrained functions).
    for fn in generated_instance_fns:
        rewritten_body = _rewrite_expr(
            fn.body,
            scope={p.name for p in fn.params},
            method_aliases={},
            current_constraints=[],
            current_binding_by_constraint={},
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
                    constraints=[],
                    body=rewritten_body,
                ),
                fn,
            )
        )

    out = ast.Program(declarations=decls_for_output)
    return _clone_with_loc(out, program)
