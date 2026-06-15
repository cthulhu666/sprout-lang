#!/usr/bin/env bash
# Validates tests/IR_XFAIL format and content.
#
# Format: one path per line, optional `# reason` after the path.
# Lines starting with `#` are comments. Blank lines ignored.
#
# Checks:
#   1. File exists.
#   2. Every entry path exists on disk (no stale entries).
#   3. No duplicate path entries.
#   4. Every path is under tests/ or examples/.
#
# Used by: just check-ir-xfail-format, the test-ir CI job.

set -euo pipefail

XFAIL="${1:-tests/IR_XFAIL}"

if [[ ! -f "$XFAIL" ]]; then
  echo "check-ir-xfail: $XFAIL not found" >&2
  exit 1
fi

# Strip comments + blanks, keep only the path (first whitespace-delimited token).
paths=$(sed -E 's/#.*$//; s/^[[:space:]]+|[[:space:]]+$//g' "$XFAIL" \
          | grep -v '^$' \
          | awk '{print $1}')

if [[ -z "$paths" ]]; then
  echo "check-ir-xfail: $XFAIL is empty (no entries)" >&2
  exit 1
fi

errors=0

# Duplicate detection.
dups=$(printf '%s\n' "$paths" | sort | uniq -d || true)
if [[ -n "$dups" ]]; then
  echo "check-ir-xfail: duplicate path entries:" >&2
  printf '  %s\n' $dups >&2
  errors=$((errors + 1))
fi

# Existence + prefix check.
while IFS= read -r p; do
  [[ -z "$p" ]] && continue
  case "$p" in
    tests/*|examples/*) ;;
    *)
      echo "check-ir-xfail: path not under tests/ or examples/: $p" >&2
      errors=$((errors + 1))
      ;;
  esac
  if [[ ! -f "$p" ]]; then
    echo "check-ir-xfail: stale entry (file does not exist): $p" >&2
    errors=$((errors + 1))
  fi
done <<< "$paths"

if (( errors > 0 )); then
  echo "check-ir-xfail: $errors error(s) in $XFAIL" >&2
  exit 1
fi

count=$(echo "$paths" | wc -l | tr -d ' ')
echo "==> check-ir-xfail ✓ — $count entry/entries in $XFAIL"
