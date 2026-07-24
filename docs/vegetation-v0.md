# Loam vegetation v0 — biome-driven trees

Status: **experimental** (view-layer demo feature; not part of the normative core spec).
Code: `loam/vegetation.sprout`, `tests/loam/test_vegetation.spr`,
`examples/gfx/terrain_rivers_demo.sprout`, shim `graphics/sprout_gfx.c`, binding `stdlib/gfx.sprout`.

## Goal

Scatter trees over the Loam terrain where **the plant type is a function of the tile's biome**
(palms on beaches, cacti in deserts, conifers on mountains, dense broadleaf/conifer in forests),
at a density that reads as a living landscape — and hold 60 FPS on a 256×256 map.

## Model / view split

The decision is a pure, headless classifier so it is unit-testable and reusable without a window:

```
tree_for_biome(k: TileKind, h: Int) -> Maybe TreeKind
```

- `h` is `loam.terrain.spatial_hash(gx, gz, seed)` (wraps `stdlib.rng.rng_hash2`). One hash does
  double duty: `h mod 100` is the **density roll**, `h / 100` selects the **category** where a
  biome blends kinds (e.g. Forest is mostly Broadleaf with some Conifer). `Nothing` = bare tile.
- Input is **`TileKind` only** — the classifier's moisture/temperature fields
  (`loam.terrain.classify`) are consumed at generation time and not persisted, so the biome is the
  only signal available at placement time. That is exactly "tree type depending on biome".
- `TreeKind` (`Palm | Broadleaf | Conifer | DarkConifer | Cactus | Shrub`, `deriving Enum`) is a
  coarse category. Resolving a category to a concrete model is the **view's** job: the demo keys a
  fixed `(base, count)` slice of its loaded-model vector off `tree_kind_tag`, and a second
  decorrelated hash (`spatial_hash(gx, gz, seed + offset)`) picks a variant + jitter/rotation/scale.
  So the same biome yields a varied, organic-looking-but-reproducible stand.

Per-biome densities (`biome_density`, 0..100) are the tunable knobs: Forest 55, Grass 14,
Mountain 12, Beach 8, Tundra 6, Desert 5, Snow 4, Water 0.

## Rendering: GPU instancing (not baking)

Trees are **not** baked into the static terrain mesh. A monolithic mesh cannot be culled or
LOD'd per-tree, cannot sway, and its memory scales with mesh detail — all of which the AAA
roadmap (detailed trees, distance LOD, wind) needs. Instead each tree *type* is drawn once per
frame with `DrawMeshInstanced` under a dedicated instanced shader (`TREE_VS`/`TREE_FS`):

- `gfx_instance_push(handle, x, y, z, angle, scale)` builds a per-instance model matrix
  (`scale · rotateY · translate`) into a per-model host-side buffer, once at setup.
- `gfx_draw_instanced(handle)` issues one `DrawMeshInstanced` per mesh of the model — ~40k trees
  cost a couple dozen draw calls total, holding ~50 FPS.
- The shader lights a world-space normal (`mat3(instanceTransform) · normal` — **not** the
  `matNormal` uniform, which raylib binds from the identity model matrix under instancing) and
  colours from the material `colDiffuse`. A `wind` uniform is reserved for a future vertex sway.

Deferred (see `BACKLOG.md` §9): wind, camera-distance LOD/culling, higher-poly models. All three
are drop-ins on this architecture — wind is a shader uniform; LOD/culling is a per-frame filter
over the host-side instance buffers.

## Assets — OBJ, not GLB

Vendored subset in `assets/models/nature/` (CC0). The kit's `Models/GLTF format/*.glb` are
exported by **UniGLTF** (Unity) and raylib's bundled cgltf **fails to parse them** (a model with
zero meshes). The kit's **OBJ+MTL** load cleanly via tinyobjloader, and each `.mtl`'s per-material
`Kd` becomes the `colDiffuse` the shader lights. See `assets/models/nature/SOURCE.md`.
