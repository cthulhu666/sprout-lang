"""
Checker parity test: bootstrap Sprout checker vs Python typechecker.

Each test file in CORPUS is typechecked by both:
  - tools/dump_types.py           (Python typechecker via sprout.typechecker)
  - stdlib/compiler/type_driver.sprout  (bootstrap Sprout checker)

and their "name : scheme" outputs are compared line by line.

Both checkers use GHC-style forall variable ordering (left-to-right
first-appearance) and rename bound vars to a, b, c, ... in that order.
"""
from __future__ import annotations

import subprocess
import sys
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORPUS_DIR = ROOT / "tests" / "conformance" / "run"
TYPE_DRIVER = ROOT / "stdlib" / "compiler" / "type_driver.sprout"
DUMP_TYPES = ROOT / "tools" / "dump_types.py"

# Files from the conformance corpus checked by both checkers.
# tools/dump_types.py injects a PRELUDE_SEED_ENV (ADT constructors + list/dict
# helpers) so corpus files that call prelude functions can be type-checked
# without a full module-load step.
CORPUS = [
    "factorial.spr",
    "maybe_map.spr",
    "aoc2025_day1_sample.spr",
    "aoc2025_day2_sample.spr",
    # Language-feature coverage
    "type_classes.spr",
    "record_types.spr",
    "poly_types.spr",
    "where_clauses.spr",
    # Prelude-usage corpus: these files call fold/map/filter/split_ints and
    # Result constructors that are not native builtins; tools/dump_types.py
    # injects them via PRELUDE_SEED_ENV so both sides can typecheck.
    "stdlib_fold_filter_map.spr",
    "stdlib_mixed_io_result_do.spr",
    "stdlib_mixed_io_maybe_do.spr",
]

def is_known_divergence(py_line: str, spr_line: str) -> str | None:
    """Return a description if this diff pair is an expected known divergence."""
    return None


def run_python_dump(path: Path) -> list[str]:
    result = subprocess.run(
        [sys.executable, str(DUMP_TYPES), str(path)],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    if result.returncode != 0:
        raise RuntimeError(f"Python dump failed for {path}:\n{result.stderr}")
    return result.stdout.splitlines()


def run_sprout_dump(path: Path) -> list[str]:
    result = subprocess.run(
        [sys.executable, "-m", "sprout.cli", "run", str(TYPE_DRIVER), str(path)],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    if result.returncode != 0:
        raise RuntimeError(f"Sprout dump failed for {path}:\n{result.stderr}")
    return result.stdout.splitlines()


def compare_outputs(
    py_lines: list[str], spr_lines: list[str], label: str
) -> list[str]:
    """Compare line-by-line. Returns a list of failure messages."""
    failures: list[str] = []
    max_lines = max(len(py_lines), len(spr_lines))

    for i in range(max_lines):
        py = py_lines[i] if i < len(py_lines) else "<missing>"
        spr = spr_lines[i] if i < len(spr_lines) else "<missing>"
        if py == spr:
            continue
        desc = is_known_divergence(py, spr)
        if desc is not None:
            continue
        failures.append(
            f"{label} line {i + 1} differs:\n"
            f"  Python : {py[:200]}\n"
            f"  Sprout : {spr[:200]}"
        )

    return failures


class CheckerParityTests(unittest.TestCase):
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


for _corpus_file in CORPUS:
    _test_name = "test_" + _corpus_file.replace(".", "_").replace("-", "_")
    setattr(CheckerParityTests, _test_name, _make_test(_corpus_file))


if __name__ == "__main__":
    unittest.main()
