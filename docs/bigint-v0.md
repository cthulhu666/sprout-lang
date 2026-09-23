# Arbitrary-precision integers (v0)

Status: **design, approved in shape 2026-09-22, unimplemented.** Normative once Stage 1 lands;
until then `docs/spec-v0.md` §6.5 and §8.4 still describe the old behaviour.

Companion decision: `docs/int-overflow-policy-decision.md` (settled as Option A by this design).
Driving requirement: GitHub issue #337, ECDSA P-256 (ES256) signature verification.

## 1. Problem statement

Sprout's `Int` is *specified* as a mathematical (arbitrary-precision) integer. Until this design,
`docs/spec-v0.md` §6.5 and §8.4 both called the 64-bit lowering "a temporary v0 implementation
constraint, not the intended long-term meaning of `Int`" — wording this document retires. No
implementation has ever realised that promise, and nothing was scheduled to.

Issue #337 turned the promise into a blocker. Verifying an ECDSA P-256 signature — the one
primitive standing between the language and passkey authentication — is modular arithmetic on
256-bit integers. The issue concluded that Sprout "cannot express the numbers involved at all"
and asked for either a runtime builtin or arbitrary-precision integers.

Two things had to be established before that could be answered.

**The blocking claim is false.** A carry flag is only needed for *saturated* limbs. At a reduced
radix nothing carries out: with ten limbs of radix 2^28, a limb product is below 2^56 and a
ten-term column sum below 2^59.3, both inside a signed i64. Measured, not assumed — a 10x10
schoolbook multiply plus carry pass written in Sprout today, `MutVec`-backed, `-O2`, Apple
Silicon:

```
reps=100000  total_us=116323  ns_per_mul=1163
```

**1.17 µs** per unreduced field multiply. At roughly 6,000 field multiplications per verify and
about twice the probe's cost once reduction is included, a pure-Sprout P-256 verify lands near
**15 ms**, with `MutVec` being the slow representation. So arbitrary precision is not a
prerequisite for #337; it is a choice made on its merits, and this document records why.

**The promise collides with the GC.** `stdlib/compiler/type_kind.sprout:48`
(`type_is_non_heap_scalar`) classifies `Int`, `Bool`, `Char` and `Double` as scalars, which is
what keeps them off the GC root stack. `docs/compiler-internals.md` records the value of that
exclusion: 67% of N-queens CPU was in GC root calls, and type-aware rooting gave a measured
**1.5–2.7x** speedup. An `Int` that may be a pointer to a heap bignum cannot stay on that list.

## 2. Goals and non-goals

**Goals.**

- Arbitrary-precision integer arithmetic available to Sprout programs, in Sprout.
- Enough of it to write ECDSA P-256 verification as ordinary stdlib code, with no new builtin.
- Settle `docs/int-overflow-policy-decision.md`, open since 2026-07-06.
- Leave `Int`'s current performance intact.
- Keep the door to a future arbitrary-precision `Int` open rather than nailed shut.

**Non-goals.**

- Making `Int` itself arbitrary-precision (§4 explains the decision; §4.4 what would reopen it).
- Operator support for `BigInt` (`+`, `*`). Deferred to milestone N1 of
  `docs/numeric-types-v1-draft.md`, which introduces the numeric classes. Until then `BigInt`
  arithmetic is named functions.
- Performance parity with a C bignum. GMP-class speed is not a requirement of #337 and is not
  pursued.
- `Decimal`, `Float`, `Complex`, or any other numeric type. Unrelated milestones.
- Signing, key generation, ECDH, curves other than P-256, or constant-time guarantees. #337
  scopes all of these out; verification touches only public inputs.

## 3. Prior-art survey

Every row verified against a primary source on 2026-09-22.

| Language | Default integer | Arbitrary precision via | Source |
|---|---|---|---|
| **Haskell** | `Int` — "fixed-precision", "at least the range [−2^29, 2^29−1]" | `Integer` — "arbitrary-precision integers" | Haskell 2010 Report §6.4 |
| **OCaml** | `int` — **63-bit** on 64-bit platforms; one bit is the GC tag | Zarith, external library | OCaml manual, *Values* |
| **Zig** | fixed-width | `std.math.big.int` — `Managed`/`Mutable`/`Const`, sign-magnitude limbs | `lib/std/math/big/int.zig` |
| **Go** | `int`, fixed | `math/big`, stdlib | — |
| **Java** | `long`, fixed | `java.math.BigInteger`, stdlib | — |
| **Python** | `int`, unbounded | n/a — "Integers have unlimited precision"; every int is heap | `docs.python.org`, *stdtypes* |

