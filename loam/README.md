# Loam — a headless game engine

Loam is the seed of a game engine, written in Sprout. Two properties define it:

- **Headless.** The simulation and world-generation layers import **no graphics** and run without a
  window — they are asserted directly in `tests/loam/*.spr`. Rendering is a separate, thin layer
  (`loam.view`) that reads the model and draws it through [`stdlib.gfx`](../graphics/README.md).
- **Seeded & deterministic.** The procedural world is a pure function of `(config, seed, coordinate)`:
  the same inputs always produce the same terrain, rivers, and vegetation, on every target.

Loam lives **outside** `stdlib/` because it is *game* code, not standard library. Modules import each
other as `loam.<name>`, resolved via `--package-root` (the repo root). Tests run with
`just test-loam`; the flagship consumer is [`examples/gfx/terrain_rivers_demo.sprout`](../examples/gfx/terrain_rivers_demo.sprout).

## Architecture

Three concerns are kept deliberately separate, so the model can be tested without a GPU and the
renderer can be swapped without touching game logic:

```
  data              simulation            presentation
  ─────             ──────────            ────────────
  scene   ◀────────  agent   (decide/move/energy)      camera  (rig state + math)
 (ECS store)         physics (vertical dynamics)  ───▶ view    (read components → gfx)
                     driver  (fixed-timestep loop)      config  (Int tunables from a file)

  procedural world (pure, headless):  terrain ──▶ hydrology ──▶ ribbon
                                          └────────────────────▶ vegetation
```

## Modules

### Procedural world (pure, deterministic, headless)
| Module | What it does | Tests |
|---|---|---|
| [`terrain`](terrain.sprout) | Seeded terrain model — fBm value-noise elevation, biome classification, discrete elevation bands, chunked generation. A pure function of one coordinate. | `test_terrain` |
| [`hydrology`](hydrology.sprout) | Drainage model layered on `terrain`: Planchon-Darboux depression fill → D8 flow direction → accumulation → flow tiers, plus channel **carve**, river **widen**, and **meander**. Deterministic in `(cfg, span)`; termination is the load-bearing invariant. | `test_hydrology` |
| [`ribbon`](ribbon.sprout) | Turns the blocky per-tile river mask into a **smooth centerline** (neighbour-averaging) for a continuous water-surface mesh (`docs/rivers-v0.md §6d`). | `test_ribbon` |
| [`vegetation`](vegetation.sprout) | Headless "what plant grows on this tile?" classifier — biome-driven, deterministic. | `test_vegetation` |

### Simulation (ECS)
| Module | What it does | Tests |
|---|---|---|
| [`scene`](scene.sprout) | The entity/component store — the core of the engine. | `test_scene` |
| [`agent`](agent.sprout) | The behaviour layer: `world_step` advances every entity one fixed step (decide, move, spend/regain energy), touching only component arrays. | `test_agent`, `test_flock` |
| [`physics`](physics.sprout) | A minimal vertical-dynamics layer (a ballistic hop under gravity), in its own component store. | `test_physics` |
| [`driver`](driver.sprout) | A fixed-timestep driver — decouples the simulation rate from the render rate so the model advances at a constant wall-clock pace regardless of FPS (Gaffer-style). | `test_driver` |

### Presentation
| Module | What it does | Tests |
|---|---|---|
| [`camera`](camera.sprout) | Free-look camera controller — pure state + math for a rig the user drives (rotate around the target, tilt, zoom, pan). | `test_camera` |
| [`view`](view.sprout) | Rendering systems that read the scene's component arrays and draw them via `gfx`. The only module aware of graphics. | (screenshot) |
| [`config`](config.sprout) | A tiny `key value` (Int) config reader, so a demo reads its map-size / terrain / hydrology tunables from a file and re-runs with different settings **without recompiling**. | `test_config` |

## The terrain-rivers world, end to end

`examples/gfx/terrain_rivers_demo.sprout` composes the stack:

1. `config` reads the tunables from `examples/gfx/terrain_rivers.conf`.
2. `terrain` generates biome tags + elevation bands into grids.
3. `hydrology` computes drainage, then `meander` + `widen` reshape the river mask and `carve` sinks
   the channels into the elevation bands.
4. `ribbon` smooths the river centerlines for the water surface.
5. `vegetation` decides where trees grow; `view`/the demo bake terrain + water meshes and scatter
   instanced trees, then run the `camera`-driven render loop.

Everything through step 4 is headless and unit-tested; only step 5 needs a GPU.

## Testing

```sh
just test-loam        # all tests/loam/*.spr (headless, no window)
```

Each `.spr` under `tests/loam/` asserts one module's invariants (determinism, bounds, and the
domain properties above). Because loam resolves via `--package-root`, running a single file directly
needs that flag — see the recipe in the [`justfile`](../justfile) (`test-loam`).
