# Loam camera controls — prior-art survey & design (v0)

Status: **research / design — no code**. This doc surveys state-of-the-art camera
control for loam's class of app, maps each interaction to the raw input + math it
needs, and tees up the one decision that requires sign-off: **which raw input
primitives to expose from the raylib shim** (a builtin addition — AGENTS.md
Collaboration rule 6). Nothing here is implemented yet.

## 1. Problem statement

Loam's terrain viewer is driven **entirely by ten on-screen GUI buttons**
(`terrain_rivers_demo.sprout` render loop: `Rot L/R`, `Tilt±`, `Zoom±`,
`Fwd/Back/Left/Right`). Each is a `gfx.button_held` rectangle you hold the left
mouse button over; holding nudges the camera one step per frame
(`drive_cam` folds the ten booleans into the next `Cam`). There is no
drag-to-orbit, no scroll-to-zoom, no keyboard pan. You steer a 3D camera by
mouse-clicking little arrow widgets one at a time — that is what "cumbersome"
means here.

The pure rig itself is fine. `loam.camera.Cam` is a clean, headless-tested,
graphics-free orbit rig: `yaw`, `radius` (horizontal standoff), `height`
(vertical eye axis — deliberately *not* a spherical pitch angle), and a panned
ground target `(tx, ty, tz)`. `cam_rotate / cam_zoom / cam_pitch / cam_pan` are
all pure and clamped; `cam_apply` is the single IO seam. **The bottleneck is the
input surface feeding this rig, not the rig.**

### Current input surface (verified against `graphics/sprout_gfx.c`)

raylib is fully linked (`raylib.h`, `raymath.h`, `rlgl.h`), so every input
primitive below is available in C for free. But the Sprout `gfx` module exposes
only:

| Sprout binding      | raylib call behind it                              |
| ------------------- | -------------------------------------------------- |
| `gfx.space_pressed` | `IsKeyPressed(KEY_SPACE)`                           |
| `gfx.button`        | `GetMousePosition` + `IsMouseButtonPressed(LEFT)` *(internal only)* |
| `gfx.button_held`   | `GetMousePosition` + `IsMouseButtonDown(LEFT)` *(internal only)*    |

Mouse position, mouse buttons, wheel, and every key except Space are **not**
reachable from Sprout. The GUI-button scheme was built on top of the only two
input primitives that existed.

## 2. Goals and non-goals

