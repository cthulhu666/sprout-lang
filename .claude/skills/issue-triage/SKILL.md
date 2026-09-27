---
name: issue-triage
description: Re-triage the repo's open GitHub issues by complexity and leverage, verifying every claim against source instead of trusting the issue body, and update the untracked issue-triage.md at the MAIN checkout root. Invoke when the user asks to triage, re-triage or rank the open issues, to pick what to work on next, or to update the triage doc.
argument-hint: "[full|delta] [<issue#>...]"
---

# issue-triage

Rates every open issue on two axes — **Complexity** (work + risk + gates) and **Leverage**
(impact) — and writes the result to `issue-triage.md`.

The rating is not the product. **The verification is.** An issue body is a claim by whoever filed
it, and the last three passes each found a body that source contradicts: #372 opened by asserting
that base64url fed to the standard decoder is silently corrupted, when `base64_value` returns -1
for both `-` and `_` so both directions fail loudly through a `Result`. That issue was ranked
above others on a hazard it does not have. A triage that copies issue bodies into a table adds
nothing a `gh issue list` does not already give.

So the procedure below spends most of its budget opening the files each issue cites, and it has a
section — **Corpus fixes** — whose whole content is issues whose own text is wrong.

## Arguments

```
/issue-triage [full|delta] [<issue#>...]
```

**`delta` is the default.** It carries the previous ratings forward as *input*, fully rates issues
that are new since the last run, drops issues that closed, and re-verifies a carried issue only
when the files it cites have changed since the previous run's date. That last clause is the point:
a rating is a claim about source, so it expires when the source moves. #356 was rated before #354
landed; once `instance Eq Bytes` existed, the obvious tool for a constant-time compare became the
*leaky* one and the entry needed rewriting, with nothing about #356 itself having changed.

**`full`** re-verifies and re-rates everything, ignoring the prior ratings. Use it when the file
is older than a few days, after a release, or when you do not trust the last pass.

**Explicit issue numbers** triage only those, and merge them into the existing file. Everything
else is carried untouched.

A first token that is neither `full`, `delta` nor an issue number is an error: **say so and stop**
rather than guessing, because the difference between the modes is how much of the file is trusted.

## Where the file lives, and why it is easy to get wrong

```
MAIN="$(dirname "$(git rev-parse --path-format=absolute --git-common-dir)")"
TRIAGE="$MAIN/issue-triage.md"
```

**Not `git rev-parse --show-toplevel`.** In a worktree that resolves to the worktree, and the file
belongs at the main checkout root, where it survives the worktree being pruned — which is how the
first version of it was lost and had to be recovered from a session transcript.

**It is untracked and NOT gitignored** (`git check-ignore issue-triage.md` exits 1). So it appears
in `git status` in the main checkout, and it must never be staged or committed. Do not "fix" its
visibility by adding it to `.gitignore` either: that is a versioned file, and a local scratch path
does not belong in one. If the noise ever becomes a problem, say so and let the user decide.

If the file does not exist, create it — a first run is `full` whatever was asked for, and say that.

## Procedure

**1. Resolve the mode and the path.** Read the existing file if there is one. Its ratings are the
prior state; its dates tell you what "since the last run" means. Tell the user the mode, the issue
count and the agent count before spending any.

**2. List the open issues.**

```
gh issue list --state open --limit 100 --json number,title,createdAt,labels
```

`gh issue view <n>` has returned **empty output** in this repo's sessions, both to a terminal and
redirected to a file, while `gh issue view <n> --json body` works. Read bodies through `--json`
and do not spend a turn diagnosing the blank one.

**3. Reconcile the three sets** — new, closed, carried — and keep the closed ones visible. A
closed issue that led the previous suggested order is information: the entry for it comes out of
the table, and a line above the table says it closed and what that settles. #341 closing turned
#342's motivation from *predicted* into *answered*, which lowered #342's leverage without anyone
touching #342.

**4. Verify, and this is where the budget goes.** For each issue needing it: read the body, then
open every file and symbol it cites and check the claim actually holds.

Group the issues by **subsystem** and give one agent each group — `crypto`/`bytes`, `http_server`,
`net`, `json`, the compiler, `task`. One agent reading one area's source once beats one agent per
issue re-reading the same files, the same argument the review skill makes for a single skeptic.
State the group count before spawning; it should be about four to six, not one per issue.

Each group reports, per issue: does the cited code say what the body says; are the cited locations
right; what would actually have to change; and which gates that path triggers.

