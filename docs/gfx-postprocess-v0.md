# gfx post-processing — v0

Status: **experimental** (native gfx binding; not part of the normative spec or the
bootstrap seed). Implemented in `graphics/sprout_gfx.c` + `stdlib/gfx.sprout`.

## 1. Problem

The gfx backend rendered the 3D scene straight to the window backbuffer
(`BeginDrawing → BeginMode3D → … → EndMode3D → EndDrawing`), so there was no seam
at which to run screen-space effects (vignette, tone-map, blur, later bloom). Loam
scenes read flat and clipped: blown-out sky/water highlights, hard frame edges, no
sense of motion when the camera orbits.

## 2. Goals / non-goals

Goals
- An **opt-in**, per-effect post-processing stage that existing demos are unaffected by.
- A first effect set: **vignette + filmic tone-map + camera-motion-scaled blur**.
- A structure that extends to **multi-pass** effects (bloom, DoF) with no rewrite.

Non-goals (v0)
- Depth-of-field (Phase 2 — needs the target's depth attachment + a CoC pass). Bloom
  was a v0 non-goal but has since landed — see §6.
- Multisampled off-screen target: the render texture is single-sampled, so the
  opt-in path trades the window's 4× MSAA for effects (the blur masks aliasing;
  `gfx.supersample` is the implemented SSAA mitigation, FXAA-as-a-pass an option).

Delivered after the first cut
- **UI-crisp-on-top** via `overlay_begin()` (§3a): opt-in seam so the HUD draws sharp
  over the post-processed scene. A frame that omits it keeps the UI-in-scene behaviour.
- **Altitude-driven blur** (§3): the blur baseline can track camera height.

## 3. Pipeline

When **any** effect is enabled (`g_post_mask != 0`):

```
gfx_frame_begin →  BeginTextureMode(g_scene_target)   # 3D pass into off-screen colour+depth
                   ClearBackground; BeginMode3D
   …app draws (terrain, trees, water, UI overlays)…
gfx_frame_end   →  EndMode3D; EndTextureMode           # resolve the scene target
                   present_post():                     # ONE full-screen shader pass:
                     set POST_FS uniforms (mask, vignette, exposure, blur radius)
                     blit_pass(scene.texture, NULL, POST_FS)   # → backbuffer, y-flipped
                   (TakeScreenshot here grabs the post-processed screen)
```

When **no** effect is enabled the shim keeps the original direct-to-screen path
verbatim, so the other gfx demos are byte-identical and keep MSAA.

### 3a. Crisp UI overlay (`overlay_begin`)

By default the 2D overlays (`draw_fps`, `button`, `button_held`) are drawn *between*
`frame_begin` and `frame_end`, i.e. into the scene target, so they get post-processed
too. To keep the HUD sharp, the app calls `overlay_begin()` **after** its 3D draws and
**before** its UI:

```
frame_begin → …3D draws…
overlay_begin →  EndMode3D; EndTextureMode; BeginDrawing; present_scene_shaded()   # left OPEN
   …UI draws (draw_fps/button)…                                                     # crisp, on screen
frame_end   →  EndDrawing (+ screenshot)
```

