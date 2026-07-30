# Galaxy Map Demo (v0)

Status: experimental example (`examples/gfx/galaxy_map.sprout`), not normative. A three-scene demo:
**scene 1** is the streaming galaxy map; **scene 2** is the solar-system view — click a star,
then `System >` to fly into it and see its star, planets, and orbits (built; see "Scene 2" below);
**scene 3** is a third-person view of a spaceship out in that system (a realistic vista — sun a
distant disc, planets specks) — `Ship >` from scene 2 (built; see "Scene 3" below).

## What it is

A 3D galaxy map of the 1,000,000-system universe produced by the Haskell generator at
`~/GameDev/universegen`. Stars are drawn as an instanced 3D point cloud, coloured by spectral class;
black holes are the violet/magenta landmarks (the supermassive one anchors the galactic centre). The
camera orbits, tilts, zooms and pans; clicking a system shows its details. Level-of-detail is
**zoom-adaptive**: only a coarse overview is shown when zoomed out, and finer stars stream in as you
zoom into a region — so all 1M systems are reachable without ever drawing them at once.

## Pipeline

```
galaxy.json (645 MB, 1M systems)                         ~/GameDev/universegen
   │  cabal run catalog  (app/CatalogExporter.hs)
   ▼
catalog/  meta.txt + L<level>/tile_<ix>_<iy>.txt         a quadtree LoD pyramid (~97 MB)
   │  ensure_balanced (in galaxy_map.sprout) — rebalances per class AT STARTUP, cached
   ▼
catalog-balanced/  same layout, per-class-budgeted        class-balanced pyramid (~48 MB, built once)
   │  read_file + str_split_lines + parse_int  (at runtime, streamed per-tile)
   ▼
examples/gfx/galaxy_map.sprout  ──▶  stdlib.gfx  ──▶  graphics/sprout_gfx.c (raylib)
```

(The rebalance step exists only for the spectral-class filter — see "Spectral-class filter" below.
The demo builds `<catalog>-balanced` on first launch and reuses it thereafter; you pass the plain
`catalog/` and never manage the balanced copy by hand.)

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
  `sizeCode · star_scale / 2^level` (`star_size` in the demo) — the sprite *diameter*, passed as the
  billboard instance scale. A quadtree halves linear tile spacing per level, so dividing the radius by
  `2^level` holds the star-radius-to-neighbour-spacing ratio roughly constant at every depth: the
  brightest L0 stars (and all black holes, forced into L0) stay large landmarks, while the dense
  deep-level stars shrink to fine dots instead of merging into one solid carpet. This is also what
  makes zoom **visually smooth** — a level that streams in on zoom-in arrives as small points rather
  than popping in as full-size discs. (Before this, a flat `star_scale` made every level's stars the
  same world size, so zooming into the core drew a wall of overlapping balls.) The billboard's
  minimum-pixel floor (in `loam.billboard`) is what keeps this world-size falloff from making the L0
  overview vanish — a far star never renders below ~3 px.
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
- **Picking (left-click).** Left-click is overloaded: a press-and-drag ORBITS the camera, so
  selection is a **tap** — press and release in place. The tap is detected on the release edge by
  `loam.camera.is_tap` (the press-down point is latched on the press edge and threaded through the
  render loop; a release within a small `click_slop` square of it is a tap, larger is a drag). On a
  tap, and only off the UI, the pick re-reads the ~focus tiles (no retained star store), projects each
  star with `gfx.world_to_screen`, and selects the nearest to the cursor within a pixel radius. Its
  `name`/`detailLine` render in the bottom-left panel. `is_tap` is pure and headless-tested in
  `tests/loam/test_camera.spr`.
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
- `gfx.billboard_model(r,g,b) -> handle` — a uniformly-coloured unit **quad** model, the instanced
  counterpart of `sphere_model`, for **billboards (point sprites)**. Registered identically, so it
  flows through `instance_push`/`draw_instances`/`draw_instances_masked` and the per-tile group
  culling unchanged — a star is still one instance with a position + scale. Assign a billboard-capable
  shader (`loam.billboard.glow_shader`) via `model_set_shader`; a star then renders as a round glowing
  **screen-space sprite** instead of a faceted sphere mesh — no low-poly silhouette at any zoom (not
  even on the huge central black hole, previously a visible decagon), and cheaper (4 verts vs an 8×12
  sphere). The instanced draw auto-feeds two billboard uniforms — `uProjScale` (the projection's
  `(Px,Py)` perspective diagonal, so sprites stay round) and `uViewportH` (target height, for the
  minimum-pixel size floor that keeps distant points from vanishing sub-pixel) — by the same
  convention it feeds `mvp`/`colDiffuse`, so a billboard shader authored in loam needs no per-frame
  Sprout call. This is the "screen-constant point/billboard primitive" the LoD notes flagged; the glow
  look (radial falloff, hot whitened core) lives in `loam.billboard`, not the shim. General engine
  capability — reusable by any instanced point cloud (particles, spark lights, distant markers).
- `gfx.load_shader(vs, fs) -> handle` + `gfx.shader_set_float/vec3/int(h, name, …)` +
  `gfx.draw_sphere_shaded(h, …)` + `gfx.model_set_shader(model_h, shader_h)` — the **generic
  custom-shader API**. A caller authors GLSL (a string) and drives it from Sprout, so domain
  look-and-feel is NOT baked into the engine. The emissive starfield/sun and the procedural planets
  are built on this — their shaders live in `loam.starfield` / `loam.planet`, not the shim.
