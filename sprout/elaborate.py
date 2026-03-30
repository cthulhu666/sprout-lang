from __future__ import annotations

from . import ast


class ElaborateError(ValueError):
    pass


def _make_ctor_pattern(name: str, args: list[ast.Pattern], src: object) -> ast.ConstructorPattern:
    return ast.attach_loc(ast.ConstructorPattern(name=name, args=args), getattr(src, "line", 0), getattr(src, "column", 0))


def _make_var_pattern(name: str, src: object) -> ast.VarPattern:
    return ast.attach_loc(ast.VarPattern(name=name), getattr(src, "line", 0), getattr(src, "column", 0))


def _make_var_expr(name: str, src: object) -> ast.VarExpr:
    return ast.attach_loc(ast.VarExpr(name=name), getattr(src, "line", 0), getattr(src, "column", 0))


def _make_call_expr(callee: ast.Expr, args: list[ast.Expr], src: object) -> ast.CallExpr:
    return ast.attach_loc(ast.CallExpr(callee=callee, args=args), getattr(src, "line", 0), getattr(src, "column", 0))


def _build_do_failure_expr(step: ast.DoBindStep, family: str) -> ast.Expr:
    if family == "Maybe":
        return _make_var_expr("Nothing", step)
    if family == "Result":
        err_name = "__sprout_do_err"
        err_pattern = _make_var_pattern(err_name, step)
        err_expr = _make_var_expr(err_name, step)
        return _make_call_expr(_make_var_expr("Err", step), [err_expr], err_pattern)
    raise ElaborateError(f"Unsupported do family {family}")


def _build_do_lowered_expr(expr: ast.DoExpr) -> ast.Expr:
    final_step = expr.steps[-1]
    if not isinstance(final_step, ast.DoExprStep):
        raise ElaborateError("Internal error: do block is missing a final expression")
    out = elaborate_expr(final_step.value)
    for step in reversed(expr.steps[:-1]):
        if not isinstance(step, ast.DoBindStep):
            raise ElaborateError("Internal error: only bind steps may precede the final do expression")
        family = getattr(step, "_do_family", None)
        if family is None:
            raise ElaborateError("Internal error: unresolved do step family")
        success_name = "Just" if family == "Maybe" else "Ok"
        failure_name = "Nothing" if family == "Maybe" else "Err"
        success_pattern = _make_ctor_pattern(success_name, [_make_var_pattern(step.name, step)], step)
        failure_args: list[ast.Pattern] = []
        if family == "Result":
            failure_args = [_make_var_pattern("__sprout_do_err", step)]
        failure_pattern = _make_ctor_pattern(failure_name, failure_args, step)
        branches = [
            ast.attach_loc(ast.MatchBranch(pattern=failure_pattern, value=_build_do_failure_expr(step, family)), getattr(step, "line", 0), getattr(step, "column", 0)),
            ast.attach_loc(ast.MatchBranch(pattern=success_pattern, value=out), getattr(step, "line", 0), getattr(step, "column", 0)),
        ]
        out = ast.attach_loc(
            ast.MatchExpr(scrutinee=elaborate_expr(step.value), branches=branches),
            getattr(step, "line", 0),
            getattr(step, "column", 0),
        )
    return ast.attach_loc(out, getattr(expr, "line", 0), getattr(expr, "column", 0))


def elaborate_expr(expr: ast.Expr) -> ast.Expr:
    if isinstance(expr, ast.DoExpr):
        return _build_do_lowered_expr(expr)
    if isinstance(expr, ast.IfExpr):
        expr.condition = elaborate_expr(expr.condition)
        expr.then_branch = elaborate_expr(expr.then_branch)
        expr.else_branch = elaborate_expr(expr.else_branch)
        return expr
    if isinstance(expr, ast.MatchExpr):
        expr.scrutinee = elaborate_expr(expr.scrutinee)
        for branch in expr.branches:
            branch.value = elaborate_expr(branch.value)
        return expr
    if isinstance(expr, ast.TupleExpr):
        expr.items = [elaborate_expr(item) for item in expr.items]
        return expr
    if isinstance(expr, ast.RecordExpr):
        for field in expr.fields:
            field.value = elaborate_expr(field.value)
        return expr
    if isinstance(expr, ast.GetFieldExpr):
        expr.record = elaborate_expr(expr.record)
        return expr
    if isinstance(expr, ast.BinaryExpr):
        expr.left = elaborate_expr(expr.left)
        expr.right = elaborate_expr(expr.right)
        return expr
    if isinstance(expr, ast.IntRangeExpr):
        expr.start = elaborate_expr(expr.start)
        expr.end = elaborate_expr(expr.end)
        return expr
    if isinstance(expr, ast.UnaryExpr):
        expr.operand = elaborate_expr(expr.operand)
        return expr
    if isinstance(expr, ast.CallExpr):
        expr.callee = elaborate_expr(expr.callee)
        expr.args = [elaborate_expr(arg) for arg in expr.args]
        return expr
    if isinstance(expr, ast.LambdaExpr):
        expr.body = elaborate_expr(expr.body)
        return expr
    return expr


def elaborate_program(program: ast.Program) -> ast.Program:
    for decl in program.declarations:
        if isinstance(decl, ast.FnDecl):
            decl.body = elaborate_expr(decl.body)
        elif isinstance(decl, ast.LetDecl):
            decl.value = elaborate_expr(decl.value)
        elif isinstance(decl, ast.InstanceDecl):
            for method in decl.methods:
                method.body = elaborate_expr(method.body)
    return program
