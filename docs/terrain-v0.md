# Procedural Terrain — v0

**Status:** design approved, implementation in progress (`loam/terrain.sprout`,
`tests/loam/test_terrain.spr`, `examples/gfx/terrain_demo.sprout`). Part of the
graphics/game arc that produced the Loam engine (`docs/ecs-v0.md` §10). This document is
**experimental / supporting**, not normative: it explains the v0 terrain subsystem and the
reasoning behind its scope. `docs/spec-v0.md` remains the normative source for the language
core; nothing here changes the language.

## 1. Problem statement

Loam has entities that wander, flock, and jump on a *flat, featureless* ground plane — the
arena is a bare square (`gfx.draw_grid`) and physics lands every body at `y = 0`
(`loam/physics.sprout`). A game needs a *world*: ground with shape and material, generated
rather than hand-placed, and big enough that it cannot all live in memory at once.

Two eventual scales are in view: a coarse **world map** (a Civ/Settlers/city-builder board)
and a fine **location** (a tactical RTS/shooter/autobattler playfield). They should share one
substrate, differing only in tile scale and what a tile *means*.

## 2. Goals / non-goals

**Goals (v0)**
- A **headless, seeded, deterministic** terrain model in the Loam ethos: same seed ⇒ same
  world, testable without a window (mirrors `docs/ecs-v0.md` §9.3).
- A **chunked** substrate: terrain is generated **upfront to disk**, and only the chunks in use
  are resident in memory (a cache), so map size is bounded by disk, not RAM. (Note: **rivers**
  — [rivers-v0.md](./rivers-v0.md) — deliberately step outside this per-coordinate model: drainage
  is non-local, so it is a global pass over a *bounded* region, deterministic in `(seed, bounds)`
  rather than per tile.)
- A **layered** generator: a continuous elevation field underneath, tiles **derived** by
  sampling it and classifying — one pipeline that later serves both a tile view and a
  continuous-field view.
- **Multi-field biomes** (elevation + moisture + temperature → a Whittaker classifier).
- A **visible demo**: colored cubes, one per tile, culled to resident chunks.

**Non-goals (v0)** — deferred, with rationale in §9:
- Real-valued (smooth) terrain height, and physics landing on the terrain surface.
- Runtime editing / manual placement.
- The world-map scale, negative coordinates, and chunk eviction policy.
- Region files (many chunks per file), AAA asset/mesh/LOD rendering.
- More than one generation algorithm (the seam exists; only fBm value noise ships).

## 3. Prior-art survey (verified against primary sources)

The base noise algorithm is a choice among established alternatives, so it warrants a survey.

| Algorithm | What it needs | Character | Fit for Sprout/v0 |
|---|---|---|---|
| **Value noise** (chosen) | hash lattice + interpolation | cheapest; **blocky** without octaves | best — no gradient table, pure integer hash, only `+ - * /` + `sqrt`/`sin` |
| Perlin (gradient) | gradient table + dot products | smoother, classic | more machinery ×3 fields |
| Simplex | simplex grid | fewest artifacts, cheap in high-D | patent (US 6,867,776) **expired 2022-01-08**, now free; still more complex |
| Diamond-square | recursive grid midpoint displacement | fast, hilly, grid-creased | hostile to per-chunk generation |

