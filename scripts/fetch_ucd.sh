#!/usr/bin/env bash
# Fetch the pinned Unicode Character Database files that stdlib/unicode is
# generated from, and verify each against its SHA-256.
#
# The UCD is not vendored (docs/stdlib-unicode-v0.md §3.5): it is ~10 MB of
# input for 21 KB of output, and the generated tables plus the generated
# conformance suite are what the repo actually needs to be reproducible. The
# checksums below are what make "regenerate" mean the same thing next year as
# it does today -- unicode.org serves versioned paths, but a checksum is proof
# rather than a promise.
#
# Usage: scripts/fetch_ucd.sh [dest-dir]     (default build/ucd)
set -euo pipefail

VER=17.0.0
DEST="${1:-build/ucd}"
mkdir -p "$DEST"

# sha256  remote-path-under-ucd/
FILES=(
  "ea7ce50f3444a050333448dffef1cadd9325af55cbb764b4a2280faf52170a33  EastAsianWidth.txt"
  "24c7fed1195c482faaefd5c1e7eb821c5ee1fb6de07ecdbaa64b56a99da22c08  DerivedCoreProperties.txt"
  "d62e5bab70ca74f099343f71224fa051cb1fdd61a1ab45c0488c44cfc0b6102e  extracted/DerivedGeneralCategory.txt"
  "d6b51d1d2ae5c33b451b7ed994b48f1f4dc62b2272a5831e7fd418514a6bae89  auxiliary/GraphemeBreakProperty.txt"
  "e2d134d2c52919bace503ebb6a551c1855fe1a1faec18478c78fff254a1793ec  auxiliary/GraphemeBreakTest.txt"
  "2cb2bb9455cda83e8481541ecf5b6dfda66a3bb89efa3fa7c5297eccf607b72b  emoji/emoji-data.txt"
)

sha256() {
  if command -v shasum >/dev/null 2>&1; then shasum -a 256 "$1" | cut -d' ' -f1
  else sha256sum "$1" | cut -d' ' -f1
  fi
}

for entry in "${FILES[@]}"; do
  want="${entry%% *}"
  rel="${entry##* }"
  name="$(basename "$rel")"
  out="$DEST/$name"
  if [[ -f "$out" && "$(sha256 "$out")" == "$want" ]]; then
    printf '%-32s cached\n' "$name"
    continue
  fi
  curl -fsS --max-time 120 -o "$out" "https://www.unicode.org/Public/$VER/ucd/$rel"
  got="$(sha256 "$out")"
  if [[ "$got" != "$want" ]]; then
    echo "ERROR: $name checksum mismatch" >&2
    echo "  expected $want" >&2
    echo "  got      $got" >&2
    echo "  (a new Unicode release needs the pins here and the version in" >&2
    echo "   docs/stdlib-unicode-v0.md updated together)" >&2
    rm -f "$out"
    exit 1
  fi
  printf '%-32s fetched\n' "$name"
done

echo "==> Unicode $VER inputs verified in $DEST/"
