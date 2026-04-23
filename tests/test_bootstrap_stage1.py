"""
Bootstrap stage-1 parity test: native compile_driver_bin vs Python-hosted output.

Verifies that the native binary produced by `sprout compile compile_driver.sprout`
(stage-0) produces identical type-checker output to the Python-hosted interpreter
running the same driver on the same corpus files.

The comparison establishes M5's reproducibility invariant:
  Python pipeline (stage-0) → native binary → same typecheck output as Python driver

Notes:
  - The binary must exist at ROOT/compile_driver_bin (built by `sprout compile --native`).
  - Tests are skipped if compile_driver_bin is absent.
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORPUS_DIR = ROOT / "tests" / "conformance" / "run"
IMPORT_CORPUS_DIR = ROOT / "tests" / "conformance" / "parity_import"
COMPILE_DRIVER = ROOT / "stdlib" / "compiler" / "compile_driver.sprout"
NATIVE_BINARY = ROOT / "compile_driver_bin"

# Corpus shared with test_checker_parity — same files, same expectations.
CORPUS = [
    "factorial.spr",
    "maybe_map.spr",
    "type_classes.spr",
]

IMPORT_CORPUS = [
    "stdlib_json_basic.spr",
    "stdlib_string_ops.spr",
]

# ---------------------------------------------------------------------------
# Batch output parser
# ---------------------------------------------------------------------------

def _parse_batch_output(stdout: str) -> dict[str, list[str]]:
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


# ---------------------------------------------------------------------------
# Caches — both drivers run once for all files
# ---------------------------------------------------------------------------

_PYTHON_CACHE: dict[str, list[str]] | None = None
_NATIVE_CACHE: dict[str, list[str]] | None = None


def _all_paths() -> list[Path]:
    return (
        [CORPUS_DIR / f for f in CORPUS] +
        [IMPORT_CORPUS_DIR / f for f in IMPORT_CORPUS]
    )


def _ensure_python_cache() -> dict[str, list[str]]:
    global _PYTHON_CACHE
    if _PYTHON_CACHE is not None:
        return _PYTHON_CACHE
    stdlib_root = str(ROOT / "stdlib")
    paths = [str(p) for p in _all_paths() if p.exists()]
    result = subprocess.run(
        [sys.executable, "-m", "sprout.cli", "run", str(COMPILE_DRIVER),
         stdlib_root, *paths],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Python compile_driver failed:\n{result.stderr}\n{result.stdout}"
        )
    raw = _parse_batch_output(result.stdout)
    # Strip the leading "OK" line so we compare the typed-name block only.
    _PYTHON_CACHE = {
        k: (v[1:] if v and v[0] == "OK" else v) for k, v in raw.items()
    }
    return _PYTHON_CACHE


def _ensure_native_cache() -> dict[str, list[str]] | None:
    """Return None if the binary doesn't exist (tests will be skipped)."""
    global _NATIVE_CACHE
    if _NATIVE_CACHE is not None:
        return _NATIVE_CACHE
    if not NATIVE_BINARY.exists():
        return None
    stdlib_root = str(ROOT / "stdlib")
    paths = [str(p) for p in _all_paths() if p.exists()]
    env = os.environ.copy()
    result = subprocess.run(
        [str(NATIVE_BINARY), stdlib_root, *paths],
        capture_output=True, text=True, cwd=str(ROOT), env=env,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Native compile_driver_bin failed:\n{result.stderr}\n{result.stdout}"
        )
    raw = _parse_batch_output(result.stdout)
    _NATIVE_CACHE = {
        k: (v[1:] if v and v[0] == "OK" else v) for k, v in raw.items()
    }
    return _NATIVE_CACHE


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------

class BootstrapStage1Tests(unittest.TestCase):
    pass


def _make_test(corpus_file: str, corpus_dir: Path = CORPUS_DIR):
    def test(self: unittest.TestCase) -> None:
        if not NATIVE_BINARY.exists():
            self.skipTest(
                f"compile_driver_bin not found at {NATIVE_BINARY}; "
                "run: sprout compile -o compile_driver_bin --with-stdlib --native "
                "stdlib/compiler/compile_driver.sprout"
            )

        path = corpus_dir / corpus_file
        if not path.exists():
            self.skipTest(f"Corpus file not found: {path}")

        try:
            py_cache = _ensure_python_cache()
            native_cache = _ensure_native_cache()
        except RuntimeError as e:
            self.fail(str(e))

        if native_cache is None:
            self.skipTest("compile_driver_bin not found")

        key = str(path)
        py_lines = py_cache.get(key, [])
        native_lines = native_cache.get(key, [])

        if py_lines != native_lines:
            diff_lines = []
            for i, (p, n) in enumerate(zip(py_lines, native_lines)):
                if p != n:
                    diff_lines.append(f"  line {i+1}: python={p!r} native={n!r}")
            if len(py_lines) != len(native_lines):
                diff_lines.append(
                    f"  length: python={len(py_lines)} native={len(native_lines)}"
                )
            self.fail(
                f"{corpus_file}: native output differs from Python output:\n"
                + textwrap.indent("\n".join(diff_lines or ["(no detailed diff)"]), "  ")
            )

    return test


for _corpus_file in CORPUS:
    _test_name = "test_" + _corpus_file.replace(".", "_").replace("-", "_")
    setattr(BootstrapStage1Tests, _test_name, _make_test(_corpus_file))

for _corpus_file in IMPORT_CORPUS:
    _test_name = "test_" + _corpus_file.replace(".", "_").replace("-", "_")
    setattr(BootstrapStage1Tests, _test_name,
            _make_test(_corpus_file, corpus_dir=IMPORT_CORPUS_DIR))


if __name__ == "__main__":
    unittest.main()
