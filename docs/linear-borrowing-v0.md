# Borrowing for linear types — design note (v0)

Status: **APPROVED and IMPLEMENTED** as M4.5 (2026-08-07). The user chose **Option D + the
field-read borrow** (§6, §15) and scoped v0 to `stdlib/net.sprout`, with all of
`stdlib/http_server.sprout` deferred. §16 below records what implementation changed relative to
this note — read it before treating any section here as a description of the shipped behaviour.
The normative description is `docs/spec-v0.md` §5.8.

Original status line: *design note, pre-approval.* Written 2026-08-07. Proposes adding **borrowing** to Sprout's
linear type system (M4, landed in PR #22/#23), so a `type linear` value can be *used without being
consumed*. This is the feature standing between linearity and the stdlib's real resources:
`TcpConnection` (and `TcpListener`) are **acquire → use N times → release once**, a shape strict
use-exactly-once cannot express, so today their `close`/`close_listener` discipline is unenforced.
Borrowing turns that convention into a compile error.

The one **hard** constraint is that Sprout is **rank-1 Hindley-Milner**: that eliminates the
higher-order bracket (Option C, §5.1) outright — a type-system fact, not a preference. The remaining
options — **first-class regioned references** (A, Rust/Austral), a **`borrow` block** (B, Austral's
statement), and **`borrowing`/`consuming` parameter modifiers** (D, Swift) — are all rank-1-safe and
are judged here on **expressiveness vs engineering cost**, not on any "beginner language" narrative
(the project is stepping back from that framing; approachability is a *soft* preference, not a veto).
This note **recommends D as the minimal first step** (§15) but keeps **A live as a larger option**
whose extra machinery would additionally solve the closure-escape problem (§7) that B and D defer —
the choice between "minimal now" and "the region system" is a scope call, and that call is the user's.
Follows the resource survey in `docs/linear-task-v0.md` §8 and the M4 docs
(`docs/linear-types-m4-scoping-2026-08-01.md`, `docs/linear-types-m4.2-enforcement-2026-08-06.md`).

## 1. Problem statement

Linear `Task a` landed cleanly because `N = 1`: a fork handle is awaited (or detached) exactly once.
**A socket has `N > 1`**, and that is exactly where the current system fails:

```
conn <- connect(host, port)     # acquire
write_all(conn, request)        # use  — but under strict linearity this CONSUMES conn ...
line  <- read_exact(conn, 128)  # ... so this second use is a REUSE error
close(conn)                     # release — a third use
```

Under strict M4 the first `write_all(conn, …)` consumes `conn`; the following `read_exact` and
`close` are "used more than once". So `TcpConnection`/`TcpListener` **cannot** be made `type linear`
at all — yet each ships a release operation (`close`, `close_listener`) that *nothing forces the
caller to invoke*. That unenforced-release gap is the bug class linear types exist to kill, and it is
the one we currently cannot reach.

The missing capability: say *"this function reads/writes the resource without claiming its one
consuming use."* That is a **borrow**.

## 2. Goals / non-goals

**Goals.**
- Let a `type linear` value be **used non-consumingly** by an operation, so it stays live and still
  owes its one consuming use afterward.
- Make **`TcpConnection` (straight-line and recursive sessions)** enforceable-linear: `read`/`write`
  borrow; `close` consumes. `TcpListener`'s recursive accept-loop is the same shape and follows.
- Stay within **rank-1 HM** — a hard type-system constraint: no higher-rank types. (Whether to take
  on a lifetime/region system is an open scope question, not a goal boundary; see A vs D in §6/§15.)
  Where it costs no expressiveness, prefer an approachable surface — a soft preference, not a veto.
- No new builtin — borrowing is a type-checker feature; it emits no runtime operation and is erased
  before IR (§9).

**Non-goals / explicitly gated (corrected after review).**
- **`Scope` is NOT unblocked by borrowing.** A `Scope` is only ever delivered as a lambda parameter
  — `with_scope(\s -> body(s, …))` — and `body` then captures and reuses `s`. A linear `Scope` is a
  linear value **captured in a closure**, which is higher-order linearity (**M4.4**, deferred), *on
  top of* the multiple-use problem borrowing solves. Borrowing is necessary but not sufficient for
  `Scope`; do not claim it.
- **The combinator-over-a-borrow form** — `list_each(xs, \x -> write(conn, x))` — is **not** in v0.
  It captures the borrowed value into a lambda, and soundness then depends on whether that lambda
  escapes (§7). Sprout has no escaping/non-escaping-closure distinction yet, so v0 covers the
  **straight-line and recursive** session shapes (no lambda capturing the resource) and defers the
  combinator shape to the same escaping-closure work M4.4 needs.
- **First-class references as values** (store a borrow in a record, return it, two independent
  borrows threaded) — the full Rust/Austral regioned-reference system; out of scope, likely
  permanently (§6, Option A).
- **A shared-XOR-mutable split** (Rust `&`/`&mut`). v0 needs only "borrow = use without consuming";
  a read-vs-write refinement is a later increment (§13).
- **Governing raw heap memory.** The GC owns allocations; borrowing here conserves **logical
  resources** (sockets), never memory (§4).

## 3. Prior-art survey (primary-sourced)

Every row is verified against the language's own reference/spec (URLs in §14). The design question —
*"how does a use-once system let you use a value several times before releasing it?"* — has exactly
three established answers: **borrow via a reference + region** (Rust, Austral), **borrow via a
parameter modifier** (Swift), or **thread it / CPS** (Linear Haskell).

| Language | Ownership discipline | Borrowing mechanism | Escape prevention |
|----------|---------------------|---------------------|-------------------|
| **Rust** | *affine* (used **at most** once; scope-exit drops) | `&T` shared ref — *"any number of shared references may exist"*, creating one *"prevents direct mutation"*; `&mut T` exclusive, *"the only way to access the value"*, not `Copy`; a value *"cannot be moved while borrowed"* | **lifetimes** `&'a T`, inferred by the borrow checker |
| **Austral** | *linear* (used **exactly** once) | *"stolen … from Rust"*: `&x`→`&[T,R]`, `&!x`→`&![T,R]`; `borrow x as r in R do … end borrow;`. During the block the value *"is unusable, since it has been borrowed."* | **regions** — `R` is a *"lexically-scoped type-level tag that prevents references from escaping"* |
| **Swift** | *noncopyable* `~Copyable` — *"values … always have unique ownership, and can never be copied"* | **parameter modifiers**, no reference type: `borrowing` = *"temporary, non-owning access … the argument remains valid after the function returns … cannot be escaped or stored"*; `consuming` = *"transfers ownership … caller cannot use the argument after the call"* | **the borrowing param cannot be escaped/stored** — a *local, per-function* rule; no regions, no lifetimes |
| **Linear Haskell** (`LinearTypes`) | *linear* arrow `a %1 -> b` (`⊸`) | **none** — thread the value / CPS so it is *"consumed exactly once"* overall | n/a |

**Reading for Sprout.** Rust and Austral both introduce a **reference type + a region/lifetime**. That
machinery is exactly what makes the escape rule *decidable in general* — the region tracks whether a
borrow outlives its owner — so it is the only surveyed design that **solves the closure-escape crux
(§7) rather than deferring it**. Its cost is building a region/lifetime system (Option A), a real
type-system project. Swift is the outlier that gets borrowing **without a reference type or a region**:
the borrow is a *parameter convention* and "cannot escape" is a **local rule on the callee** — cheap,
but it cannot decide the escaping-closure case, so it defers it (Option D). Rank-1 caveat, precisely
scoped: the `runST`-style **rank-2** obstruction bites **only Option C** — the scoped HOF bracket
`with_borrow : T -> (forall r. Ref r T -> b) -> b`, where the region is quantified *under* the lambda
argument. It does **not** condemn Option A: a region variable quantified at a function's *own* binding
site (`read : Ref r TcpConnection -> …`, `r` generalized in `read`'s scheme) is **rank-1**, and Sprout
already does exactly this for a second sort of variable — **effect variables** are rank-1-quantified in
`Scheme` today (`types.sprout:220`). So A's region-polymorphism is feasible by mirroring the existing
effect-variable machinery; its cost is *building* that region sort and its escape rules, not a
type-system rank jump. Linear Haskell proves borrowing is an ergonomic, not a
soundness, requirement — but its thread-everything answer is the verbosity Austral names and a poor
fit for `write; read; close`.

## 4. How this "borrowing" relates to Rust's memory-management "borrowing"

Kuba's question, from the sources above: **they are the same construct — Rust's is the
memory-management instance of it.**

- **Rust ownership *is* a substructural (affine) type discipline.** A Rust value is usable at most
  once by move; leaving scope drops it (its one use, spent). The borrow checker is that affine system
  specialized so the conserved resource is *ownership of an allocation*, and a borrow (`&`/`&mut`) is
  a **time-bounded, non-consuming capability** to touch it without spending the owner's one move; the
  lifetime `'a` is the time bound.
- **Linear-types "borrowing" is the same idea, resource generalized.** Austral is a *pure linear*
  language that took Rust's borrowing verbatim (*"lock, stock, and barrel"*) but applies it to **any
  linear value**. Its `&[T,R]` + region `R` are Rust's `&T` + lifetime `'a` respelled.
- **Swift shows the mechanism minus memory.** `~Copyable` + `borrowing`/`consuming` give the exact
  ownership discipline with the compiler-inserted-copies removed and **no lifetime surface** — the
  borrow is a calling convention, not a pointer with a lifetime.
- **So: identical mechanism, different conserved thing.** Rust conserves *memory ownership*; Sprout
  would conserve *resource handles* (sockets). Crucially **Sprout borrowing never governs raw memory**
  — the GC owns allocations, so there is no move/drop of a heap block to protect. That deletes the
  hardest part of Rust's model (dangling-pointer reasoning) and leaves only what Sprout needs: *don't
  release the resource while it's in use, and don't lose track of the release.* Same borrow, strictly
  smaller job — which is *why* Sprout can take Swift's lifetime-free convention rather than Rust's
  lifetimes.

One line: **Rust borrowing = affine borrowing applied to memory; Sprout borrowing = the same applied
to resource handles, with the GC handling memory so lifetimes never have to.**

## 5. The one hard constraint, and the axis it leaves open

**The hard constraint — rank-1 Hindley-Milner (verified: `types.sprout:219` — `Scheme` quantifies
only at binding sites; no higher-rank types).** This eliminates exactly *one* design: the
higher-order bracket `with_borrow(res, \r -> body)`, whose escape-safety needs a `runST`-style rank-2
phantom region `forall r. Ref r T -> …` quantified *under* the lambda argument. Sprout cannot express
that signature, so **Option C is out** unless Sprout gains higher-rank types (not proposed here). This
is a type-system fact, not a judgment call.

**What rank-1 does *not* decide.** It does not rule out first-class regioned references (A): a region
variable quantified at a function's *own* binding site is rank-1, and Sprout already quantifies a
second sort of variable this way — **effect variables** (`types.sprout:220`). Nor does it favor any of
A/B/D over the others on soundness — all three are rank-1-safe. So the real decision among A, B, D is
**expressiveness vs engineering cost**, and it is genuinely open:

- **A** builds a region system (new variable sort + escape rules, modeled on the existing effect
  machinery) and in return **decides the escaping-closure case (§7)** — the combinator-over-borrow
  form works soundly and immediately, and borrows can eventually be first-class.
- **D** adds two parameter keywords, a checker-only change with no new type machinery, and **defers**
  the escaping-closure case to a later non-escaping-closure notion (shared with M4.4).
- **B** sits between: a lexical `borrow` block, rank-1-safe, but it neither reaches A's expressiveness
  nor undercuts D's cost (§6).

Approachability of the surface is a *soft* input to this call, not a veto — and the call (minimal step
now vs. the region system) is the user's to make. §15 recommends D as the minimal first step while
keeping A explicitly on the table.

## 6. Design options

**Option A — first-class regioned references (Rust / Austral).** `&T` values with a region variable,
storable and (within their region) passable. **The most expressive, and the only option that solves
§7:** the region tracks whether a borrow outlives its owner, so escaping-closure capture is *decided*,
not deferred — the combinator-over-borrow form works soundly and now. Region-polymorphism is rank-1
(quantify the region at each function's binding site, mirroring Sprout's existing effect variables,
`types.sprout:220`), so this is **not** blocked on higher-rank types. Its cost is real but *nameable*:
build a region variable sort in the type checker (fresh-region generation on `borrow`, escape rule =
"a region may not appear in a scheme generalized outside it"), and add a `&T` reference type. This is
a larger project than B or D — a genuine scope choice, **not** rejected here. Choose A if the project
wants the escaping-closure case handled properly up front and/or foresees first-class borrows.

**Option B — `borrow x as r in …` special form (Austral's statement).** A block that binds a
non-consuming `r` and freezes `x`. Sound under rank-1 (the block *is* the region; escape is a local
scope check). *Viable, but bettered by D:* it still introduces a reference binding `r` and a `&T`
type, and the "does `r` escape into a captured lambda" question (§7) is unavoidable and identical to
D's — so B pays for a new type former and a new expression without solving anything D doesn't.

**Option C — higher-order `with_borrow(res, \r -> body)` bracket.** The most Sprout-idiomatic
shape, but **unsound without rank-2 phantom regions** (§5.1). *Blocked on higher-rank types.*

**Option D — Swift-style `borrowing`/`consuming` parameter modifiers (recommended as the minimal
first step, §15).** No reference type, no region, no block. Operations declare how they take a linear
parameter:

```
export fn read (conn: borrowing TcpConnection, n: Int) -> Result … !{IO} = …   # non-consuming
export fn write(conn: borrowing TcpConnection, s: String) -> Result … !{IO} = …  # non-consuming
export fn close(conn: consuming TcpConnection)          -> Unit       !{IO} = …   # consuming

# straight-line session — no block, no reference, reads like ordinary calls:
conn <- connect(host, port)
_    <- write(conn, request)     # borrows; conn still owned & unconsumed
line <- read(conn, 128)          # borrows again
close(conn)                      # the single consuming use

# recursive session — the listener/read-loop shape, also fully covered:
fn drain(conn: borrowing TcpConnection) -> Result … !{IO} =
  do
    r <- read(conn, 4096)
    if at_eof(r) then Ok(()) else drain(conn)   # each call borrows; no consume in the loop
# caller: drain(conn); close(conn)
```

The checker rules are **local** and need no inference, no higher-rank types, no runtime cost:

- **Callee side (per function):** a `borrowing` parameter may be read/passed-onward to other
  `borrowing` positions, but **may not be consumed, returned, or captured by an escaping closure** —
  Swift's *"cannot be escaped or stored."* A `consuming` parameter is the value's one consuming use.
  Both are checkable looking only at the function body.
- **Caller side:** a use in a `borrowing` argument position **does not count as a consume**; a use in
  a `consuming` position **is** the consume. This is a small extension to the existing M4 accounting
  (add "borrowing-position ≠ consume"); everything else — leak if never consumed, reuse if consumed
  twice, branch convergence — is unchanged and still fires (a missing `close` is still a leak).

*Verdict: recommended as the minimal first step.* Rank-1-safe, lowest engineering cost (no new type
former, region sort, or block — two parameter keywords), a **checker-only** change (§9) sufficient for
the straight-line and recursive socket shapes. Its concession is real and stated: it **defers** the
escaping-closure case (§7), which A would solve now. Scope v0 to **borrow-vs-consume only** and to
**`TcpConnection` first** — and note D does not foreclose A: a region system can be added later over
the same `type linear` foundation if the deferred case becomes pressing.

## 7. The discriminating question: closures that capture a borrow

This is the crux the earlier draft glossed, and it decides B/C/D alike. Consider capturing the
resource into a lambda:

```
list_each(lines, \l -> write(conn, l))   # write borrows conn; the lambda captures conn
close(conn)
```

`write` only *borrows*, so no consume happens inside the lambda — the leak/reuse accounting is
untouched. The **only** hazard is a captured borrow that **outlives the consume**: if the lambda
escaped (were stored and run after `close(conn)`), it would `write` a closed socket — use-after-free.
So safety turns entirely on **escaping vs non-escaping** capture:

- **Non-escaping** (`list_each` calls the lambda synchronously and discards it before returning):
  the lambda cannot outlive `close`, so the borrow-capture is **sound**.
- **Escaping** (`task_fork(\_ -> write(conn, …))` stores the lambda to run later): it can outlive
  `close` → unsound, and it is *also* the M4.4 captured-linear case.

Sprout has **no escaping/non-escaping-closure distinction** (no Swift `@escaping`). Therefore v0
**cannot** safely admit the combinator form yet — permitting it would require either that distinction
or a whole-program escape analysis, and special-casing a known-safe combinator set is the
magic-hack pattern we reject. **v0 rule (chosen explicitly):** a `borrowing` value **may not be
captured by any lambda**; borrowing works only in direct calls and recursion. This is strictly local
and sound. The combinator-over-borrow form is a fast-follow, unlocked by the **same non-escaping-
closure notion** that M4.4 needs — so it is one feature, filed once, not two.

(Under Option B this reappears verbatim as "may `r` be captured by a lambda?" — B does not escape it;
D and B are equivalent on the hard part, and D is cheaper on the easy part.)

## 8. Syntax & semantics impact (Option D)

- **Two new parameter modifiers**, `borrowing` and `consuming`, valid only on a parameter whose type
  is a `type linear`. A linear parameter with **no** modifier keeps today's meaning (consuming —
  passing a linear value *is* a consume), so existing linear code is unchanged; `borrowing` is the
  new, weaker convention.
- **No new type former, no new expression, no keyword block.** `borrowing`/`consuming` are annotations
  in parameter position; there is no `&T` type and no reference value in the language.
- **Runtime-transparent.** Both modifiers are erased after type-checking; the emitted call is
  identical to today (§9). No sequencing or effect change.
- Parser: two contextual keywords in a parameter position; additive, no existing syntax changes
  meaning.

## 9. Type-system impact — and the erasure guarantee

- **`linear_check` gains a borrow-vs-consume distinction on uses**, not a new binding state: a
  `borrowing`-argument use is recorded as a non-consuming read; a `consuming`-argument use (or an
  unmodified linear-argument use) is the consume. Leak, reuse, and branch-convergence checks are
  otherwise unchanged. Callee-side: verify a `borrowing` param is neither consumed nor returned nor
  captured (the §7 rule).
- **Erasure — closes the classification-completeness bug class.** The modifiers live **only** in
  parameter metadata consumed by `linear_check`; they are **stripped before lowering** and never
  reach `ast_to_ir` → `ir_rooting`. This matters because `docs/linear-ir-m5-feasibility-2026-08-07.md`
  found the historical GC-UAF class is *classification-completeness* (a lowering pass meeting a shape
  it doesn't handle). A borrow modifier that never reaches lowering cannot introduce that failure
  mode: `IRType`/rooting see the same `TcpConnection` handle they see today. One assertion in the
  lowering entry (no `borrowing`/`consuming` survives into the AST-to-IR input) makes this a checked
  invariant, not a hope. No `Scheme`/quantification interaction (rank-1 preserved).

## 10. Error-message impact

M4-style diagnostics:
- Consuming a borrowed param inside its function → *"'conn' is a borrowing parameter and cannot be
  consumed; take it `consuming` if this function should release it."*
- Returning / capturing a borrowing param → *"borrowing parameter 'conn' cannot escape this function
  (returned or captured by a closure)."*
- Capturing a borrow into a lambda at a call site (the §7 v0 limit) → *"a borrowed value cannot be
  captured by a closure yet; use a direct call or recursion (combinator support tracked with M4.4)."*
- The existing leak message is unchanged and **still fires** if `close` is omitted — the whole point.

## 11. Compatibility / migration

- **Additive.** No existing program changes meaning; an unmodified linear param stays consuming.
- **Opt-in per resource.** `TcpConnection` becomes `type linear` *and* `read`/`write`/… gain
  `borrowing`, `close` gains `consuming`. Breaking only for direct callers of those ops, who add no
  block — the call sites read the same; they just now type-check the release. `net.sprout` has few
  in-repo callers (`http_client`, echo examples); migrate one resource at a time.
- **`http_server` stays blocked on M4.4**, not borrowing: its `accept → task_spawn(\_ ->
  handle(conn))` captures the connection into an escaping task closure (§7). Borrowing unblocks the
  **client/session** shape first; the concurrent server waits on the escaping-closure work. Say so.

## 12. Tests

- **Positive (run):** straight-line `connect; write; read; close`; recursive `drain(conn); close`;
  `borrow`-only helper that passes `conn` onward to another `borrowing` op.
- **Negative (`type_error`):** consume a `borrowing` param (`close` inside a `borrowing`-param fn);
  return a `borrowing` param; capture a borrow in a lambda (§7 limit); omit `close` (leak still
  fires); pass a `borrowing`-only value where a `consuming` op is required and vice-versa. Each
  verified RED on the pre-change tree.
- **Parser:** `borrowing`/`consuming` parse in a parameter position; rejected on a non-linear param.
- **Migration:** `net.sprout` + `http_client`/echo examples compile and run;
  `just compile-examples-stage1`.

## 13. Spec / docs

- `docs/spec-v0.md` §5.8 (Linear types, Experimental): add a **Borrowing** subsection — the
  `borrowing`/`consuming` modifiers, the callee-side no-escape rule, caller-side borrow-≠-consume,
  and the §7 no-capture-yet limit; mark Experimental.
- `docs/idiomatic-sprout.md`: the socket-session idiom (`borrowing` ops, `consuming` `close`).
- Update `docs/linear-task-v0.md` §8 and the arc's `BACKLOG.md` entry to point here once approved;
  file the combinator-over-borrow follow-up under the M4.4 escaping-closure item.

## 14. Primary sources

- **Rust** references: <https://doc.rust-lang.org/reference/types/pointer.html>
- **Austral** borrowing (`&x`/`&!x`, `&[T,R]`, `borrow … as … in … end borrow`, region = lexical
  tag, borrowed value "unusable"): <https://austral-lang.org/tutorial/borrowing>,
  <https://austral-lang.org/spec/spec.html>
- **Swift** parameter ownership modifiers (`borrowing`/`consuming`, "cannot be escaped or stored"):
  <https://github.com/swiftlang/swift-evolution/blob/main/proposals/0377-parameter-ownership-modifiers.md>;
  noncopyable types (`~Copyable`, "unique ownership … never … copied"):
  <https://github.com/swiftlang/swift-evolution/blob/main/proposals/0390-noncopyable-structs-and-enums.md>
- **Linear Haskell** (`%1 ->`/`⊸`, "consumed exactly once", no borrowing):
  <https://downloads.haskell.org/ghc/latest/docs/users_guide/exts/linear_types.html>

## 15. Recommendation

The choice is a genuine scope call, and it is the user's. The hard facts fix only part of it:
**C is out** (rank-1 HM cannot express its rank-2 region), and **B is dominated** — it pays for a
`&T` type and a `borrow` block yet still defers the escaping-closure case exactly as D does, so it
buys nothing over D on the hard part (§7) and costs more surface. That leaves a real decision between
**A and D**, on engineering scope, not soundness:

- **A (region system)** — larger build, but it *decides* the escaping-closure case and enables
  first-class borrows. Right if the project wants the combinator-over-borrow form working properly up
  front, or foresees storable borrows.
- **D (parameter modifiers)** — checker-only, runtime-erased, turns `TcpConnection`'s unenforced
  `close` into a compile error via two keywords, and **does not foreclose A** (regions can be added
  later over the same `type linear` foundation). Right if the goal is the smallest sound step that
  enforces the socket-release discipline now, accepting that combinator-over-borrow waits.

**My recommendation is D as the first step** — smallest change that hits the immediate goal, non-
foreclosing of A — with A as the deliberate escalation if/when the deferred §7 case (or first-class
borrows) becomes the priority. But that recommendation weights "minimal now" over "solve it all up
front," and that weighting is a call for the user to confirm or overturn. If A is the target, the note
above already establishes it is rank-1-feasible (§5, §6) — it is a scope decision, not a blocked one.

Whichever is chosen, scope v0 to **`TcpConnection` first**, **borrow-vs-consume only** (defer the
`&`/`&mut` split), with `TcpListener` following the same shape and **`Scope` gated on M4.4** (§2).

## 16. What implementation changed (added 2026-08-07, post-landing)

The design above was written before the code existed. Five things turned out differently; the
sections above are left as written, and this section is authoritative where they conflict.

**1. §9 understated the checker work: borrowing forces ORDERING, not just a second category.**
M4.2's analysis was an order-*insensitive* set analysis — `seq2` only checked that sibling
consumed-sets were disjoint. Adding a non-consuming use opens a hole a set-only check cannot see:

```
close(conn)           # records one consume
write(conn, payload)  # records NO consume -> sets stay disjoint -> accepted
```

That is a write to a closed socket. `LinRes` therefore carries **two** sets (consumed, borrowed)
and sequential composition rejects a borrow that *follows* a consume. This is sound rather than
approximate because `docs/spec-v0.md` §6 already fixes evaluation order left-to-right for
application arguments, binary operands and constructor/tuple fields, and the walk folds in that
same order. Fixture: `tests/conformance/type_error/borrow_after_consume`.

**2. The field-read borrow is keyed on the BINDING's mode, not on `TGetField` syntax.** The
obvious rule — "a field read borrows" — breaks accepted code: `fn get_x(p: Pos) = p.x` is a
passing positive test (`tests/stdlib/test_linear_type_decl.spr`) precisely because the field read
*is* the consume (spec §5.8), and an owned linear record has no other way to be consumed. Relaxing
the leak rule to compensate would destroy the socket case, which is the whole feature. The shipped
rule instead splits by **position**: reading positions (field-access base, `match` scrutinee)
borrow *when the binding is a `borrowing` parameter* and still consume otherwise. Consequences:
`p.x + p.y` is legal for `p: borrowing Pos`, unchanged (a reuse) for an owned `p`, and
`BACKLOG.md`'s linear-record-ergonomics item is only *partly* closed — the owned case still needs
a `RecordPattern`.

**3. A soundness hole this note did not consider: borrowed CONTENTS.** Destructuring a borrowed
value was binding its linear fields as *owned*, so
`fn steal(w: borrowing Wrap) = match w with | Wrap f -> release(f)` consumed the inner resource
while the caller still owned the `Wrap` and would release it again — a double consume laundered
through a pattern match. Reads through a borrow are now borrows all the way down. Fixture:
`tests/conformance/type_error/borrow_field_of_borrow`.

**4. §11's migration claims were wrong, and the real blocker was elsewhere.**
`stdlib/http_client.sprout` is **not** a `stdlib.net` caller — it calls a C builtin
`http_request`. The `TcpConnection` ADT's only in-repo users were `tests/task_io_smoke/*`;
`http_server.sprout` and `examples/tcp_echo_once.sprout` use the raw `Int` handles and were
untouched by the migration. Four of the five smoke tests turned out to be **leaking their
connections outright** — the migration's first act was for the compiler to report that.

More seriously, `bundler.process_line` had a branch for `"export type alias "` but none for
`"export type linear "`, so `export type linear Foo` fell into the plain `"export type "` branch
and read the marker word **`linear`** as the type's name. Every module with a linear type has
therefore been exporting a phantom type called `linear` and **never exporting the real one** since
M4.1 — which meant no annotation naming it could be qualified (`Type mismatch: mod.Foo vs Foo`).
Because a modifier *requires* an annotation, this made `fn f(c: borrowing TcpConnection)`
unwritable in any module but the defining one; borrowing would have shipped unusable across module
boundaries. Fixed here; regression: `tests/stdlib/test_linear_cross_module.spr`
`consume_annotated`. `BACKLOG.md`'s entry for this claimed it was "not linearity-specific" — it
is: a non-linear imported ADT annotation always worked.

**5. §9's erasure wording is stronger than what is enforced.** The modifiers are *not* "stripped
before lowering" — they stay on `ast.Param` and lowering simply never reads them
(`ast_to_ir.ast_params_to_irtype_pairs` destructures `ast.Param _ ann _`). An assertion that no
modifier survives into `ast_to_ir` would fail. The testable form of the guarantee is
byte-identical IR, pinned by `tests/stdlib/compiler/test_borrow_erasure.spr`: `consuming` and
unmodified mean the same thing to the checker, so those two sources differ by one token and must
emit identical IR. `just ir-golden-diff` is the standing whole-corpus version of the same guard.

**Also worth recording:** a `consuming` function must genuinely *destructure* the value. `close`
was first written as `tcp_close(tcp_connection_handle(conn))`, but that accessor is `borrowing`,
so `conn` was only borrowed and the checker correctly reported that `close` never disposed of what
it was handed. `match conn with | TcpConnection handle -> …` is the consuming form.

And `stdlib.net.tcp_connect` is now **exported**, completing the raw-handle family alongside
`tcp_listen`/`tcp_accept`/`tcp_read_some`/`tcp_write`/`tcp_close`. Code whose shape borrowing cannot
express must still be able to speak TCP, and gets no release enforcement in exchange.

*(Updated: the family's read is `tcp_read_some`. `tcp_read` returned a String built from unvalidated
socket bytes and is gone; see `stdlib/net.sprout`. `http_server.sprout` and `concurrent_read.spr`
have both since moved to the linear API, so what keeps the raw family alive is narrower than it was:
a task force-dropped by `scope_cancel`/`with_timeout` never runs its linear `close`, so the
cancellation fixtures cannot hold a `TcpConnection` at all.)*

## 17. Post-merge review: what M4.5 shipped broken (2026-08-07)

A high-effort adversarial review of the merged change found **ten** defects, six of them
soundness holes that let a linear value be consumed twice or leaked with no diagnostic. All are
fixed in the follow-up; each has a regression fixture. Recording them because the *pattern* is more
instructive than any individual bug.

**The dominant root cause: the mode lives in a name-keyed side table, not in the type.**
`@parammode:<name>` is only consultable when the callee is a literal top-level name. Every other
callee shape loses the mode and silently reads as *consuming*, which **discharges the caller's
obligation while the real callee only borrows** — so the resource leaks and nothing is reported.
Two reachable instances:

- a `borrowing` function passed as a first-class value (`apply(peek, f)`);
- a `borrowing` parameter on an **instance method**, since a call dispatches through the class
  signature, which carries no modifier.

Both are now rejected outright, which is the honest v0 answer: the real fix is to put the mode in
the function type, and that is a type-system change (it must survive unification, generalization
and the iface codec). Note this class was *unreachable before M4.5* — pre-borrowing, every
`(File) -> Int` had to consume its argument.

**Second cause: `LinScope` appended instead of shadowing.** A non-linear binder shadowing a linear
one left the stale entry, so a use of the *shadow* was credited as a consume of the shadowed
resource: `fn shadow(c: File) -> Int = do { let c = 5; c + 1 }` type-checked with `c` never
released. Fixed by making every binder introduction shadow — do-`let`, pattern binders (all of
them, not only the linear ones) and lambda parameters.

**Third: the field-access half of "borrowed contents" was never closed.** M4.5 found this hole
during implementation, wrote `borrow_field_of_borrow.spr` for the `match` form, fixed that path —
and left the field form open, because the fixture used `match`. `release(w.inner)` on a
`borrowing Wrap` was a double consume and `fn take(w: borrowing Wrap) -> File = w.inner` laundered
an owned value out of a borrow. A fix verified only by the test that motivated it is not verified.

**Fourth: do-blocks were assumed unconditional.** A `Maybe`/`Result` block short-circuits on the
first failing `<-` (§11), so a trailing `close` counted as executed does not run on the error
path. This is exactly the session shape M4.5 makes idiomatic. The block's own type discriminates:
`Result … !{IO}` short-circuits, `Unit !{IO}` does not.

**Fifth, and the cheapest lesson: `mask_is_borrow` used `str_slice(mask, k, k + 1)`.** str_slice's
third argument is a **length**, not an end index, so it read one character only when `k` was the
first or last parameter; every middle `borrowing` position degraded to consuming. Every borrowing
parameter in `stdlib/net.sprout` sits at index 0, so the entire M4.5 suite passed over a broken
mask reader — **the tests were shaped by the implementation rather than by the spec.**

Also fixed: `extern fn` modifiers were never validated (the guard only runs over function
*bodies*, and an extern has none); a module-level linear `let` at a borrowing position was
rejected with "must be a variable reference", which contradicted the source; and the
type-variable rejection reused the non-linear-parameter message, telling authors to make `a`
linear when that is not expressible.

**One review finding did not reproduce.** A local shadowing a `borrowing` function was reported as
inheriting its `@parammode:` mask. It does not: the bundler qualifies top-level names
(`main.peek`), so a local can never collide — the claimed discriminator behaves identically either
way. The latent case is a local shadowing a *prelude* borrowing function, since the prelude owns
unqualified names; there are none today, and the shadowing fix above closes it regardless.

## 18. M4.6 — parameter ownership moved into the function type (2026-08-08)

§17 named the dominant root cause of M4.5's ten defects: the mode lived in an env sentinel keyed
by declaration name (`@parammode:<name>`), consultable only when the callee was a literal
top-level name. Everywhere else `callee_mask` returned `""` and every argument read as
**consuming** — which discharges the caller's obligation via a call that only borrows, leaking the
resource with no diagnostic. M4.5's answer was to *reject* the two shapes where the mode was lost.
M4.6 removes the side table instead.

**Representation.** `types.TFunc` gains a fourth field, `types.Ownership` (`OwnConsume` |
`OwnBorrow`), alongside the effect row it already carried — the same kind of annotation on the
arrow, and since `TFunc` is curried, one tag per node is exactly one tag per parameter. The tag is
**two-valued** though `ast.ParamMode` has three: at the type level an unmodified parameter and a
`consuming` one are the same thing, and making them distinct types would mean `consuming File ->
Int` failed to unify with `File -> Int`.

**Producers.** Every declaration form — top-level `fn`, `extern fn`, class method, instance method
— reaches `TFunc` through `infer.scheme_from_fn_parts_inner`, so one new builder
(`build_fn_type_modes`) puts `borrowing` into all four. Imported signatures get it from
`iface_codec.params_to_func_type` and from the wire tag; annotated arrow types are `OwnConsume`.

**Why call sites copy rather than default.** The arrow synthesized from the argument types at a
call (`infer.build_fn_type_like`) takes its ownership from the *callee's own spine*. A fixed
`OwnConsume` there would make every call to a borrowing function a mismatch under invariant
unification — the feature's own tests would fail. Where the callee type is still a type variable
there is nothing to copy and `OwnConsume` is the conservative reading: the tyvar binds to a
consuming arrow, so a caller later supplying a borrowing function gets a mismatch at *its* call
site rather than a silent leak.

**Invariance, not subtyping.** `unifier.unify_applied` compares the tags and rejects a mismatch.
Both directions are unsound (double consume one way, leak the other), matching Swift SE-0377's
rule that a noncopyable parameter's convention "must match exactly". Effects are still ignored
there; ownership is not, because an ownership mismatch is a soundness bug rather than a
conservatism knob. **This is the property worth having**: every type flow already goes through
`unify`, so coverage is structural instead of a list of sites someone has to remember — and
under-coverage of an enumerated list is exactly what produced §17.

**The alignment hazard, and how it was verified.** `infer.maybe_rewrite_class_method_call`
prepends a `TDict` evidence node per constraint to a constrained call's argument list, while the
callee's type spine has no dictionary parameter (those arrive later, in
`lowering.append_hidden_param_types`). Zipping args against the spine naively shifts every mode by
the number of dictionaries. The fix drops the leading `TDict` run. It was verified by *removing*
it and confirming the new class-method test fails ("linear value 'f' is used more than once") —
§17's lesson was that a fix verified only by the test that motivated it is not verified, so here
the test was checked to actually motivate the fix.

**What lifted, and what did not.**

- A `borrowing` function may now be bound and called as a value (`let g = peek`).
- Class and instance methods may carry modifiers, with the instance required to **match** the
  class — compared as ownership, so `consuming` against an unmodified class parameter agrees.
- **Not lifted: `borrowing` in arrow-type syntax.** `fn apply(g: (File) -> Int, f: File) = g(f)`
  typechecks today, so this gap is real — an earlier draft of the plan wrongly claimed M4.4 blocks
  its only users; M4.4 blocks *lambdas*, not function-typed parameters. Deferred for cost: it needs
  a parser change (and so the 2-step bootstrap), a mode field on `ast.TypeExpr`'s arrow, formatter
  and codec work. Purely additive afterwards — it reuses this tag. The mismatch diagnostic says
  explicitly that an arrow type cannot yet be written with `borrowing`.
- **Not lifted: a modifier on a type-variable parameter.** Not a representation limit (ownership
  survives instantiation) but a universe one: without a linearity bound on `a`, `borrowing Int`
  would be an error while `borrowing a` at `Int` silently was not. That is polymorphism over linear
  types. Prior art bounds the parameter first — Swift SE-0427 (`<T: ~Copyable>` to opt out of the
  default `Copyable`), Austral (every type parameter annotated `Free`/`Linear`/`Type`).

**Interface format.** `IfaceFile` v4 → v5. A v4 iface decoded leniently would read every borrowing
parameter as consuming — the very erasure this milestone removes — so the version gate rejects it,
and `decode_tfunc_at` rejects a tagless `TFunc` independently.

**Erasure held.** `just ir-golden-diff` across 58 files: **additions only, zero changed or removed
lines**. The two added `define` blocks are the `ast.mode_is_borrowing` / `ast.param_mode_of`
helpers, and they appear only in the one golden program that bundles the compiler itself — at the
time `examples/repl_hosted.sprout`, since replaced by
`tests/smoke_shapes/11_compiler_bundle.spr`. No pre-existing function's body moved by an
instruction.

---

## 19. Superseded: the spawn-a-handler shape (M4.4a, 2026-08-08)

Several sections above (§2, §7, §13, and the `net.sprout` note in §14) record that handing a socket
to a *spawned* task is unreachable, and file it under M4.4. **That half is no longer true**, and the
sections are left as written because they are an accurate record of the M4.5 decision, not because
they still describe the language.

`docs/one-shot-closures-v0.md` (M4.4a) added the `once` parameter modifier: a callee that promises
to invoke a closure at most once licenses the caller to **move** linear captures into it. That is
exactly the accept → `task_spawn` handoff, so `stdlib/http_server.sprout` and
`tests/task_io_smoke/concurrent_read.spr` now run on the linear `TcpConnection`/`TcpListener`
throughout.

What §7's reasoning still holds for, unchanged: a **borrowed** value may not be captured by any
closure, `once` or not. Whether that is sound turns on whether the closure outlives the consume, and
Sprout still has no escaping/non-escaping distinction — `with_scope` joins *after* its body, so a
spawned closure really can run against a value the body already released. Rust draws the same line
(`thread::spawn`'s `'static` bound). Linear lambda *parameters* are likewise still rejected.
