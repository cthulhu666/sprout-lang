from __future__ import annotations

import os
import pty
import select
import shutil
import subprocess
import tempfile
import time
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from sprout import cli as sprout_cli
from sprout.analysis_service_python import default_analysis_service_cmd


class CliReplTests(unittest.TestCase):
    _PTY_TIMEOUT = 20.0
    _PROCESS_TIMEOUT = 10.0

    def _read_pty_until(self, fd: int, buffer: str, needle: str, timeout: float = 5.0) -> str:
        deadline = time.monotonic() + timeout
        while needle not in buffer and time.monotonic() < deadline:
            ready, _, _ = select.select([fd], [], [], 0.1)
            if fd not in ready:
                continue
            chunk = os.read(fd, 4096)
            if not chunk:
                break
            buffer += chunk.decode("utf-8", errors="replace").replace("\r", "")
        self.assertIn(needle, buffer)
        return buffer

    def _write_pty_slowly(self, fd: int, data: bytes) -> None:
        for byte in data:
            os.write(fd, bytes([byte]))
            time.sleep(0.03)

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_native_repl_hosted_frontend_runs_end_to_end_non_interactively(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "repl_bin"
            compile_proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "sprout.cli",
                    "compile",
                    "examples/repl_hosted.sprout",
                    "--native",
                    "-o",
                    str(out),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(compile_proc.returncode, 0, msg=compile_proc.stderr)
            env = dict(os.environ)
            env["SPROUT_ANALYSIS_SERVICE_CMD"] = default_analysis_service_cmd()
            run_proc = subprocess.run(
                [str(out)],
                check=False,
                capture_output=True,
                text=True,
                input='let x = 41\nx + 1\n:t x\n:quit\n',
                env=env,
            )
            self.assertEqual(run_proc.returncode, 0, msg=run_proc.stderr)
            self.assertEqual(run_proc.stderr, "")
            self.assertEqual(run_proc.stdout.strip().splitlines(), ["ok", "42", "Int"])

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_native_repl_hosted_frontend_evaluates_bare_typeclass_method_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "repl_bin"
            compile_proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "sprout.cli",
                    "compile",
                    "examples/repl_hosted.sprout",
                    "--native",
                    "-o",
                    str(out),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(compile_proc.returncode, 0, msg=compile_proc.stderr)
            env = dict(os.environ)
            env["SPROUT_ANALYSIS_SERVICE_CMD"] = default_analysis_service_cmd()
            run_proc = subprocess.run(
                [str(out)],
                check=False,
                capture_output=True,
                text=True,
                input="to_string\n:quit\n",
                env=env,
            )
            self.assertEqual(run_proc.returncode, 0, msg=run_proc.stderr)
            self.assertEqual(run_proc.stderr, "")
            self.assertNotIn("Unknown variable to_string", run_proc.stdout)
            self.assertIn("FunctionValue(", run_proc.stdout)

    def test_repl_supports_declarations_expressions_and_type_queries(self) -> None:
        run = subprocess.run(
            [sys.executable, "-m", "sprout.cli", "repl"],
            check=False,
            capture_output=True,
            text=True,
            input="let x = 41\nx + 1\n:t x\n:quit\n",
        )
        self.assertEqual(run.returncode, 0, msg=run.stderr)
        self.assertEqual(run.stderr, "")
        self.assertIn("ok", run.stdout)
        self.assertIn("42", run.stdout)
        self.assertIn("Int", run.stdout)

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_repl_native_launcher_supports_declarations_expressions_and_type_queries(self) -> None:
        run = subprocess.run(
            [sys.executable, "-m", "sprout.cli", "repl", "--native"],
            check=False,
            capture_output=True,
            text=True,
            input="let x = 41\nx + 1\n:t x\n:quit\n",
        )
        self.assertEqual(run.returncode, 0, msg=run.stderr)
        self.assertEqual(run.stderr, "")
        self.assertIn("ok", run.stdout)
        self.assertIn("42", run.stdout)
        self.assertIn("Int", run.stdout)

    def test_repl_block_mode_supports_multiline_function_declaration(self) -> None:
        run = subprocess.run(
            [sys.executable, "-m", "sprout.cli", "repl"],
            check=False,
            capture_output=True,
            text=True,
            input=":{\nfn add(x: Int, y: Int) -> Int =\n  x + y\n:}\nadd(40, 2)\n:quit\n",
        )
        self.assertEqual(run.returncode, 0, msg=run.stderr)
        self.assertEqual(run.stderr, "")
        self.assertIn("ok", run.stdout)
        self.assertIn("42", run.stdout)

    def test_repl_block_mode_runs_mixed_submissions_sequentially(self) -> None:
        run = subprocess.run(
            [sys.executable, "-m", "sprout.cli", "repl"],
            check=False,
            capture_output=True,
            text=True,
            input=":{\nlet x = 5\nlet y = 10\nx * y\n:}\n:quit\n",
        )
        self.assertEqual(run.returncode, 0, msg=run.stderr)
        self.assertEqual(run.stderr, "")
        self.assertIn("ok", run.stdout)
        self.assertIn("50", run.stdout)

    def test_repl_block_mode_supports_multiline_class_declaration(self) -> None:
        run = subprocess.run(
            [sys.executable, "-m", "sprout.cli", "repl"],
            check=False,
            capture_output=True,
            text=True,
            input=":{\nclass Answer a {\n  fn answer(x: a) -> Int\n}\ninstance Answer Int {\n  fn answer(x: Int) -> Int = x\n}\nanswer(42)\n:}\n:quit\n",
        )
        self.assertEqual(run.returncode, 0, msg=run.stderr)
        self.assertEqual(run.stderr, "")
        self.assertIn("ok", run.stdout)
        self.assertIn("42", run.stdout)

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_repl_native_launcher_block_mode_supports_multiline_function_declaration(self) -> None:
        run = subprocess.run(
            [sys.executable, "-m", "sprout.cli", "repl", "--native"],
            check=False,
            capture_output=True,
            text=True,
            input=":{\nfn add(x: Int, y: Int) -> Int =\n  x + y\n:}\nadd(40, 2)\n:quit\n",
        )
        self.assertEqual(run.returncode, 0, msg=run.stderr)
        self.assertEqual(run.stderr, "")
        self.assertIn("ok", run.stdout)
        self.assertIn("42", run.stdout)

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_repl_native_launcher_block_mode_runs_mixed_submissions_sequentially(self) -> None:
        run = subprocess.run(
            [sys.executable, "-m", "sprout.cli", "repl", "--native"],
            check=False,
            capture_output=True,
            text=True,
            input=":{\nlet x = 5\nlet y = 10\nx * y\n:}\n:quit\n",
        )
        self.assertEqual(run.returncode, 0, msg=run.stderr)
        self.assertEqual(run.stderr, "")
        self.assertIn("ok", run.stdout)
        self.assertIn("50", run.stdout)

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_repl_native_launcher_block_mode_supports_multiline_class_declaration(self) -> None:
        run = subprocess.run(
            [sys.executable, "-m", "sprout.cli", "repl", "--native"],
            check=False,
            capture_output=True,
            text=True,
            input=":{\nclass Answer a {\n  fn answer(x: a) -> Int\n}\ninstance Answer Int {\n  fn answer(x: Int) -> Int = x\n}\nanswer(42)\n:}\n:quit\n",
        )
        self.assertEqual(run.returncode, 0, msg=run.stderr)
        self.assertEqual(run.stderr, "")
        self.assertIn("ok", run.stdout)
        self.assertIn("42", run.stdout)

    def test_repl_block_mode_cancel_discards_buffered_declaration(self) -> None:
        run = subprocess.run(
            [sys.executable, "-m", "sprout.cli", "repl"],
            check=False,
            capture_output=True,
            text=True,
            input=":{\nlet hidden = 41\n:cancel\nhidden\n:quit\n",
        )
        self.assertEqual(run.returncode, 0, msg=run.stderr)
        self.assertEqual(run.stderr, "")
        self.assertIn("cancelled block", run.stdout)
        self.assertIn("Unknown variable hidden", run.stdout)

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_repl_native_launcher_block_mode_cancel_discards_buffered_declaration(self) -> None:
        run = subprocess.run(
            [sys.executable, "-m", "sprout.cli", "repl", "--native"],
            check=False,
            capture_output=True,
            text=True,
            input=":{\nlet hidden = 41\n:cancel\nhidden\n:quit\n",
        )
        self.assertEqual(run.returncode, 0, msg=run.stderr)
        self.assertEqual(run.stderr, "")
        self.assertIn("cancelled block", run.stdout)
        self.assertIn("Unknown variable hidden", run.stdout)

    def test_stdlib_repl_frontend_avoids_legacy_host_hooks(self) -> None:
        source = Path("stdlib/repl.sprout").read_text(encoding="utf-8")

        self.assertNotIn("repl_add_import(", source)
        self.assertNotIn("repl_add_declaration(", source)
        self.assertNotIn("repl_eval_expr(", source)
        self.assertNotIn("repl_type_of(", source)
        self.assertNotIn("repl_instances(", source)
        self.assertNotIn("repl_complete(", source)
        self.assertNotIn("repl_complete_in_state(", source)
        self.assertNotIn("repl_reset_session(", source)

    @unittest.skipUnless(hasattr(os, "openpty") and shutil.which("clang"), "pty/native prerequisites unavailable")
    def test_repl_native_interactive_tab_completion_is_case_insensitive_for_imported_namespaces(self) -> None:
        master_fd, slave_fd = pty.openpty()
        env = dict(os.environ)
        env.setdefault("SPROUT_ANALYSIS_SERVICE_CMD", default_analysis_service_cmd())
        proc = subprocess.Popen(
            [sys.executable, "-m", "sprout.cli", "repl", "--native"],
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            close_fds=True,
            env=env,
        )
        os.close(slave_fd)
        try:
            buffer = self._read_pty_until(master_fd, "", "sprout> ", timeout=self._PTY_TIMEOUT)
            self._write_pty_slowly(master_fd, b"import stdlib.json as JSON\n")
            buffer = self._read_pty_until(master_fd, buffer, "ok\nsprout> ", timeout=self._PTY_TIMEOUT)
            self._write_pty_slowly(master_fd, b":t JSON.St\t\n")
            buffer = self._read_pty_until(master_fd, buffer, "JSON.string", timeout=self._PTY_TIMEOUT)
            buffer = self._read_pty_until(master_fd, buffer, "JSON.stringify", timeout=self._PTY_TIMEOUT)
            buffer = self._read_pty_until(master_fd, buffer, "sprout> ")
            self._write_pty_slowly(master_fd, b":quit\n")
            proc.wait(timeout=self._PROCESS_TIMEOUT)
            self.assertEqual(proc.returncode, 0)
            self.assertNotIn("Unknown import alias 'JSON'", buffer)
        finally:
            os.close(master_fd)
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=self._PROCESS_TIMEOUT)

    @unittest.skipUnless(hasattr(os, "openpty") and shutil.which("clang"), "pty/native prerequisites unavailable")
    def test_repl_native_interactive_block_mode_uses_distinct_prompt(self) -> None:
        master_fd, slave_fd = pty.openpty()
        env = dict(os.environ)
        env.setdefault("SPROUT_ANALYSIS_SERVICE_CMD", default_analysis_service_cmd())
        proc = subprocess.Popen(
            [sys.executable, "-m", "sprout.cli", "repl", "--native"],
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            close_fds=True,
            env=env,
        )
        os.close(slave_fd)
        try:
            buffer = self._read_pty_until(master_fd, "", "sprout> ", timeout=self._PTY_TIMEOUT)
            self._write_pty_slowly(master_fd, b":{\n")
            buffer = self._read_pty_until(master_fd, buffer, "block| ", timeout=self._PTY_TIMEOUT)
            self._write_pty_slowly(master_fd, b":cancel\n")
            buffer = self._read_pty_until(master_fd, buffer, "cancelled block", timeout=self._PTY_TIMEOUT)
            buffer = self._read_pty_until(master_fd, buffer, "sprout> ", timeout=self._PTY_TIMEOUT)
            self._write_pty_slowly(master_fd, b":quit\n")
            proc.wait(timeout=self._PROCESS_TIMEOUT)
            self.assertEqual(proc.returncode, 0)
            self.assertNotIn("error:", buffer)
        finally:
            os.close(master_fd)
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=self._PROCESS_TIMEOUT)

    @unittest.skipUnless(hasattr(os, "openpty") and shutil.which("clang"), "pty/native prerequisites unavailable")
    def test_repl_native_interactive_up_arrow_recalls_history(self) -> None:
        master_fd, slave_fd = pty.openpty()
        env = dict(os.environ)
        env.setdefault("SPROUT_ANALYSIS_SERVICE_CMD", default_analysis_service_cmd())
        proc = subprocess.Popen(
            [sys.executable, "-m", "sprout.cli", "repl", "--native"],
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            close_fds=True,
            env=env,
        )
        os.close(slave_fd)
        try:
            buffer = self._read_pty_until(master_fd, "", "sprout> ", timeout=self._PTY_TIMEOUT)
            self._write_pty_slowly(master_fd, b"40 + 2\n")
            buffer = self._read_pty_until(master_fd, buffer, "sprout> ", timeout=self._PTY_TIMEOUT)
            self._write_pty_slowly(master_fd, b"\x1b[A")
            buffer = self._read_pty_until(master_fd, buffer, "40 + 2", timeout=self._PTY_TIMEOUT)
            self._write_pty_slowly(master_fd, b"\n")
            buffer = self._read_pty_until(master_fd, buffer, "sprout> ", timeout=self._PTY_TIMEOUT)
            self._write_pty_slowly(master_fd, b":quit\n")
            proc.wait(timeout=self._PROCESS_TIMEOUT)
            self.assertEqual(proc.returncode, 0)
            self.assertIn("40 + 2", buffer)
            self.assertNotIn("error:", buffer)
        finally:
            os.close(master_fd)
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=self._PROCESS_TIMEOUT)

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_repl_native_launcher_reuses_cached_binary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp) / "cache"
            env = dict(os.environ)
            env["SPROUT_NATIVE_REPL_CACHE_DIR"] = str(cache_dir)
            env["SPROUT_ANALYSIS_SERVICE_CMD"] = default_analysis_service_cmd()

            first = subprocess.run(
                [sys.executable, "-m", "sprout.cli", "repl", "--native"],
                check=False,
                capture_output=True,
                text=True,
                input=":quit\n",
                env=env,
            )
            self.assertEqual(first.returncode, 0, msg=first.stderr)
            cached = sorted(cache_dir.glob("repl-*"))
            self.assertEqual(len(cached), 1)
            binary = cached[0]
            first_mtime = binary.stat().st_mtime_ns

            second = subprocess.run(
                [sys.executable, "-m", "sprout.cli", "repl", "--native"],
                check=False,
                capture_output=True,
                text=True,
                input=":quit\n",
                env=env,
            )
            self.assertEqual(second.returncode, 0, msg=second.stderr)
            self.assertEqual(sorted(cache_dir.glob("repl-*")), [binary])
            self.assertEqual(binary.stat().st_mtime_ns, first_mtime)

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_repl_native_launcher_works_without_analysis_service_env_override(self) -> None:
        env = dict(os.environ)
        env.pop("SPROUT_ANALYSIS_SERVICE_CMD", None)
        run = subprocess.run(
            [sys.executable, "-m", "sprout.cli", "repl", "--native"],
            check=False,
            capture_output=True,
            text=True,
            input="41 + 1\n:quit\n",
            env=env,
        )
        self.assertEqual(run.returncode, 0, msg=run.stderr)
        self.assertEqual(run.stderr, "")
        self.assertIn("42", run.stdout)

    def test_repl_native_launcher_reports_cache_build_failure_clearly(self) -> None:
        failure = subprocess.CalledProcessError(
            1,
            ["python", "-m", "sprout.cli", "compile"],
            stderr="clang not found; install clang or compile with --emit-llvm only",
        )
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"SPROUT_NATIVE_REPL_CACHE_DIR": tmp}, clear=False):
                with patch("sprout.repl.subprocess.run", side_effect=failure):
                    stdout = StringIO()
                    with redirect_stdout(stdout):
                        status = sprout_cli.main(["repl", "--native"])
        self.assertEqual(status, 1)
        text = stdout.getvalue()
        self.assertIn("error: native REPL startup failed while building cached binary", text)
        self.assertIn("clang not found", text)
        self.assertIn("python -m sprout.cli repl", text)

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_repl_native_launcher_starts_without_analysis_service_for_quit_only(self) -> None:
        env = dict(os.environ)
        env["SPROUT_ANALYSIS_SERVICE_CMD"] = "sprout-missing-analysis-service-command"
        run = subprocess.run(
            [sys.executable, "-m", "sprout.cli", "repl", "--native"],
            check=False,
            capture_output=True,
            text=True,
            input=":quit\n",
            env=env,
        )
        self.assertEqual(run.returncode, 0, msg=run.stderr)
        self.assertEqual(run.stderr, "")
        self.assertEqual(run.stdout, "")
        self.assertNotIn("analysis service:", run.stdout)

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_repl_native_launcher_reports_bad_analysis_service_on_first_query(self) -> None:
        env = dict(os.environ)
        env["SPROUT_ANALYSIS_SERVICE_CMD"] = "sprout-missing-analysis-service-command"
        run = subprocess.run(
            [sys.executable, "-m", "sprout.cli", "repl", "--native"],
            check=False,
            capture_output=True,
            text=True,
            input=":type 1\n:quit\n",
            env=env,
        )
        self.assertEqual(run.returncode, 0, msg=run.stderr)
        self.assertEqual(run.stderr, "")
        self.assertIn("analysis service: command failed to start", run.stdout)
        self.assertIn("SPROUT_ANALYSIS_SERVICE_CMD", run.stdout)

    def test_repl_default_loads_prelude(self) -> None:
        run = subprocess.run(
            [sys.executable, "-m", "sprout.cli", "repl"],
            check=False,
            capture_output=True,
            text=True,
            input=":type split_ints(\"1 2 3\")\n:quit\n",
        )
        self.assertEqual(run.returncode, 0, msg=run.stderr)
        self.assertEqual(run.stderr, "")
        self.assertIn("List Int", run.stdout)

    def test_repl_type_output_uses_friendly_type_variables(self) -> None:
        run = subprocess.run(
            [sys.executable, "-m", "sprout.cli", "repl"],
            check=False,
            capture_output=True,
            text=True,
            input=":t map\n:quit\n",
        )
        self.assertEqual(run.returncode, 0, msg=run.stderr)
        self.assertEqual(run.stderr, "")
        self.assertIn("forall a b c. (a -> b) -> c a -> c b", run.stdout)

    def test_repl_type_query_supports_typeclass_method_values(self) -> None:
        run = subprocess.run(
            [sys.executable, "-m", "sprout.cli", "repl"],
            check=False,
            capture_output=True,
            text=True,
            input=":t fmap\n:quit\n",
        )
        self.assertEqual(run.returncode, 0, msg=run.stderr)
        self.assertEqual(run.stderr, "")
        self.assertIn("forall a b c. (a -> b) -> c a -> c b", run.stdout)

    def test_repl_evaluates_bare_typeclass_method_values(self) -> None:
        run = subprocess.run(
            [sys.executable, "-m", "sprout.cli", "repl"],
            check=False,
            capture_output=True,
            text=True,
            input="to_string\n:quit\n",
        )
        self.assertEqual(run.returncode, 0, msg=run.stderr)
        self.assertEqual(run.stderr, "")
        self.assertNotIn("Unknown variable to_string", run.stdout)
        self.assertIn("FunctionValue(", run.stdout)

    def test_repl_instances_lists_matching_typeclass_instances(self) -> None:
        run = subprocess.run(
            [sys.executable, "-m", "sprout.cli", "repl"],
            check=False,
            capture_output=True,
            text=True,
            input=":instances List Int\n:quit\n",
        )
        self.assertEqual(run.returncode, 0, msg=run.stderr)
        self.assertEqual(run.stderr, "")
        self.assertIn("Instances for List Int:", run.stdout)
        self.assertIn("Foldable List", run.stdout)
        self.assertIn("Functor List", run.stdout)
        self.assertIn("Semigroup (List a)", run.stdout)

    def test_repl_instances_shorthand_reports_no_matches(self) -> None:
        run = subprocess.run(
            [sys.executable, "-m", "sprout.cli", "repl"],
            check=False,
            capture_output=True,
            text=True,
            input=":i Int\n:quit\n",
        )
        self.assertEqual(run.returncode, 0, msg=run.stderr)
        self.assertEqual(run.stderr, "")
        self.assertIn("Instances for Int:", run.stdout)
        self.assertIn("Ord Int", run.stdout)
        self.assertIn("ToString Int", run.stdout)

    def test_repl_help_mentions_type_shorthand(self) -> None:
        run = subprocess.run(
            [sys.executable, "-m", "sprout.cli", "repl"],
            check=False,
            capture_output=True,
            text=True,
            input=":help\n:quit\n",
        )
        self.assertEqual(run.returncode, 0, msg=run.stderr)
        self.assertIn(":type EXPR", run.stdout)
        self.assertIn(":t EXPR", run.stdout)
        self.assertIn(":instances TYPE", run.stdout)
        self.assertIn(":i TYPE", run.stdout)

    def test_repl_instances_supports_qualified_types_after_import(self) -> None:
        run = subprocess.run(
            [sys.executable, "-m", "sprout.cli", "repl"],
            check=False,
            capture_output=True,
            text=True,
            input="import stdlib.collections\n:instances collections.Vec Int\n:quit\n",
        )
        self.assertEqual(run.returncode, 0, msg=run.stderr)
        self.assertEqual(run.stderr, "")
        self.assertIn("ok", run.stdout)
        self.assertIn("Instances for Vec Int:", run.stdout)
        self.assertIn("Foldable Vec", run.stdout)
        self.assertIn("Functor Vec", run.stdout)
        self.assertIn("Semigroup (Vec a)", run.stdout)

    def test_repl_imports_make_stdlib_modules_available(self) -> None:
        run = subprocess.run(
            [sys.executable, "-m", "sprout.cli", "repl"],
            check=False,
            capture_output=True,
            text=True,
            input="import stdlib.http\n:type split_ints(\"1 2 3\")\n:type http.http_ok(\"x\")\n:quit\n",
        )
        self.assertEqual(run.returncode, 0, msg=run.stderr)
        self.assertEqual(run.stderr, "")
        self.assertIn("ok", run.stdout)
        self.assertIn("List Int", run.stdout)
        self.assertIn("String", run.stdout)

    def test_repl_resolves_foldable_to_vec_for_list_literal(self) -> None:
        run = subprocess.run(
            [sys.executable, "-m", "sprout.cli", "repl"],
            check=False,
            capture_output=True,
            text=True,
            input="foldable_to_vec([1,2,3])\n:quit\n",
        )
        self.assertEqual(run.returncode, 0, msg=run.stderr)
        self.assertEqual(run.stderr, "")
        self.assertNotIn("Cannot resolve constraint Foldable", run.stdout)
        self.assertIn("Vec(", run.stdout)

    def test_repl_reports_friendly_argument_type_mismatch(self) -> None:
        run = subprocess.run(
            [sys.executable, "-m", "sprout.cli", "repl"],
            check=False,
            capture_output=True,
            text=True,
            input="let l = [1,2,3]\nfmap(l, \\x -> 2 * x)\n:quit\n",
        )
        self.assertEqual(run.returncode, 0, msg=run.stderr)
        self.assertEqual(run.stderr, "")
        self.assertIn("Argument type mismatch: expected a -> b, got List Int", run.stdout)

    def test_repl_default_supports_list_literals(self) -> None:
        run = subprocess.run(
            [sys.executable, "-m", "sprout.cli", "repl"],
            check=False,
            capture_output=True,
            text=True,
            input="[1,2,3]\n:quit\n",
        )
        self.assertEqual(run.returncode, 0, msg=run.stderr)
        self.assertEqual(run.stderr, "")
        self.assertIn("Cons(1, Cons(2, Cons(3, Nil)))", run.stdout)

    def test_repl_invalid_qualified_lookup_reports_error(self) -> None:
        run = subprocess.run(
            [sys.executable, "-m", "sprout.cli", "repl"],
            check=False,
            capture_output=True,
            text=True,
            input="import stdlib.collections\n:t collections.Monoid\n:quit\n",
        )
        self.assertEqual(run.returncode, 0, msg=run.stderr)
        self.assertEqual(run.stderr, "")
        self.assertIn("ok", run.stdout)
        self.assertIn("error:", run.stdout)
        self.assertIn("does not export value 'Monoid'", run.stdout)

    def test_repl_session_tracks_imports_and_declarations(self) -> None:
        from sprout.repl_host import ReplSession

        session = ReplSession()
        session.add_import("import stdlib.http")
        session.add_declaration("let answer = 41")

        self.assertEqual(session.imports, ["import stdlib.http"])
        self.assertEqual(session.declarations, ["let answer = 41"])
        self.assertEqual(session.infer_type("answer + 1"), "Int")
        self.assertEqual(session.infer_type("http.http_ok(\"x\")"), "String")
        self.assertEqual(session.eval_expression_lines("answer + 1"), ("42",))
        query_type, matches = session.instances_for_type("List Int")
        self.assertEqual(query_type, "List Int")
        self.assertIn("Functor List", matches)
        self.assertIn("http", session.completion_matches("htt", "htt"))
        self.assertIn("answer", session.completion_matches("ans", "ans"))

    def test_repl_imports_and_prelude_append_work_together(self) -> None:
        run = subprocess.run(
            [sys.executable, "-m", "sprout.cli", "repl"],
            check=False,
            capture_output=True,
            text=True,
            input='import stdlib.string\n"foo" ++ "foo"\n:type string.concat("a", "b")\n:quit\n',
        )
        self.assertEqual(run.returncode, 0, msg=run.stderr)
        self.assertEqual(run.stderr, "")
        self.assertIn("ok", run.stdout)
        self.assertIn("foofoo", run.stdout)
        self.assertIn("String", run.stdout)

    def test_repl_declared_names_include_declared_symbols(self) -> None:
        from sprout.repl_host import ReplSession

        session = ReplSession(
            declarations=[
                "let answer = 42",
                "fn double(x: Int) -> Int = x + x",
                "type MaybeInt = | Some Int | None",
                "class Renderable t { fn render(x: t) -> Int }",
                "instance Renderable MaybeInt { fn render(x: MaybeInt) -> Int = 0 }",
            ]
        )
        names = set(session.completion_matches("", ""))

        self.assertIn("answer", names)
        self.assertIn("double", names)
        self.assertIn("MaybeInt", names)
        self.assertIn("Some", names)
        self.assertIn("None", names)
        self.assertIn("Renderable", names)
        self.assertIn("render", names)

    def test_repl_declared_names_in_source_reports_declared_symbols(self) -> None:
        from sprout.analysis_snapshot_backend import declared_names_in_source

        names = set(
            declared_names_in_source(
                "module app.repl\n\ntype AAA =\n  | AAA\n\ntype Maybe a =\n  | Just a\n  | Nothing\n\nlet local = 41"
            )
        )

        self.assertIn("AAA", names)
        self.assertIn("Maybe", names)
        self.assertIn("Just", names)
        self.assertIn("Nothing", names)
        self.assertIn("local", names)

    def test_repl_exported_names_in_source_reports_explicit_exports(self) -> None:
        from sprout.analysis_snapshot_backend import exported_names_in_source

        names = set(
            exported_names_in_source(
                "module app.lib\n\nexport type Box(..) =\n  | Wrap String\n\nexport fn unwrap(value: Box) -> String =\n  match value with\n  | Wrap raw -> raw\n\nfn hidden() -> Int = 1"
            )
        )

        self.assertIn("Box", names)
        self.assertIn("Wrap", names)
        self.assertIn("unwrap", names)
        self.assertNotIn("hidden", names)

    def test_repl_symbol_inventory_in_source_reports_declared_imported_and_exported_names(self) -> None:
        from sprout.analysis_snapshot_backend import symbol_inventory_in_source

        declared, imported, exported = symbol_inventory_in_source(
            "module app.lib\n\nimport stdlib.string\nimport stdlib.bytes (from_string)\n\nexport type Box(..) =\n  | Wrap String\n\nexport fn unwrap(value: Box) -> String =\n  match value with\n  | Wrap raw -> raw\n\nlet local = 1"
        )

        self.assertIn("Box", declared)
        self.assertIn("Wrap", declared)
        self.assertIn("unwrap", declared)
        self.assertIn("local", declared)
        self.assertIn("string", imported)
        self.assertIn("from_string", imported)
        self.assertIn("Box", exported)
        self.assertIn("Wrap", exported)
        self.assertIn("unwrap", exported)
        self.assertNotIn("local", exported)

    def test_repl_completion_matches_commands_and_prelude_names(self) -> None:
        from sprout.repl_host import ReplSession

        session = ReplSession()
        self.assertEqual(session.completion_matches(":t", ":t"), [":t", ":type"])
        matches = session.completion_matches("sp", "sp")
        self.assertIn("split_ints", matches)
        range_matches = session.completion_matches("range_", "range_")
        self.assertIn("range_count", range_matches)
        self.assertIn("range_fold", range_matches)
        self.assertNotIn("range_fold_go", range_matches)
        self.assertNotIn("range_to_list_go", range_matches)

    def test_repl_completion_matches_declared_names(self) -> None:
        from sprout.repl_host import ReplSession

        session = ReplSession(declarations=["let answer = 42", "fn annotate(x: Int) -> Int = x"])
        matches = session.completion_matches("ans", "ans")

        self.assertIn("answer", matches)
        self.assertNotIn("annotate", matches)

    def test_repl_completion_matches_stdlib_module_names(self) -> None:
        from sprout.repl_host import ReplSession

        session = ReplSession()
        matches = session.completion_matches("htt", "htt")

        self.assertIn("http", matches)
        self.assertIn("http_client", matches)

    def test_repl_completion_matches_imported_aliases_and_names(self) -> None:
        from sprout.repl_host import ReplSession

        session = ReplSession(imports=["import stdlib.string", "import stdlib.bytes (from_string)"])
        alias_matches = session.completion_matches("str", "str")
        name_matches = session.completion_matches("fr", "fr")

        self.assertIn("string", alias_matches)
        self.assertIn("from_string", name_matches)

    def test_repl_completion_candidates_return_suffix_prefix(self) -> None:
        from sprout.repl_host import ReplSession

        session = ReplSession(declarations=["let answer = 42"])
        prefix, matches = session.completion_candidates(":t ans")

        self.assertEqual(prefix, "ans")
        self.assertIn("answer", matches)

    def test_repl_completion_candidates_keep_dotted_prefixes(self) -> None:
        from sprout.repl_host import ReplSession

        session = ReplSession(imports=["import stdlib.string"])
        prefix, matches = session.completion_candidates("string.st")

        self.assertEqual(prefix, "string.st")
        self.assertEqual(matches, [])
