#!/usr/bin/env bash
# After whole-program linking, a constructor tag the runtime names must be a constant, not a
# lazy name lookup. Three checks on the optimised module:
#   a. each fixture `@main.*` function (an inlined CPR `_unboxed` read) calls neither
#      @find_ctor_tag_by_name nor @sprout_ctor_tag_lookup and reads no tag-cache global; it also
#      fails if a read was NOT inlined, since the check would then prove nothing.
#   b. only @sprout_ctor_tag_lookup calls @find_ctor_tag_by_name.
#   c. @sprout_chan_make_got, which builds a ctor the program registers, folds its tag too.
# See docs/gates.md §Constructor tag fold.
#
# usage: ctor_tag_fold_check.sh <emitted.ll>
set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "usage: $0 <emitted.ll>" >&2
  exit 2
fi
LL="$1"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MAX_LISTED=15

TMPD="$(mktemp -d "${TMPDIR:-/tmp}/sprout_tagfold_XXXXXX")"
trap 'rm -rf "$TMPD"' EXIT

bash "$ROOT/scripts/link_whole_program.sh" "$LL" "$TMPD/opt.ll" -S -emit-llvm

# One line per finding: "<check>|<fn>|<what>". The fixture's functions are the `@main.*`
# defines, `@main` itself excluded. Cache globals: the old @g_tag_* pair and any
# `sprout_tag_cache` name.
perl -ne '
  if (/^define\b.*?@("?)([^"(\s]+)\1\(/) {
    $fn = $2; $fixture = $fn =~ /^main\./; $seen{$fn} = 1; $nfix++ if $fixture; next;
  }
  if (/^\}/) { $fn = ""; $fixture = 0; next; }
  next unless $fn ne "";
  $call = /call\b.*\@(find_ctor_tag_by_name|sprout_ctor_tag_lookup)\b/ ? $1 : "";
  ($cache) = /\@"?(g_tag_just|g_tag_nothing|[\w.\$-]*sprout_tag_cache[\w.\$-]*)/;
  if ($fixture) {
    print "a|$fn|$call\n" if $call ne "";
    print "a|$fn|$cache\n" if defined $cache;
    print "a|$fn|VACUOUS:$1\n" if /call\b.*\@(vector_get_unboxed|bytes_get_unboxed)\b/;
  }
  print "b|$fn|find_ctor_tag_by_name\n"
    if $fn ne "sprout_ctor_tag_lookup" && /call\b.*\@find_ctor_tag_by_name\b/;
  if ($fn eq "sprout_chan_make_got") {
    print "c|$fn|$call\n" if $call ne "";
    print "c|$fn|$cache\n" if defined $cache;
  }
  END {
    print "COUNT|" . ($nfix + 0) . "|\n";
    print "NOGOT|\n" unless $seen{"sprout_chan_make_got"};
  }
' "$TMPD/opt.ll" > "$TMPD/findings"

nfn="$(sed -n 's/^COUNT|\([0-9]*\)|$/\1/p' "$TMPD/findings")"
if [ "${nfn:-0}" -eq 0 ]; then
  echo "ctor-tag-fold-check: no @main.* functions in the linked IR; the pattern is stale" >&2
  exit 1
fi
if grep -q '^NOGOT|' "$TMPD/findings"; then
  echo "ctor-tag-fold-check: no @sprout_chan_make_got in the linked IR; the pattern is stale" >&2
  exit 1
fi

failed=0
while IFS="|" read -r check fn what; do
  case "$check" in COUNT | NOGOT) continue ;; esac
  case "$check:$what" in
    a:VACUOUS:*)
      echo "ctor-tag-fold-check: $fn still calls @${what#VACUOUS:}; the read was not inlined" >&2 ;;
    a:find_ctor_tag_by_name | a:sprout_ctor_tag_lookup | c:find_ctor_tag_by_name | c:sprout_ctor_tag_lookup)
      echo "ctor-tag-fold-check: $fn calls @$what; the tag is not a constant" >&2 ;;
    a:* | c:*)
      echo "ctor-tag-fold-check: $fn reads @$what; the tag is not a constant" >&2 ;;
    b:*) continue ;; # listed below, capped
  esac
  [ "$check" = b ] || failed=$((failed + 1))
done < <(sort -u "$TMPD/findings")

# Check b: every other caller of the cold lookup, one per line, capped.
nb="$(grep -c '^b|' "$TMPD/findings" || true)"
if (( nb > 0 )); then
  grep '^b|' "$TMPD/findings" | sort -u | head -n "$MAX_LISTED" | while IFS="|" read -r _ fn _; do
    echo "ctor-tag-fold-check: $fn calls @find_ctor_tag_by_name; only @sprout_ctor_tag_lookup may" >&2
  done
  (( nb > MAX_LISTED )) && echo "ctor-tag-fold-check: ... and $((nb - MAX_LISTED)) more" >&2
  failed=$((failed + nb))
fi

if (( failed > 0 )); then
  echo "ctor-tag-fold-check: $failed finding(s)" >&2
  exit 1
fi
echo "ctor-tag-fold-check: $nfn functions inlined with constant tags; no stray name lookups"
