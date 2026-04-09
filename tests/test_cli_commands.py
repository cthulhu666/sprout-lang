from __future__ import annotations

import os
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

    def test_run_with_http_imports(self) -> None:
        src = """
        module main
        import stdlib.http (http_ok)

        fn main() -> Unit !{IO} =
          print(http_ok("ready"))
        """
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "http_test.spr"
            path.write_text(src, encoding="utf-8")
            run = subprocess.run(
                [sys.executable, "-m", "sprout.cli", "run", str(path)],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(run.returncode, 0, msg=run.stderr)
            self.assertIn("HTTP/1.1 200 OK", run.stdout)
            self.assertIn("ready", run.stdout)

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

    def test_run_passes_program_args_to_argv_get(self) -> None:
        src = """
        module main
        import stdlib.collections (Maybe)

        fn main() -> Unit !{IO} =
          match argv_get(0) with
          | Just value -> print(value)
          | Nothing -> print("missing")
        """
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "argv_test.spr"
            path.write_text(src, encoding="utf-8")
            run = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "sprout.cli",
                    "run",
                    str(path),
                    "http://example.test",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(run.returncode, 0, msg=run.stderr)
            self.assertEqual(run.stdout.strip(), "http://example.test")

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
        fn main() -> Unit !{IO} =
          print(show_box(Box(42)))
        """
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "typeclass_test.spr"
            path.write_text(src, encoding="utf-8")
            run = subprocess.run(
                [sys.executable, "-m", "sprout.cli", "run", str(path)],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(run.returncode, 0, msg=run.stderr)
            self.assertIn("42", run.stdout)

    def test_run_functor_foldable_demo(self) -> None:
        run = subprocess.run(
            [sys.executable, "-m", "sprout.cli", "run", "examples/typeclass_functor_foldable_demo.sprout"],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(run.returncode, 0, msg=run.stderr)
        self.assertIn("27", run.stdout)

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

    def test_run_foldable_to_vec_demo(self) -> None:
        run = subprocess.run(
            [sys.executable, "-m", "sprout.cli", "run", "examples/foldable_demo.sprout"],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(run.returncode, 0, msg=run.stderr)
        self.assertIn("5", run.stdout)

    def test_run_collections_utils_demo(self) -> None:
        run = subprocess.run(
            [sys.executable, "-m", "sprout.cli", "run", "examples/collections_demo.sprout"],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(run.returncode, 0, msg=run.stderr)
        self.assertIn("57", run.stdout)

    def test_run_text_demo(self) -> None:
        run = subprocess.run(
            [sys.executable, "-m", "sprout.cli", "run", "examples/text_demo.sprout", "zażółć gęślą jaźń"],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(run.returncode, 0, msg=run.stderr)
        self.assertEqual(run.stdout.strip(), "len=17|first=z|last=ń|initials=zgj|no_vowels=zżłć gśl jźń")

    def test_run_regex_demo(self) -> None:
        run = subprocess.run(
            [sys.executable, "-m", "sprout.cli", "run", "examples/regex_demo.sprout", "ticket=AB-42 owner=ada"],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(run.returncode, 0, msg=run.stderr)
        self.assertEqual(
            run.stdout.strip(),
            "matched=AB-42|prefix=ticket=|suffix= owner=ada|replaced=ticket=<ID> owner=ada|escaped=literal \\[A-Z\\]\\+",
        )

    def test_run_result_control_flow_demo(self) -> None:
        run = subprocess.run(
            [sys.executable, "-m", "sprout.cli", "run", "--with-stdlib", "examples/result_demo.sprout"],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(run.returncode, 0, msg=run.stderr)
        self.assertEqual(run.stdout.strip(), "43\n43\n43\n43\nerror:too-small\n7")

    def test_run_io_do_demo_without_configured_name(self) -> None:
        env = dict(os.environ)
        env.pop("SPROUT_NAME", None)
        run = subprocess.run(
            [sys.executable, "-m", "sprout.cli", "run", "examples/io_do_demo.sprout"],
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(run.returncode, 0, msg=run.stderr)
        self.assertEqual(run.stdout.strip(), "do:io-maybe\nreading SPROUT_NAME\nio-do:name=anonymous")

    def test_run_io_result_do_demo(self) -> None:
        run = subprocess.run(
            [sys.executable, "-m", "sprout.cli", "run", "examples/io_result_do_demo.sprout"],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(run.returncode, 0, msg=run.stderr)
        self.assertEqual(run.stdout.strip(), "do:io-result\ncapturing payload size\nio-do:bytes=8")

    def test_compile_all_examples(self) -> None:
        example_flags = {
            "examples/result_demo.sprout": ["--with-stdlib"],
        }
        failures: list[tuple[Path, str, str]] = []
        for path in sorted(Path("examples").glob("*.sprout")):
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

    def test_run_sentry_issue_browser_reports_missing_env(self) -> None:
        proc = subprocess.run(
            [sys.executable, "-m", "sprout.cli", "run", "examples/sentry_issue_browser.sprout"],
            check=False,
            capture_output=True,
            text=True,
            env={},
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("Configuration error: missing environment variable: SENTRY_ORG", proc.stdout)

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

    def test_run_rejects_raw_vector_builtins_in_non_stdlib_module(self) -> None:
        src = """
        module app.main
        fn main() -> Unit !{IO} =
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
                fn main() -> Unit !{IO} =
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
        fn main() -> Unit !{IO} =
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

    def test_run_rejects_raw_string_builtins_in_non_stdlib_module(self) -> None:
        src = """
        module app.main
        fn main() -> Unit !{IO} =
          print(str_len("sprout"))
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
            self.assertIn("string builtin is internal", run.stdout)

    def test_run_rejects_raw_regex_builtins_in_non_stdlib_module(self) -> None:
        src = """
        module app.main
        fn main() -> Unit !{IO} =
          print(regex_escape("a+b"))
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
            self.assertIn("regex_* builtin is internal", run.stdout)

    def test_run_rejects_raw_bytes_builtins_in_non_stdlib_module(self) -> None:
        src = """
        module app.main
        fn main() -> Unit !{IO} =
          print(bytes_length(bytes_singleton(7)))
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
            self.assertIn("bytes_* builtin is internal", run.stdout)

    def test_run_allows_raw_string_builtins_in_stdlib_module(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "stdlib").mkdir(parents=True)
            (root / "stdlib" / "internal_string.sprout").write_text(
                """
                module stdlib.internal_string
                export fn raw_len() -> Int =
                  str_len("sprout")
                """,
                encoding="utf-8",
            )
            (root / "main.sprout").write_text(
                """
                module app.main
                import stdlib.internal_string (raw_len)
                fn main() -> Unit !{IO} =
                  print(raw_len())
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

    def test_run_allows_raw_regex_builtins_in_stdlib_module(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "stdlib").mkdir(parents=True)
            (root / "stdlib" / "internal_regex.sprout").write_text(
                """
                module stdlib.internal_regex
                export fn escaped() -> String =
                  regex_escape("a+b")
                """,
                encoding="utf-8",
            )
            (root / "main.sprout").write_text(
                """
                module app.main
                import stdlib.internal_regex (escaped)
                fn main() -> Unit !{IO} =
                  print(escaped())
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
            self.assertEqual(run.stdout.strip(), "a\\+b")

    def test_run_allows_raw_bytes_builtins_in_stdlib_module(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "stdlib").mkdir(parents=True)
            (root / "stdlib" / "internal_bytes.sprout").write_text(
                """
                module stdlib.internal_bytes
                export fn raw_count() -> Int =
                  bytes_length(bytes_append(bytes_singleton(1), bytes_singleton(2)))
                """,
                encoding="utf-8",
            )
            (root / "main.sprout").write_text(
                """
                module app.main
                import stdlib.internal_bytes (raw_count)
                fn main() -> Unit !{IO} =
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
            self.assertIn("2", run.stdout)

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
                fn main() -> Unit !{IO} =
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

    def test_run_rejects_non_unit_io_entrypoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "main.sprout"
            path.write_text(
                """
                module app.main
                fn main() -> Maybe String !{IO} =
                  Just("hello")
                """,
                encoding="utf-8",
            )
            run = subprocess.run(
                [sys.executable, "-m", "sprout.cli", "run", str(path)],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(run.returncode, 1)
            self.assertIn("Executable entrypoint `app.main.main` must have type Unit !{IO}", run.stdout)
            self.assertIn("Maybe String !{IO}", run.stdout)

    def test_compile_rejects_non_unit_io_entrypoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "main.sprout"
            out = Path(tmp) / "out.ll"
            path.write_text(
                """
                module app.main
                fn main() -> Int !{IO} =
                  print_int(41)
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
            self.assertIn("Executable entrypoint `app.main.main` must have type Unit !{IO}", compile_proc.stdout)
            self.assertIn("Int !{IO}", compile_proc.stdout)
            self.assertFalse(out.exists())

    def test_run_rejects_non_zero_arity_entrypoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "main.sprout"
            path.write_text(
                """
                module app.main
                fn main(x: Int) -> Unit !{IO} =
                  print(x)
                """,
                encoding="utf-8",
            )
            run = subprocess.run(
                [sys.executable, "-m", "sprout.cli", "run", str(path)],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(run.returncode, 1)
            self.assertIn("Executable entrypoint `app.main.main` must take zero arguments", run.stdout)

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

    def test_run_rejects_pure_entrypoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "main.sprout"
            path.write_text(
                """
                module app.main
                fn main() -> Unit =
                  print("ok")
                """,
                encoding="utf-8",
            )
            run = subprocess.run(
                [sys.executable, "-m", "sprout.cli", "run", str(path)],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(run.returncode, 1)
            self.assertIn("Function app.main.main requires undeclared effects", run.stdout)

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
