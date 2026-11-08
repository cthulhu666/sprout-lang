#!/usr/bin/env bash
# Exercise scripts/bench_vm.sh against a stub `gcloud` (no network, no real cloud calls), and the
# parsing helpers of scripts/bench_vm_remote.sh against hand-made `time -v` / alloc-counter text.
# The property under test: the VM is deleted on every exit path, and only ever by its exact name.
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT="$ROOT/scripts/bench_vm.sh"
REMOTE="$ROOT/scripts/bench_vm_remote.sh"
T="$(mktemp -d "${TMPDIR:-/tmp}/sprout_bench_vm_test_XXXXXX")"
mkdir -p "$T/bin" "$T/out"

cleanup() {
  if [ -f "$T/sleep.pid" ]; then kill "$(cat "$T/sleep.pid")" 2>/dev/null || true; fi
  rm -rf "$T"
}
trap cleanup EXIT

FAILS=0
ok() { echo "ok - $1"; }
fail() { echo "FAIL - $1"; FAILS=$((FAILS + 1)); }
check() { # check <name> <command...>: ok when the command succeeds
  local name="$1"; shift
  if "$@" >/dev/null 2>&1; then ok "$name"; else fail "$name"; fi
}

# ── stub gcloud ──────────────────────────────────────────────────────────────
cat > "$T/bin/gcloud" <<'STUB'
#!/usr/bin/env bash
echo "$*" >> "$STUB_LOG"
kind=""
case "$*" in
  "compute instances create "*) kind=create ;;
  "compute instances delete "*) kind=delete ;;
  "compute instances list "*) kind=list ;;
  "compute ssh "*) case "$*" in *bench_vm_remote.sh*) kind=run ;; *) kind=ssh ;; esac ;;
  "compute scp "*) case "$*" in *:results.txt*) kind=scp_back ;; *) kind=scp ;; esac ;;
esac
[ "${STUB_FAIL_AT:-}" = "$kind" ] && exit 1
if [ "$kind" = create ] && [ "${STUB_SLEEP_AT:-}" = create ]; then
  sleep 3
  echo create-done >> "$STUB_LOG"
fi
if [ "$kind" = scp ]; then
  # Record what the base tarball holds, for the archive-contents checks.
  for a in "$@"; do
    case "$a" in *base.tar) tar -tf "$a" > "$STUB_LOG.tar" 2>/dev/null ;; esac
  done
fi
if [ "$kind" = run ] && [ "${STUB_SLEEP_AT:-}" = run ]; then
  echo $$ > "$STUB_DIR/sleep.pid"
  exec sleep 30
fi
if [ "$kind" = list ] && [ "${STUB_LEFTOVER:-}" = 1 ]; then
  for a in "$@"; do
    case "$a" in --filter=name=*) echo "${a#--filter=name=}" ;; esac
  done
fi
if [ "$kind" = scp_back ]; then
  # The destination is the positional argument after the remote source.
  take=0
  for a in "$@"; do
    if [ "$take" = 1 ]; then echo "fake results" > "$a"; break; fi
    case "$a" in *:results.txt) take=1 ;; esac
  done
fi
exit 0
STUB
chmod +x "$T/bin/gcloud"

# run_case <case-name> [VAR=value | -VAR]...
# Runs bench_vm.sh HEAD HEAD with the stub first on PATH; `-VAR` unsets VAR.
# Sets CASE_RC, CASE_LOG, CASE_ERR, CASE_OUT.
run_case() {
  local name="$1"; shift
  CASE_LOG="$T/$name.log"; CASE_ERR="$T/$name.err"; CASE_OUT="$T/$name.out"
  : > "$CASE_LOG"
  (
    export PATH="$T/bin:$PATH" STUB_LOG="$CASE_LOG" STUB_DIR="$T"
    export BENCH_PROJECT=test-project BENCH_ACCOUNT=tester@example.invalid BENCH_ZONE=test-zone-a
    export BENCH_SSH_RETRY_SLEEP=0 BENCH_OUT_DIR="$T/out" BENCH_VM_CONFIG="$T/no-such-config.env"
    unset STUB_FAIL_AT STUB_SLEEP_AT STUB_LEFTOVER BENCH_MACHINE BENCH_TTL BENCH_ROUNDS
    for kv in "$@"; do
      # shellcheck disable=SC2163
      case "$kv" in
        -*) unset "${kv#-}" ;;
        *) export "$kv" ;;
      esac
    done
    bash "$SCRIPT" "${CASE_REFS[@]}" > "$CASE_OUT" 2> "$CASE_ERR"
  )
  CASE_RC=$?
}
CASE_REFS=(HEAD HEAD)

