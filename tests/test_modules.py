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
                  json.object_from_pairs([("count", json.int(2))])

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
            out = io.StringIO()
            run_program(program, stdout=out)
            self.assertEqual(out.getvalue().strip(), "ok")

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
                  print(json_stringify(payload()))
                """,
                encoding="utf-8",
            )
            bundle = load_module_bundle(main)
            program = parse(bundle.source)
            resolve_program_names(program, bundle)
            typecheck_program(program)
            out = io.StringIO()
            run_program(program, stdout=out)
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
                  fold_values(add, 0, fmap(add_one, xs))

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
                import stdlib.string (concat, trim, contains, ends_with)

                fn suffix_status() -> String =
                  if ends_with("sprout-lang", "lang") then "ok" else "bad-end"

                fn contains_status() -> String =
                  if contains("sprout-lang", "out") then suffix_status() else "bad-contains"

                fn main() -> Unit !{IO} =
                  print(
                    concat(trim(" \\t sprout\\n"), concat("|", contains_status()))
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
            self.assertEqual(out.getvalue().strip(), "sprout|ok")

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
