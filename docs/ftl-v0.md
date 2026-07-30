# FTL / Jump Drive (v0)

Status: **experimental example**, not normative. Design + implementation of faster-than-light travel
for the loam galaxy-map demo (`examples/gfx/galaxy_map.sprout`). Both modes are now **built**:
iteration 1 the **interstellar jump** (`loam.ftl`), iteration 2 the **intra-system supercruise**
(`loam.supercruise`).

## Problem

The demo has three scenes — galaxy point cloud (view 0), solar-system view (view 1), third-person
ship vista (view 2) — but no way to *travel*. We want a believable FTL mechanic that lets a player
move **between stars** and (later) **within a system**, grounded in how real space games solve it.

The core difficulty is scale. Interstellar distances are light-years (~10¹³ km), interplanetary are
AU (~10⁸ km), and the ship is ~50 m. No single linear world scale (float32) or ordinary depth buffer
spans ~13 orders of magnitude — which is *why* nearly every serious space sim uses **two distinct
drives**, one per regime. Our two coordinate systems already mirror that split: the catalog stores
integer **light-years**; a system's planets are in **AU**.

## Goals / non-goals

**Goals.** A believable, legible two-mode design. Interstellar travel that reads as a deliberate hop
(select → spool → tunnel → arrive) with a meaningful economy. Reuse the demo's existing data and
scenes; no generator change. Keep the risky logic pure and headless-tested.

**Non-goals (this iteration).** Combat, NPCs, or **interdiction** (see below). A flyable free-roam
ship. Multi-hop route planning. Physically accurate jump ranges (we inflate for playability).

## Prior-art survey (verified)

How shipping games handle two-mode FTL. Every claim below is sourced to a primary/authoritative page
(official wiki / dev post). Where a wiki blocked direct fetch, the claim was cross-checked against an
official source; anything that could not be pinned is omitted rather than guessed.

