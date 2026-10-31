#!/usr/bin/env bash
# Compile runtime/*.c with -Wall -Wextra; -Werror only when the local clang major equals the
# major CI pins (docs/gates.md §Runtime warnings). Each file is compiled in every build variant
# (default, GC profile, age-profile stores).
#
# usage: check_runtime_warnings.sh [--pinned-major N | --print-local-major] [file.c ...]
set -uo pipefail
cd "$(dirname "$0")/.."

pinned=""
print_major=0
files=()
while (($#)); do
  case "$1" in
    --pinned-major) pinned="${2:?--pinned-major needs a value}"; shift 2 ;;
    --print-local-major) print_major=1; shift ;;
    *) files+=("$1"); shift ;;
  esac
done

local_major=$(clang --version | LC_ALL=C sed -n 's/.*clang version \([0-9][0-9]*\).*/\1/p' | head -1)
[[ -n "$local_major" ]] || { echo "ERROR: cannot read clang major" >&2; exit 2; }
if ((print_major)); then
  echo "$local_major"
  exit 0
fi

if [[ -z "$pinned" ]]; then
  pinned=$(sed -n 's/^LLVM_VERSION=\([0-9][0-9]*\).*/\1/p' scripts/ci_install_llvm.sh | head -1)
  [[ -n "$pinned" ]] || { echo "ERROR: no LLVM_VERSION in scripts/ci_install_llvm.sh" >&2; exit 2; }
fi
if ((${#files[@]} == 0)); then
  files=(runtime/*.c)
fi

# CI is where the gate enforces, so a toolchain drift there must not turn it warn-only.
if [[ -n "${CI:-}" && "$local_major" != "$pinned" ]]; then
  echo "FAIL: CI clang major $local_major != pinned $pinned (scripts/ci_install_llvm.sh)" >&2
  exit 1
fi

flags=(-fsyntax-only -Wall -Wextra)
enforce=0
if [[ "$local_major" == "$pinned" ]]; then
  flags+=(-Werror)
  enforce=1
fi

# The runtime's compile-time switches (justfile: gc-profile, gc-ageprof-check). "" is the default.
variants=("" -DSPROUT_GC_PROFILE -DSPROUT_GC_AGEPROF_STORES)

rc=0
warned=0
for f in "${files[@]}"; do
  for v in "${variants[@]}"; do
    out=$(clang "${flags[@]}" ${v:+"$v"} "$f" 2>&1) || rc=1
    if [[ -n "$out" ]]; then
      warned=1
      echo "$out"
    fi
  done
done

if ((rc)); then
  echo "FAIL: runtime has warnings or errors (clang $local_major, enforcing=$enforce)" >&2
  exit 1
fi
if ((enforce)); then
  echo "==> runtime-warnings ✓ (clang $local_major, -Werror, ${#variants[@]} variants)"
elif ((warned)); then
  echo "warnings not enforced: local clang major $local_major != pinned $pinned"
fi
exit 0
