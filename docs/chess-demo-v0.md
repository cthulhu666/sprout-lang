# 3D Chess Engine Demo — v0 (design + build plan)

**Status:** proposed. Non-normative — `docs/spec-v0.md` remains the source of truth
for the language core. This doc records the design for a showroom demo: a full chess
engine written in pure Sprout, rendered in 3D via the `gfx`/Loam stack, playing itself
(AI vs AI) with free downloaded 3D piece models. No code has landed yet; this is the
plan to build against.

## 1. Problem statement

Sprout needs a flagship graphics/game example that exercises the language's
functional core at real depth — non-trivial data modelling, exhaustive pattern
matching, recursion-as-iteration, purity — while showing off the 3D backend end to
end (model loading, tiled geometry, an orbit camera, animation). A **chess engine**
is an ideal vehicle: the rules layer is pure, self-contained, and has an unambiguous
correctness oracle (perft node counts); the 3D layer is a thin driver over the
existing `loam/` patterns.

The first deliverable is **AI-vs-AI self-play**: the engine plays both sides while the
camera orbits and pieces slide between squares. The architecture reserves a swappable
**move-source seam** so a human player (keyboard-cursor selection) can be added later
without restructuring the loop.

## 2. Goals / non-goals

**Goals**
- A **pure-Sprout** chess engine (no `!{IO}`): legal move generation, check /
  checkmate / stalemate, and a negamax + alpha-beta AI with position evaluation.
- Correctness proven headless via **perft node-count gates** (the chess-programming
  gold standard), runnable in `just test` with no graphics dependency.
- A 3D self-play demo mirroring the existing `loam/` + `examples/gfx/ecs_agents.sprout`
  structure — nothing touching the compiler, prelude, or **bootstrap seed**.
- Free (CC0) downloaded 3D piece models, loaded via the existing `gfx.load_model`.

**Non-goals (v0)**
- A strong engine. Demo search depth is shallow (default 2 ply) and paced for a smooth
  60 fps camera, not for playing strength.
- Human interaction now (the seam is designed; the human `Mover` is a v0 stub).
- Full draw-rule fidelity (threefold repetition is out; 50-move + a hard ply cap
  guarantee termination).
- Mouse/ray picking (deferred with the human player).

## 3. Verified foundation (facts checked against source before design)

1. **No `%` operator, no `for`/`while`.** Modulo is the local idiom
   `imod(a,b) = a - ((a/b)*b)` (as in `stdlib/rng.sprout:24`, `loam/agent.sprout:59`);
   board-square parity is `is_even(file+rank)` (`stdlib/math.sprout:61`). All iteration
   is tail recursion.
2. **Immutable `Vec Int`** (`vec_from_list`, `vec_get_or`, persistent-copy `vec_set`) is
   pure — it keeps the whole engine pure and headless-testable. `MutVec` (all `!{IO}`)
   is reserved for driver-side render state only.
3. **9-field cap** on single-constructor products — both records and ADT constructors
   lower through `sprout_makeN`, which stops at `make9` (`loam/scene.sprout:20`). State
   records are sized to fit; overflow is split into sub-records.
4. **`gfx.draw_model` pins raylib's tint to `WHITE`** (`graphics/sprout_gfx.c:836`), and
   there is no generic text-draw. Chess needs a light and a dark army plus a HUD — see
   §7.
5. **`stdlib/gfx.sprout` is never bundled into the compiler** (`gfx.sprout:5`), so gfx
   shim edits **do not touch `bootstrap/compile_driver.ll`** — a `seed-fp-ack`, not a
   `refresh-seed`. This is what makes the approved host additions in §7 seed-safe.
6. **Build/test harness.** Graphics demos run only via `just run-gfx <file>` (links the
   raylib shim). Headless engine tests mirror `just test-loam`: compile with
   `--package-root`, link the **core** runtime, run, assert `SUITE PASSED`.
   `compile-examples-stage1` treats gfx examples as an **xfail** set.

## 4. Structure (mirrors the existing `loam/` package)

- **`chess/`** — the pure engine package: modules `chess.coords`, `chess.piece`,
  `chess.move`, `chess.board`, `chess.attack`, `chess.movegen`, `chess.rules`,
  `chess.eval`, `chess.search`, `chess.perft`, `chess.fen`.
- **`tests/chess/*.spr`** — headless suites (core runtime, `--package-root`), run by a
  new `just test-chess` recipe cloned from `test-loam` and wired into `just test`.
- **`examples/gfx/chess_3d.sprout`** — the `!{IO}` 3D self-play driver.
- **`assets/models/chess/{pawn,knight,bishop,rook,queen,king}.glb`** + `ATTRIBUTION.md`.
- **`graphics/sprout_gfx.c`** + **`stdlib/gfx.sprout`** — two additive externs (§7).
- **`justfile`** — `test-chess` (+ a separate slow `test-chess-perft-deep`); add
  `chess_3d.sprout` to the `compile-examples-stage1` xfail list.

