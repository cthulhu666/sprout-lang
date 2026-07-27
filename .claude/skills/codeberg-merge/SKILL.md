---
name: codeberg-merge
description: Shepherd one or more open Codeberg PRs through CI to a fast-forward merge by running the scripts/codeberg/pr-babysit.sh orchestrator. It CI-gates on the combined commit status, fast-forward-merges, and autonomously handles the "master moved during CI → rebase (+ regenerate the bootstrap seed) → requeue" cascade, serialized across PRs. Skips draft / WIP-titled PRs; escalates everything else (CI failure, non-seed conflict, force-push rejected, rebase-cap, missing config) via structured `ESCALATION:` lines. Invoke when the user asks to merge, monitor, babysit, or auto-merge open PRs on this Codeberg repo.
---

# codeberg-merge

Drive one or more open Codeberg PRs all the way to a fast-forward merge with a
**single background invocation** of `scripts/codeberg/pr-babysit.sh`. The script —
not this prose — owns the merge-queue logic: CI-green gating, the fast-forward
merge, the master-moved rebase cascade (including bootstrap-seed regeneration),
serialized merges, and caps. Your job is to launch it once and relay its output.

> This replaced a hand-driven, per-event loop that proved unreliable: it
> mis-handled Gitea's HTTP 500 non-fast-forward signature (escalating a benign
> "needs rebase"), over-deferred merges, and thrashed the cascade. **Do NOT
> reconstruct that loop inline — run the script.**

## Authorisation

When Kuba says **"babysit PRs X-Z"** he means he has **reviewed them and
authorises the merges, INCLUDING PRs you authored this session** (memory:
[[babysit-semantics-and-comms]]). `pr-babysit.sh` merges every PR you pass it on
that basis. If the harness still blocks a self-authored merge, say so in one
plain message immediately — do not silently wait.

## Inputs

`$ARGUMENTS` is a space-separated list of PR numbers. If empty, discover open PRs:

```sh
tea pr list --output simple 2>/dev/null | awk '/^[0-9]/' | cut -d' ' -f1
```

If there are none, print `DONE: no open PRs to handle` and exit 0 (not an escalation).

> **Skill-template gotcha**: the harness substitutes any bare `$N` digit token
> (`$1`, `$2`, …) with the Nth token of `$ARGUMENTS`. **Never write a bare digit
> token** — use `$@`, named vars, or `cut -d' ' -f1`.

## Run it

Launch in the **background** (a real run spans CI + rebases — minutes to hours;
it self-terminates at a 4h wall-clock cap):

```
Bash (run_in_background: true):
  scripts/codeberg/pr-babysit.sh <N> [<M> ...]
```

It sources `_lib.sh`, which loudly escalates if `.codeberg.config` is missing, so
no separate setup step is needed. Optional flags: `--dry-run` (log the decisions
without merging/rebasing — use to preview), `--interval=N` (poll seconds, default
60), `--max-wall=N` (cap seconds, default 14400).

## Read its output

The output is line-oriented. React to the terminal signals; ignore `[babysit]`
progress lines:

- `MERGED: PR#<N> (title)` — landed. Nothing to do.
- `ESCALATION: PR#<N> <reason>` — surface to Kuba **verbatim**. That PR is halted;
  the others keep going.
- `DONE: babysit complete — merged=.. skipped=.. escalated=.. pending=..` — the
  final line, and the run's exit code is 0 iff nothing escalated or was left
  pending.

When the background run finishes, summarise for Kuba: what merged, what was
skipped (draft/WIP), and every `ESCALATION:` line with a suggested next step.

## What it handles autonomously (do not intervene)

- **CI gate on ground truth**: merges only when the *combined commit status* is
  `success` (`ci_is_green`), never on the Actions-run heuristic — so it can't
  merge in the setup-green / test-not-spawned window.
- **Fast-forward-only merge** with the FULL head SHA (a truncated SHA spuriously
  409s).
- **Master-moved race**: a non-success merge is NOT judged by HTTP code (Gitea
  returns 500, not 405/409, for a non-ff ff-only merge). It diagnoses with
  `git merge-base --is-ancestor` → retry (transient), recheck (already in
  master), or rebase via `pr-rebase.sh` (which regenerates the bootstrap seed
  iff compiler source changed) then re-gates. Merges are **serialized** so the
  cascade resolves in order instead of thrashing.
- **Caps**: ≤3 rebases and ≤3 transient merge-retries per PR, plus the wall-clock
  cap. A breach escalates that PR only.

## Escalate (surface to Kuba, don't auto-handle)

Everything the script emits as `ESCALATION:`: CI failure, non-seed rebase
conflict, force-push rejected, rebase-cap exhausted, seed-regeneration failure,
closed-without-merge, wall-clock cap, missing config. Halt that PR; the rest
proceed.

## Internals (reference; don't reinvent)

- `scripts/codeberg/pr-babysit.sh` — the orchestrator; this skill's entrypoint.
- `scripts/codeberg/pr-rebase.sh` — rebase + seed regen + force-push (called by the orchestrator).
- `scripts/codeberg/pr-monitor.sh` — standalone CI/state event stream (not needed for merging; useful for passive watching).
- `scripts/codeberg/_lib.sh` — shared helpers (`ci_is_green`, `is_merge_success`, `ci_from_tasks`, `pr_snapshot`, …).
- `scripts/codeberg/pr-create.sh` — PR creation.
- Unit tests: `scripts/codeberg/pr-babysit-test.sh`, `pr-monitor-test.sh` (pure/offline).
- `docs/codeberg-pr-workflow.md` — full protocol context.

## Hard rules (do not violate)

- Prefer the script; **do NOT hand-drive the merge/rebase loop inline.**
- Never `git push --force` (the script uses `--force-with-lease`).
- Never pass `--no-verify`, never modify `git config`, never delete a remote branch.
- During a rebase only `bootstrap/compile_driver.ll` may be auto-resolved (`--theirs`).
- Draft / WIP-titled PRs are **skipped, not escalated** ([[feedback_skip_wip_prs_in_automation]]).
- `just refresh-seed` is always wrapped by `scripts/memwatch.sh 4096 1 --` inside `pr-rebase.sh` ([[feedback_memory_watchdog]]).

## Escalation contract

Every escalation line starts with `ESCALATION:` followed by `PR#<N>` (or `setup`),
then the reason. Grep `ESCALATION:` for triage. Halt that PR; continue the rest.
