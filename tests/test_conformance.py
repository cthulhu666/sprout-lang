from __future__ import annotations

from pathlib import Path
import unittest

from sprout.parser import ParseError, parse
from sprout.stdlib import with_prelude
from sprout.tokenizer import TokenizeError
from sprout.typechecker import TypeCheckError, typecheck_program


ROOT = Path(__file__).parent / "conformance"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _load_for_case(path: Path) -> str:
    source = _read(path)
    if path.stem.startswith("stdlib_"):
        return with_prelude(source)
    return source


class ConformanceTests(unittest.TestCase):
    def test_parse_error_cases(self) -> None:
        for spr_file in sorted((ROOT / "parse_error").glob("*.spr")):
            with self.subTest(case=spr_file.name):
                err_file = spr_file.with_suffix(".err")
                expected = _read(err_file).strip()
                with self.assertRaises((ParseError, TokenizeError)) as ctx:
                    parse(_read(spr_file))
                self.assertIn(expected, str(ctx.exception))

    def test_type_error_cases(self) -> None:
        for spr_file in sorted((ROOT / "type_error").glob("*.spr")):
            with self.subTest(case=spr_file.name):
                err_file = spr_file.with_suffix(".err")
                expected = _read(err_file).strip()
                with self.assertRaises(TypeCheckError) as ctx:
                    typecheck_program(parse(_load_for_case(spr_file)))
                self.assertIn(expected, str(ctx.exception))

if __name__ == "__main__":
    unittest.main()
