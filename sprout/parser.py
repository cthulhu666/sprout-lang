from __future__ import annotations

from dataclasses import dataclass

from . import ast
from .tokenizer import Token, tokenize


class ParseError(ValueError):
    pass


@dataclass
class Parser:
    tokens: list[Token]
    i: int = 0

    def current(self) -> Token:
        return self.tokens[self.i]

    def advance(self) -> Token:
        t = self.current()
        self.i += 1
        return t

    def check(self, kind: str, value: str | None = None) -> bool:
        t = self.current()
        if t.kind != kind:
            return False
        if value is not None and t.value != value:
            return False
        return True

    def match(self, kind: str, value: str | None = None) -> bool:
        if self.check(kind, value):
            self.advance()
            return True
        return False

    def expect(self, kind: str, value: str | None = None, label: str | None = None) -> Token:
        if self.check(kind, value):
            return self.advance()
        t = self.current()
        wanted = label or (f"{kind} {value}" if value is not None else kind)
        raise ParseError(f"Expected {wanted} at {t.line}:{t.column}, got {t.kind} {t.value!r}")

    def mark(self, node, token: Token):
        return ast.attach_loc(node, token.line, token.column)

    def parse_program(self) -> ast.Program:
        decls = []
        while not self.check("EOF"):
            decls.append(self.parse_declaration())
        if decls:
            first = decls[0]
            return self.mark(ast.Program(decls), Token("META", "", first.line, first.column))
        eof = self.current()
        return self.mark(ast.Program(decls), Token("META", "", eof.line, eof.column))

    def parse_declaration(self):
        # `export` is a declaration modifier. Visibility is handled by module loading.
        self.match("KEYWORD", "export")
        if self.check("KEYWORD", "type"):
            return self.parse_type_decl()
        if self.check("KEYWORD", "class"):
            return self.parse_class_decl()
        if self.check("KEYWORD", "instance"):
            return self.parse_instance_decl()
        if self.check("KEYWORD", "fn"):
            return self.parse_fn_decl()
        if self.check("KEYWORD", "let"):
            return self.parse_let_decl()
        t = self.current()
        raise ParseError(f"Expected top-level declaration at {t.line}:{t.column}, got {t.value!r}")

    def parse_type_decl(self) -> ast.TypeDecl:
        start = self.expect("KEYWORD", "type")
        name = self.expect("IDENT", label="type name").value
        params = []
        while self.check("IDENT"):
            params.append(self.advance().value)
        self.expect("SYMBOL", "=")

        constructors = []
        if self.match("SYMBOL", "|"):
            pass
        constructors.append(self.parse_type_constructor())
        while self.match("SYMBOL", "|"):
            constructors.append(self.parse_type_constructor())
        return self.mark(ast.TypeDecl(name=name, type_params=params, constructors=constructors), start)

    def parse_type_constructor(self) -> ast.TypeConstructor:
        tok = self.expect("IDENT", label="constructor name")
        name = tok.value
        args = []
        while self._starts_type_atom():
            args.append(self.parse_type_atom())
        return self.mark(ast.TypeConstructor(name=name, args=args), tok)

    def parse_fn_decl(self) -> ast.FnDecl:
        start = self.expect("KEYWORD", "fn")
        name = self.expect("IDENT", label="function name").value
        self.expect("SYMBOL", "(")
        params = []
        if not self.check("SYMBOL", ")"):
            params.append(self.parse_param())
            while self.match("SYMBOL", ","):
                params.append(self.parse_param())
        self.expect("SYMBOL", ")")

        return_type = None
        effects = None
        if self.match("SYMBOL", "->"):
            return_type = self.parse_type_expr()
            if isinstance(return_type, ast.TypeEffect):
                effects = return_type.effects
                return_type = return_type.base
            else:
                effects = self.parse_effect_annotation()

        constraints: list[ast.TypeConstraint] = []
        if self.match("KEYWORD", "where"):
            constraints.append(self.parse_type_constraint())
            while self.match("SYMBOL", ","):
                constraints.append(self.parse_type_constraint())

        self.expect("SYMBOL", "=")
        body = self.parse_expr()
        return self.mark(
            ast.FnDecl(
                name=name,
                params=params,
                return_type=return_type,
                effects=effects,
                constraints=constraints,
                body=body,
            ),
            start,
        )

    def parse_class_decl(self) -> ast.ClassDecl:
        start = self.expect("KEYWORD", "class")
        name = self.expect("IDENT", label="class name").value
        type_params: list[str] = []
        while self.check("IDENT"):
            type_params.append(self.advance().value)
        if not type_params:
            t = self.current()
            raise ParseError(f"Expected at least one class type parameter at {t.line}:{t.column}")
        methods: list[ast.ClassMethodSig] = []
        if self.match("SYMBOL", "{"):
            while not self.check("SYMBOL", "}"):
                methods.append(self.parse_class_method_sig())
            self.expect("SYMBOL", "}")
        return self.mark(ast.ClassDecl(name=name, type_params=type_params, methods=methods), start)

    def parse_instance_decl(self) -> ast.InstanceDecl:
        start = self.expect("KEYWORD", "instance")
        constraint = self.parse_type_constraint()
        methods: list[ast.InstanceMethodImpl] = []
        if self.match("SYMBOL", "{"):
            while not self.check("SYMBOL", "}"):
                methods.append(self.parse_instance_method_impl())
            self.expect("SYMBOL", "}")
        return self.mark(ast.InstanceDecl(constraint=constraint, methods=methods), start)

    def parse_class_method_sig(self) -> ast.ClassMethodSig:
        start = self.expect("KEYWORD", "fn")
        name = self.expect("IDENT", label="method name").value
        self.expect("SYMBOL", "(")
        params: list[ast.Param] = []
        if not self.check("SYMBOL", ")"):
            params.append(self.parse_param(require_type=True))
            while self.match("SYMBOL", ","):
                params.append(self.parse_param(require_type=True))
        self.expect("SYMBOL", ")")
        self.expect("SYMBOL", "->")
        return_type = self.parse_type_expr()
        if isinstance(return_type, ast.TypeEffect):
            effects = return_type.effects
            return_type = return_type.base
        else:
            effects = self.parse_effect_annotation()
        return self.mark(
            ast.ClassMethodSig(name=name, params=params, return_type=return_type, effects=effects),
            start,
        )

    def parse_instance_method_impl(self) -> ast.InstanceMethodImpl:
        start = self.expect("KEYWORD", "fn")
        name = self.expect("IDENT", label="method name").value
        self.expect("SYMBOL", "(")
        params: list[ast.Param] = []
        if not self.check("SYMBOL", ")"):
            params.append(self.parse_param())
            while self.match("SYMBOL", ","):
                params.append(self.parse_param())
        self.expect("SYMBOL", ")")
        return_type = None
        effects = None
        if self.match("SYMBOL", "->"):
            return_type = self.parse_type_expr()
            if isinstance(return_type, ast.TypeEffect):
                effects = return_type.effects
                return_type = return_type.base
            else:
                effects = self.parse_effect_annotation()
        self.expect("SYMBOL", "=")
        body = self.parse_expr()
        return self.mark(
            ast.InstanceMethodImpl(name=name, params=params, return_type=return_type, effects=effects, body=body),
            start,
        )

    def parse_type_constraint(self) -> ast.TypeConstraint:
        start = self.expect("IDENT", label="class name")
        class_name = start.value
        args: list[ast.TypeExpr] = []
        while self._starts_type_atom():
            args.append(self.parse_type_atom())
        if not args:
            t = self.current()
            raise ParseError(f"Expected at least one constraint argument at {t.line}:{t.column}")
        return self.mark(ast.TypeConstraint(class_name=class_name, args=args), start)

    def parse_param(self, require_type: bool = False) -> ast.Param:
        tok = self.expect("IDENT", label="parameter name")
        name = tok.value
        typ = None
        if self.match("SYMBOL", ":"):
            typ = self.parse_type_expr()
        elif require_type:
            t = self.current()
            raise ParseError(f"Expected ':' after parameter name at {t.line}:{t.column}")
        return self.mark(ast.Param(name=name, type_expr=typ), tok)

    def parse_let_decl(self) -> ast.LetDecl:
        start = self.expect("KEYWORD", "let")
        name = self.expect("IDENT", label="binding name").value
        self.expect("SYMBOL", "=")
        value = self.parse_expr()
        return self.mark(ast.LetDecl(name=name, value=value), start)

    def parse_expr(self):
        if self.check("KEYWORD", "if"):
            start = self.advance()
            cond = self.parse_expr()
            self.expect("KEYWORD", "then")
            then_b = self.parse_expr()
            self.expect("KEYWORD", "else")
            else_b = self.parse_expr()
            return self.mark(ast.IfExpr(cond, then_b, else_b), start)

        if self.check("KEYWORD", "match"):
            start = self.advance()
            scrutinee = self.parse_expr()
            self.expect("KEYWORD", "with")
            branches = []
            while self.match("SYMBOL", "|"):
                pattern = self.parse_pattern()
                self.expect("SYMBOL", "->")
                value = self.parse_expr()
                branches.append(self.mark(ast.MatchBranch(pattern=pattern, value=value), Token("META", "", pattern.line, pattern.column)))
            if not branches:
                t = self.current()
                raise ParseError(f"Expected at least one match branch at {t.line}:{t.column}")
            return self.mark(ast.MatchExpr(scrutinee=scrutinee, branches=branches), start)

        return self.parse_pipe()

    def parse_pipe(self):
        expr = self.parse_logical_or()
        while self.match("SYMBOL", "|>"):
            op = self.tokens[self.i - 1]
            rhs = self.parse_logical_or()
            expr = self.mark(self._pipe_into_call(expr, rhs), op)
        return expr

    def _pipe_into_call(self, left: ast.Expr, rhs: ast.Expr) -> ast.Expr:
        if isinstance(rhs, ast.CallExpr):
            return ast.CallExpr(callee=rhs.callee, args=[left] + rhs.args)
        return ast.CallExpr(callee=rhs, args=[left])

    def parse_logical_or(self):
        expr = self.parse_logical_and()
        while self.match("SYMBOL", "||"):
            op = self.tokens[self.i - 1]
            expr = self.mark(ast.BinaryExpr(op="||", left=expr, right=self.parse_logical_and()), op)
        return expr

    def parse_logical_and(self):
        expr = self.parse_equality()
        while self.match("SYMBOL", "&&"):
            op = self.tokens[self.i - 1]
            expr = self.mark(ast.BinaryExpr(op="&&", left=expr, right=self.parse_equality()), op)
        return expr

    def parse_equality(self):
        expr = self.parse_comparison()
        while self.check("SYMBOL") and self.current().value in {"==", "!="}:
            tok = self.advance()
            expr = self.mark(ast.BinaryExpr(op=tok.value, left=expr, right=self.parse_comparison()), tok)
        return expr

    def parse_comparison(self):
        expr = self.parse_term()
        while self.check("SYMBOL") and self.current().value in {"<", "<=", ">", ">="}:
            tok = self.advance()
            expr = self.mark(ast.BinaryExpr(op=tok.value, left=expr, right=self.parse_term()), tok)
        return expr

    def parse_term(self):
        expr = self.parse_factor()
        while self.check("SYMBOL") and self.current().value in {"+", "-", "++"}:
            tok = self.advance()
            right = self.parse_factor()
            if tok.value == "++":
                expr = self.mark(
                    ast.CallExpr(
                        callee=self.mark(ast.VarExpr(name="append"), tok),
                        args=[expr, right],
                    ),
                    tok,
                )
            else:
                expr = self.mark(ast.BinaryExpr(op=tok.value, left=expr, right=right), tok)
        return expr

    def parse_factor(self):
        expr = self.parse_composition()
        while self.check("SYMBOL") and self.current().value in {"*", "/"}:
            tok = self.advance()
            expr = self.mark(ast.BinaryExpr(op=tok.value, left=expr, right=self.parse_composition()), tok)
        return expr

    def parse_composition(self):
        expr = self.parse_unary()
        if self.match("SYMBOL", ">>"):
            op = self.tokens[self.i - 1]
            return self.mark(ast.BinaryExpr(op=">>", left=expr, right=self.parse_composition()), op)
        return expr

    def parse_unary(self):
        if self.match("SYMBOL", "-"):
            op = self.tokens[self.i - 1]
            return self.mark(ast.UnaryExpr(op="-", operand=self.parse_unary()), op)
        return self.parse_call()

    def parse_call(self):
        expr = self.parse_primary()
        while self.match("SYMBOL", "("):
            args = []
            if not self.check("SYMBOL", ")"):
                args.append(self.parse_expr())
                while self.match("SYMBOL", ","):
                    args.append(self.parse_expr())
            self.expect("SYMBOL", ")")
            open_tok = self.tokens[self.i - 1]
            expr = self.mark(ast.CallExpr(callee=expr, args=args), open_tok)
        return expr

    def parse_primary(self):
        if self.match("SYMBOL", "\\"):
            start = self.tokens[self.i - 1]
            params: list[ast.Param] = []
            if self.match("SYMBOL", "("):
                if self.check("SYMBOL", ")"):
                    t = self.current()
                    raise ParseError(f"Lambda parameter list cannot be empty at {t.line}:{t.column}")
                params.append(self.parse_param())
                while self.match("SYMBOL", ","):
                    params.append(self.parse_param())
                self.expect("SYMBOL", ")")
            else:
                params.append(self.parse_lambda_shorthand_param())
            self.expect("SYMBOL", "->")
            body = self.parse_expr()
            return self.mark(ast.LambdaExpr(params=params, body=body), start)
        if self.match("SYMBOL", "["):
            open_tok = self.tokens[self.i - 1]
            items = []
            if not self.check("SYMBOL", "]"):
                items.append(self.parse_expr())
                while self.match("SYMBOL", ","):
                    items.append(self.parse_expr())
            self.expect("SYMBOL", "]")
            # Desugar list literal [a, b, c] into Cons(a, Cons(b, Cons(c, Nil))).
            out = self.mark(ast.VarExpr(name="Nil"), open_tok)
            for item in reversed(items):
                out = self.mark(
                    ast.CallExpr(callee=self.mark(ast.VarExpr(name="Cons"), open_tok), args=[item, out]),
                    open_tok,
                )
            return out
        if self.match("SYMBOL", "{"):
            open_tok = self.tokens[self.i - 1]
            entries: list[tuple[str, ast.Expr]] = []
            if not self.check("SYMBOL", "}"):
                entries.append(self.parse_dict_entry())
                while self.match("SYMBOL", ","):
                    if self.check("SYMBOL", "}"):
                        break
                    entries.append(self.parse_dict_entry())
            self.expect("SYMBOL", "}")
            # Desugar dict literal {foo: 1, bar: 2} into nested dict_set calls.
            out: ast.Expr = self.mark(
                ast.CallExpr(callee=self.mark(ast.VarExpr(name="dict_empty"), open_tok), args=[]),
                open_tok,
            )
            for key, value in entries:
                out = self.mark(
                    ast.CallExpr(
                        callee=self.mark(ast.VarExpr(name="dict_set"), open_tok),
                        args=[out, self.mark(ast.StringExpr(value=key), open_tok), value],
                    ),
                    open_tok,
                )
            return out
        if self.check("INT"):
            tok = self.advance()
            return self.mark(ast.IntExpr(value=int(tok.value)), tok)
        if self.check("STRING"):
            tok = self.advance()
            return self.mark(ast.StringExpr(value=tok.value), tok)
        if self.match("KEYWORD", "true"):
            tok = self.tokens[self.i - 1]
            return self.mark(ast.BoolExpr(value=True), tok)
        if self.match("KEYWORD", "false"):
            tok = self.tokens[self.i - 1]
            return self.mark(ast.BoolExpr(value=False), tok)
        if self.check("IDENT"):
            tok = self.advance()
            return self.mark(ast.VarExpr(name=tok.value), tok)
        if self.match("SYMBOL", "("):
            open_tok = self.tokens[self.i - 1]
            expr = self.parse_expr()
            if self.match("SYMBOL", ","):
                items = [expr, self.parse_expr()]
                while self.match("SYMBOL", ","):
                    items.append(self.parse_expr())
                self.expect("SYMBOL", ")")
                return self.mark(ast.TupleExpr(items=items), open_tok)
            self.expect("SYMBOL", ")")
            return self.mark(expr, open_tok)
        t = self.current()
        raise ParseError(f"Expected expression at {t.line}:{t.column}, got {t.kind} {t.value!r}")

    def parse_pattern(self):
        if self.match("IDENT", "_"):
            tok = self.tokens[self.i - 1]
            return self.mark(ast.WildcardPattern(), tok)
        if self.check("INT"):
            tok = self.advance()
            return self.mark(ast.IntPattern(value=int(tok.value)), tok)
        if self.match("KEYWORD", "true"):
            tok = self.tokens[self.i - 1]
            return self.mark(ast.BoolPattern(value=True), tok)
        if self.match("KEYWORD", "false"):
            tok = self.tokens[self.i - 1]
            return self.mark(ast.BoolPattern(value=False), tok)
        if self.check("STRING"):
            tok = self.advance()
            return self.mark(ast.StringPattern(value=tok.value), tok)
        if self.match("SYMBOL", "("):
            open_tok = self.tokens[self.i - 1]
            inner = self.parse_pattern()
            if self.match("SYMBOL", ","):
                items = [inner, self.parse_pattern()]
                while self.match("SYMBOL", ","):
                    items.append(self.parse_pattern())
                self.expect("SYMBOL", ")")
                return self.mark(ast.TuplePattern(items=items), open_tok)
            self.expect("SYMBOL", ")")
            return self.mark(inner, open_tok)

        name_token = self.expect("IDENT", label="pattern")
        name = name_token.value
        if self._is_constructor_name(name):
            args = []
            while self._starts_pattern_atom():
                args.append(self.parse_pattern_atom())
            return self.mark(ast.ConstructorPattern(name=name, args=args), name_token)
        return self.mark(ast.VarPattern(name=name), name_token)

    def parse_pattern_atom(self):
        if self.match("IDENT", "_"):
            tok = self.tokens[self.i - 1]
            return self.mark(ast.WildcardPattern(), tok)
        if self.check("INT"):
            tok = self.advance()
            return self.mark(ast.IntPattern(value=int(tok.value)), tok)
        if self.match("KEYWORD", "true"):
            tok = self.tokens[self.i - 1]
            return self.mark(ast.BoolPattern(value=True), tok)
        if self.match("KEYWORD", "false"):
            tok = self.tokens[self.i - 1]
            return self.mark(ast.BoolPattern(value=False), tok)
        if self.check("STRING"):
            tok = self.advance()
            return self.mark(ast.StringPattern(value=tok.value), tok)
        if self.check("IDENT"):
            tok = self.advance()
            name = tok.value
            if self._is_constructor_name(name):
                return self.mark(ast.ConstructorPattern(name=name, args=[]), tok)
            return self.mark(ast.VarPattern(name=name), tok)
        if self.match("SYMBOL", "("):
            open_tok = self.tokens[self.i - 1]
            inner = self.parse_pattern()
            if self.match("SYMBOL", ","):
                items = [inner, self.parse_pattern()]
                while self.match("SYMBOL", ","):
                    items.append(self.parse_pattern())
                self.expect("SYMBOL", ")")
                return self.mark(ast.TuplePattern(items=items), open_tok)
            self.expect("SYMBOL", ")")
            return self.mark(inner, open_tok)
        t = self.current()
        raise ParseError(f"Expected pattern atom at {t.line}:{t.column}")

    def parse_dict_entry(self) -> tuple[str, ast.Expr]:
        if self.check("IDENT"):
            key = self.advance().value
        elif self.check("STRING"):
            key = self.advance().value
        else:
            t = self.current()
            raise ParseError(f"Expected dict key at {t.line}:{t.column}, got {t.kind} {t.value!r}")
        self.expect("SYMBOL", ":")
        return key, self.parse_expr()

    def parse_lambda_shorthand_param(self) -> ast.Param:
        tok = self.expect("IDENT", label="parameter name")
        typ = None
        if self.match("SYMBOL", ":"):
            typ = self.parse_type_apply()
        return self.mark(ast.Param(name=tok.value, type_expr=typ), tok)

    def parse_type_expr(self):
        left = self.parse_type_apply()
        if self.match("SYMBOL", "->"):
            right = self.parse_type_expr()
            effects = None
            if isinstance(right, ast.TypeEffect):
                effects = right.effects
                right = right.base
            else:
                effects = self.parse_effect_annotation()
            return ast.TypeArrow(left=left, right=right, effects=effects)
        effects = self.parse_effect_annotation()
        if effects is not None:
            return ast.TypeEffect(base=left, effects=effects)
        return left

    def parse_effect_annotation(self) -> tuple[str, ...] | None:
        if not self.match("SYMBOL", "!"):
            return None
        self.expect("SYMBOL", "{")
        effects: list[str] = []
        if not self.check("SYMBOL", "}"):
            effects.append(self.expect("IDENT", label="effect name").value)
            while self.match("SYMBOL", ","):
                effects.append(self.expect("IDENT", label="effect name").value)
        self.expect("SYMBOL", "}")
        return tuple(effects)

    def parse_type_apply(self):
        typ = self.parse_type_atom()
        while self._starts_type_atom():
            typ = ast.TypeApply(base=typ, arg=self.parse_type_atom())
        return typ

    def parse_type_atom(self):
        if self.check("IDENT"):
            return ast.TypeName(name=self.advance().value)
        if self.match("SYMBOL", "("):
            open_tok = self.tokens[self.i - 1]
            inner = self.parse_type_expr()
            if self.match("SYMBOL", ","):
                items = [inner, self.parse_type_expr()]
                while self.match("SYMBOL", ","):
                    items.append(self.parse_type_expr())
                self.expect("SYMBOL", ")")
                return self.mark(ast.TupleType(items=items), open_tok)
            self.expect("SYMBOL", ")")
            return inner
        t = self.current()
        raise ParseError(f"Expected type at {t.line}:{t.column}, got {t.kind} {t.value!r}")

    def _starts_type_atom(self) -> bool:
        return self.check("IDENT") or self.check("SYMBOL", "(")

    def _starts_pattern_atom(self) -> bool:
        if self.check("INT") or self.check("STRING"):
            return True
        if self.check("IDENT"):
            return True
        if self.check("SYMBOL", "("):
            return True
        if self.check("KEYWORD") and self.current().value in {"true", "false"}:
            return True
        return False

    def _is_constructor_name(self, name: str) -> bool:
        leaf = name.rsplit(".", 1)[-1]
        return bool(leaf) and leaf[0].isupper()


def parse(source: str) -> ast.Program:
    parser = Parser(tokenize(source))
    program = parser.parse_program()
    parser.expect("EOF")
    return program
