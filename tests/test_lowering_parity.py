"""
Lowering parity test: Sprout lower_driver vs Python lower_typeclasses.

Each corpus file in CORPUS is processed by both:
  - sprout.typeclass_lowering.lower_typeclasses   (Python lowering)
  - stdlib/compiler/lower_driver.sprout             (Sprout lowering)

The set of generated __tc_* instance-method function names is compared.
Both sides are sorted before comparison to ignore declaration-order differences.

lower_driver.sprout is run once in batch mode (stdlib_root as argv[0],
file paths as argv[1..N]) so driver startup cost is paid only once.
"""
from __future__ import annotations

import subprocess
import sys
import textwrap
import unittest
from pathlib import Path

from sprout import parse, typecheck_program
from sprout import ast
from sprout.typeclass_lowering import lower_typeclasses

ROOT = Path(__file__).resolve().parent.parent
CORPUS_DIR = ROOT / "tests" / "conformance" / "parity_lowering"
LOWER_DRIVER = ROOT / "stdlib" / "compiler" / "lower_driver.sprout"

CORPUS = [
    "tc_basic.spr",
    "tc_poly.spr",
    "tc_two_methods.spr",
]


# ---------------------------------------------------------------------------
# Python-side lowering: extract sorted __tc_* names
# ---------------------------------------------------------------------------

def python_tc_names(path: Path) -> list[str]:
    src = path.read_text()
    program = parse(src)
    typecheck_program(program)
    lowered = lower_typeclasses(program)
    names = [
        d.name
        for d in lowered.declarations
        if isinstance(d, ast.FnDecl) and d.name.startswith("__tc_")
    ]
    return sorted(names)


# ---------------------------------------------------------------------------
# Sprout-side lowering: batch-run lower_driver and cache results
# ---------------------------------------------------------------------------

def _parse_batch_output(stdout: str) -> dict[str, list[str]]:
    """Split batch driver output into {path: [lines]} by '=== <path> ===' separators."""
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


_LOWER_DRIVER_CACHE: dict[str, list[str]] | None = None


def _ensure_lower_driver_cache() -> dict[str, list[str]]:
    global _LOWER_DRIVER_CACHE
    if _LOWER_DRIVER_CACHE is not None:
        return _LOWER_DRIVER_CACHE
    stdlib_root = str(ROOT / "stdlib")
    paths = [str(CORPUS_DIR / f) for f in CORPUS if (CORPUS_DIR / f).exists()]
    result = subprocess.run(
        [sys.executable, "-m", "sprout.cli", "run", str(LOWER_DRIVER), stdlib_root, *paths],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    if result.returncode != 0:
        raise RuntimeError(f"lower_driver batch run failed:\n{result.stderr}")
    _LOWER_DRIVER_CACHE = _parse_batch_output(result.stdout)
    return _LOWER_DRIVER_CACHE


def sprout_tc_names(path: Path) -> list[str]:
    """Return sorted __tc_* names from lower_driver.sprout output for this path."""
    cache = _ensure_lower_driver_cache()
    key = str(path)
    if key not in cache:
        raise RuntimeError(f"Path not in lower_driver batch output: {path}")
    lines = cache[key]
    # First line should be "OK"; remaining lines are __tc_* names
    if not lines or lines[0] != "OK":
        error_lines = "\n".join(lines)
        raise RuntimeError(f"lower_driver reported failure for {path}:\n{error_lines}")
    return sorted(line for line in lines[1:] if line.startswith("__tc_"))


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------

class LoweringParityTests(unittest.TestCase):
    pass


def _make_parity_test(corpus_file: str):
    def test(self: unittest.TestCase) -> None:
        path = CORPUS_DIR / corpus_file
        if not path.exists():
            self.skipTest(f"Corpus file not found: {path}")

        try:
            py_names = python_tc_names(path)
        except Exception as e:
            self.fail(f"Python lowering failed for {corpus_file}: {e}")

        try:
            spr_names = sprout_tc_names(path)
        except Exception as e:
            self.fail(f"Sprout lowering failed for {corpus_file}: {e}")

        if py_names != spr_names:
            py_only = sorted(set(py_names) - set(spr_names))
            spr_only = sorted(set(spr_names) - set(py_names))
            lines = [f"__tc_* name mismatch in {corpus_file}:"]
            if py_only:
                lines.append(f"  Python only: {py_only}")
            if spr_only:
                lines.append(f"  Sprout only: {spr_only}")
            self.fail("\n".join(lines))

    return test


for _corpus_file in CORPUS:
    _test_name = "test_" + _corpus_file.replace(".", "_").replace("-", "_")
    setattr(LoweringParityTests, _test_name, _make_parity_test(_corpus_file))


if __name__ == "__main__":
    unittest.main()
