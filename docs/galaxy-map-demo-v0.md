# Galaxy Map Demo (v0)

Status: experimental example (`examples/gfx/galaxy_map.sprout`), not normative. Scene 1 of a planned
two-scene demo. Scene 2 (solar-system view) is scaffolded but stubbed.

## What it is

A 3D galaxy map of the 1,000,000-system universe produced by the Haskell generator at
`~/GameDev/universegen`. Stars are drawn as an instanced 3D point cloud, coloured by spectral class;
black holes are the violet/magenta landmarks (the supermassive one anchors the galactic centre). The
camera orbits, tilts, zooms and pans; right-clicking a system shows its details. Level-of-detail is
**zoom-adaptive**: only a coarse overview is shown when zoomed out, and finer stars stream in as you
zoom into a region — so all 1M systems are reachable without ever drawing them at once.

## Pipeline

```
galaxy.json (645 MB, 1M systems)                         ~/GameDev/universegen
   │  cabal run catalog  (app/CatalogExporter.hs)
   ▼
catalog/  meta.txt + L<level>/tile_<ix>_<iy>.txt         a quadtree LoD pyramid (~97 MB)
   │  read_file + str_split_lines + parse_int  (at runtime, streamed per-tile)
   ▼
examples/gfx/galaxy_map.sprout  ──▶  stdlib.gfx  ──▶  graphics/sprout_gfx.c (raylib)
```

### Offline: the catalog exporter (`universegen/app/CatalogExporter.hs`)

`cabal run catalog -- --input galaxy.json --output-dir catalog --max-level 7 --budget 6000`

- **Quadtree pyramid** over the galactic (x,y) plane. Level `L` has `2^L × 2^L` tiles.
- **Incremental refinement, brightest-first.** Each system is placed at the *shallowest* level whose
  target tile still has room (`--budget` per tile). Black holes are given an enormous brightness so
  they always land in L0. At `--max-level` a system is placed unconditionally, so **nothing is
  dropped**. Result: L0 is a small always-loaded overview of the brightest systems; deeper levels
  reveal the rest as you zoom. Each system appears at **exactly one** level (the union of a region's
  tiles across levels 0..L is that region's full star set at detail L).
- **Line format** (pipe-delimited; `|` chosen because `spectralClass` contains spaces):
  `id|xi|yi|zi|classCode|sizeCode|name|detailLine`
  - `xi,yi,zi` — integer **light-years** (raw, lossless for a map; the demo uses ly directly as world
    units and pushes the far clip plane out rather than scaling coordinates down and losing precision).
  - `classCode` — 0..11 (see palette below); indexes the demo's colour-model table.
  - `sizeCode` — per-star render radius in ly (visibility, not physical scale).
  - `detailLine` — **pre-formatted ASCII** panel text (e.g. `Class M, 0.44 Msun, 2686 K, 0.005 Lsun,
    32093 ly from core`) for the star-catalog lines, so the overview never parses a float. ASCII
    only — raylib's default font has no `☉`/`·`/`—`. (Scene 2 reads the per-system JSON directly:
    `stdlib.json` now parses floats via the pure-Sprout `parse_double`, so no pre-formatting there.)
- `meta.txt` carries `galaxy_radius_ly`, `ly_per_unit`, `max_level`, `tile_budget`, and counts.
- Invariants guarded by `universegen/scripts/verify_catalog.sh`: every system appears exactly once,
  all black holes are in L0, coordinates stay within ±radius, per-tile budgets are respected.

### Runtime: streaming + LoD (`galaxy_map.sprout`, pure math in `loam/galaxy_lod.sprout`)

- **Permanent group per tile, never evict.** 341 tiles for L0..L4 fit well under the shim's 4096-group
  cap, and ~64 MB of instance transforms fits resident. Each tile has a fixed group id
  `group_of(level,ix,iy) = level_offset(level) + iy·2^level + ix` (level bases `0,1,5,21,85`). Tiles
  are lazy-loaded once (throttled to `load_budget` reads/frame) and never cleared — so there is no
  eviction/reload/free-list, and no boundary-crossing stutter.
