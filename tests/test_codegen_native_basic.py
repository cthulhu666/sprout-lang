from __future__ import annotations

import unittest

from tests.codegen_test_support import *


class CodegenNativeBasicTests(CodegenTestCase):
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
    def test_native_vec_sort_by(self) -> None:
        src = """
        fn key(value: IntRange) -> Int =
          range_start(value)

        fn sample() -> Vec IntRange =
          vec_append(range(3, 4), vec_append(range(1, 2), vec_append(range(1, 3), vec_empty())))

        fn encode(value: Vec IntRange) -> Int =
          range_end(vec_get_or(0, range(0, 0), value)) * 10 +
          range_end(vec_get_or(1, range(0, 0), value))

        fn main() -> Unit !{IO} =
          print(encode(vec_sort_by(key, sample())))
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
                    "--with-stdlib",
                    "--native",
                    "-o",
                    str(bin_path),
                ],
                check=True,
            )
            run = subprocess.run([str(bin_path)], check=False, capture_output=True, text=True)
            self.assertEqual(run.stdout.strip(), "32")
            self.assertEqual(run.returncode, 0)

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_native_vec_sort_strings(self) -> None:
        src = """
        fn sorted() -> Vec String =
          vec_sort(vec_append("beta", vec_append("alpha", vec_empty())))

        fn seq(a: Unit !{IO}, b: Unit !{IO}) -> Unit !{IO} = b

        fn main() -> Unit !{IO} =
          seq(
            print(vec_get_or(0, "", sorted()) == "alpha"),
            print(vec_get_or(1, "", sorted()) == "beta")
          )
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
                    "--with-stdlib",
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
    def test_native_show_to_string(self) -> None:
        src = """
        fn main() -> Unit !{IO} =
          print(to_string(-42))
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
                    "--with-stdlib",
                    "--native",
                    "-o",
                    str(bin_path),
                ],
                check=True,
            )
            run = subprocess.run([str(bin_path)], check=False, capture_output=True, text=True)
            self.assertEqual(run.stdout.strip(), "-42")
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
    def test_native_string_lines_handles_crlf_without_trailing_empty_line(self) -> None:
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
                  print(vec_get_or(1, "missing", string_lines("alpha\\r\\nbeta\\r\\n")))
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

    @unittest.skipUnless(hasattr(os, "openpty") and shutil.which("clang"), "pty/native prerequisites unavailable")
    def test_native_term_read_key_builtin_normalizes_up_arrow(self) -> None:
        src = """
        module main

        fn main() -> Unit !{IO} =
          print(term_read_key())
        """
        with compiled_native_binary(self, src) as bin_path:
            master_fd, slave_fd = pty.openpty()
            proc = subprocess.Popen(
                [str(bin_path)],
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                close_fds=True,
            )
            os.close(slave_fd)
            try:
                os.write(master_fd, b"\x1b[A")
                output = b""
                deadline = time.time() + 5.0
                while time.time() < deadline and b"up" not in output:
                    chunk = os.read(master_fd, 1024)
                    if not chunk:
                        break
                    output += chunk
                proc.wait(timeout=5)
                self.assertEqual(proc.returncode, 0)
                self.assertIn("up", output.decode("utf-8", errors="replace"))
            finally:
                os.close(master_fd)
                if proc.poll() is None:
                    proc.kill()
                    proc.wait(timeout=5)

            run = subprocess.run([str(bin_path)], check=False, capture_output=True, text=True, input="\x01")
            self.assertEqual(run.stdout.strip(), "ctrl-a")
            self.assertEqual(run.returncode, 0)

            run = subprocess.run([str(bin_path)], check=False, capture_output=True, text=True, input="\x05")
            self.assertEqual(run.stdout.strip(), "ctrl-e")
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


if __name__ == "__main__":
    unittest.main()
