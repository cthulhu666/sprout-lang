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
keys=$'\x1b[C\x1b'

# Outside the fixture: a file written into it would show up in the tree.
out=$(mktemp /tmp/sprout_tui_files_out_XXXXXX)
trap 'rm -rf "$FIX" "$out"' EXIT
( cd "$FIX" && printf '%s' "$keys" | "$BIN" > "$out" 2>&1 ) &
pid=$!
for _ in $(seq 1 100); do
  kill -0 $pid 2>/dev/null || break
  sleep 0.1
done
if kill -0 $pid 2>/dev/null; then
  kill -9 $pid 2>/dev/null
  wait $pid 2>/dev/null
  echo "FAIL: tui_files did not exit on Esc within 10s" >&2
  exit 1
fi
wait $pid 2>/dev/null

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
# Leaving the alternate screen proves Esc was seen and the app shut down
# cleanly rather than being killed by the timeout above.
want '1049l' 'the alternate screen being left'

if [ "$fail" -ne 0 ]; then
  echo "--- captured output ---" >&2
  cat -v "$out" >&2
  exit 1
fi
echo "==> tui-files-smoke: app loop ran, tree filled and expanded, Esc quit"
