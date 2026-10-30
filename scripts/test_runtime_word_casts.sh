#!/usr/bin/env bash
# runtime_word_casts.py against copies of runtime/ with defects planted.
# Each case checks the exit code AND that the message names the offending decl.
#
# A clang AST dump of runtime/ takes seconds, so the checker runs with --scan-cache: three trees
# are scanned (the real one, mutants, planted functions) and every case that only varies the
# ADDRESS_CASTS file re-reads that tree's table. `just runtime-word-casts` never passes the flag. Case (a) IS
# the real gate on the real tree, so CI does not run `runtime-word-casts` separately.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CHECK="$ROOT/scripts/runtime_word_casts.py"
CLANG="${CLANG:-clang}"
TMPD="$(mktemp -d "${TMPDIR:-/tmp}/sprout_wordcasts_test_XXXXXX")"
trap 'rm -rf "$TMPD"' EXIT

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

# One source for every case: $TMPD/orig is the pristine tree, the address list inside it is the
# one every case starts from, and each tree below is a copy of it.
cp -R "$ROOT/runtime" "$TMPD/orig"
ADDR="$TMPD/orig/ADDRESS_CASTS"
RT="$TMPD/orig"
RT_M="$TMPD/rt_mutants"
RT_T="$TMPD/rt_planted"
cp -R "$RT" "$RT_M"
cp -R "$RT" "$RT_T"
CACHE="$TMPD/cache_orig.json"

# run <runtime-dir> <address-casts> <cache>: output in $OUT, exit code in $RC.
run() {
  RC=0
  OUT="$(python3 -I "$CHECK" "$CLANG" "$1" "$2" --scan-cache "$3" 2>&1)" || RC=$?
}

# expect_fail <case> <needle>: the last run exited 1 and printed <needle>.
expect_fail() {
  [[ "$RC" == 1 ]] || fail "$1: exit $RC, want 1: $OUT"
  grep -qF -- "$2" <<<"$OUT" || fail "$1: output lacks '$2': $OUT"
  echo "ok: $1"
}

# expect_absent <case> <needle>: the last run's output does not mention <needle>.
expect_absent() {
  if grep -qF -- "$2" <<<"$OUT"; then fail "$1: output mentions '$2': $OUT"; fi
  echo "ok: $1"
}

# plant <file> <exact line> <inserted line>: insert after the first line equal to <exact line>.
plant() {
  local tmp="$1.tmp"
  awk -v at="$2" -v add="$3" '{ print } !done && $0 == at { print add; done = 1 }' "$1" >"$tmp"
  cmp -s "$1" "$tmp" && fail "plant: no line '$2' in $1"
  mv "$tmp" "$1"
}

# (a) the real tree passes, against its own lists. The cache file does not exist yet, so this
# run scans and reads nothing; the copies below reuse its table, since the sources are identical.
run "$ROOT/runtime" "$ROOT/runtime/ADDRESS_CASTS" "$CACHE"
[[ "$RC" == 0 ]] || fail "(a) real tree: exit $RC: $OUT"
grep -q '^runtime-word-casts: ok' <<<"$OUT" || fail "(a) no ok line: $OUT"
echo "ok: (a) real tree passes"

# (b), (c) planted in one tree. The cache is the pristine tree's, so the run also shows that a
# cache written for other sources is ignored.
cp "$CACHE" "$TMPD/cache_mutants.json"
# (b) char_to_string is an exported String producer with no cast today.
plant "$RT_M/sprout_runtime.c" 'long long char_to_string(long long ch) {' \
  '  return (long long)(uintptr_t)"x";'
# (c) a cast in ref_new, which reaches the word through sprout_ptr_word and has none of its own.
plant "$RT_M/sprout_runtime.c" 'long long ref_new(long long value) {' \
  '  uintptr_t extra_ = (uintptr_t)&value; (void)extra_;'
run "$RT_M" "$ADDR" "$TMPD/cache_mutants.json"
expect_fail "(b) mutant in char_to_string" 'runtime/sprout_runtime.c:char_to_string has 1 pointer-to-integer cast(s) outside the exits and ADDRESS_CASTS'
expect_fail "(c) cast in ref_new" 'runtime/sprout_runtime.c:ref_new has 1 pointer-to-integer cast(s) outside the exits and ADDRESS_CASTS'

