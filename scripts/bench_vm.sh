#!/usr/bin/env bash
# On-demand quiet-machine A/B benchmark of the Sprout compiler on a throwaway GCE VM.
#
#   scripts/bench_vm.sh <base-ref> <head-ref>
#   scripts/bench_vm.sh --gc <ref> "<arms>" [<ab.sh args>...]
#
# Creates one VM, ships the parts of both trees the VM needs (each ref's committed seed, runtime
# and stdlib), runs scripts/bench_vm_remote.sh there, copies the results back, and ALWAYS deletes
# the VM (EXIT/INT/TERM/HUP trap), then checks it is gone. Each ref's committed seed must be
# current: the VM builds each tree's compiler from its own seed.
# --gc runs the ref's bench/gc_minor/ab.sh there with `-a "<arms>"` (e.g.
# "s50:SPROUT_GC_NURSERY_SHARE=0.5,SPROUT_GC_REGION_LISTS=1;rl:SPROUT_GC_REGION_LISTS=1") and the extra
# args, then copies the results directory back and summarizes results.tsv locally.
# Config: BENCH_PROJECT, BENCH_ACCOUNT, BENCH_ZONE (env, or the file named by BENCH_VM_CONFIG,
# default ~/.config/sprout/bench-vm.env). The active gcloud config is never read. See docs/bench-vm.md.
set -euo pipefail

CFG="${BENCH_VM_CONFIG:-$HOME/.config/sprout/bench-vm.env}"
CFG_VARS="BENCH_PROJECT BENCH_ACCOUNT BENCH_ZONE BENCH_MACHINE BENCH_IMAGE_FAMILY BENCH_IMAGE_PROJECT BENCH_TTL BENCH_ROUNDS BENCH_SSH_RETRY_SLEEP BENCH_OUT_DIR"
# What the VM needs from each tree: the seed, the runtime, the stdlib (the timed workload) and the clang check.
ARCHIVE_PATHS=(bootstrap/compile_driver.ll runtime stdlib scripts/check_clang_cpr.sh)
# --gc: the same, plus what ab.sh needs to build and run its workloads (the compiler bundle for
# selfcompile, the sources, the digit data, the testsupport package of compiler_test) and the
# whole-program link script and its compile wrapper. galaxy_client is not shipped: it needs raylib
# and a display.
GC_ARCHIVE_PATHS=("${ARCHIVE_PATHS[@]}" scripts/link_whole_program.sh scripts/compile_native.sh testsupport
  tests/stdlib/test_ir_codegen_match.spr examples/nqueens.sprout examples/astar.sprout
  examples/digit_recognizer bench/gc_minor bench/gc_roots bench/http_log_middleware
  bench/math_transcendental)

usage() {
  echo "usage: scripts/bench_vm.sh <base-ref> <head-ref>" >&2
  echo "       scripts/bench_vm.sh --gc <ref> \"<arms>\" [<ab.sh args>...]" >&2
}

