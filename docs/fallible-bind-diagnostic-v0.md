# Discarded fallible bind — diagnostic design (v0)

**Status: PROPOSAL, SUPERSEDED IN PART. Not implemented, not approved.** This document exists so the
decision is made from measured data and verified prior art rather than from intuition. The
measurement (§3) and the survey (§4) stand and are the reason to read it. One defect the measurement
found *is* fixed (§9), and stands on its own.

> **Read §0 first.** Investigating whether the *design* was sound — rather than only whether the
> pattern was tidy — found that the `do` block's `Result` short-circuit is **never type-checked
> against the enclosing function's return type**, which makes this a soundness bug and not a lint.
> A warning is therefore the wrong instrument, and §5's severity is superseded by the `P0` entry in
> `BACKLOG.md`. The framing below is kept because the measurement is what located the bug.

## 0. Superseding finding: this is a type-checking bug

For `x <- e` with `e : Result E A`, codegen emits a short-circuit that allocates a fresh `Err` box and
returns it from the enclosing function; the `phi` that merges it with the success value is typed
`i64`, and **nothing checks that the enclosing function can carry an error, or carry that error.**
Measured:

| Shape | Result |
| --- | --- |
| `fn returns_int() -> Int !{IO}` binding a failing `Result String Int` | returns `35184372088840` — `0x200000000008`, a heap pointer printed as an `Int` |
| the same at `-> String !{IO}` | returns a pointer *read as a CSTR* → `H\xef\xbf\xbdn`, arbitrary heap bytes as text |
| `fn mismatched() -> Result Int Int` binding a `Result String Int` | compiles; `"boom"` arrives as `Err e` with `e : Int` |

Not IO-specific (a pure `-> Int` binding a pure `Result` is equally accepted), and **not affected by
the binder** — `_ <- e` emits the identical short-circuit, so the `_ <-` opt-out proposed in §5 is not
a soundness opt-out at all, only a silenced name. `Unit`-returning functions are the sole
*unobservable* case: the returned `Err` box is discarded by convention, so the type lie has no witness.

One missing unification explains every row: require the short-circuit's type to match the enclosing
function's declared return type. Consequence for §4 — its consensus is about *discarding a value*, a
style concern where "warn, never error" is right; this is *returning a value of the wrong type*, so
that precedent does not transfer and a warning here would leave memory-unsafe code compiling.

## 1. Problem statement

In a `do` block, binding a `Result` with `<-` sequences it: on `Err` the enclosing computation
returns early carrying the error. That is correct and useful when the enclosing function itself
returns a `Result`. When it does **not**, the error is simply *dropped* and the function returns
early with no trace:

```sprout
fn handle(conn: borrowing TcpConnection) -> Unit !{IO} =
  do
    request <- read_avail_timeout(conn, 5000)   # on Err: returns here, error discarded
    respond(conn, request)                       # never runs, and nobody is told why
```

This type-checks, emits no diagnostic, and is invisible at the call site. It converts a
deliberately-recoverable API into a silently-swallowing one exactly where a caller most needs to
react — the failure mode that motivated making `tcp_accept` recoverable in the first place
(`BACKLOG`, W2/C3). The defect is not the language rule; it is that the rule is applied *silently*
in a context where it cannot mean what the author intended.

## 2. Goals and non-goals

**Goals**
- Make an accidentally-discarded failure visible at compile time.
- Keep the deliberate discard expressible, cheaply, and locally visible.
- Do not regress any currently-valid program into a compile error without a migration.

**Non-goals**
- Not a general unused-value analysis. Only fallible binds are in scope.
- Not effect-system work. This is orthogonal to the deferred effect-enforcement pass (D2/W6).
- Not a `Maybe`-discard diagnostic. `Nothing` carries no payload to lose, so nothing is silently
  dropped; the argument that motivates this rule does not transfer.

## 3. Measurement — the blast radius, before choosing a severity

Measured 2026-08-11 over `stdlib/`, `stdlib/compiler/`, `tests/`, `examples/`, `testsupport/`,
`bench/` (1539 `<-` binds total) with a syntactic pass: resolve each bind's callee by name against
declared return types, expand type aliases, and compare against the enclosing `fn`'s declared
return type.

| | Sites |
| --- | --- |
| Total `<-` binds | 1539 |
| Discarded fallible bind, binder is `_` | 21 |
| **Discarded fallible bind, binder is a NAME** | **5** |
| …of those, in production code (`stdlib/`, `stdlib/compiler/`) | **1** |

The five named-binder sites:

| Site | Enclosing fn | Bound call |
| --- | --- | --- |
| `stdlib/compiler/compile_driver.sprout` | `run_check_iface -> Unit !{IO}` | `read_file -> Result String String` |
| `tests/stdlib/test_unresolved_dict_poison.spr` | `probe_ir -> String !{IO}` | `translate_program -> Result String …` |
| `tests/task_io_smoke/await_dropped_fails.spr` | `accept_forever -> Int !{IO}` | `tcp_accept -> Result TcpError Int` |
| `tests/task_io_smoke/cancel_io_drop.spr` | `accept_forever -> Int !{IO}` | `tcp_accept -> Result TcpError Int` |
| `tests/task_io_smoke/timeout_io_drop.spr` | `accept_forever -> Int !{IO}` | `tcp_accept -> Result TcpError Int` |

