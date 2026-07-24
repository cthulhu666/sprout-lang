# Rivers v0 — procedural drainage for Loam

Status: **experimental** (loam engine feature, not normative language spec). Companion to
[terrain-v0.md](./terrain-v0.md). Implemented in `loam/hydrology.sprout`; asserted headlessly in
`tests/loam/test_hydrology.spr`; rendered by `examples/gfx/terrain_rivers_demo.sprout`.

## 1. Problem

Loam's terrain (terrain-v0) is a heightfield with no water flow. A game about **water transport
by rafts** needs rivers that behave like real drainage: they flow downhill, merge, and reach the
sea, so the network can be navigated (downstream is fast, upstream is work). A river is also the
hard case for this engine, because whether a tile carries a river depends on its **entire
upstream watershed** — a non-local, path-dependent property that contradicts terrain's "each tile
is a pure function of its own coordinate + seed" streaming model.

## 2. Goals / non-goals

**Goals (v0):**
- Deterministic drainage over a **bounded region**: rivers flow downhill and every cell reaches
  a valid outlet (sea or map edge) — no inland dead-ends, no cycles.
- Store per-cell integer results (a `flow_tier` size class + a `flow_dir`), consistent with
  terrain's integer-tile design.
- A watchable demo: rivers painted on the existing terrain, coloured by size.

**Non-goals (v0, deferred):**
- Infinite/streamable rivers (needs a coarse-global + fine-local hybrid; out of scope given the
  bounded-world decision).
- Valley carving / meandering — v0 rivers are blocky D8 paths coloured on the terrain surface.
- True Horton–Strahler stream order — v0 buckets **drainage area** (accumulation), which is what
  cartography scales river width by anyway. The field is named `flow_tier`, deliberately *not*
  `stream_order`, so a later navigability phase does not conflate the two.
- Persistence: the demo computes hydrology in memory at startup; nothing is written to the chunk
  format, so `terrain-v0`'s on-disk format and `TileKind` are unchanged.

## 3. Key decision: a global bounded-region pass

Rivers are computed **once, upfront, over the whole region**, deterministic in `(cfg, span)`.
This trades terrain's per-tile locality for "reproducible from the seed + bounds" — still fully
deterministic, just not per-tile-independent. It slots into the demo's existing "generate the
region to disk, then bake the mesh" flow (the pass runs between generation and the bake). This is
the documented divergence from terrain-v0's streamability property, and is acceptable because the
target is a hand-designed, fixed-size map, not an endless world.

## 4. Pipeline (the classic GIS hydrology backbone)

Routed on the **continuous** fBm elevation (`loam.terrain.elev_at`), not the 12 discrete bands —
routing on the smooth field avoids the flat-plateau tie problem. All steps are comparison/integer
over one `Double` field, so no `Double↔Int` primitive is required.

1. **Sample** elevation for every region cell; initialise the Planchon–Darboux water surface
   (`= elevation` at outlets, `+∞` elsewhere).
2. **Fill depressions — Planchon–Darboux.** A repeat-until-stable sweep (forward + backward each
   pass, alternating so fills propagate from all sides in a handful of passes). We use the
   sweep-based method, **not** the textbook priority-flood, because Sprout's stdlib has no
   priority queue / heap. A strictly-positive slope increment `ε` is carved across filled flats.
3. **D8 flow direction** — steepest descent to the strictly-lowest filled neighbor; ties broken
   deterministically by `rng_hash2` (the nonlinear spatial hash added alongside this work).
   Outlet cells (sea or border) are sinks (direction 0).
4. **Flow accumulation** — each cell walks downstream, adding 1 to every cell it passes; the
   result is drainage area per cell.
5. **Threshold → `flow_tier`** — accumulation buckets into 0 (none) / 1 (creek) / 2 (river) /
   3 (major). Sea cells are never rivers.

### The termination guarantee (why ε matters)

The single load-bearing correctness property is **termination**. With `ε > 0`, every non-outlet
cell has a *strictly lower* filled neighbor, so the flow field is acyclic and every downstream
walk (in accumulation, in a view, in future raft routing) provably terminates. With `ε = 0`,
filled flats have equal-elevation neighbors and tie-breaking can produce `A→B→A`, which would
**hang** a windowed demo. The headless test asserts strict descent directly, and caps every walk
at `4·span` steps so a regression fails loudly instead of hanging.

## 5. Data model & API (`loam/hydrology.sprout`)

- `Hydrology = (span, filled: MutMatrix Double, flow_dir: MutMatrix Int, flow_tier: MutMatrix Int)`
  — grids indexed `[row=z][col=x]`, matching `Chunk`.
- `compute_hydrology(cfg, span) -> Hydrology !{IO}` — the whole pipeline; pure in `(cfg, span)`.
- `hydro_tier / hydro_dir / hydro_filled (h, gx, gz)` — per-cell accessors.
- `dir_delta(d) -> (drow, dcol)` — D8 decode, shared by module, view and tests.

All region-scale sweeps are **`Int`-returning direct tail recursion**: Sprout TCO fires only for
`i64` returns and statically-resolved self/mutual calls, and a 262k-deep `Int !{IO}` sweep is
proven O(1)-stack (mirrors `deep_tail_recursion.spr`). A `Unit`-returning or higher-order sweep at
that depth would risk the runtime stack-overflow guard.

## 6. Terrain surface impact

Additive only: `loam/terrain.sprout` now **exports** `elev_at` (the continuous field the pass
samples) and `sea_level` (so the pass and the drains-to-sea test agree with the classifier on
"is sea"). No change to `Chunk`, its serialization, `TileKind`, or generation. No compiler,
runtime, or bootstrap-seed change — hydrology is app code, not bundled into the seed.

## 7. Tests

`tests/loam/test_hydrology.spr` (TDD — written failing first), at span 128:
- **No inland dead-ends / cycles**: every cell drains to a sea-or-border outlet within the cap.
- **Strict descent**: `flow_dir` always points to a strictly-lower filled neighbor.
- **Non-vacuous**: some river cells exist.
- **Determinism**: same `(cfg, span)` ⇒ identical `flow_tier`.

## 8. Roadmap (raft game)

1. **Meander + valley carving** — lateral offset (stronger in low-gradient lowlands); lower
   terrain near river cells so rivers sit in valleys.
2. **Raft mechanics** — current from `flow_dir` + accumulation (downstream cheap, upstream
   portage); navigability tiers from a true Strahler order; rapids where the band drop is steep;
   confluences = junctions, mouths = ports, lakes = hubs.
3. **River graph** — promote the per-cell grid to a `RiverNetwork` of segments/junctions
   (`Dict Set` adjacency) for route queries.
