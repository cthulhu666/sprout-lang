#!/usr/bin/env bash
# Gate: the parts of `LinVec` (stdlib/linvec.sprout) that `just test` cannot see.
#
#   -O2     tests/stdlib/test_linvec.spr links at -O0 there. It runs here at -O2 as well,
#           so a reordering of a borrowed read and a set only the optimizer makes shows.
#   oracle  the same test against a stdlib root whose `linvec_set`, `linvec_push` and
#           `linvec_truncate` copy: it must print what the in-place build prints. A
#           difference is a linearity hole seen as a wrong answer
#           (docs/persistent-array-v0.md §9).
#   trap    a set, scatter or accum out of range exits nonzero with its message.
#   alloc   a set allocates nothing and `linvec.new` one vector: the runtime's
#           SPROUT_DEBUG_ALLOC counters match across runs that differ only in the number
#           of sets, and grow by one vector per `linvec.new`.
#   ir      a `linvec.get` matched with a binding compiles to a worker over
#           `vector_get_unboxed`, so a read allocates no `Maybe` (§3.4).
#   bce     `stdlib/linvec.sprout` and `stdlib/linheap.sprout` never name `vector_get_direct`,
#           `vector_mutset`, `vector_push` or `vector_truncate`; why: stdlib/compiler/bce.sprout,
#           above `safe_io_externs`.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BIN="${SPROUT_LINVEC_BIN:-$ROOT/build/compile_driver_bin_stage1}"
STDLIB="${SPROUT_LINVEC_STDLIB:-$ROOT/stdlib}"
CC_EXTRA="${SPROUT_LINVEC_CC_EXTRA:-}"
SMOKE="$ROOT/tests/linvec_smoke"
TMPD=$(mktemp -d /tmp/sprout_linvec_XXXXXX)
trap 'rm -rf "$TMPD"' EXIT
fail=0

bad() { echo "linvec-smoke: FAIL $*" >&2; fail=1; }

# The runtime is compiled once (each source -> its own .o); every build links the set.
mkdir -p "$TMPD/rtobj"
for src in "$ROOT"/runtime/*.c; do
  # shellcheck disable=SC2086
  clang -c "$src" -O2 $CC_EXTRA -o "$TMPD/rtobj/$(basename "$src" .c).o" 2> "$TMPD/rt.err" \
    || { echo "linvec-smoke: runtime compile failed ($src)" >&2; cat "$TMPD/rt.err" >&2; exit 1; }
done

# build <source> <stdlib root> <out binary>: -O2; the IR is kept at <out binary>.ll.
build() {
  "$BIN" --emit-ir "$2" --package-root "$ROOT" "$1" > "$3.ll" 2> "$3.err" \
    || { echo "linvec-smoke: emit-IR failed for $1" >&2; cat "$3.err" >&2; return 1; }
  # shellcheck disable=SC2086
  clang "$3.ll" "$TMPD"/rtobj/*.o -O2 $CC_EXTRA -o "$3" 2> "$3.err" \
    || { echo "linvec-smoke: link failed for $1" >&2; cat "$3.err" >&2; return 1; }
}

# --- -O2 and oracle ---
TEST="$ROOT/tests/stdlib/test_linvec.spr"
if build "$TEST" "$STDLIB" "$TMPD/real"; then
  "$TMPD/real" > "$TMPD/real.out" 2>&1
  grep -q '^SUITE PASSED' "$TMPD/real.out" || { bad "test_linvec at -O2"; tail -5 "$TMPD/real.out" >&2; }
else
  fail=1
fi

ORACLE="$TMPD/oracle_stdlib"
mkdir -p "$ORACLE"
cp -R "$STDLIB"/. "$ORACLE"/
grep -Ev '^extern fn linvec_(set|push|truncate)' "$STDLIB/linvec.sprout" > "$ORACLE/linvec.sprout"
cat >> "$ORACLE/linvec.sprout" <<'EOF'

fn linvec_set(i: Int, x: a, v: LinVec a) -> LinVec a =
  match v with
  | LinVec raw ->
      match vec_set(i, x, Vec(raw)) with
      | Vec copy -> LinVec(copy)

fn linvec_push(x: a, v: LinVec a) -> LinVec a =
  match v with
  | LinVec raw -> LinVec(vector_append(raw, x))

fn linvec_truncate(n: Int, v: LinVec a) -> LinVec a =
  match v with
  | LinVec raw ->
      match vec_slice(0, n, Vec(raw)) with
      | Vec copy -> LinVec(copy)
EOF
if build "$TEST" "$ORACLE" "$TMPD/oracle"; then
  "$TMPD/oracle" > "$TMPD/oracle.out" 2>&1
  if cmp -s "$TMPD/real.out" "$TMPD/oracle.out"; then :; else
    bad "test_linvec prints differently with copying linvec_set, linvec_push and linvec_truncate"
    diff "$TMPD/real.out" "$TMPD/oracle.out" | head -10 >&2
  fi
else
  fail=1
fi

# --- traps ---
for entry in "trap_set_high|linvec_set: index out of bounds" \
             "trap_set_negative|linvec_set: index out of bounds" \
             "trap_scatter|linvec_set: index out of bounds" \
             "trap_accum|linvec.accum: index out of bounds"; do
  name="${entry%%|*}"; want="${entry#*|}"
  build "$SMOKE/$name.spr" "$STDLIB" "$TMPD/$name" || { fail=1; continue; }
  "$TMPD/$name" > "$TMPD/$name.out" 2> "$TMPD/$name.err"
  ec=$?
  if [ "$ec" -eq 0 ]; then bad "$name did not trap"
  elif ! grep -qF "$want" "$TMPD/$name.err"; then bad "$name: expected '$want' on stderr"; cat "$TMPD/$name.err" >&2
  fi
done

# --- allocation counters ---
field() { sed -n "s/.*\[sprout alloc\].* $2=\([0-9]*\).*/\1/p" "$1"; }

