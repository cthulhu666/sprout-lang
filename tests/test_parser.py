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

    def test_parse_composition_precedence_and_associativity(self) -> None:
        src = "fn main() -> Int = (f >> g >> h)(x) * y"
        program = parse(src)
        fn_decl = program.declarations[0]
        self.assertIsInstance(fn_decl, ast.FnDecl)
        self.assertIsInstance(fn_decl.body, ast.BinaryExpr)
        self.assertEqual(fn_decl.body.op, "*")

        callee = fn_decl.body.left
        self.assertIsInstance(callee, ast.CallExpr)
        self.assertIsInstance(callee.callee, ast.BinaryExpr)
        self.assertEqual(callee.callee.op, ">>")
        self.assertIsInstance(callee.callee.right, ast.BinaryExpr)
        self.assertEqual(callee.callee.right.op, ">>")

    def test_parse_string_escape_carriage_return(self) -> None:
        src = 'fn main() -> String = "a\\r\\nb"'
        program = parse(src)
        fn_decl = program.declarations[0]
        self.assertIsInstance(fn_decl, ast.FnDecl)
        self.assertIsInstance(fn_decl.body, ast.StringExpr)
        self.assertEqual(fn_decl.body.value, "a\r\nb")

    def test_parse_export_prefix_on_declarations(self) -> None:
        src = """
        export type Box a =
          | Box a
        export fn id(x: Int) -> Int = x
        export let answer = id(42)
        """
        program = parse(src)
        self.assertEqual(len(program.declarations), 3)
        self.assertIsInstance(program.declarations[0], ast.TypeDecl)
        self.assertIsInstance(program.declarations[1], ast.FnDecl)
        self.assertIsInstance(program.declarations[2], ast.LetDecl)

    def test_parse_class_instance_and_where_constraints(self) -> None:
        src = """
        class Functor f
        instance Functor List
        fn map_id(xs: List Int) -> List Int where Functor List = xs
        """
        program = parse(src)
        self.assertEqual(len(program.declarations), 3)
        self.assertIsInstance(program.declarations[0], ast.ClassDecl)
        self.assertIsInstance(program.declarations[1], ast.InstanceDecl)
        self.assertIsInstance(program.declarations[2], ast.FnDecl)

        class_decl = program.declarations[0]
        self.assertEqual(class_decl.name, "Functor")
        self.assertEqual(class_decl.type_params, ["f"])

        inst_decl = program.declarations[1]
        self.assertEqual(inst_decl.constraint.class_name, "Functor")
        self.assertEqual(len(inst_decl.constraint.args), 1)
        self.assertIsInstance(inst_decl.constraint.args[0], ast.TypeName)
        self.assertEqual(inst_decl.constraint.args[0].name, "List")

        fn_decl = program.declarations[2]
        self.assertEqual(len(fn_decl.constraints), 1)
        self.assertEqual(fn_decl.constraints[0].class_name, "Functor")


if __name__ == "__main__":
    unittest.main()
