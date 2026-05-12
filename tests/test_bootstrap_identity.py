"""
Bootstrap bundle-identity tests: stage-0 vs stage-1 --phase bundle output.

Runs compile_driver_bin and compile_driver_bin_stage1 both with
`--phase bundle <stdlib_root> <file>` on a corpus of stdlib modules and
checks that their output is identical.

All corpus files are expected to pass.  Files can be added to XFAIL_FILES
to mark a known stage-1 regression while a fix is in progress; remove the
file from the set once the regression is resolved.

XFAIL_FILES controls which corpus files are known-broken in stage-1.
Remove a file from this set once stage-1 correctly bundles it.

Skip behaviour: if either binary is missing the test is skipped.
Build with:
  python3 -m sprout.cli compile --with-stdlib --native -o compile_driver_bin \\
    stdlib/compiler/compile_driver.sprout
  just build-stage1
"""
from __future__ import annotations

import os
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STDLIB = ROOT / "stdlib"
STAGE0_BINARY = ROOT / "compile_driver_bin"
STAGE1_BINARY = ROOT / "compile_driver_bin_stage1"

# Corpus: stdlib modules exercising a variety of decl shapes.
CORPUS: list[Path] = [
    STDLIB / "compiler" / "token.sprout",
    STDLIB / "compiler" / "ast.sprout",
    STDLIB / "prelude.sprout",
]

# Files where stage-1 is known to produce wrong --phase bundle output.
# These tests use @expectedFailure; remove the file once stage-1 is fixed.
XFAIL_FILES: frozenset[str] = frozenset()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_phase_bundle(binary: Path, src_file: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.setdefault("SPROUT_GC_LIVELOCK_ACTION", "abort")
    return subprocess.run(
        [str(binary), "--phase", "bundle", str(STDLIB), str(src_file)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(ROOT),
        env=env,
        timeout=60,
    )


def _parse_bundle_output(stdout: str) -> list[str]:
    """Return qualified-name lines that follow the '=== ... ===' header."""
    lines = stdout.splitlines()
    result: list[str] = []
    past_header = False
    for line in lines:
        if line.startswith("=== ") and line.endswith(" ==="):
            past_header = True
            continue
        if past_header and line == "OK":
            continue
        if past_header:
            result.append(line)
    return result


# ---------------------------------------------------------------------------
# Stage-0 smoke: bundle output must be non-empty and contain no errors
# ---------------------------------------------------------------------------

class Stage0BundleSmokeTests(unittest.TestCase):
    """compile_driver_bin --phase bundle produces valid qualified names."""


def _make_stage0_smoke(src_file: Path):
    def test(self: unittest.TestCase) -> None:
        if not STAGE0_BINARY.exists():
            self.skipTest(f"compile_driver_bin not found at {STAGE0_BINARY}")
        if not src_file.exists():
            self.skipTest(f"Corpus file not found: {src_file}")
        result = _run_phase_bundle(STAGE0_BINARY, src_file)
        self.assertEqual(
            result.returncode, 0,
            msg=f"stage-0 bundle exited {result.returncode}:\n{result.stderr[:500]}",
        )
        names = _parse_bundle_output(result.stdout)
        self.assertTrue(
            len(names) > 0,
            msg=f"stage-0 bundle produced no qualified names for {src_file.name}",
        )
        dot_prefix = [l for l in names if l.startswith(".")]
        self.assertEqual(
            dot_prefix, [],
            msg=f"stage-0 bundle: names start with '.':\n" + "\n".join(dot_prefix[:5]),
        )

    test.__name__ = f"test_{src_file.stem}"
    return test


for _f in CORPUS:
    setattr(Stage0BundleSmokeTests, f"test_{_f.stem}", _make_stage0_smoke(_f))


# ---------------------------------------------------------------------------
# Identity: stage-0 == stage-1 --phase bundle output.
# Files in XFAIL_FILES are @expectedFailure (known stage-1 regression).
# Files NOT in XFAIL_FILES pass normally; failure means a new regression.
# ---------------------------------------------------------------------------

class BundleIdentityTests(unittest.TestCase):
    """stage-0 and stage-1 --phase bundle output must be identical."""


def _make_identity_test(src_file: Path):
    def _run(self: unittest.TestCase) -> None:
        if not STAGE0_BINARY.exists():
            self.skipTest(f"compile_driver_bin not found at {STAGE0_BINARY}")
        if not STAGE1_BINARY.exists():
            self.skipTest(
                f"compile_driver_bin_stage1 not found at {STAGE1_BINARY}; "
                "run: just build-stage1"
            )
        if not src_file.exists():
            self.skipTest(f"Corpus file not found: {src_file}")

        r0 = _run_phase_bundle(STAGE0_BINARY, src_file)
        r1 = _run_phase_bundle(STAGE1_BINARY, src_file)

        names0 = _parse_bundle_output(r0.stdout)
        names1 = _parse_bundle_output(r1.stdout)

        diff_lines: list[str] = []
        for i, (a, b) in enumerate(zip(names0, names1)):
            if a != b:
                diff_lines.append(f"  line {i + 1}: stage-0={a!r}  stage-1={b!r}")
        if len(names0) != len(names1):
            diff_lines.append(
                f"  length: stage-0={len(names0)} stage-1={len(names1)}"
            )

        self.assertEqual(
            names0, names1,
            msg=f"{src_file.name}: stage-1 bundle output differs from stage-0:\n"
            + "\n".join(diff_lines[:20] or ["(no detailed diff)"]),
        )

    if src_file.name in XFAIL_FILES:
        test = unittest.expectedFailure(_run)
    else:
        test = _run
    test.__name__ = f"test_{src_file.stem}"
    return test


for _f in CORPUS:
    setattr(BundleIdentityTests, f"test_{_f.stem}", _make_identity_test(_f))


if __name__ == "__main__":
    unittest.main()