- `gfx.camera_x()/camera_y()/camera_z()` — the current camera eye (as last set by `set_camera`),
  read component-wise (i64 ABI). For view-dependent shading, e.g. `loam.planet`'s atmosphere limb.
- `gfx.world_to_screen(x,y,z) -> Bool` + `gfx.projected_x()/projected_y()` — project a world point to
  screen for click-picking (`GetWorldToScreen`; the split trio matches the `mouse_x`/`mouse_y` idiom
  since the i64 ABI returns one word).
- `gfx.draw_glow(x,y,z,size,r,g,b)` — an immediate-mode **additive, camera-facing radial glow** of
  world-space `size` at a point, tinted `(r,g,b)`. A soft luminous halo with no geometry to facet,
  additively blended (depth-write off, so it never occludes what's behind it) — a general light
  primitive (sun corona, explosion flash, light source). The galaxy demo layers three at the galactic
  centre for the **core bulge** (see "Galactic-core glow" below). Backed by raylib `GenImageGradientRadial`
  (a cached soft-gradient texture) + `DrawBillboard` under `BLEND_ADDITIVE`. Call inside the 3D pass.
- `gfx.draw_shaded_plane(sh,x,y,z,half) ` — a large horizontal (XZ) quad of half-extent `half` under a
  custom shader, drawn **additively with depth-write off** — a volumetric gas / fog / energy-field
  plane in a ground plane. The shader gets each fragment's world position (auto-fed `matModel`); if a
  density field is active (below) it is bound as the shader's `texture0`. The galaxy demo draws the
  nebula gas with it (`loam.nebula`).
- `gfx.density_begin(half)` + `gfx.density_build()` — a **density field**: a top-down (x,z) grid of the
  instanced point cloud. `density_begin` sets the extent (once, before streaming); every
  `instance_push` then bins its point (count + the model's colour) automatically; `density_build`
  normalises + blurs + uploads it as an RGBA texture (`rgb` = mean population colour, `a` = density),
  rebuilt only when new points arrived. `draw_shaded_plane` binds it as `texture0`, so a field shader
  can key on the **actual structure** of the cloud (star density, spiral arms, clusters) and its
  population colour rather than a fixed formula. General: any point-cloud heat/influence field.
- `gfx.draw_text(x,y,text,size,r,g,b)` — the first general text primitive (previously `DrawText` was
  reachable only as a button label).
- `gfx.draw_line3d(x0,y0,z0,x1,y1,z1,r,g,b)` — an immediate-mode 3D line (`DrawLine3D`); scene 2
  draws each orbit ellipse as a loop of these.
- `gfx.draw_sphere(x,y,z,radius,r,g,b)` — an immediate-mode diffuse-lit solid sphere (`DrawSphereEx`).
  (Distinct from `sphere_model`, which is for the instanced starfield.) The emissive star and
  procedural planet draws are NOT gfx primitives — they are `loam.starfield.draw_star` /
  `loam.planet.draw_planet`, thin wrappers over `gfx.draw_sphere_shaded` with loam-owned shaders.
- `gfx.post_bloom(threshold, intensity)` — additive bloom (bright-pass → blurred glow) over the
  scene, so emissive stars/sun bleed a halo; the galaxy demo enables it with `supersample(2)`.
- `gfx.draw_instances_masked(group_count, cull_dist, class_mask)` — like `draw_instances`, but draws
  only the models whose bit is set in `class_mask` (bit *m* == model *m*). Since the starfield pushes
  one model per spectral class (model index == `classCode`), this is the spectral-class filter — and
  nearly free, because the draw already batches one instanced call per model, so a masked-off class is
  just a skipped iteration. An all-ones mask behaves exactly like `draw_instances`.
- `gfx.draw_rect(x,y,w,h,r,g,b)` — a filled 2D rectangle (`DrawRectangle`); the filter legend's
  class swatches / checkbox boxes. (Before this, `DrawRectangle` was reachable only inside `button`.)
- `gfx.double_to_int(d) -> Int` — a **stopgap** floor conversion; the core stdlib has none
  (`math.floor` returns a `Double`). See BACKLOG: promote to a core runtime/prelude primitive.
  (The *offline* rebalance tool avoids it entirely — its coordinates are integer ly, so it bins with
  the pure-integer `loam.galaxy_lod.tile_index_i`.)

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
- Stars are drawn as **billboards (screen-space point sprites)**, not sphere meshes — `loam.billboard`
  glow sprites on `gfx.billboard_model` (see the primitives list above). This replaced the original
  instanced sphere models: at close range a world-size sphere shows its low-poly facets (the central
  supermassive black hole was a visible decagon), whereas a billboard has no silhouette to facet at
  any zoom, and it is cheaper. The tradeoff a billboard introduces — world-size sprites vanishing
  sub-pixel when zoomed out — is handled by the primitive's minimum-pixel floor, so both zoom extremes
  read well. The now-removed instanced emissive sphere shader (`loam.starfield.load_instance_shader`)
  is gone; `loam.starfield` retains only the *immediate* emissive shader for the scene-2 sun.

## Galactic-core glow (built)

The generator's disk is uniform (no spiral arms, no brightness concentration), so the raw overview is a
flat carpet of dots. A **galactic-core bulge** gives it a bright nucleus: `draw_core_glow` (in the demo)
layers three `gfx.draw_glow` additive sprites at the galactic centre (world origin, where the
supermassive black hole sits) — a wide faint halo, a mid body, and a bright warm core — which
accumulate into a smooth luminous falloff. The warm old-population tint and the three-layer stack are
demo composition; the additive-glow primitive itself (`gfx.draw_glow`) is domain-agnostic.

Because a `draw_glow` sprite is **world-size**, zooming into the core would otherwise turn the whole
screen into a warm additive wash. So the bulge is **distance-faded**: `draw_core_glow` takes a `fade`
(0..1) derived from the camera distance relative to the galaxy radius — full when viewing the whole
galaxy, gone once the camera descends past ~0.35·radius. The glow is the *unresolved* core light, so as
the LoD resolves individual stars the bulge dissolves rather than floods the view; at `fade == 0` the
three draws are skipped entirely (zero cost, and the core-zoom render is unchanged). Scene 1 only.

The bulge fade — and the nebula gas below — are keyed to the **LoD ladder** (`level_boundary`): full
through LoD 0–1, fading across LoD 2–3, gone only at the deepest core zoom. So the atmosphere persists
while you explore mid-zoom and only dissolves as the LoD resolves individual stars.

## Nebula atmosphere — data-driven gas (built)

To push the look toward a real galaxy (warm bulge + cool disk gas, à la Andromeda) without fabricating
structure the uniform data lacks, the demo adds a **volumetric gas layer that traces the actual star
field** rather than a fixed formula:

- **Density field (engine, general).** As tiles stream, every `gfx.instance_push` bins its star into a
  top-down (x,z) grid — accumulating count and the star's spectral colour. `gfx.density_build`
  normalises + blurs it into a texture (`rgb` = mean population colour, `a` = density). This is a
  general point-cloud field, not galaxy-specific.
- **Gas plane (`loam.nebula` on `gfx.draw_shaded_plane`).** A large additive ground-plane quad whose
  fBm-noise shader **samples the density texture**: gas brightness follows real star density, and its
  hue is a blend (`uColorMix`) of a radial palette (warm gold core → cool blue → violet rim) and the
  field's own population colour. fBm adds wispiness; the plane lies in the galactic plane, so edge-on
  it reads as a haze band and from a high angle as a broad disk.
- **Why this matters for structure.** The gas *is* a blurred picture of where the stars are. A uniform
  disk gives smooth haze; **a spiral galaxy would give gas that follows the arms, automatically** — and
  with `uColorMix` raised, young-star arms read blue and the old bulge gold straight from the stellar
  populations. It shows what is really in the data, so it stays believable for any structure the
  generator produces. (`uColorMix` defaults low because the current uniform catalog has no colour
  structure to trace; raise it once the generator emits populations.)
- **Nebula knots.** A few cool-toned `gfx.draw_glow` sprites scattered in the disk plane — the bright
  embedded gas-cluster glows of the reference nebulae. Decorative demo composition, not per-star.
- **Fade.** Gas, bulge, and knots share one LoD-keyed `atmo` factor, so the whole atmosphere is a
  far-field layer that dissolves as you zoom in; at the deepest core zoom the render is unchanged.

## Scene 2 — solar-system view (built)

Click a star in scene 1 to select it, then the `System >` button flips `view` to 1 and flies
into that system. The scene:

- **Loads the real per-system JSON.** `systems/block-<floor(id/10000)>/SYS-<id>.json` (a sibling of
  the catalog dir) is `read_file` + `json.parse`d into a `List Planet`. The `name` catalog field is
  the file stem (`SYS-00001`), so the id and path derive from the current selection — no extra pick
  state. Parsing uses the pure-Sprout, **float-capable** `json_parse` (`json_get_float`); no
  fixed-point pre-export. Parsed **once on entry** and cached in the loop (re-reading per frame
  halved the frame rate — GC churn from the parse).
- **Draws star + orbits + planets.** The star is an **emissive** `loam.starfield.draw_star` at the
  origin (glowing, not diffuse-lit); each planet's orbit is a polyline loop of `draw_line3d` sampled
  from `loam.orbit.ellipse_point` (pure Kepler-ellipse math from
  `orbitSemiMajorAU`/`eccentricity`/`argumentOfPeriapsisDeg`), and the planet body sits at its current
  `trueAnomalyDeg`, sized by `radiusEarths`. Each body is a **procedural** `loam.planet.draw_planet`:
  `planet_kind` picks rocky vs gas giant by the sub-2-Earth-radius split, `planet_rgb` supplies the base
  tint the shader derives its surface from, and `planet_seed` (static orbit params — never
  `trueAnomaly`, which would make the surface swim) varies it per planet. Bodies are lit from the star
  at the origin, so the day-side terminator points at it, with half-Lambert wrap keeping the night side
  dim-but-visible (no view-dependent hotspot).
- **Rings and moons.** A planet with `hasRings` gets a flat ring circle (`draw_line3d` loop) around
  it; a planet with N `moons` gets N schematic dots just outside it (each a small `draw_planet` of
  `kind` 2, a grey mottled moon) — real moon distances are far sub-pixel at system scale, so the dots
  indicate *presence*, not true geometry (the count is exact, shown in the panel). `hasRings` reads
  through a new `stdlib.json.json_get_bool` accessor.
- **Look (procedural, no assets).** Stars are emissive + bloomed (`gfx.post_bloom`, scene-wide); the
  sun glows. Planets/moons get their surfaces analytically in the shader (3D value-noise fBm →
  continents/bands/mottle) so the demo stays asset-free. Bloom is threshold-based, so it keys on
  brightness — the dark procedural planets now sit below the threshold and no longer bloom like the
  sun (the earlier flat-tan planets did); an emissive-only bloom source is the principled follow-up
  (`BACKLOG.md`).
- **Hover detail panel.** Moving the cursor over a planet projects each planet to the screen
  (`world_to_screen`, same as scene-1 picking, but over the in-memory cached list) and shows the
  nearest one's name, type, radius, mass, temperature, semi-major axis, moon count, and rings in the
  bottom-left panel. Type/mass/temp are folded into a pre-formatted `detail` string at parse time (so
  the `Planet` record stays within the 10-field constructor-arity ceiling — the star catalog uses the
  same trick).
- **Fits scale per system, with a sqrt radial compression.** Systems span 0.5–30 AU, a dynamic range
  (up to ~70× inner-to-outer) that a *linear* AU→world scale cannot show: fitting the outer orbit to
  `fit_world_radius` shrinks the inner orbits below the star's own render radius, so the star swallows
  the inner planets (and a 10-Earth giant dwarfs everything). Instead each orbit gets its own **uniform
  sqrt-compressed scale** `k(a) = fit / sqrt(a · a_ext)` (world semi-major `= fit·sqrt(a/a_ext)`) from
  the pure `loam.system_view`. Scaling each orbit uniformly *about the origin* — where `loam.orbit`
  places the star's focus — keeps every orbit a true ellipse of unchanged eccentricity and shared
  focus; only the spacing *between* orbits is compressed, so the inner planets spread clear of the star
  while the outer orbit still fits the frame. Body radii are sqrt-compressed the same way
  (`body_world_size`, keeping the size ordering) so giants stop dominating. Headless-tested in
  `tests/loam/test_system_view.spr`. A compact and a spread system both frame legibly.
- **Own camera.** A separate AU-scale `Cam` (the system is always at the origin), driven by the same
  `loam.camera` rig; the galaxy camera is held while in scene 2.
- **Scripting.** `argv[6] = <system id>` opens directly in scene 2 for that system (screenshot/canary
  of a view that otherwise needs an interactive click).

## Scene 3 — third-person ship, realistic in-system vista (built, iteration 1)

From scene 2 the **`Ship >`** button flips `view` to 2: a **third-person view of a spaceship out in
the selected system** — a physically-scaled *vista*, **not** the scene-2 map. The ship is the hero at
the world origin, near-black space around it, the sun a small distant disc and the planets pinpoint
specks. No orbit lines, no size inflation. `< System` returns to scene 2.

- **Why not just place things at true coordinates.** A ~50 m ship and a 1 AU sun span ~10 orders of
  magnitude; no single linear world scale (float32) or ordinary depth buffer holds both — this is the
  standard space-scale problem. So AU distances never enter world coordinates.
- **Planetarium projection (`loam.vista.project_body`, pure + headless-tested in
  `tests/loam/test_vista.spr`).** The ship sits at the origin; every distant body (sun, each planet) is
  placed on a render **sphere** of radius `vista_sphere_r` (8000) around it, in the body's **true
  direction** from the ship and at its **true angular size** (`sphere_r · body_rad_au / dist`), floored
  to `vista_min_speck` so a sub-pixel body still draws as a crisp speck. Because all bodies share one
  distance, orbiting the nearby ship gives them negligible parallax — they read as a fixed backdrop,
  which is correct. Positions come from the real per-system JSON: sun at the AU origin, planets at their
  `loam.orbit` ellipse points; the ship is `ship_sun_dist_au` (1.0 AU) out. Astronomical constants
  (`sun_radius_au`, `earth_radius_au`) are the real values.
- **Look.** The sun is a full-bright emissive disc (`loam.starfield.draw_star`, so bloom bleeds a halo)
  under two additive `draw_glow` layers (a corona), its true ~0.5° disc scaled up by `sun_disc_boost`
  (2.0) so it reads clearly against the black; the planets are true-size crisp emissive specks in their
  `planet_rgb` tint. All tunables live in the scene-3 block in `galaxy_map.sprout`.
- **Body markers (HUD).** Because the bodies are pinpoint specks, every body in the current system
  (the star + all planets) carries an **Elite-style corner-bracket reticle** with a name + distance
  label, so they are identifiable. Drawn as a 2D overlay (`overlay_begin` phase): each body's on-sphere
  world position (the same `project_body` result it is *drawn* at) is run through `world_to_screen`
  (valid in 2D — it reads the cached camera), front-culled, and a reticle of four L-shaped corners
  (filled `draw_rect`s — no rectangle-outline primitive needed) is drawn at that screen point. The
  distance is the **true ship→body range in AU** (`sqrt` on the AU vector, ship at `(1 AU, 0, 0)`),
  formatted `N.NN` (`fmt_au`); the jump HUD's integer truncation would read "0"/"1" at system scale.
  The star is warm-tinted and labelled `Star` (the system name is already in the corner HUD); planets
  are HUD-cyan. **Known limitation:** labels can overlap when bodies line up on screen (worst near
  edge-on) — overlap declutter is a follow-up (BACKLOG). Only bodies in front of the camera show
  (the far hemisphere is correctly hidden).
- **Sky (faked galaxy, `loam.skydome`).** Thousands of background stars, the Milky Way band, and the
  central bulge glow are a GPU fragment shader over the view direction — no geometry, no catalog read
  (one `draw_sphere_shaded` call). Correct directions from the galaxy's own geometry: the disk is the
  world x/z plane, so the band is directions with small `|dir.y|` (`uBandNormal`), and the bulge is a
  glow toward `uCoreDir = normalize(centre − system)` (centre = the origin black hole). Drawn on a
  **camera-centred sphere seen from inside** via the new `gfx.set_backface_cull(false)` toggle (mirror
  of `set_depth_mask`) + depth-write off, so it never occludes.
- **Real stars on top.** A couple hundred *actual* catalog stars near the system, projected in their
  true directions over the faked field as landmark stars. Each is one small additive glow whose colour
  is pre-mixed toward white (`bg_star_whiten`), so it reads as a white-hot **point of light** with a
  subtle spectral tint — a star, not a saturated ball; the catalog size code (which is large, e.g. 45)
  drives only a gentle, hard-capped size lift, never a disc. Sourced from the system's finest-level
  tile (reusing scene 1's field parser), placed with `project_body` at `body_rad = 0` (so it returns
  direction only, size floored). Cached per system, one glow per star (~260) to hold the frame rate.
  They cluster toward the galactic centre — the same direction the bulge glows — because that is where
  the real neighbourhood is densest.
- **Model.** `assets/models/rusty_spaceship.glb` (glTF/GLB via raylib's `LoadModel`). At **44 MB it is
  *not committed*** (`.gitignore`); drop the file in to see it. `gfx.load_model` returns `-1` only on
  OOM, so an absent file yields an empty model that simply draws nothing (the vista still renders).
- **Scale gotcha.** raylib **bakes the glTF node transforms** into the mesh, so the model's baked
  bounding box (`GetModelBoundingBox`) is ~19 world units on its longest axis — the *raw* accessor
  `min`/`max` are ~90× larger, before the node scale. `ship_scale = 2.8` maps the baked extent to ~53
  world units. Measure with the same loader that renders, not the raw accessors.
- **Camera.** A third `Cam` (`shipcam`) targets the origin, so the `loam.camera` rig circles/tilts/zooms
  around the ship — "third person" is the orbit rig pointed at the ship. Panning is disabled here (the
  target stays on the ship); the galaxy and system cameras are held.
- **Scripting.** `argv[9] = 1` (with a system via `argv[6]`) boots straight into scene 3 for a
  screenshot/canary, which otherwise needs two interactive clicks. `argv[10..12]` give the boarded
  system's galaxy position (x,y,z, ly) so the canary orients the sky and sources the real-star layer
  without an interactive pick (e.g. SYS-00013 is `-25141 6520 436`); absent → the galactic centre.
- **Next.** Optionally a flyable ship with a chase camera, and star labels/selection on the real
  layer — see "Planned extensions".

## Interstellar jump drive — FTL (scene 0, built, iteration 1)

The galaxy view is also where you **travel between stars**. Select a target star, then **Engage Jump**
(the bottom button, or **Space**): the drive **spools** (a countdown), a **warp tunnel** opens, and you
**arrive** in that system's third-person ship vista (scene 3) — the tunnel resolves straight into "you
are now here, in space". Full design + the verified prior-art survey it is based on is in
[docs/ftl-v0.md](ftl-v0.md); this is the demo-side summary.

- **The shape** is the Elite-Dangerous-canonical pair (supercruise + a map-selected hyperspace jump),
  **stripped of combat**: no interdiction/mass-lock (the demo has no NPCs to be pulled out by — it stays
  a documented future hook). Iteration 1 builds the **interstellar jump**; the **intra-system
  supercruise** is designed in the doc and deferred to iteration 2.
- **Free-jump.** Any star within `jump_range_ly` is a valid target — our galaxy is a free point cloud
  with no gate graph, so a gate network would need edges the generator doesn't emit. The range is
  **inflated for playability** (`6000` ly; a realistic drive is ~tens of ly, which at this catalog's
  ~90 ly mean spacing would reach only nearest neighbours).
- **Fuel + range economy.** A jump needs the target in range AND fuel for its cost (`dist · fuel_per_ly`).
  Arrival tops fuel up by a **flat** `refuel_amount`; since cost scales with distance but the top-up is
  flat, short hops net a gain and long hops a drain — self-balancing, and with the range cap it can
  **never hard-strand** you. The HUD shows a fuel gauge, the range, and the target's distance/cost (or
  the blocked reason: `OUT OF RANGE` / `LOW FUEL` / `SELECT TARGET`). A cyan **route line** is drawn
  from the ship's current system to the selected target.
- **Pure logic in loam, headless-tested.** The jump geometry, the economy, and the `idle → spool →
  tunnel → arrive` **state machine** (advanced by `gfx.get_frame_time()`) are `loam.ftl` — 27 assertions
  in `tests/loam/test_ftl.spr` cover the range/fuel gate, every phase transition, and the refuel sign.
  The general interpolation atoms it builds on are `loam.ease` (`clamp01`/`smoothstep`/`ease_out`/
  `inv_lerp`/`remap`, on the new `stdlib.math` `fclamp`/`lerp`), tested in `tests/loam/test_ease.spr`.
- **The warp tunnel (`loam.warp`)** is a GPU shader, a near-copy of `loam.skydome`'s skeleton — a
  camera-centred inside-out sphere whose fragment shader draws blue-shifted radial star-streaks from the
  travel axis, length/brightness/flow driven by a `uProgress` uniform, with an envelope that fades in
  from spool and to black at the end (masking the cut to the vista). No new engine primitive — it uses
  the existing generic gfx shader API, like every loam shader.
- **State.** `render_loop` threads the ship's **current location** (`loc_*`, distinct from the `sel_*`
  target so a jump has a *from* and a *to*), `fuel`, and the `ftl_phase`/`ftl_timer`. `loc_*` is
  **seeded at boot to a random real home system** (see "Starting home" below) and **moves only on jump
  arrival** — picking a star selects a jump target, it does not relocate you. Arrival sets `loc := sel`,
  burns+tops-up fuel, and flips to view 2; the existing per-system JSON / real-star streaming
  (`need_load`/`need_bg`) then loads the destination on the next frame — no new loader.
- **Scripting.** `argv[13] = 1` auto-engages a jump at boot (galaxy view → spool → tunnel → arrival
  vista) with no interaction, so a headless screenshot can capture the warp tunnel mid-run and the
  destination vista — see Running below.

### Starting home (built)

The demo **opens with the ship already in a real random system** — no first-click "board a star"
step. At boot `pick_home` reads one line from the balanced catalog's **L0 root tile** (a few thousand
landmark stars, always on disk), indexed by `time_now_micros()` so each launch starts **somewhere
new**, and parses it exactly like `pick_line` (`id|x|y|z|class|size|name|detail`) into the home's name
+ galaxy coordinates. The galactic-centre sentinel at `(0,0,0)` is filtered out (it doubles as the
"no system" marker). The demo then opens in that system's **ship vista (view 2)**: `loc_*` and `sel_*`
are both seeded to the home, so `is_diff` is false (no self-jump offered) and the `sel_name != csys`
load path streams the home system on the first frame. The galaxy camera is also centered on the home
so "you are here" is framed when you toggle out to the map. A boot log line reports the choice
(`[galaxy] ship home: SYS-… at (x, y, z)`).