# Rejects, before any gcloud call, what ab.sh would reject (or mishandle) only after the VM is
# built: a malformed -a arm, an unknown option, -o/-B (the output dir is fixed, the VM is fresh),
# a bad -n, a -s outside fixed|default|both|none, and galaxy_client in -w. The -w names are
# collected in GC_WLS; the ref's ab.sh knows the valid ones, so they are checked once it is read.
GC_WLS=""
check_gc_args() { # check_gc_args "<arms>" [ab.sh args...]
  local arms="$1" part label opt wl
  shift
  local -a parts
  IFS=';' read -ra parts <<< "$arms"
  if [ "${#parts[@]}" -eq 0 ]; then echo "bench_vm: --gc needs at least one arm" >&2; exit 2; fi
  for part in "${parts[@]}"; do
    case "$part" in
      *:*) ;;
      *) echo "bench_vm: bad arm '$part': expected <label>:ENV=V[,ENV=V...]" >&2; exit 2 ;;
    esac
    label="${part%%:*}"
    if ! [[ "$label" =~ ^[A-Za-z0-9_.-]+$ ]]; then
      echo "bench_vm: bad arm label '$label': use letters, digits, _ . -" >&2
      exit 2
    fi
  done
  OPTIND=1
  while getopts "n:s:w:e:a:o:B" opt "$@"; do
    case "$opt" in
      n)
        case "$OPTARG" in
          ''|*[!0-9]*|0) echo "bench_vm: -n must be an integer >= 1, got '$OPTARG'" >&2; exit 2 ;;
        esac ;;
      w)
        for wl in ${OPTARG//,/ }; do
          if [ "$wl" = galaxy_client ]; then
            echo "bench_vm: galaxy_client needs a display and raylib; the VM has neither (drop it from -w)" >&2
            exit 2
          fi
          GC_WLS="$GC_WLS $wl"
        done ;;
      s)
        case "$OPTARG" in
          fixed|default|both|none) ;;
          *) echo "bench_vm: bad -s '$OPTARG': expected fixed|default|both|none" >&2; exit 2 ;;
        esac ;;
      a) echo "bench_vm: pass the arms as the argument after <ref>, not with -a" >&2; exit 2 ;;
      o|B) echo "bench_vm: -$opt is fixed on the VM (fresh output dir); results are copied back" >&2; exit 2 ;;
      e) ;;
      *) echo "bench_vm: unknown ab.sh option" >&2; exit 2 ;;
    esac
  done
  if [ "$OPTIND" -le "$#" ]; then
    echo "bench_vm: unexpected ab.sh argument '${!OPTIND}'" >&2
    exit 2
  fi
}

MODE=compiler
AB_ARGS=()
if [ "${1:-}" = "--gc" ]; then
  MODE=gc
  if [ "$#" -lt 3 ]; then usage; exit 2; fi
  GC_REF="$2"
  GC_ARMS="$3"
  shift 3
  AB_ARGS=("$@")
  check_gc_args "$GC_ARMS" ${AB_ARGS[@]+"${AB_ARGS[@]}"}
elif [ "$#" -ne 2 ]; then
  usage
  exit 2
else
  BASE_REF="$1"
  HEAD_REF="$2"
fi

# Env wins; the config file fills in only what the environment leaves empty. The file is sourced
# once, in a subshell that prints each value shell-quoted; the parent evals only those lines.
if [ -f "$CFG" ]; then
  cfg_out="$(
    set +eu
    # shellcheck disable=SC1090
    . "$CFG" >/dev/null 2>&1
    for v in $CFG_VARS; do printf 'CFG_%s=%q\n' "$v" "${!v:-}"; done
  )" || cfg_out=""
  eval "$cfg_out"
  for v in $CFG_VARS; do
    c="CFG_$v"
    if [ -z "${!v:-}" ] && [ -n "${!c:-}" ]; then eval "$v=\${$c}"; fi
  done
fi
missing=""
for v in BENCH_PROJECT BENCH_ACCOUNT BENCH_ZONE; do
  if [ -z "${!v:-}" ]; then missing="$missing $v"; fi
done
if [ -n "$missing" ]; then
  echo "bench_vm: missing config:$missing" >&2
  echo "  set them in the environment or in $CFG (shell syntax, one NAME=value per line)" >&2
  exit 2
fi

BENCH_MACHINE="${BENCH_MACHINE:-c4a-standard-2}"
BENCH_IMAGE_FAMILY="${BENCH_IMAGE_FAMILY:-ubuntu-2604-lts-arm64}"
BENCH_IMAGE_PROJECT="${BENCH_IMAGE_PROJECT:-ubuntu-os-cloud}"
BENCH_ROUNDS="${BENCH_ROUNDS:-9}"
BENCH_SSH_RETRY_SLEEP="${BENCH_SSH_RETRY_SLEEP:-5}"
case "$BENCH_ROUNDS" in
  ''|*[!0-9]*) echo "bench_vm: BENCH_ROUNDS must be an integer >= 1, got '$BENCH_ROUNDS'" >&2; exit 2 ;;
