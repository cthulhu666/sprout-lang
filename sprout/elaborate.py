from __future__ import annotations

from dataclasses import fields, is_dataclass

from . import ast, core


class ElaborateError(ValueError):
    pass


def _type_expr_name(te: ast.TypeExpr | None) -> str | None:
    """Return the bare type-constructor name if te is a simple TypeName, else None."""
    if isinstance(te, ast.TypeName):
        return te.name
    return None


def _build_fn_sig_index(
    program: ast.Program,
) -> tuple[dict[str, list[ast.TypeExpr | None]], dict[str, ast.TypeExpr | None]]:
    """Walk program declarations and build:
    - param_types_by_fn : fn_name -> [param.type_expr, ...]  (positional)
    - return_type_by_fn : fn_name -> return_type TypeExpr or None

    Only FnDecl entries are indexed; class methods are not included because
    call-site resolution for methods would require typeclass dispatch which is
    beyond the scope of the pre-typecheck desugar.
    """
    param_types: dict[str, list[ast.TypeExpr | None]] = {}
    return_types: dict[str, ast.TypeExpr | None] = {}
    for decl in program.declarations:
        if isinstance(decl, ast.FnDecl):
            param_types[decl.name] = [p.type_expr for p in decl.params]
            return_types[decl.name] = decl.return_type
    return param_types, return_types


def desugar_string_template_expr(expr: ast.StringTemplateExpr) -> ast.Expr:
    """Desugar a StringTemplateExpr to a String-producing call-based form.

    Rules:
    - Empty template  ``  → StringExpr("")
    - All-literal     `hello`  → StringExpr("hello")
    - Single interp, no surrounding literals  `${x}`  → CallExpr(to_string, [x])
    - General case  → CallExpr(string_concat_many, [Cons(part, ... Nil)])
      where each LitPart becomes StringExpr and each InterpPart becomes
      CallExpr(to_string, [inner_expr]).
    """
    line = getattr(expr, "line", 0)
    col = getattr(expr, "column", 0)

    def loc(node: object) -> object:
        return ast.attach_loc(node, line, col)

    parts = expr.parts

    # Empty template
    if not parts:
        return loc(ast.StringExpr(value=""))

    # All-literal: merge into single StringExpr
    if all(isinstance(p, ast.LitPart) for p in parts):
        combined = "".join(p.text for p in parts)  # type: ignore[union-attr]
        return loc(ast.StringExpr(value=combined))

    # Build per-part expressions
    part_exprs: list[ast.Expr] = []
    for p in parts:
        if isinstance(p, ast.LitPart):
            if p.text:  # skip empty literal segments
                part_exprs.append(loc(ast.StringExpr(value=p.text)))
        else:
            assert isinstance(p, ast.InterpPart)
            to_str_call = loc(ast.CallExpr(
                callee=loc(ast.VarExpr(name="to_string")),
                args=[p.expr],
            ))
            part_exprs.append(to_str_call)

    # Single part: just return it directly
    if len(part_exprs) == 1:
        return part_exprs[0]

    # General: string_concat_many(Cons(p0, Cons(p1, ... Nil)))
    list_expr: ast.Expr = loc(ast.VarExpr(name="Nil"))
    for p in reversed(part_exprs):
        list_expr = loc(ast.CallExpr(
            callee=loc(ast.VarExpr(name="Cons")),
            args=[p, list_expr],
        ))
    return loc(ast.CallExpr(
        callee=loc(ast.VarExpr(name="string_concat_many")),
        args=[list_expr],
    ))


