from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
import sys
import unittest


class CliTests(unittest.TestCase):
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

    def test_repl_supports_declarations_expressions_and_type_queries(self) -> None:
        run = subprocess.run(
            [sys.executable, "-m", "sprout.cli", "repl"],
            check=False,
            capture_output=True,
            text=True,
            input="let x = 41\nx + 1\n[1,2,3]\n:t x\n:quit\n",
        )
        self.assertEqual(run.returncode, 0, msg=run.stderr)
        self.assertEqual(run.stderr, "")
        self.assertIn("ok", run.stdout)
        self.assertIn("42", run.stdout)
        self.assertIn("Cons(1, Cons(2, Cons(3, Nil)))", run.stdout)
        self.assertIn("Int", run.stdout)

    def test_repl_help_mentions_type_shorthand(self) -> None:
        run = subprocess.run(
            [sys.executable, "-m", "sprout.cli", "repl"],
            check=False,
            capture_output=True,
            text=True,
            input=":help\n:quit\n",
        )
        self.assertEqual(run.returncode, 0, msg=run.stderr)
        self.assertIn(":type EXPR", run.stdout)
        self.assertIn(":t EXPR", run.stdout)

    def test_repl_with_stdlib_flag_loads_prelude(self) -> None:
        run = subprocess.run(
            [sys.executable, "-m", "sprout.cli", "repl", "--with-stdlib"],
            check=False,
            capture_output=True,
            text=True,
            input=":type split_ints(\"1 2 3\")\n:quit\n",
        )
        self.assertEqual(run.returncode, 0, msg=run.stderr)
        self.assertEqual(run.stderr, "")
        self.assertIn("List Int", run.stdout)

    def test_repl_default_supports_list_append_operator(self) -> None:
        run = subprocess.run(
            [sys.executable, "-m", "sprout.cli", "repl"],
            check=False,
            capture_output=True,
            text=True,
            input="[1,2] ++ [3,4]\n:quit\n",
        )
        self.assertEqual(run.returncode, 0, msg=run.stderr)
        self.assertEqual(run.stderr, "")
        self.assertIn("Cons(1, Cons(2, Cons(3, Cons(4, Nil))))", run.stdout)

    def test_repl_with_http_stdlib_loads_http_helpers(self) -> None:
        run = subprocess.run(
            [sys.executable, "-m", "sprout.cli", "repl", "--with-http-stdlib"],
            check=False,
            capture_output=True,
            text=True,
            input=":type http_ok(\"x\")\n:quit\n",
        )
        self.assertEqual(run.returncode, 0, msg=run.stderr)
        self.assertEqual(run.stderr, "")
        self.assertIn("String", run.stdout)

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

    def test_run_collections_utils_demo(self) -> None:
        run = subprocess.run(
            [
                sys.executable,
                "-m",
                "sprout.cli",
                "run",
                "examples/collections_utils_demo.sprout",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(run.returncode, 0, msg=run.stderr)
        self.assertIn("57", run.stdout)

    def test_run_result_control_flow_demo(self) -> None:
        run = subprocess.run(
            [
                sys.executable,
                "-m",
                "sprout.cli",
                "run",
                "--with-stdlib",
                "examples/result_control_flow_demo.sprout",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(run.returncode, 0, msg=run.stderr)
        self.assertEqual(run.stdout.strip(), "43\n43\n43\n43\nerror:too-small\n7")

    def test_compile_all_examples(self) -> None:
        example_flags = {
            "examples/result_control_flow_demo.sprout": ["--with-stdlib"],
        }
        failures: list[tuple[Path, str, str]] = []
        for path in sorted(Path("examples").glob("*.sprout")):
            with tempfile.TemporaryDirectory() as tmp:
                out = Path(tmp) / f"{path.stem}.ll"
                extra = example_flags.get(str(path), [])
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
                if proc.returncode != 0:
                    failures.append((path, proc.stdout, proc.stderr))
        if failures:
            details = "\n".join(
                f"{path}:\nstdout:\n{stdout}\nstderr:\n{stderr}"
                for path, stdout, stderr in failures
            )
            self.fail(f"example compile failures:\n{details}")

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

    def test_run_rejects_raw_string_builtins_in_non_stdlib_module(self) -> None:
        src = """
        module app.main
        fn main() -> IO Unit =
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

    def test_run_rejects_raw_bytes_builtins_in_non_stdlib_module(self) -> None:
        src = """
        module app.main
        fn main() -> IO Unit =
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
                fn main() -> IO Unit =
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
            self.assertIn("6", run.stdout)

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
