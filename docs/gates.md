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

### Both seed checks hang off a commit, and a rebase makes no commit they can see

Two things check the seed before it lands, and a rebase defeats both:

| check | fires on | why a rebase misses it |
|---|---|---|
| `scripts/seed_gate.sh` (PreToolUse) | command text matching a literal `git … commit` | `rebase --continue`, `cherry-pick`, `revert`, `merge`, and any commit made inside a script are all different command text |
| `.githooks/pre-commit` | git's `pre-commit` hook | git does **not** run `pre-commit` for commits replayed by a rebase (verified: three `git commit`s fired it three times, a rebase of one of them fired it zero more) |

Rebase is the case that bites, because rebasing is exactly when the seed goes stale — `master`
moved, its compiler sources moved with it, and your replayed commit still carries the seed you
built against the old base. The gate had nothing to object to when that commit was first written,
and there is no second commit event for it to fire on. Observed 2026-09-20 on PR #317: master's
newer seed was overwritten by a rebase and the push went out stale.

CI is the backstop — `just verify-bootstrap-fixed-point` runs there, so this costs a round-trip and
a confusing red, not a bad merge. Until it is closed: **after any rebase of a branch touching
`stdlib/compiler/`, reseed before pushing**; do not read a quiet hook as a clean seed.

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

## fmt/lint batching — `just fmt-batch-smoke`

Asserts what the `fmt`, `fmt-check` and `lint` loops rely on: every path in a batch is processed, an
unreadable *and* an unwritable path each report on **stderr** and exit nonzero **without abandoning
the rest of the batch**, and a flag outside its recognised position is refused **without writing the
file**.

`just test` cannot reach the IO ones — they live in the exit-status fold, and a `.spr` suite has no
way to make a path unreadable or to observe which stream a line went to. It *does* reach the flag
case, because that half is the pure `fmt_cli.check_paths`, and
`tests/stdlib/compiler/test_fmt_cli.spr` asserts it directly. That overlap is deliberate: the unit
test gives the fast red signal on the parse, and the gate is the only thing that can check the
consequence — that the file on disk was left alone. Keep both; neither subsumes the other.

`fmt_cli` is a separate module because a module with `fn main` cannot be imported by a test at all —
the imported `main` becomes the entry point, so the suite silently runs the driver instead.

It runs against fixtures it writes into `$TMPD`, never against tracked sources. Both earlier versions
of this gate asserted on `stdlib/bytes.sprout` and `stdlib/string.sprout`, which is wrong twice over:
a new lint rule firing on either would fail this gate with a message about batching, and the flag
regression it guards would have had it **rewrite a tracked file** — while `ci-fast-gates` runs
`fmt-check` over the same file in parallel.

Added 2026-09-28 with `-n 100` batching. Before it, `run_lint_file` **panicked** on an unreadable
path: harmless at one file per process, but a batch would lose every later path in it. The flag case
guards a bug that was live — `fmt <path> --check` matched `["fmt", path | _]` and **wrote** the file
the caller asked to only check, because the trailing `| _` discarded the flag.

The no-write assertion is mutation-checked, not assumed: restoring the swallow (`is_flag` never
matching) makes the gate print the rewrite it caught, `fn  add_one( n: Int )` → `fn add_one(n: Int)`.
Its fixture is deliberately *unformatted*, since "the file is unchanged" holds trivially for a file
that was already formatted — which is what made the tracked-source version of this check vacuous.

Wired into `just ci-fast-gates` and `just gate`. It was briefly left standalone, on the reasoning
that `just lint` is not in CI (`.github/workflows/ci.yml`) so only `fmt-check` runs batched there —
and `just gate-audit` rejected that immediately, which is the right answer: `fmt-check` batching is
exactly what CI depends on, and a gate nobody runs reads as coverage it does not provide.

## Runtime line refs — `just runtime-line-refs`

Rejects `sprout_runtime.c:NNNN`-style citations of the C runtime anywhere but `docs/archive/`, which
is exempt because a retrospective records what was believed when it was written. Cite the identifier;
grep finds it wherever it moved.

