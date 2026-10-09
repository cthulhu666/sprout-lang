---
name: sprout-review
description: Review the current diff, or a PR number/branch/path target, for real bugs at a chosen effort level, as `/code-review` does, and record the run in the branch's review ledger so the status line can report how many reviews this PR has had. Invoke when the user asks for a code review of the working tree, the branch, or a PR.
argument-hint: "[low|medium|high|xhigh|max] [<pr#>|<branch>|<path>]"
---

# sprout-review

An ensemble diff review that **records that it ran**: N independent bug passes, one adversarial
verify pass over their findings, C cleanup passes beside them, and a ledger row plus a findings file
on disk either side of it.

It costs at most **N + C + 1 agents**, known before the run — five at the default level, or four
when the bug passes found nothing at all. The effort level moves `N` and `C`; §Arguments has the
table.

Three things follow from that and govern the procedure below. The run is **owned** — the row is
opened before reviewing and closed after, so the count is exact by construction rather than inferred
from hooks. The run **reports, then stops**: the findings are written down and handed over, not acted
on. And the script **does not interpret** — it groups nothing, ranks nothing by agreement and
withholds nothing from the skeptic, because deciding whether two reports describe one bug is a
reading task, and the machinery that tried it got that wrong on every run it was measured on
(§Notes).

The **cleanup track** is `/simplify`'s four angles — reuse, simplification, efficiency, altitude —
with Sprout idioms added, reporting instead of applying. It shares the diff and the fan-out with the
bug passes and nothing else: no skeptic, no cap, no share of `found` or `confirmed`. Other
repo-specific dimensions (GC rooting, seed staleness) are NOT here yet.

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

**What the level moves — three dials, and only three:**

| level | N passes | C cleanup | reviewer effort | agents (max) |
|---|---|---|---|---|
| `low` | 1 | 1 | `low` | 3 |
| `medium` | 2 | 1 | `medium` | 4 |
| **`high`** (default) | **3** | **1** | `high` | **5** |
| `xhigh` | 5 | 4 | `xhigh` | 10 |
| `max` | 8 | 4 | `max` | 13 |

`C = 1` is one pass holding all four angles; `C = 4` is one angle per pass, as `/simplify` runs.

These five rows are a cost ladder, not a measurement — the same caveat `README.md` already records
for `N = 3`, now multiplied by five. `BACKLOG.md` owns closing that.

**What the level does NOT move**, deliberately: `VERIFY_CAP`. It is what one skeptic can hold at
once, which does not grow because more reviewers ran, and it has an open `BACKLOG.md` entry to
measure it — a constant that varies with a flag cannot be calibrated. One consequence is worth
stating plainly: at `xhigh` and `max` the cap binds hard — 8 passes at up to 8 findings each is 64
against a cap of 10 — so most findings come back **unverified rather than
unchecked-and-presented-as-checked**. The fix is more skeptics (the judge-panel entry in
`BACKLOG.md`), not a bigger cap.

**There is no verify gate.** Every finding goes to the skeptic, severity first, until the cap runs
out, so the cap is the only reason one can come back unjudged. §Notes has what the gate cost.

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

The script returns the findings as its passes reported them, each with a verdict. It merges nothing
and counts no agreement, so two reports of one bug arrive as two findings and `found` counts
**reports**. Turning them into issues is step 4's job, and yours. The cleanups come back separately,
as `cleanups`, each with a `category` and no verdict.

**4. Write the findings to disk** before reporting them, at the path the ledger names:

```
bash "$(git rev-parse --show-toplevel)/scripts/review_ledger.sh" findings <run-id>
```

Write every finding there — confirmed, unverified and refuted, each with its file:line, severity
and scenario. **Group them as you write**: two reports of one bug become one entry carrying both
wordings, and state how many reports became how many issues, because `found` counts the former while
a reader wants the latter. This is the step that makes the next one checkable. Findings that
exist only inside a chat message cannot be pointed at afterwards, which is the same failure the
ledger exists to fix, one level down: a count without a list says a review happened but not what it
said.

Write the `cleanups` there too, under their own heading after the bugs, with their category, and
grouped the same way.

**Also write the workflow's `raw` array**, verbatim JSON, to the path `review_ledger.sh raw <id>`
names. That file is the only record of what each pass said in its own words, and it is what made the
clustering constants measurable at all. They were carried for three runs on recollection — "synonyms,
~0.3 overlap" — because the summaries they scored were discarded as soon as they were merged;
measuring them is what ended them. `VERIFY_CAP` is the only constant left to check this way.

