from __future__ import annotations

from http.server import BaseHTTPRequestHandler, HTTPServer
import unittest
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path
import socket
import sys
import time

from sprout import CodegenError, compile_to_llvm, parse, typecheck_program
from sprout.module_loader import load_module_bundle, resolve_program_names
from sprout.stdlib import with_http_prelude


class CodegenTests(unittest.TestCase):
    @staticmethod
    def _find_free_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            return probe.getsockname()[1]

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
        self.assertIn("call i64 %f(i64 %x)", ir)

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
                import stdlib.net (Result, TcpConnection, TcpError, connect, read_exact, write_all)

                fn main() -> IO Unit =
                  match connect("127.0.0.1", 5432) with
                  | Err _ -> print("err")
                  | Ok conn ->
                      match write_all(conn, "ping") with
                      | Err _ -> print("write")
                      | Ok _ ->
                          match read_exact(conn, 4) with
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
            self.assertIn("declare i64 @tcp_write_all(i64, ptr)", ir)

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
        self.assertIn("declare i64 @str_find(ptr, ptr)", ir)
        self.assertIn("declare i1 @str_starts_with(ptr, ptr)", ir)

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
    def test_native_tcp_echo_once(self) -> None:
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

            proc = subprocess.Popen(
                [str(bin_path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            echoed = ""
            deadline = time.time() + 2.0
            while time.time() < deadline:
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.2) as client:
                        client.sendall(b"native-echo")
                        client.shutdown(socket.SHUT_WR)
                        echoed = client.recv(4096).decode("utf-8", errors="replace")
                        break
                except OSError:
                    time.sleep(0.02)

            run = proc.communicate(timeout=2.0)
            self.assertEqual(proc.returncode, 0, msg=run[1])
            self.assertEqual(echoed, "native-echo")

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_native_typed_tcp_client_builtins(self) -> None:
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
            tmp_path = Path(tmp)
            spr_path = tmp_path / "prog.sprout"
            bin_path = tmp_path / "prog"
            spr_path.write_text(
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
            self.assertEqual(run.stdout.strip(), "pong")

        server_thread.join(timeout=2.0)
        self.assertEqual(received, [b"ping"])

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
                self.assertEqual(run.stdout.strip(), "ok:hello")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=1.0)

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_native_http_request_http_error(self) -> None:
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
                self.assertEqual(run.stdout.strip(), "404")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=1.0)

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
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

        try:
            server = HTTPServer(("127.0.0.1", 0), Handler)
        except PermissionError:
            self.skipTest("network socket bind not permitted in this environment")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            src = """
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

            fn main() -> IO Unit =
              match argv_get(0) with
              | Nothing -> print("missing")
              | Just url ->
                  match http_get(url, "", 5000) with
                  | Ok resp -> print(http_response_body(resp))
                  | Err err -> print(http_error_message(err))
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
                run = subprocess.run(
                    [str(bin_path), f"http://127.0.0.1:{port}/ok"],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(run.returncode, 0, msg=run.stderr)
                self.assertEqual(run.stdout.strip(), "module-http-ok")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=1.0)

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_native_http_request_remote_close_without_response(self) -> None:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            listener.bind(("127.0.0.1", 0))
        except PermissionError:
            listener.close()
            self.skipTest("network socket bind not permitted in this environment")
        listener.listen(1)
        port = listener.getsockname()[1]

        def close_immediately() -> None:
            try:
                conn, _ = listener.accept()
                conn.close()
            finally:
                listener.close()

        thread = threading.Thread(target=close_immediately, daemon=True)
        thread.start()
        try:
            src = f"""
            fn main() -> IO Unit =
              match http_request("GET", "http://127.0.0.1:{port}/", "", "", 500) with
              | Ok _ -> print("ok")
              | Err err ->
                  match err with
                  | HttpNetwork msg -> print(msg)
                  | HttpTimeout -> print("timeout")
                  | HttpBadStatus _ -> print("bad-status")
                  | HttpDecode _ -> print("decode")
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
                run = subprocess.run([str(bin_path)], check=False, capture_output=True, text=True)
                self.assertEqual(run.returncode, 0, msg=run.stderr)
                self.assertIn("remote closed connection without response", run.stdout)
        finally:
            thread.join(timeout=1.0)


if __name__ == "__main__":
    unittest.main()