Added 2026-09-26 with the sweep that made it green. The runtime is append-mostly, so a line number
drifts in one direction and never back: of the 47 refs then live, 25 named an identifier near the
citation and **15 of those 25 pointed at the wrong place**, by 14 to 75 lines — `VectorVal` cited at
`:91` and living at `:105`, `repl_eval_expr` cited at `:5114` and living at `:5189`. The other 22
named no identifier at all, so they were unverifiable by a gate *and* by a reader: there was nothing
to search for. Every wrong ref pointed earlier than the truth, which is the signature of a file that
only grows.

**Its blind spot is the bare continuation ref** — `:1052` a clause after a real citation. Same
defect, but the pattern cannot be matched without also matching times, ports and version numbers.
The sweep removed the ones sitting beside a named ref; thirteen survive in
`docs/gc-header-rewrite-handoff-2026-07-03.md`, whose subject layout (`ManagedNode`, the heap index)
was deleted, so there is no identifier left to name. `.sprout` line refs — several hundred — are out
of scope.

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

**A ratio arm must not time a header-reading builtin.** CI sets `SPROUT_GC_HDRCHECK=1` for
every `test-*` shard, and under it `str_byte_len` validates the header with a `strlen` — so the O(1)
byte-length read that `string.byte_length` documents is O(n) exactly where the gate runs. A
`string.take` guard built on it measured 1.0x locally and 164x on CI (255x locally once the flag
was set); an earlier `sprout_cstr_byte_len` revision failed the same way at 389x. The comment on
that helper in `runtime/sprout_runtime.c` is the primary record. Run a new ratio gate under the flag
before trusting it — `SPROUT_GC_HDRCHECK=1 just test` reproduces CI.

The flag also makes the **sweep** costlier: since 2026-09-26 it checks every slot boundary against
the slotmap. Measured on `--emit-ir stdlib/compiler/ast_to_ir.sprout`, interleaved, min of 5:
2.18s with the flag off, 2.44s on without that check, 2.60s on with it — the walk check is +6% and
the flag as a whole +19%, most of it the older CSTR `strlen`. So a collection-heavy arm pays a
constant factor on CI it does not pay locally. Same rule, same reproduction command.

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

## Compiling one long block — `just rooting-cost-gate`

Prices the **compiler**, not a compiled program. Compiles three fixtures that differ only in the length
of one list literal (`tests/cost/rooting_block_{small,large,huge}.sprout`, 60, 120 and 240 elements) and
bounds the **difference per added element**, so the several hundred thousand allocations it takes to
compile the prelude cancel instead of entering the budget. An absolute number would drift as the
compiler grows until it guarded nothing. The third fixture exists so the per-element figure can be
computed twice and compared — see §A third quadratic below for what one measurement cannot see.

**It measures stage-2, not stage-1, and that is the whole gate.** §Reseed before you diff applies to
every `bootstrap-from-seed` gate, but it bites hardest here: for the others the subject is a compiled
*program*, so a stale seed degrades the measurement; here the compiler **is** the subject, so stage-1
would price the pre-edit pass and report "within budget" for a reintroduced quadratic — total vacuity,
not partial. `just gate` also runs this gate *before* `verify-bootstrap-fixed-point`, so a stale seed
would be caught only after it had reported green. Depending on `build-stage2` removes the trap instead
of documenting it: `_build-stage` has no no-op guard, so stage-2 is always relinked from the working
tree. Verified by reintroducing the quadratic without reseeding and confirming the rebuilt stage-2
carried it (RSS 88 MB → 1907 MB).

Added 2026-09-28, after two independent quadratics in the same shape — per-op cost in a single basic
block — made `tests/stdlib/test_bigint_vectors.spr` (330 assertions in one `run_suite` list) cost
1.5 GB to compile. `ir_rooting` materialised a live-set per op and asked `roots_across` for the whole
live set at every trigger; `ir_lowering` rendered IR text with right-nested `++`. A generated vector
suite is one 13k-instruction function, so both scaled with the literal while the emitted IR stayed
linear. **Every output-checking gate passed throughout** — golden IR was byte-identical before and
after the fix. It surfaced only as the OOM backstop SIGKILLing a parallel test worker, which reads as
`COMPILE FAILED` with empty stderr: indistinguishable from a real compile error, and nearly filed as
a regression in an unrelated PR.

