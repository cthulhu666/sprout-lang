from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


class JustfileTests(unittest.TestCase):
    def _recipe_body(self, text: str, name: str) -> str:
        match = re.search(rf"^{re.escape(name)}:\n(?P<body>(?:  .*\n)+)", text, re.MULTILINE)
        self.assertIsNotNone(match)
        assert match is not None
        return match.group("body")

    def test_native_stdlib_recipes_count_test_binary_nonzero_exits(self) -> None:
        text = (ROOT / "justfile").read_text(encoding="utf-8")
        for recipe in ("test-stdlib-stage1", "test-stdlib-stage2"):
            with self.subTest(recipe=recipe):
                body = self._recipe_body(text, recipe)
                self.assertNotIn('out=$("$TMP_BIN" 2>&1) || true', body)
                self.assertIn('if out=$("$TMP_BIN" 2>&1); then', body)
                self.assertIn("RUN FAILED:", body)
