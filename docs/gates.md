# Gates

`AGENTS.md` carries the rules. This file carries what each gate checks, how it has been defeated,
and the evidence — read a section when its gate fires, when you are about to override it, or when
you are changing it.

## Definition of Done — `just dod`

Runs the Definition of Done's gates sequentially, with `verify-bootstrap-fixed-point` first: it
compiles the current compiler sources with stage-1 and compares the result with the seed, so an
edit anywhere in the compiler's closure (the prelude and `stdlib/*.sprout` included) that the seed
does not reflect stops it before any gate runs the old compiler. `seed-stale` would not: it hashes
`stdlib/compiler/` only. Every gate is a `just` dependency, and `just` runs a
dependency once per call, so naming a gate that `gate-quick` or `test` already contains costs nothing. It does not
fan out, unlike `ci-fast-gates`, which also overlaps `test` (conformance, type errors, linear grid).

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

### A change to rooting itself: diff with the rooting stripped

A change that rewrites rooting rewrites nearly every function's roots, so no diff of the goldens
can be read, and "the property to check" is that **nothing else moved**. `scripts/golden_ir_root_strip.sh
<old.ll> <new.ll>` strips the rooting from both sides — the push, pop and scan-push calls, every
`alloca i64`, the frame's `alloca`, `getelementptr`s and memsets, the stores into any of them,
`llvm.stacksave`/`stackrestore`, and the `declare`s of all of these — renumbers `%t$N` by first
appearance per function, and prints the unified diff of what is left (exit 1 if any). Run it over
every `tests/golden/ir` file: old = the committed snapshot, new = the output of `just
ir-golden-snapshot` (copy the old ones out first). The residual must be empty. If it is not, read
it; do not regenerate. Frame-slot rooting (2026-10-26) was empty over all 75 goldens and over a
512k-line self-compile of the compiler, and the script reports a one-constant change in a copy
of the new module as a 9.8k-line residual, so it is not blind.

It does not see the stores into TCO slots or a missing slot store, which it strips with the rest:
`just verify-roots` is the check for those.

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
| `just seed-stale` | `shasum` of `stdlib/compiler/*.sprout` vs the `; seed-fingerprint:` line at the top of the seed | `.githooks/pre-commit`, when `stdlib/compiler/` is staged |
| `just verify-bootstrap-fixed-point` | the re-emitted IR is byte-identical | **CI** (`.github/workflows/ci.yml`) |

Edit only a comment in a compiler source and the source bytes change while the emitted IR does not,
so `seed-stale` reports STALE while the fixed point holds. **CI runs only the fixed-point check, so
a red `seed-stale` is not evidence CI will fail.** The git pre-commit hook does run it, so a commit
that stages `stdlib/compiler/` needs a reseed even then; its diff is the fingerprint line alone,
which confirms the IR did not change. The verify-then-ack bypass above is for the seed gate, on an
IR-unchanged edit to `stdlib/*.sprout` outside the compiler. Note the seed gate compares *tree hashes*,
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

Wired as a Stop hook in `.claude/settings.json`. Once per distinct working-tree state it blocks the
turn and returns a path-aware checklist: idiomatic Sprout for `.sprout`/`.spr`, `docs/guidelines.md`
for `stdlib/`, GC/rooting for `stdlib/compiler/` and `runtime/`, docs+spec sync always. Stop is the
only unconditional exit from a turn, so it is the only event that can gate "the change is finished"
— `PostToolUse` fires mid-edit and cannot block. It cannot loop: the session's first Stop records
the tree as a baseline, a state already shown is never shown twice, and `MAX_BLOCKS_PER_TURN` caps
any one user turn. Generated artifacts (`bootstrap/compile_driver.ll`, `tests/golden/ir/`, `build/`,
`.claude/`) are invisible to it, so a reseed or a golden snapshot never trips it. Test with `just
test-review-gate`.

It blocks by printing `{"hookSpecificOutput": {"additionalContext": ...}}` and exiting 0, which Claude
Code shows as "Stop hook feedback", not as a hook error. The report asks for one-line verdicts per
item, groups the changed-file list by directory (two or more paths in a directory print as
`<dir>/ (N files)`), and shows each item's instructions once per turn; later blocks in the same turn
show only its title.

Re-fires on a changed tree are kept on purpose. Of 778 firings (2026-09-07 to 2026-10-31), the
agent's reply reported a real defect in 53 (classified per reply, a sample checked by hand).
Suppressing a re-fire when no new file appeared would have lost 18 of them: second looks at the
same files found defects at 8%, first looks at 7%.

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

## LinVec — `just linvec-smoke`

`linvec_set`, `linvec_push` and `linvec_truncate` are pure to the checker and a C write underneath,
behind `unsafe_pure`, so their soundness rests on linearity (docs/persistent-array-v0.md §5.3,
docs/unsafe-primitives-v0.md). `just test` links at `-O0` and cannot see most of what could
go wrong, so `scripts/linvec_smoke.sh` checks, in `just ci-fast-gates`:

- `tests/stdlib/test_linvec.spr` at `-O2`, where an optimizer could move a borrowed read past a set.
- The same test against a copy of the stdlib root where the region of `linvec.sprout` between
  `# oracle-begin` and `# oracle-end` (`linvec_set`, `linvec_push` and `linvec_truncate`) copies
  (`vec_set`, `vector_append`, `vec_slice`). Both must print the same: a difference is a linearity
  hole seen as a wrong answer. The root is a command-line argument, so nothing in the compiler is
  involved. It finds only holes the test reaches.
- A set, scatter or accum out of range exits non-zero with its message (`vector_mutset: index out
  of bounds` for a set).
- Allocation counters (`SPROUT_DEBUG_ALLOC`) match between runs that differ only in the number of
  sets, so a set allocates nothing, and grow by one vector per `linvec.new`.
- A `linvec.get` matched with a binding compiles to a worker over `vector_get_unboxed`, and
  `linvec_set` calls `vector_mutset` and builds no closure.
- The two shape rules of `unsafe_pure` (a lambda literal; no use as a value), in a copy of the
  stdlib root with a case appended to `linvec.sprout`, since a user module cannot get past the
  reference gate.
- Bounds-check elimination (`SPROUT_TRACE_BCE=1`) versions no loop in a `stdlib.linvec.*` or
  `stdlib.linheap.*` declaration: the pass counts only `!{IO}` code as changing a length, which
  stays sound while a `LinVec`'s `Vector` never reaches an access that pass rewrites
  (`docs/bounds-check-elimination-v0.md`, condition 4). This checks their own loops, not the
  alias premise, which rests on linearity and the abstract type.

## In-place reuse — `just linear-reuse-smoke`

`ast_to_ir` writes a rebuilt linear value into the consumed cell (docs/linear-reuse-v0.md), which
is memory-unsafe if a premise of `linear_check` leaks. `just test` counts `reuse_ctor` in printed
IR (`test_linear_reuse_ir`) and checks values (`test_linear_reuse`); `scripts/linear_reuse_smoke.sh`
adds what only `-O2` and the runtime counters show, in `just ci-fast-gates`:

- The `Grid` sieve, a three-constructor CSV splitter and a linear binary search tree
  (`tests/linear_reuse_smoke/`) allocate the same number of objects (`SPROUT_DEBUG_ALLOC`) at two
  input sizes, so a step allocates nothing. The tree inserts keys `i * 37 mod 100`: once all 100
  are present, an insert rebuilds its whole path in place.
- All print the answer their input has.

`test_linear_reuse` is also a `SPROUT_GC_STRESS=1` workload: `tock` is entered by a tail call, so
only the token's rooting keeps its cell from being recycled under the rebuild.

## CPR declares — `just cpr-declare-check`

Compares each `declare … @<name>_unboxed(…)` in emitted IR with clang's `define` of that C
function, return and parameter types both. The registers agree even when the types do not, so a
mismatch passes every test, but LLVM will not inline a call whose type differs from the callee's,
and whole-program linking (`scripts/link_whole_program.sh`) silently keeps the call.

Added 2026-10-19, when the declares said `{ i64, i64 }` and clang compiled `SproutUnboxed2`, then
a two-word struct, to `[2 x i64]` on aarch64: a base64 loop kept every read as a call. x86_64
lowers that struct to `{ i64, i64 }`, so the gate runs on both: in `just ci-fast-gates` (Linux
x86_64) and as its own step in the macOS (arm64) CI job.

The declares now return `i128`, which aarch64 clang and x86_64 clang >= 22 both match. x86_64
clang <= 21 lowers `unsigned __int128` as `{ i64, i64 }`, so `scripts/check_clang_cpr.sh` probes
the lowering, not the version number (Apple clang 17 passes): LLVM discovery skips a clang that
fails it, `just bootstrap-from-seed` refuses one, and `just test-check-clang-cpr` tests the probe.
CI installs clang 22 from apt.llvm.org (`scripts/ci_install_llvm.sh`).

## Constructor tag fold — `just ctor-tag-fold-check`

Whole-program links `tests/ctor_tag_fold/reads.sprout` (a `Vec` loop, a `Bytes` loop and a
channel send/recv loop) to optimised IR and checks four things:

- Each `@main.*` function calls neither `@find_ctor_tag_by_name` nor `@sprout_ctor_tag_lookup`
  and reads no tag-cache global (any `sprout_tag_cache` name).
  An inlined `_unboxed` read that still carries the lazy tag lookup grows its caller past
  LLVM's inline threshold, and hot loops such as nqueens slow down. The check also fails when a
  fixture function still calls `@vector_get_unboxed` or `@bytes_get_unboxed`, or when no fixture
  function is found, because it proves nothing about a read that was never inlined.
