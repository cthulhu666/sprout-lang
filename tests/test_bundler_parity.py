"""
Bundler parity test: Sprout bundle_driver vs Python load_module_bundle.

Each corpus file in CORPUS is processed by both:
  - sprout.module_loader.load_module_bundle + resolve_program_names  (Python)
  - stdlib/compiler/bundle_driver.sprout                             (Sprout)

The set of qualified FnDecl names is compared (sorted, to ignore order).
bundle_driver.sprout is run once in batch mode so startup cost is amortised.
"""
from __future__ import annotations

import unittest

# Python interpreter is being removed; these tests are skipped pending full removal.

import subprocess
import sys
from pathlib import Path

from sprout.module_loader import load_module_bundle, resolve_program_names
from sprout.parser import parse
from sprout import ast

ROOT = Path(__file__).resolve().parent.parent
CORPUS_DIR = ROOT / "tests" / "conformance" / "parity_bundler"
BUNDLE_DRIVER = ROOT / "stdlib" / "compiler" / "bundle_driver.sprout"

CORPUS = [
    "simple.spr",
    "with_imports.spr",
    "with_list_ops.spr",
]


# ---------------------------------------------------------------------------
# Python-side: qualified FnDecl names
# ---------------------------------------------------------------------------

def python_fn_names(path: Path) -> list[str]:
    bundle = load_module_bundle(path)
    tree = parse(bundle.source)
    resolve_program_names(tree, bundle)
    return sorted(
        d.name
        for d in tree.declarations
        if isinstance(d, ast.FnDecl)
    )


# ---------------------------------------------------------------------------
# Sprout-side: batch-run bundle_driver and cache results
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


_BUNDLE_DRIVER_CACHE: dict[str, list[str]] | None = None


def _ensure_bundle_driver_cache() -> dict[str, list[str]]:
    global _BUNDLE_DRIVER_CACHE
    if _BUNDLE_DRIVER_CACHE is not None:
        return _BUNDLE_DRIVER_CACHE
    stdlib_root = str(ROOT / "stdlib")
    paths = [str(CORPUS_DIR / f) for f in CORPUS if (CORPUS_DIR / f).exists()]
    result = subprocess.run(
        [sys.executable, "-m", "sprout.cli", "run", str(BUNDLE_DRIVER), stdlib_root, *paths],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    if result.returncode != 0:
        raise RuntimeError(f"bundle_driver batch run failed:\n{result.stderr}")
    _BUNDLE_DRIVER_CACHE = _parse_batch_output(result.stdout)
    return _BUNDLE_DRIVER_CACHE


def sprout_fn_names(path: Path) -> list[str]:
    """Return sorted FnDecl names from bundle_driver.sprout output for this path."""
    cache = _ensure_bundle_driver_cache()
    key = str(path)
    if key not in cache:
        raise RuntimeError(f"Path not in bundle_driver batch output: {path}")
    lines = cache[key]
    if not lines or lines[0] != "OK":
        error_lines = "\n".join(lines)
        raise RuntimeError(f"bundle_driver reported failure for {path}:\n{error_lines}")
    return sorted(lines[1:])


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------

@unittest.skip("Python interpreter being removed")
class BundlerParityTests(unittest.TestCase):
    pass


def _make_parity_test(corpus_file: str):
    def test(self: unittest.TestCase) -> None:
        path = CORPUS_DIR / corpus_file
        if not path.exists():
            self.skipTest(f"Corpus file not found: {path}")

        try:
            py_names = python_fn_names(path)
        except Exception as e:
            self.fail(f"Python bundler failed for {corpus_file}: {e}")

        try:
            spr_names = sprout_fn_names(path)
        except Exception as e:
            self.fail(f"Sprout bundler failed for {corpus_file}: {e}")

        if py_names != spr_names:
            py_only = sorted(set(py_names) - set(spr_names))
            spr_only = sorted(set(spr_names) - set(py_names))
            lines = [f"FnDecl name mismatch in {corpus_file}:"]
            if py_only:
                lines.append(f"  Python only ({len(py_only)}): {py_only[:20]}")
            if spr_only:
                lines.append(f"  Sprout only ({len(spr_only)}): {spr_only[:20]}")
            self.fail("\n".join(lines))

    return test


for _corpus_file in CORPUS:
    _test_name = "test_" + _corpus_file.replace(".", "_").replace("-", "_")
    setattr(BundlerParityTests, _test_name, _make_parity_test(_corpus_file))


if __name__ == "__main__":
    unittest.main()
