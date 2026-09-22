#!/usr/bin/env bash
# Gate: a TUI program is RUN, not merely compiled.
#
# `compile-examples-stage1` type-checks every example and `run-example-canary`
# executes five of them, none of which is a TUI. So until this gate, no CI step
# ever started the app loop: the widget suites paint into a synthetic `Screen`,
# which cannot catch a widget that never receives its content at all.
#
# It caught one immediately. `tree`'s splice treated the empty path as a no-op,
# so `examples/tui_files.sprout`'s initial directory read — the one case whose
# path IS empty, because it names the forest itself — silently did nothing and
# the pane stayed blank. Every unit test spliced at a named path and passed.
#
# No pty is needed. `term_raw_enter` returns early on a non-tty and the pump
# still reads piped stdin, so a key sequence on stdin drives the whole loop;
# only the SIGWINCH arm needs a real pty (scripts/tui_resize_probe.sh).
#
# The fixture is a temporary directory rather than the repo, so what the tree
# shows is fixed rather than whatever happens to be checked out.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BIN="${SPROUT_TUI_FILES_BIN:-$ROOT/build/tui_files}"

if [ ! -x "$BIN" ]; then
  echo "ERROR: $BIN not found; run: just tui-files-smoke" >&2
  exit 1
fi

FIX=$(mktemp -d /tmp/sprout_tui_files_XXXXXX)
trap 'rm -rf "$FIX"' EXIT
# The two names share NO CHARACTER, and that is load-bearing rather than
# fussy. `diff_to_ansi` emits only CHANGED cells, so any letter of the child
# that happens to equal the letter at the same column of the row it pushes down
# is simply not emitted, and the name arrives split across cursor moves where no
# contiguous grep can find it. Both `nested.txt` and `DEEPMARKER` were tried and
# both split on one coincidental letter. Assert on the NAME alone for the same
# reason — a cursor jump means the leading indent is not emitted either.
mkdir -p "$FIX/tree_dir"
printf 'inner\n' > "$FIX/tree_dir/QQQQQQ"
printf 'hello\n' > "$FIX/ZFILE"

# Keys: Right expands row 0, then Esc quits. Row 0 is `tree_dir` because the
# app SORTS — directories first, then by name — and that is the only reason
# this is deterministic. It did not always: the app passed `fs.read_dir`
# through, whose order the builtin documents as the filesystem's and
# unspecified, so row 0 was the directory on one machine and the file on
# another. The gate went red on a Linux runner and the SAME COMMIT passed on a
# re-run. Right on a file is inert, so the child simply never appeared.
#
# So this gate now rests on a property with a unit test under it
# (`tests/ide/test_ide_filetree.spr`), not on the filesystem's whim. Do not
# "fix" a future failure here by pressing more keys until something expands —
# that would hide the sort regressing.
#
# The writer SLEEPS after the Esc instead of closing, and that is the whole
# point of the shape. `TermEof` closes the pump's channel, so a pipe that ends
# quits the app whatever the keys were — an earlier version of this gate read
# an exit as proof that Esc worked when it only proved EOF did. Holding the
# stream open makes the exit attributable: it can only be the Escape key.
#
# Esc reaches the pump at all only because of the ESCAPE TIMEOUT: `keys.decode`
# holds a lone ESC as a prefix, and `app.idled` resolves it when the read
# deadline elapses with the byte still held. Before that existed, Esc was held
# forever and `examples/tui_dashboard.sprout` could not be quit at all.

# Outside the fixture: a file written into it would show up in the tree.
out=$(mktemp /tmp/sprout_tui_files_out_XXXXXX)
keys=$(mktemp -u /tmp/sprout_tui_files_keys_XXXXXX)
# Separate from $out, which the `want` assertions grep: a stray writer message
# landing there could satisfy one of them.
keyserr=$(mktemp /tmp/sprout_tui_files_keyserr_XXXXXX)
trap 'rm -rf "$FIX" "$out" "$keys" "$keyserr"' EXIT

# That stderr never reaches the terminal, so a failure starting in the writer
# would otherwise be invisible.
dump_writer_err() {
  if [ -s "$keyserr" ]; then
    echo "--- keystroke writer stderr ---" >&2
    cat -v "$keyserr" >&2
  fi
}

# The writer outlives the window on purpose — a stream that ENDS quits the app
# whatever the keys were — but must not be WAITED ON: `wait` takes a pid and
# waits for its whole job. Hence a FIFO, giving it its own pid to kill, and no
# inherited stdin or stderr. docs/gates.md §Driven smokes.
mkfifo "$keys"
{ printf '\x1b[C'; sleep 1; printf '\x1b'; sleep 12; } \
  < /dev/null > "$keys" 2>"$keyserr" &
wpid=$!
( cd "$FIX" && exec "$BIN" ) < "$keys" > "$out" 2>&1 &
pid=$!
# 5s: comfortably after the Esc at 1s plus one read deadline, and comfortably
# before the writer's 12s, so an exit inside the window cannot be EOF.
quit=0
for _ in $(seq 1 50); do
  if ! kill -0 $pid 2>/dev/null; then quit=1; break; fi
  sleep 0.1
done
# The writer's own children first, while it still HAS children: killing the
# subshell reparents its trailing `sleep` and `-P` can no longer find it.
pkill -P $wpid 2>/dev/null
kill -9 $pid $wpid 2>/dev/null
wait $pid $wpid 2>/dev/null
if [ "$quit" -ne 1 ]; then
  echo "FAIL: still running 5s after Esc, with stdin still open" >&2
  echo "      (the escape timeout in app.idled is what resolves a lone ESC)" >&2
  dump_writer_err
  exit 1
fi

fail=0
want() {
  if ! grep -q "$1" "$out"; then
    echo "FAIL: expected $2 in the output ($1)" >&2
    fail=1
  fi
}

# The chrome proves the app loop started and painted a frame.
want 'Sprout' 'the title'
# The fixture's own entries prove `boot`'s read reached the tree — the splice
# at the empty path.
want 'tree_dir' 'the fixture directory'
want 'ZFILE' 'the fixture file'
# The child proves the expand round trip: on_expand -> command -> fs.read_dir
# -> addressed answer -> `At` splice -> repaint. This is the assertion that was
# red before the empty-path fix, and the reason the gate exists.
want 'QQQQQQ' "the expanded directory's child"
# Leaving the alternate screen proves the shutdown ran its restore path rather
# than the process being killed. That Esc is what caused it is established by
# the exit-window check above, not by this line.
want '1049l' 'the alternate screen being left'

if [ "$fail" -ne 0 ]; then
  echo "--- captured output ---" >&2
  cat -v "$out" >&2
  dump_writer_err
  exit 1
fi
echo "==> tui-files-smoke: app loop ran, tree filled and expanded, Esc quit"
