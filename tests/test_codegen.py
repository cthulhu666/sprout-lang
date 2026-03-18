from __future__ import annotations

import unittest
import shutil
import subprocess
import tempfile
from pathlib import Path
import sys

from sprout import CodegenError, compile_to_llvm, parse, typecheck_program
from sprout.module_loader import load_module_bundle, resolve_program_names
from sprout.stdlib import with_http_prelude


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
        fn main() -> Int = print_int(base + two)
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
        fn main() -> Int = print_int(x)
        """
        program = parse(src)
        typecheck_program(program)
        ir = compile_to_llvm(program)
        self.assertIn("@x = global i64 0", ir)
        self.assertIn("define void @__sprout_init_globals()", ir)
        self.assertIn("store i64", ir)

    def test_compile_with_print_int_external(self) -> None:
        src = """
        fn main() -> Int =
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
        fn main() -> IO Unit =
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

        fn main() -> Int =
          print_int(unwrap(Just(42)))
        """
        program = parse(src)
        typecheck_program(program)
        ir = compile_to_llvm(program)
        self.assertIn("call i64 @sprout_make1(i64 0, i64 42)", ir)
        self.assertIn("call i64 @sprout_tag", ir)
        self.assertIn("call i64 @sprout_field", ir)

    def test_compile_direct_constructor_match_avoids_materializing_maybe(self) -> None:
        src = """
        type MaybeInt =
          | Just Int
          | Nothing

        fn main() -> Int =
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

        fn main() -> Int =
          match if true then Just(42) else Nothing with
          | value -> print_int(unwrap(value))
        """
        program = parse(src)
        typecheck_program(program)
        ir = compile_to_llvm(program)
        self.assertIn("call i64 @sprout_make1(i64 0, i64 42)", ir)
        self.assertIn("call i64 @sprout_make0(i64 1)", ir)
        self.assertIn("call i64 @sprout_tag", ir)

    def test_compile_print_adt_value(self) -> None:
        src = """
        type Pair =
          | Pair Int Int

        fn main() -> IO Unit =
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
        fn main() -> IO Unit =
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

    def test_compile_generic_identity_erased(self) -> None:
        src = """
        fn id(x: a) -> a = x
        fn main() -> Int = print_int(id(42))
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
        fn main() -> Int = print_int(apply(41, inc))
        """
        program = parse(src)
        typecheck_program(program)
        ir = compile_to_llvm(program)
        self.assertIn("define i64 @apply(i64 %x, ptr %f)", ir)
        self.assertIn("call ptr @malloc(i64 %t", ir)
        self.assertIn("load ptr, ptr %f", ir)

    def test_compile_lambda_with_capture_to_llvm(self) -> None:
        src = r"""
        fn make_adder(base: Int) -> Int -> Int =
          \(x) -> base + x

        fn main() -> Int =
          print_int(make_adder(41)(1))
        """
        program = parse(src)
        typecheck_program(program)
        ir = compile_to_llvm(program)
        self.assertIn("define ptr @__sprout_fn_closure_", ir)
        self.assertIn("define i64 @__sprout_lambda_", ir)
        self.assertIn("call ptr @malloc(i64 %t", ir)
        self.assertIn("getelementptr i64, ptr %env, i64 1", ir)

    def test_compile_runtime_top_level_function_value_let_to_llvm(self) -> None:
        src = r"""
        fn inc(x: Int) -> Int = x + 1
        let f = inc
        let g = \(x: Int) -> x + 2

        fn main() -> Int =
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

        fn main() -> Int =
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

        fn main() -> Int =
          print_int(outer()(true)(41))
        """
        program = parse(src)
        typecheck_program(program)
        ir = compile_to_llvm(program)
        self.assertIn("define ptr @__sprout_lambda_", ir)
        self.assertIn("call ptr @malloc(i64 %t", ir)
        self.assertIn("@__sprout_fn_closure_", ir)

    def test_compile_tuple_match_to_llvm(self) -> None:
        src = """
        fn sum_pair(pair: (Int, Int)) -> Int =
          match pair with
          | (x, y) -> x + y

        fn main() -> Int =
          print_int(sum_pair((20, 22)))
        """
        program = parse(src)
        typecheck_program(program)
        ir = compile_to_llvm(program)
        self.assertIn("define i64 @sum_pair({ i64, i64 } %pair)", ir)
        self.assertIn("extractvalue { i64, i64 } %pair, 0", ir)
        self.assertIn("extractvalue { i64, i64 } %pair, 1", ir)

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_native_compile_tuple_match(self) -> None:
        src = """
        fn sum_pair(pair: (Int, Int)) -> Int =
          match pair with
          | (x, y) -> x + y

        fn main() -> Int =
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
    def test_native_print_tuple_value(self) -> None:
        src = """
        fn main() -> IO Unit =
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

    def test_compile_tcp_builtins_to_llvm(self) -> None:
        src = """
        fn seq(a: IO Unit, b: IO Unit) -> IO Unit = b
        fn main() -> IO Unit =
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

                fn main() -> IO Unit =
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

                fn main() -> IO Unit =
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

                fn main() -> IO Unit =
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

                fn main() -> IO Unit =
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

                fn score_random() -> Int =
                  match crypto.random_bytes(0) with
                  | Ok nonce -> length(nonce)
                  | Err _ -> 0

                fn main() -> IO Unit =
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

    def test_compile_string_builtins_to_llvm(self) -> None:
        src = """
        fn main() -> IO Unit =
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
        fn main() -> IO Unit =
          print(str_slice("sprout", 0, 3) == "spr")
        """
        program = parse(src)
        typecheck_program(program)
        ir = compile_to_llvm(program)
        self.assertIn("call i1 @str_eq(", ir)
        self.assertNotIn("icmp eq ptr", ir)

    def test_compile_http_request_to_llvm(self) -> None:
        src = """
        fn main() -> IO Unit =
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
        fn main() -> IO Unit =
          print(json_stringify(JsonArray(JsonArrayCons(JsonInt(1), JsonArrayNil))))
        """
        program = parse(with_http_prelude(src))
        typecheck_program(program)
        ir = compile_to_llvm(program)
        self.assertIn("declare ptr @json_stringify(i64)", ir)

    def test_compile_http_prelude_registers_qualified_runtime_ctors(self) -> None:
        src = """
        fn main() -> IO Unit =
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
        fn main() -> IO Unit =
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
        fn main() -> Int =
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

        fn main() -> Int =
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

        fn main() -> IO Unit =
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

        fn main() -> IO Unit =
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
        fn side() -> Bool =
          print_int(1) == 1

        fn main() -> Int =
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
        fn main() -> IO Unit =
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

                fn seq(a: IO Unit, b: IO Unit) -> IO Unit = b

                fn main() -> IO Unit =
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

                fn seq(a: IO Unit, b: IO Unit) -> IO Unit = b

                fn main() -> IO Unit =
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

                fn main() -> IO Unit =
                  print(vec_get_or(string_lines("alpha\\nbeta\\n"), 1, "missing"))
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

                fn main() -> IO Unit =
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

                fn seq(a: IO Unit, b: IO Unit) -> IO Unit = b

                fn main() -> IO Unit =
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

                fn main() -> IO Unit =
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

                fn score_random() -> Int =
                  match crypto.random_bytes(0) with
                  | Ok nonce -> length(nonce)
                  | Err _ -> 0

                fn main() -> IO Unit =
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
        fn main() -> IO Unit =
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

        fn main() -> Int =
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

        fn main() -> IO Unit =
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
        fn main() -> Int = print_int(apply(41, inc))
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
    def test_native_compile_lambda_closure(self) -> None:
        src = r"""
        fn make_adder(base: Int) -> Int -> Int =
          \(x) -> base + x

        fn main() -> Int =
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

        fn main() -> IO Unit =
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
