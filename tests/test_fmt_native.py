from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

FMT_BIN = os.path.join(os.path.dirname(__file__), "..", "fmt_bin")
REPO_ROOT = Path(__file__).parent.parent


def run_fmt(args: list[str], **kw) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [FMT_BIN] + args,
        check=False,
        capture_output=True,
        text=True,
        **kw,
    )


def run_py_fmt(args: list[str], **kw) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "sprout.cli"] + args,
        check=False,
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        **kw,
    )


@unittest.skipUnless(os.path.exists(FMT_BIN), "fmt_bin not built; run: just build-fmt")
class FmtNativeTests(unittest.TestCase):
    # --------------------------------------------------------------------- fmt
    def test_fmt_rewrites_unformatted_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.sprout"
            path.write_text("fn f(x:Int)->Int=x+1\n", encoding="utf-8")
            proc = run_fmt(["fmt", str(path)])
            self.assertEqual(proc.returncode, 0)
            self.assertIn("formatted", proc.stdout)
            reformatted = path.read_text(encoding="utf-8")
            self.assertNotEqual(reformatted, "fn f(x:Int)->Int=x+1\n")

    def test_fmt_leaves_already_formatted_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.sprout"
            path.write_text("fn f(x: Int) -> Int = x + 1\n", encoding="utf-8")
            run_py_fmt(["fmt", str(path)])
            formatted = path.read_text(encoding="utf-8")
            proc = run_fmt(["fmt", str(path)])
            self.assertEqual(proc.returncode, 0)
            self.assertIn("ok", proc.stdout)
            self.assertEqual(path.read_text(encoding="utf-8"), formatted)

    # ---------------------------------------------------------- fmt --check
    def test_fmt_check_exits_1_for_unformatted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.sprout"
            path.write_text("fn f(x:Int)->Int=x+1\n", encoding="utf-8")
            proc = run_fmt(["fmt", "--check", str(path)])
            self.assertEqual(proc.returncode, 1)
            self.assertIn("needs formatting", proc.stdout)

    def test_fmt_check_exits_0_for_formatted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.sprout"
            path.write_text("fn f(x: Int) -> Int = x + 1\n", encoding="utf-8")
            # normalise first via Python
            run_py_fmt(["fmt", str(path)])
            proc = run_fmt(["fmt", "--check", str(path)])
            self.assertEqual(proc.returncode, 0)
            self.assertIn("ok", proc.stdout)

    # --------------------------------------------------------------- lint
    def test_lint_detects_tabs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.sprout"
            path.write_text("fn f() -> Int =\n\t1\n", encoding="utf-8")
            proc = run_fmt(["lint", str(path)])
            self.assertEqual(proc.returncode, 1)
            self.assertIn("tab", proc.stdout)

    def test_lint_detects_trailing_whitespace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.sprout"
            path.write_text("fn f() -> Int = 1   \n", encoding="utf-8")
            proc = run_fmt(["lint", str(path)])
            self.assertEqual(proc.returncode, 1)
            self.assertIn("trailing", proc.stdout)

    def test_lint_detects_missing_newline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.sprout"
            path.write_text("fn f() -> Int = 1", encoding="utf-8")
            proc = run_fmt(["lint", str(path)])
            self.assertEqual(proc.returncode, 1)
            self.assertIn("newline", proc.stdout)

    def test_lint_detects_unformatted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.sprout"
            path.write_text("fn f(x:Int)->Int=x+1\n", encoding="utf-8")
            proc = run_fmt(["lint", str(path)])
            self.assertEqual(proc.returncode, 1)
            self.assertIn("formatted", proc.stdout)

    def test_lint_clean_file_exits_0(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.sprout"
            path.write_text("fn f(x: Int) -> Int = x + 1\n", encoding="utf-8")
            run_py_fmt(["fmt", str(path)])
            proc = run_fmt(["lint", str(path)])
            self.assertEqual(proc.returncode, 0)
            self.assertIn("ok", proc.stdout)

    # ---------------------------------------------------------------- parity
    def test_parity_fmt_check_with_python_formatter(self) -> None:
        """Native and Python formatters must agree on exit code for every sprout file."""
        sprout_files = list(REPO_ROOT.glob("stdlib/**/*.sprout")) + list(
            REPO_ROOT.glob("examples/**/*.sprout")
        )
        mismatches: list[str] = []
        for path in sorted(sprout_files):
            native = run_fmt(["fmt", "--check", str(path)])
            py = run_py_fmt(["fmt", "--check", str(path)])
            if native.returncode != py.returncode:
                mismatches.append(
                    f"{path.relative_to(REPO_ROOT)}: native={native.returncode} py={py.returncode}"
                )
        if mismatches:
            self.fail("fmt parity failures:\n" + "\n".join(mismatches))

    def test_parity_lint_with_python_linter(self) -> None:
        """Native and Python linters must agree on exit code for every sprout file."""
        sprout_files = list(REPO_ROOT.glob("stdlib/**/*.sprout")) + list(
            REPO_ROOT.glob("examples/**/*.sprout")
        )
        mismatches: list[str] = []
        for path in sorted(sprout_files):
            native = run_fmt(["lint", str(path)])
            py = run_py_fmt(["lint", str(path)])
            if native.returncode != py.returncode:
                mismatches.append(
                    f"{path.relative_to(REPO_ROOT)}: native={native.returncode} py={py.returncode}"
                )
        if mismatches:
            self.fail("lint parity failures:\n" + "\n".join(mismatches))