**Four arms and a growth check.** `map` covers the rooting pass and is sharp and stable: 4076 per
element before the rooting fix against 300–301 after, reproducing to ±0.3% across builds, and it *rose
with every element added*, so a return of that quadratic overshoots by 6.3× here and more at any
larger fixture.

`arena_bytes` is the only arm that can **separate** `ir_lowering`'s two concatenation forms — though not
the only pass it sees, which §A third quadratic below corrects. The two concatenation forms
allocate the same *number* of objects and differ only in bytes copied, so no count separates them:
reintroducing right-nested `++` in `ir_lowering.lower_ops` moves peak RSS from 88 MB to 1907 MB while
`gc_swept` moves 7650 → **7628** and `map` 300 → **300**. `arena_bytes` moves 249,472 per element →
**1,475,716**, a 5.9× separation that grows with block size. Red-verified by reintroducing that
quadratic alone and confirming the gate fails on the byte arm while `map` and `gc_swept` stay inside
their budgets.

It is named for the allocator it covers, and the name is load-bearing. `arena_bytes` is what
`sprout_gc_alloc_block` hands out; Vector element arrays, `Bytes` payloads and Builder chunk arrays
are plain `malloc` behind `sprout_alloc_counted` and land in `offarena_bytes`, which this gate
REPORTS nothing about and does not budget — the compiler barely touches those paths (3.5 MB against
166 MB here), so there is no measured number to set a ceiling from. A `Bytes`/`Builder` workload
needs its own fixture before that arm means anything.

`gc_swept` is floored only, and that floor is a **fixture check, not a churn detector**. An earlier
revision of this entry claimed it separated the two by 1.7×. That was wrong, and the error is why the
byte arm above was verified the way it was: the 11707 figure it compared against came from *master's*
binary, which carried **both** quadratics, so all the movement was the rooting fix. A counter that
shifts when you fix two things at once has not been shown to see either — reintroduce the one
regression alone and re-measure. (`ulimit -v` is not settable on macOS, so a peak-RSS arm was never
available; the byte counter is what closed this, not a memory limit.)

Floors are asserted as well as ceilings, for the reason the render-cost entry gives: if an edit leaves
the two fixtures the same size, the delta collapses and the ceilings stay green over nothing.

### A third quadratic sat inside a green ceiling for as long as the ceiling existed

The three arms above each name a pass, and between them they missed one. `ast_to_ir` threaded the open
block's ops as a `List IROp` and appended one op at a time — `list_append(cur_ops, [op])` at 94 sites —
so every op copied the block emitted so far. Per-element `sprout_obj` measured **10454 / 19643 / 38002**
at block sizes 120 / 240 / 480: doubling as the block doubles, which is the signature of a quadratic
whatever the absolute figure is. `map` never moved (303 → 343, flat), because the rooting pass was
innocent. `arena_bytes` *did* move, and was inside a ceiling of 550,000 the whole time at 397,637.

Two lessons, and the second is the one that cost the time.

**A ceiling measured at one size cannot tell a big constant from a growing one.** 397,637 against
550,000 reads as 28% of headroom used. It was really a number proportional to block length, so the
same gate would have gone red on a fixture twice as long and green again on half — a property of the
fixture, not of the compiler. This is why there is now a **third fixture**
(`tests/cost/rooting_block_huge.sprout`, 240 elements) and a **growth arm**: the per-element figure is
computed twice, over 60→120 and over 120→240, and their ratio is bounded. Flat is 1.0 and quadratic
2.0. The first bound was 15/10 on both counters, red-verified at **18/10** on the pre-conversion
compiler for both objects and bytes, green at 11/10 and 13/10 after. It is now in hundredths, with
objects at 105 and bytes at 106, and closures have their own arm at 125 — see the front-end,
rooting and fixture paragraphs below for why.

