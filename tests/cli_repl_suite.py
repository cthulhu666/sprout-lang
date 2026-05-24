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
        self.assertIn("just build-stage1", text)

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