- Only `@sprout_ctor_tag_lookup` calls `@find_ctor_tag_by_name`. Any other caller scans the
  constructor table by name on every call; the report names each function, capped at 15.
- `@sprout_chan_make_got` (builds `stdlib.chan.Got`) and `@string_concat_many` (reads `Cons` and
  `Nil`), runtime functions using constructors every program registers, have no lookup call and
  no cache reference. The check fails when either is missing from the linked IR.
  `find_ctor_tag_by_name` is `noinline, cold`, so a stray caller stays a visible call.
- Every `@"sprout_tag$..."` name in the fixture IR and in `bootstrap/compile_driver.ll` keeps `$`
  to the `.` separator and a leading `$entry$`; `$$` elsewhere would let two names collide.

`tests/c_runtime/ctor_tag_fallback.c` pins the other side: with no constant supplied, the
runtime returns the registered tags. The constants are emitted by
`ir_lowering.collect_ctor_globals` ([compiler-internals.md](compiler-internals.md) §CPR extern
ABI). The recipe runs the stage-1 binary, so an emitter change shows here only after
`just refresh-seed`.

## Bounds-check elimination — `just bce-gate`

Compiles each `tests/bce_fixtures/*.spr` twice, with the pass on and with `SPROUT_OPT_OFF=bce`,
and runs every case (the case name is `argv[0]`) against both. Three things must hold: the pass
fires (a `$fast` clone with it on, none with it off); on and off print the same text and exit with
the same status; both equal `tests/bce_fixtures/expected/<fixture>.<case>.out`. The cases break
one term of an entry test each (past the end, below zero, `Int` bounds that would overflow), so a
failing test that reached the unchecked clone would read out of range instead of printing the
original panic. `scripts/bce_gate.sh --snapshot` rewrites the goldens from the pass-off build;
read the diff before staging it, since the off build is the behaviour being preserved.

The IR-shape half (what qualifies, what is left alone and why) is `tests/stdlib/compiler/test_bce.spr`.
Wired into `just ci-fast-gates` as `bce-gate`.

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

## Prelude private names — `just prelude-private-names`

Rejects a string literal in `stdlib/compiler/` that names an unexported prelude `fn` or `let`. The
bundler rejects such a name in source (spec §3.1), but a name the compiler writes itself after
bundling never meets that check, so it would reach a private helper silently. The rule holds only
while every generated name is exported; this keeps it so. Export the helper, or generate a name that
is exported.

## Stdlib reference — `just stdlib-reference`

Every top-level `stdlib/*.sprout` module but the prelude needs a `## stdlib.<name>` section in
`docs/stdlib-reference.md`. Added 2026-10-07 when that file was split out of
`builtins-reference.md`: 20 of 35 modules had a reference, and nothing had noticed the other 15.

They are listed in `UNDOCUMENTED` in the script so the gate was green on arrival, and the list only
shrinks — a listed module that gains a section fails until it is removed from the list, and so does
a listed name with no module file. Nested modules (`fs/path`, `math/int`) are out of scope, as for
the REPL's completion list. It checks that a section exists, not what it says.

## Runtime warnings — `just runtime-warnings`

Compiles each `runtime/*.c` with `clang -fsyntax-only -Wall -Wextra`
(`scripts/check_runtime_warnings.sh`), once per build variant: default, `-DSPROUT_GC_PROFILE` and
`-DSPROUT_GC_AGEPROF_STORES` (the only compile-time switches the justfile passes). When the local clang major equals the one
`scripts/ci_install_llvm.sh` pins (`LLVM_VERSION`), it adds `-Werror` and any warning fails the
gate. On any other major it prints the warnings, says they are not enforced, and exits 0.

`-Werror` is gate-only. User builds (`stdlib/compiler/analysis_service_driver.sprout`) compile the
runtime themselves, and a warning a newer clang adds must not break them. The same rule keeps a
newer local clang from failing the gate: only CI's pinned major enforces. Under `CI` a major
mismatch fails, so a toolchain drift there cannot turn the gate warn-only. `just
test-runtime-warnings` plants an unused variable and checks all four outcomes, then runs the gate
on the real runtime. The `macos` CI job runs the gate pinned to its own clang major
(`--print-local-major`), so `__APPLE__` code is checked under `-Werror` too.

## Runtime word casts — `just runtime-word-casts`

Counts **sites** in `runtime/*.c` and `runtime/*.h` on clang's JSON AST
(`scripts/runtime_word_casts.py`), per enclosing top-level decl, and fails on any outside the exits,
the `runtime/ADDRESS_CASTS` functions. Only types tell `(long long)p` from `(long long)len`,
which is why it reads the AST. Rule 1 of
`docs/string-header-invariant-v0.md` §4.3 (rules 1 to 3). `uintptr_t` is the address type: a pointer
becomes one, and the gate watches where a `uintptr_t` or `intptr_t` value goes. Rule 1 counts
sites. A site is either

