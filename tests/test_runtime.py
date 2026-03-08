from __future__ import annotations

import io
import unittest

from sprout import parse, run_program, typecheck_program


class RuntimeTests(unittest.TestCase):
    def test_run_main_prints_result(self) -> None:
        src = """
        fn fact(n: Int) -> Int =
          if n == 0 then 1 else n * fact(n - 1)

        fn main() -> IO Unit =
          print(fact(5))
        """
        program = parse(src)
        typecheck_program(program)

        out = io.StringIO()
        run_program(program, stdout=out)
        self.assertEqual(out.getvalue().strip(), "120")

    def test_run_match_with_adt(self) -> None:
        src = """
        type Maybe a =
          | Just a
          | Nothing

        fn with_default(m: Maybe Int, d: Int) -> Int =
          match m with
          | Just x -> x
          | Nothing -> d

        fn main() -> IO Unit =
          print(with_default(Just(7), 0))
        """
        program = parse(src)
        typecheck_program(program)

        out = io.StringIO()
        run_program(program, stdout=out)
        self.assertEqual(out.getvalue().strip(), "7")

    def test_top_level_let_evaluates_in_order(self) -> None:
        src = """
        let a = 1
        let b = a + 2

        fn main() -> IO Unit =
          print(b)
        """
        program = parse(src)
        typecheck_program(program)

        out = io.StringIO()
        run_program(program, stdout=out)
        self.assertEqual(out.getvalue().strip(), "3")


if __name__ == "__main__":
    unittest.main()
