# GC phase-2 retro — actionable findings (2026-07-05)

Retro question: looking at the code written for the GC region rewrite
(PRs #125 + #136), what language/tooling features would have made it
substantially better? Four actionable findings, ordered by
effort-to-value. Each is independent; none blocks current work.

## 1. C exhaustiveness for heap-kind dispatch (small, mechanical — do first)

**Problem.** Every `switch (kind)` in `runtime/sprout_runtime.c`
(`slot_bytes`, `sprout_heap_child_count/child_value`, the sweep free
path, `print_inline_value`) is a hand-rolled, non-exhaustive pattern
match. Adding an eleventh heap kind means finding every dispatch by
grep; a missed one dispatches silently wrong. Sprout's own `IROp`
classifier tables solve exactly this in-language (new op = compile
error); the C runtime has no equivalent today.

**Fix recipe** (clang's `-Wswitch` warns on missing enumerators, but
only when the switch is over the enum TYPE and has NO `default:` arm):

1. Move `SPROUT_GC_POISON` (bare `0xFF` macro) into `SproutHeapKind`
   so the enum is complete (`SPROUT_HEAP_FREE` already is).
2. Switch on the enum type, not raw bits: one
   `(SproutHeapKind)(h & 0xFF)` cast boundary per function, then a
   real `switch`.
3. Delete the `default:` arms; the "corrupt kind byte" guard (the kind
   comes from raw memory and can be garbage) becomes an explicit
   pre-switch range check or post-switch fallthrough, never `default:`.
4. Add `-Werror=switch` to the runtime clang invocations in the
   justfile.

**Limits.** Protects `switch` only — the few `if/else` kind chains
(e.g. `print_inline_value`) must be converted to switches to benefit.

**Gate.** Standard runtime DoD: full suite + `test-stress` under
`SPROUT_GC_HDRCHECK=1`, examples, canaries. No seed refresh
(runtime-only).

## 2. Sprout design pair: bit-packed records + sized ints/bitwise ops (design item)

**Evidence from this session.** The 64-bit heap header
(kind 8b | color 2b | reserved 4b | aux 50b, with aux sub-packed per
kind: OBJ `(tag<<4)|arity`, CSTR byte length) has ~14 consumer sites.
The 8-angle code review clustered its findings exactly there: three
alloc paths open-coding the pack+memcpy instead of `sprout_hdr_write`,
five open-coded stride computations, and the orchestrator itself
mis-specced `str_len` vs `str_byte_len` from aux-semantics confusion.
The lldb tracer broke silently partly because the tag's bit position
lives in comments, not in a type.

**Proposed feature shape** (design, not committed syntax):

```
packed type HeapHeader = { kind: U8, color: U2, gc_bits: U4, aux: U50 }
```

- Compiler-derived accessors; layout is documentation that cannot go
  stale; changing the layout forces every consumer through the type.
- Composes with `wrap` (zero-cost distinct types) so a packed header is
  not confusable with a plain integer.
- **Prerequisite gap it exposes:** Sprout has NO language-level bitwise
  ops and no sized unsigned ints (verified during the integer-tagging
  feasibility assessment — none of the slotmap/header code could be
  expressed in Sprout today). `U8/U16/U32/U64` + `band/bor/bxor/shl/shr`
  are the entry ticket, and the deferred OCaml-style tagging project
  needs the same machinery at codegen level — one design effort, two
  consumers.
- **Update 2026-08-17: the bitwise half of that entry ticket has
  LANDED** — seven intrinsics in `stdlib.bits`, plus `0x`/`0b` literals
  (`docs/bitwise-int-ops-v0.md`). It splits the two: bitwise-on-`Int`
  needed no representation decision, while sized unsigned ints stay open.
  A `packed type` design can now assume the primitives exist, and the
  field-extract shape sketched above is exercised as a test case
  (`tests/stdlib/test_bits.spr`, the kind/colour/aux extraction).

**Why it matters strategically:** this + finding 1's observation are
the evidence pair for eventually moving runtime logic into Sprout,
where kind dispatch would be exhaustive `match` for free.

## 3. Effectful iteration priority bump (roadmap re-rank, no new design)

Already on README's Not-Yet-Supported list; this session adds three
data points: every GC safety-net test
(`test_gc_string_churn/container_churn/scalar_collision.spr`) had to
hand-write a tail-recursive `churn(n, acc)` accumulator helper to
express "do this 5000 times for its side effects". It is the top
ergonomic tax in test-writing practice. Recommend bumping its roadmap
priority on this evidence.

## 4. Test-authoring gotcha worth documenting: prelude is data-last

Both the test-writing agent and the orchestrator independently guessed
`vec_append(vec, x)` and lost a build cycle to
"Type mismatch: Vec Vec Int vs Int". The prelude is uniformly
data-last (`vec_append(value, vec)`, `vec_set(index, value, vec)`,
`dict_get(key, dict)`). Action: state the convention explicitly in
`docs/guidelines.md` (or the style guide) so it is documentation, not
tribal knowledge. (Also recorded in agent memory.)

## Non-actions (considered, rejected in the retro)

- Refactoring the C runtime toward table-driven kind dispatch
  (X-macros): more machinery than `-Werror=switch` buys, same
  guarantee, worse readability.
- Any retroactive C fix for the header-packing class beyond helpers
  already extracted in the review pass — the real fix is finding 2.

## Context pointers

- Phase-2 results + benchmarks: `docs/gc-profile-findings-2026-07-03.md`
  (addenda), `docs/gc-header-rewrite-handoff-2026-07-03.md` (status
  block), BACKLOG P2 generational item (next GC work) and P3 allocator
  polish.
- Review findings this retro draws on: PR #136's
  "apply phase-2 code-review findings" commit message enumerates them.
