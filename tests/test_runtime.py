from __future__ import annotations

from http.server import BaseHTTPRequestHandler, HTTPServer
import io
import os
import socket
import tempfile
import threading
import time
from pathlib import Path
import unittest
from unittest.mock import patch

from sprout import RuntimeError, parse, run_program, typecheck_program
from sprout.module_loader import load_module_bundle, resolve_program_names
from sprout.stdlib import with_http_prelude, with_prelude
from sprout.typeclass_lowering import lower_typeclasses


class RuntimeTests(unittest.TestCase):
    @staticmethod
    def _find_free_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            return probe.getsockname()[1]

    def test_run_main_prints_result(self) -> None:
        src = """
        fn fact(n: Int) -> Int =
          if n == 0 then 1 else n * fact(n - 1)

        fn main() -> IO Unit =
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

        fn main() -> IO Unit =
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

        fn main() -> IO Unit =
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

        fn main() -> IO Unit =
          print(fold(split_ints("1, 2 3 4"), 0, add))
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

        fn main() -> IO Unit =
          print(
            result_with_default(
              result_map_error(
                result_and_then(
                  result_map(Ok(20), plus1),
                  twice
                ),
                tag
              ),
              0
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

        fn main() -> IO Unit =
          print(
            result_with_default(
              result_pipe_error(
                result_pipe_ok(
                  result_pipe(
                    Ok(pipe(20, plus1)),
                    twice
                  ),
                  plus1
                ),
                tag
              ),
              0
            )
          )
        """
        program = parse(with_prelude(src))
        typecheck_program(program)
        out = io.StringIO()
        run_program(program, stdout=out)
        self.assertEqual(out.getvalue().strip(), "43")

    def test_forward_pipe_operator_with_result_helpers(self) -> None:
        src = """
        fn plus1(x: Int) -> Int = x + 1
        fn twice(x: Int) -> Result String Int = Ok(x * 2)
        fn tag(e: String) -> String = str_concat("err:", e)

        fn main() -> IO Unit =
          print(
            result_with_default(
              Ok(20)
              |> result_pipe_ok(plus1)
              |> result_pipe(twice)
              |> result_pipe_ok(plus1)
              |> result_pipe_error(tag),
              0
            )
          )
        """
        program = parse(with_prelude(src))
        typecheck_program(program)
        out = io.StringIO()
        run_program(program, stdout=out)
        self.assertEqual(out.getvalue().strip(), "43")

    def test_stdlib_when_ok_and_when_error_helpers(self) -> None:
        src = """
        fn show_ok(x: Int) -> IO Unit = print(x)
        fn show_err(e: String) -> IO Unit = print(e)

        fn main() -> IO Unit =
          print(
            result_with_default(
              when_error(
                when_ok(Ok(42), show_ok),
                show_err
              ),
              0
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
        fn show_err(e: String) -> IO Unit = print(e)

        fn main() -> IO Unit =
          print(
            result_with_default(
              when_error(Err("boom"), show_err),
              7
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

            fn main() -> IO Unit =
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

        fn main() -> IO Unit =
          print((double >> inc)(20))
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

        fn main() -> IO Unit =
          print(sum_down(5000, 0))
        """
        program = parse(src)
        typecheck_program(program)
        out = io.StringIO()
        run_program(program, stdout=out)
        self.assertEqual(out.getvalue().strip(), "12502500")

    def test_read_file_builtin_missing_path_reports_runtime_error_convention(self) -> None:
        src = """
        fn main() -> IO Unit =
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
        fn main() -> IO Unit =
          tcp_close(1)
        """
        program = parse(src)
        typecheck_program(program)
        with self.assertRaises(RuntimeError) as ctx:
            run_program(program)
        self.assertIn("runtime error: builtin `tcp_close`: got unknown connection handle", str(ctx.exception))

    def test_tcp_echo_single_connection(self) -> None:
        try:
            port = self._find_free_port()
        except PermissionError:
            self.skipTest("network socket bind not permitted in this environment")
        src = f"""
        fn seq(a: IO Unit, b: IO Unit) -> IO Unit = b

        fn handle_conn(conn: Int) -> IO Unit =
          seq(tcp_write(conn, tcp_read(conn)), tcp_close(conn))

        fn serve_once(listener: Int) -> IO Unit =
          handle_conn(tcp_accept(listener))

        fn close_after_serve(listener: Int) -> IO Unit =
          seq(serve_once(listener), tcp_close_listener(listener))

        fn main() -> IO Unit =
          close_after_serve(tcp_listen({port}))
        """
        program = parse(src)
        typecheck_program(program)

        errors: list[BaseException] = []

        def server() -> None:
            try:
                run_program(program)
            except BaseException as exc:  # pragma: no cover
                errors.append(exc)

        server_thread = threading.Thread(target=server, daemon=True)
        server_thread.start()

        request = "echo me"
        response = ""
        deadline = time.time() + 2.0
        while time.time() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.5) as client:
                    client.sendall(request.encode("utf-8"))
                    client.shutdown(socket.SHUT_WR)
                    response = client.recv(4096).decode("utf-8", errors="replace")
                    break
            except OSError:
                time.sleep(0.02)

        server_thread.join(timeout=2.0)
        self.assertFalse(server_thread.is_alive(), "echo server did not exit after one connection")
        self.assertFalse(errors, f"server thread raised: {errors!r}")
        self.assertEqual(response, request)

    def test_tcp_echo_serve_builtin_reactor(self) -> None:
        try:
            port = self._find_free_port()
        except PermissionError:
            self.skipTest("network socket bind not permitted in this environment")
        src = f"fn main() -> IO Unit = tcp_echo_serve({port}, 1)"
        program = parse(src)
        typecheck_program(program)
        errors: list[BaseException] = []

        def server() -> None:
            try:
                run_program(program)
            except BaseException as exc:  # pragma: no cover
                errors.append(exc)

        with patch.dict(os.environ, {"SPROUT_NET_MODEL": "reactor"}, clear=False):
            server_thread = threading.Thread(target=server, daemon=True)
            server_thread.start()
            response = ""
            deadline = time.time() + 2.0
            while time.time() < deadline:
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.5) as client:
                        client.sendall(b"reactor")
                        client.shutdown(socket.SHUT_WR)
                        response = client.recv(4096).decode("utf-8", errors="replace")
                        break
                except OSError:
                    time.sleep(0.02)
            server_thread.join(timeout=2.0)

        self.assertFalse(server_thread.is_alive(), "reactor server did not exit after one connection")
        self.assertFalse(errors, f"reactor server thread raised: {errors!r}")
        self.assertEqual(response, "reactor")

    def test_tcp_echo_serve_builtin_blocking_backend(self) -> None:
        try:
            port = self._find_free_port()
        except PermissionError:
            self.skipTest("network socket bind not permitted in this environment")
        src = f"fn main() -> IO Unit = tcp_echo_serve({port}, 1)"
        program = parse(src)
        typecheck_program(program)
        errors: list[BaseException] = []

        def server() -> None:
            try:
                run_program(program)
            except BaseException as exc:  # pragma: no cover
                errors.append(exc)

        with patch.dict(os.environ, {"SPROUT_NET_MODEL": "blocking"}, clear=False):
            server_thread = threading.Thread(target=server, daemon=True)
            server_thread.start()
            response = ""
            deadline = time.time() + 2.0
            while time.time() < deadline:
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.5) as client:
                        client.sendall(b"blocking")
                        client.shutdown(socket.SHUT_WR)
                        response = client.recv(4096).decode("utf-8", errors="replace")
                        break
                except OSError:
                    time.sleep(0.02)
            server_thread.join(timeout=2.0)

        self.assertFalse(server_thread.is_alive(), "blocking server did not exit after one connection")
        self.assertFalse(errors, f"blocking server thread raised: {errors!r}")
        self.assertEqual(response, "blocking")

    def test_tcp_client_builtins_success(self) -> None:
        try:
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            listener.bind(("127.0.0.1", 0))
        except PermissionError:
            self.skipTest("network socket bind not permitted in this environment")
        listener.listen(1)
        port = listener.getsockname()[1]
        received: list[bytes] = []

        def server() -> None:
            with listener:
                conn, _ = listener.accept()
                with conn:
                    received.append(conn.recv(4))
                    conn.sendall(b"pong")

        server_thread = threading.Thread(target=server, daemon=True)
        server_thread.start()

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / "main.sprout"
            main.write_text(
                f"""
                module main
                import stdlib.net (Result, TcpConnection, TcpError, close, connect, read_exact, tcp_error_message, write_all)

                fn seq(a: IO Unit, b: IO Unit) -> IO Unit = b

                fn finish(conn: TcpConnection, message: String) -> IO Unit =
                  seq(close(conn), print(message))

                fn with_conn(conn: TcpConnection) -> IO Unit =
                  match write_all(conn, "ping") with
                  | Err err -> finish(conn, tcp_error_message(err))
                  | Ok _ ->
                      match read_exact(conn, 4) with
                      | Err err -> finish(conn, tcp_error_message(err))
                      | Ok body -> finish(conn, body)

                fn main() -> IO Unit =
                  match connect("127.0.0.1", {port}) with
                  | Err err -> print(tcp_error_message(err))
                  | Ok conn -> with_conn(conn)
                """,
                encoding="utf-8",
            )
            bundle = load_module_bundle(main)
            program = parse(bundle.source)
            resolve_program_names(program, bundle)
            typecheck_program(program)
            out = io.StringIO()
            run_program(program, stdout=out)
            self.assertEqual(out.getvalue().strip(), "pong")

        server_thread.join(timeout=2.0)
        self.assertEqual(received, [b"ping"])

    def test_tcp_read_exact_reports_end_of_stream(self) -> None:
        try:
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            listener.bind(("127.0.0.1", 0))
        except PermissionError:
            self.skipTest("network socket bind not permitted in this environment")
        listener.listen(1)
        port = listener.getsockname()[1]

        def server() -> None:
            with listener:
                conn, _ = listener.accept()
                with conn:
                    conn.sendall(b"hi")

        server_thread = threading.Thread(target=server, daemon=True)
        server_thread.start()

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / "main.sprout"
            main.write_text(
                f"""
                module main
                import stdlib.net (Result, TcpConnection, TcpError, close, connect, read_exact, tcp_error_message)

                fn seq(a: IO Unit, b: IO Unit) -> IO Unit = b

                fn main() -> IO Unit =
                  match connect("127.0.0.1", {port}) with
                  | Err err -> print(tcp_error_message(err))
                  | Ok conn ->
                      match read_exact(conn, 4) with
                      | Ok body -> seq(close(conn), print(body))
                      | Err TcpEndOfStream -> seq(close(conn), print("eof"))
                      | Err err -> seq(close(conn), print(tcp_error_message(err)))
                """,
                encoding="utf-8",
            )
            bundle = load_module_bundle(main)
            program = parse(bundle.source)
            resolve_program_names(program, bundle)
            typecheck_program(program)
            out = io.StringIO()
            run_program(program, stdout=out)
            self.assertEqual(out.getvalue().strip(), "eof")

        server_thread.join(timeout=2.0)

    def test_string_builtins(self) -> None:
        src = """
        fn seq(a: IO Unit, b: IO Unit) -> IO Unit = b

        fn main() -> IO Unit =
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
        fn main() -> IO Unit =
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
        fn main() -> IO Unit =
          print(http_response_body(HttpResponse(200, "h: v", "payload")))
        """
        program = parse(with_http_prelude(src))
        typecheck_program(program)
        out = io.StringIO()
        run_program(program, stdout=out)
        self.assertEqual(out.getvalue().strip(), "payload")

    def test_http_request_builtin_success(self) -> None:
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                size = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(size).decode("utf-8", errors="replace")
                payload = f"ok:{body}".encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, format: str, *args: object) -> None:
                return

        try:
            server = HTTPServer(("127.0.0.1", 0), Handler)
        except PermissionError:
            self.skipTest("network socket bind not permitted in this environment")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            src = f"""
            fn main() -> IO Unit =
              match http_request("POST", "http://127.0.0.1:{port}/echo", "X-Test: yes", "hello", 500) with
              | Ok resp -> print(http_response_body(resp))
              | Err _ -> print("err")
            """
            program = parse(with_http_prelude(src))
            typecheck_program(program)
            out = io.StringIO()
            run_program(program, stdout=out)
            self.assertEqual(out.getvalue().strip(), "ok:hello")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=1.0)

    def test_http_request_builtin_http_error(self) -> None:
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                self.send_response(404)
                self.end_headers()

            def log_message(self, format: str, *args: object) -> None:
                return

        try:
            server = HTTPServer(("127.0.0.1", 0), Handler)
        except PermissionError:
            self.skipTest("network socket bind not permitted in this environment")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            src = f"""
            fn main() -> IO Unit =
              match http_request("GET", "http://127.0.0.1:{port}/missing", "", "", 500) with
              | Ok _ -> print(0)
              | Err e ->
                  match e with
                  | HttpBadStatus code -> print(code)
                  | HttpTimeout -> print(-1)
                  | HttpNetwork _ -> print(-2)
                  | HttpDecode _ -> print(-3)
            """
            program = parse(with_http_prelude(src))
            typecheck_program(program)
            out = io.StringIO()
            run_program(program, stdout=out)
            self.assertEqual(out.getvalue().strip(), "404")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=1.0)

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

        fn main() -> IO Unit =
          match json_parse("{\\"title\\":\\"hello\\",\\"count\\":2}") with
          | Ok value -> print(title_or_missing(value))
          | Err _ -> print("decode-error")
        """
        program = parse(with_http_prelude(src))
        typecheck_program(program)
        out = io.StringIO()
        run_program(program, stdout=out)
        self.assertEqual(out.getvalue().strip(), "hello")

    def test_json_parse_invalid(self) -> None:
        src = """
        fn main() -> IO Unit =
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

        fn main() -> IO Unit =
          match json_parse("{\\"items\\":[1,2]}") with
          | Ok value -> print(first_int_from_value(value))
          | Err _ -> print(-6)
        """
        program = parse(with_http_prelude(src))
        typecheck_program(program)
        out = io.StringIO()
        run_program(program, stdout=out)
        self.assertEqual(out.getvalue().strip(), "1")

    def test_terminal_builtins_emit_ansi(self) -> None:
        src = """
        fn seq(a: IO Unit, b: IO Unit) -> IO Unit = b
        fn main() -> IO Unit =
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
        fn main() -> IO Unit =
          print(term_read_key())
        """
        program = parse(src)
        typecheck_program(program)
        out = io.StringIO()
        with patch.dict(os.environ, {"SPROUT_TERM_KEY": "j"}, clear=False):
            run_program(program, stdout=out)
        self.assertEqual(out.getvalue().strip(), "j")

    def test_vector_builtins(self) -> None:
        src = """
        type Maybe a =
          | Just a
          | Nothing

        fn value_or(v: Maybe Int, fallback: Int) -> Int =
          match v with
          | Just x -> x
          | Nothing -> fallback

        fn main() -> IO Unit =
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

        fn main() -> IO Unit =
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

                fn main() -> IO Unit =
                  print(
                    vec_get_or(
                      vec_reverse(
                        vec_slice(
                          vec_append(vec_append(vec_append(vec_append(vec_empty(), 10), 20), 30), 40),
                          1,
                          2
                        )
                      ),
                      0,
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
                  dict_set(dict_set(dict_empty(), "alpha", 7), "beta", 11)

                fn main() -> IO Unit =
                  print(
                    vec_get_or(dict_values(sample()), 0, -100)
                    + string.length(vec_get_or(dict_keys(sample()), 1, ""))
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

                fn value_or_missing(name: String) -> String =
                  match env_get(name) with
                  | Just value -> value
                  | Nothing -> "missing"

                fn main() -> IO Unit =
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

                fn arg_or_missing(index: Int) -> String =
                  match argv_get(index) with
                  | Just value -> value
                  | Nothing -> "missing"

                fn main() -> IO Unit =
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

                fn main() -> IO Unit =
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

    def test_stdlib_bytes_utf8_decode_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / "main.sprout"
            main.write_text(
                """
                module main
                import stdlib.bytes (Result, append, singleton, to_string)

                fn main() -> IO Unit =
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
                  vec_append(vec_append(vec_append(vec_empty(), 4), 5), 6)

                fn sum_after_map(xs: c) -> Int where Functor c, Foldable c =
                  fold_values(fmap(add_one, xs), 0, add)

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

    def test_stdlib_semigroup_instances(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / "main.sprout"
            main.write_text(
                """
                module main
                import stdlib.collections (Dict, List, Maybe, Semigroup, Vec, dict_empty, dict_get, dict_set, vec_append, vec_empty, vec_get_or)

                fn left_vec() -> Vec Int =
                  vec_append(vec_append(vec_empty(), 1), 2)

                fn right_vec() -> Vec Int =
                  vec_append(vec_empty(), 3)

                fn left_dict() -> Dict Int =
                  dict_set(dict_set(dict_empty(), "a", 1), "shared", 7)

                fn right_dict() -> Dict Int =
                  dict_set(dict_set(dict_empty(), "b", 2), "shared", 9)

                fn value_or(d: Dict Int, key: String, fallback: Int) -> Int =
                  match dict_get(d, key) with
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

                fn seq(a: IO Unit, b: IO Unit) -> IO Unit = b

                fn main() -> IO Unit =
                  seq(
                    print(append_string("sprout", "-lang")),
                    seq(
                      print(list_count(append_list(Cons(1, Nil), Cons(2, Cons(3, Nil))))),
                      seq(
                        print(vec_get_or(append_vec(left_vec(), right_vec()), 2, -1)),
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

                fn seq(a: IO Unit, b: IO Unit) -> IO Unit = b

                fn value_or(d: Dict Int, key: String, fallback: Int) -> Int =
                  match dict_get(d, key) with
                  | Just value -> value
                  | Nothing -> fallback

                fn main() -> IO Unit =
                  seq(
                    print("sprout" ++ "-lang"),
                    seq(
                      print([1, 2] ++ [3, 4]),
                      seq(
                        print(vec_get_or(vec_append(vec_empty(), 1) ++ vec_append(vec_empty(), 2), 1, -1)),
                        print(value_or(dict_set(dict_empty(), "x", 1) ++ dict_set(dict_empty(), "x", 9), "x", -1))
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
