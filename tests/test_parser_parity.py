"""
Parser parity test: bootstrap Sprout parser vs Python parser.

Each test file in CORPUS is parsed by both:
  - tools/dump_ast.py  (Python parser via sprout.parse)
  - stdlib/compiler/driver.sprout  (bootstrap Sprout parser)

and their flat s-expression outputs are compared line by line.

Known divergences are documented in KNOWN_DIVERGENCES and treated as
expected; unexpected divergences fail the test.
"""
from __future__ import annotations

import subprocess
import sys
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORPUS_DIR = ROOT / "tests" / "conformance" / "run"
DRIVER = ROOT / "stdlib" / "compiler" / "driver.sprout"
DUMP_AST = ROOT / "tools" / "dump_ast.py"

# Files in the conformance/run corpus to include in parity tests.
CORPUS = [
    "factorial.spr",
    "maybe_map.spr",
    "stdlib_fold_filter_map.spr",
    "stdlib_mixed_io_maybe_do.spr",
    "stdlib_mixed_io_result_do.spr",
    "aoc2025_day1_sample.spr",
    "aoc2025_day2_sample.spr",
]

# Known divergences between the Python and Sprout parsers.
# Each entry is (description, python_fragment, sprout_fragment).
# A diff line containing python_fragment on the Python side and
# sprout_fragment on the Sprout side is accepted as a known divergence.
KNOWN_DIVERGENCES: list[tuple[str, str, str]] = [
    (
        "++ desugars to 'append' in Python parser, 'list_append' in Sprout parser",
        '(var "append")',
        '(var "list_append")',
    ),
]


def run_python_dump(path: Path) -> list[str]:
    result = subprocess.run(
        [sys.executable, str(DUMP_AST), str(path)],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    if result.returncode != 0:
        raise RuntimeError(f"Python dump failed for {path}:\n{result.stderr}")
    return result.stdout.splitlines()


def run_sprout_dump(path: Path) -> list[str]:
    result = subprocess.run(
        [sys.executable, "-m", "sprout.cli", "run", str(DRIVER), str(path)],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    if result.returncode != 0:
        raise RuntimeError(f"Sprout dump failed for {path}:\n{result.stderr}")
    return result.stdout.splitlines()


def is_known_divergence(py_line: str, spr_line: str) -> str | None:
    """Return divergence description if the diff pair is a known divergence, else None."""
    for desc, py_frag, spr_frag in KNOWN_DIVERGENCES:
        if py_frag in py_line and spr_frag in spr_line:
            # Confirm the lines differ ONLY by this substitution
            if py_line.replace(py_frag, spr_frag) == spr_line:
                return desc
    return None


def compare_outputs(
    py_lines: list[str], spr_lines: list[str], label: str
) -> list[str]:
    """
    Compare line-by-line.  Returns a list of failure messages.
    Known divergences are reported as informational, not failures.
    """
    failures: list[str] = []
    max_lines = max(len(py_lines), len(spr_lines))

    for i in range(max_lines):
        py = py_lines[i] if i < len(py_lines) else "<missing>"
        spr = spr_lines[i] if i < len(spr_lines) else "<missing>"
        if py == spr:
            continue
        desc = is_known_divergence(py, spr)
        if desc is not None:
            # Known divergence — not a failure, but we could log it.
            continue
        failures.append(
            f"{label} line {i + 1} differs:\n"
            f"  Python : {py[:200]}\n"
            f"  Sprout : {spr[:200]}"
        )

    return failures


class ParserParityTests(unittest.TestCase):
    pass


def _make_test(corpus_file: str):
    def test(self: unittest.TestCase) -> None:
        path = CORPUS_DIR / corpus_file
        if not path.exists():
            self.skipTest(f"Corpus file not found: {path}")

        try:
            py_lines = run_python_dump(path)
        except RuntimeError as e:
            self.fail(str(e))

        try:
            spr_lines = run_sprout_dump(path)
        except RuntimeError as e:
            self.fail(str(e))

        failures = compare_outputs(py_lines, spr_lines, corpus_file)
        if failures:
            msg = f"\n{len(failures)} parity failure(s) in {corpus_file}:\n"
            msg += "\n".join(textwrap.indent(f, "  ") for f in failures)
            self.fail(msg)

    return test


# Dynamically create one test method per corpus file.
for _corpus_file in CORPUS:
    _test_name = "test_" + _corpus_file.replace(".", "_").replace("-", "_")
    setattr(ParserParityTests, _test_name, _make_test(_corpus_file))


if __name__ == "__main__":
    unittest.main()
