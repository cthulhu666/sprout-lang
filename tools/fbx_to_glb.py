# Blender headless FBX -> GLB converter.
#
# raylib cannot load FBX, so game-asset packs that ship FBX (e.g. Kenney's
# animated-characters-retro) must be converted to a raylib-readable format. GLB
# preserves the mesh AND the skeleton, which skeletal animation (M4) needs.
#
# Usage (see tools/convert_kenney.sh for the wrapper):
#   blender --background --python tools/fbx_to_glb.py -- <input.fbx> <output.glb>
import bpy
import sys

argv = sys.argv[sys.argv.index("--") + 1:]
src, dst = argv[0], argv[1]

# Start from an empty scene so nothing from Blender's default file leaks in.
bpy.ops.wm.read_factory_settings(use_empty=True)

bpy.ops.import_scene.fbx(filepath=src)

# Self-contained binary glTF: mesh + skeleton, Y-up (glTF/raylib convention).
bpy.ops.export_scene.gltf(
    filepath=dst,
    export_format='GLB',
    export_yup=True,
)
print("WROTE_GLB:", dst)
