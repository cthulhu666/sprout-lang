# A Lean model of Sprout's core typing rules (v0)

Status: **approved 2026-11-17; stage 1 in progress.** Non-normative. Proposes tooling; changes
no language rule.
`docs/spec-v0.md` stays the source of truth. The model is checked against it, never the reverse.

## 1. Problem

The type checker's soundness bugs are found by people writing misuse programs by hand.

- `docs/type-system-review-2026-08-13.md` lists 7 soundness findings in `infer.sprout` and
  `unifier.sprout`; 5 were untracked when it was written.
- `docs/linearity-bound-retro-2026-10-20.md`: two PRs took 8 review rounds, 7 of which confirmed
  at least one bug. Its §3 causes: a check removed before its premise held; binding groups never
  designed as a whole; `just test` green every round because "the tests could not see it"; most
  bugs found by "a reviewer writing a misuse program and checking that master rejects it".
- `scripts/gen_linear_grid.sh` made part of that search systematic, but each cell's expected
  verdict is still written by hand, and only for the reach paths someone thought of.

Two different failures hide here:

1. **The rules are wrong.** The spec accepts a bad program. No amount of testing the compiler
   against the spec finds this.
2. **The code does not follow the rules.** The spec rejects a program, the compiler accepts it, or
   the reverse. Today only a reviewer's misuse program finds this.

**What this would have caught.** Of the retro's 16 bugs (§2), 5 fall inside the calculus of §4:
the 4 binding-group bugs and 1 of the 2 premise removals. The other premise removal came through
an instance head; the 5 "uncovered surface" bugs came through externs, stdlib callers, class
methods and a spec example; 3 were diagnostics and 2 parser bugs. Those are outside §4 or are
non-goals. The model is a tool for one class of bug, not for all of them.

## 2. Goals and non-goals

Goals:

- **G1.** A Lean 4 model of a core calculus ("Sprout Core", §4) with the spec's rules for HM
  inference, effects, match exhaustiveness and linearity.
- **G2.** Machine-checked theorems that the model's rules are sound where the spec claims they are
  (§5). This targets failure 1.
- **G3.** The model's checker runs as an executable oracle. Generated programs go to both the
  oracle and `compile_driver_bin_stage1 --phase check`; a disagreement is a bug in the compiler,
  the model or the spec (§6). This targets failure 2.
- **G4.** A gate that fails when a modelled spec section changes and the model has not been
  checked against the change (§7).

Non-goals:

- Proving the compiler correct. `stdlib/compiler/` is ~57k lines of Sprout; Lean cannot reason
  about Sprout source without a formal semantics of all of Sprout. Out of reach (§3).
- Codegen, IR, rooting, the runtime, the GC, the scheduler. Lean does not read C or LLVM IR.
- Typeclasses, records, modules, externs, GADTs, existentials, borrowing and the `<-` bind in v0
  of the model (§9 says which come later).
