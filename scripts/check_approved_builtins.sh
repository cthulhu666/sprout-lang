#!/usr/bin/env bash
# check_approved_builtins.sh — runtime/APPROVED_BUILTINS lists exactly the runtime functions
# Sprout can call.
#
#   check_approved_builtins.sh --allowlist FILE --ir FILE [--extern-root DIR]...
#                              [--exclude DIR]... -- RUNTIME.c...
#
# builtins = (Sprout side) ∩ (runtime side)
#   Sprout side:  every `extern fn` name under the --extern-root dirs (*.sprout, *.spr; paths
#                 under an --exclude dir skipped), plus every `declare` name in the --ir file
#                 (the header the compiler emits).
#   runtime side: global text symbols of the RUNTIME.c files compiled with `clang -c`.
# The intersection drops compiler intrinsics (no runtime symbol) and cross-TU internals
# (never named from Sprout). The allowlist must equal it: a missing name and a stale name both
# fail. docs/string-header-invariant-v0.md §7 step 6.

set -euo pipefail

allowlist="" ir="" roots=() excludes=() srcs=()
while (($#)); do
  case "$1" in
    --allowlist) allowlist=$2; shift 2 ;;
    --ir) ir=$2; shift 2 ;;
    --extern-root) roots+=("$2"); shift 2 ;;
    --exclude) excludes+=("$2"); shift 2 ;;
    --) shift; srcs=("$@"); break ;;
    *) echo "check-approved-builtins: unknown argument $1" >&2; exit 2 ;;
  esac
done
if [[ -z "$allowlist" || -z "$ir" || ${#srcs[@]} -eq 0 ]]; then
  echo "check-approved-builtins: need --allowlist, --ir and runtime sources" >&2; exit 2
fi
for f in "$allowlist" "$ir"; do
  [[ -f "$f" ]] || { echo "check-approved-builtins: missing $f" >&2; exit 1; }
done

CLANG="${CLANG:-clang}"
TMPD=$(mktemp -d "${TMPDIR:-/tmp}/sprout_approved_XXXXXX")
trap 'rm -rf "$TMPD"' EXIT

# Sprout side.
find_args=()
for d in ${excludes[@]+"${excludes[@]}"}; do find_args+=(-not -path "$d/*"); done
if ((${#roots[@]})); then
  find "${roots[@]}" \( -name '*.sprout' -o -name '*.spr' \) ${find_args[@]+"${find_args[@]}"} -print0 \
    | xargs -0 perl -ne '
        print "$1\n" if /^\s*(?:export\s+)?extern\s+fn\s+([A-Za-z_][A-Za-z0-9_]*)/;
      ' > "$TMPD/sprout_extern.txt"
else
  : > "$TMPD/sprout_extern.txt"
fi
sed -n -E 's/^declare [^@]*@([A-Za-z0-9_.]+)\(.*/\1/p' "$ir" > "$TMPD/sprout_declare.txt"
sort -u "$TMPD/sprout_extern.txt" "$TMPD/sprout_declare.txt" > "$TMPD/sprout.txt"

# Runtime side. Mach-O prefixes every C symbol with `_`.
strip_us=0
[[ "$(uname -s)" == Darwin ]] && strip_us=1
: > "$TMPD/runtime_raw.txt"
i=0
for src in "${srcs[@]}"; do
  i=$((i + 1))
  obj="$TMPD/rt$i.o"
  if ! "$CLANG" -c "$src" -O0 -o "$obj" 2> "$TMPD/cc.err"; then
    echo "check-approved-builtins: runtime compile failed ($src)" >&2
    cat "$TMPD/cc.err" >&2
    exit 1
  fi
  nm -g "$obj" | awk 'NF == 3 && $2 == "T" { print $3 }' >> "$TMPD/runtime_raw.txt"
done
if ((strip_us)); then sed -E 's/^_//' "$TMPD/runtime_raw.txt" | sort -u > "$TMPD/runtime.txt"
else sort -u "$TMPD/runtime_raw.txt" > "$TMPD/runtime.txt"; fi

comm -12 "$TMPD/sprout.txt" "$TMPD/runtime.txt" > "$TMPD/builtins.txt"
sed -E 's/#.*$//; s/^[[:space:]]+|[[:space:]]+$//g' "$allowlist" | grep -v '^$' | sort -u > "$TMPD/approved.txt" || true

missing=$(comm -23 "$TMPD/builtins.txt" "$TMPD/approved.txt")
stale=$(comm -13 "$TMPD/builtins.txt" "$TMPD/approved.txt")
rc=0
if [[ -n "$missing" ]]; then
  echo "check-approved-builtins: runtime functions Sprout can call, missing from $allowlist:" >&2
  while IFS= read -r n; do echo "  $n" >&2; done <<<"$missing"
  echo >&2
  echo "  Per AGENTS.md 'Builtin vs Stdlib' rules 4-6: add each name to" >&2
  echo "  $allowlist with an inline comment explaining why it cannot be" >&2
  echo "  done in Sprout." >&2
  rc=1
fi
if [[ -n "$stale" ]]; then
  echo "check-approved-builtins: entries in $allowlist that are not builtins" >&2
  echo "  (no runtime definition, or no extern fn / compiler declare names them):" >&2
  while IFS= read -r n; do echo "  $n" >&2; done <<<"$stale"
  echo >&2
  echo "  Remove them." >&2
  rc=1
fi
((rc == 0)) || exit 1

echo "==> check-approved-builtins ✓"