# measure <sets> <news>: the counters from one run of the alloc fixture.
measure() {
  sed -e "s/SETS/$1/" -e "s/NEWS/$2/" "$SMOKE/alloc_sets.spr" > "$TMPD/alloc_$1_$2.spr"
  build "$TMPD/alloc_$1_$2.spr" "$STDLIB" "$TMPD/alloc_$1_$2" || return 1
  SPROUT_DEBUG_ALLOC=1 "$TMPD/alloc_$1_$2" > /dev/null 2> "$TMPD/alloc_$1_$2.err" || return 1
  grep -q '\[sprout alloc\]' "$TMPD/alloc_$1_$2.err"
}

if measure 0 0 && measure 300000 0 && measure 0 5; then
  few="$TMPD/alloc_0_0.err"; many="$TMPD/alloc_300000_0.err"; news="$TMPD/alloc_0_5.err"
  for k in sprout_obj vector arena_bytes offarena_bytes; do
    a=$(field "$few" "$k"); b=$(field "$many" "$k")
    [ -n "$a" ] && [ "$a" = "$b" ] || bad "300000 sets changed $k: ${a:-?} -> ${b:-?}"
  done
  v0=$(field "$few" vector); v5=$(field "$news" vector)
  [ -n "$v0" ] && [ -n "$v5" ] && [ $((v5 - v0)) -eq 5 ] || bad "5 linvec.new allocated $((v5 - v0)) vectors, not 5"
  echo "linvec-smoke: 0 and 300000 sets both allocate $(field "$few" vector) vectors / $(field "$few" sprout_obj) objects; 5 more linvec.new add $((v5 - v0))"
else
  bad "the alloc fixture did not build or run"
fi

# --- IR: the read gets a CPR worker over the unboxed read ---
ir="$TMPD/alloc_0_0.ll"
if [ -s "$ir" ]; then
  grep -q 'call i128 @stdlib.linvec.get_worker(' "$ir" \
    || bad "no caller uses get_worker"
  awk '/^define i128 @stdlib.linvec.get_worker\(/ {on=1} on {print} on && /^}/ {exit}' "$ir" > "$TMPD/worker.ll"
  grep -q 'call i128 @vector_get_unboxed(' "$TMPD/worker.ll" \
    || bad "get_worker does not read through vector_get_unboxed"
else
  bad "no IR for the alloc fixture"
fi

# --- bce: the premise behind the pure length-changing builtins ---
# Comments are stripped first, so a mention in prose does not count.
for f in linvec linheap; do
  if sed 's/#.*//' "$STDLIB/$f.sprout" | grep -Eq 'vector_(get_direct|mutset|push|truncate)'; then
    bad "$f.sprout names vector_get_direct, vector_mutset, vector_push or vector_truncate"
  fi
done

if [ "$fail" -ne 0 ]; then exit 1; fi
echo "==> linvec-smoke ✓"
