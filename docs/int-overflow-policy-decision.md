# Int Overflow Policy — Design Decision (OPEN / DEFERRED)

**Status:** OPEN. Findings documented 2026-07-06; decision deferred by Kuba.
**Owner decision required:** runtime overflow behavior for `+` / `-` / `*` (Option A vs B below).
**Couples to:** W9/X4 (integer-literal overflow, `docs/fundamentals-code-review-handoff-2026-07-03.md`)
and W7's deferred `INT_MIN / -1` operator guard.

---

## 1. Problem

Sprout's `Int` is *specified* as a mathematical (arbitrary-precision) integer — the
interpreter uses host bignum arithmetic (spec §8.4, line 426). The native backend lowers
`Int` to machine `i64`, and both spec §6.5 (line 349) and §8.4 (line 432) explicitly call
this **"a temporary v0 implementation constraint, not the intended long-term meaning of
`Int`."**

The concern: the temporary divergence is **silent**. A program that overflows i64 gets a
garbage-but-defined value with no signal — the opposite of Sprout's stated identity
("strong safety with beginner-friendly ergonomics"). A beginner who writes `factorial(50)`
should get a loud error, not a silently negative number.

## 2. Ground truth (verified against source, 2026-07-06)

Current native behavior is **defined two's-complement wraparound, NOT undefined behavior**:

- Typed path — `stdlib/compiler/ir_lowering.sprout:124-126` emits plain `add i64` /
  `sub i64` / `mul i64` with **no `nsw`/`nuw` flags**.
- Direct path — `stdlib/compiler/codegen.sprout:2106` emits plain `add`/`sub`/`mul`.

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

## 5. Recommendation (author): Option A

1. Go's *silent runtime wrap* (Option B) is exactly the footgun that prompted this review.
2. **Forward-compatibility with the bignum end-state (Option C).** Under A, an overflowing
   program panics today → *succeeds* under bignum (a safe transition). Under B, a program
   relying on wrap *changes output* under bignum — a real breaking change. Trap-now
   protects the migration path; wrap-now sabotages it.
3. Go chose wrap for systems-level performance; Sprout is positioned safety-first +
   beginner-friendly, so Go's rationale does not transfer.
4. Cheap to build: reuse W7's `IRPanic` terminator + LLVM `llvm.sadd/ssub/smul.with.overflow`
   intrinsics (branch on the overflow bit to `IRPanic`). The `.with.overflow` intrinsics
   also close W7's deferred `INT_MIN / -1` gap for free (no need to materialize the
   `INT_MIN` literal the lexer can't yet represent).

**Honest cost of A:** a branch per `+`/`-`/`*` (this is why Rust ships *release* with
wrap). Branch-predictable and cheap, but a real tax in hot loops — mitigable later with
explicit `wrapping_add`-style operators if a measured hot loop needs them.

## 6. Implementation notes (for whoever picks this up, if A is chosen)

- Reuse the W7 pattern: `IRPanic` op (`sprout_ir.sprout`), the guard-CFG built in
  `ast_to_ir.sprout` (not the `ir_lowering` text layer — block-splitting there breaks phi
  predecessors), and the four exhaustive `ir_rooting.sprout` classifications.
- Emit `llvm.s{add,sub,mul}.with.overflow.i64`, `extractvalue` the `{result, i1}` pair,
  `br` on the overflow bit to a panic block (`IRStrConst` + `IRPanic`) vs an ok block that
  carries the result forward.
- Spec §6.5 / §8.4 updated in the same change; the literal-overflow half (X4) lands
  together so the policy is uniform.
- If B is chosen instead: no code change; document the deliberate asymmetry in §8.4 so it
  reads as intentional (the Go model), and land X4 as the literal-only guard.

## 7. Blocking

W9 was requested to land "as one piece." X4 (integer-literal overflow) depends on this
decision, so **W9 is parked behind this deferral.** X1/X2/X3/X5/X6 could proceed
independently, but per the one-piece request they wait too.
