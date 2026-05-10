"""Reusable IR health checks for Sprout codegen tests.

Invariant registry
------------------
  assert_structural_ir       — IR contains at least one 'define' block
  assert_no_str_concat_null  — no str_concat call receives a null ptr argument
  assert_no_undeclared_calls — every 'call @foo' must be define'd or declare'd in the IR
  assert_valid_ir            — all of the above + optional llvm-as syntax check
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import unittest


def assert_structural_ir(test: unittest.TestCase, ir: str, label: str) -> None:
    if "define" not in ir:
        test.fail(
            f"{label}: emit-ir produced no LLVM IR ('define' not found)\n"
            f"stdout preview: {ir[:400]}"
        )


def assert_no_str_concat_null(test: unittest.TestCase, ir: str, label: str) -> None:
    """Catch the TVar-typed ++ bug that produces str_concat(ptr null, ...) in the IR.

    When emit_append_call falls through to zero_val("ptr") because the LHS/RHS has
    type TVar instead of String, the emitted call carries a literal null pointer and
    crashes at runtime.
    """
    for line in ir.splitlines():
        stripped = line.strip()
        if "str_concat(ptr null," in stripped or ", ptr null)" in stripped:
            test.fail(
                f"{label}: str_concat with null argument:\n  {stripped}\n"
                "emit_append_call produced zero_val for a TVar-typed operand. "
                "Ensure ++ operands have concrete String types at the codegen call site."
            )


def assert_no_undeclared_calls(test: unittest.TestCase, ir: str, label: str) -> None:
    """Every direct call target must be define'd or declare'd in the IR.

    LLVM intrinsics (llvm.*) are excluded — they are built into LLVM and never appear
    as declarations.  Any other call to an undeclared symbol will produce a linker error
    at best and silent misbehaviour at worst.  This catches missing C-runtime entries
    (e.g. a codegen-added builtin that was never added to cli.py).
    """
    # Collect all symbols that are defined or declared in the module.
    known: set[str] = set()
    for m in re.finditer(
        r"^(?:define|declare)\s+[^@\n]*@([\w.]+)\s*\(",
        ir,
        re.MULTILINE,
    ):
        known.add(m.group(1))

    # Collect all direct-call targets (handles tail call / musttail prefixes).
    missing: list[str] = []
    for m in re.finditer(r"\bcall\b[^@\n]*@([\w.]+)\s*\(", ir):
        name = m.group(1)
        if name not in known and not name.startswith("llvm."):
            missing.append(name)

    if missing:
        test.fail(
            f"{label}: IR calls undeclared symbols: {sorted(set(missing))}\n"
            "Add a declare line in codegen.sprout or a definition in cli.py."
        )


def assert_valid_ir(test: unittest.TestCase, ir: str, label: str) -> None:
    """Composite IR health check: structural + null-ptr + undeclared-call + llvm-as."""
    assert_structural_ir(test, ir, label)
    assert_no_str_concat_null(test, ir, label)
    assert_no_undeclared_calls(test, ir, label)

    if shutil.which("llvm-as"):
        with tempfile.NamedTemporaryFile(
            suffix=".ll", mode="w", delete=False, encoding="utf-8"
        ) as f:
            f.write(ir)
            ll_path = f.name
        try:
            asm = subprocess.run(
                ["llvm-as", ll_path, "-o", "/dev/null"],
                capture_output=True,
                text=True,
            )
            if asm.returncode != 0:
                test.fail(
                    f"{label}: llvm-as rejected the emitted IR:\n{asm.stderr[:2000]}"
                )
        finally:
            os.unlink(ll_path)
