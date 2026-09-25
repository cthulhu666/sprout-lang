# Arbitrary-precision integers (v0)

Status: **normative. All four stages landed.** `Int` traps on overflow, and
`stdlib/math/bigint.sprout`, `stdlib/math/modular.sprout` and `stdlib/crypto/p256.sprout`
all exist. Issue #337 is answered: ES256 verification is ordinary stdlib code, no new builtin.

Companion decision: `docs/int-overflow-policy-decision.md` (settled as Option A by this design).
Driving requirement: GitHub issue #337, ECDSA P-256 (ES256) signature verification.
Retro, once all four stages had landed: `docs/bigint-arc-retro-2026-09-25.md` — where the rework
went, and why Stage 1 attracted four rounds of it while Stages 2–4 attracted one each.

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

(The 15 ms projection was **wrong, and §9's Stage 4 note records the measurement that replaced
it: 240 ms.** The error is in "about twice the probe's cost once reduction is included" — the
probe never ran a division, and reduction turned out to be 71% of a field multiply, not 50% of
one. The claim this paragraph exists to support survives it: 240 ms is still not a case for a
builtin.)

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
overflow with a located message; `/` already panics on a zero divisor.
`bit_shl` and `bit_shr_zf` keep their current definitions and stay exempt — `bits.sprout:63-67`
makes the left shift's discarding "specified behaviour, not an overflow condition", deliberately
independent of what `*` does.

The rationale for trapping rather than wrapping belongs to
`docs/int-overflow-policy-decision.md`, which this design settles as Option A. The part that
belongs here is the connection: Option A was deferred in 2026-07 partly because a trap had no
escape hatch to point a user at. `BigInt` is that escape hatch, which is why the two land
together.

#### `INT_MIN / -1` is not yet guarded

Only the zero-divisor case is guarded today, in `ast_to_ir.finish_checked_div`. The W7 commit
that added that guard (`29c69b7c`) says so directly, in its own comment: "The `INT_MIN / -1`
overflow (the other undefined case) ... guarding it here too is a follow-up." Measured on arm64:
`INT_MIN / -1` returns `INT_MIN` and exits 0. On x86-64 the same expression traps, because the
`idiv` instruction faults. So today this is an architecture-dependent silent wrong answer, not a
closed case.

**DECIDED: closed in Stage 1. `INT_MIN / -1` panics**, on every target, uniformly.

Prior art, verified 2026-09-22:

| Language | `INT_MIN / -1` | Source |
|---|---|---|
| **Rust** | traps | Reference, *Overflow*: "Using `/` or `%`, where the left-hand argument is the smallest integer of a signed integer type and the right-hand argument is `-1` ... These checks occur even when `-C overflow-checks` is disabled, for legacy reasons." `i64::strict_div`: "This function will always panic on overflow, regardless of whether overflow checks are enabled." |
| **Swift** | traps | No `&/` wrapping-division operator exists; `/` is checked unconditionally. |
| **Zig** | traps | `@divTrunc`'s documented precondition excludes this case. |
| **C#** | traps | ECMA-334 §12.12.3; .NET throws even in an `unchecked` context. |
| **Java** | wraps | JLS §15.17.2: "no exception is thrown in this case" (the result is the operand itself). |
| **Go** | wraps | Go spec: "the quotient `q = x / -1` is equal to `x`." |
| **C / C++** | undefined | No defined result specified. |

Four of six memory-safe languages trap. The two that wrap, Java and Go, have no checked
arithmetic mode at all — they wrap everything, so the div case is not a special exemption for
them the way it would be for Sprout. **No surveyed language traps `+`/`-`/`*` while leaving `/`
unchecked**, which is the position Sprout would otherwise be left in. Closing the gap in Stage 1
keeps `/` consistent with the other four operators.

Sprout has no `%` operator (`stdlib/math.sprout:211`), so the division/remainder divergence every
surveyed language has to rule on separately does not arise here — one operator, one rule.

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
exists precisely so that case does *not* panic.

**Fix: `fn int_is_min(a: Int) -> Bool = a == 0 - 9223372036854775807 - 1`.** The §5.3
unary-minus carve-out now makes `-9223372036854775808` itself a legal literal, but that is
*not* the fix to reach for here: `int_is_min` lives in `stdlib/prelude.sprout`, which declares
its `extern fn`s directly rather than importing `stdlib.bits`, so pulling in a bitwise primitive
(`bit_shl`) — or, equally, depending on a parser change that has not landed yet at the point this
fix is written — would be new prelude surface for a one-line predicate. The
`0 - 9223372036854775807 - 1` idiom is what `stdlib/math.sprout:595` and
`tests/stdlib/test_math_to_int.spr:32` already use, is overflow-free under trapping (`0 - INT_MAX`
stays in range, then `- 1` reaches `INT_MIN` exactly), needs no new extern, and — this is the
decisive property — parses identically whether or not the §5.3 literal carve-out has landed yet in
the parser being used to build it. It is safe regardless of the order Stage 1's pieces land in;
the new literal form is not.

