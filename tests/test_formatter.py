from __future__ import annotations

import unittest

from sprout.formatter import format_source, lint_source


class FormatterTests(unittest.TestCase):
    def test_format_source_normalizes_spacing_without_erasing_surface_sugar(self) -> None:
        src = 'fn main()->Result String Int=Ok(20)|>result_pipe_ok(inc)  #demo'
        self.assertEqual(
            format_source(src),
            'fn main() -> Result String Int = Ok(20) |> result_pipe_ok(inc)  #demo\n',
        )

    def test_format_source_preserves_comment_only_lines(self) -> None:
        src = "# heading\nfn main()->Int=1\n"
        self.assertEqual(format_source(src), "# heading\nfn main() -> Int = 1\n")

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
