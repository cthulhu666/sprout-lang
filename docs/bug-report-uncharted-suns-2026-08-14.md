# Bug report — diagnostics that blame the wrong line, and one blocking stdlib gap

**Reported:** 2026-08-14
**Reported from:** the `uncharted-suns` repo (the galaxy game + `loam` engine), during a refactor
extracting the simulation from the renderer.
**Compiler:** `build/compile_driver_bin_stage1`, invoked as the game's `Justfile` does —
`compile_driver_bin_stage1 --emit-ir <stdlib> --package-root <root> <file>`.

## How to read this

Four issues. **Three are diagnostics bugs**: the compiler is *right to reject* the program, but points
at somewhere other than the mistake. That is what makes them expensive — the message sends you to
investigate correct code. **One is a missing stdlib primitive** that has crossed from an ergonomic
annoyance into an architectural blocker.

Every item below was hit during real work and *then* reduced to the minimal reproduction shown. **All
compiler output is verbatim** — nothing here is recalled or paraphrased. Ranked by cost.

Nothing here is a wrong-code (miscompilation) bug. No item produces a running program that behaves
incorrectly; issue 3 is the closest, in that it silently discards source you wrote.

---

## 1. An unresolved type annotation becomes a PHANTOM TYPE instead of an error

**Severity: high** — cost ~6 compile iterations on the real case, and sent me down a disproved
hypothesis first.

A parameter annotated with a type that was never imported **compiles the signature happily**. The name
silently becomes a fresh opaque type distinct from the real one, and the first complaint arrives at a
*call site*, phrased as a mismatch between two things that look identical.

### Reproduction

`probes/hastype.sprout`:

```sprout
module probes.hastype

export type T = (a: Double, b: Int)

export fn make() -> T = T(a = 4.0, b = 2)
```

`probes/missing_type.spr`:

```sprout
import stdlib.math (sqrt)
import probes.hastype (make)      # NOTE: the TYPE `T` is not imported — only `make`

fn f(t: T) -> Double = sqrt(t.a)  # line 8: the actual mistake, accepted in silence

fn main() -> Unit !{IO} =
  do
    let v = make()
    print(to_string(f(v)))        # line 13: where the error is reported
```

Actual:

```
13:22: ERROR: check: Call type mismatch: Type mismatch: T vs probes.hastype.T in function main
```

Expected: an error **at line 8**, naming the unresolved type and, ideally, where it could come from —
e.g. `Unknown type 'T' (did you forget to import it from probes.hastype?)`.

The message is decipherable once you already know the cause — bare `T` versus qualified
`probes.hastype.T` is the tell — but it names neither the annotation nor the missing import. Note the
type here is used **only as an annotation**; nothing in the file constructs a `T`. If you *do* construct
one locally the diagnostic is much better (`Unknown record type or field: T.a`), which is why this hides
precisely in the "value comes from another module" case.

### Why this was expensive in the real code

In `game/app.sprout` (~4,400 lines) I added a parameter `sim: Sim` and forgot `Sim` in the import list.
The phantom type propagated through `sim.ship.flight.vx`, and **the only error reported was**:

```
2296:35: ERROR: check: Call type mismatch: Type mismatch: Double vs Int in function game.app.render_loop
```

— inside an unrelated `sqrt` call, **~1,700 lines away** from the mistake, in code I had not touched.
The honest `Unknown record type or field: Sim.loc` appeared only after I edited that first error out.
Because the error implicated a chained field read, I first hypothesised that chained reads work off a
function *parameter* but not off a `let`-local; a probe disproved that (both compile fine) — time spent
entirely because of where the message pointed.

### Suggested fix

Reject an unresolved type name **at the annotation site**, rather than admitting it as a fresh opaque
type. If admitting it is deliberate (implicit type variables?), then at minimum the mismatch message
should say that one side is unresolved rather than printing two spellings of the same name.

---

## 2. `import` must be one line, and the error does not say so

**Severity: medium** — cheap once known, but hit repeatedly, and it forces genuinely bad formatting.

