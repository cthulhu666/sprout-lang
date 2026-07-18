# HTTP Stateful Server (v0) — design

**Status:** experimental. Describes the effectful-handler capability of
`stdlib/http_server.sprout` and the state/redirect patterns a form-driven CRUD app
builds on. Supporting design material, not normative — `docs/spec-v0.md` remains the
source of truth for the language core. Reference implementation:
`examples/http_web_server.sprout` (a users CRUD app). Builds on the request-param
layer (`docs/http-request-params-v0.md`).

## 1. Problem statement

Route handlers were pure: `Route` wrapped `HttpRequest -> HttpServerResponse` with no
effect. A pure handler is a function of its request alone, so nothing could carry
state from one request to the next — the pre-CRUD `/users` page could only render a
*hardcoded* list. Any real app (a form that creates/edits/deletes records) needs
handlers that read and write state shared across requests.

## 2. Goals / non-goals

**Goals**
- Let a handler perform I/O (read/write shared state, touch disk/network) while
  building its response.
- Keep the change additive: existing pure handlers and examples compile unchanged.
- Support the Post/Redirect/Get pattern so form submissions survive a browser refresh.

**Non-goals (deferred)**
- Enforcing effects at the type level (Sprout's effect system is currently erased at
  runtime — see `docs/fundamentals-code-review-handoff-2026-07-03.md`). The flip below
  is a *types-honesty* change, not something the checker gates today.
- Path/route params (`/users/:id`) — still deferred (request-params doc §7); IDs ride
  in the query string / form body instead.
- A session/auth layer, persistence across process restarts (state is in-memory).

## 3. Design decisions

- **Effectful handlers.** `Route`'s handler field, and `run_route`/`dispatch`/`serve`/
  `serve_n` (plus the internal `serve_loop`/`serve_forever_loop`/`handle_connection`),
  now carry `!{IO}`: `HttpRequest -> HttpServerResponse !{IO}`. This is **additive** —
  Sprout's effects are additive (pure ⊑ effectful), so a pure handler subsumes into the
  effectful slot with no change (`Route("GET", "/health", health)` still typechecks with
  a pure `health`). Only code that *consumes* `dispatch`'s result must now thread the
  effect through a `do`-block; handlers themselves are opt-in.
- **In-memory state via `Ref`.** State lives in a `Ref` the handlers close over
  (`ref_new`/`ref_read`/`ref_write`, runtime builtins). A `Ref`-backed store is simpler
  than a channel-actor and needs no file I/O.
- **Read-modify-write is atomic without a lock.** `ref_read` and `ref_write` are plain
  memory load/store (no scheduler yield). Sprout's green scheduler is cooperative and
  single-core; it only preempts at I/O/channel/sleep parks. So a handler's
  `read → compute new value → write` runs as one uninterrupted section even while many
  connections are served concurrently — no lost updates, no mutex. (This guarantee is
  specific to the current single-core cooperative runtime; a future multi-core scheduler
  would need a real lock or the actor pattern.)
- **Post/Redirect/Get.** Mutating routes reply `303 See Other` with a `Location` header
  (`see_other(location)`), so a browser refresh after a POST re-GETs the result page
  instead of re-submitting the form. This adds `303` to the shared status table
  (`stdlib/http.sprout`) — the one status the CRUD pattern needs that was missing.
- **Auto-escaped rendering.** Pages are `stdlib.template` templates; `{{ }}`
  auto-escapes, so user-supplied names (`Tom & Jerry`, `Ada <Countess>`) render as
  entities in both cell and `value="…"` attribute contexts — the app is XSS-safe by
  construction, not by handler discipline.

## 4. API surface added

`stdlib/http_server.sprout`:
- `see_other(location: String) -> HttpServerResponse` — a 303 redirect with an empty
  body and a `Location` header.
- Handler type flip (above): `Route`, `dispatch`, `serve`, `serve_n` now carry `!{IO}`.

`stdlib/http.sprout`:
- `303 See Other` added to the status table (used by `status_line`/`render`).

## 5. Tests

- `tests/stdlib/test_http_redirect.spr` — `see_other` renders a 303 + `Location`
  (RED without the status-table arm).
- `tests/stdlib/test_http_effectful_handler.spr` — characterization: a handler closing
  over a `Ref Int` returns a different body on the second hit (state persists). Guards
  the flip's behavior since the typechecker cannot (effects erased).
- `tests/stdlib/test_http_router.spr` — unchanged pure handlers still route through the
  now-effectful `dispatch` (subsumption).
- End-to-end: `examples/http_web_server.sprout` drives the full create/list/edit/update/
  delete lifecycle over real HTTP (covered by `just compile-examples-stage1` for compile;
  the run is exercised manually — see the example's header for the curl script).

## 6. Deferred roadmap

- **Path/route params** (`/users/:id`) — see `docs/http-request-params-v0.md` §7. Would
  replace the current id-in-query/form workaround with `/users/3` + `path_param`.
- **Persistence** — a file- or DB-backed store behind the same handler interface; the
  handler effect (`!{IO}`) already permits it.
- **Effect enforcement** — once effects are enforced, the pure/effectful boundary the
  flip documents becomes load-bearing rather than advisory.
