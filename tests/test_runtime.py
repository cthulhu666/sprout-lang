from __future__ import annotations

import io
import tempfile
from pathlib import Path
import unittest

from sprout import parse, run_program, typecheck_program
from sprout.stdlib import with_prelude


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

    def test_stdlib_split_ints_and_fold(self) -> None:
        src = """
        fn add(acc: Int, x: Int) -> Int = acc + x

        fn main() -> IO Unit =
          print(fold(split_ints("1, 2 3 4"), 0, add))
        """
        program = parse(with_prelude(src))
        typecheck_program(program)
        out = io.StringIO()
        run_program(program, stdout=out)
        self.assertEqual(out.getvalue().strip(), "10")

    def test_stdlib_read_lines_and_parse_int(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "numbers.txt"
            input_path.write_text("7\n8\n9\n", encoding="utf-8")
            src = f"""
            type List a =
              | Cons a (List a)
              | Nil

            fn sum_lines(lines: List String) -> Int =
              match lines with
              | Nil -> 0
              | Cons s rest -> parse_int(s) + sum_lines(rest)

            fn main() -> IO Unit =
              print(sum_lines(read_lines("{input_path}")))
            """
            program = parse(src)
            typecheck_program(program)
            out = io.StringIO()
            run_program(program, stdout=out)
            self.assertEqual(out.getvalue().strip(), "24")

    def test_run_function_composition(self) -> None:
        src = """
        fn inc(x: Int) -> Int = x + 1
        fn double(x: Int) -> Int = x * 2

        fn main() -> IO Unit =
          print((double >> inc)(20))
        """
        program = parse(src)
        typecheck_program(program)
        out = io.StringIO()
        run_program(program, stdout=out)
        self.assertEqual(out.getvalue().strip(), "42")


if __name__ == "__main__":
    unittest.main()
