# Codeberg PR workflow

Operational reference for opening, monitoring, and merging pull requests
against this repo on **Codeberg** (Forgejo-based forge, *not* GitHub).
The `gh` CLI will not work here — use `tea` (Gitea CLI) for most
operations, falling back to the Codeberg/Forgejo REST API directly when
`tea` lacks a feature (most notably: `fast-forward-only` merge).

For autonomous monitoring + ff-merging + the standard "master moved →
rebase + seed regen → requeue" cycle, the
[`codeberg-merge` skill](../.claude/skills/codeberg-merge.md) is a thin
orchestrator over the recipes below.

## Setup (one-time, per machine)

The recipes in this doc reference three environment variables that
identify forge coordinates and the tea config location. They live in
`.codeberg.config` at the repo root, which is **gitignored**. Copy the
example template and fill in your values:

```sh
cp .codeberg.config.example .codeberg.config
# edit .codeberg.config
```

Before running any recipe below, source the file:

```sh
source .codeberg.config
```

That exports:

- `CODEBERG_OWNER` — repo owner (Codeberg user/org).
- `CODEBERG_REPO` — repo name.
- `TEA_CONFIG_PATH` — path to your tea config file. The API token lives
  there and is read at runtime; it is *not* copied into `.codeberg.config`.

## Authentication

Confirm `tea` is already configured:

```sh
tea login list
```

Look for a `codeberg.org` entry. If missing, run `tea login add` and
follow the prompts.

For direct API calls, extract the token from the tea config at runtime:

```sh
source .codeberg.config
TOKEN=$(grep -A2 'name: codeberg.org' "$TEA_CONFIG_PATH" | grep token | cut -d: -f2 | tr -d ' ')
curl -H "Authorization: token $TOKEN" ...
```

Do not paste the token into commit messages, PR bodies, or anything
that lands on remote.

## Open a PR

> **Fastest path — one command (works from a worktree):**
> ```sh
> scripts/codeberg/pr-create.sh "<short title under 70 chars>" /path/to/pr_body.md
> ```
> It reads `.codeberg.config` + the token via `scripts/codeberg/_lib.sh`, checks
> whether an open PR already exists for the current branch (idempotent — prints
> its URL and exits), pushes the branch, and POSTs to `/pulls` with the body via
> `jq --rawfile` (correct markdown escaping). Base defaults to `master`. Prefer
> this over the manual steps below — it encodes the worktree/curl/idempotency
> traps. The manual `tea pr create` / raw-curl instructions remain as reference
> and fallback.

> **Use `tea pr create` (alias for `tea pulls create`). Do NOT use `tea api`.**
> In tea **0.14.1** the `tea api` subcommand is broken — it returns
> `404 page not found` for *every* endpoint (including valid ones like
> `/user`) even with a working token. Diagnostic: `tea repos list` succeeds
> (token is fine) while `tea api GET /user` 404s and an *unauthenticated*
> `curl .../api/v1/user` returns 401 — proving it is tea's path handling, not
> auth. If you need raw API calls, use `curl` with the token extracted per the
> "Prerequisites" section, not `tea api`.

```sh
# Branch must already be pushed to the remote first:
git push -u origin <branch-name>

# From the MAIN checkout (see the worktree note below):
tea pr create \
  --login codeberg.org \
  --base master \
  --head <branch-name> \
  --title "<short title under 70 chars>" \
  --description "$(< /path/to/pr_body.md)"
```

The command prints the new PR (number + URL) on success; note the `#<N>`.

**Body from a file.** For anything longer than a couple of bullets, write the
markdown to a file and pass `--description "$(< file.md)"` — the `$(< file)`
shell builtin reads it without invoking `cat` (the repo's Bash file-ops hook
blocks `cat <file>`). An inline heredoc — `--description "$(cat <<'EOF' … EOF)"`
— also works (heredoc `cat` has no file argument, so the hook allows it).

**Worktrees.** `tea pr create` **always** fails from a linked git worktree with
`Error: local repository required: execute from a repo dir, or specify a path
with --repo` — the worktree's `.git` is a pointer file that tea's go-git can't
open. **`-r <owner>/<repo>` does NOT fix this** (verified 2026-07-13): `pr
create` still opens the local repo to read the head branch, so the slug override
fails identically; only read-only commands like `tea pr ls --repo …` work
without a local repo. If you cannot run from the main checkout, create the PR via
a raw curl POST to the `/pulls` endpoint (raw curl works; only `tea api`'s path
handling is broken):

