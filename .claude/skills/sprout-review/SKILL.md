---
name: sprout-review
description: Review the current diff, or a PR number/branch/path target, for real bugs at a chosen effort level, as `/code-review` does, and record the run in the branch's review ledger so the status line can report how many reviews this PR has had. Invoke when the user asks for a code review of the working tree, the branch, or a PR.
argument-hint: "[low|medium|high|xhigh|max] [<pr#>|<branch>|<path>]"
---

# sprout-review

An ensemble diff review that **records that it ran**: N independent passes, dedup, one adversarial
verify pass, and a ledger row plus a findings file on disk either side of it.

It costs at most **N + 1 agents**, known before the run — four at the default level, or three when
nothing clears the verify gate and the skeptic is skipped. The effort level moves `N`; §Arguments
has the table.

Two things follow from that and govern the procedure below. The run is **owned** — the row is
opened before reviewing and closed after, so the count is exact by construction rather than
inferred from hooks. And the run **reports, then stops**: the findings are written down and handed
over, not acted on.

Repo-specific review dimensions (GC rooting, seed staleness, idiomatic Sprout) are NOT here yet.

Why any of this exists, what was measured to get here, and what is still open: `README.md` and
`BACKLOG.md` beside this file.

## Arguments

```
/sprout-review [low|medium|high|xhigh|max] [<pr#>|<branch>|<path>]
```

This invocation's arguments, verbatim (empty when none were given): `$ARGUMENTS`

**The level.** The first token, if it names a level. `med` abbreviates `medium`, as in
`/code-review`. Everything after it is the review target; with no level, everything is the target.

A first token that *looks* like a level but is not one — `higher`, `mid`, `maximum` — is an error:
**say so and stop**, rather than reviewing at the default or treating it as a branch name. Both
silent readings are worse than the question, because the run is about to be recorded as having
happened at a level nobody chose. `/code-review` makes the same distinction.

**The default is `high`.** Not the session effort: a review's job is to be comparable to the last
one on the same branch, and a level that drifts with whatever `/effort` happens to be set to makes
`review:3` mean three different things. `high` is also what this skill already did (`N = 3`).

**What the level moves — two dials, and only two:**

| level | N passes | reviewer effort | agents (max) |
|---|---|---|---|
| `low` | 1 | `low` | 2 |
| `medium` | 2 | `medium` | 3 |
| **`high`** (default) | **3** | `high` | **4** |
| `xhigh` | 5 | `xhigh` | 6 |
| `max` | 8 | `max` | 9 |

These five rows are a cost ladder, not a measurement — the same caveat `README.md` already records
for `N = 3`, now multiplied by five. `BACKLOG.md` owns closing that.

**What the level does NOT move**, deliberately: `VERIFY_CAP`, `LINE_WINDOW`, `OVERLAP_MIN` and the
verify gate. Those are calibration constants with an open `BACKLOG.md` entry to measure them, and a
constant that varies with a flag cannot be calibrated. One consequence is worth stating plainly:
at `xhigh` and `max` the cap binds hard — 8 passes at up to 8 findings each is 64 raw against a cap
of 10 — so most findings come back **unverified rather than unchecked-and-presented-as-checked**.
The fix for that is more skeptics (the judge-panel entry in `BACKLOG.md`), not a bigger cap.

**The target.** A PR number, branch name or path, passed through to the reviewers, which review it
instead of the working diff. The reviewer prompt has always described this; until the arguments
were parsed, nothing could ever pass one.

## Procedure

**1. Resolve the level and target** from the arguments quoted above, per §Arguments. Do this first:
a bad level must fail before a ledger row exists, or an abandoned run leaves a `start` row behind
for a review that was never attempted. Tell the user the level and the agent count before spending
them.

**2. Open the ledger row.** Before any review work:

```
bash "$(git rev-parse --show-toplevel)/scripts/review_ledger.sh" open
```

Resolve the repo root rather than using a relative path: a session started in a subdirectory would
otherwise fail to find the script, and the failure shows up as a *missing row* — indistinguishable
from "never reviewed", which is the one thing this skill exists to report.

Keep the run id it prints. If this fails, say so and continue — a review that cannot be recorded is
still worth doing, but do not silently skip the recording.

**3. Run the review.** Call the `Workflow` tool with the script below **verbatim**, passing the
resolved level and target as `args`:

```
args: { "effort": "<level>", "target": "<target, or empty>" }
```

The skill's instructions telling you to call it are the user's opt-in, so no further confirmation
is needed.

The level travels as data, not as an edit to the script. The script holds the level→N table, so it
is the one place `N` is decided and an unrecognised level cannot silently produce a broken one;
hand-substituting three values into two hundred lines could. Editing the script also breaks
`resumeFromRunId`, which caches on the exact `(prompt, opts)` pair.