A second confirmed casualty: `accum_digits` (`stdlib/prelude.sprout`), the digit-accumulation
loop under `parse_int`, does an unguarded `acc * 10 + digit`. Today it wraps silently on an
over-range digit run. Under trapping this would PANIC — and `panic` in Sprout is `exit(1)`,
uncatchable. `parse_int`'s callers include `stdlib/http_server.sprout:293`
(`parse_content_length`, reading the attacker-controlled `Content-Length` header) and
`stdlib/json.sprout:116` (`p_number`, parsing JSON numbers — RFC 8259 permits arbitrary-length
digit runs). Left unguarded, this is a remotely triggerable process abort.

**DECIDED: guard it.** `parse_int` returns `Nothing` on an over-range digit run. All 19 call
sites already match on `Maybe`, so nothing failed to compile.

> **That is not the same as "no caller changes", and reading it that way cost a bug.** A
> `Nothing` that was previously unreachable now arrives, and each caller's existing `Nothing`
> branch was written for a *different* situation — a malformed token, not an over-range one.
> `json.sprout`'s `p_number` treated both as "reject the document", so a conformant JSON
> integer wider than `Int` stopped parsing at all; the float branch beside it was fixed in
> the same commit for exactly this reason, and the integer branch was not. Type-checking a
> totality change tells you where it still compiles, not where it now means something else.
> Fixed by falling back to `JsonFloat`, as the `.`/`e` branch already does.

`stdlib/compiler/iface_codec.sprout` had
the identical unguarded `acc * 10 + digit` shape, on compiler-internal input rather than
network input; it is fixed the same way and its accumulator is now `parse_neg_magnitude`.
That one was missed on the first pass and shipped broken — the decoder aborted on the
encoder's own `IntExpr(INT_MIN)`, which §5.3 is what makes writable. Its round-trip test
covered no boundary value, so nothing caught it.

Prior art, verified 2026-09-22 — what a string-to-int parser does on an over-range digit run:

| Language | Over-range parse | Source |
|---|---|---|
| **Rust** | `Err` | `from_str_radix` returns `IntErrorKind::PosOverflow`: "Integer is too large to store in target integer type." |
| **Go** | error + clamped value | `strconv.ParseInt` returns `ErrRange` alongside a clamped result. |
| **Swift** | `nil` | The failable integer initializer returns nil "if the value it denotes in the given radix is not representable". |
| **Zig** | error | `std.fmt.parseInt` returns `error.Overflow`. |
| **OCaml** | `None` | `int_of_string_opt` returns `None` "if the integer represented exceeds the range of representable integers." |
| **Java** | throws | `Integer.parseInt` throws `NumberFormatException`. |
| **C** | saturates + errno | `strtoll` saturates to `LLONG_MAX`/`MIN` and sets `errno = ERANGE`. |
| **Haskell** | wraps | `Data.Int`'s `fromInteger` is modulo 2^n — a leak from a general conversion, not a parser design. |

**No surveyed language aborts the process.** `Nothing` matches the shape every other surveyed
language reaches for (an error value), and matches `parse_int`'s existing `Maybe` signature, so no
caller's type changes.

Two further sites, checked rather than assumed, and both cleared SAFE:

- `stdlib/math.sprout:604`, `magnitude_to_int` (`hi * 4294967296 + round_to_int_pos(lo)`) —
  **SAFE**. Its only caller is `to_int` (`math.sprout:620`), which guards `t >= two63` before
  calling it, so `hi <= 2^31 - 1` and `hi * 2^32 + lo <= 2^63 - 1` exactly. The split at `2^32`
  was designed to keep every intermediate in range; trapping changes nothing here.
- The `IntRange` walkers in `stdlib/prelude.sprout` — **SAFE, guard confirmed to fire first**.
  `range_to_list_go` (`prelude.sprout`) tests `range_past_end` and then `range_at_end`
  before ever evaluating `current + step`; at `current == end_value == INT_MAX` the second test
  returns `Cons(current, Nil)` and the addition is never reached. Pinned by
  `tests/stdlib/test_range_empty.spr`.
  The clearance covers the WALKERS only. `range_count` is not one — it computes
  `(end - start) + 1` with no `range_at_end` ahead of it, and a range wider than `MAX_INT`
  has no representable count, so it panics by design with a message naming itself
  (`tests/overflow_smoke/range_count_span.spr`). Enumerating the functions that looked like
  walkers, rather than every function doing range arithmetic, is what let it through.

**NOT cleared, and this entry was wrong.** `stdlib/rng.sprout`'s header states the LCG was chosen
so that `A * (M - 1) < 2^61 < 2^63`, "so the multiply never overflows an i64 — the stream is
identical regardless of a target's integer-overflow semantics." That covers `rng_next`. It does
not cover `rng_hash2`, which this audit waved through on the strength of a documented precondition
on COORDINATE magnitude — while the overflowing term was `seed + (ix * prime)`, and the seed
carries no precondition at all, being an arbitrary `Int` from the caller. A seed within
`rng_hash_px` of `MAX_INT` therefore aborted. Fixed by reducing the seed before it meets the
coordinate term; `rng_mod(seed) + k` has the same residue as `seed + k`, so no hash value moved.
The lesson is specific: reading the precondition a comment states, rather than the operands the
expression actually has, is how an audit clears a site it never examined.
Separately, the header's stated bound was off by a power of two: `A * (M - 1)` is
2,369,780,942,852,698,515, above `2^61` (2,305,843,009,213,693,952) and below `2^62`
(4,611,686,018,427,387,904). The `rng_next` conclusion was unaffected — `2^62 < 2^63` — and the
comment now reads `2^62`. Worth recording that the audit caught the wrong number in a comment
while missing the overflow in the code three lines below it.

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
complement; any 64-bit pattern is writable and `0xFFFFFFFFFFFFFFFF` is `-1`. A run wider than
64 *significant* bits denotes no pattern and is rejected like the decimal case
(amended after Stage 1 — see `docs/bitwise-int-ops-v0.md` §5.6; leading zeros are not
significant). A *decimal* literal denotes a mathematical value and is **rejected at compile
time** if it does not fit in `Int`, with the error naming `BigInt.from_string` as the
alternative. Pinned by `tests/stdlib/test_int_literals.spr` and
`tests/conformance/parse_error/{hex,binary,int}_literal_overflow.spr`.