**5. Close the ledger row** with the counts the workflow returned, and the level it ran at:

```
bash "$(git rev-parse --show-toplevel)/scripts/review_ledger.sh" done <run-id> <found> <confirmed> <level> <cleanups>
```

`found` is the workflow's `found`: how many findings its passes **reported**, ungrouped, so the
column means one fixed thing in every row. Your own count of distinct issues goes in the findings
file, never here. `confirmed` is how many survived verification.
For `<level>` use the workflow's returned `effort`, not the token the user typed and not what you
resolved in step 1 — the three differ precisely when something went wrong, and the returned one is
the level the passes actually ran at. The ledger stores only the known vocabulary, so a level it
does not recognise is dropped silently rather than corrupting the row: nothing downstream will
complain about a wrong one. `<cleanups>` is the length of the returned `cleanups`, ungrouped: its
own column, because `found` means bug reports in every row written before cleanups existed.

Close the row even when the count is zero — a review that found nothing still happened, and a
missing row reads as "never reviewed".

**6. Report the findings and STOP.** Most severe first, with the unverified ones marked as such and
the refuted ones listed briefly. Then hand the decision over and wait.

**Lead every finding with its severity chip**, at the very start of the line, before the file:line
and before the summary:

| severity | chip |
|---|---|
| `high` | 🔴 **HIGH** |
| `medium` | 🟠 **MEDIUM** |
| `low` | 🟡 **LOW** |

First position is the whole point: a severity that arrives after a 90-character summary is read at
prose speed, which is to say skipped. Keep both halves — the glyph is what the eye catches running
down the left edge, the word is what survives a copy-paste somewhere that renders no colour.

Severity and verification status are two axes, so never fold them into one chip. A confirmed
`🟡 LOW` and an unverified `🔴 HIGH` are different things to do next, and a reader who cannot tell
them apart has lost the distinction the verify phase was spent on. The status stays a word beside
the chip: `CONFIRMED`, `UNVERIFIED — <reason>`, `REFUTED`.

