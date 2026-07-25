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
deterministic, just not per-tile-independent. It slots into the demo's "generate the region, then
bake the mesh" flow (the pass runs between generation and the bake). This is
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
- `HydroParams = (tier1_min, tier2_min, tier3_min, carve1, carve2, carve3, widen2, widen3)` — the
  accumulation→`flow_tier` cutoffs, the per-tier channel carve depths (§6a), and the per-tier sideways
  widen radii (§6b). `default_hydro_params()` reproduces the stock `40 / 200 / 900` tiers, `1 / 2 / 3`
  carve, `0 / 1` widen. Parameterized (not module constants) so a caller — e.g. the terrain-rivers
  demo reading a config file — can sweep river size, channel depth and width without recompiling.
- `compute_hydrology(cfg, span, p: HydroParams) -> Hydrology !{IO}` — the whole pipeline; pure in
  `(cfg, span, p)`.
- `carve_depth(p, tier) -> Int` — pure: how many elevation bands a river cell of that `flow_tier` is
  sunk below its own terrain (0 for non-river/sea). Monotonic in tier (§6a).
- `widen_radius(p, tier) -> Int` — pure: how many cells sideways a river of that tier spreads (§6b).
- `widen_rivers(h, p, span) -> Unit !{IO}` — dilates `flow_tier` in place so big rivers span multiple
  tiles (§6b). Double-buffered; a no-op when no tier widens.
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

## 6a. Channel carving (visual — the "looks like a river" step 1)

Painting river cells blue on the flat terrain top does not read as a river; a river reads from
**depth**. So before baking, the demo lowers each river cell's elevation band by `carve_depth(tier)`
(1 / 2 / 3 bands for creek / river / major by default, config-tunable). The existing top-surface bake
then produces the channel for free: a land neighbour, now higher than the carved cell, emits its
earth-coloured step wall down to it (the **bank**), and the river cell's own top quad — coloured by
`river_rgb` and lit by the scene shader — becomes the **shaded water floor**. Opaque; a translucent
animated surface is later work.

The load-bearing property is that carving **preserves downhill flow**: carve depth is monotonic in
`flow_tier`, and `flow_tier` (via accumulation) is non-decreasing downstream, so a downstream cell is
lowered *at least* as much as its upstream neighbour — carving can only steepen the descent, never
reverse it. `carve_depth` monotonicity is unit-tested; the carve itself is a pure band adjustment the
demo applies over the existing `flow_tier` grid (no new grid, no runtime, no assets).

## 6b. River width (visual — step 1.5)

A 1-tile-wide major river reads as a creek no matter how deep it is carved. `widen_rivers` **dilates
the `flow_tier` grid in place** so a river spreads `widen_radius(tier)` cells sideways (default
`0 / 1` for river / major → a major becomes 3 tiles wide; creeks never widen). A non-river cell
within a river's radius is promoted to that river's tier, so it then carves, colours and skips-trees
as water via the *unchanged* downstream readers — widening needs **no new grid threaded through the
bake**, just one call after `compute_hydrology`.

Two correctness points: it is **double-buffered** (the widened tiers are computed into a temp grid
from the *original* `flow_tier`, then committed) so a promoted cell cannot seed further promotion —
spread is bounded to `widen_radius`, not an unbounded flood; and it is a **no-op when no tier widens**
(`max_widen ≤ 0`), so the pre-widen look is preserved exactly. The dilation adds no measurable startup
cost (≈0 s of the 1024² demo's generate+solve+bake). `widen_radius` (pure) and `widen_rivers` (on a
synthetic 3×3 grid — a centre major promotes its neighbours; radius 0 leaves them untouched) are both
unit-tested.

## 6c. River meander (visual — step 1c)

The D8 flow field bends only in 45° increments, so on gentle terrain a river renders as an
axis-aligned **staircase** of right angles. `meander_rivers` bends it into a sinuous ribbon by
**displacing the rendered mask (`flow_tier`) laterally** by a smooth seeded noise field — and,
crucially, leaves `flow_dir`/`filled` untouched. Those two carry every drainage invariant (strict
descent, bounded accumulation, termination); meandering only the *paint* keeps them true **by
construction**, so no hydrology property test changes. It composes with the other passes exactly like
widening — rewrite `flow_tier` in place, double-buffered, then the *unchanged* carve/bake/tree-skip
readers follow — and runs **before** `widen_rivers` (relocate the thin centre line, then thicken it).

Three design points, each fixing a concrete failure mode:

- **Forward-scatter, not backward-gather.** Each source river cell *stamps* its tier at the displaced
  target (max-merge on collision). A backward warp (`out[c] = src[c+off]`) can leave a **gap** in a
  1-cell creek at a noise extremum; forward-scatter never drops a source cell, so a creek stays
  connected — spatial neighbours (a river path is a chain of them) get near-equal smooth offsets, so
  their targets stay adjacent.