def desugar_string_template_as_string_template(expr: ast.StringTemplateExpr) -> ast.Expr:
    """Desugar a StringTemplateExpr into a StringTemplate ADT value.

    Produces:
      StringTemplate(Cons(TemplateLit("text"), Cons(TemplateInterp(to_string(x)), Nil)))

    Optimisations:
    - Empty  → StringTemplate(Nil)
    - All-literal → StringTemplate(Cons(TemplateLit("combined"), Nil))
    - Single interp only → StringTemplate(Cons(TemplateInterp(to_string(x)), Nil))
    """
    line = getattr(expr, "line", 0)
    col = getattr(expr, "column", 0)

    def loc(node: object) -> object:
        return ast.attach_loc(node, line, col)

    parts = expr.parts

    def wrap(cons_list: ast.Expr) -> ast.Expr:
        return loc(ast.CallExpr(
            callee=loc(ast.VarExpr(name="StringTemplate")),
            args=[cons_list],
        ))

    nil: ast.Expr = loc(ast.VarExpr(name="Nil"))

    # Empty template
    if not parts:
        return wrap(nil)

    # All-literal: single TemplateLit
    if all(isinstance(p, ast.LitPart) for p in parts):
        combined = "".join(p.text for p in parts)  # type: ignore[union-attr]
        lit_node = loc(ast.CallExpr(
            callee=loc(ast.VarExpr(name="TemplateLit")),
            args=[loc(ast.StringExpr(value=combined))],
        ))
        list_expr = loc(ast.CallExpr(
            callee=loc(ast.VarExpr(name="Cons")),
            args=[lit_node, nil],
        ))
        return wrap(list_expr)

    # Build TemplatePart nodes, skipping empty literal segments
    part_nodes: list[ast.Expr] = []
    for p in parts:
        if isinstance(p, ast.LitPart):
            if p.text:
                part_nodes.append(loc(ast.CallExpr(
                    callee=loc(ast.VarExpr(name="TemplateLit")),
                    args=[loc(ast.StringExpr(value=p.text))],
                )))
        else:
            assert isinstance(p, ast.InterpPart)
            to_str_call = loc(ast.CallExpr(
                callee=loc(ast.VarExpr(name="to_string")),
                args=[p.expr],
            ))
            part_nodes.append(loc(ast.CallExpr(
                callee=loc(ast.VarExpr(name="TemplateInterp")),
                args=[to_str_call],
            )))

    # Build Cons list
    list_expr = nil
    for node in reversed(part_nodes):
        list_expr = loc(ast.CallExpr(
            callee=loc(ast.VarExpr(name="Cons")),
            args=[node, list_expr],
        ))
    return wrap(list_expr)


def _attach_ast_loc(node: object, src: object | None) -> object:
    out = ast.attach_loc(node, getattr(src, "line", 0), getattr(src, "column", 0))
    if src is None or not hasattr(src, "__dict__"):
        return out
    structural_fields: set[str] = set()
    if is_dataclass(out):
        structural_fields = {field.name for field in fields(out)}
    for key, value in src.__dict__.items():
        if key in {"line", "column"} or key in structural_fields:
            continue
        setattr(out, key, value)
    return out


