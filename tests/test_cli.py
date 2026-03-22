from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
import sys
import unittest


class CliTests(unittest.TestCase):
    def test_analysis_service_check_source_returns_structured_success(self) -> None:
        run = subprocess.run(
            [sys.executable, "-m", "sprout.cli", "analysis-service"],
            check=False,
            capture_output=True,
            text=True,
            input=json.dumps(
                {
                    "op": "check_source",
                    "module_source": "module app.repl\n\nlet local = 41",
                }
            ),
        )
        self.assertEqual(run.returncode, 0, msg=run.stderr)
        self.assertEqual(run.stderr, "")
        self.assertEqual(json.loads(run.stdout), {"ok": True, "value": None})

    def test_analysis_service_type_of_in_source_returns_structured_success(self) -> None:
        run = subprocess.run(
            [sys.executable, "-m", "sprout.cli", "analysis-service"],
            check=False,
            capture_output=True,
            text=True,
            input=json.dumps(
                {
                    "op": "type_of_in_source",
                    "module_source": "module app.repl\n\nlet local = 41",
                    "expr": "local + 1",
                }
            ),
        )
        self.assertEqual(run.returncode, 0, msg=run.stderr)
        self.assertEqual(run.stderr, "")
        self.assertEqual(json.loads(run.stdout), {"ok": True, "value": "Int"})

    def test_analysis_service_reports_unknown_operation(self) -> None:
        run = subprocess.run(
            [sys.executable, "-m", "sprout.cli", "analysis-service"],
            check=False,
            capture_output=True,
            text=True,
            input=json.dumps({"op": "not-real"}),
        )
        self.assertEqual(run.returncode, 1, msg=run.stderr)
        self.assertEqual(run.stderr, "")
        self.assertEqual(
            json.loads(run.stdout),
            {"error": "unknown analysis service op `not-real`", "ok": False},
        )

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

    def test_stdlib_repl_frontend_avoids_legacy_host_hooks(self) -> None:
        source = Path("stdlib/repl.sprout").read_text(encoding="utf-8")

        self.assertNotIn("repl_add_import(", source)
        self.assertNotIn("repl_add_declaration(", source)
        self.assertNotIn("repl_eval_expr(", source)
        self.assertNotIn("repl_type_of(", source)
        self.assertNotIn("repl_instances(", source)
        self.assertNotIn("repl_complete(", source)

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
        from sprout.repl_host import ReplSession

        session = ReplSession()
        session.add_import("import stdlib.http")
        session.add_declaration("let answer = 41")

        self.assertEqual(session.imports, ["import stdlib.http"])
        self.assertEqual(session.declarations, ["let answer = 41"])
        self.assertEqual(session.infer_type("answer + 1"), "Int")
        self.assertEqual(session.infer_type("http.http_ok(\"x\")"), "String")
        self.assertEqual(session.eval_expression_lines("answer + 1"), ("42",))
        query_type, matches = session.instances_for_type("List Int")
        self.assertEqual(query_type, "List Int")
        self.assertIn("Functor List", matches)
        self.assertIn("http", session.completion_matches("htt", "htt"))
        self.assertIn("answer", session.completion_matches("ans", "ans"))

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

    def test_repl_declared_names_include_declared_symbols(self) -> None:
        from sprout.repl_host import ReplSession

        session = ReplSession(
            declarations=[
                "let answer = 42",
                "fn double(x: Int) -> Int = x + x",
                "type MaybeInt = | Some Int | None",
                "class Renderable t { fn render(x: t) -> Int }",
                "instance Renderable MaybeInt { fn render(x: MaybeInt) -> Int = 0 }",
            ]
        )
        names = set(session.completion_matches("", ""))

        self.assertIn("answer", names)
        self.assertIn("double", names)
        self.assertIn("MaybeInt", names)
        self.assertIn("Some", names)
        self.assertIn("None", names)
        self.assertIn("Renderable", names)
        self.assertIn("render", names)

    def test_repl_declared_names_in_source_reports_declared_symbols(self) -> None:
        from sprout.analysis import declared_names_in_source

        names = set(
            declared_names_in_source(
                "module app.repl\n\ntype AAA =\n  | AAA\n\ntype Maybe a =\n  | Just a\n  | Nothing\n\nlet local = 41"
            )
        )

        self.assertIn("AAA", names)
        self.assertIn("Maybe", names)
        self.assertIn("Just", names)
        self.assertIn("Nothing", names)
        self.assertIn("local", names)

    def test_repl_exported_names_in_source_reports_explicit_exports(self) -> None:
        from sprout.analysis import exported_names_in_source

        names = set(
            exported_names_in_source(
                "module app.lib\n\nexport type Box(..) =\n  | Wrap String\n\nexport fn unwrap(value: Box) -> String =\n  match value with\n  | Wrap raw -> raw\n\nfn hidden() -> Int = 1"
            )
        )

        self.assertIn("Box", names)
        self.assertIn("Wrap", names)
        self.assertIn("unwrap", names)
        self.assertNotIn("hidden", names)

    def test_repl_symbol_inventory_in_source_reports_declared_imported_and_exported_names(self) -> None:
        from sprout.analysis import symbol_inventory_in_source

        declared, imported, exported = symbol_inventory_in_source(
            "module app.lib\n\nimport stdlib.string\nimport stdlib.bytes (from_string)\n\nexport type Box(..) =\n  | Wrap String\n\nexport fn unwrap(value: Box) -> String =\n  match value with\n  | Wrap raw -> raw\n\nlet local = 1"
        )

        self.assertIn("Box", declared)
        self.assertIn("Wrap", declared)
        self.assertIn("unwrap", declared)
        self.assertIn("local", declared)
        self.assertIn("string", imported)
        self.assertIn("from_string", imported)
        self.assertIn("Box", exported)
        self.assertIn("Wrap", exported)
        self.assertIn("unwrap", exported)
        self.assertNotIn("local", exported)

    def test_analysis_symbol_locations_in_source_reports_top_level_locations(self) -> None:
        from sprout.analysis import symbol_locations_in_source

        locations = symbol_locations_in_source(
            "module app.lib\n\nlet alpha = 1\n\nfn beta(x: Int) -> Int = x\n\nclass Render a {\n  fn render(value: a) -> String\n}\n\ntype Box =\n  | Wrap String"
        )

        self.assertIn(("value", "alpha", 3, 1), locations)
        self.assertIn(("value", "beta", 5, 1), locations)
        self.assertIn(("class", "Render", 7, 1), locations)
        self.assertIn(("type", "Box", 11, 1), locations)
        self.assertIn(("constructor", "Wrap", 12, 5), locations)

    def test_analysis_symbol_metadata_in_source_reports_declared_and_imported_symbols(self) -> None:
        from sprout.analysis import symbol_metadata_in_source

        entries = symbol_metadata_in_source(
            "module app.lib\n\nimport stdlib.string\nimport stdlib.string (concat)\nimport stdlib.bytes (from_string)\n\nexport type Box(..) =\n  | Wrap String\n\nexport fn unwrap(value: Box) -> String =\n  match value with\n  | Wrap raw -> raw\n\nlet local = 1"
        )

        by_name = {entry.visible_name: entry for entry in entries}

        self.assertEqual(by_name["Box"].kind, "type")
        self.assertEqual(by_name["Box"].canonical_name, "app.lib.Box")
        self.assertEqual(by_name["Box"].location.line, 7)
        self.assertEqual(by_name["Box"].location.column, 1)
        self.assertTrue(by_name["Box"].exported)

        self.assertEqual(by_name["Wrap"].kind, "constructor")
        self.assertEqual(by_name["Wrap"].canonical_name, "app.lib.Wrap")
        self.assertEqual(by_name["Wrap"].location.line, 8)
        self.assertEqual(by_name["Wrap"].location.column, 5)
        self.assertTrue(by_name["Wrap"].exported)

        self.assertEqual(by_name["unwrap"].kind, "value")
        self.assertEqual(by_name["unwrap"].canonical_name, "app.lib.unwrap")
        self.assertEqual(by_name["unwrap"].location.line, 10)
        self.assertEqual(by_name["unwrap"].location.column, 1)
        self.assertTrue(by_name["unwrap"].exported)

        self.assertEqual(by_name["local"].kind, "value")
        self.assertEqual(by_name["local"].canonical_name, "app.lib.local")
        self.assertEqual(by_name["local"].location.line, 14)
        self.assertEqual(by_name["local"].location.column, 1)
        self.assertFalse(by_name["local"].exported)
        self.assertEqual(by_name["local"].definition_location, by_name["local"].location)

        self.assertEqual(by_name["string"].kind, "module_alias")
        self.assertEqual(by_name["string"].introduced_via, "namespace")
        self.assertEqual(by_name["string"].imported_from_module, "stdlib.string")
        self.assertIsNone(by_name["string"].definition_location)

        self.assertEqual(by_name["concat"].kind, "value")
        self.assertEqual(by_name["concat"].canonical_name, "stdlib.string.concat")
        self.assertEqual(by_name["concat"].introduced_via, "imported")
        self.assertEqual(by_name["concat"].imported_from_module, "stdlib.string")
        self.assertIsNotNone(by_name["concat"].definition_location)
        assert by_name["concat"].definition_location is not None
        self.assertEqual(by_name["concat"].definition_location.path.name, "string.sprout")
        self.assertEqual(by_name["concat"].definition_location.line, 7)
        self.assertEqual(by_name["concat"].definition_location.column, 1)

        self.assertEqual(by_name["from_string"].kind, "value")
        self.assertEqual(by_name["from_string"].introduced_via, "imported")
        self.assertEqual(by_name["from_string"].imported_from_module, "stdlib.bytes")
        self.assertIsNotNone(by_name["from_string"].definition_location)
        assert by_name["from_string"].definition_location is not None
        self.assertEqual(by_name["from_string"].definition_location.path.name, "bytes.sprout")
        self.assertEqual(by_name["from_string"].definition_location.line, 24)
        self.assertEqual(by_name["from_string"].definition_location.column, 1)

    def test_structured_diagnostics_in_source_reports_stage_and_location(self) -> None:
        from sprout.analysis import structured_diagnostics_in_source

        type_diagnostics = structured_diagnostics_in_source(
            "module app.repl\n\nlet broken = missing"
        )
        self.assertEqual(len(type_diagnostics), 1)
        type_diag = type_diagnostics[0]
        self.assertEqual(type_diag.severity, "error")
        self.assertEqual(type_diag.stage, "typecheck")
        self.assertEqual(type_diag.message, "Unknown variable missing")
        self.assertIsNotNone(type_diag.location)
        assert type_diag.location is not None
        self.assertEqual(type_diag.location.line, 3)
        self.assertEqual(type_diag.location.column, 14)

        parse_diagnostics = structured_diagnostics_in_source(
            "module app.repl\n\nlet broken ="
        )
        self.assertEqual(len(parse_diagnostics), 1)
        parse_diag = parse_diagnostics[0]
        self.assertEqual(parse_diag.severity, "error")
        self.assertEqual(parse_diag.stage, "parse")
        self.assertIsNotNone(parse_diag.location)

    def test_repl_completion_matches_commands_and_prelude_names(self) -> None:
        from sprout.repl_host import ReplSession

        session = ReplSession()
        self.assertEqual(session.completion_matches(":t", ":t"), [":t", ":type"])
        matches = session.completion_matches("sp", "sp")
        self.assertIn("split_ints", matches)

    def test_repl_completion_matches_declared_names(self) -> None:
        from sprout.repl_host import ReplSession

        session = ReplSession(declarations=["let answer = 42", "fn annotate(x: Int) -> Int = x"])
        matches = session.completion_matches("ans", "ans")

        self.assertIn("answer", matches)
        self.assertNotIn("annotate", matches)

    def test_repl_completion_matches_stdlib_module_names(self) -> None:
        from sprout.repl_host import ReplSession

        session = ReplSession()
        matches = session.completion_matches("htt", "htt")

        self.assertIn("http", matches)
        self.assertIn("http_client", matches)

    def test_repl_completion_matches_imported_aliases_and_names(self) -> None:
        from sprout.repl_host import ReplSession

        session = ReplSession(imports=["import stdlib.string", "import stdlib.bytes (from_string)"])
        alias_matches = session.completion_matches("str", "str")
        name_matches = session.completion_matches("fr", "fr")

        self.assertIn("string", alias_matches)
        self.assertIn("from_string", name_matches)

    def test_repl_completion_candidates_return_suffix_prefix(self) -> None:
        from sprout.repl_host import ReplSession

        session = ReplSession(declarations=["let answer = 42"])
        prefix, matches = session.completion_candidates(":t ans")

        self.assertEqual(prefix, "ans")
        self.assertIn("answer", matches)

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
