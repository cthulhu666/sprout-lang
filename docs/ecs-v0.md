# Scene / Entity System (ECS) — v0 proposal

**Status:** proposal, not implemented. Forward-looking design for the graphics/
game arc (follows `docs/graphics-v0.md`; the M0–M4 graphics work is in PR #232).
This doc is the entry point for the next session on this thread.

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

A minimal **fixed-capacity SoA-ECS**: one `Scene` (record of component `MutVec`s),
a `spawn`, and the animation + render systems — then rebuild
`examples/gfx/character_crowd.sprout` on top of it as a before/after. Likely lands
as a small `stdlib` scene helper + a new example. Start by answering §6.1.

## 8. Path to an engine

Each new capability is "add a component + a system":
- `velocity` (Vec) + `movement_system` → things move.
- `collider` + `physics_system` → things collide.
- `ai_state` + `behavior_system` → things act.
This progression is how the demo becomes an engine; the ECS skeleton is the brick
it all stacks on.
