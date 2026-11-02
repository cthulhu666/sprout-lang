#!/usr/bin/env bash
# Every descriptor the runtime creates is close-on-exec (docs/gates.md §Close-on-exec).
#
# Fails on a raw fd-creating call in runtime/*.c outside the cloexec_* wrappers. The markers that
# exempt the wrapper bodies and the fork-child dup2 lines, and the known bypasses, are in
# docs/gates.md, section Close-on-exec. Comments and string contents are blanked before matching.
#
# usage: cloexec_gate.sh [runtime-dir]   (default: runtime/ beside this script's repo)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIR="${1:-$ROOT/runtime}"

shopt -s nullglob
files=("$DIR"/*.c)
if [[ ${#files[@]} -eq 0 ]]; then
  echo "cloexec-gate: no runtime/*.c under $DIR" >&2
  exit 1
fi

# A call is a name followed by `(`. `(^|[^A-Za-z0-9_])` keeps freopen, fdopen and cloexec_open out.
CALLS='(^|[^A-Za-z0-9_])(socket|socketpair|accept|accept4|pipe|pipe2|open|openat|fopen|opendir|dup|dup2|dup3|mkstemp|mkostemp|eventfd|signalfd|inotify_init|inotify_init1)[[:space:]]*\('

hits=$(CALLS="$CALLS" awk '
  function strip(line,   out, i, n, c, d) {
    out = ""; n = length(line); i = 1; instr = ""
    while (i <= n) {
      c = substr(line, i, 1); d = substr(line, i, 2)
      if (inblock) { if (d == "*/") { inblock = 0; i += 2 } else i++; continue }
      if (instr != "") {
        if (c == "\\") { i += 2; continue }
        if (c == instr) { instr = ""; out = out c }
        i++; continue
      }
      if (d == "/*") { inblock = 1; i += 2; out = out " "; continue }
      if (d == "//") break
      if (c == "\"" || c == "\047") { instr = c; out = out c; i++; continue }
      out = out c; i++
    }
    return out
  }
  BEGIN { calls = ENVIRON["CALLS"] }
  FNR == 1 { inblock = 0; wrap = 0 }
  {
    orig = $0
    if (index(orig, "cloexec-gate: begin wrappers")) wrap = 1
    code = strip(orig)
    skip = wrap || index(orig, "cloexec-gate: fork child")
    if (index(orig, "cloexec-gate: end wrappers")) wrap = 0
    if (skip) next
    if (code ~ calls) { print FILENAME ":" FNR ": " orig; next }
    # epoll_create / epoll_create1 need EPOLL_CLOEXEC, timerfd_create needs TFD_CLOEXEC.
    if (code ~ /(^|[^A-Za-z0-9_])epoll_create1?[[:space:]]*\(/ && code !~ /EPOLL_CLOEXEC/) { print FILENAME ":" FNR ": " orig; next }
    if (code ~ /(^|[^A-Za-z0-9_])timerfd_create[[:space:]]*\(/ && code !~ /TFD_CLOEXEC/) { print FILENAME ":" FNR ": " orig; next }
  }
' "${files[@]}")

if [[ -n "$hits" ]]; then
  echo "cloexec-gate: raw descriptor-creating call(s); use the cloexec_* wrappers in sprout_runtime.c:" >&2
  printf '%s\n' "$hits" >&2
  exit 1
fi
echo "cloexec-gate: ok (${#files[@]} files)"
