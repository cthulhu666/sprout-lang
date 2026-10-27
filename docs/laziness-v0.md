# Optional laziness — v0 survey

Status: **survey, no language change proposed.** Dated 2026-10-18. Strict evaluation stays
normative (`docs/spec-v0.md` §1).

## 1. Question

Would opt-in laziness — a `Lazy a` type, lazy pipelines, or lazy arguments — pay for itself in
Sprout? `docs/language-design-v0.md` leaves "explicit laziness" open as a later extension. This
doc checks that against the code we have: `sprout_lang`, `repbit` and `uncharted-suns`.

## 2. Goals and non-goals

**Goals.** Find where strictness costs real work today. For each case, name the cheapest fix and
the layer it belongs to.

**Non-goals.** Lazy-by-default evaluation (`docs/haskell-lessons-learned.md` §1). Changing the
evaluation order of any existing program.

## 3. What laziness is for

Laziness does three jobs. Sprout already covers some of them without it.

| job | lazy-language answer | Sprout today |
|---|---|---|
| stop a traversal early | lazy `foldr` | `fold_while` (`docs/fold-while-v0.md`); `any`/`all`/`find` stop at the first hit |
| skip work nobody reads | unevaluated argument | none; fix per site |
| infinite structures | lazy list | `IntRange` is three ints, never a list; no other user |

A fourth thing often credited to laziness, pipelines without intermediate collections, is an
iterator or fusion question. See §6.

## 4. Prior art

Each row checked against the language's own docs.

| language | mechanism | quoted |
|---|---|---|
| Rust | lazy argument by closure | `unwrap_or`: "Arguments passed to `unwrap_or` are eagerly evaluated; if you are passing the result of a function call, it is recommended to use `unwrap_or_else`, which is lazily evaluated." |
| Rust | pull iterators | "Iterators (and iterator adapters) are *lazy*… Nothing really happens until you call `next`." |
| Kotlin | `Sequence` beside `Iterable` | "the lazy nature of sequences adds some overhead which may be significant when processing smaller collections or doing simpler computations" |
| Clojure | transducers (push) | "a transformation from one reducing function to another"; `reduced` "indicat[es] reduction should stop" |
| GHC | `RULES` fusion | "If a 'good consumer' consumes an intermediate list constructed by a 'good producer', the intermediate list should be eliminated entirely." |
| OCaml | `Lazy.t`, memoized | `force` returns the cached value on later calls; raises `Undefined` "when forcing a suspension concurrently from multiple fibers, systhreads or domains"; "`Lazy.force` is not concurrency-safe" |

Sources: `doc.rust-lang.org/std/option/enum.Option.html`, `doc.rust-lang.org/std/iter/`,
`kotlinlang.org/docs/sequences.html`, `clojure.org/reference/transducers`, GHC User's Guide
"Rewrite rules", `ocaml.org/manual/5.3/api/Lazy.html`.

Two things carry over. Rust and Kotlin keep the strict form as the default and add the lazy form
beside it. OCaml shows the cost of a memoized lazy value once there is concurrency, and Sprout
has green threads.

## 5. Evidence from the corpus

Scanned 2026-10-18: repbit (~2k lines of app code), uncharted-suns (`loam` + `game` ~32k lines,
~81k more in the other games). Paths are relative to each repo.

### 5.1 Work built every time, usually discarded — the real case

- uncharted `game/app.sprout` (around `debug_pump`): every frame builds `scan_field`,
  `col_field`, `col_hit_field`, `orb_field`, `msl_scan`/`svy_scan` and `market_rows`, and passes
  them to `debug_pump`. It reads them only when `debug.next_cmd` returns a command.
- uncharted `grimward/combat_log.sprout` `drain` guards by hand with `log.should_log`. Its
  comment says it would otherwise "build the list sixty times a second for a logger that wants
  none of it".
- sprout_lang: `maybe_or_else` and `maybe_with_default` evaluate the fallback eagerly.
  `BACKLOG.md` (the `and_then` adoption entry) records that moving a site with a real lookup in
  its `Nothing` arm onto `maybe_or_else` changes its meaning.

None of the `maybe_with_default` calls downstream (3 in repbit, 40 in uncharted's main
checkout) computes its fallback with a function call. Each fallback is a literal, a variable or
a field, so none needs to be lazy.

### 5.2 Whole-file read, then split

- uncharted `game/starmap.sprout`: a click reads a ~600 KB tile and splits all of it into
  lines to find one row by id. `game/starcat.sprout` keeps folding each tile after it reaches
  `node_cap`.
- repbit `GET /api/sessions`: up to 1000 sessions plus their sets go DB rows → List → decoded →
  regrouped → reversed → Vec → JSON → string, about eight full copies
  (`app/store.sprout`, `app/model.sprout`, `app/api.sprout` `json_response`).

### 5.3 Sort everything, keep a few

- uncharted `loam/lights.sprout` `reaching`: filters, partitions and sorts every light in
  range, then `take(keep, …)`. It runs per chunk and per mesh, every frame. This is top-k
  selection. A lazy sort would cover it only by accident.