- **Per-star size scales with the star's level.** A star's render radius is
  `sizeCode · star_scale / 2^level` (`star_size` in the demo). A quadtree halves linear tile spacing
  per level, so dividing the radius by `2^level` holds the star-radius-to-neighbour-spacing ratio
  roughly constant at every depth: the brightest L0 stars (and all black holes, forced into L0) stay
  large landmarks, while the dense deep-level stars shrink to fine dots instead of merging into one
  solid carpet. This is also what makes zoom **visually smooth** — a level that streams in on zoom-in
  arrives as small points rather than popping in as full-size spheres. (Before this, a flat
  `star_scale` made every level's stars the same world size, so zooming into the core drew a wall of
  overlapping balls.)
- **`group_count` is the LoD selector.** Because levels occupy contiguous ascending group ranges,
  `gfx.draw_instances(level_offset(L+1), …)` draws exactly levels 0..L. The zoom-derived level (a
  distance ladder in `desired_level`) is therefore both what is streamed *and* what is drawn, in one
  call; the engine frustum-culls the rest. Deep tiles loaded from an earlier zoom-in are simply not
  scanned when zoomed back out.
- **Hysteretic level selection.** The committed level is threaded through the render loop and updated
  per frame by `next_level(cur, dist, maxlvl, hys)`, not read straight off `desired_level`. A level is
  a discrete choice, so selecting off the raw ladder makes it flip-flop whenever the eye hovers on a
  boundary — streaming and un-streaming a whole level's stars every other frame. `next_level` adds a
  deadband of `hys` (0.15): it steps finer only once the distance drops that fraction *below* the
  boundary and coarser only once it rises that fraction *above*, and moves at most one level per frame
  (a fast zoom refines progressively). `desired_level` is kept only to seed the committed level at
  startup so the opening frame is already at the right detail. Both read `level_boundary`, the single
  source of the ladder thresholds. Pure and headless-tested in `tests/loam/test_galaxy_lod.spr`.
- **Picking (right-click).** No retained star store — a click re-reads the ~focus tiles, projects each
  star with `gfx.world_to_screen`, and selects the nearest to the cursor within a pixel radius. Its
  `name`/`detailLine` render in the bottom-left panel.
- **Scene switch.** A `view: Int` threaded through the render loop (0 = galaxy, 1 = solar-system
  stub) with one toggle button — the app-managed pattern the real scene 2 will build on.

## Spectral → colour/size palette (demo-owned)

Instancing colours per **model**, not per instance, so the demo creates one sphere model per class in
class order (handle == `classCode`) and varies per-star size via the instance scale.

| classCode | class | colour (r,g,b) | notes |
|---|---|---|---|
| 0 | O | 155,176,255 | blue-white |
| 1 | B | 170,191,255 | |
| 2 | A | 210,220,255 | white-blue |
| 3 | F | 248,247,240 | white |
| 4 | G | 255,244,180 | yellow (Sun-like) |
| 5 | K | 255,200,120 | orange |
| 6 | M | 255,150,110 | red-orange (red dwarfs, 74% of stars) |
| 7 | Red Giant | 255,120,80 | |
| 8 | White Dwarf | 220,235,255 | |
| 9 | BH stellar-mass | 180,80,255 | violet (non-stellar, reads as exotic) |
| 10 | BH intermediate | 210,110,255 | |
| 11 | BH supermassive | 255,90,230 | magenta landmark at the core |

## New graphics primitives (added to `graphics/sprout_gfx.c` + `stdlib/gfx.sprout`)

These are gfx-binding additions (non-bootstrap; no seed refresh):

- `gfx.set_clip_planes(near, far)` — raylib's default far plane is 1000; a galaxy is ~100k ly across,
  so it would clip to black without pushing far out (`rlSetClipPlanes`, persists).
- `gfx.sphere_model(r,g,b) -> handle` — a uniformly-coloured unit sphere model for instancing a
  starfield (`GenMeshSphere` + `LoadModelFromMesh`), coloured via the material's `colDiffuse`.
- `gfx.world_to_screen(x,y,z) -> Bool` + `gfx.projected_x()/projected_y()` — project a world point to
  screen for click-picking (`GetWorldToScreen`; the split trio matches the `mouse_x`/`mouse_y` idiom
  since the i64 ABI returns one word).
- `gfx.draw_text(x,y,text,size,r,g,b)` — the first general text primitive (previously `DrawText` was
  reachable only as a button label).