# (e) a long long function in ADDRESS_CASTS, where its casts could reach a Sprout word.
{ cat "$ADDR"; echo 'runtime/sprout_runtime.c:ref_new  # wrong: returns long long'; } >"$TMPD/addr_ll"
run "$RT" "$TMPD/addr_ll" "$CACHE"
expect_fail "(e) long long ref_new in ADDRESS_CASTS" 'runtime/sprout_runtime.c:ref_new returns a 64-bit integer type (long long)'

# (f) ADDRESS_CASTS names with no cast, and with no definition, and in the wrong file.
{ cat "$ADDR"; echo 'runtime/sprout_runtime.c:str_len  # defined, no pointer-to-integer cast'; } >"$TMPD/addr_nocast"
run "$RT" "$TMPD/addr_nocast" "$CACHE"
expect_fail "(f) str_len has no cast" 'ADDRESS_CASTS: runtime/sprout_runtime.c:str_len has no pointer-to-integer cast'
{ cat "$ADDR"; echo 'runtime/sprout_runtime.c:no_such_fn  # not defined'; } >"$TMPD/addr_undef"
run "$RT" "$TMPD/addr_undef" "$CACHE"
expect_fail "(f) no_such_fn is not defined" 'ADDRESS_CASTS: runtime/sprout_runtime.c:no_such_fn is not defined'
{ cat "$ADDR"; echo 'runtime/sprout_scheduler.c:arena_contains  # defined in another file'; } >"$TMPD/addr_wrongfile"
run "$RT" "$TMPD/addr_wrongfile" "$CACHE"
expect_fail "(f) arena_contains is not defined in sprout_scheduler.c" 'ADDRESS_CASTS: runtime/sprout_scheduler.c:arena_contains is not defined'

# (g)-(i) platform tags.  THIS is the host python sees; OTHER is the one it ignores.
if [[ "$(uname -s)" == Darwin ]]; then THIS=darwin OTHER=linux; else THIS=linux OTHER=darwin; fi
{ cat "$ADDR"; echo "runtime/sprout_runtime.c:str_len $THIS  # defined, no cast"; } >"$TMPD/addr_tag_this"
run "$RT" "$TMPD/addr_tag_this" "$CACHE"
expect_fail "(g) str_len tagged $THIS is stale" 'ADDRESS_CASTS: runtime/sprout_runtime.c:str_len has no pointer-to-integer cast'
{ cat "$ADDR"; echo "runtime/sprout_runtime.c:str_len $OTHER  # defined, no cast"; } >"$TMPD/addr_tag_other"
run "$RT" "$TMPD/addr_tag_other" "$CACHE"
[[ "$RC" == 0 ]] || fail "(h) str_len tagged $OTHER: exit $RC: $OUT"
echo "ok: (h) entry tagged $OTHER is ignored"
{ cat "$ADDR"; echo 'runtime/sprout_runtime.c:str_len beos  # bad tag'; } >"$TMPD/addr_tag_bad"
run "$RT" "$TMPD/addr_tag_bad" "$CACHE"
expect_fail "(i) unknown tag" 'malformed entry'
{ cat "$ADDR"; echo 'str_len  # no file'; } >"$TMPD/addr_nofile"
run "$RT" "$TMPD/addr_nofile" "$CACHE"
expect_fail "(i) entry without a file" 'malformed entry'

# (j)-(q) planted functions, all in one tree. Each case lists some of them in ADDRESS_CASTS. The
# rest stay unlisted and fail rule 1, so a case that expects a decl to pass checks that the
# output does not name it.
R=runtime/sprout_runtime.c
S=runtime/sprout_scheduler.c
N=runtime/sprout_wc_new.c
Q=runtime/sprout_wc_q.c
PLANTED=("$R:wc_ret_uintptr" "$R:wc_ret_long" "$R:wc_ret_intptr" "$R:wc_out_param" "$R:wc_byval_ok"
  "$R:wc_same" "$S:wc_same" "$N:wc_new_fn"
  "$R:wc_a2_field" "$R:wc_a2_call" "$R:wc_a2_intptr" "$R:wc_a2_implicit" "$R:wc_a1_ulong"
  "$R:wc_ok_size" "$R:wc_ok_narrow" "$Q:sprout_ptr_word")