Value noise is honestly the *blockiest* of the family ([Wikipedia, "Value
noise"](https://en.wikipedia.org/wiki/Value_noise)); it is chosen for **implementability**, and
the blockiness is softened by summing octaves (fBm) with smoothstep interpolation. Because tiles
are *derived* by sampling a field (§5), the algorithm sits behind a single `value_at` seam and
can later be swapped for Perlin/simplex without touching chunking, disk, classification, or
rendering. Sources: [Value noise](https://en.wikipedia.org/wiki/Value_noise),
[Simplex noise](https://en.wikipedia.org/wiki/Simplex_noise),
[US 6867776B2](https://patents.google.com/patent/US6867776B2/en),
[FastNoise2 — Understanding Noise Types](https://github.com/Auburn/FastNoise2/wiki/Understanding-Noise-Types).

## 4. The constraint that fixed the data model

Two stdlib primitives are missing (verified):
1. **No `Double → Int`** conversion. `math.floor` returns a `Double`; only the one-way
   `to_double` intrinsic exists (`stdlib/prelude.sprout:1221`).
2. **No `String → Double` parser**, and `double_to_string` uses `%g` (6 significant figures,
   lossy). Doubles **do not round-trip through text**.

Rather than route around these (integer fixed-point noise, or a new `to_int` intrinsic), v0
treats them as the **v0 / follow-up boundary**: discrete integer tile data is what serializes
cleanly and is what we build now; real-valued continuous height is exactly the follow-up path
(§9) that would need those primitives. Concretely:

- A tile stores **only two `Int`s**: a `TileKind` tag and an integer **elevation band**.
- Noise math stays in readable `Double` internally; the **only `Double → Int` crossing is
  threshold classification**, which is comparison-based (`if elev < t then …`) and needs no
  conversion primitive.
- **Consequence:** v0 terrain is **stepped** (discrete elevation levels, like Civ z-levels), not
  smooth. This is consistent with tiles + colored cubes, and reads as intentional.
- **Result:** v0 needs no compiler change and no new *core* builtin. (Rendering adds one
  optional gfx-shim extern — §7.)

This is also why the tests can assert exact `Int` equality (§8): all persisted state is `Int`.

## 5. Architecture

New headless module `loam/terrain.sprout` (`module loam.terrain`, no gfx import). Data flows
elevation/moisture/temperature fields → classify → tiles → chunk → disk → resident cache →
(view). Layers:

### 5.1 Stateless spatial hash
`spatial_hash(ix, iz, seed) -> Int` — a **pure function of lattice coordinates** (not a
threaded stream), a thin wrapper over `stdlib.rng.rng_hash2` (range `[0, 2^31)`). Purity here is
what makes generation independent of sample/iteration order.

The hash must be **nonlinear** in the coordinates. A single `rng_next` step is affine
(`A*x + C mod 2^31`), so evenly-spaced coordinates map to evenly-spaced outputs; for the Perlin
mixing primes `A * 19349663 mod 2^31 ≈ (3/5)·2^31`, which makes `hash(x, z+5) ≈ hash(x, z)` — a
~5-lattice-cell period that renders as tiled, "copy-pasted" terrain. `rng_hash2` breaks this by
multiplying the two coordinate-hashes together in its final step (a mix nonlinear in the coords);
see `stdlib/rng.sprout` for the full rationale and the overflow bound. Switching a prime modulus
does **not** fix it — the field stays affine and the period merely relocates.

### 5.2 Integer-lattice value noise + fBm
- `value_at(tx, tz, L, seed) -> Double` in `[0,1)`: lattice cell `= tx / L` (integer division),
  in-cell fraction `fx = to_double(tx - cell*L) / to_double(L)`; hash the four lattice corners,
  normalize to `[0,1)`, and **bilinearly interpolate with `smoothstep`**. Sampling only at
  integer tile coordinates with an integer lattice spacing `L` means **no `floor`/`to_int` is
  ever needed**.
- `fbm(tx, tz, seed, octaves, L0) -> Double`: sum octaves, halving `L` (`L0, L0/2, …`, floored at
  1) and amplitude each octave; normalize to `[0,1)`.
- Local `Double` helpers (absent from `stdlib.math`): `lerp`, `smoothstep`, `clampd`. Amplitude
  halving avoids any need for `Double` `pow`.

### 5.3 Whittaker classifier — the sole `Double → Int` gate
- Three **decorrelated** fields per tile: `elev = fbm(seed)`, `moist = fbm(seed + OFF_M)`,
  `temp = fbm(seed + OFF_T)`, each `Double` in `[0,1)`.
- `classify(elev, moist, temp) -> TileKind`: below sea level ⇒ `Water`; otherwise a nested
  comparison ladder over `(moist, temp)` selects the biome (a small Whittaker matrix).
- `band_of(elev, levels) -> Int`: counts how many of `levels` evenly-spaced thresholds `elev`
  exceeds (short recursion, `elev >= to_double(i)/to_double(levels)`), yielding a band in
  `0..levels` — arbitrary resolution, comparison-only, **no `to_int`**.

### 5.4 Types
`TileKind` is a sum type (nullary variants). The product types are **records** (records-v0 is
landed — `loam/scene.sprout` is itself a record now; named fields remove the miscounted-accessor
bug class positional matching invited). The 9-field-per-product cap still applies
(`loam/scene.sprout` arity note).

```
type TileKind (..) =
  | Water | Beach | Grass | Forest | Desert | Mountain | Snow | Tundra
# nullary variants; the 9-field cap is per-product *fields*, so the variant count is free.
# tile_kind_tag : TileKind -> Int  and  tile_kind_of : Int -> TileKind  (for serialization)

type Chunk  = (cx: Int, cz: Int, kind_grid: MutMatrix Int, elev_grid: MutMatrix Int)
type Config = (seed: Int, chunk_size: Int, octaves: Int, lattice0: Int,
               elevation_levels: Int, sea_level_band: Int)
# moisture/temp seeds are derived by offset from `seed`; Whittaker thresholds are v0 constants.
type World  = (config: Config, base_dir: String, resident: Ref (Dict Chunk))
```

`MutMatrix` (`stdlib/mutable.sprout:101`, `MutMatrix Int Int (MutVec a)`) with
`mutmatrix_fill(f: Int -> Int -> a)` fills a grid directly from generation. `Ref`
(`prelude.sprout:1288`) holds the mutable resident cache; `Dict` (`prelude.sprout:61`) is
String-keyed, so chunk coordinates are encoded to a key.

### 5.5 Serialization (text, `Int`-only, exact round-trip)
- `chunk_to_string(c) -> String`: header line `"<cx> <cz> <size> <levels>"`, then one line per
  row of `"<kindTag>:<band>"` cells. Built with `int_to_string` (`prelude.sprout:1209`) and
  `stdlib.string.join` (`string.sprout:170`).
- `chunk_from_string(s) -> Maybe Chunk`: `string_lines` (`string.sprout:211`) → parse header with
  `words`/`split_once` + `parse_int` (`prelude.sprout:1203`), then fill a fresh `MutMatrix`,
  splitting each cell on `":"` with `split_once`. (There is no general N-way `split`; loop
  `split_once`.)

### 5.6 Disk + paging
- One file per chunk, `chunk_<cx>_<cz>.terrain` under `base_dir`, via `write_file`/`read_file`
  (`prelude.sprout:1140-1141`, both `Result`-returning). `read_file` reads a whole file with no
  seek, so *file granularity = load granularity* — one file per chunk is the exact fit.
- `generate_chunk(config, cx, cz) -> Chunk`, `save_chunk`, `load_chunk`, and a
  `generate_region(config, base_dir, cx0, cz0, cxN, czN)` offline pass.
- `chunk_key(cx, cz) = int_to_string(cx) ++ "," ++ int_to_string(cz)`.
- `page_in(world, cx, cz) -> Maybe Chunk`: cache hit (`ref_read` → `dict_get`) else `load_chunk`
  then insert (`dict_set` → `ref_write`). `page_out` drops it (`dict_remove` → `ref_write`);
  the chunk is reloadable from disk.

## 6. Syntax / semantics impact

None on the language. This is a new stdlib-level engine module plus one example. No parser,
typechecker, evaluation-order, visibility, or spec change. The rendering externs (§7 —
`draw_cube`, the terrain-batch pair, and the mesh-capture trio) are the only C-level changes,
and they are graphics-shim additions, not core builtins.

## 7. Rendering (the demo)

`examples/gfx/terrain_demo.sprout` follows the `examples/gfx/ecs_agents.sprout` skeleton
(window, target FPS, orbit camera from `loam.view`, recursive frame loop). It generates a
region to a temp dir on start, pages the chunks in, and draws **one cube per tile** at
`(world_x, to_double(band) * band_height, world_z)`, colored by biome.

**Colored cubes.** `gfx.draw_model` has no color/tint and no cube asset exists, so a
`gfx.draw_cube(x, y, z, size, r, g, b)` extern wraps raylib `DrawCube` (`graphics/sprout_gfx.c`
+ `stdlib/gfx.sprout`). It is a **graphics-shim** extern — linked only under `run-gfx`, outside
the `runtime/sprout_runtime.c` + `runtime/APPROVED_BUILTINS` core-builtin discipline — so it is
lighter than a core builtin, though still a C addition. Colour comes from the cube's **vertex
colour** under a dedicated cube shader (a texture-sampling model shader discards it → white),
lit by world-space face normals so stepped relief reads as top-vs-side brightness. (The
alternative, authoring N colored `.glb` assets, was rejected: an asset dependency with no
in-repo tooling.)

**Static mesh bake (the scaling move).** Drawing one cube per tile *every frame* is
per-frame work on data that never changes: at 256×256 (65,536 cubes ≈ 2.36 M vertices) the
per-frame CPU rebuild + model traversal caps at ~20 FPS (measured; even a no-op draw only
reaches ~48, so the traversal alone can't hit 60). The demo instead **bakes the whole region
into one static GPU mesh once at startup** and draws it in a single call per frame:
`gfx.mesh_capture_begin()` makes each `gfx.draw_cube` *append* geometry instead of drawing,
`gfx.mesh_capture_end()` uploads the mesh and returns a handle, and `gfx.draw_captured(handle)`
draws it (backface culling off, so cube winding need not be exact). Per-frame cost becomes
independent of tile count → **256×256 at 59 FPS**. The same per-tile loop is reused verbatim;
only *when* it runs changes (once, not per frame).

The colored-cube view is the **first rung of the asset-per-tile path**: the same loop later
bakes real tile/prop assets by swapping the geometry. Editable terrain would re-bake affected
chunks; frustum/LOD culling and truly huge (memory-bounded) maps are follow-ups (§9).

## 8. Tests

Headless, `stdlib.test` (`assert_true`/`assert_eq`, `main` opens with `new_state()` and closes
with `summary()`). Written failing first (Definition of Ready), run via `just test-loam`
(globs `tests/loam/*.spr`, passes `--package-root` at repo root). There is no epsilon-assert
helper — v0 state is all `Int`, so exact `==` is valid; `fabs(a-b) < eps` inline only for
intermediate `Double` checks.

1. **Determinism** — same `(seed, cx, cz)` ⇒ identical chunk grids.
2. **Chunk-boundary continuity** — the tile at a global coordinate is identical whether reached
   via chunk A or its neighbor B (the seam-correctness property that makes chunking sound).
3. **Classification** — `elev` below sea ⇒ `Water`; chosen `(elev, moist, temp)` ⇒ expected biome.
4. **Band monotonicity** — larger `elev` ⇒ `band_of` non-decreasing.
5. **Serialization round-trip** — generate → `chunk_to_string` → `chunk_from_string` → identical
   grids; and once through `write_file`/`read_file` to a temp path.
6. **Paging** — `page_in` twice returns the cached chunk; `page_out` then `page_in` reloads.

## 9. Follow-ups (deferred, with rationale)

- **Real-valued continuous field + physics-ground.** Land bodies on `terrain_height(x, z)`
  instead of `y = 0` (twin of the gravity field in `loam/physics.sprout`). Needs a **`to_int`
  intrinsic** (mirror of `to_double`, `fptosi`) to sample the field at arbitrary positions and a
  **lossless float text encoding** to persist heights — the two missing primitives §4 designs
  around. This is the "continuous" half of the layered pipeline.
- **World-map scale**: coarse tiles, huge extent, a chunk **eviction policy**, and **negative
  coordinates** (v0 lattice math assumes non-negative coords via integer division / `imod`; a
  world map needs floor-division semantics).
- **Region files** (many chunks per file): needs partial-read I/O the runtime lacks today.
- **Runtime editing / manual placement**: mutate a resident chunk, mark dirty, `write_file` back
  — the write path already exists.
- **Smoother noise** (Perlin/simplex) behind the `value_at` seam if the stepped look matters.
- **Extract terrain rendering into `loam.view`** (as `view` was extracted from the demos), and
  the far-horizon **AAA asset/mesh/LOD** render track the current raylib shim cannot reach.

These, and their scoping decisions (Q1–Q8), are tracked in `BACKLOG.md`.