**Two results decide the design.**

1. **The `_ <-` sites are deliberate.** All 21 are code that means it — benchmark servers that do not
   care about an I/O error, and test fixtures writing scratch files. `_ <-` is already Sprout's
   spelling for "I am discarding this", and it is *the same spelling GHC uses to suppress its
   equivalent warning* (§4). A rule that fires on `_ <-` would be flagging authors who already said
   what they meant, and would be suppressed rather than fixed.
2. **Only ONE production site is affected.** Not dozens. The migration cost of a strict rule is
   therefore ~5 edits, which makes severity a genuinely open choice rather than being forced to
   "warn" by the volume of existing hits. That one site was a real, live defect — §9.

**Blind spots of the measurement, stated so the number is not over-trusted.** It is syntactic, not
inference-based: 7 callees did not resolve; 13 binds have a non-call right-hand side (a `match`, a
lambda application) and were not classified; 15 binds call a higher-order *parameter* and were
skipped rather than resolved (an early version resolved those against unrelated globals of the same
name and produced 6 false positives). Two earlier bugs in the pass are worth recording because both
inflated confidence in a wrong answer: `extern fn` declarations have no `=`, so a signature
accumulator that stopped only at `=` glued each extern onto the *next* declaration and read its
return type; and `stdlib/prelude.sprout` has no `module` header, so keying the symbol table on a
declared module name dropped the prelude entirely — hiding all 16 of its `Result`-returning
declarations, including `read_file` and `write_file`. Before the fixes the pass reported 10 hits and
missed the only production one.

## 4. Prior-art survey

Every row verified against a primary source; sources cited.

| Language | Mechanism | Default | Call-site opt-out |
| --- | --- | --- | --- |
| Rust | `unused_must_use` lint, driven by `#[must_use]` (which `Result` carries) | **warn** by default | `let _ = …` |
| Haskell (GHC) | `-Wunused-do-bind` — a `do` statement whose non-`()` result is unbound | **off**, not even in `-Wall` | **`_ <- …`** |
| Swift | unused result of a non-`Void` function; `@discardableResult` opts out at the declaration | **warn** by default (SE-0047) | `_ = f()` |