**Precedence:** the explicit `argv[6]` system canary and the `argv[13]` auto-jump canary both override
the random home (they set their own view/framing), so screenshots stay deterministic.

**Persistence across runs is future work** (BACKLOG): the home is currently re-randomized each launch.
The `loc_*`/`sel_*` split already models "where you are" independently of "where you're aiming", which
is exactly what a saved-home restore will populate.

## Spectral-class filter (built)

Right-side legend of 12 clickable class checkboxes (multiselect) + `All`/`None`. Each checkbox box is
filled with the class colour when enabled (so it doubles as the palette swatch) and dark when not;
scene 1 draws only the enabled classes via `gfx.draw_instances_masked` (model index == `classCode`,
so the mask enables/disables whole classes). The filter is instant — a pure render mask, no restream —
and picking respects it (a click can only select a currently-visible star). `argv[7]` opens on
an initial mask (e.g. `64` = M-only) for screenshots.

- **The hard part was coverage, not rendering.** The render mask is nearly free, but the raw catalog
  is ordered **brightest-first**, so its coarse levels are all bright classes — L0 holds *zero*
  M/F/G/K/A stars (verified). Filtering to a dim class (M dwarfs are 74% of all stars) over that
  pyramid is a **blank screen** when zoomed out. The fix rebuilds coverage **per class**.
