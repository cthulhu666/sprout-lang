# `/sprout-review` Backlog

Open work for this skill only. Same discipline as the repo's `BACKLOG.md`: wrap at 100 columns, at
most 10 lines per entry, and **delete an entry when it lands** rather than ticking it — `just
backlog-shape` enforces all three here too. The root `BACKLOG.md` carries a pointer to this file.

Rationale and measurements live in `README.md`; the skill itself is `SKILL.md`.

## Backlog

- [ ] `P1` **The port has never been A/B'd against the built-in, so "faithful" is untested.**
  The whole reason for porting rather than rewriting was to keep a baseline to compare with. Run
  both on one non-trivial diff and compare finding sets: anything the built-in catches and ours
  misses is a port defect, not a tuning question. Until that runs, the claim in `README.md` that
  this reproduces the original's shape rests on reading one agent's prompt, not on behaviour.

- [ ] `P1` **8 reviewers instead of 15 is an unmeasured cost/recall trade.**
  The original fans out 15; `SKILL.md` uses 8 to halve the spend. Nobody has checked what the
  missing 7 would have found. Depends on the A/B above: with both finding sets in hand, re-run ours
  at 15 and see whether the extra passes add distinct confirmed findings or just repeat. If they
  add nothing, write the number down in `README.md` so the dial stops looking arbitrary.

- [ ] `P2` **Dedup keys on `file:line`, so a finding that moves one line reads as two.**
  `SKILL.md`'s dedup uses `${f.file}:${f.line}`. Two reviewers describing the same bug at lines 104
  and 106 produce two entries, each with one vote, and both get verified separately — wasted spend
  and a `votes` count that understates agreement. Key on a normalised summary as well, or cluster
  within a small line window.

- [ ] `P2` **One verifier decides each finding; the documented pattern is a panel.**
  Verify spawns a single refuter per finding, so one bad call silently kills a real bug or keeps a
  false one. The adversarial pattern is N independent skeptics with a majority rule, and the
  perspective-diverse variant gives each a different lens (correctness, security, does-it-repro).
  Cheap to add — the findings list is already small by then.

- [ ] `P2` **Sprout-specific review dimensions are absent, which was the point of owning this.**
  A generic reviewer cannot know GC rooting rules for `stdlib/compiler/` and `runtime/`, that a
  compiler-source edit without `just refresh-seed` blocks every CI gate, idiomatic Sprout
  (`let..else`, combinators, no `not` operator), or the golden-IR trap where regenerating an unread
  diff launders a regression. Add as extra passes *after* the A/B, so their effect is measurable
  against a known baseline rather than mixed into it. See `docs/compiler-internals.md`,
  `docs/gates.md`, `docs/idiomatic-sprout.md`.

- [ ] `P2` **The ledger records counts, not findings or what was done about them.**
  The MVP stores `found`/`confirmed` per run. It cannot answer "which findings were fixed, which
  were refuted, and by what commit" — the audit trail originally wanted, where the reviewer writes
  the findings before anyone acts so the denominator cannot quietly shrink. Needs a second file
  keyed by finding id, written by the skill, with dispositions appended as work lands.

- [ ] `P2` **Nothing says whether the code changed since the last review.**
  `rv:2` on a branch reviewed two commits ago reads exactly like `rv:2` on one reviewed just now.
  The `done` row already stores the head SHA; comparing it against the current tree would give a
  staleness marker (`rv:2*`). `review_gate.py` already computes a per-path tree digest that could
  be reused rather than reinvented.

- [ ] `P2` **The skill assumes it runs from the repo root.**
  `SKILL.md` invokes `bash scripts/review_ledger.sh`, which breaks in a session started in a
  subdirectory. Resolve via `git rev-parse --show-toplevel` instead. Untested, and it would fail
  quietly as a missing-record rather than a loud error — the worst shape for this bug.
