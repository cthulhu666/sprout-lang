# Smooth terrain mesh (de-Minecraft the terrain-rivers demo) — v0 design

Status: **proposal, awaiting approval**. Scope: `examples/gfx/terrain_rivers_demo.sprout`,
a new `loam/surface.sprout` module + its test, and one new gfx host function in
`graphics/sprout_gfx.c` / `stdlib/gfx.sprout`. No change to language semantics, the spec, or the
runtime (`runtime/sprout_runtime.c`).

This is "rung (c)" of the de-blockifying ladder discussed with the user: continuous heightfield mesh
**plus per-vertex gradient normals**. Rungs (a) more-bands and (b) continuous-mesh/per-face-normals
were considered and rejected as end states — (a) stays faceted and walled, (b) removes the staircase
but reads as low-poly. Only (c) fully stops looking like Minecraft.

## 1. Problem statement

The terrain renders like Minecraft. Two independent causes:

1. **Vertical quantization.** `tile_band_at` → `band_of(elev_at(...), levels)` snaps each tile's
   continuous elevation to one of `elevation_levels` (default 12) integer bands. `bake_tile` draws a
   flat top quad at `band_top_y(band)` and a vertical *step wall* per side down to any lower neighbour
   (`nbr_drop` / `exposed_step_bands`). Result: plateaus + cliffs — a staircase.
2. **Flat per-facet lighting.** Every top quad is axis-aligned and gets one straight-up normal
   `(0,1,0)` (demo line 207). The CUBE shader does real diffuse lighting (`graphics/sprout_gfx.c`,
   `CUBE_FS`, `diff = max(dot(fragNormal, lightDir), 0)`), so each tile shades as one flat patch —
   even a smooth slope reads as a grid of equal-brightness squares with hard edges.

The continuous data already exists and is discarded one step before baking: `elev_at` returns a
`Double` (`loam/terrain.sprout:161`), and its own comment notes drainage must route on "the smooth
field, not the discrete bands." The demo computes it, `band_of`-quantizes it, and throws the
fractional part away.

## 2. Goals / non-goals

**Goals**
- Terrain reads as a smooth, continuously-shaded surface: no vertical staircase, no per-facet
  lighting banding, no interior step walls.
- Rivers still sit in carved valleys; trees still sit *on* the ground; the four views (Main / Relief
  / Flow / Lakes) and the sea plane still work.
- The de-quantization math lives in a **headless, unit-tested** Sprout module so the TDD gate is met
  even though the payoff is visual.
- No regression to the per-chunk bake / frustum-cull / instanced-tree architecture or its startup
  cost. Expected to be *faster*: the top quad stays, all step walls are deleted.

**Non-goals (explicitly out of scope for this change; candidate follow-ups in §10)**
- Normal maps / detail-normal micro-bump, splat/triplanar texturing, hardware tessellation, terrain
  LOD. These are the AAA layers *above* a smooth heightfield; none are needed to stop looking blocky.
- Smoothing the biome-colour boundaries (biome is per-tile; a smooth surface with hard colour seams
  is fine and matches the current palette semantics).
- Changing `fbm` / the noise field, the hydrology routing, or the config schema.

## 3. Prior-art survey (how real terrain renderers avoid blockiness)

The technique choice here is a graphics-rendering one, not a language-semantics one, so this surveys
real-time terrain practice rather than comparable languages. The load-bearing consensus:

| Technique | What it does | Relevance here |
|---|---|---|
| **Heightfield mesh** (regular grid, vertex Y = continuous height sample) | The universal base for non-voxel terrain — never quantize height | This is the core of the change; we already generate the field (`elev_at`). |
| **Per-vertex gradient normals** (central differences / Sobel over the heightmap) | Smooth (Gouraud/interpolated) shading across the surface | The decisive half — positions alone give low-poly facets; smooth normals give smooth shading. |
| **Marching cubes / dual contouring** (Lorensen–Cline 1987; surface nets) | Extract a *smooth* triangle mesh from *voxel* data | Confirms even voxel games (dug/destructible terrain) render smooth — "voxel storage" ≠ "blocky render". Minecraft's blockiness is a deliberate art choice, the exception. |
| Normal/detail maps, splat + triplanar, tessellation, clipmap/CDLOD/Nanite LOD | Add micro-detail, material variety, and scale on *top* of the smooth base | Out of scope (§2 non-goals); listed so the boundary is explicit. |

Takeaway: rung (c) *is* the industry base case. The rest is additive polish.

## 4. High-level implementation overview (approval gate)

