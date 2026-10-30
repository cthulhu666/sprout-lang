# pr-tracker

A side pane (or a band above the prompt) that lists PRs with their CI state, the
`/sprout-review` runs for each, and the issues those runs found with their status.
Companion to `.claude/skills/sprout-review/`.

## Commands

- `/prs` shows it; `/prs pane` and `/prs band` pick the view.
- `/prs add <n…>` and `/prs remove <n…>` edit the list. The list holds the PRs added here plus
  any PR that a review from any session in this repo (the ledger is shared) finished after this
  session started, unless removed.

## Data

- `gh pr view` every 60 s, and after `git push` or `gh pr` in Bash. An open PR's header shows its
  mergeability, and its base branch when that is not `master`.
- The review ledger every 5 s, only through `scripts/review_ledger.sh` (`runs`, `dir`, `issues`),
  run from the repo root.

## Loading

Claude Code loads it as `pr-tracker@skills-dir` in a trusted checkout. No install step.
`.claude-plugin/types/` is written on first load and is gitignored; `tsc -p` needs the plugin to
have loaded once, because `tsconfig.json` extends it.

## Check

- `just test-pr-tracker` (local only; CI has no `claude` CLI)
- `claude plugin validate .claude/skills/pr-tracker`
