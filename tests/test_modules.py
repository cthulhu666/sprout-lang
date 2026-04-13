from __future__ import annotations

import io
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from sprout import parse, run_program, typecheck_program
from sprout.module_loader import ModuleLoadError, load_module_bundle, load_module_source, resolve_program_names
from sprout.typechecker import TypeCheckError
from sprout.typeclass_lowering import TypeclassLoweringError, lower_typeclasses


class ModuleLoaderTests(unittest.TestCase):
    def test_plain_single_file_unknown_name_reaches_type_error_without_resolver_crash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            main = Path(tmp) / "main.sprout"
            main.write_text(
                """
                fn demo() -> Int =
                  missing
                """,
                encoding="utf-8",
            )

            bundle = load_module_bundle(main)
            program = parse(bundle.source)
            resolve_program_names(program, bundle)
            with self.assertRaises(TypeCheckError) as ctx:
                typecheck_program(program)
            self.assertIn("Unknown variable missing", str(ctx.exception))

    def test_load_module_source_imports_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "math").mkdir(parents=True)
            (root / "math" / "util.sprout").write_text(
                """
                module math.util
                export fn double(x: Int) -> Int = x * 2
                """,
                encoding="utf-8",
            )
            main = root / "main.sprout"
            main.write_text(
                """
                module app.main
                import math.util

                fn main() -> Unit !{IO} =
                  print(util.double(21))
                """,
                encoding="utf-8",
            )

            bundle = load_module_bundle(main)
            program = parse(bundle.source)
            resolve_program_names(program, bundle)
            typecheck_program(program)
            out = io.StringIO()
            run_program(program, stdout=out)
            self.assertEqual(out.getvalue().strip(), "42")

    def test_module_resolution_handles_do_notation_bindings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / "main.sprout"
            main.write_text(
                """
                module app.main

                fn half_if_even(x: Int) -> Maybe Int =
                  if (x / 2) * 2 == x then Just(x / 2) else Nothing

                fn render(value: Maybe Int) -> String =
                  match value with
                  | Just n -> if n == 5 then "five" else "other"
                  | Nothing -> "none"

                fn main() -> Unit !{IO} =
                  print(
                    render(
                      do
                        n <- half_if_even(8)
                        Just(n + 1)
                    )
                  )
                """,
                encoding="utf-8",
            )

            bundle = load_module_bundle(main)
            program = parse(bundle.source)
            resolve_program_names(program, bundle)
            typecheck_program(program)
            out = io.StringIO()
            run_program(program, stdout=out)
            self.assertEqual(out.getvalue().strip(), "five")

    def test_module_resolution_do_binding_shadows_imported_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app_dir = root / "app"
            app_dir.mkdir()
            (app_dir / "helper.sprout").write_text(
                """
                module app.helper

                export fn a() -> Int =
                  99
                """,
                encoding="utf-8",
            )
            main = root / "main.sprout"
            main.write_text(
                """
                module app.main

                import app.helper (a)

                fn main() -> Unit !{IO} =
                  print(
                    do
                      a <- Just(1)
                      Just(a)
                  )
                """,
                encoding="utf-8",
            )

            bundle = load_module_bundle(main)
            program = parse(bundle.source)
            resolve_program_names(program, bundle)
            typecheck_program(program)
            out = io.StringIO()
            run_program(program, stdout=out)
            self.assertEqual(out.getvalue().strip(), "Just(1)")

    def test_module_rejects_effectful_top_level_let(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "lib.sprout").write_text(
                """
                module lib
                export let boot = print("boot")
                """,
                encoding="utf-8",
            )
            main = root / "main.sprout"
            main.write_text(
                """
                module app.main
                import lib

                fn main() -> Unit !{IO} =
                  print("ok")
                """,
                encoding="utf-8",
            )

            bundle = load_module_bundle(main)
            program = parse(bundle.source)
            resolve_program_names(program, bundle)
            with self.assertRaises(TypeCheckError) as ctx:
                typecheck_program(program)
            self.assertIn("Top-level let bindings must not perform effects", str(ctx.exception))

    def test_module_rejects_effect_polymorphic_qualified_main(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / "main.sprout"
            main.write_text(
                """
                module app.main

                fn main() -> Unit !{e} =
                  print("ok")
                """,
                encoding="utf-8",
            )

            bundle = load_module_bundle(main)
            program = parse(bundle.source)
            resolve_program_names(program, bundle)
            with self.assertRaises(TypeCheckError) as ctx:
                typecheck_program(program)
            self.assertIn("main must not be effect-polymorphic", str(ctx.exception))

    def test_module_resolution_warns_on_imported_deprecated_value_use(self) -> None:
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

            bundle = load_module_bundle(main)
            program = parse(bundle.source)
            warnings = resolve_program_names(program, bundle)
            self.assertEqual(len(warnings), 1)
            self.assertIn("'old' is deprecated: use fresh instead", warnings[0].message)
            self.assertEqual(warnings[0].path, main.resolve())

    def test_module_resolution_does_not_warn_for_same_module_annotated_use(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / "main.sprout"
            main.write_text(
                """
                module app.main
                #@unstable
                fn local_helper(x: Int) -> Int = x + 1

                fn main() -> Unit !{IO} =
                  print(local_helper(1))
                """,
                encoding="utf-8",
            )

            bundle = load_module_bundle(main)
            program = parse(bundle.source)
            warnings = resolve_program_names(program, bundle)
            self.assertEqual(warnings, [])

    def test_module_resolution_imports_stdlib_collections_vec_sort_by_without_warning(self) -> None:
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

            bundle = load_module_bundle(main)
            program = parse(bundle.source)
            warnings = resolve_program_names(program, bundle)
            self.assertEqual(warnings, [])

    def test_load_module_source_detects_cycle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.sprout").write_text(
                """
                module a
                import b
                fn a() -> Int = 1
                """,
                encoding="utf-8",
            )
            (root / "b.sprout").write_text(
                """
                module b
                import a
                fn b() -> Int = 2
                """,
                encoding="utf-8",
            )
            with self.assertRaises(ModuleLoadError):
                load_module_source(root / "a.sprout")

    def test_load_module_source_rejects_unknown_exposed_symbol(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "lib.sprout").write_text(
                """
                module lib
                export fn ok() -> Int = 1
                """,
                encoding="utf-8",
            )
            (root / "main.sprout").write_text(
                """
                module main
                import lib (missing)
                fn main() -> Unit !{IO} = print(0)
                """,
                encoding="utf-8",
            )
            with self.assertRaises(ModuleLoadError) as ctx:
                load_module_source(root / "main.sprout")
            self.assertIn("exported names", str(ctx.exception))
            self.assertIn("'ok'", str(ctx.exception))
            self.assertIn("main.sprout:3:17", str(ctx.exception))
            self.assertIn("3 |                 import lib (missing)", str(ctx.exception))
            self.assertIn("^", str(ctx.exception))

    def test_load_module_source_rejects_duplicate_implicit_namespace_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "foo").mkdir(parents=True)
            (root / "bar").mkdir(parents=True)
            (root / "foo" / "common.sprout").write_text(
                """
                module foo.common
                export fn value() -> Int = 1
                """,
                encoding="utf-8",
            )
            (root / "bar" / "common.sprout").write_text(
                """
                module bar.common
                export fn value() -> Int = 2
                """,
                encoding="utf-8",
            )
            (root / "main.sprout").write_text(
                """
                module main
                import foo.common
                import bar.common
                fn main() -> Unit !{IO} = print(0)
                """,
                encoding="utf-8",
            )
            with self.assertRaises(ModuleLoadError):
                load_module_source(root / "main.sprout")

    def test_load_module_source_rejects_local_selected_import_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "lib.sprout").write_text(
                """
                module lib
                export fn value() -> Int = 1
                """,
                encoding="utf-8",
            )
            (root / "main.sprout").write_text(
                """
                module main
                import lib (value)
                fn value() -> Int = 2
                fn main() -> Unit !{IO} = print(value())
                """,
                encoding="utf-8",
            )
            with self.assertRaises(ModuleLoadError):
                load_module_source(root / "main.sprout")

    def test_load_module_source_supports_alias_qualified_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "http.sprout").write_text(
                """
                module http
                export fn ok() -> String = "ok"
                """,
                encoding="utf-8",
            )
            main = root / "main.sprout"
            main.write_text(
                """
                module main
                import http as h

                fn main() -> Unit !{IO} =
                  print(h.ok())
                """,
                encoding="utf-8",
            )

            bundle = load_module_bundle(main)
            program = parse(bundle.source)
            resolve_program_names(program, bundle)
            typecheck_program(program)
            out = io.StringIO()
            run_program(program, stdout=out)
            self.assertEqual(out.getvalue().strip(), "ok")

    def test_load_module_source_supports_implicit_namespace_qualified_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "http.sprout").write_text(
                """
                module http
                export fn ok() -> String = "ok"
                """,
                encoding="utf-8",
            )
            main = root / "main.sprout"
            main.write_text(
                """
                module main
                import http

                fn main() -> Unit !{IO} =
                  print(http.ok())
                """,
                encoding="utf-8",
            )

            bundle = load_module_bundle(main)
            program = parse(bundle.source)
            resolve_program_names(program, bundle)
            typecheck_program(program)
            out = io.StringIO()
            run_program(program, stdout=out)
            self.assertEqual(out.getvalue().strip(), "ok")

    def test_import_stdlib_http_server_parse_and_render_helpers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / "main.sprout"
            main.write_text(
                """
                module main
                import stdlib.http_server (HttpRequest, HttpServerResponse, ok, parse, render, request_body, request_header, request_path, with_header)

                fn show(req: HttpRequest) -> String =
                  match request_header("Host", req) with
                  | Nothing -> request_path(req)
                  | Just host -> str_concat(request_path(req), str_concat(":", str_concat(host, str_concat(":", request_body(req)))))

                fn main() -> Unit !{IO} =
                  match parse("POST /hello HTTP/1.1\\r\\nHost: local\\r\\nContent-Length: 5\\r\\n\\r\\nhello") with
                  | Err _ -> print("parse-err")
                  | Ok req ->
                      match render(with_header("X-Test", "yes", ok(show(req)))) with
                      | Err _ -> print("render-err")
                      | Ok raw -> print(raw)
                """,
                encoding="utf-8",
            )

            bundle = load_module_bundle(main)
            program = parse(bundle.source)
            resolve_program_names(program, bundle)
            typecheck_program(program)
            out = io.StringIO()
            run_program(program, stdout=out)
            rendered = out.getvalue().strip()
            self.assertIn("HTTP/1.1 200 OK", rendered)
            self.assertIn("\r\nx-test: yes\r\n\r\n", rendered)
            self.assertTrue(rendered.endswith("/hello:local:hello"), msg=rendered)

    def test_load_module_source_rejects_duplicate_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.sprout").write_text(
                """
                module a
                fn x() -> Int = 1
                """,
                encoding="utf-8",
            )
            (root / "b.sprout").write_text(
                """
                module b
                fn y() -> Int = 2
                """,
                encoding="utf-8",
            )
            (root / "main.sprout").write_text(
                """
                module main
                import a as m
                import b as m
                fn main() -> Unit !{IO} = print(0)
                """,
                encoding="utf-8",
            )
            with self.assertRaises(ModuleLoadError) as ctx:
                load_module_source(root / "main.sprout")
            self.assertIn("Use an explicit `as ...` alias", str(ctx.exception))

    def test_resolver_requires_unqualified_import_or_parenthesized_import(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "lib.sprout").write_text(
                """
                module lib
                export fn answer() -> Int = 42
                """,
                encoding="utf-8",
            )
            main = root / "main.sprout"
            main.write_text(
                """
                module main
                import lib as l
                fn main() -> Unit !{IO} =
                  print(answer())
                """,
                encoding="utf-8",
            )
            bundle = load_module_bundle(main)
            program = parse(bundle.source)
            with self.assertRaises(ModuleLoadError) as ctx:
                resolve_program_names(program, bundle)
            self.assertIn("requires explicit import or qualification", str(ctx.exception))
            self.assertIn("Use `import module (answer)`", str(ctx.exception))
            self.assertIn("main.sprout:5:25", str(ctx.exception))
            self.assertIn("5 |                   print(answer())", str(ctx.exception))
            self.assertIn("^", str(ctx.exception))

    def test_resolver_reports_source_context_for_unknown_qualified_export(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "lib.sprout").write_text(
                """
                module lib
                export fn answer() -> Int = 42
                """,
                encoding="utf-8",
            )
            main = root / "main.sprout"
            main.write_text(
                """
                module main
                import lib
                fn main() -> Unit !{IO} =
                  print(lib.missing())
                """,
                encoding="utf-8",
            )
            bundle = load_module_bundle(main)
            program = parse(bundle.source)
            with self.assertRaises(ModuleLoadError) as ctx:
                resolve_program_names(program, bundle)
            self.assertIn("does not export value 'missing'", str(ctx.exception))
            self.assertIn("main.sprout:5:25", str(ctx.exception))
            self.assertIn("5 |                   print(lib.missing())", str(ctx.exception))

    def test_resolver_unknown_alias_lists_available_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "lib.sprout").write_text(
                """
                module lib
                export fn answer() -> Int = 42
                """,
                encoding="utf-8",
            )
            main = root / "main.sprout"
            main.write_text(
                """
                module main
                import lib as good
                fn main() -> Unit !{IO} =
                  print(bad.answer())
                """,
                encoding="utf-8",
            )
            bundle = load_module_bundle(main)
            program = parse(bundle.source)
            with self.assertRaises(ModuleLoadError) as ctx:
                resolve_program_names(program, bundle)
            self.assertIn("available aliases", str(ctx.exception))
            self.assertIn("'good'", str(ctx.exception))

    def test_import_sees_only_explicit_exports_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "lib.sprout").write_text(
                """
                module lib
                export fn public() -> Int = 1
                fn hidden() -> Int = 2
                """,
                encoding="utf-8",
            )
            main = root / "main.sprout"
            main.write_text(
                """
                module main
                import lib
                fn main() -> Unit !{IO} = print(hidden())
                """,
                encoding="utf-8",
            )
            bundle = load_module_bundle(main)
            program = parse(bundle.source)
            with self.assertRaises(ModuleLoadError):
                resolve_program_names(program, bundle)

    def test_exported_type_without_ctor_export_hides_constructor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "lib.sprout").write_text(
                """
                module lib
                export type Token =
                  | Token String

                export fn unwrap(value: Token) -> String =
                  match value with
                  | Token raw -> raw
                """,
                encoding="utf-8",
            )
            main = root / "main.sprout"
            main.write_text(
                """
                module main
                import lib (Token, unwrap)

                fn main() -> Unit !{IO} =
                  print(unwrap(Token("secret")))
                """,
                encoding="utf-8",
            )
            bundle = load_module_bundle(main)
            program = parse(bundle.source)
            with self.assertRaises(ModuleLoadError) as ctx:
                resolve_program_names(program, bundle)
            self.assertIn("Value 'Token' is not exported by any imported module", str(ctx.exception))

    def test_exported_type_with_all_constructors_keeps_constructor_visible(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "lib.sprout").write_text(
                """
                module lib
                export type Token(..) =
                  | Token String

                export fn unwrap(value: Token) -> String =
                  match value with
                  | Token raw -> raw
                """,
                encoding="utf-8",
            )
            main = root / "main.sprout"
            main.write_text(
                """
                module main
                import lib (Token, unwrap)

                fn main() -> Unit !{IO} =
                  print(unwrap(Token("secret")))
                """,
                encoding="utf-8",
            )
            bundle = load_module_bundle(main)
            program = parse(bundle.source)
            resolve_program_names(program, bundle)
            typecheck_program(program)
            out = io.StringIO()
            run_program(program, stdout=out)
            self.assertEqual(out.getvalue().strip(), "secret")

    def test_import_without_explicit_export_cannot_use_symbol(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "lib.sprout").write_text(
                """
                module lib
                fn value() -> Int = 7
                """,
                encoding="utf-8",
            )
            main = root / "main.sprout"
            main.write_text(
                """
                module main
                import lib
                fn main() -> Unit !{IO} = print(value())
                """,
                encoding="utf-8",
            )
            bundle = load_module_bundle(main)
            program = parse(bundle.source)
            with self.assertRaises(ModuleLoadError):
                resolve_program_names(program, bundle)

    def test_resolver_allows_local_shadowing_of_hidden_top_level_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "lib.sprout").write_text(
                """
                module lib
                export fn public(x: Int) -> Int = x + hidden()
                fn hidden() -> Int = 1
                """,
                encoding="utf-8",
            )
            main = root / "main.sprout"
            main.write_text(
                """
                module main
                import lib (public)
                fn use(hidden: Int) -> Int = hidden + public(1)
                fn main() -> Unit !{IO} = print(use(5))
                """,
                encoding="utf-8",
            )
            bundle = load_module_bundle(main)
            program = parse(bundle.source)
            resolve_program_names(program, bundle)
            typecheck_program(program)
            out = io.StringIO()
            run_program(program, stdout=out)
            self.assertEqual(out.getvalue().strip(), "7")

    def test_import_stdlib_http_client_wrappers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / "main.sprout"
            main.write_text(
                """
                module main
                import stdlib.http (Result, HttpError, HttpResponse)
                import stdlib.http_client (http_get)

                fn use_get(url: String) -> Result HttpError HttpResponse !{IO} =
                  http_get(url, "", 1000)

                fn main() -> Unit !{IO} = print("ok")
                """,
                encoding="utf-8",
            )
            bundle = load_module_bundle(main)
            program = parse(bundle.source)
            resolve_program_names(program, bundle)
            typecheck_program(program)
            out = io.StringIO()
            run_program(program, stdout=out)
            self.assertEqual(out.getvalue().strip(), "ok")

    def test_import_stdlib_json_helpers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / "main.sprout"
            main.write_text(
                """
                module main
                import stdlib.json as json

                fn payload() -> json.Json =
                  json.object_from_pairs([("count", 2)])

                fn has_count(value: json.Json) -> String =
                  match json.json_get_field(value, "count") with
                  | Just _ -> "ok"
                  | Nothing -> "missing"

                fn main() -> Unit !{IO} = print(has_count(payload()))
                """,
                encoding="utf-8",
            )
            bundle = load_module_bundle(main)
            program = parse(bundle.source)
            resolve_program_names(program, bundle)
            typecheck_program(program)
            lowered = lower_typeclasses(program)
            typecheck_program(lowered)
            out = io.StringIO()
            run_program(lowered, stdout=out)
            self.assertEqual(out.getvalue().strip(), "ok")

    def test_public_modules_do_not_get_raw_terminal_builtins_implicitly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / "main.sprout"
            main.write_text(
                """
                module main

                fn main() -> Unit !{IO} =
                  term_write("hello")
                """,
                encoding="utf-8",
            )
            bundle = load_module_bundle(main)
            program = parse(bundle.source)
            with self.assertRaises(ModuleLoadError) as ctx:
                resolve_program_names(program, bundle)
            self.assertIn("term_write", str(ctx.exception))

    def test_public_modules_do_not_get_raw_analysis_builtins_implicitly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / "main.sprout"
            main.write_text(
                """
                module main

                fn main() -> Unit !{IO} =
                  match analysis_check_source("module app") with
                  | Ok _ -> print("ok")
                  | Err message -> print(message)
                """,
                encoding="utf-8",
            )
            bundle = load_module_bundle(main)
            program = parse(bundle.source)
            with self.assertRaises(ModuleLoadError) as ctx:
                resolve_program_names(program, bundle)
            self.assertIn("analysis_check_source", str(ctx.exception))

    def test_public_modules_do_not_get_raw_repl_builtins_implicitly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / "main.sprout"
            main.write_text(
                """
                module main

                fn main() -> Unit !{IO} =
                  match repl_check_source("module app") with
                  | Ok _ -> print("ok")
                  | Err message -> print(message)
                """,
                encoding="utf-8",
            )
            bundle = load_module_bundle(main)
            program = parse(bundle.source)
            with self.assertRaises(ModuleLoadError) as ctx:
                resolve_program_names(program, bundle)
            self.assertIn("repl_check_source", str(ctx.exception))

    def test_import_stdlib_terminal_wrappers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / "main.sprout"
            main.write_text(
                """
                module main
                import stdlib.terminal as terminal

                fn main() -> Unit !{IO} =
                  terminal.write("ok")
                """,
                encoding="utf-8",
            )
            bundle = load_module_bundle(main)
            program = parse(bundle.source)
            resolve_program_names(program, bundle)
            typecheck_program(program)
            out = io.StringIO()
            run_program(program, stdout=out)
            self.assertEqual(out.getvalue().strip(), "ok")

    def test_import_stdlib_compiler_direct_source_wrappers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / "main.sprout"
            main.write_text(
                """
                module main
                import stdlib.compiler as compiler

                fn main() -> Unit !{IO} =
                  match compiler.check_source("module app\n\nlet answer = 41") with
                  | Ok _ -> print("ok")
                  | Err message -> print(message)
                """,
                encoding="utf-8",
            )
            bundle = load_module_bundle(main)
            program = parse(bundle.source)
            resolve_program_names(program, bundle)
            typecheck_program(program)
            out = io.StringIO()
            run_program(program, stdout=out)
            self.assertEqual(out.getvalue().strip(), "ok")

    def test_import_stdlib_compiler_source_cursor_helpers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / "main.sprout"
            main.write_text(
                """
                module main
                import stdlib.compiler.source as source
                import stdlib.string as string

                fn render_char(value: Maybe Char) -> String =
                  match value with
                  | Nothing -> "_"
                  | Just ch -> string.string_from_char(ch)

                fn render_pos(cursor: source.SourceCursor) -> String =
                  string.concat(
                    int_to_string(source.position_index(source.source_position(cursor))),
                    string.concat(
                      ":",
                      string.concat(
                        int_to_string(source.position_line(source.source_position(cursor))),
                        string.concat(":", int_to_string(source.position_column(source.source_position(cursor))))
                      )
                    )
                  )

                fn render_match_char(cursor: source.SourceCursor) -> String =
                  match source.match_char('a', cursor) with
                  | Nothing -> "no-match"
                  | Just next_cursor ->
                      string.concat(
                        render_char(source.peek_char(next_cursor)),
                        if source.at_end(next_cursor) then "|end" else "|more"
                      )

                fn render_match_string(cursor: source.SourceCursor) -> String =
                  match source.match_string("ab", cursor) with
                  | Nothing -> "no-match"
                  | Just next_cursor ->
                      if source.at_end(next_cursor) then "done" else "open"

                fn render_summary(cursor: source.SourceCursor) -> String =
                  string.concat(
                    render_char(source.peek_char(cursor)),
                    string.concat(
                      "|",
                      string.concat(
                        render_pos(cursor),
                        string.concat(
                          "|",
                          match source.match_char('a', cursor) with
                          | Nothing -> "missing-advance"
                          | Just advanced ->
                              string.concat(
                                render_char(source.peek_char(advanced)),
                                string.concat(
                                  "|",
                                  string.concat(
                                    render_pos(advanced),
                                    string.concat(
                                      "|",
                                      string.concat(
                                        render_match_char(cursor),
                                        string.concat("|", render_match_string(cursor))
                                      )
                                    )
                                  )
                                )
                              )
                        )
                      )
                    )
                  )

                fn main() -> Unit !{IO} =
                  print(render_summary(source.cursor_from_string("ab")))
                """,
                encoding="utf-8",
            )
            bundle = load_module_bundle(main)
            program = parse(bundle.source)
            resolve_program_names(program, bundle)
            typecheck_program(program)
            out = io.StringIO()
            run_program(program, stdout=out)
            self.assertEqual(out.getvalue().strip(), "a|0:1:1|b|1:1:2|b|more|done")

    def test_import_stdlib_compiler_token_types(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / "main.sprout"
            main.write_text(
                """
                module main
                import stdlib.compiler.source as source
                import stdlib.compiler.token as token
                import stdlib.string as string

                fn render_kind(value: token.TokenKind) -> String =
                  match value with
                  | token.TokenIdentKind -> "ident"
                  | token.TokenKeywordKind -> "keyword"
                  | token.TokenIntKind -> "int"
                  | token.TokenCharKind -> "char"
                  | token.TokenStringKind -> "string"
                  | token.TokenSymbolKind -> "symbol"
                  | token.TokenEofKind -> "eof"

                fn render_pos(pos: source.SourcePos) -> String =
                  string.concat(
                    int_to_string(source.position_index(pos)),
                    string.concat(
                      ":",
                      string.concat(
                        int_to_string(source.position_line(pos)),
                        string.concat(":", int_to_string(source.position_column(pos)))
                      )
                    )
                  )

                fn render_token(value: token.Token) -> String =
                  string.concat(
                    render_kind(token.token_kind(value)),
                    string.concat(
                      "|",
                      string.concat(
                        token.token_text(value),
                        string.concat("|", render_pos(token.token_position(value)))
                      )
                    )
                  )

                fn render_error(value: token.TokenizeError) -> String =
                  string.concat(
                    token.error_message(value),
                    string.concat("|", render_pos(token.error_position(value)))
                  )

                fn main() -> Unit !{IO} =
                  print(
                    string.concat(
                      render_token(token.Token(token.TokenKeywordKind, "fn", source.SourcePos(3, 2, 4))),
                      string.concat(
                        "|",
                        string.concat(
                          render_token(token.eof_token(source.SourcePos(5, 4, 6))),
                          string.concat(
                            "|",
                            string.concat(
                              if token.is_eof_token(token.eof_token(source.SourcePos(5, 4, 6))) then "eof" else "not-eof",
                              string.concat(
                                "|",
                                render_error(token.TokenizeError("bad char", source.SourcePos(8, 3, 2)))
                              )
                            )
                          )
                        )
                      )
                    )
                  )
                """,
                encoding="utf-8",
            )
            bundle = load_module_bundle(main)
            program = parse(bundle.source)
            resolve_program_names(program, bundle)
            typecheck_program(program)
            out = io.StringIO()
            run_program(program, stdout=out)
            self.assertEqual(out.getvalue().strip(), "keyword|fn|3:2:4|eof||5:4:6|eof|bad char|8:3:2")

    def test_import_stdlib_compiler_lexer_tokenize_bootstrap_subset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / "main.sprout"
            main.write_text(
                """
                module main
                import stdlib.collections (Vec, vec_append, vec_empty, vec_get, vec_length)
                import stdlib.compiler.lexer as lexer
                import stdlib.compiler.source as source
                import stdlib.compiler.token as token
                import stdlib.string as string

                fn render_kind(value: token.TokenKind) -> String =
                  match value with
                  | token.TokenIdentKind -> "ident"
                  | token.TokenKeywordKind -> "keyword"
                  | token.TokenIntKind -> "int"
                  | token.TokenCharKind -> "char"
                  | token.TokenStringKind -> "string"
                  | token.TokenSymbolKind -> "symbol"
                  | token.TokenEofKind -> "eof"

                fn render_pos(pos: source.SourcePos) -> String =
                  string.concat(
                    int_to_string(source.position_line(pos)),
                    string.concat(":", int_to_string(source.position_column(pos)))
                  )

                fn render_token(value: token.Token) -> String =
                  string.concat(
                    render_kind(token.token_kind(value)),
                    string.concat(
                      ":",
                      string.concat(
                        token.token_text(value),
                        string.concat("@", render_pos(token.token_position(value)))
                      )
                    )
                  )

                fn append_rendered(acc: String, current: String) -> String =
                  if acc == "" then current else string.concat(acc, string.concat(",", current))

                fn render_tokens(tokens: Vec token.Token, index: Int, total: Int, acc: String) -> String =
                  if index >= total then acc
                  else
                    match vec_get(index, tokens) with
                    | Nothing -> acc
                    | Just value -> render_tokens(tokens, index + 1, total, append_rendered(acc, render_token(value)))

                fn render_result(value: Result token.TokenizeError (Vec token.Token)) -> String =
                  match value with
                  | Ok tokens -> render_tokens(tokens, 0, vec_length(tokens), "")
                  | Err err ->
                      string.concat(
                        token.error_message(err),
                        string.concat("@", render_pos(token.error_position(err)))
                      )

                fn main() -> Unit !{IO} =
                  print(
                    string.concat(
                      render_result(lexer.tokenize("fn add(x) -> x # note")),
                      string.concat(
                        "|",
                        string.concat(
                          render_result(lexer.tokenize("let value = 42")),
                          string.concat("|", render_result(lexer.tokenize("@")))
                        )
                      )
                    )
                  )
                """,
                encoding="utf-8",
            )
            bundle = load_module_bundle(main)
            program = parse(bundle.source)
            resolve_program_names(program, bundle)
            typecheck_program(program)
            out = io.StringIO()
            run_program(program, stdout=out)
            self.assertEqual(
                out.getvalue().strip(),
                "keyword:fn@1:1,ident:add@1:4,symbol:(@1:7,ident:x@1:8,symbol:)@1:9,symbol:->@1:11,ident:x@1:14,eof:@1:22|"
                "keyword:let@1:1,ident:value@1:5,symbol:=@1:11,int:42@1:13,eof:@1:15|"
                "Unexpected character @@1:1",
            )

    def test_import_stdlib_compiler_lexer_tokenize_string_char_and_multi_char_symbols(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / "main.sprout"
            main.write_text(
                """
                module main
                import stdlib.collections (Vec, vec_get, vec_length)
                import stdlib.compiler.lexer as lexer
                import stdlib.compiler.source as source
                import stdlib.compiler.token as token
                import stdlib.string as string

                fn render_kind(value: token.TokenKind) -> String =
                  match value with
                  | token.TokenIdentKind -> "ident"
                  | token.TokenKeywordKind -> "keyword"
                  | token.TokenIntKind -> "int"
                  | token.TokenCharKind -> "char"
                  | token.TokenStringKind -> "string"
                  | token.TokenSymbolKind -> "symbol"
                  | token.TokenEofKind -> "eof"

                fn render_pos(pos: source.SourcePos) -> String =
                  string.concat(
                    int_to_string(source.position_line(pos)),
                    string.concat(":", int_to_string(source.position_column(pos)))
                  )

                fn render_token(value: token.Token) -> String =
                  string.concat(
                    render_kind(token.token_kind(value)),
                    string.concat(
                      ":",
                      string.concat(
                        token.token_text(value),
                        string.concat("@", render_pos(token.token_position(value)))
                      )
                    )
                  )

                fn append_rendered(acc: String, current: String) -> String =
                  if acc == "" then current else string.concat(acc, string.concat(",", current))

                fn render_tokens(tokens: Vec token.Token, index: Int, total: Int, acc: String) -> String =
                  if index >= total then acc
                  else
                    match vec_get(index, tokens) with
                    | Nothing -> acc
                    | Just value -> render_tokens(tokens, index + 1, total, append_rendered(acc, render_token(value)))

                fn render_result(value: Result token.TokenizeError (Vec token.Token)) -> String =
                  match value with
                  | Ok tokens -> render_tokens(tokens, 0, vec_length(tokens), "")
                  | Err err ->
                      string.concat(
                        token.error_message(err),
                        string.concat("@", render_pos(token.error_position(err)))
                      )

                fn main() -> Unit !{IO} =
                  print(
                    string.concat(
                      render_result(lexer.tokenize("\\"abc\\"")),
                      string.concat(
                        "|",
                        string.concat(
                          render_result(lexer.tokenize("'a'")),
                          string.concat(
                            "|",
                            string.concat(
                              render_result(lexer.tokenize("x <- y")),
                              string.concat(
                                "|",
                                string.concat(
                                  render_result(lexer.tokenize("a |> b")),
                                  string.concat(
                                    "|",
                                    string.concat(
                                      render_result(lexer.tokenize("a == b && c")),
                                      string.concat(
                                        "|",
                                        string.concat(
                                          render_result(lexer.tokenize("\\"hello")),
                                          string.concat(
                                            "|",
                                            string.concat(
                                              render_result(lexer.tokenize("'ab'")),
                                              string.concat("|", render_result(lexer.tokenize("'")))
                                            )
                                          )
                                        )
                                      )
                                    )
                                  )
                                )
                              )
                            )
                          )
                        )
                      )
                    )
                  )
                """,
                encoding="utf-8",
            )
            bundle = load_module_bundle(main)
            program = parse(bundle.source)
            resolve_program_names(program, bundle)
            typecheck_program(program)
            out = io.StringIO()
            run_program(program, stdout=out)
            self.assertEqual(
                out.getvalue().strip(),
                "string:abc@1:1,eof:@1:6"
                "|char:a@1:1,eof:@1:4"
                "|ident:x@1:1,symbol:<-@1:3,ident:y@1:6,eof:@1:7"
                "|ident:a@1:1,symbol:|>@1:3,ident:b@1:6,eof:@1:7"
                "|ident:a@1:1,symbol:==@1:3,ident:b@1:6,symbol:&&@1:8,ident:c@1:11,eof:@1:12"
                "|Unterminated string literal@1:1"
                "|Char literal must contain exactly one character@1:1"
                "|Unterminated char literal@1:1",
            )

    def test_import_stdlib_compiler_ast_constructors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / "main.sprout"
            main.write_text(
                """
                module main
                import stdlib.compiler.ast as ast
                import stdlib.compiler.source as source

                fn dummy_pos() -> source.SourcePos =
                  source.SourcePos(1, 1, 0)

                fn describe_expr(e: ast.Expr) -> String =
                  match e with
                  | ast.VarExpr name _ -> name
                  | ast.IntExpr n _ -> "int"
                  | ast.BoolExpr _ _ -> "bool"
                  | ast.StringExpr _ _ -> "str"
                  | ast.CharExpr _ _ -> "char"
                  | ast.IfExpr _ _ _ _ -> "if"
                  | ast.CallExpr _ _ _ -> "call"
                  | ast.MatchExpr _ _ _ -> "match"
                  | ast.LambdaExpr _ _ _ -> "lambda"
                  | ast.TupleExpr _ _ -> "tuple"
                  | ast.UnitExpr _ -> "unit"
                  | ast.BinaryExpr _ _ _ _ -> "binary"
                  | ast.UnaryExpr _ _ _ -> "unary"
                  | ast.IntRangeExpr _ _ _ -> "range"
                  | ast.DoExpr _ _ -> "do"
                  | ast.RecordExpr _ _ _ -> "record"
                  | ast.GetFieldExpr _ _ _ -> "getfield"

                fn describe_pattern(p: ast.Pattern) -> String =
                  match p with
                  | ast.WildcardPattern _ -> "_"
                  | ast.VarPattern name _ -> name
                  | ast.IntPattern _ _ -> "int"
                  | ast.BoolPattern _ _ -> "bool"
                  | ast.StringPattern _ _ -> "str"
                  | ast.CharPattern _ _ -> "char"
                  | ast.UnitPattern _ -> "unit"
                  | ast.TuplePattern _ _ -> "tuple"
                  | ast.ConstructorPattern name _ _ -> name

                fn main() -> Unit !{IO} =
                  print(
                    str_concat(describe_expr(e1),
                    str_concat(",",
                    str_concat(describe_expr(e2),
                    str_concat(",",
                    str_concat(describe_expr(e3),
                    str_concat(",",
                    str_concat(describe_pattern(p1),
                    str_concat(",",
                    str_concat(describe_pattern(p2),
                    str_concat(",",
                    str_concat(describe_pattern(p3),
                    str_concat(",",
                    describe_expr(call)
                    ))))))))))))
                  )
                where
                  pos = dummy_pos()
                  e1 = ast.VarExpr("x", pos)
                  e2 = ast.IntExpr(42, pos)
                  e3 = ast.BoolExpr(true, pos)
                  p1 = ast.WildcardPattern(pos)
                  p2 = ast.VarPattern("y", pos)
                  p3 = ast.ConstructorPattern("Just", [p2], pos)
                  call = ast.CallExpr(e1, [e2, e3], pos)
                """,
                encoding="utf-8",
            )
            bundle = load_module_bundle(main)
            program = parse(bundle.source)
            resolve_program_names(program, bundle)
            typecheck_program(program)
            out = io.StringIO()
            run_program(program, stdout=out)
            self.assertEqual(out.getvalue().strip(), "x,int,bool,_,y,Just,call")

    def test_import_stdlib_dict_entries_and_json_object_from_dict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / "main.sprout"
            main.write_text(
                """
                module main
                import stdlib.collections (Dict, dict_empty, dict_entries, dict_set, vec_get_or)
                import stdlib.json as json
                import stdlib.string as string

                fn payload() -> Dict Int =
                  dict_set("count", 2, dict_set("title", 5, dict_empty()))

                fn entry_metric(entry: (String, Int)) -> Int =
                  match entry with
                  | (key, value) -> string.length(key) + value

                fn main() -> Unit !{IO} =
                  do
                    print(json.stringify(json.object_from_dict(payload())))
                    print(
                      entry_metric(vec_get_or(0, ("missing", -1), dict_entries(payload())))
                      + entry_metric(vec_get_or(1, ("missing", -1), dict_entries(payload())))
                    )
                """,
                encoding="utf-8",
            )
            bundle = load_module_bundle(main)
            program = parse(bundle.source)
            resolve_program_names(program, bundle)
            typecheck_program(program)
            lowered = lower_typeclasses(program)
            typecheck_program(lowered)
            out = io.StringIO()
            run_program(lowered, stdout=out)
            self.assertEqual(out.getvalue().strip(), '{"count":2,"title":5}\n17')

    def test_qualified_module_value_resolution_inside_tuple_expr(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / "main.sprout"
            main.write_text(
                """
                module main
                import stdlib.json as json

                fn payload() -> json.Json =
                  json.object_from_pairs(
                    [
                      ("title", json.string("hello")),
                      ("count", json.int(2))
                    ]
                  )

                fn main() -> Unit !{IO} =
                  print(json.stringify(payload()))
                """,
                encoding="utf-8",
            )
            bundle = load_module_bundle(main)
            program = parse(bundle.source)
            resolve_program_names(program, bundle)
            typecheck_program(program)
            lowered = lower_typeclasses(program)
            typecheck_program(lowered)
            out = io.StringIO()
            run_program(lowered, stdout=out)
            self.assertEqual(out.getvalue().strip(), '{"title":"hello","count":2}')

    def test_import_stdlib_net_helpers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / "main.sprout"
            main.write_text(
                """
                module main
                import stdlib.net (TcpConnection, TcpError, TcpListener, tcp_error_message)

                fn main() -> Unit !{IO} =
                  print(tcp_error_message(TcpConnectFailed("refused")))
                """,
                encoding="utf-8",
            )
            bundle = load_module_bundle(main)
            program = parse(bundle.source)
            resolve_program_names(program, bundle)
            typecheck_program(program)
            out = io.StringIO()
            run_program(program, stdout=out)
            self.assertEqual(out.getvalue().strip(), "refused")

    def test_import_stdlib_net_handles_are_opaque(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / "main.sprout"
            main.write_text(
                """
                module main
                import stdlib.net (TcpConnection)

                fn main() -> Unit !{IO} =
                  print(TcpConnection(1))
                """,
                encoding="utf-8",
            )
            bundle = load_module_bundle(main)
            program = parse(bundle.source)
            with self.assertRaises(ModuleLoadError) as ctx:
                resolve_program_names(program, bundle)
            self.assertIn("Value 'TcpConnection' is not exported by any imported module", str(ctx.exception))

    def test_import_stdlib_bytes_helpers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / "main.sprout"
            main.write_text(
                """
                module main
                import stdlib.collections (Maybe)
                import stdlib.bytes (Result, Utf8Error, c_string, from_string, length, read_c_string, read_u16_be, slice, to_string, u16_be)

                fn unwrap_or_zero(value: Maybe Int) -> Int =
                  match value with
                  | Just n -> n
                  | Nothing -> 0

                fn utf8_score(value: Result Utf8Error String, expected: String, score: Int) -> Int =
                  match value with
                  | Ok text -> if text == expected then score else 0
                  | Err _ -> 0

                fn main() -> Unit !{IO} =
                  print(
                    unwrap_or_zero(read_u16_be(slice(u16_be(513), 0, length(u16_be(513)))))
                    + utf8_score(to_string(from_string("zaż")), "zaż", 3)
                    + utf8_score(read_c_string(c_string("ok")), "ok", 2)
                  )
                """,
                encoding="utf-8",
            )
            bundle = load_module_bundle(main)
            program = parse(bundle.source)
            resolve_program_names(program, bundle)
            typecheck_program(program)
            out = io.StringIO()
            run_program(program, stdout=out)
            self.assertEqual(out.getvalue().strip(), "518")

    def test_import_stdlib_bytes_builder_helpers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / "main.sprout"
            main.write_text(
                """
                module main
                import stdlib.bytes (Builder, Result, Utf8Error, builder_append, builder_build, builder_byte, builder_bytes, builder_empty, builder_u16_be, builder_u32_be, from_string, length, to_string)

                fn sample() -> Builder =
                  builder_append(
                    builder_append(builder_empty(), builder_byte(65)),
                    builder_append(
                      builder_u16_be(16963),
                      builder_append(builder_u32_be(1145390663), builder_bytes(from_string("H")))
                    )
                  )

                fn score(text: String) -> Int =
                  match text with
                  | "ABCDEFGH" -> 1
                  | _ -> 0

                fn main() -> Unit !{IO} =
                  match to_string(builder_build(sample())) with
                  | Ok text -> print(text)
                  | Err _ -> print("bad")
                """,
                encoding="utf-8",
            )
            bundle = load_module_bundle(main)
            program = parse(bundle.source)
            resolve_program_names(program, bundle)
            typecheck_program(program)
            out = io.StringIO()
            run_program(program, stdout=out)
            self.assertEqual(out.getvalue().strip(), "ABCDEFGH")

    def test_import_stdlib_crypto_helpers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / "main.sprout"
            main.write_text(
                """
                module main
                import stdlib.bytes (from_string)
                import stdlib.crypto as crypto

                fn main() -> Unit !{IO} =
                  print(
                    crypto.base64_encode(crypto.sha256(from_string("abc")))
                  )
                """,
                encoding="utf-8",
            )
            bundle = load_module_bundle(main)
            program = parse(bundle.source)
            resolve_program_names(program, bundle)
            typecheck_program(program)
            out = io.StringIO()
            run_program(program, stdout=out)
            self.assertEqual(out.getvalue().strip(), "ungWv48Bz+pBQUDeXa4iI7ADYaOWF3qctBD/YfIAFa0=")

    def test_import_stdlib_scram_helpers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / "main.sprout"
            main.write_text(
                """
                module main
                import stdlib.scram as scram

                fn main() -> Unit !{IO} =
                  print(
                    scram.client_first_message("user", "nonce")
                  )
                """,
                encoding="utf-8",
            )
            bundle = load_module_bundle(main)
            program = parse(bundle.source)
            resolve_program_names(program, bundle)
            typecheck_program(program)
            out = io.StringIO()
            run_program(program, stdout=out)
            self.assertEqual(out.getvalue().strip(), "n,,n=user,r=nonce")

    def test_import_stdlib_scram_escapes_reserved_username_chars(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / "main.sprout"
            main.write_text(
                """
                module main
                import stdlib.scram as scram

                fn main() -> Unit !{IO} =
                  print(
                    scram.client_first_message("a,b=c", "nonce")
                  )
                """,
                encoding="utf-8",
            )
            bundle = load_module_bundle(main)
            program = parse(bundle.source)
            resolve_program_names(program, bundle)
            typecheck_program(program)
            out = io.StringIO()
            run_program(program, stdout=out)
            self.assertEqual(out.getvalue().strip(), "n,,n=a=2Cb=3Dc,r=nonce")

    def test_import_stdlib_math_helpers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / "main.sprout"
            main.write_text(
                """
                module main
                import stdlib.collections (Maybe)
                import stdlib.math (gcd, lcm, mod, pow)

                fn unwrap_or(value: Maybe Int, fallback: Int) -> Int =
                  match value with
                  | Just n -> n
                  | Nothing -> fallback

                fn main() -> Unit !{IO} =
                  print(
                    gcd(54, 24)
                    + lcm(6, 8)
                    + unwrap_or(mod(-17, 5), -100)
                    + unwrap_or(pow(2, 5), -100)
                    + unwrap_or(mod(9, 0), 1)
                    + unwrap_or(pow(2, -1), 1)
                  )
                """,
                encoding="utf-8",
            )
            bundle = load_module_bundle(main)
            program = parse(bundle.source)
            resolve_program_names(program, bundle)
            typecheck_program(program)
            out = io.StringIO()
            run_program(program, stdout=out)
            self.assertEqual(out.getvalue().strip(), "67")

    def test_import_examples_sentry_api_module(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / "main.sprout"
            main.write_text(
                """
                module main
                import examples.sentry_api (sentry_auth_header)

                fn main() -> Unit !{IO} =
                  print(sentry_auth_header("abc"))
                """,
                encoding="utf-8",
            )
            bundle = load_module_bundle(main)
            program = parse(bundle.source)
            resolve_program_names(program, bundle)
            typecheck_program(program)
            out = io.StringIO()
            run_program(program, stdout=out)
            self.assertEqual(out.getvalue().strip(), "Authorization: Bearer abc")

    def test_import_examples_sentry_tui_scaffold_module(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / "main.sprout"
            main.write_text(
                """
                module main
                import examples.sentry_issue_browser_tui (run_once)

                fn main() -> Unit !{IO} =
                  print("ok")
                """,
                encoding="utf-8",
            )
            bundle = load_module_bundle(main)
            program = parse(bundle.source)
            resolve_program_names(program, bundle)
            typecheck_program(program)
            out = io.StringIO()
            run_program(program, stdout=out)
            self.assertEqual(out.getvalue().strip(), "ok")

    def test_examples_sentry_api_decodes_issue_summaries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / "main.sprout"
            main.write_text(
                """
                module main
                import examples.sentry_api (issue_short_id, issue_title, sentry_decode_issue_summaries)
                import stdlib.json (parse)
                import stdlib.string as string

                fn main() -> Unit !{IO} =
                  match parse("[{\\"shortId\\":\\"APP-17\\",\\"title\\":\\"Broken checkout\\",\\"status\\":\\"unresolved\\"}]") with
                  | Err _ -> print("decode-error")
                  | Ok payload ->
                      match vec_get(0, sentry_decode_issue_summaries(payload)) with
                      | Nothing -> print("missing")
                      | Just issue -> print(string.concat(issue_short_id(issue), string.concat(":", issue_title(issue))))
                """,
                encoding="utf-8",
            )
            bundle = load_module_bundle(main)
            program = parse(bundle.source)
            resolve_program_names(program, bundle)
            typecheck_program(program)
            out = io.StringIO()
            run_program(program, stdout=out)
            self.assertEqual(out.getvalue().strip(), "APP-17:Broken checkout")

    def test_examples_sentry_api_decodes_issue_detail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / "main.sprout"
            main.write_text(
                """
                module main
                import examples.sentry_api (issue_detail_culprit, issue_detail_permalink, issue_detail_short_id, issue_detail_status, sentry_decode_issue_detail)
                import stdlib.json (parse)
                import stdlib.string as strlib

                fn main() -> Unit !{IO} =
                  match parse("{\\"id\\":\\"123\\",\\"shortId\\":\\"APP-17\\",\\"title\\":\\"Broken checkout\\",\\"status\\":\\"unresolved\\",\\"level\\":\\"error\\",\\"culprit\\":\\"checkout.submit\\",\\"permalink\\":\\"https://sentry.io/issues/123/\\"}") with
                  | Err _ -> print("decode-error")
                  | Ok payload ->
                      match sentry_decode_issue_detail(payload) with
                      | detail ->
                          print(
                            strlib.concat(
                              strlib.concat(
                                strlib.concat(issue_detail_short_id(detail), "|"),
                                strlib.concat(issue_detail_status(detail), "|")
                              ),
                              strlib.concat(
                                strlib.concat(issue_detail_culprit(detail), "|"),
                                issue_detail_permalink(detail)
                              )
                            )
                          )
                """,
                encoding="utf-8",
            )
            bundle = load_module_bundle(main)
            program = parse(bundle.source)
            resolve_program_names(program, bundle)
            typecheck_program(program)
            out = io.StringIO()
            run_program(program, stdout=out)
            self.assertEqual(
                out.getvalue().strip(),
                "APP-17|unresolved|checkout.submit|https://sentry.io/issues/123/",
            )

    def test_examples_sentry_tui_entrypoint_falls_back_to_non_interactive_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / "main.sprout"
            main.write_text(
                """
                module main
                import examples.sentry_issue_browser_tui (run_entrypoint)

                fn main() -> Unit !{IO} =
                  run_entrypoint()
                """,
                encoding="utf-8",
            )
            bundle = load_module_bundle(main)
            program = parse(bundle.source)
            resolve_program_names(program, bundle)
            typecheck_program(program)
            out = io.StringIO()
            with patch.dict("os.environ", {}, clear=True):
                run_program(program, stdout=out)
            self.assertEqual(
                out.getvalue().strip(),
                "Configuration error: missing environment variable: SENTRY_ORG",
            )

    def test_examples_sentry_tui_renders_issue_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / "main.sprout"
            main.write_text(
                """
                module main
                import examples.sentry_api (sentry_decode_issue_summaries)
                import examples.sentry_issue_browser_tui (render_issue_list)
                import stdlib.json (parse)

                fn main() -> Unit !{IO} =
                  match parse("[{\\"shortId\\":\\"APP-17\\",\\"title\\":\\"Broken checkout\\",\\"status\\":\\"unresolved\\"},{\\"shortId\\":\\"APP-18\\",\\"title\\":\\"Slow query\\",\\"status\\":\\"resolved\\"}]") with
                  | Err _ -> print("decode-error")
                  | Ok payload -> print(render_issue_list(sentry_decode_issue_summaries(payload)))
                """,
                encoding="utf-8",
            )
            bundle = load_module_bundle(main)
            program = parse(bundle.source)
            resolve_program_names(program, bundle)
            typecheck_program(program)
            out = io.StringIO()
            run_program(program, stdout=out)
            self.assertEqual(
                out.getvalue().strip(),
                "Sentry issues\n- [unresolved] APP-17 - Broken checkout\n- [resolved] APP-18 - Slow query",
            )

    def test_examples_sentry_tui_load_config_reports_missing_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / "main.sprout"
            main.write_text(
                """
                module main
                import examples.sentry_issue_browser_tui (run_from_env)

                fn main() -> Unit !{IO} =
                  print(run_from_env())
                """,
                encoding="utf-8",
            )
            bundle = load_module_bundle(main)
            program = parse(bundle.source)
            resolve_program_names(program, bundle)
            typecheck_program(program)
            out = io.StringIO()
            with patch.dict("os.environ", {}, clear=True):
                run_program(program, stdout=out)
            self.assertEqual(
                out.getvalue().strip(),
                "Configuration error: missing environment variable: SENTRY_ORG",
            )

    def test_import_examples_aoc_2025_day_3_module(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / "main.sprout"
            main.write_text(
                """
                module main
                import examples.aoc_2025_day_3 (solve_stdin)

                fn main() -> Unit !{IO} =
                  print(solve_stdin())
                """,
                encoding="utf-8",
            )
            bundle = load_module_bundle(main)
            program = parse(bundle.source)
            resolve_program_names(program, bundle)
            typecheck_program(program)
            out = io.StringIO()
            with patch("sys.stdin", io.StringIO("987654321111111\n811111111111119\n234234234234278\n818181911112111\n")):
                run_program(program, stdout=out)
            self.assertEqual(out.getvalue().strip(), "Answers(357, 3121910778619)")

    def test_import_examples_aoc_2025_day_4_module(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / "main.sprout"
            main.write_text(
                """
                module main
                import examples.aoc_2025_day_4 (solve_stdin)

                fn main() -> Unit !{IO} =
                  print(solve_stdin())
                """,
                encoding="utf-8",
            )
            bundle = load_module_bundle(main)
            program = parse(bundle.source)
            resolve_program_names(program, bundle)
            typecheck_program(program)
            out = io.StringIO()
            with patch(
                "sys.stdin",
                io.StringIO(
                    "..@@.@@@@.\n@@@.@.@.@@\n@@@@@.@.@@\n@.@@@@..@.\n@@.@@@@.@@\n.@@@@@@@.@\n.@.@.@.@@@\n@.@@@.@@@@\n.@@@@@@@@.\n@.@.@@@.@.\n"
                ),
            ):
                run_program(program, stdout=out)
            self.assertEqual(out.getvalue().strip(), "Answers(13, 43)")

    def test_import_examples_aoc_2025_day_5_module(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / "main.sprout"
            main.write_text(
                """
                module main
                import examples.aoc_2025_day_5 (solve_stdin)

                fn main() -> Unit !{IO} =
                  print(solve_stdin())
                """,
                encoding="utf-8",
            )
            bundle = load_module_bundle(main)
            program = parse(bundle.source)
            resolve_program_names(program, bundle)
            typecheck_program(program)
            lowered = lower_typeclasses(program)
            typecheck_program(lowered)
            out = io.StringIO()
            with patch(
                "sys.stdin",
                io.StringIO("3-5\n10-14\n16-20\n12-18\n\n1\n5\n8\n11\n17\n32\n"),
            ):
                run_program(lowered, stdout=out)
            self.assertEqual(out.getvalue().strip(), "Answers(3, 14)")

    def test_import_stdlib_collections_vec_and_dict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / "main.sprout"
            main.write_text(
                """
                module main
                import stdlib.collections (Vec, Dict, Maybe, vec_append, vec_empty, vec_get, vec_length, dict_empty, dict_get, dict_set)

                fn third_or_zero(v: Vec Int) -> Int =
                  match vec_get(2, v) with
                  | Just x -> x
                  | Nothing -> 0

                fn read_or_missing(d: Dict Int, key: String) -> Int =
                  match dict_get(key, d) with
                  | Just x -> x
                  | Nothing -> -1

                fn main() -> Unit !{IO} =
                  print(
                    read_or_missing(
                      dict_set("b", third_or_zero(vec_append(30, vec_append(20, vec_append(10, vec_empty())))), dict_set("a", 1, dict_empty())),
                      "b"
                    )
                  )
                """,
                encoding="utf-8",
            )
            bundle = load_module_bundle(main)
            program = parse(bundle.source)
            resolve_program_names(program, bundle)
            typecheck_program(program)
            out = io.StringIO()
            run_program(program, stdout=out)
            self.assertEqual(out.getvalue().strip(), "30")

    def test_import_stdlib_collections_dict_literal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / "main.sprout"
            main.write_text(
                """
                module main
                import stdlib.collections (Dict, Maybe, dict_empty, dict_get, dict_set)

                fn read_or_missing(d: Dict Int, key: String) -> Int =
                  match dict_get(key, d) with
                  | Just x -> x
                  | Nothing -> -1

                fn main() -> Unit !{IO} =
                  print(read_or_missing({foo: 10, "bar": 20}, "bar"))
                """,
                encoding="utf-8",
            )
            bundle = load_module_bundle(main)
            program = parse(bundle.source)
            resolve_program_names(program, bundle)
            typecheck_program(program)
            out = io.StringIO()
            run_program(program, stdout=out)
            self.assertEqual(out.getvalue().strip(), "20")

    def test_import_stdlib_collections_vec_slice_and_reverse(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / "main.sprout"
            main.write_text(
                """
                module main
                import stdlib.collections (Vec, Maybe, vec_append, vec_empty, vec_get, vec_reverse, vec_slice)

                fn value_or(v: Maybe Int, fallback: Int) -> Int =
                  match v with
                  | Just x -> x
                  | Nothing -> fallback

                fn sample() -> Vec Int =
                  vec_append(40, vec_append(30, vec_append(20, vec_append(10, vec_empty()))))

                fn main() -> Unit !{IO} =
                  print(
                    value_or(
                      vec_get(0, vec_reverse(vec_slice(1, 2, sample()))),
                      -1
                    )
                  )
                """,
                encoding="utf-8",
            )
            bundle = load_module_bundle(main)
            program = parse(bundle.source)
            resolve_program_names(program, bundle)
            typecheck_program(program)
            out = io.StringIO()
            run_program(program, stdout=out)
            self.assertEqual(out.getvalue().strip(), "30")

    def test_import_stdlib_collections_dict_keys_and_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / "main.sprout"
            main.write_text(
                """
                module main
                import stdlib.collections (Dict, dict_empty, dict_keys, dict_set, dict_values, vec_get_or)
                import stdlib.string as string

                fn sample() -> Dict Int =
                  dict_set("beta", 11, dict_set("alpha", 7, dict_empty()))

                fn main() -> Unit !{IO} =
                  print(
                    vec_get_or(1, -100, dict_values(sample()))
                    + string.length(vec_get_or(0, "", dict_keys(sample())))
                  )
                """,
                encoding="utf-8",
            )
            bundle = load_module_bundle(main)
            program = parse(bundle.source)
            resolve_program_names(program, bundle)
            typecheck_program(program)
            out = io.StringIO()
            run_program(program, stdout=out)
            self.assertEqual(out.getvalue().strip(), "16")

    def test_import_stdlib_collections_dict_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / "main.sprout"
            main.write_text(
                """
                module main
                import stdlib.collections (Dict, dict_empty, dict_entries, dict_set, vec_get_or)
                import stdlib.string as string

                fn sample() -> Dict Int =
                  dict_set("beta", 11, dict_set("alpha", 7, dict_empty()))

                fn entry_metric(entry: (String, Int)) -> Int =
                  match entry with
                  | (key, value) -> string.length(key) + value

                fn main() -> Unit !{IO} =
                  print(
                    entry_metric(vec_get_or(0, ("missing", -1), dict_entries(sample())))
                    + entry_metric(vec_get_or(1, ("missing", -1), dict_entries(sample())))
                  )
                """,
                encoding="utf-8",
            )
            bundle = load_module_bundle(main)
            program = parse(bundle.source)
            resolve_program_names(program, bundle)
            typecheck_program(program)
            out = io.StringIO()
            run_program(program, stdout=out)
            self.assertEqual(out.getvalue().strip(), '27')

    def test_import_stdlib_collections_vec_sum_helpers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / "main.sprout"
            main.write_text(
                """
                module main
                import stdlib.collections (Vec, vec_append, vec_empty, vec_sum, vec_sum_by)

                fn sample() -> Vec Int =
                  vec_append(30, vec_append(20, vec_append(10, vec_empty())))

                fn tens(value: Int) -> Int = value / 10

                fn main() -> Unit !{IO} =
                  print(vec_sum(sample()) + vec_sum_by(tens, sample()))
                """,
                encoding="utf-8",
            )
            bundle = load_module_bundle(main)
            program = parse(bundle.source)
            resolve_program_names(program, bundle)
            typecheck_program(program)
            out = io.StringIO()
            run_program(program, stdout=out)
            self.assertEqual(out.getvalue().strip(), "66")

    def test_import_stdlib_collections_vec_sort_by(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / "main.sprout"
            main.write_text(
                """
                module main
                import stdlib.collections (Vec, vec_append, vec_empty, vec_sort_by)

                fn key(value: IntRange) -> Int =
                  range_start(value)

                fn sample() -> Vec IntRange =
                  vec_append(range(3, 4), vec_append(range(1, 2), vec_append(range(1, 3), vec_empty())))

                fn main() -> Unit !{IO} =
                  print(vec_sort_by(key, sample()))
                """,
                encoding="utf-8",
            )
            bundle = load_module_bundle(main)
            program = parse(bundle.source)
            resolve_program_names(program, bundle)
            typecheck_program(program)
            lowered = lower_typeclasses(program)
            typecheck_program(lowered)
            out = io.StringIO()
            run_program(lowered, stdout=out)
            self.assertEqual(out.getvalue().strip(), "Vec([1..3, 1..2, 3..4])")

    def test_import_stdlib_collections_vec_sort_strings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / "main.sprout"
            main.write_text(
                """
                module main
                import stdlib.collections (Vec, vec_append, vec_empty, vec_sort)

                fn sample() -> Vec String =
                  vec_append("beta", vec_append("alpha", vec_append("beta", vec_empty())))

                fn main() -> Unit !{IO} =
                  print(vec_sort(sample()))
                """,
                encoding="utf-8",
            )
            bundle = load_module_bundle(main)
            program = parse(bundle.source)
            resolve_program_names(program, bundle)
            typecheck_program(program)
            lowered = lower_typeclasses(program)
            typecheck_program(lowered)
            out = io.StringIO()
            run_program(lowered, stdout=out)
            self.assertEqual(out.getvalue().strip(), "Vec([alpha, beta, beta])")

    def test_import_stdlib_collections_show_to_string(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / "main.sprout"
            main.write_text(
                """
                module main
                import stdlib.collections (Show, to_string)

                fn render(x: a) -> String where Show a =
                  to_string(x)

                fn main() -> Unit !{IO} =
                  print(render(42))
                """,
                encoding="utf-8",
            )
            bundle = load_module_bundle(main)
            program = parse(bundle.source)
            resolve_program_names(program, bundle)
            typecheck_program(program)
            lowered = lower_typeclasses(program)
            typecheck_program(lowered)
            out = io.StringIO()
            run_program(lowered, stdout=out)
            self.assertEqual(out.getvalue().strip(), "42")

    def test_import_stdlib_collections_vec_filter_helpers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / "main.sprout"
            main.write_text(
                """
                module main
                import stdlib.collections (Maybe, Vec, vec_all, vec_any, vec_append, vec_count, vec_empty, vec_filter, vec_filter_map, vec_get_or)

                fn sample() -> Vec Int =
                  vec_append(4, vec_append(3, vec_append(2, vec_append(1, vec_empty()))))

                fn is_even(value: Int) -> Bool =
                  (value / 2) * 2 == value

                fn to_even_label(value: Int) -> Maybe String =
                  if is_even(value) then Just("even") else Nothing

                fn filter_map_score(xs: Vec Int) -> Int =
                  if vec_get_or(0, "", vec_filter_map(to_even_label, xs)) == "even" then 1 else 0

                fn any_score(xs: Vec Int) -> Int =
                  if vec_any(is_even, xs) then 1 else 0

                fn all_score(xs: Vec Int) -> Int =
                  if vec_all(is_even, xs) then 10 else 0

                fn main() -> Unit !{IO} =
                  print(
                    vec_count(is_even, sample())
                    + vec_get_or(0, -1, vec_filter(is_even, sample()))
                    + filter_map_score(sample())
                    + any_score(sample())
                    + all_score(sample())
                  )
                """,
                encoding="utf-8",
            )
            bundle = load_module_bundle(main)
            program = parse(bundle.source)
            resolve_program_names(program, bundle)
            typecheck_program(program)
            out = io.StringIO()
            run_program(program, stdout=out)
            self.assertEqual(out.getvalue().strip(), "6")

    def test_import_stdlib_collections_functor_and_foldable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / "main.sprout"
            main.write_text(
                """
                module main
                import stdlib.collections (Functor, Foldable, List, Vec, vec_append, vec_empty)

                fn add_one(x: Int) -> Int = x + 1
                fn add(acc: Int, x: Int) -> Int = acc + x

                fn sum_after_map(xs: c) -> Int where Functor c, Foldable c =
                  fold(add, 0, map(add_one, xs))

                fn sample_list() -> List Int =
                  Cons(1, Cons(2, Cons(3, Nil)))

                fn sample_vec() -> Vec Int =
                  vec_append(6, vec_append(5, vec_append(4, vec_empty())))

                fn sum_list(xs: List Int) -> Int where Functor List, Foldable List =
                  sum_after_map(xs)

                fn sum_vec(xs: Vec Int) -> Int where Functor Vec, Foldable Vec =
                  sum_after_map(xs)

                fn main() -> Unit !{IO} =
                  print(sum_list(sample_list()) + sum_vec(sample_vec()))
                """,
                encoding="utf-8",
            )
            bundle = load_module_bundle(main)
            program = parse(bundle.source)
            resolve_program_names(program, bundle)
            typecheck_program(program)
            lowered = lower_typeclasses(program)
            typecheck_program(lowered)
            out = io.StringIO()
            run_program(lowered, stdout=out)
            self.assertEqual(out.getvalue().strip(), "27")

    def test_import_stdlib_string_helpers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / "main.sprout"
            main.write_text(
                """
                module main
                import stdlib.collections (vec_get_or)
                import stdlib.string (string_lines, string_digits)

                fn main() -> Unit !{IO} =
                  print(
                    vec_get_or(1, "missing", string_lines("a\\nb\\n"))
                  )
                """,
                encoding="utf-8",
            )
            bundle = load_module_bundle(main)
            program = parse(bundle.source)
            resolve_program_names(program, bundle)
            typecheck_program(program)
            out = io.StringIO()
            run_program(program, stdout=out)
            self.assertEqual(out.getvalue().strip(), "b")

    def test_import_stdlib_string_lines_handles_crlf_and_trailing_newline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / "main.sprout"
            main.write_text(
                """
                module main
                import stdlib.collections (vec_get_or)
                import stdlib.string (string_lines)

                fn main() -> Unit !{IO} =
                  print(
                    vec_get_or(1, "missing", string_lines("alpha\\r\\nbeta\\r\\n"))
                  )
                """,
                encoding="utf-8",
            )
            bundle = load_module_bundle(main)
            program = parse(bundle.source)
            resolve_program_names(program, bundle)
            typecheck_program(program)
            out = io.StringIO()
            run_program(program, stdout=out)
            self.assertEqual(out.getvalue().strip(), "beta")

    def test_import_stdlib_string_digits_helper(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / "main.sprout"
            main.write_text(
                """
                module main
                import stdlib.collections (vec_get_or)
                import stdlib.string (string_digits)

                fn main() -> Unit !{IO} =
                  print(
                    vec_get_or(1, -1, string_digits("x7y3z"))
                  )
                """,
                encoding="utf-8",
            )
            bundle = load_module_bundle(main)
            program = parse(bundle.source)
            resolve_program_names(program, bundle)
            typecheck_program(program)
            out = io.StringIO()
            run_program(program, stdout=out)
            self.assertEqual(out.getvalue().strip(), "3")

    def test_import_stdlib_string_runtime_helpers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / "main.sprout"
            main.write_text(
                """
                module main
                import stdlib.string as string

                fn main() -> Unit !{IO} =
                  print(
                    if string.starts_with(string.concat("sprout", "-lang"), "sprout")
                    then string.slice("abcdef", 1, string.length("abc"))
                    else "nope"
                  )
                """,
                encoding="utf-8",
            )
            bundle = load_module_bundle(main)
            program = parse(bundle.source)
            resolve_program_names(program, bundle)
            typecheck_program(program)
            out = io.StringIO()
            run_program(program, stdout=out)
            self.assertEqual(out.getvalue().strip(), "bcd")

    def test_import_stdlib_string_text_helpers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / "main.sprout"
            main.write_text(
                """
                module main
                import stdlib.string (char_at_or, concat, trim, contains, ends_with, string_from_char)

                fn suffix_status() -> String =
                  if ends_with("sprout-lang", "lang") then "ok" else "bad-end"

                fn contains_status() -> String =
                  if contains("sprout-lang", "out") then suffix_status() else "bad-contains"

                fn main() -> Unit !{IO} =
                  print(
                    concat(trim(" \\t sprout\\n"), concat("|", concat(string_from_char(char_at_or("abc", 1, '?')), concat("|", contains_status()))))
                  )
                """,
                encoding="utf-8",
            )
            bundle = load_module_bundle(main)
            program = parse(bundle.source)
            resolve_program_names(program, bundle)
            typecheck_program(program)
            out = io.StringIO()
            run_program(program, stdout=out)
            self.assertEqual(out.getvalue().strip(), "sprout|b|ok")

    def test_import_stdlib_string_ascii_predicates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / "main.sprout"
            main.write_text(
                """
                module main
                import stdlib.string as string

                fn flag(value: Bool) -> String =
                  if value then "1" else "0"

                fn main() -> Unit !{IO} =
                  print(
                    string.concat(
                      flag(string.is_ascii_whitespace(' ')),
                      string.concat(
                        flag(string.is_ascii_whitespace('\\t')),
                        string.concat(
                          flag(string.is_ascii_whitespace('\\n')),
                          string.concat(
                            flag(string.is_ascii_whitespace('\\r')),
                            string.concat(
                              flag(string.is_ascii_whitespace('x')),
                              string.concat(
                                "|",
                                string.concat(
                                  flag(string.is_ascii_digit('0')),
                                  string.concat(
                                    flag(string.is_ascii_digit('9')),
                                    string.concat(
                                      flag(string.is_ascii_digit('a')),
                                      string.concat(
                                        "|",
                                        string.concat(
                                          flag(string.is_ascii_alpha('a')),
                                          string.concat(
                                            flag(string.is_ascii_alpha('Z')),
                                            string.concat(
                                              flag(string.is_ascii_alpha('1')),
                                              string.concat(
                                                "|",
                                                string.concat(
                                                  flag(string.is_ascii_alnum('A')),
                                                  string.concat(
                                                    flag(string.is_ascii_alnum('7')),
                                                    string.concat(
                                                      flag(string.is_ascii_alnum('_')),
                                                      string.concat(
                                                        "|",
                                                        string.concat(
                                                          flag(string.is_ident_start('a')),
                                                          string.concat(
                                                            flag(string.is_ident_start('_')),
                                                            string.concat(
                                                              flag(string.is_ident_start('1')),
                                                              string.concat(
                                                                "|",
                                                                string.concat(
                                                                  flag(string.is_ident_continue('a')),
                                                                  string.concat(
                                                                    flag(string.is_ident_continue('_')),
                                                                    string.concat(
                                                                      flag(string.is_ident_continue('1')),
                                                                      flag(string.is_ident_continue('-'))
                                                                    )
                                                                  )
                                                                )
                                                              )
                                                            )
                                                          )
                                                        )
                                                      )
                                                    )
                                                  )
                                                )
                                              )
                                            )
                                          )
                                        )
                                      )
                                    )
                                  )
                                )
                              )
                            )
                          )
                        )
                      )
                    )
                  )
                """,
                encoding="utf-8",
            )
            bundle = load_module_bundle(main)
            program = parse(bundle.source)
            resolve_program_names(program, bundle)
            typecheck_program(program)
            out = io.StringIO()
            run_program(program, stdout=out)
            self.assertEqual(out.getvalue().strip(), "11110|110|110|110|110|1110")

    def test_import_stdlib_string_split_and_strip_helpers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / "main.sprout"
            main.write_text(
                """
                module main
                import stdlib.collections (Maybe)
                import stdlib.string (concat, drop, is_empty, split_once, strip_prefix, strip_suffix, take)

                fn prefix_score() -> String =
                  match strip_prefix("sprout-lang", "sprout-") with
                  | Just rest -> rest
                  | Nothing -> "missing-prefix"

                fn suffix_score() -> String =
                  match strip_suffix("sprout-lang", "-lang") with
                  | Just rest -> rest
                  | Nothing -> "missing-suffix"

                fn split_score() -> String =
                  match split_once("alpha=beta", "=") with
                  | Just (left, right) -> concat(left, concat("|", right))
                  | Nothing -> "missing-split"

                fn main() -> Unit !{IO} =
                  print(
                    concat(
                      take("sprout", 3),
                      concat(
                        "|",
                        concat(
                          drop("sprout", 3),
                          concat(
                            "|",
                            concat(
                              prefix_score(),
                              concat(
                                "|",
                                concat(
                                  suffix_score(),
                                  concat(
                                    "|",
                                    concat(
                                      split_score(),
                                      concat("|", if is_empty("") then "empty" else "not-empty")
                                    )
                                  )
                                )
                              )
                            )
                          )
                        )
                      )
                    )
                  )
                """,
                encoding="utf-8",
            )
            bundle = load_module_bundle(main)
            program = parse(bundle.source)
            resolve_program_names(program, bundle)
            typecheck_program(program)
            out = io.StringIO()
            run_program(program, stdout=out)
            self.assertEqual(out.getvalue().strip(), "spr|out|lang|sprout|alpha|beta|empty")

    def test_import_stdlib_regex_helpers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / "main.sprout"
            main.write_text(
                """
                module main
                import stdlib.regex as regex

                fn main() -> Unit !{IO} =
                  match regex.compile("ab|cd") with
                  | Ok re ->
                      print(
                        if regex.is_match(re, "--cd--")
                        then regex.replace_all_literal(re, "X", "ab cd")
                        else "bad-match"
                      )
                  | Err _ -> print("compile-failed")
                """,
                encoding="utf-8",
            )
            bundle = load_module_bundle(main)
            program = parse(bundle.source)
            resolve_program_names(program, bundle)
            typecheck_program(program)
            out = io.StringIO()
            run_program(program, stdout=out)
            self.assertEqual(out.getvalue().strip(), "X X")

    def test_import_stdlib_repl_module(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / "main.sprout"
            main.write_text(
                """
                module main
                import stdlib.repl as repl

                fn main() -> Unit !{IO} =
                  print(repl.main)
                """,
                encoding="utf-8",
            )
            bundle = load_module_bundle(main)
            program = parse(bundle.source)
            resolve_program_names(program, bundle)
            types = typecheck_program(program)
            self.assertIn("Unit !{IO}", [typ for name, typ in types.items() if name.endswith(".main") or name == "main"])

    def test_import_stdlib_semigroup_instances(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / "main.sprout"
            main.write_text(
                """
                module main
                import stdlib.collections (List, Semigroup)

                fn list_count(xs: List Int) -> Int =
                  match xs with
                  | Nil -> 0
                  | Cons _ rest -> 1 + list_count(rest)

                fn append_string(x: String, y: String) -> String where Semigroup String =
                  append(x, y)

                fn append_list(xs: List Int, ys: List Int) -> List Int where Semigroup (List Int) =
                  append(xs, ys)

                fn seq(a: Unit !{IO}, b: Unit !{IO}) -> Unit !{IO} = b

                fn main() -> Unit !{IO} =
                  seq(
                    print(append_string("sprout", "-lang")),
                    print(list_count(append_list(Cons(1, Nil), Cons(2, Cons(3, Nil)))))
                  )
                """,
                encoding="utf-8",
            )
            bundle = load_module_bundle(main)
            program = parse(bundle.source)
            resolve_program_names(program, bundle)
            typecheck_program(program)
            lowered = lower_typeclasses(program)
            typecheck_program(lowered)
            out = io.StringIO()
            run_program(lowered, stdout=out)
            self.assertEqual(out.getvalue().strip(), "sprout-lang\n3")

    def test_import_stdlib_semigroup_vec_and_dict_append(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / "main.sprout"
            main.write_text(
                """
                module main
                import stdlib.collections (Dict, Maybe, Semigroup, Vec, dict_empty, dict_get, dict_set, vec_append, vec_empty, vec_get_or)

                fn left_vec() -> Vec Int =
                  vec_append(2, vec_append(1, vec_empty()))

                fn right_vec() -> Vec Int =
                  vec_append(3, vec_empty())

                fn left_dict() -> Dict Int =
                  dict_set("shared", 7, dict_set("a", 1, dict_empty()))

                fn right_dict() -> Dict Int =
                  dict_set("shared", 9, dict_set("b", 2, dict_empty()))

                fn value_or(d: Dict Int, key: String, fallback: Int) -> Int =
                  match dict_get(key, d) with
                  | Just value -> value
                  | Nothing -> fallback

                fn append_vec(left: Vec Int, right: Vec Int) -> Vec Int where Semigroup (Vec Int) =
                  append(left, right)

                fn append_dict(left: Dict Int, right: Dict Int) -> Dict Int where Semigroup (Dict Int) =
                  append(left, right)

                fn main() -> Unit !{IO} =
                  print(
                    vec_get_or(2, -1, append_vec(left_vec(), right_vec()))
                    + value_or(append_dict(left_dict(), right_dict()), "shared", -1)
                    + value_or(append_dict(left_dict(), right_dict()), "b", -1)
                  )
                """,
                encoding="utf-8",
            )
            bundle = load_module_bundle(main)
            program = parse(bundle.source)
            resolve_program_names(program, bundle)
            typecheck_program(program)
            lowered = lower_typeclasses(program)
            typecheck_program(lowered)
            out = io.StringIO()
            run_program(lowered, stdout=out)
            self.assertEqual(out.getvalue().strip(), "14")

    def test_import_stdlib_semigroup_append_operator(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / "main.sprout"
            main.write_text(
                """
                module main
                import stdlib.collections (Dict, List, Maybe, Vec, dict_empty, dict_get, dict_set, vec_append, vec_empty, vec_get_or)

                fn left_vec() -> Vec Int =
                  vec_append(2, vec_append(1, vec_empty()))

                fn right_vec() -> Vec Int =
                  vec_append(3, vec_empty())

                fn value_or(d: Dict Int, key: String, fallback: Int) -> Int =
                  match dict_get(key, d) with
                  | Just value -> value
                  | Nothing -> fallback

                fn main() -> Unit !{IO} =
                  print(
                    vec_get_or(2, -1, left_vec() ++ right_vec())
                    + value_or(dict_set("a", 1, dict_empty()) ++ dict_set("a", 7, dict_empty()), "a", -1)
                  )
                """,
                encoding="utf-8",
            )
            bundle = load_module_bundle(main)
            program = parse(bundle.source)
            resolve_program_names(program, bundle)
            typecheck_program(program)
            lowered = lower_typeclasses(program)
            typecheck_program(lowered)
            out = io.StringIO()
            run_program(lowered, stdout=out)
            self.assertEqual(out.getvalue().strip(), "10")

    def test_lowering_rejects_namespaced_instances_with_same_leaf_type_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "renderable.sprout").write_text(
                """
                module renderable
                export class Renderable t {
                  fn render(x: t) -> Int
                }
                """,
                encoding="utf-8",
            )
            (root / "a.sprout").write_text(
                """
                module a
                import renderable (Renderable)

                export type Box(..) =
                  | Box Int

                instance Renderable Box {
                  fn render(x: Box) -> Int =
                    match x with
                    | Box n -> n
                }
                """,
                encoding="utf-8",
            )
            (root / "b.sprout").write_text(
                """
                module b

                export type Box(..) =
                  | Box Int
                """,
                encoding="utf-8",
            )
            main = root / "main.sprout"
            main.write_text(
                """
                module main
                import b (Box)
                import renderable (Renderable)

                fn render_box(x: Box) -> Int where Renderable Box =
                  render(x)

                fn main() -> Unit !{IO} =
                  print(render_box(Box(7)))
                """,
                encoding="utf-8",
            )
            bundle = load_module_bundle(main)
            program = parse(bundle.source)
            resolve_program_names(program, bundle)
            typecheck_program(program)
            with self.assertRaises(TypeclassLoweringError):
                lower_typeclasses(program)


if __name__ == "__main__":
    unittest.main()
