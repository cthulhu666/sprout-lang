#!/usr/bin/env bash
# Gate: the IDE is DRIVEN — tree to editor to disk — not merely compiled.
#
# `tui-files-smoke` already proves the app loop starts and a tree fills. What is
# new here is the SAVE handshake, which no unit test can reach end to end: the
# pane's text lives inside an existential, so the only proof that `update` got
# the right bytes out of it is a file on disk with the right bytes in it.
#
# THREE RUNS, each PINNED to one save strategy with `--save-when`. Pinning is
# the point, not tidiness: with autosave on, a run that presses ctrl-s proves
# nothing about ctrl-s, because the file would have reached the disk either way.
# So `manual` presses ctrl-s, `idle` types and pauses, and `unfocused` types and
# Tabs away — and each was checked to leave the file alone when its own strategy
# is swapped for `manual`. Design: docs/ide-save-v0.md §9.
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

fail=0

# One scratch root, trapped ONCE. A trap re-registered per run would only ever
# clean the last run's directory, and each run can sit for 15s — long enough to
# be interrupted and leave /tmp/sprout_ide_* behind.
WORK=$(mktemp -d /tmp/sprout_ide_XXXXXX)
trap 'rm -rf "$WORK"' EXIT

# Drive the IDE once. $1 names the run, $2 is the --save-when value, $3 is a
# shell fragment writing the keystrokes, $4 the file content expected after.
#
# The writer SLEEPS after the Esc rather than closing: `TermEof` closes the
# pump's channel, so a pipe that ends quits the app whatever the keys were, and
# an exit would prove nothing. Holding the stream open makes the exit
# attributable to Escape alone. The binary must be LAST in the pipeline so `$!`
# is the process that matters.
#
# `run_fail` is local so a later run cannot be blamed for an earlier one's
# failure: keying the output dump off the global would dump all three whenever
# the first went wrong, and two of those blocks would be from passing runs.
run_ide() {
  local name="$1" strategy="$2" keys="$3" want="$4"
  local run_fail=0
  local fix="$WORK/$name" out="$WORK/$name.out"
  mkdir -p "$fix"

  # EXACTLY ONE entry, so row 0 of the tree is it whatever readdir order is. A
  # second entry would make which file Enter opens depend on the filesystem.
  printf 'hello\n' > "$fix/ZFILE"

  eval "$keys" | ( cd "$fix" && exec "$BIN" "--save-when=$strategy" ) > "$out" 2>&1 &
  local pid=$!

  # 15s: after the last key plus a read deadline, well before the writer's 20s,
  # so an exit inside the window cannot be EOF.
  local quit=0
  for _ in $(seq 1 150); do
    if ! kill -0 $pid 2>/dev/null; then quit=1; break; fi
    sleep 0.1
  done
  kill -9 $pid 2>/dev/null
  wait $pid 2>/dev/null

  if [ "$quit" -ne 1 ]; then
    echo "FAIL [$name]: still running 15s after Esc, with stdin still open" >&2
    run_fail=1
  fi

  local got
  got=$(cat "$fix/ZFILE")
  if [ "$got" != "$want" ]; then
    if ! grep -q 'hello' "$out"; then
      # The file never reached the pane, so nothing downstream of it was tested.
      # A timing failure, not a broken handshake — widen the post-Enter gap.
      echo "FAIL [$name]: the fixture never loaded, so the save was never exercised" >&2
      echo "       Enter -> read_body -> Loaded did not land inside its window" >&2
    else
      echo "FAIL [$name]: the save round trip did not reach the disk" >&2
      echo "       wanted '$want', got '$got'" >&2
    fi
    run_fail=1
  fi

  # The fixture's entry proves `boot`'s read reached the tree.
  if ! grep -q 'ZFILE' "$out"; then
    echo "FAIL [$name]: expected the fixture file (ZFILE) in the output" >&2
    run_fail=1
  fi
  # Leaving the alternate screen proves the shutdown ran its restore path rather
  # than the process being killed.
  if ! grep -q '1049l' "$out"; then
    echo "FAIL [$name]: expected the alternate screen being left (1049l)" >&2
    run_fail=1
  fi

  if [ "$run_fail" -ne 0 ]; then
    echo "--- captured output [$name] ---" >&2
    cat -v "$out" >&2
    fail=1
  fi
}

# Enter opens row 0, Tab moves the keyboard to the editor, Q types at the top of
# the document, ctrl-s (0x13) writes it back, Esc leaves.
#
# The gap AFTER Enter is the one that has to be generous, and the only one that
# does: the keys are pipe-buffered and so ordered against each other at any
# speed, but the file read comes back on a task of its own, unordered against
# them. Miss that window under `ci-fast-gates`' parallel load and every later
# key lands on a still-empty buffer. The "never loaded" branch above exists so
# that failure names itself instead of reading as a broken save.
#
# "hello\n" opens as two lines and a caret at the top, so one typed Q gives
# "Qhello" and the empty second line is the trailing newline back again.
run_ide 'ctrl-s' manual \
  "{ printf '\r'; sleep 3; printf '\t'; sleep 1; printf 'Q'; sleep 1; \
     printf '\x13'; sleep 2; printf '\x1b'; sleep 20; }" \
  'Qhello'

# The same keys with the ctrl-s REMOVED. One tick of quiet is 500 ms, so three
# seconds of it is many chances to save; if the file still says "hello" the
# idle trigger did not fire.
run_ide 'idle' idle:500 \
  "{ printf '\r'; sleep 3; printf '\t'; sleep 1; printf 'Q'; sleep 3; \
     printf '\x1b'; sleep 20; }" \
  'Qhello'

# A second Tab instead of the ctrl-s, moving the keyboard back to the tree. The
# blur's messages reach the loop only because `focus.move_focus` blurs BEFORE it
# lights and keeps what the blurred widget said — a wiring detail no unit test
# of the pane can see, which is what this run is for.
run_ide 'unfocused' unfocused \
  "{ printf '\r'; sleep 3; printf '\t'; sleep 1; printf 'Q'; sleep 1; \
     printf '\t'; sleep 2; printf '\x1b'; sleep 20; }" \
  'Qhello'

if [ "$fail" -ne 0 ]; then exit 1; fi
echo "==> ide-smoke: ctrl-s, an idle autosave and a blur autosave each reached the disk"
