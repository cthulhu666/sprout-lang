# bigint-v0 arc retro — what the rework cost, and why (2026-09-25)

Retro question: the `bigint-v0` arc (`812de7c7`..`da6860c0`, 2026-09-22 → 2026-09-25)
took 18 commits to deliver four stages. What made the other fourteen necessary, and
what process, tooling or language change would have removed them?

Scope: `docs/bigint-v0.md` Stages 1–4 and everything that landed to fix them. Companion
docs: `docs/int-overflow-policy-decision.md`, `docs/bitwise-int-ops-v0.md` §5.6.

## 1. The shape, counted

Sixteen substantive commits (two unrelated backlog filings excluded):

| kind | n | commits |
|---|---|---|
| design doc | 1 | `812de7c7` |
| stage / planned feature | 5 | `61fd1617` `17918713` `c595f62c` `3b7e9f17` `4c163d43` |
| **rework on an earlier arc commit** | **10** | `fb0dfa68` `15fd2545` `e6316697` `b0cd0fe5` `eca5626b` `36eafcec` `5526173f` `6e6fdcb4` `66403d37` `da6860c0` |

**62% of substantive commits were fixes to earlier arc commits.** Every stage accrued
post-merge follow-ups, across at least four ensemble review rounds (PRs #340, #344,
#347/#348, #351 twice).

## 2. Stage 1 is the outlier, and the reason generalises

Stage 1 attracted rework in four separate commits — `fb0dfa68`, `15fd2545`, `5526173f`,
`6e6fdcb4` — the last two a month after it landed. Stages 2–4 attracted one each.

Stages 2–4 added *new modules*. Stage 1 changed *the meaning of an existing operation
every module already used*. New code is reviewed as a unit; a semantics change's blast
radius is the entire pre-existing codebase, and nothing enumerates it.

The clearest instance, from `fb0dfa68`'s own message: `iface_codec.parse_unsigned_atom`
kept an unguarded `acc * 10 + digit`, so **the decoder aborted on the encoder's own
output**. Its round-trip test covered no boundary value, so it stayed green.

**Carry this forward:** a totality or trapping change is not "done" when it compiles. Every
caller's existing failure branch was written for a *different* failure, and the type checker
cannot see the difference. `docs/bigint-v0.md` §5.2 concluded "All 19 call sites already
match on `Maybe`, so no caller changes" — true of the types, false of the behaviour, and it
cost two separate bugs (`iface_codec`, then `json.p_number` a month later).

## 3. Defect taxonomy

| class | n | examples |
|---|---|---|
| Semantics-change fallout — compiles, means something new | ~5 | `iface_codec` aborting on its own output; `json` rejecting conformant integers; `rng_hash2` trapping on a large coordinate; `pow10(INT_MIN)` aborting |
| Claim drift — a comment or doc asserts something now false | ~6 | `builtins-reference` "arithmetic wraps"; `bits.sprout` "still open on that"; policy doc `UNIMPLEMENTED`; `parse_double`'s own "exact within Int" |
| Test shaped to pass | 3 | round-trip with no boundary value; `approx`-only fraction assertions; literal-only `bit_shl` cases, which constant-fold |
| Algorithm chosen from unrepresentative data | 2 | the per-digit fraction rewrite, tuned on 309-digit runs; its replacement, tuned on 3-digit statistics |
| Guard removed as collateral | 2 | `bit_shl`/`bit_or` cannot overflow, so nothing noticed a 17th hex digit; `felem_of_bigint`'s `Nothing` arm returning zero |

Two of these classes are worth separating from "bugs". **Claim drift** and **test shaped to
pass** are not defects in shipped behaviour — they are defects in the project's ability to
*detect* defects, and they are why the other classes survived as long as they did.

## 4. Three planned items were silently dropped

`docs/bigint-v0.md` §9's Stage 1 list named three things that did not happen as written:

1. the `iface_codec.parse_unsigned_atom` fix — dropped entirely; found by the #340 review
   after shipping a reachable abort;
2. the `stdlib/rng.sprout` header comment — applied, but the *substantive* half was missed:
   the coordinates needed the same reduction as the seed, found by the #351 review;
3. the `stdlib/bits.sprout:64` parenthetical — deferred by §11 to Stage 1, not picked up by
   Stage 1, and fixed only in #352 — whereupon #353 had to remove the deferral note itself.

**A design doc's implementation list is not a checklist: nothing verifies it was executed.**
That single gap accounts for three separate later commits.

The `bits.sprout` deferral also recorded a *wrong reason* — that editing a `stdlib/*.sprout`
comment forces a reseed "for no behavioural gain". It does not, for that file: `bits.sprout`
is seven `extern fn` declarations and nothing else, so it emits no line-numbered IR and
`just seed-fp-ack` is the whole cost. A wrong reason in a deferral note outlives the
deferral and deters the next person.

## 5. What the gates could not see

Everything above shipped green: `just test` (407 suites), `just ci-fast-gates` (47), golden
IR, and the bootstrap fixed point. That is not a gap in the gates so much as a category
boundary. **Gates verify behaviour against recordings that the author regenerates.** They
cannot check whether a sentence is true, whether a fix generalises beyond its test cases, or
whether a threshold came from representative data — which is every row of §3 except "guard
removed as collateral".

Two non-obvious catches are worth recording, because both were side effects:

- **Golden IR caught a dictionary escalation.** Writing the `json` non-finite check with
  `and_then` pulled `Monad Maybe`'s whole superclass chain (Applicative + Functor eta
  wrappers) into every consumer of the module. No test can see that; the IR diff can. It was
  rewritten as a plain `match`. (`stdlib/prelude.sprout` already records the precedent: an
  import that added 62 unused wrapper bodies grew a consumer ~12%.)