A shape arm can go vacuous the same way a ceiling can, and the first version of this one did: it only
tested `-gt`, so a truncated `huge` fixture collapsed its delta to zero, read as *perfectly* flat, and
passed. Review caught it. Three things close it, and they are the same three every other arm here
already had. The element counts are **counted from the fixtures** rather than declared in the script,
so the coupling is not a comment saying "change one, change all three". The doubling is **asserted**
before any compile, because the ratio only means "cost of doubling the block" while the sizes double.
And the second delta is **floored** like the first, since a ratio is only as meaningful as its
numerator. The floors also now run *before* the growth arm: the arm divides by the first-delta figure,
and a collapsed fixture pair used to reach it as a sentinel that reported the collapse as a quadratic —
the wrong cause, in the gate whose own entry is about wrong causes.

**An arm attributed to one pass can be driven by another.** This entry said `arena_bytes` "covers
`ir_lowering`, and it is the only arm that can", which was true of what it *separates* and false of
what it *sees*: moving the accumulator to `ListBuilder`, with `lower_ops` untouched, took it
397,637 → 108,996 per element. Objects and bytes both fall for an accumulator fix and only bytes fall
for a string fix, so the two arms read together are the diagnosis; the failure messages now say so,
because the byte arm's old advice pointed exclusively at string building and would have misdirected
anyone who hit it this way.

`sprout_obj` is now budgeted (3000 per element against 1434 observed), and `arena_bytes` retightened to
250,000 — the old 550,000 was set around a quadratic.

**The residual was the front end, and not where it was looked for.** The objects ratio stayed at 11/10
after the fix. Split by phase, `bundle` (lexing included) was flat to 1920 elements, and `check` was
quadratic. Varying the element showed it was only elements holding a `++`, which carries a
`Semigroup` dictionary. `verify_dispatch` gathered its per-call outcomes as
`list_append(collect_expr(h), collect_exprs(t))`, and `list_append` copies its left side. So each
nested `Cons` of a list literal copied every outcome below it. Threading one `ListBuilder` through
the walk took `check` to a flat 976 objects per element, and the ratio to 107/100.

That is also why the arm moved to **hundredths**: tenths floored 119 and 107 to 11 and 10, too close
to set a bound between. Objects were bounded at 112 and bytes at 150. The last 7 in the objects figure
was blamed on the fixtures' digits; it was the fixtures' headers (below).

**The rooting pass was cubic, and the gate read it as flat.** `--emit-ir` took 4.9 s at 480 elements
and 37 s at 960. At every trigger `roots_across` walked every value in scope and ran `list_member`
against the root stack for each one. This entry first said that scan allocates nothing. It does: each
`list_member` call builds its `Eq String` dictionary as a fresh closure. The report printed
`closure=` all along, and no arm read it. Per-element closures went 1212 → 2382 per doubling, and the
same closures were most of the byte growth, 130/100.

Only values defined since the last trigger can need a root. A value passed over at a trigger was dead
after it, and liveness only shrinks within a block; a value pushed is still rooted or was popped dead.
Scanning just those gives byte-identical IR, 36 closures per element at every size, and 0.34 s at
960 elements. The new **closure growth arm** bounds it at 125/100, and bytes tightened to 125. That
arm sees the comparisons, not the walk: a rewrite that drops `list_member` but still visits the
whole scope per trigger allocates no closure and would pass.

**The fixtures differed in more than element count.** Profiled by allocation site, the remaining
byte growth had two sources. `ir_lowering.walk_ops_for_strs` appended each string global to the
function's growing globals text, 8,550 → 17,088 bytes per element: one literal per element, so
quadratic. And `rooting_block_huge.sprout` had a three-line longer header comment than its siblings,
while `strip_headers` re-scanned from byte 0 at every header line, so those lines read as 901 bytes
per element of growth — and as the objects arm's 7, which this entry had put down to the digits.
Collecting the globals as parts and joining once, scanning the header once, and making the headers
identical took bytes to 102/100 and objects to 100/100. The gate now fails if the headers differ,
and the bounds are 105 (objects) and 106 (bytes).

The fix is `ListBuilder IROp` rather than a hand-kept reversed list, because the file was *already*
carrying both conventions under one type: `translate_expr` held `cur_ops` in source order while
`bind_ctor_field_args` held it reversed with the difference recorded only in a comment, and the boundary
between them reversed the list back and forth. A `wrap` makes the compiler enforce what the comment
asked for — and the whole conversion is verified by emitting byte-identical IR for all 73 compilable
fixtures under `examples/`, `tests/smoke_shapes/` and `tests/cost/`.

