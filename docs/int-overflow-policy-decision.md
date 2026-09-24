# Int Overflow Policy — Design Decision (DECIDED, IMPLEMENTED)

**Status:** DECIDED 2026-09-22, **implemented** by `docs/bigint-v0.md` Stage 1 — **Option A**
(trap on overflow). Findings documented 2026-07-06; deferred until an escape hatch existed for
programs that genuinely need values above i64. `BigInt` is that escape hatch, so the two landed
together: see `docs/bigint-v0.md`, which carries the decision and the staged implementation.
`+`, `-`, `*` and unary negation panic on overflow, `INT_MIN / -1` panics, and an over-range
decimal or radix literal is a compile error; §5–§7 below record the decision and the resolution
of the items it was coupled to.
**Couples to:** W9/X4 (integer-literal overflow, `docs/fundamentals-code-review-handoff-2026-07-03.md`,
resolved — §7) and W7's deferred `INT_MIN / -1` operator guard (resolved — `docs/bigint-v0.md` §5.1).

---

## 1. Problem

Sprout's `Int` was *specified* as a mathematical (arbitrary-precision) integer, and both spec
§6.5 and §8.4 explicitly called the native `i64` lowering **"a temporary v0 implementation
constraint, not the intended long-term meaning of `Int`."** (Both sections are rewritten by this
decision landing — see the header.)

**Correction:** an earlier version of this section claimed "the interpreter uses host bignum
arithmetic." False — there is no such path. `repl_eval_expr` in `runtime/sprout_runtime.c:5114`
aborts with `"not supported in native backend"`; the i64 lowering described below is the only
implementation that exists.

The concern: the temporary divergence is **silent**. A program that overflows i64 gets a
garbage-but-defined value with no signal — the opposite of Sprout's stated identity
("strong safety with beginner-friendly ergonomics"). A beginner who writes `factorial(50)`
should get a loud error, not a silently negative number.

## 2. Ground truth (verified against source, 2026-09-14)

Current native behavior is **defined two's-complement wraparound, NOT undefined behavior**:

- The native lowering — the `IRIAdd` / `IRISub` / `IRIMul` arms of `lower_op` in
  `stdlib/compiler/ir_lowering.sprout` — emits plain `add i64` / `sub i64` / `mul i64` with
  **no `nsw`/`nuw` flags**. There is no second codegen path.
- Unary negation is the same: the `IRINeg` arm emits `sub i64 0, x`, also flagless, so
  `-INT_MIN` wraps instead of trapping.

In LLVM, plain `add i64` has fully-defined wraparound; it is the `nsw` flag that makes
signed overflow UB. So this is materially different from the div-by-zero case (which *was*
genuine UB — `sdiv i64 x, 0` — and was fixed in W7, commit `29c69b7`). Overflow today is
**silently wrong, but well-defined and memory-safe**. This is a semantics-policy choice,
not a soundness hole.

## 3. Prior art (verified against primary sources, 2026-07-06)

