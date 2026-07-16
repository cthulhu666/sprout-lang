#!/usr/bin/env bash
# PreToolUse hook: surface code authoring guidelines when an agent is about to
# edit Sprout source. Reads the tool input JSON on stdin, checks the file path,
# and prints a short reminder of the six basics if the target is .sprout / .spr.
# Always exits 0 — this hook informs, never blocks.

set -euo pipefail

input=$(cat)
file=$(printf '%s' "$input" | jq -r '.tool_input.file_path // ""' 2>/dev/null || echo "")

case "$file" in
  *.sprout|*.spr)
    cat >&2 <<'EOF'
[guidelines] About to edit Sprout source. Recheck docs/guidelines.md:
  1. Functional core, IO at edges  (don't widen !{IO} unnecessarily)
  2. Total over partial            (no panics; return Maybe/Result)
  3. Illegal states unrepresentable (ADTs, no boolean blindness)
  4. Parse, don't validate         (transform raw input into structured types at boundaries)
  5. Errors carry a Span from inception
  6. Data-last argument order      (collection/receiver in the last parameter slot)
For idiomatic shapes (let..else, combinators, pipes, wrap): docs/idiomatic-sprout.md
EOF
    ;;
esac

exit 0
