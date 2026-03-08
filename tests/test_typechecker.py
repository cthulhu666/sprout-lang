from __future__ import annotations

import unittest

from sprout import TypeCheckError, parse, typecheck_program


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


if __name__ == "__main__":
    unittest.main()
