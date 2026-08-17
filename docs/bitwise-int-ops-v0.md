# Bitwise integer operations (v0)

**Status:** proposed. Design only; nothing here is implemented. `docs/spec-v0.md` is
normative for the stable core; this document holds the rationale and the decisions that
implementation would need.

## 1. Problem statement

Sprout has **no language-level bitwise operations**. There is no way to mask a field out
of a word, no way to shift, and no way to XOR two integers. The arithmetic operator set
is `+ - * /` — there is not even a `%`, which is why `stdlib/rng.sprout:24` carries a
hand-rolled `imod(a, b) = a - ((a / b) * b)`.

Four consequences are visible in the tree today:

| Consequence | Where |
|---|---|
| SHA-256, HMAC-SHA-256 **and a plain byte-wise XOR** are C builtins | `runtime/APPROVED_BUILTINS:64-67`, `stdlib/crypto.sprout:17` |
| The PRNG is an LCG over `imod`, not a xorshift | `stdlib/rng.sprout:24,28` |
| The 64-bit GC header word (kind 8b \| colour 2b \| reserved 4b \| aux 50b, ~14 consumer sites) cannot be read from Sprout at all | `docs/gc-phase2-retro-handoff-2026-07-05.md` §2 |
| `stdlib.math`'s exponent extraction has to substitute division and subtraction for shift and mask | `docs/double-bit-access-v0.md` §5 |

The last row is the instructive one. `double-bit-access-v0.md` §5 declared bitwise
operators an explicit non-goal and recorded why it could get away with that: range
reduction only ever runs on **positive** magnitudes, so the sign bit is 0, the biased
exponent is exact integer division by `2^52`, and the mantissa is recovered by
*subtraction* rather than masking. That is a real precondition documented at the use
site — and it does not generalize. Nothing outside that narrow case can avoid masking.

`docs/gc-phase2-retro-handoff-2026-07-05.md` §2 names the gap as the **entry ticket** for
two larger items — bit-packed record types, and the deferred OCaml-style integer-tagging
project — and `BACKLOG.md` §528 carries it as a `P2` pair with sized unsigned ints.

**This is a capability decision, not a speed one.** Stated plainly because "Builtin vs
Stdlib" rule 6 requires it: there is no measured application bottleneck here. The
argument is that a class of program — hashing, packing, PRNGs, anything word-oriented —
is currently *inexpressible*, and the workaround has been to move it into C. That is the
opposite of the direction the runtime is supposed to travel.

## 2. Goals and non-goals

**Goals**

- Make mask, shift and field-extract expressible in pure Sprout on `Int`.
- Add **no runtime symbol** and **no `runtime/APPROVED_BUILTINS` entry** — the same bar
  `double_to_bits` met.
- Make a pure-Sprout SHA-256 core and a xorshift PRNG implementable, so the crypto
  builtins become a *choice* rather than a necessity.

> Verified that bitwise ops are the **sole remaining** language-level blocker for a
> pure-Sprout hash core, not one of several: `stdlib/bytes.sprout` already provides
> `bytes_get : Bytes -> Int -> Maybe Int` (`:9`) and a pure-Sprout `read_u32_be` (`:98`),
> so the byte→word loads such a core needs are expressible today.

**Non-goals** — each deferred with a pointer, not merely absent:

- **Sized unsigned ints (`U8`…`U64`) and `packed type`.** The other half of
  `BACKLOG.md` §528. Bitwise-on-`Int` is independently useful and far cheaper; the
  packed-record half additionally needs a representation decision for sub-word types.
