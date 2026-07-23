# Scene / Entity System (ECS) — v0

**Status:** first cut implemented (`stdlib/scene.sprout`, `tests/stdlib/test_scene.spr`,
`examples/gfx/ecs_crowd.sprout`). Forward-looking design for the graphics/game arc
(follows `docs/graphics-v0.md`; the M0–M4 graphics work is in PR #232). §§1–8 are the
original design; **§9 records what the prototype actually validated and where it
diverged from the sketch.**

## 1. Problem statement

`examples/gfx/character_crowd.sprout` hardcodes "5 characters, fixed positions,
one shared clip." Any real scene needs *N heterogeneous things, each with its own
data, updated and drawn generically* — varied positions, skins, animation clips,
and eventually gameplay state (velocity, health, AI). A scene/entity system is the
structure that generalizes the crowd example into that.

**Key observation:** the crowd example is already a hand-rolled 2-component,
1-system ECS. `models: MutVec Int` and `frames: MutVec Double` are *component
arrays*; `step_characters` is a *system* that iterates entities by index. This
proposal is just generalizing that shape.

## 2. Goals / non-goals

**Goals**
- A minimal, data-oriented scene representation that generalizes the crowd demo.
- Systems as plain functions over the scene (animation, render to start).
- Built only on what exists today (`MutVec`, the gfx shim, `anim_advance`).
- Serve as a real forcing function for the records-v0 design (see §6).

**Non-goals (v0)**
- Archetypes / heterogeneous component sets (every entity has every component).
- Growable storage, despawn/free-lists, parent-child transforms.
- A general physics/AI framework — just the entity/component/system skeleton.

## 3. Prior-art survey

- **Godot** — scene *tree* of Nodes (node-based scene graph); transforms compose
  parent→child. OO, ergonomic, mutable object graph.
- **Unity** — `GameObject` + component objects (object-component model); its newer
  DOTS stack is a data-oriented archetype ECS.
- **Bevy** (Rust) — ECS-first: entities are IDs, components in typed storages,
  systems are functions the scheduler runs.
- **Flecs / EnTT** (C/C++) — archetype and sparse-set ECS libraries.

Two families: **scene graph** (object tree, OO) vs **ECS** (IDs + component arrays,
data-oriented). See §4 for the choice.

## 4. Architecture choice: SoA-ECS

**Chosen: a structure-of-arrays ECS.** It fits Sprout for three concrete reasons:
- **Storage already exists.** Component arrays are `MutVec`s; `MutVec Double` hits
  the unboxed `Vector Double` fast path (raw f64 load/store, no boxing).
- **Systems are functions over arrays** — the functional style Sprout rewards.
- **It avoids what Sprout lacks** — no inheritance, no mutable object graph (a
  scene graph would want both).

A scene graph (Godot-style) is rejected for v0: it needs OO ergonomics and mutable
node trees that don't map cleanly onto Sprout today.

## 5. Sketch

An **entity** is an integer index. A **Scene** is an immutable record holding
mutable component arrays (the record is immutable; mutation flows through the
`MutVec` handles it holds):

```
type Scene = Scene {
  count: Int,            # live entity count (fixed capacity behind the arrays)
  model: MutVec Int,     # component: model instance handle
  clip:  MutVec Int,     # component: animation clip index
  frame: MutVec Double,  # component: animation playhead
  speed: MutVec Double,  # component: animation speed
  pos_x: MutVec Double,  # component: transform
  pos_z: MutVec Double,
  rot_y: MutVec Double,
}
```

- **spawn(scene, …components…) -> Int** — write components at slot `count`, bump
  count, return the entity id.
- **animation_system(scene)** — per entity: `anim_advance` the frame in place,
  `gfx.update_animation`.
- **render_system(scene)** — per entity: `gfx.draw_model` at its transform.
- **frame loop** — `animation_system; frame_begin; draw_grid; render_system;
  frame_end; recurse`.

The crowd demo is then: create a scene, `spawn` 5 entities with varied components,
run the two systems — but the same code scales to N with mixed skins/clips/positions.

## 6. Open questions to validate FIRST (load-bearing)

> **Answered by the prototype — see §9.** §6.1 (record ergonomics) resolved
> *negatively*: records don't codegen, so the §5 record `Scene` is replaced by a
> single-constructor ADT. §6.2 (the `wrap` wart) materialized as predicted and
> steered the substrate away from `wrap`. §6.3 (fixed capacity) stands.

1. **Records-of-`MutVec` ergonomics.** Is `Scene` as an immutable record holding
   mutable-array handles pleasant to write and pass around? Records are still
   experimental (`docs/records-v0.md`). **Validate this before building the ECS** —
   it is the load-bearing assumption, and the answer feeds back into records-v0.
   Fallback if records aren't ready: thread the arrays as explicit params (as the
   crowd example does) or pack them into a `wrap` over a tuple.
