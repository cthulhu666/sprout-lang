#!/usr/bin/env bash
# Gate: the `TermResized` arm of `stdlib/tui/app.sprout`'s message pump.
#
# Every other arm of `run` was verifiable with piped stdin; this one is not.
# `term_raw_enter` returns early on a non-tty (runtime/sprout_runtime.c) and never
# installs the SIGWINCH handler, and SIGWINCH's default disposition is ignore — so
# with a pipe on stdin the flag is never set and `read_avail` never answers
# `TermResized`. A pty is what arms the handler. It is NOT needed to change the
# window size: the runtime reports a flag, not a measurement, so an ordinary
# `kill -WINCH` at the process exercises the whole arm.
#
# The observable is the repaint. `resized` replaces the screen, and a fresh screen
# is blank in both buffers, so the next `diff_to_ansi` re-emits the whole frame.
# The marker therefore appears TWICE with the signal. The control run — same
# binary, same pty, no signal — is the load-bearing half: one occurrence alone
# would not distinguish a repaint from the initial paint.
#
# What this does NOT cover: that a CHANGED size is adopted. That needs TIOCSWINSZ
# on the pty master, which `script(1)` owns and does not expose.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# Overridable because `just linux-run` mounts the repo read-only and redirects
# build_dir to /tmp/build.
BIN="${SPROUT_RESIZE_PROBE_BIN:-$ROOT/build/resize_probe}"
MARKER="RESIZE-PROBE-MARKER"

if [ ! -x "$BIN" ]; then
  echo "ERROR: $BIN not found; run: just tui-resize-probe" >&2
  exit 1
fi

# BSD and util-linux `script` take the command in different positions. Both copy
# the child's output to their own stdout, which is what gets captured, and both
# allocate a real pty. Both branches run green (`just linux-run tui-resize-probe`
# covers the second, on the epoll backend).
run_probe() {
  local out="$1"; shift
  case "$(uname -s)" in
    Darwin) script -q /dev/null "$BIN" "$@" </dev/null >"$out" 2>&1 ;;
    *)      script -q -e -c "'$BIN' $*" /dev/null </dev/null >"$out" 2>&1 ;;
  esac
}

count_marker() { grep -o "$MARKER" "$1" | wc -l | tr -d ' '; }

TMPD=$(mktemp -d /tmp/sprout_tui_resize_XXXXXX); trap 'rm -rf "$TMPD"' EXIT
fail=0

run_probe "$TMPD/winch.txt"
winch_exit=$?
run_probe "$TMPD/control.txt" --no-winch
control_exit=$?

winch_n=$(count_marker "$TMPD/winch.txt")
control_n=$(count_marker "$TMPD/control.txt")

if [ "$winch_exit" -ne 0 ] || [ "$control_exit" -ne 0 ]; then
  echo "  FAIL: probe exited nonzero (winch=$winch_exit control=$control_exit)"
  sed -n '1,5p' "$TMPD/winch.txt"
  fail=1
fi

if [ "$control_n" -eq 1 ]; then
  echo "  ok: control paints the frame once (no signal, no repaint)"
else
  echo "  FAIL: control painted $control_n frames, expected 1"; fail=1
fi

if [ "$winch_n" -eq 2 ]; then
  echo "  ok: SIGWINCH forces a full repaint (frame painted twice)"
else
  echo "  FAIL: signalled run painted $winch_n frames, expected 2"; fail=1
fi

[ "$fail" -eq 0 ] || { echo "tui-resize-probe: FAIL" >&2; exit 1; }
echo "tui-resize-probe: ok"