A parenthesised import list wrapped across lines fails at the continuation, reported as a generic
declaration error.

### Reproduction

```sprout
import stdlib.math (pi, sin,
                    cos, sqrt)

fn main() -> Unit !{IO} = print(to_string(sqrt(4.0)))
```

Actual:

```
ERROR: bundle: Parse error in …/multiline_import.spr: Expected declaration at 2:21
```

Expected: either the wrapped list parses (the obvious reading), or the error says an import must be a
single line.

### Why it matters beyond the message

Import lists get long, and this rule makes them unformattable. `game/app.sprout` in uncharted-suns
carries **123 imports, 13 of them over 200 columns**, kept on one line only because they must be:

```sprout
import game.docking (DockPhase, DockOff, DockApproach, DockBerth, Docked, dock_advance, dock_rails, dock_name, dock_hold_au, dock_berth_au, berth_advance, berth_arrived, dock_hold_pos, dock_k, dock_v_min_ms, dock_v_max_ms)
```

The usual workaround is repeating the module on several `import` lines, which reads as though the
groupings mean something when they only mean "the line got long".

### Suggested fix

Allow the parenthesised list to span lines. Failing that, emit "an import declaration must be a single
line" at the continuation.

---

## 3. A multi-name `let` as a `do`-block statement silently binds only the first name

**Severity: medium** — silent source loss; the failure surfaces at the *use*, and only if there is one.

The expression form `let a = … b = … in …` binds every name. As a **statement** inside a `do` block the
same syntax parses, binds the first name, and **discards the rest with no diagnostic**.

### Reproduction

```sprout
fn main() -> Unit !{IO} =
  do
    let a = 1
        b = 2
        c = 3
    print(to_string(a))
    print(to_string(b))   # line 9
    print(to_string(c))
```

Actual:

```
9:21: ERROR: check: Unknown variable: b in function main
```

Expected: an error at the `b = 2` binding, or (better) the statement form binding all three.

Because the complaint lands at the *use*, a binding that is written but never read is dropped in total
silence. I hit this writing a test that built a record from several `let`s and read back only some.

### Suggested fix

Support the multi-name form as a statement — it already parses, it just discards — or reject the second
and subsequent bindings where they are written.

---

## 4. No exported Double→Int conversion — now blocking a module boundary

**Already tracked** in `BACKLOG.md` §9 as `P1` ("Promote a Double→Int conversion to a core
runtime/prelude primitive"). **This report is new evidence that it has changed category, and a
suggestion to re-rank it.**

### What changed

It is no longer an ergonomic gap. uncharted-suns is extracting its simulation from its renderer so the
world can be advanced and tested headlessly. The constraint that makes this bite:

> A module that imports `loam.gfx` drags in every `extern fn gfx_*`, and those resolve only in a GL
> build. Headless test suites link the core runtime alone. **So any module that needs Double→Int cannot
> appear in a headless suite** — the only conversion available is `gfx.double_to_int`, in the graphics
> shim.

`game/starmap.sprout` needs it in three places for work that is **purely arithmetic over a star catalog**
— sector indexing, sector labels, corridor padding — with no rendering involved:

```sprout
raw = gfx.double_to_int((coord + radius) / (2.0 * radius) * to_double(n))   # sector index
… sector_label(xi, yi, gfx.double_to_int(radius))                          # label
… gfx.double_to_int(pad) + 1                                                # corridor padding
```

That single dependency pins the whole module to the graphics side of the line, which in turn keeps
route planning out of the extracted simulation module. **The workaround is no longer "call the shim" —
it is "split a module in two, or leave model code in the renderer."**

### Note for whoever picks this up

`stdlib/math.sprout` already contains a **private** `round_to_int` (the branch-free
add-and-subtract-a-magic-constant trick, around lines 64–90), so exporting a conversion may be most of
the work already done. Two cautions:

- Callers here want **truncation or floor**, not round-to-nearest — the uses above are index/bucket
  computations where rounding up at `.5` puts a star in the wrong sector.
- `stdlib/math.sprout`'s own comments note `/` truncates toward zero, so `floor` for negative inputs
  needs the same care the existing `floor_div_2`/`floor_div_3` helpers take.

Verified 2026-08-14: `grep -rn 'export fn.*: Double) -> Int' stdlib/` returns nothing.

---

## Not reported here

The downstream repo's `AGENTS.md` lists other Sprout gotchas (a dotted read off an imported `let` not
resolving; no scientific-notation literals; `not` not being an operator; `[x | xs]` cons being
pattern-position only). Those were encountered in **earlier** sessions and were **not re-verified
against the current compiler for this report**, so they are deliberately excluded rather than passed on
as hearsay. Several may already be fixed.

