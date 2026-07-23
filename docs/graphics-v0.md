# Graphics & Game Engine — v0 (design + roadmap)

**Status:** experimental. Non-normative — `docs/spec-v0.md` remains the source of
truth for the language core. This doc records the plan for building a game/3D
capability in Sprout, the architecture of the native rendering backend, and the
milestone roadmap. Milestones 0 and 1 have **landed** (working code); everything
from M2 on is proposed.

## 1. Problem statement

Sprout should be able to build a real-time interactive 3D application — starting
with a spinning cube, building toward playing a skeletal-animated character from
the `kenney_animated-characters-retro` pack, and ultimately toward the class of
things a small engine (Godot-scale) can do. This requires capabilities Sprout
does not have natively: a window, GPU rendering, a timed frame loop, floating-
point math ergonomics, and 3D-asset loading.

## 2. Goals / non-goals

**Goals**
- A thin, clearly-fenced native backend that never pollutes the core runtime,
  the test suite, or the bootstrap seed.
- Drive the frame loop, scene logic, and (progressively) the math from Sprout.
- A migration path where the engine moves *into* Sprout over time and the native
  dependency shrinks toward a swappable "give me a window + draw these triangles"
  leaf.

**Non-goals (for now)**
- A production renderer, physics, or editor.
- Committing permanently to raylib or to OpenGL (see §7, backend migration).
- Pulling large amounts of engine logic permanently into C.

## 3. Verified architectural facts (the foundation)

These were confirmed against the runtime/compiler before any code was written;
they are the load-bearing facts the design rests on.

1. **Uniform i64 ABI.** Every Sprout value crosses function boundaries as a
   64-bit word. A `Double` is stored/passed as its raw IEEE-754 bit pattern and
   only bit-cast to a real `double` at arithmetic sites (`ast_to_ir.sprout`).
2. **`extern fn` is the FFI mechanism.** Each `extern fn` resolves to a C
   function with a `long long`-uniform signature. Foreign libraries are reached
   through a hand-written C **shim** that unpacks i64 words — proven by the
   existing `double_to_string(long long bits)` (Sprout side: `extern fn
   double_to_string(x: Double) -> String`), which `memcpy`s the bits to a
   `double`. Every float coordinate in the graphics shim uses this exact trick.
3. **`String` crosses as `char*`.** Runtime externs such as `http_request`
   declare their String parameters directly as `const char*`, so the shim
   receives string data with no conversion.
4. **The link line is open.** `justfile` links `clang $IR {{runtime_src}}
   {{clang_extra}}`, so linker flags (`-lraylib -L… -I…`, frameworks) are a
   one-line extension. Native libraries *can* be linked.
5. **Performance foundation — flat `Vector Double`.** The primitive
   `Vector Double` is a contiguous, GC-managed `long long[]` whose slots hold
   f64 bit patterns (physically identical to a C `double[]`). On statically-typed
   `Vector Double` sites, `vector_get_direct` / `vector_mutset` are **inlined by
   codegen** (`IRVecGetD`/`IRVecSetD`) to raw, unchecked `double` load/store —
   no boxing, no allocation, no runtime call. `vector_mutset` mutates **in
   place**; the generic `vector_append`/`vector_set` are persistent (O(n) copy).
   The fast path fires **only** on the exact canonical primitive `Vector
   Double` — a record/ADT of Doubles, or `Vector Int`, silently falls back to
   boxed pointers.

## 4. The shim architecture

A single C file, `graphics/sprout_gfx.c`, adapts the i64 ABI to raylib:

- **Floats** cross as raw bit patterns; `as_float(long long)` reinterprets them.
- **Structs raylib passes by value** (`Camera3D`, `Vector3`, `Color`) can't cross
  the i64 ABI, so the shim holds them C-side (e.g. a global `Camera3D`) and
  exposes *decomposed* setters (`gfx_set_camera(px,py,pz, tx,ty,tz, fovy)`).
- **Objects raylib returns by value** (`Model`, `Texture`) will be kept in a
  C-side registry, with Sprout receiving an integer **handle** (not needed for
  the cube; required for the character).
- **Void-semantics** functions return `long long` 0 so Sprout binds them at
  `Unit` (mirrors `vector_mutset`).

**Isolation — the critical constraint.** `graphics/sprout_gfx.c` lives *outside*
the `runtime/*.c` glob and links **only** via `just run-gfx`. The Sprout binding
`stdlib/gfx.sprout` is a non-prelude module imported explicitly, so its externs
never enter the bootstrap seed or the core test link. Nothing else in Sprout
pays for raylib. (`stdlib/gfx.sprout` sits under `stdlib/` only because that is a
loader-resolvable path; it is a *native binding*, not portable core stdlib.)

## 5. Build & configuration

- `just run-gfx <file>` — compiles the shim + links raylib + runs.
- `SPROUT_RAYLIB_PREFIX` — overrides the raylib location (default: `brew
  --prefix raylib`). No install path is hardcoded in tracked files.
- `SPROUT_GFX_MAX_FRAMES=N` — the shim auto-closes the window after N presented
  frames, so a demo runs non-interactively (the M1 canary; see §8).
- `SPROUT_GFX_SCREENSHOT=<path>` — the shim saves one PNG of a warmed-up frame
  (relative to the working directory) so a change can be verified *visually*
  without a human at the screen. Pair with `SPROUT_GFX_MAX_FRAMES` to capture and
  exit. Shim-only (no Sprout API); the output is gitignored (`sprout_shot.png`).
