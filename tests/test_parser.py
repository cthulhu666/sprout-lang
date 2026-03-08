from __future__ import annotations

import unittest

from sprout import ParseError, parse
from sprout import ast


class ParserTests(unittest.TestCase):
    def test_parse_type_fn_and_let(self) -> None:
        src = """
        type Maybe a =
          | Just a
          | Nothing

        fn inc(x: Int) -> Int = x + 1
        let answer = inc(41)
        """
        program = parse(src)
        self.assertEqual(len(program.declarations), 3)
        self.assertIsInstance(program.declarations[0], ast.TypeDecl)
        self.assertIsInstance(program.declarations[1], ast.FnDecl)
        self.assertIsInstance(program.declarations[2], ast.LetDecl)

    def test_parse_match_expression(self) -> None:
        src = """
        fn with_default(m: Maybe Int, d: Int) -> Int =
          match m with
          | Just x -> x
          | Nothing -> d
        """
        program = parse(src)
        fn_decl = program.declarations[0]
        self.assertIsInstance(fn_decl, ast.FnDecl)
        self.assertIsInstance(fn_decl.body, ast.MatchExpr)
        self.assertEqual(len(fn_decl.body.branches), 2)

    def test_parse_recursive_fn(self) -> None:
        src = """
        fn fact(n: Int) -> Int =
          if n == 0 then 1 else n * fact(n - 1)
        """
        program = parse(src)
        fn_decl = program.declarations[0]
        self.assertIsInstance(fn_decl.body, ast.IfExpr)

    def test_parse_error_missing_else(self) -> None:
        src = "fn bad(x: Int) -> Int = if x > 0 then 1"
        with self.assertRaises(ParseError):
            parse(src)


if __name__ == "__main__":
    unittest.main()