**The pattern.** Unbounded-by-default clusters with dynamic typing and a boxed or tagged value
representation. Every statically typed language with an unboxed uniform integer keeps a machine
integer and ships arbitrary precision as a *type*.

**OCaml is the load-bearing row.** It pays the tag bit — its `int` is 63 bits, not 64 — and
*still* does not make `int` unbounded. Tagging buys the ability to box on overflow; it does not
pay for it. A language that has already accepted the tag is the one best placed to go further,
and it declined.

A second survey, of how comparable languages implement P-256 itself, is in §9.4: it bears on the
implementation, not on this decision.

## 4. Decision, and why the alternative lost

**Decided: `Int` is normatively 64-bit and traps on overflow; `BigInt` is the arbitrary-precision
type.**

The two candidates were:

- **C1** — `Int` becomes arbitrary-precision: a 63-bit fixnum with a low-bit tag, overflowing into
  a heap bignum. Faithful to the spec's stated intent.
- **C2** — `Int` stays 64-bit; a `BigInt` type provides arbitrary precision. Already filed in the
  repo as milestone **N6** of `docs/numeric-types-v1-draft.md`, "demand-driven (crypto/finance)".

### 4.1 The argument that decides it

**C1 is a strict superset of C2's work.** Under C1 the multi-limb bignum library still has to be
written — it is what the fixnum slow path calls — and the value ABI is re-plumbed on top. The
only thing C1 buys over C2 is that overflow *succeeds* instead of panicking. Under the overflow
policy settled alongside this design, a panic is a safe, located, debuggable outcome that no
working program depends on.

### 4.2 What C1 would cost, ranked

Rooting is the cost that gets named first. It is not the worst one.

1. **The C runtime boxes raw `Int`s throughout.** `runtime/sprout_runtime.c:9010`,
   `bytes_get`, is representative: `sprout_make1(find_ctor_tag_by_name("Just"), (long
   long)value->data[index])` — a raw C integer stored straight into a heap object. There are
   **170** exported `long long` functions and **73** stdlib `extern fn` signatures mentioning
   `Int`. The compiler can insert untagging at extern call sites, because it knows the
   signature; it cannot fix the runtime's *own* stores into heap objects. Every such site is a
   hand audit, and a missed one does not crash: under a conservative collector a raw even
   integer is silently mistaken for a pointer and retained, or dereferenced by `sprout_field`.
2. **Arithmetic becomes a GC safe point.** `stdlib/compiler/ir_rooting.sprout:161-164` and
   `:801-804` — two exhaustive matches — classify `IRIAdd`/`IRISub`/`IRIMul`/`IRIDiv` as
   neither triggering nor exposing, with the comment *"operands are unboxed i64 and nothing
   allocates, so there is no window in which a collection could see them."* Under C1 an
   overflowing `+` allocates. Either every `+` becomes a safe point, rooting all live heap
   values across it, or every arithmetic op splits into fast and slow blocks with only the slow
   one a safe point.
3. **The bootstrap catch-22 becomes an ABI catch-22.** `just bootstrap-from-seed` links the
   committed seed against the runtime. An old-ABI seed against a tagged runtime produces a
   *corrupt binary*, not a parse failure — a failure mode the existing 2-step protocol
   (`docs/debugging.md`) was not designed for. Seed, runtime and compiler must flip atomically.
4. **Golden IR stops being a gate for this change.** All 65 snapshots are rewritten. `docs/gates.md`
   names regenerating an unread diff as the one way that gate is defeated, and a total rewrite
   makes reading it impossible in the sense the gate intends.
5. **`IRTScalar` conflates `Int` with `Bool`, `Char`, `Double` and constructor tags.** C1 needs a
   distinct kind threaded through every assignment and match site, because a ctor-tag compare
   must not get a bignum check and a `Double` must not get a tag.