## 5. Engine design

Dependency order `coords → piece → move → board → attack → movegen → rules → eval →
search → perft`, plus `fen`. Every module is pure.

- **`chess.coords`** — `rank_of = sq/8`, `file_of = sq - rank_of*8`, `make_sq`,
  `on_board(file,rank)` (off-board via bounds, never index arithmetic — dodges file-wrap
  bugs with no modulo), `light_square = is_even(file+rank)`.
- **`chess.piece`** — signed-Int encoding: `0` empty, white `+1..+6`, black `-1..-6`
  (P,N,B,R,Q,K); `color_of = sign`, `type_of = abs`. A `PType` ADT `deriving (Enum)` is
  kept for **exhaustive** movegen dispatch (a missed kind is a compile error).
- **`chess.move`** — `type Move deriving (Eq) = | Move from to promo flag` (4 fields;
  free `Eq` gives move-list equality for tests). Flags cover quiet / capture /
  double-push / en-passant / castle-K / castle-Q / promotion. `ToString` (algebraic,
  `e2e4`) is hand-written.
- **`chess.board`** — `type Position = (board: Vec Int, side, castling, ep, halfmove,
  fullmove)` (6 fields; castling = 4-bit mask, ep = square or `-1`, side = `±1`).
  `piece_at`, `set_piece`, `initial_position`.
- **`chess.attack`** — `king_square`, `is_square_attacked(pos, sq, by)` via pawn/knight/
  king offsets and bounds-checked sliding rays. Shared by legality and check detection.
- **`chess.movegen`** — `pseudo_legal_moves(pos) -> List Move`; per-piece tail-recursive
  generators including double-push, en-passant, promotions (4 moves), castling.
- **`chess.rules`** — `make_move` (ep capture, castle rook move, promotion, rights/ep/
  clock update, side flip), `in_check`, `legal_moves` (pseudo filtered by make-move +
  own-king-safety; castle-through-check filtered here), `is_checkmate`, `is_stalemate`,
  `is_draw_50`.
- **`chess.eval`** — `evaluate(pos) -> Int`, white-positive: material
  (P100 N320 B330 R500 Q900) + six piece-square tables (`Vec Int` len 64; black mirrors
  via `make_sq(file, 7-rank)`).