#### The INT_MIN carve-out

A decimal fits-in-`Int` check cannot be applied to the bare literal token without also rejecting
`INT_MIN`. Sprout's parser treats a leading `-` as a separate unary operator
(`parse_unary`, `stdlib/compiler/parser.sprout`), not part of the literal token, so
`-9223372036854775808` parses as unary minus applied to the literal `9223372036854775808` —
whose magnitude, `2^63`, does not itself fit in `[0, 2^63-1]`. A naive check rejects it.

**DECIDED: special-case it.** The range check applies to the value *after* a directly-applied
unary minus: the admissible range is `[-2^63, 2^63-1]` for a decimal literal that is the
immediate operand of unary `-` — no parentheses, and the next TOKEN, so intervening
whitespace or a line break is invisible to the rule, as in Rust and Java (verified by
compiling `- 9223372036854775808` under rustc 1.75 and javac 25) — and
`[0, 2^63-1]` everywhere else. `-9223372036854775808` stays valid; `-(9223372036854775808)` does
not, same as the bare positive form, because the carve-out is syntactic, not semantic.
`0x8000000000000000` remains an equally valid spelling of the same value.

Prior art, verified 2026-09-22 — every one of nine languages surveyed accepts
`-9223372036854775808`; they differ only in mechanism:

| Language | Mechanism | Source |
|---|---|---|
| **Rust** | grammar identical to Sprout's — carve the range check, not the grammar | Reference, *Literal expressions*: "`-1i8`, for example, is an application of the negation operator to the literal expression `1i8`, not a single integer literal expression." *Overflow*: "The exception for literal expressions behind unary `-` means that forms such as `-128_i8` ... never cause a panic and have the expected value of -128 ... these most negative expressions are also ignored by the overflowing_literals lint check." |
| **Java** | normative carve-out in the grammar | JLS SE21 §3.10.1: "The decimal literal `9223372036854775808L` may appear only as the operand of the unary minus operator `-`. It is a compile-time error if [it] appears anywhere other than as the operand of the unary minus operator." |
| **C#** | same carve-out; bare positive magnitude retypes as `ulong` instead of erroring | ECMA-334 §6.4.5.3 |
| **Swift, OCaml** | sign is inside the literal production; the question does not arise | — |
| **Go, Zig, Haskell** | literals are arbitrary-precision; the range error happens at point of use | — |
| **C** | the only language where the bare decimal form is genuinely inexpressible | glibc `limits.h`: `LLONG_MIN` is defined as `(-LLONG_MAX - 1LL)`, the workaround idiom |

Rust is the decisive row: its grammar is the same shape as Sprout's, and it reached the identical
fork — rescuing the literal in the range check, not the grammar.

Sprout already uses C's workaround idiom in two places, `stdlib/math.sprout:595` and
`tests/stdlib/test_math_to_int.spr:32`, both `0 - 9223372036854775807 - 1` with an explanatory
comment. That idiom stays valid and overflow-free under the new rules; it needs no change.

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
  module header, `mul` rejects operands above the limb ceiling rather than overflowing, and
  `tests/overflow_smoke/bigint_mul_ceiling.spr` pins the boundary. Note the bound is a property of
  the limb COUNT, not of the radix alone: the tempting larger radix 2^30 fails at **nine** limbs,
  which is exactly P-256 width. As implemented the ceiling is tested against `min(n, m)`, since a
  column holds at most that many products — so a wide-by-narrow multiply is unrestricted.

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

As shipped by Stage 2:

```sprout
from_int      : Int -> BigInt
to_int        : BigInt -> Maybe Int                  # Nothing outside [INT_MIN, INT_MAX]
from_bytes_be : Bytes -> BigInt                      # unsigned big-endian
to_bytes_be   : BigInt -> Int -> Maybe Bytes         # fixed width, left-padded
from_string   : String -> Result ParseError BigInt   # decimal or 0x hex, optional sign
to_string     : BigInt -> String
to_hex        : BigInt -> String                     # "0xff" / "-0xff"
add, sub, mul : BigInt -> BigInt -> BigInt
negate, abs   : BigInt -> BigInt
divmod        : BigInt -> BigInt -> Maybe (BigInt, BigInt)   # Nothing on a zero divisor
cmp           : BigInt -> BigInt -> Int              # -1 / 0 / 1
is_zero, is_negative : BigInt -> Bool
bit_length    : BigInt -> Int
test_bit      : BigInt -> Int -> Bool
shl, shr      : BigInt -> Int -> BigInt
mul_bit_ceiling : Int                                # widest bit_length `mul` never panics on
```

