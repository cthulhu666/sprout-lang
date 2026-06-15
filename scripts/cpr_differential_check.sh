#!/usr/bin/env bash
# CPR differential check: emit IR via both --emit-ir (direct codegen) and
# --use-ir-codegen (typed IR path) for each corpus file, extract external
# signatures from each, compare the INTERSECTION.
#
# Premise: any function declared by BOTH paths must have the same ABI
# signature. Divergence in the intersection = real parity bug (the kind
# that crashes silently at runtime). Divergence in the set difference
# (functions declared by only one path) is a coverage gap, not an ABI bug —
# reported but does not fail.
#
# Why this exists: GHC's CoreLint and Idris 2's TTC parity check have the
# same role. Cost: one debugging session. Benefit: every PR 2+ regression
# at structural-diff time, not runtime.
#
# Used by: just cpr-differential-check, the test-ir CI job.

set -euo pipefail

STAGE="${STAGE:-build/compile_driver_bin_stage1}"
STDLIB="${SPROUT_STDLIB:-stdlib}"
EXTRACT="scripts/extract_call_signatures.sh"
ALLOWLIST="${CPR_DIFF_ALLOWLIST:-tests/CPR_DIFF_ALLOWLIST}"

if [[ ! -x "$STAGE" ]]; then
  echo "ERROR: $STAGE not found (run: just bootstrap-from-seed)" >&2
  exit 1
fi
if [[ ! -x "$EXTRACT" ]]; then
  echo "ERROR: $EXTRACT not found" >&2
  exit 1
fi

# Corpus: files that compile cleanly under BOTH --emit-ir AND
# --use-ir-codegen. Files added here MUST currently round-trip clean.
# Start small; grows as each M3 PR closes parity gaps.
#
# To add a file: confirm both
#   stage1 --emit-ir          $f produces valid IR
#   stage1 --use-ir-codegen   $f produces valid IR
# then append below.  CI will compare signatures on every PR.
CORPUS=(
  examples/scalar_arithmetic_demo.sprout
)

TMPD=$(mktemp -d "/tmp/sprout_cpr_diff_$$_XXXXXX")
trap 'rm -rf "$TMPD"' EXIT

# Load allowlist (names whose divergences are warnings, not failures).
allowlist_set=""
if [[ -f "$ALLOWLIST" ]]; then
  allowlist_set=$(sed -E 's/#.*$//; s/^[[:space:]]+|[[:space:]]+$//g' "$ALLOWLIST" \
                    | grep -v '^$' \
                    | awk '{print $1}')
fi

total=0; ok=0; failed=0; skipped=0
total_intersection=0; total_set_diff=0
total_allowed_warnings=0