```bash
git push -u origin <branch>
# $TOKEN extracted per the "Prerequisites" section (from $TEA_CONFIG_PATH)
jq -Rs --arg t "TITLE" --arg h "<branch>" --arg b master \
  '{title:$t, body:., head:$h, base:$b}' pr_body.md > payload.json
curl -sS -X POST "https://codeberg.org/api/v1/repos/$CODEBERG_OWNER/$CODEBERG_REPO/pulls" \
  -H "Authorization: token $TOKEN" -H "Content-Type: application/json" \
  -d @payload.json | jq '{number, html_url, state}'
```

## Check PR status

```
GET /api/v1/repos/$CODEBERG_OWNER/$CODEBERG_REPO/pulls/<N>
```

Key fields:

- `state` — `open` / `closed`
- `merged` — `true` / `false`
- `mergeable` — Codeberg's cached "can be merged" verdict. **Stale after
  force-push for ~minutes.** Trust `git merge-tree` locally instead.
- `head.sha` — the PR branch tip.
- `base.ref` — usually `master`.

CI status for the head commit:

```
GET /api/v1/repos/$CODEBERG_OWNER/$CODEBERG_REPO/commits/<head-sha>/status
```

Returns `{"state": "pending" | "success" | "failure" | "no-status", ...}`.
`no-status` means CI hasn't been registered for that SHA yet (look
again in ~30s).

One-liner to summarize both:

```sh
source .codeberg.config
PR=<N>
sha=$(curl -sf "https://codeberg.org/api/v1/repos/$CODEBERG_OWNER/$CODEBERG_REPO/pulls/$PR" | jq -r '.head.sha')
ci=$(curl -sf "https://codeberg.org/api/v1/repos/$CODEBERG_OWNER/$CODEBERG_REPO/commits/$sha/status" | jq -r '.state')
echo "head=${sha:0:7} ci=$ci"
```

## Monitor CI to completion

When you want to wait for one or more PRs to finish CI without manual
polling, the standard pattern (which the `codeberg-merge` skill
implements) is below. **Write CI responses to a temp file first, then
jq from the file** — capturing JSON into a bash variable via
`$(curl ...)` and piping back through `echo "$var" | jq` corrupts
multi-line strings (commit messages with newlines trigger the
"control characters from U+0000 through U+001F" jq error). The
file-mediated pattern is what works:

```sh
source .codeberg.config
PRS="29 32"
declare -A prev final
while :; do
  done_count=0
  for pr in $PRS; do
    [ -n "${final[$pr]:-}" ] && { done_count=$((done_count + 1)); continue; }
    tmp=/tmp/pr_${pr}_mon.json
    if ! curl -sf "https://codeberg.org/api/v1/repos/$CODEBERG_OWNER/$CODEBERG_REPO/pulls/$pr" > "$tmp" 2>/dev/null; then
      continue
    fi
    pr_state=$(jq -r '.state // "?"' "$tmp")
    merged=$(jq -r '.merged // false' "$tmp")
    sha=$(jq -r '.head.sha // "none"' "$tmp")
    [ "$sha" = "none" ] && continue
    ci=$(curl -sf "https://codeberg.org/api/v1/repos/$CODEBERG_OWNER/$CODEBERG_REPO/commits/$sha/status" | jq -r '.state // "no-status"')
    cur="pr=$pr_state merged=$merged ci=$ci sha=${sha:0:7}"
    if [ "$cur" != "${prev[$pr]:-}" ]; then
      echo "PR#$pr: $cur"
      prev[$pr]="$cur"
    fi
    if [ "$merged" = "true" ] || [ "$pr_state" = "closed" ] || [ "$ci" = "failure" ]; then
      final[$pr]="$cur"
      done_count=$((done_count + 1))
    fi
  done
  set -- $PRS
  [ "$done_count" -eq "$#" ] && break
  sleep 45
done
for pr in $PRS; do echo "DONE: PR#$pr=${final[$pr]}"; done
```

Tune `45` to ~30s for fast-feedback, up to ~120s to be polite. Typical
CI wall time on this repo is **~40-50 min** (self-hosted runner: full
bootstrap + verify-fixed-point + tests + examples).

> Shell compat: the snippet uses `declare -A` (associative arrays),
> which needs bash 4+ or zsh. macOS's stock `/bin/bash` is 3.2 — if
> you must run this in stock bash, replace `prev[$pr]` / `final[$pr]`
> with per-PR positional vars (`prev29` / `final29` / `prev32` / …)
> and the corresponding `eval` indirection.

## Merge

### tea (default styles)

```sh
tea pr merge --style <style> <pr-number>
```

