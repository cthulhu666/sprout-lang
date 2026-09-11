# Gates

`AGENTS.md` carries the rules. This file carries what each gate checks, how it has been defeated,
and the evidence — read a section when its gate fires, when you are about to override it, or when
you are changing it.

## Golden IR — `just ir-golden-diff`

The rule in `AGENTS.md` (Definition of Done #12): read a reported diff before regenerating.
Regenerating an unread diff launders a regression into an "expected" snapshot, which is the one way
this gate can be defeated. Everything below is why that is harder than it sounds.

### The output is truncated — do not mistake it for the diff

`scripts/ir_golden_diff.sh:55` pipes each file's diff through `head -40`, so a change touching 16
files shows at most ~640 lines. One such change produced a 27,274-line real diff, i.e. the report
was a 2% sample — and the visible part contained only string-constant renumbering while the hidden
part contained three new function bodies and a rewritten `int_from_lexed`. "The report looks
harmless" is therefore not evidence of anything. To actually read it: run `just ir-golden-snapshot`,
then inspect `git diff tests/golden/ir` (which is complete and revertible) before staging. A useful
triage on the full diff is to classify every `-` line — additive changes show removals only in
`@.str.N` declarations and their `getelementptr` references, so any *other* removed line is existing
code that changed and needs explaining.

### `@.str.N` is not the only renumbered identifier, and a subtractive change produces insertions

Compiler-generated wrappers carry a sequential counter too — `__sprout_ir_eta_<target>_<N>`,
`__sprout_ir_lambda_<N>` — and unlike string constants those appear in `define` lines, so a
renumbered wrapper reads as a brand-new function to any check keyed on define names. Removing dead
code renumbers them: the 2026-08-27 DCE landing shed 874,281 lines and still showed **96,392
insertions**, and a subset check first flagged 24 goldens as having "added" defines before
normalising the counter (`06_tuple_param` kept the same two `ToString` wrappers, moved `_25`/`_26` →
`_0`/`_1`, while seven others vanished with their pruned targets). Classify by the *target* name
with the trailing `_<digits>` stripped, never by the full symbol.

### When the diff is too large to read, say so and check a property instead

That DCE diff was 970k lines across 56 files; "I read it" would have been a lie, and regenerating
unread is the one way this gate is defeated. The substitute that carries real weight is a structural
invariant the change is supposed to have — for a pure deletion, *every `define` in each new golden
already exists in the old one* (verified 60/60), paired with a behavioural differential that
compiles **and runs** each corpus file under both compilers and compares stdout and exit status.
State which check you actually ran.

### A new example is a golden-corpus change

`scripts/ir_golden_diff.sh:103` walks `examples/*.sprout` **and** `tests/smoke_shapes/*.spr`, so the
corpus is defined by what is *in those directories*, not by what you edited. Add a file there and
the gate fails with `MISSING GOLDEN: … -> expected tests/golden/ir/examples__<name>.sprout.ll` —
even though you touched no compiler source and no existing golden moved. This caught a two-file
examples-only PR on 2026-08-18: every other gate was green and only `ir-golden-diff` was red. Fix is
`just ir-golden-snapshot` + stage `tests/golden/ir/`; for a purely additive change the snapshot
writes only the new files, and *that* is the thing to verify — if it also modifies an existing
golden, your new file perturbed another file's IR and the "read the diff first" rule above applies
in full.

The inclusion rule has one asymmetry worth knowing (`ir_golden_diff.sh:69-95`): a corpus file is
required to have a golden only if it currently emits non-empty, `ERROR:`-free IR that passes `opt
--passes=verify`. One that does not is skipped **silently** — but only while no golden exists for
it. So the gate's silence about a non-compiling example is not a permanent exemption: it flips to
`MISSING GOLDEN` the moment that file starts compiling, and to `REGRESSION` if it ever stops. Adding
a deliberately-uncompilable fixture to `examples/` therefore passes today and can fail later for
reasons unrelated to the commit that breaks it.

### Reseed before you diff, or the gate answers a question you did not ask

`bootstrap-from-seed` decides whether to rebuild stage-1 by comparing the binary's mtime against
`bootstrap/compile_driver.ll` and `runtime/*.c` — and **nothing else** (`justfile:877`).
`stdlib/compiler/*.sprout` is not in that comparison. So after editing compiler source, every gate
that depends on `bootstrap-from-seed` — `ir-golden-diff` included — happily prints `==> Stage-1
binary is up-to-date with seed + runtime; skipping bootstrap.` and runs the **old** binary.
`ir-golden-diff` then compares goldens emitted by the pre-edit compiler against goldens committed by
the pre-edit compiler: guaranteed `0 differences`, proving nothing. This is worse than a red gate,
because a green one gets cited as evidence. Correct order for a compiler-source change is `just
refresh-seed` **first**, then `ir-golden-diff`. **`just seed-dep-check` does not relieve you of
this.** That gate (added 2026-08-25) closes a *different* staleness axis — it asserts every gate
recipe consuming `build/compile_driver_bin_stage1` also depends on `bootstrap-from-seed`, so no gate
runs a binary older than the seed. Seed-vs-**source** drift is exactly the case above and is still
ungated, because it is `refresh-seed`'s job and nothing can infer it from the recipe graph.

This also makes the pair a usable proof for a *deletion*. To show removed code was unreachable,
check **both** halves: (a) `git diff bootstrap/compile_driver.ll` is NON-empty — the edit reached
the binary; and (b) `ir-golden-diff` reports 0 differences — nothing in the corpus depended on it.
Either half alone is worthless: an empty seed diff means you never rebuilt, and a clean golden diff
without it is the vacuous case above. Used this way on 2026-08-23 to retire an unreachable `++` arm
in `ast_to_ir.translate_binary` (seed −324 lines net, 60/60 goldens byte-identical). Note the seed
diff is large even for a small deletion — IR temporaries and `@.str.N` constants are numbered
sequentially, so removing code from mid-function renumbers everything after it; the *goldens* are
the load-bearing half, not the seed diff's size.

### 0 differences is weak evidence for a purity change in `dce`

Both halves of the 2026-09-08 nullary DCE work reported 62 files, 0 differences: the arm that
*eliminates* more (a pure nullary call bound to an unused name) and the arrow walk that *keeps*
more (a curried `!{IO}` call that was being dropped — a real dropped-effect miscompile). No
golden program binds a call it then ignores, in either direction, so the corpus cannot observe
`is_pure_callee_type` at all. A change there needs a synthetic test over `dce.elim_program` and
an executable fixture; the golden gate will report clean either way.

## Bootstrap seed — `scripts/seed_gate.sh`, `just refresh-seed`

Wired as a PreToolUse Bash hook. Intercepts `git commit` and blocks if `stdlib/compiler/*.sprout` or
`stdlib/*.sprout` is staged without a refreshed `bootstrap/compile_driver.ll`. Bypass (when IR is
genuinely unchanged): run `just verify-bootstrap-fixed-point` then `just seed-fp-ack`.

### `just seed-stale` and CI answer different questions, and a comment-only edit splits them

Three checks are easy to run together and are not the same thing:

| check | compares | run by |
|---|---|---|
| `scripts/seed_gate.sh` (commit hook) | staged tree hash vs `.git/seed-fp-ack` | local `git commit` |
| `just seed-stale` | `shasum` of `stdlib/compiler/*.sprout` vs the `; seed-fingerprint:` line at the top of the seed | nothing automatic |
| `just verify-bootstrap-fixed-point` | the re-emitted IR is byte-identical | **CI** (`.github/workflows/ci.yml`) |

Edit only a comment in a compiler source and the source bytes change while the emitted IR does not,
so `seed-stale` reports STALE while the fixed point holds. **CI runs only the fixed-point check, so
a red `seed-stale` is not evidence CI will fail** — and `seed-stale`'s own message says "Run: just
refresh-seed", which will send you through a full reseed you did not need. For an IR-unchanged edit
the bypass above (verify, then ack) is the correct path; reseed when you want the fingerprint line
back in sync, not because the fixed point demands it. Note the commit hook compares *tree hashes*,
not fingerprints: `just seed-fp-ack` must be its own step with nothing touching the index between it
and the commit, or the ack goes stale and the hook blocks for a reason unrelated to the seed.

### A new prelude `extern fn` is not an IR-unchanged edit

`ir_lowering.lower_extern_decls` emits a `declare` for *every* bundled prelude extern, and
`compile_driver` bundles the prelude, so adding one `extern fn` to `stdlib/prelude.sprout` adds one
`declare` line to `bootstrap/compile_driver.ll`. `verify-bootstrap-fixed-point` will break; use a
full `just refresh-seed` (delete the stale stage-1 binary first), **not** the `seed-fp-ack` bypass —
even though `stdlib/compiler/` was untouched. No 2-step bootstrap is needed (no
parser/compiler-source change; the seed diff is purely the additive declare line).

## Review gate — `scripts/review_gate.py`

Wired as a Stop hook in `.claude/settings.json`. Once per distinct working-tree state it refuses the
turn and prints a path-aware checklist: idiomatic Sprout for `.sprout`/`.spr`, `docs/guidelines.md`
for `stdlib/`, GC/rooting for `stdlib/compiler/` and `runtime/`, docs+spec sync always. Stop is the
only unconditional exit from a turn, so it is the only event that can gate "the change is finished"
— `PostToolUse` fires mid-edit and cannot block. It cannot loop: the session's first Stop records
the tree as a baseline, a state already shown is never shown twice, and `MAX_BLOCKS_PER_TURN` caps
any one user turn. Generated artifacts (`bootstrap/compile_driver.ll`, `tests/golden/ir/`, `build/`,
`.claude/`) are invisible to it, so a reseed or a golden snapshot never trips it. Test with `just
test-review-gate`.

### The budget is per user turn, and deliberately weaker than the loop safety it looks like

Terminating a runaway is not this hook's job: Claude Code force-ends a turn after
`CLAUDE_CODE_STOP_HOOK_BLOCK_CAP` (default **8**) *consecutive* blocking Stops, resetting that count
on any non-blocking transition. `MAX_BLOCKS_PER_TURN = 3` only has to sit under 8, so a runaway is
released quietly by our `systemMessage` instead of by the platform's warning. Until 2026-09-07 this
was `MAX_BLOCKS = 5` counted over the whole **session**, which duplicated platform behaviour at the
wrong scope: the fuse blew a few changes in and the gate was then silently dead for the rest of the
session — worst in exactly the long sessions it matters most in. A turn is identified by the
payload's `prompt_id` ("UUID correlating a user prompt with all subsequent events"); it is optional,
and `stop_hook_active` is the fallback, being false exactly on a turn's first Stop.

### The baseline re-anchors on an accepted review, and a clean tree still baselines

`moved` is measured against the last state the agent was shown and then left alone — not against
session start. A frozen baseline grows the report *and the checklist filter* until all four items
fire on every block regardless of what changed, which is how the gate becomes noise. Two edges
follow from that. The baseline is now recorded even when the tree is clean: the early return for
"nothing changed" used to run first, so a session starting in a fresh worktree — the normal case
here — spent its baseline on its own first change and never reviewed it. And a tree that goes clean
again re-anchors too, or the files you just committed reappear as phantom `X` deletions in the next
report.

## Both hooks read one worktree — the one named by `cwd`

Until 2026-09-07 neither did. Each resolved a single root from `CLAUDE_PROJECT_DIR` (review gate) or
the hook's own cwd (seed gate), which is always the main checkout. A session doing its work in a
linked worktree — the normal case here, and the worktree-per-session workflow — was therefore
reviewed zero times and could commit compiler sources with a stale seed unopposed. Measured on a
real session: `blocks: 0`, with a 3645-path baseline holding nothing but the main checkout's
untracked dirt. Each gate now resolves the checkout the session is actually in: the review gate from
the Stop payload's `cwd`, the seed gate from the command's own `cd <dir>` or `git -C <dir>`. Covered
by `just test-shell-hooks` and case 12 of `just test-review-gate`; both run in `just ci-fast-gates`.

Corrected 2026-09-07, same day: the first repair unioned `git worktree list`, which is a different
bug, not a fix. This repo has 66 worktrees. Reading all of them makes every session's gate fire on
*other* sessions' concurrent edits — a review the blocked session cannot perform and did not cause.
Measured while writing this: a session with `git status` empty in its own worktree was blocked three
times in two minutes on another session's `fix/type-alias-expansion` work, spending 3 of its 5
lifetime blocks; its baseline held 3,678 paths, of which 3,645 were the main checkout's untracked
dirt. The right scope was never a union — it is the one worktree named by `cwd`, which also drops 66
`git status` invocations and 3,659 `stat()` calls per Stop.

## Hook authoring — inform on stdout, never stderr

Claude never sees stderr from a hook that exits 0 — it goes to the debug log only ([hooks
reference](https://code.claude.com/docs/en/hooks)). `scripts/guidelines_reminder.sh` printed its
checklist to stderr and exited 0 for its whole life, so the one gate that points at
`docs/guidelines.md` before a `.sprout` edit reached nobody. It now emits
`{"hookSpecificOutput":{"hookEventName":"PreToolUse","additionalContext":…}}` on stdout, the shape
`.claude/hooks/just-test-tee` already used. Only a hook that exits 2 may use stderr, and that text
becomes the blocking message.

## Example canary — `just run-example-canary`

Emits IR, links and *runs* `examples/tuples.sprout`, `examples/factorial.sprout`,
`examples/maybe_map.sprout`, `examples/typeclass_collections_demo.sprout` and
`examples/fizzbuzz.sprout`, failing on a non-zero exit. `just compile-examples-stage1` only covers
compile, which is why this exists.

Corrected 2026-08-20: `AGENTS.md` used to call this "a manual gate until CI covers it", which had
stopped being true. The recipe is wired in as `example-canary` inside `just ci-fast-gates`, and CI
runs that aggregate (`.github/workflows/ci.yml`). Running `ci-fast-gates` satisfies the item; the
stale note was liable to send you hand-running five examples a gate you already ran had covered.

## Optimized codegen — `just o2-codegen-smoke`

Emits IR for each `tests/codegen_o2/*.spr` and runs `clang -O2` on it, failing on a non-zero exit.
Every other test path links test IR with **no `-O` flag** (`_test-stdlib` in the justfile), so an
IR bug that only the `-O2` pass pipeline reaches is invisible to `just test` while every shipped
binary is built `-O2`.

Added 2026-09-11 for a specific class: LLVM passes that break the `musttail` invariant we emit.
`tailcallelim` does exactly that (docs/mutual-tco-v0.md §2.1), and the failure is a backend
`fatal error`, not a wrong answer — nothing softer would have caught it. Wired into
`just ci-fast-gates` as `o2-codegen-smoke`.

A fixture belongs here when its *shape* is what provokes the optimizer, not its output; assert
nothing about what it prints, and keep it small enough that the IR is readable when it fires.

## Render cost — `just render-cost-gate`

Paints 20 frames of a 200×50 screen through the real stack (`tests/cost/render_frame.sprout`) under
`SPROUT_DEBUG_ALLOC=1`, and fails when the run exceeds a budget of allocations **per painted cell**.
Every other gate asks whether the output is right; this one asks what it cost.

Added 2026-09-11, after a bug that every existing gate passed: three UCD table searches per painted
character, each slicing substrings per probe, ~100 ms a frame with a source file on screen. The
output was correct throughout. A person using `examples/tui_files.sprout` found it.

**It counts allocations, not time.** For a workload with no clock and no input the counters repeat
exactly — byte-identical across runs — which is what an absolute budget needs and what
`just bench-string-concat` cannot offer (its header says as much: a wall-clock number cannot carry a
fixed threshold).

Pick the shape from what you are pinning, because the repo now has both. A *complexity* claim — "the
cost must not depend on this input's size" — is a **ratio** of two timed arms, and
`tests/stdlib/test_byte_offset_cost.spr` shows how to make that non-flaky: vary only the size, take
the minimum of several rounds, allow an order of magnitude. A *constant-factor* claim — "one frame
must not cost more than this" — has no second arm to normalise against, so it needs a counter that
does not vary with the machine. Ratios catch a wrong exponent; budgets catch a bad constant. This
gate is the second kind, and today's bug was the second kind: correct complexity, 8× the constant.

**`gc_swept` is the load-bearing counter, not `sprout_obj`.** Verified by building the probe against
the pre-fix `grapheme`: objects came out *identical* at 18 per cell, swept at 106 against 42.
`sprout_obj` does not count cstr allocations, and that bug was `str_slice` churn — an objects-only
budget would have passed it.

**The floor matters as much as the ceiling**, because "cheap" and "did nothing" are the same number
to a budget. A probe whose list renders no rows scores 5 and 6 against a ceiling of 26 and 60 — green,
measuring an empty screen, with nothing else in the repo exercising `tests/cost/`. Both bounds are
asserted, and both were verified to fire.

**Two properties of the fixture are load-bearing.** Every frame's content differs, because
`diff_to_ansi` emits only changed cells: a repeating screen emits nothing from frame 2 on, which
leaves the whole ANSI-emission half outside the budget at ~1% of the total while looking covered.
And one row in three is non-ASCII, because the ASCII fast path means Latin rows never reach a UCD
table, so an all-ASCII corpus cannot see a regression in the table path at all. Both were found by
review *after* the first version shipped with neither.

The budget is generous on purpose: it catches a 2× arriving unnoticed, not ordinary churn, and the
observed value prints on every run so drift is visible long before the ceiling. A legitimate change
that crosses it moves the ceiling in the same commit, with the new number in the message — the same
discipline as a golden, for the same reason. Exact values are deliberately **not** pinned: the gate
must also pass on CI's Linux x86_64.

## Optimisation-pass harness — `just opt-harness-check`

Compiles `tests/opt_harness/dead_let.spr` twice, once with every pass on and once with
`SPROUT_OPT_OFF=dle`, and asserts four things: the stats line appears in both modes, the pass
removes a non-zero number of nodes, the two IRs differ, and the two binaries print the same thing.
It then compiles with `SPROUT_OPT_OFF=nosuchpass` and requires a warning plus unchanged output.

The gate exists because every part of this can fail *quietly*. A switch that never reaches codegen,
a pass that silently stops firing, a typo'd pass name that disables nothing — each leaves a green
build and a compiler that is no longer doing what the flag says. Added 2026-09-11 with M0
(docs/opt-passes-v0.md); wired into `just ci-fast-gates` as `opt-harness-check`.

The fixture is deliberately wasteful, and has to be: DLE removes **zero** nodes from every real
program in the bench corpus (`bench/results-2026-09-11-opt.md`), so a realistic fixture would assert
nothing. Keep the dead binding pure and unread — making it effectful or reading it turns the gate
into a tautology that passes for the wrong reason.

## `just linux-smoke`

Every other local gate runs the kqueue backend; CI runs epoll + timerfd, and the two diverge in ways
that are *unreachable* on macOS — `task_sleep` needs a descriptor on Linux and none on macOS, and
`accept(2)` passes already-pending network errors through on Linux only. Two such failures reached
CI on locally-green branches on 2026-08-11.

It stays a recommendation rather than a Definition of Done item because it needs a container
runtime, and requires the repo to live under `$HOME` (the container sees it through the VM's `$HOME`
mount).

## CI — the `changes` job, and what a 30-second green `test` means

`test` is the only required check on `master`, and it is strict (branch must be up to date). A
docs-only PR used to pay its full ~20 min: `ci.yml`'s per-event detection only reached the
`tests/stdlib/compiler/` suites, after bootstrap and `ci-fast-gates` had already run.

The `changes` job now classifies the event once (~10 s) and every other job waits on it. `docs_only`
is a strict allowlist — `docs/**`, top-level `*.md`, and `LICENSE`/`NOTICE`, which are named
literally because they carry no extension. Nothing else: `examples/` is compiled by
`compile-examples-stage1`, `bench/` by `compile-bench`, and a `.github/` edit must run the workflow
it edits, so none of those three counts as docs. Fail open, as before: any non-`pull_request` event,
an unresolvable base, or a failed diff runs everything.

**`test` skips its steps, never itself.** GitHub reports a workflow skipped by a path filter as
*pending*, not as success, so a `paths-ignore` on this workflow would leave the required check
pending forever and block every docs PR — the obvious fix is the broken one. `macos`, `lsp`,
`intellij-plugin` and `windows` skip as whole jobs, which is safe only because none of them is
required.

So a `test` green in 30 seconds is not evidence the suite passed — only that nothing the suite can
observe changed. Check the `changes` job's log before citing a green.