cat >>"$RT_T/sprout_runtime.c" <<'EOF'
uintptr_t wc_ret_uintptr(void* p) { return (uintptr_t)p; }
long wc_ret_long(void* p) { return (long)(uintptr_t)p; }
intptr_t wc_ret_intptr(void* p) { return (intptr_t)p; }
int wc_out_param(void* p, long long* out) { *out = (long long)(uintptr_t)p; return 0; }
void* wc_byval_ok(uintptr_t a, void* p) { return (void*)((uintptr_t)p + a); }
static int wc_same(void* p) { return (int)(uintptr_t)p; }
struct wc_box { long long f; };
extern void wc_sink(long long);
int wc_a2_field(struct wc_box* o, void* p) { o->f = (long long)(uintptr_t)p; return 0; }
void wc_a2_call(void* p) { wc_sink((long long)(uintptr_t)p); }
void wc_a2_intptr(void* p, intptr_t i) { wc_sink(i); (void)(uintptr_t)p; }
void wc_a2_implicit(void) { uintptr_t u = 0; wc_sink(u); }
void wc_a1_ulong(void* p) { unsigned long x = (unsigned long)p; (void)x; }
void wc_ok_size(void* p) { size_t n = (size_t)(uintptr_t)p; (void)n; }
int wc_ok_narrow(void* p) { uintptr_t u = (uintptr_t)p; return (int)u; }
EOF
cat >>"$RT_T/sprout_scheduler.c" <<'EOF'
static int wc_same(void* p) { return (int)(uintptr_t)p; }
EOF
cat >"$RT_T/sprout_wc_new.c" <<'EOF'
#include <stdint.h>
#include "sprout_word.h"
int wc_new_fn(void* p) { return (int)(uintptr_t)p; }
EOF
# Does not include sprout_word.h, so its own sprout_ptr_word is a different function from the exit.
cat >"$RT_T/sprout_wc_q.c" <<'EOF'
#include <stdint.h>
static int sprout_ptr_word(void* p) { return (int)(uintptr_t)p; }
EOF
CACHE_T="$TMPD/cache_planted.json"

# lists <decl>...: writes $TMPD/addr_case, the real ADDRESS_CASTS plus those planted decls.
lists() {
  cp "$ADDR" "$TMPD/addr_case"
  local d
  for d in "$@"; do echo "$d  # planted" >>"$TMPD/addr_case"; done
}

# expect_allowed <case> <decl>...: the last run still failed on the planted decls left unlisted,
# and named none of <decl>.
expect_allowed() {
  local c="$1" d
  shift
  [[ "$RC" == 1 ]] || fail "$c: exit $RC, want 1 (unlisted planted decls): $OUT"
  for d in "$@"; do
    if grep -qF -- "$d " <<<"$OUT"; then fail "$c: output names '$d': $OUT"; fi
  done
  echo "ok: $c"
}

# (j) return rule and out-param rule; a by-value uintptr_t parameter is not named.
lists "$R:wc_ret_uintptr" "$R:wc_ret_long" "$R:wc_ret_intptr" "$R:wc_out_param" "$R:wc_byval_ok"
run "$RT_T" "$TMPD/addr_case" "$CACHE_T"
expect_fail "(j) uintptr_t return" "$R:wc_ret_uintptr returns a 64-bit integer type (uintptr_t)"
expect_fail "(j) long return" "$R:wc_ret_long returns a 64-bit integer type (long)"
expect_fail "(j) intptr_t return" "$R:wc_ret_intptr returns a 64-bit integer type (intptr_t)"
expect_fail "(j) long long* parameter" "$R:wc_out_param takes a pointer to a 64-bit integer type (long long *)"
expect_absent "(j) by-value uintptr_t parameter is not rejected" "wc_byval_ok"

# (k) a by-value uintptr_t parameter with a pointer return is allowed; so is the static
# wc_same of the file the entry names.
lists "$R:wc_byval_ok" "$R:wc_same"
run "$RT_T" "$TMPD/addr_case" "$CACHE_T"
expect_allowed "(k) by-value uintptr_t parameter with a pointer return is allowed" "$R:wc_byval_ok" "$R:wc_same"

# (l) the entry for wc_same in sprout_runtime.c does not exempt wc_same in sprout_scheduler.c.
expect_fail "(l) same-named static in another file" "$S:wc_same has 1 pointer-to-integer cast(s) outside the exits and ADDRESS_CASTS"
expect_absent "(l) the listed file's static is not named" "$R:wc_same has"

# (m) a runtime/*.c the gate did not list before is scanned.
expect_fail "(m) planted cast in a new runtime file" "$N:wc_new_fn has 1 pointer-to-integer cast(s) outside the exits and ADDRESS_CASTS"