---

## Resolution log

Appended as each item lands. All four reproduced verbatim against
`build/compile_driver_bin_stage1` on 2026-08-14 before any work started.

**One correction to the report's own framing.** It states that "nothing here is a wrong-code
(miscompilation) bug". That is not true of issue 1. A phantom type is not merely reported late — it is
*accepted*: `fn ident(t: T) -> T = t`, with `T` never declared or imported, compiles cleanly and exits
0. The report only saw the case where the phantom collides with a real type at a call site. So issue 1
is a soundness gap, and fixing it **rejects programs that build today** — a language tightening
needing a spec change and a corpus sweep, not a message rewording.

The bug is also wider than the reproduction shows: the qualified spelling has the same hole.
`fn f(t: nosuch.Bar) -> Int = 1` compiles cleanly too, so any fix must cover both shapes.

- **§4 — no exported Double→Int.** Fixed. `math.to_int : Double -> Maybe Int` and `to_int_or`, with
  `ceiling`/`truncate`/`round` completing the rounding family beside the existing `floor`. Pure Sprout
  over `double_to_bits`, so it links in a headless build with no graphics shim — which was the point.
  The report's request for "truncation or floor" is served by composition rather than by two separate
  conversions: `to_int` alone truncates toward zero, `to_int(floor(x))` floors, and for the sector
  indexing described they differ exactly on negative coordinates. Design: `docs/double-to-int-v0.md`.
- **§1 — phantom type.** Fixed, and **wider than reported in two further directions**. Besides the
  qualified spelling noted above, a **lambda parameter** annotation had the same hole
  (`\ (x: Nope) -> x` compiled) — `LambdaExpr` is the one expression node that reaches a type. A fix
  aimed only at the reproduction would have left two ways in. All three are now one rule, stated
  normatively in `docs/spec-v0.md` §4.

  The check lives in the **bundler**, because that is the only place that knows what a module
  *imports*. It is a membership test run after qualification: every type name must be one that some
  declaration in the bundle carries, in whatever spelling qualification left it with. That single test
  covers the bare and the qualified shape with no special case for either.

  The diagnostic does more than the report asked for. Where a module exports a matching name it says
  which one: ``unknown type `T` in `f`: nothing in scope declares that name. `probes.hastype` exports
  it — add it to that module's import list``. It is reported at the declaration carrying the
  annotation — the "line 8" the report wanted — and the suggested import was verified to compile.

  **Two things nearly made the fix wrong**, both caught by sweeping the corpus rather than trusting
  the reproduction. `Builder`, the opaque C type behind `stdlib/bytes.sprout`, reaches a type position
  only through an `extern fn` and is declared nowhere — 234 of 656 files failed on that name alone.
  And importless files are *deliberately* denied the prelude (`bundler.sprout` explains why), so no
  prelude declaration reaches their bundle — yet the checker still knows the prelude's types, and
  `fn f() -> Maybe Int = Just(1)` compiles in such a file today. **Bundled is not the same set as in
  scope**, and a membership test over the wrong one rejects working programs.