6. **The rooting regression proper** — the 1.5–2.7x from §1, on `Int`-dense code.
7. **`bit_shl` and `bit_shr_zf` must be respecified.** `docs/bitwise-int-ops-v0.md` §5.1 already
   says "Re-open this if the bignum migration is ever scheduled", and argues `bit_shr_zf` has no
   arbitrary-precision meaning at all — zero-fill presumes a top bit.

Items 1 and 3 have no incremental path. They are the one-way door.

An instructive counter-measurement: the compiler is **not** an arithmetic-heavy `Int` consumer.
In `bootstrap/compile_driver.ll` across 3,825 functions there are 13,972 `add i64`, of which
13,283 are `IRConst` materialisation (`add i64 0, n`) — about 689 real additions, 138
subtractions and 12 multiplications, against **47,631** `sprout_gc_push_i64_root` calls. C1's
cost to the compiler would be `Int`-typed *locals* becoming roots, not tag checks on arithmetic.

### 4.3 The strongest case for C1, and why it loses

Stated at its best, because it is a real case:

- **Spec fidelity.** §8.4 promises a mathematical `Int`, and C2 walks that back. `factorial(25)`
  stays an error instead of simply working. This is a genuine loss, not a deferral.
- **Two types to teach.** Sprout has no operator overloading for `+` (`infer.sprout` hardwires
  the arithmetic operators), so `BigInt` code reads `mul(add(a, b), c)`. P-256 over that is
  verbose.
- **The tagging cost is smaller than it first appears.** With an odd-tag fixnum, `+` on two
  fixnums is an add, a subtract and one overflow branch; comparisons need no untagging. Heap
  payloads sit at `slot+8` in 16-byte slots, hence always even, so fixnums tagged 1 need no
  pointer adjustment.

It loses on §4.1. The third bullet argues C1 is affordable, not that it is *cheaper* — and
affordable-but-a-superset still loses to the subset that delivers the same capability.

### 4.4 What would reopen C1

The door stays open by design, and trap-on-overflow is what holds it open: a program that panics
today can only become a program that *succeeds* under a future wider `Int`. Under silent wrap,
the same widening changes results — a real breaking change. Reopen C1 if `BigInt`'s ergonomics
prove intolerable after N1 lands operators, or if a measured workload is dominated by the
fixnum/bignum boundary. The `BigInt` library written here becomes C1's slow path rather than
wasted work.

### 4.5 Rejected third shapes

- **Static "provably small `Int`" analysis.** Needs whole-program range analysis or refinement
  types Sprout lacks, and gives P-256 nothing — every value there is 256-bit. It also makes
  performance a property of what the analysis can prove, which is the wrong thing to make
  load-bearing.
- **A fixed-width `U256` or `Nat` instead of a general `BigInt`.** Narrower than what was asked
  for, and P-256 needs two different moduli (the field prime `p` and the group order `n`), so a
  fixed-modulus type ends up parameterised anyway. Viable later as a *specialisation* inside
  `stdlib.crypto.p256`, not as the primary type.
- **`BigInt` implemented in C.** This is roughly ten new builtins (add, sub, mul, divmod, cmp,
  shifts, conversions) under a different name. AGENTS.md "Builtin vs Stdlib" rule 6 bars it
  without a measured bottleneck, and §1's measurement says there is none. Revisit only if a real
  service is shown to be CPU-bound on verification.
- **A P-256-specific field implementation with no `BigInt` at all.** Cheapest route to #337 and
  explicitly rejected by the owner in favour of the general capability. Retained as the fallback
  in §9.4 if `BigInt`-based verification proves too slow.

## 5. Syntax and semantics

### 5.1 `Int` becomes normatively 64-bit, and traps

`Int` is a 64-bit two's-complement integer. `+`, `-`, `*` and unary negation **panic** on
overflow with a located message; `/` already panics on a zero divisor and on `INT_MIN / -1`.
`bit_shl` and `bit_shr_zf` keep their current definitions and stay exempt — `bits.sprout:63-67`
makes the left shift's discarding "specified behaviour, not an overflow condition", deliberately
independent of what `*` does.

The rationale for trapping rather than wrapping belongs to
`docs/int-overflow-policy-decision.md`, which this design settles as Option A. The part that
belongs here is the connection: Option A was deferred in 2026-07 partly because a trap had no
escape hatch to point a user at. `BigInt` is that escape hatch, which is why the two land
together.

### 5.2 The wrap audit — code that relies on wraparound today