- macOS link line: `-lraylib -framework Cocoa -framework IOKit -framework
  CoreVideo -framework OpenGL`. Note: macOS serves OpenGL capped at 4.1 over
  Metal (observed: `Version: 4.1 Metal`) — OpenGL is a *bootstrap* leaf, not the
  long-term target.

## 6. Milestones

### M0 — loop spike ✅ (landed)
Verified the "Sprout drives the loop" architecture: a tail-recursive IO frame
loop (`if should_close then () else do { …; loop(n+1) }`) runs in **flat stack**.
A 20,000,000-frame spike completed cleanly (exit 0, correct count) — impossible
unless tail-call-optimized. The proven loop shape is the contract for the engine.
(Observed footprint vs. RSS gap re-confirmed the §3.5 rule: boxed `Int`
arithmetic churns garbage; hot-path math must use flat `Vector Double`.)

### M1 — spinning cube ✅ (landed)
`examples/gfx/spinning_cube.sprout` drives a real GPU window: raylib 6.0 initialized,
shaders compiled, render batch in VRAM, 60 FPS, cube rotating on a grid via
raylib's own matrix stack (`rlRotatef`) — so the Sprout side supplies only a
scalar angle (`to_double(frame) * 0.8`). **Zero new core builtins, zero seed
impact.** Files: `graphics/sprout_gfx.c`, `stdlib/gfx.sprout`, `run-gfx` recipe.

### M2 — math library (proposed; the first real language exercise)
Build `Vec2/3/4`, `Mat4`, `Quat` and operations. **Representation contract:** all
bulk/hot data is flat primitive `Vector Double` + `vector_mutset`, pre-allocated
and reused — never records/ADTs of Doubles (§3.5). This is not a compromise; it
is what C engines do (`float[16]` matrices, flat vertex arrays). Requires the
first `libm` builtins — `sqrt`, `sin`, `cos` — the legitimate builtin case
(transcendentals can't live in Sprout). **Needs user approval + APPROVED_BUILTINS
entries.** See `tests/stdlib/test_native_mutmatrix.spr` for existing groundwork.

### M3 — model loading & static draw ✅ (landed)
`examples/gfx/character_view.sprout` loads the Kenney character and draws it rotating
on the grid. Introduced the C-side model handle registry (§4:
`gfx_load_model`/`gfx_draw_model`), plus tested `tan`/`radians`/`camera_fit_distance`
math for framing. Loaded models are drawn with a small directional-light GLSL
shader (diffuse + ambient) so they read as 3D, not flat silhouettes.
**Asset-pipeline finding:** the Kenney pack ships **FBX, which
raylib cannot load** — it must be converted to GLB (which preserves the mesh and
the 58-bone skeleton). `tools/convert_kenney.sh` (Blender headless) does this; the
pack itself is not vendored (CC0, download from kenney.nl), but the converted
`assets/models/characterMedium.glb` is (see NOTICE). raylib 6.0 note: bones live
under `model.skeleton.boneCount`, not the older flat `model.boneCount`.

### M4 — skeletal animation playback ✅ (landed)
`examples/gfx/character_animated.sprout` plays the Kenney Idle clip — the original
goal: an animated character on screen. raylib does the GPU skinning via
`UpdateModelAnimation`; the Sprout loop advances the playhead with the tested
pure `anim_advance` (loops at `keyframeCount`). **Key simplification:** raylib
skins by *bone index*, so an animation loaded from a *separate* GLB
(`character_idle.glb`) drives the model as long as the skeletons match
(`IsModelAnimationValid` confirms) — no Blender action-merging into one file.
Shim adds a model-agnostic animation-set registry (`gfx_load_animations` /
`gfx_update_animation`). raylib 6.0 note: `ModelAnimation.keyframeCount`, not
the older `frameCount`. The `run` and `jump` clips are now
baked too (`assets/models/character_run.glb`, `character_jump.glb`, via
`tools/convert_kenney.sh`) and drive the walking / occasionally-leaping agents in
`examples/gfx/ecs_agents.sprout` (see `docs/ecs-v0.md` §10). Follow-up: one-GLB
packing (idle+run+jump in a single file).

### M5+ — hollow out the black box (proposed)
Progressively move engine work into Sprout: glTF parsing (Sprout already has
`bytes` + `json`), skinning matrices, camera, a scene graph. Demote raylib toward
a thin draw leaf; eventually swap the leaf for a modern API (Metal/WebGPU — §7).

## 7. Backend migration (why not OpenGL forever)

raylib runs on OpenGL, which is legacy (last release 4.6 in 2017; Apple
deprecated it in 2018, caps it at 4.1 over Metal). It is an excellent *bootstrap*
leaf — simplest real GPU API, hidden inside raylib, we write almost none of it.
The long-term leaf, once Sprout owns the engine layer, should be a modern API:
**WebGPU** (portable, sanely designed) or **Metal** (best on Apple). The shim
boundary keeps the leaf swappable, so starting on GL does not lock us in.

## 8. Testing / DoD deviation (explicit)

M1 cannot meet the normal TDD/test gate: raylib can't `InitWindow` on a headless
CI box (no GL context), and the cube has almost no pure-Sprout logic to unit-test.
**Accepted deviation:** treat "compiles + links raylib + runs `SPROUT_GFX_MAX_
FRAMES=N` + exits clean" as a **manual example-canary** (the category AGENTS.md
already uses for examples), with the visual check performed by a human. Pure math
introduced from M2 on *is* unit-testable and follows the normal TDD gate.
