#!/usr/bin/env bash
# Every descriptor the runtime creates is close-on-exec (docs/gates.md §Close-on-exec).
#
# Fails on a raw fd-creating call in runtime/*.c. Calls go through the cloexec_* wrappers in
# sprout_runtime.c, whose bodies sit between these two comment lines and are not scanned:
#     /* cloexec-gate: begin wrappers */  ...  /* cloexec-gate: end wrappers */
# A line that carries the comment `cloexec-gate: fork child` is also skipped; the dup2 calls in the
# fork children use it, since dup2 clears the flag on its target by design.
#
# Comments and string contents are blanked first, so prose such as `accept(2)` does not match.
# Known bypasses: a parenthesised name `(socket)(...)`, a macro or function pointer that hides the
# name, and a call split from its `(`.
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

# Calls that take an argument. `(^|[^A-Za-z0-9_])` keeps freopen, fdopen and cloexec_open out.
CALLS='(^|[^A-Za-z0-9_])(socket|socketpair|accept|accept4|pipe|pipe2|open|openat|fopen|opendir|dup|dup2|dup3|mkstemp|mkostemp|eventfd|signalfd|inotify_init|inotify_init1)[[:space:]]*\([[:space:]]*[A-Za-z_&"0-9]'

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
