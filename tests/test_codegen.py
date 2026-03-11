from __future__ import annotations

import unittest
import shutil
import subprocess
import tempfile
from pathlib import Path
import socket
import sys
import time

from sprout import CodegenError, compile_to_llvm, parse, typecheck_program


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
        self.assertIn("define i64 @main()", ir)
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
        self.assertIn("define i64 @main()", ir)
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


if __name__ == "__main__":
    unittest.main()
