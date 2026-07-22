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
skin = argv[2] if len(argv) > 2 else None  # optional diffuse texture to bake in

# Start from an empty scene so nothing from Blender's default file leaks in.
bpy.ops.wm.read_factory_settings(use_empty=True)

bpy.ops.import_scene.fbx(filepath=src)

# Optionally wire a skin texture into every material's Base Color so the GLB is
# self-contained and colored. Nearest filtering keeps the low-res atlas crisp.
if skin is not None:
    img = bpy.data.images.load(skin)
    for mat in bpy.data.materials:
        mat.use_nodes = True
        nt = mat.node_tree
        bsdf = nt.nodes.get("Principled BSDF")
        if bsdf is None:
            continue
        tex = nt.nodes.new("ShaderNodeTexImage")
        tex.image = img
        tex.interpolation = 'Closest'
        nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
        # Reset base color factor to white so the texture shows unmodulated.
        bsdf.inputs["Base Color"].default_value = (1.0, 1.0, 1.0, 1.0)

# Self-contained binary glTF: mesh + skeleton, Y-up (glTF/raylib convention).
bpy.ops.export_scene.gltf(
    filepath=dst,
    export_format='GLB',
    export_yup=True,
)
print("WROTE_GLB:", dst)
