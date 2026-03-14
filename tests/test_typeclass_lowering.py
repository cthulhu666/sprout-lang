from __future__ import annotations

import io
import unittest

from sprout import parse, run_program, typecheck_program
from sprout.typeclass_lowering import TypeclassLoweringError, lower_typeclasses


class TypeclassLoweringTests(unittest.TestCase):
    def test_lowering_runs_concrete_constraint_program(self) -> None:
        src = """
        class Renderable t {
          fn render(x: t) -> Int
        }
        type Box =
          | Box Int
        instance Renderable Box {
          fn render(x: Box) -> Int =
            match x with
            | Box n -> n
        }
        fn show_box(x: Box) -> Int where Renderable Box =
          render(x)
        fn main() -> IO Unit =
          print(show_box(Box(42)))
        """
        program = parse(src)
        typecheck_program(program)
        lowered = lower_typeclasses(program)
        typecheck_program(lowered)
        out = io.StringIO()
        run_program(lowered, stdout=out)
        self.assertEqual(out.getvalue().strip(), "42")

    def test_lowering_supports_polymorphic_constraint_with_concrete_wrapper(self) -> None:
        src = """
        class Renderable t {
          fn render(x: t) -> Int
        }
        instance Renderable Int {
          fn render(x: Int) -> Int = x
        }
        fn show_any(x: a) -> Int where Renderable a =
          render(x)
        fn show_int(x: Int) -> Int where Renderable Int =
          show_any(x)
        fn main() -> IO Unit =
          print(show_int(42))
        """
        program = parse(src)
        typecheck_program(program)
        lowered = lower_typeclasses(program)
        typecheck_program(lowered)
        out = io.StringIO()
        run_program(lowered, stdout=out)
        self.assertEqual(out.getvalue().strip(), "42")

    def test_lowering_errors_when_call_site_cannot_resolve_constraint(self) -> None:
        src = """
        class Renderable t {
          fn render(x: t) -> Int
        }
        fn show_any(x: a) -> Int where Renderable a =
          render(x)
        fn main() -> IO Unit =
          print(show_any(42))
        """
        program = parse(src)
        typecheck_program(program)
        with self.assertRaises(TypeclassLoweringError):
            lower_typeclasses(program)

    def test_lowering_supports_parametric_instance_head(self) -> None:
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
        program = parse(src)
        typecheck_program(program)
        lowered = lower_typeclasses(program)
        typecheck_program(lowered)
        out = io.StringIO()
        run_program(lowered, stdout=out)
        self.assertEqual(out.getvalue().strip(), "Cons(1, Cons(2, Nil))")


if __name__ == "__main__":
    unittest.main()
