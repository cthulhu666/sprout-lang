#!/usr/bin/env bash
# Every `declare … @<name>_unboxed(…)` in emitted IR must spell the same return and
# parameter types as clang's `define` of that C function on this host. The registers
# agree even when the types do not, so a mismatch passes every test — but LLVM will
# not inline a call whose type differs from the callee's, so whole-program linking
# (scripts/link_whole_program.sh) silently keeps the call. CI runs this on x86_64
# and contributors on arm64, which between them cover both ways clang lowers a
# two-word return. See docs/gates.md §CPR declares.
#
# usage: cpr_declare_check.sh <emitted.ll> <runtime.c>
set -euo pipefail

if [ "$#" -ne 2 ]; then
  echo "usage: $0 <emitted.ll> <runtime.c>" >&2
  exit 2
fi
LL="$1"; SRC="$2"

TMPD="$(mktemp -d "${TMPDIR:-/tmp}/sprout_cprdecl_XXXXXX")"
trap 'rm -rf "$TMPD"' EXIT

clang -O2 -S -emit-llvm "$SRC" -o "$TMPD/rt.ll"

# "<name>|<ret>|<param,param…>" per function, attributes and value names dropped.
sig() {
  perl -ne '
    next unless /^(declare|define)\s+(.*?)\s*@(\w+_unboxed)\((.*?)\)/;
    my ($ret, $name, $params) = ($2, $3, $4);
    $ret =~ s/\brange\([^)]*\)//g;
    $ret =~ s/\b(dso_local|local_unnamed_addr|unnamed_addr|hidden|internal|noundef|signext|zeroext)\b//g;
    $ret =~ s/^\s+|\s+$//g;
    my @p = map { (split " ", $_)[0] } grep { /\S/ } split /,/, $params;
    print "$name|$ret|", join(",", @p), "\n";
  ' "$1" | sort
}

sig "$LL" > "$TMPD/declared"
sig "$TMPD/rt.ll" > "$TMPD/defined"

if [ ! -s "$TMPD/declared" ]; then
  echo "cpr-declare-check: no _unboxed declares in $LL — the pattern is stale" >&2
  exit 1
fi

failed=0
while IFS="|" read -r name ret params; do
  want="$(awk -F"|" -v n="$name" '$1 == n { print $2 "|" $3 }' "$TMPD/defined")"
  if [ -z "$want" ]; then
    echo "cpr-declare-check: @$name is declared but $SRC does not define it" >&2
    failed=$((failed + 1))
  elif [ "$want" != "$ret|$params" ]; then
    echo "cpr-declare-check: @$name declared $ret($params), clang defines ${want%%|*}(${want#*|})" >&2
    failed=$((failed + 1))
  fi
done < "$TMPD/declared"

if (( failed > 0 )); then
  echo "cpr-declare-check: $failed mismatch(es)" >&2
  exit 1
fi
echo "cpr-declare-check: $(wc -l < "$TMPD/declared" | tr -d ' ') declares match"