Note that `votes` does not measure how many *passes* agreed: dedup pools every pass's findings
before clustering, so one pass reporting the same bug at two nearby lines produces a 2-vote cluster
on its own.

**4. Write the findings to disk** before reporting them, at the path the ledger names:

```
bash "$(git rev-parse --show-toplevel)/scripts/review_ledger.sh" findings <run-id>
```

Write every finding there — confirmed, unverified and refuted, each with its file:line, severity,
votes and scenario. This is the step that makes the next one checkable. Findings that exist only
inside a chat message cannot be pointed at afterwards, which is the same failure the ledger exists
to fix, one level down: a count without a list says a review happened but not what it said.

**5. Close the ledger row** with the counts the workflow returned, and the level it ran at:

```
bash "$(git rev-parse --show-toplevel)/scripts/review_ledger.sh" done <run-id> <found> <confirmed> <level>
```

`found` is the deduplicated finding count before verification; `confirmed` is how many survived it.
For `<level>` use the workflow's returned `effort`, not the token the user typed and not what you
resolved in step 1 — the three differ precisely when something went wrong, and the returned one is
the level the passes actually ran at. The ledger stores only the known vocabulary, so a level it
does not recognise is dropped silently rather than corrupting the row: nothing downstream will
complain about a wrong one.

Close the row even when the count is zero — a review that found nothing still happened, and a
missing row reads as "never reviewed".

**6. Report the findings and STOP.** Most severe first, with the unverified ones marked as such and
the refuted ones listed briefly. Then hand the decision over and wait.

Do not fix anything in this turn, and do not commit, amend or push. The temptation is strong when a
finding is obviously right and the fix is three lines — and it defeats the skill. A review whose
findings arrive alongside "…and I have already fixed all of them, and force-pushed" gave the reader
no decision to make; they got a changelog. **This has happened** (run `1789385000-27845`: the
workflow returned at 12:07, the findings reached the user at 12:31, after the fixes were amended and
pushed), which is why it is written here as a rule rather than left to judgement.

## The workflow script

Pass this to `Workflow` as `script`, unmodified. Everything variable arrives through `args`.

```js
export const meta = {
  name: 'sprout-review',
  description: 'Ensemble diff review: N careful reviewers, dedup, adversarial verify',
  phases: [
    { title: 'Review', detail: 'N independent careful passes over the diff' },
    { title: 'Verify', detail: 'one skeptic refutes the severe or corroborated findings' },
  ],
}

// The effort ladder, and the only place N is decided. An unrecognised level
// falls back to the default rather than to `undefined` passes — the caller
// should have rejected it already, but a review that silently runs zero passes
// and closes a ledger row is the worst available failure.
const LADDER = { low: 1, medium: 2, high: 3, xhigh: 5, max: 8 }
const EFFORT = (args && LADDER[args.effort]) ? args.effort : 'high'
const N = LADDER[EFFORT]
const TARGET = (args && typeof args.target === 'string') ? args.target.trim() : ''
if (!args || !LADDER[args.effort]) log(`no usable effort in args — defaulting to ${EFFORT}`)

// The skeptic never drops below medium, however cheap the reviewers are. It is
// prompted to default to refuted=true when unsure, so lowering its effort makes
// it cheaper at KILLING real findings — the one direction in which saving
// tokens costs correctness rather than coverage.
const VERIFY_EFFORT = (EFFORT === 'low') ? 'medium' : EFFORT

// Deliberately NOT a function of EFFORT: this is what one skeptic can hold at
// once, which does not grow because more reviewers ran. At xhigh and max it
// binds hard and the excess is reported unverified. See BACKLOG.md.
const VERIFY_CAP = 10

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

const VERDICTS = {
  type: 'object',
  properties: {
    verdicts: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          index: { type: 'number' },
          refuted: { type: 'boolean' },
          reason: { type: 'string' },
        },
        required: ['index', 'refuted', 'reason'],
      },
    },
  },
  required: ['verdicts'],
}

// The scope paragraph. The original said "if a target was passed as an argument,
// review that instead" while nothing could ever pass one — a branch that read as
// supported and was unreachable. The target now arrives in `args`, so the two
// cases are separate prompts and neither mentions the other's.
const SCOPE = TARGET ? `You are reviewing \`${TARGET}\` for real bugs. Resolve it as a PR number, a
branch name, or a file path — in that order — and get the unified diff it names
(\`gh pr diff <n>\`, \`git diff <branch>...HEAD\`, or the file's current contents).
Treat that, and nothing else, as the review scope.` : `You are reviewing a pull request for real bugs. Run \`git diff @{upstream}...HEAD\` (or \`git diff main...HEAD\` / \`git diff HEAD~1\`
if there's no upstream) to get the unified diff under review. If there are
uncommitted changes, or the range diff is empty, also run \`git diff HEAD\` and
include the working-tree changes in scope — the review often runs before the
commit. Treat this diff as the review scope.`