2. **Cross-module `wrap` annotation wart** (BACKLOG §9 P2). If `Scene`/`Entity` are
   `wrap` types, a game author writing helpers over them *in their own module* hits
   the annotation-canonicalization bug. This is the first place it really bites —
   may need fixing before the ECS is usable by end users (vs. just stdlib).
3. **Fixed capacity.** `MutVec` is fixed-size, so v0 is capacity + `count` (spawn up
   to capacity). Despawn/free-list/growable storage are follow-ups.

## 7. Recommended first cut

A minimal **fixed-capacity SoA-ECS**: one `Scene` (a single-ctor ADT of component
`MutVec`s — see §9.2, not the record §5 drew), a `spawn`, and the systems — then
rebuild the crowd on top as a before/after. This landed as `stdlib/scene.sprout`
(gfx-free core) + `examples/gfx/ecs_crowd.sprout` (gfx systems), exactly as
sketched. See §9 for what actually shipped.

## 8. Path to an engine

Each new capability is "add a component + a system":
- `velocity` (Vec) + `movement_system` → things move.
- `collider` + `physics_system` → things collide.
- `ai_state` + `behavior_system` → things act.
This progression is how the demo becomes an engine; the ECS skeleton is the brick
it all stacks on.

## 9. What the prototype validated (and where it diverged)

> **Superseded by §10.** This section records the first ECS prototype
> (`stdlib.scene` + `ecs_crowd`, 6 components). §10 documents the current state:
> the prototype grew into the **Loam** engine, `scene` moved out of `stdlib/`, and
> the demo became a wandering-agent crowd. Read §9 as history; §10 is current.

The first cut answered §6's load-bearing questions empirically. The findings
inverted the recommended substrate in §4/§5.

### 9.1 Records are non-executable — the record `Scene` (§5) was rejected

The §6.1 spike (a record holding `MutVec` component arrays) **fails at codegen**.
Records parse and type-check — the AST/inference carry `TRecord`/`@rec:` markers
and `tests/conformance/run/record_types.spr` exists — but `translate_expr` in
`stdlib/compiler/ast_to_ir.sprout:926` bails with `record not yet supported`, and
`TGetField` (field access) at `:927` likewise. No runner exercises
`tests/conformance/run/`, so that fixture is **dormant**, not passing. Records are
frontend-only groundwork today. Wiring record codegen (mirroring single-ctor-ADT
`IRMakeCtor` construction + the existing `IRGetField` projection) is a tractable
follow-up but out of scope for "simple ECS"; it is the right path once records
are wanted for real (see `docs/records-v0.md` §10).

### 9.2 Substrate: single-constructor ADT, not a record or a `wrap`-tuple

`Scene` is a **single-constructor ADT over the component arrays**:

```
type Scene (..) =
  | Scene (MutVec Int) (MutVec Int) (MutVec Double) (MutVec Double) (MutVec Double) (MutVec Double)
#          count_cell   model         pos_x           pos_z           frame           speed
```