calls() { grep -c . "$CASE_LOG"; }
calls_of() { grep -c "^compute $1 " "$CASE_LOG"; }
created_name() { grep '^compute instances create ' "$CASE_LOG" | head -1 | awk '{print $4}'; }

# Every delete names exactly the instance this run created, with no other positional argument;
# nothing lists or deletes by label.
deletes_only_own_name() {
  local n; n="$(created_name)"
  [ -n "$n" ] || return 1
  local line tok count
  while IFS= read -r line; do
    count=0
    for tok in $line; do
      case "$tok" in --*) ;; *) count=$((count + 1)) ;; esac
    done
    [ "$count" -eq 4 ] || return 1
    [ "$(echo "$line" | awk '{print $4}')" = "$n" ] || return 1
  done < <(grep '^compute instances delete ' "$CASE_LOG")
  ! grep -q 'labels\.' "$CASE_LOG"
}
deleted_own_name() { # exactly one delete of the created name
  local n; n="$(created_name)"
  [ -n "$n" ] && [ "$(grep -c "^compute instances delete $n " "$CASE_LOG")" -eq 1 ] \
    && [ "$(calls_of 'instances delete')" -eq 1 ]
}
every_call_scoped() {
  [ "$(calls)" -gt 0 ] \
    && ! grep -v -e '--project=test-project' "$CASE_LOG" | grep -q . \
    && ! grep -v -e '--account=tester@example.invalid' "$CASE_LOG" | grep -q .
}

# ── 1. all three vars missing ────────────────────────────────────────────────
run_case missing -BENCH_PROJECT -BENCH_ACCOUNT -BENCH_ZONE
{ [ "$CASE_RC" -eq 2 ] && [ "$(calls)" -eq 0 ] \
  && grep -q 'BENCH_PROJECT' "$CASE_ERR" && grep -q 'BENCH_ACCOUNT' "$CASE_ERR" \
  && grep -q 'BENCH_ZONE' "$CASE_ERR" && grep -q 'no-such-config.env' "$CASE_ERR"; } \
  && ok "missing config: exit 2, no gcloud call, names vars and path" \
  || fail "missing config: exit 2, no gcloud call, names vars and path (rc=$CASE_RC)"

# ── 2. vars from a config file only ──────────────────────────────────────────
cat > "$T/file.env" <<'EOF'
BENCH_PROJECT=file-project
BENCH_ACCOUNT=filer@example.invalid
BENCH_ZONE=file-zone-a
EOF
run_case fileonly -BENCH_PROJECT -BENCH_ACCOUNT -BENCH_ZONE "BENCH_VM_CONFIG=$T/file.env"
{ [ "$CASE_RC" -eq 0 ] && grep -q '^compute instances create ' "$CASE_LOG" \
  && grep -q -e '--project=file-project' "$CASE_LOG" \
  && grep -q -e '--account=filer@example.invalid' "$CASE_LOG" \
  && grep -q -e '--zone=file-zone-a' "$CASE_LOG"; } \
  && ok "config file supplies the three vars" \
  || fail "config file supplies the three vars (rc=$CASE_RC)"

# ── 3. env overrides the file ────────────────────────────────────────────────
run_case envwins -BENCH_ACCOUNT -BENCH_ZONE BENCH_PROJECT=env-project "BENCH_VM_CONFIG=$T/file.env"
{ [ "$CASE_RC" -eq 0 ] && grep -q -e '--project=env-project' "$CASE_LOG" \
  && ! grep -q -e '--project=file-project' "$CASE_LOG" \
  && grep -q -e '--account=filer@example.invalid' "$CASE_LOG"; } \
  && ok "env value wins over config file" \
  || fail "env value wins over config file (rc=$CASE_RC)"

