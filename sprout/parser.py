from __future__ import annotations

from dataclasses import dataclass
import re

from . import ast
from .tokenizer import Token, tokenize


class ParseError(ValueError):
    pass


_ANNOTATION_RE = re.compile(r"^\s*#@([A-Za-z_][A-Za-z0-9_]*)(?:\s+(.*))?\s*$")
_DECL_START_RE = re.compile(r"^\s*(?:export\s+)?(?:fn|type|let|class|instance)\b")
_ANNOTATION_KINDS = {"unstable", "temporary", "wip", "deprecated"}


def extract_decl_annotations(source: str) -> dict[int, tuple[ast.DeclAnnotation, ...]]:
    lines = source.splitlines()
    top_level_indent = min(
        (len(line) - len(line.lstrip()) for line in lines if line.strip() != ""),
        default=0,
    )
    pending: list[tuple[int, ast.DeclAnnotation]] = []
    out: dict[int, tuple[ast.DeclAnnotation, ...]] = {}
    for line_no, line in enumerate(lines, start=1):
        stripped = line.strip()
        if stripped == "" or (stripped.startswith("#") and not stripped.startswith("#@")):
            continue
        indent = len(line) - len(line.lstrip())
        if indent != top_level_indent:
            continue
        match = _ANNOTATION_RE.match(line)
        if match is not None:
            kind = match.group(1)
            message = match.group(2).strip() if match.group(2) is not None else None
            if kind not in _ANNOTATION_KINDS:
                raise ParseError(f"Unknown declaration annotation {kind!r} at {line_no}:1")
            if kind != "deprecated" and message is not None:
                raise ParseError(f"Only #@deprecated accepts a message at {line_no}:1")
            pending.append((line_no, ast.DeclAnnotation(kind=kind, message=message)))
            continue
        if pending and _DECL_START_RE.match(line):
            out[line_no] = tuple(annotation for _, annotation in pending)
            pending = []
            continue
        if pending:
            first_line = pending[0][0]
            raise ParseError(
                f"Declaration annotation at {first_line}:1 must be followed by a top-level declaration"
            )
    if pending:
        first_line = pending[0][0]
        raise ParseError(f"Declaration annotation at {first_line}:1 must be followed by a top-level declaration")
    return out


