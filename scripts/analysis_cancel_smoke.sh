#!/usr/bin/env bash
# Gate: how the analysis service behaves when its stdin ends.
#
# The REPL starts the service in its own process group, so a terminal Ctrl-C reaches only the REPL
# and the service has to notice by itself that its stdin has closed. The rules checked here:
#
#   - Every request that was read gets a reply, in order, even if stdin ended before the service
#     got to it (`sproutd --analysis-service stdlib < requests.jsonl` is a batch run).
#   - After stdin ends no child process (clang, the eval binary) starts, and one that is running
#     is killed. An eval that was not answered before then gets an error reply ("input closed").
#   - The service exits, and TMPDIR holds no scratch dir and no eval binary is left.
#
# Requests are answered on the service's main task, whose root pool is larger than a forked
# task's: one case sends a source big enough to need it.
#
# Each case runs `sproutd --analysis-service` with a private TMPDIR. Nothing under `just test`
# starts the service as a process, hence a script.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SPROUTD="${SPROUTD:-$ROOT/build/sproutd}"
STDLIB="$ROOT/stdlib"
EXIT_WAIT=30     # seconds the service gets to exit after stdin closes
START_WAIT=180   # seconds the first eval gets to compile the runtime and start
fail=0

if [ ! -x "$SPROUTD" ]; then
  echo "ERROR: $SPROUTD not found; run: just build-sproutd" >&2
  exit 1
fi

WORK="$(mktemp -d)"
pid=""
cleanup() {
  [ -n "$pid" ] && kill "$pid" 2>/dev/null
  pkill -f "$WORK/.*/eval_bin" 2>/dev/null
  rm -rf "$WORK"
}
trap cleanup EXIT

check() { # label, status (0 = pass)
  if [ "$2" -eq 0 ]; then echo "PASS $1"; else echo "FAIL $1"; fail=1; fi
}

spin_request='{"op":"eval_expr_in_source","module_source":"fn spin(n: Int) -> Int = spin(n + 1)\n","expr":"spin(0)"}'
sum_request='{"op":"eval_expr_in_source","module_source":"","expr":"1 + 1"}'
instances_request='{"op":"instances_in_source","module_source":"","query":"Int"}'
names_request='{"op":"symbol_locations_in_source","module_source":"fn a() -> Int = 1\n"}'
three_requests="$names_request
$names_request
$names_request"
# A list literal and a sum of 1000 elements each: the compiler needs a large root pool for them.
big_request='{"op":"diagnostics_in_source","module_source":"fn big_list() -> List Int = ['"$(seq 1 1000 | paste -sd, -)"']\nfn big_sum() -> Int = '"$(seq 1 1000 | paste -sd+ -)"'\n"}'

# Polls `cond` (a command string) once a second for up to $2 seconds.
wait_for() {
  local cond="$1" secs="$2" i=0
  while [ "$i" -lt "$secs" ]; do
    eval "$cond" && return 0
    sleep 1
    i=$((i + 1))
  done
  eval "$cond"
}

service_gone() { ! kill -0 "$pid" 2>/dev/null; }
# The eval binary only: its command line is its path and nothing else. clang and ld, which write
# `-o <scratch>/eval_bin` while they link it, must not count, or a case could close stdin during the
# link and never test that a running eval binary is killed.
eval_bin_pattern() { echo "^$TMP/[^ ]*/eval_bin *\$"; }
eval_started() { pgrep -f "$(eval_bin_pattern)" > /dev/null; }
eval_gone() { ! eval_started; }
no_scratch() { [ -z "$(ls -A "$TMP")" ]; }
reply_count() { [ -f "$OUT" ] && grep -c '"ok":' "$OUT"; }

