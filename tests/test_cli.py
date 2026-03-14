from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
import sys
import unittest


class CliTests(unittest.TestCase):
    def test_run_with_http_stdlib_flag(self) -> None:
        src = """
        fn main() -> IO Unit =
          print(http_ok("ready"))
        """
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "http_test.spr"
            path.write_text(src, encoding="utf-8")
            run = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "sprout.cli",
                    "run",
                    "--with-http-stdlib",
                    str(path),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(run.returncode, 0, msg=run.stderr)
            self.assertIn("HTTP/1.1 200 OK", run.stdout)
            self.assertIn("ready", run.stdout)

    def test_run_with_typeclass_lowering(self) -> None:
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
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "typeclass_test.spr"
            path.write_text(src, encoding="utf-8")
            run = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "sprout.cli",
                    "run",
                    str(path),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(run.returncode, 0, msg=run.stderr)
            self.assertIn("42", run.stdout)

    def test_run_functor_foldable_demo(self) -> None:
        run = subprocess.run(
            [
                sys.executable,
                "-m",
                "sprout.cli",
                "run",
                "examples/typeclass_functor_foldable_demo.sprout",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(run.returncode, 0, msg=run.stderr)
        self.assertIn("27", run.stdout)

    def test_compile_native_functor_foldable_demo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "demo_bin"
            compile_proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "sprout.cli",
                    "compile",
                    "examples/typeclass_functor_foldable_demo.sprout",
                    "--native",
                    "-o",
                    str(out),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(compile_proc.returncode, 0, msg=compile_proc.stderr)
            run_proc = subprocess.run([str(out)], check=False, capture_output=True, text=True)
            self.assertEqual(run_proc.returncode, 0, msg=run_proc.stderr)
            self.assertIn("27", run_proc.stdout)

    def test_run_foldable_to_vec_demo(self) -> None:
        run = subprocess.run(
            [
                sys.executable,
                "-m",
                "sprout.cli",
                "run",
                "examples/foldable_to_vec_demo.sprout",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(run.returncode, 0, msg=run.stderr)
        self.assertIn("5", run.stdout)

    def test_compile_native_foldable_to_vec_demo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "foldable_demo_bin"
            compile_proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "sprout.cli",
                    "compile",
                    "examples/foldable_to_vec_demo.sprout",
                    "--native",
                    "-o",
                    str(out),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(compile_proc.returncode, 0, msg=compile_proc.stderr)
            run_proc = subprocess.run([str(out)], check=False, capture_output=True, text=True)
            self.assertEqual(run_proc.returncode, 0, msg=run_proc.stderr)
            self.assertIn("5", run_proc.stdout)

    def test_run_rejects_raw_vector_builtins_in_non_stdlib_module(self) -> None:
        src = """
        module app.main
        fn main() -> IO Unit =
          print(vector_length(vector_empty()))
        """
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "main.sprout"
            path.write_text(src, encoding="utf-8")
            run = subprocess.run(
                [sys.executable, "-m", "sprout.cli", "run", str(path)],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(run.returncode, 1)
            self.assertIn("vector_* builtin is internal", run.stdout)

    def test_run_allows_raw_vector_builtins_in_stdlib_module(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "stdlib").mkdir(parents=True)
            (root / "stdlib" / "internal.sprout").write_text(
                """
                module stdlib.internal
                export fn raw_count() -> Int =
                  vector_length(vector_empty())
                """,
                encoding="utf-8",
            )
            (root / "main.sprout").write_text(
                """
                module app.main
                import stdlib.internal (raw_count)
                fn main() -> IO Unit =
                  print(raw_count())
                """,
                encoding="utf-8",
            )
            run = subprocess.run(
                [sys.executable, "-m", "sprout.cli", "run", str(root / "main.sprout")],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(run.returncode, 0, msg=run.stderr)
            self.assertIn("0", run.stdout)

    def test_run_rejects_raw_map_builtins_in_non_stdlib_module(self) -> None:
        src = """
        module app.main
        fn main() -> IO Unit =
          print(map_size(map_empty()))
        """
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "main.sprout"
            path.write_text(src, encoding="utf-8")
            run = subprocess.run(
                [sys.executable, "-m", "sprout.cli", "run", str(path)],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(run.returncode, 1)
            self.assertIn("map_* builtin is internal", run.stdout)

    def test_run_allows_raw_map_builtins_in_stdlib_module(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "stdlib").mkdir(parents=True)
            (root / "stdlib" / "internal_map.sprout").write_text(
                """
                module stdlib.internal_map
                export fn raw_count() -> Int =
                  map_size(map_set(map_empty(), "x", 1))
                """,
                encoding="utf-8",
            )
            (root / "main.sprout").write_text(
                """
                module app.main
                import stdlib.internal_map (raw_count)
                fn main() -> IO Unit =
                  print(raw_count())
                """,
                encoding="utf-8",
            )
            run = subprocess.run(
                [sys.executable, "-m", "sprout.cli", "run", str(root / "main.sprout")],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(run.returncode, 0, msg=run.stderr)
            self.assertIn("1", run.stdout)


if __name__ == "__main__":
    unittest.main()
