#!/usr/bin/env bash
# PreToolUse hook: surface code authoring guidelines when an agent is about to
# edit Sprout source. Reads the tool input JSON on stdin, checks the file path,
# and prints the basics if the target is .sprout / .spr.
#
# The basics list is DERIVED from docs/guidelines.md's `### N.` headings (and
# their audience tags) at runtime, so it never drifts out of sync with the doc.
# Always exits 0 — this hook informs, never blocks.

set -euo pipefail

input=$(cat)
file=$(printf '%s' "$input" | jq -r '.tool_input.file_path // ""' 2>/dev/null || echo "")

case "$file" in
  *.sprout|*.spr) ;;
  *) exit 0 ;;
esac

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
doc="$script_dir/../docs/guidelines.md"

{
  echo "[guidelines] About to edit Sprout source. Recheck docs/guidelines.md:"
  if [ -f "$doc" ]; then
    # Print each numbered basic as "N. Title   [audience tags]", pulling both the
    # heading and the bracketed tags out of the doc so #7/#8/... appear for free.
    awk '
      function flush(   s, tags) {
        if (title == "") return
        tags = ""; s = tag
        while (match(s, /\[[A-Za-z]+\]/)) {
          tags = tags substr(s, RSTART, RLENGTH) " "
          s = substr(s, RSTART + RLENGTH)
        }
        printf "  %s   %s\n", title, tags
        title = ""; tag = ""
      }
      /^### [0-9]+\./ { flush(); title = substr($0, 5); next }
      /^## /          { flush() }
      title != "" && tag == "" && /^\*\[/ { tag = $0 }
      END             { flush() }
    ' "$doc"
    echo "  Tags: [Universal] all Sprout code . [Library] stdlib/public APIs . [Compiler] the pipeline."
  else
    echo "  (could not locate guidelines.md at $doc — read the repo copy directly)"
  fi
  echo "For idiomatic shapes (let..else, combinators, pipes, wrap): docs/idiomatic-sprout.md"
} >&2

exit 0