Trapping turns silent wraparound into a loud panic, so every site that *depends* on wrapping is
a behavioural regression. One confirmed casualty, in the prelude:

```sprout
# Detected without an INT_MIN literal (which the lexer cannot yet represent):
# negating INT_MIN wraps back to a negative value.  # O(1)
fn int_is_min(a: Int) -> Bool =
  a < 0 && (0 - a) < 0
```

`0 - INT_MIN` overflows. Under Option A `safe_div(INT_MIN, -1)` would panic — and `safe_div`
exists precisely so that case does *not* panic. The fix is available and already sanctioned:
`bit_shl(1, 63)` **is** `INT_MIN` (`docs/spec-v0.md` §on bitwise ops states this explicitly) and
the shift is exempt from the overflow policy, so the literal the lexer cannot write becomes
expressible exactly where it is needed.

Two further sites to check rather than assume, neither yet confirmed either way:

- `stdlib/math.sprout:605`, `hi * 4294967296 + round_to_int_pos(lo)` — a large-magnitude
  `Double` to `Int` conversion turns from a garbage result into a panic. Probably an improvement;
  needs a decision and a test either way.
- The `IntRange` walkers in `stdlib/prelude.sprout`, whose comments state they avoid computing
  `current + step` at the extremes *because* that addition wraps. The guard already exists; it
  needs confirming that it fires before the arithmetic, not after.

Cleared: `stdlib/rng.sprout` is safe. Its header (lines 11-12) states the LCG was chosen so that
`A * (M - 1) < 2^61 < 2^63`, "so the multiply never overflows an i64 — the stream is identical
regardless of a target's integer-overflow semantics." `rng_hash2` carries a documented
precondition on coordinate magnitude, which under Option A becomes enforced rather than assumed.

Detection for everything not listed here is the full test suite plus `just compile-examples-stage1`
and `just run-example-canary`: an overflow that previously produced a wrong number now aborts.

### 5.3 Over-range literals (X4), and the radix carve-out

`docs/int-overflow-policy-decision.md` §7 parks W9/X4 — rejecting over-range integer literals at
compile time — behind the runtime decision. It lands with Stage 1.

The rule must be stated for radix literals **explicitly**, not derived from a general "must fit in
`Int`" test. `docs/bitwise-int-ops-v0.md` §5.6 and `docs/spec-v0.md` (lexical section, lines
1842-1847) both record the current behaviour and why it is wanted: `0xFFFFFFFFFFFFFFFF` is `-1`,
which is the only reading under which every 64-bit pattern is writable as a mask. A naive
fits-in-`Int` rule rejects the all-ones mask idiom that both documents endorse.

**Rule:** a *hex or binary* literal denotes a 64-bit pattern and is read as signed two's
complement; any 64-bit pattern is writable, `0xFFFFFFFFFFFFFFFF` is `-1`, and nothing is
rejected. A *decimal* literal denotes a mathematical value and is **rejected at compile time**
if it does not fit in `Int`, with the error naming `BigInt.from_string` as the alternative.
Pinned today by `tests/stdlib/test_int_literals.spr`, which must be updated in the same change.

### 5.4 `BigInt` representation

Immutable, sign-magnitude, little-endian limbs:

```sprout
# stdlib/math/bigint.sprout
export type BigInt = BigInt Bool (Vec Int)   # sign (true = negative), limbs little-endian
```

- **Radix 2^26**, and the choice is governed by the *accumulator*, not by the limb. A limb
  product is below 2^52, and a schoolbook multiply of two n-limb values has a middle column of
  **n** products, so the column sum is below `n * 2^52`. A signed i64 holds that while
  `n <= 2047` — roughly 53,000 bits, far above anything v0 serves. Inside that bound no
  intermediate carry is needed at all, which is what makes the inner loop a plain
  multiply-accumulate on a language with no carry flag.

  This is the one invariant a later optimisation can silently break, so it is stated in the
  module header, `mul` rejects operands above the limb ceiling rather than overflowing, and a
  test pins the boundary. Note the bound is a property of `n`, not of the radix alone: the
  tempting larger radix 2^30 fails at **nine** limbs, which is exactly P-256 width.

  P-256 values occupy 10 limbs at this radix (⌈256/26⌉), the same count radix 2^28 would give,
  so the extra headroom costs nothing at the size that motivated the work.