esac
BENCH_ROUNDS=$((10#$BENCH_ROUNDS))
if [ "$BENCH_ROUNDS" -lt 1 ]; then
  echo "bench_vm: BENCH_ROUNDS must be an integer >= 1, got '$BENCH_ROUNDS'" >&2
  exit 2
fi
# The TTL must outlast the run: package install and builds (~20 min) plus the timed rounds. The
# --gc run is arms x workloads x -n runs, which this script cannot price: a fixed 2 hours.
if [ "$MODE" = gc ]; then
  BENCH_TTL="${BENCH_TTL:-120m}"
else
  BENCH_TTL="${BENCH_TTL:-$((20 + 2 * BENCH_ROUNDS))m}"
fi

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
GFLAGS=(--project="$BENCH_PROJECT" --account="$BENCH_ACCOUNT")
ZFLAG=(--zone="$BENCH_ZONE")

# A long gcloud call runs in the background and is waited on: bash runs a trap only after a
# foreground command returns, but `wait` is interrupted by it, so a signal reaches cleanup at once.
gc() { gcloud "$@" & wait $!; }

resolve() { # resolve <ref>: print the commit SHA or fail
  local sha
  if ! sha="$(git -C "$ROOT" rev-parse --verify "$1^{commit}" 2>/dev/null)"; then
    echo "bench_vm: cannot resolve ref '$1' to a commit" >&2
    exit 2
  fi
  printf '%s\n' "$sha"
}
if [ "$MODE" = gc ]; then
  GC_SHA="$(resolve "$GC_REF")"
else
  BASE_SHA="$(resolve "$BASE_REF")"
  HEAD_SHA="$(resolve "$HEAD_REF")"
fi

TMP=""
NAME=""
CREATE_ATTEMPTED=0
CREATE_PID=""
CLEANED=0
# Deletes exactly $NAME, never anything found by label or pattern, then proves it is gone.
cleanup() {
  local rc=$?
  trap '' INT TERM HUP
  if [ "$CLEANED" -eq 1 ]; then return 0; fi
  CLEANED=1
  set +e
  # An in-flight create may not have inserted the VM yet: a delete now would find nothing and the
  # VM would appear later. Let the create finish, then delete. Other jobs (ssh, scp) are killed.
  local p
  for p in $(jobs -p); do
    if [ "$p" != "$CREATE_PID" ]; then kill "$p" 2>/dev/null; fi
  done
  if [ -n "$CREATE_PID" ] && kill -0 "$CREATE_PID" 2>/dev/null; then
    echo "==> waiting for create to finish before deleting" >&2
    wait "$CREATE_PID" 2>/dev/null
  fi
  local leftover=0 unverified=0
  if [ "$CREATE_ATTEMPTED" -eq 1 ]; then
    echo "==> deleting $NAME" >&2
    gcloud compute instances delete "$NAME" --quiet "${GFLAGS[@]}" "${ZFLAG[@]}" >&2
    local out
    if out="$(gcloud compute instances list --filter="name=$NAME" --format="value(name)" "${GFLAGS[@]}" 2>/dev/null)"; then
      if grep -qxF "$NAME" <<<"$out"; then leftover=1; fi
    else
      unverified=1
    fi
  fi
  if [ -n "$TMP" ]; then rm -rf "$TMP"; fi
  if [ "$leftover" -eq 1 ]; then
    {
      echo "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
      echo "!!! bench-vm: VM $NAME STILL EXISTS and is billing."
      echo "!!! Delete it by hand:"
      echo "!!!   gcloud compute instances delete $NAME --quiet --project=\$BENCH_PROJECT --account=\$BENCH_ACCOUNT --zone=\$BENCH_ZONE"
      echo "!!! (its max-run-duration of $BENCH_TTL deletes it eventually)"
      echo "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
    } >&2
    exit 1
  fi
  if [ "$unverified" -eq 1 ]; then
    echo "bench_vm: WARNING: could not confirm $NAME is deleted; check the console." >&2
    if [ "$rc" -eq 0 ]; then rc=1; fi
  fi
  exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

check_paths() { # check_paths <ref> <sha> <path>...: every shipped path exists at that commit
  local ref="$1" sha="$2" p
  shift 2
  for p in "$@"; do
    if ! git -C "$ROOT" cat-file -e "$sha:$p" 2>/dev/null; then
      echo "bench_vm: ref '$ref' has no $p, which the VM needs" >&2
      exit 2
    fi
  done
}
if [ "$MODE" = gc ]; then
  check_paths "$GC_REF" "$GC_SHA" "${GC_ARCHIVE_PATHS[@]}"
else
  check_paths "$BASE_REF" "$BASE_SHA" "${ARCHIVE_PATHS[@]}"
  check_paths "$HEAD_REF" "$HEAD_SHA" "${ARCHIVE_PATHS[@]}"
fi

TMP="$(mktemp -d "${TMPDIR:-/tmp}/sprout_bench_vm_XXXXXX")"
if [ "$MODE" = gc ]; then
  git -C "$ROOT" archive --format=tar -o "$TMP/tree.tar" "$GC_SHA" "${GC_ARCHIVE_PATHS[@]}"
  git -C "$ROOT" show "$GC_SHA:bench/gc_minor/summarize.py" > "$TMP/summarize.py"
  # An ab.sh from before it ran on Linux dies after the VM is built, 20 minutes in.
  git -C "$ROOT" show "$GC_SHA:bench/gc_minor/ab.sh" > "$TMP/ab.sh"
  if grep -q 'ERROR: macOS only' "$TMP/ab.sh"; then
    echo "bench_vm: ref '$GC_REF' has a macOS-only bench/gc_minor/ab.sh; use a ref that includes the Linux-capable one" >&2
    exit 2
  fi
  known="$(sed -n 's/^ALL_WORKLOADS="\(.*\)"$/\1/p' "$TMP/ab.sh")"
  for w in $GC_WLS; do
    case " $known " in
      *" $w "*) ;;
      *) echo "bench_vm: unknown workload '$w' in -w (ab.sh knows: $known)" >&2; exit 2 ;;
    esac
  done
  NAME_PREFIX=sprout-bench-gc
else
  git -C "$ROOT" archive --format=tar -o "$TMP/base.tar" "$BASE_SHA" "${ARCHIVE_PATHS[@]}"
  git -C "$ROOT" archive --format=tar -o "$TMP/head.tar" "$HEAD_SHA" "${ARCHIVE_PATHS[@]}"
  NAME_PREFIX=sprout-bench
fi

NAME="$NAME_PREFIX-$(date -u +%Y%m%d-%H%M%S)-$(od -An -N2 -tx1 /dev/urandom | tr -d ' \n')"
OUT_DIR="${BENCH_OUT_DIR:-$ROOT/build/bench-vm}/$NAME"

echo "==> creating $NAME ($BENCH_MACHINE, ttl $BENCH_TTL)"
CREATE_ATTEMPTED=1
gcloud compute instances create "$NAME" \
  --machine-type="$BENCH_MACHINE" \
  --image-family="$BENCH_IMAGE_FAMILY" \
  --image-project="$BENCH_IMAGE_PROJECT" \
  --max-run-duration="$BENCH_TTL" \
  --instance-termination-action=DELETE \
  --labels=purpose=sprout-bench \
  --metadata=block-project-ssh-keys=true \
  --no-service-account --no-scopes \
  "${GFLAGS[@]}" "${ZFLAG[@]}" &
CREATE_PID=$!
if wait "$CREATE_PID"; then create_rc=0; else create_rc=$?; fi
CREATE_PID=""
if [ "$create_rc" -ne 0 ]; then exit "$create_rc"; fi

echo "==> waiting for ssh"
ready=0
for _ in $(seq 1 30); do
  if gc compute ssh "$NAME" --command=true --quiet --strict-host-key-checking=no \
      "${GFLAGS[@]}" "${ZFLAG[@]}"; then
    ready=1
    break
  fi
  sleep "$BENCH_SSH_RETRY_SLEEP"
done
if [ "$ready" -ne 1 ]; then
  echo "bench_vm: $NAME never accepted ssh" >&2
  exit 1
fi

run_gc() {
  local cmd a run_rc=0 got=0
  echo "==> copying the tree and the remote script"
  gc compute scp "$TMP/tree.tar" "$ROOT/scripts/bench_vm_remote.sh" "$NAME:~" \
    --quiet --strict-host-key-checking=no "${GFLAGS[@]}" "${ZFLAG[@]}"

  # The remote shell re-parses this line: every argument is quoted for it (arms hold `;`).
  cmd="bash bench_vm_remote.sh --gc tree.tar $GC_SHA"
  for a in "$GC_ARMS" ${AB_ARGS[@]+"${AB_ARGS[@]}"}; do cmd="$cmd $(printf '%q' "$a")"; done
  echo "==> running ab.sh on ${GC_SHA:0:12}: arms '$GC_ARMS' ${AB_ARGS[*]-}"
  if gc compute ssh "$NAME" --quiet --strict-host-key-checking=no --command="$cmd" \
      "${GFLAGS[@]}" "${ZFLAG[@]}"; then :; else run_rc=$?; fi

  # The remote script packs what exists even when ab.sh failed: fetch it either way.
  echo "==> fetching results"
  mkdir -p "$OUT_DIR"
  if gc compute scp "$NAME:gc_out.tgz" "$TMP/gc_out.tgz" \
      --quiet --strict-host-key-checking=no "${GFLAGS[@]}" "${ZFLAG[@]}" \
      && tar -xzf "$TMP/gc_out.tgz" -C "$OUT_DIR"; then
    got=1
    echo "==> saved $OUT_DIR"
  fi
  if [ "$run_rc" -ne 0 ]; then
    echo "bench_vm: ab.sh run failed (exit $run_rc); results fetched: $got ($OUT_DIR)" >&2
    exit "$run_rc"
  fi
  if [ "$got" -ne 1 ]; then echo "bench_vm: could not fetch the results" >&2; exit 1; fi
  if ! python3 -I "$TMP/summarize.py" "$OUT_DIR/results.tsv" > "$OUT_DIR/summary.md"; then
    echo "bench_vm: summarize.py failed on $OUT_DIR/results.tsv" >&2
    exit 1
  fi
  echo "==> summary: $OUT_DIR/summary.md"
}

if [ "$MODE" = gc ]; then
  run_gc
  exit 0
fi

echo "==> copying trees and the remote script"
gc compute scp "$TMP/base.tar" "$TMP/head.tar" "$ROOT/scripts/bench_vm_remote.sh" "$NAME:~" \
  --quiet --strict-host-key-checking=no "${GFLAGS[@]}" "${ZFLAG[@]}"

echo "==> running ($BENCH_ROUNDS rounds; base ${BASE_SHA:0:12} head ${HEAD_SHA:0:12})"
gc compute ssh "$NAME" --quiet --strict-host-key-checking=no \
  --command="BENCH_ROUNDS=$BENCH_ROUNDS bash bench_vm_remote.sh base.tar head.tar $BASE_SHA $HEAD_SHA" \
  "${GFLAGS[@]}" "${ZFLAG[@]}"

echo "==> fetching results"
mkdir -p "$OUT_DIR"
gc compute scp "$NAME:results.txt" "$OUT_DIR/results.txt" \
  --quiet --strict-host-key-checking=no "${GFLAGS[@]}" "${ZFLAG[@]}"
echo "==> saved $OUT_DIR/results.txt"
echo
cat "$OUT_DIR/results.txt"
