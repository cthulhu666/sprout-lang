#!/usr/bin/env bash
# After whole-program linking, an inlined CPR `_unboxed` read must carry its `Just`/`Nothing`
# tags as constants: no call to @find_ctor_tag_by_name and no load of @g_tag_just or
# @g_tag_nothing in the caller. A lazy tag lookup in every inlined copy bloats the caller past
# LLVM's inline threshold and slows hot loops. Also fails when a read is NOT inlined, since
# the check above would then prove nothing. See docs/gates.md §CPR tag fold.
#
# usage: cpr_tag_fold_check.sh <emitted.ll>
set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "usage: $0 <emitted.ll>" >&2
  exit 2
fi
LL="$1"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

TMPD="$(mktemp -d "${TMPDIR:-/tmp}/sprout_tagfold_XXXXXX")"
trap 'rm -rf "$TMPD"' EXIT

bash "$ROOT/scripts/link_whole_program.sh" "$LL" "$TMPD/opt.ll" -S -emit-llvm

# One line per finding: "<fn>|<what>". Functions are the fixture's `@main.*` defines,
# `@main` itself excluded.
perl -ne '
  if (/^define\b.*?@("?)(main\.[^"(\s]*)\1\(/) { $fn = $2; $seen{$fn} = 1; next; }
  if (/^\}/) { $fn = ""; next; }
  next unless $fn ne "";
  print "$fn|find_ctor_tag_by_name\n" if /call\b.*\@find_ctor_tag_by_name\b/;
  print "$fn|$1\n" if /\@(g_tag_just|g_tag_nothing)\b/;
  print "$fn|VACUOUS:$1\n" if /call\b.*\@(vector_get_unboxed|bytes_get_unboxed)\b/;
  END { print "COUNT|" . scalar(keys %seen) . "\n"; }
' "$TMPD/opt.ll" > "$TMPD/findings"

nfn="$(sed -n 's/^COUNT|//p' "$TMPD/findings")"
if [ "${nfn:-0}" -eq 0 ]; then
  echo "cpr-tag-fold-check: no @main.* functions in the linked IR; the pattern is stale" >&2
  exit 1
fi

failed=0
while IFS="|" read -r fn what; do
  [ "$fn" = "COUNT" ] && continue
  case "$what" in
    VACUOUS:*)
      echo "cpr-tag-fold-check: $fn still calls @${what#VACUOUS:}; the read was not inlined" >&2 ;;
    find_ctor_tag_by_name)
      echo "cpr-tag-fold-check: $fn calls @find_ctor_tag_by_name; the tag is not a constant" >&2 ;;
    *)
      echo "cpr-tag-fold-check: $fn reads @$what; the tag is not a constant" >&2 ;;
  esac
  failed=$((failed + 1))
done < <(sort -u "$TMPD/findings")

if (( failed > 0 )); then
  echo "cpr-tag-fold-check: $failed finding(s) in $nfn function(s)" >&2
  exit 1
fi
echo "cpr-tag-fold-check: $nfn functions inlined with constant tags"