- a cast with castKind `PointerToIntegral`, or
- an `IntegralCast` (implicit or explicit) whose operand is spelled `uintptr_t` or `intptr_t` and
  whose destination is a signed integer of 64 bits or more after typedef resolution (`long`,
  `long long`, `int64_t`, `__int128`), unless the operand is itself a counted `PointerToIntegral`
  cast. So `(long long)(uintptr_t)p` outside a listed function is one site, and `long long x = u;`
  with a `uintptr_t u` is one. Conversions to unsigned or narrower types (`size_t`, `int`) are not
  sites. The operand's spelling is read from the child expression with parentheses skipped.

- **Exits** are `runtime/sprout_word.h:sprout_str_word_in` and `runtime/sprout_word.h:sprout_ptr_word`
  (`EXITS` in the script), keyed `file:name`: a same-named function in another file is not exempt.
  They are exempt from rule 1. `sprout_str_word` and `sprout_str_of_word` are macros over the
  `_in` functions, which take the caller's `__func__` for the HDRCHECK failure message.
- **Rule 2** (everywhere): a call to `sprout_ptr_word` fails when its argument, with
  pointer-to-pointer casts (`BitCast`, `NoOp`, implicit or explicit) and parentheses stripped, has
  at any layer the type pointer to `char`, `signed char` or `unsigned char`, whatever the
  qualifiers. So `"x"`, `(void*)"x"` and `s.hdr_p` all fail: a String leaves through
  `sprout_str_word`.
