# Ranges v0 — a backwards range is empty

Status: **design for approval**. Do not implement before sign-off (AGENTS.md Collaboration Rule 5).
Date: 2026-08-19.
Scope: `stdlib/prelude.sprout`, one new diagnostic, docs. No runtime change, no new builtin, under
the recommended package.

**This is a design reversal, not a bug fix.** `docs/int-ranges-v1-draft.md` §2 goal 4 ("Support both
ascending and descending ranges without extra syntax") and §6 rule 3 ("If `lo > hi`, the range
enumerates downward") specify the behaviour this document removes. The draft's own §14 Open
Question 2 asks whether descending should instead "be rejected and deferred to a separate design".
This document answers that question: **defer**.

---

## 1. Problem statement

`range(a, b)` with `b < a` counts downward instead of being empty. Reported from the
uncharted-suns project 2026-08-18 and reproduced.

The mechanism is a single inference. `stdlib/prelude.sprout:76`:

```sprout
export fn range_step(value: IntRange) -> Int =
  if range_start(value) <= range_end(value) then 1 else -1
```

Direction is *fabricated from bound order*, so `range(0, -1)` becomes a two-element descending
range. `range_count` (`:85`) agrees, returning `|start - end| + 1`.

The deeper defect is that **an empty range is not representable at all**. All three walkers
terminate on `current == end_value` (`:91`, `:98`, `:111`), so every one of them emits at least one
element or applies its step at least once. `range_fold` cannot return `init`. Emptiness having no
encoding is *why* the backwards case had to become something, and descending was the something
available.

### Why this matters more than an ergonomic wrinkle

`IntRange` is inclusive at both ends, so the ordinary spelling of "walk n rows" is
`range_fold(f, acc, range(0, n - 1))` — which is wrong at `n == 0`. Two independent in-tree
workarounds already exist for exactly this:

- `bench/dispatch/dispatch_bench.sprout:35` — `fn upto(n: Int) -> IntRange = range(0, n - 1)`
- `examples/digit_recognizer/recognizer.sprout:44` — the same definition, whose comment at `:43`
  already *asserts* that "`upto(0)` is the empty range". In-tree code believes the fixed semantics
  today.

`README.md:205-209` documents a caller-side guard as the recommended workaround.

The failure mode is worse than "crashes at `n == 0`". Two shapes:

- A **bounds-checked read** fails loudly — `mutvec_at` routes through `vector_get_direct`, which
  "fails loudly out of range" (`stdlib/mutable.sprout:85-90`). Recoverable to diagnose.
- A **pure fold** returns a silently wrong answer. `BACKLOG.md:2448`'s measured repro:
  `range_fold` over `range(0, 0 - 1)` printed `visit 0` / `visit -1` and returned `count=2`.

The silent case is the hazard, and it is the one an author never writes a test for.

---

## 2. Goals and non-goals

**Goals.**

1. `range(a, b)` with `b < a` is empty: zero elements, `range_count == 0`, `range_fold` returns
   `init`, `range_each` is a no-op.
2. Make emptiness *representable* — restructure the three walkers so `init` can be returned.
3. Diagnose the one case that is unambiguously a mistake: a **literal** reversed range.
4. Leave every accessor that is passed as a first-class function intact.
5. Keep the change free of runtime edits, new builtins, and representation churn.

**Non-goals (hard fence).**

1. **No descending range value in this change.** Deferred with a documented path — §4, Package B.
2. **No arbitrary step** (`range_by`). See §4 for the concrete arithmetic reason, not taste.
3. **No half-open constructor.** The fix makes `range(0, n - 1)` correct at `n == 0`, which demotes
   `BACKLOG.md:1804` (B5) from a correctness requirement to optional sugar. Separate decision.
4. **No representation change.** Two fields suffice to encode empty; see §4 and Appendix B.
5. No descending `..` literal syntax.

---

## 3. Prior-art survey

Every row verified 2026-08-18/19 by **executing the toolchain locally**, except Kotlin, which is
quoted from the language reference. No row is claimed from recall.

### 3a. What a backwards range does

| language | `0 … -1` / `5..1` | descending spelled as | verified by |
|---|---|---|---|
| Python 3.12 | `[]` | `range(0,-1,-1)`, `reversed()` | executed |
| Haskell GHC 9.x | `[]` | `[5,4..1]` | executed |
| Rust 1.x | empty (`..` and `..=`) | `.rev()` | executed |
| Ruby 3.1 | `[]`, `.size == 0` | `5.downto(1)` | executed |
| Scala 3.7 | `List()` | `5 to 1 by -1`, `.reverse` | executed |
| Kotlin | empty | `downTo` | `ClosedRange.isEmpty`: *"The range is empty if its start value is greater than the end value."* |
| Swift 6.2 | **runtime trap**: *"Range requires lowerBound <= upperBound"* | `stride(from:through:by:)`, `.reversed()` | executed |

**Seven for seven, no language silently counts downward.** Six make it empty; Swift traps. Of the
six with `..`-style syntax, five make `5..1` empty and none descends.

**Swift's trap is not a live option for Sprout, for two independent reasons.**

First, project doctrine forbids it. `docs/guidelines.md:39` makes "Total over partial" a *hard
mandate* — "the stdlib must not export a partial function" — and `:51` rules out panic for exactly
this shape: "`panic` is **not** for input the caller could plausibly supply." Sprout does panic on
div-by-zero (`spec-v0.md:1327`) and on a negative shift count (`:1633`), but in both cases there is
no total answer to give. An empty interval is mathematically well-defined: `[5,1] = ∅` **is** the
total answer. `examples/aoc_2025_day_5.sprout:24-27` builds ranges straight from parsed input, so a
trap would turn one malformed input line into a crash — precisely the prohibited shape.

Second, and decisively, **a trap does not fix the motivating bug.** The defect is
`range_fold(f, acc, range(0, n - 1))` at `n == 0`. Trapping converts a silent wrong answer into a
loud crash but *still fails the `n == 0` walk*, which is the case this design exists to make work.
Only "empty" makes the ordinary spelling correct.

### 3b. What languages do about the surprise — and Sprout's own precedent

**Sprout has already decided this question in another corner of the language.**
`docs/spec-v0.md:1632-1633`, normative:

> A **negative** count is an error: it panics at run time, and a negative *literal* count is
> rejected at compile time.

And the implementation states the rationale in the codebase's own words
(`stdlib/compiler/ast_to_ir.sprout:4915-4918`):

> A literal NEGATIVE count is rejected outright: the dynamic case can only panic at run time, but a
> statically known one is **a program that cannot be right**, and Go and Zig reject constant
> out-of-range shifts at compile time for the same reason.

That is exactly the split this design needs, already established: **the computed case gets the
total runtime rule; the statically-known-wrong case is rejected at compile time.** The reversed
literal range is therefore not a new idea being imported — it is an existing Sprout convention being
applied consistently. (The runtime halves differ — a bad shift panics, a backwards range is empty —
but the diagnostic structure is identical.)

External corroboration: only one surveyed language ships such a diagnostic. Rust's clippy has
`reversed_empty_ranges` **`deny`-by-default** — an error, not a warning, with the fix spelled out.
Executed against `(5..1).collect()`:

```
error: this range is empty so it will yield no values
 --> rev.rs:2:23
  |
2 |     let v: Vec<i32> = (5..1).collect();
  |                       ^^^^^^
  = note: `#[deny(clippy::reversed_empty_ranges)]` on by default
help: consider using the following if you are attempting to iterate over this range in reverse
  |
2 |     let v: Vec<i32> = (1..5).rev().collect();
```

The split this encodes is the one that resolves the "empty is surprising" objection:

- a **literal** `5..1` is essentially always a typo → diagnose it at compile time;
- a **computed** `a..b` with `b < a` is legitimately empty → stay silent, stay total.

### 3c. Naming — the marked/unmarked question

Every opposite-meaning pair in the Sprout tree marks **both** sides:

| pair | location |
|---|---|
| `starts_with` / `ends_with` | `stdlib/string.sprout:221,226` |
| `trim_left` / `trim_right` | `stdlib/string.sprout:270,274` |
| `strip_prefix` / `strip_suffix` | `stdlib/string.sprout:104,111` |
| `vec_prepend` / `vec_append` | `stdlib/prelude.sprout:190,196` |

There is no marked/unmarked pair anywhere in the prelude. So *if* a descending constructor is ever
added, `range` + `range_down` would violate house convention and the pair must be
`range_up` / `range_down` with bare `range` retired. This is why descending is deferred rather than
added asymmetrically — see §4.

Note also that every surveyed language marks **only** the descending side (`..` vs `downTo`,
default step vs explicit `-1`, `..` vs `.rev()`). Ascending-unmarked is the external convention and
marked-both-sides is the internal one; they conflict, and that conflict is a reason to decide
descending on its own rather than as a rider on a bug fix.

---

## 4. Implementation overview — the approval gate

Two coherent packages. **Package A is recommended.**

### Package A (recommended) — semantics + diagnostic, prelude-only

`IntRange` stays a two-field C heap block. This is sufficient because two fields can encode
*empty* **or** *descending* — just not both — and we are choosing empty. No representation change
is needed for the fix.

| change | file |
|---|---|
| `range_step` → always `+1`; demote from `export` to private | `prelude.sprout:76` |
| `range_count` → `max(0, end - start + 1)` | `prelude.sprout:85` |
| `range_contains` → `target >= start && target <= end` | `prelude.sprout:79` |
| three walkers → `current > end_value` base case, so empty is representable | `prelude.sprout:91,98,111` |
| reversed-literal diagnostic | see §7 |
| retire the two `upto` workarounds | `dispatch_bench.sprout:35`, `recognizer.sprout:44` |

`range` keeps its name. Nothing downstream is renamed. Accessors stay functions (§6).

### Package B — symmetry now, if you want it despite §4's recommendation

Requires direction in the value, which two fields cannot hold alongside emptiness. Everything in
Package A, plus:

- `IntRangeVal` gains `long long step` (`runtime/sprout_runtime.c:122`). The GC block size derives
  from `sizeof` (`:1081`) and the type has zero pointer slots (`:1876`), so tracing stays at zero
  traced children — no GC change.
- `int_range` gains a step parameter, or a new 3-arg extern joins it. Either way
  `runtime/APPROVED_BUILTINS:80-82` is amended and **explicit approval is required** per AGENTS.md
  "Builtin vs Stdlib" rules 4-6.
- **One new accessor builtin** `int_range_step`, peer to the existing `int_range_start`/`int_range_end`
  — also approval-gated.
- The hardcoded declare at `stdlib/compiler/ir_lowering.sprout:557`
  (`declare i64 @int_range(i64, i64)`) must match the new arity, and the `IntRangeExpr` lowering
  (`ast_to_ir.sprout:1069`) must pass `step = 1`.
- `range_up(a,b)` / `range_down(a,b)`; bare `range` retired (§3c); `range_step` stays public and
  becomes load-bearing again for the walkers.
- Every in-tree `range(` call site renamed (~12), plus a rename sweep in uncharted-suns.
- A prelude extern signature change forces a **full `just refresh-seed`**, not the `seed-fp-ack`
  bypass — see the AGENTS.md caveat on new prelude externs adding `declare` lines to the seed.

**This is a sketch, not a costed plan.** If it is chosen, it gets its own design section before
implementation; the list above is what is currently known to be required, not a guarantee of
completeness.

**Do not** implement Package B as a native Sprout record. Appendix B records why, with the
load-bearing fact that kills it.

### Why `range_by(a, b, step)` is not in either package

Not taste — arithmetic. `range_contains` and `range_count` with arbitrary step need modular
arithmetic to answer "is `target` on the step grid". Sprout has no `%` operator, and the hand-rolled
idiom is truncated division (`n - (n / m) * m`, as used at `bench/dispatch/dispatch_bench.sprout:37`),
which is **wrong for negative operands** — exactly the case a negative step introduces. Arbitrary-step
`contains` is an off-by-one factory until Sprout has a correct `mod`. File it; do not ship it.

---

## 5. Syntax and semantics impact

No spelling changes. `a..b` still parses to `ast.IntRangeExpr` (`stdlib/compiler/ast.sprout:118`)
and still means the inclusive interval. What changes is what a backwards one *means*.

Normative contracts, to be added to the spec (§10):

| expression | today | after |
|---|---|---|
| `range_count(range(5, 1))` | `5` | `0` |
| `range_contains(range(5, 1), 3)` | `true` | `false` |
| `range_to_list(range(5, 1))` | `[5,4,3,2,1]` | `Nil` |
| `range_fold(f, init, range(5, 1))` | folds 5 elements | `init` |
| `range_each(f, range(5, 1))` | applies `f` 5 times | no-op |
| `range_to_vec(range(5, 1))` | 5 elements | empty |
| `range_count(range(3, 3))` | `1` | `1` (unchanged — a single-element range) |
| `to_string(range(5, 1))` | `"IntRange(5, 1)"` | `"IntRange(5, 1)"` (bounds stored as given) |

Bounds are stored as written, so `to_string` is unaffected and
`tests/stdlib/test_to_string.spr:41-43` needs no update.

**One asymmetry to document loudly rather than paper over.** `..` exists only in the ascending
form, so under Package B `5..1` would be empty while `range_down(5, 1)` descends. Under Package A
there is no such split. A descending literal is a separate decision either way.

---

## 6. Type-system impact

**None.** No new types, no representation change, no inference change. `IntRange` stays a primitive
type name in the two hardcoded lists (`bundler.sprout:1316`, `infer.sprout:5034`) and
`infer_range` (`infer.sprout:4925`) is untouched.

**Accessors stay functions, and this is settled by evidence rather than preference.**
`examples/aoc_2025_day_5.sprout:83` passes `range_start` first-class to `vec_sort_by`. A field read
is not a value and Sprout has no field sections, so replacing accessors with documented field reads
would break working code. `docs/style-guide-v0.md:282` also names `range_start(r)` as an
exemplar of permitted data-first accessor ordering. See Appendix A for why a microbenchmark
initially suggested otherwise and why that reading was wrong.

---

## 7. Error-message impact

One new diagnostic, following the convention `spec-v0.md:1632` already states (§3b). Proposed text:

```
ERROR: check: range `5..1` is empty, so it yields no values
  the start bound 5 is greater than the end bound 1, and a range counts upward only
  to iterate downward, reverse an ascending range instead
```

### Scope — what it does and does not catch

It fires **only when both bounds are integer literals** and `hi < lo`. Purely syntactic; no type
information needed.

It deliberately does **not** fire on a computed range. `range(0, n - 1)` at `n == 0` stays silent and
yields empty — that is the whole point of §2 goal 1, and warning on it would make the fixed idiom
unusable.

**Both spellings must be covered.** `5..1` is an `ast.IntRangeExpr`, but `range(5, 1)` is an ordinary
call to a prelude function and is *not* that node. Catching only the syntax form would be a
half-diagnostic — and since most in-tree code uses `range(a, b)` rather than `a..b`, it would be the
less common half. The call form is recognised by canonical callee name, the same way
`recognize_string_builtin("str_concat")` already special-cases prelude functions. Both forms produce
the same message.

### Where it lives — the check phase, *not* where the precedent lives

The negative-literal-shift rejection is implemented as an `Err` from translation in
`stdlib/compiler/ast_to_ir.sprout:4924-4926`. **Do not copy that location**, for a reason worth
recording: `tests/conformance/` categories are keyed to *phases* — `type_error/` is `--phase check`
output, `parse_error/` is the parse phase, `executable_error/` is `validate_entrypoint`
(`tests/conformance/README.md:12-15`) — and **there is no category for an `ast_to_ir`-phase
rejection**. Consistently, the shift precedent has **no conformance fixture at all** (grepped: zero
hits for `bit_shl`/`shift count` under `tests/conformance/`). That is a coverage gap in the
precedent, not a pattern to reproduce.

The check phase gives the diagnostic a harness that is already gated in `ci-fast-gates`, plus better
source positions. So this design follows the *convention* the spec states while diverging from the
*location* the one existing instance chose.

Rejected alternative: `stdlib/compiler/lint_rules.sprout` — cheapest, and it already walks
`ast.IntRangeExpr` at `:126`, `:192`, `:433`, `:553` with `LintFinding` (`:15-19`) carrying
line/col/rule-id/message. **But lint is a separate `fmt_bin lint` subcommand (`justfile:80`), not
part of compilation**, so a finding would not block a build. A diagnostic that only fires when
someone runs `just lint` does not prevent the bug this document exists to prevent.

---

## 8. Compatibility and migration notes

**Breaking**: any caller relying on descending iteration. In-tree that is:

- `examples/int_range_demo.sprout:17,23` — `return_trip_crosses_loading_bay(6..2)` calls
  `range_contains(6..2, 4)`, which flips `true` → `false`. **The example's printed output changes
  from `124` to `24`.** No test catches this; only golden IR covers the file, and golden IR checks
  IR rather than output. This example exists to demonstrate the semantics being removed and must be
  rewritten, not merely re-snapshotted.
- Nothing else. No in-tree code *iterates* a descending range.

**uncharted-suns** is the language's primary consumer and the reporter of this defect. Under
Package A it needs no migration at all: `range` keeps its name and signature, and the only
behaviour change is the one it asked for. Under Package B every `range(` call site there must be
renamed to `range_up`.

`range_step` moving from `export` to private is source-breaking in principle. Grepped: zero callers
outside the prelude, in-tree.

**Golden IR**: all 60 files in `tests/golden/ir/` reference `int_range` — the prelude is bundled
into every program — so **any** prelude change rewrites all 60. This is a constant, not a cost of
this design. Per AGENTS.md DoD #12 the full `git diff tests/golden/ir` must be read before
regenerating; the `just ir-golden-diff` report is truncated to `head -40` per file and is useless at
this width.

**Bootstrap seed**: `stdlib/prelude.sprout` is bundled into `compile_driver`, so `just refresh-seed`
is required and the `seed-fp-ack` bypass does not apply.

---

## 9. Tests added and updated

Per Definition of Ready #2/#3, these are written and confirmed failing before implementation.

**New** — `tests/stdlib/test_range_empty.spr`:

1. `range_count(range(5, 1)) == 0`
2. `range_to_list(range(5, 1)) == Nil`
3. `range_fold(add, 7, range(5, 1)) == 7` — the accumulator is returned untouched
4. `range_each` over a backwards range applies `f` zero times (observable via a `MutVec` witness)
5. `range_contains(range(5, 1), 3) == false`
6. `range_to_vec(range(5, 1))` is empty
7. `range(0, n - 1)` at `n == 0` iterates zero times — **the motivating case**
8. Boundary preserved: `range(3, 3)` still has exactly one element
9. Boundary preserved: `range(1, 5)` still ascends over five elements
10. `to_string(range(5, 1)) == "IntRange(5, 1)"` — bounds stored as given

**New** — `tests/conformance/type_error/reversed_literal_range.spr` plus its `.err` file, per §7.
Two fixtures, since §7 requires both spellings to be caught: one for `5..1` and one for
`range(5, 1)`. A positive fixture under `tests/conformance/run/` must also confirm that a *computed*
backwards range is accepted silently and yields empty — otherwise nothing guards against the
diagnostic being widened to catch the case §2 goal 1 exists to make legal.

**Updated**:

- `examples/int_range_demo.sprout` — rewritten; it currently demonstrates removed semantics
- `bench/dispatch/dispatch_bench.sprout:35`, `examples/digit_recognizer/recognizer.sprout:44` —
  `upto` workarounds retired
- golden IR snapshots (all 60), after reading the full diff

---

## 10. Spec and docs updates

`docs/spec-v0.md` currently says **nothing** about range semantics — `IntRange` appears only at
`:641` as a primitive type name and `..` only at `:948` in passing. It must acquire a normative
section stating:

- `lo..hi : Int -> Int -> IntRange`; both operands evaluated left-to-right
- the inclusive interval `[lo, hi]` when `lo <= hi`
- **empty when `lo > hi`**
- bounds stored as given, so `to_string` reflects the operands
- the empty contracts pinned per §5's table
- the surface remains **experimental**, not stable v0

Also updated:

- `docs/int-ranges-v1-draft.md` — §2 goal 4 and §6 rules 2-3 amended; §14 Q2 marked resolved
  (*defer*); §14 Q4 (half-open) noted as still open and unaffected
- `README.md:205-209` — the `n == 0` guard is obsolete; replace with the fixed semantics
- `BACKLOG.md:2448` closed; `:1804` (B5 half-open helper) annotated as demoted to sugar

---

## Appendix A — measured performance, and a wrong turn worth recording

Benchmarked on macOS arm64, clang `-O2`, two runs, order-reversed to cancel drift. 20M probe
iterations, 5M construction iterations.

| operation | builtin `IntRange` | Sprout record | delta |
|---|---|---|---|
| two field reads | 334ms / 342ms | 27.2ms / 28.3ms | record ~12x faster |
| construct + one read | 87.9ms / 97.2ms | 91.2ms / 102ms | record ~4% slower |
| `range_fold` over 5M | 27.3ms / 29.9ms | unaffected | neutral |

The 12x is **GC-rooting elision, not the heap-kind check**. `ir_rooting.sprout:89` treats every
`IRCall` as possibly allocating, so a live handle is pushed and popped as a GC root around each
accessor call; `IRGetField` is not a trigger at all.

**Why this did not change the API.** Iteration is unaffected because `range_fold` reads the
accessors *once per loop* (`prelude.sprout:109`) and then recurses on plain `Int`s. The only
beneficiaries would be the bodies of `range_contains` and `range_count`. Reshaping the public
accessor surface to chase a 12x figure that iteration never sees would have broken
`aoc_2025_day_5.sprout:84` (§6) for no measurable gain in real code.

**Rejected as unsound:** adding `int_range_start`/`int_range_end` to `is_nonallocating_read`
(`ir_rooting.sprout:45`) looked like a free win — both are provably non-allocating. But that
allow-list is keyed on **C symbol names**, and its invariant (`:39-44`) holds only because its three
entries are C symbols. A bare `.spr` file emits top-level Sprout functions *unqualified*, so a
Sprout `range_start` can collide with the allow-listed name and have its rooting elided while
genuinely allocating — a latent use-after-free. The C-extern-only version of this win remains
available and is unrelated to this design.

## Appendix B — why `IntRange` is not becoming a native Sprout record

Considered and rejected. It was attractive because it would have made `step` an ordinary field, so
symmetric constructors would need no new builtin and no approval.

**The load-bearing assumption is false.** Records are never registered with the runtime:
`ast_to_ir.sprout:551-558` passes `regs` through unchanged, commenting *"Not added to regs — GC
tracing is header-driven (arity in the object header), so no runtime ctor registration is needed."*
`find_ctor_tag_by_name` (`runtime/sprout_runtime.c:2678-2692`) therefore ends in
`tcp_fail("constructor metadata not registered")`. **C cannot construct a record by name.**

That is survivable — `stdlib/regex.sprout:32` is the only consumer of `regex_find_range` and
converts to a `Match` ADT immediately, so the three externs could move under an opaque carrier name
with zero C change. But three further costs are not:

1. **GC tracing gets worse.** `SPROUT_HEAP_RANGE` traces **zero** children
   (`sprout_runtime.c:1934`). An arity-3 record reports 3, so three `Int`s are pointer-tested on
   every mark.
2. **Ctor-tag renumbering.** A prelude record consumes a tag (`ast_to_ir.sprout:553`,
   `next_tag + 1`) and the prelude bundles first, so *every subsequent tag in every program* shifts
   by one — visible in every `sprout_register_ctor` and `sprout_alloc_obj`. All 60 goldens change
   anyway (§8), but this makes the diff far wider and harder to read, defeating the read-before-
   regenerating rule.
3. **Bare-file regression.** `a..b` works with no prelude *today* because
   `declare i64 @int_range(i64, i64)` is hardcoded at `ir_lowering.sprout:557`. As a record literal
   it would need a ctor-table entry that a preludeless file has no way to get.

**Conclusion:** if symmetry is wanted, the `long long step` field in `IntRangeVal` (Package B) is
strictly better than the record — it keeps GC tracing at zero children, renumbers no tags, and does
not regress bare files. Its only cost is one accessor builtin needing approval.

---

## Appendix C — adjacent findings

Turned up while surveying; none is in scope here. **All five are filed under `BACKLOG.md`
§"Compiler / Stdlib Misc"** with the detail and reproduction steps; the summaries below exist so a
reviewer of this document does not have to cross-reference to see what was noticed and set aside.

1. **`examples/digit_recognizer/recognizer.sprout:256`** uses `range(0, total_epochs)`, which is
   inclusive and therefore runs `total_epochs + 1` epochs — inconsistent with the `upto(n)`
   convention the same file uses at `:90, 98, 175, 176, 185, 216, 217`. Likely an off-by-one.
2. **`lo..hi` unspaced does not compile**: `ERROR: check: Cannot infer the record type of '.hi'`,
   because a dot inside an identifier run is absorbed by the lexer. `lo .. hi` and `0..n` both work.
   `docs/int-ranges-v1-draft.md` §7 uses the broken spelling in its own example.
3. **`regex_find_range` puts a half-open span in an inclusive-range type.** POSIX `rm_eo` is
   exclusive, and `stdlib/regex.sprout:53-55` slices the suffix starting *at* `end` — treating it as
   exclusive, contrary to every other `IntRange` operation. Currently dormant: `match_from_range`
   (`:32`) projects both fields into a `Match` ADT before anything could misread them. It becomes
   actively misleading once `range_count` means "elements in a closed interval".
4. **The negative-literal shift rejection has no conformance fixture**, because no conformance
   category covers an `ast_to_ir`-phase rejection. This is the gap §7 declines to reproduce.
5. **`range_to_vec`** (`prelude.sprout:203`) is O(n²) via copying `vec_append`, already noted at
   `tests/stdlib/test_vec_sort_stacksafe.spr:10`. Worth a doc note.

Deliberately **not** filed: there is no `range_map`, and none should be added until a caller asks
for one — `range_to_list |> list_map` covers it, and the repo's convention is to add on demand
rather than for symmetry. Recorded here so a future reader does not read its absence as an oversight.