- **Normalised.** No leading zero limbs; zero is the empty limb vector with a positive sign. This
  makes structural equality value equality, which is what lets `Eq` be derived rather than
  hand-written — and hand-written is where a denormalised-zero bug would live.
- **Opaque.** The constructor is not exported; callers go through `from_int` / `from_bytes_be` /
  `from_string`. This is `docs/guidelines.md` #3, make illegal states unrepresentable: a caller
  cannot build a denormalised or negative-zero `BigInt`.

Arithmetic stays inside the trapping `Int` by construction — every intermediate is bounded by the
radix invariant — so `BigInt` is not an exception to §5.1 and needs no wrapping primitives.

### 5.5 API surface (v0)

```sprout
from_int, to_int : BigInt -> Maybe Int          # Nothing when out of i64 range
from_bytes_be, to_bytes_be : Int -> BigInt -> Bytes   # fixed width, for crypto
from_string : String -> Result ParseError BigInt      # decimal and 0x-prefixed hex
to_string, to_hex
add, sub, mul, negate, abs
divmod : BigInt -> BigInt -> Maybe (BigInt, BigInt)   # Nothing on a zero divisor
cmp, is_zero, is_negative, bit_length, test_bit, shl, shr
```

Instances: `Eq`, `Ord`, `ToString`. No `Numeric` instance until N1 lands the classes.

`divmod` returns `Maybe` rather than panicking, matching `stdlib.math.int`'s Rule 1
(`docs/math-partiality-v0.md`): an `Int` out-of-domain argument answers `Maybe`. The tuple is
returned as a pair because callers that need one almost always need the other, and computing them
separately doubles the work.

### 5.6 The modular layer

```sprout
# stdlib/math/modular.sprout
mod_add, mod_sub, mod_mul : BigInt -> BigInt -> BigInt -> BigInt   # modulus last
mod_pow : BigInt -> BigInt -> BigInt -> BigInt
mod_inv : BigInt -> BigInt -> Maybe BigInt                          # Nothing when not coprime
```

Modulus-last follows `docs/guidelines.md` #6, data-last argument order, so partial application
against a fixed modulus reads naturally. This layer plus `from_bytes_be`, `cmp` and `test_bit` is
the entire surface P-256 consumes.

### 5.7 Module placement

`stdlib/math/bigint.sprout` and `stdlib/math/modular.sprout`, not top level. `stdlib/math.sprout`
states the convention (lines 3-5): the `stdlib.math` / `stdlib.math.int` split exists "so each
type gets the plain, unprefixed names in its own module (Sprout has no overloading, so one name
cannot serve both)". `BigInt` is a third numeric type wanting the same plain names — `add`,
`mul`, `pow`, `abs`, `to_string` — so it belongs inside that convention:

```sprout
import stdlib.math.bigint as bigint
bigint.from_int(42)
```

This also matches how `docs/numeric-types-v1-draft.md` already writes call sites
(`BigInt.from_int(42)`). `stdlib.bits` and `stdlib.crypto` sit at top level, but those are
capability modules, not numeric-type layers.

`bigint` is the first `stdlib.math.*` submodule to export a *type*, which makes the qualified
import idiom matter more than it does for `math.int`: the module is `stdlib.math.bigint`, the type
is `BigInt`.

## 6. Type-system impact

None. `BigInt` is an ordinary exported ADT with derived instances; no new kinds, no class
machinery, no inference changes. `Int` keeps its type, its literals and its operators.

This is the whole point of C2 over C1: the change is additive at the type level. The C1 blast
radius in §4.2 is almost entirely below the type system, in the value ABI and the collector.

## 7. Error-message impact

Three new classes of diagnostic, all from Stage 1:

- **Runtime overflow panic.** `Int overflow in +` with a source location, reusing W7's `IRPanic`
  path so the message shape matches the existing division-by-zero panic. A beginner writing
  `factorial(50)` gets a located error naming the operator, not a negative number.
- **Over-range decimal literal**, at compile time, naming `BigInt.from_string` as the alternative
  (§5.3). The error must not fire on hex or binary literals.
- **`divmod` by zero** answers `Nothing`; no diagnostic. The `Maybe` is the message.

The overflow panic's wording should say which operator overflowed. `+` and `*` in the same
expression are otherwise indistinguishable in the report, and that is the common case in the
arithmetic a beginner writes.