`tea` exposes: `merge`, `rebase`, `rebase-merge`, `squash`. It does
**not** expose `fast-forward-only` — that style only works via the
direct API call below.

### Fast-forward-only (the strict no-merge-commit, no-SHA-rewrite path)

Required when you want master to literally advance to the PR's branch
tip without creating any new commits. The branch must be a strict
descendant of `master` at the moment the merge actually runs.

> **Re-verify CI ground truth before you POST this merge.** Do NOT merge on
> `pr-monitor.sh`'s `ci=success` event alone — it is a heuristic over Actions
> runs and can fire early in the `setup`-green / `test`-not-spawned window (it
> merged PR#121 prematurely, 2026-07-03; see `feedback_codeberg_merge_reverify_ci`).
> Gate the merge on the combined commit status via the `_lib.sh` helper:
>
> ```sh
> ci_is_green "$SHA" || { echo "CI not green for $SHA — not merging"; exit 1; }
> ```
>
> The combined-status endpoint returns `pending` while any required check is
> unfinished, so requiring `success` here cannot be fooled by that window nor by
> the null-state ghost (null != success).

```sh
source .codeberg.config
TOKEN=$(grep -A2 'name: codeberg.org' "$TEA_CONFIG_PATH" | grep token | cut -d: -f2 | tr -d ' ')
PR=<N>
SHA=$(curl -sf "https://codeberg.org/api/v1/repos/$CODEBERG_OWNER/$CODEBERG_REPO/pulls/$PR" | jq -r '.head.sha')

# Ground-truth gate — see the note above. Requires _lib.sh sourced.
ci_is_green "$SHA" || { echo "CI not green for $SHA — not merging"; exit 1; }

curl -s -X POST "https://codeberg.org/api/v1/repos/$CODEBERG_OWNER/$CODEBERG_REPO/pulls/$PR/merge" \
  -H "Authorization: token $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"Do\": \"fast-forward-only\", \"head_commit_id\": \"$SHA\"}" \
  -w "\nHTTP: %{http_code}\n"
```

Expected:

- `HTTP: 200` (or `204`) and empty body — merge succeeded.
- `HTTP: 500` and `{"message":"","url":"https://codeberg.org/api/swagger"}` —
  the precondition failed (almost always: PR head is not a strict
  descendant of `master` at this moment, i.e. master moved ahead since
  you last rebased). Rebase locally onto current master, force-push,
  retry.
- `HTTP: 422` and `{"message":"[Do]: In", ...}` — the `Do` value isn't
  in the enum. The accepted enum is:
  `merge`, `rebase`, `rebase-merge`, `squash`, `fast-forward-only`,
  `manually-merged`.

### Queue a fast-forward-only merge for auto-execution when CI passes

Instead of polling for CI and racing master, ask Codeberg to schedule
the merge:

```sh
source .codeberg.config
TOKEN=$(grep -A2 'name: codeberg.org' "$TEA_CONFIG_PATH" | grep token | cut -d: -f2 | tr -d ' ')
PR=<N>
SHA=$(curl -sf "https://codeberg.org/api/v1/repos/$CODEBERG_OWNER/$CODEBERG_REPO/pulls/$PR" | jq -r '.head.sha')

curl -s -X POST "https://codeberg.org/api/v1/repos/$CODEBERG_OWNER/$CODEBERG_REPO/pulls/$PR/merge" \
  -H "Authorization: token $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"Do\": \"fast-forward-only\", \"head_commit_id\": \"$SHA\", \"merge_when_checks_succeed\": true}" \
  -w "\nHTTP: %{http_code}\n"
```

`HTTP: 201` = queued. Codeberg waits for CI to go green AND verifies
the PR is still ff-able at that moment. If master moves between
queueing and CI completion, the auto-merge fails silently and the PR
stays open — just re-queue after rebasing.

This is the cheapest pattern for ff-only against a moving `master`
because there is no race window between "CI green" and "merge attempt".

## Common gotchas

### `mergeable: false` after force-push

Codeberg's mergeable cache lags behind the actual git state by several
minutes after a force-push. To check the real state, run locally:

```sh
git fetch origin master
git merge-tree --write-tree --merge-base $(git merge-base HEAD origin/master) origin/master HEAD
```

If the output is a single tree SHA with no `<<<<<<<` / `=======` /
`>>>>>>>` markers, the merge is clean regardless of what the API says.

### Master moves while you wait for CI

This repo's `master` receives commits regularly (docs, REPL fixes,
unrelated work). The window between "rebase onto current master" and
"CI green" can be 40-50 min, during which master can advance several
times. Each advance breaks ff-only mergeability.