## Object-age instrument — `just gc-ageprof-check`

Calibrates `SPROUT_GC_AGEPROF=1` against two workloads with known answers. Details and the
current bounds: [gc-generational-v0.md](gc-generational-v0.md) §4.

### An absolute ratio tracked the number of rooted globals, not what the gate measured

`retain_none` asserted `marked_age_ge1/marked_total ≤ 15%`. That workload keeps only ~6 objects
live per collection, so a single permanently rooted global — re-marked every cycle, one age≥1
mark per cycle — is worth ~12pp of it. The bound read **0%** when it was set, 12% once
`pow10_exact_unit` became a rooted global, and 24% when `list_builder_empty` was added, where it
failed. Nothing had regressed: same cycle count, same `freed_total`, 99% of the churn still dying
young, separation 49pp against a 40pp floor. The failure message said "objects are surviving
cycles that should not" about an object whose whole job is to survive.

The fix is to subtract the steady state rather than to raise the bound: a rooted object is marked
once at each age, so buckets 1..30 of `marked_by_age` each hold exactly the root count, and
`marked_age_ge1 - roots × (cycles-1)` is the churn that actually outlived a collection (0% on
both sides of this change, where the raw ratio read 12% and 24%). Raising 15% to 35% would have
worked for exactly one more root.

**Subtracting a floor needs the floor bounded, or a leak hides inside it.** The histogram cannot
tell a rooted global from an object retained for the whole run — both are flat steady state — so
a 500-object leak old enough to fill buckets 1..30 would be read as a 502-root floor and
subtracted away. Hence `roots ≤ 8` beside the flatness check: the count is small, known, and
changes only when someone adds a global, so bounding it costs nothing and is what keeps the
correction from absorbing the thing it exists to detect.

## Sweep-walk counter — `just gc-walk-check`

Calibrates `walked=` on the `SPROUT_DEBUG_GC` cycle line: slots the sweep stepped over, FREE
included. Over `swept` it is the work spent per object reclaimed, the quantity
[gc-trigger-v0.md](gc-trigger-v0.md) §3.1 says the trigger cannot see. Two known answers —
`test_gc_walk_sparse` (regions pinned full of FREE slots no allocation can refill) must read
≥10 slots per object swept, and `test_gc_age_retain_none` (dense churn) ≤2 — plus, on every cycle
of every probe, `walked ≥ live + swept`. At introduction they read 25 and 1.007.

**The invariant only covers the branches its workloads reach.** Neither calibration workload
allocates an object large enough for its own region, so deleting the `is_large` branch's count
left both green. `test_gc_large_object_arena` is probed for the invariant alone, and fails the
same deletion on its first cycle (walked 4,076 < 10 + 4,086).

## Optimisation-pass harness — `just opt-harness-check`

Compiles `tests/opt_harness/dead_let.spr` twice, once with every pass on and once with
`SPROUT_OPT_OFF=dle`, and asserts four things: the stats line appears in both modes, the pass
removes a non-zero number of nodes, the two IRs differ, and the two binaries print the same thing.
It then compiles twice more with names that must *not* take effect: `nosuchpass` requires a "no
such pass" warning plus unchanged output, and `cse` — declared in the switch's vocabulary but not
implemented — requires a "not implemented yet" warning.

The gate exists because every part of this can fail *quietly*. A switch that never reaches codegen,
a pass that silently stops firing, a typo'd pass name that disables nothing — each leaves a green
build and a compiler that is no longer doing what the flag says. Added 2026-09-11 with M0
(docs/opt-passes-v0.md); wired into `just ci-fast-gates` as `opt-harness-check`.

The fixture is deliberately wasteful, and has to be: DLE removes **zero** nodes from every real
program in the bench corpus (`bench/results-2026-09-11-opt.md`), so a realistic fixture would assert
nothing. Keep the dead binding pure and unread — making it effectful or reading it turns the gate
into a tautology that passes for the wrong reason.

## Driven smokes — `just ide-smoke`, `just tui-files-smoke`

### A gate that pressed a key on an unspecified row