- Closing the spec's effect-variable channel. Spec §7 rule 8 says an effect arriving through an
  effect variable is unchecked (`fn f(x: Int) -> Int !{e}` whose body prints "launders the same
  way"). The model follows the spec, so it accepts these programs too; T3 (§5) is stated around
  them.
- Completeness or principality of inference. The spec already admits non-principal cases
  (`type-system-review-2026-08-13.md`).
- Matching diagnostics. The oracle compares verdicts, not messages.

## 3. Prior art

| Project | Prover | What is proved | Model used as a test oracle? |
|---|---|---|---|
| Cedar (AWS) [1] | Lean 4 | Validator soundness, authorizer properties, termination | Yes: the Lean model and the Rust code get the same generated inputs and must agree ("validator parity", "authorizer parity") |
| Wasm, Watt CPP 2018 [2] | Isabelle/HOL | Type safety (progress, preservation); a verified checker and interpreter | Fuzzed the interpreter against engines; a later verified interpreter became a Wasmtime fuzzing oracle [3] |
| WasmCert-Coq [4] | Rocq | Type safety; a checker sound and complete against the rules | — |
| CakeML [5] | HOL4 | The whole compiler, source to machine code; inference sound and complete | — |
| CompCert [6] | Rocq | 16 passes preserve semantics; the parser and type checker are not verified | — |

What this says for Sprout:

- **Cedar is the template.** It uses the same toolchain, the same split (prove things about the
  model, diff the model against the real code) and the same target, a type checker. Its numbers
  [1, §3]: proofs found 4 validator bugs, and differential and property tests found 21 more. The
  Lean model was 1,673 lines and the proofs 5,714, against 15,693 lines of Rust; all proofs check
  in about 3 minutes. So most bugs came from the oracle, not the proofs.
- **Writing the rules down formally finds spec bugs.** Watt's model found unsound rules in the
  Wasm spec, for example `return` was well-typed outside a function [2]. That is failure 1 in §1.
- **CakeML and CompCert are the scale of option "verify the compiler".** CakeML is about 100k
  lines of HOL4 proof [5]. That is why it is a non-goal here.
- **Oracles decay.** Wasmtime removed its spec-interpreter differential fuzzer on 2026-11-03,
  citing the cost of a second toolchain, a stale spec version and no recent finds [7]. §7's drift
  gate and §11's upkeep cost exist because of this.
- **No small Lean 4 template for linear types was found.** Small Coq ones exist (dblib-linear [8],
  targeting Coq 8.4) and Agda ones (Rouvoet et al., CPP 2020 [9]). RustBelt is far too heavy.
  Stage 3 starts from scratch.

[1] Disselkoen et al., "How We Built Cedar: A Verification-Guided Approach", FSE Companion 2024,
<https://arxiv.org/abs/2407.01688>; <https://github.com/cedar-policy/cedar-spec>.
[2] Watt, "Mechanising and Verifying the WebAssembly Specification", CPP 2018,
<https://www.cl.cam.ac.uk/~caw77/papers/mechanising-and-verifying-the-webassembly-specification.pdf>.
[3] "WasmRef-Isabelle", PLDI 2023,
<https://pldi23.sigplan.org/details/pldi-2023-pldi/5/WasmRef-Isabelle-A-Verified-Monadic-Interpreter-and-Industrial-Fuzzing-Oracle-for-We>.
[4] <https://github.com/WasmCert/WasmCert-Coq>.
[5] <https://cakeml.org/>; <https://cakeml.org/icfp16.pdf>.
[6] <https://compcert.org/compcert-C.html>.
[7] <https://github.com/bytecodealliance/wasmtime/pull/15122>.
[8] <https://github.com/michaelsproul/dblib-linear>.
[9] <https://research.tudelft.nl/en/publications/intrinsically-typed-definitional-interpreters-for-linear-session->.

## 4. Sprout Core

A small calculus that covers the four areas and prints to valid Sprout surface syntax. Each rule
the model implements carries a tag naming the spec heading and paragraph it comes from (§7).

**Types.** Type variables, `Int`, `Bool`, `String`, `Unit`, tuples, user ADTs with parameters,
and arrows carrying one effect. Effects form a three-point order, pure < effect variable < `IO`
(spec §7 rule 8). A signature names at most one effect variable (rule 9).

**Terms.** Variables, literals, lambdas, application, local `let`, `if`, constructors, `match`
with nested constructor/tuple/literal/wildcard patterns, `do` blocks with `let` and non-final
statements, and one `!{IO}` primitive: `print_str(String)`, a prelude extern and so reachable
everywhere (spec §3). `print` is out: it needs `ToString`.

**Declarations.** ADTs (linear or not), top-level `fn` with or without annotations, top-level
`let`, and mutually recursive binding groups.

**Typing rules the model follows, and the ones easy to get wrong:**

- Only top-level `fn` and `let` generalise. A local `let` is monomorphic (spec §5.6, existentials:
  "a local `let` is monomorphic"), so `let id = \x -> x in (id(1), id(true))` is rejected.
- A written type variable is rigid (§7 rule 15). An omitted annotation is monomorphic until
  inferred (rule 16). Polymorphic recursion needs a full signature.
- Arguments are checked in two passes, non-lambdas first (rule 3).
- A top-level `let` must be pure and may not hold a linear value (§5.2, §5.8). A `fn` may not
  come before a top-level `let` it uses, so the printer orders `let`s first.
- A top-level `let` generalises only when its initialiser is a syntactic value. The compiler does
  this (`is_syntactic_value` in `infer.sprout`); the spec does not say so. The model follows the
  spec, so the oracle is expected to flag it as a spec gap (§6).
- Exhaustiveness is per column (§5.5). Top-level unreachable branches are an error (§5.5,
  "Unreachable top-level branches").
- Linearity: a declared `linear` ADT; a type that contains one structurally, not through an arrow
  (§5.8); a type variable with or without `where linear`; exactly one use per binder on every path;
  a bare non-final `do` statement is a discard, not a use; **a lambda may not capture a linear
  value** (§5.8, Borrowing: "a captured linear value, owned or borrowed, is still rejected").

## 5. Theorems

Each theorem is stated over the model's typing judgment and a small-step semantics.

- **T1 Type safety.** A well-typed closed program either steps, is a value, or is stuck on a
  non-exhaustive match (the `sprout_abort_match` v0 codegen emits). Progress and preservation.
- **T2 Inference soundness.** If the model's algorithm returns a type, the declarative judgment
  derives it. The judgment encodes rules 15 and 16, which the spec states as an algorithm ("first
  use commits"); writing them declaratively is part of the work and may show gaps in the text.
- **T3 Effect soundness, outside the variable channel.** A term typed pure, from which no function
  with an effect variable in its signature is reachable through calls, takes no `print_str` step.
  Direct callees are not enough: a pure `f` may call `g : … !{e}` at purity, and `g` may print.
  Includes rule 8's directional comparison and peer joins. Not claimed: anything about effect
  variables, because the spec does not claim it either (§2 non-goals).
- **T4 Exhaustiveness.** A full usefulness check is correct: a match it accepts never gets stuck.
  The per-column rule is stated as its own function, and the model describes exactly what it
  accepts that the full check rejects. `(true, true) | (false, false)` is one case (spec §5.5);
  `type-system-review-2026-08-13.md` F9 lists more.
- **T5 Linearity.** In a semantics that tags linear values, a well-typed program **that reaches a
  value** has used each linear value exactly once: never dropped, never used twice. Covers `where
  linear` bounds and binding groups. A program stuck on a match may have consumed some linear values
  and still hold others; the abort releases nothing. That is why the theorem covers only runs that
  reach a value.

No `sorry` merges. A theorem lands proved, or its statement waits on a branch. The gate (§7) fails
on any `sorry`, so a merged theorem is a proved one.

## 6. The oracle

```
generator ──► Sprout Core term ──┬──► Lean checker ──────────────► accept/reject
                                 └──► printer ──► .spr ──► --phase check ──► accept/reject/harness error
                                                                   compare ◄─┘
```

- **Generator.** Written in Lean, so one toolchain owns the term type, the printer and the
  checker. Plausible (§10) supplies generators and shrinking. Type-directed generation for
  accepting programs; mutation (duplicate a use, drop a use, capture in a lambda, swap a pure
  callee for an `!{IO}` one, remove a match arm, move an arm earlier) for rejecting ones.
- **Compiler side.** `--phase check` bundles and type-checks and prints `OK`
  (`docs/debugging.md` §Diagnostic Phases). No compiler change is needed.
- **Three verdicts, not two.** A failure prefixed `bundle:` (`stdlib/compiler/compiler.sprout`,
  `compile_phase_check_with_roots`'s `BundleErr` arm) means the printer wrote something the
  parser or bundler refused. That is a harness error, never a disagreement: it fails the run and
  points at the printer.
- **Disagreement.** The harness shrinks the program, writes it out, and fails. A human decides
  which side is wrong. A compiler bug becomes a `type_error/` or `run/` fixture. A model bug is
  fixed in Lean. A spec gap goes to `BACKLOG.md`.
- **Known gaps** go in an allowlist, one id, shape and reason per line, like `run/XFAIL`. Each id
  names a fixed witness program (`formal/SproutCore/Witnesses.lean`) that the run checks on both
  sides every time: a listed gap whose witness stops disagreeing fails the run so the list cannot
  rot, and a gap the random sample did not reach is not stale. The first entries are
  the exhaustiveness check's accept-everything fallbacks in `infer.sprout`: a scrutinee whose type
  is a type variable, a function or a record; a constructor missing from the map; field types that
  do not resolve. The model's per-column function follows the spec's text and does not have them.

## 7. Drift gate

- `just lean-model`: `lake build`, then fail on any `sorry`, and on any theorem whose axioms
  (`#print axioms`) go beyond Lean's standard three (`propext`, `Classical.choice`, `Quot.sound`).
  A `sorry` would show there as `sorryAx`, so the two checks agree.
- `just lean-oracle N`: N generated programs through both sides. Fixed seed in CI, so failures
  reproduce. A scheduled job runs a fresh seed.
- **Spec snapshot.** Every spec section a rule tag (§4) cites is copied into
  `formal/spec-snapshot/`, like `tests/golden/ir`. A tag citing a section that is not in the
  snapshot fails the gate (the heading number must match exactly: §5.6.1 is not covered by
  §5.6), so the list cannot fall behind the model. Any change to a snapshotted section fails the gate until the snapshot is refreshed in the same commit as the model change,
  or with a note that the model needs none. Rule numbers alone are not enough: §5.8 is
  unnumbered prose, rule 8 alone runs to 45 lines, and the retro's cause 1 was a premise change
  inside an existing rule, which adds no number.
- CI: a separate job, not part of `just test`, so a Lean breakage does not block compiler work
  until the model has proved its value. Promote it after stage 3.

## 8. Alternatives

- **More hand-written grid cells** (`gen_linear_grid.sh`). No new toolchain. But a human still
  writes each expected verdict, and only for paths they thought of — §1's problem, unchanged.
- **Compiler against itself** (stage-1 vs stage-2 checker). Catches a miscompiled checker. Both
  sides run the same rules, so a rule the code gets wrong is wrong on both sides. Does not touch
  failure 1 or 2.
- **Oracle without proofs.** A Lean (or Sprout) reference checker and the oracle, no theorems.
  Cedar's numbers say this finds most bugs. It loses failure 1: a reference checker that follows a
  wrong rule agrees with a compiler that follows it too. The stages below build the checker and
  oracle before the proofs in each area, so this is the first half of every stage anyway.

## 9. Stages

1. **Toolchain, HM and the effect order.** Pin Lean (§10). Types, terms, inference, T2, the
   printer, the oracle. Effects as pure < `IO` only; the effect-variable point comes in stage 4.
   Needed now, since the compiler enforces rule 8 on every program and `print_str` is `!{IO}`.
   Exit: oracle green in CI.
   - **1a (landed): everything but T2.** `formal/SproutCore/` holds the stage-1 fragment: Int, Bool,
     String, Unit, tuples and effect-carrying arrows; lambdas, application, local `let`, `if`, `!`,
     `do` blocks and `print_str`; top-level `fn` (annotated or not), `let` and mutually recursive
     groups; `main`. The checker follows the spec, one `-- spec §…` tag per rule. The printer,
     the Plausible generator with mutations, the shrinker and the `sprout-oracle` executable are in;
     so are the three recipes, the spec snapshot and the `lean` CI job (`docs/gates.md` §Lean
     model). No theorems, so the `sorry` count is 0. The `lean` CI job passes on Linux.
     The generator also builds `() -> T` arrows (a zero-parameter `fn`, spec §7 rule 9), n-ary
     arrows, a `fn` returning an arrow, and an `if` joining arrows of different stored arity.
     Review found two model bugs there, both now `#guard` regressions in `Tests.lean`: a
     zero-parameter `fn` returning an arrow lost its `()` arrow, and a join that rejected arrows whose
     stored arity differs (a result variable solved to an arrow later).
     The oracle's findings are in `formal/oracle/ALLOWLIST`, each with a witness: one compiler gap
     (an effectful `let` right-hand side, spec §5.2.1, already scheduled in `try-propagate-v0.md`),
     one spec gap (the compiler's value restriction on a top-level `let`, which the spec does not
     state), and three compiler bugs, all `BACKLOG.md` entries: a pure top-level `let` calling a
     non-generalised function value is rejected as IO; a non-generalised function value passed to
     an `!{IO}` slot is then read back as IO at its other uses; and a peer join of a nested join
     with an `!{IO}` function is order-dependent.
   - **1b: T2**, inference soundness, and the axiom check of §7.
2. **ADTs and match.** T1, T4, the unreachable-branch rule. Exit: the per-column gap and the
   fallbacks are written up in this doc.
3. **Linearity.** T5 over linear ADTs, containment, `where linear`, binding groups, top-level
   `let`, lambda capture, discards. Exit: every grid cell Core can express is generated and
   agrees. That is about half the grid: the `inst`, `instb`, `method`, `mbound`, `ext` and `exist`
   rows and the `mode`, `lend`, `comp` and `wild` columns need classes, externs, existentials,
   borrowing or `<-`. The exact list is written here when the stage starts.
4. **Effect variables.** Rule 9's singleton limit, bounds on variables, T3 as stated.
5. **Later, each its own decision:** single-parameter classes (the retro's instance-head bug lives
   here), externs, `<-`, records, borrowing, existentials.

## 10. Toolchain

- Lean is not in the mise registry (checked 2026-11-17). It comes through mise's `github:`
  backend instead, which keeps AGENTS.md's "tools via mise" rule; elan is not needed. It installs
  `lean` and `lake`, Lake fetches Plausible from git and builds a native executable; checked on
  macOS arm64 and on CI's Linux x86_64.
- **Not in `mise.toml`.** The release ships its own `clang` 23 without system headers. Listed in
  `mise.toml`, it went on every job's PATH, shadowed the real `clang` on the macOS runner, and
  broke the runtime build (`stdio.h` not found). The justfile's `lake` variable runs
  `mise exec github:leanprover/lean4@4.35.0 -- lake`, so Lean is on PATH only for that command.
- Pin an exact release, not the newest: v4.36.0 came out 2026-11-16, v4.35.0 on 2026-10-12
  (Cedar pins v4.35.0).
- No Mathlib unless a proof needs it. It is large and slows CI. Plausible
  (`leanprover-community/plausible`) gives generators and shrinking without it; it is
  community-maintained, not part of Lean. Its `plausible` tactic closes a goal with `sorry`
  (checked), so it belongs in tests and the oracle, never in `formal/`'s proof modules — the
  `sorry` count (§7) would see it.
- The model lives in `formal/` with its own `lakefile`. Nothing in `stdlib/` or `runtime/`
  depends on it.

## 11. Costs and risks

- **Upkeep.** Every rule change in a modelled section now has a Lean side. That is the point, but
  it slows spec changes there.
- **Model–spec gap.** A proof covers the model, not the spec text. The oracle and the snapshot
  gate are what tie them; without them the proofs are about a different language.
- **The oracle only sees the subset.** A bug reachable only through a class, an extern, a record
  or a module is invisible until stage 5. So is rule 8's covariant type-argument hole, which needs
  `Ref`. §1 puts the reach at 5 of the retro's 16 bugs.
- **Generator bias.** Random programs miss deep shapes. Mutating known-bad shapes is the
  mitigation.

## 12. Tests, docs and backlog

- **Tests.** `just test-lean-oracle`, as `test-review-gate` tests its gate: a planted model with
  one rule removed must produce a disagreement, and a planted printer bug must produce a harness
  error, not a disagreement.
- **Docs.** `docs/gates.md` gets an entry for both recipes when they land. No spec change. No
  Definition of Done change until the CI job is promoted (§7).
- **Backlog.** The retro's deferred T3 ("a `/sprout-review` pass that writes linear misuse
  programs") is partly automated by stage 3, inside Core only; the entry stays for the rest. X3
  (compile the spec's `linear` examples) and C2 (one group-boundary check) are unaffected. One
  `BACKLOG.md` entry tracks the stages and links here.

## 13. Impact

No syntax, semantics, typing or diagnostic change. No spec change. No builtin or runtime change.
New: `formal/`, three `just` recipes, one CI job, a Lean toolchain pin.
