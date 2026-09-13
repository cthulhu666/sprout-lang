---
name: sprout-review
description: Review the current diff for real bugs, as `/code-review` does, and record the run in the branch's review ledger so the status line can report how many reviews this PR has had. Invoke when the user asks for a code review of the working tree, the branch, or a PR.
---

# sprout-review

A port of the built-in `/code-review`, with one thing added: it **records that it ran**.

`/code-review` leaves no trace on disk. Its findings arrive as prose, its agents carry no
invocation id, and nothing says how many reviews a branch has had. This skill answers that by
owning the run: it opens a ledger row before reviewing and closes it after, so the count is exact
by construction rather than inferred from hooks.

This is a **faithful port** — deliberately the same review shape as the original, so the two can be
run on the same diff and compared. Repo-specific review dimensions (GC rooting, seed staleness,
idiomatic Sprout) are NOT here yet; add them only after the port is known to match.

## What the original does

One invocation fans out to **15 identical reviewers** — not 15 specialists. Each gets the same
careful prompt; the ensemble buys sampling diversity, and the results are aggregated. That shape is
reproduced below.

## Procedure

**1. Open the ledger row.** Before any review work:

```
bash scripts/review_ledger.sh open
```

Keep the run id it prints. If this fails, say so and continue — a review that cannot be recorded is
still worth doing, but do not silently skip the recording.

**2. Run the review.** Call the `Workflow` tool with the script below. The skill's instructions
telling you to call it are the user's opt-in, so no further confirmation is needed.

Use `N = 8` reviewers by default, or the number the user named. **This is the one deliberate
deviation from the original's 15**, for cost; it is a dial, and it is the first thing to change if
the port finds less than the built-in does.

**3. Close the ledger row** with the counts the workflow returned:

```
bash scripts/review_ledger.sh done <run-id> <found> <confirmed>
```

`found` is the deduplicated finding count before verification; `confirmed` is how many survived it.
Close the row even when the count is zero — a review that found nothing still happened, and a
missing row reads as "never reviewed".

**4. Report the findings** to the user, most severe first. Do not fix anything yet; let them decide.

## The workflow script

Pass this to `Workflow` as `script`, substituting `N`:

```js
export const meta = {
  name: 'sprout-review',
  description: 'Ensemble diff review: N careful reviewers, dedup, adversarial verify',
  phases: [
    { title: 'Review', detail: 'N independent careful passes over the diff' },
    { title: 'Verify', detail: 'try to refute each deduplicated finding' },
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
const byPlace = new Map()
for (const f of all) {
  const key = `${f.file}:${f.line}`
  const prior = byPlace.get(key)
  // Keep the highest severity seen at a place, and count the agreement — a
  // finding several independent passes reached is worth more than a lone one.
  if (!prior) byPlace.set(key, { ...f, votes: 1 })
  else {
    prior.votes += 1
    const rank = { low: 0, medium: 1, high: 2 }
    if (rank[f.severity] > rank[prior.severity]) prior.severity = f.severity
  }
}
const deduped = [...byPlace.values()]
log(`${all.length} raw findings from ${passes.filter(Boolean).length} passes -> ${deduped.length} distinct`)

phase('Verify')
const judged = await parallel(deduped.map(f => () =>
  agent(`Try to REFUTE this finding. Default to refuted=true if you are unsure it is real.

File: ${f.file}:${f.line}
Claim: ${f.summary}
Scenario given: ${f.scenario}

Read the code and decide whether the failure genuinely occurs.`,
    { label: `verify:${f.file}:${f.line}`, phase: 'Verify', schema: VERDICT })
    .then(v => ({ ...f, verdict: v }))))

const confirmed = judged.filter(Boolean).filter(f => f.verdict && !f.verdict.refuted)
return {
  found: deduped.length,
  confirmed: confirmed.length,
  findings: confirmed.sort((a, b) => b.votes - a.votes),
  refuted: judged.filter(Boolean).filter(f => f.verdict && f.verdict.refuted),
}
```

## Notes

- **Report the refuted findings too**, briefly. A finding the verifier killed is information about
  the reviewers, and hiding it makes the confirmed count look better than it is.
- **`votes` is agreement, not truth.** Several passes reaching the same conclusion makes a finding
  worth reading first; it does not make it right. The verify phase is what decides that.
- The ledger lives at `$GIT_DIR/claude-review/runs.tsv` — per-worktree, invisible to `git status`,
  append-only so two concurrent sessions cannot clobber each other. See `scripts/review_ledger.sh`.
