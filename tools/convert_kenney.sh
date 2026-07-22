#!/usr/bin/env bash
# Convert the Kenney "animated-characters-retro" character from the pack's FBX to
# a raylib-loadable GLB, written to assets/models/characterMedium.glb.
#
# The Kenney pack itself is NOT vendored (download it from
# https://kenney.nl/assets/animated-characters-retro — CC0). Point this script at
# the extracted pack directory:
#
#   tools/convert_kenney.sh /path/to/kenney_animated-characters-retro
#
# Requires Blender (imports FBX, exports GLB). Override its location with BLENDER;
# the default is the standard macOS app bundle.
set -euo pipefail

PACK_DIR="${1:?usage: convert_kenney.sh <kenney_animated-characters-retro dir>}"
BLENDER="${BLENDER:-/Applications/Blender.app/Contents/MacOS/Blender}"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$PACK_DIR/Model/characterMedium.fbx"
SKIN_NAME="${SKIN:-humanMaleA.png}"           # override with SKIN=zombieMaleA.png etc.
SKIN="$PACK_DIR/Skins/$SKIN_NAME"
OUT_DIR="$HERE/assets/models"
OUT="$OUT_DIR/characterMedium.glb"

[ -f "$SRC" ] || { echo "error: $SRC not found — is PACK_DIR the extracted pack?" >&2; exit 1; }
[ -f "$SKIN" ] || { echo "error: skin $SKIN not found (set SKIN=<name>.png)" >&2; exit 1; }
command -v "$BLENDER" >/dev/null 2>&1 || [ -x "$BLENDER" ] || { echo "error: Blender not found at $BLENDER (set BLENDER=...)" >&2; exit 1; }

mkdir -p "$OUT_DIR"
"$BLENDER" --background --python "$HERE/tools/fbx_to_glb.py" -- "$SRC" "$OUT" "$SKIN"
echo "converted -> $OUT (skin: $SKIN_NAME)"