# ── 4. happy path ────────────────────────────────────────────────────────────
run_case happy
NAME="$(created_name)"
HEAD_SHA="$(git -C "$ROOT" rev-parse --verify 'HEAD^{commit}')"
create_line="$(grep '^compute instances create ' "$CASE_LOG" | head -1)"
has() { case "$create_line" in *"$1"*) return 0 ;; *) return 1 ;; esac; }
[ "$CASE_RC" -eq 0 ] && ok "happy: exit 0" || fail "happy: exit 0 (rc=$CASE_RC)"
{ has --max-run-duration= && has --instance-termination-action=DELETE \
  && has --labels=purpose=sprout-bench && has --metadata=block-project-ssh-keys=true \
  && has --no-service-account && has --no-scopes && has --machine-type=c4a-standard-2 \
  && has --zone=test-zone-a; } \
  && ok "happy: create carries the safety flags" || fail "happy: create carries the safety flags"
check "happy: exactly one delete, of the created name" deleted_own_name
check "happy: delete names nothing else, no label filter" deletes_only_own_name
check "happy: every call carries --project and --account" every_call_scoped
check "happy: delete is zoned" grep -q '^compute instances delete .* --zone=test-zone-a' "$CASE_LOG"
check "happy: no call mentions config" bash -c '[ -s "$1" ] && ! grep -qw config "$1"' _ "$CASE_LOG"
check "happy: name shape" bash -c '[[ "$1" =~ ^sprout-bench-[0-9]{8}-[0-9]{6}-[0-9a-f]{4}$ ]] && [ ${#1} -le 63 ]' _ "$NAME"
check "happy: results land under the out dir" test -s "$T/out/$NAME/results.txt"
check "happy: results printed" grep -q 'fake results' "$CASE_OUT"
check "happy: run call has rounds and both SHAs" \
  grep -q "BENCH_ROUNDS=9 bash bench_vm_remote.sh base.tar head.tar $HEAD_SHA $HEAD_SHA" "$CASE_LOG"
check "happy: leftover check ran" grep -q '^compute instances list .*--filter=name='"$NAME" "$CASE_LOG"
check "happy: default ttl scales with rounds (38m for 9)" has --max-run-duration=38m
check "happy: tarball holds seed, runtime, stdlib and the clang check" \
  bash -c 'for p in bootstrap/compile_driver.ll runtime/ stdlib/ scripts/check_clang_cpr.sh; do grep -qx "$p" "$1" || exit 1; done' _ "$CASE_LOG.tar"
check "happy: tarball has no docs/ entry" bash -c '[ -s "$1" ] && ! grep -q "^docs/" "$1"' _ "$CASE_LOG.tar"

# ── TTL, rounds and config-file knobs ────────────────────────────────────────
run_case ttl15 BENCH_ROUNDS=15
create_line="$(grep '^compute instances create ' "$CASE_LOG" | head -1)"
{ [ "$CASE_RC" -eq 0 ] && has --max-run-duration=50m; } \
  && ok "ttl: 15 rounds gives 50m" || fail "ttl: 15 rounds gives 50m (rc=$CASE_RC)"
run_case ttlexplicit BENCH_ROUNDS=15 BENCH_TTL=70m
create_line="$(grep '^compute instances create ' "$CASE_LOG" | head -1)"
{ [ "$CASE_RC" -eq 0 ] && has --max-run-duration=70m; } \
  && ok "ttl: explicit BENCH_TTL wins" || fail "ttl: explicit BENCH_TTL wins (rc=$CASE_RC)"

for bad in 0 abc 1x -3; do
  run_case "rounds_bad_$bad" "BENCH_ROUNDS=$bad"
  { [ "$CASE_RC" -eq 2 ] && [ "$(calls)" -eq 0 ] && grep -q 'BENCH_ROUNDS' "$CASE_ERR"; } \
    && ok "BENCH_ROUNDS='$bad': exit 2, no gcloud call" \
    || fail "BENCH_ROUNDS='$bad': exit 2, no gcloud call (rc=$CASE_RC)"
done

