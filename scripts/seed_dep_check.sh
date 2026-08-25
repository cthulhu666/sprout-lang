#!/usr/bin/env bash
# Every gate recipe that USES the stage-1 binary must also BUILD it.
#
# `_test-stdlib` and `_test-reject` take the binary as a path argument and guard
# it with `[[ -x ]]` — an existence test, not a freshness test. So a gate that
# names the path without depending on `bootstrap-from-seed` runs whatever binary
# happens to be in build/, which after a branch switch or a `refresh-seed` is the
# PREVIOUS compiler. The suite then reports green for code that is not in the
# working tree (hit for real on 2026-08-08, BACKLOG.md).
#
# Depending on the aggregate is not enough, and this is the subtle half. `just`
# runs a recipe's dependencies in order, each fully, before starting the next. In
# `test: test-stdlib-stage1 ... test-conformance-run ...` only the LATER members
# declare `bootstrap-from-seed`, so the earlier ones have already run against the
# stale binary by the time it is rebuilt — one `just test` invocation testing two
# different compilers, with nothing reporting the discontinuity. The dependency
# therefore has to sit on EACH consuming recipe, which is what this asserts.
#
# `bootstrap-from-seed` has an mtime freshness guard, so the added dependency
# costs a few stat calls once everything is current.
#
# ── On being loud ────────────────────────────────────────────────────────────
# This script reads the recipe graph by shelling out to `just --show`. Its first
# version treated a FAILED `just --show` as "this recipe has no dependencies"
# (`|| return 0`), which is the worst possible reading: every recipe then looks
# like it is missing the bootstrap, and the gate blames source code for what is
# an environment problem. It shipped that way and was caught twice —
#
#   * `just linux-run seed-dep-check`: the container invokes just by ABSOLUTE
#     PATH (`/opt/sprout-just/just-<ver>`) and puts nothing named `just` on PATH,
#     so every lookup failed and the whole graph came back empty.
#   * GitHub CI: a single recipe (`compile-bench`) was reported missing a
#     dependency that is plainly present in the committed justfile.
#
# So: resolve the interpreter the same way the caller did (JUST env var, set from
# `{{just_executable()}}` by the recipe), cache one lookup per recipe instead of
# re-walking, and treat any lookup failure as a HARD ERROR naming the recipe.
# A gate that cannot read its input must say so, not invent an answer.
set -euo pipefail

# The recipe passes JUST="{{just_executable()}}" so this uses the very binary that
# invoked it. Falling back to PATH keeps `bash scripts/seed_dep_check.sh` usable
# by hand.
JUST="${JUST:-just}"

# Recipes that legitimately name the binary without depending on the bootstrap:
# they are the ones that BUILD it (depending on it would be circular), or that
# deliberately exercise the bootstrap path itself.
EXEMPT="bootstrap-from-seed refresh-seed verify-bootstrap-fixed-point build-stage2 _build-stage"

ROOTS="gate test ci-fast-gates"
BINARY="compile_driver_bin_stage1"

declare -A SHOW_CACHE=()

# These helpers return through GLOBALS rather than stdout, and that is load-
# bearing rather than style. A `$(deps_of …)` call runs in a SUBSHELL, so every
# cache write inside it is discarded when the subshell exits — measured: the
# stdout-returning version re-ran `just --show` 145 times for 44 distinct
# recipes, i.e. the cache never once hit. Returning through globals keeps the
# walk in one shell, which both cuts the process churn and — the part that
# matters — guarantees the `uses=` probe and the closure walk read the SAME
# cached text for a recipe, so they cannot disagree mid-run.

# Populate SHOW_CACHE[$1]. FATAL on failure: a gate that cannot read its input
# must say so, never infer "no dependencies".
load_show() {
  local r="$1" out
  [[ -n "${SHOW_CACHE[$r]+set}" ]] && return 0
  if ! out=$("$JUST" --show "$r" 2>/dev/null); then
    echo "ERROR: '$JUST --show $r' failed — cannot read the recipe graph." >&2
    echo "       This is an ENVIRONMENT fault, not a missing dependency." >&2
    echo "       (Is \$JUST correct? The linux container has no 'just' on PATH.)" >&2
    exit 2
  fi
  SHOW_CACHE[$r]="$out"
}