- **§2 — single-line `import`.** Fixed, taking the report's preferred option: a parenthesised import
  list may span lines, so the 200-column lines it describes can be wrapped. `docs/spec-v0.md` §3.

  The care here is that **two independent line scanners decide the same question** and neither can
  detect disagreement. `source.strip_headers` chooses which leading lines to blank before tokenizing;
  `module_loader.collect_imports` chooses which lines make up one import. If stripping consumed three
  lines where collection read two, the parser receives a dangling `cos, sqrt)`; if the reverse, names
  vanish. Both now call one exported paren-depth scan rather than keeping a private copy each.

  That scan stops at `#`, so `import x (a, b) # (` stays balanced — comment handling has broken import
  collection before, which is why it is pinned by a test rather than left to the reader.

  Note the report's second complaint, that the workaround "reads as though the groupings mean
  something", is answered too: repeating the module across several `import` lines is no longer needed.
- **§3 — multi-name `let` as a `do` statement.** Fixed, taking the report's preferred option: the
  statement form now binds every name, sequentially, splitting bindings by the same layout rule the
  expression form uses. `docs/spec-v0.md` §5.2.1a states it.

  **The root cause was not `let`-specific**, and that is the more important half. Every arm of
  `parse_do_step_sub` ended `(value, _) <- parse_expr(...)`, discarding the index it had just computed
  — nothing ever checked that a step consumed its slice, for *any* step shape. Verified:
  `print("first") print("second")` on one line printed only "first" and exited 0, erasing a whole
  effectful statement with no diagnostic at all. That is worse than the reported case, where a dropped
  binding at least fails later at its use.

  So this is two changes: multi-binding support fixes the reported symptom, and a full-consumption
  backstop turns every other silent discard into a located parse error. The backstop is a tightening
  in its own right; its corpus sweep is 656 files with 1 finding, which is its own fixture.

  A binding carrying an `else` still has to stand alone — its desugaring puts the remaining steps
  inside a `match` arm, a shape one binding among several cannot have. That restriction is written
  into the spec rather than left for someone to discover.

  **Follow-up (2026-08-14): the backstop immediately earned its keep.** Reported downstream right
  after landing — a bare `Double` literal as a `do` step stopped parsing, e.g. a block ending in
  `0.0`. The backstop was the messenger, not the cause. A step ends where the *next* step begins, and
  `scan_do_step_end` asks `looks_like_do_step_start` whether the next line's first token can head an
  expression; that allow-list must mirror `parse_primary` and `parse_unary`, and it was missing float
  literals and prefix `!`. On a "no" the scan runs on, so the previous step's slice swallowed the
  whole line.

  Before the backstop this was **silent wrong code**, which is the point worth carrying: a block of
  two bare `Double` steps compiled clean on the pre-backstop compiler and evaluated to the *first*
  step's value — `0.0` where `1.0` is correct, exit 0, no diagnostic. Confirmed by building the
  pre-backstop compiler from its committed seed and running it, not inferred. So the parse error was
  strictly better than what it replaced, and the allow-list gap is now fixed as well.
  `tests/stdlib/test_do_step_starts.spr` pins one step of each accepted form so the two lists cannot
  drift apart again, and `docs/spec-v0.md` §5.2.1a now states where a step ends normatively.


---

## 5. `let … in` cannot end a `do` block — §5.2.1a shadows §5.2.1

**Found:** 2026-08-14, against `ed8162b0` / `baf2c113`, while extracting the simulation out of
Uncharted Suns' render loop (`docs/sim-extraction-v0.md` step 3b).

**Status:** fallout of the fix for issue 1 in this document, so it is filed here rather than fresh.

### The repro (11 lines, no project code)

```sprout
fn tail(a: Int, b: Int) -> Unit !{IO} = print(to_string(a + b))

fn shape_a(n: Int) -> Unit !{IO} =
  do
    print("a")
    let x = n + 1
        y = n + 2
    in tail(x, y)

fn main() -> Unit !{IO} = shape_a(1)
```

```
ERROR: bundle: Parse error: Expected pattern at 9:5
```

Indenting the chain deeper does not help: the same shape inside a real do block, at 12 columns
rather than 8, reports `Expected pattern at 3779:13`.

### Why it is a bug rather than a restriction

The spec permits it in one section and forbids it in another:

- **§5.2.1** — *"`let … in` is an ordinary expression (**usable anywhere**)."* A final `do` step is
  "anywhere".
