#!/usr/bin/env bash
# Usage: scripts/error_line_marker.sh <fixture.spr> <output-file>
# A reject fixture may mark the line its error must be reported on: a comment line
# `#~^ ERROR` names the line above it, and each further `^` one line higher (rustc's
# `//~^`). The first `<line>:<col>: ERROR` in the output must be on that line. No
# marker, nothing to check. Exits 1 with a message on a mismatch or a bad marker.
set -uo pipefail

FIXTURE="${1:?usage: error_line_marker.sh <fixture.spr> <output-file>}"
OUTPUT="${2:?usage: error_line_marker.sh <fixture.spr> <output-file>}"

markers=$(grep -n -E '^[[:space:]]*#~\^+ ERROR' "$FIXTURE" || true)
[[ -z "$markers" ]] && exit 0
if [[ $(printf '%s\n' "$markers" | wc -l) -gt 1 ]]; then
  echo "more than one #~^ ERROR marker: the check stops at its first error"; exit 1
fi
at="${markers%%:*}"
carets=$(printf '%s' "${markers#*:}" | sed -E 's/^[[:space:]]*#~(\^+).*/\1/')
want=$((at - ${#carets}))
if [[ $want -lt 1 ]]; then
  echo "the #~^ ERROR marker on line $at points above line 1"; exit 1
fi
got=$(grep -m1 -o -E '^[0-9]+:[0-9]+: ERROR' "$OUTPUT" | cut -d: -f1 || true)
if [[ -z "$got" ]]; then
  echo "marked line $want, but the output has no <line>:<col>: ERROR"; exit 1
fi
if [[ "$got" != "$want" ]]; then
  echo "error reported on line $got, marked on line $want"; exit 1
fi
