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


if __name__ == "__main__":
    unittest.main()
