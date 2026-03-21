from __future__ import annotations

import io
import os
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from sprout import RuntimeError, parse, run_program, typecheck_program
from sprout.module_loader import load_module_bundle, resolve_program_names
from sprout.stdlib import with_http_prelude, with_prelude
from sprout.typeclass_lowering import lower_typeclasses


class RuntimeTests(unittest.TestCase):
    def test_run_main_prints_result(self) -> None:
        src = """
        fn fact(n: Int) -> Int =
          if n == 0 then 1 else n * fact(n - 1)

        fn main() -> Unit !{IO} =
          print(fact(5))
        """
        program = parse(src)
        typecheck_program(program)

        out = io.StringIO()
        run_program(program, stdout=out)
        self.assertEqual(out.getvalue().strip(), "120")

    def test_run_match_with_adt(self) -> None:
        src = """
        type Maybe a =
          | Just a
          | Nothing

        fn with_default(m: Maybe Int, d: Int) -> Int =
          match m with
          | Just x -> x
          | Nothing -> d

        fn main() -> Unit !{IO} =
          print(with_default(Just(7), 0))
        """
        program = parse(src)
        typecheck_program(program)

        out = io.StringIO()
        run_program(program, stdout=out)
        self.assertEqual(out.getvalue().strip(), "7")

    def test_top_level_let_evaluates_in_order(self) -> None:
        src = """
        let a = 1
        let b = a + 2

        fn main() -> Unit !{IO} =
          print(b)
        """
        program = parse(src)
        typecheck_program(program)

        out = io.StringIO()
        run_program(program, stdout=out)
        self.assertEqual(out.getvalue().strip(), "3")

    def test_stdlib_split_ints_and_fold(self) -> None:
        src = """
        fn add(acc: Int, x: Int) -> Int = acc + x

        fn main() -> Unit !{IO} =
          print(fold(add, 0, split_ints("1, 2 3 4")))
        """
        program = parse(with_prelude(src))
        typecheck_program(program)
        out = io.StringIO()
        run_program(program, stdout=out)
        self.assertEqual(out.getvalue().strip(), "10")

    def test_stdlib_result_helpers(self) -> None:
        src = """
        fn plus1(x: Int) -> Int = x + 1
        fn twice(x: Int) -> Result String Int = Ok(x * 2)
        fn tag(e: String) -> String = str_concat("err:", e)

        fn main() -> Unit !{IO} =
          print(
            result_with_default(
              0,
              result_map_error(
                tag,
                result_and_then(
                  twice,
                  result_map(plus1, Ok(20))
                )
              )
            )
          )
        """
        program = parse(with_prelude(src))
        typecheck_program(program)
        out = io.StringIO()
        run_program(program, stdout=out)
        self.assertEqual(out.getvalue().strip(), "42")

    def test_stdlib_pipeline_helpers(self) -> None:
        src = """
        fn plus1(x: Int) -> Int = x + 1
        fn twice(x: Int) -> Result String Int = Ok(x * 2)
        fn tag(e: String) -> String = str_concat("err:", e)

        fn main() -> Unit !{IO} =
          print(
            result_with_default(
              0,
              result_pipe_error(
                tag,
                result_pipe_ok(
                  plus1,
                  result_pipe(
                    twice,
                    Ok(pipe(plus1, 20))
                  )
                )
              )
            )
          )
        """
        program = parse(with_prelude(src))
        typecheck_program(program)
        out = io.StringIO()
        run_program(program, stdout=out)
        self.assertEqual(out.getvalue().strip(), "43")

    def test_pipe_operator_with_result_helpers(self) -> None:
        src = """
        fn plus1(x: Int) -> Int = x + 1
        fn twice(x: Int) -> Result String Int = Ok(x * 2)
        fn tag(e: String) -> String = str_concat("err:", e)

        fn main() -> Unit !{IO} =
          print(
            result_with_default(
              0,
              Ok(20)
              |> result_pipe_ok(plus1)
              |> result_pipe(twice)
              |> result_pipe_ok(plus1)
              |> result_pipe_error(tag)
            )
          )
        """
        program = parse(with_prelude(src))
        typecheck_program(program)
        out = io.StringIO()
        run_program(program, stdout=out)
        self.assertEqual(out.getvalue().strip(), "43")

    def test_pipe_operator_preserves_left_to_right_evaluation_order(self) -> None:
        src = """
        fn then_io(a: Unit !{IO}, b: Int) -> Int !{IO} = b
        fn add(x: Int, y: Int) -> Int = x + y
        fn mark(label: String, value: Int) -> Int !{IO} =
          then_io(print(label), value)

        fn main() -> Unit !{IO} =
          print(
            mark("left", 1)
            |> add(mark("arg", 2))
          )
        """
        program = parse(with_prelude(src))
        typecheck_program(program)
        out = io.StringIO()
        run_program(program, stdout=out)
        self.assertEqual(out.getvalue().strip(), "left\narg\n3")

    def test_stdlib_when_ok_and_when_error_helpers(self) -> None:
        src = """
        fn show_ok(x: Int) -> Unit !{IO} = print(x)
        fn show_err(e: String) -> Unit !{IO} = print(e)

        fn main() -> Unit !{IO} =
          print(
            result_with_default(
              0,
              when_error(
                show_err,
                when_ok(show_ok, Ok(42))
              )
            )
          )
        """
        program = parse(with_prelude(src))
        typecheck_program(program)
        out = io.StringIO()
        run_program(program, stdout=out)
        self.assertEqual(out.getvalue().strip(), "42\n42")

    def test_stdlib_when_error_runs_effect_and_preserves_result(self) -> None:
        src = """
        fn show_err(e: String) -> Unit !{IO} = print(e)

        fn main() -> Unit !{IO} =
          print(
            result_with_default(
              7,
              when_error(show_err, Err("boom"))
            )
          )
        """
        program = parse(with_prelude(src))
        typecheck_program(program)
        out = io.StringIO()
        run_program(program, stdout=out)
        self.assertEqual(out.getvalue().strip(), "boom\n7")

    def test_stdlib_read_lines_and_parse_int(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "numbers.txt"
            input_path.write_text("7\n8\n9\n", encoding="utf-8")
            src = f"""
            type List a =
              | Cons a (List a)
              | Nil

            fn sum_lines(lines: List String) -> Int =
              match lines with
              | Nil -> 0
              | Cons s rest -> parse_int(s) + sum_lines(rest)

            fn main() -> Unit !{{IO}} =
              print(sum_lines(read_lines("{input_path}")))
            """
            program = parse(src)
            typecheck_program(program)
            out = io.StringIO()
            run_program(program, stdout=out)
            self.assertEqual(out.getvalue().strip(), "24")

    def test_run_function_composition(self) -> None:
        src = """
        fn inc(x: Int) -> Int = x + 1
        fn double(x: Int) -> Int = x * 2

        fn main() -> Unit !{IO} =
          print((inc >> double)(20))
        """
        program = parse(src)
        typecheck_program(program)
        out = io.StringIO()
        run_program(program, stdout=out)
        self.assertEqual(out.getvalue().strip(), "42")

    def test_run_reverse_function_composition(self) -> None:
        src = """
        fn inc(x: Int) -> Int = x + 1
        fn double(x: Int) -> Int = x * 2

        fn main() -> Unit !{IO} =
          print((double << inc)(20))
        """
        program = parse(src)
        typecheck_program(program)
        out = io.StringIO()
        run_program(program, stdout=out)
        self.assertEqual(out.getvalue().strip(), "42")

    def test_run_right_associative_forward_function_composition(self) -> None:
        src = """
        fn inc(x: Int) -> Int = x + 1
        fn double(x: Int) -> Int = x * 2
        fn dec(x: Int) -> Int = x - 3

        fn main() -> Unit !{IO} =
          print((inc >> double >> dec)(20))
        """
        program = parse(src)
        typecheck_program(program)
        out = io.StringIO()
        run_program(program, stdout=out)
        self.assertEqual(out.getvalue().strip(), "39")

    def test_run_partial_application_of_named_function(self) -> None:
        src = """
        fn add(x: Int, y: Int) -> Int = x + y
        let inc = add(1)

        fn main() -> Unit !{IO} =
          print(inc(41))
        """
        program = parse(src)
        typecheck_program(program)
        out = io.StringIO()
        run_program(program, stdout=out)
        self.assertEqual(out.getvalue().strip(), "42")

    def test_run_partial_application_of_builtin(self) -> None:
        src = """
        let greet = str_concat("hi ")

        fn main() -> Unit !{IO} =
          print(greet("sprout"))
        """
        program = parse(src)
        typecheck_program(program)
        out = io.StringIO()
        run_program(program, stdout=out)
        self.assertEqual(out.getvalue().strip(), "hi sprout")

    def test_run_lambda_argument(self) -> None:
        src = r"""
        fn apply(x: Int, f: Int -> Int) -> Int =
          f(x)

        fn main() -> Unit !{IO} =
          print(apply(20, \n -> n + 22))
        """
        program = parse(src)
        typecheck_program(program)
        out = io.StringIO()
        run_program(program, stdout=out)
        self.assertEqual(out.getvalue().strip(), "42")

    def test_run_lambda_closure_captures_outer_value(self) -> None:
        src = r"""
        fn main() -> Unit !{IO} =
          print((\(base) -> (\(x) -> base + x))(40)(2))
        """
        program = parse(src)
        typecheck_program(program)
        out = io.StringIO()
        run_program(program, stdout=out)
        self.assertEqual(out.getvalue().strip(), "42")

    def test_run_effect_polymorphic_helper_with_pure_and_io_instantiations(self) -> None:
        src = """
        fn apply_twice(f: Int -> Int !{e}, x: Int) -> Int !{e} =
          f(f(x))

        fn inc(x: Int) -> Int = x + 1

        fn show(x: Int) -> Int !{IO} =
          print_int(x)

        fn main() -> Unit !{IO} =
          print(apply_twice(inc, 20) + apply_twice(show, 1))
        """
        program = parse(src)
        typecheck_program(program)
        out = io.StringIO()
        run_program(program, stdout=out)
        self.assertEqual(out.getvalue().strip(), "1\n1\n23")

    def test_run_lambda_return_value(self) -> None:
        src = r"""
        fn make_adder(base: Int) -> Int -> Int =
          \x -> base + x

        fn main() -> Unit !{IO} =
          print(make_adder(39)(3))
        """
        program = parse(src)
        typecheck_program(program)
        out = io.StringIO()
        run_program(program, stdout=out)
        self.assertEqual(out.getvalue().strip(), "42")

    def test_run_lambda_shadowing_prefers_inner_binding(self) -> None:
        src = r"""
        let x = 100

        fn main() -> Unit !{IO} =
          print((\(x) -> (\(y) -> x + y))(40)(2))
        """
        program = parse(src)
        typecheck_program(program)
        out = io.StringIO()
        run_program(program, stdout=out)
        self.assertEqual(out.getvalue().strip(), "42")

    def test_run_tuple_match(self) -> None:
        src = """
        fn sum_pair(pair: (Int, Int)) -> Int =
          match pair with
          | (x, y) -> x + y

        fn main() -> Unit !{IO} =
          print(sum_pair((20, 22)))
        """
        program = parse(src)
        typecheck_program(program)
        out = io.StringIO()
        run_program(program, stdout=out)
        self.assertEqual(out.getvalue().strip(), "42")

    def test_run_nested_tuple_match(self) -> None:
        src = """
        fn main() -> Unit !{IO} =
          match (1, (20, 22)) with
          | (_, (x, y)) -> print(x + y)
        """
        program = parse(src)
        typecheck_program(program)
        out = io.StringIO()
        run_program(program, stdout=out)
        self.assertEqual(out.getvalue().strip(), "42")

    def test_tail_recursive_function_does_not_overflow_python_stack(self) -> None:
        src = """
        fn sum_down(n: Int, acc: Int) -> Int =
          if n == 0 then acc else sum_down(n - 1, acc + n)

        fn main() -> Unit !{IO} =
          print(sum_down(5000, 0))
        """
        program = parse(src)
        typecheck_program(program)
        out = io.StringIO()
        run_program(program, stdout=out)
        self.assertEqual(out.getvalue().strip(), "12502500")

    def test_read_file_builtin_missing_path_reports_runtime_error_convention(self) -> None:
        src = """
        fn main() -> Unit !{IO} =
          print(read_file("/definitely/missing/sprout-runtime-test.txt"))
        """
        program = parse(src)
        typecheck_program(program)
        with self.assertRaises(RuntimeError) as ctx:
            run_program(program)
        self.assertIn("runtime error: builtin `read_file`:", str(ctx.exception))
        self.assertIn("No such file", str(ctx.exception))

    def test_tcp_close_unknown_handle_reports_runtime_error_convention(self) -> None:
        src = """
        fn main() -> Unit !{IO} =
          tcp_close(1)
        """
        program = parse(src)
        typecheck_program(program)
        with self.assertRaises(RuntimeError) as ctx:
            run_program(program)
        self.assertIn("runtime error: builtin `tcp_close`: got unknown connection handle", str(ctx.exception))

    def test_string_builtins(self) -> None:
        src = """
        fn seq(a: Unit !{IO}, b: Unit !{IO}) -> Unit !{IO} = b

        fn main() -> Unit !{IO} =
          seq(
            print(str_concat(str_slice("sprout-lang", 0, 6), "-ok")),
            seq(
              print(str_len("sprout-lang") == 11),
              seq(
                print(str_find("sprout-lang", "lang") == 7),
                print(str_starts_with("sprout-lang", "sprout"))
              )
            )
          )
        """
        program = parse(src)
        typecheck_program(program)
        out = io.StringIO()
        run_program(program, stdout=out)
        self.assertEqual(out.getvalue().strip(), "sprout-ok\nTrue\nTrue\nTrue")

    def test_http_stdlib_echo_response(self) -> None:
        src = """
        fn main() -> Unit !{IO} =
          print(http_echo_response("GET /hello HTTP/1.1\\r\\nHost: local\\r\\n\\r\\n"))
        """
        program = parse(with_http_prelude(src))
        typecheck_program(program)
        out = io.StringIO()
        run_program(program, stdout=out)
        result = out.getvalue()
        self.assertIn("HTTP/1.1 200 OK", result)
        self.assertIn("Connection: close", result)
        self.assertIn("GET /hello HTTP/1.1", result)

    def test_http_stdlib_response_body_helper(self) -> None:
        src = """
        fn main() -> Unit !{IO} =
          print(http_response_body(HttpResponse(200, "h: v", "payload")))
        """
        program = parse(with_http_prelude(src))
        typecheck_program(program)
        out = io.StringIO()
        run_program(program, stdout=out)
        self.assertEqual(out.getvalue().strip(), "payload")

    def test_json_parse_and_lookup(self) -> None:
        src = """
        fn json_string_or_default(value: Json) -> String =
          match json_get_string(value) with
          | Just s -> s
          | Nothing -> "not-string"

        fn title_or_missing(value: Json) -> String =
          match json_get_field(value, "title") with
          | Just field -> json_string_or_default(field)
          | Nothing -> "missing"

        fn main() -> Unit !{IO} =
          match json_parse("{\\"title\\":\\"hello\\",\\"count\\":2}") with
          | Ok value -> print(title_or_missing(value))
          | Err _ -> print("decode-error")
        """
        program = parse(with_http_prelude(src))
        typecheck_program(program)
        out = io.StringIO()
        run_program(program, stdout=out)
        self.assertEqual(out.getvalue().strip(), "hello")

    def test_http_response_formats_supported_status(self) -> None:
        src = """
        fn main() -> Unit !{IO} =
          match http_response(503, "down") with
          | Ok response -> print(response)
          | Err _ -> print("err")
        """
        program = parse(with_http_prelude(src))
        typecheck_program(program)
        out = io.StringIO()
        run_program(program, stdout=out)
        self.assertIn("HTTP/1.1 503 Service Unavailable", out.getvalue())
        self.assertIn("down", out.getvalue())

    def test_http_response_rejects_unsupported_status(self) -> None:
        src = """
        fn main() -> Unit !{IO} =
          match http_response(418, "teapot") with
          | Ok _ -> print("ok")
          | Err err ->
              match err with
              | HttpUnsupportedStatus code -> print(code)
        """
        program = parse(with_http_prelude(src))
        typecheck_program(program)
        out = io.StringIO()
        run_program(program, stdout=out)
        self.assertEqual(out.getvalue().strip(), "418")

    def test_json_parse_invalid(self) -> None:
        src = """
        fn main() -> Unit !{IO} =
          match json_parse("{bad json}") with
          | Ok _ -> print("ok")
          | Err e ->
              match e with
              | JsonDecode _ -> print("decode-error")
        """
        program = parse(with_http_prelude(src))
        typecheck_program(program)
        out = io.StringIO()
        run_program(program, stdout=out)
        self.assertEqual(out.getvalue().strip(), "decode-error")

    def test_json_array_and_object_iteration_helpers(self) -> None:
        src = """
        fn int_from_json(value: Json) -> Int =
          match json_get_int(value) with
          | Just n -> n
          | Nothing -> -2

        fn first_int_from_step(step: JsonArrayStep) -> Int =
          match step with
          | JsonArrayStep first _ -> int_from_json(first)

        fn first_int_from_array(arr: JsonArray) -> Int =
          match json_array_next(arr) with
          | Just step -> first_int_from_step(step)
          | Nothing -> -3

        fn first_int_from_items(items: Json) -> Int =
          match json_get_array(items) with
          | Just arr -> first_int_from_array(arr)
          | Nothing -> -4

        fn first_int_from_value(value: Json) -> Int =
          match json_get_field(value, "items") with
          | Just items -> first_int_from_items(items)
          | Nothing -> -5

        fn main() -> Unit !{IO} =
          match json_parse("{\\"items\\":[1,2]}") with
          | Ok value -> print(first_int_from_value(value))
          | Err _ -> print(-6)
        """
        program = parse(with_http_prelude(src))
        typecheck_program(program)
        out = io.StringIO()
        run_program(program, stdout=out)
        self.assertEqual(out.getvalue().strip(), "1")

    def test_json_stringify_compact_and_escaped(self) -> None:
        src = """
        fn sample() -> Json =
          JsonObject(
            JsonObjectCons(
              "message",
              JsonString("hi\\n\\"ok\\""),
              JsonObjectCons(
                "items",
                JsonArray(
                  JsonArrayCons(
                    JsonInt(1),
                    JsonArrayCons(JsonBool(false), JsonArrayCons(JsonNull, JsonArrayNil))
                  )
                ),
                JsonObjectNil
              )
            )
          )

        fn main() -> Unit !{IO} =
          print(json_stringify(sample()))
        """
        program = parse(with_http_prelude(src))
        typecheck_program(program)
        out = io.StringIO()
        run_program(program, stdout=out)
        self.assertEqual(out.getvalue().strip(), '{"message":"hi\\n\\"ok\\"","items":[1,false,null]}')

    def test_json_builder_helpers(self) -> None:
        src = """
        module test.main
        import stdlib.json as json

        fn sample() -> json.Json =
          json.object_from_pairs(
            [
              ("title", json.string("hello")),
              ("count", json.int(2)),
              ("items", json.array_from_list([json.string("a"), json.bool(true), json.null()]))
            ]
          )

        fn main() -> Unit !{IO} =
          print(json_stringify(sample()))
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / "main.sprout"
            main.write_text(src, encoding="utf-8")
            bundle = load_module_bundle(main)
            program = parse(bundle.source)
            resolve_program_names(program, bundle)
            typecheck_program(program)
            out = io.StringIO()
            run_program(program, stdout=out)
            self.assertEqual(out.getvalue().strip(), '{"title":"hello","count":2,"items":["a",true,null]}')

    def test_stdlib_crypto_helpers(self) -> None:
        src = """
        module main
        import stdlib.bytes (from_string)
        import stdlib.crypto as crypto

        fn seq(a: Unit !{IO}, b: Unit !{IO}) -> Unit !{IO} = b

        fn main() -> Unit !{IO} =
          seq(
            print(crypto.base64_encode(crypto.sha256(from_string("abc")))),
            seq(
              print(
                crypto.base64_encode(
                  crypto.hmac_sha256(
                    from_string("key"),
                    from_string("The quick brown fox jumps over the lazy dog")
                  )
                )
              ),
              seq(
                match crypto.base64_decode("c3Byb3V0") with
                | Ok decoded -> print(crypto.base64_encode(decoded))
                | Err _ -> print("bad"),
                match crypto.bytes_xor(from_string("abc"), from_string("ABC")) with
                | Ok bytes -> print(crypto.base64_encode(bytes))
                | Err _ -> print("bad")
              )
            )
          )
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / "main.sprout"
            main.write_text(src, encoding="utf-8")
            bundle = load_module_bundle(main)
            program = parse(bundle.source)
            resolve_program_names(program, bundle)
            typecheck_program(program)
            out = io.StringIO()
            run_program(program, stdout=out)
            self.assertEqual(
                out.getvalue().strip(),
                "\n".join(
                    [
                        "ungWv48Bz+pBQUDeXa4iI7ADYaOWF3qctBD/YfIAFa0=",
                        "97yD9DBThCSxMpjmqm+xQ+9NWaFJRhdZl0edvC0aPNg=",
                        "c3Byb3V0",
                        "ICAg",
                    ]
                ),
            )

    def test_stdlib_crypto_random_bytes_and_errors(self) -> None:
        src = """
        module main
        import stdlib.bytes (length)
        import stdlib.crypto as crypto

        fn random_score() -> Int !{IO} =
          match crypto.random_bytes(8) with
          | Ok bytes -> length(bytes)
          | Err _ -> -1

        fn random_error_score() -> Int !{IO} =
          match crypto.random_bytes(-1) with
          | Ok _ -> -2
          | Err _ -> 0

        fn main() -> Unit !{IO} =
          print(random_score() + random_error_score())
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / "main.sprout"
            main.write_text(src, encoding="utf-8")
            bundle = load_module_bundle(main)
            program = parse(bundle.source)
            resolve_program_names(program, bundle)
            typecheck_program(program)
            out = io.StringIO()
            run_program(program, stdout=out)
            self.assertEqual(out.getvalue().strip(), "8")

    def test_terminal_builtins_emit_ansi(self) -> None:
        src = """
        fn seq(a: Unit !{IO}, b: Unit !{IO}) -> Unit !{IO} = b
        fn main() -> Unit !{IO} =
          seq(term_clear(), seq(term_move(2, 3), term_write("x")))
        """
        program = parse(src)
        typecheck_program(program)
        out = io.StringIO()
        run_program(program, stdout=out)
        text = out.getvalue()
        self.assertIn("\x1b[2J\x1b[H", text)
        self.assertIn("\x1b[2;3H", text)
        self.assertTrue(text.endswith("x"))

    def test_terminal_read_key_builtin(self) -> None:
        src = """
        fn main() -> Unit !{IO} =
          print(term_read_key())
        """
        program = parse(src)
        typecheck_program(program)
        out = io.StringIO()
        with patch("sys.stdin", io.StringIO("j")):
            run_program(program, stdout=out)
        self.assertEqual(out.getvalue().strip(), "j")

        out = io.StringIO()
        with patch("sys.stdin", io.StringIO("\x04")):
            run_program(program, stdout=out)
        self.assertEqual(out.getvalue().strip(), "ctrl-d")

    def test_terminal_read_line_builtin(self) -> None:
        src = """
        type Maybe a =
          | Just a
          | Nothing

        fn render(v: Maybe String) -> String =
          match v with
          | Just text -> text
          | Nothing -> "eof"

        fn main() -> Unit !{IO} =
          print(render(term_read_line()))
        """
        program = parse(src)
        typecheck_program(program)
        out = io.StringIO()
        with patch("sys.stdin", io.StringIO("hello\n")):
            run_program(program, stdout=out)
        self.assertEqual(out.getvalue().strip(), "hello")

        out = io.StringIO()
        with patch("sys.stdin", io.StringIO("")):
            run_program(program, stdout=out)
        self.assertEqual(out.getvalue().strip(), "eof")

    def test_repl_service_builtins_track_session_and_capture_expression_output(self) -> None:
        src = """
        type Maybe a =
          | Just a
          | Nothing

        type Result e a =
          | Ok a
          | Err e

        type Vec a =
          | Vec (Vector a)

        fn seq(a: Unit !{IO}, b: Unit !{IO}) -> Unit !{IO} = b

        fn first_or(lines: Vec String, fallback: String) -> String =
          match lines with
          | Vec raw ->
              match vector_get(raw, 0) with
              | Just text -> text
              | Nothing -> fallback

        fn render_unit_result(result: Result String Unit) -> String =
          match result with
          | Ok _ -> "ok"
          | Err message -> str_concat("error: ", message)

        fn render_type_result(result: Result String String) -> String =
          match result with
          | Ok value -> value
          | Err message -> str_concat("error: ", message)

        fn render_expr_result(result: Result String (Vec String)) -> String =
          match result with
          | Ok lines -> first_or(lines, "<empty>")
          | Err message -> str_concat("error: ", message)

        fn render_instances_result(result: Result String (String, Vec String)) -> String =
          match result with
          | Err message -> str_concat("error: ", message)
          | Ok pair ->
              match pair with
              | (query_type, Vec raw) ->
                  if vector_length(raw) == 0 then str_concat("No instances for ", query_type)
                  else first_or(Vec(raw), "<empty>")

        fn render_completion_result(result: (String, Vec String)) -> String =
          match result with
          | (prefix, lines) -> str_concat(prefix, str_concat(":", first_or(lines, "<empty>")))

        fn main() -> Unit !{IO} =
          seq(
            repl_reset_session(),
            seq(
              print(render_expr_result(repl_eval_expr("1 + 1"))),
              seq(
                print(render_unit_result(repl_add_declaration("let answer = 41"))),
                seq(
                  print(render_type_result(repl_type_of("answer"))),
                    seq(
                      print(render_instances_result(repl_instances("List Int"))),
                      seq(
                        print(render_completion_result(repl_complete("str"))),
                        seq(
                      print(render_unit_result(repl_add_import("import stdlib.string"))),
                      seq(
                        print(render_expr_result(repl_eval_expr("string.concat(\\"a\\", \\"b\\")"))),
                        seq(
                          print(render_type_result(repl_type_of("missing_name"))),
                          repl_reset_session()
                        )
                        )
                      )
                    )
                  )
                )
              )
            )
          )
        """
        program = parse(src)
        typecheck_program(program)
        out = io.StringIO()
        run_program(program, stdout=out)
        lines = out.getvalue().splitlines()
        self.assertEqual(lines[0], "2")
        self.assertEqual(lines[1], "ok")
        self.assertEqual(lines[2], "Int")
        self.assertEqual(lines[3], "Foldable List")
        self.assertEqual(lines[4], "str:string")
        self.assertEqual(lines[5], "ok")
        self.assertEqual(lines[6], "ab")
        self.assertTrue(lines[7].startswith("error: "))

    def test_vector_builtins(self) -> None:
        src = """
        type Maybe a =
          | Just a
          | Nothing

        fn value_or(v: Maybe Int, fallback: Int) -> Int =
          match v with
          | Just x -> x
          | Nothing -> fallback

        fn main() -> Unit !{IO} =
          print(
            value_or(
              vector_get(
                vector_set(
                  vector_append(vector_append(vector_empty(), 10), 20),
                  1,
                  25
                ),
                1
              ),
              -1
            )
          )
        """
        program = parse(src)
        typecheck_program(program)
        out = io.StringIO()
        run_program(program, stdout=out)
        self.assertEqual(out.getvalue().strip(), "25")

    def test_map_builtins(self) -> None:
        src = """
        type Maybe a =
          | Just a
          | Nothing

        fn value_or(v: Maybe Int, fallback: Int) -> Int =
          match v with
          | Just x -> x
          | Nothing -> fallback

        fn main() -> Unit !{IO} =
          print(
            value_or(
              map_get(
                map_remove(
                  map_set(map_set(map_empty(), "a", 1), "b", 2),
                  "a"
                ),
                "b"
              ),
              -1
            )
          )
        """
        program = parse(src)
        typecheck_program(program)
        out = io.StringIO()
        run_program(program, stdout=out)
        self.assertEqual(out.getvalue().strip(), "2")

    def test_stdlib_vec_slice_and_reverse(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / "main.sprout"
            main.write_text(
                """
                module main
                import stdlib.collections (vec_append, vec_empty, vec_get_or, vec_reverse, vec_slice)

                fn main() -> Unit !{IO} =
                  print(
                    vec_get_or(
                      0,
                      -1,
                      vec_reverse(
                        vec_slice(
                          1,
                          2,
                          vec_append(40, vec_append(30, vec_append(20, vec_append(10, vec_empty()))))
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
            self.assertEqual(out.getvalue().strip(), "30")

    def test_stdlib_dict_keys_and_values(self) -> None:
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
                    vec_get_or(0, -100, dict_values(sample()))
                    + string.length(vec_get_or(1, "", dict_keys(sample())))
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
            self.assertEqual(out.getvalue().strip(), "11")

    def test_env_get_builtin_returns_maybe_string(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / "main.sprout"
            main.write_text(
                """
                module main
                import stdlib.collections (Maybe)

                fn value_or_missing(name: String) -> String !{IO} =
                  match env_get(name) with
                  | Just value -> value
                  | Nothing -> "missing"

                fn main() -> Unit !{IO} =
                  print(value_or_missing("SPROUT_TEST_ENV_GET"))
                """,
                encoding="utf-8",
            )
            bundle = load_module_bundle(main)
            program = parse(bundle.source)
            resolve_program_names(program, bundle)
            typecheck_program(program)
            out = io.StringIO()
            with patch.dict(os.environ, {"SPROUT_TEST_ENV_GET": "sprout-env"}, clear=False):
                run_program(program, stdout=out)
            self.assertEqual(out.getvalue().strip(), "sprout-env")

            out = io.StringIO()
            with patch.dict(os.environ, {}, clear=True):
                run_program(program, stdout=out)
            self.assertEqual(out.getvalue().strip(), "missing")

    def test_argv_get_builtin_returns_program_argument(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / "main.sprout"
            main.write_text(
                """
                module main
                import stdlib.collections (Maybe)

                fn arg_or_missing(index: Int) -> String !{IO} =
                  match argv_get(index) with
                  | Just value -> value
                  | Nothing -> "missing"

                fn main() -> Unit !{IO} =
                  print(arg_or_missing(0))
                """,
                encoding="utf-8",
            )
            bundle = load_module_bundle(main)
            program = parse(bundle.source)
            resolve_program_names(program, bundle)
            typecheck_program(program)

            out = io.StringIO()
            run_program(program, stdout=out, argv=["http://example.test"])
            self.assertEqual(out.getvalue().strip(), "http://example.test")

            out = io.StringIO()
            run_program(program, stdout=out, argv=[])
            self.assertEqual(out.getvalue().strip(), "missing")

    def test_stdlib_bytes_helpers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / "main.sprout"
            main.write_text(
                """
                module main
                import stdlib.collections (Maybe)
                import stdlib.bytes (Result, Utf8Error, append, c_string, from_string, get, length, read_c_string, read_u16_be, read_u32_be, slice, to_string, u16_be, u32_be)

                fn int_or(value: Maybe Int, fallback: Int) -> Int =
                  match value with
                  | Just n -> n
                  | Nothing -> fallback

                fn string_score(value: Result Utf8Error String, expected: String, score: Int, fallback: Int) -> Int =
                  match value with
                  | Ok text -> if text == expected then score else fallback
                  | Err _ -> fallback

                fn main() -> Unit !{IO} =
                  print(
                    int_or(get(slice(append(u16_be(258), u32_be(16909060)), 1, 4), 0), -1)
                    + int_or(read_u16_be(u16_be(258)), -10)
                    + int_or(read_u32_be(u32_be(16909060)), -100)
                    + length(append(u16_be(258), u32_be(16909060)))
                    + string_score(to_string(from_string("zaż")), "zaż", 3, -1000)
                    + string_score(read_c_string(c_string("ok")), "ok", 2, -1000)
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
            self.assertEqual(out.getvalue().strip(), "16909331")

    def test_stdlib_bytes_builder_helpers(self) -> None:
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
                  | Ok text -> print(length(builder_build(builder_empty())) + score(text))
                  | Err _ -> print(0)
                """,
                encoding="utf-8",
            )
            bundle = load_module_bundle(main)
            program = parse(bundle.source)
            resolve_program_names(program, bundle)
            typecheck_program(program)
            out = io.StringIO()
            run_program(program, stdout=out)
            self.assertEqual(out.getvalue().strip(), "1")

    def test_stdlib_bytes_utf8_decode_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / "main.sprout"
            main.write_text(
                """
                module main
                import stdlib.bytes (Result, append, singleton, to_string)

                fn main() -> Unit !{IO} =
                  match to_string(append(singleton(255), singleton(97))) with
                  | Ok _ -> print("ok")
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
            self.assertEqual(out.getvalue().strip(), "bad")

    def test_stdlib_vec_sum_helpers(self) -> None:
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

    def test_stdlib_math_helpers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / "main.sprout"
            main.write_text(
                """
                module main
                import stdlib.collections (Maybe)
                import stdlib.math (abs, clamp, gcd, is_even, is_odd, lcm, max, min, mod, pow, sign)

                fn unwrap_or(value: Maybe Int, fallback: Int) -> Int =
                  match value with
                  | Just n -> n
                  | Nothing -> fallback

                fn seq(a: Unit !{IO}, b: Unit !{IO}) -> Unit !{IO} = b

                fn main() -> Unit !{IO} =
                  seq(
                    print(abs(-7)),
                    seq(
                      print(min(3, -2)),
                      seq(
                        print(max(3, -2)),
                        seq(
                          print(clamp(15, 0, 10)),
                          seq(
                            print(sign(-9)),
                            seq(
                              print(sign(0)),
                              seq(
                                print(sign(9)),
                                seq(
                                  print(unwrap_or(pow(2, 10), -1)),
                                  seq(
                                    print(unwrap_or(pow(2, -1), -1)),
                                    seq(
                                      print(unwrap_or(mod(-17, 5), -1)),
                                      seq(
                                        print(unwrap_or(mod(17, 0), -1)),
                                        seq(
                                          print(unwrap_or(mod(17, -5), -1)),
                                          seq(
                                            print(gcd(54, 24)),
                                            seq(
                                              print(gcd(0, 9)),
                                              seq(
                                                print(lcm(6, 8)),
                                                seq(
                                                  print(lcm(0, 9)),
                                                  seq(
                                                    print(is_even(10)),
                                                    seq(
                                                      print(is_even(7)),
                                                      seq(print(is_odd(10)), print(is_odd(7)))
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
            self.assertEqual(
                out.getvalue().strip(),
                "\n".join(
                    [
                        "7",
                        "-2",
                        "3",
                        "10",
                        "-1",
                        "0",
                        "1",
                        "1024",
                        "-1",
                        "3",
                        "-1",
                        "-1",
                        "6",
                        "9",
                        "24",
                        "0",
                        "True",
                        "False",
                        "False",
                        "True",
                    ]
                ),
            )

    def test_stdlib_functor_and_foldable_instances(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / "main.sprout"
            main.write_text(
                """
                module main
                import stdlib.collections (Functor, Foldable, List, Vec, vec_append, vec_empty)

                fn add_one(x: Int) -> Int = x + 1
                fn add(acc: Int, x: Int) -> Int = acc + x

                fn sample_list() -> List Int =
                  Cons(1, Cons(2, Cons(3, Nil)))

                fn sample_vec() -> Vec Int =
                  vec_append(6, vec_append(5, vec_append(4, vec_empty())))

                fn sum_after_map(xs: c) -> Int where Functor c, Foldable c =
                  fold_values(add, 0, fmap(add_one, xs))

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

    def test_stdlib_semigroup_instances(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / "main.sprout"
            main.write_text(
                """
                module main
                import stdlib.collections (Dict, List, Maybe, Semigroup, Vec, dict_empty, dict_get, dict_set, vec_append, vec_empty, vec_get_or)

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

                fn list_count(xs: List Int) -> Int =
                  match xs with
                  | Nil -> 0
                  | Cons _ rest -> 1 + list_count(rest)

                fn append_string(x: String, y: String) -> String where Semigroup String =
                  append(x, y)

                fn append_list(xs: List Int, ys: List Int) -> List Int where Semigroup (List Int) =
                  append(xs, ys)

                fn append_vec(left: Vec Int, right: Vec Int) -> Vec Int where Semigroup (Vec Int) =
                  append(left, right)

                fn append_dict(left: Dict Int, right: Dict Int) -> Dict Int where Semigroup (Dict Int) =
                  append(left, right)

                fn seq(a: Unit !{IO}, b: Unit !{IO}) -> Unit !{IO} = b

                fn main() -> Unit !{IO} =
                  seq(
                    print(append_string("sprout", "-lang")),
                    seq(
                      print(list_count(append_list(Cons(1, Nil), Cons(2, Cons(3, Nil))))),
                      seq(
                        print(vec_get_or(2, -1, append_vec(left_vec(), right_vec()))),
                        print(
                          value_or(append_dict(left_dict(), right_dict()), "shared", -1)
                          + value_or(append_dict(left_dict(), right_dict()), "b", -1)
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
            lowered = lower_typeclasses(program)
            typecheck_program(lowered)
            out = io.StringIO()
            run_program(lowered, stdout=out)
            self.assertEqual(out.getvalue().strip(), "sprout-lang\n3\n3\n11")

    def test_stdlib_semigroup_append_operator(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / "main.sprout"
            main.write_text(
                """
                module main
                import stdlib.collections (Dict, List, Maybe, Vec, dict_empty, dict_get, dict_set, vec_append, vec_empty, vec_get_or)

                fn seq(a: Unit !{IO}, b: Unit !{IO}) -> Unit !{IO} = b

                fn value_or(d: Dict Int, key: String, fallback: Int) -> Int =
                  match dict_get(key, d) with
                  | Just value -> value
                  | Nothing -> fallback

                fn main() -> Unit !{IO} =
                  seq(
                    print("sprout" ++ "-lang"),
                    seq(
                      print([1, 2] ++ [3, 4]),
                      seq(
                        print(vec_get_or(1, -1, vec_append(1, vec_empty()) ++ vec_append(2, vec_empty()))),
                        print(value_or(dict_set("x", 1, dict_empty()) ++ dict_set("x", 9, dict_empty()), "x", -1))
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
            lowered = lower_typeclasses(program)
            typecheck_program(lowered)
            out = io.StringIO()
            run_program(lowered, stdout=out)
            self.assertEqual(out.getvalue().strip(), "sprout-lang\nCons(1, Cons(2, Cons(3, Cons(4, Nil))))\n2\n9")


if __name__ == "__main__":
    unittest.main()
