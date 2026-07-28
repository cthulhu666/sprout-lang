# Sprout graphics (`gfx`)

A small [raylib](https://www.raylib.com/)-backed 3D graphics layer for Sprout.

- **`graphics/sprout_gfx.c`** — the C shim: window/GL lifecycle, a data-encoded lit shader for baked
  meshes, GPU instancing, static mesh capture, post-processing, input, and an immediate-mode HUD.
- **`stdlib/gfx.sprout`** — the Sprout binding: `extern fn` declarations for every shim entry point
  plus thin `export fn` wrappers. This is the surface a demo imports as `import stdlib.gfx`.

The shim links **only** under `just run-gfx <file> [args...]` (which compiles the demo, `sprout_gfx.c`,
and links raylib). Plain `just run` does not link graphics, so a gfx demo type-checks everywhere but
only *runs* via `run-gfx`. `just gfx-smoke` compiles `tests/gfx_smoke/gfx_surface.spr` to IR to prove
the whole binding surface resolves without needing a window.

Design docs: [`docs/gfx-engine-api-v0.md`](../docs/gfx-engine-api-v0.md) (API generalization),
[`docs/graphics-v0.md`](../docs/graphics-v0.md), [`docs/gfx-postprocess-v0.md`](../docs/gfx-postprocess-v0.md),
[`docs/gfx-effects-roadmap-v0.md`](../docs/gfx-effects-roadmap-v0.md).

## How a frame is structured

```
gfx.open_window(w, h, title); gfx.set_target_fps(60)
# ... bake static meshes once (mesh_capture_begin/end), load models ...
loop until window_should_close:
    gfx.set_camera(...)            # position the eye
    gfx.frame_begin()             # clear + BeginMode3D (+ off-screen target if post FX on)
    gfx.draw_captured(handle)     # opaque 3D
    gfx.draw_instances(...)       # instanced props
    # transparent pass (see Transparency below)
    gfx.overlay_begin()           # switch to 2D for a crisp, un-post-processed HUD
    gfx.draw_fps(10, 10); clicked <- gfx.button(...)
    gfx.frame_end()               # composite post FX + present
gfx.close_window()
```

## Built-ins reference

All are `!{IO}`. Coordinates/sizes are world-space `Double`; colours are `Int` 0–255.

### Window & lifecycle
| Function | Purpose |
|---|---|
| `open_window(w, h, title)` | Create the window + GL context. |
| `set_target_fps(fps)` | Cap the frame rate. |
| `get_frame_time() -> Double` | Seconds elapsed for the last frame. |
| `window_should_close() -> Bool` | True when the user closes the window (or `SPROUT_GFX_MAX_FRAMES` is hit). |
| `close_window()` | Tear down the window + GL context. |

### Frame & camera
| Function | Purpose |
|---|---|
| `set_camera(px,py,pz, tx,ty,tz, fovy)` | Eye position, look-at target, vertical FOV. |
| `frame_begin()` | Begin the 3D scene: clear, `BeginMode3D`, refresh the frustum (and bind the off-screen target if post FX are on). |
| `overlay_begin()` | End 3D and switch to 2D so the HUD draws crisp (unblurred/unvignetted) on top. |
| `frame_end()` | Composite any post effects and present the frame. |

### Immediate-mode drawing
| Function | Purpose |
|---|---|
| `draw_grid(slices, spacing)` | Reference grid on the XZ plane. |
| `draw_cube(x,y,z, size, r,g,b)` | A solid lit cube. |
| `draw_plane(x,y,z, sx,sz, r,g,b,a)` | A filled XZ plane; `a<255` blends (e.g. a sea surface). |
| `draw_model(handle, x,y,z, angle, scale)` | Draw one loaded model instance. |

### Static mesh capture (bake once, draw many frames)
| Function | Purpose |
|---|---|
| `mesh_capture_begin()` | Start capturing subsequent quads into one mesh. |
| `capture_quad(12 pos, 3 normal, r,g,b)` | Add a quad with an explicit vertex colour. |
| `capture_quad_data(12 pos, 3 normal, tag,tier,dir,band,lake)` | Add a quad carrying **data** in the colour attribute (see the CUBE shader) instead of a colour. |
| `mesh_capture_end() -> Int` | Finish; returns a mesh handle. |
| `draw_captured(handle)` | Draw a captured mesh (frustum-culled in the shim). |
| `mesh_reset()` | Free all captured meshes (e.g. before a re-bake). |

### Data-encoded terrain shader uniforms
The CUBE shader decodes `capture_quad_data`'s payload (R=biome tag, G=flow tier, B=flow dir, A=band +
lake flag) and picks a colour per **view mode**, so switching views is one uniform — no re-bake.
| Function | Purpose |
|---|---|
| `set_view_mode(mode)` | −1 raw vertex colour (default; other demos unaffected); 0 Main, 1 Relief, 2 Flow, 3 Lakes. |
| `set_terrain_levels(levels)` | Elevation band count for the grey relief ramp. |

### Animation clock & transparency (general state)
| Function | Purpose |
|---|---|
| `set_time(t)` / `get_time() -> Double` | Set the shader `uTime` uniform / read the engine wall-clock. Drives any animated surface (e.g. the water ribbon). |
| `begin_blend_alpha()` / `end_blend()` | Standard alpha-blend state on/off. |
| `set_depth_mask(on)` | Toggle depth-buffer **writes** (the depth test still applies). |