`tui-files-smoke` presses Right on tree row 0 and asserts the expanded directory's child appears.
Row 0 was whatever `fs.read_dir` answered first, and the builtin behind it documents that order as
the filesystem's — unspecified. So the gate was a coin flip on Linux: it went red on a runner, and
a re-run of the **same commit** went green. It had been green for months because the weighting is
per-machine, which also means `just ci-fast-gates` on a Mac says nothing about it.

Fixed at the root: `ide/filetree.sprout` and `examples/tui_files.sprout` now sort, directories
first then by name, with a unit test under the property. The lesson generalises — **a driven smoke
must not assert on anything the program leaves unspecified**; make the program specify it, rather
than making the gate press more keys until something works, which hides the regression the gate
exists to catch.

Both drive a real binary by writing keystrokes into its stdin, and both need the writer to **outlive**
the window they watch: a key stream that ENDS quits the app whatever the keys were (`TermEof` closes
the pump's channel), so only a writer still open makes the exit attributable to Escape. Hence the
trailing `sleep` well past the timeout.

### `wait` waits for the JOB, not the pid you hand it

The writer must outlive the window; it must not be *waited on*. For a backgrounded pipeline `$!` names
only the last process, but `wait $!` blocks until the whole job finishes — so waiting on the app also
waited out the writer's remaining sleep. That was **19.5 of every 27 seconds** in `ide-smoke` (three
runs: 81s of which 58.5s was a `wait` on a process with nothing left to say) and ~10s in
`tui-files-smoke`.

`kill -0 $pid` checks one process and `wait $pid` waits for one job, so the two lines disagree about
what they track. `tui_files_smoke.sh` already carried a comment reasoning about this exact hazard and
had fixed the *detection* loop with `exec`; `wait` two lines below re-coupled the teardown anyway.

The fix is a FIFO instead of an anonymous pipe, giving the writer its own pid to kill once the app has
gone. Two things to keep if you touch this again:

- **Do not shorten the sleeps instead.** They are what makes an exit attributable to Escape. Verify by
  deleting the `\x1b` from the key fragments — every run must then FAIL with "still running"; a pass
  means stdin is closing early and the runs prove nothing.
- **Give the writer neither stdin nor stderr.** Killing it orphans its trailing `sleep`, and an orphan
  holding the script's stderr open makes a caller reading to EOF wait out the delay just removed.

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

**`test` is never skipped; the shards are.** GitHub treats the two kinds of skip differently, and
both are traps for a required check. A workflow skipped by a path filter leaves its checks
*pending* forever, so a `paths-ignore` here would block every docs PR. A job skipped by `if:`
reports *success*, so a required job that could be skipped would pass with a red shard under it.
So the Linux suite runs as five shards — `test-gates`, `test-core-1`, `test-core-2`,
`test-compiler-1` and `test-compiler-2` — that skip as whole jobs, and `test` is a separate job
that gives the verdict. It runs under `if: !cancelled()`, so a failed shard does not skip it. It
accepts a skipped shard only where `changes` says that shard should skip. A split suite is named
jobs, not a matrix, because GitHub's docs do not say how a matrix's legs combine into
`needs.<job>.result`.
`macos`, `lsp`, `intellij-plugin` and `windows` skip as whole jobs too, which is safe because none
of them is required.

**Why shards.** One job ran the suite step by step on one 4-vCPU runner. Each step already
filled all four cores, so wall time was the sum: 18–23 min. Sharded, it is the slowest shard plus
the ~1 min of setup each shard repeats. Standard runners are free on a public repo, so the repeat
costs runner time, not money. The limit that matters is the free plan's 20 concurrent jobs: a run
has at most 9 running at once. Rebalance from the per-step times in a run's log: inside
`test-gates`, `ci-fast-gates` starts its longest gates first and prints each gate's seconds
beside its ✓. The core and compiler pairs split their suites with `SPROUT_TEST_SHARD=k/n`,
which `_test-stdlib` reads: every n-th file from the k-th. Unset runs all files; an empty shard
fails rather than passing.

So a `test` green in 30 seconds is not evidence the suite passed — only that nothing the suite can
observe changed. Check the `changes` job's log before citing a green.
