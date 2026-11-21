# Why `/sprout-review` exists

`/code-review` leaves no trace, so "has this branch been reviewed, and how many times?" cannot be
answered. This skill owns the run: it opens a ledger row before `/code-review` and closes it after,
so the count is recorded, not inferred.

Four ways to recover the count from outside the review failed:

| approach | why it fails |
|---|---|
| parse the transcript | findings are prose; no `ReportFindings` calls in it |
| count `subagents/*.meta.json` | counts forks, not reviews; per session, not per branch |
| group agents by invocation id | there is none |
| a `TaskCompleted` hook | tied to `TaskCreate`, never fires for a review |

## History

Since 2026-11-21 the skill wraps `/code-review`. Earlier ledger rows came from a custom N-pass
ensemble whose `found` counted every pass's reports, refuted included. From the wrapper on, `found`
counts one review's reports with refuted ones already dropped, and `confirmed` is `-` when no verify
pass ran. The ensemble's last version, with its measurements and the A/B against `/code-review`,
is at `297cbed7`.

## Files

- `SKILL.md`: the skill.
- `../../scripts/review_ledger.sh`: the ledger.
- `../../`: the `pr-tracker` plugin, which ships this skill and the `/prs` pane that reads the ledger.