The §6.1 fallback offered a `wrap` over a tuple. That was tried and **hit the
§6.2 cross-module annotation wart** immediately: a helper in the *example* module
annotating `s: Scene` failed with `stdlib.scene.Scene vs Scene` (BACKLOG §9 P2) —
exactly "the first place it really bites." A single-ctor ADT is the same nominal,
zero-indirection shape `MutVec` itself uses, and it **resolves unqualified across
module boundaries**, so game-author code stays clean (`s: Scene`, no
qualification). The immutable constructor still shares mutable component storage
through the `MutVec` handles it holds — the SoA property is unchanged. The `wrap`
wart (§6.2) therefore remains open but no longer blocks the ECS.

### 9.3 The core is renderer-independent; gfx systems live in the example

`stdlib/scene.sprout` holds storage + `spawn` + accessors + one pure system
(`move_system`), and imports **no** gfx — so it links and tests under the core
runtime (`tests/stdlib/test_scene.spr`, 10 assertions). The gfx systems
(`animation_system`, `render_system`) live in `examples/gfx/ecs_crowd.sprout`,
which imports both `stdlib.scene` and `stdlib.gfx` (raylib links only under
`run-gfx`). This is a cleaner split than the §5 sketch implied: the ECS core does
not depend on the renderer.

### 9.4 `spawn` and the count cell

`spawn` needs to bump a live count, but a `MutVec` is Sprout's only mutable cell,
so `count` is a one-element `MutVec Int` (`count_cell`), not a bare `Int` field as
§5 drew it. `spawn(s, model, x, speed) -> Int` writes at the count slot, bumps the
cell, and returns the id. Fixed capacity + `count` per §6.3; despawn/free-list/
growable storage remain follow-ups.

### 9.5 Before/after

`examples/gfx/character_crowd.sprout` (hand-threaded parallel arrays, hardcoded
"5") is kept intact as the "before"; `examples/gfx/ecs_crowd.sprout` is the same
scene expressed as data — `spawn` N entities, run systems that iterate generically.
Adding a character is one more `spawn`, not a new code path.

### 9.6 Scaling to 100 — the §8 growth path, demonstrated

The example spawns **100 entities on a 10×10 grid** (verified rendering: a full
grid of independently-posed, lit, textured characters). Getting there from the
5-in-a-line version was exactly the proposal's §8 recipe — *add a component*:
`Scene` gained a `pos_z` array (now 6 components) and `spawn` a `z` parameter.
Crucially, **the systems did not change**: `animation_system` and `render_system`
still iterate `scene_count` blind to how many entities exist or where they sit.
Capability grew by adding data, not by rewriting loops — the whole point of the
entity/component/system split.

## 10. The Loam engine — prototype → engine seed

