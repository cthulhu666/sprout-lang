"""
Emit-IR smoke tests and execution parity tests for stage-0 and stage-1 native compilers.

Three test classes:

  Stage0EmitIrTests   — compile_driver_bin --emit-ir: structural + IR-health audits
  Stage1EmitIrTests   — compile_driver_bin_stage1 --emit-ir: same audits
  Stage0ExecutionTests — compile_driver_bin IR linked with clang: verify runtime output

Shapes under test (each chosen to exercise a distinct code path):

  noparam          — top-level fn with no parameters; baseline
  oneparam         — fn with one Int parameter; exercises fn_params / wrapper params
  adtmatch         — fn taking an ADT and doing a pattern match; exercises emit_branches
  generic          — fn using an Eq constraint via a type class; exercises lowered dicts
  strconcat        — ++ with function parameters; previously crashed with null ptr arg
  tuple_fn_as_value — named fn used as first-class value; exercises closure calling convention

Tests skip gracefully if a required binary is absent.  Run `just build-stage1` to produce
compile_driver_bin_stage1.
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from tests.ir_health import assert_valid_ir

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
    # module main triggers prelude inclusion in the bundler, which is required
    # for the native codegen to find CtorSig entries for List/Nil/Cons.
    # Programs without a module declaration run in "simple script" mode where
    # the bundler omits the prelude, leaving those types absent from ctor_sigs.
    "generic": textwrap.dedent("""\
        module main
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
    # module main is required for the same bundler/prelude reason as generic.
    "tuple_fn_as_value": textwrap.dedent("""\
        module main
        fn fst_str(p: (String, String)) -> String =
          match p with | (a, _) -> a
        fn main() -> Unit !{IO} =
          match list_map(fst_str, Cons(("hello", "world"), Nil)) with
          | Cons h _ -> print(h)
          | Nil -> print("empty")
    """),
}

# Expected stdout (stripped) for each shape when compiled and executed.
SHAPE_OUTPUTS: dict[str, str] = {
    "noparam": "42",
    "oneparam": "1",
    "adtmatch": "0",
    "generic": "1",
    "strconcat": "helloworld",
    "tuple_fn_as_value": "hello",
}

# ---------------------------------------------------------------------------
# Shared helpers
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
            # Use errors='replace' so non-UTF-8 bytes in IR don't crash the test
            # runner; ill-formed IR will be caught by the llvm-as validator.
            encoding="utf-8",
            errors="replace",
            cwd=str(ROOT),
            timeout=60,
        )
    finally:
        os.unlink(tmp_path)


def _check_emit_ir_result(
    test: unittest.TestCase,
    result: subprocess.CompletedProcess[str],
    label: str,
) -> None:
    """Gate on non-zero exit, then delegate to the ir_health module."""
    if result.returncode != 0:
        test.fail(
            f"{label}: emit-ir exited {result.returncode}\n"
            f"stderr: {result.stderr[:2000]}\n"
            f"stdout: {result.stdout[:500]}"
        )
    assert_valid_ir(test, result.stdout, label)


# ---------------------------------------------------------------------------
# C-runtime extraction (cached, used by execution tests)
# ---------------------------------------------------------------------------

_RUNTIME_C_CACHE: str | None = None


def _get_runtime_c() -> str:
    """Return the Sprout C runtime source, extracting it once via the Python CLI."""
    global _RUNTIME_C_CACHE
    if _RUNTIME_C_CACHE is not None:
        return _RUNTIME_C_CACHE
    trivial = "fn main() -> Unit !{IO} = print(0)\n"
    with tempfile.TemporaryDirectory() as tmp:
        spr = Path(tmp) / "trivial.spr"
        c_out = Path(tmp) / "runtime.c"
        spr.write_text(trivial, encoding="utf-8")
        r = subprocess.run(
            [
                sys.executable, "-m", "sprout.cli", "compile",
                "--emit-runtime-c", str(c_out),
                "-o", "/dev/null",
                str(spr),
            ],
            capture_output=True,
            text=True,
            cwd=str(ROOT),
            timeout=30,
        )
        if r.returncode != 0:
            raise RuntimeError(
                f"emit-runtime-c failed (rc={r.returncode}):\n{r.stderr[:500]}"
            )
        _RUNTIME_C_CACHE = c_out.read_text(encoding="utf-8")
    return _RUNTIME_C_CACHE


