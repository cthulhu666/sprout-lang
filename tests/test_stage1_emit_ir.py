"""
Emit-IR smoke tests for stage-0 and stage-1 native compilers.

Verifies that compile_driver_bin (stage-0) and compile_driver_bin_stage1 (stage-1)
can successfully emit LLVM IR for representative function shapes. A crash or
non-zero exit code indicates a codegen regression.

Four shapes under test (each chosen to exercise a distinct code path):

  noparam  — top-level fn with no parameters; baseline
  oneparam — fn with one Int parameter; exercises fn_params / wrapper params
  adtmatch — fn taking an ADT and doing a pattern match; exercises emit_branches
  generic  — fn using an Eq constraint via a type class; exercises lowered dicts

Tests run against both binaries when present; they skip gracefully if a binary
is absent.  Run `just build-stage1` to produce compile_driver_bin_stage1.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STDLIB = ROOT / "stdlib"
NATIVE_BINARY = ROOT / "compile_driver_bin"
STAGE1_BINARY = ROOT / "compile_driver_bin_stage1"

# ---------------------------------------------------------------------------
# Source snippets — one per function-shape
# ---------------------------------------------------------------------------

SHAPES: dict[str, str] = {
    "noparam": textwrap.dedent("""\
        fn val() -> Int = 42
        fn main() -> Unit !{IO} = print(val())
    """),
    "oneparam": textwrap.dedent("""\
        fn id(x: Int) -> Int = x
        fn main() -> Unit !{IO} = print(id(1))
    """),
    "adtmatch": textwrap.dedent("""\
        type Color = Red | Green | Blue
        fn color_code(c: Color) -> Int =
          match c with
          | Red   -> 0
          | Green -> 1
          | Blue  -> 2
        fn main() -> Unit !{IO} = print(color_code(Red))
    """),
    "generic": textwrap.dedent("""\
        fn contains(x: Int, xs: List Int) -> Bool =
          match xs with
          | Nil        -> false
          | Cons h t   -> if h == x then true else contains(x, t)
        fn main() -> Unit !{IO} = print(contains(1, Cons(1, Nil)))
    """),
    # Exercises ++ with function parameters — previously crashed stage-1
    # with str_concat(ptr null, ...) due to TVar-typed left operand in
    # emit_append_call when the operand came from a lambda or CPS binding.
    "strconcat": textwrap.dedent("""\
        fn cat(a: String, b: String) -> String = a ++ b
        fn main() -> Unit !{IO} = print(cat("hello", "world"))
    """),
    # Exercises a named function used as a first-class value via list_map.
    # Previously revealed a calling-convention mismatch: named functions with
    # tuple parameters get their tuple unpacked into separate pointer args in
    # the closure wrapper, but list_map_go passes a single packed i64.
    "tuple_fn_as_value": textwrap.dedent("""\
        fn fst_str(p: (String, String)) -> String =
          match p with | (a, _) -> a
        fn main() -> Unit !{IO} =
          match list_map(fst_str, Cons(("hello", "world"), Nil)) with
          | Cons h _ -> print(h)
          | Nil -> print("empty")
    """),
}

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _run_emit_ir(binary: Path, source: str) -> subprocess.CompletedProcess[str]:
    """Write source to a temp file and run binary --emit-ir stdlib <file>."""
    with tempfile.NamedTemporaryFile(
        suffix=".spr", mode="w", delete=False, encoding="utf-8"
    ) as f:
        f.write(source)
        tmp_path = f.name
    try:
        return subprocess.run(
            [str(binary), "--emit-ir", str(STDLIB), tmp_path],
            capture_output=True,
            text=True,
            cwd=str(ROOT),
            timeout=60,
        )
    finally:
        os.unlink(tmp_path)


def _assert_valid_ir(test: unittest.TestCase, result: subprocess.CompletedProcess[str], label: str) -> None:
    if result.returncode != 0:
        test.fail(
            f"{label}: emit-ir exited {result.returncode}\n"
            f"stderr: {result.stderr[:2000]}\n"
            f"stdout: {result.stdout[:500]}"
        )
    if "define" not in result.stdout:
        test.fail(
            f"{label}: emit-ir produced no LLVM IR ('define' not found)\n"
            f"stdout: {result.stdout[:500]}\n"
            f"stderr: {result.stderr[:500]}"
        )
    # Null-pointer audit: no string builtin should ever receive a null pointer
    # argument.  str_concat(ptr null, ...) or str_concat(..., ptr null) means
    # emit_append_call fell through to zero_val("ptr") because the LHS/RHS had
    # type TVar instead of String — a codegen bug that produces a runtime crash.
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if "str_concat(ptr null," in stripped or ", ptr null)" in stripped:
            test.fail(
                f"{label}: generated IR contains str_concat with null argument:\n"
                f"  {stripped}\n"
                "This indicates emit_append_call produced zero_val for a TVar-typed "
                "operand. Check that the ++ operands have concrete String types at "
                "the codegen call site."
            )
    # If llvm-as is available, validate IR syntax.
    if shutil.which("llvm-as"):
        with tempfile.NamedTemporaryFile(
            suffix=".ll", mode="w", delete=False, encoding="utf-8"
        ) as f:
            f.write(result.stdout)
            ll_path = f.name
        try:
            asm = subprocess.run(
                ["llvm-as", ll_path, "-o", "/dev/null"],
                capture_output=True, text=True,
            )
            if asm.returncode != 0:
                test.fail(
                    f"{label}: llvm-as rejected the emitted IR:\n{asm.stderr[:2000]}"
                )
        finally:
            os.unlink(ll_path)


# ---------------------------------------------------------------------------
# Stage-0 tests (compile_driver_bin)
# ---------------------------------------------------------------------------

class Stage0EmitIrTests(unittest.TestCase):
    """compile_driver_bin --emit-ir smoke tests."""


def _make_stage0_test(shape_name: str, source: str):
    def test(self: unittest.TestCase) -> None:
        if not NATIVE_BINARY.exists():
            self.skipTest(f"compile_driver_bin not found at {NATIVE_BINARY}")
        result = _run_emit_ir(NATIVE_BINARY, source)
        _assert_valid_ir(self, result, f"stage-0/{shape_name}")
    test.__name__ = f"test_{shape_name}"
    return test


for _name, _src in SHAPES.items():
    setattr(Stage0EmitIrTests, f"test_{_name}", _make_stage0_test(_name, _src))


# ---------------------------------------------------------------------------
# Stage-1 tests (compile_driver_bin_stage1)
# ---------------------------------------------------------------------------

class Stage1EmitIrTests(unittest.TestCase):
    """compile_driver_bin_stage1 --emit-ir smoke tests (requires just build-stage1)."""


def _make_stage1_test(shape_name: str, source: str):
    def test(self: unittest.TestCase) -> None:
        if not STAGE1_BINARY.exists():
            self.skipTest(
                f"compile_driver_bin_stage1 not found at {STAGE1_BINARY}; "
                "run: just build-stage1"
            )
        result = _run_emit_ir(STAGE1_BINARY, source)
        _assert_valid_ir(self, result, f"stage-1/{shape_name}")
    test.__name__ = f"test_{shape_name}"
    return test


for _name, _src in SHAPES.items():
    setattr(Stage1EmitIrTests, f"test_{_name}", _make_stage1_test(_name, _src))


if __name__ == "__main__":
    unittest.main()