def _desugar_expr(
    expr: ast.Expr,
    expected_type: str | None = None,
    param_types: dict[str, list[ast.TypeExpr | None]] | None = None,
) -> ast.Expr:
    """Recursively desugar StringTemplateExpr nodes in an expression tree.

    expected_type: if "StringTemplate", a StringTemplateExpr at this position
                   will be lowered to the StringTemplate ADT form instead of
                   the default String-producing form.
    param_types:   maps function name → [param TypeExpr, ...] for resolving
                   call-site expected types.
    """
    pt = param_types or {}

    def walk(e: ast.Expr, exp: str | None = None) -> ast.Expr:
        return _desugar_expr(e, expected_type=exp, param_types=pt)

    if isinstance(expr, ast.StringTemplateExpr):
        if expected_type == "StringTemplate":
            # Lower to StringTemplate ADT; recursively desugar interp sub-exprs first
            inner_parts: list[ast.TemplateExprPart] = []
            for p in expr.parts:
                if isinstance(p, ast.InterpPart):
                    # walk the inner expression with no expected type (produces String)
                    inner_parts.append(ast.InterpPart(expr=walk(p.expr)))
                else:
                    inner_parts.append(p)
            rewired = ast.StringTemplateExpr(parts=inner_parts)
            ast.attach_loc(rewired, getattr(expr, "line", 0), getattr(expr, "column", 0))
            return desugar_string_template_as_string_template(rewired)
        else:
            desugared = desugar_string_template_expr(expr)
            return walk(desugared)
    if isinstance(expr, (ast.IntExpr, ast.BoolExpr, ast.StringExpr, ast.CharExpr,
                         ast.UnitExpr, ast.VarExpr)):
        return expr
    if isinstance(expr, ast.TupleExpr):
        return _attach_ast_loc(
            ast.TupleExpr(items=[walk(i) for i in expr.items]),
            expr,
        )
    if isinstance(expr, ast.RecordExpr):
        return _attach_ast_loc(
            ast.RecordExpr(
                type_name=expr.type_name,
                fields=[
                    _attach_ast_loc(
                        ast.RecordFieldValue(name=f.name, value=walk(f.value)),
                        f,
                    )
                    for f in expr.fields
                ],
            ),
            expr,
        )
    if isinstance(expr, ast.GetFieldExpr):
        return _attach_ast_loc(
            ast.GetFieldExpr(record=walk(expr.record), field_name=expr.field_name),
            expr,
        )
    if isinstance(expr, ast.BinaryExpr):
        return _attach_ast_loc(
            ast.BinaryExpr(op=expr.op, left=walk(expr.left), right=walk(expr.right)),
            expr,
        )
    if isinstance(expr, ast.IntRangeExpr):
        return _attach_ast_loc(
            ast.IntRangeExpr(start=walk(expr.start), end=walk(expr.end)),
            expr,
        )
    if isinstance(expr, ast.UnaryExpr):
        return _attach_ast_loc(
            ast.UnaryExpr(op=expr.op, operand=walk(expr.operand)),
            expr,
        )
    if isinstance(expr, ast.CallExpr):
        # Determine per-argument expected types from callee's declared param types.
        callee_name: str | None = None
        if isinstance(expr.callee, ast.VarExpr):
            callee_name = expr.callee.name
        callee_param_types = pt.get(callee_name) if callee_name else None
        desugared_args: list[ast.Expr] = []
        for idx, arg in enumerate(expr.args):
            arg_expected: str | None = None
            if callee_param_types is not None and idx < len(callee_param_types):
                arg_expected = _type_expr_name(callee_param_types[idx])
            desugared_args.append(walk(arg, arg_expected))
        return _attach_ast_loc(
            ast.CallExpr(callee=walk(expr.callee), args=desugared_args),
            expr,
        )
    if isinstance(expr, ast.LambdaExpr):
        return _attach_ast_loc(
            ast.LambdaExpr(params=expr.params, body=walk(expr.body)),
            expr,
        )
    if isinstance(expr, ast.IfExpr):
        return _attach_ast_loc(
            ast.IfExpr(
                condition=walk(expr.condition),
                then_branch=walk(expr.then_branch, expected_type),
                else_branch=walk(expr.else_branch, expected_type),
            ),
            expr,
        )
    if isinstance(expr, ast.MatchExpr):
        return _attach_ast_loc(
            ast.MatchExpr(
                scrutinee=walk(expr.scrutinee),
                branches=[
                    _attach_ast_loc(
                        ast.MatchBranch(pattern=b.pattern, value=walk(b.value, expected_type)),
                        b,
                    )
                    for b in expr.branches
                ],
            ),
            expr,
        )
    if isinstance(expr, ast.DoExpr):
        rewritten: list[ast.DoStep] = []
        steps = expr.steps
        for step_idx, step in enumerate(steps):
            is_last = (step_idx == len(steps) - 1)
            if isinstance(step, ast.DoBindStep):
                s = _attach_ast_loc(ast.DoBindStep(pattern=step.pattern, value=walk(step.value)), step)
                if hasattr(step, "_do_family"):
                    setattr(s, "_do_family", getattr(step, "_do_family"))
                rewritten.append(s)
            elif isinstance(step, ast.DoLetStep):
                rewritten.append(_attach_ast_loc(ast.DoLetStep(name=step.name, value=walk(step.value)), step))
            elif isinstance(step, ast.DoExprStep):
                # Only propagate expected_type to the tail expression of the do block
                step_exp = expected_type if is_last else None
                rewritten.append(_attach_ast_loc(ast.DoExprStep(value=walk(step.value, step_exp)), step))
            else:
                rewritten.append(step)
        return _attach_ast_loc(ast.DoExpr(steps=rewritten), expr)
    # Unrecognized: return unchanged
    return expr


