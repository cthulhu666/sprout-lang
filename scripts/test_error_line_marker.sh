#!/usr/bin/env bash
# Exercise scripts/error_line_marker.sh: a fixture's `#~^ ERROR` marker against the
# line the compiler reports. Each case writes a fixture and an output, and names the
# exit status the check must give.
set -uo pipefail

CHECK="$(dirname "$0")/error_line_marker.sh"
TMPD=$(mktemp -d /tmp/sprout_marker_XXXXXX)
trap 'rm -rf "$TMPD"' EXIT
fails=0

# case <name> <want-status> <fixture> <output>
case_() {
  printf '%s' "$3" > "$TMPD/f.spr"
  printf '%s' "$4" > "$TMPD/out.txt"
  bash "$CHECK" "$TMPD/f.spr" "$TMPD/out.txt" > "$TMPD/msg.txt" 2>&1
  local got=$?
  if [[ $got -ne $2 ]]; then
    echo "FAIL $1: exit $got, want $2: $(cat "$TMPD/msg.txt")"
    fails=$((fails + 1))
  else
    echo "PASS $1"
  fi
}

PROG='module main

fn f(x: Int) -> Int =
  g(x)
#~^ ERROR

fn main() -> Unit !{IO} = print("x")
'

case_ "no marker: nothing to check" 0 'module main
' '4:3: ERROR: check: boom'
case_ "marked line matches" 0 "$PROG" '4:3: ERROR: check: boom'
case_ "marked line differs" 1 "$PROG" '3:1: ERROR: check: boom'
case_ "two carets reach two lines up" 0 'module main

fn f(x: Int) -> Int =
  g(x)
#~^^ ERROR
' '3:1: ERROR: check: boom'
case_ "first error decides" 1 "$PROG" '3:1: ERROR: check: boom
4:3: ERROR: check: second'
case_ "marker but no position in the output" 1 "$PROG" 'ERROR: check: boom'
case_ "two markers are refused" 1 'module main
fn f() -> Int = 1
#~^ ERROR
fn g() -> Int = 2
#~^ ERROR
' '2:1: ERROR: check: boom'
case_ "a caret above line 1 is refused" 1 '#~^ ERROR
module main
' '1:1: ERROR: check: boom'

if [[ $fails -gt 0 ]]; then
  echo "==> error-line-marker: $fails case(s) FAILED"
  exit 1
fi
echo "==> error-line-marker: all cases pass"