# Starts the service with `$3` as its input. `$2` is how stdin is given:
#   hold      a pipe that stays open until `close_stdin`, as the REPL's is
#   pipe      a pipe that closes after the last line
#   pipe_nonl a pipe that closes after the last line, which has no newline
#   file      a regular file
# Sets pid, TMP, OUT and CASE.
start_service() {
  CASE="$1"
  TMP="$WORK/$CASE/tmp"
  OUT="$WORK/$CASE/out"
  mkdir -p "$TMP"
  local run=(env TMPDIR="$TMP" PATH="${CASE_PATH:-$PATH}" "$SPROUTD" --analysis-service "$STDLIB")
  case "$2" in
    file)
      printf '%s\n' "$3" > "$WORK/$CASE/in"
      "${run[@]}" < "$WORK/$CASE/in" > "$OUT" 2> "$WORK/$CASE/err" &
      ;;
    pipe_nonl)
      printf '%s' "$3" | "${run[@]}" > "$OUT" 2> "$WORK/$CASE/err" &
      ;;
    pipe)
      printf '%s\n' "$3" | "${run[@]}" > "$OUT" 2> "$WORK/$CASE/err" &
      ;;
    hold)
      (
        printf '%s\n' "$3"
        while [ ! -e "$WORK/$CASE/close" ]; do sleep 0.02; done
      ) | "${run[@]}" > "$OUT" 2> "$WORK/$CASE/err" &
      ;;
  esac
  pid=$!
}

# Closes the service's stdin (a `hold` pipe) and returns once it has exited, or the wait is over.
close_stdin() {
  touch "$WORK/$CASE/close"
  wait_for service_gone "$EXIT_WAIT"
}

# The service has exited and left nothing behind.
finish_case() { # label
  close_stdin
  service_gone
  check "$1: the service exits after stdin ends" $?
  if ! service_gone; then
    echo "---- service stderr ----"; cat "$WORK/$CASE/err" 2>/dev/null
    kill "$pid" 2>/dev/null
  fi
  wait_for eval_gone 10
  check "$1: no eval binary is left running" $?
  wait_for no_scratch 5
  check "$1: the scratch dir is gone" $?
  if ! no_scratch; then
    echo "---- left in TMPDIR ----"; ls -R "$TMP"
    echo "---- service stderr ----"; cat "$WORK/$CASE/err" 2>/dev/null
  fi
  pkill -f "$TMP/.*/eval_bin" 2>/dev/null
  wait "$pid" 2>/dev/null
  pid=""
}

# Waits for the service to exit by itself (stdin was closed from the start).
await_exit() {
  wait_for service_gone "$EXIT_WAIT"
}

# 0. The pattern above matches a process run as the eval binary and not one that only has the path
# in its arguments, as clang has. The old pattern, which did not anchor, matches both.
TMP="$WORK/matcher/tmp"
real_path="$TMP/sprout-analysis-x/eval_bin"
# The dummies are `cat` reading a fifo that this script holds open, started under another name.
# Each is stopped by its own PID.
mkdir -p "$WORK/matcher"
mkfifo "$WORK/matcher/hold"
exec 4<> "$WORK/matcher/hold"
(exec -a "clang x.ll -o $real_path" cat <&4 > /dev/null) &
dummy=$!
sleep 0.5
pgrep -f "$TMP/.*/eval_bin" > /dev/null
check "matcher: the old pattern matches a process that only has the path in its arguments" $?
! eval_started
check "matcher: the eval binary pattern does not match it" $?
kill "$dummy" 2>/dev/null
wait "$dummy" 2>/dev/null
(exec -a "$real_path" cat <&4 > /dev/null) &
dummy=$!
sleep 0.5
eval_started
check "matcher: the eval binary pattern matches a process run as the eval binary" $?
kill "$dummy" 2>/dev/null
wait "$dummy" 2>/dev/null
exec 4>&-

# 1. An eval that never ends is running when stdin closes.
start_service spin hold "$spin_request"
wait_for eval_started "$START_WAIT"
check "spin: the eval binary started" $?
finish_case "spin"

# 2. Stdin closes right after the request, before the eval can have started.
start_service early pipe "$spin_request"
await_exit
finish_case "early"

# 3. A normal eval is answered, then the end of stdin ends the service.
start_service plain hold "$sum_request"
wait_for "grep -q '\"ok\":true' '$OUT'" "$START_WAIT"
check "plain: the eval is answered" $?
grep -qx '{"ok":true,"value":\["2"\]}' "$OUT"
check "plain: the answer is the value" $?
finish_case "plain"