Sources: [rustc warn-by-default lint listing](https://doc.rust-lang.org/rustc/lints/listing/warn-by-default.html);
[GHC User's Guide, "Warnings and sanity-checking"](https://downloads.haskell.org/ghc/latest/docs/users_guide/using-warnings.html);
[Swift Evolution SE-0047](https://github.com/swiftlang/swift-evolution/blob/main/proposals/0047-nonvoid-warn.md).

**Consensus.** Two things are agreed across all three, and one is not.

- Agreed: this is a **warning, never an error**. No surveyed language rejects the program.
- Agreed: the opt-out is an **underscore binding at the call site**. GHC's is literally `_ <-`;
  Rust's `let _ =`; Swift's `_ =`.
- Not agreed: **who declares fallibility.** Rust and Swift attach it to the *type or declaration*
  (`#[must_use]`, `@discardableResult`), so the rule is opt-in per API. GHC derives it structurally
  from the statement's type. Sprout can derive it structurally too — `Result` is a single known type
  constructor — so no new attribute surface is needed. That is the cheaper choice and the one this
  proposal takes.

GHC's default (off, not in `-Wall`) is the outlier and reflects a much broader rule: it fires on any
discarded non-`()` result, so it is noisy in ordinary monadic code. The rule proposed here is
narrow — only `Result`, only when the enclosing function cannot carry it — so GHC's noise argument
does not transfer, and Rust's and Swift's warn-by-default is the better fit.

## 5. Proposed rule

Emit a **warning** when all of the following hold:

1. a `do` bind step binds a **name** (not `_`),
2. the bound expression's type is `Result _ _` (after alias expansion),
3. the enclosing function's declared return type is **not** `Result _ _` (after alias expansion),
4. and no arm of the enclosing construct consumes the error.

`_ <- expr` is the opt-out, matching GHC exactly and requiring no new syntax: Sprout already has
the spelling, and the 21 existing sites already use it to mean precisely this.

## 6. Implementation overview (for approval before any code)

**The obvious approach is the expensive one, and should be rejected.** Emitting from `infer` looks
natural, but `compiler.CompileResult` is `CompileOk (Dict types.Scheme) | CompileFail (List
Diagnostic)` — so a warning has **no channel on the success path** and can only be delivered by
*failing* the compile. Adding one means putting a diagnostics list on `CompileOk` and threading it up
through `checker.CheckOk` and `InferResult`: a change to the compiler's core result type touching
~10 pattern sites across four drivers, plus inference, for a warning.

Note also that `compiler.Diagnostic` already has a `DiagWarning source.SourcePos String` arm and
four drivers already render it — `compile_driver` prints `WARNING:` to stderr without setting the
failure flag, `lsp_driver` maps it to LSP severity 2 — but **it is constructed nowhere.** All of that
is unexercised scaffolding, not working infrastructure; this would be its first producer, and the
rendering paths should be treated as untested.

**Proposed instead: a driver-side lint pass.** After a successful check, `compile_driver` holds the
typed environment (`CompileOk (Dict types.Scheme)`) and can already recover the AST via
`parse_for_decls`. A standalone pass walks each `FnDecl` body for `do` bind steps, resolves the bound
expression's callee in the env, and applies §5. This needs **no change to `CompileResult`**, no
threading through inference, and gives `DiagWarning` its first real producer via the existing
`print_diags` path.

Known limitation, accepted deliberately: it classifies binds whose right-hand side is a resolvable
call, which is the reported shape. Other RHS forms (a `match`, an applied lambda) are a *coverage
gap*, not a wrong answer — the pass stays silent rather than guessing. If that gap later matters,
that is the point to reconsider the `infer` route, with the cost above priced in.

## 7. Impact

- **Syntax:** none. No new surface; `_ <-` already parses.
- **Semantics:** none. Runtime behaviour is unchanged; this is a diagnostic only.
- **Type system:** none. No new rule, relation, or inference change. The pass *reads* the env
  produced by a successful check and runs strictly after it.
- **Error messages:** one new warning. Proposed text, following the repo's convention of naming both
  the fix and the opt-out:

  ```
  12:5: WARNING: this bind can fail and the failure is unused: `read_file` returns
    Result String String, but `run_check_iface` returns Unit !{IO}, so an Err returns
    early and is discarded. `match` it, make the function return a Result, or write
    `_ <-` to discard it deliberately.
  ```

- **Compatibility/migration:** as a warning, none — no program changes meaning or stops compiling.
  Were it ever promoted to an error, §3 bounds the migration at 5 sites, 1 of them production.

## 8. Open decisions (these are the blockers, not the code)

1. ~~**Warn or error?**~~ **Settled by §0: error.** The survey's unanimity on "warn" governs
   *discarding a value*; §0 shows this construct *returns a value of the wrong type*, so a warning
   would leave memory-unsafe code compiling. What remains open is narrower: whether the
   `Unit`-returning case — the one shape where the mistyped value has no witness — is permitted
   explicitly or also rejected. Rejecting it is more consistent; permitting it avoids churn in the 21
   deliberate `_ <-` sites, all of which are `Unit`-returning.
2. **Fail CI on the warning?** A warning nothing gates on rots exactly like an un-run gate — the
   lesson of `gate-audit` Assertion D. Options: leave it advisory; add a `--warnings-as-errors`
   flag the CI gate passes; or gate on a zero-warning count for `stdlib/` only. Recommend the last:
   it keeps production code clean without breaking fixtures that discard deliberately.
3. **Do the 4 `bench/` + 21 `_ <-` sites need review anyway?** Out of scope for the rule, but the
   bench servers ignoring `write_all_utf8` failures may be measuring a shape they do not intend.

## 9. Defect already found and fixed by the measurement

`compile_driver.sprout`'s `run_check_iface` opened `contents <- read_file(path)` in a
`Unit !{IO}` function. On an unreadable path the `Err` was discarded and the function returned early,
so `--check-iface` printed **nothing** and exited **0** for a file it never read — defeating the
exit-status contract documented immediately above it ("so a caller can gate on the status instead of
scraping text"). Fixed by matching on `read_file` and reporting `INVALID: … (unreadable: …)` with a
nonzero status, the shape `run_file` already used.

Regression: two `--check-iface` cases in `just diagnostic-stream-smoke` — readable-but-undecodable
and unreadable — each asserting a nonzero exit, an `INVALID:` line, and the absence of the `^OK:`
line that `just check-iface-all` greps for. Verified red before the fix on the unreadable case only,
which is what isolates the defect: the malformed-file path already behaved.

This is the whole argument for the diagnostic in miniature. The bug was invisible, sat in production
compiler code, and was found by *looking for the shape* rather than by any test — and it lived in a
path reached by `check-iface-all`, one of the recipes `gate-audit` Assertion D excludes as needing a
precondition, so nothing ran it either.

## 10. Tests and documentation status

- Tests for the *proposal*: none yet; it is unimplemented. When built it needs typechecker-style
  positive and negative fixtures (AGENTS.md, Code and Testing #5) plus a case per §5 clause and one
  asserting `_ <-` stays silent.
- Tests for the fix in §9: landed, described above.
- This document is **non-normative**. `docs/spec-v0.md` is unchanged and must stay unchanged: a
  warning constrains no program. Should the rule ever become an error, the spec's diagnostics
  expectations would need updating in the same change.
