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
- **§2 — single-line `import`.** Pending.
- **§3 — multi-name `let` as a `do` statement.** Pending. The root cause is not `let`-specific:
  `parse_do_step_sub` discards unconsumed step tokens for *every* statement shape, so the silent
  discard is general.
