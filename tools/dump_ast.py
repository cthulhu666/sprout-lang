#!/usr/bin/env python3
"""
Dump a Sprout source file as flat s-expression AST (one declaration per line).
Used for parity testing against the bootstrap Sprout parser in driver.sprout.

Usage:
    python tools/dump_ast.py <file.spr>
    python tools/dump_ast.py --strip-headers <file.sprout>

The --strip-headers flag removes module/import lines before parsing, matching
the behaviour of driver.sprout's strip_headers function.
"""
from __future__ import annotations

import sys
import os
import re

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sprout import parse
import sprout.ast as ast


# ---------------------------------------------------------------------------
# String helpers


def q(s: str) -> str:
    """Quote a string value as a double-quoted s-expression atom."""
    escaped = (
        s.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\t", "\\t")
        .replace("\r", "\\r")
    )
    return f'"{escaped}"'


def sexp(tag: str, items: list[str]) -> str:
    if not items:
        return f"({tag})"
    return f"({tag} {' '.join(items)})"


def bool_str(b: bool) -> str:
    return "true" if b else "false"


# ---------------------------------------------------------------------------
# TypeExpr


def dump_type(t: ast.TypeExpr) -> str:
    if isinstance(t, ast.TypeName):
        return sexp("type-name", [q(t.name)])
    if isinstance(t, ast.TypeApply):
        return sexp("type-apply", [dump_type(t.base), dump_type(t.arg)])
    if isinstance(t, ast.TypeArrow):
        parts = [dump_type(t.left), dump_type(t.right)]
        if t.effects is not None:
            parts.append(dump_effects(list(t.effects)))
        return sexp("type-arrow", parts)
    if isinstance(t, ast.TypeEffect):
        return sexp("type-effect", [dump_type(t.base)] + [q(e) for e in t.effects])
    if isinstance(t, ast.TupleType):
        return sexp("tuple-type", [dump_type(i) for i in t.items])
    raise ValueError(f"Unknown TypeExpr: {type(t)}")


def dump_effects(effs: list[str] | None) -> str:
    if effs is None:
        return "(effects)"
    return sexp("effects", [q(e) for e in effs])


# ---------------------------------------------------------------------------
# Pattern


def dump_pat(p: ast.Pattern) -> str:
    if isinstance(p, ast.WildcardPattern):
        return "(wildcard)"
    if isinstance(p, ast.VarPattern):
        return sexp("var-pat", [q(p.name)])
    if isinstance(p, ast.IntPattern):
        return sexp("int-pat", [str(p.value)])
    if isinstance(p, ast.BoolPattern):
        return sexp("bool-pat", [bool_str(p.value)])
    if isinstance(p, ast.StringPattern):
        return sexp("string-pat", [q(p.value)])
    if isinstance(p, ast.CharPattern):
        return sexp("char-pat", [q(p.value)])
    if isinstance(p, ast.UnitPattern):
        return "(unit-pat)"
    if isinstance(p, ast.TuplePattern):
        return sexp("tuple-pat", [dump_pat(i) for i in p.items])
    if isinstance(p, ast.ConstructorPattern):
        return sexp("ctor-pat", [q(p.name)] + [dump_pat(a) for a in p.args])
    raise ValueError(f"Unknown Pattern: {type(p)}")


# ---------------------------------------------------------------------------
# Param


def dump_param(p: ast.Param) -> str:
    if p.type_expr is not None:
        return sexp("param", [q(p.name), dump_type(p.type_expr)])
    return sexp("param", [q(p.name)])


def dump_params(params: list[ast.Param]) -> str:
    return sexp("params", [dump_param(p) for p in params])


# ---------------------------------------------------------------------------
# Expressions


def dump_expr(e: ast.Expr) -> str:
    if isinstance(e, ast.VarExpr):
        return sexp("var", [q(e.name)])
    if isinstance(e, ast.IntExpr):
        return sexp("int", [str(e.value)])
    if isinstance(e, ast.BoolExpr):
        return sexp("bool", [bool_str(e.value)])
    if isinstance(e, ast.StringExpr):
        return sexp("string", [q(e.value)])
    if isinstance(e, ast.CharExpr):
        return sexp("char", [q(e.value)])
    if isinstance(e, ast.UnitExpr):
        return "(unit)"
    if isinstance(e, ast.TupleExpr):
        return sexp("tuple", [dump_expr(i) for i in e.items])
    if isinstance(e, ast.IfExpr):
        return sexp("if", [dump_expr(e.condition), dump_expr(e.then_branch), dump_expr(e.else_branch)])
    if isinstance(e, ast.MatchExpr):
        branches = [dump_branch(b) for b in e.branches]
        return sexp("match", [dump_expr(e.scrutinee)] + branches)
    if isinstance(e, ast.CallExpr):
        return sexp("call", [dump_expr(e.callee)] + [dump_expr(a) for a in e.args])
    if isinstance(e, ast.LambdaExpr):
        return sexp("lambda", [dump_params(e.params), dump_expr(e.body)])
    if isinstance(e, ast.BinaryExpr):
        return sexp("binary", [q(e.op), dump_expr(e.left), dump_expr(e.right)])
    if isinstance(e, ast.UnaryExpr):
        return sexp("unary", [q(e.op), dump_expr(e.operand)])
    if isinstance(e, ast.IntRangeExpr):
        return sexp("range", [dump_expr(e.start), dump_expr(e.end)])
    if isinstance(e, ast.DoExpr):
        return sexp("do", [dump_step(s) for s in e.steps])
    if isinstance(e, ast.RecordExpr):
        fields = [sexp("field", [q(f.name), dump_expr(f.value)]) for f in e.fields]
        return sexp("record", [q(e.type_name)] + fields)
    if isinstance(e, ast.GetFieldExpr):
        return sexp("get-field", [dump_expr(e.record), q(e.field_name)])
    if isinstance(e, ast.StringTemplateExpr):
        return sexp("template-expr", [dump_template_part(p) for p in e.parts])
    raise ValueError(f"Unknown Expr: {type(e)}")