## 8. Compatibility and migration

**Source compatibility.** Additive except for §5.2 and §5.3. A program that overflows silently
today panics after Stage 1; a program with an over-range decimal literal stops compiling. Both
are intended, and both are loud.

**Forward compatibility.** Trapping is what preserves the option of a future arbitrary-precision
`Int` (§4.4). This was `docs/int-overflow-policy-decision.md` §5.2's argument and it survives
unchanged: trap-now protects the migration path, wrap-now sabotages it.

**Seed and golden IR.** Stage 1 changes emitted IR for every `+`, `-`, `*` and negation, so
`just refresh-seed` is mandatory and every affected golden snapshot moves. The diff is large and
mechanical. It will be verified by *shape* — per-file op-count deltas, and spot-reading the
emitted guard CFG for a representative function — rather than by a claim to have read it whole;
`docs/gates.md` is explicit that regenerating an unread diff is how this gate gets defeated, and
an honest account of how it was checked is the minimum that replaces reading it.

Stages 2–4 add files and touch no existing IR, so their golden-IR exposure is only the new
snapshots (Definition of Done #12 covers files *added* to `examples/` or `tests/smoke_shapes/`).

## 9. Implementation

Four stages, four PRs. Each is independently landable and independently revertible.

### Stage 1 — `Int` traps on overflow

- `ast_to_ir.sprout`: guard CFG for `+`/`-`/`*`/negate, following W7's division-by-zero pattern.
  Built in `ast_to_ir`, never in the `ir_lowering` text layer — block-splitting there breaks phi
  predecessors (`docs/compiler-internals.md`).
- `ir_lowering.sprout`: `llvm.sadd.with.overflow.i64` and siblings, `extractvalue`, branch on the
  overflow bit to an `IRPanic` block. This also closes the deferred `INT_MIN / -1` gap for free,
  with no need to materialise an `INT_MIN` literal the lexer cannot represent.
- `ir_rooting.sprout`: the new ops added to all four exhaustive classifications, as
  **non-triggering**. The panic block has no continuation, so nothing needs rooting across it and
  arithmetic stays off the GC-safe-point list. This is what keeps Stage 1 from costing what §4.2
  item 2 would have cost under C1.
- The §5.2 wrap audit, including the `int_is_min` fix.
- X4, with the §5.3 radix carve-out.
- `docs/spec-v0.md` §6.5 and §8.4 rewritten; `docs/int-overflow-policy-decision.md` marked
  decided; `BACKLOG.md` entries for the overflow policy and for `INT_MIN / -1` deleted as part of
  landing, per Backlog Discipline.

### Stage 2 — `stdlib/math/bigint.sprout`

§5.4 and §5.5. Schoolbook multiplication only; no Karatsuba, no Barrett, no Montgomery — at
P-256 widths (nine limbs) schoolbook wins anyway, and an unmeasured asymptotic improvement is the
wrong thing to carry into a first implementation.

### Stage 3 — `stdlib/math/modular.sprout`

§5.6. `mod_pow` by square-and-multiply; `mod_inv` by the extended Euclidean algorithm. Neither is
constant-time, and the module header must say so plainly next to the name of the one caller for
which that is acceptable.

### Stage 4 — `stdlib/crypto/p256.sprout`

Point arithmetic in Jacobian coordinates, DER signature parsing, public-key point decoding, and
`verify`. The public entry point is total: every malformed input — a point not on the curve, `r`
or `s` zero or out of range, a malformed DER envelope — answers `false` rather than panicking,
because verification is the security boundary (#337 states this as a requirement).

A general `BigInt` pays a reduction cost that a Solinas-specific field implementation avoids;
expect several times §1's 15 ms. If that proves too slow for a real caller, the escalation is a
dedicated field type *inside* `p256.sprout`, which changes no public API.

### 9.4 Prior art for the P-256 implementation itself

Verified 2026-09-22, and relevant only once Stage 4 starts:

- **Java** (JDK ≥ 16) implements P-256 in pure Java; the native C ECC was removed (JDK-8241386).
- **Go** implements it in Go, with field arithmetic generated by fiat-crypto
  (`word_by_word_montgomery --lang Go`, 4x64 Montgomery limbs).
- **Zig** implements it in Zig, `std.crypto.sign.ecdsa`.
- **fiat-crypto** emits C, Bedrock2, Go, Rust, Zig, Java and JSON — no Sprout — and only the
  Bedrock2 backend carries proofs. Its 32-bit P-256 target (8x32 Montgomery limbs) transliterates
  to Sprout mechanically if hand-written field arithmetic proves error-prone: every intermediate
  fits the i64 plus `bit_shr_zf` model, and signedness never enters because both `bit_and` and
  `bit_shr_zf` operate on the raw two's-complement pattern.

Languages that ship their own crypto write P-256 in themselves; languages that bind OpenSSL
already link it. Sprout is in the first group — it hand-rolls SHA-256 in
`runtime/sprout_runtime.c` rather than linking anything, and links no crypto library on Linux.

## 10. Tests

Per AGENTS.md "Code and Testing", the failing tests come first in every stage.

**Stage 1.** Conformance fixtures for overflow panics on `+`, `-`, `*` and negation at the
boundary; `safe_div(INT_MIN, -1)` returning `Err` rather than panicking — the regression test for
§5.2, which must be written *before* the `int_is_min` fix and confirmed to fail; parse-error
fixtures for over-range decimal literals; and an update to `tests/stdlib/test_int_literals.spr`
pinning that hex literals are still accepted and `0xFFFFFFFFFFFFFFFF` is still `-1`.

**Stage 2.** Unit coverage per operation, and round-trips: `from_bytes_be`/`to_bytes_be`,
`from_string`/`to_string`, `from_int`/`to_int` including the `Nothing` boundary. Property-style
checks against `Int` for values inside i64 range, which is the cheapest oracle available.
Normalisation edge cases — zero, negative zero, leading-zero limbs, subtraction to zero — because
those are where a denormalised value would first appear and structural `Eq` would first lie.

**Stage 3.** `mod_inv` against known inverses, including the non-coprime `Nothing` case; `mod_pow`
against known vectors; Fermat cross-check (`mod_pow(a, p-1, p) == 1` for prime `p`).

**Stage 4.** All **484** vectors of Wycheproof's `ecdsa_secp256r1_sha256_test.json`, which covers
exactly the adversarial cases #337 requires: `r=0` and `s=0`, BER-encoded signature envelopes,
and points off the curve. Vectors are vendored, not fetched at test time.

## 11. Spec/docs status

Normative once Stage 1 lands. The spec changes:

- **§6.5** — the sentence "Int addition, subtraction, and multiplication **wrap** on overflow in
  the native backend … This is a temporary v0 implementation constraint, not the intended
  long-term meaning of `Int`" is replaced by the trapping rule.
- **§8.4** — "`Int` is *specified* as a mathematical (arbitrary-precision) integer. No
  implementation realizes that today" is replaced by: `Int` is a 64-bit two's-complement integer
  that traps on overflow, and `BigInt` is the arbitrary-precision type. The "temporary
  implementation constraint" framing goes with it.
- **Lexical section** — the over-range literal rule of §5.3 replaces the current "slated for
  revisit" note.
- **Bitwise section** — the caveat that `bit_shl` and `bit_shr_zf` "would have to be respecified"
  should `Int` become arbitrary-precision can be reduced to a pointer here, since it no longer
  will.

`docs/int-overflow-policy-decision.md` moves from OPEN to decided, recording Option A and the
escape hatch that unblocked it. Its §1 also carries a stale claim — "the interpreter uses host
bignum arithmetic" — which is false: `repl_eval_expr` in `runtime/sprout_runtime.c:5114` aborts
with "not supported in native backend", and the i64 lowering is the only implementation. Corrected
in the same pass.

One stale reference is deliberately left for Stage 1 rather than fixed with this design:
`stdlib/bits.sprout:64` says the overflow decision "is still open on that". Its substantive
claim — that `bit_shl`'s discarding is exempt from whatever `*` does — is unchanged and still
true, only the parenthetical is stale. Editing a comment in a `stdlib/*.sprout` file forces the
`verify-bootstrap-fixed-point` + `seed-fp-ack` path at commit time for no behavioural gain, and
Stage 1 edits that file anyway.

`docs/numeric-types-v1-draft.md` milestone N6 (`BigInt` + `Integer` instance) is half-delivered by
Stage 2: the type lands, the class instance waits for N1. The draft is updated to say so rather
than left to imply the milestone is untouched.