# 4. Requests that need no child process are all answered when stdin ends before they run.
start_service batch_file file "$three_requests"
await_exit
[ "$(reply_count)" = 3 ]
check "batch_file: three requests in a file get three replies" $?
finish_case "batch_file"

start_service batch_pipe pipe "$three_requests"
await_exit
[ "$(reply_count)" = 3 ]
check "batch_pipe: three requests in a pipe get three replies" $?
finish_case "batch_pipe"

# 5. An eval needs a child process, so after the end of stdin it is answered with an error.
start_service eval_file file "$sum_request"
await_exit
grep -q '"ok":false' "$OUT" && grep -q 'input closed' "$OUT"
check "eval_file: an eval in a file gets an error reply, not silence" $?
finish_case "eval_file"

start_service eval_nonl pipe_nonl "$sum_request"
await_exit
grep -q '"ok":false' "$OUT" && grep -q 'input closed' "$OUT"
check "eval_nonl: an eval without a final newline gets an error reply" $?
finish_case "eval_nonl"

# 6. An instances request makes a scratch dir but starts no child: it is answered, and the dir
# is removed at exit.
start_service instances_file file "$instances_request"
await_exit
[ "$(reply_count)" = 1 ] && grep -q '"ok":true' "$OUT"
check "instances_file: an instances request in a file is answered" $?
finish_case "instances_file"

# 7. A request that arrives while an eval runs waits, and the replies keep the order of the
# requests.
start_service queued hold "$sum_request
$names_request"
wait_for '[ "$(reply_count)" = 2 ]' "$START_WAIT"
check "queued: both requests are answered" $?
[ "$(head -n 1 "$OUT")" = '{"ok":true,"value":["2"]}' ]
check "queued: the eval is answered first" $?
finish_case "queued"

# 8. Stdin closes while the linker runs. clang writes the eval IR as an object file to a temp file
# in TMPDIR and removes it when it finishes; killed during the link it leaves it there. So TMPDIR
# must be empty at the end (`finish_case` checks it): the service compiles the IR into the scratch
# dir. The linker is a stand-in that signals and then waits, so the link is certain to be running
# when stdin closes. `clang` is a wrapper that hands that linker to every link and leaves a
# compile (`-c`) alone.
real_clang="$(command -v clang)"
mkdir -p "$WORK/stub"
# The stub linker runs under a name of its own, so the case can tell whether it was killed.
stub_ld_pattern="^$WORK/stub/linker-wait( |\$)"
cat > "$WORK/stub/ld" <<EOF
#!/usr/bin/env bash
: > "$WORK/stub/linking"
exec -a "$WORK/stub/linker-wait" sleep 60
EOF
cat > "$WORK/stub/clang" <<EOF
#!/bin/sh
for arg in "\$@"; do
  if [ "\$arg" = "-c" ]; then exec "$real_clang" "\$@"; fi
done
exec "$real_clang" --ld-path="$WORK/stub/ld" "\$@"
EOF
chmod +x "$WORK/stub/ld" "$WORK/stub/clang"
CASE_PATH="$WORK/stub:$PATH" start_service linkcancel hold "$sum_request"
wait_for '[ -e "$WORK/stub/linking" ]' "$START_WAIT"
check "linkcancel: the linker was running when stdin closed" $?
finish_case "linkcancel"
stub_ld_gone() { ! pgrep -f "$stub_ld_pattern" > /dev/null; }
wait_for stub_ld_gone 10
check "linkcancel: the linker was killed with the rest of clang's group" $?
pkill -f "$stub_ld_pattern" 2>/dev/null

# 9. A request that needs a large root pool is answered.
start_service big hold "$big_request"
wait_for "grep -q '\"ok\":true' '$OUT'" 60
check "big: a list literal and a sum of 1000 elements are answered" $?
finish_case "big"

if [ "$fail" -eq 0 ]; then echo "==> analysis cancel smoke: OK"; else echo "==> analysis cancel smoke: FAILED"; fi
exit $fail
