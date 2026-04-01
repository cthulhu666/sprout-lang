from __future__ import annotations

import io
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout

from sprout import cli as sprout_cli
from sprout.interpreter import RuntimeError, run_program
from sprout.parser import ParseError, parse
from sprout.stdlib import with_prelude
from sprout.tokenizer import TokenizeError
from sprout.typeclass_lowering import lower_typeclasses
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
    def test_run_cases(self) -> None:
        for spr_file in sorted((ROOT / "run").glob("*.spr")):
            with self.subTest(case=spr_file.name):
                out_file = spr_file.with_suffix(".out")
                program = parse(_load_for_case(spr_file))
                typecheck_program(program)
                lowered = lower_typeclasses(program)
                typecheck_program(lowered)
                out = io.StringIO()
                run_program(lowered, stdout=out)
                self.assertEqual(out.getvalue(), _read(out_file))

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

    def test_runtime_error_cases(self) -> None:
        for spr_file in sorted((ROOT / "runtime_error").glob("*.spr")):
            with self.subTest(case=spr_file.name):
                err_file = spr_file.with_suffix(".err")
                expected = _read(err_file).strip()
                program = parse(_load_for_case(spr_file))
                typecheck_program(program)
                lowered = lower_typeclasses(program)
                typecheck_program(lowered)
                with self.assertRaises(RuntimeError) as ctx:
                    run_program(lowered)
                self.assertIn(expected, str(ctx.exception))

    def test_executable_error_cases(self) -> None:
        for spr_file in sorted((ROOT / "executable_error").glob("*.spr")):
            with self.subTest(case=spr_file.name):
                err_file = spr_file.with_suffix(".err")
                expected = _read(err_file).strip()
                extra_args = ["--with-stdlib"] if spr_file.stem.startswith("stdlib_") else []

                run_output = io.StringIO()
                with redirect_stdout(run_output):
                    run_status = sprout_cli.main(["run", *extra_args, str(spr_file)])
                self.assertEqual(run_status, 1)
                self.assertIn(expected, run_output.getvalue())

                with tempfile.TemporaryDirectory() as tmp:
                    out = Path(tmp) / "case.ll"
                    compile_output = io.StringIO()
                    with redirect_stdout(compile_output):
                        compile_status = sprout_cli.main(["compile", *extra_args, str(spr_file), "-o", str(out)])
                    self.assertEqual(compile_status, 1)
                    self.assertIn(expected, compile_output.getvalue())
                    self.assertFalse(out.exists())


if __name__ == "__main__":
    unittest.main()