Replace the per-tile flat-top-plus-walls bake with a **continuous shared-vertex heightfield** carrying
**per-vertex normals** from the height gradient. Concretely:

1. **Keep a continuous height grid, not integer bands, as the source of truth.**
   Introduce a mutable `heights: MutMatrix Double` of world-Y per tile centre:
   `heights[z][x] = ground_y(elev_at(cfg, x, z))` where
   `ground_y(e) = e * levels * band_step + tile_size*0.5` — the un-`floor`ed `band_top_y` (verified:
   `band_of(e,L) = floor(e*L)`, `fbm ∈ [0,1)`, so this is exactly `band_top_y(band_of(e))` minus the
   floor). The integer `bands` grid is retained **only** as a per-quad datum for the Relief ramp
   (`band_of(elev)`), no longer for positioning.

2. **Carve becomes continuous.** `carve_bands` currently subtracts integer bands; instead subtract
   `carve_depth(tier) * band_step` world-units from `heights` (clamped at the world floor). Same
   valleys, no quantization.

3. **Precompute a corner grid, globally, before per-chunk baking (seam-free).**
   - `corner_y: MutMatrix Double`, size `(span+1)²`: `corner_y[i][j]` = average of the up-to-4 tile
     centres touching that grid corner. Adjacent tiles read the same corner from the same cells ⇒ C0
     continuous, seam-free across chunk boundaries (same reason the current neighbour lookups are).
   - `corner_nx/ny/nz: MutMatrix Double` (three grids, or one flattened `MutVec`): the unit surface
     normal at each corner from **central differences** of `corner_y`:
     `n ∝ ( yWest - yEast , 2*tile_size , yNorth - ySouth )`, normalized
     (heightfield normal `(-∂h/∂x, 1, -∂h/∂z)` scaled by `2*tile_size`; +x east, +z south).
     Flat field → `(0,1,0)`; a downhill-east slope → tilts west. This is the unit under test (§9).

4. **`bake_tile` emits ONE quad, four corner heights + four corner normals, and DELETES the walls.**
   Corners `(x0,z0),(x0,z1),(x1,z1),(x1,z0)` take their Y from `corner_y` and their normal from the
   corner-normal grid. The `dn/ds/de/dw` step-wall quads and `nbr_drop`/`exposed_step_bands` usage are
   removed entirely — walls existed only to bridge quantization. (Perimeter: skirt handling in §5.)

5. **New gfx host function `capture_quad_data_vn`** (per-vertex normals). See §6.

6. **Trees** place on continuous ground: `wy = tile-centre height` from the `heights` grid (or the
   mean of its 4 corners), replacing `band * band_step + tile_size*0.5`.

7. **Lakes / sea** float on the continuous mapping: lake quad Y from `ground_y`-of-filled-level;
   `water_y` from `ground_y(sea_level)` instead of `sea_level_band`. Flat quads, unchanged otherwise.

New module **`loam/surface.sprout`** holds the pure helpers (`ground_y`, corner averaging, gradient
normal) so they are headless-testable; the demo keeps only the bake wiring.

## 5. Syntax & semantics impact

None to the language. Demo/stdlib-level only:

- **Perimeter.** Today off-map neighbours drop the wall to `y=0`, reading the map edge as a solid
  slab. With walls gone, the continuous sheet would show its underside at the rim. Fix: at the map
  boundary, emit a **skirt** — one downward quad per edge tile from the rim corner heights to `y=0`
  (a deliberate, small wall set only at `span` edges, not per interior tile). Keeps the "solid slab"
  read without reintroducing interior stepping.
- **Corner averaging at edges** clamps to in-range cells (a corner on the boundary averages the 2–3
  cells that exist).

## 6. Type-system / host-ABI impact — the one new host function

`gfx_capture_quad_data` (`graphics/sprout_gfx.c`) takes **one** normal per quad and writes it to all
four corners via `cap_quad`. The capture buffer *already stores a normal per vertex* (`g_cap_norms`,
`cap_vertex`), so the storage exists — only the entry point forces one normal.

**Proposed new builtin (requires user sign-off per AGENTS.md Collaboration Rule 6):**

```
extern fn gfx_capture_quad_data_vn(
  p0x,p0y,p0z, p1x,p1y,p1z, p2x,p2y,p2z, p3x,p3y,p3z : Double,   -- 4 corner positions
  n0x,n0y,n0z, n1x,n1y,n1z, n2x,n2y,n2z, n3x,n3y,n3z : Double,   -- 4 per-corner normals
  tag, tier, dir, band, lake : Int) -> Unit !{IO}
```