@dataclass
class Parser:
    tokens: list[Token]
    i: int = 0
    pipe_tmp_counter: int = 0

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
        if self.check("SYMBOL", "{"):
            self.advance()
            fields: list[ast.RecordFieldDecl] = []
            if not self.check("SYMBOL", "}"):
                fields.append(self.parse_record_field_decl())
                while self.match("SYMBOL", ","):
                    if self.check("SYMBOL", "}"):
                        break
                    fields.append(self.parse_record_field_decl())
            self.expect("SYMBOL", "}")
            return self.mark(ast.RecordDecl(name=name, type_params=params, fields=fields), start)

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
        if self.match("KEYWORD", "where"):
            body = self._desugar_local_where(body, self.parse_local_where_bindings())
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

    def parse_local_where_bindings(self) -> list[tuple[ast.Pattern, Token, ast.Expr]]:
        bindings: list[tuple[ast.Pattern, Token, ast.Expr]] = []
        seen: set[str] = set()
        if not self._starts_local_where_binding():
            t = self.current()
            raise ParseError(f"Expected at least one local binding after where at {t.line}:{t.column}")
        while self._starts_local_where_binding():
            start = self.current()
            pattern = self.parse_pattern()
            bound_names = self._local_where_pattern_names(pattern)
            for name in bound_names:
                if name in seen:
                    raise ParseError(f"Duplicate local binding {name!r} at {start.line}:{start.column}")
                seen.add(name)
            self.expect("SYMBOL", "=")
            bindings.append((pattern, start, self.parse_expr()))
        return bindings

    def _desugar_local_where(self, body: ast.Expr, bindings: list[tuple[ast.Pattern, Token, ast.Expr]]) -> ast.Expr:
        out = body
        for pattern, start, value in reversed(bindings):
            if isinstance(pattern, ast.VarPattern):
                param = self.mark(ast.Param(name=pattern.name, type_expr=None), start)
                lam = self.mark(ast.LambdaExpr(params=[param], body=out), start)
                out = self.mark(ast.CallExpr(callee=lam, args=[value]), start)
            else:
                tmp_name = self._fresh_local_where_tmp_name(out, value)
                tmp_param = self.mark(ast.Param(name=tmp_name, type_expr=None), start)
                tmp_expr = self.mark(ast.VarExpr(name=tmp_name), start)
                branch = self.mark(ast.MatchBranch(pattern=pattern, value=out), start)
                match_expr = self.mark(ast.MatchExpr(scrutinee=tmp_expr, branches=[branch]), start)
                lam = self.mark(ast.LambdaExpr(params=[tmp_param], body=match_expr), start)
                out = self.mark(ast.CallExpr(callee=lam, args=[value]), start)
        return out

    def _local_where_pattern_names(self, pattern: ast.Pattern) -> list[str]:
        if isinstance(pattern, ast.VarPattern):
            return [pattern.name]
        if isinstance(pattern, ast.WildcardPattern):
            return []
        if isinstance(pattern, ast.TuplePattern):
            names: list[str] = []
            for item in pattern.items:
                names.extend(self._local_where_pattern_names(item))
            return names
        raise ParseError(
            f"Local where bindings support only names, `_`, and tuple patterns at {pattern.line}:{pattern.column}"
        )

    def _fresh_local_where_tmp_name(self, left: ast.Expr, right: ast.Expr) -> str:
        used = self._expr_names(left) | self._expr_names(right)
        while True:
            name = f"__sprout_where_{self.pipe_tmp_counter}"
            self.pipe_tmp_counter += 1
            if name not in used:
                return name

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

    def parse_record_field_decl(self) -> ast.RecordFieldDecl:
        start = self.expect("IDENT", label="record field name")
        self.expect("SYMBOL", ":")
        return self.mark(ast.RecordFieldDecl(name=start.value, type_expr=self.parse_type_expr()), start)

    def parse_expr(self):
        if self.check("KEYWORD", "do"):
            return self.parse_do_expr()
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

    def parse_do_expr(self) -> ast.DoExpr:
        start = self.expect("KEYWORD", "do")
        steps: list[ast.DoStep] = []
        block_indent: int | None = None

        while True:
            if self.check("EOF"):
                break
            token = self.current()
            if token.line == start.line:
                break
            if block_indent is None:
                block_indent = token.column
            elif token.column < block_indent:
                break
            elif token.column > block_indent:
                raise ParseError(f"Unexpected indentation in do block at {token.line}:{token.column}")

            step_tokens = self._consume_do_step_tokens(block_indent)
            if not step_tokens:
                break
            steps.append(self._parse_do_step(step_tokens))

        if not steps:
            raise ParseError(f"Expected at least one do step at {start.line}:{start.column}")
        return self.mark(ast.DoExpr(steps=steps), start)

    def _consume_do_step_tokens(self, block_indent: int) -> list[Token]:
        start_index = self.i
        depth = 0
        index = self.i
        while index < len(self.tokens):
            token = self.tokens[index]
            if token.kind == "EOF":
                break
            if index > start_index and depth == 0 and token.line > self.tokens[index - 1].line:
                if token.column < block_indent:
                    break
                if token.column == block_indent and self._looks_like_do_step_start(index):
                    break
            if token.kind == "SYMBOL":
                if token.value in {"(", "[", "{"}:
                    depth += 1
                elif token.value in {")", "]", "}"}:
                    depth = max(0, depth - 1)
            index += 1
        self.i = index
        return self.tokens[start_index:index]

    def _looks_like_do_step_start(self, index: int) -> bool:
        token = self.tokens[index]
        if token.kind == "KEYWORD" and token.value == "let":
            return True
        if token.kind == "IDENT" and index + 1 < len(self.tokens):
            next_token = self.tokens[index + 1]
            if next_token.kind == "SYMBOL" and next_token.value == "<-":
                return True
        return self._token_starts_expr(token)

    def _token_starts_expr(self, token: Token) -> bool:
        if token.kind in {"IDENT", "INT", "STRING", "CHAR"}:
            return True
        if token.kind == "KEYWORD" and token.value in {"if", "match", "do", "true", "false"}:
            return True
        return token.kind == "SYMBOL" and token.value in {"\\", "[", "{", "(", "-"}

    def _parse_do_step(self, tokens: list[Token]) -> ast.DoStep:
        last = tokens[-1]
        eof = Token("EOF", "", last.line, last.column + len(last.value))
        parser = Parser(tokens + [eof], pipe_tmp_counter=self.pipe_tmp_counter)
        if parser.check("KEYWORD", "let"):
            start = parser.advance()
            name_token = parser.expect("IDENT", label="do let binding name")
            parser.expect("SYMBOL", "=")
            value = parser.parse_expr()
            parser.expect("EOF")
            self.pipe_tmp_counter = parser.pipe_tmp_counter
            return self.mark(ast.DoLetStep(name=name_token.value, value=value), start)
        if parser._starts_pattern_atom() or parser.check("IDENT", "_"):
            checkpoint = parser.i
            try:
                pattern = parser.parse_pattern()
                parser.expect("SYMBOL", "<-")
                value = parser.parse_expr()
                parser.expect("EOF")
                self.pipe_tmp_counter = parser.pipe_tmp_counter
                return self.mark(ast.DoBindStep(pattern=pattern, value=value), pattern)
            except ParseError:
                parser.i = checkpoint

        value = parser.parse_expr()
        parser.expect("EOF")
        self.pipe_tmp_counter = parser.pipe_tmp_counter
        return self.mark(ast.DoExprStep(value=value), tokens[0])

    def parse_pipe(self):
        expr = self.parse_logical_or()
        while self.match("SYMBOL", "|>"):
            op = self.tokens[self.i - 1]
            rhs = self.parse_logical_or()
            expr = self.mark(self._pipe_into_call(expr, rhs), op)
        return expr

    def _pipe_into_call(self, left: ast.Expr, rhs: ast.Expr) -> ast.Expr:
        if isinstance(rhs, ast.CallExpr):
            tmp_name = self._fresh_pipe_tmp_name(left, rhs)
            tmp_expr = ast.attach_loc(ast.VarExpr(name=tmp_name), getattr(left, "line", 0), getattr(left, "column", 0))
            body = ast.CallExpr(callee=rhs.callee, args=[*rhs.args, tmp_expr])
            lam = ast.attach_loc(
                ast.LambdaExpr(params=[ast.Param(name=tmp_name, type_expr=None)], body=body),
                getattr(left, "line", 0),
                getattr(left, "column", 0),
            )
            return ast.CallExpr(callee=lam, args=[left])
        return ast.CallExpr(callee=rhs, args=[left])

    def _fresh_pipe_tmp_name(self, left: ast.Expr, rhs: ast.Expr) -> str:
        used = self._expr_names(left) | self._expr_names(rhs)
        while True:
            name = f"__sprout_pipe_{self.pipe_tmp_counter}"
            self.pipe_tmp_counter += 1
            if name not in used:
                return name

    def _expr_names(self, expr: ast.Expr) -> set[str]:
        names: set[str] = set()

        def walk(node: ast.Expr) -> None:
            if isinstance(node, ast.VarExpr):
                names.add(node.name)
                return
            if isinstance(node, ast.LambdaExpr):
                for param in node.params:
                    names.add(param.name)
                walk(node.body)
                return
            if isinstance(node, ast.CallExpr):
                walk(node.callee)
                for arg in node.args:
                    walk(arg)
                return
            if isinstance(node, ast.IfExpr):
                walk(node.condition)
                walk(node.then_branch)
                walk(node.else_branch)
                return
            if isinstance(node, ast.MatchExpr):
                walk(node.scrutinee)
                for branch in node.branches:
                    walk(branch.value)
                return
            do_expr = getattr(ast, "DoExpr", None)
            do_bind_step = getattr(ast, "DoBindStep", None)
            do_expr_step = getattr(ast, "DoExprStep", None)
            if do_expr is not None and isinstance(node, do_expr):
                for step in node.steps:
                    if do_bind_step is not None and isinstance(step, do_bind_step):
                        walk(step.value)
                    elif do_expr_step is not None and isinstance(step, do_expr_step):
                        walk(step.value)
                return
            if isinstance(node, ast.BinaryExpr):
                walk(node.left)
                walk(node.right)
                return
            if isinstance(node, ast.IntRangeExpr):
                walk(node.start)
                walk(node.end)
                return
            if isinstance(node, ast.UnaryExpr):
                walk(node.operand)
                return
            if isinstance(node, ast.TupleExpr):
                for item in node.items:
                    walk(item)

        walk(expr)
        return names

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
        expr = self.parse_range()
        while self.check("SYMBOL") and self.current().value in {"==", "!="}:
            tok = self.advance()
            expr = self.mark(ast.BinaryExpr(op=tok.value, left=expr, right=self.parse_range()), tok)
        return expr

    def parse_range(self):
        expr = self.parse_comparison()
        while self.match("SYMBOL", ".."):
            tok = self.tokens[self.i - 1]
            expr = self.mark(ast.IntRangeExpr(start=expr, end=self.parse_comparison()), tok)
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
        if self.check("SYMBOL") and self.current().value in {"<<", ">>"}:
            op_value = self.current().value
            self.advance()
            op = self.tokens[self.i - 1]
            return self.mark(ast.BinaryExpr(op=op_value, left=expr, right=self.parse_composition()), op)
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
        if (
            self.check("IDENT")
            and self.current().value == "get"
            and not (self.i + 1 < len(self.tokens) and self.tokens[self.i + 1].kind == "SYMBOL" and self.tokens[self.i + 1].value == "(")
        ):
            start = self.advance()
            record = self.parse_primary()
            field = self.expect("IDENT", label="field name").value
            return self.mark(ast.GetFieldExpr(record=record, field_name=field), start)
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
                        args=[self.mark(ast.StringExpr(value=key), open_tok), value, out],
                    ),
                    open_tok,
                )
            return out
        if self.check("INT"):
            tok = self.advance()
            return self.mark(ast.IntExpr(value=int(tok.value)), tok)
        if self.check("CHAR"):
            tok = self.advance()
            return self.mark(ast.CharExpr(value=tok.value), tok)
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
            if self.check("SYMBOL", "{"):
                self.advance()
                fields: list[ast.RecordFieldValue] = []
                if not self.check("SYMBOL", "}"):
                    fields.append(self.parse_record_field_value())
                    while self.match("SYMBOL", ","):
                        if self.check("SYMBOL", "}"):
                            break
                        fields.append(self.parse_record_field_value())
                self.expect("SYMBOL", "}")
                return self.mark(ast.RecordExpr(type_name=tok.value, fields=fields), tok)
            return self.mark(ast.VarExpr(name=tok.value), tok)
        if self.match("SYMBOL", "("):
            open_tok = self.tokens[self.i - 1]
            if self.check("SYMBOL", ")"):
                self.advance()
                return self.mark(ast.UnitExpr(), open_tok)
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
        if self.check("CHAR"):
            tok = self.advance()
            return self.mark(ast.CharPattern(value=tok.value), tok)
        if self.check("STRING"):
            tok = self.advance()
            return self.mark(ast.StringPattern(value=tok.value), tok)
        if self.match("SYMBOL", "("):
            open_tok = self.tokens[self.i - 1]
            if self.check("SYMBOL", ")"):
                self.advance()
                return self.mark(ast.UnitPattern(), open_tok)
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
        if self.check("CHAR"):
            tok = self.advance()
            return self.mark(ast.CharPattern(value=tok.value), tok)
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
            if self.check("SYMBOL", ")"):
                self.advance()
                return self.mark(ast.UnitPattern(), open_tok)
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

    def parse_record_field_value(self) -> ast.RecordFieldValue:
        start = self.expect("IDENT", label="record field name")
        self.expect("SYMBOL", "=")
        return self.mark(ast.RecordFieldValue(name=start.value, value=self.parse_expr()), start)

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
        start = self.expect("SYMBOL", "{")
        effects: list[str] = []
        if not self.check("SYMBOL", "}"):
            effects.append(self.expect("IDENT", label="effect name").value)
            while self.match("SYMBOL", ","):
                effects.append(self.expect("IDENT", label="effect name").value)
        self.expect("SYMBOL", "}")
        if len(effects) > 1:
            raise ParseError(
                f"Only singleton effect rows are supported at {start.line}:{start.column}; mixed or multi-effect rows are not supported yet"
            )
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
        if self.check("INT") or self.check("STRING") or self.check("CHAR"):
            return True
        if self.check("IDENT"):
            return True
        if self.check("SYMBOL", "("):
            return True
        if self.check("KEYWORD") and self.current().value in {"true", "false"}:
            return True
        return False

    def _starts_local_where_binding(self) -> bool:
        if self.check("IDENT"):
            return (
                self.i + 1 < len(self.tokens)
                and self.tokens[self.i + 1].kind == "SYMBOL"
                and self.tokens[self.i + 1].value == "="
            )
        if not self.check("SYMBOL", "("):
            return False
        depth = 0
        idx = self.i
        while idx < len(self.tokens):
            token = self.tokens[idx]
            if token.kind == "SYMBOL" and token.value == "(":
                depth += 1
            elif token.kind == "SYMBOL" and token.value == ")":
                depth -= 1
                if depth == 0:
                    next_idx = idx + 1
                    return (
                        next_idx < len(self.tokens)
                        and self.tokens[next_idx].kind == "SYMBOL"
                        and self.tokens[next_idx].value == "="
                    )
            idx += 1
        return False

    def _is_constructor_name(self, name: str) -> bool:
        leaf = name.rsplit(".", 1)[-1]
        return bool(leaf) and leaf[0].isupper()


def parse(source: str) -> ast.Program:
    annotations_by_line = extract_decl_annotations(source)
    parser = Parser(tokenize(source))
    program = parser.parse_program()
    parser.expect("EOF")
    for decl in program.declarations:
        decl.annotations = annotations_by_line.get(getattr(decl, "line", -1), ())
    return program