- **The TDD guard fired on a comment edit** — `stdlib/bits.sprout` with no test touched —
  and the gap underneath it was real: the §5.2 exemption was pinned only with literal
  operands, which `test_bits.spr`'s own header warns are constant-folded and "never reach
  the guard CFG at all". A crude heuristic surfaced a genuine hole.

## 6. What worked, and should not be traded away

- **The ensemble review found what the gates structurally could not**, twice, including
  against the fix for its own previous round. Eleven findings verified across two runs,
  **zero refuted** — every one arrived with a reproduction the skeptic re-ran.
- **The verify pass narrowed rather than rubber-stamped.** One verdict confirmed a defect
  while explicitly flagging a sub-claim ("old shape wrong in 2% of random fractions") it
  could not reproduce.
- **Vendored test vectors caught nothing and are still right.** The 484-vector Wycheproof
  suite means the Montgomery rewrite and the remaining `P3` optimisations are safe to
  attempt. Zero findings landed in `bigint.sprout`, `modular.sprout` or `p256.sprout` —
  the arithmetic where a missed carry would be catastrophic came through clean.

## 7. Findings, ordered by effort-to-value

Each is filed in `BACKLOG.md`; this section is the rationale, not the tracker.

1. **Differential harness for `parse_double`** (tooling, ~40 lines, highest leverage). All
   six `parse_double` defects across two review rounds were found by comparing against a
   correctly-rounded reference. A random-run-per-digit-length harness asserting ≤1 ULP would
   have caught every one before either review. It is also what finally settled the algorithm
   choice: a 1500-runs-per-length table cost one command and resolved in seconds what two
   rounds had argued about.
2. **Doc-staleness gate** (tooling, small). Catches the mechanical half of §3's claim drift:
   a title saying `UNIMPLEMENTED` while the doc's own `**Status:**` says `DECIDED`.
3. **Wrapping arithmetic operators** (language). `rng_hash2` genuinely wants mod-2⁶⁴
   arithmetic — it is a hash. `bit_shl` got an exemption by fiat; hash multiplication did
   not, so the function was rewritten to pre-reduce, and the first attempt reduced only the
   seed. `int-overflow-policy-decision.md` §5 already anticipates these for hot loops; this
   arc supplies a *correctness* motivation, which is the stronger one.
4. **Review ledger is per-worktree**, so it cannot answer its own question: the arc's four
   earlier review rounds are recorded nowhere machine-readable.
5. **Hex float literals** (`0x1p63`), **a wide-multiply primitive**, **surfacing new
   dictionary wrappers in the golden IR report**, and **dropping unused superclass
   dictionary slots** — each motivated by a specific incident above, each independent.

## 8. Process changes

Four were proposed. Two were adopted into `AGENTS.md` in the same change; two were declined,
and the reasons are recorded here so they are not re-proposed from the same evidence.

**Adopted.** The test applied was: *did its absence cost something more than once?*

- **A stage's implementation list is closed out at landing** — every bullet struck through or
  annotated with the `BACKLOG` entry it became (Docs & Spec #2). Three dropped bullets, three
  later commits, one of which shipped a reachable abort. Evidence in §4.
- **A totality or trapping change states what each caller's failure branch meant** — not that
  it still compiles (Design Change Process). Two bugs a month apart from one false inference.
  Evidence in §2.

**Declined.**

- *Review before merge per stage.* The reviews already ran per PR (#340, #344, #347/#348) and
  the rule would not have changed what happened. What the whole-arc pass added was
  cross-stage reach — finding Stage 1's fallout in `json` needed Stage 1 and Stage 4 in one
  view. The useful version is "a multi-stage design gets one final review over the completed
  arc", which needs no rule to perform.
- *When closing a documented deferral, grep for citations of the deferral, not only for the
  stale text.* True, and it is why #353 was needed after #352 — but it is a search habit, not
  a repo policy, and Docs & Spec #1 already implies it. Recorded here instead, where a reader
  meets it in context.

A rule in `AGENTS.md` costs every future contributor attention whether or not it applies to
them, so a lesson that is true but happened once belongs in a dated doc like this one.
