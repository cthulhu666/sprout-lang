# linearity-bound steps 1–2 retro — why eight review rounds kept finding bugs (2026-10-20)

Retro question: `docs/linearity-bound-v0.md` §12 steps 1 and 2 (PRs #696 and #697) took eight
`/sprout-review` rounds, and seven of them confirmed at least one bug. Why, and what process,
tooling, compiler or test change would have caught them before review?

Source: the review ledger (`review_ledger.sh count`) and the eight findings files of
`linearity-bound-check`, runs `1792500341-45363` to `1792523906-4454`.

## 1. The count

| round | PR @ commit | confirmed bugs | severities |
|---|---|---|---|
| 1 | #696 @ `92b46c81` | 2 | low, low |
| 2 | #696 @ `6bd3b30f` | 1 | low |
| 3 | #697 @ `9ffccfcf` | 5 | high, medium, medium, low, low |
| 4 | #697 @ `0d8dcd89` | 3 | low ×3 |
| 5 | #697 @ `fbc59eb9` | 2 | high, low |
| 6 | #697 @ `05f1b37c` | 0 | — |
| 7 | #697 @ `ad0afd6f` | 2 | low, low |
| 8 | #697 @ `de8abf05` | 1 | medium |

16 confirmed bugs. Both highs let a program copy or leak a linear value. Round 6 found nothing and
round 7 then found two, so a zero round was not convergence.

## 2. Defect classes

| class | n | rounds |
|---|---|---|
| a safety check removed ahead of its premise | 2 | 3 #1, 8 #1 |
| binding groups fixed one example at a time | 4 | 3 #2, 5 #1, 7 #1, 7 #2 |
| a surface the change reaches but did not cover (extern, stdlib caller, class-method signature, spec example) | 5 | 2 #1, 3 #3, 3 #4, 4 #3, 5 #2 |
| a diagnostic whose hint cannot be followed, or that prints an internal name | 3 | 3 #5, 4 #1, 4 #2 |
| parser edge cases | 2 | 1 #1, 1 #2 |

Round 7 #2 was caused by round 5's fix, which marks every member's bounds in every member.

## 3. Causes

1. **A check was removed before its premise held.** `beba0a9f` dropped the rule that a discarded
   bare type variable may be linear, on the premise that no caller can instantiate an unbounded
   variable at a linear type. That holds only where every instantiation is checked. A binding-group
   sibling (round 3) and an instance head (round 8) were not; the design doc already gave the
   instance head to step 3. An incomplete new check only misses some errors; a premature removal
   accepts bad programs.
2. **Binding groups were never designed as a whole.** Each fix covered the reported example and
   left its neighbour. Round 5's cleanup #4 proposed the structural fix — decide everything once,
   at the group boundary, under the final substitution — and was skipped at worth 2. That design
   covers round 3 #2 and round 5 #1; round 7 #1 also needs cause 3 fixed.
3. **A soundness check depends on the whole program.** `program_has_linear` gates bound parking,
   so a library's acceptance depended on whether an unrelated module declares a linear type
   (round 7 #1). Parking costs nothing measurable: checking the compiler (4,536 declarations) with
   `stdlib.task` imported took 2.38–2.49 s with parking and 2.39–2.67 s with it removed.
4. **The tests could not see it.** `just test` was green every round; the corpus holds little
   linear code. Fixtures check one declaration at a time. Some `.err` files pin a 14-byte prefix,
   so a wrong hint passes. Spec examples are never compiled.
5. **A medium review samples.** Two passes found about two bugs a round. Most confirmed bugs were
   found by a reviewer writing a misuse program and checking that master rejects it.

## 4. What worked

- The ledger and findings files made this count possible; without them it would be recollection.
- The adversarial skeptic refuted round 8 #2 correctly (a step-3 gap, not a regression).
- Master-versus-head comparison found both regressions. §5 T1 makes it a tool.

## 5. Plan

To do after #697 lands and before step 3, in this order.

1. **P1 — premises before removals** (AGENTS.md, Design Change Process). A change that removes or
   relaxes a check lists the premises it rests on, with a test for each. A premise owned by a later
   step moves the removal to that step. Done when the rule is in AGENTS.md.
2. **C1 — park bound obligations unconditionally.** Drop `program_has_linear` from
   `park_bound_obligations`; keep it only where it cannot change a result. Measured cost in §3.3.
   Done when the round 7 #1 library is rejected standalone.
3. **C2 — one group-boundary check.** Decide bound obligations and marks once per binding group,
   under its final substitution, instead of at park time, in the gate env and by group-wide
   marking. Done when one function at the group boundary decides all three and the group
   fixtures pass.
4. **T1 — accept-diff.** `scripts/accept_diff.sh <base-bin> <head-bin> <dir>`: report every
   program the base rejects and the head accepts. CI builds the base from `master`'s seed.
5. **T2 — generated misuse corpus.** Emit one program per reach path × misuse into T1's input.
   Reach paths: direct call, annotated and unannotated group sibling, instance head, class method's
   own variable, extern, constructor, top-level `let`, existential. Misuses: copy, drop, discarded
   step, `_ <-`, pass to an unbounded generic. Done when T1 runs it in `ci-fast-gates`.
6. **X1 — hint is followable.** A `type_error/<name>.fixed.spr` beside a fixture applies its hint
   and must check clean; `test-type-errors` runs it.
7. **X2 — full messages.** A new diagnostic's `.err` pins the whole message. A review-gate line,
   not a gate: existing short pins stay.
8. **T3 — soundness-attack pass.** `/sprout-review` gains a pass for type-system diffs whose job
   is to write and run programs that leak or copy a linear value, against master and head.
9. **P2 — fix the class.** A review bug's fix adds the neighbouring reach paths from T2's grid,
   and a skipped altitude cleanup that would remove an open bug class is raised with the bug.
10. **X3 — compile spec examples** in §5.8, at least: extract the fenced blocks and check each,
    marked expected-error where the text says it is rejected.

Step 3 starts with T2's grid extended to dictionaries and existentials, as failing tests.
