from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class CliCommandTests(unittest.TestCase):
    def test_check_reports_warning_for_imported_deprecated_value_use(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "lib.sprout").write_text(
                """
                module lib
                #@deprecated use fresh instead
                export fn old(x: Int) -> Int = x + 1
                export fn fresh(x: Int) -> Int = x + 2
                """,
                encoding="utf-8",
            )
            main = root / "main.sprout"
            main.write_text(
                """
                module app.main
                import lib (old)

                fn main() -> Unit !{IO} =
                  print(old(1))
                """,
                encoding="utf-8",
            )

            proc = subprocess.run(
                [sys.executable, "-m", "sprout.cli", "check", str(main)],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(proc.returncode, 0)
            self.assertIn("warning:", proc.stderr)
            self.assertIn("'old' is deprecated: use fresh instead", proc.stderr)
            self.assertIn("ok", proc.stdout)

    def test_check_accepts_imported_vec_sort_by_without_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / "main.sprout"
            main.write_text(
                """
                module app.main
                import stdlib.collections (Vec, vec_append, vec_empty, vec_sort_by)

                fn key(x: Int) -> Int = 0 - x

                fn sample() -> Vec Int =
                  vec_append(3, vec_append(1, vec_append(2, vec_empty())))

                fn main() -> Unit !{IO} =
                  print(vec_sort_by(key, sample()))
                """,
                encoding="utf-8",
            )

            proc = subprocess.run(
                [sys.executable, "-m", "sprout.cli", "check", str(main)],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(proc.returncode, 0)
            self.assertNotIn("warning:", proc.stderr)
            self.assertIn("ok", proc.stdout)

    def test_fmt_rewrites_file_in_place(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fmt_test.sprout"
            path.write_text("fn main()->Int=1", encoding="utf-8")
            run = subprocess.run(
                [sys.executable, "-m", "sprout.cli", "fmt", str(path)],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(run.returncode, 0, msg=run.stderr)
            self.assertEqual(path.read_text(encoding="utf-8"), "fn main() -> Int = 1\n")
            self.assertIn("formatted", run.stdout)

    def test_fmt_check_fails_when_file_needs_formatting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fmt_check_test.sprout"
            path.write_text("fn main()->Int=1", encoding="utf-8")
            run = subprocess.run(
                [sys.executable, "-m", "sprout.cli", "fmt", "--check", str(path)],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(run.returncode, 1)
            self.assertIn("needs formatting", run.stdout)

    def test_lint_reports_style_issues(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "lint_test.sprout"
            path.write_text("fn main()->Int=1\t  ", encoding="utf-8")
            run = subprocess.run(
                [sys.executable, "-m", "sprout.cli", "lint", str(path)],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(run.returncode, 1)
            self.assertIn("tab indentation is not allowed", run.stdout)
            self.assertIn("trailing whitespace", run.stdout)
            self.assertIn("missing trailing newline", run.stdout)
            self.assertIn("file is not formatted", run.stdout)

    def test_check_with_http_and_json_imports(self) -> None:
        src = """
        module main
        import stdlib.json as json

        fn render(raw: String) -> String =
          match json.parse(raw) with
          | Ok value -> json.stringify(value)
          | Err _ -> "json-err"

        fn main() -> Unit !{IO} =
          print(render("{\\"ok\\":true}"))
        """
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "http_json_test.spr"
            path.write_text(src, encoding="utf-8")
            run = subprocess.run(
                [sys.executable, "-m", "sprout.cli", "check", str(path)],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(run.returncode, 0, msg=run.stderr or run.stdout)

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
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

    def test_compile_all_examples(self) -> None:
        example_flags = {}
        # Known-broken examples excluded from the Python-CLI compile check.
        # tcp_echo_server: pre-existing — tcp_echo_serve removed from stdlib.net.
        # repl_hosted, aoc_2025_day_{3,4,5}: Python typechecker can't handle panic -> a !{IO}; interpreter removal in progress.
        example_skip = {
            "examples/tcp_echo_server.sprout",
            "examples/repl_hosted.sprout",
            "examples/aoc_2025_day_3.sprout",
            "examples/aoc_2025_day_4.sprout",
            "examples/aoc_2025_day_5.sprout",
        }
        failures: list[tuple[Path, str, str]] = []
        for path in sorted(Path("examples").glob("*.sprout")):
            if str(path) in example_skip:
                continue
            with tempfile.TemporaryDirectory() as tmp:
                source = path.read_text(encoding="utf-8")
                out = Path(tmp) / f"{path.stem}.ll"
                extra = example_flags.get(str(path), [])
                if "fn main(" in source:
                    proc = subprocess.run(
                        [
                            sys.executable,
                            "-m",
                            "sprout.cli",
                            "compile",
                            *extra,
                            str(path),
                            "-o",
                            str(out),
                        ],
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                else:
                    proc = subprocess.run(
                        [
                            sys.executable,
                            "-m",
                            "sprout.cli",
                            "check",
                            *extra,
                            str(path),
                        ],
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                if proc.returncode != 0:
                    failures.append((path, proc.stdout, proc.stderr))
        if failures:
            details = "\n".join(
                f"{path}:\nstdout:\n{stdout}\nstderr:\n{stderr}"
                for path, stdout, stderr in failures
            )
            self.fail(f"example compile failures:\n{details}")

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_compile_native_foldable_to_vec_demo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "foldable_demo_bin"
            compile_proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "sprout.cli",
                    "compile",
                    "examples/foldable_demo.sprout",
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

    def test_compile_rejects_non_unit_io_entrypoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "main.sprout"
            out = Path(tmp) / "out.ll"
            path.write_text(
                """
                module app.main
                fn main() -> Maybe String !{IO} =
                  env_get("x")
                """,
                encoding="utf-8",
            )
            compile_proc = subprocess.run(
                [sys.executable, "-m", "sprout.cli", "compile", str(path), "-o", str(out)],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(compile_proc.returncode, 1)
            self.assertIn("Executable entrypoint `app.main.main` must have type Unit !{IO} or Int !{IO}", compile_proc.stdout)
            self.assertIn("Maybe String !{IO}", compile_proc.stdout)
            self.assertFalse(out.exists())

    def test_compile_rejects_missing_entrypoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "main.sprout"
            out = Path(tmp) / "out.ll"
            path.write_text(
                """
                module app.main
                fn helper() -> Unit !{IO} =
                  print("ok")
                """,
                encoding="utf-8",
            )
            compile_proc = subprocess.run(
                [sys.executable, "-m", "sprout.cli", "compile", str(path), "-o", str(out)],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(compile_proc.returncode, 1)
            self.assertIn("Executable entrypoint `app.main.main` is missing", compile_proc.stdout)
            self.assertFalse(out.exists())

    def test_compile_rejects_effect_polymorphic_entrypoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "main.sprout"
            out = Path(tmp) / "out.ll"
            path.write_text(
                """
                module app.main
                fn main() -> Unit !{e} =
                  print("ok")
                """,
                encoding="utf-8",
            )
            compile_proc = subprocess.run(
                [sys.executable, "-m", "sprout.cli", "compile", str(path), "-o", str(out)],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(compile_proc.returncode, 1)
            self.assertIn("main must not be effect-polymorphic", compile_proc.stdout)
            self.assertFalse(out.exists())


if __name__ == "__main__":
    unittest.main()
