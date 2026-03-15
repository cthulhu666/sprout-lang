from __future__ import annotations

import unittest

from sprout import TypeCheckError, parse, typecheck_program
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

    def test_type_error_invalid_function_composition(self) -> None:
        src = """
        fn inc(x: Int) -> Int = x + 1
        fn is_even(x: Int) -> Bool = (x / 2) * 2 == x
        fn bad(x: Int) -> Int = (inc >> is_even)(x)
        """
        with self.assertRaises(TypeCheckError):
            typecheck_program(parse(src))

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
