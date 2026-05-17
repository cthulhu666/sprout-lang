#!/usr/bin/env python3
"""
GC safety linter for the Sprout C runtime (runtime/sprout_runtime.c).

Detects C functions where sprout_gc_maybe_collect_threshold() fires while
const char* / char* locals or parameters are live but not yet registered
as GC roots.  Such values can be freed by the GC before the function
finishes using them.

Pattern checked:
  1. Function contains a sprout_gc_maybe_collect_threshold() call.
  2. A const char*/char* parameter or local variable is used AFTER that call.
  3. The variable is not registered via register_managed_ptr(),
     SPROUT_GC_PUSH_PTR_LOCAL(), or SPROUT_HANDLE() before the call.

long long heap-pointer arguments (e.g. list handles) are excluded: they are
harder to distinguish from plain integers without full type information.

Rooting mechanisms recognized:
  - register_managed_ptr(var, ...)          — registers a managed allocation
  - SPROUT_GC_PUSH_PTR_LOCAL(var)           — shadow root stack (old style)
  - SPROUT_HANDLE(name, (long long)(uintptr_t)var) — handle table (preferred)

Exit codes:
  0  — no findings, or findings are only in functions whose callers are
       expected to root their arguments (default/informational mode)
  1  — findings present in --strict mode

Usage:
  python3 scripts/gc_safety_check.py            # informational (always 0)
  python3 scripts/gc_safety_check.py --strict   # exit 1 if any issues found
  python3 scripts/gc_safety_check.py --verbose  # also list clean functions
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNTIME_C = ROOT / "runtime" / "sprout_runtime.c"

# Matches the first line of a C function definition:
#   [static] [const] [unsigned] <type> <name>(<params>) {
_FN_SIG_RE = re.compile(
    r"^(?:static\s+)?(?:const\s+)?(?:unsigned\s+)?"
    r"(?:long\s+long|int|char\s*\*?|void|_Bool|size_t|long)\s+"
    r"(?:\*\s*)?(\w+)\s*\([^)]*\)\s*\{",
)


def extract_c_runtime(runtime_c: Path) -> str:
    """Read the C runtime from runtime/sprout_runtime.c."""
    if not runtime_c.exists():
        print(f"ERROR: {runtime_c} not found; run: just update-runtime", file=sys.stderr)
        sys.exit(2)
    return runtime_c.read_text(encoding="utf-8")


def split_into_functions(c: str) -> list[tuple[str, str, int]]:
    """
    Return (fn_name, body, approx_line_number) for each C function.
    Uses balanced-brace extraction starting at each function-signature line.
    """
    results: list[tuple[str, str, int]] = []
    lines = c.splitlines(keepends=True)

    fn_starts: list[tuple[int, int, str]] = []
    offset = 0
    for lineno, line in enumerate(lines, start=1):
        m = _FN_SIG_RE.match(line)
        if m:
            fn_starts.append((offset, lineno, m.group(1)))
        offset += len(line)

    for start, lineno, fn_name in fn_starts:
        depth = 0
        end = start
        for ci in range(start, len(c)):
            ch = c[ci]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = ci + 1
                    break
        body = c[start:end]
        results.append((fn_name, body, lineno))

    return results


def _extract_char_ptr_params(sig_line: str) -> set[str]:
    """Extract const char*/char* parameter names from a function signature line."""
    m = re.search(r"\(([^)]*)\)", sig_line)
    if not m:
        return set()
    params_text = m.group(1)
    return {
        m2.group(1)
        for m2 in re.finditer(r"(?:const\s+)?char\s*\*\s+(\w+)", params_text)
    }


def _extract_char_ptr_locals(body_text: str) -> set[str]:
    """Extract const char*/char* local variable names from a C code block."""
    return {
        m.group(1)
        for m in re.finditer(
            r"(?:const\s+)?char\s*\*\s+(\w+)\s*(?:=|;)", body_text
        )
    }


def check_gc_safety(fn_name: str, body: str, lineno: int) -> list[str]:
    """
    Return warning strings if char* variables are used after a GC call
    without having been registered as managed pointers beforehand.
    """
    gc_call = "sprout_gc_maybe_collect_threshold()"
    if gc_call not in body:
        return []

    before, after = body.split(gc_call, 1)

    # Parameters: from the signature (first line of the body).
    sig_line = body.split("\n", 1)[0]
    heap_params = _extract_char_ptr_params(sig_line)

    # Locals declared before the GC call: skip the signature line itself.
    body_before_without_sig = before.split("\n", 1)[1] if "\n" in before else ""
    heap_locals = _extract_char_ptr_locals(body_before_without_sig)

    # Variables registered or explicitly rooted before the GC call are safe.
    already_tracked = {
        m.group(1)
        for m in re.finditer(r"register_managed_ptr\((\w+)\s*,", before)
    } | {
        m.group(1)
        for m in re.finditer(r"SPROUT_GC_PUSH_(?:PTR|I64)_LOCAL\((\w+)\)", before)
    } | {
        m.group(1)
        for m in re.finditer(r"SPROUT_HANDLE\(\s*\w+\s*,\s*\(long long\)\(uintptr_t\)\s*(\w+)\s*\)", before)
    }

    suspect = (heap_params | heap_locals) - already_tracked

    issues: list[str] = []
    for var in sorted(suspect):
        if re.search(r"\b" + re.escape(var) + r"\b", after):
            issues.append(
                f"  sprout_runtime.c ~line {lineno}: {fn_name}(): "
                f"'{var}' (char* heap ptr) used after sprout_gc_maybe_collect_threshold()"
            )
    return issues


def main() -> int:
    verbose = "--verbose" in sys.argv
    strict = "--strict" in sys.argv

    c = extract_c_runtime(RUNTIME_C)
    functions = split_into_functions(c)

    all_issues: list[str] = []
    for fn_name, body, lineno in functions:
        issues = check_gc_safety(fn_name, body, lineno)
        if issues:
            all_issues.extend(issues)
        elif verbose:
            print(f"  OK  {fn_name}()")

    if all_issues:
        prefix = "WARN" if not strict else "FAIL"
        print(
            f"{prefix}: GC safety issues in {RUNTIME_C.relative_to(ROOT)} "
            f"({len(all_issues)} found):"
        )
        for issue in all_issues:
            print(issue)
        if not strict:
            print(
                "\n  NOTE: these are pre-existing patterns where callers are "
                "expected to root their heap arguments.\n"
                "  Re-run with --strict to treat them as errors."
            )
        return 1 if strict else 0

    print(
        f"GC safety OK — {len(functions)} runtime functions checked, 0 issues."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
