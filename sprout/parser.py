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

    def parse_program(self) -> ast.Program:
        decls = []
        while not self.check("EOF"):
            decls.append(self.parse_declaration())
        return ast.Program(decls)

    def parse_declaration(self):
        if self.check("KEYWORD", "type"):
            return self.parse_type_decl()
        if self.check("KEYWORD", "fn"):
            return self.parse_fn_decl()
        if self.check("KEYWORD", "let"):
            return self.parse_let_decl()
        t = self.current()
        raise ParseError(f"Expected top-level declaration at {t.line}:{t.column}, got {t.value!r}")

    def parse_type_decl(self) -> ast.TypeDecl:
        self.expect("KEYWORD", "type")
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
        return ast.TypeDecl(name=name, type_params=params, constructors=constructors)

    def parse_type_constructor(self) -> ast.TypeConstructor:
        name = self.expect("IDENT", label="constructor name").value
        args = []
        while self._starts_type_atom():
            args.append(self.parse_type_atom())
        return ast.TypeConstructor(name=name, args=args)

    def parse_fn_decl(self) -> ast.FnDecl:
        self.expect("KEYWORD", "fn")
        name = self.expect("IDENT", label="function name").value
        self.expect("SYMBOL", "(")
        params = []
        if not self.check("SYMBOL", ")"):
            params.append(self.parse_param())
            while self.match("SYMBOL", ","):
                params.append(self.parse_param())
        self.expect("SYMBOL", ")")

        return_type = None
        if self.match("SYMBOL", "->"):
            return_type = self.parse_type_expr()

        self.expect("SYMBOL", "=")
        body = self.parse_expr()
        return ast.FnDecl(name=name, params=params, return_type=return_type, body=body)

    def parse_param(self) -> ast.Param:
        name = self.expect("IDENT", label="parameter name").value
        self.expect("SYMBOL", ":")
        typ = self.parse_type_expr()
        return ast.Param(name=name, type_expr=typ)

    def parse_let_decl(self) -> ast.LetDecl:
        self.expect("KEYWORD", "let")
        name = self.expect("IDENT", label="binding name").value
        self.expect("SYMBOL", "=")
        value = self.parse_expr()
        return ast.LetDecl(name=name, value=value)

    def parse_expr(self):
        if self.match("KEYWORD", "if"):
            cond = self.parse_expr()
            self.expect("KEYWORD", "then")
            then_b = self.parse_expr()
            self.expect("KEYWORD", "else")
            else_b = self.parse_expr()
            return ast.IfExpr(cond, then_b, else_b)

        if self.match("KEYWORD", "match"):
            scrutinee = self.parse_expr()
            self.expect("KEYWORD", "with")
            branches = []
            while self.match("SYMBOL", "|"):
                pattern = self.parse_pattern()
                self.expect("SYMBOL", "->")
                value = self.parse_expr()
                branches.append(ast.MatchBranch(pattern=pattern, value=value))
            if not branches:
                t = self.current()
                raise ParseError(f"Expected at least one match branch at {t.line}:{t.column}")
            return ast.MatchExpr(scrutinee=scrutinee, branches=branches)

        return self.parse_logical_or()

    def parse_logical_or(self):
        expr = self.parse_logical_and()
        while self.match("SYMBOL", "||"):
            expr = ast.BinaryExpr(op="||", left=expr, right=self.parse_logical_and())
        return expr

    def parse_logical_and(self):
        expr = self.parse_equality()
        while self.match("SYMBOL", "&&"):
            expr = ast.BinaryExpr(op="&&", left=expr, right=self.parse_equality())
        return expr

    def parse_equality(self):
        expr = self.parse_comparison()
        while self.check("SYMBOL") and self.current().value in {"==", "!="}:
            op = self.advance().value
            expr = ast.BinaryExpr(op=op, left=expr, right=self.parse_comparison())
        return expr

    def parse_comparison(self):
        expr = self.parse_term()
        while self.check("SYMBOL") and self.current().value in {"<", "<=", ">", ">="}:
            op = self.advance().value
            expr = ast.BinaryExpr(op=op, left=expr, right=self.parse_term())
        return expr

    def parse_term(self):
        expr = self.parse_factor()
        while self.check("SYMBOL") and self.current().value in {"+", "-"}:
            op = self.advance().value
            expr = ast.BinaryExpr(op=op, left=expr, right=self.parse_factor())
        return expr

    def parse_factor(self):
        expr = self.parse_unary()
        while self.check("SYMBOL") and self.current().value in {"*", "/"}:
            op = self.advance().value
            expr = ast.BinaryExpr(op=op, left=expr, right=self.parse_unary())
        return expr

    def parse_unary(self):
        if self.match("SYMBOL", "-"):
            return ast.UnaryExpr(op="-", operand=self.parse_unary())
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
            expr = ast.CallExpr(callee=expr, args=args)
        return expr

    def parse_primary(self):
        if self.check("INT"):
            return ast.IntExpr(value=int(self.advance().value))
        if self.check("STRING"):
            return ast.StringExpr(value=self.advance().value)
        if self.match("KEYWORD", "true"):
            return ast.BoolExpr(value=True)
        if self.match("KEYWORD", "false"):
            return ast.BoolExpr(value=False)
        if self.check("IDENT"):
            return ast.VarExpr(name=self.advance().value)
        if self.match("SYMBOL", "("):
            expr = self.parse_expr()
            self.expect("SYMBOL", ")")
            return expr
        t = self.current()
        raise ParseError(f"Expected expression at {t.line}:{t.column}, got {t.kind} {t.value!r}")

    def parse_pattern(self):
        if self.match("IDENT", "_"):
            return ast.WildcardPattern()
        if self.check("INT"):
            return ast.IntPattern(value=int(self.advance().value))
        if self.match("KEYWORD", "true"):
            return ast.BoolPattern(value=True)
        if self.match("KEYWORD", "false"):
            return ast.BoolPattern(value=False)
        if self.check("STRING"):
            return ast.StringPattern(value=self.advance().value)

        name_token = self.expect("IDENT", label="pattern")
        name = name_token.value
        if name and name[0].isupper():
            args = []
            while self._starts_pattern_atom():
                args.append(self.parse_pattern_atom())
            return ast.ConstructorPattern(name=name, args=args)
        return ast.VarPattern(name=name)

    def parse_pattern_atom(self):
        if self.match("IDENT", "_"):
            return ast.WildcardPattern()
        if self.check("INT"):
            return ast.IntPattern(value=int(self.advance().value))
        if self.match("KEYWORD", "true"):
            return ast.BoolPattern(value=True)
        if self.match("KEYWORD", "false"):
            return ast.BoolPattern(value=False)
        if self.check("STRING"):
            return ast.StringPattern(value=self.advance().value)
        if self.check("IDENT"):
            name = self.advance().value
            if name and name[0].isupper():
                return ast.ConstructorPattern(name=name, args=[])
            return ast.VarPattern(name=name)
        if self.match("SYMBOL", "("):
            inner = self.parse_pattern()
            self.expect("SYMBOL", ")")
            return inner
        t = self.current()
        raise ParseError(f"Expected pattern atom at {t.line}:{t.column}")

    def parse_type_expr(self):
        left = self.parse_type_apply()
        if self.match("SYMBOL", "->"):
            right = self.parse_type_expr()
            return ast.TypeArrow(left=left, right=right)
        return left

    def parse_type_apply(self):
        typ = self.parse_type_atom()
        while self._starts_type_atom():
            typ = ast.TypeApply(base=typ, arg=self.parse_type_atom())
        return typ

    def parse_type_atom(self):
        if self.check("IDENT"):
            return ast.TypeName(name=self.advance().value)
        if self.match("SYMBOL", "("):
            inner = self.parse_type_expr()
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


def parse(source: str) -> ast.Program:
    parser = Parser(tokenize(source))
    program = parser.parse_program()
    parser.expect("EOF")
    return program