cat > "$T/all.env" <<EOF
BENCH_PROJECT=file-project
BENCH_ACCOUNT=filer@example.invalid
BENCH_ZONE=file-zone-a
BENCH_OUT_DIR="$T/cfgout"
BENCH_TTL=7m
BENCH_ROUNDS=3
EOF
run_case cfgall -BENCH_PROJECT -BENCH_ACCOUNT -BENCH_ZONE -BENCH_OUT_DIR "BENCH_VM_CONFIG=$T/all.env"
NAME="$(created_name)"
create_line="$(grep '^compute instances create ' "$CASE_LOG" | head -1)"
{ [ "$CASE_RC" -eq 0 ] && [ -s "$T/cfgout/$NAME/results.txt" ] && [ ! -e "$T/out/$NAME" ] \
  && has --max-run-duration=7m && grep -q 'BENCH_ROUNDS=3 bash' "$CASE_LOG"; } \
  && ok "config file sets BENCH_OUT_DIR, BENCH_TTL and BENCH_ROUNDS" \
  || fail "config file sets BENCH_OUT_DIR, BENCH_TTL and BENCH_ROUNDS (rc=$CASE_RC)"
run_case cfgenv -BENCH_PROJECT -BENCH_ACCOUNT -BENCH_ZONE -BENCH_OUT_DIR BENCH_TTL=9m "BENCH_VM_CONFIG=$T/all.env"
create_line="$(grep '^compute instances create ' "$CASE_LOG" | head -1)"
{ [ "$CASE_RC" -eq 0 ] && has --max-run-duration=9m; } \
  && ok "env BENCH_TTL wins over config file" || fail "env BENCH_TTL wins over config file (rc=$CASE_RC)"

# ── 5-7. failures at create / ssh readiness / run ────────────────────────────
for at in create ssh run; do
  run_case "fail_$at" "STUB_FAIL_AT=$at"
  { [ "$CASE_RC" -ne 0 ] && deleted_own_name && deletes_only_own_name; } \
    && ok "fail at $at: nonzero exit, VM deleted by its own name" \
    || fail "fail at $at: nonzero exit, VM deleted by its own name (rc=$CASE_RC)"
done
run_case fail_scp STUB_FAIL_AT=scp
{ [ "$CASE_RC" -ne 0 ] && deleted_own_name && deletes_only_own_name; } \
  && ok "fail at scp: nonzero exit, VM deleted by its own name" \
  || fail "fail at scp: nonzero exit, VM deleted by its own name (rc=$CASE_RC)"
run_case fail_scp_back STUB_FAIL_AT=scp_back
{ [ "$CASE_RC" -ne 0 ] && deleted_own_name && deletes_only_own_name; } \
  && ok "fail at scp_back: nonzero exit, VM deleted by its own name" \
  || fail "fail at scp_back: nonzero exit, VM deleted by its own name (rc=$CASE_RC)"
run_case fail_list STUB_FAIL_AT=list
{ [ "$CASE_RC" -eq 1 ] && deleted_own_name && deletes_only_own_name \
  && grep -q 'could not confirm' "$CASE_ERR"; } \
  && ok "list fails after a good run: exit 1, warning on stderr" \
  || fail "list fails after a good run: exit 1, warning on stderr (rc=$CASE_RC)"