- **`chess.search`** — pure `negamax(pos, depth, alpha, beta)` and
  `best_move(pos, depth) -> Maybe Move`. Leaf returns `pos.side * evaluate`; no legal
  moves returns `-mate_score` (checkmate) or `0` (stalemate). Depth-only budget (a pure
  function can't hold a mutable node counter without going `!{IO}`); optional MVV-LVA
  ordering.
- **`chess.perft`** — `perft(pos, depth) -> Int`: the movegen oracle and the
  immutability canary (§8).
- **`chess.fen`** — `parse_fen` / `to_fen`: every test position is a string, and
  `Position` equality is decided via `to_fen`.

## 6. 3D self-play driver (`examples/gfx/chess_3d.sprout`)

- **Board** — 8×8 thin `draw_cube` tiles, origin-centered `(to_double(file)-3.5)*tile`,
  light/dark via `light_square`. Static geometry → **baked once** with
  `mesh_capture_begin` / `draw_captured` (the `terrain_demo` idiom).
- **Pieces** — six neutral model handles loaded once (a `Handles` record); each frame
  iterate 0..63 and draw each non-empty piece with `gfx.draw_model_tinted` (light tint =
  white army, dark = black), Y-angle 180° for black, tuned scale.
- **Animation** — `type AnimState = (active, piece, fx, fz, tx, tz, t)`. The board updates
  logically on move-apply; the renderer lerps the moving piece `(fx,fz)→(tx,tz)` and skips
  drawing it at its destination until `t >= 1`.
- **Camera** — `loam.camera.Cam` + `cam_drive` auto-yaw → `cam_eye` →
  `gfx.set_camera(..., 0,0,0, fovy)` (target = board center).
- **Move-source seam** — `type Mover = | EngineMover depth | HumanMover`. The driver polls
  the side-to-move's `Mover` uniformly; `EngineMover` calls the pure `best_move`,
  `HumanMover` is a v0 stub returning `Nothing`. The human path drops in later (keyboard
  cursor) without touching the loop's structure.
- **Search pacing (never per-frame)** — a `think_acc` timer; when a ~0.6 s interval fires
  **and** no slide animation is in flight, run one synchronous `best_move` at demo depth,
  apply it, and start a ~0.35 s slide. Search cost is amortized to ≤ 1 per interval — at
  worst one dropped frame, no mid-frame interruption needed. Demo depth defaults to 2,
  confirmed empirically via a `time_now_micros()` + `perft` bench before raising.
- **State** — `type GameState = (pos, cam, anim, think_acc, white_mover, black_mover,
  result, ply)` (8 ≤ 9); `Handles` threaded separately. A ~200-move cap forces a draw
  result so self-play always terminates with a shown outcome. Tail-recursive loop,
  `window_should_close` guard, `SPROUT_GFX_MAX_FRAMES` honored for the canary.

## 7. Host additions (approved)

Two **additive** externs — existing `draw_model` and every `character_*` demo untouched,
and both **seed-free** per §3.5:

- `gfx_draw_model_tinted(handle, x, y, z, angle, scale, r, g, b)` — a copy of
  `gfx_draw_model` with `WHITE` replaced by `{r,g,b,255}`. Enables the two armies.
- `gfx_draw_text(x, y, text, size)` — raylib `DrawText`, for the in-window HUD
  (side to move / last move / result).

Each gets a thin wrapper in `stdlib/gfx.sprout`. Both are generically useful beyond chess.

## 8. Test plan (TDD)

Each `tests/chess/*.spr` uses `stdlib.test` and asserts `SUITE PASSED`.

- **Perft (always-on gate)** — from the initial position: **d1 = 20, d2 = 400,
  d3 = 8902**. Built green **before any eval or search**: perft is the movegen oracle and
  the *immutability canary* — a wrong count most likely means `vec_set` aliasing the
  `Position` across the move loop, i.e. the persistent-copy assumption failing. Caught
  here, at the cheapest point.
- **Perft (positions)** — Kiwipete via `parse_fen`: d1 = 48, d2 = 2039, d3 = 97862.
- **Perft (deep, separate slow recipe)** — d4 = 197281; not on the always-on path
  (a copying board makes perft4 add seconds to every `just test`).
- **Rules** — a mate-in-1 → `is_checkmate` after the mating move; a known stalemate →
  `is_stalemate`; `in_check` detection.
- **Eval** — `evaluate(initial) == 0` (symmetry); up-a-queen → large positive.
- **Search** — `best_move` returns the mating move on a forced mate-in-1; wins hanging
  material on a simple tactic.
- **Coords / FEN** — sq ↔ (file,rank) round-trip, parity, `imod`;
  `parse_fen(to_fen(p)) == p`.

## 9. Assets

Six **CC0** low-poly `.glb` pieces (candidate sources: Poly Pizza, Kenney, OpenGameArt,
Sketchfab filtered to CC0 — **GLB, not FBX**; the loader rejects FBX) downloaded to
`assets/models/chess/`, each verified to return a non-negative `gfx.load_model` handle
(as `assets/models/characterMedium.glb` does today). Source/author/license recorded in
`assets/models/chess/ATTRIBUTION.md`. With the tint extern, six neutral models suffice.

## 10. Build order (failing-test-first each phase)

0. **Assets & scaffold** — download models + `ATTRIBUTION.md`; empty `chess/` modules;
   `test-chess` recipe wired into `test` (green harness, 0 tests).
1. **Coords & piece** — `test_coords.spr`.
2. **Board & FEN** — `test_fen.spr`.
3. **Movegen + perft (immutability canary)** — `test_movegen_perft.spr` (20/400/8902)
   green before eval/search; then Kiwipete and the deep-perft slow recipe.
4. **Rules / endgame** — `test_rules.spr` (mate-in-1, stalemate, check).
5. **Eval + search** — `test_eval.spr`, `test_search.spr`; then the depth bench.
6. **3D static board** — bake tiles, orbit camera; `run-gfx` smoke with
   `SPROUT_GFX_MAX_FRAMES`.
7. **Pieces + tint + animation** — add the two externs; load handles; slide interpolation.
8. **Self-play integration** — `Mover` seam (EngineMover live), pacing, move cap → result,
   HUD.
9. **Polish & docs** — update this doc's status, README/examples note, `just fmt`, add
   `chess_3d.sprout` to the xfail list, final gates.

## 11. Definition-of-Done notes (per AGENTS.md)

- **Bootstrap seed is not touched** — no prelude/compiler change; the gfx externs are
  seed-free (`seed-fp-ack` if the gate flags a staged `stdlib/gfx.sprout`, **not**
  `refresh-seed`).
- **Must** add `examples/gfx/chess_3d.sprout` to the `compile-examples-stage1` xfail
  string — an unlisted gfx example is treated as expected-pass and link-fails against the
  core runtime.
- Primary gate is `just test-chess` (perft1–3 + rules + eval + search), wired into
  `just test`; `test-chess-perft-deep` runs perft4 separately.
- End-to-end canary: `SPROUT_GFX_MAX_FRAMES=300 just run-gfx examples/gfx/chess_3d.sprout`
  — window opens, board + tinted pieces render, camera orbits, AI plays with slides, HUD
  shows state, game reaches a result.
</content>
</invoke>
