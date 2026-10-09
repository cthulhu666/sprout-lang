# `/sprout-review` Backlog

Open work for this skill only. Same discipline as the repo's `BACKLOG.md`: wrap at 100 columns, at
most 10 lines per entry, and **delete an entry when it lands** rather than ticking it — `just
backlog-shape` enforces all three here too. The root `BACKLOG.md` carries a pointer to this file.

Rationale and measurements live in `README.md`; the skill itself is `SKILL.md`.

## Backlog

- [ ] `P1` **The ensemble has never been A/B'd against the built-in, so 4 agents may find less than
  one does.** Only the reviewer *prompt* is close to a port; the fan-out and the verify pass around
  it are this skill's own design (`README.md` §What the original actually does). Run both on one
  non-trivial diff and compare finding sets. Anything the built-in catches and this misses is a
  design defect, not a tuning question — and if a single careful agent matches three plus a verify
  pass, the ensemble is not worth its cost and should go.

- [ ] `P1` **The whole effort ladder is a cost choice, not a measured one.** `low|medium|high|xhigh
  |max` buy 1/2/3/5/8 passes, and nobody has checked what the 4th or the 8th adds — the one
  unmeasured number this entry used to name is now five of them. Depends on the A/B above: with a
  baseline in hand, sweep N and count *distinct confirmed* findings per agent spent, then write
  the numbers and their date into `README.md` so the ladder stops looking invented. Until then the
  levels are honestly describable only as pass counts, never as "thorough".

- [ ] `P2` **One skeptic now judges every finding, so a prejudice carries across all of them.**
  Verify is a single agent holding the whole list — cheaper than one refuter each, and the shared
  context is why, but a bad call no longer costs one finding. Removing the verify gate widened its
  reach: every finding reaches it now, not just the severe or corroborated ones. The documented
  adversarial pattern is N independent skeptics with a majority rule, the perspective-diverse
  variant giving each a lens (correctness, security, does-it-repro). A 3-judge panel over the
  batched list is `N + 3` — under the old cost at the default level, though not at `max`, where the
  panel is also what would make the verify cap survivable. Do it after the A/B, so its effect is
  visible against a baseline.

- [ ] `P2` **`VERIFY_CAP = 10` comes from nothing and binds hardest where it matters least.**
  At `max`, 8 passes at up to 8 findings each is 64 raw against a cap of 10, so most findings come
  back unverified. Fix with more skeptics (the judge-panel entry above), not a bigger cap: one
  skeptic holding 64 findings judges none of them well.

- [ ] `P2` **Sprout-specific review dimensions are absent, which was the point of owning this.**
  A generic reviewer cannot know GC rooting rules for `stdlib/compiler/` and `runtime/`, that a
  compiler-source edit without `just refresh-seed` blocks every CI gate, idiomatic Sprout
  (`let..else`, combinators, no `not` operator), or the golden-IR trap where regenerating an unread
  diff launders a regression. Add as extra passes *after* the A/B, so their effect is measurable
  against a known baseline rather than mixed into it. See `docs/compiler-internals.md`,
  `docs/gates.md`, `docs/idiomatic-sprout.md`.

- [ ] `P2` **The findings file records what was found, not what was done about it.**
  `review_ledger.sh findings <id>` gives the skill a path and it writes the list there before
  reporting, so the denominator can no longer quietly shrink. What is still missing is the other
  half: which findings were fixed, which were consciously declined, and by what commit. Needs a
  disposition appended per finding as work lands, and something that notices a finding nobody ever
  answered.

- [ ] `P2` **Nothing says whether the code changed since the last review.**
  `rv:2` on a branch reviewed two commits ago reads exactly like `rv:2` on one reviewed just now.
  The `done` row already stores the head SHA; comparing it against the current tree would give a
  staleness marker (`rv:2*`). `review_gate.py` already computes a per-path tree digest that could
  be reused rather than reinvented.

- [ ] `P2` **The cleanup track's value is unmeasured.** Nothing says how many of its findings get
  acted on, or whether four one-angle passes at `xhigh` find more than one four-angle pass. Count
  cleanups fixed vs declined over a few runs (needs the disposition entry above), then decide
  whether `CLEANUP_LADDER` earns its upper rung — or the track its agent at all.

- [ ] `P2` **Doc and comment drift is ~20% of findings, and no angle asks for it.** Of 67 raw
  findings over 11 runs, about 14 were stale comments, spec prose, PR-body claims or diagnostic
  text. They pass only because a stale comment can be phrased as a misbehaviour. Name it: a drift
  angle, or `/code-review`'s conventions angle pointed at `AGENTS.md` §Docs & Spec. `README.md`
  §Why there is a cleanup track has the classification.
