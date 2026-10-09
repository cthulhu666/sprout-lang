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
design: N independent passes and one adversarial verify pass over every finding they returned. It
grouped and ranked them too until 2026-09-30, when that was removed rather than retuned (§Why the
clustering went). Its justification is the quality patterns in the `Workflow` tool's guidance, not
fidelity to the original. Sprout-specific review dimensions are still deliberately
absent until the A/B runs — see `BACKLOG.md`.

## What it costs

The first version cost `N + D` agents at `N = 8` — eight reviewers, then one verifier per finding
that cleared the severity/votes cap. D is only known at runtime, so the bill was not knowable
before the run and reached the mid-teens.

It is now **at most `N + 1`**, at a default `N = 3`: four agents, or three when the passes found
nothing at all, since there is then nothing to judge. Three changes got there, and only the first is
a pure reduction:

| change | why |
|---|---|
| `N` 8 → 3 | 8 was picked as a saving against a "the original uses 15" premise that turned out to be false (above). 3 has no measurement behind it either — it is a cost choice, and `BACKLOG.md` says so. |
| one skeptic for all findings, not one each | Bounds the count at `N + 1`. Findings cluster in the same few files, so a shared context reads each file once where D agents each re-read it. |
| the reviewer prompt bounds its search | Per-agent tokens, not agent count. The observed built-in review made 80–95 `Bash` calls; most of a pass's cost is exploration, so the prompt now says to stay inside the diff and what it touches. |

The second change trades independence for cost: one skeptic judging ten findings can carry a
prejudice across all ten, where ten separate ones cannot. The prompt says to judge each on its own
evidence, which is mitigation, not a guarantee.

It also creates a join that per-agent dispatch did not need, and the first review of this change
found three bugs in it. A verdict the skeptic omits leaves its finding **unverified** — not
refuted, which would bury an unjudged high-severity finding in a list the reader is told to skim.
Duplicate or out-of-range indices mean the numbering itself is untrustworthy, so the whole mapping
is discarded and logged rather than applied: a 1-based reply would otherwise give every finding
its predecessor's verdict and confirm exactly what was refuted. And the cap that decides who gets
judged evicts by severity, because sorting it by agreement once evicted a lone `high` in favour of
ten corroborated `low`s.

None of this is measured against finding quality. The agent counts are exact by construction; what
four agents find relative to fifteen is unknown, and is the A/B in `BACKLOG.md`. If the cut costs
real findings, `N` is the dial and the per-finding verifier is in git.

## Why the level is a dial the user turns

`N` was always a dial; until 2026-09-22 only the file could turn it, by being edited. `/code-review`
takes an effort level as its first argument, so this takes the same one, with the same vocabulary
(`low|medium|high|xhigh|max`, `med` abbreviating `medium`) and the same refusal to guess at a token
that looks like a level and is not.

Two decisions in it are worth recording, because both had a plausible alternative.

**The default is `high`, not the session effort.** `${CLAUDE_EFFORT}` is substituted into a skill
body, so inheriting was available and is what `/code-review` does when nothing was ever typed. It
was rejected because it breaks the one thing this skill is for: `review:3` on a branch has to mean
something, and it means less if each of those three ran at whatever `/effort` happened to be set to
that afternoon. A fixed default makes the runs comparable, and `high` is what the skill already did.

**The level moves `N` and the per-agent reasoning effort, and nothing else.** The one threshold it
does *not* move — `VERIFY_CAP` — is a calibration constant that `BACKLOG.md` has an open entry to
measure. A constant that varies with a flag cannot be calibrated, so making it level-dependent would
have quietly closed off the measurement. The visible cost is that `xhigh` and `max` overrun the verify cap and report most
findings unverified; that is the honest reading of "eight passes and one skeptic", and the fix is
the judge panel in `BACKLOG.md`, not a cap that grows to hide it.

The ladder — 1/2/3/5/8 passes — is a cost ladder with no measurement behind it, exactly as `N = 3`
was. It is now five unmeasured numbers instead of one, which makes the A/B below more valuable
rather than less.

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

**The level is an eighth column, appended.** `rv:3` cannot tell three `low` reviews from three
`max` ones, which is the question a reader asks next. It could be appended because every reader —
`count`, `show`, and the status line's own inline `awk` — selects columns by number and stops at
`$7`, so rows written before it exists still parse. `show` reports the *latest* completed run's
level (`review:2 9 found 3 real @max`): averaging levels across runs answers nothing, and "how
hard was the last look" is the question worth answering.

## Why the clustering went

It grouped findings by line proximity plus word-set overlap, and it was **removed rather than
retuned** on 2026-09-30. Two reasons, in order of weight.