# (n) rule A2: a cast result that leaves a listed function through a field store, a call
# argument or an intptr_t value converted to long long; rule A1: a cast to unsigned long.
lists "$R:wc_a2_field" "$R:wc_a2_call" "$R:wc_a2_intptr" "$R:wc_a1_ulong" "$R:wc_byval_ok" "$R:wc_same"
run "$RT_T" "$TMPD/addr_case" "$CACHE_T"
expect_fail "(n) A2 field store" "ADDRESS_CASTS: $R:wc_a2_field converts uintptr_t to long long"
expect_fail "(n) A2 call argument" "ADDRESS_CASTS: $R:wc_a2_call converts uintptr_t to long long"
expect_fail "(n) A2 intptr_t operand" "ADDRESS_CASTS: $R:wc_a2_intptr converts intptr_t to long long"
expect_fail "(n) A1 cast to unsigned long" "ADDRESS_CASTS: $R:wc_a1_ulong casts a pointer to unsigned long"
expect_absent "(n) A1 is not reported for a uintptr_t cast" "$R:wc_a2_intptr casts a pointer"

# (o) a uintptr_t converted to size_t or to int is allowed in a listed function.
lists "$R:wc_ok_size" "$R:wc_ok_narrow" "$R:wc_byval_ok" "$R:wc_same"
run "$RT_T" "$TMPD/addr_case" "$CACHE_T"
expect_allowed "(o) uintptr_t to size_t and to int are allowed" "$R:wc_ok_size" "$R:wc_ok_narrow"

# (p) outside a listed function a uintptr_t local converted to long long is a counted site, and
# the cast that feeds a conversion is not counted twice.
expect_fail "(p) implicit uintptr_t to long long outside a listed function" "$R:wc_a2_implicit has 1 pointer-to-integer cast(s) outside the exits and ADDRESS_CASTS"
expect_fail "(p) a cast and the conversion it feeds count once" "$R:wc_a2_field has 1 pointer-to-integer cast(s)"

# (q) the exits are keyed by file: a sprout_ptr_word outside runtime/sprout_word.h is not exempt.
expect_fail "(q) sprout_ptr_word in another file" "$Q:sprout_ptr_word has 1 pointer-to-integer cast(s) outside the exits and ADDRESS_CASTS"
expect_absent "(q) the exits in runtime/sprout_word.h are exempt" "runtime/sprout_word.h"

