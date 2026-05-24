from __future__ import annotations

import unittest

from sprout import parse, typecheck_program
from sprout.stdlib import with_prelude
from sprout.typeclass_lowering import TypeclassLoweringError, lower_typeclasses


class TypeclassLoweringTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
