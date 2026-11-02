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
#   stores a scalar `vector_mutset` lowers inline, directly and through a store
#          wrapper (`mutvec_set`, the `wsi` pass); any other value keeps the call.
#
# Run: mise exec -- just b1-gate   (needs build/compile_driver_bin_stage1)
set -uo pipefail

# Honour the justfile's build_dir (the b1-gate recipe passes it) rather than hardcoding
# build/. Without this the gate cannot run under `just linux-run b1-gate`, which overrides
# build_dir so a Linux container never writes a Linux ELF into the host's build/ — it would
# have tried to exec the host's Mach-O binary instead.
STAGE1="${SPROUT_STAGE1:-build/compile_driver_bin_stage1}"
TESTS="tests/stdlib"          # runnable behaviour test (also scanned by `just test`)
FIX="tests/b1_fixtures"       # compile-only fixtures (NOT scanned by `just test`)
TMP="$(mktemp -d /tmp/sprout_b1gate_XXXXXX)"
trap 'rm -rf "$TMP"' EXIT
fail=0

# The ④b case LINKS and RUNS a binary, so it needs the platform's link flags. These were
# hardcoded to macOS frameworks while this gate was unreachable from `gate` and CI (see
# BACKLOG, "gates that claim verification but nothing runs"); wiring it into CI without
# this would have failed to link on the Linux runner. Same conditional as
# tests/c_runtime/run.sh, and the same reason: Security/CoreFoundation exist only on Darwin.
if [[ "$(uname)" == "Darwin" ]]; then
  CLANG_EXTRA=(-framework Security -framework CoreFoundation)
else
  CLANG_EXTRA=()
fi

emit() { "$STAGE1" --emit-ir stdlib "$1" 2>/dev/null; }

# Extract one function's body from emitted IR.
#
# The two NEGATIVE assertions below must look at the FIXTURE's OWN function, not at the whole
# module. Since the prelude-extern split, `vector_get_direct` is owned by stdlib.mutable, so
# these fixtures must import it — and that module contains genuine `Vector Double` kernels
# (mutmatrix_row_dot and friends) whose inlined `vec_get_d` ops are CORRECT and have nothing
# to do with what these cases assert. A whole-module grep counts those and reports a UAF that
# is not there. Scoping to the fixture's own `define` is what the assertions always meant.
fn_body() { awk "/^define i64 @$1\(/,/^\}/"; }

# ①/④a — B1 fires on a genuine Vector Double.
if [ "$(emit "$TESTS/test_b1_double.spr" | grep -cF '$ep = getelementptr')" -gt 0 ]; then
  echo "  ok: B1 inlines real Vector Double"
else
  echo "  FAIL: B1 not firing on real Vector Double (silent optimization regression)"; fail=1
fi

# ① — shadowed heap `Double` must NOT be inlined (UAF guard).
if [ "$(emit "$FIX/fixture_b1_shadowed_double.spr" | fn_body 'main\.read_first' | grep -cEe 'vec_get_d|[$]ep = getelementptr')" -eq 0 ]; then
  echo "  ok: shadowed heap 'Double' stays a call"
else
  echo "  FAIL: B1 fired on a shadowed heap 'Double' — unrooted heap load (UAF)"; fail=1
fi

# ①' — a read of an ordinary non-Double PRIMITIVE (Vector Int) stays a call.
if [ "$(emit "$FIX/fixture_b1_nondouble.spr" | fn_body 'main\.read_first' | grep -cEe 'vec_get_d|[$]ep = getelementptr')" -eq 0 ]; then
  echo "  ok: non-Double primitive (Vector Int) stays a call"
else
  echo "  FAIL: B1 fired on a Vector Int — only Vector Double reads inline"; fail=1
fi

# ② — under-applied vector_get_direct on Vector Double compiles AND actually builds a
# closure. "Compiles" alone was the original assertion and is weaker than the property
# ② names: it would also pass if the call were silently fully applied or erased.
#
# NOTE, verified 2026-08-11 — do NOT "fix" this by asserting B1 does not fire. It DOES
# fire here, and correctly: the emitted IR contains
#     define i64 @__sprout_ir_lambda_N(i64 %p$env$, i64 %p$$ph_0)
# whose body bounds-checks (`icmp uge` → panic) and then does the inlined load indexed
# by %p$$ph_0 — the placeholder bound as the CLOSURE'S PARAMETER. That is the
# inline happening inside the closure body at call time, which is both safe (a
# `Vector Double` element is a scalar, so no unrooted heap pointer — the ① hazard does
# not apply) and desirable. What ② forbids is the arity hard-Err, not the inline.
if b1_partial_ir="$(emit "$FIX/fixture_b1_partial.spr" 2>/dev/null)" && [ -n "$b1_partial_ir" ]; then
  if grep -qEe '^define .*@__sprout_ir_lambda_[0-9]+\(.*%p[$][$]ph_0' <<<"$b1_partial_ir"; then
    echo "  ok: partial application compiles and builds a placeholder closure"
  else
    echo "  FAIL: under-applied vector_get_direct compiled but built NO placeholder closure"; fail=1
  fi
else
  echo "  FAIL: under-applied vector_get_direct on Vector Double no longer compiles"; fail=1
fi