def dump_template_part(p: ast.TemplateExprPart) -> str:
    if isinstance(p, ast.LitPart):
        return sexp("lit-part", [q(p.text)])
    if isinstance(p, ast.InterpPart):
        return sexp("interp-part", [dump_expr(p.expr)])
    raise ValueError(f"Unknown TemplateExprPart: {type(p)}")


def dump_branch(b: ast.MatchBranch) -> str:
    return sexp("branch", [dump_pat(b.pattern), dump_expr(b.value)])


def dump_step(s: ast.DoStep) -> str:
    if isinstance(s, ast.DoBindStep):
        return sexp("bind", [dump_pat(s.pattern), dump_expr(s.value)])
    if isinstance(s, ast.DoLetStep):
        return sexp("do-let", [q(s.name), dump_expr(s.value)])
    if isinstance(s, ast.DoExprStep):
        return sexp("do-expr", [dump_expr(s.value)])
    raise ValueError(f"Unknown DoStep: {type(s)}")


# ---------------------------------------------------------------------------
# Declarations


def dump_constraint(c: ast.TypeConstraint) -> str:
    return sexp("constraint", [q(c.class_name)] + [dump_type(a) for a in c.args])


def dump_method_sig(m: ast.ClassMethodSig) -> str:
    return sexp("method-sig", [
        q(m.name),
        dump_params(m.params),
        dump_type(m.return_type),
        dump_effects(list(m.effects) if m.effects is not None else None),
    ])


def dump_method_impl(m: ast.InstanceMethodImpl) -> str:
    ret = sexp("ret", [dump_type(m.return_type)]) if m.return_type is not None else "(ret)"
    return sexp("method-impl", [
        q(m.name),
        dump_params(m.params),
        ret,
        dump_effects(list(m.effects) if m.effects is not None else None),
        dump_expr(m.body),
    ])


def dump_decl(d: ast.Decl) -> str:
    if isinstance(d, ast.FnDecl):
        ret = sexp("ret", [dump_type(d.return_type)]) if d.return_type is not None else "(ret)"
        effs = dump_effects(list(d.effects) if d.effects is not None else None)
        constraints = sexp("constraints", [dump_constraint(c) for c in d.constraints])
        return sexp("fn", [q(d.name), dump_params(d.params), ret, effs, constraints, dump_expr(d.body)])
    if isinstance(d, ast.TypeDecl):
        type_params = sexp("type-params", [q(p) for p in d.type_params])
        ctors = [sexp("ctor", [q(c.name)] + [dump_type(a) for a in c.args]) for c in d.constructors]
        return sexp("type", [q(d.name), type_params] + ctors)
    if isinstance(d, ast.RecordDecl):
        type_params = sexp("type-params", [q(p) for p in d.type_params])
        fields = [sexp("field-decl", [q(f.name), dump_type(f.type_expr)]) for f in d.fields]
        return sexp("record", [q(d.name), type_params] + fields)
    if isinstance(d, ast.LetDecl):
        return sexp("let", [q(d.name), dump_expr(d.value)])
    if isinstance(d, ast.ClassDecl):
        type_params = sexp("type-params", [q(p) for p in d.type_params])
        methods = [dump_method_sig(m) for m in d.methods]
        return sexp("class", [q(d.name), type_params] + methods)
    if isinstance(d, ast.InstanceDecl):
        methods = [dump_method_impl(m) for m in d.methods]
        return sexp("instance", [dump_constraint(d.constraint)] + methods)
    raise ValueError(f"Unknown Decl: {type(d)}")


# ---------------------------------------------------------------------------
# Header stripping (mirrors driver.sprout's strip_headers)


_HEADER_LINE_RE = re.compile(r"^(module |import |\n|$)")


def strip_headers(src: str) -> str:
    lines = src.split("\n")
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped == "" or stripped.startswith("module ") or stripped.startswith("import "):
            continue
        return "\n".join(lines[i:])
    return ""


# ---------------------------------------------------------------------------
# Entry point


def main() -> None:
    args = sys.argv[1:]
    do_strip = False
    if args and args[0] == "--strip-headers":
        do_strip = True
        args = args[1:]
    if len(args) != 1:
        print("usage: dump_ast.py [--strip-headers] <file.spr>", file=sys.stderr)
        sys.exit(1)

    src = open(args[0]).read()
    if do_strip:
        src = strip_headers(src)

    try:
        prog = parse(src)
    except Exception as e:
        print(f"ERROR: parse: {e}", file=sys.stderr)
        sys.exit(1)

    for decl in prog.declarations:
        print(dump_decl(decl))


if __name__ == "__main__":
    main()
