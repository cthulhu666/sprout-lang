from __future__ import annotations

import os
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
            input="let x = 41\nx + 1\n:t x\n:quit\n",
        )
        self.assertEqual(run.returncode, 0, msg=run.stderr)
        self.assertEqual(run.stderr, "")
        self.assertIn("ok", run.stdout)
        self.assertIn("42", run.stdout)
        self.assertIn("Int", run.stdout)

    def test_repl_default_loads_prelude(self) -> None:
        run = subprocess.run(
            [sys.executable, "-m", "sprout.cli", "repl"],
            check=False,
            capture_output=True,
            text=True,
            input=":type split_ints(\"1 2 3\")\n:quit\n",
        )
        self.assertEqual(run.returncode, 0, msg=run.stderr)
        self.assertEqual(run.stderr, "")
        self.assertIn("List Int", run.stdout)

    def test_repl_type_output_uses_friendly_type_variables(self) -> None:
        run = subprocess.run(
            [sys.executable, "-m", "sprout.cli", "repl"],
            check=False,
            capture_output=True,
            text=True,
            input=":t map\n:quit\n",
        )
        self.assertEqual(run.returncode, 0, msg=run.stderr)
        self.assertEqual(run.stderr, "")
        self.assertIn("forall a b. (a -> b) -> List a -> List b", run.stdout)

    def test_repl_type_query_supports_typeclass_method_values(self) -> None:
        run = subprocess.run(
            [sys.executable, "-m", "sprout.cli", "repl"],
            check=False,
            capture_output=True,
            text=True,
            input=":t fmap\n:quit\n",
        )
        self.assertEqual(run.returncode, 0, msg=run.stderr)
        self.assertEqual(run.stderr, "")
        self.assertIn("forall a b c. (a -> b) -> c a -> c b", run.stdout)

    def test_repl_instances_lists_matching_typeclass_instances(self) -> None:
        run = subprocess.run(
            [sys.executable, "-m", "sprout.cli", "repl"],
            check=False,
            capture_output=True,
            text=True,
            input=":instances List Int\n:quit\n",
        )
        self.assertEqual(run.returncode, 0, msg=run.stderr)
        self.assertEqual(run.stderr, "")
        self.assertIn("Instances for List Int:", run.stdout)
        self.assertIn("Foldable List", run.stdout)
        self.assertIn("Functor List", run.stdout)
        self.assertIn("Semigroup (List a)", run.stdout)

    def test_repl_instances_shorthand_reports_no_matches(self) -> None:
        run = subprocess.run(
            [sys.executable, "-m", "sprout.cli", "repl"],
            check=False,
            capture_output=True,
            text=True,
            input=":i Int\n:quit\n",
        )
        self.assertEqual(run.returncode, 0, msg=run.stderr)
        self.assertEqual(run.stderr, "")
        self.assertIn("No instances for Int", run.stdout)

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
        self.assertIn(":instances TYPE", run.stdout)
        self.assertIn(":i TYPE", run.stdout)

    def test_repl_instances_supports_qualified_types_after_import(self) -> None:
        run = subprocess.run(
            [sys.executable, "-m", "sprout.cli", "repl"],
            check=False,
            capture_output=True,
            text=True,
            input="import stdlib.collections\n:instances collections.Vec Int\n:quit\n",
        )
        self.assertEqual(run.returncode, 0, msg=run.stderr)
        self.assertEqual(run.stderr, "")
        self.assertIn("ok", run.stdout)
        self.assertIn("Instances for Vec Int:", run.stdout)
        self.assertIn("Foldable Vec", run.stdout)
        self.assertIn("Functor Vec", run.stdout)
        self.assertIn("Semigroup (Vec a)", run.stdout)

    def test_repl_imports_make_stdlib_modules_available(self) -> None:
        run = subprocess.run(
            [sys.executable, "-m", "sprout.cli", "repl"],
            check=False,
            capture_output=True,
            text=True,
            input="import stdlib.http\n:type split_ints(\"1 2 3\")\n:type http.http_ok(\"x\")\n:quit\n",
        )
        self.assertEqual(run.returncode, 0, msg=run.stderr)
        self.assertEqual(run.stderr, "")
        self.assertIn("ok", run.stdout)
        self.assertIn("List Int", run.stdout)
        self.assertIn("String", run.stdout)

    def test_repl_resolves_foldable_to_vec_for_list_literal(self) -> None:
        run = subprocess.run(
            [sys.executable, "-m", "sprout.cli", "repl"],
            check=False,
            capture_output=True,
            text=True,
            input="foldable_to_vec([1,2,3])\n:quit\n",
        )
        self.assertEqual(run.returncode, 0, msg=run.stderr)
        self.assertEqual(run.stderr, "")
        self.assertNotIn("Cannot resolve constraint Foldable", run.stdout)
        self.assertIn("Vec(", run.stdout)

    def test_repl_reports_friendly_argument_type_mismatch(self) -> None:
        run = subprocess.run(
            [sys.executable, "-m", "sprout.cli", "repl"],
            check=False,
            capture_output=True,
            text=True,
            input="let l = [1,2,3]\nfmap(l, \\x -> 2 * x)\n:quit\n",
        )
        self.assertEqual(run.returncode, 0, msg=run.stderr)
        self.assertEqual(run.stderr, "")
        self.assertIn("Argument type mismatch: expected a -> b, got List Int", run.stdout)

    def test_repl_default_supports_list_literals(self) -> None:
        run = subprocess.run(
            [sys.executable, "-m", "sprout.cli", "repl"],
            check=False,
            capture_output=True,
            text=True,
            input="[1,2,3]\n:quit\n",
        )
        self.assertEqual(run.returncode, 0, msg=run.stderr)
        self.assertEqual(run.stderr, "")
        self.assertIn("Cons(1, Cons(2, Cons(3, Nil)))", run.stdout)

    def test_repl_invalid_qualified_lookup_reports_error(self) -> None:
        run = subprocess.run(
            [sys.executable, "-m", "sprout.cli", "repl"],
            check=False,
            capture_output=True,
            text=True,
            input="import stdlib.collections\n:t collections.Monoid\n:quit\n",
        )
        self.assertEqual(run.returncode, 0, msg=run.stderr)
        self.assertEqual(run.stderr, "")
        self.assertIn("ok", run.stdout)
        self.assertIn("error:", run.stdout)
        self.assertIn("does not export value 'Monoid'", run.stdout)

    def test_repl_session_tracks_imports_and_declarations(self) -> None:
        from sprout.repl import ReplSession, lookup_type, parse_submission, run_submission

        session = ReplSession()
        session.add_import("import stdlib.http")
        session.add_declaration("let answer = 41")
        _, types = session.parse_and_check(["let temp = answer + 1"])

        self.assertEqual(session.imports, ["import stdlib.http"])
        self.assertEqual(session.declarations, ["let answer = 41"])
        self.assertEqual(session.infer_type("answer + 1"), "Int")
        self.assertEqual(session.infer_type("http.http_ok(\"x\")"), "String")
        self.assertEqual(lookup_type(types, "temp"), "Int")
        self.assertIn("http", session.completion_matches("htt", "htt"))
        self.assertIn("answer", session.completion_matches("ans", "ans"))
        self.assertEqual(parse_submission(""), parse_submission("   "))
        self.assertEqual(parse_submission("import stdlib.http").kind, "import")
        self.assertEqual(parse_submission("let z = 1").kind, "declaration")
        self.assertEqual(parse_submission(":type z").kind, "type")
        self.assertEqual(parse_submission(":instances Int").kind, "instances")
        self.assertEqual(parse_submission("module app.main").kind, "module")
        self.assertEqual(parse_submission("z + 1").kind, "expression")
        self.assertEqual(run_submission(session, parse_submission("let x = 1")).lines, ("ok",))
        self.assertEqual(run_submission(session, parse_submission(":type answer")).lines, ("Int",))

    def test_repl_imports_and_prelude_append_work_together(self) -> None:
        run = subprocess.run(
            [sys.executable, "-m", "sprout.cli", "repl"],
            check=False,
            capture_output=True,
            text=True,
            input='import stdlib.string\n"foo" ++ "foo"\n:type string.concat("a", "b")\n:quit\n',
        )
        self.assertEqual(run.returncode, 0, msg=run.stderr)
        self.assertEqual(run.stderr, "")
        self.assertIn("ok", run.stdout)
        self.assertIn("foofoo", run.stdout)
        self.assertIn("String", run.stdout)

    def test_repl_history_path_defaults_to_home_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            old_home = os.environ.get("HOME")
            os.environ["HOME"] = tmp
            try:
                from sprout.repl import repl_history_path

                self.assertEqual(repl_history_path(), Path(tmp) / ".sprout_repl_history")
            finally:
                if old_home is None:
                    os.environ.pop("HOME", None)
                else:
                    os.environ["HOME"] = old_home

    def test_repl_history_path_honors_environment_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            override = str(Path(tmp) / "custom-history")
            old_value = os.environ.get("SPROUT_REPL_HISTORY")
            os.environ["SPROUT_REPL_HISTORY"] = override
            try:
                from sprout.repl import repl_history_path

                self.assertEqual(repl_history_path(), Path(override))
            finally:
                if old_value is None:
                    os.environ.pop("SPROUT_REPL_HISTORY", None)
                else:
                    os.environ["SPROUT_REPL_HISTORY"] = old_value

    def test_repl_declared_names_include_declared_symbols(self) -> None:
        from sprout.repl import declared_names

        names = declared_names(
            [
                "let answer = 42",
                "fn double(x: Int) -> Int = x + x",
                "type MaybeInt = | Some Int | None",
                "class Renderable t { fn render(x: t) -> Int }",
                "instance Renderable MaybeInt { fn render(x: MaybeInt) -> Int = 0 }",
            ]
        )

        self.assertIn("answer", names)
        self.assertIn("double", names)
        self.assertIn("MaybeInt", names)
        self.assertIn("Some", names)
        self.assertIn("None", names)
        self.assertIn("Renderable", names)
        self.assertIn("render", names)

    def test_repl_completion_matches_commands_and_prelude_names(self) -> None:
        from sprout.repl import completion_matches

        self.assertEqual(completion_matches(":t", ":t", [], []), [":t", ":type"])
        matches = completion_matches("sp", "sp", [], [])
        self.assertIn("split_ints", matches)

    def test_repl_completion_matches_declared_names(self) -> None:
        from sprout.repl import completion_matches

        matches = completion_matches(
            "ans",
            "ans",
            [],
            ["let answer = 42", "fn annotate(x: Int) -> Int = x"],
        )

        self.assertIn("answer", matches)
        self.assertNotIn("annotate", matches)

    def test_repl_completion_matches_stdlib_module_names(self) -> None:
        from sprout.repl import completion_matches

        matches = completion_matches("htt", "htt", [], [])

        self.assertIn("http", matches)
        self.assertIn("http_client", matches)

    def test_repl_completion_matches_imported_aliases_and_names(self) -> None:
        from sprout.repl import completion_matches

        alias_matches = completion_matches(
            "str",
            "str",
            ["import stdlib.string", "import stdlib.bytes (from_string)"],
            [],
        )
        name_matches = completion_matches(
            "fr",
            "fr",
            ["import stdlib.string", "import stdlib.bytes (from_string)"],
            [],
        )

        self.assertIn("string", alias_matches)
        self.assertIn("from_string", name_matches)

    def test_repl_readline_tab_binding_uses_libedit_form_when_needed(self) -> None:
        from sprout.repl import repl_readline_tab_binding

        class FakeReadline:
            __doc__ = "Importing this module enables command line editing using libedit readline."

        self.assertEqual(repl_readline_tab_binding(FakeReadline), "bind ^I rl_complete")

    def test_repl_readline_tab_binding_uses_gnu_readline_form_otherwise(self) -> None:
        from sprout.repl import repl_readline_tab_binding

        class FakeReadline:
            __doc__ = "GNU readline support"

        self.assertEqual(repl_readline_tab_binding(FakeReadline), "tab: complete")

    def test_run_with_http_stdlib_flag(self) -> None:
        src = """
        fn main() -> Unit !{IO} =
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
                "examples/foldable_demo.sprout",
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
                "examples/collections_demo.sprout",
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
                "examples/result_demo.sprout",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(run.returncode, 0, msg=run.stderr)
        self.assertEqual(run.stdout.strip(), "43\n43\n43\n43\nerror:too-small\n7")

    def test_compile_all_examples(self) -> None:
        example_flags = {
            "examples/result_demo.sprout": ["--with-stdlib"],
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


if __name__ == "__main__":
    unittest.main()