- **`ensure_balanced` (in `galaxy_map.sprout`, run at STARTUP)** re-pyramids the existing catalog into
  a class-**balanced** one: shallowest-fit tiling with a **per-class** per-tile budget, so every class
  fills the coarse levels first and appears galaxy-wide at every zoom. It is a **Sprout-side** transform
  that consumes the *catalog* (universegen's output), not `galaxy.json` — so **universegen is
  untouched** — and it is **not a separate tool or `just` recipe**: the demo builds it on first launch
  into `<catalog>-balanced` (a sibling of `catalog/`, so scene 2's `<dir>/../systems` still resolves)
  and **caches** it, rebuilding only if that directory is absent (delete it to force a rebuild). The
  rebalance is headless — integer-ly coordinates bin with the pure `loam.galaxy_lod.tile_index_i`, no
  `double_to_int` — and creates its output dirs by shelling `mkdir -p` via `stdlib.process` (no `mkdir`
  builtin; `write_file` can't create directories). The demo then streams the balanced catalog for both
  the `All` view and any filter.
- **Which stars fill a bucket: a spatially-uniform hash sample (streaming, two-pass).** A bucket keeps
  up to `budget` stars; *which* ones must be spread across the whole tile, not clustered in one
  sub-quadrant (see "Coarse-level clumping" below). The rebalance therefore keeps a **hash-thresholded
  sample**: whether a star is kept depends only on `hash(id)` (via `loam.galaxy_sample`), which is
  uncorrelated with position. "Keep the `budget` lowest-hash" is a global sort of the whole scan set,
  and a pure-Sprout merge sort of the current ~1M-line scan **does not finish in minutes** at startup —
  so instead the build is a **streaming two-pass count-then-keep**: pass 1 tallies each class's count
  in every nested tile (a Dict bounded to `≤ 341×12` slots — *independent of star count*); from those
  counts `loam.galaxy_sample.level_thresholds` derives a per-bucket cumulative hash threshold
  `t_L = min(1, t_{L-1} + budget/count_L)`; pass 2 re-streams and keeps each star at the shallowest
  level whose threshold `hash01(id)` falls under. Peak memory is the **output** (bounded by
  `budget × #buckets`) plus the counts — both independent of the number of stars — so the rebalance
  **scales to a 10M+ star galaxy** at the cost of one extra streaming pass over the source.
- **Tradeoffs (deliberate).** The kept count per bucket is `budget` in *expectation* (± O(√budget)),
  not exactly `budget` — the price of the O(n) streaming sample vs. an exact top-`budget`, and
  invisible for a visual LoD. The catalog dropped per-star luminosity, so the sample is spatially
  uniform, not brightest-first — fine for a filter overview. The per-class per-tile budget (default
  800) caps deep-zoom density for the huge M class; overflow past the deepest level is dropped. At
  budget 800 the balanced catalog keeps ~½M of 1M stars; every class has L0 presence.
- **Follow-ups (BACKLOG):** a brightness-ordered rebalance (needs luminosity carried on the catalog
  line, or re-reading `galaxy.json`); a higher/adaptive budget so dense M regions keep full depth.

The filter and sampling math are pure, headless-tested modules: `loam.galaxy_filter` (class bitmask via
integer arithmetic — Sprout has no bitwise ops; `>>`/`<<` are function composition — plus the shared
`class_name`/`class_rgb` palette; tests in `tests/loam/test_galaxy_filter.spr`) and `loam.galaxy_sample`
(the `hash01` / `level_thresholds` / `keep_level` spatially-uniform sampler; tests in
`tests/loam/test_galaxy_sample.spr`, including a regression that a position-correlated id set is still
kept spread across the whole tile). The demo's star models are built from `class_rgb`, so a star and its
legend swatch can never diverge.

## Radial sector grid (built)

A galactocentric **polar partition** of the disk for navigation/orientation: a small undivided
**Core** disc around the central black hole (radius `sector_core_frac`·R, default 0.15·R), then
`sector_rings` (5) equal-**area** annuli × `sector_wedges` (12, 30° clock-face) wedges tiling
`[core, rim]`. This is the astronomically faithful framing — real galactic structure is described in
galactocentric cylindrical coordinates (radius R, azimuth φ) — in contrast to the Cartesian cube grids
of space games like Elite: Dangerous.

- **Equal-area rings, not equal-width.** Over the annulus `[inner, outer]` the k-th ring boundary is
  `r_k = √(inner² + (k/rings)·(outer²−inner²))`, so every ring encloses the same area. Squaring the
  edge removes the `√` for binning: a star is in ring `j = floor(rings·(x²+y²−inner²)/(outer²−inner²))`
  — exact integer arithmetic, no `sqrt`, no `Double→Int` floor, mirroring `loam.galaxy_lod.tile_index_i`
  (`inner = 0` recovers the plain `radius·√(k/rings)` full-disc case). Because the generator's disk is
  areal-uniform, equal area also means ~equal star **population** per ring (measured full-disc on this
  catalog: 20.14 / 20.00 / 19.90 / 20.00 / 19.96 %), so each ring is a comparably-dense navigation band.
  Visually the rings thin outward — the same sqrt spacing scene 2 uses for orbits. **Why an inner
  radius:** on the full disc the innermost equal-area ring spans `[0, R/√5] = 0.447·R` — nearly half the
  radius — so carving a small `Core` disc out first (and tiling the rings over `[core, rim]`) keeps the
  central navigation cells a sensible size instead of one dominant blob.
- **Wedges without `atan2`.** The stdlib has no `atan2` (see `loam/camera.sprout`). Binning an angle
  does not require recovering it: a star is in wedge `j` iff it is counter-clockwise-of-or-on boundary
  ray `j` and strictly clockwise of ray `j+1`, tested by the sign of the cross product
  `cos(θ_k)·y − sin(θ_k)·x` (the range-reduction step a real `atan2` does first). A scale-relative
  epsilon (`1e-6·|p|`, above the Taylor-series `sin`/`cos` noise floor, below a wedge half-width) makes
  every boundary cleanly lower-closed. Wedge 0 starts at galaxy +x, counter-clockwise; the centre
  (undefined azimuth) falls through to wedge 0.
- **Pure, general, headless-tested: `loam.polar_grid`.** `in_disc` (the Core test) + `ring_of` (equal-
  area over `[inner, outer]`) / `wedge_of` / `sector_id` / `sector_count` (the uniform `ring·w + wedge`
  grid) + `ring_radius` / `boundary_point` (the overlay's circle/spoke geometry). It is
  renderer-independent engine code — nothing in it is galaxy-specific (a radar/minimap ring grid is an
  equal consumer) — with the Core radius, ring, and wedge counts all parameters end-to-end; `0.15`/`5`/
  `12` live only in the demo. Tests: `tests/loam/test_polar_grid.spr` (44 assertions, incl. the annulus
  binning and the wedge-seam boundary cases). The galaxy naming — a small undivided **"Core"** disc,
  else `R<ring>-W<wedge>` with 1-based ring numbers (ASCII only; raylib's default font has no middot) —
  is demo presentation, not baked into the grid.
- **Where it shows.** Selecting a star appends its sector to the detail panel
  (`…ly from core   Sector R2-W07`, or `Core`); the label is folded in at pick time and the star's
  galaxy `(x,y,z)` rides along in the pick result. A **selection drop-line** is then drawn from the
  picked star's true 3D position straight down to the disk plane. Its length makes the cylindrical-radius
  binning legible — a rare halo star (large `|z|`) visibly hangs off the thin disk, and the line's foot
  marks the in-plane point that lands in the very cell its label names, so "R4 yet looks farther than an
  R5 star" reads as height rather than a bug. (`ly from core` is itself the in-plane radius, so the panel
  number and the ring always agree.) For the 99.8% of stars in the disk the line is near-zero, a light
  selection tick. The grid itself — the Core circle, the annulus ring circles, and spokes from the Core
  out to the rim (so the Core stays an undivided disc) — is **always drawn** on the disk ground plane
  with `gfx.draw_line3d` (thin, non-additive, reads at any zoom, no distance fade). A `show_grid` flag is
  kept threaded through the render loop (gating the draw) as a programmatic toggle in case it should be
  made switchable again; `argv[8] = 0` disables it at launch (default on) for a grid-free screenshot.

## Planned extensions (next iterations — designed for, not built)

- **Scene 2 depth — remaining.** Rings, moons, the hover detail panel, and the **sqrt radial/body
  compression** (spread systems no longer bury inner planets in the star — see "Fits scale per system"
  above) are built. Still open: **asteroid belts** (the exporter emits `asteroidBelts: []` for every
  system in this catalog — nothing to draw until it populates them); and true moon geometry (needs a
  >10-field `Planet` or a moon sub-record — the constructor arity ceiling is 10; see BACKLOG).
- **Scene 3 depth — remaining.** Iteration 1 is a static ship with an orbit camera. Next: the
  **galaxy's stars rendered behind the ship** (the point cloud as a backdrop, not just the local
  system), and optionally a **flyable ship** (WASD thrust + a chase camera that trails its heading,
  replacing the fixed placement + orbit rig).

## Running

```
# Pass the plain catalog/. On first launch the demo builds catalog-balanced (~15 s) for the
# spectral filter and caches it; later launches reuse it. The ship opens in a RANDOM real home
# system's ship vista (see "Starting home"); the console prints the chosen home. Toggle out with
# `< System` to reach the galaxy map (centered on your home) and select a jump target.
mise exec -- just run-gfx examples/gfx/galaxy_map.sprout /Users/cthulhu/GameDev/universegen/catalog
# canary + screenshot:
SPROUT_GFX_MAX_FRAMES=120 SPROUT_GFX_SCREENSHOT=galaxy.png \
  mise exec -- just run-gfx examples/gfx/galaxy_map.sprout /Users/cthulhu/GameDev/universegen/catalog
# scene 3 (ship) canary — argv[6]=system id, argv[9]=1 boots straight into the ship view,
# argv[10..12]=that system's galaxy x,y,z (orients the sky + sources the real-star layer).
# (SPROUT_GFX_SCREENSHOT must be a RELATIVE path — raylib resolves it against the working dir):
SPROUT_GFX_MAX_FRAMES=120 SPROUT_GFX_SCREENSHOT=ship.png SPROUT_GFX_SCREENSHOT_FRAME=100 \
  mise exec -- just run-gfx examples/gfx/galaxy_map.sprout /Users/cthulhu/GameDev/universegen/catalog 85000 14000 700 0 0 13 4095 1 1 -25141 6520 436
# interstellar JUMP canary — argv[13]=1 auto-engages a jump at boot (galaxy view -> spool -> warp
# tunnel -> arrival vista) with no interaction. argv[6]=destination system id, argv[10..12]=its galaxy
# x,y,z (the ship's current location is seeded 3000 ly off it, an in-range origin). The phase timing is
# wall-clock (set_target_fps(60)), so the tunnel spans ~frames 132..288 and arrival is ~frame 288 at
# 60 fps — pick SPROUT_GFX_SCREENSHOT_FRAME in the warp window, and a later frame for the arrival vista:
SPROUT_GFX_MAX_FRAMES=210 SPROUT_GFX_SCREENSHOT=warp.png SPROUT_GFX_SCREENSHOT_FRAME=200 \
  mise exec -- just run-gfx examples/gfx/galaxy_map.sprout /Users/cthulhu/GameDev/universegen/catalog 85000 14000 700 0 0 13 4095 1 0 -25141 6520 436 1
```

`argv[7]` is the initial spectral-class filter mask (bit *c* = class *c* shown; absent = all). It
requires the camera args to be present too (positional), so a filtered screenshot passes the full row,
e.g. M-only edge-on overview: `… <catalog> 85000 14000 700 0 0 -1 64`. `argv[8]` (non-zero) opens with
the radial **sector grid** overlay on, e.g. a top-down grid canary:
`… <catalog> 45000 120000 0 0 0 -1 4095 1`.

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
