# gfx effects roadmap — what makes builders look "AAA", and loam's next steps (v0)

Status: **planning / research-backed** (supporting doc, not normative). Companion to
`docs/gfx-postprocess-v0.md` (the implemented post-processing pipeline). This doc records
a sourced survey of how polished city-builder / colony games achieve their look, and turns
it into a prioritized backlog for loam's raylib engine.

## The mental model

AAA polish is **two layers**:
1. a **deeper rendering pipeline** — HDR/linear working space, cascaded shadow maps, global
   illumination, PBR materials, volumetric lighting;
2. a **post-processing chain** composited on top — tone-map, colour grade, bloom, ambient
   occlusion, fog, DoF, anti-aliasing.

loam already has the second layer's *plumbing* (`docs/gfx-postprocess-v0.md`). Two findings
reframe what's worth building:

- **Forward rendering is not a ceiling.** Unity's HDRP docs: forward and deferred *"both
  implement the same features, but the quality can differ between them."* Deferred wins on
  many-light efficiency, not exclusive effects — so loam's forward + single-light setup can
  carry nearly the whole stack. The gates are specific **buffers** (depth, HDR), not the
  architecture. ([Unity HDRP docs](https://docs.unity3d.com/Packages/com.unity.render-pipelines.high-definition@14.0/manual/Forward-And-Deferred-Rendering.html))
- **Selective, not wholesale.** Manor Lords (UE4 at 2024 launch → UE5 ~2025) ships with
  **Lumen deliberately OFF**: *"So far Lumen wasn't worth the extra performance cost … I left
  it off."* Even a flagship builder turns GI off. ([Unreal dev interview](https://www.unrealengine.com/en-US/developer-interviews/solo-dev-makes-sophisticated-sim-manor-lords-using-unreal-engine), [GamesRadar](https://www.gamesradar.com/games/city-builder/))

## Verified reference: Cities: Skylines II (the one hard cost breakdown)

Unity 2022.3.7, **HDRP, D3D11, deferred** (RenderDoc teardown, [paavo.me](https://blog.paavo.me/cities-skylines-2-performance/)). Measured on a Nov-2023 launch build:

| Effect | Layer | Cost |
|---|---|---|
| Cascaded shadow maps (4× 2048²) | deeper | **~40 ms / ~88 ms frame; 72% of draw calls** |
| GTAO (ambient occlusion) | screen-space | ~1.6 ms |
| SSR (reflections) | screen-space | ~1.5 ms |
| SSGI (half-res + temporal) | screen-space | cheap-ish |
| TAA | screen-space | needs a motion-vector pass |

The lesson: **screen-space post is cheap (~1–2 ms each); shadows cost ~half the frame.**
(Cross-check: Intel's XeGTAO reference measures ~0.56 ms at 1080p on an RTX 2060 —
[XeGTAO README](https://github.com/GameTechDev/XeGTAO/blob/master/README.md).)

## Prioritized backlog — cheapest, biggest uplift first

Ordered by AAA-uplift-per-engineering; each tagged with what it needs.

1. **Distance / atmospheric fog** — *no new buffers, ~free.* **← IMPLEMENTED** (`gfx.fog`).
   Exponential (Beer-Lambert) falloff, applied in the scene shaders using `gl_Position.w`
   (view depth); background clears to the fog colour so terrain fades into the horizon. The
   single best cohesion/scale cue for a terrain map. ([Distance fog](https://en.wikipedia.org/wiki/Distance_fog))
2. **Colour grade / LUT** — *pure post ALU, no buffers.* A lift/gamma/gain or 3D-LUT on top of
   the existing saturation knob. The stylized-look lever: *"a consistent, limited colour
   palette … prevents chaos on the screen."* ([low-poly art guide](https://retrostylegames.com/blog/low-poly-game-art-an-ultimate-guide/))
3. **SSAO / GTAO** — *needs a depth (+normal) buffer; ~0.5 ms.* The verified best low-cost /
   high-uplift effect; darkens contact crevices, which grounds blocky low-poly geometry and
   adds readability. Requires a depth/normal prepass or MRT on the scene target.
4. **HDR RGBA16F target + linear pipeline** — *the foundational investment; ~free perf on
   Apple-Silicon TBDR, the cost is plumbing.* LDR *"clamps to [0,1] … breaks multi-light
   accumulation";* HDR + tone-map + gamma unlock the next tier and make the existing ACES
   curve do its real job. raylib's `LoadRenderTexture` is RGBA8-only → hand-build the FBO via
   `rlgl`. ([LearnOpenGL HDR](https://learnopengl.com/Advanced-Lighting/HDR))
5. **Bloom** — *cheap threshold version now (LDR); real thresholdless version after #4.* Real
   PBR bloom *"requires an HDR floating-point colour buffer"*, but threshold bloom on LDR
   *"still works as a cheaper stylized approximation and is what many engines actually ship."*
   Reuses the `blit_pass` chain (Phase-2 plan in `gfx-postprocess-v0.md`). ([PBR bloom](https://learnopengl.com/Guest-Articles/2022/Phys.-Based-Bloom))
6. **Anti-aliasing (FXAA/SMAA)** — *cheap post-pass.* The off-screen post path drops the
   window's MSAA; TAA needs motion vectors loam lacks, so FXAA/SMAA is the practical re-AA.

### Skip / defer
- **Cascaded shadow maps + soft shadows** — biggest uplift *and* biggest cost (~40 ms in
  C:S2). Defer until #1–3 land; it's a project of its own.
- **SSR, SSGI, volumetric fog, TAA** — deeper/expensive; volumetric fog is a *frustum-voxel*
  system, not screen-space ([Epic docs](https://dev.epicgames.com/documentation/en-us/unreal-engine/volumetric-fog-in-unreal-engine)). Simple distance fog (#1) gets ~80% of the look for ~1% of the cost.

## Confidence & caveats
- **Refuted (do not rely on):** the precise post chain ordering (DoF→motion→bloom→tonemap→AA)
  — verifiers split 1-2; and that SSAO needs a *full* G-buffer with per-fragment position
  (depth + normals is still needed).
- **Weakly sourced:** the stylized-look guidance rests on art blogs; per-game tech for
  Timberborn / Townscaper / Against the Storm / Anno 1800 / Frostpunk was **not** primary-
  verified — trust the general principle (palette + AO + grade + fog + soft-shadow cohesion),
  not the per-game attributions.
- **Time-sensitive:** C:S2 numbers are a Nov-2023 launch build; Manor Lords is now UE5 with
  Lumen off.

## Open engineering questions (for when we pick these up)
- Minimal raylib path from RGBA8 → RGBA16F (custom `rlgl` FBO; does `RenderTexture` support a
  float colour attachment out of the box?).
- Cheapest depth-buffer exposure for SSAO given the existing forward + single-light setup.
- FXAA vs SMAA vs re-enabling MSAA for a forward, no-motion-vector engine.