# Dependency names on a recipe's header line -> DEPS. Parenthesised dependencies
# carry arguments — `(_test-stdlib "build/...")` — so strip the parens and keep
# only tokens shaped like recipe names, which drops the quoted path arguments.
declare -a DEPS=()
deps_of() {
  local r="$1" line="" candidate d
  load_show "$r"
  DEPS=()
  while IFS= read -r candidate; do
    case "$candidate" in "$r"[\ :]*) line="$candidate"; break ;; esac
  done <<<"${SHOW_CACHE[$r]}"
  [[ -n "$line" ]] || return 0
  line=${line#*:}
  line=${line%%#*}
  line=${line//[()]/ }
  # Disable globbing: the argument lists contain patterns like `bench/*.sprout`,
  # and unquoted word-splitting would expand them against the working directory
  # before the recipe-name filter ever sees them.
  set -f
  for d in $line; do
    [[ "$d" =~ ^[a-z_][a-z0-9_-]*$ ]] && DEPS+=("$d")
  done
  set +f
}

# Transitive dependency closure of the given recipes -> CLOSURE (roots excluded).
declare -a CLOSURE=()
closure() {
  local -a stack=("$@") mine=()
  local -A seen=()
  local r d
  CLOSURE=()
  while ((${#stack[@]})); do
    r="${stack[-1]}"
    unset 'stack[-1]'
    deps_of "$r"
    mine=(${DEPS[@]+"${DEPS[@]}"})   # copy: the next deps_of overwrites DEPS
    for d in ${mine[@]+"${mine[@]}"}; do
      if [[ -z "${seen[$d]:-}" ]]; then
        seen[$d]=1
        CLOSURE+=("$d")
        stack+=("$d")
      fi
    done
  done
}

closure $ROOTS
mapfile -t ALL < <(printf '%s\n' ${CLOSURE[@]+"${CLOSURE[@]}"} | sort -u)
# The roots are aggregates, but check them too so a root that names the binary
# directly in its own body is not skipped.
for r in $ROOTS; do ALL+=("$r"); done

# Vacuity guard. Every assertion below is of the form "no recipe is missing the
# dependency", which a check that examined NOTHING also satisfies — and the way
# this script fails is precisely by seeing nothing. So count what was actually
# inspected and fail if the numbers collapse. Without this, the linux-container
# breakage above would have presented as a GREEN gate proving only that the
# parser was broken.
if ((${#ALL[@]} < 20)); then
  echo "VACUOUS: expanded only ${#ALL[@]} recipes from '$ROOTS'; the dependency" >&2
  echo "         walk is broken, so a PASS here would prove nothing." >&2
  exit 1
fi

violations=0
examined=0
for r in $(printf '%s\n' "${ALL[@]}" | sort -u); do
  case " $EXEMPT " in *" $r "*) continue ;; esac
  load_show "$r"
  case "${SHOW_CACHE[$r]}" in *"$BINARY"*) ;; *) continue ;; esac
  examined=$((examined + 1))
  closure "$r"
  deps=$'\n'"$(printf '%s\n' ${CLOSURE[@]+"${CLOSURE[@]}"})"$'\n'
  if [[ "$deps" != *$'\n'"bootstrap-from-seed"$'\n'* ]]; then
    echo "MISSING DEP: recipe \`$r\` uses $BINARY but does not depend on bootstrap-from-seed" >&2
    echo "  its dependency closure was:${deps//$'\n'/ }" >&2
    violations=$((violations + 1))
  fi
done

if ((violations)); then
  echo "" >&2
  echo "==> $violations gate recipe(s) can run a STALE stage-1 binary." >&2
  echo "    Fix: add \`bootstrap-from-seed\` as the FIRST dependency of each," >&2
  echo "    so it is scheduled before the dependency that consumes the binary." >&2
  exit 1
fi

# Second half of the vacuity guard: the recipes that USE the binary are the only
# ones this can assert anything about, so a collapse to zero is the failure the
# count above cannot see. The floor sits below the known consumer count, so
# retiring a gate is a number to edit here rather than a red build.
if ((examined < 6)); then
  echo "VACUOUS: only $examined recipe(s) matched $BINARY (expected at least 6);" >&2
  echo "         'just --show' parsing is broken, so a PASS proves nothing." >&2
  exit 1
fi

echo "==> All $examined gate recipes that use $BINARY depend on bootstrap-from-seed"
echo "    (${#ALL[@]} recipes reachable from: $ROOTS)"