- `gfx.double_to_int(d) -> Int` — a **stopgap** floor conversion; the core stdlib has none
  (`math.floor` returns a `Double`). See BACKLOG: promote to a core runtime/prelude primitive.

## Coordinate / camera conventions

- **Axis map:** galaxy `(x,y)` → world `(x,z)`, galaxy `z` → world height `y`. The disk lies on the
  ground plane, so `loam.camera`'s ground-plane pan glides across the galaxy and tilt reveals the halo
  — the pure orbit rig drops in unchanged, no volumetric camera needed.
- Coordinates are raw integer light-years; `set_clip_planes(5, 400000)` keeps the whole galaxy inside
  the frustum with comfortable float32 precision at ±50,000.

## Known limitations / lessons

- **Parsing must use `str_split_lines`, not `string_lines`.** The pure-Sprout `string_lines` is
  O(n²) on a ~650 KB tile (codepoint `char_at` per index); it made streaming grind for minutes. The
  C-implemented `str_split_lines` extern is O(n).
- The generator's disk is **uniform** (`generateDiskStar` draws `baseTheta` uniformly over the full
  circle), so there are no visible spiral arms — the render is faithful to the data.
- Star sizing (`sizeCode · star_scale / 2^level`) is a visibility compromise, not physical — a real
  star is sub-pixel at any of these scales. The `/2^level` falloff keeps the dense levels legible; the
  `sizeCode` and `star_scale` numbers are hand-tuned for the opening framing.

## Planned extensions (next iterations — designed for, not built)

- **Spectral-class filter** (e.g. "show only red dwarfs"). Nearly free at the render layer (colour is
  per-model, so filtering = drawing only the enabled `classCode`s). The real work is galaxy-*wide*
  coverage vs. spatial streaming: the exporter should be able to emit a **class-partitioned pyramid**
  so a filter fills the on-screen budget from the filtered class when zoomed out, and the residency
  key should include the active filter. `classCode` is already on every catalog line for this.
- **Solar-system scene (scene 2).** Already scaffolded via `view`. The real build lazily reads
  `systems/block-<floor(id/10000)>/SYS-<id>.json` for the selected system and draws planets/orbits
  (the per-system files carry full ellipse descriptors). The float-parsing blocker is now removed:
  `stdlib.json` parses float numbers (`JsonFloat`, read via `json_get_float`) using the pure-Sprout
  `parse_double` (prelude), so scene 2 walks the real JSON tree — no fixed-point pre-export needed.

## Running

```
mise exec -- just run-gfx examples/gfx/galaxy_map.sprout /Users/cthulhu/GameDev/universegen/catalog
# canary + screenshot:
SPROUT_GFX_MAX_FRAMES=120 SPROUT_GFX_SCREENSHOT=galaxy.png \
  mise exec -- just run-gfx examples/gfx/galaxy_map.sprout /Users/cthulhu/GameDev/universegen/catalog
```

**Scripting the opening camera** (for screenshots / canaries at a fixed viewpoint). Optional integer
args after the catalog dir override the starting `Cam` without a rebuild:
`… <catalog> <radius> <height> <yaw_milli> <tx> <tz>` — all light-years except `yaw_milli`
(milliradians; 500 = 0.5 rad). A small `height` with a large `radius` gives a low pitch (disk seen
near edge-on); a small `radius`+`height` zooms into the core (deeper LoD). Absent args keep the
`init_*` defaults. Note `TakeScreenshot` writes relative to the working directory (the repo root).
```
# edge-on overview:
SPROUT_GFX_MAX_FRAMES=110 SPROUT_GFX_SCREENSHOT=hero.png SPROUT_GFX_SCREENSHOT_FRAME=100 \
  mise exec -- just run-gfx examples/gfx/galaxy_map.sprout <catalog> 85000 14000 700 0 0
# zoomed into the core (deepest LoD):
SPROUT_GFX_MAX_FRAMES=160 SPROUT_GFX_SCREENSHOT=core.png SPROUT_GFX_SCREENSHOT_FRAME=150 \
  mise exec -- just run-gfx examples/gfx/galaxy_map.sprout <catalog> 3000 3000 500 0 0
```

Regenerate the catalog (in the universegen repo):
```
cabal run catalog -- --input galaxy.json --output-dir catalog --max-level 7 --budget 6000
```
