from __future__ import annotations

import tempfile
from pathlib import Path
import unittest

from sprout import TypeCheckError, parse, typecheck_program
from sprout.module_loader import load_module_bundle, resolve_program_names
from sprout.stdlib import with_http_prelude, with_prelude


class TypecheckerTests(unittest.TestCase):
    def test_typecheck_valid_program(self) -> None:
        src = """
        type Maybe a =
          | Just a
          | Nothing

        fn map(m: Maybe a, f: a -> b) -> Maybe b =
          match m with
          | Just x -> Just(f(x))
          | Nothing -> Nothing

        fn main() -> IO Unit =
          print(map(Just(2), fn_inc))

        fn fn_inc(x: Int) -> Int = x + 1
        """
        prog = parse(src)
        types = typecheck_program(prog)
        self.assertIn("map", types)
        self.assertIn("main", types)
        self.assertIn("read_lines", types)

    def test_typecheck_infers_function_param_and_return_types(self) -> None:
        src = """
        fn inc(x) = x + 1
        fn main() -> IO Unit =
          print(inc(41))
        """
        prog = parse(src)
        types = typecheck_program(prog)
        self.assertEqual(types["inc"], "Int -> Int")
        inc = prog.declarations[0]
        self.assertEqual(inc.params[0].type_expr.name, "Int")
        self.assertEqual(inc.return_type.name, "Int")

    def test_typecheck_infers_recursive_function_signature(self) -> None:
        src = """
        fn fact(n) =
          if n == 0 then 1 else n * fact(n - 1)
        """
        types = typecheck_program(parse(src))
        self.assertEqual(types["fact"], "Int -> Int")

    def test_type_error_top_level_let_cannot_be_io(self) -> None:
        src = """
        let boot = print("boot")
        fn main() -> IO Unit = print("ok")
        """
        with self.assertRaises(TypeCheckError) as ctx:
            typecheck_program(parse(src))
        self.assertIn("Top-level let bindings must not have type IO a", str(ctx.exception))

    def test_typecheck_with_stdlib_loaded(self) -> None:
        src = """
        fn is_even(x: Int) -> Bool =
          (x / 2) * 2 == x

        fn add(acc: Int, x: Int) -> Int = acc + x

        fn main() -> IO Unit =
          print(fold(filter(split_ints("1 2 3 4"), is_even), 0, add))
        """
        prog = parse(with_prelude(src))
        types = typecheck_program(prog)
        self.assertIn("map", types)
        self.assertIn("fold", types)
        self.assertIn("filter", types)
        self.assertIn("result_map", types)
        self.assertIn("result_and_then", types)
        self.assertIn("pipe", types)
        self.assertIn("result_pipe", types)
        self.assertIn("result_pipe_ok", types)
        self.assertIn("result_pipe_error", types)
        self.assertIn("when_ok", types)
        self.assertIn("when_error", types)

    def test_typecheck_stdlib_bytes_builder_helpers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / "main.sprout"
            main.write_text(
                """
                module main
                import stdlib.bytes (Builder, Result, Utf8Error, builder_append, builder_build, builder_byte, builder_bytes, builder_empty, builder_u16_be, builder_u32_be, from_string, length, to_string)

                fn sample() -> Builder =
                  builder_append(
                    builder_append(builder_empty(), builder_byte(65)),
                    builder_append(
                      builder_u16_be(16963),
                      builder_append(builder_u32_be(1145390663), builder_bytes(from_string("H")))
                    )
                  )

                fn score(text: String) -> Int =
                  match text with
                  | "ABCDEFGH" -> 1
                  | _ -> 0

                fn main() -> IO Unit =
                  match to_string(builder_build(sample())) with
                  | Ok text -> print(length(builder_build(builder_empty())) + score(text))
                  | Err _ -> print(0)
                """,
                encoding="utf-8",
            )
            bundle = load_module_bundle(main)
            prog = parse(bundle.source)
            resolve_program_names(prog, bundle)
            types = typecheck_program(prog)
            self.assertTrue(any(value == "Builder" for value in types.values()))

    def test_typecheck_stdlib_bytes_builder_rejects_non_int_byte(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / "main.sprout"
            main.write_text(
                """
                module main
                import stdlib.bytes (builder_byte)

                fn main() -> IO Unit =
                  print(builder_byte("x"))
                """,
                encoding="utf-8",
            )
            bundle = load_module_bundle(main)
            prog = parse(bundle.source)
            resolve_program_names(prog, bundle)
            with self.assertRaises(TypeCheckError) as ctx:
                typecheck_program(prog)
        self.assertIn("Argument type mismatch", str(ctx.exception))

    def test_typecheck_stdlib_crypto_helpers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / "main.sprout"
            main.write_text(
                """
                module main
                import stdlib.bytes (from_string)
                import stdlib.crypto as crypto

                fn main() -> IO Unit =
                  print(
                    crypto.base64_encode(crypto.sha256(from_string("abc")))
                  )
                """,
                encoding="utf-8",
            )
            bundle = load_module_bundle(main)
            prog = parse(bundle.source)
            resolve_program_names(prog, bundle)
            types = typecheck_program(prog)
            self.assertIn("main.main", types)

    def test_typecheck_stdlib_crypto_rejects_non_bytes_input(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / "main.sprout"
            main.write_text(
                """
                module main
                import stdlib.crypto as crypto

                fn main() -> IO Unit =
                  print(crypto.sha256(1))
                """,
                encoding="utf-8",
            )
            bundle = load_module_bundle(main)
            prog = parse(bundle.source)
            resolve_program_names(prog, bundle)
            with self.assertRaises(TypeCheckError) as ctx:
                typecheck_program(prog)
        self.assertIn("Argument type mismatch", str(ctx.exception))

    def test_type_error_if_branches_mismatch(self) -> None:
        src = """
        fn bad(x: Int) -> Int =
          if x > 0 then x else false
        """
        with self.assertRaises(TypeCheckError):
            typecheck_program(parse(src))

    def test_type_error_non_exhaustive_match(self) -> None:
        src = """
        type Maybe a =
          | Just a
          | Nothing

        fn bad(m: Maybe Int) -> Int =
          match m with
          | Just x -> x
        """
        with self.assertRaises(TypeCheckError):
            typecheck_program(parse(src))

    def test_type_error_unreachable_match_after_wildcard(self) -> None:
        src = """
        fn bad(x: Int) -> Int =
          match x with
          | _ -> 1
          | 2 -> 2
        """
        with self.assertRaises(TypeCheckError) as ctx:
            typecheck_program(parse(src))
        self.assertIn("Unreachable match branch", str(ctx.exception))

    def test_type_error_unreachable_match_after_variable_pattern(self) -> None:
        src = """
        fn bad(x: Int) -> Int =
          match x with
          | value -> value
          | 2 -> 2
        """
        with self.assertRaises(TypeCheckError) as ctx:
            typecheck_program(parse(src))
        self.assertIn("Unreachable match branch", str(ctx.exception))

    def test_type_error_unreachable_duplicate_literal_patterns(self) -> None:
        src = """
        fn bad(x: Int) -> Int =
          match x with
          | 1 -> 10
          | 1 -> 20
          | 2 -> 30
        """
        with self.assertRaises(TypeCheckError) as ctx:
            typecheck_program(parse(src))
        self.assertIn("Unreachable match branch for literal 1", str(ctx.exception))

    def test_type_error_unreachable_duplicate_constructor_pattern(self) -> None:
        src = """
        type Maybe a =
          | Just a
          | Nothing

        fn bad(m: Maybe Int) -> Int =
          match m with
          | Just x -> x
          | Just y -> y + 1
          | Nothing -> 0
        """
        with self.assertRaises(TypeCheckError) as ctx:
            typecheck_program(parse(src))
        self.assertIn("Unreachable match branch for constructor Just", str(ctx.exception))

    def test_typecheck_constructor_specific_then_general_branch_is_allowed_in_v0(self) -> None:
        src = """
        type NetErr =
          | TcpEndOfStream
          | TcpClosed

        type Result a =
          | Ok a
          | Err NetErr

        fn show(r: Result String) -> String =
          match r with
          | Ok body -> body
          | Err TcpEndOfStream -> "eof"
          | Err err -> "other"
        """
        types = typecheck_program(parse(src))
        self.assertEqual(types["show"], "Result String -> String")

    def test_type_error_unreachable_branch_after_exhaustive_adt_coverage(self) -> None:
        src = """
        type Maybe a =
          | Just a
          | Nothing

        fn bad(m: Maybe Int) -> Int =
          match m with
          | Just x -> x
          | Nothing -> 0
          | _ -> 1
        """
        with self.assertRaises(TypeCheckError) as ctx:
            typecheck_program(parse(src))
        self.assertIn("Unreachable match branch", str(ctx.exception))

    def test_typecheck_nested_pattern_overlap_is_not_rejected_in_v0(self) -> None:
        src = """
        fn ok(pair: (Bool, Bool)) -> Int =
          match pair with
          | (true, x) -> 1
          | (true, y) -> 2
          | (false, _) -> 3
        """
        types = typecheck_program(parse(src))
        self.assertEqual(types["ok"], "(Bool, Bool) -> Int")

    def test_typecheck_function_composition(self) -> None:
        src = """
        fn inc(x: Int) -> Int = x + 1
        fn double(x: Int) -> Int = x * 2
        fn apply(x: Int, f: Int -> Int) -> Int = f(x)
        fn main() -> IO Unit =
          print(apply(20, double >> inc))
        """
        types = typecheck_program(parse(src))
        self.assertIn("main", types)

    def test_typecheck_lambda_expression(self) -> None:
        src = r"""
        let inc = \x -> x + 1
        fn main() -> IO Unit =
          print(inc(41))
        """
        prog = parse(src)
        types = typecheck_program(prog)
        self.assertEqual(types["inc"], "Int -> Int")
        let_decl = prog.declarations[0]
        self.assertEqual(let_decl.value.params[0].type_expr.name, "Int")

    def test_typecheck_lambda_captures_lexical_bindings(self) -> None:
        src = r"""
        fn make_adder(base: Int) -> Int -> Int =
          \(x) -> base + x

        fn main() -> IO Unit =
          print(make_adder(41)(1))
        """
        types = typecheck_program(parse(src))
        self.assertEqual(types["make_adder"], "Int -> Int -> Int")

    def test_typecheck_lambda_annotation_mismatch(self) -> None:
        src = r"""
        let bad = \(x: Int) -> str_concat(x, "!")
        fn main() -> IO Unit = print("ok")
        """
        with self.assertRaises(TypeCheckError):
            typecheck_program(parse(src))

    def test_typecheck_higher_order_lambda_with_shared_annotation_vars(self) -> None:
        src = r"""
        let apply = \(f: a -> b, x: a) -> f(x)
        fn inc(x: Int) -> Int = x + 1
        fn main() -> IO Unit =
          print(apply(inc, 41))
        """
        types = typecheck_program(parse(src))
        self.assertEqual(types["apply"], "forall a b. (a -> b) -> a -> b")

    def test_typecheck_allows_class_method_as_first_class_value(self) -> None:
        src = """
        let mapper = fmap
        """
        types = typecheck_program(parse(with_prelude(src)))
        self.assertEqual(types["mapper"], "forall a b c. (a -> b) -> c a -> c b")

    def test_type_error_invalid_function_composition(self) -> None:
        src = """
        fn inc(x: Int) -> Int = x + 1
        fn is_even(x: Int) -> Bool = (x / 2) * 2 == x
        fn bad(x: Int) -> Int = (inc >> is_even)(x)
        """
        with self.assertRaises(TypeCheckError):
            typecheck_program(parse(src))

    def test_type_error_reports_expected_and_actual_call_argument_types(self) -> None:
        src = """
        fn bad(xs: List Int) -> List Int =
          fmap(xs, \\x -> x + 1)
        """
        with self.assertRaises(TypeCheckError) as ctx:
            typecheck_program(parse(with_prelude(src)))
        self.assertIn("Argument type mismatch: expected a -> b, got List Int", str(ctx.exception))

    def test_typecheck_tuples(self) -> None:
        src = """
        fn swap(pair: (Int, Bool)) -> (Bool, Int) =
          match pair with
          | (x, y) -> (y, x)
        """
        types = typecheck_program(parse(src))
        self.assertEqual(types["swap"], "(Int, Bool) -> (Bool, Int)")

    def test_type_error_tuple_pattern_arity_mismatch(self) -> None:
        src = """
        fn bad(pair: (Int, Bool)) -> Int =
          match pair with
          | (x, y, z) -> x
        """
        with self.assertRaises(TypeCheckError) as ctx:
            typecheck_program(parse(src))
        self.assertIn("Tuple pattern expects 2 items, got 3", str(ctx.exception))

    def test_type_error_unreachable_duplicate_string_literal_pattern(self) -> None:
        src = """
        fn bad(s: String) -> Int =
          match s with
          | "ok" -> 1
          | "ok" -> 2
          | "no" -> 3
        """
        with self.assertRaises(TypeCheckError) as ctx:
            typecheck_program(parse(src))
        self.assertIn("Unreachable match branch for literal 'ok'", str(ctx.exception))

    def test_typecheck_string_builtins(self) -> None:
        src = """
        fn main() -> IO Unit =
          print(
            if str_starts_with(str_concat("sprout", "-lang"), "sprout")
            then str_slice("abcdef", 1, str_len("abc"))
            else "nope"
          )
        """
        types = typecheck_program(parse(src))
        self.assertIn("main", types)

    def test_typecheck_with_http_stdlib_loaded(self) -> None:
        src = """
        fn main() -> IO Unit =
          print(http_echo_response("GET /ping HTTP/1.1\\r\\n\\r\\n"))
        """
        types = typecheck_program(parse(with_http_prelude(src)))
        self.assertIn("http_echo_response", types)
        self.assertIn("http_response_body", types)
        self.assertIn("http_request", types)
        self.assertIn("json_parse", types)
        self.assertIn("json_stringify", types)
        self.assertIn("json_get_field", types)
        self.assertIn("json_get_int", types)
        self.assertIn("json_get_array", types)
        self.assertIn("json_array_next", types)
        self.assertIn("json_object_next", types)

    def test_typecheck_terminal_builtins(self) -> None:
        src = """
        fn seq(a: IO Unit, b: IO Unit) -> IO Unit = b

        fn main() -> IO Unit =
          seq(term_hide_cursor(), seq(term_move(1, 1), term_show_cursor()))
        """
        types = typecheck_program(parse(src))
        self.assertIn("term_clear", types)
        self.assertIn("term_move", types)
        self.assertIn("term_read_key", types)

    def test_typecheck_vector_builtins(self) -> None:
        src = """
        type Maybe a =
          | Just a
          | Nothing

        fn third_or_zero(v: Vector Int) -> Int =
          match vector_get(v, 2) with
          | Just x -> x
          | Nothing -> 0

        fn main() -> IO Unit =
          print(third_or_zero(vector_append(vector_append(vector_append(vector_empty(), 1), 2), 3)))
        """
        types = typecheck_program(parse(src))
        self.assertIn("vector_empty", types)
        self.assertIn("vector_get", types)
        self.assertIn("third_or_zero", types)

    def test_typecheck_map_builtins(self) -> None:
        src = """
        type Maybe a =
          | Just a
          | Nothing

        fn find_or(m: Map Int, key: String, fallback: Int) -> Int =
          match map_get(m, key) with
          | Just x -> x
          | Nothing -> fallback

        fn main() -> IO Unit =
          print(find_or(map_set(map_empty(), "a", 7), "a", -1))
        """
        types = typecheck_program(parse(src))
        self.assertIn("map_empty", types)
        self.assertIn("map_get", types)
        self.assertIn("find_or", types)

    def test_typecheck_program_with_class_and_instance_decls(self) -> None:
        src = """
        class Functor f {
          fn fmap(f: a -> b, xs: f a) -> f b
        }
        instance Functor List {
          fn fmap(f: a -> b, xs: List a) -> List b = xs
        }

        fn main() -> IO Unit where Functor List =
          print(1)
        """
        types = typecheck_program(parse(src))
        self.assertIn("main", types)

    def test_typecheck_parametric_semigroup_instance(self) -> None:
        src = """
        type List a =
          | Cons a (List a)
          | Nil
        class Semigroup t {
          fn append(x: t, y: t) -> t
        }
        fn list_append(left: List a, right: List a) -> List a =
          match left with
          | Nil -> right
          | Cons x rest -> Cons(x, list_append(rest, right))
        instance Semigroup (List a) {
          fn append(x: List a, y: List a) -> List a =
            list_append(x, y)
        }
        fn combine(xs: List Int, ys: List Int) -> List Int where Semigroup (List Int) =
          append(xs, ys)
        fn main() -> IO Unit =
          print(combine(Cons(1, Nil), Cons(2, Nil)))
        """
        types = typecheck_program(parse(src))
        self.assertIn("combine", types)

    def test_typecheck_instance_method_can_call_concrete_class_method(self) -> None:
        src = """
        class Combiner t {
          fn append(x: t, y: t) -> t
          fn duplicate(x: t) -> t
        }
        instance Combiner String {
          fn append(x: String, y: String) -> String = str_concat(x, y)
          fn duplicate(x: String) -> String = append(x, x)
        }
        fn main() -> IO Unit where Combiner String =
          print(duplicate("ab"))
        """
        types = typecheck_program(parse(src))
        self.assertIn("main", types)

    def test_typecheck_semigroup_append_operator(self) -> None:
        src = """
        type List a =
          | Cons a (List a)
          | Nil
        class Semigroup t {
          fn append(x: t, y: t) -> t
        }
        instance Semigroup String {
          fn append(x: String, y: String) -> String = str_concat(x, y)
        }
        fn list_append(left: List a, right: List a) -> List a =
          match left with
          | Nil -> right
          | Cons x rest -> Cons(x, list_append(rest, right))
        instance Semigroup (List a) {
          fn append(x: List a, y: List a) -> List a = list_append(x, y)
        }
        let s = "a" ++ "b"
        let xs = [1, 2] ++ [3, 4]
        fn main() -> IO Unit = print(xs)
        """
        types = typecheck_program(parse(src))
        self.assertIn("s", types)
        self.assertIn("xs", types)

    def test_type_error_instance_missing_method(self) -> None:
        src = """
        class Foldable f {
          fn fold_count(xs: f a) -> Int
        }
        instance Foldable List {
        }
        fn main() -> IO Unit = print(1)
        """
        with self.assertRaises(TypeCheckError):
            typecheck_program(parse(src))

    def test_type_error_instance_method_signature_mismatch(self) -> None:
        src = """
        class Foldable f {
          fn fold_count(xs: f a) -> Int
        }
        instance Foldable List {
          fn fold_count(xs: List Int) -> Bool = true
        }
        fn main() -> IO Unit = print(1)
        """
        with self.assertRaises(TypeCheckError):
            typecheck_program(parse(src))

    def test_type_error_unknown_class_in_constraint(self) -> None:
        src = """
        fn main() -> IO Unit where Missing List =
          print(1)
        """
        with self.assertRaises(TypeCheckError):
            typecheck_program(parse(src))

    def test_type_error_class_arity_mismatch(self) -> None:
        src = """
        class Foldable f
        instance Foldable List Int
        fn main() -> IO Unit = print(1)
        """
        with self.assertRaises(TypeCheckError):
            typecheck_program(parse(src))

    def test_type_error_duplicate_instance_head(self) -> None:
        src = """
        class Foldable f
        instance Foldable List
        instance Foldable List
        fn main() -> IO Unit = print(1)
        """
        with self.assertRaises(TypeCheckError):
            typecheck_program(parse(src))


if __name__ == "__main__":
    unittest.main()
