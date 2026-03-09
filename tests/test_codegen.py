from __future__ import annotations

import unittest
import shutil
import subprocess
import tempfile
from pathlib import Path
import sys

from sprout import CodegenError, compile_to_llvm, parse, typecheck_program


class CodegenTests(unittest.TestCase):
    def test_compile_recursive_if_program_to_llvm(self) -> None:
        src = """
        fn fact(n: Int) -> Int =
          if n == 0 then 1 else n * fact(n - 1)

        fn main() -> Int =
          fact(5)
        """
        program = parse(src)
        typecheck_program(program)
        ir = compile_to_llvm(program)

        self.assertIn("define i64 @fact(i64 %n)", ir)
        self.assertIn("define i64 @main()", ir)
        self.assertIn("icmp eq i64", ir)
        self.assertIn("call i64 @fact", ir)

    def test_compile_supports_top_level_const_let(self) -> None:
        src = """
        let base = 40
        let two = 2
        fn main() -> Int = print_int(base + two)
        """
        program = parse(src)
        typecheck_program(program)
        ir = compile_to_llvm(program)
        self.assertIn("@base = private constant i64 40", ir)
        self.assertIn("@two = private constant i64 2", ir)
        self.assertIn("load i64, ptr @base", ir)
        self.assertIn("load i64, ptr @two", ir)

    def test_compile_rejects_non_const_top_level_let(self) -> None:
        src = """
        fn value() -> Int = 1
        let x = value()
        fn main() -> Int = x
        """
        program = parse(src)
        typecheck_program(program)
        with self.assertRaises(CodegenError):
            compile_to_llvm(program)

    def test_compile_with_print_int_external(self) -> None:
        src = """
        fn main() -> Int =
          print_int(42)
        """
        program = parse(src)
        typecheck_program(program)
        ir = compile_to_llvm(program)
        self.assertIn("declare i64 @print_int(i64)", ir)
        self.assertIn("call i64 @print_int(i64 42)", ir)
        self.assertIn("declare i64 @print_str(ptr)", ir)

    def test_compile_main_io_unit_with_print_string(self) -> None:
        src = """
        fn main() -> IO Unit =
          print("hello")
        """
        program = parse(src)
        typecheck_program(program)
        ir = compile_to_llvm(program)
        self.assertIn("define i64 @main()", ir)
        self.assertIn("call i64 @print_str(ptr", ir)

    def test_compile_adt_match(self) -> None:
        src = """
        type MaybeInt =
          | Just Int
          | Nothing

        fn unwrap(m: MaybeInt) -> Int =
          match m with
          | Just x -> x
          | Nothing -> 0

        fn main() -> Int =
          print_int(unwrap(Just(42)))
        """
        program = parse(src)
        typecheck_program(program)
        ir = compile_to_llvm(program)
        self.assertIn("call i64 @sprout_make1(i64 0, i64 42)", ir)
        self.assertIn("call i64 @sprout_tag", ir)
        self.assertIn("call i64 @sprout_field", ir)

    def test_compile_generic_identity_erased(self) -> None:
        src = """
        fn id(x: a) -> a = x
        fn main() -> Int = print_int(id(42))
        """
        program = parse(src)
        typecheck_program(program)
        ir = compile_to_llvm(program)
        self.assertIn("define i64 @id(i64 %x)", ir)
        self.assertIn("call i64 @id(i64 42)", ir)

    def test_compile_higher_order_function_param(self) -> None:
        src = """
        fn inc(x: Int) -> Int = x + 1
        fn apply(x: Int, f: Int -> Int) -> Int = f(x)
        fn main() -> Int = print_int(apply(41, inc))
        """
        program = parse(src)
        typecheck_program(program)
        ir = compile_to_llvm(program)
        self.assertIn("define i64 @apply(i64 %x, ptr %f)", ir)
        self.assertIn("call i64 %f(i64 %x)", ir)

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_native_compile_and_execute(self) -> None:
        src = """
        fn main() -> Int =
          print_int(42)
        """
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            spr_path = tmp_path / "prog.spr"
            bin_path = tmp_path / "prog"
            spr_path.write_text(src, encoding="utf-8")

            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "sprout.cli",
                    "compile",
                    str(spr_path),
                    "--native",
                    "-o",
                    str(bin_path),
                ],
                check=True,
            )
            run = subprocess.run([str(bin_path)], check=False, capture_output=True, text=True)
            self.assertEqual(run.stdout.strip(), "42")
            self.assertEqual(run.returncode, 42)

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_native_short_circuit_and(self) -> None:
        src = """
        fn side() -> Bool =
          print_int(1) == 1

        fn main() -> Int =
          if false && side() then print_int(0) else print_int(42)
        """
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            spr_path = tmp_path / "prog.spr"
            bin_path = tmp_path / "prog"
            spr_path.write_text(src, encoding="utf-8")
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "sprout.cli",
                    "compile",
                    str(spr_path),
                    "--native",
                    "-o",
                    str(bin_path),
                ],
                check=True,
            )
            run = subprocess.run([str(bin_path)], check=False, capture_output=True, text=True)
            self.assertEqual(run.stdout.strip(), "42")
            self.assertEqual(run.returncode, 42)

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_native_compile_main_io_unit(self) -> None:
        src = """
        fn main() -> IO Unit =
          print("hello")
        """
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            spr_path = tmp_path / "prog.spr"
            bin_path = tmp_path / "prog"
            spr_path.write_text(src, encoding="utf-8")
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "sprout.cli",
                    "compile",
                    str(spr_path),
                    "--native",
                    "-o",
                    str(bin_path),
                ],
                check=True,
            )
            run = subprocess.run([str(bin_path)], check=False, capture_output=True, text=True)
            self.assertEqual(run.stdout.strip(), "hello")
            self.assertEqual(run.returncode, 0)

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_native_compile_adt_match(self) -> None:
        src = """
        type MaybeInt =
          | Just Int
          | Nothing

        fn unwrap(m: MaybeInt) -> Int =
          match m with
          | Just x -> x
          | Nothing -> 0

        fn main() -> Int =
          print_int(unwrap(Just(42)))
        """
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            spr_path = tmp_path / "prog.spr"
            bin_path = tmp_path / "prog"
            spr_path.write_text(src, encoding="utf-8")
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "sprout.cli",
                    "compile",
                    str(spr_path),
                    "--native",
                    "-o",
                    str(bin_path),
                ],
                check=True,
            )
            run = subprocess.run([str(bin_path)], check=False, capture_output=True, text=True)
            self.assertEqual(run.stdout.strip(), "42")
            self.assertEqual(run.returncode, 42)

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_native_compile_higher_order(self) -> None:
        src = """
        fn inc(x: Int) -> Int = x + 1
        fn apply(x: Int, f: Int -> Int) -> Int = f(x)
        fn main() -> Int = print_int(apply(41, inc))
        """
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            spr_path = tmp_path / "prog.spr"
            bin_path = tmp_path / "prog"
            spr_path.write_text(src, encoding="utf-8")
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "sprout.cli",
                    "compile",
                    str(spr_path),
                    "--native",
                    "-o",
                    str(bin_path),
                ],
                check=True,
            )
            run = subprocess.run([str(bin_path)], check=False, capture_output=True, text=True)
            self.assertEqual(run.stdout.strip(), "42")
            self.assertEqual(run.returncode, 42)


if __name__ == "__main__":
    unittest.main()
