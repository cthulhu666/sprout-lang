#!/usr/bin/env bash
# Windows port: gate the claim that Sprout's CODE GENERATOR is already portable.
#
# ir_lowering emits `target triple = "unknown-unknown-unknown"` with no datalayout, and every
# value crossing a function boundary is a boxed i64 handle — so there is no struct-passing
# surface for Win64-vs-SysV to disagree about (docs/windows-port-v0.md §1.1). That claim was
# verified once, by hand, at planning time. This runs it on every commit instead.
#
# What it proves: each committed golden IR snapshot compiles to a valid Windows COFF object for
# both Windows targets. What it does NOT prove: that the object LINKS or RUNS — the runtime is
# still POSIX-only, so there is nothing to link against yet. Growth path in .github/workflows/ci.yml.
#
# `musttail` is the reason this is worth gating rather than assuming: mutual-recursion TCO is the
# one codegen feature with a real ABI dependency, so the run asserts the corpus still contains it
# (a golden refresh that dropped every musttail would otherwise silently weaken this gate).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IR_DIR="$ROOT/tests/golden/ir"

# clang is not on PATH by default on the GitHub Windows image (LLVM lives in its own directory),
# so discover it rather than assuming. Opaque-pointer IR needs LLVM >= 16.
CLANG="${SPROUT_CLANG:-}"
if [ -z "$CLANG" ]; then
  if command -v clang >/dev/null 2>&1; then
    CLANG="clang"
  elif [ -x "/c/Program Files/LLVM/bin/clang.exe" ]; then
    CLANG="/c/Program Files/LLVM/bin/clang.exe"
  elif [ -x "/usr/bin/clang" ]; then
    CLANG="/usr/bin/clang"
  else
    echo "[windows-ir-gate] no clang found. Set SPROUT_CLANG to one (LLVM >= 16)." >&2
    exit 1
  fi
fi

echo "==> clang: $CLANG"
"$CLANG" --version | head -1

shopt -s nullglob
FILES=("$IR_DIR"/*.ll)
if [ ${#FILES[@]} -eq 0 ]; then
  echo "[windows-ir-gate] no golden IR in $IR_DIR — nothing to gate, which is itself wrong." >&2
  exit 1
fi

# Guard against a golden refresh silently removing the ABI-sensitive construct this gate exists for.
if ! grep -lq musttail "${FILES[@]}" 2>/dev/null; then
  echo "[windows-ir-gate] no golden contains \`musttail\`. Either TCO codegen changed or the" >&2
  echo "  corpus was regenerated from sources that no longer exercise it. Do not silence this." >&2
  exit 1
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# COFF machine type, little-endian, at offset 0. IMAGE_FILE_MACHINE_AMD64 = 0x8664,
# IMAGE_FILE_MACHINE_ARM64 = 0xAA64. Checking these means the gate verifies a real object for the
# requested architecture rather than merely a zero exit status.
probe_target() {   # probe_target <triple> <expected first two bytes>
  local triple="$1" want="$2" ok=0 fail=0
  echo
  echo "== $triple"
  for f in "${FILES[@]}"; do
    local obj="$TMP/out.obj"
    if ! "$CLANG" -c --target="$triple" -Wno-override-module "$f" -o "$obj" 2>"$TMP/err"; then
      echo "  FAIL (compile)  $(basename "$f")"
      head -3 "$TMP/err" | sed 's/^/      /'
      fail=$((fail + 1))
      continue
    fi
    local magic
    magic="$(od -An -tx1 -N2 "$obj" | tr -d ' \n')"
    if [ "$magic" != "$want" ]; then
      echo "  FAIL (magic $magic, want $want)  $(basename "$f")"
      fail=$((fail + 1))
      continue
    fi
    ok=$((ok + 1))
  done
  echo "  $ok ok, $fail failed"
  TOTAL_FAIL=$((TOTAL_FAIL + fail))
}

TOTAL_FAIL=0
probe_target x86_64-pc-windows-msvc  6486
probe_target aarch64-pc-windows-msvc 64aa

echo
if [ "$TOTAL_FAIL" -ne 0 ]; then
  echo "==> windows-ir-gate FAILED ($TOTAL_FAIL objects)" >&2
  echo "    Codegen has grown a target assumption. Read the diagnostics above before changing" >&2
  echo "    this gate — docs/windows-port-v0.md §1.1 is what just stopped being true." >&2
  exit 1
fi
echo "==> windows-ir-gate ✓ (${#FILES[@]} golden IR files × 2 Windows targets)"
