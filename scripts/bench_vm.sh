#!/usr/bin/env bash
# On-demand quiet-machine A/B benchmark of the Sprout compiler on a throwaway GCE VM.
#
#   scripts/bench_vm.sh <base-ref> <head-ref>
#
# Creates one VM, ships the parts of both trees the VM needs (each ref's committed seed, runtime
# and stdlib), runs scripts/bench_vm_remote.sh there, copies the results back, and ALWAYS deletes
# the VM (EXIT/INT/TERM/HUP trap), then checks it is gone. Each ref's committed seed must be
# current: the VM builds each tree's compiler from its own seed.
# Config: BENCH_PROJECT, BENCH_ACCOUNT, BENCH_ZONE (env, or the file named by BENCH_VM_CONFIG,
# default ~/.config/sprout/bench-vm.env). The active gcloud config is never read. See docs/bench-vm.md.
set -euo pipefail

CFG="${BENCH_VM_CONFIG:-$HOME/.config/sprout/bench-vm.env}"
CFG_VARS="BENCH_PROJECT BENCH_ACCOUNT BENCH_ZONE BENCH_MACHINE BENCH_IMAGE_FAMILY BENCH_IMAGE_PROJECT BENCH_TTL BENCH_ROUNDS BENCH_SSH_RETRY_SLEEP BENCH_OUT_DIR"
# What the VM needs from each tree: the seed, the runtime, the stdlib (the timed workload) and the clang check.
ARCHIVE_PATHS=(bootstrap/compile_driver.ll runtime stdlib scripts/check_clang_cpr.sh)

if [ "$#" -ne 2 ]; then
  echo "usage: scripts/bench_vm.sh <base-ref> <head-ref>" >&2
  exit 2
fi
BASE_REF="$1"
HEAD_REF="$2"

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
# The TTL must outlast the run: package install and builds (~20 min) plus the timed rounds.
BENCH_TTL="${BENCH_TTL:-$((20 + 2 * BENCH_ROUNDS))m}"

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
BASE_SHA="$(resolve "$BASE_REF")"
HEAD_SHA="$(resolve "$HEAD_REF")"

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
      if printf '%s\n' "$out" | grep -qxF "$NAME"; then leftover=1; fi
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

check_paths() { # check_paths <ref> <sha>: every shipped path exists at that commit
  local p
  for p in "${ARCHIVE_PATHS[@]}"; do
    if ! git -C "$ROOT" cat-file -e "$2:$p" 2>/dev/null; then
      echo "bench_vm: ref '$1' has no $p, which the VM needs" >&2
      exit 2
    fi
  done
}
check_paths "$BASE_REF" "$BASE_SHA"
check_paths "$HEAD_REF" "$HEAD_SHA"

TMP="$(mktemp -d "${TMPDIR:-/tmp}/sprout_bench_vm_XXXXXX")"
git -C "$ROOT" archive --format=tar -o "$TMP/base.tar" "$BASE_SHA" "${ARCHIVE_PATHS[@]}"
git -C "$ROOT" archive --format=tar -o "$TMP/head.tar" "$HEAD_SHA" "${ARCHIVE_PATHS[@]}"

NAME="sprout-bench-$(date -u +%Y%m%d-%H%M%S)-$(od -An -N2 -tx1 /dev/urandom | tr -d ' \n')"
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
