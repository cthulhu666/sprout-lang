# Sprout gfx engine API — generalization (v0)

Status: **design — approved in scope ("full sweep"), API pending sign-off.** No
code until the concrete API below is confirmed. This doc is the implementation
overview for a wide refactor of the `stdlib.gfx` surface (AGENTS.md Design Change
Process) and the durable record of the target engine API.

## 1. Problem

`stdlib.gfx` has accreted **demo-specific** primitives instead of a small set of
general ones — the opposite of what a game engine wants. Concretely:

- **Two parallel instancing systems.** A generic `instance_push(handle,…)` /
  `draw_instanced(handle)` (flat per-model buffer, no culling) that is **used by
  zero code in the repo**, and a `tree_push(group,model,…)` /
  `draw_trees_culled(count,cull_dist)` that is the *same idea plus* spatial-group
  frustum + distance culling — but hardcoded to "trees" (`GFX_TREE_MODELS`, tree
  naming). The general primitive atrophied; the special case grew the features.
- **Terrain-locked mesh capture.** `capture_tile(cx,cz,top_y,size,rgb,dn,ds,de,dw)`
  bakes a terrain top-quad + four band-drop walls — one demo uses it.
- **Per-entity draws in crowds.** The ECS/character demos draw N entities with N
  `draw_model` calls; no instancing path is available to them as a crowd.
- **Toys / dead weight.** `draw_spinning_cube` (one demo); `terrain_begin/end`
  (just `Begin/EndShaderMode`, misnamed, used by no example).

Nothing about `draw_trees_culled` is tree-specific: it is a **spatially-bucketed
instanced renderer with frustum + distance culling** — what trees, rocks,
buildings, crowds, and grass all want.

## 2. Goals / non-goals

**Goals.** One instancing system for all repeated props. Generic static-mesh
building (any app bakes meshes, not just terrain). Retire demo-only surface.
Keep the change behavior-preserving where it can be (the rivers demo must look
and perform identically after migration; the tree distance-LOD from #249 must
carry forward intact).

**Non-goals.** No new rendering *features* (no shadows, LOD model-swap, etc. —
those stay in `BACKLOG.md`). No renderer-backend change (still raylib). Not a
rewrite of the working parts (camera, input, `draw_grid`, `draw_captured`,
`mesh_capture_*`, animations are already general — left alone).

## 3. Inventory — keep / generalize / retire

| Current                         | Verdict | Notes |
| ------------------------------- | ------- | ----- |
| `open_window`,`close_window`,`set_target_fps`,`get_frame_time`,`window_should_close` | keep | general |
| `set_camera`,`frame_begin`,`frame_end`                                              | keep | general |
| `key_down`,`mouse_*`,`space_pressed`,`button`,`button_held` + key/mouse consts      | keep | general (shipped this cycle) |
| `draw_grid`,`draw_plane`,`draw_cube`,`draw_model`,`draw_fps`                         | keep | general raylib wrappers |
| `load_model`,`load_animations`,`animation_*`,`update_animation`                     | keep | general |
| `mesh_capture_begin/end`,`draw_captured`                                            | keep | general static-mesh baking |
| `instance_push(handle,…)`,`draw_instanced(handle)`                                  | **subsume** | dead flat path → folded into the grouped system |
| `tree_push`,`draw_trees_culled`                                                     | **generalize** | → the unified instancing API (§4a) |
| `capture_tile`                                                                       | **generalize** | → `capture_quad` (+ terrain composes it, §4b) |
| `draw_spinning_cube`                                                                  | **retire** | demo toy (§4c) |
| `terrain_begin`,`terrain_end`                                                         | **retire** | just `Begin/EndShaderMode`; unused, misnamed |

## 4. Target API

### 4a. Unified instancing (the flagship)

Replace *both* instancing paths with one spatially-bucketed, culled system:

```
# Register one instance of `model` into spatial bucket `group` (caller-assigned;
# keep groups spatially coherent — one per chunk — so culling can drop them wholesale).
instance_push(group: Int, model: Int, x: Double, y: Double, z: Double, angle: Double, scale: Double) -> Unit !{IO}

# Draw all groups [0, group_count): one DrawMeshInstanced per model over the
# visible groups' compacted instances. A group draws only if its centre is within
# `cull_dist` of the camera eye (distance-LOD; <= 0 disables) AND its AABB is in
# the frustum. `group_count` is the scan bound over spatial buckets (the shim
# derives the MODEL count internally from the loaded-model registry).
draw_instances(group_count: Int, cull_dist: Double) -> Unit !{IO}

# Drop all instances in one spatial bucket (counts -> 0, allocations kept). For
# DYNAMIC callers (moving crowds) that re-push their group every frame.
instance_clear(group: Int) -> Unit !{IO}
```

- **`group_count`, not model_count** — mirrors the working `draw_trees_culled`,
  whose loop bound is the group scan and which reads model count from
  `g_model_count`. (The demo passes `tree_group_count` today.)
- **Static vs dynamic — designed in now, not retrofitted.** Trees push **once** at
  setup and never move (no clear needed). Phase-4 crowds (flocking/agents) **move
  every frame**, so they `instance_clear(group)` then re-push each frame; the cull
  path must therefore recompute a group's AABB from its current instances on
  draw, not assume frozen buffers. Adding `instance_clear` up front keeps the API
  whole so the crowd migration doesn't re-break `instance_push`/`draw_instances`.
  A per-group clear (not reset-all) lets static trees and dynamic crowds coexist:
  each dynamic caller clears only its own bucket(s).