def desugar_string_templates_in_program(program: ast.Program) -> None:
    """In-place, context-aware desugar of all StringTemplateExpr nodes.

    Context sources (Shape A — pre-typecheck-aware):
    1. FnDecl return annotation is StringTemplate → body desugared with
       expected_type="StringTemplate".
    2. CallExpr whose callee is a known top-level fn → each arg desugared
       with the expected type taken from the callee's declared param type.

    Default: produce String (existing behaviour), so existing programs are
    completely unaffected.
    """
    param_types, return_types = _build_fn_sig_index(program)
    for decl in program.declarations:
        if isinstance(decl, ast.FnDecl):
            ret_name = _type_expr_name(return_types.get(decl.name))
            decl.body = _desugar_expr(decl.body, expected_type=ret_name, param_types=param_types)
        elif isinstance(decl, ast.LetDecl):
            decl.value = _desugar_expr(decl.value, param_types=param_types)
        elif isinstance(decl, ast.InstanceDecl):
            for method in decl.methods:
                ret_name = _type_expr_name(method.return_type)
                method.body = _desugar_expr(method.body, expected_type=ret_name, param_types=param_types)


def _core_pattern_to_ast(pattern: core.Pattern) -> ast.Pattern:
    if isinstance(pattern, core.WildcardPattern):
        return _attach_ast_loc(ast.WildcardPattern(), pattern.src)
    if isinstance(pattern, core.VarPattern):
        return _attach_ast_loc(ast.VarPattern(name=pattern.name), pattern.src)
    if isinstance(pattern, core.IntPattern):
        return _attach_ast_loc(ast.IntPattern(value=pattern.value), pattern.src)
    if isinstance(pattern, core.BoolPattern):
        return _attach_ast_loc(ast.BoolPattern(value=pattern.value), pattern.src)
    if isinstance(pattern, core.StringPattern):
        return _attach_ast_loc(ast.StringPattern(value=pattern.value), pattern.src)
    if isinstance(pattern, core.CharPattern):
        return _attach_ast_loc(ast.CharPattern(value=pattern.value), pattern.src)
    if isinstance(pattern, core.UnitPattern):
        return _attach_ast_loc(ast.UnitPattern(), pattern.src)
    if isinstance(pattern, core.TuplePattern):
        return _attach_ast_loc(ast.TuplePattern(items=[_core_pattern_to_ast(item) for item in pattern.items]), pattern.src)
    if isinstance(pattern, core.ConstructorPattern):
        return _attach_ast_loc(
            ast.ConstructorPattern(name=pattern.name, args=[_core_pattern_to_ast(arg) for arg in pattern.args]),
            pattern.src,
        )
    raise ElaborateError(f"Unsupported core pattern {type(pattern).__name__}")


