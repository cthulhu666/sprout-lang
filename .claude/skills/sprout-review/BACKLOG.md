# `/sprout-review` Backlog

Open work for this skill only. Same discipline as the repo's `BACKLOG.md`: wrap at 100 columns, at
most 10 lines per entry, and **delete an entry when it lands** rather than ticking it — `just
backlog-shape` enforces all three here too. The root `BACKLOG.md` carries a pointer to this file.

Rationale and measurements live in `README.md`; the skill itself is `SKILL.md`.

## Backlog

- [ ] `P1` **The ensemble has never been A/B'd against the built-in, so 4 agents may find less than
  one does.** Only the reviewer *prompt* is close to a port; the fan-out, dedup and verify around it
  are this skill's own design (`README.md` §What the original actually does). Run both on one
  non-trivial diff and compare finding sets. Anything the built-in catches and this misses is a
  design defect, not a tuning question — and if a single careful agent matches three plus a verify
  pass, the ensemble is not worth its cost and should go.

- [ ] `P1` **`N = 3` reviewers is a cost choice, not a measured one.** Nobody has checked what a
  4th or an 8th pass adds; 8 was itself picked against a premise that turned out false, and 3 is
  just the smallest N where `votes >= 2` means agreement. Depends on the A/B above: with a baseline
  in hand, sweep N and count *distinct confirmed* findings per agent spent, then write the number
  and its date into `README.md` so the dial stops looking arbitrary.

- [ ] `P2` **One skeptic now judges every finding, so a prejudice carries across all of them.**
  Verify is a single agent holding the whole list — cheaper than one refuter each, and the shared
  context is why, but a bad call no longer costs one finding. The documented adversarial pattern is
  N independent skeptics with a majority rule, the perspective-diverse variant giving each a lens
  (correctness, security, does-it-repro). A 3-judge panel over the batched list is `N + 3`, still
  under the old cost. Do it after the A/B, so its effect is visible against a baseline.

- [ ] `P2` **Every threshold in the verify path rests on one run or on nothing.**
  `SKILL.md` verifies a finding only when `severity !== 'low' || votes >= 2`, clusters within 6
  lines at 0.5 summary overlap, and now caps the batch at `VERIFY_CAP = 10`. The first two come
  from run `1789385000-27845` alone: 5 adjacent pairs scoring 0.60/0.67/0.67 against 0.33/0.29 — a
  wide gap, but n=5 is not a calibration set. The cap comes from nothing; with 3 passes capped at 8
  findings each it has never bound. Re-measure over the findings files as they accumulate.

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