| Game | Intra-system | Interstellar | Notable constraint |
|---|---|---|---|
| **Elite Dangerous** | **Supercruise** — seamless; top speed scales *non-linearly with proximity* to bodies (up to 2001c only far from mass); drop-out via the "blue zone". | **Hyperspace jump** ("witchspace") — free-jump, no gates; spool countdown + a one-time fuel burn ∝ mass/distance; **mass-lock** blocks jumping near bodies. | Fuel scooping (KGBFOAM stars); jumponium / neutron-cone supercharge extends range. **Interdiction** pulls you out of supercruise. |
| **No Man's Sky** | **Pulse Engine** — built into every ship; auto cuts out near planets/large objects. | **Hyperdrive** — galaxy-map free-jump; fueled by Warp Cells; star-class gating via upgrade drives. | Famously **no loading screens** (seamless space→surface). |
| **EVE Online** | **Warp** — align to target + hit 75% speed to enter; min warp distance 150 km; lands ~3 km off. | **Stargates** (fixed network, near-instant) **and** capital **Jump Drives** (to a cyno beacon, isotope fuel). | **Jump Fatigue** (multiplicative, caps 5 h) throttles capital mobility; warp scramblers / bubbles interrupt. |
| **Star Citizen** | **Quantum Travel** — spool + calibrate; a body in the way blocks it; separate quantum-fuel tank. | **Jump Points** — fixed size-gated wormholes; interstellar went live in Alpha 4.0. | **Quantum Snare / Dampener** (interdiction). |
| **X4: Foundations** | **Travel Mode** — engine mode, ~3 s charge; broken by *incoming fire*. Plus **Highways**. | **Gates / Trans-Orbital Accelerators** — deliberately *no* jumpdrive; seamless single-shard. | Anomalies = hidden one-way wormholes. |
| **Freelancer** | **Cruise Engine** (spool; can't fire while cruising) + **Trade Lanes** (ring infrastructure). | **Jump Gates** (guarded network) + **Jump Holes** (natural, hidden, off-network). | Cruise/Trade-Lane **Disruptors** knock you out. |
| **Mass Effect** | **Element-Zero Drive Core** — builds static charge, must periodically discharge; ME2 adds fuel + stranding. | **Mass Relays** — fixed network; you cannot deviate from where relays link. | Pure galaxy-map point-and-click; narrative-gated. |
| **FreeSpace** | **Intrasystem Jump Drive** — near-instant point-to-point, needs only a gravity well. | **Subspace Jump Nodes** — travel only at (mostly unstable) natural nodes; Knossos portals stabilise. | Nodes collapsible by a large explosion. |

Sources: Elite [Supercruise](https://elite-dangerous.fandom.com/wiki/Supercruise) /
[FSD](https://elite-dangerous.fandom.com/wiki/Frame_Shift_Drive) /
[Hyperspace](https://elite-dangerous.fandom.com/wiki/Hyperspace) /
[Interdiction](https://elite-dangerous.fandom.com/wiki/Interdiction);
NMS [Pulse Engine](https://nomanssky.fandom.com/wiki/Pulse_Engine) /
[Hyperdrive](https://nomanssky.fandom.com/wiki/Hyperdrive);
EVE [Warp mechanics](https://wiki.eveuniversity.org/Warp_mechanics) /
[Stargates](https://wiki.eveuniversity.org/Stargates) /
[Jump drives](https://wiki.eveuniversity.org/Jump_drives) /
[Jump fatigue](https://wiki.eveuniversity.org/Jump_fatigue);
Star Citizen [Fuel Mechanics](https://robertsspaceindustries.com/en/comm-link/engineering/16517-The-Shipyard-Fuel-Mechanics) /
[Interdiction](https://starcitizen.tools/Interdiction);
X4 [Piloting and Travel](https://wiki.egosoft.com/X4%20Foundations%20Wiki/Manual%20and%20Guides/X4:%20Foundations%20Manual/Piloting%20And%20Travel/);
Freelancer [Cruise Disruptor](https://freelancer.fandom.com/wiki/Cruise_Disruptor) /
[Jump hole](https://freelancer.fandom.com/wiki/Jump_hole);
Mass Effect [FTL](https://masseffect.fandom.com/wiki/FTL) /
[Mass Relay](https://masseffect.fandom.com/wiki/Mass_Relay);
FreeSpace [Subspace](https://wiki.hard-light.net/index.php/Subspace) /
[Intrasystem jump drive](https://wiki.hard-light.net/index.php/Intrasystem_jump_drive).

### Common patterns

1. The two modes solve two different problems: intra-system = cover a big but bounded, *visible*
   distance and arrive precisely; interstellar = hop the effectively-infinite gap chosen from a map.
2. **Interstellar travel is map-selected, never nose-aimed** — a discrete point-to-point hop.
3. **Intra-system travel auto-drops near mass** — the fast mode disengages as you approach a body.
4. A **spool / charge / align delay** gates the fast mode — and is the vulnerability window every
   interdiction mechanic hangs on.
5. **Interruption (interdiction)** is a core *combat* verb — pulling someone out of FTL.
6. **Mass-lock**: you can't engage FTL too close to a big body.

### Axes of variation (the design dials)

Free-jump vs gate network · instant cut vs animated tunnel · fuel/economy vs cooldown/fatigue gate ·
interruptible vs safe · proximity-scaled vs binary speed · twitch-piloted vs on-rails.

## Chosen design

**The Elite-Dangerous-canonical pair, stripped of combat.** Elite is the reference implementation of
exactly the pattern we want: supercruise within a system, a map-selected hyperspace jump between
them, gated by fuel + range.

Deliberate deviations, each forced by our context:

- **Free-jump, no gate network.** Our galaxy is a *free point cloud* of light-year coordinates; the
  generator emits no wormhole graph. Free-jump (Elite/NMS) is the only shape that fits the data with
  zero generator change. A gate network (EVE/ME/Freelancer) would require edges we don't have.
- **No interdiction / mass-lock.** Patterns 5–6 are *combat* verbs; the demo has no NPCs to be pulled
  out by, so there is nothing to interrupt *with*. Interdiction stays a **documented future hook**:
  the spool phase is exactly where it would attach.
- **Ranges inflated for playability.** A realistic jump range is ~tens of ly; at this catalog's ~90
  ly mean spacing that reaches only nearest neighbours. `jump_range_ly = 6000` reaches a visible
  local cluster you can actually click. This is a *number* choice; the *mechanic* stays believable.

### Two-mode model

- **Interstellar jump (iteration 1, built).** From the galaxy view: select a target star, **Engage**
  (button or Space). Phases: `idle → spool → tunnel → arrive`. Spool is a countdown; the tunnel is a
  procedural warp shader; arrival drops you into the destination's **ship vista (view 2)** — the
  tunnel resolves straight into "you are now here, in space". Gated by the economy below.
- **Intra-system supercruise (iteration 2, built).** In the vista, **click a body** to lock it (a
  green target reticle), then **Supercruise** (button / Space): the drive **spools** briefly, then
  **cruises** — the ship's AU position slides on-rails toward the target at a proximity-scaled speed,
  so `loam.vista.project_body`'s angular size grows (the disc swells) and *every* body's range
  recomputes as the single observer moves — and **auto-drops** at a standoff. The planetarium
  projection *is* the supercruise renderer: the only change was making the observer a moving,
  threaded position. No new scene, no system reload, no new gfx primitive.

  - **Piloting: on-rails point-and-go** (not a throttle/free-steer) — it fits the orbit-around-ship
    vista camera (which has no cockpit "forward") and mirrors the interstellar select→engage→arrive
    UX. Speed `v = clamp((remaining − standoff)·k, v_min, v_max)` AU/s: fast far, eases to the drop,
    with a `v_min` floor so the approach never Zeno-stalls. Standoff is larger for the star (don't fly
    into it) and a few body-radii for a planet, with a floor.
  - **Attitude: the ship turns to face the travel vector *before* the drive engages** (added in a
    second pass over iteration 2). The hull yaw is a physical body with momentum, slewed toward the
    target bearing by maneuvering thrusters — a **bang-bang-with-braking** (time-optimal) controller:
    full RCS torque toward the heading error, then a flip to full counter-thrust once inside the
    braking distance `ω²/2α`, settling at the bearing without ringing. This reads as physical (you see
    the RCS couple fire, then flip to brake) and, because it terminates deterministically, is unit-
    testable. Yaw-only: `draw_model` rotates about Y alone, which also matches supercruise being planar
    in (x, z). Visual effects reuse the existing `draw_glow` billboard (no new gfx primitive) —
    layered **additive** glows so the exhaust reads as emitted light, not solid geometry: a
    **cool-blue RCS couple** (fore/aft, off-axis; each jet a tight bright core inside a dim halo)
    fires while aligning and flips side on the brake; a **main-drive plume** — a dense trail of glows
    stepping aft from the nozzle, cooling white-hot → red as it widens into a tapering exhaust column,
    with a `sin(time)` flicker — burns aft while cruising.
  - **State machine** `sc_idle → sc_align → sc_spool → sc_cruise`, mirroring the interstellar machine.
    Both `align` and `cruise` end on a **spatial** condition (`aligned` / `arrived`), not a timer — so
    those are caller-supplied gates — while `spool` is the one timed phase. Turn-then-charge: `align`
    holds until the ship faces the target, only then does the drive spool. On a system change the ship
    AU position, heading + drive reset (a fresh system starts you 1 AU out, facing forward); within a
    system they persist across galaxy↔vista toggles.
  - **Target picking** reuses the body-marker projection (nearest body to the cursor); the locked
    target's reticle is highlighted and always labelled (exempt from declutter).

### Economy model

Range is the hard gate; fuel is a self-balancing budget:

- A jump is permitted iff the target is **in range** (`dist ≤ jump_range_ly`) **and** there is fuel
  for its cost (`cost = dist · fuel_per_ly`).
- On arrival, fuel = `min(fuel_max, fuel − cost + refuel_amount)`. Because `cost` scales with
  distance but the arrival top-up `refuel_amount` is **flat**, short hops net a gain and long hops a
  drain — a self-balancing economy that, with the range cap on single-hop size, can **never hard-
  strand** the player (unlike ME2's fuel model, deliberately). The blocked reason (`OUT OF RANGE` /
  `LOW FUEL` / `SELECT TARGET`) is shown on the HUD.

*Deferred (roadmap):* scoop-class-gated refuel (only Elite's KGBFOAM stars top you up), and multi-hop
route planning when a target is out of single-hop range.

## Engine-hook mapping (implementation)

- **`stdlib.math`** — the general numeric atoms `fclamp` / `lerp`, plus `atan` / `atan2` (pure, same
  self-hosted style as `sin`: a halving reduction `atan(x)=2·atan(x/(1+√(1+x²)))` down to a Taylor
  series, then `atan2` quadrant bookkeeping) for the attitude bearing. Also fixed a latent `floor` bug
  the bearing flushed out — `round_nearest`'s `+2^52` magic-number round was wrong for negative inputs
  (they land in the ULP=0.5 binade and snap to a half-integer); now rounds the magnitude and re-signs.
- **`loam.ease`** (new, pure, headless-tested) — `clamp01` / `smoothstep` / `ease_out` / `inv_lerp` /
  `remap`. Domain-agnostic easing; reused by the jump fade, iter-2 supercruise, camera dollies.
- **`loam.ftl`** (new, pure, headless-tested — `tests/loam/test_ftl.spr`) — the whole risky core:
  `jump_distance`, `jump_fuel_cost`, `in_range`, `can_jump`, `advance_phase` (the `idle→spool→tunnel→
  arrive` machine, advanced by a frame delta), `phase_progress`, `refuel`. Phase codes are `Int`
  (0..3), matching the demo's `view: Int` idiom. 27 assertions lock down gating + every transition +
  the refuel sign before any rendering exists.
- **`loam.warp`** (new) — the warp-tunnel GPU shader, a near-copy of `loam.skydome`'s skeleton (pass-
  through VS; fragment recovers `dir = normalize(vWorld − uEye)` on a camera-centred inside-out
  sphere), drawing blue-shifted radial star-streaks from the travel axis, driven by a `uProgress`
  uniform. Uses only the existing generic gfx shader API — **no new engine primitive**.
- **`loam.supercruise`** (new, pure, headless-tested — `tests/loam/test_supercruise.spr`) — the
  intra-system closing kinematics + machine: `sc_speed` (clamped proximity speed), `sc_step` (one
  convergent frame of on-rails travel toward the target), `sc_arrived` (standoff test), `sc_advance`
  (the `idle→align→spool→cruise` machine, `align`/`cruise` ending on the caller's `aligned`/`arrived`
  gates). Phase codes are `Int` unordered tags. 19 assertions lock the speed clamp, `sc_step`
  convergence, the standoff arrival, and every transition (incl. the align gate) before any rendering.
- **`loam.attitude`** (new, pure, headless-tested — `tests/loam/test_attitude.spr`) — the rotational
  dual of `loam.supercruise`: `wrap_pi` (shortest-path angle fold), `bearing` (atan2 direction),
  `attitude_step` (one bang-bang-with-braking slew frame, returning an `Attitude` record of heading +
  angular velocity + the RCS couple sign for the VFX), `attitude_aligned` (settle predicate: on-target
  *and* nearly stopped). The controller uses a **terminal capture** (null ω when the target is within
  one frame's stopping range) to defeat the discrete-step chatter a fixed `omega_tol` would cause. 18
  assertions lock wrapping, the bearing convention, spin-up, convergence, and no-overshoot.
- **`galaxy_map.sprout`** — threads `loc_*` (current location, distinct from the `sel_*` target),
  `fuel`, `ftl_phase`, `ftl_timer`, `warp_sh` through `render_loop`; advances the machine with
  `gfx.get_frame_time()`; draws the warp during the tunnel phase (skipping the normal scene) and the
  fuel/range/status HUD when idle; performs the arrival side-effects (loc := sel, refuel, view := 2)
  on the one-frame `arrive`, after which the existing `need_load`/`need_bg` paths stream the
  destination. A canary arg (`argv[13]=1`) auto-engages a jump at boot for a headless screenshot.
  For supercruise it also threads the ship's **AU position** `(ship_ax, ship_az)` + `sc_phase`/
  `sc_timer`/`sc_target` through `render_loop`, feeds that moving position to `project_body` as the
  observer (sun, planets, and the markers), steps it with `sc_step` while cruising, and resets it on a
  system change. A second canary (`argv[14]=<body index>`) auto-targets + supercruises at boot. The
  attitude adds three more threaded accumulators — `ship_heading` / `ship_omega` / `ship_thrust` —
  stepped by `attitude_step` after each frame's draw (render-current-state, then integrate); the
  heading feeds `draw_model`'s yaw (via a calibration offset) and the thruster-plume VFX.

## Tests

- `tests/loam/test_ftl.spr` (27 assertions) — the jump geometry, the range+fuel gate, every phase
  transition (incl. blocked-when-`!can` and the arrive→idle latch), progress bounds, refuel economy.
- `tests/loam/test_supercruise.spr` (19) — the proximity speed clamp, `sc_step` convergence to the
  standoff, the arrival test, and every supercruise transition (incl. the `align` gate).
- `tests/loam/test_attitude.spr` (18) — angle wrap, the bearing convention, thruster spin-up,
  convergence to the target with ω settling, and the no-large-overshoot property.
- `tests/loam/test_ease.spr` (19) — the interpolation atoms.
- `tests/stdlib/test_math_double.spr` — extended with `fclamp` / `lerp` / `atan` / `atan2` and the
  negative-`floor` regression cases.
- The rendering (warp shader, the moving-observer vista, HUD, arrival flows) is validated by the
  build-and-run gate (headless screenshot canaries), per AGENTS DoD #13 — GLSL and the render loop are
  not unit-testable.

## Roadmap

Both drive modes are built. Further follow-ups (scoop-class refuel, multi-hop routing, an interdiction
hook on the spool phase, richer supercruise motion cues, the clamp/lerp consolidation of the scattered
`clampd`/`clamp01` copies) are tracked in `BACKLOG.md`.
