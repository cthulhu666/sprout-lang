---
name: sprout-review
description: Review the current diff for real bugs, as `/code-review` does, and record the run in the branch's review ledger so the status line can report how many reviews this PR has had. Invoke when the user asks for a code review of the working tree, the branch, or a PR.
---

# sprout-review

An ensemble diff review that **records that it ran**: N independent passes, dedup, an adversarial
verify, and a ledger row plus a findings file on disk either side of it.

Two things follow from that and govern the procedure below. The run is **owned** — the row is
opened before reviewing and closed after, so the count is exact by construction rather than
inferred from hooks. And the run **reports, then stops**: the findings are written down and handed
over, not acted on.

Repo-specific review dimensions (GC rooting, seed staleness, idiomatic Sprout) are NOT here yet.

Why any of this exists, what was measured to get here, and what is still open: `README.md` and
`BACKLOG.md` beside this file.

## Procedure

**1. Open the ledger row.** Before any review work:

```
bash "$(git rev-parse --show-toplevel)/scripts/review_ledger.sh" open
```

Resolve the repo root rather than using a relative path: a session started in a subdirectory would
otherwise fail to find the script, and the failure shows up as a *missing row* — indistinguishable
from "never reviewed", which is the one thing this skill exists to report.

Keep the run id it prints. If this fails, say so and continue — a review that cannot be recorded is
still worth doing, but do not silently skip the recording.

**2. Run the review.** Call the `Workflow` tool with the script below. The skill's instructions
telling you to call it are the user's opt-in, so no further confirmation is needed.

Use `N = 8` reviewers by default, or the number the user named. It is a dial: raise it if the
review finds less than the built-in does on the same diff.

**3. Write the findings to disk** before reporting them, at the path the ledger names:

```
bash "$(git rev-parse --show-toplevel)/scripts/review_ledger.sh" findings <run-id>
```

Write every finding there — confirmed, unverified and refuted, each with its file:line, severity,
votes and scenario. This is the step that makes the next one checkable. Findings that exist only
inside a chat message cannot be pointed at afterwards, which is the same failure the ledger exists
to fix, one level down: a count without a list says a review happened but not what it said.

**4. Close the ledger row** with the counts the workflow returned:

```
bash "$(git rev-parse --show-toplevel)/scripts/review_ledger.sh" done <run-id> <found> <confirmed>
```

`found` is the deduplicated finding count before verification; `confirmed` is how many survived it.
Close the row even when the count is zero — a review that found nothing still happened, and a
missing row reads as "never reviewed".

**5. Report the findings and STOP.** Most severe first, with the unverified ones marked as such and
the refuted ones listed briefly. Then hand the decision over and wait.

Do not fix anything in this turn, and do not commit, amend or push. The temptation is strong when a
finding is obviously right and the fix is three lines — and it defeats the skill. A review whose
findings arrive alongside "…and I have already fixed all of them, and force-pushed" gave the reader
no decision to make; they got a changelog. **This has happened** (run `1789385000-27845`: the
workflow returned at 12:07, the findings reached the user at 12:31, after the fixes were amended and
pushed), which is why it is written here as a rule rather than left to judgement.

## The workflow script

Pass this to `Workflow` as `script`, substituting `N`:

```js
export const meta = {
  name: 'sprout-review',
  description: 'Ensemble diff review: N careful reviewers, dedup, adversarial verify',
  phases: [
    { title: 'Review', detail: 'N independent careful passes over the diff' },
    { title: 'Verify', detail: 'try to refute each severe or corroborated finding' },
  ],
}

const N = 8

const FINDINGS = {
  type: 'object',
  properties: {
    findings: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          file: { type: 'string' },
          line: { type: 'number' },
          severity: { enum: ['low', 'medium', 'high'] },
          summary: { type: 'string' },
          scenario: { type: 'string' },
        },
        required: ['file', 'line', 'severity', 'summary', 'scenario'],
      },
    },
  },
  required: ['findings'],
}

const VERDICT = {
  type: 'object',
  properties: {
    refuted: { type: 'boolean' },
    reason: { type: 'string' },
  },
  required: ['refuted', 'reason'],
}

// Verbatim from the built-in reviewer, so the port stays comparable. The only
// change: findings come back through the schema instead of ReportFindings,
// which was not available to the original's agents in practice.
const REVIEWER = `You are reviewing a pull request for real bugs. Run \`git diff @{upstream}...HEAD\` (or \`git diff main...HEAD\` / \`git diff HEAD~1\`
if there's no upstream) to get the unified diff under review. If there are
uncommitted changes, or the range diff is empty, also run \`git diff HEAD\` and
include the working-tree changes in scope — the review often runs before the
commit. If a PR number, branch name, or file path was passed as an argument,
review that target instead. Treat this diff as the review scope.

Review the diff as a careful senior engineer would: read every hunk, open the surrounding files for context as needed (Read, Grep, git log/blame/show), and hunt for correctness issues — wrong or inverted conditions, off-by-one, null/undefined dereference, missing \`await\`, dropped error handling, removed guards or validations, broken callers of changed functions, races. Prefer real failure modes over style; every finding needs a concrete scenario in which the code misbehaves.

Report at most 15 findings. Quality over quantity: include everything you genuinely believe is a real issue, and nothing you don't.`

phase('Review')
const passes = await parallel(
  Array.from({ length: N }, (_, i) => () =>
    agent(REVIEWER, { label: `review:${i + 1}`, phase: 'Review', schema: FINDINGS })))

// A barrier is right here: dedup needs every pass at once, and verifying the
// same finding eight times would cost eight times as much for one answer.
const all = passes.filter(Boolean).flatMap(p => p.findings || [])
const RANK = { low: 0, medium: 1, high: 2 }

