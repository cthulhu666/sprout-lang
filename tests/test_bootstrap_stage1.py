"""
Bootstrap stage-1/stage-2/stage-3 parity tests.

Stage-1 (BootstrapStage1Tests, M5):
  Verifies that compile_driver_bin (stage-0, Python-compiled) produces the same
  type-checker output as the Python-hosted interpreter on the bootstrap corpus.
  Invariant: Python pipeline → native binary → same typecheck output as Python driver.

Stage-2 (BootstrapStage2Tests, M6):
  Verifies that compile_driver_bin_stage1 (produced from Sprout-native --emit-ir
  codegen, without Python involvement in the compile step) produces the same output
  as the Python-hosted driver.  Establishes that the Sprout-native LLVM IR emitter
  (codegen.sprout) is correct end-to-end.

  Build compile_driver_bin_stage1 with:
    just build-stage1

Stage-3 (BootstrapStage3Tests, M7):
  Verifies that compile_driver_bin_stage2 (compiled by stage-1, fully Sprout-native
  round-trip) produces the same output as the Python-hosted driver.  Confirms the
  compiler is self-hosting: stage-1 compiles the compiler source and the resulting
  binary is behaviourally identical to the Python reference.

  Build compile_driver_bin_stage2 with:
    just build-stage2

Notes:
  - compile_driver_bin must exist at ROOT/compile_driver_bin.
  - compile_driver_bin_stage1 must exist at ROOT/compile_driver_bin_stage1.
  - compile_driver_bin_stage2 must exist at ROOT/compile_driver_bin_stage2.
  - Tests are skipped if the required binary is absent.
"""
from __future__ import annotations

import unittest

# Python interpreter is being removed; these tests are skipped pending full removal.

import os
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORPUS_DIR = ROOT / "tests" / "conformance" / "run"
IMPORT_CORPUS_DIR = ROOT / "tests" / "conformance" / "parity_import"
COMPILE_DRIVER = ROOT / "stdlib" / "compiler" / "compile_driver.sprout"
NATIVE_BINARY = ROOT / "compile_driver_bin"
STAGE1_BINARY = ROOT / "compile_driver_bin_stage1"
STAGE2_BINARY = ROOT / "compile_driver_bin_stage2"

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
# Caches — each driver runs once for all files
# ---------------------------------------------------------------------------

_PYTHON_CACHE: dict[str, list[str]] | None = None
_NATIVE_CACHE: dict[str, list[str]] | None = None
_STAGE2_CACHE: dict[str, list[str]] | None = None
_STAGE3_CACHE: dict[str, list[str]] | None = None


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
    env.setdefault("SPROUT_GC_LIVELOCK_ACTION", "abort")
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

@unittest.skip("Python interpreter being removed")
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


def _ensure_stage2_cache() -> dict[str, list[str]] | None:
    """Return None if compile_driver_bin_stage1 doesn't exist (tests will be skipped)."""
    global _STAGE2_CACHE
    if _STAGE2_CACHE is not None:
        return _STAGE2_CACHE
    if not STAGE1_BINARY.exists():
        return None
    stdlib_root = str(ROOT / "stdlib")
    paths = [str(p) for p in _all_paths() if p.exists()]
    env = os.environ.copy()
    env.setdefault("SPROUT_GC_LIVELOCK_ACTION", "abort")
    result = subprocess.run(
        [str(STAGE1_BINARY), stdlib_root, *paths],
        capture_output=True, text=True, cwd=str(ROOT), env=env,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"compile_driver_bin_stage1 failed:\n{result.stderr}\n{result.stdout}"
        )
    raw = _parse_batch_output(result.stdout)
    _STAGE2_CACHE = {
        k: (v[1:] if v and v[0] == "OK" else v) for k, v in raw.items()
    }
    return _STAGE2_CACHE


# ---------------------------------------------------------------------------
# Stage-2 test class (M6): stage1 binary vs Python reference
# ---------------------------------------------------------------------------

@unittest.skip("Python interpreter being removed")
class BootstrapStage2Tests(unittest.TestCase):
    pass


