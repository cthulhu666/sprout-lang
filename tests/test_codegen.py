from __future__ import annotations

import os
import re
import unittest
import shutil
import subprocess
import tempfile
from pathlib import Path
import sys

from sprout import CodegenError, compile_to_llvm, parse, typecheck_program
from sprout import cli as sprout_cli
from sprout.module_loader import load_module_bundle, resolve_program_names
from sprout.stdlib import with_http_prelude
from tests.integration_support import compiled_native_binary, running_tcp_fixture


class CodegenTests(unittest.TestCase):
    def test_compile_recursive_if_program_to_llvm(self) -> None:
        src = """
        fn fact(n: Int) -> Int =
          if n == 0 then 1 else n * fact(n - 1)

        fn main() -> Int =
          fact(5)
        """
        program = parse(src)
        typecheck_program(program)
        ir = compile_to_llvm(program)

        self.assertIn("define i64 @fact(i64 %n)", ir)
        self.assertIn("define i64 @main(i32 %argc, ptr %argv)", ir)
        self.assertIn("icmp eq i64", ir)
        self.assertIn("call i64 @fact", ir)

    def test_compile_inferred_function_signature_to_llvm(self) -> None:
        src = """
        fn inc(x) = x + 1

        fn main() -> Int =
          inc(5)
        """
        program = parse(src)
        typecheck_program(program)
        ir = compile_to_llvm(program)
        self.assertIn("define i64 @inc(i64 %x)", ir)

    def test_compile_supports_top_level_const_let(self) -> None:
        src = """
        let base = 40
        let two = 2
        fn main() -> Int !{IO} = print_int(base + two)
        """
        program = parse(src)
        typecheck_program(program)
        ir = compile_to_llvm(program)
        self.assertIn("@base = private constant i64 40", ir)
        self.assertIn("@two = private constant i64 2", ir)
        self.assertIn("load i64, ptr @base", ir)
        self.assertIn("load i64, ptr @two", ir)

    def test_compile_supports_runtime_top_level_let(self) -> None:
        src = """
        fn value() -> Int = 1
        let x = value()
        fn main() -> Int !{IO} = print_int(x)
        """
        program = parse(src)
        typecheck_program(program)
        ir = compile_to_llvm(program)
        self.assertIn("@x = global i64 0", ir)
        self.assertIn("define void @__sprout_init_globals()", ir)
        self.assertIn("store i64", ir)

    def test_compile_with_print_int_external(self) -> None:
        src = """
        fn main() -> Int !{IO} =
          print_int(42)
        """
        program = parse(src)
        typecheck_program(program)
        ir = compile_to_llvm(program)
        self.assertIn("declare i64 @print_int(i64)", ir)
        self.assertIn("call i64 @print_int(i64 42)", ir)
        self.assertIn("declare i64 @print_str(ptr)", ir)

    def test_compile_main_io_unit_with_print_string(self) -> None:
        src = """
        fn main() -> Unit !{IO} =
          print("hello")
        """
        program = parse(src)
        typecheck_program(program)
        ir = compile_to_llvm(program)
        self.assertIn("define i64 @main(i32 %argc, ptr %argv)", ir)
        self.assertIn("call i64 @print_str(ptr", ir)

    def test_compile_adt_match(self) -> None:
        src = """
        type MaybeInt =
          | Just Int
          | Nothing

        fn unwrap(m: MaybeInt) -> Int =
          match m with
          | Just x -> x
          | Nothing -> 0

        fn main() -> Int !{IO} =
          print_int(unwrap(Just(42)))
        """
        program = parse(src)
        typecheck_program(program)
        ir = compile_to_llvm(program)
        self.assertIn("call i64 @sprout_make1(i64 0, i64 42)", ir)
        self.assertIn("call i64 @sprout_tag", ir)
        self.assertIn("call i64 @sprout_field", ir)
        self.assertIn("call i64 @sprout_gc_push_i64_root(ptr", ir)
        self.assertIn("call i64 @sprout_gc_pop_roots(i64 1)", ir)

    def test_compile_nothing_uses_native_singleton_helper(self) -> None:
        src = """
        type MaybeInt =
          | Just Int
          | Nothing

        fn make_none() -> MaybeInt =
          Nothing

        fn main() -> Int !{IO} =
          match make_none() with
          | Nothing -> print_int(0)
          | Just value -> print_int(value)
        """
        program = parse(src)
        typecheck_program(program)
        ir = compile_to_llvm(program)
        self.assertIn("call i64 @sprout_nothing(i64 1)", ir)
        self.assertNotIn("call i64 @sprout_make0(i64", ir)

    def test_compile_direct_constructor_match_avoids_materializing_maybe(self) -> None:
        src = """
        type MaybeInt =
          | Just Int
          | Nothing

        fn main() -> Int !{IO} =
          match if true then Just(42) else Nothing with
          | Just value -> print_int(value)
          | Nothing -> print_int(0)
        """
        program = parse(src)
        typecheck_program(program)
        ir = compile_to_llvm(program)
        self.assertNotIn("call i64 @sprout_make0", ir)
        self.assertNotIn("call i64 @sprout_make1", ir)
        self.assertNotIn("call i64 @sprout_tag", ir)
        self.assertNotIn("call i64 @sprout_field", ir)
        self.assertIn("phi i64", ir)

    def test_compile_direct_constructor_match_falls_back_for_top_level_var_pattern(self) -> None:
        src = """
        type MaybeInt =
          | Just Int
          | Nothing

        fn unwrap(m: MaybeInt) -> Int =
          match m with
          | Just value -> value
          | Nothing -> 0

        fn main() -> Int !{IO} =
          match if true then Just(42) else Nothing with
          | value -> print_int(unwrap(value))
        """
        program = parse(src)
        typecheck_program(program)
        ir = compile_to_llvm(program)
        self.assertIn("call i64 @sprout_make1(i64 0, i64 42)", ir)
        self.assertIn("call i64 @sprout_nothing(i64 1)", ir)
        self.assertIn("call i64 @sprout_tag", ir)

    def test_compile_print_adt_value(self) -> None:
        src = """
        type Pair =
          | Pair Int Int

        fn main() -> Unit !{IO} =
          print(Pair(3, 6))
        """
        program = parse(src)
        typecheck_program(program)
        ir = compile_to_llvm(program)
        self.assertIn("declare i64 @print_value(i64)", ir)
        self.assertIn("call i64 @print_value(i64", ir)
        self.assertIn("call i64 @sprout_register_ctor", ir)

    def test_compile_print_tuple_value(self) -> None:
        src = """
        fn main() -> Unit !{IO} =
          print(((1, 2), 3, "ok"))
        """
        program = parse(src)
        typecheck_program(program)
        ir = compile_to_llvm(program)
        self.assertIn("declare i64 @print_text(ptr)", ir)
        self.assertIn("declare i64 @print_newline()", ir)
        self.assertIn("declare i64 @print_value_part(i64)", ir)
        self.assertIn("call i64 @print_text(ptr", ir)
        self.assertIn("call i64 @print_value_part(i64", ir)

    def test_compile_tuple_packing_uses_gc_managed_blob(self) -> None:
        src = """
        type Wrap =
          | Wrap (Int, String)

        fn main() -> Unit !{IO} =
          print(Wrap((1, "ok")))
        """
        program = parse(src)
        typecheck_program(program)
        ir = compile_to_llvm(program)
        self.assertIn("declare ptr @sprout_alloc_tuple_blob(i64)", ir)
        self.assertIn("call ptr @sprout_alloc_tuple_blob(i64", ir)
        self.assertIn("call i64 @sprout_gc_push_scan_root(ptr", ir)
        self.assertNotIn("call ptr @malloc(i64", ir)

    def test_compile_generic_identity_erased(self) -> None:
        src = """
        fn id(x: a) -> a = x
        fn main() -> Int !{IO} = print_int(id(42))
        """
        program = parse(src)
        typecheck_program(program)
        ir = compile_to_llvm(program)
        self.assertIn("define i64 @id(i64 %x)", ir)
        self.assertIn("call i64 @id(i64 42)", ir)

    def test_compile_higher_order_function_param(self) -> None:
        src = """
        fn inc(x: Int) -> Int = x + 1
        fn apply(x: Int, f: Int -> Int) -> Int = f(x)
        fn main() -> Int !{IO} = print_int(apply(41, inc))
        """
        program = parse(src)
        typecheck_program(program)
        ir = compile_to_llvm(program)
        self.assertIn("define i64 @apply(i64 %x, ptr %f)", ir)
        self.assertIn("call ptr @sprout_alloc_closure_env(i64 %t", ir)
        self.assertIn("load ptr, ptr %f", ir)

    def test_compile_lambda_with_capture_to_llvm(self) -> None:
        src = r"""
        fn make_adder(base: Int) -> Int -> Int =
          \(x) -> base + x

        fn main() -> Int !{IO} =
          print_int(make_adder(41)(1))
        """
        program = parse(src)
        typecheck_program(program)
        ir = compile_to_llvm(program)
        self.assertIn("define ptr @__sprout_fn_closure_", ir)
        self.assertIn("define i64 @__sprout_lambda_", ir)
        self.assertIn("call ptr @sprout_alloc_closure_env(i64 %t", ir)
        self.assertIn("call i64 @sprout_gc_push_i64_root(ptr", ir)
        self.assertIn("call i64 @sprout_gc_push_ptr_root(ptr", ir)
        self.assertIn("call i64 @sprout_gc_pop_roots(i64", ir)
        self.assertIn("getelementptr i64, ptr %env, i64 1", ir)

    def test_compile_function_params_root_managed_values(self) -> None:
        src = """
        type Box =
          | Box Int

        fn keep(box: Box, pair: (Int, String), f: Int -> Int) -> Int =
          match box with
          | Box(x) -> f(x)

        fn inc(x: Int) -> Int = x + 1

        fn main() -> Int !{IO} =
          print_int(keep(Box(1), (2, "ok"), inc))
        """
        program = parse(src)
        typecheck_program(program)
        ir = compile_to_llvm(program)
        self.assertIn("call i64 @sprout_gc_push_i64_root(ptr", ir)
        self.assertIn("call i64 @sprout_gc_push_scan_root(ptr", ir)
        self.assertIn("call i64 @sprout_gc_push_ptr_root(ptr", ir)
        self.assertIn("call i64 @sprout_gc_pop_roots(i64 3)", ir)

    def test_compile_partial_application_of_named_function_to_llvm(self) -> None:
        src = """
        fn add(x: Int, y: Int) -> Int = x + y
        let inc = add(1)

        fn main() -> Int !{IO} =
          print_int(inc(41))
        """
        program = parse(src)
        typecheck_program(program)
        ir = compile_to_llvm(program)
        self.assertIn("define i64 @__sprout_partial_", ir)
        self.assertIn("@inc = global ptr null", ir)

    def test_compile_partial_application_of_builtin_to_llvm(self) -> None:
        src = """
        let greet = str_concat("hi ")

        fn main() -> Unit !{IO} =
          print(greet("sprout"))
        """
        program = parse(src)
        typecheck_program(program)
        ir = compile_to_llvm(program)
        self.assertIn("define ptr @__sprout_partial_", ir)
        self.assertIn("@greet = global ptr null", ir)

    def test_compile_runtime_top_level_function_value_let_to_llvm(self) -> None:
        src = r"""
        fn inc(x: Int) -> Int = x + 1
        let f = inc
        let g = \(x: Int) -> x + 2

        fn main() -> Int !{IO} =
          print_int(f(20) + g(20))
        """
        program = parse(src)
        typecheck_program(program)
        ir = compile_to_llvm(program)
        self.assertIn("define void @__sprout_init_globals()", ir)
        self.assertIn("store ptr", ir)
        self.assertIn("@f = global ptr null", ir)
        self.assertIn("@g = global ptr null", ir)

    def test_compile_runtime_top_level_multi_arg_lambda_value_to_llvm(self) -> None:
        src = r"""
        let add = \(x: Int, y: Int) -> x + y

        fn main() -> Int !{IO} =
          print_int(add(20, 22))
        """
        program = parse(src)
        typecheck_program(program)
        ir = compile_to_llvm(program)
        self.assertIn("@add = global ptr null", ir)
        self.assertIn("call i64 %", ir)

    def test_compile_lambda_returning_named_function_value_to_llvm(self) -> None:
        src = r"""
        fn inc(x: Int) -> Int = x + 1

        fn outer() -> Bool -> Int -> Int =
          \flag -> if flag then inc else inc

        fn main() -> Int !{IO} =
          print_int(outer()(true)(41))
        """
        program = parse(src)
        typecheck_program(program)
        ir = compile_to_llvm(program)
        self.assertIn("define ptr @__sprout_lambda_", ir)
        self.assertIn("call ptr @sprout_alloc_closure_env(i64 %t", ir)
        self.assertIn("@__sprout_fn_closure_", ir)

    def test_compile_tuple_match_to_llvm(self) -> None:
        src = """
        fn sum_pair(pair: (Int, Int)) -> Int =
          match pair with
          | (x, y) -> x + y

        fn main() -> Int !{IO} =
          print_int(sum_pair((20, 22)))
        """
        program = parse(src)
        typecheck_program(program)
        ir = compile_to_llvm(program)
        self.assertIn("define i64 @sum_pair({ i64, i64 } %pair)", ir)
        self.assertIn("extractvalue { i64, i64 } %pair, 0", ir)
        self.assertIn("extractvalue { i64, i64 } %pair, 1", ir)
        self.assertIn("call i64 @sprout_gc_push_i64_root(ptr", ir)
        self.assertIn("call i64 @sprout_gc_pop_roots(i64 2)", ir)

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_native_compile_tuple_match(self) -> None:
        src = """
        fn sum_pair(pair: (Int, Int)) -> Int =
          match pair with
          | (x, y) -> x + y

        fn main() -> Int !{IO} =
          print_int(sum_pair((20, 22)))
        """
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            spr_path = tmp_path / "prog.spr"
            bin_path = tmp_path / "prog"
            spr_path.write_text(src, encoding="utf-8")

            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "sprout.cli",
                    "compile",
                    str(spr_path),
                    "--native",
                    "-o",
                    str(bin_path),
                ],
                check=True,
            )
            run = subprocess.run([str(bin_path)], check=False, capture_output=True, text=True)
            self.assertEqual(run.stdout.strip(), "42")
            self.assertEqual(run.returncode, 42)

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_native_compile_reachable_helper_inside_tuple_expr(self) -> None:
        src = """
        fn bump(x: Int) -> Int = x + 1

        fn pair(x: Int) -> (Int, Int) =
          (bump(x), bump(x + 1))

        fn main() -> Int !{IO} =
          match pair(40) with
          | (left, right) -> print_int(left + right)
        """
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            spr_path = tmp_path / "prog.spr"
            bin_path = tmp_path / "prog"
            spr_path.write_text(src, encoding="utf-8")
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "sprout.cli",
                    "compile",
                    str(spr_path),
                    "--native",
                    "-o",
                    str(bin_path),
                ],
                check=True,
            )
            run = subprocess.run([str(bin_path)], check=False, capture_output=True, text=True)
            self.assertEqual(run.stdout.strip(), "83")
            self.assertEqual(run.returncode, 83)

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_native_print_tuple_value(self) -> None:
        src = """
        fn main() -> Unit !{IO} =
          print(((1, 2), 3, "ok"))
        """
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            spr_path = tmp_path / "prog.spr"
            bin_path = tmp_path / "prog"
            spr_path.write_text(src, encoding="utf-8")

            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "sprout.cli",
                    "compile",
                    str(spr_path),
                    "--native",
                    "-o",
                    str(bin_path),
                ],
                check=True,
            )
            run = subprocess.run([str(bin_path)], check=False, capture_output=True, text=True)
            self.assertEqual(run.stdout.strip(), "((1, 2), 3, ok)")
            self.assertEqual(run.returncode, 0)

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_native_nothing_singleton_execution(self) -> None:
        src = """
        type MaybeInt =
          | Just Int
          | Nothing

        fn make_none() -> MaybeInt =
          Nothing

        fn main() -> Int !{IO} =
          match make_none() with
          | Nothing -> print_int(0)
          | Just value -> print_int(value)
        """
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            spr_path = tmp_path / "prog.spr"
            bin_path = tmp_path / "prog"
            spr_path.write_text(src, encoding="utf-8")

            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "sprout.cli",
                    "compile",
                    str(spr_path),
                    "--native",
                    "-o",
                    str(bin_path),
                ],
                check=True,
            )
            run = subprocess.run([str(bin_path)], check=False, capture_output=True, text=True)
            self.assertEqual(run.stdout.strip(), "0")
            self.assertEqual(run.returncode, 0)

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_native_top_level_nothing_initializer_does_not_require_ctor_metadata(self) -> None:
        src = """
        type MaybeInt =
          | Just Int
          | Nothing

        let none = Nothing

        fn main() -> Int !{IO} =
          match none with
          | Nothing -> print_int(0)
          | Just value -> print_int(value)
        """
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            spr_path = tmp_path / "prog.spr"
            bin_path = tmp_path / "prog"
            spr_path.write_text(src, encoding="utf-8")

            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "sprout.cli",
                    "compile",
                    str(spr_path),
                    "--native",
                    "-o",
                    str(bin_path),
                ],
                check=True,
            )
            run = subprocess.run([str(bin_path)], check=False, capture_output=True, text=True)
            self.assertEqual(run.stdout.strip(), "0")
            self.assertEqual(run.returncode, 0, msg=run.stderr)

    def test_compile_tcp_builtins_to_llvm(self) -> None:
        src = """
        fn seq(a: Unit !{IO}, b: Unit !{IO}) -> Unit !{IO} = b
        fn main() -> Unit !{IO} =
          seq(
            tcp_write(1, tcp_read(1)),
            seq(tcp_close(1), tcp_close_listener(tcp_listen(8081)))
          )
        """
        program = parse(src)
        typecheck_program(program)
        ir = compile_to_llvm(program)
        self.assertIn("declare i64 @tcp_listen(i64)", ir)
        self.assertIn("declare i64 @tcp_accept(i64)", ir)
        self.assertIn("declare ptr @tcp_read(i64)", ir)
        self.assertIn("declare i64 @tcp_write(i64, ptr)", ir)
        self.assertIn("declare i64 @tcp_close(i64)", ir)
        self.assertIn("declare i64 @tcp_close_listener(i64)", ir)
        self.assertIn("declare i64 @tcp_echo_serve(i64, i64)", ir)

    def test_compile_typed_tcp_client_builtins_to_llvm(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            spr_path = tmp_path / "main.sprout"
            spr_path.write_text(
                """
                module main
                import stdlib.net (Result, TcpConnection, TcpError, connect, read_exact_utf8, write_all_utf8)

                fn main() -> Unit !{IO} =
                  match connect("127.0.0.1", 5432) with
                  | Err _ -> print("err")
                  | Ok conn ->
                      match write_all_utf8(conn, "ping") with
                      | Err _ -> print("write")
                      | Ok _ ->
                          match read_exact_utf8(conn, 4) with
                          | Err _ -> print("read")
                          | Ok body -> print(body)
                """,
                encoding="utf-8",
            )
            bundle = load_module_bundle(spr_path)
            program = parse(bundle.source)
            resolve_program_names(program, bundle)
            typecheck_program(program)
            ir = compile_to_llvm(program)
            self.assertIn("declare i64 @tcp_connect(ptr, i64)", ir)
            self.assertIn("declare i64 @tcp_read_exact(i64, i64)", ir)
            self.assertIn("declare i64 @tcp_write_all(i64, i64)", ir)

    def test_compile_env_get_builtin_to_llvm(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            spr_path = tmp_path / "main.sprout"
            spr_path.write_text(
                """
                module main
                import stdlib.collections (Maybe)

                fn main() -> Unit !{IO} =
                  match env_get("SPROUT_TEST_ENV_GET") with
                  | Just value -> print(value)
                  | Nothing -> print("missing")
                """,
                encoding="utf-8",
            )
            bundle = load_module_bundle(spr_path)
            program = parse(bundle.source)
            resolve_program_names(program, bundle)
            typecheck_program(program)
            ir = compile_to_llvm(program)
            self.assertIn("declare i64 @env_get(ptr)", ir)

    def test_compile_bytes_builtins_to_llvm(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            spr_path = tmp_path / "main.sprout"
            spr_path.write_text(
                """
                module main
                import stdlib.collections (Maybe)
                import stdlib.bytes (Result, Utf8Error, append, from_string, length, read_u16_be, to_string, u16_be)

                fn value_or_zero(value: Maybe Int) -> Int =
                  match value with
                  | Just n -> n
                  | Nothing -> 0

                fn string_score_or_zero(value: Result Utf8Error String, expected: String, score: Int) -> Int =
                  match value with
                  | Ok text -> if text == expected then score else 0
                  | Err _ -> 0

                fn main() -> Unit !{IO} =
                  print(
                    value_or_zero(read_u16_be(append(u16_be(1), u16_be(length(u16_be(2))))))
                    + string_score_or_zero(to_string(from_string("zaż")), "zaż", 3)
                  )
                """,
                encoding="utf-8",
            )
            bundle = load_module_bundle(spr_path)
            program = parse(bundle.source)
            resolve_program_names(program, bundle)
            typecheck_program(program)
            ir = compile_to_llvm(program)
            self.assertIn("declare i64 @bytes_empty()", ir)
            self.assertIn("declare i64 @bytes_length(i64)", ir)
            self.assertIn("declare i64 @bytes_get(i64, i64)", ir)
            self.assertIn("declare i64 @bytes_slice(i64, i64, i64)", ir)
            self.assertIn("declare i64 @bytes_append(i64, i64)", ir)
            self.assertIn("declare i64 @bytes_singleton(i64)", ir)
            self.assertIn("declare i64 @bytes_from_utf8(ptr)", ir)
            self.assertIn("declare i64 @bytes_to_utf8(i64)", ir)

    def test_compile_bytes_builder_builtins_to_llvm(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            spr_path = tmp_path / "main.sprout"
            spr_path.write_text(
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
                  | Ok text -> print(length(builder_build(builder_empty())) + score(text))
                  | Err _ -> print(0)
                """,
                encoding="utf-8",
            )
            bundle = load_module_bundle(spr_path)
            program = parse(bundle.source)
            resolve_program_names(program, bundle)
            typecheck_program(program)
            ir = compile_to_llvm(program)
            self.assertIn("declare i64 @bytes_builder_empty()", ir)
            self.assertIn("declare i64 @bytes_builder_bytes(i64)", ir)
            self.assertIn("declare i64 @bytes_builder_byte(i64)", ir)
            self.assertIn("declare i64 @bytes_builder_u16_be(i64)", ir)
            self.assertIn("declare i64 @bytes_builder_u32_be(i64)", ir)
            self.assertIn("declare i64 @bytes_builder_append(i64, i64)", ir)
            self.assertIn("declare i64 @bytes_builder_build(i64)", ir)

    def test_compile_crypto_builtins_to_llvm(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            spr_path = tmp_path / "main.sprout"
            spr_path.write_text(
                """
                module main
                import stdlib.bytes (from_string, length)
                import stdlib.crypto as crypto
                import stdlib.string as string

                fn score_decode() -> Int =
                  match crypto.base64_decode("c3Byb3V0") with
                  | Ok decoded -> string.length(crypto.base64_encode(crypto.sha256(decoded)))
                  | Err _ -> 0

                fn score_xor() -> Int =
                  match crypto.bytes_xor(from_string("abc"), from_string("ABC")) with
                  | Ok xored ->
                      string.length(crypto.base64_encode(crypto.hmac_sha256(from_string("key"), from_string("sprout"))))
                      + string.length(crypto.base64_encode(xored))
                  | Err _ -> 0

                fn score_random() -> Int !{IO} =
                  match crypto.random_bytes(0) with
                  | Ok nonce -> length(nonce)
                  | Err _ -> 0

                fn main() -> Unit !{IO} =
                  print(score_decode() + score_xor() + score_random())
                """,
                encoding="utf-8",
            )
            bundle = load_module_bundle(spr_path)
            program = parse(bundle.source)
            resolve_program_names(program, bundle)
            typecheck_program(program)
            ir = compile_to_llvm(program)
            self.assertIn("declare i64 @crypto_sha256(i64)", ir)
            self.assertIn("declare i64 @crypto_hmac_sha256(i64, i64)", ir)
            self.assertIn("declare ptr @crypto_base64_encode(i64)", ir)
            self.assertIn("declare i64 @crypto_base64_decode(ptr)", ir)
            self.assertIn("declare i64 @crypto_bytes_xor(i64, i64)", ir)
            self.assertIn("declare i64 @crypto_random_bytes(i64)", ir)

    def test_native_runtime_roots_managed_args_in_key_allocating_helpers(self) -> None:
        runtime_source = Path(sprout_cli.__file__).read_text(encoding="utf-8")
        self.assertIn("#define SPROUT_GC_PUSH_PTR_LOCAL(slot_name)", runtime_source)
        self.assertIn("long long rooted_vec = vec;", runtime_source)
        self.assertIn("long long rooted_map = map_h;", runtime_source)
        self.assertIn("long long rooted_builder = builder_h;", runtime_source)
        self.assertIn("long long rooted_bytes = bytes_h;", runtime_source)
        self.assertIn("long long rooted_left = left_h;", runtime_source)
        self.assertIn("long long rooted_right = right_h;", runtime_source)
        self.assertIn("long long rooted_key = key_h;", runtime_source)
        self.assertIn("long long rooted_msg = msg_h;", runtime_source)
        self.assertIn("SPROUT_GC_PUSH_I64_LOCAL(rooted_value);", runtime_source)
        self.assertIn("SPROUT_GC_PUSH_PTR_LOCAL(chunk);", runtime_source)
        self.assertIn("SPROUT_GC_PUSH_PTR_LOCAL(out);", runtime_source)
        self.assertIn("SPROUT_GC_PUSH_I64_LOCAL(err);", runtime_source)
        self.assertIn("long long rooted_payload = payload;", runtime_source)
        self.assertIn('BytesVal* out = sprout_alloc_bytes_val("crypto_bytes_xor: out of memory");', runtime_source)
        self.assertIn("long long result = sprout_make1(find_ctor_tag_by_name(\"Ok\"), (long long)(uintptr_t)out);", runtime_source)
        self.assertIn("SPROUT_GC_POP_LOCALS(3);", runtime_source)
        self.assertIn("SPROUT_GC_POP_LOCALS(2);", runtime_source)

    def test_compile_string_builtins_to_llvm(self) -> None:
        src = """
        fn main() -> Unit !{IO} =
          print(str_concat(str_slice("sprout", 0, 3), " ok"))
        """
        program = parse(src)
        typecheck_program(program)
        ir = compile_to_llvm(program)
        self.assertIn("declare ptr @str_concat(ptr, ptr)", ir)
        self.assertIn("declare i64 @str_len(ptr)", ir)
        self.assertIn("declare ptr @str_slice(ptr, i64, i64)", ir)
        self.assertIn("declare i1 @str_eq(ptr, ptr)", ir)
        self.assertIn("declare i64 @str_find(ptr, ptr)", ir)
        self.assertIn("declare i1 @str_starts_with(ptr, ptr)", ir)

    def test_compile_string_equality_uses_content_compare(self) -> None:
        src = """
        fn main() -> Unit !{IO} =
          print(str_slice("sprout", 0, 3) == "spr")
        """
        program = parse(src)
        typecheck_program(program)
        ir = compile_to_llvm(program)
        self.assertIn("call i1 @str_eq(", ir)
        self.assertNotIn("icmp eq ptr", ir)

    def test_compile_http_request_to_llvm(self) -> None:
        src = """
        fn main() -> Unit !{IO} =
          match http_request("GET", "http://127.0.0.1:8080/ok", "", "", 500) with
          | Ok resp -> print(http_response_body(resp))
          | Err _ -> print("err")
        """
        program = parse(with_http_prelude(src))
        typecheck_program(program)
        ir = compile_to_llvm(program)
        self.assertIn("declare i64 @http_request(ptr, ptr, ptr, ptr, i64)", ir)

    def test_compile_json_stringify_to_llvm(self) -> None:
        src = """
        fn main() -> Unit !{IO} =
          print(json_stringify(JsonArray(JsonArrayCons(JsonInt(1), JsonArrayNil))))
        """
        program = parse(with_http_prelude(src))
        typecheck_program(program)
        ir = compile_to_llvm(program)
        self.assertIn("declare ptr @json_stringify(i64)", ir)

    def test_compile_http_prelude_registers_qualified_runtime_ctors(self) -> None:
        src = """
        fn main() -> Unit !{IO} =
          match http_request("GET", "http://127.0.0.1:8080/ok", "", "", 500) with
          | Ok resp -> print(http_response_body(resp))
          | Err _ -> print("err")
        """
        program = parse(with_http_prelude(src))
        typecheck_program(program)
        ir = compile_to_llvm(program)
        self.assertGreaterEqual(ir.count("call i64 @sprout_register_ctor(i64 8"), 2)
        self.assertGreaterEqual(ir.count("call i64 @sprout_register_ctor(i64 9"), 2)
        self.assertGreaterEqual(ir.count("call i64 @sprout_register_ctor(i64 10"), 2)
        self.assertIn("declare i64 @argv_get(i64)", ir)
        self.assertIn("declare i64 @sprout_set_argv(i32, ptr)", ir)
        self.assertIn("define i64 @main(i32 %argc, ptr %argv)", ir)
        self.assertIn("declare i64 @sprout_make3(i64, i64, i64, i64)", ir)
        self.assertIn("call i64 @sprout_field(i64 %resp, i64 2)", ir)

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_native_compile_http_request_program(self) -> None:
        src = """
        fn main() -> Unit !{IO} =
          match http_request("GET", "http://127.0.0.1:8080/ok", "", "", 500) with
          | Ok resp -> print(http_response_body(resp))
          | Err _ -> print("err")
        """
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            spr_path = tmp_path / "prog.sprout"
            bin_path = tmp_path / "prog"
            spr_path.write_text(src, encoding="utf-8")
            compile_proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "sprout.cli",
                    "compile",
                    "--with-http-stdlib",
                    str(spr_path),
                    "--native",
                    "-o",
                    str(bin_path),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(compile_proc.returncode, 0, msg=compile_proc.stderr)
            self.assertTrue(bin_path.exists())

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_native_compile_and_execute(self) -> None:
        src = """
        fn main() -> Int !{IO} =
          print_int(42)
        """
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            spr_path = tmp_path / "prog.spr"
            bin_path = tmp_path / "prog"
            spr_path.write_text(src, encoding="utf-8")

            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "sprout.cli",
                    "compile",
                    str(spr_path),
                    "--native",
                    "-o",
                    str(bin_path),
                ],
                check=True,
            )
            run = subprocess.run([str(bin_path)], check=False, capture_output=True, text=True)
            self.assertEqual(run.stdout.strip(), "42")
            self.assertEqual(run.returncode, 42)

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_native_direct_constructor_match_executes_without_maybe_regression(self) -> None:
        src = """
        type MaybeInt =
          | Just Int
          | Nothing

        fn main() -> Int !{IO} =
          match if false then Just(7) else Nothing with
          | Just value -> print_int(value)
          | Nothing -> print_int(0)
        """
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            spr_path = tmp_path / "prog.spr"
            bin_path = tmp_path / "prog"
            spr_path.write_text(src, encoding="utf-8")

            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "sprout.cli",
                    "compile",
                    str(spr_path),
                    "--native",
                    "-o",
                    str(bin_path),
                ],
                check=True,
            )
            run = subprocess.run([str(bin_path)], check=False, capture_output=True, text=True)
            self.assertEqual(run.stdout.strip(), "0")
            self.assertEqual(run.returncode, 0)

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_native_program_receives_program_args(self) -> None:
        src = """
        module main
        import stdlib.collections (Maybe)

        fn main() -> Unit !{IO} =
          match argv_get(0) with
          | Just value -> print(value)
          | Nothing -> print("missing")
        """
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            spr_path = tmp_path / "prog.spr"
            bin_path = tmp_path / "prog"
            spr_path.write_text(src, encoding="utf-8")

            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "sprout.cli",
                    "compile",
                    str(spr_path),
                    "--native",
                    "-o",
                    str(bin_path),
                ],
                check=True,
            )
            run = subprocess.run([str(bin_path), "http://example.test"], check=False, capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, msg=run.stderr)
            self.assertEqual(run.stdout.strip(), "http://example.test")

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_native_program_uses_nothing_path_for_missing_arg(self) -> None:
        src = """
        module main
        import stdlib.collections (Maybe)

        fn main() -> Unit !{IO} =
          match argv_get(0) with
          | Just value -> print(value)
          | Nothing -> print("missing")
        """
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            spr_path = tmp_path / "prog.spr"
            bin_path = tmp_path / "prog"
            spr_path.write_text(src, encoding="utf-8")

            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "sprout.cli",
                    "compile",
                    str(spr_path),
                    "--native",
                    "-o",
                    str(bin_path),
                ],
                check=True,
            )
            run = subprocess.run([str(bin_path)], check=False, capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, msg=run.stderr)
            self.assertEqual(run.stdout.strip(), "missing")

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_native_short_circuit_and(self) -> None:
        src = """
        fn side() -> Bool !{IO} =
          print_int(1) == 1

        fn main() -> Int !{IO} =
          if false && side() then print_int(0) else print_int(42)
        """
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            spr_path = tmp_path / "prog.spr"
            bin_path = tmp_path / "prog"
            spr_path.write_text(src, encoding="utf-8")
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "sprout.cli",
                    "compile",
                    str(spr_path),
                    "--native",
                    "-o",
                    str(bin_path),
                ],
                check=True,
            )
            run = subprocess.run([str(bin_path)], check=False, capture_output=True, text=True)
            self.assertEqual(run.stdout.strip(), "42")
            self.assertEqual(run.returncode, 42)

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_native_compile_main_io_unit(self) -> None:
        src = """
        fn main() -> Unit !{IO} =
          print("hello")
        """
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            spr_path = tmp_path / "prog.spr"
            bin_path = tmp_path / "prog"
            spr_path.write_text(src, encoding="utf-8")
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "sprout.cli",
                    "compile",
                    str(spr_path),
                    "--native",
                    "-o",
                    str(bin_path),
                ],
                check=True,
            )
            run = subprocess.run([str(bin_path)], check=False, capture_output=True, text=True)
            self.assertEqual(run.stdout.strip(), "hello")
            self.assertEqual(run.returncode, 0)

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_native_string_builtins(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "stdlib").mkdir(parents=True)
            (tmp_path / "stdlib" / "internal_string.sprout").write_text(
                """
                module stdlib.internal_string
                export fn demo() -> String =
                  str_concat(str_slice("sprout-lang", 0, 6), "-ok")
                export fn demo_len_ok() -> Bool =
                  str_len("sprout-lang") == 11
                export fn demo_find_ok() -> Bool =
                  str_find("sprout-lang", "lang") == 7
                export fn demo_prefix_ok() -> Bool =
                  str_starts_with("sprout-lang", "sprout")
                """,
                encoding="utf-8",
            )
            spr_path = tmp_path / "prog.sprout"
            bin_path = tmp_path / "prog"
            spr_path.write_text(
                """
                module app.main
                import stdlib.internal_string (demo, demo_find_ok, demo_len_ok, demo_prefix_ok)

                fn seq(a: Unit !{IO}, b: Unit !{IO}) -> Unit !{IO} = b

                fn main() -> Unit !{IO} =
                  seq(
                    print(demo()),
                    seq(
                      print(demo_len_ok()),
                      seq(
                        print(demo_find_ok()),
                        print(demo_prefix_ok())
                      )
                    )
                  )
                """,
                encoding="utf-8",
            )
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "sprout.cli",
                    "compile",
                    str(spr_path),
                    "--native",
                    "-o",
                    str(bin_path),
                ],
                check=True,
            )
            run = subprocess.run([str(bin_path)], check=False, capture_output=True, text=True)
            self.assertEqual(run.stdout.strip(), "sprout-ok\n1\n1\n1")
            self.assertEqual(run.returncode, 0)

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_native_string_equality_uses_content_not_pointer_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            spr_path = tmp_path / "prog.sprout"
            bin_path = tmp_path / "prog"
            spr_path.write_text(
                """
                module app.main
                import stdlib.string (slice)

                fn seq(a: Unit !{IO}, b: Unit !{IO}) -> Unit !{IO} = b

                fn main() -> Unit !{IO} =
                  seq(
                    print(slice("sprout", 0, 3) == "spr"),
                    print(slice("sprout", 0, 3) != "out")
                  )
                """,
                encoding="utf-8",
            )
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "sprout.cli",
                    "compile",
                    str(spr_path),
                    "--native",
                    "-o",
                    str(bin_path),
                ],
                check=True,
            )
            run = subprocess.run([str(bin_path)], check=False, capture_output=True, text=True)
            self.assertEqual(run.stdout.strip(), "1\n1")
            self.assertEqual(run.returncode, 0)

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_native_string_lines_compiles_generic_vec_string_flow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            spr_path = tmp_path / "prog.sprout"
            bin_path = tmp_path / "prog"
            spr_path.write_text(
                """
                module app.main
                import stdlib.collections (vec_get_or)
                import stdlib.string (string_lines)

                fn main() -> Unit !{IO} =
                  print(vec_get_or(1, "missing", string_lines("alpha\\nbeta\\n")))
                """,
                encoding="utf-8",
            )
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "sprout.cli",
                    "compile",
                    str(spr_path),
                    "--native",
                    "-o",
                    str(bin_path),
                ],
                check=True,
            )
            run = subprocess.run([str(bin_path)], check=False, capture_output=True, text=True)
            self.assertEqual(run.stdout.strip(), "beta")
            self.assertEqual(run.returncode, 0)

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_native_env_get_builtin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            spr_path = tmp_path / "prog.sprout"
            bin_path = tmp_path / "prog"
            spr_path.write_text(
                """
                module main
                import stdlib.collections (Maybe)

                fn main() -> Unit !{IO} =
                  match env_get("SPROUT_TEST_ENV_GET") with
                  | Just value -> print(value)
                  | Nothing -> print("missing")
                """,
                encoding="utf-8",
            )
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "sprout.cli",
                    "compile",
                    str(spr_path),
                    "--native",
                    "-o",
                    str(bin_path),
                ],
                check=True,
            )
            run = subprocess.run(
                [str(bin_path)],
                check=False,
                capture_output=True,
                text=True,
                env={"SPROUT_TEST_ENV_GET": "native-env"},
            )
            self.assertEqual(run.stdout.strip(), "native-env")
            self.assertEqual(run.returncode, 0)

            run = subprocess.run([str(bin_path)], check=False, capture_output=True, text=True, env={})
            self.assertEqual(run.stdout.strip(), "missing")
            self.assertEqual(run.returncode, 0)

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_native_term_read_key_builtin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            spr_path = tmp_path / "prog.sprout"
            bin_path = tmp_path / "prog"
            spr_path.write_text(
                """
                module main

                fn main() -> Unit !{IO} =
                  print(term_read_key())
                """,
                encoding="utf-8",
            )
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "sprout.cli",
                    "compile",
                    str(spr_path),
                    "--native",
                    "-o",
                    str(bin_path),
                ],
                check=True,
            )
            run = subprocess.run([str(bin_path)], check=False, capture_output=True, text=True, input="j")
            self.assertEqual(run.stdout.strip(), "j")
            self.assertEqual(run.returncode, 0)

            run = subprocess.run([str(bin_path)], check=False, capture_output=True, text=True, input="\x04")
            self.assertEqual(run.stdout.strip(), "ctrl-d")
            self.assertEqual(run.returncode, 0)

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_native_term_read_line_builtin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            spr_path = tmp_path / "prog.sprout"
            bin_path = tmp_path / "prog"
            spr_path.write_text(
                """
                module main
                import stdlib.collections (Maybe)

                fn render(v: Maybe String) -> String =
                  match v with
                  | Just text -> text
                  | Nothing -> "eof"

                fn main() -> Unit !{IO} =
                  print(render(term_read_line()))
                """,
                encoding="utf-8",
            )
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "sprout.cli",
                    "compile",
                    str(spr_path),
                    "--native",
                    "-o",
                    str(bin_path),
                ],
                check=True,
            )
            run = subprocess.run([str(bin_path)], check=False, capture_output=True, text=True, input="native line\n")
            self.assertEqual(run.stdout.strip(), "native line")
            self.assertEqual(run.returncode, 0)

            run = subprocess.run([str(bin_path)], check=False, capture_output=True, text=True, input="")
            self.assertEqual(run.stdout.strip(), "eof")
            self.assertEqual(run.returncode, 0)

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_native_term_is_interactive_builtin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            spr_path = tmp_path / "prog.sprout"
            bin_path = tmp_path / "prog"
            spr_path.write_text(
                """
                module main
                fn main() -> Unit !{IO} =
                  print(term_is_interactive())
                """,
                encoding="utf-8",
            )
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "sprout.cli",
                    "compile",
                    str(spr_path),
                    "--native",
                    "-o",
                    str(bin_path),
                ],
                check=True,
            )
            run = subprocess.run([str(bin_path)], check=False, capture_output=True, text=True)
            self.assertEqual(run.returncode, 0)
            self.assertEqual(run.stdout.strip(), "0")

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_native_repl_service_builtin_reports_unsupported_backend(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            spr_path = tmp_path / "prog.sprout"
            bin_path = tmp_path / "prog"
            spr_path.write_text(
                """
                module main
                fn main() -> Unit !{IO} =
                  match repl_type_of("1") with
                  | Ok _ -> print("ok")
                  | Err message -> print(message)
                """,
                encoding="utf-8",
            )
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "sprout.cli",
                    "compile",
                    str(spr_path),
                    "--native",
                    "-o",
                    str(bin_path),
                ],
                check=True,
            )
            run = subprocess.run([str(bin_path)], check=False, capture_output=True, text=True)
            self.assertEqual(run.returncode, 1)
            self.assertEqual(run.stdout, "")
            self.assertIn("runtime error: builtin `repl_type_of`: not supported in native backend", run.stderr)

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_native_repl_complete_builtin_reports_unsupported_backend(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            spr_path = tmp_path / "prog.sprout"
            bin_path = tmp_path / "prog"
            spr_path.write_text(
                """
                module main
                fn main() -> Unit !{IO} =
                  match repl_complete("str") with
                  | (_, _) -> print("ok")
                """,
                encoding="utf-8",
            )
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "sprout.cli",
                    "compile",
                    str(spr_path),
                    "--native",
                    "-o",
                    str(bin_path),
                ],
                check=True,
            )
            run = subprocess.run([str(bin_path)], check=False, capture_output=True, text=True)
            self.assertEqual(run.returncode, 1)
            self.assertEqual(run.stdout, "")
            self.assertIn("runtime error: builtin `repl_complete`: not supported in native backend", run.stderr)

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_native_repl_complete_in_state_builtin_reports_unsupported_backend(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            spr_path = tmp_path / "prog.sprout"
            bin_path = tmp_path / "prog"
            spr_path.write_text(
                """
                module main
                import stdlib.collections (vec_empty)
                fn main() -> Unit !{IO} =
                  match repl_complete_in_state("str", vec_empty(), vec_empty()) with
                  | (prefix, _) -> print(prefix)
                """,
                encoding="utf-8",
            )
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "sprout.cli",
                    "compile",
                    str(spr_path),
                    "--native",
                    "-o",
                    str(bin_path),
                ],
                check=True,
            )
            run = subprocess.run([str(bin_path)], check=False, capture_output=True, text=True)
            self.assertEqual(run.returncode, 1)
            self.assertEqual(run.stdout, "")
            self.assertIn("runtime error: builtin `repl_complete_in_state`: not supported in native backend", run.stderr)

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_native_repl_type_of_in_source_builtin_reports_unsupported_backend(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            spr_path = tmp_path / "prog.sprout"
            bin_path = tmp_path / "prog"
            spr_path.write_text(
                """
                module main
                fn main() -> Unit !{IO} =
                  match repl_type_of_in_source("module app.repl", "1") with
                  | Ok _ -> print("ok")
                  | Err message -> print(message)
                """,
                encoding="utf-8",
            )
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "sprout.cli",
                    "compile",
                    str(spr_path),
                    "--native",
                    "-o",
                    str(bin_path),
                ],
                check=True,
            )
            run = subprocess.run([str(bin_path)], check=False, capture_output=True, text=True)
            self.assertEqual(run.returncode, 1)
            self.assertEqual(run.stdout, "")
            self.assertIn("runtime error: builtin `repl_type_of_in_source`: not supported in native backend", run.stderr)

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_native_repl_check_source_builtin_reports_unsupported_backend(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            spr_path = tmp_path / "prog.sprout"
            bin_path = tmp_path / "prog"
            spr_path.write_text(
                """
                module main
                fn main() -> Unit !{IO} =
                  match repl_check_source("module app.repl") with
                  | Ok _ -> print("ok")
                  | Err message -> print(message)
                """,
                encoding="utf-8",
            )
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "sprout.cli",
                    "compile",
                    str(spr_path),
                    "--native",
                    "-o",
                    str(bin_path),
                ],
                check=True,
            )
            run = subprocess.run([str(bin_path)], check=False, capture_output=True, text=True)
            self.assertEqual(run.returncode, 1)
            self.assertEqual(run.stdout, "")
            self.assertIn("runtime error: builtin `repl_check_source`: not supported in native backend", run.stderr)

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_native_repl_declared_names_in_source_builtin_reports_unsupported_backend(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            spr_path = tmp_path / "prog.sprout"
            bin_path = tmp_path / "prog"
            spr_path.write_text(
                """
                module main
                fn main() -> Unit !{IO} =
                  match repl_declared_names_in_source("module app.repl") with
                  | Ok _ -> print("ok")
                  | Err message -> print(message)
                """,
                encoding="utf-8",
            )
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "sprout.cli",
                    "compile",
                    str(spr_path),
                    "--native",
                    "-o",
                    str(bin_path),
                ],
                check=True,
            )
            run = subprocess.run([str(bin_path)], check=False, capture_output=True, text=True)
            self.assertEqual(run.returncode, 1)
            self.assertEqual(run.stdout, "")
            self.assertIn("runtime error: builtin `repl_declared_names_in_source`: not supported in native backend", run.stderr)

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_native_repl_exported_names_in_source_builtin_reports_unsupported_backend(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            spr_path = tmp_path / "prog.sprout"
            bin_path = tmp_path / "prog"
            spr_path.write_text(
                """
                module main
                fn main() -> Unit !{IO} =
                  match repl_exported_names_in_source("module app.lib") with
                  | Ok _ -> print("ok")
                  | Err message -> print(message)
                """,
                encoding="utf-8",
            )
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "sprout.cli",
                    "compile",
                    str(spr_path),
                    "--native",
                    "-o",
                    str(bin_path),
                ],
                check=True,
            )
            run = subprocess.run([str(bin_path)], check=False, capture_output=True, text=True)
            self.assertEqual(run.returncode, 1)
            self.assertEqual(run.stdout, "")
            self.assertIn("runtime error: builtin `repl_exported_names_in_source`: not supported in native backend", run.stderr)

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_native_repl_symbol_inventory_in_source_builtin_reports_unsupported_backend(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            spr_path = tmp_path / "prog.sprout"
            bin_path = tmp_path / "prog"
            spr_path.write_text(
                """
                module main
                fn main() -> Unit !{IO} =
                  match repl_symbol_inventory_in_source("module app.lib") with
                  | Ok _ -> print("ok")
                  | Err message -> print(message)
                """,
                encoding="utf-8",
            )
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "sprout.cli",
                    "compile",
                    str(spr_path),
                    "--native",
                    "-o",
                    str(bin_path),
                ],
                check=True,
            )
            run = subprocess.run([str(bin_path)], check=False, capture_output=True, text=True)
            self.assertEqual(run.returncode, 1)
            self.assertEqual(run.stdout, "")
            self.assertIn("runtime error: builtin `repl_symbol_inventory_in_source`: not supported in native backend", run.stderr)

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_native_repl_diagnostics_in_source_builtin_reports_unsupported_backend(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            spr_path = tmp_path / "prog.sprout"
            bin_path = tmp_path / "prog"
            spr_path.write_text(
                """
                module main
                fn main() -> Unit !{IO} =
                  print(vec_length(repl_diagnostics_in_source("module app.repl")))
                """,
                encoding="utf-8",
            )
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "sprout.cli",
                    "compile",
                    str(spr_path),
                    "--native",
                    "-o",
                    str(bin_path),
                ],
                check=True,
            )
            run = subprocess.run([str(bin_path)], check=False, capture_output=True, text=True)
            self.assertEqual(run.returncode, 1)
            self.assertEqual(run.stdout, "")
            self.assertIn("runtime error: builtin `repl_diagnostics_in_source`: not supported in native backend", run.stderr)

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_native_repl_eval_expr_in_source_builtin_reports_unsupported_backend(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            spr_path = tmp_path / "prog.sprout"
            bin_path = tmp_path / "prog"
            spr_path.write_text(
                """
                module main
                fn main() -> Unit !{IO} =
                  match repl_eval_expr_in_source("module app.repl", "1 + 1") with
                  | Ok _ -> print("ok")
                  | Err message -> print(message)
                """,
                encoding="utf-8",
            )
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "sprout.cli",
                    "compile",
                    str(spr_path),
                    "--native",
                    "-o",
                    str(bin_path),
                ],
                check=True,
            )
            run = subprocess.run([str(bin_path)], check=False, capture_output=True, text=True)
            self.assertEqual(run.returncode, 1)
            self.assertEqual(run.stdout, "")
            self.assertIn("runtime error: builtin `repl_eval_expr_in_source`: not supported in native backend", run.stderr)

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_native_repl_instances_in_source_builtin_reports_unsupported_backend(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            spr_path = tmp_path / "prog.sprout"
            bin_path = tmp_path / "prog"
            spr_path.write_text(
                """
                module main
                fn main() -> Unit !{IO} =
                  match repl_instances_in_source("module app.repl", "List Int") with
                  | Ok _ -> print("ok")
                  | Err message -> print(message)
                """,
                encoding="utf-8",
            )
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "sprout.cli",
                    "compile",
                    str(spr_path),
                    "--native",
                    "-o",
                    str(bin_path),
                ],
                check=True,
            )
            run = subprocess.run([str(bin_path)], check=False, capture_output=True, text=True)
            self.assertEqual(run.returncode, 1)
            self.assertEqual(run.stdout, "")
            self.assertIn("runtime error: builtin `repl_instances_in_source`: not supported in native backend", run.stderr)

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_native_bytes_helpers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            spr_path = tmp_path / "prog.sprout"
            bin_path = tmp_path / "prog"
            spr_path.write_text(
                """
                module main
                import stdlib.collections (Maybe)
                import stdlib.bytes (Result, Utf8Error, append, c_string, from_string, get, length, read_c_string, read_u16_be, read_u32_be, slice, to_string, u16_be, u32_be)

                fn int_or(value: Maybe Int, fallback: Int) -> Int =
                  match value with
                  | Just n -> n
                  | Nothing -> fallback

                fn seq(a: Unit !{IO}, b: Unit !{IO}) -> Unit !{IO} = b

                fn main() -> Unit !{IO} =
                  seq(
                    print(
                      int_or(get(slice(append(u16_be(258), u32_be(16909060)), 1, 4), 0), -1)
                      + int_or(read_u16_be(u16_be(258)), -10)
                      + int_or(read_u32_be(u32_be(16909060)), -100)
                      + length(append(u16_be(258), u32_be(16909060)))
                    ),
                    seq(
                      match to_string(from_string("zaż")) with
                      | Ok text -> print(text)
                      | Err _ -> print("bad"),
                      match read_c_string(c_string("ok")) with
                      | Ok text -> print(text)
                      | Err _ -> print("bad")
                    )
                  )
                """,
                encoding="utf-8",
            )
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "sprout.cli",
                    "compile",
                    str(spr_path),
                    "--native",
                    "-o",
                    str(bin_path),
                ],
                check=True,
            )
            run = subprocess.run([str(bin_path)], check=False, capture_output=True, text=True)
            self.assertEqual(run.stdout.strip(), "16909326\nzaż\nok")
            self.assertEqual(run.returncode, 0)

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_native_bytes_builder_helpers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            spr_path = tmp_path / "prog.sprout"
            bin_path = tmp_path / "prog"
            spr_path.write_text(
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
                  | Ok text -> print(length(builder_build(builder_empty())) + score(text))
                  | Err _ -> print(0)
                """,
                encoding="utf-8",
            )
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "sprout.cli",
                    "compile",
                    str(spr_path),
                    "--native",
                    "-o",
                    str(bin_path),
                ],
                check=True,
            )
            run = subprocess.run([str(bin_path)], check=False, capture_output=True, text=True)
            self.assertEqual(run.stdout.strip(), "1")
            self.assertEqual(run.returncode, 0)

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_native_debug_alloc_report_is_opt_in(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            ints_path = tmp_path / "ints.txt"
            spr_path = tmp_path / "prog.sprout"
            bin_path = tmp_path / "prog"
            ints_path.write_text("10\n20\n", encoding="utf-8")
            spr_path.write_text(
                f"""
                module main
                import stdlib.bytes (from_string, length)
                import stdlib.collections (dict_empty, dict_keys, dict_remove, dict_set, vec_length)
                import stdlib.crypto as crypto

                fn force(x: a, y: b) -> b = y

                fn map_score() -> Int =
                  vec_length(
                    dict_keys(
                      dict_remove(
                        "a",
                        dict_set("b", 2, dict_set("a", 1, dict_empty()))
                      )
                    )
                  )

                fn score_decode() -> Int =
                  match crypto.base64_decode("c3Byb3V0") with
                  | Ok decoded -> length(decoded)
                  | Err _ -> 0

                fn score_xor() -> Int =
                  match crypto.bytes_xor(from_string("abc"), from_string("ABC")) with
                  | Ok xored -> length(xored)
                  | Err _ -> 0

                fn score_random() -> Int !{{IO}} =
                  match crypto.random_bytes(0) with
                  | Ok nonce -> length(nonce)
                  | Err _ -> 0

                fn main() -> Int !{{IO}} =
                  force(
                    read_int_lines("{ints_path.as_posix()}"),
                    print_int(map_score() + score_decode() + score_xor() + score_random())
                  )
                """,
                encoding="utf-8",
            )
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "sprout.cli",
                    "compile",
                    str(spr_path),
                    "--native",
                    "-o",
                    str(bin_path),
                ],
                check=True,
            )

            default_run = subprocess.run([str(bin_path)], check=False, capture_output=True, text=True)
            self.assertEqual(default_run.stdout.strip(), "10")
            self.assertEqual(default_run.stderr, "")
            self.assertEqual(default_run.returncode, 10)

            debug_env = os.environ.copy()
            debug_env["SPROUT_DEBUG_ALLOC"] = "1"
            debug_run = subprocess.run(
                [str(bin_path)],
                check=False,
                capture_output=True,
                text=True,
                env=debug_env,
            )
            self.assertEqual(debug_run.stdout.strip(), "10")
            self.assertEqual(debug_run.returncode, 10)
            match = re.search(
                r"\[sprout alloc\] sprout_obj=(\d+) closure=(\d+) vector=(\d+) map=(\d+) bytes=(\d+) builder=(\d+) gc_swept=(\d+)",
                debug_run.stderr,
            )
            self.assertIsNotNone(match)
            assert match is not None
            self.assertGreater(int(match.group(1)), 0)
            self.assertEqual(int(match.group(2)), 0)
            self.assertEqual(int(match.group(3)), 5)
            self.assertEqual(int(match.group(4)), 12)
            self.assertEqual(int(match.group(5)), 9)
            self.assertEqual(int(match.group(6)), 0)
            self.assertGreater(int(match.group(7)), 0)

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_native_crypto_helpers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            spr_path = tmp_path / "prog.sprout"
            bin_path = tmp_path / "prog"
            spr_path.write_text(
                """
                module main
                import stdlib.bytes (from_string, length)
                import stdlib.crypto as crypto
                import stdlib.string as string

                fn score_decode() -> Int =
                  match crypto.base64_decode("c3Byb3V0") with
                  | Ok decoded -> string.length(crypto.base64_encode(crypto.sha256(decoded)))
                  | Err _ -> 0

                fn score_xor() -> Int =
                  match crypto.bytes_xor(from_string("abc"), from_string("ABC")) with
                  | Ok xored ->
                      string.length(crypto.base64_encode(crypto.hmac_sha256(from_string("key"), from_string("sprout"))))
                      + string.length(crypto.base64_encode(xored))
                  | Err _ -> 0

                fn score_random() -> Int !{IO} =
                  match crypto.random_bytes(0) with
                  | Ok nonce -> length(nonce)
                  | Err _ -> 0

                fn main() -> Unit !{IO} =
                  print(score_decode() + score_xor() + score_random())
                """,
                encoding="utf-8",
            )
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "sprout.cli",
                    "compile",
                    str(spr_path),
                    "--native",
                    "-o",
                    str(bin_path),
                ],
                check=True,
            )
            run = subprocess.run([str(bin_path)], check=False, capture_output=True, text=True)
            self.assertEqual(run.stdout.strip(), "92")
            self.assertEqual(run.returncode, 0)

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_native_runtime_builtin_failure_uses_runtime_error_convention(self) -> None:
        src = """
        fn main() -> Unit !{IO} =
          tcp_close(1)
        """
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            spr_path = tmp_path / "prog.spr"
            bin_path = tmp_path / "prog"
            spr_path.write_text(src, encoding="utf-8")
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "sprout.cli",
                    "compile",
                    str(spr_path),
                    "--native",
                    "-o",
                    str(bin_path),
                ],
                check=True,
            )
            run = subprocess.run([str(bin_path)], check=False, capture_output=True, text=True)
            self.assertEqual(run.returncode, 1)
            self.assertIn("runtime error: builtin `tcp_close`: unknown connection handle", run.stderr)

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_native_compile_adt_match(self) -> None:
        src = """
        type MaybeInt =
          | Just Int
          | Nothing

        fn unwrap(m: MaybeInt) -> Int =
          match m with
          | Just x -> x
          | Nothing -> 0

        fn main() -> Int !{IO} =
          print_int(unwrap(Just(42)))
        """
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            spr_path = tmp_path / "prog.spr"
            bin_path = tmp_path / "prog"
            spr_path.write_text(src, encoding="utf-8")
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "sprout.cli",
                    "compile",
                    str(spr_path),
                    "--native",
                    "-o",
                    str(bin_path),
                ],
                check=True,
            )
            run = subprocess.run([str(bin_path)], check=False, capture_output=True, text=True)
            self.assertEqual(run.stdout.strip(), "42")
            self.assertEqual(run.returncode, 42)

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_native_print_adt_value(self) -> None:
        src = """
        type Pair =
          | Pair Int Int

        fn main() -> Unit !{IO} =
          print(Pair(3, 6))
        """
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            spr_path = tmp_path / "prog.spr"
            bin_path = tmp_path / "prog"
            spr_path.write_text(src, encoding="utf-8")
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "sprout.cli",
                    "compile",
                    str(spr_path),
                    "--native",
                    "-o",
                    str(bin_path),
                ],
                check=True,
            )
            run = subprocess.run([str(bin_path)], check=False, capture_output=True, text=True)
            self.assertEqual(run.stdout.strip(), "Pair(3, 6)")
            self.assertEqual(run.returncode, 0)

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_native_compile_higher_order(self) -> None:
        src = """
        fn inc(x: Int) -> Int = x + 1
        fn apply(x: Int, f: Int -> Int) -> Int = f(x)
        fn main() -> Int !{IO} = print_int(apply(41, inc))
        """
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            spr_path = tmp_path / "prog.spr"
            bin_path = tmp_path / "prog"
            spr_path.write_text(src, encoding="utf-8")
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "sprout.cli",
                    "compile",
                    str(spr_path),
                    "--native",
                    "-o",
                    str(bin_path),
                ],
                check=True,
            )
            run = subprocess.run([str(bin_path)], check=False, capture_output=True, text=True)
            self.assertEqual(run.stdout.strip(), "42")
            self.assertEqual(run.returncode, 42)

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_native_compile_effect_polymorphic_higher_order(self) -> None:
        src = """
        fn apply_twice(f: Int -> Int !{e}, x: Int) -> Int !{e} =
          f(f(x))

        fn show(x: Int) -> Int !{IO} =
          print_int(x)

        fn main() -> Int !{IO} =
          apply_twice(show, 1)
        """
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            spr_path = tmp_path / "prog.spr"
            bin_path = tmp_path / "prog"
            spr_path.write_text(src, encoding="utf-8")
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "sprout.cli",
                    "compile",
                    str(spr_path),
                    "--native",
                    "-o",
                    str(bin_path),
                ],
                check=True,
            )
            run = subprocess.run([str(bin_path)], check=False, capture_output=True, text=True)
            self.assertEqual(run.stdout, "1\n1\n")
            self.assertEqual(run.returncode, 1)

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_native_compile_lambda_closure(self) -> None:
        src = r"""
        fn make_adder(base: Int) -> Int -> Int =
          \(x) -> base + x

        fn main() -> Int !{IO} =
          print_int(make_adder(40)(2))
        """
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            spr_path = tmp_path / "prog.spr"
            bin_path = tmp_path / "prog"
            spr_path.write_text(src, encoding="utf-8")
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "sprout.cli",
                    "compile",
                    str(spr_path),
                    "--native",
                    "-o",
                    str(bin_path),
                ],
                check=True,
            )
            run = subprocess.run([str(bin_path)], check=False, capture_output=True, text=True)
            self.assertEqual(run.stdout.strip(), "42")
            self.assertEqual(run.returncode, 42)

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_native_debug_alloc_report_counts_closures(self) -> None:
        src = r"""
        fn make_adder(base: Int) -> Int -> Int =
          \(x) -> base + x

        fn main() -> Int !{IO} =
          print_int(make_adder(40)(2))
        """
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            spr_path = tmp_path / "prog.spr"
            bin_path = tmp_path / "prog"
            spr_path.write_text(src, encoding="utf-8")
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "sprout.cli",
                    "compile",
                    str(spr_path),
                    "--native",
                    "-o",
                    str(bin_path),
                ],
                check=True,
            )
            env = os.environ.copy()
            env["SPROUT_DEBUG_ALLOC"] = "1"
            run = subprocess.run([str(bin_path)], check=False, capture_output=True, text=True, env=env)
            self.assertEqual(run.stdout.strip(), "42")
            self.assertEqual(run.returncode, 42)
            match = re.search(r"closure=(\d+).*gc_swept=(\d+)", run.stderr)
            self.assertIsNotNone(match)
            assert match is not None
            self.assertGreater(int(match.group(1)), 0)
            self.assertGreater(int(match.group(2)), 0)

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_native_debug_gc_logs_exit_collection(self) -> None:
        src = """
        type Box =
          | Box Int

        fn make_box(x: Int) -> Box =
          Box(x)

        fn main() -> Int !{IO} =
          match make_box(42) with
          | Box(x) -> print_int(x)
        """
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            spr_path = tmp_path / "prog.spr"
            bin_path = tmp_path / "prog"
            spr_path.write_text(src, encoding="utf-8")
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "sprout.cli",
                    "compile",
                    str(spr_path),
                    "--native",
                    "-o",
                    str(bin_path),
                ],
                check=True,
            )
            env = os.environ.copy()
            env["SPROUT_DEBUG_GC"] = "1"
            run = subprocess.run([str(bin_path)], check=False, capture_output=True, text=True, env=env)
            self.assertEqual(run.stdout.strip(), "42")
            self.assertEqual(run.returncode, 42)
            self.assertRegex(
                run.stderr,
                r"\[sprout gc\] cycle=\d+ reason=atexit threshold=\d+ heap_before=\d+ heap_after=\d+ swept=\d+",
            )

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_native_gc_threshold_collects_during_execution_and_preserves_live_values(self) -> None:
        src = """
        module main
        import stdlib.bytes (from_string, length)
        import stdlib.crypto as crypto

        fn main() -> Unit !{IO} =
          match crypto.bytes_xor(from_string("abc"), from_string("ABC")) with
          | Ok out -> print(length(out))
          | Err _ -> print(0)
        """
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            spr_path = tmp_path / "prog.sprout"
            bin_path = tmp_path / "prog"
            spr_path.write_text(src, encoding="utf-8")
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "sprout.cli",
                    "compile",
                    str(spr_path),
                    "--native",
                    "-o",
                    str(bin_path),
                ],
                check=True,
            )
            env = os.environ.copy()
            env["SPROUT_DEBUG_GC"] = "1"
            env["SPROUT_GC_THRESHOLD"] = "1"
            run = subprocess.run([str(bin_path)], check=False, capture_output=True, text=True, env=env)
            self.assertEqual(run.stdout.strip(), "3")
            self.assertEqual(run.returncode, 0)
            self.assertIn("reason=threshold threshold=1", run.stderr)
            self.assertIn("reason=atexit threshold=1", run.stderr)

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_native_gc_default_threshold_collects_during_execution(self) -> None:
        src = """
        module main
        import stdlib.bytes (from_string, length)
        import stdlib.crypto as crypto

        fn churn(n: Int, acc: Int) -> Int =
          if n == 0 then acc else
            match crypto.bytes_xor(from_string("abc"), from_string("ABC")) with
            | Ok out -> churn(n - 1, acc + length(out))
            | Err _ -> acc

        fn main() -> Int !{IO} =
          print_int(churn(400, 0))
        """
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            spr_path = tmp_path / "prog.sprout"
            bin_path = tmp_path / "prog"
            spr_path.write_text(src, encoding="utf-8")
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "sprout.cli",
                    "compile",
                    str(spr_path),
                    "--native",
                    "-o",
                    str(bin_path),
                ],
                check=True,
            )
            env = os.environ.copy()
            env["SPROUT_DEBUG_GC"] = "1"
            run = subprocess.run([str(bin_path)], check=False, capture_output=True, text=True, env=env)
            self.assertEqual(run.stdout.strip(), "1200")
            self.assertEqual(run.returncode, 176)
            self.assertIn("reason=threshold threshold=1024", run.stderr)

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_native_gc_threshold_off_disables_mid_execution_collection(self) -> None:
        src = """
        module main
        import stdlib.bytes (from_string, length)
        import stdlib.crypto as crypto

        fn churn(n: Int, acc: Int) -> Int =
          if n == 0 then acc else
            match crypto.bytes_xor(from_string("abc"), from_string("ABC")) with
            | Ok out -> churn(n - 1, acc + length(out))
            | Err _ -> acc

        fn main() -> Int !{IO} =
          print_int(churn(400, 0))
        """
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            spr_path = tmp_path / "prog.sprout"
            bin_path = tmp_path / "prog"
            spr_path.write_text(src, encoding="utf-8")
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "sprout.cli",
                    "compile",
                    str(spr_path),
                    "--native",
                    "-o",
                    str(bin_path),
                ],
                check=True,
            )
            env = os.environ.copy()
            env["SPROUT_DEBUG_GC"] = "1"
            env["SPROUT_GC_THRESHOLD"] = "off"
            run = subprocess.run([str(bin_path)], check=False, capture_output=True, text=True, env=env)
            self.assertEqual(run.stdout.strip(), "1200")
            self.assertEqual(run.returncode, 176)
            self.assertNotIn("reason=threshold", run.stderr)
            self.assertIn("reason=atexit threshold=0", run.stderr)

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_native_gc_threshold_preserves_live_vector_flow(self) -> None:
        src = """
        module main
        import stdlib.collections (Vec, vec_append, vec_empty, vec_get_or, vec_length)

        fn build(n: Int, acc: Vec Int) -> Vec Int =
          if n == 0 then acc else build(n - 1, vec_append(n, acc))

        fn score(vec: Vec Int) -> Int =
          vec_length(vec) + vec_get_or(0, 0, vec)

        fn main() -> Int !{IO} =
          print_int(score(build(200, vec_empty())))
        """
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            spr_path = tmp_path / "prog.sprout"
            bin_path = tmp_path / "prog"
            spr_path.write_text(src, encoding="utf-8")
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "sprout.cli",
                    "compile",
                    str(spr_path),
                    "--native",
                    "-o",
                    str(bin_path),
                ],
                check=True,
            )
            env = os.environ.copy()
            env["SPROUT_DEBUG_GC"] = "1"
            env["SPROUT_GC_THRESHOLD"] = "1"
            run = subprocess.run([str(bin_path)], check=False, capture_output=True, text=True, env=env)
            self.assertEqual(run.stdout.strip(), "400")
            self.assertEqual(run.returncode, 144)
            self.assertIn("reason=threshold threshold=1", run.stderr)

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_native_gc_threshold_preserves_live_builder_flow(self) -> None:
        src = """
        module main
        import stdlib.bytes (Builder, builder_append, builder_build, builder_byte, builder_empty, length)

        fn build(n: Int, acc: Builder) -> Builder =
          if n == 0 then acc else build(n - 1, builder_append(acc, builder_byte(65)))

        fn main() -> Int !{IO} =
          print_int(length(builder_build(build(64, builder_empty()))))
        """
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            spr_path = tmp_path / "prog.sprout"
            bin_path = tmp_path / "prog"
            spr_path.write_text(src, encoding="utf-8")
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "sprout.cli",
                    "compile",
                    str(spr_path),
                    "--native",
                    "-o",
                    str(bin_path),
                ],
                check=True,
            )
            env = os.environ.copy()
            env["SPROUT_DEBUG_GC"] = "1"
            env["SPROUT_GC_THRESHOLD"] = "1"
            run = subprocess.run([str(bin_path)], check=False, capture_output=True, text=True, env=env)
            self.assertEqual(run.stdout.strip(), "64")
            self.assertEqual(run.returncode, 64)
            self.assertIn("reason=threshold threshold=1", run.stderr)

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_native_gc_threshold_preserves_direct_call_argument_values(self) -> None:
        src = """
        module main
        import stdlib.bytes (from_string, length)
        import stdlib.crypto as crypto

        fn keep(left: Bytes, n: Int) -> Int =
          length(left) + n

        fn churn(n: Int) -> Int =
          if n == 0 then 7 else
            match crypto.bytes_xor(from_string("abc"), from_string("ABC")) with
            | Ok _ -> churn(n - 1)
            | Err _ -> 0

        fn main() -> Int !{IO} =
          print_int(keep(from_string("abc"), churn(32)))
        """
        with compiled_native_binary(self, src) as bin_path:
            env = os.environ.copy()
            env["SPROUT_DEBUG_GC"] = "1"
            env["SPROUT_GC_THRESHOLD"] = "1"
            run = subprocess.run([str(bin_path)], check=False, capture_output=True, text=True, env=env)
        self.assertEqual(run.stdout.strip(), "10")
        self.assertEqual(run.returncode, 10)
        self.assertIn("reason=threshold threshold=1", run.stderr)

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_native_gc_threshold_preserves_local_function_call_argument_values(self) -> None:
        src = """
        module main
        import stdlib.bytes (from_string, length)
        import stdlib.crypto as crypto

        fn keep(left: Bytes, n: Int) -> Int =
          length(left) + n

        fn apply(f: Bytes -> Int -> Int) -> Int =
          f(from_string("abc"), churn(32))

        fn churn(n: Int) -> Int =
          if n == 0 then 7 else
            match crypto.bytes_xor(from_string("abc"), from_string("ABC")) with
            | Ok _ -> churn(n - 1)
            | Err _ -> 0

        fn main() -> Int !{IO} =
          print_int(apply(keep))
        """
        with compiled_native_binary(self, src) as bin_path:
            env = os.environ.copy()
            env["SPROUT_DEBUG_GC"] = "1"
            env["SPROUT_GC_THRESHOLD"] = "1"
            run = subprocess.run([str(bin_path)], check=False, capture_output=True, text=True, env=env)
        self.assertEqual(run.stdout.strip(), "10")
        self.assertEqual(run.returncode, 10)
        self.assertIn("reason=threshold threshold=1", run.stderr)

    def test_runtime_managed_bytes_error_paths_do_not_manually_free_gc_objects(self) -> None:
        runtime_src = Path(sprout_cli.__file__).read_text(encoding="utf-8")
        random_bytes_body = re.search(
            r"long long crypto_random_bytes\(long long count\) \{.*?^}",
            runtime_src,
            re.MULTILINE | re.DOTALL,
        )
        self.assertIsNotNone(random_bytes_body)
        assert random_bytes_body is not None
        self.assertNotIn("free(out->data);", random_bytes_body.group(0))
        self.assertNotIn("free(out);", random_bytes_body.group(0))

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_native_tcp_read_exact_eof_survives_exit_gc(self) -> None:
        src = """
        module main
        import stdlib.bytes (length)
        import stdlib.net (TcpError, close, connect, read_exact, tcp_error_message)

        fn seq(a: Unit !{IO}, b: Unit !{IO}) -> Unit !{IO} = b

        fn main() -> Unit !{IO} =
          match connect("127.0.0.1", PORT) with
          | Err err -> print(tcp_error_message(err))
          | Ok conn ->
              match read_exact(conn, 4) with
              | Ok payload -> seq(close(conn), print(length(payload)))
              | Err TcpEndOfStream -> seq(close(conn), print("eof"))
              | Err err -> seq(close(conn), print(tcp_error_message(err)))
        """

        def handle(conn) -> None:
            conn.sendall(b"hi")

        with running_tcp_fixture(self, handle) as port:
            source = src.replace("PORT", str(port))
            with compiled_native_binary(self, source) as bin_path:
                env = os.environ.copy()
                env["SPROUT_DEBUG_GC"] = "1"
                env["SPROUT_GC_THRESHOLD"] = "1"
                run = subprocess.run([str(bin_path)], check=False, capture_output=True, text=True, env=env)
        self.assertEqual(run.stdout.strip(), "eof")
        self.assertEqual(run.returncode, 0, msg=run.stderr)
        self.assertIn("reason=atexit threshold=1", run.stderr)

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_native_tuple_global_root_keeps_children_live_at_exit(self) -> None:
        src = """
        type Box =
          | Box Int

        let pair = (Box(1), Box(2))

        fn main() -> Int !{IO} =
          match pair with
          | (Box(x), Box(y)) -> print_int(x + y)
        """
        program = parse(src)
        typecheck_program(program)
        llvm_ir = compile_to_llvm(program)
        self.assertIn("call i64 @sprout_gc_register_scan_root(ptr @pair", llvm_ir)
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            spr_path = tmp_path / "prog.spr"
            bin_path = tmp_path / "prog"
            spr_path.write_text(src, encoding="utf-8")
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "sprout.cli",
                    "compile",
                    str(spr_path),
                    "--native",
                    "-o",
                    str(bin_path),
                ],
                check=True,
            )
            env = os.environ.copy()
            env["SPROUT_DEBUG_ALLOC"] = "1"
            env["SPROUT_DEBUG_GC"] = "1"
            run = subprocess.run([str(bin_path)], check=False, capture_output=True, text=True, env=env)
            self.assertEqual(run.stdout.strip(), "3")
            self.assertEqual(run.returncode, 3)
            self.assertIn("reason=atexit", run.stderr)
            alloc_match = re.search(r"gc_swept=(\d+)", run.stderr)
            self.assertIsNotNone(alloc_match)
            assert alloc_match is not None
            self.assertEqual(int(alloc_match.group(1)), 0)

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_native_compile_partial_application_of_named_function(self) -> None:
        src = """
        fn add(x: Int, y: Int) -> Int = x + y
        let inc = add(1)

        fn main() -> Int !{IO} =
          print_int(inc(41))
        """
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            spr_path = tmp_path / "prog.spr"
            bin_path = tmp_path / "prog"
            spr_path.write_text(src, encoding="utf-8")
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "sprout.cli",
                    "compile",
                    str(spr_path),
                    "--native",
                    "-o",
                    str(bin_path),
                ],
                check=True,
            )
            run = subprocess.run([str(bin_path)], check=False, capture_output=True, text=True)
            self.assertEqual(run.stdout.strip(), "42")
            self.assertEqual(run.returncode, 42)

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_native_json_stringify(self) -> None:
        src = """
        fn sample() -> Json =
          JsonObject(
            JsonObjectCons(
              "ok",
              JsonBool(true),
              JsonObjectCons(
                "items",
                JsonArray(JsonArrayCons(JsonInt(2), JsonArrayCons(JsonString("x\\n"), JsonArrayNil))),
                JsonObjectNil
              )
            )
          )

        fn main() -> Unit !{IO} =
          print(json_stringify(sample()))
        """
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            spr_path = tmp_path / "prog.sprout"
            bin_path = tmp_path / "prog"
            spr_path.write_text(src, encoding="utf-8")
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "sprout.cli",
                    "compile",
                    "--with-http-stdlib",
                    str(spr_path),
                    "--native",
                    "-o",
                    str(bin_path),
                ],
                check=True,
            )
            run = subprocess.run([str(bin_path)], check=False, capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, msg=run.stderr)
            self.assertEqual(run.stdout.strip(), '{"ok":true,"items":[2,"x\\n"]}')


if __name__ == "__main__":
    unittest.main()