- **§5.2.1a** — the new multi-name `let` **statement** form, layout-aligned under the first binding.

The two are **textually identical up to the `in`**. `let x = n + 1` / `y = n + 2` is simultaneously a
valid §5.2.1a statement prefix and a valid §5.2.1 expression prefix; the parser commits to the
statement reading, and the `in` then has nowhere to go. §5.2.1a is the newcomer, and the shape it
displaced is one §5.2.1 explicitly allows.

Suggested fix: one token of lookahead — on reaching `in` at the end of a layout-aligned binding
group in a `do` step, reparse the group as the §5.2.1 expression form. Whichever way it is resolved,
§5.2.1 and §5.2.1a should stop contradicting each other.

### Why it matters downstream (not a corner case)

This is the shape a large refactor has to produce. Uncharted Suns' render loop ends in

```sprout
match picked with
| (best_d2, …) ->
    let hit = …
        sel2 = …
    in render_loop(…)
```

— a ~350-line `let` chain whose only reason for being wrapped in that `match` is that the wrapper
puts it in **expression position**. Extracting the simulation means splitting that chain, and a
`let … in` expression cannot be cut apart while statements can. With this bug in place the wrapper
cannot be removed, so the chain cannot become statements, so the split has to be worked around
rather than done.

**Not verified:** whether this is strictly a *regression*. Proving the shape parsed before
`ed8162b0` needs the pre-fix compiler, which has since been rebuilt over. Uncharted Suns never used
`let … in` as a `do` step, so our tree is no evidence either way — the case against it here is the
spec's own §5.2.1, not observed past behaviour.

### Resolution

Fixed. The report is right that §5.2.1 and §5.2.1a contradicted each other; two of its supporting
claims turned out otherwise, and both changed the shape of the fix.

**Not a regression — settled, not left open.** The pre-`ed8162b0` compiler was rebuilt from its
committed seed (`git show ed8162b0^:bootstrap/compile_driver.ll`, linked against the current runtime)
and run. It **accepted** this shape and discarded the `in <body>` in silence. The report's own repro
failed there only because dropping the body left the `let`'s `Int` where `Unit` was wanted; with the
types lined up nothing complained at all:

```sprout
do
  print("a")
  let x = print("b")
  in print("c")          # pre-ed8162b0: printed "a" only. Exit 0, no diagnostic.
```

So `let … in` as a `do` step has never once worked. `ed8162b0` turned silent wrong code into a loud
parse error, exactly as it did for the `0.0` step of issue 1 — the error is the messenger.

**Wider than "multi-name", and the suggested fix would not have covered it.** Single-binding is
equally broken, and so are both `else` forms. One token of lookahead after the first binding finds
the `in` only when the group has one plain binding: an `else` carries the parse past it, so
`let Ok x = a else Err c -> c` / `Ok y = b else Err c -> c` / `in …` — valid §5.2.1, and only ever
expressible as the expression form, since §5.2.1a makes an `else` binding stand alone — still failed.

The fix instead decides the reading from the **whole step**: a step beginning with `let` that holds a
bracket-depth-0 `in` is parsed as one expression, and that reading is kept only if it accounts for
the entire step. Anything else falls through to the statement path untouched, which is what keeps a
`let` *statement* whose right-hand side contains a nested `let … in` (a `match` arm, say) parsing as
it does today. Nothing accepted today changes meaning: a step-level `in` is rejected outright right
now, because `in` heads no expression and so can never begin a step.

`in` must still be **dedented to the `let` column**, as §5.2.1 has always required. The one-line
spelling `let x = 1 in x` remains rejected — not a do-step matter, since it fails in ordinary
expression position too, and §5.2.1 never permitted it. Filed in `BACKLOG.md` together with its
diagnostic, which reports `Expected pattern` against the *following* declaration.

Covered by `tests/stdlib/test_do_let_in_step.spr` (single, multi, `else`, chained `else`, a
non-final step, an effectful body, and the nested-right-hand-side case that must stay a statement).
§5.2.1 and §5.2.1a now state the `in` rule together instead of contradicting each other.