def core_expr_to_ast(expr: core.Expr) -> ast.Expr:
    if isinstance(expr, core.VarExpr):
        return _attach_ast_loc(ast.VarExpr(name=expr.name), expr.src)
    if isinstance(expr, core.IntExpr):
        return _attach_ast_loc(ast.IntExpr(value=expr.value), expr.src)
    if isinstance(expr, core.BoolExpr):
        return _attach_ast_loc(ast.BoolExpr(value=expr.value), expr.src)
    if isinstance(expr, core.StringExpr):
        return _attach_ast_loc(ast.StringExpr(value=expr.value), expr.src)
    if isinstance(expr, core.CharExpr):
        return _attach_ast_loc(ast.CharExpr(value=expr.value), expr.src)
    if isinstance(expr, core.UnitExpr):
        return _attach_ast_loc(ast.UnitExpr(), expr.src)
    if isinstance(expr, core.TupleExpr):
        return _attach_ast_loc(ast.TupleExpr(items=[core_expr_to_ast(item) for item in expr.items]), expr.src)
    if isinstance(expr, core.RecordExpr):
        return _attach_ast_loc(
            ast.RecordExpr(
                type_name=expr.type_name,
                fields=[
                    ast.RecordFieldValue(name=field.name, value=core_expr_to_ast(field.value))
                    for field in expr.fields
                ],
            ),
            expr.src,
        )
    if isinstance(expr, core.GetFieldExpr):
        return _attach_ast_loc(
            ast.GetFieldExpr(record=core_expr_to_ast(expr.record), field_name=expr.field_name),
            expr.src,
        )
    if isinstance(expr, core.BinaryExpr):
        return _attach_ast_loc(
            ast.BinaryExpr(
                op=expr.op,
                left=core_expr_to_ast(expr.left),
                right=core_expr_to_ast(expr.right),
            ),
            expr.src,
        )
    if isinstance(expr, core.IntRangeExpr):
        return _attach_ast_loc(
            ast.IntRangeExpr(start=core_expr_to_ast(expr.start), end=core_expr_to_ast(expr.end)),
            expr.src,
        )
    if isinstance(expr, core.UnaryExpr):
        return _attach_ast_loc(ast.UnaryExpr(op=expr.op, operand=core_expr_to_ast(expr.operand)), expr.src)
    if isinstance(expr, core.CallExpr):
        return _attach_ast_loc(
            ast.CallExpr(callee=core_expr_to_ast(expr.callee), args=[core_expr_to_ast(arg) for arg in expr.args]),
            expr.src,
        )
    if isinstance(expr, core.LambdaExpr):
        return _attach_ast_loc(ast.LambdaExpr(params=expr.params, body=core_expr_to_ast(expr.body)), expr.src)
    if isinstance(expr, core.IfExpr):
        return _attach_ast_loc(
            ast.IfExpr(
                condition=core_expr_to_ast(expr.condition),
                then_branch=core_expr_to_ast(expr.then_branch),
                else_branch=core_expr_to_ast(expr.else_branch),
            ),
            expr.src,
        )
    if isinstance(expr, core.MatchExpr):
        return _attach_ast_loc(
            ast.MatchExpr(
                scrutinee=core_expr_to_ast(expr.scrutinee),
                branches=[
                    ast.MatchBranch(
                        pattern=_core_pattern_to_ast(branch.pattern),
                        value=core_expr_to_ast(branch.value),
                    )
                    for branch in expr.branches
                ],
            ),
            expr.src,
        )
    raise ElaborateError(f"Unsupported core expr {type(expr).__name__}")


def _surface_pattern_to_core(pattern: ast.Pattern) -> core.Pattern:
    if isinstance(pattern, ast.WildcardPattern):
        return core.WildcardPattern(src=pattern)
    if isinstance(pattern, ast.VarPattern):
        return core.VarPattern(name=pattern.name, src=pattern)
    if isinstance(pattern, ast.IntPattern):
        return core.IntPattern(value=pattern.value, src=pattern)
    if isinstance(pattern, ast.BoolPattern):
        return core.BoolPattern(value=pattern.value, src=pattern)
    if isinstance(pattern, ast.StringPattern):
        return core.StringPattern(value=pattern.value, src=pattern)
    if isinstance(pattern, ast.CharPattern):
        return core.CharPattern(value=pattern.value, src=pattern)
    if isinstance(pattern, ast.UnitPattern):
        return core.UnitPattern(src=pattern)
    if isinstance(pattern, ast.TuplePattern):
        return core.TuplePattern(items=[_surface_pattern_to_core(item) for item in pattern.items], src=pattern)
    if isinstance(pattern, ast.ConstructorPattern):
        return core.ConstructorPattern(
            name=pattern.name,
            args=[_surface_pattern_to_core(arg) for arg in pattern.args],
            src=pattern,
        )
    raise ElaborateError(f"Unsupported surface pattern {type(pattern).__name__}")


