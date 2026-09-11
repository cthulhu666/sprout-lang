#!/usr/bin/env bash
# Gate: the IDE is DRIVEN — tree to editor to disk — not merely compiled.
#
# `tui-files-smoke` already proves the app loop starts and a tree fills. What is
# new here is the SAVE handshake, which no unit test can reach end to end: the
# pane's text lives inside an existential, so the only proof that `update` got
# the right bytes out of it is a file on disk with the right bytes in it.
#
# The assertion is therefore the fixture file's CONTENT after the run, not
# anything in the frame. Screen output is checked only for the chrome that shows
# the loop ran at all — `diff_to_ansi` emits just the changed cells, so a name
# that shares a letter with what it replaced arrives split across cursor moves
# where no contiguous grep can find it (scripts/tui_files_smoke.sh documents the
# two fixtures that were lost to this).
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BIN="${SPROUT_IDE_BIN:-$ROOT/build/ide}"

if [ ! -x "$BIN" ]; then
  echo "ERROR: $BIN not found; run: just ide-smoke" >&2
  exit 1
fi

FIX=$(mktemp -d /tmp/sprout_ide_XXXXXX)
out=$(mktemp /tmp/sprout_ide_out_XXXXXX)
trap 'rm -rf "$FIX" "$out"' EXIT

# EXACTLY ONE entry, so row 0 of the tree is it whatever readdir order is. A
# second entry would make which file Enter opens depend on the filesystem.
printf 'hello\n' > "$FIX/ZFILE"

# Enter opens row 0, Tab moves the keyboard to the editor, Q types at the top of
# the document, ctrl-s (0x13) writes it back, Esc leaves.
#
# The writer SLEEPS after the Esc rather than closing: `TermEof` closes the
# pump's channel, so a pipe that ends quits the app whatever the keys were, and
# an exit would prove nothing. Holding the stream open makes the exit
# attributable to Escape alone. The binary must be LAST in the pipeline so `$!`
# is the process that matters.
{ printf '\r'; sleep 1; printf '\t'; sleep 1; printf 'Q'; sleep 1; \
  printf '\x13'; sleep 1; printf '\x1b'; sleep 12; } \
  | ( cd "$FIX" && exec "$BIN" ) > "$out" 2>&1 &
pid=$!

# 9s: after the Esc at 4s plus a read deadline, well before the writer's 12s, so
# an exit inside the window cannot be EOF.
quit=0
for _ in $(seq 1 90); do
  if ! kill -0 $pid 2>/dev/null; then quit=1; break; fi
  sleep 0.1
done
kill -9 $pid 2>/dev/null
wait $pid 2>/dev/null

fail=0
if [ "$quit" -ne 1 ]; then
  echo "FAIL: still running 9s after Esc, with stdin still open" >&2
  fail=1
fi

# The whole point of the gate. "hello\n" opens as two lines and a caret at the
# top, so one typed Q gives "Qhello" and the empty second line is the trailing
# newline back again.
got=$(cat "$FIX/ZFILE")
if [ "$got" != "Qhello" ]; then
  echo "FAIL: the save round trip did not reach the disk" >&2
  echo "      wanted 'Qhello', got '$got'" >&2
  echo "      (tree Enter -> read -> pane -> ctrl-s -> HandOver -> write)" >&2
  fail=1
fi

want() {
  if ! grep -q "$1" "$out"; then
    echo "FAIL: expected $2 in the output ($1)" >&2
    fail=1
  fi
}

# The fixture's entry proves `boot`'s read reached the tree.
want 'ZFILE' 'the fixture file'
# Leaving the alternate screen proves the shutdown ran its restore path rather
# than the process being killed.
want '1049l' 'the alternate screen being left'

if [ "$fail" -ne 0 ]; then
  echo "--- captured output ---" >&2
  cat -v "$out" >&2
  exit 1
fi
echo "==> ide-smoke: tree opened a file, an edit reached the disk, Esc quit"