**Goals.** Make loam's camera feel like a normal 3D map/terrain viewer:
direct-manipulation drag to orbit, wheel/scroll to zoom, keyboard pan. **Keep the
on-screen buttons** — but reorganise them logically (§4a); they are the
discoverable, precise control and are retained, not retired (Kuba's call). **The
MacBook trackpad must work** as a first-class input alongside the mouse — mouse
stays a primary control, the trackpad is additive, and every gesture must work on
both. Keep the pure rig headless-testable. Expose the *minimum* raw-input surface
that unblocks this and future gfx demos (not a one-off for loam).

**Non-goals.** No physics-based / inertial camera, no cinematic path system, no
gamepad, no first-person mode. No change to the `Cam` rig's *shape* unless a
control genuinely needs it (see §6). No dependence on OS pinch/magnify gestures —
verified not deliverable through raylib's desktop backend (§4b).

## 3. Prior-art survey

Three app classes have solved this, and loam's rig is already shaped like the
first two (orbit-around-a-ground-target). Every row below is verified against a
primary/official source (cited); nothing is asserted from memory.

### 3a. RTS / city-builder — *Cities: Skylines*

Primary source: [Cities: Skylines — Controls (Paradox wiki)](https://skylines.paradoxwikis.com/Controls).

| Action        | Default binding                          |
| ------------- | ---------------------------------------- |
| Pan           | `W/A/S/D` (and mouse-to-screen-edge scroll) |
| Rotate (yaw)  | `Q/E`, **or hold Middle Mouse + drag**   |
| Tilt (pitch)  | `R` / `F`                                |
| Zoom          | Mouse wheel (`Z/X` as keys)              |

This is the closest match to loam's rig: a target-orbiting camera over a ground
plane, yaw + tilt + zoom + planar pan, with keyboard pan as the primary mover
and MMB-drag as the mouse alternative.

### 3b. Geo / map — *Google Earth* and *MapLibre GL*

Primary sources:
[Google Earth Help — Explore the Earth on your computer](https://support.google.com/earth/answer/148186?hl=en);
[MapLibre GL JS — ScrollZoomHandler](https://maplibre.org/maplibre-gl-js/docs/API/classes/ScrollZoomHandler/).

| Action        | Google Earth default                     |
| ------------- | ---------------------------------------- |
| Pan           | **Left-click + drag** the ground         |
| Zoom          | Mouse wheel                              |
| Tilt          | `Shift` + scroll, or middle-button + drag forward/back |
| Rotate        | `Shift` + left-drag                      |

The defining geo convention is **zoom-toward-cursor**: the wheel zooms toward
the point *under the pointer*, keeping that point fixed on screen. Both
MapLibre and Mapbox expose a `scrollZoom.enable({ around: 'center' })` option
whose docs say it makes the map "zoom around center of map" — i.e. it is an
opt-*out* of zooming around the pointer. Neither library's reference page
*explicitly states* the unqualified default, so read this as: the existence of a
center-zoom opt-out strongly implies cursor-relative is the baseline (not a
directly documented fact). Either way, cursor-relative zoom is the behaviour
users expect from a map viewer, and loam has none of it.

### 3c. DCC orbit viewer — *Blender*

Primary source: [Blender Manual — 3D Viewport Navigation](https://docs.blender.org/manual/en/latest/editors/3dview/navigate/navigation.html)
(summarised; the manual host 403s automated fetches, but the binding below is
the long-standing documented default confirmed across the manual and mirrors).

| Action | Default binding              |
| ------ | ---------------------------- |
| Orbit  | **Middle Mouse + drag**      |
| Pan    | `Shift` + Middle Mouse + drag |
| Zoom   | Mouse wheel (or `Ctrl` + MMB + drag) |

Blender orbits a *true* arcball (free pitch + yaw), which loam's height-axis rig
does not reproduce cheaply (see §6).

### Convergence vs divergence (the useful finding)

The three classes **diverge on rotate** (which drag-button, and around what) but
**converge on zoom and pan**:

- **Zoom = mouse wheel** — unanimous across all four apps.
- **Pan = drag** (LMB in geo, Shift+MMB in DCC) **and/or `WASD`/edge** (RTS).
- **Rotate = a modifier-or-middle-button drag** — the specific chord is the only
  real choice point.

loam can adopt the converged parts wholesale. The divergent rotate is settled not
by taste but by the **trackpad constraint** (§4b): a MacBook trackpad has no
reliable middle or right button, so the only universally-available "drag" is a
**left-button drag disambiguated by a modifier key** — which is also fine on a
mouse. That is the Blender-on-a-laptop answer and the touchpad-safe answer at
once, so the RMB-vs-MMB question from the prior draft is closed.

## 4. Recommendation

Keep the on-screen buttons (reorganised — §4a) as the primary, discoverable,
precise control, and add a small **gesture layer** that works identically on
mouse and trackpad (§4b). The gesture bindings map almost entirely onto the
*existing* rig:

| loam action              | Proposed gesture (mouse **and** trackpad)             | Rig function today |
| ------------------------ | ----------------------------------------------------- | ------------------ |
| Zoom                     | Scroll — mouse wheel **or** two-finger trackpad scroll | `cam_zoom`         |
| Orbit (yaw) + tilt       | **Left-drag** on the view: Δx → yaw, Δy → tilt        | `cam_rotate` + `cam_pitch` |
| Pan look-at              | `W/A/S/D`, **and** `Shift`+left-drag                  | `cam_pan`          |
| (stretch) Zoom-to-cursor | Scroll zooms toward the ground point under the pointer | needs new math — §6 |

Left-drag over a button still clicks the button (the widget hit-test wins); only
a left-drag starting on empty view orbits. Every row except the last is a pure
re-wiring of `drive_cam` onto raw input deltas — **no rig change required.** The
buttons and the gestures drive the *same* `drive_cam`, so they stay in sync for
free.

Two cheap SOTA refinements that ride on Phase 1 (no new primitive):
- **Adaptive zoom speed** — scale `cam_zoom`'s `dr` by the current `radius` (a
  fraction of standoff per wheel notch, not a fixed world step) so zoom slows as
  you close in. This is a real part of why good map zoom doesn't feel
  cumbersome, and it is a one-line change to the step passed to `cam_zoom`.
- Note that Phase-1 drag-pan is **delta-proportional** (drag Δ scaled to world
  units), *not* pixel-accurate "grab-the-world" pan (the point under the cursor
  staying pinned under the cursor). Grab-accurate pan needs the same
  screen→ground unproject that zoom-to-cursor needs (§6a), so it belongs in the
  same Phase 3 bucket, not Phase 1.

## 4a. Reorganising the on-screen buttons

The buttons stay; today's layout is the problem. In `terrain_rivers_demo.sprout`
they are two flat columns filled by row index, which **splits related controls**
and gives the arrows no spatial meaning:

```
   left column (col_l)      right column (col_r)
   Rot L                    Fwd
   Rot R                    Back
   Tilt+                    Left
   Tilt-                    Right
   Zoom+                    Zoom-      <-- zoom split across BOTH columns
```

Zoom-in and zoom-out sit in different columns; pan (Fwd/Back/Left/Right) is a
vertical list that doesn't match the directions it moves. Reorganise by
**grouping each function and arranging it to mirror the motion** — the same
principle a car dashboard or a game HUD uses:

```
  ┌─ Pan ──────┐     ┌─ Rotate ─┐   ┌─ Tilt ─┐   ┌─ Zoom ─┐
  │     [Fwd]  │     │ [L] [R]  │   │  [+]   │   │  [+]   │
  │ [L][ ][R]  │     └──────────┘   │  [-]   │   │  [-]   │
  │    [Back]  │                    └────────┘   └────────┘
  └────────────┘
```

- **Pan** as a plus/D-pad (Fwd top, Left/Right flanking, Back below) — the arrow
  you press is where the view goes.
- **Rotate** (yaw) as a left/right pair; **Tilt** and **Zoom** each as a
  stacked +/- pair, kept whole (no more split zoom).
- Group with a one-word header per cluster so the panel reads as four labelled
  controls, not ten loose boxes.

This needs no runtime change — it is layout constants + labels in the demo, and
it can land *independently of and before* the gesture work below. (A follow-up
could add a tiny `gfx` filled-rect/label helper so clusters get visible frames;
the current `button`/`button_held` widgets already self-draw, so headers can be
drawn with the existing text overlay.)

## 4b. Trackpad + mouse — what actually works (verified against raylib 6.0)

The build links **raylib 6.0** (`brew --prefix raylib`), desktop GLFW backend.
Verified facts that shape the gesture layer:

- **Zoom via scroll works on both devices through one primitive.** A MacBook
  trackpad's two-finger scroll arrives as mouse-wheel input, same as a physical
  wheel. But the scalar `GetMouseWheelMove()` is unreliable on macOS — it returns
  "whichever of X/Y is larger," and Mac trackpads deliver a blend of both axes,
  so vertical intent can read as horizontal
  ([raylib #2480](https://github.com/raysan5/raylib/issues/2480),
  [#2371](https://github.com/raysan5/raylib/issues/2371)). **Use
  `GetMouseWheelMoveV()`** (returns a `Vector2`) and take `.y` for zoom. This is
  the single most important trackpad detail.
- **Do NOT bind `Shift`+scroll.** macOS remaps Shift+scroll to horizontal, and
  raylib then reports `0` for the wheel while Shift is held
  ([raylib #1948](https://github.com/raysan5/raylib/issues/1948)). Pan's modifier
  chord uses Shift+**drag**, not Shift+scroll — drag is unaffected.
- **Do NOT depend on OS pinch/magnify.** raylib's `GESTURE_PINCH_IN/OUT` are
  built for touchscreens; GLFW does not deliver macOS trackpad magnify events on
  desktop without native Obj-C swizzling, so pinch will not fire here
  ([raylib #3369](https://github.com/raysan5/raylib/issues/3369)). Two-finger
  scroll (→ wheel) is the trackpad zoom, and it needs no pinch.
- **Left-drag orbit works on both.** A trackpad click-drag reports
  `IsMouseButtonDown(MOUSE_BUTTON_LEFT)` with a live `GetMouseDelta()`, exactly
  like a mouse drag. This is why the scheme leans on left-drag + modifier rather
  than middle/right buttons the trackpad lacks.

Net: mouse and trackpad share **one** binding set — scroll = zoom, left-drag =
orbit, Shift+left-drag = pan, WASD = pan — with zero device-specific branches in
Sprout.

## 5. Feature → raw input → math (the actionable spine)

| Feature            | Raw input primitive needed        | Math needed                                             |
| ------------------ | --------------------------------- | ------------------------------------------------------- |
| Scroll zoom (mouse+trackpad) | `wheel_y() -> Double` (← `GetMouseWheelMoveV().y`) | none — feed `dr = -wheel_y * step` to `cam_zoom` |
| Left-drag orbit + tilt | `mouse_button_down(LEFT)` + `mouse_delta_x/_y` | none — `Δx→cam_rotate`, `Δy→cam_pitch` (already clamped) |
| Shift+drag pan     | same + `key_down(SHIFT)`          | none — `cam_pan` already works in the camera's own frame |
| WASD pan           | `key_down(code) -> Bool`          | none — same booleans `drive_cam` already folds          |
| **Zoom-to-cursor** | wheel + **screen→ground ray**     | unproject mouse ray, intersect ground plane, then pan target under cursor while zooming — **new** |

## 6. Two feasibility flags

These separate the cheap wins from the real work — flagged, not hidden.

**(a) Zoom-to-cursor needs a raycast the shim does not have.** There is **no**
`GetScreenToWorldRay` / `GetMouseRay` / unproject anywhere in
`graphics/sprout_gfx.c` (verified). Zoom-to-cursor requires unprojecting the
mouse into a world ray and intersecting the ground plane — either a new
`gfx_screen_to_ground(mx,my) -> (x,z)` C primitive (raylib's
`GetScreenToWorldRay` + a ray/plane intersect, ~10 lines) or exposing the
inverse view-projection matrix and doing the unproject in Sprout (heavier, and
matrix math is not in `stdlib.linalg` today). **This is a heavier ask than the
trivial input primitives and should be its own phase.** Everything in §4 except
this row ships without it.

**(b) Constant-distance tilt needs NO `atan2` — corrected, and now landed.** An
earlier draft claimed a "true" tilt needs `atan2` (which `stdlib.math` indeed
lacks — it has `sin/cos/tan/sqrt/floor/fabs`, no inverse trig). That was wrong.
An *incremental* tilt is a 2D rotation of the eye's `(radius, height − ty)`
offset by the frame's delta angle — `sin`/`cos` of the delta only — and the angle
clamp tests each bound with the leg that is monotonic there (the horizontal leg
`dist · cos φ`, strictly decreasing over `[0, π]`, catches the steep ceiling even
past vertical; the vertical leg `dist · sin φ` catches the shallow floor),
recovering the other leg on the arc — never needing an absolute angle. `atan2` is
only needed to *read back* an absolute pitch from a look vector, which this rig
never does because it keeps the offset in Cartesian form. **This shipped:** `cam_pitch` now
orbits at constant distance (tilt changes angle only) and `cam_zoom` scales the
distance at constant angle, so the four controls are orthogonal — see
`loam/camera.sprout` and the invariants in `tests/loam/test_camera.spr`. (A
Blender-style *free* arcball that seeds absolute pitch/yaw from an arbitrary look
vector would still want `atan2`, but loam's rig does not, so it stays out of
scope.)

## 7. The raw-input builtin surface (LANDED)

Exposing raw per-frame input is a builtin addition (AGENTS.md rules 4–6): it is
live device state only raylib can read, uncomposable from `term_*` / the GUI-
button widgets, so it is a legitimate builtin (correctness, not performance).
Approved and shipped — these now exist in `graphics/sprout_gfx.c` with `gfx.*`
wrappers in `stdlib/gfx.sprout`:

| `gfx_*` builtin                    | raylib call                | role |
| ---------------------------------- | -------------------------- | ---- |
| `gfx_key_down(code) -> Int`        | `IsKeyDown(code)`          | WASD pan + Shift modifier |
| `gfx_mouse_wheel_y() -> Double`    | `GetMouseWheelMoveV().y`   | zoom (wheel + trackpad two-finger); **`…MoveV`, not scalar `…Move`** — trackpad-correct per §4b |
| `gfx_mouse_delta_x/_y() -> Double` | `GetMouseDelta().x/.y`     | drag orbit/pan |
| `gfx_mouse_button_down(b) -> Int`  | `IsMouseButtonDown(b)`     | gate a drag on the held left button |
| `gfx_mouse_x/_y() -> Int`          | `GetMouseX/Y()`            | gate a drag to the view region (cursor left of the panel) |
| `gfx_screen_to_ground(mx,my)`      | `GetScreenToWorldRay` + plane hit | **not yet** — zoom-to-cursor only (§6a, Phase 3) |

Note the ABI: `Double`-returning gfx builtins return `long long` carrying the
IEEE-754 bit pattern (like `gfx_get_frame_time`), not a C `double`. Key/button
codes are Sprout `let` constants (`gfx.key_w`, `gfx.mouse_left`, …) mirroring
raylib's `KEY_*` / `MOUSE_BUTTON_*`. `mouse_x/_y` were pulled into this phase
(not Phase 2 as first planned) because gating the orbit-drag against the panel
region needs the cursor position. **`APPROVED_BUILTINS` does NOT apply here** —
that allowlist (and DoD #10) governs only `runtime/sprout_runtime.c`; the gfx
shim is a separate optional backend, and `stdlib/gfx.sprout` is not bundled into
the compiler so the bootstrap seed is unaffected.

**No open control-scheme decision remains** — the trackpad constraint (§4b)
closed the earlier rotate-button question (left-drag + Shift modifier).

## 8. Phasing (status)

0. **Layout — LANDED.** Buttons regrouped per §4a (pan D-pad + rotate/tilt/zoom
   clusters). Demo-side layout only; visually verified by screenshot.
1. **Phase 1 (core gestures) — LANDED.** The §7 builtins ship, and the rivers
   demo folds both buttons and gestures through the single `loam.camera.cam_drive`
   fold: scroll = zoom, left-drag over the view = orbit/tilt, Shift+left-drag /
   WASD = pan — one binding set for **mouse and trackpad**. Verified: compiles,
   links, renders, and the camera is provably stationary with zero input (no
   startup drift). Gesture *feel* (drag sign/scale) needs interactive testing —
   the steps in §4b are tunable constants in the demo. Adaptive zoom speed (§4)
   is deferred — a follow-up, not shipped in this phase.
2. **Phase 2 (edge-scroll, stretch):** `gfx_mouse_x/_y` now exist (used for the
   drag gate); RTS edge-scroll pan on top of them is not yet wired.
3. **Phase 3 (zoom-to-cursor + grab-pan):** still needs `gfx_screen_to_ground`
   (the raycast primitive, §6a). Highest polish, most work. Not started.

## 9. Follow-up

Tracked in `BACKLOG.md` under the `CAM` entry: Track 0 and Phase 1 landed; the
open items are adaptive zoom speed, edge-scroll pan, and zoom-to-cursor/grab-pan
(the last gated on a new raycast builtin).
