"""
Full-pipeline parity test: full_driver.sprout (bundle → typecheck → lower).

Verifies that the Sprout-native end-to-end pipeline (M4) successfully
processes real programs without crashing or producing errors.

Each file in CORPUS is run through full_driver.sprout and the output is
checked for "OK" as the first line of each section.  No cross-comparison
with Python output is performed — the goal is to confirm all three passes
(bundler, typechecker, lowering) compose cleanly on real source.
"""
from __future__ import annotations

import subprocess
import sys
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORPUS_DIR = ROOT / "tests" / "conformance" / "run"
IMPORT_CORPUS_DIR = ROOT / "tests" / "conformance" / "parity_import"
FULL_DRIVER = ROOT / "stdlib" / "compiler" / "full_driver.sprout"

# Files that exercise the pipeline without stdlib imports.
# These go through the bundler (which injects the prelude), the HM
# typechecker, and the dictionary-passing lowering pass.
CORPUS = [
    "factorial.spr",
    "maybe_map.spr",
    "type_classes.spr",
]

# Files that exercise real import resolution through the bundler.
IMPORT_CORPUS = [
    "stdlib_json_basic.spr",
    "stdlib_string_ops.spr",
]


# ---------------------------------------------------------------------------
# Batch runner
# ---------------------------------------------------------------------------

def _parse_batch_output(stdout: str) -> dict[str, list[str]]:
    """Split batch driver output into {path: [lines]} by === separators."""
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


_FULL_DRIVER_CACHE: dict[str, list[str]] | None = None


def _ensure_full_driver_cache() -> dict[str, list[str]]:
    global _FULL_DRIVER_CACHE
    if _FULL_DRIVER_CACHE is not None:
        return _FULL_DRIVER_CACHE
    stdlib_root = str(ROOT / "stdlib")
    all_paths = (
        [CORPUS_DIR / f for f in CORPUS] +
        [IMPORT_CORPUS_DIR / f for f in IMPORT_CORPUS]
    )
    existing = [p for p in all_paths if p.exists()]
    result = subprocess.run(
        [sys.executable, "-m", "sprout.cli", "run", str(FULL_DRIVER),
         stdlib_root, *[str(p) for p in existing]],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"full_driver batch run failed:\n{result.stderr}\n{result.stdout}"
        )
    _FULL_DRIVER_CACHE = _parse_batch_output(result.stdout)
    return _FULL_DRIVER_CACHE


def run_full_pipeline(path: Path) -> list[str]:
    cache = _ensure_full_driver_cache()
    key = str(path)
    if key not in cache:
        raise RuntimeError(f"Path not in full_driver batch output: {path}")
    return cache[key]


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------

class FullPipelineTests(unittest.TestCase):
    pass


def _make_test(corpus_file: str, corpus_dir: Path = CORPUS_DIR):
    def test(self: unittest.TestCase) -> None:
        path = corpus_dir / corpus_file
        if not path.exists():
            self.skipTest(f"Corpus file not found: {path}")

        try:
            lines = run_full_pipeline(path)
        except RuntimeError as e:
            self.fail(str(e))

        if not lines:
            self.fail(f"{corpus_file}: full_driver produced no output")

        first = lines[0]
        if first != "OK":
            detail = "\n".join(lines[:10])
            self.fail(
                f"{corpus_file}: expected 'OK' as first line, got:\n"
                + textwrap.indent(detail, "  ")
            )

    return test


for _corpus_file in CORPUS:
    _test_name = "test_" + _corpus_file.replace(".", "_").replace("-", "_")
    setattr(FullPipelineTests, _test_name, _make_test(_corpus_file))

for _corpus_file in IMPORT_CORPUS:
    _test_name = "test_" + _corpus_file.replace(".", "_").replace("-", "_")
    setattr(FullPipelineTests, _test_name, _make_test(_corpus_file, corpus_dir=IMPORT_CORPUS_DIR))


if __name__ == "__main__":
    unittest.main()