// Two reviewers describing ONE bug rarely land on one line. Keying on
// `file:line` split those into two entries with one vote each, and both were
// verified separately — the votes then understated the very agreement they
// exist to measure. Proximity alone over-merges, though: two real and distinct
// findings can sit one line apart. So both must hold.
const STOP = new Set(['that', 'this', 'with', 'from', 'when', 'which', 'been',
  'have', 'into', 'then', 'than', 'only', 'also', 'same', 'does', 'make',
  'made', 'will', 'would', 'should', 'could'])
const words = s => new Set((String(s).toLowerCase().match(/[a-z_]{4,}/g) || [])
  .filter(w => !STOP.has(w)))
const overlap = (a, b) => {
  const A = words(a), B = words(b)
  if (!A.size || !B.size) return 0
  let hit = 0
  for (const w of A) if (B.has(w)) hit += 1
  return hit / Math.min(A.size, B.size)
}
// Calibrated on one run (5 adjacent pairs): the three true duplicates scored
// 0.60/0.67/0.67 and the two genuinely-different pairs 0.33/0.29, so 0.5 sits
// in a wide gap rather than on a knife edge. One run is not a calibration set;
// if a real finding is ever swallowed, raise it and say so here.
const LINE_WINDOW = 6
const OVERLAP_MIN = 0.5

const byFile = new Map()
for (const f of all) {
  if (!byFile.has(f.file)) byFile.set(f.file, [])
  byFile.get(f.file).push(f)
}
const deduped = []
for (const [, group] of byFile) {
  group.sort((a, b) => a.line - b.line)
  const clusters = []
  for (const f of group) {
    // First cluster still within reach whose wording matches. `lastLine` moves as
    // a cluster grows, so 100/105/110 stay one finding instead of splitting when
    // the chain outruns the window.
    const hit = clusters.find(c => f.line - c.lastLine <= LINE_WINDOW &&
                                   overlap(f.summary, c.summary) >= OVERLAP_MIN)
    if (!hit) { clusters.push({ ...f, votes: 1, lastLine: f.line }); continue }
    hit.votes += 1
    hit.lastLine = f.line
    // Carry the more severe wording, not the lower-numbered line's: the summary
    // is what the reader acts on, and the harsher reading is the one to answer.
    if (RANK[f.severity] > RANK[hit.severity]) {
      hit.severity = f.severity
      hit.summary = f.summary
      hit.scenario = f.scenario
      hit.line = f.line
    }
  }
  for (const c of clusters) { delete c.lastLine; deduped.push(c) }
}
log(`${all.length} raw findings from ${passes.filter(Boolean).length} passes -> ${deduped.length} distinct`)

// One verifier per finding is what makes the agent count unbounded: it is
// `N + D`, and D is only known at runtime. Verification earns its cost on a
// finding that is severe or corroborated; on a lone low-severity doc nit it
// spends a full agent to confirm a typo. Those are still REPORTED, just
// unverified — visible and cheap, rather than invisible or expensive.
//
// The cut is deliberately NOT on votes alone. On the run that motivated it, the
// most valuable finding — a real regression the author had just introduced —
// was single-vote medium, and a `votes >= 2` gate would have dropped it.
const worthVerifying = f => f.severity !== 'low' || f.votes >= 2
const toVerify = deduped.filter(worthVerifying)
const unverified = deduped.filter(f => !worthVerifying(f))
log(`verifying ${toVerify.length} of ${deduped.length}; ${unverified.length} single-vote low-severity reported UNVERIFIED`)

phase('Verify')
const judged = await parallel(toVerify.map(f => () =>
  agent(`Try to REFUTE this finding. Default to refuted=true if you are unsure it is real.

File: ${f.file}:${f.line}
Claim: ${f.summary}
Scenario given: ${f.scenario}

Read the code and decide whether the failure genuinely occurs.`,
    { label: `verify:${f.file}:${f.line}`, phase: 'Verify', schema: VERDICT })
    .then(v => ({ ...f, verdict: v }))))

const confirmed = judged.filter(Boolean).filter(f => f.verdict && !f.verdict.refuted)
const byVotes = (a, b) => b.votes - a.votes || RANK[b.severity] - RANK[a.severity]
return {
  found: deduped.length,
  confirmed: confirmed.length,
  findings: confirmed.sort(byVotes),
  unverified: unverified.sort(byVotes),
  refuted: judged.filter(Boolean).filter(f => f.verdict && f.verdict.refuted),
}
```

## Notes

- **Report the refuted findings too**, briefly. A finding the verifier killed is information about
  the reviewers, and hiding it makes the confirmed count look better than it is.
- **Report the unverified ones as unverified.** They were never put to a refuter, so "confirmed"
  and "not confirmed" both misdescribe them. Silently dropping them would be the failure the
  `Workflow` guidance names: a bounded pass that reads as full coverage.
- **`found` counts all distinct findings**, verified or not, so the ledger's denominator does not
  shrink when the cap is tightened. Only `confirmed` moves with the verifier.
- **`votes` is agreement, not truth.** Several passes reaching the same conclusion makes a finding
  worth reading first; it does not make it right. The verify phase is what decides that.
- **The agent count is `N + D`,** where D is the findings that clear the cap — not a fixed number.
  Eight reviewers finding two apiece is two dozen agents if nothing bounds the second phase.
- The ledger lives at `$GIT_DIR/claude-review/runs.tsv` — per-worktree, invisible to `git status`,
  append-only so two concurrent sessions cannot clobber each other. See `scripts/review_ledger.sh`.