// The built-in reviewer's prompt, with two changes: findings come back through
// the schema instead of ReportFindings, which was not available to the
// original's agents in practice, and the last paragraph bounds the search —
// exploration is where a pass spends its tokens.
const REVIEWER = `${SCOPE}

Review the diff as a careful senior engineer would: read every hunk, open the surrounding files for context as needed (Read, Grep, git log/blame/show), and hunt for correctness issues — wrong or inverted conditions, off-by-one, null/undefined dereference, missing \`await\`, dropped error handling, removed guards or validations, broken callers of changed functions, races. Prefer real failure modes over style; every finding needs a concrete scenario in which the code misbehaves.

Report at most 8 findings. Quality over quantity: include everything you genuinely believe is a real issue, and nothing you don't.

Stay inside the diff and what it touches. Read the changed hunks, the files they are in, and the callers of anything whose signature or behaviour changed. That is the budget — do not survey the repository, re-read a file you have already read, or go looking for pre-existing bugs the diff did not introduce.`

phase('Review')
log(`${EFFORT}: ${N} pass(es) at ${EFFORT} effort, verify at ${VERIFY_EFFORT}` +
    (TARGET ? `, target ${TARGET}` : ''))
const passes = await parallel(
  Array.from({ length: N }, (_, i) => () =>
    agent(REVIEWER, {
      label: `review:${i + 1}`, phase: 'Review', schema: FINDINGS, effort: EFFORT,
    })))

// A barrier is right here: dedup needs every pass at once, and verifying one
// bug once per pass that found it costs N times as much for one answer.
const all = passes.filter(Boolean).flatMap(p => p.findings || [])
const RANK = { low: 0, medium: 1, high: 2 }
const byVotes = (a, b) => b.votes - a.votes || RANK[b.severity] - RANK[a.severity]

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

// Verification earns its cost on a finding that is severe or corroborated; on a
// lone low-severity doc nit it is a full agent spent confirming a typo. Those
// are still REPORTED, just unverified — visible and cheap, rather than
// invisible or expensive.
//
// The cut is deliberately NOT on votes alone. On the run that motivated it, the
// most valuable finding — a real regression the author had just introduced —
// was single-vote medium, and a `votes >= 2` gate would have dropped it.
//
// VERIFY_CAP bounds what one skeptic is asked to hold at once. Past it the
// findings are reported unverified rather than dropped.
//
// Eviction is severity-major, NOT `byVotes`: votes-first put ten corroborated
// lows ahead of a lone high and evicted the high, reinstating through the cap
// the votes-only gate the paragraph above rejects. Report order stays
// votes-first — that is about reading order, not about what gets checked.
const worthVerifying = f => f.severity !== 'low' || f.votes >= 2
const bySeverity = (a, b) => RANK[b.severity] - RANK[a.severity] || b.votes - a.votes
const ranked = deduped.filter(worthVerifying).sort(bySeverity)
const toVerify = ranked.slice(0, VERIFY_CAP)
const belowGate = deduped.filter(f => !worthVerifying(f))
  .map(f => ({ ...f, unverifiedBecause: 'below the verify gate' }))
const pastCap = ranked.slice(VERIFY_CAP)
  .map(f => ({ ...f, unverifiedBecause: `past VERIFY_CAP (${VERIFY_CAP})` }))
log(`verifying ${toVerify.length} of ${deduped.length}; ${belowGate.length} below the gate, ${pastCap.length} past the cap reported UNVERIFIED`)

phase('Verify')
// ONE skeptic for the whole list, not one per finding. Two reasons. The agent
// count becomes `N + 1` and known before the run instead of `N + D` discovered
// during it. And findings cluster in the same few files, so a shared context
// reads each file once where D separate agents each re-read it.
const listed = toVerify.map((f, i) =>
  `[${i}] ${f.file}:${f.line} (${f.severity}, ${f.votes} vote(s))\n` +
  `Claim: ${f.summary}\nScenario given: ${f.scenario}`).join('\n\n')

const panel = toVerify.length === 0 ? { verdicts: [] } : await agent(
  `Try to REFUTE each finding below. Read the code around each one and decide whether the
failure genuinely occurs. Default to refuted=true when you are unsure a finding is real.

Judge each on its own evidence — they come from different reviewers and one being wrong
says nothing about the next. Return exactly one verdict per finding, keyed by its [index].

${listed}`,
  { label: `verify:${toVerify.length}`, phase: 'Verify', schema: VERDICTS,
    effort: VERIFY_EFFORT })