Mitigations, in order of preference:

1. Use `merge_when_checks_succeed: true` (above) — atomic at server side.
2. After CI passes, fetch master and check whether the PR is still ff. If
   not, rebase onto current master (the typical change is just the seed
   conflict, see below), force-push, requeue auto-merge.

### Bootstrap seed conflict on rebase

Any rebase that crosses a commit which touched `bootstrap/compile_driver.ll`
will conflict. The seed is a derived artifact and not meaningfully
hand-mergeable. The recipe:

```sh
git rebase origin/master
# stop on conflict, working tree has bootstrap/compile_driver.ll in conflict
git checkout --theirs bootstrap/compile_driver.ll
git add bootstrap/compile_driver.ll
git rebase --continue
# ...repeat for each replayed commit that touched the seed...

# after rebase succeeds:
rm -f build/compile_driver_bin_stage1   # silently-stale-binary trap
scripts/memwatch.sh 4096 1 -- mise exec -- just refresh-seed
git add bootstrap/compile_driver.ll
git commit -m "chore(bootstrap): refresh seed after rebase onto master"
```

The "take theirs at each step, regenerate once at the end" pattern
means intermediate commits in the rebased branch have stale seeds.
That is acceptable — bisecting through the branch is not a typical
workflow on this repo, and the HEAD seed is the only one that matters
for merge.

### Push is rejected after rebase

A rebase rewrites SHAs. To update the remote branch you need a force
push, but a plain `--force` can clobber concurrent work. Always use:

```sh
git push --force-with-lease origin <branch>
```

`--force-with-lease` refuses to push if the remote has moved since you
last fetched — the safety net you want.

### `tea pr merge --style fast-forward-only` returns "is it still open?"

`tea` rejects the style string before even hitting the API (it
validates against its own enum, which lacks `fast-forward-only`). Use
the direct API call above.

### jq parse errors on the PR JSON

Bash variable capture of a multi-line JSON response (`x=$(curl ...)`)
followed by `echo "$x" | jq ...` produces:

```
jq: parse error: Invalid string: control characters from U+0000 through U+001F must be escaped
```

The fix is to write the response to a temp file first, then
`jq -r ... file`. See the Monitor pattern above. Alternatively, pipe
`curl ... | jq ...` directly with no intermediate variable — that
also works.

## Reference

### Endpoints

- `GET /api/v1/repos/$CODEBERG_OWNER/$CODEBERG_REPO/pulls/<N>` — PR metadata.
- `GET /api/v1/repos/$CODEBERG_OWNER/$CODEBERG_REPO/commits/<sha>/status` — CI rollup status for a commit.
- `POST /api/v1/repos/$CODEBERG_OWNER/$CODEBERG_REPO/pulls/<N>/merge` — merge a PR.
- `GET /api/v1/repos/$CODEBERG_OWNER/$CODEBERG_REPO/actions/runs` — list workflow runs (often returns 504; the commit-status endpoint above is more reliable).

### `MergePullRequestOption` schema (POST body for merge)

| Field | Type | Notes |
|---|---|---|
| `Do` | enum | `merge` / `rebase` / `rebase-merge` / `squash` / `fast-forward-only` / `manually-merged` |
| `MergeCommitID` | string | Override merge commit SHA (rare). |
| `MergeMessageField` | string | Merge commit body. |
| `MergeTitleField` | string | Merge commit subject. |
| `delete_branch_after_merge` | bool | Auto-delete head branch on success. |
| `force_merge` | bool | Bypass branch protection. Don't. |
| `head_commit_id` | string | Defensive: refuse merge if head moved from this SHA. |
| `merge_when_checks_succeed` | bool | Queue the merge for after CI passes. |

### Workflow file

```
.forgejo/workflows/ci.yml
```

Sequential steps include: checkout, mise PATH, install clang-16/llvm-16,
restore stage-1 binary cache, `bootstrap-from-seed`,
`verify-bootstrap-fixed-point`, `check-approved-builtins`,
`smoke-shapes`, `bundle-smoke`, `build-fmt-from-seed`, `fmt-check`,
`test-stdlib-stage1`, `compile-examples-stage1`, `run-example-canary`,
`gc-safety-check --strict`.

Logs for failed jobs are NOT directly accessible via the API on
Codeberg's Forgejo instance (the `/actions/runs/<id>/jobs` endpoint
returns 404). To diagnose CI failure, reproduce each step locally —
`fmt-check`, `verify-bootstrap-fixed-point`, `test-stdlib-stage1`, and
`run-example-canary` are the typical suspects.
