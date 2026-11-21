#!/usr/bin/env bash
# Gate: the REPL on piped stdin: `:{ ... :}` blocks over several lines, and lines that are not
# valid UTF-8.
#
# A `String` cannot hold such a line, so the REPL reads it as an error. Outside a `:{ ... :}` block
# the line is skipped with a message. Inside one it cancels the block: the block must not run
# without a line it could not read, so the function it declares stays undefined.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SPROUTD="$ROOT/build/sproutd"
fail=0

# shellcheck source=scripts/test_lib.sh
source "$ROOT/scripts/test_lib.sh"

if [ ! -x "$SPROUTD" ]; then
  echo "ERROR: $SPROUTD not found; run: just build-sproutd" >&2
  exit 1
fi

check() { # label, condition-already-evaluated (0 = pass)
  if [ "$2" -eq 0 ]; then echo "PASS $1"; else echo "FAIL $1"; fail=1; fi
}

# Control: the same block, every line readable, declares f.
out="$(printf ':{\nfn f() -> Int = 1\n:}\nf()\n' | "$SPROUTD" 2>&1)"
has "$out" -x '1'
check "a block of readable lines declares its function" $?

# The block with a line that is not valid UTF-8 is cancelled, with a message.
out="$(printf ':{\nfn f() -> Int = 1\n\351\n:}\nf()\n' | "$SPROUTD" 2>&1)"
has "$out" 'input line is not valid UTF-8; block cancelled'
check "an unreadable line inside a block cancels it, with a message" $?
# ... and the function is not defined: calling it is an error, not the value 1.
! has "$out" -x '1'
check "the cancelled block's function is not defined" $?
has "$out" 'Unknown variable: f'
check "the session went on and answered the later line" $?
! has "$out" 'parse'
check "the rest of the cancelled block was not read as input" $?

# Outside a block the line is skipped with a message and the session goes on.
out="$(printf '\351\n1 + 1\n' | "$SPROUTD" 2>&1)"
has "$out" 'input line is not valid UTF-8; skipped'
check "an unreadable line outside a block is skipped, with a message" $?
has "$out" -x '2'
check "the line after a skipped one is evaluated" $?

# Blocks spanning several lines: a function body, a class and its instance, and blocks mixed with
# single-line submissions that use what earlier ones declared.
out="$(printf ':{\nfn fact(n: Int) -> Int =\n  if n <= 1 then 1\n  else n * fact(n - 1)\n:}\nfact(5)\n' | "$SPROUTD" 2>&1)"
check "a multi-line function block runs" $?
has "$out" -x '120'
check "a multi-line function block declares its function" $?

out="$(printf ':{\nclass Sized a\n  fn size(v: a) -> Int\n:}\n:{\ninstance Sized Int\n  fn size(v: Int) -> Int = v\n:}\nsize(4)\n' | "$SPROUTD" 2>&1)"
check "a class block and an instance block run" $?
has "$out" -x '4'
check "a method declared by class and instance blocks is callable" $?

out="$(printf 'let x = 1\n:{\nfn g(a: Int) -> Int =\n  a + x\n:}\ng(2)\nprint_str("hi")\n:{\nfn h() -> Int = 3\n:}\nh()\n' | "$SPROUTD" 2>&1)"
check "blocks mixed with single lines run" $?
has "$out" -x '3' && has "$out" -x 'hi' && [ "$(grep -cx '3' <<<"$out")" -eq 2 ]
check "each submission sees the ones before it" $?

if [ "$fail" -eq 0 ]; then echo "==> repl smoke: OK"; else echo "==> repl smoke: FAILED"; fi
exit $fail
