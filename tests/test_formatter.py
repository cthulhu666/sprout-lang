from __future__ import annotations

import unittest

from sprout.formatter import format_source, lint_source


class FormatterTests(unittest.TestCase):
    def test_format_source_normalizes_spacing_without_erasing_surface_sugar(self) -> None:
        src = 'fn main()->Result String Int=Ok(20)|>result_map(inc)  #demo'
        self.assertEqual(
            format_source(src),
            'fn main() -> Result String Int = Ok(20) |> result_map(inc)  #demo\n',
        )

    def test_format_source_preserves_comment_only_lines(self) -> None:
        src = "# heading\nfn main()->Int=1\n"
        self.assertEqual(format_source(src), "# heading\nfn main() -> Int = 1\n")

    def test_format_source_uses_no_space_before_colon(self) -> None:
        src = "fn pair(n : Int, xs:List Int)->Dict Int={foo : n}\n"
        self.assertEqual(
            format_source(src),
            "fn pair(n: Int, xs: List Int) -> Dict Int = {foo: n}\n",
        )

    def test_format_source_keeps_space_before_grouping_parens_after_operator(self) -> None:
        src = "fn mod100(n: Int) -> Int =\n  n - (n / 100) * 100\n"
        self.assertEqual(
            format_source(src),
            "fn mod100(n: Int) -> Int =\n  n - (n / 100) * 100\n",
        )

    def test_format_source_keeps_spaces_around_semigroup_append_operator(self) -> None:
        src = 'fn prefix_error(e: String) -> String =\n  "error:"++e\n'
        self.assertEqual(
            format_source(src),
            'fn prefix_error(e: String) -> String =\n  "error:" ++ e\n',
        )

    def test_format_source_preserves_spacing_for_imports_and_type_constructors(self) -> None:
        src = (
            "import stdlib.collections (Vec, vec_empty)\n"
            "type List a =\n"
            "  | Cons a (List a)\n"
            "  | Nil\n"
        )
        self.assertEqual(
            format_source(src),
            "import stdlib.collections (Vec, vec_empty)\n"
            "type List a =\n"
            "  | Cons a (List a)\n"
            "  | Nil\n",
        )

    def test_format_source_preserves_spacing_around_keywords_and_unary_minus(self) -> None:
        src = (
            "class Summable c {\n"
            "  fn sum_values(xs: c) -> Int\n"
            "}\n"
            "fn f(x: Int) -> Int =\n"
            "  if x > 0 then Ok(x) else Err(-1)\n"
            "fn g(r: Result String Int) -> Result String Int =\n"
            "  match r with\n"
            "  | Ok x -> Ok(x)\n"
            "  | Err e -> Err(e)\n"
        )
        self.assertEqual(
            format_source(src),
            "class Summable c {\n"
            "  fn sum_values(xs: c) -> Int\n"
            "}\n"
            "fn f(x: Int) -> Int =\n"
            "  if x > 0 then Ok(x) else Err(-1)\n"
            "fn g(r: Result String Int) -> Result String Int =\n"
            "  match r with\n"
            "  | Ok x -> Ok(x)\n"
            "  | Err e -> Err(e)\n",
        )

    def test_format_source_preserves_effect_annotation_spacing(self) -> None:
        src = "fn main()->Unit!{IO}=print(\"ok\")\n"
        self.assertEqual(
            format_source(src),
            'fn main() -> Unit !{IO} = print("ok")\n',
        )

    def test_format_source_preserves_effect_polymorphic_spacing(self) -> None:
        src = "fn apply_twice(f:Int->Int!{e},x:Int)->Int!{e}=f(f(x))\n"
        self.assertEqual(
            format_source(src),
            "fn apply_twice(f: Int -> Int !{e}, x: Int) -> Int !{e} = f(f(x))\n",
        )

    def test_format_source_preserves_local_where_blocks(self) -> None:
        src = "fn score(n:Int)->Int=\n  x+y\nwhere\n  x=n+1\n  y=x*2\n"
        self.assertEqual(
            format_source(src),
            "fn score(n: Int) -> Int =\n  x + y\nwhere\n  x = n + 1\n  y = x * 2\n",
        )

    def test_lint_source_reports_baseline_style_issues(self) -> None:
        issues = lint_source("fn main()->Int=1\t  ")
        self.assertEqual(
            [issue.message for issue in issues],
            [
                "tab indentation is not allowed",
                "trailing whitespace",
                "missing trailing newline",
                "file is not formatted",
            ],
        )


if __name__ == "__main__":
    unittest.main()