# ── 8. signals during the remote run, and during create ─────────────────────
# `set -m` gives the background job its default signal dispositions; without it a
# non-interactive shell starts async jobs with SIGINT ignored, which cannot be trapped.
create_done_before_delete() {
  local c d
  c="$(grep -n '^create-done$' "$CASE_LOG" | head -1 | cut -d: -f1)"
  d="$(grep -n '^compute instances delete ' "$CASE_LOG" | head -1 | cut -d: -f1)"
  [ -n "$c" ] && [ -n "$d" ] && [ "$c" -lt "$d" ]
}
signal_case() { # <sig> <expected-rc> [run|create]
  local sig="$1" want="$2" stage="${3:-run}"
  local name="signal_${sig}_${stage}" marker=bench_vm_remote.sh
  [ "$stage" = create ] && marker='compute instances create'
  CASE_LOG="$T/$name.log"; CASE_ERR="$T/$name.err"; CASE_OUT="$T/$name.out"
  : > "$CASE_LOG"; rm -f "$T/sleep.pid"
  set -m
  (
    export PATH="$T/bin:$PATH" STUB_LOG="$CASE_LOG" STUB_DIR="$T" STUB_SLEEP_AT="$stage"
    export BENCH_PROJECT=test-project BENCH_ACCOUNT=tester@example.invalid BENCH_ZONE=test-zone-a
    export BENCH_SSH_RETRY_SLEEP=0 BENCH_OUT_DIR="$T/out" BENCH_VM_CONFIG="$T/no-such-config.env"
    exec bash "$SCRIPT" HEAD HEAD > "$CASE_OUT" 2> "$CASE_ERR"
  ) &
  local pid=$! i=0
  set +m
  while ! grep -q "$marker" "$CASE_LOG" && [ $i -lt 150 ]; do sleep 0.2; i=$((i + 1)); done
  sleep 0.3
  local t0=$SECONDS
  kill "-$sig" "$pid" 2>/dev/null
  wait "$pid"; CASE_RC=$?
  # The run stub sleeps 30s: cleanup must not wait for the running gcloud to finish.
  # The create stub sleeps 3s: cleanup must wait for it to finish, then delete.
  local took=$((SECONDS - t0))
  if [ -f "$T/sleep.pid" ]; then kill "$(cat "$T/sleep.pid")" 2>/dev/null || true; fi
  local extra=true
  if [ "$stage" = create ]; then
    extra='create_done_before_delete && grep -q "waiting for create to finish before deleting" "$CASE_ERR"'
  fi
  { [ "$CASE_RC" -eq "$want" ] && [ "$took" -lt 10 ] && deleted_own_name && deletes_only_own_name \
    && eval "$extra"; } \
    && ok "SIG$sig during $stage: exit $want, VM deleted" \
    || fail "SIG$sig during $stage: exit $want, VM deleted (rc=$CASE_RC)"
}
signal_case INT 130
signal_case TERM 143
signal_case INT 130 create

# ── 9. leftover VM ───────────────────────────────────────────────────────────
run_case leftover STUB_LEFTOVER=1
NAME="$(created_name)"
{ [ "$CASE_RC" -eq 1 ] && grep -q "$NAME" "$CASE_ERR" && deleted_own_name; } \
  && ok "leftover VM: exit 1, stderr names it" \
  || fail "leftover VM: exit 1, stderr names it (rc=$CASE_RC)"

# ── 11. bad ref ──────────────────────────────────────────────────────────────
CASE_REFS=(HEAD no-such-ref-xyz)
run_case badref
{ [ "$CASE_RC" -ne 0 ] && [ "$CASE_RC" -ne 127 ] && [ "$(calls)" -eq 0 ] \
  && grep -q 'no-such-ref-xyz' "$CASE_ERR"; } \
  && ok "bad ref: nonzero exit, zero gcloud calls" \
  || fail "bad ref: nonzero exit, zero gcloud calls (rc=$CASE_RC)"

# The repo's first commit predates the seed: a ref without a shipped path fails before any gcloud call.
ROOT_SHA="$(git -C "$ROOT" rev-list --max-parents=0 HEAD | tail -n1)"
if ! git -C "$ROOT" cat-file -e "$ROOT_SHA:bootstrap/compile_driver.ll" 2>/dev/null; then
  CASE_REFS=("$ROOT_SHA" HEAD)
  run_case missingpath
  { [ "$CASE_RC" -ne 0 ] && [ "$CASE_RC" -ne 127 ] && [ "$(calls)" -eq 0 ] \
    && grep -q 'bootstrap/compile_driver.ll' "$CASE_ERR"; } \
    && ok "ref lacking a shipped path: nonzero exit, names the path, zero gcloud calls" \
    || fail "ref lacking a shipped path: nonzero exit, names the path, zero gcloud calls (rc=$CASE_RC)"
fi
CASE_REFS=(HEAD HEAD)

