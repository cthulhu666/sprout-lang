from __future__ import annotations

import unittest

from tests.codegen_test_support import *


class CodegenNativeRuntimeTests(CodegenTestCase):
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