# check_trap <fixture> <tag> <exact-text> <label> — the fixture builds, runs, exits non-zero
# and prints <exact-text> as a whole line.
check_trap() {
  local fixture="$1" tag="$2" text="$3" label="$4"
  if emit "$fixture" > "$TMP/$tag.ll" 2>/dev/null \
     && clang "$TMP/$tag.ll" runtime/*.c -O2 \
          "${CLANG_EXTRA[@]}" -o "$TMP/$tag" 2>/dev/null; then
    if "$TMP/$tag" > "$TMP/$tag.out" 2>&1; then
      echo "  FAIL: $label did NOT trap"; fail=1
    elif grep -qxF "$text" "$TMP/$tag.out"; then
      echo "  ok: $label traps with the runtime's text"
    else
      echo "  FAIL: $label trapped with the wrong text: $(head -3 "$TMP/$tag.out")"; fail=1
    fi
  else
    echo "  FAIL: could not build the $label fixture"; fail=1
  fi
}

# ④b — out-of-bounds Vector Double read traps at the inline guard.
check_trap "$FIX/fixture_b1_oob.spr" oob \
  'runtime error: vector_get_direct: index out of bounds' 'out-of-bounds read'

# Scalar stores — a direct `vector_mutset` whose VALUE is Int/Bool/Char/Double lowers inline (guard
# plus store, no call); a String value, or one typed `a`, keeps the call.
store_ir="$(emit "$FIX/fixture_b1_store_scalar.spr")"
for ty in int bool char double; do
  body="$(fn_body "main\\.store_$ty" <<<"$store_ir")"
  if [ -n "$body" ] && ! grep -qF '@vector_mutset' <<<"$body" && grep -qF 'vpanic_' <<<"$body"; then
    echo "  ok: $ty store lowers inline"
  else
    echo "  FAIL: $ty store still calls vector_mutset (or has no inline guard)"; fail=1
  fi
done
for fn in string generic; do
  if fn_body "main\\.store_$fn" <<<"$store_ir" | grep -qF '@vector_mutset'; then
    echo "  ok: $fn store keeps the call"
  else
    echo "  FAIL: $fn store lost its vector_mutset call (barrier skipped on a heap value)"; fail=1
  fi
done

# Out-of-range Int store traps at the inline guard.
check_trap "$FIX/fixture_b1_oob_store.spr" oobs \
  'runtime error: vector_mutset: index out of bounds' 'out-of-bounds store'

# Wrapper stores — a call to a store wrapper (`mutvec_set`, or a user wrapper of the same shape)
# whose stored value is a scalar expands to the inline store: no call to the wrapper, none to
# `vector_mutset`, and the guard is present. Every other shape keeps its wrapper call.
# `has_call <sym>` reads a function body on stdin.
has_call() { grep -qF "@$1("; }

wrap_ir="$(emit "$FIX/fixture_b1_wrapper_store.spr")"
for fn in set_int set_bool set_char set_double put_int; do
  body="$(fn_body "main\\.$fn" <<<"$wrap_ir")"
  if [ -n "$body" ] && grep -qF 'vpanic_' <<<"$body" \
     && ! has_call vector_mutset <<<"$body" \
     && ! has_call stdlib.mutable.mutvec_set <<<"$body" \
     && ! has_call main.put <<<"$body"; then
    echo "  ok: $fn wrapper store expands inline"
  else
    echo "  FAIL: $fn still calls its store wrapper (or has no inline guard)"; fail=1
  fi
done
for entry in set_string:stdlib.mutable.mutvec_set set_generic:stdlib.mutable.mutvec_set \
             put_twice_int:main.put_twice put_cell_int:main.put_cell; do
  fn="${entry%%:*}"; sym="${entry#*:}"
  if fn_body "main\\.$fn" <<<"$wrap_ir" | has_call "$sym"; then
    echo "  ok: $fn keeps the call to $sym"
  else
    echo "  FAIL: $fn lost its call to $sym (a store that must keep the barrier was expanded)"; fail=1
  fi
done
# A parameter named `mutvec_set` is the caller's own function: the call goes through the closure.
body="$(fn_body 'main\.set_shadowed' <<<"$wrap_ir")"
if [ -n "$body" ] && grep -qF 'sprout_closure_arity_check' <<<"$body" \
   && ! grep -qF 'vpanic_' <<<"$body" && ! has_call vector_mutset <<<"$body" \
   && ! has_call stdlib.mutable.mutvec_set <<<"$body"; then
  echo "  ok: a parameter named like the wrapper is not expanded"
else
  echo "  FAIL: a parameter named mutvec_set was treated as the stdlib wrapper"; fail=1
fi
# SPROUT_OPT_OFF=wsi keeps the wrapper call.
off_ir="$(env SPROUT_OPT_OFF=wsi "$STAGE1" --emit-ir stdlib "$FIX/fixture_b1_wrapper_store.spr" 2>/dev/null)"
if fn_body 'main\.set_int' <<<"$off_ir" | has_call stdlib.mutable.mutvec_set; then
  echo "  ok: SPROUT_OPT_OFF=wsi keeps the wrapper call"
else
  echo "  FAIL: SPROUT_OPT_OFF=wsi did not disable the pass"; fail=1
fi

# Out-of-range Int store through the wrapper traps with the runtime's text.
check_trap "$FIX/fixture_b1_oob_wrapper_store.spr" oobw \
  'runtime error: vector_mutset: index out of bounds' 'out-of-bounds wrapper store'

if [ "$fail" -eq 0 ]; then echo "b1-gate: PASS"; else echo "b1-gate: FAIL"; fi
exit "$fail"
