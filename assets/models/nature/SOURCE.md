# Nature models — provenance

These `.glb` models are a hand-picked subset of the **Kenney Nature Kit** (v2.1), used by
`examples/gfx/terrain_rivers_demo.sprout` to scatter biome-appropriate vegetation over the
terrain (see `loam/vegetation.sprout` for the biome → `TreeKind` mapping).

- **Source:** Kenney Nature Kit — <https://kenney.nl/assets/nature-kit>
- **License:** Creative Commons Zero (CC0 1.0) — public domain; free for personal, educational,
  and commercial use. Crediting Kenney (www.kenney.nl) is appreciated but not required.
- **Format:** Wavefront **OBJ + MTL** from the kit's `Models/OBJ format/`, loaded by raylib
  (`gfx.load_model` → tinyobjloader). Each `.obj` references its `.mtl` via a relative `mtllib`
  line, so the pair must stay together. Copied verbatim; not modified.
- **Why OBJ and not the kit's GLB:** the kit's `Models/GLTF format/*.glb` are exported by
  UniGLTF (a Unity exporter) and raylib's bundled `cgltf` **fails to parse them** (`Failed to
  load glTF data` → a model with zero meshes). raylib's OBJ path loads them cleanly, and the
  MTL's per-material `Kd` becomes the material `colDiffuse` the tree shader lights. (A Blender
  re-export to GLB would also work, as the character demo does — OBJ just avoids that step.)

Only the variants referenced by the demo are vendored here (the full ~329-model kit is not
checked in). Each maps to a `TreeKind` category:

| TreeKind    | Vendored variants                                   |
|-------------|-----------------------------------------------------|
| Palm        | `tree_palmShort`, `tree_palmTall`                   |
| Broadleaf   | `tree_default`, `tree_oak`, `tree_small`, `tree_detailed` |
| Conifer     | `tree_pineTallA`, `tree_pineSmallA`, `tree_pineDefaultA`  |
| DarkConifer | `tree_pineDefaultB`, `tree_pineSmallB`              |
| Cactus      | `cactus_short`, `cactus_tall`                       |
| Shrub       | `plant_bushLarge`, `plant_bushSmall`                |
