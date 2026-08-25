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
set -euo pipefail

# Recipes that legitimately name the binary without depending on the bootstrap:
# they are the ones that BUILD it (depending on it would be circular), or that
# deliberately exercise the bootstrap path itself.
EXEMPT="bootstrap-from-seed refresh-seed verify-bootstrap-fixed-point build-stage2 _build-stage"

ROOTS="gate test ci-fast-gates"
BINARY="compile_driver_bin_stage1"

show() { just --show "$1" 2>/dev/null; }

# Dependency names on a recipe's header line. Parenthesised dependencies carry
# arguments — `(_test-stdlib "build/...")` — so strip the parens and keep only
# tokens shaped like recipe names, which drops the quoted path arguments.
deps_of() {
  local r="$1" line d
  line=$(show "$r" | grep -E "^${r}[ :]" | head -1) || return 0
  line=${line#*:}
  line=${line%%#*}
  line=${line//[()]/ }
  for d in $line; do
    [[ "$d" =~ ^[a-z_][a-z0-9_-]*$ ]] && echo "$d"
  done
}

# Transitive dependency closure of the given recipes, excluding the roots.
closure() {
  local -a stack=("$@")
  local -A seen=()
  local r d
  while ((${#stack[@]})); do
    r="${stack[-1]}"
    unset 'stack[-1]'
    for d in $(deps_of "$r"); do
      if [[ -z "${seen[$d]:-}" ]]; then
        seen[$d]=1
        echo "$d"
        stack+=("$d")
      fi
    done
  done
}

mapfile -t ALL < <(closure $ROOTS | sort -u)
# The roots are aggregates, but check them too so a root that names the binary
# directly in its own body is not skipped.
for r in $ROOTS; do ALL+=("$r"); done

violations=0
for r in $(printf '%s\n' "${ALL[@]}" | sort -u); do
  case " $EXEMPT " in *" $r "*) continue ;; esac
  # Capture rather than pipe into `grep -q`: grep exits on its first match and
  # SIGPIPEs the producer mid-write, which under `pipefail` turns a PASSING check
  # into a spurious failure.
  uses=$(show "$r")
  case "$uses" in *"$BINARY"*) ;; *) continue ;; esac
  if ! printf '%s\n' "$(closure "$r")" | grep -qx 'bootstrap-from-seed'; then
    echo "MISSING DEP: recipe \`$r\` uses $BINARY but does not depend on bootstrap-from-seed" >&2
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

echo "==> All gate recipes that use $BINARY depend on bootstrap-from-seed"
