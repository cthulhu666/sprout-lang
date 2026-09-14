# `/sprout-review` Backlog

Open work for this skill only. Same discipline as the repo's `BACKLOG.md`: wrap at 100 columns, at
most 10 lines per entry, and **delete an entry when it lands** rather than ticking it — `just
backlog-shape` enforces all three here too. The root `BACKLOG.md` carries a pointer to this file.

Rationale and measurements live in `README.md`; the skill itself is `SKILL.md`.

## Backlog

- [ ] `P1` **The ensemble has never been A/B'd against the built-in, so it may cost 15 agents to
  find less than one does.** Only the reviewer *prompt* is a port; the fan-out, dedup and verify
  around it are this skill's own design (`README.md` §What the original actually does). Run both on
  one non-trivial diff and compare finding sets. Anything the built-in catches and this misses is a
  design defect, not a tuning question — and if a single careful agent matches eight plus a verify
  phase, the ensemble is not worth its cost and should go.

- [ ] `P1` **`N = 8` reviewers rests on nothing now that the "original uses 15" premise is gone.**
  It was picked as a saving against a number that was never real. Nobody has checked what a 4th or
  a 12th pass adds. Depends on the A/B above: with a baseline in hand, sweep N and count *distinct
  confirmed* findings per agent spent, then write the number and its date into `README.md` so the
  dial stops looking arbitrary.

- [ ] `P2` **One verifier decides each finding; the documented pattern is a panel.**
  Verify spawns a single refuter per finding, so one bad call silently kills a real bug or keeps a
  false one. The adversarial pattern is N independent skeptics with a majority rule, and the
  perspective-diverse variant gives each a different lens (correctness, security, does-it-repro).
  Now affordable: the severity/votes cap cut the verified set from 17 to 7 on the calibration run,
  so a 3-judge panel costs about what one verifier per finding used to.

- [ ] `P2` **The verify cap's thresholds rest on one run, so a real finding could be lost.**
  `SKILL.md` verifies a finding only when `severity !== 'low' || votes >= 2`, and clusters within
  6 lines at 0.5 summary overlap. Those numbers come from run `1789385000-27845` alone: 5 adjacent
  pairs, scoring 0.60/0.67/0.67 against 0.33/0.29. The gap is wide, which is why 0.5 is not a knife
  edge, but n=5 is not a calibration set. Re-measure over several runs once the findings files have
  accumulated — they are on disk now, which is what makes this checkable.

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