- **Bitwise *operators*.** Rejected surface — §4.
- **A `Bits` typeclass** (Haskell's shape). These are `Int`-monomorphic, matching the v0
  fence `docs/operators-v0.md` §2 sets for arithmetic.
- **`rotate` / `popcount` / `leading_zeros` / `reverse`.** Ordinary stdlib functions
  composed from the primitives, not primitives themselves. Go is the precedent: its
  `math/bits` is a *library*, and the package documentation notes that "Functions in this
  package may be implemented directly by the compiler, for better performance. For those
  functions the code in this package will not be used." A future intercept of a
  `bits.rotate` written in Sprout is exactly that pattern, and needs no new design.
- **Deciding `Int`'s overflow policy.** Still open in
  `docs/int-overflow-policy-decision.md`, and this design is now **independent of it**:
  §5.2 exempts `bit_shl` from it outright, so nothing here waits on that deferral and
  nothing here changes when it resolves.

## 3. Prior-art survey

Every row below was verified against the language's own reference. Where a source could
not be retrieved, the row is **omitted rather than asserted from memory**.

| Language | Bitwise surface | What it settles |
|---|---|---|
| **F#** | `&&&` `\|\|\|` `^^^` `~~~` `<<<` `>>>` | F# is in **Sprout's exact situation** — its `>>` and `<<` are the forward and backward *function composition* operators ("Composes two functions (forward composition operator)"), and its `&&`/`\|\|` are boolean. It did not reuse them: it tripled the characters. |
| **OCaml** | infix **keyword** operators `land lor lxor lsl lsr asr`; `>>`/`<<` do not exist | With a single `int` type, OCaml splits logical (`lsr`) from arithmetic (`asr`) right shift into **two separate operators**. Also: bitwise on a non-obvious width (OCaml's `int` is 63-bit). |
| **Elm** | a **named-function module**, `Bitwise`: `and or xor complement shiftLeftBy shiftRightBy shiftRightZfBy` — no operators at all | The named-function precedent, and the same arithmetic/zero-fill right-shift split: `shiftRightBy` fills "with whatever is the topmost bit", `shiftRightZfBy` fills "with zeros". |
| **Haskell** | `Data.Bits`: `.&.` `.\|.` `xor` `complement` `shiftL` `shiftR` | Two things. `shiftL`'s count "must be non-negative". And for **`Integer`** (arbitrary precision) `rotate` is equivalent to `shift` and `bitSizeMaybe` returns `Nothing` — the primary-source confirmation that width-dependent bit operations have no meaning on an unbounded integer. |
| **Rust** | `& \| ^ !` and `<< >>` | `>>` is an arithmetic shift on signed types and a logical shift on unsigned ones — the choice is made by the *type*, which Sprout cannot do with one `Int`. And the overflow rule: using `<<`/`>>` "where the right-hand argument is greater than or equal to the number of bits in the type of the left-hand argument, or is negative", which panics in debug builds. |
| **Go** | rotate/popcount/leading-zeros in the `math/bits` **library**, not operators | See the non-goal above. |

Sources: learn.microsoft.com *F# Symbol and Operator Reference*; OCaml 5.2 manual §7.1
(expressions/precedence); `elm/core` `src/Bitwise.elm`; Hackage `base` `Data.Bits`; The
Rust Reference, *Operator expressions*; pkg.go.dev `math/bits`.

**Not verified this pass, therefore not cited:** Go's *shift-count* rules. `go.dev/ref/spec`
truncates before the arithmetic-operators section on retrieval, and the wording is
load-bearing enough (it is the likely precedent for the recommendation in §5) that
paraphrasing it from memory would be worse than leaving it out. Same choice, and for the
same reason, as `docs/int-overflow-policy-decision.md` §3.

**Consensus, and where there is none.** *Spelling* is genuinely divergent — symbolic
(F#, Rust, Go), keyword-infix (OCaml), or plain named functions (Elm, and Go for
everything beyond the six primitives). So the surface is a house-style call rather than a
settled question. Two things *are* settled: a language whose `>>` means composition does
**not** reuse it for shifts (F#, OCaml), and a language with a single integer type needs
**two** right shifts (OCaml, Elm).

## 4. Decision, and why the alternative lost

**Chosen: seven intrinsic `extern fn` declarations in a new `stdlib/bits.sprout`.**

```sprout
extern fn bit_and(a: Int, b: Int) -> Int
extern fn bit_or(a: Int, b: Int) -> Int
extern fn bit_xor(a: Int, b: Int) -> Int
extern fn bit_not(a: Int) -> Int
extern fn bit_shl(x: Int, n: Int) -> Int      # left shift
extern fn bit_shr(x: Int, n: Int) -> Int      # arithmetic right shift (sign-filling)
extern fn bit_shr_zf(x: Int, n: Int) -> Int   # logical right shift (zero-fill)
```

Seven declarations over six distinct LLVM instructions: `bit_shr` is `ashr`,
`bit_shr_zf` is `lshr`, and `bit_not` is `xor -1` (so it can reuse the xor op with a
constant rather than needing a seventh).

Usage, for the two motivating shapes:

```sprout
import stdlib.bits as bits

# GC-header-style field extraction
fn header_kind(w: Int) -> Int = bits.bit_and(w, 255)
fn header_aux(w: Int) -> Int  = bits.bit_shr_zf(w, 14)

# SHA-256's rotr32 — composed from the primitives, not a primitive
fn rotr32(x: Int, n: Int) -> Int =
  bits.bit_and(bits.bit_or(bits.bit_shr_zf(x, n), bits.bit_shl(x, 32 - n)),
               4294967295)
```

### Why not operators — the F#-style route

`&`, `^` and `~` are not lexed at all today (`lexer.sprout:53-54`: the single-symbol set
is `()=,:+-*/<>\!{}[]|`), and `>>`/`<<` are **taken** — `parser.sprout:1046-1055`
desugars them to `rcompose`/`lcompose` (`prelude.sprout:1054-1059`). So operators would
mean new spellings, F#-style: `&&& ||| ^^^ ~~~ <<< >>>`. Rejected for four reasons:

1. **Zero surface cost.** No lexer entry, no precedence tier, no fixity decision, no
   associativity call. The whole change is one module plus a codegen intercept.
2. **It does not re-open the miscompile class.** `docs/operators-v0.md` §1 identifies
   hand-coded operator typing (`infer_unary`, `check_arith`) as the *mechanism* behind
   the `! 3 → 2` miscompile: a hand-written path enforces only what someone remembered
   to enforce. An `extern fn` types through the ordinary call path, so the signature
   **is** the constraint and there is no special case to forget.
3. **Forward-compatible, not a dead end.** If `docs/operators-v0.md` model B1 ever
   lands, operators desugar to exactly these functions and lower through exactly this
   intercept. That is literally OCaml's model — `external ( lsl ) : int -> int -> int =
   "%lslint"`, where the signature governs typing and the primitive name governs
   codegen. Adopting operators later costs a parser desugar and nothing else.
4. **Two right shifts cannot share one operator.** Rust gets away with a single `>>`
   because signedness lives in the type; Sprout has one `Int`, so the distinction has to
   be in the name. OCaml, with the same constraint, reached the same answer (`lsr` vs
   `asr`). An operator design would need a seventh spelling anyway, and `>>>>` is not a
   serious proposal.

## 5. Syntax and semantics

Seven monomorphic functions on `Int`. All are total for in-range shift counts; the
out-of-range cases are specified below. Argument order is `(value, count)` for the
shifts — operand-first, matching the operator reading `x shl n` rather than the prelude's
data-last convention, which applies to containers.

### 5.1 The arbitrary-precision tension — the load-bearing part

`docs/int-overflow-policy-decision.md` §1 records that Sprout's `Int` is *specified* as
an arbitrary-precision mathematical integer (spec §8.4) and that lowering it to machine
`i64` is "a temporary v0 implementation constraint, not the intended long-term meaning
of `Int`". Bitwise operations do not interact with that uniformly, so this design
partitions them by width-dependence and commits per function:

| Function | Under an arbitrary-precision `Int` | Verdict |
|---|---|---|
| `bit_and` `bit_or` `bit_xor` | Well-defined on infinite two's complement — bit *i* of the result depends only on bit *i* of each operand, and sign-extension is well-defined at every position. This is exactly Haskell's `Bits Integer`. | **Width-independent.** Survives the widening unchanged. |
| `bit_not` | `bit_not x = -x - 1` on infinite two's complement — no width appears in the definition. | **Width-independent.** |
| `bit_shr` (arithmetic) | `floor(x / 2^n)`, for any `n >= 0`. No width appears. | **Width-independent.** |
| `bit_shl` | Exact `x * 2^n` under bignum; **discards high bits** under i64, which §5.2 makes its specified behaviour rather than an overflow condition. | **64-bit-scoped.** |
| `bit_shr_zf` (zero-fill) | **Meaningless.** "Fill from the top bit" presumes a top bit; an arbitrary-precision negative has none. | **64-bit-scoped.** |

So **five of the seven functions are width-independent and two are not.** Neither of the
two can be dropped: GC-header `aux` extraction needs `bit_shr_zf`, as does any code
reading a negative `Double`'s bit pattern via `double_to_bits`, and `bit_shl` is how a
mask gets built in the first place.

Both are therefore specified honestly — **defined in terms of the 64-bit two's-complement
representation**, and flagged at their declarations as respecify-or-restrict if `Int` ever
widens. That is the whole point of writing the partition down: the widening becomes a
documented breaking change against two named functions, rather than a silent output drift
across all seven. Haskell's `bitSizeMaybe = Nothing` for `Integer` is the primary-source
confirmation that this boundary is real rather than pedantry, and not a Sprout quirk.

### 5.2 Value-overflow: `bit_shl` discards, and is not an error

Two overflows have to be kept apart, and the verified Rust wording is what separates them:

- **Count-overflow** — `n < 0` or `n >= 64`. Rust calls exactly this (and *only* this)
  overflow. Genuinely a soundness item, decided in §5.3.
- **Value-overflow** — bits shifted off the top by `bit_shl`. Rust does **not** treat this
  as overflow: `1i64 << 63` truncates silently even in debug, in a language that panics on
  `*` overflow. This subsection.

Conflating them is the trap: the first is undefined behaviour if unguarded, the second is
ordinary defined behaviour everywhere, so a single "shift overflow" policy would either
leave poison reachable or reject valid programs.

**Decided (2026-08-17, Kuba): `bit_shl` discards the high bits, always, and is exempt
from `*`'s overflow policy.** It is specified as `x * 2^n` **within a 64-bit window**,
with bits above position 63 discarded — which is why §5.1 lists it as 64-bit-scoped
rather than width-independent.

```
bit_shl(1, 63)  == -9223372036854775808   # never panics
bit_shl(3, 63)  == -9223372036854775808   # two bits discarded, still not an error
```

The consequence, stated plainly: `bit_shl`'s behaviour does **not** change if
`docs/int-overflow-policy-decision.md` later lands on trapping. It is a bit-window
operation that happens to coincide with multiplication in range, not multiplication that
happens to be implemented by shifting.

**Prior art, verified 2026-08-17 — this is the unanimous answer**, and the survey is what
decided it. Note it cuts *against* the alternative that was recommended here in the first
draft (couple to `*`, inheriting the deferred decision), which is recorded below rather
than deleted, because the argument for it is real and will resurface when `Int` widens.

| Language | Traps `*` overflow? | Left shift with bits falling off the top |
|---|---|---|
| **Rust** | yes, panics in debug | **discards silently** — for shifts, overflow is *only* "the right-hand argument is greater than or equal to the number of bits in the type of the left-hand argument, or is negative". `1i64 << 63` never panics. |
| **Swift** | yes — "by default Swift reports an error rather than allowing an invalid value to be created" | **discards silently** — "Any bits that are moved beyond the bounds of the integer's storage are discarded." |
| **Zig** | yes (Illegal Behaviour; panics in Debug/ReleaseSafe) | plain `<<` carries no overflow qualification: "Moves all bits to the left, inserting new zeroes at the least-significant bit". Checking is in *separate* operations — `<<\|` (saturating), `@shlExact`, `@shlWithOverflow`. (Their exact semantics were not retrievable; only their existence and the `<<` wording are verified.) |
| **Python** | n/a — arbitrary precision | never truncates, and the definition is the notable part: "A left shift by *n* bits is **defined as multiplication with `pow(2,n)`**". |
| **Haskell** | n/a for `Integer` | `shiftL` is exact on `Integer` (unbounded — `bitSizeMaybe` is `Nothing`); truncates on fixed-width `Int`. |

Two things follow, and together they are the decision. **Every** language surveyed
*defines* left shift as multiplication by `2^n`, so that wording is uncontroversial and
survives here — the divergence between the camps is purely the overflow *policy*. And the
three safety-first languages that **do** trap `*` overflow all **exempt** the shift, Zig
going as far as adding three separate operations rather than making plain `<<` checked.
Coupling would therefore not have been merely "Sprout-original": it would have been
contradicted by the three closest precedents.

### The rejected alternative, and why it will resurface

Couple `bit_shl` to `*`, inheriting `docs/int-overflow-policy-decision.md`. Its argument is
the one that document's §5 item 2 makes for arithmetic, and it is not a weak one: under a
trapping policy, an overflowing program panics today and *succeeds* once `Int` is
arbitrary-precision — a safe transition — whereas a program relying on truncation
*changes output* at the widening. **None of the three trapping languages carries this
consideration, because none of them intends to widen its integer type, while
`docs/spec-v0.md` §8.4 says Sprout does.**

What settles it is that the §5.1 partition converts that risk into a *documented* one.
`bit_shl` and `bit_shr_zf` are declared 64-bit-scoped from day one, so the widening
respecifies two named functions as a visible breaking change instead of silently altering
what working programs compute. The alternative bought the same protection by making a
routine shift fail: had `*` landed on trapping, `bit_shl(1, 63)` would panic — a shift
every other language surveyed performs without complaint — and the lexer cannot even write
the `INT_MIN` literal one would use to work around it (`int-overflow-policy-decision.md`
§5).

Either way, the sign-bit mask never needed `bit_shl`:

```sprout
bits.bit_not(bits.bit_shr_zf(bits.bit_not(0), 1))   # 0x8000000000000000
```

**Re-open this if the bignum migration is ever scheduled.** At that point `bit_shl`'s
respecification is on the table regardless, and the coupling argument gets its hearing with
a concrete migration plan behind it rather than a hypothetical one.

### 5.3 Shift-count domain is a soundness item, not a footnote

LLVM's `shl` / `lshr` / `ashr` yield **poison** when the count is `>= 64`. A naive
lowering therefore reintroduces the exact undefined-behaviour class W7 closed for
`sdiv i64 x, 0` (`docs/int-overflow-policy-decision.md` §2 — and note that document's
finding that plain `add i64` overflow is *defined*, so this would be a genuine
regression in kind, not more of the same).

**Decided (2026-08-17, Kuba): the mathematical model.** A count `>= 64` yields the limit
of the mathematical definition — `0` for `bit_shl` and `bit_shr_zf`, and `0` or `-1` (all
sign bits, per the operand's sign) for `bit_shr`. A **negative** count is a **panic**
with a source location, reusing W7's `IRPanic` terminator.

```
bit_shl(1, 64)     ==  0     # not 1
bit_shl(1, 70)     ==  0
bit_shr(-8, 64)    == -1     # all sign bits
bit_shr_zf(-8, 64) ==  0
bit_shl(1, -1)     ->  panic at <file>:<line>
```

Two reasons, in order of weight. It is consistent with the arbitrary-precision reading in
§5.1, where "shift by 70" is a perfectly ordinary request that happens to fall off the
end of a 64-bit window rather than a malformed one. And Haskell's `shiftL` count "must be
non-negative" is the verified precedent for **rejecting** a negative count outright
instead of silently reinterpreting it as a large positive one.

**The alternative, and why it lost.** Masking the count to `& 63` (Java, JavaScript) is
branch-free and needs no guard at all, but it makes `bit_shl(1, 64) == 1` and turns
`bit_shl(1, -1)` into a shift by 63. Both are silent lies about what the program asked
for, which is the failure mode this language does not accept elsewhere (the same
objection `int-overflow-policy-decision.md` §5 raises against silent arithmetic wrap).

**Cost, stated honestly.** A non-constant count pays a compare and a branch. A count that
is an integer literal in `0..63` pays nothing — the guard is folded away at translate
time (§9), and that covers every GC-header field extraction and every mask in this
document's examples.

### 5.4 Not first-class values

Like `print`, `to_double` and `double_to_bits`, these are compiler intrinsics with no
runtime symbol, so they **cannot be passed as values**: `list_fold(bit_xor, 0, xs)`
references an undefined symbol and fails to link. That is loud rather than silent, and is
the pre-existing behaviour of this whole class of declaration. The workaround is a
one-line wrapper — `fn bxor(a: Int, b: Int) -> Int = bit_xor(a, b)` — which is an
ordinary function and passes fine.

### 5.5 Companion gap: there are no hex literals

`lexer.sprout:22-23` — `scan_int_end` is `take_while(is_ascii_digit)`. There is no `0x`
form, no binary form, and no `_` digit separator. So every mask in this design has to be
written in decimal, which `tests/stdlib/test_double_bits.spr` already demonstrates the
cost of: `4503599627370496` in the code with `2^52` explained in a comment, and
`0x3FF0000000000000` reachable only as `4607182418800017408`.

A bitwise design whose masks are unwritable in hex undercuts its own expressibility
claim. `0x` / `0b` integer literals are a **lexer-only** change (one more branch in
`scan_int_next`, no parser or type impact) and are **recommended to land in the same
arc**. Filed to `BACKLOG.md` either way so it cannot go silent.

## 6. Type-system impact

None. Seven monomorphic `Int -> Int -> Int` / `Int -> Int` signatures. No new type, no
class, no inference change. Under the i64-uniform value ABI there is no representation
change either.

Note what is deliberately *not* done: no `Bits` typeclass, so there is no dictionary, no
dispatch, and nothing for `verify_dispatch` to reason about. If sized ints arrive later
and a class becomes worthwhile, these seven names are its `Int` instance bodies.

## 7. Error-message impact

- Wrong arity or wrong operand type: ordinary check errors, from the ordinary call path.
- **New:** the negative-shift-count panic, which needs the same source-location quality
  as W7's divide-by-zero panic.
- The intrinsic first-class-value restriction (§5.4) surfaces as a link error, exactly as
  it does for `to_double` and `print` today.

## 8. Compatibility and migration

Purely additive. No existing program changes meaning.

- **Module resolution needs no change.** `module_loader.sprout:179-185` maps `stdlib.*`
  to a path mechanically, so `stdlib.bits` → `stdlib/bits.sprout` resolves with no
  loader edit. (Extending `module_name_to_path`'s scope is out of bounds regardless.)
- **No bundler type-list entry.** `bundler.undeclared_type_names` (`:1126`) exists for
  opaque *types* that reach a type position only through an extern signature; these
  signatures mention only `Int`.
- **Where the declarations live.** `stdlib/bits.sprout` is a leaf module and its
  consumers would import it anyway, which is precisely the condition spec
  §"Externs are outside the module system" states for declaring an extern outside the
  prelude. This also follows the 2026-08-15 prelude-extern relocation that moved
  `double_to_bits` out of the prelude into its consumer module
  (`docs/double-bit-access-v0.md` §5 note).

### 8.1 A verified shadowing hazard this widens (pre-existing, class-wide)

Worth recording precisely, because it is a **silent miscompile** rather than an error,
and adding seven short names widens its surface. Established by code trace:

1. Spec §"Externs are outside the module system" is normative that an extern "is never
   qualified, never renamed" — confirmed in `bundler.qualify_decl:1070`, where
   `ExternFnDecl` alone passes its name through untouched while every other declaration
   is canonicalized. Extern names therefore live in a **global flat namespace**. (This is
   why `tests/stdlib/test_double_bits.spr:42` calls `double_to_bits` bare despite
   importing `stdlib.math` under an alias.)
2. `ast_to_ir.translate_call:4842-4853` matches the **bare** `fname` against the
   intrinsic list *before* consulting captures' siblings, `params`, `let_names`, or the
   top level.
3. `bundler.qualified_name:157-161` returns the **bare** name when the module name is
   empty, and `bundler.scan_source_info:268-283` leaves it empty for any file with no
   `module` line — the shape of every `.spr` test and most single-file programs.

Together: a headerless file that defines its own `fn bit_and(a, b)` keeps the bare name,
and its call sites are intercepted and lowered to the intrinsic — **the user's body is
silently ignored**. The same is already true today of a headerless file defining
`fn print`, `fn to_double`, or `fn double_to_bits`; the names are just obscure enough
that nobody has hit it. Repro shape, for whoever picks this up: a one-file program with
no `module` line, `fn to_double(n: Int) -> Double = 1.0`, and one call.

Two responses, both recorded rather than silently assumed:

- The **`bit_` prefix** is chosen partly for this reason (over the shorter
  `band`/`bor`/`shl` the gc-phase2 handoff sketched) — it keeps the occupied names
  distinctive, in the same spirit as `char_to_string` / `double_to_bits`.
- The general fix — **reject a user declaration that shadows an intercepted intrinsic**
  — is a class-wide diagnostic that does not belong to this change. Filed to
  `BACKLOG.md`.

### 8.2 Seed impact

`stdlib/bits.sprout` is **not** the prelude, so its `extern fn` declarations do *not*
add `declare` lines to `bootstrap/compile_driver.ll` — the AGENTS.md caveat about a new
prelude extern does not apply, and this is exactly why `double_to_bits` was relocated out
of the prelude.

A full `just refresh-seed` is nonetheless required, for the ordinary reason: the
implementation edits `stdlib/compiler/` (four files, §9). Delete the stale stage-1 binary
first.

## 9. Implementation overview (for approval; no code written for this document)

The `double_to_bits` route, one step heavier because these ops do emit instructions.
Blast radius verified against `IRIMul` as the exact template, per new IR op:

| File | Sites | What |
|---|---|---|
| `stdlib/compiler/sprout_ir.sprout` | 3 — `:18` comment block, `:90` constructor, `:534` debug render | six new constructors |
| `stdlib/compiler/ir_lowering.sprout` | 1 — `:137` | `and` / `or` / `xor` / `shl` / `ashr` / `lshr` `i64` |
| `stdlib/compiler/ir_rooting.sprout` | 5 exhaustive matches — `:147, :280, :378, :454, :760` | all scalar: `false` / `Nothing` / operand list / result name |
| `stdlib/compiler/ast_to_ir.sprout` | 1 — `translate_call` ~`:4850` | name intercept beside `double_to_bits` |
| `stdlib/bits.sprout` | new file | the seven declarations, with the §5.1 width note at `bit_shr_zf` |

Two notes for whoever builds it:

- **There is no second codegen path to update.** `stdlib/compiler/codegen.sprout` no
  longer exists, so only the typed path needs touching — simpler than what the older
  design docs (which reference `codegen.sprout:2106` for arithmetic) describe.
- **Shift guards go in `ast_to_ir`, never `ir_lowering`.** The guard CFG must be built
  where W7 built its divide-by-zero guard; block-splitting in the `ir_lowering` text
  layer breaks phi predecessors (`docs/int-overflow-policy-decision.md` §6). And when
  the count is an integer literal in `0..63` — the dominant case, and *every* GC-header
  field extraction — the guard is folded away at translate time, so a constant shift
  costs one instruction and no branch.

## 10. Tests

To be written with the implementation, per Definition of Ready #2 (failing first):

- `tests/stdlib/test_bits.spr` — the algebraic identities (`bit_and(x, x) == x`,
  `bit_xor(x, x) == 0`, `bit_not(bit_not(x)) == x`, `bit_or(x, bit_not(x)) == -1`);
  `bit_not(0) == -1` and `bit_not(x) == -x - 1`; **`bit_shr` vs `bit_shr_zf` on a
  negative** (the pair's whole reason for existing); and a composed `rotr32` as the
  realism check.
- The §5.3 count boundary, which is decided behaviour and therefore must be pinned rather
  than left to the lowering: counts `0`, `1`, `63` normal; `bit_shl(1, 64)` and
  `bit_shr_zf(-8, 64)` are `0`; `bit_shr(-8, 64)` is `-1`; a negative count panics. Note
  counts `>= 64` are exactly where a naive lowering yields LLVM poison, so a wrong
  implementation here is liable to *look* right at `-O0` and diverge under optimisation —
  the test must assert values, not merely that it does not crash.
- The §5.2 discard behaviour, which needs pinning for the opposite reason — it must
  **not** become an error: `bit_shl(1, 63) == -9223372036854775808` and
  `bit_shl(3, 63) == -9223372036854775808` (two bits discarded), both total. These are the
  regression guard if `*` later gains overflow trapping; a shared `IRIMul`-style guard
  applied over-eagerly would break exactly here.
- A `Bytes`-driven SHA-256 round against a known digest, if the pure-Sprout core lands
  in the same arc — the strongest end-to-end evidence that the set is sufficient.
- Golden IR: a constant shift must emit one instruction and no branch (§9), which is the
  claim most likely to regress silently.

## 11. Spec/docs status

- `docs/spec-v0.md` §8 — a new subsection beside §8.1.1 (Double bit access), listing the
  seven names and marked **experimental**.
- `runtime/APPROVED_BUILTINS` — **no entry**, by design; noted there so the absence reads
  as deliberate rather than as an omission, exactly as `double_to_bits` is.
- `BACKLOG.md` §528 — the bitwise-on-`Int` half points here; the sized-int and
  packed-record halves stay open.