- **The flatness gate is a carve-correctness constraint, not flavour.** A displaced river lands on
  terrain the D8 solve never routed it through; carve then lowers *that* band, so on a slope a
  meandering channel would climb **uphill**. `meander_amp_eff` pins amplitude toward 0 as local relief
  rises (full on a floodplain, half on a moderate slope, zero in a gorge), keeping gorge rivers on
  their D8 path so the carved channel still descends.
- **Coherent noise, entirely in `Int`.** Sprout has no `Double→Int` primitive (the numeric layer's
  `floor`/`round_nearest` deliberately stay in `Double`), and the offsets must be integer cell counts.
  So the displacement is **integer bilinear interpolation of per-lattice-corner `rng_hash2` hashes**
  (mirrors terrain's `value_at`, but never leaves `Int`). Amplitude ≪ wavelength keeps the field
  slowly varying (|Δ| per cell ≪ 1), which is what guarantees a smooth bend rather than a tear.

`meander_amp` (max lateral cells) and `meander_wavelength` (bend period) are config-tunable
`HydroParams` fields; `meander_amp = 0` is an exact no-op (the straight-river look). Adding these two
fields pushed `HydroParams` to 10 fields, past the runtime's former `sprout_make9` ceiling — so the
constructor-arity cap was lifted to 10 (runtime `sprout_make10`, the `ir_header` declare, the
`ast_to_ir` cap check, and the object-header arity nibble which already had spare bits). `meander_amp`,
`meander_wavelength`, `meander_corner`/`meander_axis`/`meander_offset` (pure, unit-tested directly),
`local_relief`, and `meander_rivers` (synthetic-grid displacement/determinism/no-op) are all tested in
`tests/loam/test_hydrology.spr`.

## 7. Tests

`tests/loam/test_hydrology.spr` (TDD — written failing first), at span 128:
- **No inland dead-ends / cycles**: every cell drains to a sea-or-border outlet within the cap.
- **Strict descent**: `flow_dir` always points to a strictly-lower filled neighbor.
- **Non-vacuous**: some river cells exist.
- **Determinism**: same `(cfg, span)` ⇒ identical `flow_tier`.
- **Thresholds honored**: raising `HydroParams` cutoffs above any achievable drainage area leaves no
  river cells (proves `flow_tier` reads the params rather than a hardcoded threshold).
- **Carve depth**: `carve_depth` is 0 for tier 0, the per-tier depth otherwise, and monotonic in tier
  (the property that keeps a carved river flowing downhill — §6a).
- **Widen**: `widen_radius` per tier; `widen_rivers` on a synthetic 3×3 grid dilates a centre major
  to its neighbours, and radius 0 leaves them untouched (§6b).
- **Meander** (§6c): `meander_offset` is bounded to `[-amp, amp]`, non-vacuous, coherent (no
  adjacent-cell teleport), and an exact no-op at `amp = 0`; `meander_rivers` on a synthetic grid over a
  flat cfg relocates the mask, keeps river cells alive, is deterministic, and is an exact no-op at
  `amp = 0`.

`tests/loam/test_config.spr` covers `loam/config.sprout`, the `key value` (Int) reader the demo
uses to load these knobs from a file: comment/blank tolerance, multi-key parse, malformed-value
skip, and `cfg_int` default fallback.

## 7a. Config-driven demo (`examples/gfx/terrain_rivers_demo.sprout`)

Map size, terrain-gen (`seed / octaves / lattice0 / elevation_levels`) and the `HydroParams` tiers
are read from a config file named as the first CLI arg — so the demo re-runs with different settings
**without recompiling**, the workflow for rescaling the world until rivers span many tiles:

```
just run-gfx examples/gfx/terrain_rivers_demo.sprout examples/gfx/terrain_rivers.conf
```

Int-only (the stdlib cannot parse a `Double` from text; every scale knob is an `Int`, appearance
Doubles stay compile-time). No arg or an unreadable path falls back to defaults that byte-match the
former hardcoded constants, so the stock world is unchanged. `terrain_rivers.conf` is a documented
sample that rescales for longer rivers (`lattice0 128`, `octaves 3`).

## 8. Roadmap (raft game)

1. **Valley carving** — ✅ landed (§6a): river cells sunk into shaded channels with banks, depth by
   tier, config-tunable.
1b. **Width** — ✅ landed (§6b): major rivers dilate to multiple tiles, config-tunable radius.
1c. **Meander** — ✅ landed (§6c): the river mask is displaced laterally by a coherent, flatness-gated
   noise field so the D8 staircase reads as sinuous ribbons, config-tunable amplitude/wavelength.
   Still to do: the **animated translucent water surface** on top of the
   opaque bed (a `uTime` water shader — runtime work), with **waterfalls** where the carve/band drop
   between consecutive flow cells is steep, and **dam** props placed like trees.
2. **Raft mechanics** — current from `flow_dir` + accumulation (downstream cheap, upstream
   portage); navigability tiers from a true Strahler order; rapids where the band drop is steep;
   confluences = junctions, mouths = ports, lakes = hubs.
3. **River graph** — promote the per-cell grid to a `RiverNetwork` of segments/junctions
   (`Dict Set` adjacency) for route queries.
