#!/usr/bin/env bash
# Gate: the REPL on piped stdin, with lines that are not valid UTF-8.
#
# A `String` cannot hold such a line, so the REPL reads it as an error. Outside a `:{ ... :}` block
# the line is skipped with a message. Inside one it cancels the block: the block must not run
# without a line it could not read, so the function it declares stays undefined.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SPROUTD="$ROOT/build/sproutd"
fail=0

if [ ! -x "$SPROUTD" ]; then
  echo "ERROR: $SPROUTD not found; run: just build-sproutd" >&2
  exit 1
fi

check() { # label, condition-already-evaluated (0 = pass)
  if [ "$2" -eq 0 ]; then echo "PASS $1"; else echo "FAIL $1"; fail=1; fi
}

# Control: the same block, every line readable, declares f.
out="$(printf ':{\nfn f() -> Int = 1\n:}\nf()\n' | "$SPROUTD" 2>&1)"
echo "$out" | grep -qx '1'
check "a block of readable lines declares its function" $?

# The block with a line that is not valid UTF-8 is cancelled, with a message.
out="$(printf ':{\nfn f() -> Int = 1\n\351\n:}\nf()\n' | "$SPROUTD" 2>&1)"
echo "$out" | grep -q 'input line is not valid UTF-8; block cancelled'
check "an unreadable line inside a block cancels it, with a message" $?
# ... and the function is not defined: calling it is an error, not the value 1.
! echo "$out" | grep -qx '1'
check "the cancelled block's function is not defined" $?
echo "$out" | grep -q 'Unknown variable: f'
check "the session went on and answered the later line" $?
! echo "$out" | grep -q 'parse'
check "the rest of the cancelled block was not read as input" $?

# Outside a block the line is skipped with a message and the session goes on.
out="$(printf '\351\n1 + 1\n' | "$SPROUTD" 2>&1)"
echo "$out" | grep -q 'input line is not valid UTF-8; skipped'
check "an unreadable line outside a block is skipped, with a message" $?
echo "$out" | grep -qx '2'
check "the line after a skipped one is evaluated" $?

if [ "$fail" -eq 0 ]; then echo "==> repl smoke: OK"; else echo "==> repl smoke: FAILED"; fi
exit $fail
