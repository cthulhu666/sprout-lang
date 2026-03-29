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

    def test_parse_fn_allows_omitted_param_and_return_annotations(self) -> None:
        src = """
        fn inc(x) = x + 1
        """
        program = parse(src)
        fn_decl = program.declarations[0]
        self.assertIsInstance(fn_decl, ast.FnDecl)
        self.assertEqual(fn_decl.params[0].name, "x")
        self.assertIsNone(fn_decl.params[0].type_expr)
        self.assertIsNone(fn_decl.return_type)

    def test_parse_decl_annotations_attach_to_following_top_level_decl(self) -> None:
        src = """
        #@unstable
        #@deprecated use better_inc instead
        export fn inc(x: Int) -> Int = x + 1
        """
        program = parse(src)
        fn_decl = program.declarations[0]
        self.assertEqual(
            fn_decl.annotations,
            (
                ast.DeclAnnotation(kind="unstable", message=None),
                ast.DeclAnnotation(kind="deprecated", message="use better_inc instead"),
            ),
        )

    def test_parse_decl_annotations_reject_unknown_marker(self) -> None:
        src = """
        #@mystery
        fn inc(x: Int) -> Int = x + 1
        """
        with self.assertRaises(ParseError) as ctx:
            parse(src)
        self.assertIn("Unknown declaration annotation", str(ctx.exception))

    def test_parse_fn_local_where_desugars_to_nested_lambda_calls(self) -> None:
        src = """
        fn score(n: Int) -> Int =
          x + y
        where
          x = n + 1
          y = x * 2
        """
        program = parse(src)
        fn_decl = program.declarations[0]
        self.assertIsInstance(fn_decl.body, ast.CallExpr)
        self.assertIsInstance(fn_decl.body.callee, ast.LambdaExpr)
        self.assertEqual(fn_decl.body.callee.params[0].name, "x")
        self.assertIsInstance(fn_decl.body.callee.body, ast.CallExpr)
        inner = fn_decl.body.callee.body
        self.assertIsInstance(inner.callee, ast.LambdaExpr)
        self.assertEqual(inner.callee.params[0].name, "y")

    def test_parse_fn_allows_constraints_and_local_where(self) -> None:
        src = """
        fn map_id(xs: List Int) -> List Int where Functor List =
          value
        where
          value = fmap(id, xs)
        """
        program = parse(src)
        fn_decl = program.declarations[0]
        self.assertEqual(len(fn_decl.constraints), 1)
        self.assertEqual(fn_decl.constraints[0].class_name, "Functor")
        self.assertIsInstance(fn_decl.body, ast.CallExpr)

    def test_parse_fn_local_where_tuple_destructures_via_match(self) -> None:
        src = """
        fn score(pair: (Int, Int)) -> Int =
          x + y
        where
          (x, y) = pair
        """
        program = parse(src)
        fn_decl = program.declarations[0]
        self.assertIsInstance(fn_decl.body, ast.CallExpr)
        self.assertIsInstance(fn_decl.body.callee, ast.LambdaExpr)
        self.assertIsInstance(fn_decl.body.callee.body, ast.MatchExpr)
        branch = fn_decl.body.callee.body.branches[0]
        self.assertIsInstance(branch.pattern, ast.TuplePattern)

    def test_parse_fn_rejects_duplicate_local_where_bindings(self) -> None:
        src = """
        fn bad(n: Int) -> Int =
          x
        where
          x = n
          x = n + 1
        """
        with self.assertRaises(ParseError):
            parse(src)

    def test_parse_fn_rejects_constructor_pattern_in_local_where(self) -> None:
        src = """
        fn bad(pair: Maybe Int) -> Int =
          x
        where
          Just x = pair
        """
        with self.assertRaises(ParseError):
            parse(src)

    def test_parse_lambda_expression_with_annotations(self) -> None:
        src = r"""
        fn main() -> Int = \(x: Int, y: Int) -> x + y
        """
        program = parse(src)
        fn_decl = program.declarations[0]
        self.assertIsInstance(fn_decl.body, ast.LambdaExpr)
        self.assertEqual([param.name for param in fn_decl.body.params], ["x", "y"])
        self.assertEqual(fn_decl.body.params[0].type_expr.name, "Int")
        self.assertEqual(fn_decl.body.params[1].type_expr.name, "Int")
        self.assertIsInstance(fn_decl.body.body, ast.BinaryExpr)
        self.assertEqual(fn_decl.body.body.op, "+")

    def test_parse_single_arg_lambda_shorthand(self) -> None:
        src = r"fn main() -> Int = \x -> x + 1"
        program = parse(src)
        fn_decl = program.declarations[0]
        self.assertIsInstance(fn_decl.body, ast.LambdaExpr)
        self.assertEqual([param.name for param in fn_decl.body.params], ["x"])
        self.assertIsNone(fn_decl.body.params[0].type_expr)

    def test_parse_single_arg_lambda_shorthand_with_annotation(self) -> None:
        src = r"fn main() -> Int = \x: Int -> x + 1"
        program = parse(src)
        fn_decl = program.declarations[0]
        self.assertIsInstance(fn_decl.body, ast.LambdaExpr)
        self.assertEqual(fn_decl.body.params[0].type_expr.name, "Int")

    def test_parse_lambda_rejects_empty_parameter_list(self) -> None:
        src = r"fn main() -> Int = \() -> 42"
        with self.assertRaises(ParseError):
            parse(src)

    def test_parse_lambda_in_call_position(self) -> None:
        src = r"fn main() -> Int = apply(41, \(x) -> x + 1)"
        program = parse(src)
        fn_decl = program.declarations[0]
        self.assertIsInstance(fn_decl.body, ast.CallExpr)
        self.assertEqual(len(fn_decl.body.args), 2)
        self.assertIsInstance(fn_decl.body.args[1], ast.LambdaExpr)

    def test_parse_nested_lambda_expression(self) -> None:
        src = r"fn main() -> Int = \(x) -> \(y) -> x + y"
        program = parse(src)
        fn_decl = program.declarations[0]
        self.assertIsInstance(fn_decl.body, ast.LambdaExpr)
        self.assertIsInstance(fn_decl.body.body, ast.LambdaExpr)

    def test_parse_nested_single_arg_lambda_expression(self) -> None:
        src = r"fn main() -> Int = \x -> \y -> x + y"
        program = parse(src)
        fn_decl = program.declarations[0]
        self.assertIsInstance(fn_decl.body, ast.LambdaExpr)
        self.assertIsInstance(fn_decl.body.body, ast.LambdaExpr)

    def test_parse_lambda_body_respects_expression_precedence(self) -> None:
        src = r"fn main() -> Int = \(x) -> x + 1 * 2"
        program = parse(src)
        fn_decl = program.declarations[0]
        self.assertIsInstance(fn_decl.body, ast.LambdaExpr)
        self.assertIsInstance(fn_decl.body.body, ast.BinaryExpr)
        self.assertEqual(fn_decl.body.body.op, "+")
        self.assertIsInstance(fn_decl.body.body.right, ast.BinaryExpr)
        self.assertEqual(fn_decl.body.body.right.op, "*")

    def test_parse_int_range_precedence(self) -> None:
        src = "fn main() -> IntRange = 1..n + 1"
        program = parse(src)
        fn_decl = program.declarations[0]
        self.assertIsInstance(fn_decl.body, ast.IntRangeExpr)
        self.assertIsInstance(fn_decl.body.start, ast.IntExpr)
        self.assertIsInstance(fn_decl.body.end, ast.BinaryExpr)
        self.assertEqual(fn_decl.body.end.op, "+")

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

    def test_parse_reverse_composition_precedence_and_associativity(self) -> None:
        src = "fn main() -> Int = (f << g << h)(x) * y"
        program = parse(src)
        fn_decl = program.declarations[0]
        self.assertIsInstance(fn_decl, ast.FnDecl)
        self.assertIsInstance(fn_decl.body, ast.BinaryExpr)
        self.assertEqual(fn_decl.body.op, "*")

        callee = fn_decl.body.left
        self.assertIsInstance(callee, ast.CallExpr)
        self.assertIsInstance(callee.callee, ast.BinaryExpr)
        self.assertEqual(callee.callee.op, "<<")
        self.assertIsInstance(callee.callee.right, ast.BinaryExpr)
        self.assertEqual(callee.callee.right.op, "<<")

    def test_parse_pipe_desugars_to_unary_call(self) -> None:
        src = "fn main() -> Int = 20 |> inc"
        program = parse(src)
        fn_decl = program.declarations[0]
        self.assertIsInstance(fn_decl, ast.FnDecl)
        self.assertIsInstance(fn_decl.body, ast.CallExpr)
        self.assertIsInstance(fn_decl.body.callee, ast.VarExpr)
        self.assertEqual(fn_decl.body.callee.name, "inc")
        self.assertEqual(len(fn_decl.body.args), 1)
        self.assertIsInstance(fn_decl.body.args[0], ast.IntExpr)
        self.assertEqual(fn_decl.body.args[0].value, 20)

    def test_parse_pipe_appends_into_existing_call(self) -> None:
        src = "fn main() -> Int = Ok(20) |> result_pipe_ok(inc)"
        program = parse(src)
        fn_decl = program.declarations[0]
        self.assertIsInstance(fn_decl, ast.FnDecl)
        self.assertIsInstance(fn_decl.body, ast.CallExpr)
        self.assertEqual(len(fn_decl.body.args), 1)
        self.assertIsInstance(fn_decl.body.args[0], ast.CallExpr)
        self.assertIsInstance(fn_decl.body.callee, ast.LambdaExpr)
        self.assertEqual(len(fn_decl.body.callee.params), 1)
        inner = fn_decl.body.callee.body
        self.assertIsInstance(inner, ast.CallExpr)
        self.assertIsInstance(inner.callee, ast.VarExpr)
        self.assertEqual(inner.callee.name, "result_pipe_ok")
        self.assertEqual(len(inner.args), 2)
        self.assertIsInstance(inner.args[0], ast.VarExpr)
        self.assertEqual(inner.args[0].name, "inc")
        self.assertIsInstance(inner.args[1], ast.VarExpr)

    def test_parse_semigroup_append_operator_desugars_to_append_call(self) -> None:
        src = "fn main() -> List Int = [1, 2] ++ [3, 4]"
        program = parse(src)
        fn_decl = program.declarations[0]
        self.assertIsInstance(fn_decl, ast.FnDecl)
        self.assertIsInstance(fn_decl.body, ast.CallExpr)
        self.assertIsInstance(fn_decl.body.callee, ast.VarExpr)
        self.assertEqual(fn_decl.body.callee.name, "append")
        self.assertEqual(len(fn_decl.body.args), 2)

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

    def test_parse_record_decl_literal_and_get(self) -> None:
        src = """
        type User = { name: String, age: Int }

        fn name_of(user: User) -> String =
          get user name

        let ada = User { name = "Ada", age = 36 }
        """
        program = parse(src)
        self.assertIsInstance(program.declarations[0], ast.RecordDecl)
        record_decl = program.declarations[0]
        self.assertEqual([field.name for field in record_decl.fields], ["name", "age"])
        fn_decl = program.declarations[1]
        self.assertIsInstance(fn_decl.body, ast.GetFieldExpr)
        self.assertEqual(fn_decl.body.field_name, "name")
        let_decl = program.declarations[2]
        self.assertIsInstance(let_decl.value, ast.RecordExpr)
        self.assertEqual(let_decl.value.type_name, "User")
        self.assertEqual([field.name for field in let_decl.value.fields], ["name", "age"])

    def test_parse_list_literal_desugars_to_cons_chain(self) -> None:
        src = "fn xs() -> List Int = [1, 2, 3]"
        program = parse(src)
        fn_decl = program.declarations[0]
        self.assertIsInstance(fn_decl, ast.FnDecl)
        self.assertIsInstance(fn_decl.body, ast.CallExpr)
        self.assertIsInstance(fn_decl.body.callee, ast.VarExpr)
        self.assertEqual(fn_decl.body.callee.name, "Cons")

    def test_parse_dict_literal_desugars_to_dict_set_chain(self) -> None:
        src = 'fn xs() -> Dict Int = {foo: 1, "bar": 2}'
        program = parse(src)
        fn_decl = program.declarations[0]
        self.assertIsInstance(fn_decl, ast.FnDecl)
        self.assertIsInstance(fn_decl.body, ast.CallExpr)
        self.assertIsInstance(fn_decl.body.callee, ast.VarExpr)
        self.assertEqual(fn_decl.body.callee.name, "dict_set")

        third_arg = fn_decl.body.args[2]
        self.assertIsInstance(third_arg, ast.CallExpr)
        self.assertIsInstance(third_arg.callee, ast.VarExpr)
        self.assertEqual(third_arg.callee.name, "dict_set")

        empty_call = third_arg.args[2]
        self.assertIsInstance(empty_call, ast.CallExpr)
        self.assertIsInstance(empty_call.callee, ast.VarExpr)
        self.assertEqual(empty_call.callee.name, "dict_empty")

    def test_parse_tuple_expression_pattern_and_type(self) -> None:
        src = """
        fn swap(pair: (Int, Bool)) -> (Bool, Int) =
          match pair with
          | (x, y) -> (y, x)
        """
        program = parse(src)
        fn_decl = program.declarations[0]
        self.assertIsInstance(fn_decl.params[0].type_expr, ast.TupleType)
        self.assertIsInstance(fn_decl.return_type, ast.TupleType)
        self.assertIsInstance(fn_decl.body, ast.MatchExpr)
        self.assertIsInstance(fn_decl.body.branches[0].pattern, ast.TuplePattern)
        self.assertIsInstance(fn_decl.body.branches[0].value, ast.TupleExpr)

    def test_parse_parenthesized_expression_is_not_singleton_tuple(self) -> None:
        src = "fn main() -> Int = (1)"
        program = parse(src)
        fn_decl = program.declarations[0]
        self.assertIsInstance(fn_decl.body, ast.IntExpr)

    def test_parse_empty_dict_literal_desugars_to_dict_empty(self) -> None:
        src = "fn xs() -> Dict Int = {}"
        program = parse(src)
        fn_decl = program.declarations[0]
        self.assertIsInstance(fn_decl, ast.FnDecl)
        self.assertIsInstance(fn_decl.body, ast.CallExpr)
        self.assertIsInstance(fn_decl.body.callee, ast.VarExpr)
        self.assertEqual(fn_decl.body.callee.name, "dict_empty")

    def test_parse_effect_polymorphic_arrow(self) -> None:
        src = """
        fn apply_twice(f: Int -> Int !{e}, x: Int) -> Int !{e} =
          f(f(x))
        """
        program = parse(src)
        fn_decl = program.declarations[0]
        self.assertIsInstance(fn_decl, ast.FnDecl)
        self.assertEqual(fn_decl.effects, ("e",))
        self.assertIsInstance(fn_decl.params[0].type_expr, ast.TypeArrow)
        self.assertEqual(fn_decl.params[0].type_expr.effects, ("e",))

    def test_parse_rejects_mixed_effect_rows(self) -> None:
        src = """
        fn bad() -> Unit !{IO, e} =
          print("x")
        """
        with self.assertRaises(ParseError) as ctx:
            parse(src)
        self.assertIn("Only singleton effect rows are supported", str(ctx.exception))

    def test_parse_rejects_multiple_effect_variables(self) -> None:
        src = """
        fn bad(f: Int -> Int !{e, f}) -> Int =
          f(1)
        """
        with self.assertRaises(ParseError) as ctx:
            parse(src)
        self.assertIn("Only singleton effect rows are supported", str(ctx.exception))

    def test_parse_class_instance_and_where_constraints(self) -> None:
        src = """
        class Functor f {
          fn fmap(f: a -> b, xs: f a) -> f b
        }
        instance Functor List {
          fn fmap(f: a -> b, xs: List a) -> List b = xs
        }
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
        self.assertEqual(len(class_decl.methods), 1)
        self.assertEqual(class_decl.methods[0].name, "fmap")

        inst_decl = program.declarations[1]
        self.assertEqual(inst_decl.constraint.class_name, "Functor")
        self.assertEqual(len(inst_decl.constraint.args), 1)
        self.assertIsInstance(inst_decl.constraint.args[0], ast.TypeName)
        self.assertEqual(inst_decl.constraint.args[0].name, "List")
        self.assertEqual(len(inst_decl.methods), 1)
        self.assertEqual(inst_decl.methods[0].name, "fmap")

        fn_decl = program.declarations[2]
        self.assertEqual(len(fn_decl.constraints), 1)
        self.assertEqual(fn_decl.constraints[0].class_name, "Functor")

    def test_parse_class_method_still_requires_param_type_annotations(self) -> None:
        src = """
        class Show t {
          fn show(x) -> String
        }
        """
        with self.assertRaises(ParseError):
            parse(src)


if __name__ == "__main__":
    unittest.main()
