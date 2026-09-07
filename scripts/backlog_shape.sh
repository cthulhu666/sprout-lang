#!/usr/bin/env bash
# Enforce AGENTS.md "Backlog Discipline" rules 1 and 2 on BACKLOG.md:
#   - every line wraps at MAX_COLS
#   - every entry is at most MAX_ENTRY_LINES lines
#   - no `[x]` entry survives; a landed entry is deleted, not ticked
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
FILE="${1:-BACKLOG.md}"
MAX_COLS="${MAX_COLS:-100}"
MAX_ENTRY_LINES="${MAX_ENTRY_LINES:-10}"

fail=0

if [[ ! -f "$FILE" ]]; then
  echo "backlog-shape: no such file: $FILE" >&2
  exit 1
fi

# Rule 2: a landed entry is deleted, not ticked.
if done_lines=$(grep -n '^ *- \[[xX]\]' "$FILE"); then
  echo "backlog-shape: FAIL — closed entries must be deleted, not left as [x]:" >&2
  echo "$done_lines" | sed 's/^/  /' >&2
  fail=1
fi

# Rule 1a: wrap at MAX_COLS.
if long_lines=$(grep -n ".\{$((MAX_COLS + 1)),\}" "$FILE"); then
  echo "backlog-shape: FAIL — lines over $MAX_COLS columns:" >&2
  echo "$long_lines" | cut -c1-120 | sed 's/^/  /' >&2
  fail=1
fi

# Rule 1b: an entry is at most MAX_ENTRY_LINES lines. An entry runs from a
# top-level "- [ ]" through its indented continuations and nested bullets, and
# ends at the next line that starts in column 0. Trailing blanks do not count.
over=$(awk -v max="$MAX_ENTRY_LINES" '
  function flush() {
    n = last - start + 1
    if (start && n > max) printf "  %s:%d: %d lines — %s\n", FILENAME, start, n, title
    start = 0
  }
  /^- \[/    { flush(); start = FNR; last = FNR; title = substr($0, 1, 96); next }
  /^[ \t]*$/ { next }
  /^[^ \t]/  { flush(); next }
  { if (start) last = FNR }
  END { flush() }
' "$FILE")

if [[ -n "$over" ]]; then
  echo "backlog-shape: FAIL — entries over $MAX_ENTRY_LINES lines (move detail to docs/<feature>-v0.md):" >&2
  echo "$over" >&2
  fail=1
fi

if (( fail )); then
  echo "backlog-shape: see AGENTS.md \"Backlog Discipline\"." >&2
  exit 1
fi

echo "backlog-shape: OK — $(grep -c '^- \[' "$FILE") entries, all <= $MAX_ENTRY_LINES lines and <= $MAX_COLS columns."