C side: a `cap_quad_vn` variant that passes each corner its own normal (a 4-line edit; the existing
`cap_quad`, `cap_cube`, and the flat `gfx_capture_quad_data` are untouched, so `spinning_cube` /
`terrain_demo` and every other captured-mesh demo are byte-for-byte unchanged). Data packing
(`r=tag,g=tier,b=dir,a=band|lake`) is reused verbatim.

**Why a new host function and not pure Sprout:** the mesh capture buffer is host-owned C state; there
is no Sprout-side vertex-normal channel to write. This is an extension of an existing
capability (baking a quad), not a new capability — but it is still a new host entry point, hence the
explicit approval call-out. It lives in the **gfx shim** (`graphics/sprout_gfx.c`), not
`runtime/sprout_runtime.c`, so it is **not** an `APPROVED_BUILTINS` entry (that gate is runtime-only,
DoD #10); it *is* a new prelude/gfx `extern fn`, so it adds one `declare` line to the bootstrap seed
— refresh via full `just refresh-seed` per the AGENTS.md seed caveat, not the ack bypass.

**Alternative rejected:** compute normals in-shader via `dFdx/dFdz`. A flat triangle's screen-space
derivatives are constant, so this yields face-flat normals — it cannot produce smooth shading. Gradient
normals must be computed from neighbouring *heights*, which the shader does not have. Rejected.

## 7. Error-message impact

None. No new diagnostics; no parser/typechecker surface touched.

## 8. Compatibility / migration

- Behavioural change is confined to the one demo's appearance. No other example imports the removed
  `nbr_drop`/wall path (it is demo-local).
- `exposed_step_bands` in `loam/terrain.sprout` becomes unused by this demo; leave it exported (other
  callers / tests may use it — verify with a grep before removing, out of scope to delete here).
- New `extern fn` ⇒ bootstrap seed refresh (see §6). CI's `verify-bootstrap-fixed-point` gates on it.
- Config schema unchanged; `elevation_levels`, `carve*`, `band_step` all keep their meaning
  (`band_step` now scales the continuous height, `elevation_levels` still sets the relief ramp + the
  height scale `levels*band_step`).

## 9. Tests added/updated (TDD — written and failing first)

The visual payoff isn't unit-testable, but the geometry math is, and it's where the correctness risk
lives. New `tests/loam/test_surface.spr` (headless, pure `Double`), covering `loam/surface.sprout`:

1. **`ground_y` is the un-floored `band_top_y`.** For an `elev` landing exactly on a band boundary,
   `ground_y(elev) == band_top_y(band_of(elev))`; strictly between boundaries, `ground_y` lies
   strictly between the two neighbouring `band_top_y` values (proves de-quantization).
2. **Gradient normal — flat field.** All-equal corner heights ⇒ normal `(0,1,0)` (within ε).
3. **Gradient normal — known slope.** A constant east-west slope ⇒ normal tilts the expected way with
   the expected magnitude (closed-form check), and is unit-length.
4. **Corner averaging is seam-agnostic.** A corner's averaged height is independent of which adjacent
   tile "owns" it — computed from either side, identical (guards chunk-seam continuity).
5. **Continuous carve.** Lowering a tier-N cell drops its height by exactly `carve_depth(N)*band_step`
   (clamped at floor), matching the old integer carve at band boundaries.

All must fail against `HEAD` (module doesn't exist yet) and pass after implementation. Plus the DoD
gates: `just fmt`, full `just test`, `just compile-examples-stage1`, the gfx run-canary for the demo,
and `refresh-seed` for the new extern.

## 10. Spec/docs

- `docs/spec-v0.md`: no change (no normative language surface touched).
- `docs/terrain-v0.md` / the demo header comment: update to describe the continuous heightfield +
  gradient-normal bake, replacing the "flat top + step walls" description.
- `docs/gfx-engine-api-v0.md`: document `capture_quad_data_vn`.
- **Follow-up backlog (`BACKLOG.md`)** for the AAA layers deliberately deferred: detail normal maps,
  splat/triplanar material blending, terrain LOD (per-chunk mesh is the natural seam), optional
  per-vertex `band` datum for a smooth Relief ramp.

## Open questions for the user

1. **Approve the new host function `capture_quad_data_vn`?** (§6 — the one builtin/ABI addition.)
2. **Skirt vs. keep a perimeter wall** for the map edge (§5) — skirt is proposed; either works.
3. Land as **one change**, or split into (i) headless `loam/surface.sprout` + tests, then (ii) the
   host function + demo rewire? Splitting keeps each diff small and reviewable (Collaboration Rule 1).