The seam is required because the shim sees an undifferentiated draw stream and cannot
tell scene draws from UI draws — only the app knows the boundary. A `g_overlay` flag
makes the UI funcs skip their `Mode3D` toggle (they're already in 2D screen space) and
makes `frame_end` skip the already-done present, so `present_scene_shaded` and the
camera bookkeeping run **exactly once** per frame. Omitting `overlay_begin` keeps the
old UI-in-scene behaviour; it is also harmless (and gives crisp UI) with no effect
enabled. The overlaid UI is unvignetted/unblurred, so it reads brighter than the
scene beneath it.

`blit_pass(src, dst, shader)` is the reusable atom: run one full-screen shader from
a source texture into a destination target (`NULL` = the screen), accounting for the
render texture's y-flip. **Every** post-process pass — single- or multi-pass — is
built from it.

### POST_FS (the uber-shader)

One fragment shader folds three effects, each gated by a bit of `uMask`:

| bit | effect  | uniforms | notes |
|-----|---------|----------|-------|
| 1 | vignette | `uVigIntensity`, `uVigRadius` | `smoothstep` darkening toward the corners |
| 2 | tone-map | `uExposure`, `uSaturation` | pre-exposure × ACES filmic (Narkowicz 2015), then a saturation grade |
| 4 | blur | `uBlur`, `uResolution` | 13-tap Gaussian (2 rings); `uBlur` is the radius in pixels |

The blur **amount** is computed CPU-side each frame as
`static_amount + camera_motion · motion_gain` (clamped to `g_blur_max`), where
`camera_motion` is the eye + look-at travel since the previous frame. The amount is
unitless; the shim scales it to the shader's pixel radius via `GFX_BLUR_PX_PER_UNIT`
(~6 px/unit), so an amount of ~0.5 reads as clearly soft. Result: sharp (or
`static_amount` soft) at rest, softer while the camera moves. A **raw sub-pixel
radius does nothing** — offsets below ~1 px sample the same texel — which is why the
API takes a unitless amount and scales it, rather than exposing bare pixels (tiny
values like 0.05 then read as no-ops).

## 4. Sprout API (`stdlib.gfx`)

```
post_vignette(intensity: Double, radius: Double)          # enable + configure
post_tonemap(exposure: Double, saturation: Double)        # saturation is the visible grade knob
post_motion_blur(static_amount: Double, motion_gain: Double)   # constant baseline + motion term
post_altitude_blur(low_alt, high_alt, amt_low, amt_high: Double)  # baseline = f(camera height)
post_disable()                                            # all off → direct-to-screen
overlay_begin()                                           # scene→HUD seam (crisp UI, §3a)
```

Each setter enables its own effect bit, so an app opts in à la carte (typically in
`main`, after `open_window`). Adding a future effect is purely additive — a new
`post_*` binding + a new `gfx_post_*` extern; no existing signature changes.

The two blur setters are mutually exclusive on the **baseline**: `post_motion_blur`
sets a constant baseline (and clears any altitude map); `post_altitude_blur` makes the
baseline a clamped-linear function of camera height `eye.y` — `[low_alt, high_alt] →
[amt_low, amt_high]` (`amt_high > amt_low` hazes the overview; swap to blur when low).
To have BOTH altitude baseline and motion, call `post_motion_blur` first (for the gain),
then `post_altitude_blur` (keeps the gain, sets the altitude baseline).

## 5. Verification

The shim is not headless-testable (needs a GL context/window), so v0 is verified by
the screenshot harness — `SPROUT_GFX_SCREENSHOT=<relative.png>`
`SPROUT_GFX_SCREENSHOT_FRAME=N SPROUT_GFX_MAX_FRAMES=M just run-gfx <demo>` — with a
before/after on `examples/gfx/terrain_rivers_demo.sprout`. (raylib's `TakeScreenshot`
resolves the path against the working dir, so pass a **relative** filename.)

## 6. Phase 2 — bloom (IMPLEMENTED)

Bloom reuses the **entire** v0 substrate — `g_scene_target`, the y-flip, the screenshot
placement, and the API shape — and only ADDS to it. As built (`GFX_POST_BLOOM`,
`gfx_post_bloom`):

- **+2 scratch `RenderTexture2D`s** (`g_bloom_a`/`g_bloom_b`, half window res, ping-pong),
  allocated **lazily** on the first `gfx_post_bloom` call.
- **+2 shaders**: `BLOOM_PREFILTER_FS` (soft-knee luminance bright-pass) and
  `BLOOM_BLUR_FS` (separable 9-tap Gaussian, H/V via a `uDir` uniform). The composite is
  **folded into `POST_FS`** rather than a third shader — one fewer pass and target than the
  original plan (which composited into a separate `sceneHDR`).
- **+ `bloom_blit(src, dst, sh)`** — the render-target→render-target atom; unlike the final
  present it uses **positive** src height (straight copy) so every bloom target shares the
  scene target's storage orientation and `POST_FS` samples `texture0`/`texture1` at one uv.
- **+ `run_bloom_chain()`** called in BOTH present paths (`gfx_frame_end` and
  `gfx_overlay_begin`), between the scene's `EndTextureMode` and the final `BeginDrawing`:

```
scene ─bloom_blit(prefilter)─▶ bloom_a          # bright-pass + downsample to half-res
bloom_a ⇄ bloom_b  (bloom_blit(blurH/blurV) × GFX_BLOOM_BLUR_ROUNDS, ends in bloom_a)
present: POST_FS(texture0=scene, texture1=bloom_a) ─▶ screen   # additive composite, then tonemap/vignette
```

- **API**: `post_bloom(threshold: Double, intensity: Double)`.

Because the additive composite happens *inside* `POST_FS` before its tone-map/vignette, the
v0 present is still the single, final pass — nothing is thrown away.

**Known limitation:** threshold bloom keys on brightness, so bright *lit* surfaces (planets)
bloom like emissive ones. The principled fix — an emissive-only bloom source — is tracked in
`BACKLOG.md` (§9); see also `docs/gfx-effects-roadmap-v0.md` #5.

Related follow-ups (not scheduled): UI-crisp-on-top (draw overlays after the present,
needs a `ui_begin` seam), FXAA-as-a-pass to recover the MSAA lost to the off-screen
target, and depth-of-field (needs the target's depth attachment + a CoC pass).