**Cleanups go after every bug**, in their own section, with the same chips and the category in place
of a status: `🟡 LOW reuse — net.sprout:40 …`. Say once, in the heading, that they are not verified
by design: you are the filter that `/simplify`'s applying agent was.

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
  description: 'Ensemble diff review: N careful reviewers, cleanup passes, adversarial verify',
  phases: [
    { title: 'Review', detail: 'N independent bug passes and the cleanup passes, over the diff' },
    { title: 'Verify', detail: 'one skeptic refutes every finding the cap admits' },
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

// Cleanup passes: /simplify's four angles, report-only. One pass covers all four
// up to high; xhigh and max split them one per pass, as /simplify does. A
// separate track — never verified, capped or counted in `found`/`confirmed`.
const CLEANUP_LADDER = { low: 1, medium: 1, high: 1, xhigh: 4, max: 4 }
const C = CLEANUP_LADDER[EFFORT]

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
const scopeFor = what => TARGET ? `You are reviewing \`${TARGET}\` for ${what}. Resolve it as a PR number, a
branch name, or a file path — in that order — and get the unified diff it names
(\`gh pr diff <n>\`, \`git diff <branch>...HEAD\`, or the file's current contents).
Treat that, and nothing else, as the review scope.` : `You are reviewing a pull request for ${what}. Run \`git diff @{upstream}...HEAD\` (or \`git diff main...HEAD\` / \`git diff HEAD~1\`
if there's no upstream) to get the unified diff under review. If there are
uncommitted changes, or the range diff is empty, also run \`git diff HEAD\` and
include the working-tree changes in scope — the review often runs before the
commit. Treat this diff as the review scope.`

// The built-in reviewer's prompt, with two changes: findings come back through
// the schema instead of ReportFindings, which was not available to the
// original's agents in practice, and the last paragraph bounds the search —
// exploration is where a pass spends its tokens.
const REVIEWER = `${scopeFor('real bugs')}

Review the diff as a careful senior engineer would: read every hunk, open the surrounding files for context as needed (Read, Grep, git log/blame/show), and hunt for correctness issues — wrong or inverted conditions, off-by-one, null/undefined dereference, missing \`await\`, dropped error handling, removed guards or validations, broken callers of changed functions, races. Prefer real failure modes over style; every finding needs a concrete scenario in which the code misbehaves.

Report at most 8 findings. Quality over quantity: include everything you genuinely believe is a real issue, and nothing you don't.

Stay inside the diff and what it touches. Read the changed hunks, the files they are in, and the callers of anything whose signature or behaviour changed. That is the budget — do not survey the repository, re-read a file you have already read, or go looking for pre-existing bugs the diff did not introduce.`

// The angle text is /simplify's (Claude Code 2.1.286), with Sprout's own forms
// added; its closing "apply the fixes" phase is dropped, since this skill reports.
const ANGLES = {
  reuse: `### Reuse
Flag new code that re-implements something the codebase already has. Grep
\`stdlib/prelude.sprout\`, the \`stdlib/\` modules and the files adjacent to the
change, and name the existing helper to call instead.`,
  simplification: `### Simplification
Flag unnecessary complexity the diff adds: redundant or derivable state,
copy-paste with slight variation, deep nesting, dead code left behind. In Sprout
that includes a nested \`match\` that \`let..else\`, a combinator or \`|>\` would
flatten (\`docs/idiomatic-sprout.md\`). Name the simpler form.`,
  efficiency: `### Efficiency
Flag wasted work the diff introduces: redundant computation or repeated I/O,
blocking work added to startup or hot paths, and quadratic building — a
\`list_append(acc, [x])\` in a loop copies \`acc\` every step. Also flag a
long-lived closure that captures a large enclosing scope and keeps it alive.
Name the cheaper alternative.`,
  altitude: `### Altitude
Check that each change fixes the root cause at the right depth rather than
patching a symptom. Special cases layered on shared infrastructure are a sign the
fix is not deep enough — prefer the simpler, more general change to the
underlying mechanism over adding special cases, and name that change.`,
}

// Round-robin, so C = 1 gives one pass with every angle and C = 4 one each.
const KEYS = Object.keys(ANGLES)
const groups = Array.from({ length: C }, (_, i) => KEYS.filter((_, j) => j % C === i))

const cleanupPrompt = keys => `${scopeFor('cleanups')}

You are improving the quality of the changed code, not hunting for bugs — other
reviewers do that. REPORT ONLY: do not edit any file. Skip a finding whose fix
would change intended behaviour or need changes well outside the diff.

${keys.map(k => ANGLES[k]).join('\n\n')}

For each finding give \`file\`, \`line\`, a one-line \`summary\`, its angle as
\`category\`, and in \`scenario\` the concrete cost: what is duplicated, wasted or
harder to maintain, and the form to use instead. \`severity\` is the size of that
cost. Report at most 8 findings.

Stay inside the diff and what it touches. The one search outside it is the Reuse
angle's grep for an existing helper.`

const CLEANUP_FINDINGS = JSON.parse(JSON.stringify(FINDINGS))
CLEANUP_FINDINGS.properties.findings.items.properties.category = { enum: KEYS }
CLEANUP_FINDINGS.properties.findings.items.required.push('category')

phase('Review')
log(`${EFFORT}: ${N} pass(es) at ${EFFORT} effort, verify at ${VERIFY_EFFORT}, ` +
    `${C} cleanup pass(es)` + (TARGET ? `, target ${TARGET}` : ''))
// One barrier for both tracks: the cleanup passes cost no wall-clock beyond the
// slowest bug pass.
const results = await parallel([
  ...Array.from({ length: N }, (_, i) => () =>
    agent(REVIEWER, {
      label: `review:${i + 1}`, phase: 'Review', schema: FINDINGS, effort: EFFORT,
    })),
  ...groups.map(keys => () =>
    agent(cleanupPrompt(keys), {
      label: `cleanup:${keys.join('+')}`, phase: 'Review', schema: CLEANUP_FINDINGS,
      effort: EFFORT,
    })),
])
const passes = results.slice(0, N)

// With one angle per pass the angle is known, so it overrides the pass's label.
// A failed pass is logged: zero cleanups from a pass that never ran is not
// "the code is clean".
const cleanupResults = results.slice(N)
const cleanupFailed = cleanupResults.filter(r => !r).length
if (cleanupFailed) log(`${cleanupFailed} of ${C} cleanup pass(es) failed`)
const rawCleanups = cleanupResults.flatMap((r, i) => (r?.findings || [])
  .map(f => groups[i].length === 1 ? { ...f, category: groups[i][0] } : f))

// A barrier is right here: the skeptic needs every pass's findings at once, and
// one shared context reads each file once where separate agents each re-read it.
const all = passes.filter(Boolean).flatMap(p => p.findings || [])
const RANK = { low: 0, medium: 1, high: 2 }

// NOTHING IS GROUPED HERE, DELIBERATELY. Clustering by line proximity plus word
// overlap used to run between here and the verify phase, and it was removed
// rather than retuned. On run 1790751683-30359 it reported 8 distinct findings
// for 4 real issues and split two reports at an IDENTICAL line, which also cost
// a verify slot on the duplicate. Real duplicates measure 0.36/0.40/0.45/0.46/
// 0.53 across three runs: they straddle the 0.5 floor and overlap the band once
// recorded for non-duplicates, so no cutoff separates the classes. Two reviewers
// describing one bug share a REFERENT, not vocabulary, which is why a reader
// resolves this natively and a word-set measure cannot. Grouping is the caller's.
//
// What went with it: `votes`, `corroboration`, `alsoReported`, LINE_WINDOW,
// OVERLAP_MIN and the verify gate. `found` therefore counts REPORTS, not issues,
// and means one fixed thing across every run; the judged grouping belongs in the
// findings file, where a reader wants it anyway.

// One file, two spellings. An agent returns an absolute path or a repo-relative
// one depending on how it navigated (run 1790183127-58366). Nothing is merged on
// it now, but the findings file is durable and must not mix the two spellings.
//
// No repo root is available in here, so canonicalise by SUFFIX: the relative
// spelling is always a tail of the absolute one, so each path collapses to the
// shortest path in this run that it ends with on a segment boundary. That cannot
// rewrite a genuinely different file, because the whole relative path has to
// match — only a reviewer reporting a bare basename could collide, and that path
// was ambiguous before it got here.
const paths = [...new Set([...all, ...rawCleanups].map(f => f.file))]
const canon = new Map()
for (const p of paths) {
  let best = p
  for (const q of paths) {
    if (q.length < best.length && p.endsWith(`/${q}`)) best = q
  }
  canon.set(p, best)
}
const findings = all.map(f => ({ ...f, file: canon.get(f.file) || f.file }))
const cleanups = rawCleanups.map(f => ({ ...f, file: canon.get(f.file) || f.file }))

// The only ordering in here, and it reads the reviewer's own severity field. It
// took two comparators — one for eviction, one for reading order — while
// agreement had a say in either; with agreement gone they collapse into this.
const bySeverity = (a, b) => RANK[b.severity] - RANK[a.severity]
const ranked = findings.slice().sort(bySeverity)

// EVERY finding is judged, up to the cap. There is no gate. A severity-and-
// agreement gate withheld six of eight findings on run 1790751683-30359 and sent
// the two that were already conceded dead: every finding was a lone low, so both
// of its inputs were degenerate and the ranking they fed was arbitrary. Two of
// the six it hid contradicted a claim written in the PR body.
//
// VERIFY_CAP is now the ONLY reason a finding can come back unjudged, and past
// it a finding is still REPORTED — visible and cheap, rather than invisible.
// Eviction is severity-major: sorting it by agreement once put ten corroborated
// lows ahead of a lone high and evicted the high.
const toVerify = ranked.slice(0, VERIFY_CAP)
const pastCap = ranked.slice(VERIFY_CAP)
  .map(f => ({ ...f, unverifiedBecause: `past VERIFY_CAP (${VERIFY_CAP})` }))
log(`${findings.length} findings from ${passes.filter(Boolean).length} passes; verifying ${toVerify.length}, ${pastCap.length} past the cap reported UNVERIFIED`)

phase('Verify')
// ONE skeptic for the whole list, not one per finding. Two reasons. The agent
// count becomes `N + C + 1` and known before the run instead of `N + D` discovered
// during it. And findings cluster in the same few files, so a shared context
// reads each file once where D separate agents each re-read it.
const listed = toVerify.map((f, i) =>
  `[${i}] ${f.file}:${f.line} (${f.severity})\n` +
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
  found: findings.length,
  confirmed: confirmed.length,
  // Every pass's findings in the reviewer's own words. Step 4 writes these to
  // the ledger beside the report, so the unjudged claims survive too.
  raw: all,
  findings: confirmed.sort(bySeverity),
  unverified: [...pastCap, ...unanswered].sort(bySeverity),
  refuted: judged.filter(f => f.verdict && f.verdict.refuted),
  // Unverified by design: the reader is the filter /simplify's applying agent
  // was. Never part of `found`, `confirmed` or `raw`.
  cleanupPasses: C,
  cleanups: cleanups.sort(bySeverity),
}
```

## Notes

- **Severity leads, as a chip** — 🔴 HIGH / 🟠 MEDIUM / 🟡 LOW at the start of each finding's line,
  per step 6. The whole report is scanned before any of it is read, and the first column is the
  only one that survives that. Status stays a separate word: folding the two axes into one marker
  hides an unverified `high` behind a confirmed `low`.
- **Report the refuted findings too**, briefly. A finding the verifier killed is information about
  the reviewers, and hiding it makes the confirmed count look better than it is.
- **Report the unverified ones as unverified**, and say which kind each is — `unverifiedBecause`
  distinguishes past-the-cap from the skeptic answering but not for this one. No verdict means
  "confirmed" and "refuted" both misdescribe it; silently dropping them would be the failure the
  `Workflow` guidance names, a bounded pass that reads as full coverage.
- **A discarded batch is loud.** Duplicate or out-of-range indices in the skeptic's reply mean its
  numbering cannot be trusted, so the whole mapping goes and every finding is reported unverified,
  with `VERDICTS DISCARDED` in the log. Guessing at the offset would confirm what was refuted.
- **`found` counts reports, not issues**, verified or not, so the denominator never shrinks and
  means the same thing in every row. Only `confirmed` moves with the verifier.
- **Why there is no grouping.** Clustering by line proximity and word overlap was removed rather
  than retuned. It called 8 reports 8 distinct issues when there were 4 (run `1790751683-30359`),
  and split two reports at an *identical* line, wasting a verify slot on the duplicate. Measured
  duplicates score 0.36/0.40/0.45/0.46/0.53 across three runs — straddling the 0.5 floor and
  overlapping the band once recorded for non-duplicates. No cutoff separates them, because two
  reviewers describing one bug share a referent and not vocabulary. A reader resolves that for free.
- **Why there is no gate.** The same run made the cost concrete: every finding was a lone `low`, so
  severity and corroboration were both flat, the ranking they fed was arbitrary, and the two the
  gate admitted were the two the reviewers had already conceded were dead — while two it hid
  contradicted a claim in the PR body. A gate is only as good as the signal it ranks on, and that
  signal goes flat exactly when a diff has no severe bugs, which is most of the time.
- **The agent count is at most `N + C + 1`,** known before the run — `N + C` only when the bug
  passes found nothing at all, since there is then nothing to judge. It was `N + D` at `N = 8`,
  where eight reviewers finding two apiece meant two dozen agents: nothing bounded the second phase.
- **Why cleanups skip the skeptic.** It is told to refute when unsure and judges whether a failure
  occurs; a cleanup has no failure, so it would refute nearly all of them. `/simplify` has no
  verifier either — its applying agent skips weak findings. Here the reader does that.
- **Why cleanups are their own column.** Of 67 findings across the 11 runs on record before the
  track existed, none was reuse or simplification; the reviewer prompt asks for failures, and the
  skeptic refutes the rest. Folding cleanups into `found` would change what every older row means.
- **One comparator, severity.** Eviction order and reading order are the same now. They needed two
  while agreement had a say, and using the wrong one of the pair is how a lone `high` once got
  evicted in favour of ten corroborated `low`s.
- **Say the level in the report**, next to the counts. "3 findings" from one `low` pass and from
  eight `max` ones are different claims about the diff, and only one of them is worth trusting when
  it says nothing was found. The ledger records it for the same reason.
- **A level is a cost ladder, not a quality ladder.** `max` buys more passes and more reasoning per
  pass; it does not widen the verify cap, and nothing has measured what the extra passes find.
  Do not describe a `max` run as "thorough" — describe it as eight passes.
- The ledger lives at `$GIT_DIR/claude-review/runs.tsv` — per-worktree, invisible to `git status`,
  append-only so two concurrent sessions cannot clobber each other, and nine columns wide since
  the cleanup count joined it (older seven- and eight-field rows still parse). See
  `scripts/review_ledger.sh`.