def _make_stage2_test(corpus_file: str, corpus_dir: Path = CORPUS_DIR):
    def test(self: unittest.TestCase) -> None:
        if not STAGE1_BINARY.exists():
            self.skipTest(
                f"compile_driver_bin_stage1 not found at {STAGE1_BINARY}; "
                "build with: just build-stage1"
            )

        path = corpus_dir / corpus_file
        if not path.exists():
            self.skipTest(f"Corpus file not found: {path}")

        try:
            py_cache = _ensure_python_cache()
            stage2_cache = _ensure_stage2_cache()
        except RuntimeError as e:
            self.fail(str(e))

        if stage2_cache is None:
            self.skipTest("compile_driver_bin_stage1 not found")

        key = str(path)
        py_lines = py_cache.get(key, [])
        stage2_lines = stage2_cache.get(key, [])

        if py_lines != stage2_lines:
            diff_lines = []
            for i, (p, s) in enumerate(zip(py_lines, stage2_lines)):
                if p != s:
                    diff_lines.append(f"  line {i+1}: python={p!r} stage2={s!r}")
            if len(py_lines) != len(stage2_lines):
                diff_lines.append(
                    f"  length: python={len(py_lines)} stage2={len(stage2_lines)}"
                )
            self.fail(
                f"{corpus_file}: stage2 output differs from Python output:\n"
                + textwrap.indent("\n".join(diff_lines or ["(no detailed diff)"]), "  ")
            )

    return test


for _corpus_file in CORPUS:
    _test_name = "test_" + _corpus_file.replace(".", "_").replace("-", "_")
    setattr(BootstrapStage2Tests, _test_name, _make_stage2_test(_corpus_file))

for _corpus_file in IMPORT_CORPUS:
    _test_name = "test_" + _corpus_file.replace(".", "_").replace("-", "_")
    setattr(BootstrapStage2Tests, _test_name,
            _make_stage2_test(_corpus_file, corpus_dir=IMPORT_CORPUS_DIR))


def _ensure_stage3_cache() -> dict[str, list[str]] | None:
    """Return None if compile_driver_bin_stage2 doesn't exist (tests will be skipped)."""
    global _STAGE3_CACHE
    if _STAGE3_CACHE is not None:
        return _STAGE3_CACHE
    if not STAGE2_BINARY.exists():
        return None
    stdlib_root = str(ROOT / "stdlib")
    paths = [str(p) for p in _all_paths() if p.exists()]
    env = os.environ.copy()
    env.setdefault("SPROUT_GC_LIVELOCK_ACTION", "abort")
    result = subprocess.run(
        [str(STAGE2_BINARY), stdlib_root, *paths],
        capture_output=True, text=True, cwd=str(ROOT), env=env,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"compile_driver_bin_stage2 failed:\n{result.stderr}\n{result.stdout}"
        )
    raw = _parse_batch_output(result.stdout)
    _STAGE3_CACHE = {
        k: (v[1:] if v and v[0] == "OK" else v) for k, v in raw.items()
    }
    return _STAGE3_CACHE


# ---------------------------------------------------------------------------
# Stage-3 test class (M7): stage2 binary vs Python reference
# ---------------------------------------------------------------------------

@unittest.skip("Python interpreter being removed")
class BootstrapStage3Tests(unittest.TestCase):
    pass


def _make_stage3_test(corpus_file: str, corpus_dir: Path = CORPUS_DIR):
    def test(self: unittest.TestCase) -> None:
        if not STAGE2_BINARY.exists():
            self.skipTest(
                f"compile_driver_bin_stage2 not found at {STAGE2_BINARY}; "
                "build with: just build-stage2"
            )

        path = corpus_dir / corpus_file
        if not path.exists():
            self.skipTest(f"Corpus file not found: {path}")

        try:
            py_cache = _ensure_python_cache()
            stage3_cache = _ensure_stage3_cache()
        except RuntimeError as e:
            self.fail(str(e))

        if stage3_cache is None:
            self.skipTest("compile_driver_bin_stage2 not found")

        key = str(path)
        py_lines = py_cache.get(key, [])
        stage3_lines = stage3_cache.get(key, [])

        if py_lines != stage3_lines:
            diff_lines = []
            for i, (p, s) in enumerate(zip(py_lines, stage3_lines)):
                if p != s:
                    diff_lines.append(f"  line {i+1}: python={p!r} stage3={s!r}")
            if len(py_lines) != len(stage3_lines):
                diff_lines.append(
                    f"  length: python={len(py_lines)} stage3={len(stage3_lines)}"
                )
            self.fail(
                f"{corpus_file}: stage3 output differs from Python output:\n"
                + textwrap.indent("\n".join(diff_lines or ["(no detailed diff)"]), "  ")
            )

    return test


for _corpus_file in CORPUS:
    _test_name = "test_" + _corpus_file.replace(".", "_").replace("-", "_")
    setattr(BootstrapStage3Tests, _test_name, _make_stage3_test(_corpus_file))

for _corpus_file in IMPORT_CORPUS:
    _test_name = "test_" + _corpus_file.replace(".", "_").replace("-", "_")
    setattr(BootstrapStage3Tests, _test_name,
            _make_stage3_test(_corpus_file, corpus_dir=IMPORT_CORPUS_DIR))


if __name__ == "__main__":
    unittest.main()
