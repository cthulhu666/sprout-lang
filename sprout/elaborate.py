from __future__ import annotations

from dataclasses import fields, is_dataclass

from . import ast, core


class ElaborateError(ValueError):
    pass


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
    raise ElaborateError(f"Unsupported surface expr {type(expr).__name__}")


def _build_do_failure_core(step: ast.DoBindStep, family: str) -> core.Expr:
    if family == "Maybe":
        return core.VarExpr(name="Nothing", src=step)
    if family == "Result":
        err_expr = core.VarExpr(name="__sprout_do_err", src=step)
        return core.CallExpr(callee=core.VarExpr(name="Err", src=step), args=[err_expr], src=step)
    raise ElaborateError(f"Unsupported do family {family}")


def _build_do_let_core(name: str, value: ast.Expr, body: core.Expr, src: object) -> core.Expr:
    return core.CallExpr(
        callee=core.LambdaExpr(params=[ast.Param(name=name, type_expr=None)], body=body, src=src),
        args=[_surface_expr_to_core(value)],
        src=src,
    )


def _build_do_ignore_core(value: ast.Expr, body: core.Expr, index: int, src: object) -> core.Expr:
    return core.CallExpr(
        callee=core.LambdaExpr(params=[ast.Param(name=f"__sprout_do_ignore_{index}", type_expr=None)], body=body, src=src),
        args=[_surface_expr_to_core(value)],
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
            out = _build_do_let_core(step.name, step.value, out, step)
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
            out = _build_do_let_core(step.name, step.value, out, step)
            continue
        success_name = "Just" if family == "Maybe" else "Ok"
        failure_name = "Nothing" if family == "Maybe" else "Err"
        success_pattern = core.ConstructorPattern(
            name=success_name,
            args=[core.VarPattern(name=step.name, src=step)],
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
