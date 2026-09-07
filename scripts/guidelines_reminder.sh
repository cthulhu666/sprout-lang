#!/usr/bin/env bash
# PreToolUse hook: surface code authoring guidelines when an agent is about to
# edit Sprout source. Reads the tool input JSON on stdin, checks the file path,
# and prints the basics if the target is .sprout / .spr.
#
# The basics list is DERIVED from docs/guidelines.md's `### N.` headings (and
# their audience tags) at runtime, so it never drifts out of sync with the doc.
#
# Informs, never blocks — so the text goes to stdout as JSON `additionalContext`, NOT
# stderr. Stderr from a hook that exits 0 reaches the debug log only and Claude never
# sees it, which made this hook a no-op for the whole audience it was written for.

set -euo pipefail

input=$(cat)
file=$(printf '%s' "$input" | jq -r '.tool_input.file_path // ""' 2>/dev/null || echo "")

case "$file" in
  *.sprout|*.spr) ;;
  *) exit 0 ;;
esac

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
doc="$script_dir/../docs/guidelines.md"

text=$({
  echo "About to edit Sprout source. Recheck docs/guidelines.md:"
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
  echo "Comments: brief and local (style-guide-v0 §11) — intent and invariants, not mechanism."
})

printf '%s' "$text" \
  | jq -Rs '{hookSpecificOutput:{hookEventName:"PreToolUse",additionalContext:.}}'

exit 0
