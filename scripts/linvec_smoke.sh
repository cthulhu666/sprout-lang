#!/usr/bin/env bash
# Gate: the parts of `LinVec` (stdlib/linvec.sprout) that `just test` cannot see.
#
#   -O2     tests/stdlib/test_linvec.spr links at -O0 there. It runs here at -O2 as well,
#           so a reordering of a borrowed read and a set only the optimizer makes shows.
#   oracle  the same test against a stdlib root where the region of linvec.sprout between
#           `# oracle-begin` and `# oracle-end` (`linvec_set`, `linvec_push` and
#           `linvec_truncate`) copies: it must print what the in-place build prints. A
#           difference is a linearity hole seen as a wrong answer
#           (docs/persistent-array-v0.md §9).
#   trap    a set, scatter or accum out of range exits nonzero with its message.
#   alloc   a set allocates nothing and `linvec.new` one vector: the runtime's
#           SPROUT_DEBUG_ALLOC counters match across runs that differ only in the number
#           of sets, and grow by one vector per `linvec.new`.
#   ir      a `linvec.get` matched with a binding compiles to a worker over
#           `vector_get_unboxed`, so a read allocates no `Maybe` (§3.4). A set is a call to
#           `linvec_set`, which calls `vector_mutset` and builds no closure
#           (docs/unsafe-primitives-v0.md §4.1).
#   shape   `unsafe_pure` takes a lambda literal and cannot be a value. A user module cannot
#           reach either rule past the reference gate, so each case is appended to a copy
#           of linvec.sprout, a module the compiler lists.
#   bce     bounds-check elimination versions no loop in a `stdlib.linvec.*`,
#           `stdlib.linheap.*` or `stdlib.sort.*` declaration; why: stdlib/compiler/bce.sprout, above
#           `safe_io_externs`.
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
if [ "$(grep -c '^# oracle-begin$' "$STDLIB/linvec.sprout")" -ne 1 ] \
   || [ "$(grep -c '^# oracle-end$' "$STDLIB/linvec.sprout")" -ne 1 ]; then
  bad "linvec.sprout needs exactly one oracle-begin and one oracle-end line"
fi
cat > "$TMPD/oracle_region.sprout" <<'SPROUT'
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
SPROUT
awk -v f="$TMPD/oracle_region.sprout" '
  /^# oracle-begin$/ { while ((getline l < f) > 0) print l; skip = 1 }
  !skip { print }
  /^# oracle-end$/ { skip = 0 }
' "$STDLIB/linvec.sprout" > "$ORACLE/linvec.sprout"
if grep -q 'unsafe_pure(' "$ORACLE/linvec.sprout"; then
  bad "the oracle region was not replaced"
fi
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
for entry in "trap_set_high|vector_mutset: index out of bounds" \
             "trap_set_negative|vector_mutset: index out of bounds" \
             "trap_scatter|vector_mutset: index out of bounds" \
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
  awk '/^define i64 @stdlib.linvec.set\(/ {on=1} on {print} on && /^}/ {exit}' "$ir" > "$TMPD/set.ll"
  grep -q 'call i64 @stdlib.linvec.linvec_set(' "$TMPD/set.ll" \
    || bad "linvec.set does not call linvec_set"
  awk '/^define i64 @stdlib.linvec.linvec_set\(/ {on=1} on {print} on && /^}/ {exit}' "$ir" > "$TMPD/linvec_set.ll"
  grep -q 'call i64 @vector_mutset(' "$TMPD/linvec_set.ll" \
    || bad "linvec_set does not call vector_mutset"
  if grep -Eq 'sprout_alloc_closure|unsafe_pure' "$TMPD/linvec_set.ll"; then
    bad "linvec_set builds a closure or calls unsafe_pure"
  fi
else
  bad "no IR for the alloc fixture"
fi

# --- shape: the two type rules of unsafe_pure ---
SHAPE="$TMPD/shape_stdlib"
mkdir -p "$SHAPE"
cp -R "$STDLIB"/. "$SHAPE"/

# shape_case <name> <source> <want>: <source> is appended to linvec.sprout; <want> is a message
# that `--phase check` must print, or `ok` for a clean check.
shape_case() {
  { cat "$STDLIB/linvec.sprout"; printf '\n%s\n' "$2"; } > "$SHAPE/linvec.sprout"
  "$BIN" --phase check "$SHAPE" --package-root "$ROOT" "$SMOKE/trap_set_high.spr" > "$TMPD/shape_$1.out" 2>&1
  ec=$?
  if [ "$3" = ok ]; then
    if [ "$ec" -ne 0 ] || grep -q 'ERROR:' "$TMPD/shape_$1.out"; then
      bad "shape $1: expected a clean check"; head -3 "$TMPD/shape_$1.out" >&2
    fi
  elif ! grep -qF -- "$3" "$TMPD/shape_$1.out"; then
    bad "shape $1: expected '$3'"; head -3 "$TMPD/shape_$1.out" >&2
  fi
}

shape_case literal 'fn shape_literal(n: Int) -> Int = unsafe_pure(\_ -> n)' ok
shape_case variable 'fn shape_variable(f: Unit -> Int !{IO}) -> Int = unsafe_pure(f)' \
  'takes a lambda literal'
shape_case value 'fn shape_value(xs: List (Unit -> Int !{IO})) -> List Int = list_map(unsafe_pure, xs)' \
  'cannot be used as a value'

# --- bce: the premise behind the pure length-changing writes ---
# No loop of a LinVec, LinHeap or LinVec-sorting declaration is versioned. That checks their
# own loops, not the alias premise, which rests on linearity and the abstract type
# (bce.sprout, above `safe_io_externs`). Each test is traced, so each module is in the program.
for t in test_linvec test_linheap test_sort; do
  SPROUT_TRACE_BCE=1 "$BIN" --emit-ir "$STDLIB" --package-root "$ROOT" "$ROOT/tests/stdlib/$t.spr" \
    > /dev/null 2> "$TMPD/bce_$t.err" || { bad "bce trace: emit-IR failed for $t"; continue; }
  if grep -Eq '^\[bce\] versioned stdlib\.(linvec|linheap|sort)\.' "$TMPD/bce_$t.err"; then
    bad "bce versions a loop in stdlib.linvec, stdlib.linheap or stdlib.sort ($t)"
    grep -E '^\[bce\] versioned stdlib\.(linvec|linheap|sort)\.' "$TMPD/bce_$t.err" >&2
  fi
done
# The trace must be live: test_linvec alone reports loops, so an empty one means no trace.
grep -q '^\[bce\] ' "$TMPD/bce_test_linvec.err" || bad "the bce trace printed no [bce] line"

if [ "$fail" -ne 0 ]; then exit 1; fi
echo "==> linvec-smoke ✓"
