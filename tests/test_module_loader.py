from __future__ import annotations

import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from sprout import parse, typecheck_program
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
