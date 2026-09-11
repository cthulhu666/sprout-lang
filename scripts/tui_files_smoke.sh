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

# Keys: Right expands whatever row 0 is, then Esc quits. Which entry that is
# depends on readdir order, so the fixture holds exactly one directory — Right
# on the file is inert and the child simply never appears, failing loudly.
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
trap 'rm -rf "$FIX" "$out"' EXIT
# The binary must be the LAST element of the pipeline and must not be wrapped
# in a subshell that also waits for the writer: `$!` would then be that
# subshell, which lives until the 12s sleep ends, and a prompt exit would read
# as a hang. `exec` keeps the pid the shell reports the one that matters.
{ printf '\x1b[C'; sleep 1; printf '\x1b'; sleep 12; } \
  | ( cd "$FIX" && exec "$BIN" ) > "$out" 2>&1 &
pid=$!
# 5s: comfortably after the Esc at 1s plus one read deadline, and comfortably
# before the writer's 12s, so an exit inside the window cannot be EOF.
quit=0
for _ in $(seq 1 50); do
  if ! kill -0 $pid 2>/dev/null; then quit=1; break; fi
  sleep 0.1
done
kill -9 $pid 2>/dev/null
wait $pid 2>/dev/null
if [ "$quit" -ne 1 ]; then
  echo "FAIL: still running 5s after Esc, with stdin still open" >&2
  echo "      (the escape timeout in app.idled is what resolves a lone ESC)" >&2
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
  exit 1
fi
echo "==> tui-files-smoke: app loop ran, tree filled and expanded, Esc quit"
