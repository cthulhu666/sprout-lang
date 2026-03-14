from __future__ import annotations

import io
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from sprout import parse, run_program, typecheck_program
from sprout.module_loader import ModuleLoadError, load_module_bundle, load_module_source, resolve_program_names
from sprout.typeclass_lowering import lower_typeclasses


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

                fn main() -> IO Unit =
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
                fn main() -> IO Unit = print(0)
                """,
                encoding="utf-8",
            )
            with self.assertRaises(ModuleLoadError) as ctx:
                load_module_source(root / "main.sprout")
            self.assertIn("exported names", str(ctx.exception))
            self.assertIn("'ok'", str(ctx.exception))

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
                fn main() -> IO Unit = print(0)
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
                fn main() -> IO Unit = print(value())
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

                fn main() -> IO Unit =
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

                fn main() -> IO Unit =
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
                fn main() -> IO Unit = print(0)
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
                fn main() -> IO Unit =
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
                fn main() -> IO Unit =
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
                fn main() -> IO Unit = print(hidden())
                """,
                encoding="utf-8",
            )
            bundle = load_module_bundle(main)
            program = parse(bundle.source)
            with self.assertRaises(ModuleLoadError):
                resolve_program_names(program, bundle)

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
                fn main() -> IO Unit = print(value())
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
                fn main() -> IO Unit = print(use(5))
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

                fn use_get(url: String) -> Result HttpError HttpResponse =
                  http_get(url, "", 1000)

                fn main() -> IO Unit = print("ok")
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

    def test_import_examples_sentry_api_module(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / "main.sprout"
            main.write_text(
                """
                module main
                import examples.sentry_api (sentry_auth_header)

                fn main() -> IO Unit =
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

                fn main() -> IO Unit =
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

    def test_import_examples_aoc2025_day3_module(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / "main.sprout"
            main.write_text(
                """
                module main
                import examples.aoc2025_day3 (solve_sample)
                """,
                encoding="utf-8",
            )
            bundle = load_module_bundle(main)
            program = parse(bundle.source)
            resolve_program_names(program, bundle)
            typecheck_program(program)
            out = io.StringIO()
            with patch("sys.stdin", io.StringIO("")):
                run_program(program, stdout=out)
            self.assertEqual(out.getvalue().strip(), "Answers(357, 3121910778619)")

    def test_import_stdlib_collections_vec_and_dict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / "main.sprout"
            main.write_text(
                """
                module main
                import stdlib.collections (Vec, Dict, Maybe, vec_append, vec_empty, vec_get, vec_length, dict_empty, dict_get, dict_set)

                fn third_or_zero(v: Vec Int) -> Int =
                  match vec_get(v, 2) with
                  | Just x -> x
                  | Nothing -> 0

                fn read_or_missing(d: Dict Int, key: String) -> Int =
                  match dict_get(d, key) with
                  | Just x -> x
                  | Nothing -> -1

                fn main() -> IO Unit =
                  print(
                    read_or_missing(
                      dict_set(dict_set(dict_empty(), "a", 1), "b", third_or_zero(vec_append(vec_append(vec_append(vec_empty(), 10), 20), 30))),
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
                  match dict_get(d, key) with
                  | Just x -> x
                  | Nothing -> -1

                fn main() -> IO Unit =
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
                  vec_append(vec_append(vec_append(vec_append(vec_empty(), 10), 20), 30), 40)

                fn main() -> IO Unit =
                  print(
                    value_or(
                      vec_get(
                        vec_reverse(vec_slice(sample(), 1, 2)),
                        0
                      ),
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

                fn sample() -> Dict Int =
                  dict_set(dict_set(dict_empty(), "alpha", 7), "beta", 11)

                fn main() -> IO Unit =
                  print(
                    vec_get_or(dict_values(sample()), 1, -100)
                    + str_len(vec_get_or(dict_keys(sample()), 0, ""))
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
                  vec_append(vec_append(vec_append(vec_empty(), 10), 20), 30)

                fn tens(value: Int) -> Int = value / 10

                fn main() -> IO Unit =
                  print(vec_sum(sample()) + vec_sum_by(sample(), tens))
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
                  fold_values(fmap(add_one, xs), 0, add)

                fn sample_list() -> List Int =
                  Cons(1, Cons(2, Cons(3, Nil)))

                fn sample_vec() -> Vec Int =
                  vec_append(vec_append(vec_append(vec_empty(), 4), 5), 6)

                fn sum_list(xs: List Int) -> Int where Functor List, Foldable List =
                  sum_after_map(xs)

                fn sum_vec(xs: Vec Int) -> Int where Functor Vec, Foldable Vec =
                  sum_after_map(xs)

                fn main() -> IO Unit =
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

                fn main() -> IO Unit =
                  print(
                    vec_get_or(string_lines("a\\nb\\n"), 1, "missing")
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

                fn main() -> IO Unit =
                  print(
                    vec_get_or(string_digits("x7y3z"), 1, -1)
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


if __name__ == "__main__":
    unittest.main()
