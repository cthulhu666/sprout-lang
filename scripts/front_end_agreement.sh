#!/usr/bin/env bash
#
# Differential gate: the two typecheck front ends must agree, and the editor one must
# terminate.
#
# Sprout answers "does this file typecheck?" twice:
#
#   --phase check   the BUNDLER  — bundle_file → check. Every other gate in this repo
#                                  runs this one.
#   (no flag)       the ENV path — build_import_pairs → load_module →
#                                  check_program_with_env. This is what the REPL, the
#                                  analysis service and the LSP typecheck with.
#
# Until this script existed, NOTHING ran the env path: every compile_driver invocation in
# the justfile passes --phase check, --emit-ir or another explicit phase. So a divergence
# between the two was invisible to CI by construction and could only be found by a user in
# an editor. Two were, on 2026-08-18, both against a live RubyMine session:
#
#   examples/vec_basics.sprout      bundler accepts; env path reports a false
#                                   "Vec vs List" on correct source
#   examples/concurrent_fetch.sprout bundler accepts in 0.28s; env path never terminates,
#                                   looping in unifier.apply_full_subst
#
# The second is why this gate bounds time rather than only comparing verdicts. A hang
# cannot be caught by an in-process .spr test, because such a test cannot bound its own
# runtime — it would simply hang `just test` instead of failing it.
#
# Usage: front_end_agreement.sh [timeout-seconds]
set -uo pipefail

BIN="${SPROUT_STAGE1:-build/compile_driver_bin_stage1}"
STDLIB_ROOT="${SPROUT_STDLIB_ROOT:-stdlib}"
LIMIT="${1:-60}"

if [ ! -x "$BIN" ]; then
  echo "front-end-agreement: $BIN not built" >&2
  exit 1
fi

# The corpus. examples/ is the user-facing surface and the one an editor actually opens;
# the agreement fixtures pin the specific divergences that motivated this gate.
corpus=()
while IFS= read -r f; do corpus+=("$f"); done < <(
  find examples tests/conformance/front_end_agreement -name '*.sprout' 2>/dev/null | sort
)

if [ "${#corpus[@]}" -eq 0 ]; then
  # A gate that silently checks nothing reads as a pass. Refuse instead.
  echo "front-end-agreement: corpus is empty — refusing to report success" >&2
  exit 1
fi

verdict() { # $1 = "env" | "bundler", $2 = file  →  echoes accept | reject | TIMEOUT
  local rc
  if [ "$1" = "bundler" ]; then
    timeout -s KILL "$LIMIT" "$BIN" --phase check "$STDLIB_ROOT" "$2" 1>/dev/null 2>&1
  else
    timeout -s KILL "$LIMIT" "$BIN" "$STDLIB_ROOT" "$2" 1>/dev/null 2>&1
  fi
  rc=$?
  case $rc in
    0) echo accept ;;
    1) echo reject ;;
    *) echo "TIMEOUT($rc)" ;;   # 137 = SIGKILL from timeout; anything else is a crash
  esac
}

hangs=0
disagree=0
checked=0

for f in "${corpus[@]}"; do
  [ -f "$f" ] || { echo "front-end-agreement: $f is not a file" >&2; exit 1; }
  b=$(verdict bundler "$f")
  e=$(verdict env "$f")
  checked=$((checked + 1))
  case "$e" in
    TIMEOUT*)
      echo "HANG      $f  (env path did not finish in ${LIMIT}s; bundler said $b)"
      hangs=$((hangs + 1))
      continue
      ;;
  esac
  case "$b" in
    TIMEOUT*)
      echo "HANG      $f  (bundler did not finish in ${LIMIT}s)"
      hangs=$((hangs + 1))
      continue
      ;;
  esac
  if [ "$b" != "$e" ]; then
    echo "DISAGREE  $f  (bundler=$b env=$e)"
    disagree=$((disagree + 1))
  fi
done

echo "==> front-end-agreement: $checked files, $disagree disagreements, $hangs hangs"
if [ "$disagree" -ne 0 ] || [ "$hangs" -ne 0 ]; then
  echo "==> front-end-agreement FAILED" >&2
  exit 1
fi
echo "==> front-end-agreement ✓"