Instances: `Eq` (derived), `Ord` and `ToString` (hand-written). No `Numeric` instance until N1
lands the classes. `Ord` cannot be derived: it would order `BigInt Bool (Vec Int)`
lexicographically on (sign, limbs), which is not numeric order.

Receiver-FIRST, not data-last. `docs/guidelines.md` #6 exempts module-qualified modules, and
`stdlib.math.int` (`clamp(value, lo, hi)`, `pow(base, exp)`) is the module this one sits beside;
matching it beats matching a convention written for unqualified prelude globals.

`divmod` returns `Maybe` rather than panicking, matching `stdlib.math.int`'s Rule 1
(`docs/math-partiality-v0.md`): an `Int` out-of-domain argument answers `Maybe`. The tuple is
returned as a pair because callers that need one almost always need the other, and computing them
separately doubles the work. Division is TRUNCATED, matching `Int`'s `/`: the quotient rounds
toward zero and the remainder takes the dividend's sign, so `q * divisor + r == dividend` holds.

Three points where the implementation settled something this section had only sketched:

- **`to_bytes_be` answers `Maybe Bytes`**, not `Bytes`. A negative value has no unsigned
  big-endian encoding and a value wider than the requested width has no truncation that is not a
  silent lie, so both are `Nothing` — `docs/guidelines.md` #2. Callers already know their width
  (32 for P-256), so the `Maybe` costs one `let..else`. A width **above 6,653 bytes** is
  `Nothing` too: that is the 2047-limb ceiling expressed in bytes, the same bound `mul` refuses
  past, so a wider field is already outside the range the module serves. Without an upper bound
  the function was not total at all — it walked `0..width` for any width a caller passed, and
  answered neither `Just` nor `Nothing` (see §9 Stage 2, review of PR #344).
- **`cmp` answers `Int`**, because Sprout's `Ord` is `fn compare(left, right) -> Int`
  (`stdlib/prelude.sprout`) and the language has no `Ordering` ADT. `cmp` and `compare` are the
  same function.
- **`bit_length`, `test_bit`, `shl` and `shr` describe the MAGNITUDE.** The sign rides along
  untouched, so `shr` truncates toward zero rather than flooring and `test_bit` on a negative
  value asks about `|value|`. This is the honest reading of a sign-magnitude representation and it
  is uniform across all four, but it is NOT what `java.math.BigInteger` or Python's `int` answer
  for negative values — both define those operations on an infinite two's-complement pattern. The
  module header says so at the definition site, because the trap is porting a Java idiom.

### 5.6 The modular layer

```sprout
# stdlib/math/modular.sprout
wrap Modulus = BigInt                             # abstract: positive, and narrow enough to multiply
modulus       : BigInt -> Maybe Modulus           # Nothing when <= 0 or past mul_bit_ceiling()
modulus_value : Modulus -> BigInt
reduce        : BigInt -> Modulus -> BigInt       # Euclidean, always in [0, m)
mod_add, mod_sub, mod_mul : BigInt -> BigInt -> Modulus -> BigInt   # modulus last
mod_pow : BigInt -> BigInt -> Modulus -> Maybe BigInt   # Nothing on a negative exponent
mod_inv : BigInt -> Modulus -> Maybe BigInt             # Nothing when not coprime
```

**The `Modulus` invariant is two conditions, not one**, and the second was missed on the first
attempt — recorded here because the omission is the interesting part. A zero or negative modulus
has no residues. A modulus above `bigint.mul_bit_ceiling()` (~53,222 bits) makes `mod_mul` panic:
it multiplies two reduced operands, both of which inherit the modulus's width, and `bigint.mul`
refuses above its schoolbook ceiling. Validating only the sign moved the partiality from the call
site into the constructor rather than removing it, which defeats the §5.6 argument entirely —
guidelines #2 is the reason the type exists, so a type that carries half the precondition is worse
than an honest `Maybe` on every operation. `mul_bit_ceiling` is exported from `stdlib.math.bigint`
for this: the constant belongs to the module that enforces it, and copying 53,222 into the modular
layer would be a second place to forget. Found by the second `high` ensemble review of PR #345,
reproduced as a process abort, and fixed before merge.

Modulus-last follows `docs/guidelines.md` #6, data-last argument order, so partial application
against a fixed modulus reads naturally. This layer plus `from_bytes_be`, `cmp` and `test_bit` is
the entire surface P-256 consumes.

**The modulus is a type, not a `BigInt`, and that was a decision.** This section originally gave
`mod_add` the total signature `BigInt -> BigInt -> BigInt -> BigInt`, which is not implementable:
a zero or negative modulus has no answer, and `docs/guidelines.md` #2 makes "the stdlib must not
export a partial function" a hard mandate for library code. Three shapes were on the table —

- **`Maybe` on every operation.** Honest, and it needs no new type. It also makes Stage 4's point
  arithmetic unwrap a `Maybe` at every field multiply — dozens of sites that can never fail, each
  needing an `else` arm with nothing sensible to put in it.
- **Panic on a bad modulus.** Keeps the sketch's signatures, and has repo precedent
  (`stdlib.bits` panics on a negative shift count; `range_count` panics). Deviates from #2.
- **An abstract `wrap` with a smart constructor** — chosen. `docs/guidelines.md` #7 documents
  exactly this pattern ("a wrap can hide its constructor, so it can carry an invariant"), and #4
  is the principle: parse at the boundary, once, and let interior code consume a type that cannot
  be wrong. P-256 pays it twice, once per curve constant.

`mod_pow` still answers `Maybe`, for the one failure the `Modulus` type cannot absorb: a negative
exponent is an inverse that may not exist. That matches `stdlib.math.int.pow`, which is `Nothing`
on a negative exponent for the same reason — Rule 1 of `docs/math-partiality-v0.md`.

Residues are **Euclidean** — in `[0, m)` even for a negative input. `bigint.divmod` is truncated,
so its remainder carries the dividend's sign; `reduce` is the single place that is corrected, and
every entry point goes through it.

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

### Stage 1 — `Int` traps on overflow (landed)

- `ast_to_ir.sprout`: guard CFG for `+`/`-`/`*`/negate, following W7's division-by-zero pattern.
  Built in `ast_to_ir`, never in the `ir_lowering` text layer — block-splitting there breaks phi
  predecessors (`docs/compiler-internals.md`).
- `ir_lowering.sprout`: `llvm.sadd.with.overflow.i64` and siblings, `extractvalue`, branch on the
  overflow bit to an `IRPanic` block, for `+`/`-`/`*`/negate.
- `ast_to_ir.finish_checked_div`: extend the existing zero-divisor guard to also check
  `divisor == -1 && dividend == INT_MIN` (§5.1). This is a separate guard from the arithmetic
  `.with.overflow` intrinsics above — `sdiv` has no LLVM overflow-intrinsic form, so this gap is
  not closed "for free" by the add/sub/mul work; it needs its own check, on the same `IRPanic`
  path.
- `ir_rooting.sprout`: the new ops added to all four exhaustive classifications, as
  **non-triggering**. The panic block has no continuation, so nothing needs rooting across it and
  arithmetic stays off the GC-safe-point list. This is what keeps Stage 1 from costing what §4.2
  item 2 would have cost under C1.
- The §5.2 wrap audit: the `int_is_min` fix, guarding `parse_int`/`accum_digits` to return
  `Nothing` on an over-range digit run, and the identical fix to
  `iface_codec.parse_unsigned_atom`.
- `stdlib/rng.sprout:11`'s header comment: correct the stated bound from `A * (M - 1) < 2^61` to
  `< 2^62` (§5.2) — a one-line comment fix, riding along on the reseed Stage 1 already pays for.
  (`rng_hash2` needed more than the comment: the *coordinates* had to be reduced before their
  multiply too, not only the seed. Found by the ensemble review of the whole arc, PR #351.)
- X4, with the §5.3 radix carve-out and the unary-minus `INT_MIN` carve-out.
- `docs/spec-v0.md` §6.5 and §8.4 rewritten; `docs/int-overflow-policy-decision.md` marked
  decided; `BACKLOG.md` entries for the overflow policy and for `INT_MIN / -1` deleted as part of
  landing, per Backlog Discipline.

### Stage 2 — `stdlib/math/bigint.sprout` (landed)

§5.4 and §5.5. Schoolbook multiplication only; no Karatsuba, no Barrett, no Montgomery — at
P-256 widths (nine limbs) schoolbook wins anyway, and an unmeasured asymptotic improvement is the
wrong thing to carry into a first implementation.

Two implementation choices the design did not anticipate, both forced by the same fact — **there
is no pure mutable array.** `MutVec` carries `!{IO}` (`stdlib/mutable.sprout`), and this API is
pure, so every algorithm here had to be written without a mutable accumulator:

- **Multiplication is product scanning (Comba), not operand scanning.** A whole column is summed
  before anything is carried, which is exactly what the radix invariant was chosen to permit.
  Operand scanning wants to accumulate into a mutable result; product scanning wants only
  random READS, which an immutable `Vec` gives.
- **Division is Knuth 4.3.1 Algorithm D in its bring-down form**, so the running remainder never
  exceeds n+1 limbs and bringing a limb down is a prepend on a little-endian magnitude. The
  quotient-digit correction decrements and re-compares instead of doing Knuth's add-back: at these
  widths it is the same work, and it cannot get the add-back's carry cancellation wrong. Note
  `vec_prepend` could NOT be used for the bring-down — it is O(n²), because `vector_append` copies
  its input, so prepending one limb to an n-limb `Vec` costs n copies of a growing vector.

**Measured, Apple Silicon, `-O2`, at P-256 width (10 limbs):**

```
10x10 multiply          1.4 µs
20-by-10 divmod        31.4 µs
modular multiply       ~33 µs   (one multiply + one reduction)
```

Division costs **22x** the multiply, and that ratio is not algorithmic — the two differ by about
2x in limb operations. It is allocation: `vec_get_or` boxes every single limb read into a `Just`
(`runtime/sprout_runtime.c:vector_get`), and each intermediate magnitude is built as a `List`,
reversed, converted and trimmed. A divmod at this width does roughly 1,100 heap allocations.

**Two defects the ensemble review of PR #344 confirmed, both in `to_bytes_be`, both fixed
before merge.** They are one defect seen from two sides. `bytes.builder_append` copies *both*
operands' chunk arrays, so building the result by appending one byte at a time cost O(width²)
pointer copies; and nothing bounded `width` from above, so the walk had no stopping point a
caller could not exceed. Together they took **5.7 s and 6.7 GB of resident memory at width
80,000** — for an 80 kB answer — and a **SIGTERM at width 200,000**, where the call returned
neither `Just` nor `Nothing`. (The review reported 4.74 s and 13.7 GB for the same call; the
figures here are re-measured on this branch against a pre-fix build of the module.) The fix is both halves: the byte range is
built by halving rather than folding, which is O(width log width), and `width` above
`byte_ceiling()` is `Nothing`. Measured after: **776 µs** at the ceiling width of 6,653 bytes,
against **24.7 ms** for the removed left fold at that same width — 32x, and the gap widens
quadratically above it. At P-256 width the call is 3.3 µs and neither shape was ever visible.

The lesson is the one worth carrying into Stage 4: **this module was written and measured at
P-256 width, and nothing examined its cost above that.** The allocation cost recorded above is a
constant factor at ten limbs; it says nothing about asymptotics. Four more findings from the same
review are unverified low-severity growth claims of exactly this shape, filed in `BACKLOG.md`.

The consequence for Stage 4 is concrete: at ~6,000 field multiplications per verify, a
`BigInt`-based P-256 verification lands near **200 ms**, not the "several times 15 ms" §9's Stage 4
note projected. That does not change the plan — the escalation path there is unchanged and still
changes no public API — but it means Stage 4 should measure before assuming. It did: **240 ms**,
so this projection was the accurate one. The cheaper fix is
upstream of this module and is filed in `BACKLOG.md`: an unboxed read for immutable `Vec`, which
the runtime already has as `vector_get_direct` but which only `stdlib.mutable` declares, and only
as `!{IO}`.

### Stage 3 — `stdlib/math/modular.sprout` (landed)

§5.6. `mod_pow` by square-and-multiply, least-significant bit first; `mod_inv` by the extended
Euclidean algorithm, carrying only the one Bézout coefficient it needs rather than both. Neither
is constant-time, and the module header says so plainly next to the name of the one caller for
which that is acceptable — and next to the three for which it is not (signing, key generation,
ECDH, where every input is secret).

`mod_add` and `mod_sub` finish with a conditional add or subtract rather than a division: once
reduced, both operands are in `[0, m)`, so the sum is below `2m` and the difference above `-m`.

That is a claim about the *tail*, not about the call. Both reduce their arguments first, and a
`reduce` is a full Knuth division unless the argument is already in range, where it hits
`divmod`'s `cmp_mag` fast path instead. So `mod_add` on two unreduced arguments pays **two**
divisions where `reduce(bigint.add(left, right), m)` would pay one — the shape is a win only for
callers that keep their values reduced, which is Stage 4 and is not enforced by the signature.
`tests/stdlib/test_modular_vectors.spr` deliberately passes unreduced values, so the suite
exercises the slower path.

### Stage 4 — `stdlib/crypto/p256.sprout` (landed)

Point arithmetic in Jacobian coordinates, DER signature parsing, public-key point decoding, and
`verify`. The public entry point is total: every malformed input — a point not on the curve, `r`
or `s` zero or out of range, a malformed DER envelope — answers `false` rather than panicking,
because verification is the security boundary (#337 states this as a requirement).

The surface is one type and three functions, and nothing else is exported. `public_key` (SEC1
`0x04 || X || Y`) and `public_key_xy` (the COSE/JWK shape WebAuthn hands over) are the only ways
to build a `PublicKey`, and both validate the curve equation — the `Modulus` pattern of §5.6, one
boundary check rather than a `Maybe` on every use. `verify` answers `Bool`. Argument order is
data-first, context-last, matching `modular.mod_add(left, right, m)` rather than C's
`(key, message, sig)`. DER decoding and a SEC1 re-encoder were both written and then withdrawn
from the export list: #337 needs neither, and an exported function no test exercises is a
promise nothing holds you to.

**What the adversarial surface actually is.** Of Wycheproof's 310 invalid vectors, **173 are
encoding, not arithmetic** — `InvalidEncoding` 92, `InvalidTypesInSignature` 63,
`BerEncodedSignature` 7, `IntegerOverflow` 5, `ModifiedInteger` 5, `MissingZero` 1. Range checks
on `r`/`s` are 112 more, and only 31 are curve or point cases. That inverts the expected shape of
the work: strict DER is the bulk of the security value, and it is reached before a single field
multiply. Long-form lengths are rejected outright — a P-256 body is at most 72 bytes, so DER's
minimal-length rule requires the short form, and a long form is either non-minimal or too wide to
hold an in-range `r` and `s`.

**Cost: the first version was 252 ms, the field was replaced, and it is now 5.4 ms.** Both
numbers matter, because the first one is what the design predicted and the second is what it took
to beat it.

The `BigInt`-backed version measured 252 ms per verify and 70 s for the 484-vector suite. §9's
Stage 2 note predicted ~200 ms from the field-multiply cost alone, so that prediction held; §1's
"several times 15 ms" did not, and is retired. The breakdown at P-256 width:

| operation | per call | note |
|---|---|---|
| `modular.mod_mul` | 46.5 µs | one field multiply |
| ├ `bigint.mul` (256×256) | ~2 µs | 4% |
| └ `modular.reduce` (512-bit) | ~33 µs | **71%** |
| `modular.mod_add` | 1.3 µs | |
| `bigint.add` | 1.2 µs | |

**The Knuth division was 71% of a field multiply — and replacing only that would have been worth
3.5x, not 40x**, because `bigint.add` itself costs 1.2 µs allocating a fresh limb vector. The
floor for a `BigInt`-backed field is allocation, not algorithm. Measuring the *representation*
rather than the formula is what found the real number:

| ten-limb operation | per call | |
|---|---|---|
| constructor with ten `Int` fields: read + rebuild | **19.7 ns** | |
| `Vec Int` via `vec_get_or` | **250 ns** | **12.7x slower** |
| multiply-accumulate column on constructor fields | 1.5 ns per limb product | |

At 1.5 ns a limb product, a 10x10 schoolbook is ~150 ns against `bigint.mul`'s 2 µs for identical
arithmetic — and against the **1.17 µs** `MutVec` probe in §1 that the whole feasibility argument
was costed on. **§1 measured the third-fastest of three representations.** The 12.7x is the `Just`
boxing of `BACKLOG`'s `vec_get` entry made visible: a ten-limb operation does ten heap allocations
before it does any arithmetic, while a constructor of scalar fields does none.

So the field is `Felem`: **ten 26-bit limbs in a constructor, with CIOS Montgomery
multiplication**. The radix matches `bigint`'s, so limb products stay at 2^52 and a ten-term
column at 2^55.3, well inside i64 — measured worst case across 30,000 random pairs is 2^52
against i64's 2^63. `n0inv` is **1**, because p ≡ −1 mod 2^26, which deletes a multiply from every
inner step. Montgomery was chosen over Solinas despite Solinas being slightly cheaper: P-256's
prime aligns to 32-bit words, not 26-bit limbs, so Solinas needs a radix conversion plus signed
correction, and both beat the target by more than 10x. Inversion is Fermat (`a^(p-2)`), which
reuses `mont_mul` rather than needing a second algorithm on a second representation.

Result, same machine, same vectors:

| | `BigInt` field | `Felem` field | |
|---|---|---|---|
| one verify | 252 ms | **5.40 ms** | **46.7x** |
| 484-vector suite | 69.5 s | **1.37 s** | 51x |
| vs pure Python, same algorithm (2.66 ms) | 95x slower | **2.0x slower** | |

**The next bottleneck is no longer the field.** `modular.mod_inv` mod *n* — the one scalar
inversion a verify still does on `BigInt` — is **1.11 ms**, now 21% of the total. A second
Montgomery field for `n` would remove most of it. Shamir's trick on the two scalar
multiplications is a further ~⅓, independent of that. Neither is done.

The public API did not change, exactly as this section originally predicted the escalation would
not. `BigInt` survives at the boundary, where keys and signatures are parsed; scalars stay
`BigInt` because they are not field elements.

**Constants are top-level `let`, not nullary `fn`.** A `let` is evaluated once for the process; a
nullary `fn` re-runs its body at every call. For a body LLVM can fold this is free — which is why
`bigint`'s `fn limb_bits() -> Int = 26` is fine in an inner loop — but `from_string` on 64 hex
digits is quadratic and folds into nothing: **0.03 µs against 52 µs per reference**. Written as
functions, the curve constants were re-parsing the group order twice on every `verify`.

### 9.4 Prior art for the P-256 implementation itself

Verified 2026-09-22:

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
boundary; `INT_MIN / -1` panicking (§5.1) and `safe_div(INT_MIN, -1)` returning `Err` rather than
panicking — the regression test for §5.2, which must be written *before* the `int_is_min` fix and
confirmed to fail; `parse_int` and `parse_unsigned_atom` returning `Nothing` on an over-range digit
run (§5.2); parse-error fixtures for over-range decimal literals, including a passing fixture for
`-9223372036854775808` (the unary-minus carve-out, §5.3) and a rejected-at-compile-time fixture for
the parenthesised form `-(9223372036854775808)` (the carve-out is syntactic, not semantic — it
does not reach through a paren); an update to `tests/stdlib/test_int_literals.spr` pinning that hex
literals are still accepted and `0xFFFFFFFFFFFFFFFF` is still `-1`; and conformance coverage for
the new public-API panics named in spec §8.4: `stdlib.math.int.abs(-9223372036854775808)`
(`INT_MIN`, now a legal literal under §5.3's carve-out) and an overflowing `pow` (e.g.
`pow(2, 100)`) both panicking, per `docs/math-partiality-v0.md`'s division of labour between
Rule 1 (`Maybe` for documented domain errors) and overflow (panics).

**Stage 2.** Two suites, and they answer different questions.

`tests/stdlib/test_bigint.spr` — 89 hand-written cases pinning the named edges: both i64
boundaries through `from_int`/`to_int` (INT_MIN is the one `from_int` cannot reach by negating),
the four sign combinations of truncated `divmod`, normalisation (zero, negated zero, subtraction
to zero, structural equality across construction paths), and the rendering edge a chunked decimal
writer gets wrong — an interior run of zeros, which survives only if every chunk but the most
significant is left-padded.

`tests/stdlib/test_bigint_vectors.spr` — 330 generated checks against Python's `int` as the
oracle, vendored (not generated at test time: `just test` must not need a Python interpreter) and
regenerable via `scripts/gen_bigint_vectors.py`. Widths cluster on the boundaries where a
limb-based bignum actually breaks rather than being uniform — either side of 26 bits and its
multiples, top limbs at base/2 (the normalisation threshold Knuth's estimate is stated against),
and all-ones / all-zeros limb runs, where carry and borrow chains run longest.

Both were needed. The hand-written suite passed on its first run against a fresh Algorithm D,
which is evidence about the tests, not about the code: Knuth D's failure modes sit in the
quotient-digit correction, and hand-picked cases do not reach it. The vendored vectors are a
25-seed, 8,240-check sweep narrowed to one reproducible seed.

Seven of the 89 are the regression tests for the `to_bytes_be` defects above, and they are worth
naming because a cost bug does not usually have a test. Two are pure assertions — width 6,653 is
accepted, width 6,654 is `Nothing` — and they fail cleanly on the unfixed code rather than
hanging. One asks for width 200,000 and requires an *answer*, which on the unfixed code took the
whole suite down with a SIGTERM before it printed a line. The other four pin big-endian byte order
at an ODD width, where the halving split is uneven. Three of them read the bytes out by index
rather than round-tripping, so a mis-split is located rather than merely detected.

**Stage 3.** `tests/stdlib/test_modular.spr` — 38 cases, run against the two moduli Stage 4 will
actually use (the P-256 field prime and group order) rather than toy values: `mod_inv` against
known inverses including the non-coprime, zero and multiple-of-the-modulus `Nothing` cases;
`mod_pow` against known vectors; the Fermat cross-check (`mod_pow(a, p-1, p) == 1` for prime `p`),
which is worth more than the vector beside it because it does not depend on a transcribed answer
being right; and the same for `mod_inv`, checked as `a * a⁻¹ == 1` rather than by its digits.

`tests/stdlib/test_modular_vectors.spr` — 120 generated checks, `scripts/gen_modular_vectors.py`,
same vendoring rule as Stage 2. The moduli there are arbitrary positives, NOT primes, deliberately:
a suite of prime moduli never reaches `mod_inv`'s non-coprime arm.

**Stage 4.** Two suites again, same split as Stage 2.

`tests/stdlib/test_p256.spr` — 31 hand-written cases, each one quoted from the Wycheproof suite
so the two files cannot disagree silently. They are chosen for what they prove rather than for
coverage, and three are worth naming because they are the ones an implementation gets confidently
wrong: a signature with **r=5, s=1 is valid** (tc355), so no size heuristic on the components is
safe; the **high-s malleable form is valid** (tc5), so a verifier that rejects it breaks
interoperability while fixing no attack; and tc7 against tc6 is the same 69-byte signature
differing only in whether `s` carries its DER leading zero — valid and invalid respectively.

`tests/stdlib/test_p256_vectors.spr` — all **484** vectors of Wycheproof's
`ecdsa_secp256r1_sha256_test.json` (174 valid, 310 invalid), generated by
`scripts/gen_p256_vectors.py` and vendored under the same rule as Stage 2: `just test` must not
need a network or a Python interpreter. **The upstream path moved** — Wycheproof removed
`testvectors/` and the suite now lives in `testvectors_v1/` under `ecdsa_verify_schema_v1.json`.
The generator pins revision `878e5366` so a regeneration that changes the vector count reads as a
deliberate bump rather than as upstream drift, and it asserts the group shape (secp256r1,
SHA-256, 65-byte uncompressed key, no `acceptable` result tier) rather than assuming it.

A rejected public key in that suite **panics** rather than answering `false`. Every one of the
111 keys is on the curve, so a rejection is a bug in `public_key` — and reporting it as `false`
would leave all 310 rejection cases passing while only the 174 accepting ones failed, which points
the reader at the wrong function.

This suite cost **70 s** against the `BigInt` field and costs **1.37 s** against `Felem`.
Splitting it behind its own `just` gate was considered when it was the slow version and rejected —
the rejections are the security argument, and a gate that is not part of `just test` is a gate
that rots. At 1.37 s the question no longer arises.

It also did the job it was built for. The entire field arithmetic was replaced underneath it —
representation, multiplication algorithm, and inversion — and the suite is what made that safe to
attempt in one step.

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

`stdlib/bits.sprout:64` said the overflow decision "is still open on that". This design deferred
the parenthetical to Stage 1 — its substantive claim, that `bit_shl`'s discarding is exempt from
whatever `*` does, was unchanged and still true — and Stage 1 did not pick it up either. Fixed
separately, a month later, once a review of the whole arc went looking for what still described
the pre-Stage-1 world.

The deferral's stated reason was wrong, which is worth recording because it is the kind of
reason that deters the next person: editing a `stdlib/*.sprout` comment does **not** necessarily
force a reseed. `bits.sprout` is entirely `extern` declarations, so it contributes no
line-numbered IR and shifting its lines moves nothing — `verify-bootstrap-fixed-point` passes
with the seed untouched and `just seed-fp-ack` is the whole cost. Weigh that per file, by
checking, rather than assuming any `stdlib/` edit buys the full reseed.

`docs/numeric-types-v1-draft.md` milestone N6 (`BigInt` + `Integer` instance) is half-delivered by
Stage 2: the type lands, the class instance waits for N1. The draft is updated to say so rather
than left to imply the milestone is untouched.