**5. Rate. Complexity is mostly path→gates**, not lines of code, because the Definition of Done in
`AGENTS.md` is a cost table keyed on path:

| path touched | gates it drags in |
|---|---|
| `docs/`, `examples/` | fmt, and a verification matching the change |
| `stdlib/*.sprout` | + full `just test`, `compile-examples-stage1`, reseed-or-ack, golden IR |
| `runtime/*.c` | + `APPROVED_BUILTINS`, example canary, `linux-smoke`, **user approval** |
| `stdlib/compiler/` | + smoke shapes, bundle smoke, full `refresh-seed`, golden IR |
| new builtin, or language semantics | + a design doc and approval **before** editing |

Scale: `Low`, `Low-Med`, `Med`, `Med-High`, `High`. `Blocked` is not a complexity — use it when a
prerequisite makes the issue un-landable *as filed*, and name the prerequisite.

**Leverage** is who is unblocked times how often. There are two real consumers, `uncharted-suns`
(the game: loam/gfx/tasks/linalg) and `repbit` (the web PWA: http_server/json/crypto/bytes), so
"who asked" is answerable rather than hypothetical. Mark a `High` that is strategic rather than
immediate as `High (strategic)` — #332 unblocks a shape of project, not a waiting caller.

**6. Split an issue whose halves differ.** #373 was one filing holding a `Low` request-side
accessor pair behind a response-side half that cannot be correct while `HttpServerResponse` holds
one `Dict String`. Rate it as `373a` and `373b` and say so in the findings, because a single row
would have to pick one of two honest answers.

**7. Cross-check `BACKLOG.md`.** Work here is tracked in **both** places, so an open issue can
duplicate a backlog entry, or be blocked by one, and a triage that reads only the issue list will
rank a duplicate as new work. Grep the backlog for each issue's subject before rating it, and note
the overlap in the `Why` column. Issue #378's driver shape is already a `P3` backlog entry, with
the same blocker recorded.

**8. Write the file,** in this shape (keep it — the point of a fixed shape is that two runs are
comparable):

- Title with **today's date**, the open-issue count, and one line on method.
- The untracked/not-gitignored warning, and a provenance line: **append** to the history rather
  than replacing it, so drift is visible.
- Closed-since note.
- `## Ratings` — a table `# | Title (short) | Complexity | Leverage | Why`. The `Why` column
  carries the verification, not a paraphrase of the title: what the change actually is, which
  in-module template it mirrors, and any silent-failure risk.
- `## Cross-issue findings` — bold-titled paragraphs for anything true of several issues at once:
  a keystone that shrinks others, an ordering forced by a dependency, a stated hazard that does
  not survive the source.
- `## Suggested order` — numbered batches, batched by **gate cycle** rather than by size, since
  five `stdlib/*.sprout` items share one reseed and one golden-IR run.
- `## Corpus fixes` — issues whose own bodies are wrong: stale references to landed work, line
  numbers off by a line or two, claims source contradicts.

Write the count as a numeral. A spelled-out "Fifteen open issues" is a claim in prose that goes
stale silently, the same failure as a comment that outlives what it describes.

**9. Report** the mode, the counts, and **what moved** — a rating that changed, and why, is the
only part a reader of the previous version has not already seen. Then stop.

## What this skill must not do

- **Never stage or commit `issue-triage.md`.** It is deliberately untracked local scratch.
- **Never edit an issue on GitHub.** Corpus fixes are *reported*; rewriting someone's filing, or
  closing an issue because the triage judged it low, is the user's call. Offer, then wait.
- **Never rate from the body alone.** If a claim could not be checked, say the entry is unverified
  and why. An unverified rating labelled as such is useful; one presented as checked is not.
- **Do not start implementing** whatever came out on top. This skill produces a ranking and stops;
  picking up the first item is a separate decision, and making it inside the same turn leaves the
  user a fait accompli instead of a list.

## Notes

- Cite **identifiers**, not line numbers, wherever the file is append-mostly — `AGENTS.md`
  §Docs & Spec 6 bans line-number citations for the C runtime for exactly this reason, and the
  triage has already drifted by one to two lines on `string.sprout` and `math/bigint.sprout`. A
  line number is fine alongside a name; it is not fine alone.
- The two axes are deliberately **not** collapsed into one score. A `High`/`Low-Med` and a
  `Low`/`Low-Med` sort the same on any weighted sum and are completely different decisions.
- `Complexity` means cost to land, including gates and approvals — **not** difficulty of
  understanding. An issue can be conceptually trivial and expensive to land.