| Language | `+`/`-`/`*` overflow | Primary source (exact wording) |
|----------|----------------------|-------------------------------|
| **Swift** | **Traps — reports an error** by default; `&+`/`&-`/`&*` opt into wrapping | The Swift Programming Language, *Advanced Operators → Overflow Operators*: *"by default Swift reports an error rather than allowing an invalid value to be created."* |
| **Rust** | **Panics in debug builds**, wraps (two's complement) in release; `-C overflow-checks` overrides | Rust Reference, *Operator expressions → Overflow*: *"Integer operators will panic when they overflow when compiled in debug mode."* |
| **Zig** | Default operators = **Illegal Behavior** on overflow; **Debug/ReleaseSafe panic** (detected), ReleaseFast/ReleaseSmall unchecked; `+%` wrapping, `+\|` saturating | ziglang.org language reference: *"Operators such as `+` and `-` cause Illegal Behavior on integer overflow. Alternative operators are provided for wrapping and saturating arithmetic on all targets."* |
| **Go** | Runtime: two's-complement; **compile-time constant overflow = error** | Go spec, *Constants*: *"It is an error if the constant value cannot be represented as a value of the respective type."* |

Not re-verified this pass (omitted rather than asserted from memory): Java/Kotlin/C#
(believed silent wrap + opt-in checked), Haskell (`Int` wraps, `Integer` bignum), C/C++
(signed overflow UB). Verify before citing.

**Pattern:** newer *safety-first* languages (Swift, Rust-debug, Zig-safe) treat overflow
as a **bug to trap**, with explicit opt-in wrapping operators. Systems/perf-first
languages (Go, release-Rust) wrap.

**Key discovery — Go is the precedent for the asymmetry.** Go makes *constant/literal*
overflow a compile error while *runtime* arithmetic wraps silently. So "reject the literal,
wrap the runtime value" is a deliberate, shipped design, not an incoherence.

## 4. Options

Both A and B make the W9/X4 decision (reject over-range integer literals at compile time)
**coherent** — the only difference is runtime behavior.

- **Option A — "trap everything"** (Swift / Rust-debug / Zig-safe model).
  Literal overflow = compile error; runtime overflow = **panic** with source location.
  One rule: *`Int` never silently yields a wrong value.*

- **Option B — "the Go model."**
  Literal overflow = compile error; runtime overflow = **silent two's-complement wrap**
  (status quo). The literal-vs-runtime split is intentional and documented.

- **Option C — arbitrary-precision `Int` now** (the spec's intended end-state).
  Correct and matches the interpreter, but a large runtime/GC/ABI change: `Int` would stop
  being a uniform `i64` scalar, with blast radius on the uniform-i64 ABI and GC tagging.
  Out of scope for v0; recorded as the long-term target, not a live option here.

## 5. Decision: Option A

Adopted 2026-09-22, alongside `docs/bigint-v0.md`, which supplies the escape hatch (`BigInt`)
that this deferral was waiting on (§7).

1. Go's *silent runtime wrap* (Option B) is exactly the footgun that prompted this review.
2. **Forward-compatibility with the bignum end-state (Option C).** Under A, an overflowing
   program panics today → *succeeds* under bignum (a safe transition). Under B, a program
   relying on wrap *changes output* under bignum — a real breaking change. Trap-now
   protects the migration path; wrap-now sabotages it.
3. Go chose wrap for systems-level performance; Sprout is positioned safety-first +
   beginner-friendly, so Go's rationale does not transfer.
4. Cheap to build: reuse W7's `IRPanic` terminator + LLVM `llvm.sadd/ssub/smul.with.overflow`
   intrinsics (branch on the overflow bit to `IRPanic`) for `+`/`-`/`*`/negate. `INT_MIN / -1`
   is a separate case — `sdiv` has no LLVM overflow-intrinsic form, so it needs its own guard in
   `finish_checked_div` rather than arriving for free from the intrinsics above
   (`docs/bigint-v0.md` §5.1 corrects an earlier claim to the contrary).

**Honest cost of A:** a branch per `+`/`-`/`*` (this is why Rust ships *release* with
wrap). Branch-predictable and cheap, but a real tax in hot loops — mitigable later with
explicit `wrapping_add`-style operators if a measured hot loop needs them.

## 6. Implementation notes

- Reuse the W7 pattern: `IRPanic` op (`sprout_ir.sprout`), the guard-CFG built in
  `ast_to_ir.sprout` (not the `ir_lowering` text layer — block-splitting there breaks phi
  predecessors), and the four exhaustive `ir_rooting.sprout` classifications.
- Emit `llvm.s{add,sub,mul}.with.overflow.i64`, `extractvalue` the `{result, i1}` pair,
  `br` on the overflow bit to a panic block (`IRStrConst` + `IRPanic`) vs an ok block that
  carries the result forward. `INT_MIN / -1` gets its own compare-and-branch in
  `finish_checked_div`, alongside the existing zero-divisor check.
- Spec §6.5 / §8.4 updated in the same change; the literal-overflow half (X4, §7) lands
  together so the policy is uniform.

## 7. Blocking — resolved

W9 was requested to land "as one piece." X4 (integer-literal overflow) depended on this
decision, so W9 was parked behind this deferral until `BigInt` supplied the escape hatch.

**X4 resolved, 2026-09-22 — rules on radix literals explicitly**, per `docs/bigint-v0.md` §5.3:

- A *hex or binary* literal denotes a 64-bit pattern, read as signed two's-complement. Every
  pattern is writable; nothing is rejected. This keeps the all-ones-mask idiom
  (`0xFFFFFFFFFFFFFFFF` is `-1`) that `docs/bitwise-int-ops-v0.md` §5.6 documents.
- A *decimal* literal denotes a mathematical value and is rejected at compile time if it does
  not fit in `Int`, naming `BigInt.from_string` as the alternative.
- **Unary-minus carve-out.** Sprout's parser treats a leading `-` as a separate unary operator
  (`parse_unary`, `parser.sprout:1159`), not part of the literal token, so
  `-9223372036854775808` is unary minus applied to the bare literal `9223372036854775808`,
  whose magnitude does not fit `[0, 2^63-1]`. A decimal literal that is the immediate operand of
  unary `-` is checked against `[-2^63, 2^63-1]` instead, so `INT_MIN` stays a valid literal.
  Nine languages were surveyed; all accept `-9223372036854775808`, differing only in mechanism —
  Rust is the decisive precedent, since its grammar has the same unary-minus shape as Sprout's
  and it carved the same exception into the range check rather than the grammar. Full survey:
  `docs/bigint-v0.md` §5.3.

X1/X2/X3/X5/X6 were not gated on this and could have proceeded independently.