Transparent-pass recipe (not water-specific): draw opaque, then `begin_blend_alpha()`;
`set_depth_mask(false)`; *draw transparent, roughly back-to-front*; `set_depth_mask(true)`; `end_blend()`.

### Instancing (thousands of props in one draw)
| Function | Purpose |
|---|---|
| `load_model(path) -> Int` | Load a model (`.glb`/`.obj`); returns a handle. |
| `instance_push(group, model, x,y,z, angle, scale)` | Queue one instance into spatial bucket `group`. |
| `instance_clear(group)` | Drop a group's instances (for dynamic crowds re-pushed each frame). |
| `draw_instances(group_count, cull_dist)` | One `DrawMeshInstanced` per model over visible groups; a group draws only if within `cull_dist` of the eye (≤0 disables) and in the frustum. |

### Skeletal animation
| Function | Purpose |
|---|---|
| `load_animations(path) -> Int` | Load an animation set; returns a handle. |
| `animation_count(set) -> Int` | Number of clips in the set. |
| `animation_keyframes(set, index) -> Int` | Keyframe count of one clip. |
| `update_animation(model, set, index, frame)` | Pose `model` to `frame` of clip `index`. |

### HUD (immediate-mode, call after `overlay_begin`)
| Function | Purpose |
|---|---|
| `draw_fps(x, y)` | Draw the FPS counter. |
| `button(x,y,w,h, label) -> Bool` | Clickable button; true once per click (edge-triggered). |
| `button_held(x,y,w,h, label) -> Bool` | True every frame the button is held down. |

### Input
| Function | Purpose |
|---|---|
| `space_pressed() -> Bool` | Spacebar edge. |
| `key_down(key) -> Bool` | Held key; use the `key_w/key_a/key_s/key_d/key_left_shift` constants. |
| `mouse_button_down(button) -> Bool` | Held mouse button; use `mouse_left`/`mouse_right`. |
| `mouse_wheel_y() -> Double` | Scroll delta. |
| `mouse_x() / mouse_y() -> Int` | Cursor position. |
| `mouse_delta_x() / mouse_delta_y() -> Double` | Cursor movement since last frame. |

### Post-processing & atmosphere
Enable per effect; each stores its params and routes the scene through an off-screen target.
| Function | Purpose |
|---|---|
| `fog(density, r,g,b)` | Beer-Lambert distance fog; the colour doubles as the horizon/clear colour. |
| `supersample(factor)` | Render at `factor`× and downsample (kills minification shimmer). |
| `post_vignette(intensity, radius)` | Darkened edges. |
| `post_tonemap(exposure, saturation)` | Exposure + saturation grade. |
| `post_motion_blur(static_amount, motion_gain)` | Velocity-scaled blur. |
| `post_altitude_blur(low_alt, high_alt, amt_low, amt_high)` | Height-banded blur. |
| `post_disable()` | Clear the whole post path (back to direct-to-screen MSAA). |

## Dev / analysis env vars

These are read by the shim at startup and gate **development** aids (screenshots, deterministic
framing). They affect no production path when unset.

### Reproducible screenshots
| Variable | Effect |
|---|---|
| `SPROUT_GFX_SCREENSHOT=<path>` | Save one screenshot to `<path>` (pass a **relative** path — raylib prepends the CWD). |
| `SPROUT_GFX_SCREENSHOT_FRAME=<n>` | Which frame to capture (default 2). Larger `n` lets animation/camera settle. |
| `SPROUT_GFX_MAX_FRAMES=<n>` | Auto-close the window after `n` frames (so a headless run terminates). |

### Analysis camera override
When `SPROUT_GFX_CAM_RADIUS` is set, `frame_begin` **ignores the app's camera** and orbits a fixed
target — reproducible framing at any zoom/angle/location for **any** gfx demo, with no code edits
(parallel to the screenshot vars). All values are floats; `YAW` is in degrees.

| Variable | Effect | Default |
|---|---|---|
| `SPROUT_GFX_CAM_RADIUS` | Horizontal standoff. **Required to activate.** | — |
| `SPROUT_GFX_CAM_TX` / `SPROUT_GFX_CAM_TZ` | Look-at target, world x / z. | 0 |
| `SPROUT_GFX_CAM_TY` | Target height. | 4 |
| `SPROUT_GFX_CAM_HEIGHT` | Eye height above the target. | `radius·0.6` |
| `SPROUT_GFX_CAM_YAW` | Orbit angle, degrees. | 0 |

Example — a zoomed-in, angled shot over a chosen spot, captured at frame 25:

```sh
SPROUT_GFX_CAM_RADIUS=70 SPROUT_GFX_CAM_TX=0 SPROUT_GFX_CAM_TZ=-30 \
SPROUT_GFX_CAM_HEIGHT=42 SPROUT_GFX_CAM_YAW=0 \
SPROUT_GFX_SCREENSHOT=shot.png SPROUT_GFX_SCREENSHOT_FRAME=25 \
  just run-gfx examples/gfx/terrain_rivers_demo.sprout examples/gfx/terrain_rivers.conf
```

## Testing

There are no unit tests for rendered output — gfx correctness is verified by **screenshot**. The
automated gate is `just gfx-smoke` (the binding surface compiles). Add any new binding to
`tests/gfx_smoke/gfx_surface.spr` so the smoke test exercises it.
