from __future__ import annotations

import io
import os
import socket
import tempfile
import threading
import time
from pathlib import Path
import unittest
from unittest.mock import patch

from sprout import parse, run_program, typecheck_program
from sprout.stdlib import with_http_prelude, with_prelude


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


if __name__ == "__main__":
    unittest.main()