def _link_ir_to_binary(ir: str, tmp_dir: Path) -> Path:
    """Compile LLVM IR + C runtime into a native binary in tmp_dir."""
    ll = tmp_dir / "prog.ll"
    c = tmp_dir / "runtime.c"
    bin_path = tmp_dir / "prog"
    ll.write_text(ir, encoding="utf-8")
    c.write_text(_get_runtime_c(), encoding="utf-8")
    cmd = ["clang", str(ll), str(c), "-O0"]
    if sys.platform == "darwin":
        cmd.extend(["-framework", "Security", "-framework", "CoreFoundation"])
    cmd.extend(["-o", str(bin_path)])
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT), timeout=60)
    if r.returncode != 0:
        raise RuntimeError(f"clang linking failed:\n{r.stderr[:1000]}")
    return bin_path


# ---------------------------------------------------------------------------
# Stage-0 emit-IR tests (compile_driver_bin)
# ---------------------------------------------------------------------------

class Stage0EmitIrTests(unittest.TestCase):
    """compile_driver_bin --emit-ir smoke tests."""


def _make_stage0_test(shape_name: str, source: str):
    def test(self: unittest.TestCase) -> None:
        if not NATIVE_BINARY.exists():
            self.skipTest(f"compile_driver_bin not found at {NATIVE_BINARY}")
        result = _run_emit_ir(NATIVE_BINARY, source)
        _check_emit_ir_result(self, result, f"stage-0/{shape_name}")
    test.__name__ = f"test_{shape_name}"
    return test


for _name, _src in SHAPES.items():
    setattr(Stage0EmitIrTests, f"test_{_name}", _make_stage0_test(_name, _src))


# ---------------------------------------------------------------------------
# Stage-1 emit-IR tests (compile_driver_bin_stage1)
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
        _check_emit_ir_result(self, result, f"stage-1/{shape_name}")
    test.__name__ = f"test_{shape_name}"
    return test


for _name, _src in SHAPES.items():
    setattr(Stage1EmitIrTests, f"test_{_name}", _make_stage1_test(_name, _src))


# ---------------------------------------------------------------------------
# Stage-0 execution parity tests
#
# Verifies that IR emitted by compile_driver_bin, when compiled by clang and
# executed, produces the expected output.  This closes the loop beyond the
# structural IR audits: valid IR != correct IR.
# ---------------------------------------------------------------------------

class Stage0ExecutionTests(unittest.TestCase):
    """compile_driver_bin IR → clang → execute: verify runtime output."""


# tuple_fn_as_value: named functions with tuple parameters produce a closure
# wrapper with signature (ptr %env, { ptr, ptr } %a0), but list_map_go calls
# closures with (ptr %env, i64 %element).  The i64 is a GC-packed pointer to
# the heap tuple; the closure must convert it via inttoptr+load before calling
# the named function.  Until emit_named_fn_wrapper_lines handles this
# conversion, execution of this shape is incorrect.
_KNOWN_CC_BUG_SHAPES: frozenset[str] = frozenset({"tuple_fn_as_value"})


def _make_execution_test(shape_name: str, source: str, expected: str):
    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test(self: unittest.TestCase) -> None:
        if not NATIVE_BINARY.exists():
            self.skipTest(f"compile_driver_bin not found at {NATIVE_BINARY}")
        result = _run_emit_ir(NATIVE_BINARY, source)
        if result.returncode != 0:
            self.fail(
                f"stage-0/{shape_name}: emit-ir exited {result.returncode}\n"
                f"stderr: {result.stderr[:1000]}"
            )
        with tempfile.TemporaryDirectory() as tmp:
            try:
                bin_path = _link_ir_to_binary(result.stdout, Path(tmp))
            except RuntimeError as e:
                self.fail(f"stage-0/{shape_name}: link failed: {e}")
            run = subprocess.run(
                [str(bin_path)],
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
            )
        self.assertEqual(
            run.returncode, 0,
            msg=f"stage-0/{shape_name} binary exited {run.returncode}:\n{run.stderr[:500]}",
        )
        self.assertEqual(
            run.stdout.strip(),
            expected,
            msg=f"stage-0/{shape_name}: expected {expected!r}, got {run.stdout.strip()!r}",
        )
    if shape_name in _KNOWN_CC_BUG_SHAPES:
        test = unittest.expectedFailure(test)
    test.__name__ = f"test_{shape_name}"
    return test


for _name, _src in SHAPES.items():
    _expected = SHAPE_OUTPUTS[_name]
    setattr(
        Stage0ExecutionTests,
        f"test_{_name}",
        _make_execution_test(_name, _src, _expected),
    )


if __name__ == "__main__":
    unittest.main()