- **Subsumes the dead flat path**: "no grouping, no culling" = push into group 0
  and call `draw_instances(1, 0.0)`. So `instance_push(handle,…)` /
  `draw_instanced(handle)` are removed (verified: no caller in `loam/`, `stdlib/`,
  or `examples/`).
- **Carries #249 forward**: the squared-distance cull + `cull_dist` land here
  verbatim under the general name.
- Shim internals rename off "tree": `GFX_TREE_MODELS`→`GFX_INSTANCE_MODELS`,
  `GFX_MAX_TREE_GROUPS`→`GFX_MAX_GROUPS`, `g_tg*`→`g_grp*`. The 64-model /
  4096-group caps stay (documented), not tree-semantics.
- Naming note: `instance_push` **changes signature** (6-arg flat → 7-arg
  grouped). Safe only because the flat path is dead (confirmed above).

### 4b. Generic mesh capture

Replace the terrain-specific tile with a general quad (LANDED):

```
capture_quad(p0x,p0y,p0z, p1x,p1y,p1z, p2x,p2y,p2z, p3x,p3y,p3z,   # 4 corners
             nx,ny,nz,                                              # face normal
             r,g,b: Int) -> Unit !{IO}    # triangles p0,p1,p2 and p0,p2,p3
```

`capture_tile` is **removed**; a caller bakes arbitrary static geometry from
`capture_quad`. Terrain composes it: a tile is one top quad + a wall quad per
exposed (stepping-down) side. The 18-arg extern was FFI-checked (emits/lowers
cleanly), so the explicit 4-corner form is used — no compact/vec workaround.

**Measured decision — the terrain bake emits inline, NOT via a pure List
decomposition.** The plan first materialised the decomposition as a pure,
unit-tested `loam.terrain_mesh.tile_quads : … -> List Quad` (top + wall quads as
records) and fed it to `capture_quad`. It was correct (11 passing tests) but
**regressed the 1024² bake ~3.2× (28.5s → 92s)** — not from the extra FFI (1→5
crossings/tile is ~0.2µs/tile) but from **heap allocation**: ~30 objects/tile
(a `List` of `Quad`-of-`Vec3` records) × 1M tiles ≈ 30M allocations + GC. So the
decomposition + module + tests were **dropped**. `bake_tile` instead computes
corners and calls `capture_quad` **inline** per face — generic *and*
allocation-free, back to the 28.5s baseline, identical render.

Lesson (recorded so it isn't re-attempted): a per-cell Sprout decomposition that
heap-allocates does not scale to 1M cells; the generic primitive is fine, the
intermediate data structure is the cost. This mirrors real engines — a generic
mesh API driven by a tight allocation-free mesher, not per-cell quad objects.
The bake geometry is verified by screenshot (as all gfx is), not unit tests.

### 4c. Retire

- `draw_spinning_cube`: fold the spin into `spinning_cube.sprout` if a generic
  transform is exposed, else drop the toy (the demo is illustrative only).
  **Low priority; optional** — flag if it needs an unexposed transform primitive.
- `terrain_begin`/`terrain_end`: remove; nothing calls them.

## 5. Migration plan

**Step 0 — branch hygiene (blocker).** The sweep renames `draw_trees_culled`,
which **open PR #249** exists to modify. Land #249 first, then branch the sweep
off fresh `master`; do **not** build on #249's branch. (Alternative: fold #249
into the sweep and close it — but #249 is a clean, self-contained 27→56 FPS win;
merging first keeps history reviewable.)

Then, one PR, **phased commits** (a capture-path problem must not block the
instancing win):

1. **Instancing unification — LANDED.** `instance_push`/`instance_clear`/
   `draw_instances`; flat + tree paths removed; shim de-tree-ified. rivers demo
   migrated; overview 59 FPS (was 56), renders identically.
2. **Mesh capture — LANDED.** `capture_quad` (general); `capture_tile` removed;
   rivers demo bakes inline (no pure decomposition — it regressed 3.2×, §4b).
   Bake back to 28.5s baseline, terrain renders identically.
3. **Retire** — drop `draw_spinning_cube`/`terrain_begin`/`terrain_end`; fix
   `spinning_cube.sprout`. (next)
4. **Migrate ECS/character demos (optional this pass)** — crowds via
   `instance_push`/`instance_clear` instead of per-entity `draw_model`. Bigger;
   may defer to a follow-up.

## 6. Verification

- GPU behaviour (all of the instancing + capture surface) — verified by
  `compile-examples-stage1` + a clean screenshot per migrated demo (frame-budget
  self-close, **no `SIGKILL`** — hard-kills leak GL contexts and crash
  `UploadMesh`), plus a bake-time check. The bake geometry is *not* unit-tested
  (the pure-decomposition attempt regressed 3.2×, §4b), consistent with the rest
  of the screenshot-verified gfx shim. Confirmed: rivers overview 59 FPS, bake
  28.5s, renders identically before/after.
- `APPROVED_BUILTINS`/seed untouched (gfx shim ≠ `sprout_runtime.c`; not bundled
  into the compiler).

## 7. Resolved API decisions

1. `draw_instances(group_count, cull_dist)` — `group_count` is the bucket scan
   bound; the shim reads model count from the registry. Plural noun over the old
   `draw_instanced` adjective.
2. `capture_quad` — explicit 4-corner form (18 extern args); FFI-checked to lower
   cleanly, so no compact/vec workaround. Not consumed via a record type on the
   hot path (see §4b).
3. 64-model / 4096-group caps kept (documented constants), not made dynamic —
   generous for current demos; revisit if a scene needs more.
