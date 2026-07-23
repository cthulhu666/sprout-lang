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
# The textured character model (skin baked into the material).
"$BLENDER" --background --python "$HERE/tools/fbx_to_glb.py" -- "$SRC" "$OUT" "$SKIN"
echo "converted -> $OUT (skin: $SKIN_NAME)"

# Animation-only GLBs: one clip per file, skeleton matching the model so
# gfx.load_animations drives it (no skin needed — the pose is what matters).
# `idle`/`run`/`jump` back examples/gfx/ecs_agents.sprout (idle while resting, run
# while walking, jump for the occasional leap).
for clip in idle run jump; do
  CLIP_SRC="$PACK_DIR/Animations/$clip.fbx"
  CLIP_OUT="$OUT_DIR/character_$clip.glb"
  [ -f "$CLIP_SRC" ] || { echo "error: $CLIP_SRC not found" >&2; exit 1; }
  "$BLENDER" --background --python "$HERE/tools/fbx_to_glb.py" -- "$CLIP_SRC" "$CLIP_OUT"
  echo "converted -> $CLIP_OUT"
done