# ── 10. across all cases: nothing deletes anything but its own run's name ────
bad=0; creates=0
for f in "$T"/*.log; do
  CASE_LOG="$f"
  grep -q '^compute instances create ' "$f" || { grep -q '^compute instances delete ' "$f" && bad=1; continue; }
  creates=$((creates + 1))
  deletes_only_own_name || bad=1
  grep -q '^compute instances list .*labels' "$f" && bad=1
done
[ "$bad" -eq 0 ] && [ "$creates" -ge 5 ] && ok "all cases: deletes name only the run's own instance, no label filters" \
  || fail "all cases: deletes name only the run's own instance, no label filters"

# ── 12. parsing helpers of the remote script ─────────────────────────────────
cat > "$T/time1.txt" <<'EOF'
	Command being timed: "./A --emit-ir base/stdlib base/stdlib/compiler/compile_driver.sprout"
	User time (seconds): 11.20
	System time (seconds): 0.80
	Percent of CPU this job got: 99%
	Elapsed (wall clock) time (h:mm:ss or m:ss): 0:12.34
	Maximum resident set size (kbytes): 2097152
	Exit status: 0
EOF
sed 's/0:12.34/1:02:03.50/; s/2097152/1048576/' "$T/time1.txt" > "$T/time2.txt"
cat > "$T/alloc.txt" <<'EOF'
some compiler diagnostic
[sprout alloc] sprout_obj=10 closure=2 vector=3 map=4 bytes=500 builder=0 arena_bytes=600 offarena_bytes=7 intern=8 intern_bytes=9 gc_swept=1 gc_cycles=2 gc_walked=3 closure_apply=4
EOF
sed 's/sprout_obj=10/sprout_obj=12/' "$T/alloc.txt" > "$T/alloc2.txt"
lib() { BENCH_REMOTE_LIB_ONLY=1 bash -c '. "$1"; shift; "$@"' _ "$REMOTE" "$@"; }
[ "$(lib time_wall_secs "$T/time1.txt")" = "12.34" ] && ok "parse: wall m:ss.cc" || fail "parse: wall m:ss.cc"
[ "$(lib time_wall_secs "$T/time2.txt")" = "3723.50" ] && ok "parse: wall h:mm:ss.cc" || fail "parse: wall h:mm:ss.cc"
[ "$(lib time_rss_kb "$T/time1.txt")" = "2097152" ] && ok "parse: max RSS" || fail "parse: max RSS"
[ "$(printf '3.0\n1.0\n2.0\n' | lib stat_median)" = "2.00" ] && ok "parse: median odd" || fail "parse: median odd"
[ "$(printf '4.0\n1.0\n2.0\n3.0\n' | lib stat_median)" = "2.50" ] && ok "parse: median even" || fail "parse: median even"
[ "$(printf '3.0\n1.0\n2.0\n' | lib stat_min)" = "1.00" ] && ok "parse: min" || fail "parse: min"
[ "$(printf '3.0\n1.0\n2.0\n' | lib stat_max)" = "3.00" ] && ok "parse: max" || fail "parse: max"
tbl="$(lib alloc_table "$T/alloc.txt" "$T/alloc2.txt")"
{ echo "$tbl" | grep -E '^sprout_obj +10 +12$' >/dev/null && echo "$tbl" | grep -E '^closure_apply +4 +4$' >/dev/null; } \
  && ok "parse: alloc counter table" || fail "parse: alloc counter table"
: > "$T/empty.txt"
tbl="$(lib alloc_table "$T/empty.txt" "$T/alloc2.txt")"
{ echo "$tbl" | grep -E '^sprout_obj +12$' >/dev/null && echo "$tbl" | grep -E '^closure_apply +4$' >/dev/null; } \
  && ok "parse: alloc table with an empty A file keeps B in column B" \
  || fail "parse: alloc table with an empty A file keeps B in column B"
tbl="$(lib alloc_table "$T/alloc.txt" "$T/empty.txt")"
echo "$tbl" | grep -E '^sprout_obj +10 *$' >/dev/null \
  && ok "parse: alloc table with an empty B file leaves column B blank" \
  || fail "parse: alloc table with an empty B file leaves column B blank"

# ── scripts parse and lint cleanly ───────────────────────────────────────────
check "bash -n bench_vm.sh" bash -n "$SCRIPT"
check "bash -n bench_vm_remote.sh" bash -n "$REMOTE"

echo
if [ "$FAILS" -ne 0 ]; then echo "$FAILS check(s) failed"; exit 1; fi
echo "all bench-vm checks passed"
