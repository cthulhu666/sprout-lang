"""
Checker parity test: bootstrap Sprout checker vs Python typechecker.

Each test file in CORPUS is typechecked by both:
  - tools/dump_types.py               (Python typechecker via sprout.typechecker)
  - stdlib/compiler/type_driver.sprout  (bootstrap Sprout type_driver)
  - stdlib/compiler/compile_driver.sprout (bootstrap Sprout compile_driver)

and their "name : scheme" outputs are compared line by line.

Both checkers use GHC-style forall variable ordering (left-to-right
first-appearance) and rename bound vars to a, b, c, ... in that order.

Performance: both type_driver and compile_driver are invoked once in batch
mode (stdlib_root as argv[0], file paths as argv[1..N]) so that driver startup
cost is paid only once per test run rather than once per corpus file.
"""
from __future__ import annotations

import unittest

# Python interpreter is being removed; these tests are skipped pending full removal.

import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORPUS_DIR = ROOT / "tests" / "conformance" / "run"
IMPORT_CORPUS_DIR = ROOT / "tests" / "conformance" / "parity_import"
TYPE_DRIVER = ROOT / "stdlib" / "compiler" / "type_driver.sprout"
COMPILE_DRIVER = ROOT / "stdlib" / "compiler" / "compile_driver.sprout"
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

# Import-resolution corpus: files with real stdlib imports.
# Stored in parity_import/ (not conformance/run/) to avoid the conformance
# runner which calls parse() directly and can't handle import headers.
# Both dump_types.py (via load_module_bundle) and bootstrap checkers
# load modules from stdlib before typechecking.
IMPORT_CORPUS = [
    "stdlib_json_basic.spr",
    "stdlib_string_ops.spr",
]


def is_known_divergence(py_line: str, spr_line: str) -> str | None:
    """Return a description if this diff pair is an expected known divergence."""
    return None


# ---------------------------------------------------------------------------
# Batch runner helpers
# ---------------------------------------------------------------------------
# Both type_driver and compile_driver accept:
#   argv[0] = stdlib_root
#   argv[1..N] = file paths
# and print "=== <path> ===" before each file's typed-name output.
#
# We run each driver once for ALL corpus files and cache the results,
# so driver startup cost (~30-40s) is paid once rather than per-file.

def _parse_batch_output(stdout: str) -> dict[str, list[str]]:
    """Split batch driver output into {path: [lines]} by "=== <path> ===" separators."""
    results: dict[str, list[str]] = {}
    current_path: str | None = None
    current_lines: list[str] = []
    for line in stdout.splitlines():
        if line.startswith("=== ") and line.endswith(" ==="):
            if current_path is not None:
                results[current_path] = current_lines
            current_path = line[4:-4]
            current_lines = []
        elif current_path is not None:
            current_lines.append(line)
    if current_path is not None:
        results[current_path] = current_lines
    return results


def _all_corpus_paths() -> list[Path]:
    return (
        [CORPUS_DIR / f for f in CORPUS] +
        [IMPORT_CORPUS_DIR / f for f in IMPORT_CORPUS]
    )


_TYPE_DRIVER_CACHE: dict[str, list[str]] | None = None
_COMPILE_DRIVER_CACHE: dict[str, list[str]] | None = None


def _ensure_type_driver_cache() -> dict[str, list[str]]:
    global _TYPE_DRIVER_CACHE
    if _TYPE_DRIVER_CACHE is not None:
        return _TYPE_DRIVER_CACHE
    stdlib_root = str(ROOT / "stdlib")
    paths = [str(p) for p in _all_corpus_paths() if p.exists()]
    result = subprocess.run(
        [sys.executable, "-m", "sprout.cli", "run", str(TYPE_DRIVER), stdlib_root, *paths],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    if result.returncode != 0:
        raise RuntimeError(f"type_driver batch run failed:\n{result.stderr}")
    _TYPE_DRIVER_CACHE = _parse_batch_output(result.stdout)
    return _TYPE_DRIVER_CACHE


def _ensure_compile_driver_cache() -> dict[str, list[str]]:
    global _COMPILE_DRIVER_CACHE
    if _COMPILE_DRIVER_CACHE is not None:
        return _COMPILE_DRIVER_CACHE
    stdlib_root = str(ROOT / "stdlib")
    paths = [str(p) for p in _all_corpus_paths() if p.exists()]
    result = subprocess.run(
        [sys.executable, "-m", "sprout.cli", "run", str(COMPILE_DRIVER), stdlib_root, *paths],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    if result.returncode != 0:
        raise RuntimeError(f"compile_driver batch run failed:\n{result.stderr}")
    raw = _parse_batch_output(result.stdout)
    # compile_driver emits "OK" as first line per file; strip it
    _COMPILE_DRIVER_CACHE = {
        k: (v[1:] if v and v[0] == "OK" else v)
        for k, v in raw.items()
    }
    return _COMPILE_DRIVER_CACHE


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
    cache = _ensure_type_driver_cache()
    key = str(path)
    if key not in cache:
        raise RuntimeError(f"Path not in type_driver batch output: {path}")
    return cache[key]


def run_bootstrap_check(path: Path) -> list[str]:
    cache = _ensure_compile_driver_cache()
    key = str(path)
    if key not in cache:
        raise RuntimeError(f"Path not in compile_driver batch output: {path}")
    return cache[key]


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


# ---------------------------------------------------------------------------
# CheckerParityTests: type_driver.sprout vs dump_types.py
# ---------------------------------------------------------------------------

@unittest.skip("Python interpreter being removed")
class CheckerParityTests(unittest.TestCase):
    pass


def _make_test(corpus_file: str, corpus_dir: Path = CORPUS_DIR):
    def test(self: unittest.TestCase) -> None:
        path = corpus_dir / corpus_file
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

for _corpus_file in IMPORT_CORPUS:
    _test_name = "test_" + _corpus_file.replace(".", "_").replace("-", "_")
    setattr(CheckerParityTests, _test_name, _make_test(_corpus_file, corpus_dir=IMPORT_CORPUS_DIR))


# ---------------------------------------------------------------------------
# BootstrapCheckParityTests: compile_driver.sprout vs dump_types.py
# ---------------------------------------------------------------------------

@unittest.skip("Python interpreter being removed")
class BootstrapCheckParityTests(unittest.TestCase):
    pass


def _make_bootstrap_check_test(corpus_file: str, corpus_dir: Path = CORPUS_DIR):
    def test(self: unittest.TestCase) -> None:
        path = corpus_dir / corpus_file
        if not path.exists():
            self.skipTest(f"Corpus file not found: {path}")

        try:
            py_lines = run_python_dump(path)
        except RuntimeError as e:
            self.fail(str(e))

        try:
            spr_lines = run_bootstrap_check(path)
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
    setattr(BootstrapCheckParityTests, _test_name, _make_bootstrap_check_test(_corpus_file))

for _corpus_file in IMPORT_CORPUS:
    _test_name = "test_" + _corpus_file.replace(".", "_").replace("-", "_")
    setattr(BootstrapCheckParityTests, _test_name, _make_bootstrap_check_test(_corpus_file, corpus_dir=IMPORT_CORPUS_DIR))


if __name__ == "__main__":
    unittest.main()