The prototype became **Loam**, "the seed of a game engine" (botanical, like
Sprout: the fertile ground apps root into). This realises §8 ("Path to an
engine") and reorganises the code by responsibility.

### 10.1 The tiers — and game code leaves `stdlib/`

`stdlib/` is the *language* standard library; a scene/entity system and an AI are
game-domain code and do not belong there. So:

| Tier | Lives in | Modules |
|---|---|---|
| Language stdlib | `stdlib/` | `stdlib.rng` (general PRNG), `stdlib.gfx` |
| **Loam model** | `loam/` (a second package root) | `loam.scene`, `loam.agent` — **graphics-free, headless-tested** |
| **Loam view** | `loam/` | `loam.view` — reusable gfx systems (animation, render, orbit camera) that *read* Scene components |
| Example app | `examples/gfx/` | `ecs_agents`, `ecs_flocking` — supply only layout + the frame loop |

The `model` / `view` split inside `loam/` is deliberate and load-bearing:
`loam.scene`/`loam.agent` never import `stdlib.gfx`, which is exactly what lets the
whole game loop be asserted headless (§10.4). `loam.view` is the optional rendering
layer — it depends on gfx and so is exercised only through the examples (it cannot
be linked without the raylib shim). Apps compose the view systems and supply the
world authoring the engine has no business dictating: where entities spawn, the
camera framing, the window.

`loam.*` resolves outside `stdlib_root` via the sanctioned second-package-root
mechanism: `compile-driver --emit-ir <stdlib> --package-root <repo-root> <file>`
(`resolve_module_path` extra roots; `module_name_to_path` itself stays locked to
`stdlib_root`). `run-gfx`, `compile-examples`, and the new `test-loam` lane pass
`--package-root`. A general-purpose *seeded* PRNG is not game-specific, so it
stays in stdlib as `stdlib.rng` (a linear congruential generator; determinism is
what makes the game loop reproducible and testable).

### 10.2 `scene` — 9 components, and the arity ceiling

`loam.scene` extends the prototype's store to the agent's needs: it adds
`heading`, per-entity `rng` state, an `energy` stat, and a `resting` phase bit,
for **nine** `MutVec` components. Nine is the hard ceiling: `ast_to_ir` rejects a
single-constructor ADT of arity 10 (`sprout_make` goes to 9). A tenth component
must therefore *group* existing ones into a sub-struct (e.g. a `Transform` holding
pos+heading), **not** widen the constructor — grouping mixed-typed fields also
shrinks the same-typed-accessor footgun. To fit nine, per-entity animation *rate*
was dropped: cadence is a single global constant in the view (a uniform walk pace,
arguably more realistic). `test_scene` sentinels every component (a distinct value
per array, read back through each accessor) — the only guard against a miscounted
`match` slot returning a wrong-but-same-typed component.

### 10.3 `agent` — a pure AI hook and one game-loop tick

`loam.agent` is the behaviour layer:

- `agent_decide(seed, heading) -> (heading, seed)` — **pure** (no IO, no Scene):
  occasionally turn to a fresh random heading, else hold course. This is the
  "AI" — unit-testable on its own and the single swap point for smarter behaviour.
- `group_of(i, num_groups) -> Int` — **pure**: the flock an entity belongs to,
  derived as `i mod num_groups`. Group is *not* a stored component — the `Scene`
  constructor is already at its 9-field ceiling (§10.2), and a round-robin id
  mapping needs no slot. The view must place/tint groups by this same function.
- `cohere(nh, px, pz, gx, gz) -> Double` — **pure**, inverse-trig-free: bends a
  wander heading `nh` toward the group centroid `(gx, gz)`. The 2D cross product of
  the facing unit vector with the vector-to-centroid, over the distance, is exactly
  `sin(heading-error)` — signed, so it gives direction *and* magnitude of the turn
  (scaled, clamped). `atan2` is absent from `stdlib.math`, so this is how an agent
  steers toward a point at all. When the centroid *is* the agent's own position
  (distance ~0) it no-ops — the property both step functions below lean on.
- `should_jump(seed, energy, jump_denom) -> Bool` — **pure**: whether a walking
  agent launches a rare leap this tick. Three gates: jumping enabled
  (`jump_denom > 0` — callers pass 0 to forbid it), stamina **strictly above half**
  (a jump spends half, so it can't be afforded at or below), and a 1-in-`jump_denom`
  roll (300 for the wander tick — far rarer than a turn's 1-in-40). A jump has **no
  Scene slot of its own**: the `resting` phase field carries it as **0 walking,
  1 resting, ≥2 jumping**, where the ≥2 value *is* a countdown that decrements each
  tick and holds the agent in an in-place leap until it hits 0. One Int, three states
  plus a duration — the same "reuse the field, respect the 9-field ceiling" move as
  `group_of`.
- `world_step_flock(s, bound, num_groups) -> Unit !{IO}` — advances every entity one
  **fixed** timestep, partitioning the crowd into `num_groups` **flocks**, in **two
  phases** so the update is simultaneous: (A) snapshot each group's centroid from
  *current* positions into scratch arrays, then (B) for each entity dispatch on its
  phase — resting (regain energy in place), jumping (count the leap down), or walking
  (decide → bend toward its group centroid via `cohere` → step + wall-reflect →
  spend energy). Computing centroids inline in phase B would be O(n²) *and*
  order-dependent (agent 0's move would shift the centroid agent 1 sees), breaking
  reproducibility. Flocking agents **do not jump** (it passes `jump_denom = 0` — a
  leap would fling a member out of its flock). Touches only component arrays — **no
  graphics**. O(n) per tick.
- `world_step(s, bound) -> Unit !{IO}` — plain wandering, **no flocking, but agents
  may occasionally jump**. It is the shared tick core with one singleton group *per*
  agent (`num_groups = live count`, so `cohere` no-ops) and jumping enabled. Keeping
  this as the two-arg entry point means the wandering-crowd example needs no change to
  gain leaps, and flocking stays jump-free.

The old `move_system` is folded into these — one movement path, dispatched by the
`resting` phase field (walk / rest / jump).

### 10.4 The model/view split, made enforceable

Both step functions are the *same* functions the renderers drive and the headless
tests assert — all with no window:

- `tests/loam/test_agent.spr` (drives `world_step`): the AI on both branches,
  run-to-run determinism, a rester that does not move, edge containment, and
  **jumping** — `should_jump`'s gating (stamina/enable/rarity) plus a leap through
  `world_step`: spends half stamina, holds position while airborne, lands back to
  walking.
- `tests/loam/test_flock.spr` (drives `world_step_flock`): `group_of` binning,
  flock determinism, and **cohesion** — two groups spawned as loose clouds whose mean
  distance-to-centroid shrinks while the groups stay apart.

So "test the game loop headless" is structural, not aspirational: the loop is
verified without gfx, and each view is a pure *reader* of components it never mutates.

### 10.5 The demos — a wandering crowd and N flocking groups

Two sibling examples share the engine *and* the `loam.view` systems (animation,
render, orbit camera), so each file is now just world authoring plus a frame loop —
they differ mainly in which step they drive:

- `examples/gfx/ecs_agents.sprout` — the **plain wandering crowd** (`world_step`),
  where agents also **occasionally leap**. One knob, `agent_count`; a near-square grid
  and the wander arena derive from it. Supersedes `ecs_crowd` (kept `character_crowd`
  as the pre-ECS baseline).
- `examples/gfx/ecs_flocking.sprout` — **N groups, each of which flocks together**
  (`world_step_flock`): members steer toward their shared centre of mass. Two knobs,
  `agent_count` and `group_count`; the per-group spawn cloud, the ring the group
  homes sit on, the wander arena, and the camera framing all derive from them. Groups
  have **no colour channel** (raylib's `draw_model` takes no tint, and adding one is a
  runtime/builtin change deferred by scope — `BACKLOG.md §9`), so the flocks are
  distinguished purely **spatially**: each group spawns as a loose cloud around its
  own home on a ring, and cohesion tightens it into a distinct crowd there.

Both face their heading (`draw_model`'s Y-rotation) and animate on the **run** clip
while walking / **idle** while resting — selected per entity from the `resting` phase
field. `ecs_agents` adds a third state: phase ≥ 2 poses on the **jump** clip, via
`loam.view`'s `animation_system_jump` (the flocking demo uses the two-state
`animation_system`, since flocks never leap). Both systems share one `pose_entity`
helper, so the jump variant adds a clip choice, not a duplicated loop.
The `idle`/`run`/`jump` clips are baked from the Kenney pack's FBX via
`tools/convert_kenney.sh` (the pack has no dedicated *walk* clip — `run` is its only
locomotion). The jump clip is 33 keyframes; the model's `jump_ticks` hold is tuned to
match so a leap plays through one full cycle before the agent lands.
