from __future__ import annotations

import io
import unittest

from sprout import parse, run_program, typecheck_program
from sprout.stdlib import with_prelude
from sprout.typeclass_lowering import TypeclassLoweringError, lower_typeclasses


class TypeclassLoweringTests(unittest.TestCase):
    def test_lowering_preserves_do_until_later_elaboration(self) -> None:
        src = """
        fn pair_sum(left: Maybe Int, right: Maybe Int) -> Maybe Int =
          do
            a <- left
            b <- right
            Just(a + b)

        fn main() -> Unit !{IO} =
          print(pair_sum(Just(20), Just(22)))
        """
        program = parse(with_prelude(src))
        typecheck_program(program)
        lowered = lower_typeclasses(program)
        typecheck_program(lowered)
        out = io.StringIO()
        run_program(lowered, stdout=out)
        self.assertEqual(out.getvalue().strip(), "Just(42)")

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
        fn main() -> Unit !{IO} =
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
        fn main() -> Unit !{IO} =
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
        fn main() -> Unit !{IO} =
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
        fn main() -> Unit !{IO} =
          print(combine(Cons(1, Nil), Cons(2, Nil)))
        """
        program = parse(src)
        typecheck_program(program)
        lowered = lower_typeclasses(program)
        typecheck_program(lowered)
        out = io.StringIO()
        run_program(lowered, stdout=out)
        self.assertEqual(out.getvalue().strip(), "Cons(1, Cons(2, Nil))")

    def test_lowering_resolves_concrete_constraint_from_call_argument_types(self) -> None:
        src = """
        class Foldable f {
          fn fold_values(f: b -> a -> b, init: b, xs: f a) -> b
        }
        type List a =
          | Cons a (List a)
          | Nil
        type Vec a =
          | Vec (List a)
        fn vec_empty() -> Vec a =
          Vec(Nil)
        fn vec_append(x: a, xs: Vec a) -> Vec a =
          match xs with
          | Vec items -> Vec(Cons(x, items))
        fn vec_append_flip(acc: Vec a, x: a) -> Vec a =
          vec_append(x, acc)
        instance Foldable List {
          fn fold_values(f: b -> a -> b, init: b, xs: List a) -> b =
            match xs with
            | Nil -> init
            | Cons x rest -> fold_values(f, f(init, x), rest)
        }
        fn foldable_to_vec(xs: f a) -> Vec a where Foldable f =
          fold_values(vec_append_flip, vec_empty(), xs)
        let xs = foldable_to_vec(Cons(1, Cons(2, Nil)))
        """
        program = parse(src)
        typecheck_program(program)
        lowered = lower_typeclasses(program)
        typecheck_program(lowered)

    def test_lowering_supports_vec_sort_by_with_ord_instance(self) -> None:
        src = """
        type Box =
          | Box Int

        fn key(x: Box) -> Int =
          match x with
          | Box n -> 0 - n

        instance Ord Box {
          fn compare(x: Box, y: Box) -> Int =
            compare(key(x), key(y))
        }

        fn main() -> Unit !{IO} =
          print(vec_sort(vec_append(Box(3), vec_append(Box(1), vec_append(Box(2), vec_empty())))))
        """
        program = parse(with_prelude(src))
        typecheck_program(program)
        lowered = lower_typeclasses(program)
        typecheck_program(lowered)
        out = io.StringIO()
        run_program(lowered, stdout=out)
        self.assertEqual(out.getvalue().strip(), "Vec([Box(3), Box(2), Box(1)])")

    def test_lowering_errors_for_vec_sort_by_without_ord_instance(self) -> None:
        src = """
        type Box =
          | Box Int

        fn main() -> Unit !{IO} =
          print(vec_sort_by(\\x -> Box(x), vec_append(1, vec_empty())))
        """
        program = parse(with_prelude(src))
        typecheck_program(program)
        with self.assertRaises(TypeclassLoweringError):
            lower_typeclasses(program)

    def test_lowering_supports_show_to_string(self) -> None:
        src = """
        fn render(x: a) -> String where ToString a =
          to_string(x)

        fn main() -> Unit !{IO} =
          print(render(42))
        """
        program = parse(with_prelude(src))
        typecheck_program(program)
        lowered = lower_typeclasses(program)
        typecheck_program(lowered)
        out = io.StringIO()
        run_program(lowered, stdout=out)
        self.assertEqual(out.getvalue().strip(), "42")


if __name__ == "__main__":
    unittest.main()
