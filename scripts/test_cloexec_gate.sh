#!/usr/bin/env bash
# cloexec_gate.sh against copies of runtime/ with one defect planted at a time, and against
# lookalikes that must pass. Case (a) is the gate on the real tree.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GATE="$ROOT/scripts/cloexec_gate.sh"
TMPD="$(mktemp -d "${TMPDIR:-/tmp}/sprout_cloexec_test_XXXXXX")"
trap 'rm -rf "$TMPD"' EXIT

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

# run <runtime-dir>: output in $OUT, exit code in $RC.
run() {
  RC=0
  OUT="$(bash "$GATE" "$1" 2>&1)" || RC=$?
}

# fresh <name>: a pristine copy of runtime/ at $TMPD/<name>.
fresh() {
  rm -rf "${TMPD:?}/$1"
  cp -R "$ROOT/runtime" "$TMPD/$1"
}

# expect_fail <case> <needle>: the last run exited 1 and printed <needle>.
expect_fail() {
  [[ "$RC" == 1 ]] || fail "$1: exit $RC, want 1: $OUT"
  grep -qF -- "$2" <<<"$OUT" || fail "$1: output lacks '$2': $OUT"
  echo "ok: $1"
}

# (a) the real tree passes.
run "$ROOT/runtime"
[[ "$RC" == 0 ]] || fail "(a) real tree: exit $RC: $OUT"
echo "ok: (a) real tree passes"

# (b)-(j) one raw call each, appended to a copy. (m)-(o) at the end use the same helper.
planted() {  # <case> <file> <line> <needle>
  fresh case
  printf '%s\n' "$3" >>"$TMPD/case/$2"
  run "$TMPD/case"
  expect_fail "$1" "$4"
}
planted "(b) raw socket" sprout_runtime.c 'int planted_a(void) { return socket(AF_INET, SOCK_STREAM, 0); }' 'planted_a'
planted "(c) epoll_create1(0)" sprout_poll.c 'int planted_b(void) { return epoll_create1(0); }' 'planted_b'
planted "(d) timerfd_create without TFD_CLOEXEC" sprout_poll.c 'int planted_c(void) { return timerfd_create(CLOCK_MONOTONIC, 0); }' 'planted_c'
planted "(e) raw fopen" sprout_runtime.c 'FILE* planted_d(const char* p) { return fopen(p, "r"); }' 'planted_d'
planted "(f) raw pipe" sprout_runtime.c 'int planted_e(int* f) { return pipe(f); }' 'planted_e'
planted "(g) raw accept" sprout_runtime.c 'int planted_f(int l) { return accept(l, NULL, NULL); }' 'planted_f'
planted "(h) raw opendir" sprout_runtime.c 'void* planted_g(const char* p) { return opendir(p); }' 'planted_g'
planted "(i) raw open with a string path" sprout_runtime.c 'int planted_h(void) { return open("/dev/null", 0); }' 'planted_h'
planted "(j) raw dup2 without the marker" sprout_runtime.c 'void planted_i(int a) { dup2(a, 1); }' 'planted_i'

# (k) lookalikes that must pass: prose, strings, freopen, fdopen, wrapper names, the fork-child
# marker, and a call inside the wrapper markers.
fresh ok
cat >>"$TMPD/ok/sprout_runtime.c" <<'EOF'
/* accept(2) and socket(AF_INET, ...) in prose */
// pipe(fds) in a line comment
const char* ok_a = "open(path) and fopen(p, \"r\")";
FILE* ok_b(const char* p) { return freopen(p, "a", stderr); }
FILE* ok_c(int fd) { return fdopen(fd, "r"); }
int ok_d(void) { return cloexec_socket(AF_INET, SOCK_STREAM, 0); }
void ok_e(int a) { dup2(a, 1); }   /* cloexec-gate: fork child */
/* cloexec-gate: begin wrappers */
int ok_f(void) { return socket(AF_INET, SOCK_STREAM, 0); }
/* cloexec-gate: end wrappers */
EOF
run "$TMPD/ok"
[[ "$RC" == 0 ]] || fail "(k) lookalikes: exit $RC: $OUT"
echo "ok: (k) lookalikes pass"

# (l) the wrapper region ends at its end marker: a raw call after it is caught.
fresh after
cat >>"$TMPD/after/sprout_runtime.c" <<'EOF'
/* cloexec-gate: begin wrappers */
int after_a(void) { return socket(AF_INET, SOCK_STREAM, 0); }
/* cloexec-gate: end wrappers */
int after_b(void) { return socket(AF_INET, SOCK_STREAM, 0); }
EOF
run "$TMPD/after"
expect_fail "(l) raw call after the wrapper region" 'after_b'
if grep -qF 'after_a' <<<"$OUT"; then fail "(l) call inside the wrapper region was reported: $OUT"; fi

# (m)-(o) the first argument is not a plain name: a cast, a dereference, or on the next line.
planted "(m) raw fopen with a cast first argument" sprout_runtime.c 'FILE* planted_j(long long p) { return fopen((const char*)(uintptr_t)p, "rb"); }' 'planted_j'
planted "(n) raw open with a dereference first argument" sprout_runtime.c 'int planted_k(const char** p) { return open(*p, 0); }' 'planted_k'
planted "(o) raw socket with the first argument on the next line" sprout_runtime.c 'int planted_l(void) { return socket(
    AF_INET, SOCK_STREAM, 0); }' 'planted_l'

echo "test-cloexec-gate: ok"