def _surface_expr_to_core(expr: ast.Expr) -> core.Expr:
    if isinstance(expr, ast.VarExpr):
        return core.VarExpr(name=expr.name, src=expr)
    if isinstance(expr, ast.IntExpr):
        return core.IntExpr(value=expr.value, src=expr)
    if isinstance(expr, ast.BoolExpr):
        return core.BoolExpr(value=expr.value, src=expr)
    if isinstance(expr, ast.StringExpr):
        return core.StringExpr(value=expr.value, src=expr)
    if isinstance(expr, ast.CharExpr):
        return core.CharExpr(value=expr.value, src=expr)
    if isinstance(expr, ast.UnitExpr):
        return core.UnitExpr(src=expr)
    if isinstance(expr, ast.TupleExpr):
        return core.TupleExpr(items=[_surface_expr_to_core(item) for item in expr.items], src=expr)
    if isinstance(expr, ast.RecordExpr):
        return core.RecordExpr(
            type_name=expr.type_name,
            fields=[
                core.RecordFieldValue(name=field.name, value=_surface_expr_to_core(field.value), src=field)
                for field in expr.fields
            ],
            src=expr,
        )
    if isinstance(expr, ast.GetFieldExpr):
        return core.GetFieldExpr(record=_surface_expr_to_core(expr.record), field_name=expr.field_name, src=expr)
    if isinstance(expr, ast.BinaryExpr):
        return core.BinaryExpr(
            op=expr.op,
            left=_surface_expr_to_core(expr.left),
            right=_surface_expr_to_core(expr.right),
            src=expr,
        )
    if isinstance(expr, ast.IntRangeExpr):
        return core.IntRangeExpr(
            start=_surface_expr_to_core(expr.start),
            end=_surface_expr_to_core(expr.end),
            src=expr,
        )
    if isinstance(expr, ast.UnaryExpr):
        return core.UnaryExpr(op=expr.op, operand=_surface_expr_to_core(expr.operand), src=expr)
    if isinstance(expr, ast.CallExpr):
        return core.CallExpr(
            callee=_surface_expr_to_core(expr.callee),
            args=[_surface_expr_to_core(arg) for arg in expr.args],
            src=expr,
        )
    if isinstance(expr, ast.LambdaExpr):
        return core.LambdaExpr(params=expr.params, body=_surface_expr_to_core(expr.body), src=expr)
    if isinstance(expr, ast.IfExpr):
        return core.IfExpr(
            condition=_surface_expr_to_core(expr.condition),
            then_branch=_surface_expr_to_core(expr.then_branch),
            else_branch=_surface_expr_to_core(expr.else_branch),
            src=expr,
        )
    if isinstance(expr, ast.MatchExpr):
        return core.MatchExpr(
            scrutinee=_surface_expr_to_core(expr.scrutinee),
            branches=[
                core.MatchBranch(
                    pattern=_surface_pattern_to_core(branch.pattern),
                    value=_surface_expr_to_core(branch.value),
                    src=branch,
                )
                for branch in expr.branches
            ],
            src=expr,
        )
    if isinstance(expr, ast.DoExpr):
        return elaborate_expr_to_core(expr)
    if isinstance(expr, ast.StringTemplateExpr):
        return _surface_expr_to_core(desugar_string_template_expr(expr))
    raise ElaborateError(f"Unsupported surface expr {type(expr).__name__}")