// The join is on a number the model chose, so it is checked before it is
// trusted. Omission is survivable — those findings go back unverified. A
// duplicate or out-of-range index is not: it means the numbering itself is
// unreliable, and a 1-based reply would otherwise hand every finding its
// PREDECESSOR's verdict, silently confirming what was refuted. So the whole
// mapping is discarded and the batch is reported unverified.
const raw = panel?.verdicts || []
const inRange = v => Number.isInteger(v.index) && v.index >= 0 && v.index < toVerify.length
const seen = new Set()
const dupe = raw.some(v => seen.size === seen.add(v.index).size)
const trustworthy = raw.every(inRange) && !dupe
if (!trustworthy) {
  log(`VERDICTS DISCARDED: ${raw.length} for ${toVerify.length} findings, ` +
      `${dupe ? 'duplicate index' : 'index out of range'} — numbering is unreliable`)
} else if (raw.length !== toVerify.length) {
  log(`${toVerify.length - raw.length} of ${toVerify.length} findings got no verdict`)
}

// A finding the skeptic skipped has no evidence either way, so it is neither
// confirmed nor refuted — `refuted` means the skeptic killed it, and reporting
// an unjudged finding that way buries it in a one-line list.
const verdictBy = trustworthy ? new Map(raw.map(v => [v.index, v])) : new Map()
const judged = toVerify.map((f, i) => ({ ...f, verdict: verdictBy.get(i) || null }))
const unanswered = judged.filter(f => !f.verdict)
  .map(f => ({ ...f, unverifiedBecause: trustworthy ? 'no verdict returned' : 'verdicts discarded' }))

const confirmed = judged.filter(f => f.verdict && !f.verdict.refuted)
return {
  // Returned so step 5 records the level the run ACTUALLY used, not the one the
  // caller meant to send — they differ exactly when the args were malformed,
  // which is the case where a wrong ledger row would be least noticed.
  effort: EFFORT,
  passes: N,
  target: TARGET || null,
  found: deduped.length,
  confirmed: confirmed.length,
  findings: confirmed.sort(byVotes),
  unverified: [...belowGate, ...pastCap, ...unanswered].sort(byVotes),
  refuted: judged.filter(f => f.verdict && f.verdict.refuted),
}
```

## Notes

- **Report the refuted findings too**, briefly. A finding the verifier killed is information about
  the reviewers, and hiding it makes the confirmed count look better than it is.
- **Report the unverified ones as unverified**, and say which kind each is — `unverifiedBecause`
  distinguishes below-the-gate, past-the-cap, and the skeptic answered but not for this one.
  No verdict means "confirmed" and "refuted" both misdescribe it; silently dropping them would be
  the failure the `Workflow` guidance names, a bounded pass that reads as full coverage.
- **A discarded batch is loud.** Duplicate or out-of-range indices in the skeptic's reply mean its
  numbering cannot be trusted, so the whole mapping goes and every finding is reported unverified,
  with `VERDICTS DISCARDED` in the log. Guessing at the offset would confirm what was refuted.
- **`found` counts all distinct findings**, verified or not, so the ledger's denominator does not
  shrink when the cap is tightened. Only `confirmed` moves with the verifier.
- **`votes` is repetition, not truth, and not even agreement.** Dedup pools all passes before
  clustering, so it counts how many times a bug was *reported*, not how many passes reported it —
  one pass naming it twice scores 2. It makes a finding worth reading first and nothing more; the
  verify phase is what decides whether it is right.
- **The agent count is at most `N + 1`,** known before the run — `N` when nothing clears the verify
  gate, since the skeptic is then skipped. It was `N + D` at `N = 8`, where eight reviewers finding
  two apiece meant two dozen agents: nothing bounded the second phase.
- **The cap evicts by severity, the report sorts by votes.** Two different questions — what is most
  worth checking, and what is most worth reading first. Using one comparator for both is how a lone
  `high` ended up evicted in favour of ten corroborated `low`s.
- **Say the level in the report**, next to the counts. "3 findings" from one `low` pass and from
  eight `max` ones are different claims about the diff, and only one of them is worth trusting when
  it says nothing was found. The ledger records it for the same reason.
- **A level is a cost ladder, not a quality ladder.** `max` buys more passes and more reasoning per
  pass; it does not widen the verify cap, and nothing has measured what the extra passes find.
  Do not describe a `max` run as "thorough" — describe it as eight passes.
- The ledger lives at `$GIT_DIR/claude-review/runs.tsv` — per-worktree, invisible to `git status`,
  append-only so two concurrent sessions cannot clobber each other, and eight columns wide since
  the level joined it (older seven-field rows still parse). See `scripts/review_ledger.sh`.
