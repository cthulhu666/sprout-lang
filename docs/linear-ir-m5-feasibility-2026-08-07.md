# M5 (linear Sprout-IR) — feasibility analysis and go/no-go

Status: **DECIDED — M5 DEFERRED (2026-08-07).** Written 2026-08-07 after M4 (linear types) landed
(PR #22 + #23), when M5 became unblocked. This doc exists because reconnaissance found that
Milestone 5 as written in `gc-rooting-model-c-plan-2026-06-02.md` is **not executable against the
IR that was actually built**, and the historical bug evidence changes M5's cost/benefit. It
presents the gap, the evidence, three options, and a recommendation.

**Decision (Kuba, 2026-08-07): defer M5.** Neither full linear-IR (Option 1) nor the verifier
(Option 2) is scheduled now. The rooting invariant remains enforced by the dataflow pass
(`ir_rooting.sprout`) plus the exhaustive, no-catch-all op-classification discipline already in
place (§4). The classification-consistency **verifier (Option 2, family 1) is filed as a possible
next task** in `BACKLOG.md` (see the "Linear types / GC-rooting IR" follow-ups). The key finding
below — that the historically-real GC-UAF bugs are classification-completeness (A) and sub-op
alloc-ordering (B), *not* the forgot-to-root-a-known-value (C) that linearity catches for free —
is the durable rationale for that deferral.

## 1. Problem statement

The Model C plan's Milestone 5 (`gc-rooting-model-c-plan-2026-06-02.md:118–131`) says:

> Sprout-IR's `Heap τ` and `Rooted τ` become linear; the AST→IR translator is type-checked against
> the linear discipline; the dataflow analysis becomes redundant and is removed.

The intended payoff: **GC-rooting correctness becomes a type-checker theorem** — the class of bugs
where a codegen author forgets to root a heap value across a GC-triggering call becomes
structurally impossible.

## 2. The executability gap

The IR that was actually built (M2/M3, landed; typed IR is the sole backend since 2026-07-12,
`BACKLOG.md:247`) does **not** match the plan's M5 vision:

- **There is no `Heap τ`/`Rooted τ` type.** `IRType` is a coarse 3-way classification —
  `IRTHeap | IRTScalar | IRTUnknown` (`sprout_ir.sprout:77`) — an *optional annotation field* on
  each op, not a value algebra. There is no rooted-vs-unrooted distinction in the type.
- **Rooting is a dataflow pass, not a type.** `ir_rooting.sprout` (1271 lines; ~5 rule-tables of
  ~45–64 arms each; backward liveness; a call-graph alloc fixpoint) inserts `IRRoot`/`IRUnroot`
  ops. The translator (`ast_to_ir.sprout`) emits **zero** roots today.
- **The M4 linear checker cannot be reused.** `linear_check.sprout` operates over `TypedExpr`
  (the Sprout AST), not `IROp` (SSA form). M5 needs a *new* linearity analysis over the IR —
  structurally a cousin of the very dataflow pass it is meant to replace.

So "declare the types linear and type-check the translator" is not a mechanical step; it requires
first *inventing* the linear IR representation and *rewriting* root emission across the translator.

## 3. The evidence that reframes the decision — a four-bug replay

The value of M5 is preventing the GC-rooting bug class. We have four historical instances of that
class (`BACKLOG.md:248, 250, 251`, and the P11-2e cluster). Replaying each against "would a linear
discipline over the IR have prevented it?" (grounded in the current `ir_rooting.sprout` rules):

| Bug | Root cause | Category | Caught for free by linearity? |
|-----|-----------|----------|-------------------------------|
| 248 | `IRCall` had no kind field (`ret_ty` always `"i64"`); result never classified heap | **(A)** classification-completeness — heap-ness absent from IR | **No** — a verifier reads the same absent field |
| 250a | `IRMakeTuple`/`IRGetTupleField` results not heap-classified | **(A)** | **No** — same blind spot |
| 250b | `@sprout_makeN`/tuple-blob allocate before storing operands | **(B)** sub-op alloc-ordering | **No** — needs explicit `op_exposes_operands` rule in any framing |
| 251 | `@ref_new` (a call operand) collects before storing | **(B)** | **No** — same explicit rule |

Three categories exist:

- **(A) classification-completeness** — the IR didn't *know* a value was heap. A linear checker
  reading the same coarse `IRType` fields inherits the gap identically. Prevented only by making
  classification **total and non-optional** (the actual M5 win — see §4).
- **(B) sub-op allocation-ordering** — an op allocates *before* consuming its operands, so a
  dead-after operand is swept mid-op. Linearity ("consumed exactly once") does not model an
  intra-op GC point; this needs an explicit exposure rule regardless of framing.
- **(C) forgot to root a correctly-classified heap value live across a trigger** — the *only*
  category linearity catches for free. **Zero historical occurrences.**

**Conclusion:** the marketed M5 win (kill category C) addresses a bug class that has never bitten.
The real historical classes are (A) and (B); linearity helps (A) only by making classification
total, and does not help (B) at all.

## 4. What M5 would actually buy, precisely

Full M5's genuine, defensible value is **converting category (A) into a structural
impossibility**: if every SSA value's rooted-ness is a required, checked property of its type, a
codegen author *cannot* construct a value (e.g. add a new `IRCall`-like op) without accounting for
whether its result is a rooted heap pointer — the omission that caused bug 248 becomes a type
error at the definition site.

But note what is **already banked** toward that: `op_produces_simple_heap`, `op_triggers_gc`, and
`op_exposes_operands` are **exhaustive matches with no `_` catch-all** (`ir_rooting.sprout:272–274,
752–754`), added precisely as the P11-2e hardening. Today, a new heap-producing IROp is *already*
a compile error until it is classified in all three tables. That is a weaker, op-table-level
version of M5's core benefit — the cheap 80% is already captured. Full M5 upgrades this from
"you must classify a new op in 3 tables" to "you cannot even represent an unclassified value," at
the cost of a translator rewrite.

## 5. Cost

Regardless of representation choice, M5.2 ("insert root/unroot where the checker demands") means
moving root emission into `ast_to_ir.sprout` — **447 KB, 371 fns, low-hundreds of heap-value
sites** across 100+ `translate_*`/`finish_*`/`emit_*` helpers (the whole `translate_match`,
`translate_do`, `translate_call` families). The blast radius for the *type* change is small (6
files: `sprout_ir`, `ir_rooting`, `ast_to_ir`, `ir_lowering`, `ir_pipeline`, plus the
`field_kinds`/`type_kind` source→kind mappers). The cost is entirely in the translator rewrite and
in re-deriving, by hand and correctly, the output the dataflow pass computes automatically — a
class of work whose subtlety is exactly what made bugs 248–251 take lldb + poison-on-free +
free-trace instrumentation to find. This is a months-scale, high-risk milestone.

## 6. Non-moving-GC coupling (forward hazard)

`sprout_ir.sprout:295–302` documents that "root the alloca, never reload" is sound **only because
the GC is non-moving**; the plan sequences generational/moving GC *after* Model C
(`plan:159`). If M5 encodes the rooting invariant into the *type system*, a future moving GC
becomes a type-system migration rather than a localized pass rewrite. Keeping the invariant in a
pass (or a verifier) localizes that future cost. This argues against baking rooting into types
unless the payoff is clear.

## 7. Options

- **Option 1 — Full M5 (plan-faithful).** Invent a linear/rooted IR value representation, move
  root emission into the translator, add an IR-level linear checker, run the dataflow pass in
  parallel as a sanity check, then delete it. Achieves the plan's end-state; converts (A) to
  structurally-impossible; does **not** address (B) for free; months of high-risk work; deepens
  the non-moving-GC coupling (§6).

- **Option 2 — Targeted classification-totality (the banked win, cheaply).** Do **not** rewrite
  the translator. Instead extract only the realized-historically value: make heap-classification a
  required, verified property. Concretely — a lightweight IR well-formedness **verifier** that
  asserts every heap-producing op carries a present, consistent kind and that the post-rooting IR
  satisfies the rooting invariant, run as a debug/CI gate. Keeps the dataflow pass. Captures the
  (A) protection at low cost and low risk. **This is not the plan's M5** but delivers most of its
  historically-realized value. (Caveat from §3: a verifier over the same coarse fields is blind to
  the *same-field* gaps — so its teeth come from cross-checking kind against an independent
  structural source, e.g. `type_kind`, not from re-reading the field.)

- **Option 3 — Invoke the plan's escape hatch (`plan:171`).** Declare Model C complete at M3+M4.
  Document that the GC-rooting bug class is handled by the dataflow pass plus exhaustive
  op-classification, that full linear-IR was evaluated and deferred as not cost-justified (the
  historical bugs are (A)/(B), not (C); linearity's free win is (C), which never occurred), and
  retire the M5 section. Zero implementation; honest about the evidence.

## 8. Recommendation

The plan's own honest assessment (`plan:171`) reserved the right to stop after M3+M4 "if the M5
upgrade reveals that linearity adds more friction than it removes." The four-bug replay is that
reveal: linearity's free win (C) is a bug class we have never hit, its cost is a months-scale
447 KB-translator rewrite, and it deepens the non-moving-GC coupling. **Recommend Option 2** — bank
the classification-totality protection with a low-risk verifier — and, if that proves the discipline
sound and worth deepening, treat full Option 1 as a later, evidence-backed escalation rather than
the default next step. Option 3 is defensible if we judge even the verifier not worth it given the
exhaustive-match discipline already in place. **Do not** pursue Option 1 as written first: its
headline benefit is unsupported by the bug history.

## 9. If Option 1 is chosen anyway — the one real design decision

Everything in Option 1 turns on: does `IRType` gain a rooted/unrooted distinction (rooting becomes
a type-level property the translator must satisfy), or does rooting stay op-level with a separate
IR-level linear checker? The type change is 6 files; the translator rewrite is the cost. That fork,
plus a spike on ~10 representative `translate_*` sites to measure the per-site rewrite cost, should
precede any commitment to 5.2/5.3.
