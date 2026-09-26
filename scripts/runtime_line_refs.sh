#!/usr/bin/env bash
# Reject `sprout_runtime.c:1234`-style citations of the C runtime.
#
# The runtime is append-mostly, so every line number in a doc drifts in one
# direction and never back: when this gate landed, 15 of the 25 refs that named
# a nearby identifier were already wrong, by 14 to 75 lines, and 22 more named
# no identifier at all -- unverifiable by a gate OR a reader, since there was
# nothing to search for.  Cite the identifier instead; grep finds it wherever it
# moved.
#
# Scope is the C runtime only, and only a citation that names the file.  A bare
# continuation ref -- `:1052` a clause after a real one -- is the same defect but
# cannot be matched without also matching times, ports and version numbers; the
# sweep removed the ones beside a named ref, and thirteen survive in
# `docs/gc-header-rewrite-handoff-2026-07-03.md`, which describes a layout that no
# longer exists and has no identifier left to name.  `.sprout` line refs are far
# more numerous (hundreds) and are not covered.
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

PATTERN='sprout_(runtime|poll|scheduler)\.(c|h):[0-9]+'

# `docs/archive/` is exempt: a retrospective records what was believed when it
# was written, and rewriting its citations would falsify that record.
hits=$(git ls-files -z \
  | grep -zv '^docs/archive/' \
  | grep -zv '^scripts/runtime_line_refs\.sh$' \
  | xargs -0 grep -nEI "$PATTERN" 2>/dev/null || true)

if [ -n "$hits" ]; then
  count=$(printf '%s\n' "$hits" | wc -l | tr -d ' ')
  echo "runtime-line-refs: $count line-number citation(s) of the C runtime" >&2
  echo >&2
  printf '%s\n' "$hits" >&2
  echo >&2
  echo "Cite the identifier, not the line: \`sprout_gc_alloc_block\` in" >&2
  echo "\`runtime/sprout_runtime.c\`, not a line number that drifts." >&2
  exit 1
fi

echo "runtime-line-refs: ok (no line-number citations of the C runtime)"
