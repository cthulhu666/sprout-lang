from __future__ import annotations

from http.server import BaseHTTPRequestHandler
import shutil
import socket
import subprocess
import sys
from pathlib import Path
import unittest
from unittest.mock import patch

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from integration_support import (
    compiled_native_binary,
    connect_with_retry,
    find_free_port,
    running_http_server,
    running_tcp_fixture,
    scaled_timeout,
    tcp_roundtrip,
)


@unittest.skipUnless(shutil.which("clang"), "clang not installed")
class NativeIoIntegrationTests(unittest.TestCase):
    def _run_native(
        self,
        source: str,
        *,
        argv: list[str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        argv = argv or []
        with compiled_native_binary(self, source) as bin_path:
            return subprocess.run([str(bin_path), *argv], check=False, capture_output=True, text=True)

    def test_compiled_native_binary_caches_identical_sources(self) -> None:
        source = """
        fn main() -> Unit !{IO} =
          print("cache-probe-7f6f")
        """

        def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
            cmd = args[0]
            self.assertIsInstance(cmd, list)
            out_path = Path(cmd[cmd.index("-o") + 1])
            out_path.write_text("fake native binary", encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        with patch("integration_support.subprocess.run", side_effect=fake_run) as mocked_run:
            with compiled_native_binary(self, source) as first:
                self.assertTrue(first.exists())
            with compiled_native_binary(self, source) as second:
                self.assertEqual(first, second)
            with compiled_native_binary(self, source) as third:
                self.assertEqual(first, third)
        self.assertEqual(mocked_run.call_count, 1)

    def test_native_tcp_echo_once(self) -> None:
        port = find_free_port(self)
        src = f"""
        fn seq(a: Unit !{{IO}}, b: Unit !{{IO}}) -> Unit !{{IO}} = b

        fn handle_conn(conn: Int) -> Unit !{{IO}} =
          seq(tcp_write(conn, tcp_read(conn)), tcp_close(conn))

        fn serve_once(listener: Int) -> Unit !{{IO}} =
          handle_conn(tcp_accept(listener))

        fn close_after_serve(listener: Int) -> Unit !{{IO}} =
          seq(serve_once(listener), tcp_close_listener(listener))

        fn main() -> Unit !{{IO}} =
          close_after_serve(tcp_listen({port}))
        """
        with compiled_native_binary(self, src) as bin_path:
            proc = subprocess.Popen(
                [str(bin_path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                echoed = tcp_roundtrip(port, b"native-echo").decode("utf-8", errors="replace")
                stdout, stderr = proc.communicate(timeout=scaled_timeout(2.0))
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.communicate(timeout=scaled_timeout(2.0))
        self.assertEqual(proc.returncode, 0, msg=stderr)
        self.assertEqual(stdout, "")
        self.assertEqual(echoed, "native-echo")

    def test_native_typed_tcp_client_builtins(self) -> None:
        received: list[bytes] = []

        def handle(conn: socket.socket) -> None:
            received.append(conn.recv(4))
            conn.sendall(b"pong")

        with running_tcp_fixture(self, handle) as port:
            run = self._run_native(
                f"""
                module main
                import stdlib.net (Result, TcpConnection, TcpError, close, connect, read_exact_utf8, tcp_error_message, write_all_utf8)

                fn seq(a: Unit !{{IO}}, b: Unit !{{IO}}) -> Unit !{{IO}} = b

                fn finish(conn: TcpConnection, message: String) -> Unit !{{IO}} =
                  seq(close(conn), print(message))

                fn with_conn(conn: TcpConnection) -> Unit !{{IO}} =
                  match write_all_utf8(conn, "ping") with
                  | Err err -> finish(conn, tcp_error_message(err))
                  | Ok _ ->
                      match read_exact_utf8(conn, 4) with
                      | Err err -> finish(conn, tcp_error_message(err))
                      | Ok body -> finish(conn, body)

                fn main() -> Unit !{{IO}} =
                  match connect("127.0.0.1", {port}) with
                  | Err err -> print(tcp_error_message(err))
                  | Ok conn -> with_conn(conn)
                """
            )
        self.assertEqual(run.returncode, 0, msg=run.stderr)
        self.assertEqual(run.stdout.strip(), "pong")
        self.assertEqual(received, [b"ping"])

    def test_native_http_request_success(self) -> None:
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

        with running_http_server(self, Handler) as port:
            run = self._run_native(
                f"""
                module main
                import stdlib.http (HttpResponse, http_response_body)

                fn main() -> Unit !{{IO}} =
                  match http_request("POST", "http://127.0.0.1:{port}/echo", "X-Test: yes", "hello", 500) with
                  | Ok resp -> print(http_response_body(resp))
                  | Err _ -> print("err")
                """
            )
        self.assertEqual(run.returncode, 0, msg=run.stderr)
        self.assertEqual(run.stdout.strip(), "ok:hello")

    def test_native_http_request_chunked_json_body(self) -> None:
        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self) -> None:  # noqa: N802
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                for chunk in (b'{"ok":', b"true", b',"items":[1,2]}'):
                    self.wfile.write(f"{len(chunk):X}\r\n".encode("ascii"))
                    self.wfile.write(chunk + b"\r\n")
                self.wfile.write(b"0\r\n\r\n")

            def log_message(self, format: str, *args: object) -> None:
                return

        with running_http_server(self, Handler) as port:
            run = self._run_native(
                f"""
                module main
                import stdlib.http (HttpResponse)
                import stdlib.json as json

                fn render_json_body(body: String) -> String =
                  match json.parse(body) with
                  | Ok value -> json.stringify(value)
                  | Err _ -> "json-err"

                fn main() -> Unit !{{IO}} =
                  match http_request("GET", "http://127.0.0.1:{port}/chunked", "", "", 500) with
                  | Ok (HttpResponse _ _ body) -> print(render_json_body(body))
                  | Err _ -> print("http-err")
                """
            )
        self.assertEqual(run.returncode, 0, msg=run.stderr)
        self.assertEqual(run.stdout.strip(), '{"ok":true,"items":[1,2]}')

    def test_native_http_request_http_error(self) -> None:
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                self.send_response(404)
                self.end_headers()

            def log_message(self, format: str, *args: object) -> None:
                return

        with running_http_server(self, Handler) as port:
            run = self._run_native(
                f"""
                module main
                import stdlib.http (HttpError)

                fn main() -> Unit !{{IO}} =
                  match http_request("GET", "http://127.0.0.1:{port}/missing", "", "", 500) with
                  | Ok _ -> print(0)
                  | Err e ->
                      match e with
                      | HttpBadStatus code -> print(code)
                      | HttpTimeout -> print(-1)
                      | HttpNetwork _ -> print(-2)
                      | HttpDecode _ -> print(-3)
                """
            )
        self.assertEqual(run.returncode, 0, msg=run.stderr)
        self.assertEqual(run.stdout.strip(), "404")

    def test_native_module_http_client_with_argv_and_collections_maybe(self) -> None:
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                payload = b"module-http-ok"
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, format: str, *args: object) -> None:
                return

        with running_http_server(self, Handler) as port:
            run = self._run_native(
                """
                module app.main
                import stdlib.collections (Maybe)
                import stdlib.http (Result, HttpError, http_response_body)
                import stdlib.http_client (http_get)

                fn http_error_message(err: HttpError) -> String =
                  match err with
                  | HttpTimeout -> "request timed out"
                  | HttpNetwork msg -> "network error: " ++ msg
                  | HttpBadStatus _ -> "http error status"
                  | HttpDecode msg -> "decode error: " ++ msg

                fn main() -> Unit !{IO} =
                  match argv_get(0) with
                  | Nothing -> print("missing")
                  | Just url ->
                      match http_get(url, "", 5000) with
                      | Ok resp -> print(http_response_body(resp))
                      | Err err -> print(http_error_message(err))
                """,
                argv=[f"http://127.0.0.1:{port}/ok"],
            )
        self.assertEqual(run.returncode, 0, msg=run.stderr)
        self.assertEqual(run.stdout.strip(), "module-http-ok")

    def test_native_http_request_remote_close_without_response(self) -> None:
        def handle(conn: socket.socket) -> None:
            conn.close()

        with running_tcp_fixture(self, handle) as port:
            run = self._run_native(
                f"""
                module main
                import stdlib.http (HttpError)

                fn main() -> Unit !{{IO}} =
                  match http_request("GET", "http://127.0.0.1:{port}/", "", "", 500) with
                  | Ok _ -> print("ok")
                  | Err err ->
                      match err with
                      | HttpNetwork msg -> print(msg)
                      | HttpTimeout -> print("timeout")
                      | HttpBadStatus _ -> print("bad-status")
                      | HttpDecode _ -> print("decode")
                """
            )
        self.assertEqual(run.returncode, 0, msg=run.stderr)
        self.assertIn("remote closed connection without response", run.stdout)

    def test_native_http_server_module_serves_structured_request(self) -> None:
        port = find_free_port(self)
        src = f"""
        module main
        import stdlib.http_server (HttpRequest, HttpServerResponse, ok, request_body, request_path, serve_n, with_header)

        fn handle(req: HttpRequest) -> HttpServerResponse =
          with_header("X-Path", request_path(req), ok(request_body(req)))

        fn main() -> Unit !{{IO}} =
          serve_n({port}, 1, handle)
        """
        with compiled_native_binary(self, src) as bin_path:
            proc = subprocess.Popen(
                [str(bin_path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                response = tcp_roundtrip(
                    port,
                    b"POST /native HTTP/1.1\r\nHost: local\r\nContent-Length: 5\r\n\r\nhello",
                ).decode("utf-8", errors="replace")
                stdout, stderr = proc.communicate(timeout=scaled_timeout(2.0))
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.communicate(timeout=scaled_timeout(2.0))
        self.assertEqual(proc.returncode, 0, msg=stderr)
        self.assertEqual(stdout, "")
        self.assertIn("HTTP/1.1 200 OK", response)
        self.assertIn("x-path: /native", response)
        self.assertTrue(response.endswith("hello"), msg=response)

    def test_native_tcp_accept_reuses_closed_connection_slots(self) -> None:
        port = find_free_port(self)
        src = f"""
        fn seq(a: Unit !{{IO}}, b: Unit !{{IO}}) -> Unit !{{IO}} = b

        fn handle(conn: Int) -> Unit !{{IO}} =
          tcp_close(conn)

        fn serve(listener: Int, remaining: Int) -> Unit !{{IO}} =
          if remaining == 0 then tcp_close_listener(listener)
          else seq(handle(tcp_accept(listener)), serve(listener, remaining - 1))

        fn main() -> Unit !{{IO}} =
          serve(tcp_listen({port}), 2100)
        """
        with compiled_native_binary(self, src) as bin_path:
            proc = subprocess.Popen(
                [str(bin_path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                with connect_with_retry(port):
                    pass
                for _ in range(2099):
                    with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                        pass
                stdout, stderr = proc.communicate(timeout=scaled_timeout(5.0))
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.communicate(timeout=scaled_timeout(2.0))
        self.assertEqual(proc.returncode, 0, msg=stderr)
        self.assertEqual(stdout, "")


if __name__ == "__main__":
    unittest.main()
