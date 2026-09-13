# Why `/sprout-review` exists

`SKILL.md` is the skill. This file is why it was written rather than using the built-in
`/code-review`, and what was measured to get there. `BACKLOG.md` next to it is its open work.

## The problem

`/code-review` works. It reviewed the `ide/saving` change and found two real bugs I had argued were
not there. Nothing here is a complaint about its findings.

The problem is that it leaves **no trace**. After a review you cannot answer "has this branch been
reviewed, and how many times?" — which is the question you want answered when a PR is about to
merge. Four attempts to recover that from outside the review all failed:

| approach | why it fails |
|---|---|
| parse the transcript | 53 MB, and the findings are prose — there are zero `ReportFindings` calls in it |
| count `subagents/*.meta.json` named `code-review` | one invocation produced **15** of them |
| group agents by a shared invocation id | there isn't one; the marker file holds only `{"forkedSkill":true,"skillName":"code-review"}` |
| a `TaskCompleted` hook | that event is tied to the `TaskCreate` tool, not to agents — it never fires for a review |

The last one is worth dwelling on. `TaskCompleted` is the most plausible-sounding name for "a review
finished", and it is wrong. Wiring it and firing a task proved it silently never fires; a counter
built on it would have read `0` forever, which is worse than absent, because `review:0` looks like an
answer rather than a broken pipe.

`SubagentStop` does fire, but 15 times per review, each with a partial report. Reconstructing "5
findings" from 15 fragments is exactly the kind of inference that produces a confident wrong number.

## The fix: own the run

A skill knows when it starts and when it finishes. So the count stops being *inferred* and becomes
*recorded* — `open` a ledger row before reviewing, `done` after. No hooks, no heuristics, no
grouping problem. This is strictly less machinery than any of the four attempts above.

Owning the skill also fixed a second thing for free. The built-in's agents are told to submit
findings via `ReportFindings`, and in practice they report:

> "No `ReportFindings` tool is available in this session, so here are the findings directly."

— and fall back to prose. A workflow's `agent(..., {schema})` forces validated structured output, so
ours gets typed findings without needing that tool at all.

## What the original actually does

Recovered from a review agent's own transcript rather than guessed: one invocation fans out to **15
identical reviewers**, not 15 specialists. Every one receives the same prompt, labelled
`` `minimal prompt → single careful diff pass → ≤15 findings` ``. The ensemble buys sampling
diversity, and the results are aggregated afterwards.

`SKILL.md` reproduces that shape and embeds that prompt verbatim. It is a **faithful port** on
purpose: the original is a working baseline, and a port you cannot A/B against it is just a rewrite
you hope is equivalent. Sprout-specific review dimensions are deliberately absent until the port is
known to match — see `BACKLOG.md`.

The one deviation is **8 reviewers instead of 15**, for cost. It is a dial, and it is the first
thing to change if ours finds less than the built-in.

## Why the ledger looks the way it does

**Append-only TSV, not JSON.** Two sessions can review one worktree at once, and an append needs no
read-modify-write, so they cannot clobber each other. The status line reads it on a 300 ms debounce
and must not parse anything — one `awk`, no dependency.

**Under `$GIT_DIR`.** Per-worktree automatically, and invisible to `git status`, so a review can
never dirty a commit or collide in a merge. Same place `review_gate.py` keeps its state.

**Deduplicated by run id.** A `done` row can be written twice — a retry, a resumed workflow — and
the count must not move. This was not hypothetical: the first implementation deduplicated the run
count correctly and then summed `found`/`confirmed` over *every* row, so one re-reported 7-finding
review read as `16 found 6 real`. `scripts/test_review_ledger.sh` pins both halves.

**A `start` row is not a review.** An opened-but-unclosed run shows `rv:0`. Beginning a review is
not having had one.

## Reading it

```sh
bash scripts/review_ledger.sh count   # completed runs on this branch
bash scripts/review_ledger.sh show    # "review:2 9 found 3 real"
just test-review-ledger               # the suite, also in ci-fast-gates
```

The status line renders `rv:N` after the branch — green when reviewed, red at `rv:0`, and silent in
repositories that have no ledger at all.