- **Rule 3** (outside `WRITERS` in the script, keyed `file:name`: `sprout_str_alloc`,
  `sprout_str_intern` in `sprout_runtime.c` and `sprout_str_of_word_in` in `sprout_word.h`): an
  `InitListExpr` or `CompoundLiteralExpr` of type `SproutStr` fails (`= {p}`, `= {0}`, designated,
  `(SproutStr){p}`), and so does any `MemberExpr` naming `hdr_p` on a `SproutStr` whose parent
  (parentheses skipped) is not an `LValueToRValue` cast (`s.hdr_p = p`, `&s.hdr_p`, `s.hdr_p++`,
  `+=`). A prvalue member passes, because it cannot be written: older clang (CI's Linux) puts no
  cast on `f().hdr_p`. A read passes: `s.hdr_p[i] = c`, `memcpy(s.hdr_p, …)`, `strlen(s.hdr_p)`.
  A pointer pun fails too: any `BitCast` (implicit or explicit), in either direction, between a
  pointer to `SproutStr` (typedefs resolved, any depth) and a pointer to a type other than
  `SproutStr` or `void`. `*(SproutStr*)&raw` builds a String from a `char*`; `*(char**)&s = raw`
  writes `hdr_p`. `void*` is exempt on both sides, so `malloc` into a `SproutStr*` and
  `memcpy(&a, &b, sizeof a)` pass.
- **`ADDRESS_CASTS`** lists functions whose casts are address arithmetic, address ordering or kernel
  tokens, one `# reason` each, keyed `file:name` (`runtime/sprout_runtime.c:region_find`), so a
  same-named static in another file is not exempt. A listed function is checked for:
  - **A1**: every `PointerToIntegral` cast in it has destination type spelled `uintptr_t`.
  - **A2**: it converts no `uintptr_t` or `intptr_t` value to a signed integer of 64 bits or more.
    A cast result that leaves through a field store, a call argument or a global has to pass
    through such a conversion, so this is what stops it.
  - It does not return an integer type of 64 bits or more (`long`, `long long`, `__int128`, or a
    typedef of one such as `uintptr_t` or `size_t`) and takes no pointer to one (an out-param like
    `long long* out_id`). A by-value 64-bit parameter is fine.
  - A listed name with no cast on the host, or no definition, fails.

  A name may carry one tag, `darwin` or `linux`: it is then read only on that host and ignored on
  the other, so a cast there fails rule 1. An unknown or second tag is malformed.
- Every `runtime/*.c` is scanned (a glob, like the justfile's `runtime_src`), so a new file is
  covered without editing the gate.
- `just test-runtime-word-casts` is the CI entry for this gate: its case (a) runs the checker on the
  real `runtime/`, and the other cases plant defects in copies of `runtime/` (a cast in an exported
  String producer, a cast in a function that has none, a stale entry, a function returning
  `long long`, `uintptr_t`, `long` or `intptr_t` or taking a `long long*`, a same-named static in
  another file, a cast in a new `runtime/*.c`, a stale or unknown platform tag, an A1 cast to
  `unsigned long`, A2 conversions through a field store, a call argument, an implicit local
  conversion and an `intptr_t`, a `sprout_ptr_word` outside `sprout_word.h`; for rule 2 a literal,
  a `(void*)` cast, `hdr_p` and an `unsigned char*`; for rule 3 an initializer list, `{0}`, a
  designated initializer, a compound literal, an assignment through `.` and `->`, `&s.hdr_p`, `++`,
  `+=` and a `sprout_str_intern` outside its file; a pun in each direction, through an alias and as
  `*(SproutStr*)&raw`) and check that each fails and names the decl; a
  `uintptr_t` to `size_t` or `int` conversion, a `sprout_ptr_word` of a non-char pointer, reading
  or filling through `s.hdr_p`, `malloc` into a `SproutStr*` (cast or implicit), `memcpy` between
  two `SproutStr` and the real writers must pass. It scans four
  trees (the real one, one with mutants, one with planted functions, one with rule 2 and 3
  functions) and re-reads each decl table
  through `--scan-cache` for the cases that vary only `ADDRESS_CASTS`. The cache is keyed on a hash
  of `runtime/*.c` and `*.h`. Case (a) passes `--scan-cache` with a file that does not exist yet, so
  it scans and writes and reads nothing, and its table serves the copy of the real tree. `just
  runtime-word-casts` passes no flag, so it never reads a cache. It is not listed in
  `ci-fast-gates` or `gate`; running it there too would scan the real tree a second time.

**What it does not check:** it stops the accidental cast, not an author who routes around it.
- Laundering through arithmetic into a non-address type. Example: a `uintptr_t` copied into an
  `unsigned long` or `size_t` variable (an allowed conversion) and converted to `long long` from
  there. The operand is no longer spelled `uintptr_t`.
- `memcpy` of a pointer into a word, and union punning: no cast node exists.
- A pointer reinterpreted through `void*` (`*(long long*)(void*)&p`): no `PointerToIntegral` node.
- A zero-initialised `static SproutStr` (`static SproutStr s;`): no initializer list exists.
- `memcpy` or a union into a `SproutStr`: no `hdr_p` member access or initializer is written.
- A `char*` laundered through a `void*` variable before `sprout_ptr_word` (`void* v = "x";
  sprout_ptr_word(v)`): rule 2 sees only the argument's own layers.
- A `char*` laundered through `void*` into a `SproutStr*` (`SproutStr* p = (void*)&raw;`): the
  pun rule exempts `void*` on both sides.

**It sees one platform.** The AST holds only the host's `#ifdef` branches, so a decl that casts under
`__APPLE__` or Linux only is counted on that host alone. Tag its `ADDRESS_CASTS` entry with that
host; an untagged entry is stale on the other. `just linux-run runtime-word-casts` checks Linux.

## GC safety linter — `just gc-safety-check --strict`

`scripts/gc_safety_check.sh` finds a function body by counting braces, so a miscount hides every
later function: the next definition is folded into the open body, and the linter reports "0 issues"
with fewer functions checked. A `'{'` in `json_parse_value`, part of the deleted C JSON parser,
hid ~118 functions that way (303 checked where 421 are). `strip_literals` drops char and string literals (with escapes) and
`//` and `/* */` comments, the latter across lines, before any brace is counted. As a backstop,
`drift` exits 2, with or without `--strict`, when a column-0 definition starts inside an open body,
when depth goes negative, or when the file ends inside a function. `GC_SAFETY_RUNTIME` overrides the
file checked. `just test-gc-safety-check` (in `ci-fast-gates`) lints fixtures where each decoy sits
before a defective function, and one per drift shape. Run it with `GC_SAFETY_SCRIPT=<old copy>`
`GC_SAFETY_KEEP_GOING=1` to see which cases an older linter fails.

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
whole scope per trigger applies no closure and would pass.

**The closure arm counts applications, not allocations.** It first read `closure=`, which moved only
because each `list_member` call built its dictionary as a fresh closure. A capture-free closure is a
static constant now ([static-closures-v0.md](static-closures-v0.md)), so that count stops seeing the
comparisons. The arm reads `closure_apply=` instead, which `sprout_closure_arity_check` bumps under
`SPROUT_DEBUG_ALLOC`: the comparisons themselves. Measured on 2026-10-23 (stage-2): 211 → 212 per
element (100/100), floor 105, the growth bound unchanged at 125/100.

**The fixtures differed in more than element count.** Profiled by allocation site, the remaining
byte growth had two sources. `ir_lowering.walk_ops_for_globals` appended each string global to the
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

### The push-count arm reads what the pass emits

Every arm above prices the compiler, on one block, where "pushes per block" and "pushes per
function" are the same number. None could see
[frame-slot-rooting-v0.md](frame-slot-rooting-v0.md)'s subject: a heap value that lives across many
blocks was pushed again in each block that held a trigger, so the emitted pushes grew with blocks ×
live values while compile cost stayed flat.

`tests/cost/rooting_frame_chain.sprout` is one self-tail-recursive function over a chain of 24
`if`s — 127 blocks, 25 `++` — with two carried heap parameters and a heap value live into each
next `if`. The gate compiles it, cuts out that function's `define`, and counts calls to the two
push functions. The ceiling is the count derived by hand in the fixture's header, not a budget:
**exactly one** `sprout_gc_push_scan_root` (the frame, which `tag` earns by being live across every
later `++`) and **23** `sprout_gc_push_i64_root`, one per stage but the last. Each stage's carried
value is dead after its `++`, so it takes no slot, and the `++` call exposes its operands, so the
stage pushes it once, in the one block that appends it. A push beyond that is a value rooted again
in a block it only passes through. The pre-frame compiler pushed **50** in this function
(2026-10-26, run through this gate: it fails the scan-root check and the ceiling). Floors on blocks
(100) and appends (24) keep the fixture from going vacuous.

It reads the emitted IR of a stage-2 compile, like the arms above, so it needs no reseed first.

## Compiling many functions — `just scc-cost-gate`

The rooting gate's fixtures are one function, so it cannot see a cost that grows with the number of
functions. This one compiles a tail-call chain of 40, 80 and 160 top-level functions
(`tests/cost/scc_chain_{small,large,huge}.sprout`) and bounds objects and arena bytes per added link,
plus their growth when the chain doubles. Same method as the rooting gate: allocation counts, fixture
differences, link counts read from the files, identical headers asserted, stage-2.

It exists because mutual TCO's Phase B found its SCCs by asking "does f reach g, and g reach f?" for
every pair, one graph search per question. That is cubic in chain length: 400 links took 48 s and
33.6 GB of arena (2026-10-04). It was also 55% of the compiler's self-compile allocation, unseen,
because the IR was right. One Kosaraju pass (`scc.sccs_in_dependency_order`): 0.49 s and 362 MB.
On the gate's fixtures, pairwise read 231/100 objects and 264/100 bytes at 2x chain length; the
single pass reads 100/100 and 101/100.

**Each link passes an annotated lambda.** Inference turned the whole type environment into a list
for every lambda, twice, to pick out its `@qualalias:` and `@aliasty:` markers
(`infer.aliases_for_annotations`): 18% of self-compile allocation. `dict_entries_with_prefix` reads
only the run of keys with the prefix. Per link: 21464 objects and 797 KB before, 5404 and 258 KB
after (2026-10-05). Growth read only 109/100 and 108/100 — the environment is mostly the prelude, a
constant — so the ceilings caught it, not the growth arm. Annotated, so the fixture keeps pricing
alias resolution if unannotated lambdas ever skip it.

**The arities alternate 1, 2, 1, 2.** Phase A still runs one search per same-arity tail edge, which
is quadratic on a same-arity chain (BACKLOG). Alternating keeps Phase A out of the measurement, so a
red here means Phase B or something new, not the known debt.

`ci-fast-gates` runs it in one `just` call with `rooting-cost-gate`: both depend on `build-stage2`,
which always relinks, and two parallel entries would write the same binary at once.

## Root verifier — `just verify-roots`

`ir_verify.verify_rooting` checks one rooted `IRFunction`; `SPROUT_VERIFY_ROOTS=1` makes
`ir_pipeline` run it on every function right after `ir_rooting`, print each violation to stderr
(`fn`, block, op index, value) and fail the compile. Unset, it costs one env lookup per function.
The recipe compiles every `tests/smoke_shapes/*.spr`, every example and the compiler itself with
the variable set, on stage-2 so the verifier is the working tree's.

**Checks.** Liveness is recomputed here as a fixpoint over the whole CFG, so it holds with the
`IRTcoBack` back-edge; a phi operand is a use at the end of its predecessor. Then, per block: at
every GC trigger each heap value live after it (not its own result), and each heap operand of an
operand-exposing op, is rooted; `IRUnroot n` never pops more than is pushed; no SSA value is live
into a TCO loop header (the slots are not SSA values).

**Frame rules** ([frame-slot-rooting-v0.md](frame-slot-rooting-v0.md)). The frame is the bottom
entry of the root stack. The entry block starts with an empty stack and `IRFrameEnter` pushes the
frame; every other block of a function with a frame starts with the frame alone. A value is rooted
at a trigger if it is on the stack, **or** the frame is registered and the value either owns a slot
(some `IRFrameStore` names it) or is the `IRTcoLoad` result of a frame-backed TCO slot
(`IRFrameSlot`). Violations, one line each:

- a trigger while the frame is not registered (before `IRFrameEnter`, or after a pop);
- a slot owner defined and not stored before the next trigger, or never stored in its block;
- the terminator: a non-exit block must end with exactly the frame, an exit must have popped it
  (the exits are the blocks whose last op has no in-function successor, minus blocks that never
  return); without a frame the stack must be empty at either, as before;
- a slot with two owners (two values, a value and a TCO slot), a value in two slots, a slot
  outside the frame, a back-edge `IRFrameClear` covering a TCO slot or running past the frame;
- frame ops without an `IRFrameEnter`, a second `IRFrameEnter`, or one outside the entry block.

A block that never returns (`ir_rooting.ops_never_return`) is exempt from the terminator rules.

**Does not check.** What counts as a trigger, a heap value, a use or a def comes from `ir_rooting`
(`op_triggers_gc_summary`, `compute_heap_origin`, `op_uses`, `op_def`), so a wrong classification
there passes. `IRTScalar` needs a `type_kind` witness ([compiler-internals.md](compiler-internals.md)),
which limits who can claim a value is scalar, not whether the claim is right. Operands without a `%` (immediates such as
`0` or `-1`) are not uses. It does not look at the C runtime, and does not require a pop on a path
that never returns.

`ci-fast-gates` runs it in its `compile-cost` entry, which already builds stage-2.

## Object-age instrument — `just gc-ageprof-check`

Calibrates `SPROUT_GC_AGEPROF=1` against two workloads with known answers. Details and the
current bounds: [gc-generational-v0.md](gc-generational-v0.md) §4. It also checks the store
counters' build flag: a default runtime reports them as `off`, and one built with
`-DSPROUT_GC_AGEPROF_STORES` counts `test_mutvec_sort`'s stores.

### An absolute ratio tracked the number of rooted globals, not what the gate measured

`retain_none` asserted `marked_age_ge1/marked_total ≤ 15%`. That workload keeps only ~6 objects
live per collection, so a single permanently rooted global — re-marked every cycle, one age≥1
mark per cycle — is worth ~12pp of it. The bound read **0%** when it was set, 12% once
`pow10_exact_unit` became a rooted global, and 24% when `list_builder_empty` was added, where it
failed. Nothing had regressed: same cycle count, same `freed_total`, 99% of the churn still dying
young, separation 49pp against a 40pp floor. The failure message said "objects are surviving
cycles that should not" about an object whose whole job is to survive.

The fix is to subtract the steady state rather than to raise the bound: a rooted object is marked
once at each age, so buckets 1..14 of `marked_by_age` each hold exactly the root count, and
`marked_age_ge1 - roots × (cycles-1)` is the churn that actually outlived a collection (0% on
both sides of this change, where the raw ratio read 12% and 24%). Raising 15% to 35% would have
worked for exactly one more root.

**Subtracting a floor needs the floor bounded, or a leak hides inside it.** The histogram cannot
tell a rooted global from an object retained for the whole run — both are flat steady state — so
a 500-object leak old enough to fill buckets 1..14 would be read as a 502-root floor and
subtracted away. Hence `roots ≤ 8` beside the flatness check: the count is small, known, and
changes only when someone adds a global, so bounding it costs nothing and is what keeps the
correction from absorbing the thing it exists to detect.

## Generational barrier — `just gc-gen-check`

Runs the write barrier under minor collections ([gc-generational-v0.md](gc-generational-v0.md)
§14.4, §14.5). One program per barrier site Sprout can drive (`tests/gc_gen/`: `ref_write`,
`vector_mutset`, `vector_push` past its inline capacity, `linear_reuse`) runs with
`SPROUT_GC_MINOR=1 SPROUT_GC_GENCHECK=1 SPROUT_GC_STRESS=1`. It must print its known line, run at
least 20 minor collections, and print the same line with `SPROUT_GC_MINOR=0`. The two static
`sprout_analysis_*` helpers cannot be reached without a live analysis service, so
`tests/c_runtime/analysis_strings.c` covers them under the same switches. `just c-runtime-test`
holds the verifier's own test (`tests/c_runtime/gen_check.c`): an unbarriered old-to-young store
must abort with the owner and field named.

**Each site test was confirmed to fail with its barrier removed**, and the fact is the only reason
to trust the gate: ref_write, vector_mutset, vector_push, each analysis helper, and the barrier
call `IRReuseCtor` emits (checked by turning `sprout_gc_write_barrier` into a no-op).

**How it is defeated.**
- It checks only the paths these programs run. A new store site passes it until it gets its own
  program, and a store path with no Sprout driver and no C case is invisible.
- It needs `SPROUT_GC_STRESS=1`. At the default trigger a minor runs per 4096 or more allocations,
  and a young object that dies or stays rooted before the next minor is never checked. A test
  without allocation between the store and the read passes whether or not the barrier exists.
- The verifier can report a scalar that equals a young payload address as an edge (§14.5). The
  report names the owner kind and field index to triage that.

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

**It pins `SPROUT_GC_THRESHOLD=4096`.** The trigger's footprint floor removes the very walk the
sparse answer needs (25 → 2.8 slots per object swept), and the pin turns the floor off. 4096 is the
default, so the readings are the ones above. `gc-trigger-check` tests the floor itself.

## Trigger footprint floor — `just gc-trigger-check`

Tests the floor [gc-trigger-v0.md](gc-trigger-v0.md) §6.2 adds to the threshold, under the default
trigger. Two known answers: `test_gc_walk_sparse` walks fewer than 4 slots per object swept (2.77
at landing, 25 with the floor off), and `test_gc_trigger_adversary`'s free pool (`free=` on the
cycle line) grows by at most 2% over the second half of the run (150,802 → 150,842).

**The second probe is there to fail the floor that looks right.** Counting free slots only in
classes with allocation demand diverges on that fixture, and so does the floor without its `/ 3`.
With the divisor at 1, the pool grew 210,570 → 718,250 and the gate went red; the first probe
stayed green.

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

## Linear grid — `just test-linear-grid`, `just linear-grid-drift`

`scripts/gen_linear_grid.sh <out-root>` writes one program per reach path × misuse
(docs/linearity-bound-retro-2026-10-20.md §5 T2): reject cells in `tests/conformance/linear_grid/`
(`<path>__<misuse>.spr` + `.err` substring), accept cells in `tests/conformance/run/linear_grid_*`
(golden stdout, run by `test-conformance-run`); declarations that fail to parse go to
`parse_error/linear_grid_*` so the pre-commit lint skips them. The generator's header lists the
N/A cells.

- `test-linear-grid` runs `_test-reject` over the reject cells, with an empty xfail list. A cell
  the compiler does not yet reject goes on that list, and flips the gate RED (UNEXPECTED MATCH)
  once it is rejected.
- `linear-grid-drift` regenerates into a temp dir and diffs against the committed files. Edit the
  generator and rerun it with the repo root as out-root; never edit a cell by hand. Its output
  must already be what `just fmt` writes (e.g. `Ctor(x)` spacing): `fmt-check` fails otherwise.
- A review bug in linearity adds its neighbouring reach paths here (retro §6: the half of
  P2 that T2 keeps).
- A new linearity check gets a column, not only standalone fixtures. The `mode` column (§6.6)
  was first written as single-declaration fixtures; a group of one has one position, so an
  error reported at the group's first member instead of the member holding it passed, and
  only the `annsib`/`unannsib` cells catch it. Its cells mark the reported line with `#~^
  ERROR` (`scripts/error_line_marker.sh`, run by `_test-reject`; `just
  test-error-line-marker` tests it).
- A new way to declare a linear type gets a header, not a row. Every cell on the linear ADT
  header repeats as `w_<name>` on a `wrap linear` (docs/linear-wrap-v0.md §8).

## ASan gate — `just test-asan`

Builds the runtime and every test binary with `-fsanitize=address,undefined` and runs four things:
`c-runtime-test-asan` (`tests/c_runtime/run.sh` with `SPROUT_C_RUNTIME_SANITIZE=1`),
`asan-self-compile` (the sanitized and an unsanitized stage-2, both linked from one stage-1 IR, emit
the driver's IR, which must match byte for byte), and `test-stdlib-asan-core` / `test-stdlib-asan-compiler` (the `_test-stdlib` suites
with `sanitize=asan`). Runs use `halt_on_error=1`, so a UBSan report fails the test instead of
printing. CI runs it as four jobs, `asan-core-{1,2}` and `asan-compiler-{1,2}` (each suite split
with `SPROUT_TEST_SHARD`, so no job outlasts `test-gates`), in parallel with the shards and under
the required `test` check; `gate` runs it through `test-asan`.

A stray `payload-8` read on a headerless String lands in a malloc'd buffer's left redzone and is
reported at `sprout_hdr_of` with the allocation site (`docs/string-header-invariant-v0.md` §4.5).

**What it cannot see.**
- GC regions hold many slots in one block: arena chunks are `mmap`'d and untracked, an overflow
  region is one `malloc`. An overread between slots is invisible; only a String in a large object,
  `malloc`'d alone, is caught.
- A literal is caught only when an instrumented global happens to precede it (§4.5).
- Leaks are off (`detect_leaks=0`): the arena is never freed.
- Code no test reaches.

**How it could be defeated: a silent unsanitized fallback.** `run.sh` falls back to an unsanitized
build when the sanitizer link fails, which is what a runner without `libclang-rt` does. In
sanitize mode the fallback is an error, and `tests/c_runtime/asan_control.c` is the guard behind
it: it hands `str_eq` a `strdup`'d buffer, exits 0 unsanitized, and the case demands an ASan
`heap-buffer-overflow` report naming `sprout_hdr_of` or `str_eq`. A build that lost the flags
fails there. Do not drop the case.

**Linux.** The green-thread tests switch stacks with `swapcontext`, and `runtime/sprout_context.h`
has no `__sanitizer_start_switch_fiber` / `__sanitizer_finish_switch_fiber` annotations. If CI
shows ASan false positives in those tests, annotate `sprout_ctx_switch`; do not skip the tests.

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
