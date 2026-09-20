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
| count `subagents/*.meta.json` named `code-review` | counts forks, not reviews: a resumed review adds a marker, and the directory is per session, not per branch |
| group agents by a shared invocation id | there isn't one; the marker file holds only `{"forkedSkill":true,"skillName":"code-review"}` |
| a `TaskCompleted` hook | that event is tied to the `TaskCreate` tool, not to agents — it never fires for a review |

The last one is worth dwelling on. `TaskCompleted` is the most plausible-sounding name for "a review
finished", and it is wrong. Wiring it and firing a task proved it silently never fires; a counter
built on it would have read `0` forever, which is worse than absent, because `review:0` looks like an
answer rather than a broken pipe.

`SubagentStop` does fire, but it fires per agent with a partial report, and nothing in the event
says which review it belongs to. Reconstructing "5 findings" from a stream of fragments is exactly
the kind of inference that produces a confident wrong number.

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

This section said, until 2026-09-14, that one invocation fans out to **15 identical reviewers**. That
was wrong, and it was load-bearing — it is why the original eight reviewers read as thrift rather
than as a number someone picked. They are three now (§What it costs).

What the evidence on this machine shows:

| measurement | result |
|---|---|
| `skillName == "code-review"` markers, all retained sessions | 60 across 29 sessions |
| forks vs `/code-review` invocations, per session | tracks 1:1; worst ratio 7:4, the excess being resumes (one marker is named `code-review-2`) |
| `spawnDepth` on every one of them | `1` — no nesting anywhere |
| `Agent`/`Task` tool calls inside a review agent's own jsonl | **zero**; the two runs examined made 80 and 95 `Bash` calls |

So it runs as **one agent doing the review itself**. The original claim came from counting markers in
a session directory, which counts invocations rather than one invocation's fan-out — the same
confusion the table above now records as a failed approach.

The built-in's internals are not visible from here, so nothing stronger than "no fan-out is
observable" is asserted. Retained sessions are also a bounded sample: sessions can be pruned, and the
`ide/saving` review that prompted the original claim may no longer be on disk. The 15 does not appear
anywhere that is.

**What this means for the skill.** `SKILL.md` embeds the original's reviewer prompt, so the *prompt*
is close to a port and the A/B is still worth running. The ensemble around it is this skill's own
design: N independent passes, proximity+overlap dedup, and an adversarial verify bounded to
severe-or-corroborated findings. Its justification is the quality patterns in the `Workflow` tool's
guidance, not fidelity to the original. Sprout-specific review dimensions are still deliberately
absent until the A/B runs — see `BACKLOG.md`.

## What it costs

The first version cost `N + D` agents at `N = 8` — eight reviewers, then one verifier per finding
that cleared the severity/votes cap. D is only known at runtime, so the bill was not knowable
before the run and reached the mid-teens.

It is now **`N + 1`, fixed**, at `N = 3`: four agents. Three changes got there, and only the first
is a pure reduction:

| change | why |
|---|---|
| `N` 8 → 3 | 8 was picked as a saving against a "the original uses 15" premise that turned out to be false (above). 3 is the smallest N where a `votes >= 2` cluster still means two passes agreed. |
| one skeptic for all findings, not one each | Fixes the count at `N + 1`. Findings cluster in the same few files, so a shared context reads each file once where D agents each re-read it. |
| the reviewer prompt bounds its search | Per-agent tokens, not agent count. The observed built-in review made 80–95 `Bash` calls; most of a pass's cost is exploration, so the prompt now says to stay inside the diff and what it touches. |

The second change trades independence for cost: one skeptic judging ten findings can carry a
prejudice across all ten, where ten separate ones cannot. The prompt says to judge each on its own
evidence, which is mitigation, not a guarantee. A verdict the skeptic omits leaves its finding
**unconfirmed** — a truncated reply must not be able to inflate the confirmed count.

None of this is measured against finding quality. The agent counts are exact by construction; what
four agents find relative to fifteen is unknown, and is the A/B in `BACKLOG.md`. If the cut costs
real findings, `N` is the dial and the per-finding verifier is in git.

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
bash scripts/review_ledger.sh count          # completed runs on this branch
bash scripts/review_ledger.sh show           # "review:2 9 found 3 real"
bash scripts/review_ledger.sh findings <id>  # path to that run's findings
just test-review-ledger                      # the suite, also in ci-fast-gates
```

`findings` returns `$GIT_DIR/claude-review/findings-<id>.md`, creating the directory. The skill writes
every finding there — confirmed, unverified and refuted — before reporting, so a review can be
pointed at afterwards instead of recalled. The counts say a review happened; the file says what it
said. The path is absolute (`--absolute-git-dir`, not `--git-dir`, which answers `.git` at the root
and an absolute path from a subdirectory).

The status line renders `rv:N` after the branch — green when reviewed, red at `rv:0`, and silent in
repositories that have no ledger at all.