**The measure has no separable classes.** `OVERLAP_MIN = 0.5` was calibrated on one run of five
adjacent pairs — duplicates at 0.60/0.67/0.67, non-duplicates at 0.33/0.29 — and its note called
that "a wide gap rather than a knife edge". Real duplicates measured since, across three runs, score
**0.36, 0.40, 0.45, 0.46, 0.53**: they straddle the cutoff and overlap the old non-duplicate band. No
threshold separates these, because two reviewers describing one bug share a *referent*, not
vocabulary — "silently successful truncated result" against "turns a bounds violation into a
truncated Vec" scores 0.40. A model reading the findings resolves referents natively, which is why
the caller does this well and a word-set score cannot.

**Its justification had already been retired.** The threshold sat high because a wrong merge used to
*delete* a finding; once `alsoReported` kept the wording it did not carry, a wrong merge cost a
noisier entry and nothing else. Nobody lowered it afterwards — it guarded a risk that no longer
existed for months.

Run `1790751683-30359` is the one that ended it: 8 reports called 8 distinct issues when there were
4, two reports at an *identical* line split into two entries, a verify slot spent on the duplicate,
and — through the gate the votes fed — the six substantive findings withheld from the skeptic while
the two already-conceded-dead ones were checked.

### Why nobody noticed for three runs

Worth recording separately, because it generalises past this skill. The threshold's calibration note
said *"if a real finding is ever swallowed, raise it and say so here"* — it planned for over-merging
only. Both failures that actually happened went the other way, and that is not chance:

| failure | what a reader sees |
|---|---|
| over-merge | one entry visibly describing two different things |
| under-merge | two plausible entries, each looking like its own finding |

**An under-merge is invisible, so nobody reports it.** A threshold whose two failure modes differ in
how detectable they are will drift toward the detectable one, because that is the only side anyone
files a complaint about. The note asked for exactly the report that could never arrive.

What broke the loop was keeping the raw findings (`review_ledger.sh raw <id>`), which made the
invisible side **measurable on demand** rather than waiting to be noticed. The report beside it never
could: it is the output of the constant under test. The five scores above came from those files, and
they are the whole argument for the removal — the constant was retired by measurement, not by taste.

`VERIFY_CAP = 10` is the last threshold here with that asymmetry: overrunning it is announced in the
log, while a cap set too low just means fewer things were checked. `BACKLOG.md` owns it.

## Why there is a cleanup track

Measured 2026-10-09 over all 11 runs on record, 67 raw findings, classified by hand from their
summaries:

| kind | count |
|---|---|
| correctness bugs | ~50 |
| stale comment, doc, spec prose or PR-body claim | ~10 |
| diagnostic text that blames the wrong thing | 3 |
| missing test | 1 |
| efficiency-shaped (all reported as hangs or wrong behaviour) | 3 |
| reuse or simplification | **0** |

Zero is what the pipeline selects for, not evidence the code was clean: the reviewer prompt says
"prefer real failure modes over style", and the skeptic refutes anything without a failure. So
`/simplify`'s angles find a different set, not an overlapping one.

The track is `/simplify` (Claude Code 2.1.286) with three changes. It reports instead of applying,
because this skill stops at the report. It has Sprout's own forms in the angle text, which is the
part a generic pass cannot know. And its pass count follows the level — one pass holding all four
angles up to `high`, which is `/simplify`'s own fallback shape, and one per angle at `xhigh` and
`max`, which is its normal shape. Nothing has measured whether four passes find more than one.

The built-in `/code-review` already runs cleanup angles at `high` — three cleanup, one altitude and
one conventions, inline in one context. The conventions angle (quote a CLAUDE.md rule, quote the line
that breaks it) is not in this track.

## Reading it

```sh
bash scripts/review_ledger.sh count          # completed runs on this branch
bash scripts/review_ledger.sh show           # "review:2 9 found 3 real @max"
bash scripts/review_ledger.sh findings <id>  # path to that run's findings
bash scripts/review_ledger.sh raw <id>       # path to that run's findings as each pass worded them
just test-review-ledger                      # the suite, also in ci-fast-gates
```

`findings` returns `$GIT_DIR/claude-review/findings-<id>.md`, creating the directory. The skill writes
every finding there — confirmed, unverified and refuted — before reporting, so a review can be
pointed at afterwards instead of recalled. The counts say a review happened; the file says what it
said, and it is where the reports are grouped into issues, since `found` deliberately counts the
former. The path is absolute (`--absolute-git-dir`, not `--git-dir`, which answers `.git` at the root
and an absolute path from a subdirectory).

The status line renders `rv:N` after the branch — green when reviewed, red at `rv:0`, and silent in
repositories that have no ledger at all.