def _build_do_failure_core(step: ast.DoBindStep, family: str) -> core.Expr:
    if family == "Maybe":
        return core.VarExpr(name="Nothing", src=step)
    if family == "Result":
        err_expr = core.VarExpr(name="__sprout_do_err", src=step)
        return core.CallExpr(callee=core.VarExpr(name="Err", src=step), args=[err_expr], src=step)
    raise ElaborateError(f"Unsupported do family {family}")


def _build_do_let_core(pattern: ast.Pattern, value: ast.Expr, body: core.Expr, src: object) -> core.Expr:
    return core.MatchExpr(
        scrutinee=_surface_expr_to_core(value),
        branches=[core.MatchBranch(pattern=_surface_pattern_to_core(pattern), value=body, src=src)],
        src=src,
    )


def _build_do_ignore_core(value: ast.Expr, body: core.Expr, index: int, src: object) -> core.Expr:
    return core.MatchExpr(
        scrutinee=_surface_expr_to_core(value),
        branches=[core.MatchBranch(pattern=core.WildcardPattern(src=src), value=body, src=src)],
        src=src,
    )


def elaborate_expr_to_core(expr: ast.Expr) -> core.Expr:
    if not isinstance(expr, ast.DoExpr):
        return _surface_expr_to_core(expr)
    final_step = expr.steps[-1]
    if not isinstance(final_step, ast.DoExprStep):
        raise ElaborateError("Internal error: do block is missing a final expression")
    out = _surface_expr_to_core(final_step.value)
    for index, step in reversed(list(enumerate(expr.steps[:-1]))):
        if isinstance(step, ast.DoLetStep):
            out = _build_do_let_core(ast.VarPattern(name=step.name), step.value, out, step)
            continue
        if isinstance(step, ast.DoExprStep):
            out = _build_do_ignore_core(step.value, out, index, step)
            continue
        if not isinstance(step, ast.DoBindStep):
            raise ElaborateError("Internal error: unsupported do step")
        family = getattr(step, "_do_family", None)
        if family is None:
            raise ElaborateError("Internal error: unresolved do step family")
        if family == "IO":
            out = _build_do_let_core(step.pattern, step.value, out, step)
            continue
        success_name = "Just" if family == "Maybe" else "Ok"
        failure_name = "Nothing" if family == "Maybe" else "Err"
        success_pattern = core.ConstructorPattern(
            name=success_name,
            args=[_surface_pattern_to_core(step.pattern)],
            src=step,
        )
        failure_args: list[core.Pattern] = []
        if family == "Result":
            failure_args = [core.VarPattern(name="__sprout_do_err", src=step)]
        failure_pattern = core.ConstructorPattern(name=failure_name, args=failure_args, src=step)
        out = core.MatchExpr(
            scrutinee=_surface_expr_to_core(step.value),
            branches=[
                core.MatchBranch(pattern=failure_pattern, value=_build_do_failure_core(step, family), src=step),
                core.MatchBranch(pattern=success_pattern, value=out, src=step),
            ],
            src=step,
        )
    return out


def elaborate_expr(expr: ast.Expr) -> ast.Expr:
    return core_expr_to_ast(elaborate_expr_to_core(expr))


def elaborate_program(program: ast.Program) -> ast.Program:
    program_core_decls: dict[str, core.Expr] = {}
    for decl in program.declarations:
        if isinstance(decl, ast.FnDecl):
            core_body = elaborate_expr_to_core(decl.body)
            program_core_decls[decl.name] = core_body
            decl.body = core_expr_to_ast(core_body)
        elif isinstance(decl, ast.LetDecl):
            decl.value = elaborate_expr(decl.value)
        elif isinstance(decl, ast.InstanceDecl):
            for method in decl.methods:
                core_body = elaborate_expr_to_core(method.body)
                program_core_decls[method.name] = core_body
                method.body = core_expr_to_ast(core_body)
    setattr(program, "core_declarations", program_core_decls)
    return program