for f in "${CORPUS[@]}"; do
  if [[ ! -f "$f" ]]; then
    echo "SKIP (missing): $f"; skipped=$((skipped + 1)); continue
  fi
  total=$((total + 1))
  base=$(basename "$f")
  direct_ll="$TMPD/$base.direct.ll"
  typed_ll="$TMPD/$base.typed.ll"

  # Emit via both paths. Suppress stderr — failures show in stdout markers.
  "$STAGE" --emit-ir "$STDLIB" "$f" > "$direct_ll" 2>/dev/null || true
  if [[ ! -s "$direct_ll" ]] || grep -qE '(^|: )ERROR:' "$direct_ll"; then
    echo "SKIP (--emit-ir failed for $f — fix direct path first)"
    skipped=$((skipped + 1)); continue
  fi

  "$STAGE" --use-ir-codegen "$STDLIB" "$f" > "$typed_ll" 2>/dev/null || true
  if [[ ! -s "$typed_ll" ]] || grep -qE '(^|: )ERROR:' "$typed_ll"; then
    echo "SKIP (--use-ir-codegen failed for $f — file may not be in shared-OK corpus yet)"
    skipped=$((skipped + 1)); continue
  fi

  direct_sig="$TMPD/$base.direct.sig"
  typed_sig="$TMPD/$base.typed.sig"
  bash "$EXTRACT" "$direct_ll" > "$direct_sig"
  bash "$EXTRACT" "$typed_ll" > "$typed_sig"

  # Extract the function-name set from each: every @<name>.
  direct_names="$TMPD/$base.direct.names"
  typed_names="$TMPD/$base.typed.names"
  grep -oE '@[a-zA-Z_][a-zA-Z0-9_.]*' "$direct_sig" | LC_ALL=C sort -u > "$direct_names"
  grep -oE '@[a-zA-Z_][a-zA-Z0-9_.]*' "$typed_sig"  | LC_ALL=C sort -u > "$typed_names"

  # Intersection: names appearing in BOTH paths.  Disagreements here are real.
  intersection="$TMPD/$base.intersection"
  LC_ALL=C comm -12 "$direct_names" "$typed_names" > "$intersection"
  intersect_count=$(wc -l < "$intersection" | tr -d ' ')

  # For each name in the intersection, compare the corresponding signature
  # lines. (A name may appear in multiple sig lines, e.g. both declare and
  # define; we compare the full set per-name.)
  divergences="$TMPD/$base.divergences"
  warnings="$TMPD/$base.warnings"
  : > "$divergences"
  : > "$warnings"
  file_divergences=0
  file_warnings=0
  while IFS= read -r name; do
    [[ -z "$name" ]] && continue
    # POSIX-portable bracket ordering: `]` immediately after `[` is literal,
    # and trailing `[` is literal too.  The previous pattern `[.\\^$*+?()[\]{}|]`
    # parsed correctly on BSD sed (macOS) but GNU sed (Linux/CI) interpreted
    # the inner `\]` as terminating the class, leaving `{}` outside as an
    # empty quantifier → "sed: Invalid content of \{\}" error.  Reproduced
    # via the dev box (Debian + LLVM-16); fix verified there.
    esc=$(printf '%s\n' "$name" | sed -E 's/[].\\^$*+?(){}|[]/\\&/g')
    direct_lines=$(grep -E "(^| )${esc}( |\\(|$)" "$direct_sig" | LC_ALL=C sort)
    typed_lines=$(grep -E "(^| )${esc}( |\\(|$)" "$typed_sig" | LC_ALL=C sort)
    if [[ "$direct_lines" != "$typed_lines" ]]; then
      is_allowed=0
      for al in $allowlist_set; do [[ "$name" == "$al" ]] && is_allowed=1 && break; done
      if (( is_allowed == 1 )); then
        file_warnings=$((file_warnings + 1))
        {
          echo "  WARN (allowlisted) divergence on $name:"
          echo "    direct:"
          printf '%s\n' "$direct_lines" | sed 's/^/      /'
          echo "    typed:"
          printf '%s\n' "$typed_lines"  | sed 's/^/      /'
        } >> "$warnings"
      else
        file_divergences=$((file_divergences + 1))
        {
          echo "  DIVERGENCE on $name:"
          echo "    direct:"
          printf '%s\n' "$direct_lines" | sed 's/^/      /'
          echo "    typed:"
          printf '%s\n' "$typed_lines"  | sed 's/^/      /'
        } >> "$divergences"
      fi
    fi
  done < "$intersection"

  # Set differences (informational only — coverage gaps, not ABI bugs).
  only_direct="$TMPD/$base.only_direct"
  only_typed="$TMPD/$base.only_typed"
  LC_ALL=C comm -23 "$direct_names" "$typed_names" > "$only_direct"
  LC_ALL=C comm -13 "$direct_names" "$typed_names" > "$only_typed"
  only_direct_count=$(wc -l < "$only_direct" | tr -d ' ')
  only_typed_count=$(wc -l < "$only_typed" | tr -d ' ')

  total_intersection=$((total_intersection + intersect_count))
  total_set_diff=$((total_set_diff + only_direct_count + only_typed_count))

  total_allowed_warnings=$((total_allowed_warnings + file_warnings))

  if (( file_divergences > 0 )); then
    failed=$((failed + 1))
    echo "  DIFF  $f — $file_divergences unallowed divergence(s) in intersection of $intersect_count names (+$file_warnings allowlisted)"
    cat "$divergences"
    if [[ -s "$warnings" ]]; then cat "$warnings"; fi
    if (( only_direct_count > 0 )); then
      echo "    (info) $only_direct_count name(s) only in --emit-ir, sample:"
      head -3 "$only_direct" | sed 's/^/      /'
    fi
    if (( only_typed_count > 0 )); then
      echo "    (info) $only_typed_count name(s) only in --use-ir-codegen, sample:"
      head -3 "$only_typed" | sed 's/^/      /'
    fi
  else
    ok=$((ok + 1))
    if (( file_warnings > 0 )); then
      echo "  OK    $f — intersection of $intersect_count names matches ($file_warnings allowlisted warning(s); coverage gap: $only_direct_count direct-only, $only_typed_count typed-only)"
      cat "$warnings"
    else
      echo "  OK    $f — intersection of $intersect_count names matches (coverage gap: $only_direct_count direct-only, $only_typed_count typed-only)"
    fi
  fi
done

echo ""
printf '==> cpr-differential-check: %d total, %d OK, %d DIFF, %d skipped\n' \
  "$total" "$ok" "$failed" "$skipped"
printf '    intersection size: %d, coverage gap: %d, allowlisted warnings: %d\n' \
  "$total_intersection" "$total_set_diff" "$total_allowed_warnings"
if (( failed > 0 )); then
  echo ""
  echo "  Each DIVERGENCE is a candidate CPR/ABI parity bug. Investigate before merging."
  echo "  If benign, add @<name> to $ALLOWLIST with a justification."
  exit 1
fi
if (( total == 0 )); then
  echo "ERROR: no files probed (empty corpus)" >&2
  exit 1
fi