# (r)-(s) rules 2 and 3 (docs/string-header-invariant-v0.md §4.3), planted in a fourth tree. None
# of these adds a pointer-to-integer cast to its own body, so rule 1 stays quiet.
RT_R="$TMPD/rt_rules"
cp -R "$RT" "$RT_R"
cat >>"$RT_R/sprout_runtime.c" <<'EOF'
long long wc_r2_literal(void) { return sprout_ptr_word("x"); }
long long wc_r2_voidcast(void) { return sprout_ptr_word((void*)"x"); }
long long wc_r2_hdr_p(SproutStr s) { return sprout_ptr_word(s.hdr_p); }
long long wc_r2_uchar(unsigned char* p) { return sprout_ptr_word((const void*)(void*)p); }
long long wc_r2_ok(long long* p) { return sprout_ptr_word(p); }
long long wc_r2_ok_void(void* p) { return sprout_ptr_word(p); }
SproutStr wc_r3_init(char* p) { SproutStr s = { p }; return s; }
SproutStr wc_r3_zero(void) { SproutStr s = {0}; return s; }
SproutStr wc_r3_designated(char* p) { SproutStr s = { .hdr_p = p }; return s; }
SproutStr wc_r3_literal(char* p) { return (SproutStr){ p }; }
void wc_r3_assign(SproutStr s, char* p) { s.hdr_p = p; }
void wc_r3_assign_arrow(SproutStr* s, char* p) { s->hdr_p = p; }
void wc_r3_addr(SproutStr s) { char** pp = &s.hdr_p; (void)pp; }
void wc_r3_incr(SproutStr s) { s.hdr_p++; }
void wc_r3_compound(SproutStr s) { s.hdr_p += 1; }
void wc_r3_ok_fill(SproutStr s) { s.hdr_p[0] = 'a'; memcpy(s.hdr_p, "a", 1); (void)strlen(s.hdr_p); }
char* wc_r3_ok_read(SproutStr* s) { return s->hdr_p; }
char* wc_r3_ok_call(void) { return sprout_str_alloc(1, "wc").hdr_p; }
SproutStr wc_r3_ok_writer(size_t n) { return sprout_str_alloc(n, "wc"); }
typedef SproutStr WcAlias;
typedef struct { char* hdr_p; } SproutStrX;
WcAlias wc_r3_alias_init(char* p) { WcAlias s = { p }; return s; }
void wc_r3_alias_assign(WcAlias s, char* p) { s.hdr_p = p; }
void wc_r3_alias_arrow(WcAlias* s, char* p) { s->hdr_p = p; }
void wc_r3_ok_other(SproutStrX* s, char* p) { SproutStrX t = { p }; t.hdr_p = p; s->hdr_p = p; }
char* wc_r3p_deref(char* raw) { SproutStr s = *(SproutStr*)&raw; return s.hdr_p; }
SproutStr* wc_r3p_to_str(char** p) { return (SproutStr*)p; }
void wc_r3p_from_str(SproutStr s, char* raw) { *(char**)&s = raw; }
WcAlias* wc_r3p_alias(char** p) { return (WcAlias*)p; }
void wc_r3p_ok_malloc(void) { SproutStr* a = malloc(4 * sizeof *a); free(a); }
void wc_r3p_ok_cast_malloc(void) { SproutStr* a = (SproutStr*)malloc(4 * sizeof *a); free(a); }
void wc_r3p_ok_memcpy(SproutStr x, SproutStr y) { memcpy(&x, &y, sizeof x); }
const SproutStr* wc_r3p_ok_const(SproutStr* s) { return s; }
void wc_r3p_ok_other(char** p) { SproutStrX* q = (SproutStrX*)p; (void)q; }
EOF
cat >>"$RT_R/sprout_scheduler.c" <<'EOF'
static SproutStr sprout_str_intern(const char* s) { SproutStr r = { (char*)s }; return r; }
EOF
run "$RT_R" "$ADDR" "$TMPD/cache_rules.json"
for f in wc_r2_literal wc_r2_voidcast wc_r2_hdr_p wc_r2_uchar; do
  expect_fail "(r) rule 2: $f passes a char pointer to sprout_ptr_word" "$R:$f passes a char pointer to sprout_ptr_word (rule 2"
done
for f in wc_r3_init wc_r3_zero wc_r3_designated wc_r3_literal; do
  expect_fail "(s) rule 3: $f constructs a SproutStr" "$R:$f constructs a SproutStr outside the writers (rule 3"
done
expect_fail "(s) rule 3: a typedef alias is still a SproutStr" "$R:wc_r3_alias_init constructs a SproutStr outside the writers (rule 3"
for f in wc_r3_alias_assign wc_r3_alias_arrow; do
  expect_fail "(s) rule 3: $f writes hdr_p through an alias" "$R:$f writes SproutStr.hdr_p outside the writers (rule 3"
done
for f in wc_r3_assign wc_r3_assign_arrow wc_r3_addr wc_r3_incr wc_r3_compound; do
  expect_fail "(s) rule 3: $f writes hdr_p" "$R:$f writes SproutStr.hdr_p outside the writers (rule 3"
done
expect_fail "(s) rule 3: the writer set is keyed by file" "$S:sprout_str_intern constructs a SproutStr outside the writers (rule 3"
for f in wc_r3p_deref wc_r3p_to_str wc_r3p_from_str wc_r3p_alias; do
  expect_fail "(s) rule 3: $f puns a SproutStr pointer" "$R:$f casts between a SproutStr pointer and another pointer type outside the writers (rule 3"
done
for k in "$R:wc_r2_ok" "$R:wc_r2_ok_void" "$R:wc_r3_ok_fill" "$R:wc_r3_ok_read" "$R:wc_r3_ok_call" \
  "$R:wc_r3_ok_writer" "$R:wc_r3_ok_other" "$R:wc_r3p_ok_malloc" "$R:wc_r3p_ok_cast_malloc" "$R:wc_r3p_ok_memcpy" \
  "$R:wc_r3p_ok_const" "$R:wc_r3p_ok_other" "$R:sprout_str_alloc" "$R:sprout_str_intern" "runtime/sprout_word.h:sprout_str_of_word_in"; do
  expect_absent "(r) mirror: $k is not reported" "$k "
done

echo "test-runtime-word-casts: ok"
