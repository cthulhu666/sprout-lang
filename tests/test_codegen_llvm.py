from __future__ import annotations

import unittest

from tests.codegen_test_support import *
from sprout.stdlib import with_prelude


class CodegenLlvmTests(CodegenTestCase):
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
        self.assertIn("define i32 @main(i32 %argc, ptr %argv)", ir)
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

    def test_compile_function_with_local_where_to_llvm(self) -> None:
        src = """
        fn score(n: Int) -> Int =
          x + y
        where
          x = n + 1
          y = x * 2

        fn main() -> Int =
          score(5)
        """
        program = parse(src)
        typecheck_program(program)
        ir = compile_to_llvm(program)
        self.assertIn("define i64 @score(i64 %n)", ir)
        self.assertIn("__sprout_lambda_", ir)

    def test_compile_function_with_local_where_tuple_destructuring_to_llvm(self) -> None:
        src = """
        fn pair(n: Int) -> (Int, Int) =
          (n + 1, n * 2)

        fn score(n: Int) -> Int =
          left + right
        where
          (left, right) = pair(n)

        fn main() -> Int =
          score(5)
        """
        program = parse(src)
        typecheck_program(program)
        ir = compile_to_llvm(program)
        self.assertIn("define i64 @score(i64 %n)", ir)
        self.assertIn("__sprout_lambda_", ir)
        self.assertIn("match_branch", ir)

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

    def test_compile_do_notation_program_to_llvm(self) -> None:
        src = """
        fn pair_sum(left: Maybe Int, right: Maybe Int) -> Maybe Int =
          do
            a <- left
            b <- right
            Just(a + b)

        fn main() -> Int =
          match pair_sum(Just(5), Just(7)) with
          | Just value -> value
          | Nothing -> 0
        """
        program = parse(with_prelude(src))
        typecheck_program(program)
        ir = compile_to_llvm(program)
        self.assertIn("define i64 @pair_sum(i64 %left, i64 %right)", ir)
        self.assertIn("define i32 @main(i32 %argc, ptr %argv)", ir)

    def test_compile_int_range_helpers_to_llvm(self) -> None:
        src = """
        fn main() -> Int !{IO} =
          print_int(range_count(1..3))
        """
        program = parse(with_prelude(src))
        typecheck_program(program)
        ir = compile_to_llvm(program)
        self.assertIn("declare i64 @int_range(i64, i64)", ir)
        self.assertIn("declare i64 @int_range_start(i64)", ir)
        self.assertIn("declare i64 @int_range_end(i64)", ir)

    def test_compile_main_io_unit_with_print_string(self) -> None:
        src = """
        fn main() -> Unit !{IO} =
          print("hello")
        """
        program = parse(src)
        typecheck_program(program)
        ir = compile_to_llvm(program)
        self.assertIn("define i32 @main(i32 %argc, ptr %argv)", ir)
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

    def test_compile_direct_constructor_match_materializes_only_var_branch(self) -> None:
        src = """
        type MaybeInt =
          | Just Int
          | Nothing

        fn unwrap(m: MaybeInt) -> Int =
          match m with
          | Just value -> value
          | Nothing -> 0

        fn main() -> Int !{IO} =
          match if false then Just(42) else Nothing with
          | Just value -> print_int(value)
          | whole -> print_int(unwrap(whole))
        """
        program = parse(src)
        typecheck_program(program)
        ir = compile_to_llvm(program)
        main_ir = ir.split("define i32 @main", 1)[1]
        self.assertIn("call i64 @sprout_make1(i64 0, i64 42)", main_ir)
        self.assertIn("call i64 @sprout_nothing(i64 1)", main_ir)
        self.assertNotIn("call i64 @sprout_tag", main_ir)
        self.assertNotIn("call i64 @sprout_field", main_ir)

    def test_compile_direct_constructor_match_supports_nested_match_scrutinee(self) -> None:
        src = """
        type MaybeInt =
          | Just Int
          | Nothing

        fn classify(flag: Bool) -> MaybeInt =
          match flag with
          | true -> Just(42)
          | false -> Nothing

        fn main() -> Int !{IO} =
          match classify(true) with
          | Just value -> print_int(value)
          | Nothing -> print_int(0)
        """
        program = parse(src)
        typecheck_program(program)
        ir = compile_to_llvm(program)
        main_ir = ir.split("define i32 @main", 1)[1]
        self.assertIn("call i64 @classify(i1 1)", main_ir)
        self.assertIn("call i64 @sprout_tag", main_ir)

    def test_compile_direct_constructor_match_supports_nested_constructor_match_expression(self) -> None:
        src = """
        type MaybeInt =
          | Just Int
          | Nothing

        fn main() -> Int !{IO} =
          match (match true with | true -> Just(42) | false -> Nothing) with
          | Just value -> print_int(value)
          | Nothing -> print_int(0)
        """
        program = parse(src)
        typecheck_program(program)
        ir = compile_to_llvm(program)
        main_ir = ir.split("define i32 @main", 1)[1]
        self.assertNotIn("call i64 @sprout_make1(i64 0, i64 42)", main_ir)
        self.assertNotIn("call i64 @sprout_tag", main_ir)
        self.assertNotIn("call i64 @sprout_field", main_ir)
        self.assertIn("phi i64", main_ir)

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
        self.assertIn("declare i64 @sprout_alloc_tuple_blob(i64)", ir)
        self.assertIn("call i64 @sprout_alloc_tuple_blob(i64", ir)
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
        self.assertIn("call i64 @sprout_alloc_closure_env(i64 %t", ir)
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
        self.assertIn("call i64 @sprout_alloc_closure_env(i64 %t", ir)
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
        self.assertIn("define i64 @__sprout_partial_", ir)
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
        self.assertIn("call i64 @sprout_alloc_closure_env(i64 %t", ir)
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
        self.assertIn("declare i64 @tcp_read(i64)", ir)
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

    def test_compile_unit_literal_and_pattern_to_llvm(self) -> None:
        src = """
        fn value_or_unit(x: Unit) -> Int =
          match x with
          | () -> 1

        fn main() -> Unit !{IO} =
          match env_get("SPROUT_TEST_ENV_GET") with
          | Just value -> print(value)
          | Nothing -> ()
        """
        program = parse(with_prelude(src))
        typecheck_program(program)
        ir = compile_to_llvm(program)
        self.assertIn("icmp eq i64", ir)
        self.assertIn("ret i32 0", ir)

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
            self.assertIn("declare i64 @crypto_base64_encode(i64)", ir)
            self.assertIn("declare i64 @crypto_base64_decode(ptr)", ir)
            self.assertIn("declare i64 @crypto_bytes_xor(i64, i64)", ir)
            self.assertIn("declare i64 @crypto_random_bytes(i64)", ir)

    def test_native_runtime_roots_managed_args_in_key_allocating_helpers(self) -> None:
        runtime_source = (Path(sprout_cli.__file__).parent.parent / "runtime" / "sprout_runtime.c").read_text(encoding="utf-8")
        self.assertIn("#define SPROUT_GC_PUSH_PTR_LOCAL(slot_name)", runtime_source)
        self.assertIn("long long rooted_vec = vec;", runtime_source)
        self.assertIn("long long rm = map_h;", runtime_source)  # BST map helpers use rm not rooted_map
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
        self.assertIn("declare i64 @str_concat(i64, i64)", ir)
        self.assertIn("declare i64 @str_len(ptr)", ir)
        self.assertIn("declare i64 @str_slice(i64, i64, i64)", ir)
        self.assertIn("declare i1 @str_eq(ptr, ptr)", ir)
        self.assertIn("declare i64 @str_find(ptr, ptr)", ir)
        self.assertIn("declare i1 @str_starts_with(ptr, ptr)", ir)
        self.assertIn("declare i64 @str_compare(ptr, ptr)", ir)
        self.assertIn("declare i64 @int_to_string(i64)", ir)

    def test_compile_regex_builtins_to_llvm(self) -> None:
        src = """
        fn main() -> Unit !{IO} =
          print(regex_escape("a+b"))
        """
        program = parse(src)
        typecheck_program(program)
        ir = compile_to_llvm(program)
        self.assertIn("declare i64 @regex_validate(ptr)", ir)
        self.assertIn("declare i1 @regex_is_match(ptr, ptr)", ir)
        self.assertIn("declare i64 @regex_find_range(ptr, ptr)", ir)
        self.assertIn("declare i64 @regex_replace_all_literal(i64, i64, i64)", ir)
        self.assertIn("declare i64 @regex_escape(i64)", ir)

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
        import stdlib.http (http_response_body)

        fn main() -> Unit !{IO} =
          match http_request("GET", "http://127.0.0.1:8080/ok", "", "", 500) with
          | Ok resp -> print(http_response_body(resp))
          | Err _ -> print("err")
        """
        program = self._load_module_program(src)
        typecheck_program(program)
        ir = compile_to_llvm(program)
        self.assertIn("declare i64 @http_request(ptr, ptr, ptr, ptr, i64)", ir)

    def test_compile_json_stringify_to_llvm(self) -> None:
        src = """
        import stdlib.json as json

        fn main() -> Unit !{IO} =
          print(json.stringify(json.JsonArray(json.JsonArrayCons(json.JsonInt(1), json.JsonArrayNil))))
        """
        program = self._load_module_program(src)
        typecheck_program(program)
        ir = compile_to_llvm(program)
        self.assertIn("declare i64 @json_stringify(i64)", ir)

    def test_compile_json_parse_to_llvm(self) -> None:
        src = """
        import stdlib.json (parse, stringify)

        fn main() -> Unit !{IO} =
          match parse("{\\"ok\\":true}") with
          | Ok value -> print(stringify(value))
          | Err _ -> print("err")
        """
        program = self._load_module_program(src)
        typecheck_program(program)
        ir = compile_to_llvm(program)
        self.assertIn("declare i64 @json_parse(ptr)", ir)

    def test_compile_http_prelude_registers_qualified_runtime_ctors(self) -> None:
        src = """
        import stdlib.http (http_response_body)

        fn main() -> Unit !{IO} =
          match http_request("GET", "http://127.0.0.1:8080/ok", "", "", 500) with
          | Ok resp -> print(http_response_body(resp))
          | Err _ -> print("err")
        """
        program = self._load_module_program(src)
        typecheck_program(program)
        ir = compile_to_llvm(program)
        # Tags 12/13/14 = HttpResponse/HttpTimeout/HttpNetwork — each should register
        # under both qualified (stdlib.http.X) and leaf (X) names.
        self.assertGreaterEqual(ir.count("call i64 @sprout_register_ctor(i64 12"), 2)
        self.assertGreaterEqual(ir.count("call i64 @sprout_register_ctor(i64 13"), 2)
        self.assertGreaterEqual(ir.count("call i64 @sprout_register_ctor(i64 14"), 2)
        self.assertIn("declare i64 @argv_get(i64)", ir)
        self.assertIn("declare i64 @sprout_set_argv(i32, ptr)", ir)
        self.assertIn("define i32 @main(i32 %argc, ptr %argv)", ir)
        self.assertIn("declare i64 @sprout_make3(i64, i64, i64, i64)", ir)
        self.assertIn("call i64 @sprout_field(i64 %resp, i64 2)", ir)


if __name__ == "__main__":
    unittest.main()
