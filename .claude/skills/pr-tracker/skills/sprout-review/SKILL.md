---
name: sprout-review
description: Wrapper around `/code-review` that records each run in the branch's review ledger, so the pr-tracker pane (`/prs`) can show how many reviews a PR has had. Invoke when the user asks for a code review of the working tree, the branch, or a PR.
argument-hint: "[low|medium|high|xhigh|max] [<pr#>|<branch>|<path>] [--comment]"
---

# sprout-review

Runs `/code-review` and records the run: a ledger row opened before, closed after, plus a findings
file and an items file. It adds no review knowledge of its own. `/code-review` runs forked and sees
none of this skill's text, so nothing here can steer it.

Why it exists, and the history of the ensemble it replaced: `README.md` beside this file.

## Arguments

```
/pr-tracker:sprout-review [low|medium|high|xhigh|max] [<pr#>|<branch>|<path>] [--comment]
```

This invocation's arguments, verbatim (empty when none were given): `$ARGUMENTS`

- **Level:** the first token, if it names a level. `med` means `medium`.
- A first token that is a number or contains `/`, `.` or `-` is a target. Any other token that
  is not a level but starts with one, or is `mid` (`higher`, `maximum`), is a misspelt level: say
  so and stop. A branch like `lowercase` needs the level first: `/pr-tracker:sprout-review high lowercase`.
- `ultra`: say so and stop. It is a billed cloud review, and the ledger stores only five levels.
- `--fix`: say so and stop. This skill reports; it does not fix.
- `--max-findings`: say so and stop. It is sticky inside `/code-review`, so it would make later
  runs' `found` incomparable.
- **Default level is `high`**, always passed to `/code-review` explicitly. A level that follows
  `/effort` would let the ledger record a level nobody chose and make runs on one branch
  incomparable.
- `--comment` passes through. The rest is the target.

Validate all of this before the ledger row exists.

## Procedure

1. **Resolve** level, flags and target. Tell the user the level.

2. **Open the row**, before any review work. The script files the row in the repo of the
   session's cwd. The `bash` call must contain no `$(git ...)`: a worktree-isolated session's
   sandbox refuses it.

   ```
   bash ${CLAUDE_PLUGIN_ROOT}/scripts/review_ledger.sh open [--pr <n> | --branch <b>]
   ```

   `--pr <n>` for a PR target, `--branch <b>` for a branch target, no flag for the working tree or
   a path. The row is filed under the reviewed code's branch, not the checked-out one. If `gh`
   fails, `open` writes nothing: say so, and do not retry without the flag (wrong branch). Keep the
   run id it prints. If `open` fails there is no run id: run step 3, skip steps 4 to 6, and in
   step 7 report only, saying the run was not recorded.

3. **Run the review.** Invoke the Skill tool: skill `code-review`, args
   `"<level> [flags] [target]"`. **Wait for its result**, which arrives later as a message. Write
   no file and close no row before it does. If it never arrives or fails, do not run `done`; tell
   the user the row stays open (unfinished).

4. **Write the findings** as markdown. Write it to a scratch file with file tools, then:

   ```
   bash ${CLAUDE_PLUGIN_ROOT}/scripts/review_ledger.sh findings <run-id> --write < <file>
   ```

   Never write the ledger path directly: in a worktree it is inside the main checkout's `.git`.
   Number every finding `n` = 1..k in the order `/code-review` reported them (most severe first).
   Group two reports of one issue into one entry carrying both numbers. Each entry has file:line,
   severity, track, verdict and failure scenario. The header says which fields the reporter
   assigned: always `severity`, and `track` when `/code-review` gave no `category`.

5. **Write the items** as JSON:
   `bash ${CLAUDE_PLUGIN_ROOT}/scripts/review_ledger.sh items <run-id> --write < <file>`. One object per grouped
   issue, with the keys `validate_items` in the plugin's `scripts/review_ledger.sh` checks: `ns`,
   `track`, `severity`, `verdict`, `file`, `line`, `summary`, `decision`. Mapping:
   - `track`: from `category` when present: `cleanup` for reuse, simplification, efficiency,
     altitude or conventions, `bug` for anything else. Without `CLAUDE_CODE_REPORT_FINDINGS`
     `/code-review` returns no category at any level, so the reporter assigns
     it from `failure_scenario`: `bug` when the code does the wrong thing (wrong output, crash, lost
     data, a check that cannot fail); `cleanup` when it works but costs duplication, needless
     complexity, wasted work, a stale comment or doc, or a broken style rule (`conventions`).
   - **Performance**, either way: a cost that grows with input (worse complexity, unbounded memory,
     what a cost gate would catch) is `bug`; a constant-factor waste is `cleanup`. This overrides
     `category: efficiency`.
   - `verdict`: CONFIRMED is `confirmed`; PLAUSIBLE or absent is `unverified`. REFUTED is dropped:
     not in `found`, not in the items file, one line in the findings file under "Refuted".
   - `severity`: `low`, `medium` or `high`, assigned by the reporter from `failure_scenario`.
   - `decision`: always `null`. `/code-review` makes no do/skip call.
   - `summary`: one line, from `short_summary` or `summary`; no tabs or newlines.

6. **Close the row:**

   ```
   bash ${CLAUDE_PLUGIN_ROOT}/scripts/review_ledger.sh done <run-id> <found> <confirmed> <level> <cleanups> <cleanups-confirmed>
   ```

   `found` and `confirmed` are the bug-track reports and the bug-track CONFIRMED ones; `cleanups`
   and `cleanups-confirmed` are the same for the cleanup track. Count reports, ungrouped. When no
   finding carries a verdict (no verify pass ran), pass `-` for both confirmed columns, not `0`:
   `show` would read `0` as false alarms. Close the row even with zero findings.

7. **Report and STOP.** Per finding: `**n.**`, the severity chip (🔴 **HIGH**, 🟠 **MEDIUM**,
   🟡 **LOW**), the status word (`CONFIRMED` or `UNVERIFIED`), then file:line and the summary.
   Bugs first, cleanups in their own section after. Say the level and run id beside the counts.
   Do not fix, commit, amend or push in this turn. A fix pushed before the findings arrive leaves
   the reader a changelog, not a decision (run `1789385000-27845`).

8. **When the user acts on a finding**, record it:
   `bash ${CLAUDE_PLUGIN_ROOT}/scripts/review_ledger.sh status <run-id> <n> <open|fixed|skipped|wontfix> [<sha>]`.
   `fixed` needs the commit sha. Any `n` of a grouped issue sets the whole issue; `issues <run-id>`
   prints the result. Never in the review turn itself.

## Ledger

`$(git rev-parse --git-common-dir)/claude-review/runs.tsv`: one for all worktrees, append-only.
See `scripts/review_ledger.sh` in this plugin.