### 5.4 Caches

Six hand-made caches in uncharted (`game/app.sprout` `Cache` with `need_load`/`need_rocks`;
`loam/terrain.sprout` `Ref (Dict Chunk)`; the starmap `loaded` set; `loam/lightgrid.sprout`
sentinels). Each one reloads when a key changes. A compute-once `Lazy a` cannot be invalidated,
so it fits none of them. These were the strongest-looking case for `Lazy a`, and they rule it out.

### 5.5 Generators

`game/survey.sprout` `sweep_go` keeps a resumable cursor and a per-frame budget. Its comment
makes the explicit cursor the point: "the same ship, place and dwell finds the same bodies in any
session". Procedural generation is hash-keyed random access, not a stream. No case.

### 5.6 Pipelines in the compiler and stdlib

9 `|>` combinator stages and 23 nested combinator calls in `stdlib`, `ide` and `examples`.
Most are `join(sep, list_map(…))` over short lists in diagnostics and iface encoding. No hot
path.

## 6. Options, by layer

### 6.1 Caller — reorder

Compute after the decision, not before. For `debug_pump`: read `next_cmd` first and build only
the fields the asked verb needs. This removes the cost rather than delaying it, and costs nothing
when no command is pending. **Preferred for §5.1's game sites.**

### 6.2 Function — take a thunk

Add `_with` forms whose fallback is `() -> a`, as Rust's `unwrap_or_else`:
`maybe_or_else_with(primary, \-> lookup(k))`. `() -> T` is already a real type
(`tests/conformance/run/nullary_thunk_type_ok.spr`). The delay shows at the call site and in the
type. Each call allocates a closure, so it only pays when the fallback costs more than that.
**Preferred for the prelude fallbacks**, once a caller needs one.

### 6.3 Representation — `Lazy a`

A memoized suspension, like OCaml's `Lazy.t`. Rejected:

- Caching writes into a value the GC treats as immutable.
- Two green threads can force one value at once. OCaml raises `Undefined` there; Sprout would
  need the same or a lock.
- An effect inside a suspension fires when it is forced, not where it was written. The effect
  system exists to pin that order down.
- §5.4 shows the caches need keyed invalidation, which a compute-once value cannot give.
- Every case §6.1 and §6.2 don't already cover is better served by §7's library items.

### 6.4 Lazy pipelines

Three shapes:

1. **Pull iterator**, as Rust. Rust's speed comes from per-type compilation and inlining of the
   adapter chain. Sprout does neither, and its closures live on the heap, so each stage likely
   costs an indirect call and possibly an allocation per element. This is inferred from those
   two facts, not measured. Only this shape can `zip` two sources.
2. **Push**, as Clojure's transducers. `map(f)` turns a step function `b -> a -> b` into
   `b -> c -> b`, and one `fold_while` drives the chain. Closures are allocated per stage, not
   per element, and early exit already works. No zip.
3. **Compiler fusion**, as GHC's `RULES`. Sprout already fuses one shape: a comprehension
   `[f(x) for x in xs if p(x)]` lowers to one `list_fold` and one reverse, 2k cons cells against
   4k for `filter |> map` (`docs/list-comprehensions-v0.md` §5).

For effectful sources, shape 2 is the only safe one. A pull iterator over IO runs the IO whenever
it is pulled. A fold puts the effect row on the fold, where `!{e}` already tracks it.

## 7. Recommendation

No language-level laziness. In order of payoff:

1. **uncharted:** reorder `debug_pump` so per-frame debug fields are built only for the asked
   verb (§6.1).
2. **stdlib:** a push-style, stop-early fold over a file's lines, used by starmap and starcat
   (§5.2, §6.4 shape 2). It needs a design of its own: whether it reads in chunks or still reads
   the whole file first decides most of its value. Filed: `BACKLOG.md` §6.5.
3. **stdlib:** a top-k helper (`take_smallest_by` or similar) for `lights.reaching` (§5.3).
   Filed: `BACKLOG.md` §5.
4. **prelude:** `_with` thunk forms of `maybe_or_else` / `maybe_with_default`, when a caller
   needs one (§6.2).

Found on the way, not about laziness:

- repbit `app/store.sprout` `try_map` round-trips Vec → List → Vec "until the instance lands".
  `instance Traversable Vec` has landed (`stdlib/prelude.sprout`), so it can go.
- uncharted `game/collision.sprout` `system_colliders` and `game/body_rows.sprout` append inside
  a fold, which is quadratic. n is the planet count, so it is harmless today.

## 8. When to revisit

- A measured hot path where a two-stage pipeline's intermediate list is a real cost: revisit
  §6.4 shape 2 first.
- A cache that is truly compute-once with no key: revisit §6.3, starting from OCaml's
  concurrency behaviour.

`enumerate` waits on this decision: a strict one would fix its result to `List` before a lazy type
could claim it (`docs/list-toolkit-v0.md` §4.3).
