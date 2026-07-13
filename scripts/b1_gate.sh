#!/usr/bin/env bash
# B1-Double regression gate. Guards the four defects found in code review of the
# B1-Double change (PR #167) — behavior tests alone can't catch these because the
# numeric results are identical whether B1 fires or not.
#
#   ①/④a  real `Vector Double` kernels are inlined (vec_get_d present) — so a
#          silent regression of the optimization is caught.
#   ①      a user `type Double = <heap ADT>` (canonical `main.Double`) is NOT
#          inlined — stays a call — or IRVecGetD would load an unrooted heap
#          pointer (use-after-free under GC).
#   ②      an under-applied `vector_get_direct` on a `Vector Double` still
#          compiles (reaches partial-application, does not hard-Err).
#   ④b     an out-of-bounds `Vector Double` read hits the inline bounds guard and
#          aborts (non-zero exit + trap message).
#
# Run: mise exec -- just b1-gate   (needs build/compile_driver_bin_stage1)
set -uo pipefail

STAGE1="build/compile_driver_bin_stage1"
TESTS="tests/stdlib"          # runnable behaviour test (also scanned by `just test`)
FIX="tests/b1_fixtures"       # compile-only fixtures (NOT scanned by `just test`)
TMP="$(mktemp -d /tmp/sprout_b1gate_XXXXXX)"
trap 'rm -rf "$TMP"' EXIT
fail=0

emit() { "$STAGE1" --emit-ir stdlib "$1" 2>/dev/null; }

# ①/④a — B1 fires on a genuine Vector Double.
if [ "$(emit "$TESTS/test_b1_double.spr" | grep -cF '$ep = getelementptr')" -gt 0 ]; then
  echo "  ok: B1 inlines real Vector Double"
else
  echo "  FAIL: B1 not firing on real Vector Double (silent optimization regression)"; fail=1
fi

# ① — shadowed heap `Double` must NOT be inlined (UAF guard).
if [ "$(emit "$FIX/fixture_b1_shadowed_double.spr" | grep -cEe 'vec_get_d|[$]ep = getelementptr')" -eq 0 ]; then
  echo "  ok: shadowed heap 'Double' stays a call"
else
  echo "  FAIL: B1 fired on a shadowed heap 'Double' — unrooted heap load (UAF)"; fail=1
fi

# ①' — an ordinary non-Double PRIMITIVE (Vector Int) stays a call (RepUnsupported).
if [ "$(emit "$FIX/fixture_b1_nondouble.spr" | grep -cEe 'vec_get_d|[$]ep = getelementptr')" -eq 0 ]; then
  echo "  ok: non-Double primitive (Vector Int) stays a call"
else
  echo "  FAIL: B1 fired on a Vector Int — only scalar-Double is inlinable today"; fail=1
fi

# ② — under-applied vector_get_direct on Vector Double still compiles.
if emit "$FIX/fixture_b1_partial.spr" >/dev/null 2>&1; then
  echo "  ok: partial application compiles"
else
  echo "  FAIL: under-applied vector_get_direct on Vector Double no longer compiles"; fail=1
fi

# ④b — out-of-bounds Vector Double read traps at the inline guard.
if emit "$FIX/fixture_b1_oob.spr" > "$TMP/oob.ll" 2>/dev/null \
   && clang "$TMP/oob.ll" runtime/sprout_runtime.c -O2 \
        -framework Security -framework CoreFoundation -o "$TMP/oob" 2>/dev/null; then
  if "$TMP/oob" > "$TMP/oob.out" 2>&1; then
    echo "  FAIL: out-of-bounds Vector Double read did NOT trap"; fail=1
  elif grep -qi "index out of bounds" "$TMP/oob.out"; then
    echo "  ok: out-of-bounds read traps at inline guard"
  else
    echo "  FAIL: out-of-bounds read aborted without the expected message"; fail=1
  fi
else
  echo "  FAIL: could not build the out-of-bounds fixture"; fail=1
fi

if [ "$fail" -eq 0 ]; then echo "b1-gate: PASS"; else echo "b1-gate: FAIL"; fi
exit "$fail"
