# String building: `++` chains vs backtick templates

Status: **non-normative.** `docs/spec-v0.md` remains the normative source for
language semantics; nothing here changes the language. This document explains
what the two string-building forms *cost*, so the choice between them can be made
on evidence instead of intuition.

Date: 2026-08-21.

Short version, for readers who want only the rule:
[§7 Practical guidance](#7-practical-guidance).

## 1. The question

Sprout offers two ways to build a string out of pieces:

```sprout
fn format_pos(pos: SourcePos) -> String =
  int_to_string(pos.line) ++ ":" ++ int_to_string(pos.column) ++ ": "

fn format_pos_t(pos: SourcePos) -> String = `${pos.line}:${pos.column}: `
```

`docs/idiomatic-sprout.md` used to say only "Append with `++`; interpolate with
backtick templates", which reads as *a value to embed ⇒ use a template* and
attaches no cost to either. That framing is what produced PR #171: the `++` chain
in `format_pos` was replaced with the template on the reasoning that **one**
concatenation call must beat **three**.

It does not. The template is the slower of the two at that size, and the reason
generalises into a rule with a measurable crossover.

## 2. How each form lowers

**`++`** resolves through `instance Semigroup String` (`stdlib/prelude.sprout`),
whose `append` is the `str_concat` builtin. It is **left-associative** — verified
in emitted IR, where the three appends of `a ++ b ++ c ++ d` chain as
`append(t0,t2)` → `append(t3,t4)` → `append(t5,t7)`, each feeding the previous
result in as its left operand.

`str_concat` (`runtime/sprout_runtime.c`) `strlen`s both sides, allocates
`left+right`, and `memcpy`s twice. So an n-part chain allocates **n−1** strings,
and each intermediate is copied again by the next append.

**A backtick template** lowers to *build a `List String` of the parts, then call
`string_concat_many`*. `string_concat_many` is two-pass: walk the list summing
`strlen`, allocate exactly once, walk again copying. So it allocates **1** string
regardless of part count — but it first needs the list, which is **n** `Cons`
cells plus a `Nil`.

The template also emits **one `to_string` call per part**, and emits it even when
the instance is identity — `instance ToString String` is literally `value`. This
matters for the analysis in §5: adding a part loads the template side too, not
only the `++` side.

## 3. Allocation accounting

Allocating calls inside the function body only, for the four-part `format_pos`
case, both forms compiled in isolation so the shared prelude cannot drown the
difference (`--emit-ir`, counting calls in the emitted `define`):

| | `++` chain | `` `${…}` `` template |
|---|---|---|
| strings from `int_to_string` | 2 | 2 |
| strings from concatenation | 3 (`str_concat`) | 1 (`string_concat_many`) |
| list nodes (`sprout_alloc_obj`) | 0 | 5 — 4 × `Cons` + 1 × `Nil` |
| **total heap allocations** | **5** | **8** |
| GC root pushes | 6 | 9 |
| emitted IR lines | 34 | 68 |

Generalising: **`++` allocates n−1 objects; a template allocates n+2.** The
template is always **3 allocations behind**, at every size — there is no part
count at which it catches up on this axis.

What it wins is **bytes copied**. For n parts of length k:

- `++` copies `k · (2 + 3 + … + n)` = `k · (n(n+1)/2 − 1)` — **quadratic in n**.
- a template copies `k · n` — **linear in n**.

For the 7-byte `format_pos` result that is 15 bytes copied versus 7. Small in
absolute terms, which is why allocation count decides at that size.

## 4. Measured crossover

`just bench-string-concat` (`scripts/bench_string_concat.sh`). Best-of-3 wall
clock, 200 000 iterations per cell, `-O2`, parts pre-bound as `String` so nothing
in the loop measures `int_to_string`. Both binaries are asserted to print the
same accumulator before either is timed, so a mismatch cannot be reported as a
speedup. Apple silicon, macOS; absolute times are machine-specific, the ordering
and ratios are the result.

| parts | part len | result size | `++` (ms) | template (ms) | winner |
|---|---|---|---|---|---|
| 2 | 4 | 8 B | **20** | 42 | `++` **2.10×** |
| 4 | 4 | 16 B | **30** | 47 | `++` 1.57× |
| 8 | 4 | 32 B | **48** | 63 | `++` 1.31× |
| 16 | 4 | 64 B | **89** | 105 | `++` 1.18× |
| 4 | 40 | 160 B | **44** | 62 | `++` 1.41× |
| 8 | 40 | 320 B | **77** | 92 | `++` 1.19× |
| 2 | 40 | 80 B | **26** | 47 | `++` 1.81× |
| 2 | 400 | 800 B | **83** | 108 | `++` 1.30× |
| 32 | 4 | 128 B | 205 | 196 | *coin flip* — see below |
| 4 | 400 | 1.6 KB | 172 | 172 | *tie* |
| 16 | 40 | 640 B | 206 | **158** | template 1.30× |
| 8 | 400 | 3.2 KB | 458 | **331** | template 1.38× |
| 32 | 40 | 1.3 KB | 510 | **286** | template 1.78× |
| 16 | 400 | 6.4 KB | 2068 | **567** | template 3.65× |
| 32 | 400 | 12.8 KB | 10259 | **1090** | template **9.41×** |

Rows are ordered by winner then margin, not by the loop order, to make the band
structure visible.

**The 32 × 4 B cell is a coin flip, and is recorded as one deliberately.** Two
runs of the same settings on the same machine put it at `++` faster 1.05× and
template faster 1.05× respectively (182/192 then 205/196 ms). A ~5% gap at this
size is not a result. It is called out rather than smoothed away because it is
the cell nearest the "does part count alone flip the winner?" question in §5, and
reporting either direction as the answer would be reading noise as signal.

## 5. What actually decides it

**Bytes copied, not part count.** This is the counter-intuitive result and the
one worth remembering.

With 4-byte parts, `++` wins from 2 parts all the way to 16 (2.10× down to
1.18×), and 32 parts is a coin flip — *not* a template win. Compare that to
holding the part count at 16 and growing the parts: `++` wins at 4 B, loses at
40 B, and loses 3.65× at 400 B. **Length moves the winner; count barely does.**

Count does not tip the balance because it loads *both* sides: each extra part
costs the template a `Cons` cell **and** a `to_string` call (§2), while costing
`++` one more append whose copy is cheap when the strings are small. The two
per-part overheads largely cancel, leaving the quadratic copy term as the only
thing that can decide — and that term stays negligible until the strings get big.

The honest edge of this claim is the 32 × 4 B cell: the per-part overheads cancel
*almost* exactly there, which is why it lands within noise instead of on one
side. Read it as "count has run out of ability to decide", not as a template win.

Cross-checking the table against cumulative copy work `k · n(n+1)/2`:

| cell | copy work | winner |
|---|---|---|
| 8 parts × 40 B | ~1.4 KB | `++` 1.19× |
| 32 parts × 4 B | ~2.1 KB | coin flip (§4) |
| 4 parts × 400 B | ~4.0 KB | tie |
| 16 parts × 40 B | ~5.4 KB | template 1.30× |
| 32 parts × 400 B | ~211 KB | template 9.41× |

The flip lands consistently at **~2–4 KB of cumulative copying**, which for
typical shapes means a **result of roughly 1–2 KB** — regardless of how that size
is composed.

## 6. Building strings in a loop

A distinct trap, and **a template does not fix it.** Appending to an accumulator
once per iteration is O(n²) copying whichever syntax the append is written in:
the accumulator is re-copied every time it grows.

```sprout
# O(n^2): each step copies the whole accumulator again.
fn render_go(rows: List String, acc: String) -> String =
  match rows with
  | Nil -> acc
  | Cons r rest -> render_go(rest, acc ++ r)      # same cost as `${acc}${r}`

# O(n): collect, then concatenate once.
fn render(rows: List String) -> String = string.join("", rows)
```

Accumulate into a `List String` and concatenate once — `string.join`
(`stdlib/string.sprout`) or `string_concat_many` (a prelude extern). That buys
the single-pass sizing that makes templates win at scale, and costs no cons cell
beyond the list you were already building.

## 7. Practical guidance

Both forms are idiomatic. **Choose on readability**; reach for the numbers only
when a profile sent you here. Neither is hot enough to matter outside a measured
bottleneck — the same rule `AGENTS.md` §"Builtin vs Stdlib" rule 6 applies to
performance claims generally.

When it does matter:

| situation | use | why |
|---|---|---|
| result under ~1 KB — diagnostics, labels, keys, messages | **`++`** | wins the whole band, 1.18×–2.10× |
| result ~1–2 KB | either | a tie; readability decides |
| result over ~3 KB | **template** | 1.38× at 3 KB → 9.41× at 13 KB |
| multi-line literal block (shader, query, HTML) | **template** | not about speed: newlines are literal content, so it replaces a `"…\n" ++ "…\n"` chain |
| accumulating in a loop | **neither** — `string.join` / `string_concat_many` | both forms are O(n²) per-iteration; see §6 |

Two things *not* to conclude:

- **"A template saves allocations."** It costs 3 more, at every size (§3).
- **"Many parts means use a template."** Part count alone does not flip it (§5).

And one thing to keep: embedding a converted value is *not* on its own a reason
to prefer `${…}`. `"total: " ++ int_to_string(total)` — which is
`docs/style-guide-v0.md`'s own preferred shape — is good style, not a form
awaiting modernisation.

## 8. Re-running, and when to

```
just bench-string-concat                 # defaults: ITERS=200000, REPS=3
ITERS=50000 just bench-string-concat     # faster, noisier
```

The script generates its fixtures, compiles and links each, verifies both forms
of a pair agree, then times them; nothing is committed to the repo but the script
itself.

It is deliberately **not** a gate. The output is a wall-clock timing, so any
threshold assertion on it would go red for machine load rather than for a defect
— the same reason `scripts/ir_byte_identical_check.sh` stays manual. Re-run it
after a change to:

- the template lowering in `stdlib/compiler/ast_to_ir.sprout`,
- `str_concat` or `string_concat_many` in `runtime/sprout_runtime.c`,
- the GC allocation path (`sprout_gc_alloc_cstr`, the collection threshold) —
  the crossover is a trade between allocation count and copying, so making
  allocation cheaper moves it.

## 9. Limits of this measurement

- **One machine, one OS** (Apple silicon, macOS). Ratios should travel; absolute
  times will not. The allocation *counts* in §3 are from emitted IR and are
  platform-independent.
- **Parts are equal-length.** A chain of wildly uneven parts copies differently;
  the quadratic term is driven by the running prefix, so a long first part is
  worse for `++` than a long last part.
- **`-O2` only.** Unoptimised builds were not measured.
- **Allocation counts are static call counts**, not a heap profile. They match the
  runtime's one-allocation-per-call structure (§2) but do not account for GC
  collections triggered along the way.
- **No concurrency.** Single-task loop; nothing here says how either form behaves
  under scheduler pressure or GC stress.
- **A cheap run is a wrong run.** Lowering `ITERS` does not buy a rougher version
  of the same answer — below ~100 ms per cell, jitter exceeds the effect. An
  `ITERS=20000 REPS=1` run reported "template faster 2.27×" for 8 short parts, a
  cell the defaults put at `++` faster 1.39× — the winner inverted, not just the
  margin. The script now marks sub-100 ms cells `[NOISY]` and says so in its
  summary; believe only unmarked rows.

## 10. Making the choice moot — open, not scheduled

Everything above documents a tradeoff contributors have to *know*. The better
outcome is not a better-documented tradeoff but no tradeoff: a template that is
never slower than the `++` chain it replaces. This section records what was
learned about how to get there. **Nothing here is decided or scheduled** — see
the `BACKLOG.md` entry that points at it.

### 10.1 The structural constraint

The desugar (`stdlib/compiler/desugar_ctx.sprout`) is **purely syntactic and runs
before typechecking** — its own header says "never sees inferred types". So at the
moment the strategy is chosen, the compiler knows:

- the number of effective parts,
- which parts are literals, and their exact text,

and does **not** know the type or the rendered length of any `${…}`. This is what
makes "use `++` when the result is short" undecidable at that point: *short* is a
runtime property of the interpolated values.

Three cases are already special-cased there (`desugar_ctx.sprout:14-17`), so the
cheap wins are taken: empty → `""`, **all-literal → one merged literal** (a real
constant-fold), single effective part → that part alone. Only the general case
builds the cons list, via `desugar_template_general`.

### 10.2 Making templates faster (keeping the current lowering)

| lever | saves | cost / blocker |
|---|---|---|
| **A. Flat buffer** — write parts into an `alloca` array, add a runtime `string_concat_n(ptr, count)` | **all n `Cons` cells + the `Nil`** — the entire allocation deficit from §3 | **new builtin ⇒ needs approval up front** (`AGENTS.md` §"Builtin vs Stdlib" 4–6). Rooting an n-slot array must respect the shadow-stack ABI (`docs/compiler-internals.md`); the existing API roots one slot per call, which is already n pushes |
| **B. Elide the identity `to_string`** on parts already typed `String` | n direct calls — **no allocations** | needs types, so it cannot live in the syntactic desugar; would be a post-typecheck peephole |
| **C. Merge adjacent literal parts** | ~nothing — the lexer already emits one literal per run | negligible either way |

**Only A closes the gap**, because the gap is allocations, not calls (§3). A would
make the template form ≥ `++` at every size and delete the crossover outright. B
is small by comparison.

### 10.3 Desugaring small templates to `++`

The other direction: leave the runtime alone and pick `++` when it is the better
lowering. Two variants, and the difference between them matters.

**Variant 1 — syntactic, on part count.** In `desugar_template_general`, emit a
left-associated `++` chain when the effective part count is ≤ N. Measured support
for N = 4 (§4): `++` wins or ties in **every** cell at 2 and 4 parts, including
400-byte parts. Cheap — one function, no type info, no runtime change, no new
builtin — and N = 4 covers the `${line}:${col}: ` shape that started all this.

Its honest weakness is a tail: four *very* long interpolated values (~10 KB each)
would be pessimised by roughly the copy ratio, ~2.25× at n = 4. Bounded, but
invisible at desugar time.

**Variant 2 — type-directed, on provable bounds.** Choose after typechecking,
where `to_string`'s resolved instance is visible. `Int`, `Bool`, `Char` and string
literals have compile-time-bounded render lengths, so a template made only of
those is *provably* small and `++` is *provably* better; a template containing
`${some_string}` keeps `string_concat_many`. No heuristic and no tail risk.

Cost: the decision has to move somewhere type-aware — either a peephole in
`ast_to_ir` that recognises the `string_concat_many(<cons-list>)` shape the
desugar emits and rewrites it when every element's type is bounded, or a split of
the desugar into syntactic and post-typecheck halves. The peephole is the smaller
of the two and needs no change to `desugar_ctx.sprout` at all.

### 10.4 Why this is worth doing, and why not for the reason you'd guess

**Not for speed.** Nothing here is a measured bottleneck, and `AGENTS.md`
§"Builtin vs Stdlib" rule 6 says that alone justifies nothing. A 1.4× win on a
function that runs once per diagnostic is not a reason to touch the compiler.

**For the guidance burden.** If small templates lowered to `++`, §§3–7 of this
document stop being something a contributor has to know: the crossover would not
exist in the size range real code lives in, "choose on readability" would be the
whole rule, and PR #171 becomes *impossible to get wrong* rather than merely
documented. Deleting a rule beats explaining it.

That argument favours **Variant 2** — a heuristic that is right in the common case
still leaves a tail to explain, which is most of what makes the current situation
annoying.

### 10.5 Required before implementing

1. **Prior-art survey** (`AGENTS.md` §"Design Change Process" 3). Interpolation
   lowering is a settled question elsewhere — C#, Java, Kotlin, Scala all make a
   strategy choice here — and each row must be verified against a language
   reference or spec, not recalled. Not yet done.
2. **Builtin approval** for 10.2 A specifically, before any code.
3. **A regression pin on the emitted shape.** Whichever variant lands, the point
   is *which lowering is chosen*, so the test has to assert on emitted IR (as
   `tests/golden/ir/` and the smoke shapes already do), not only on the resulting
   string — an output-only test passes under either lowering and would let a
   silent revert through.
4. **Re-run `just bench-string-concat`** after, and update §4. A change to the
   lowering moves the crossover by construction; if the numbers do not move, the
   change did not take effect.
