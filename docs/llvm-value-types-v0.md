# LLVM value types and loop optimisation, v0

Status: exploration. Records a probe and the options it opens; proposes no change yet.
Non-normative. Measured 2026-10-27 on macOS arm64, Homebrew LLVM 23.1.1.

## 1. Question

Can LLVM's loop optimisers, the loop vectorizer and Polly, work on Sprout's dense numeric loops,
and what stops them?

Polly is available: the toolchain's LLVM ships `LLVMPolly`, and `clang -mllvm -polly` works. It
optimises a SCoP (static control part): a loop nest whose array accesses and bounds are affine in
the loop counters, with no unknown calls. On a C matrix multiply it finds one.

## 2. What "every value is an i64" means

Two decisions, which are easy to conflate:

- **A. Every value is one 64-bit word.** Generic code is compiled once with types erased, and
  vector slots, object fields and the GC scan treat every word alike. Its costs (dictionaries,
  rooting, B1's reach) are in [specialisation-v0.md](specialisation-v0.md).
- **B. LLVM sees that word as type `i64`**, even where the compiler knows it is a heap pointer
  (`IRTHeap` in `sprout_ir.sprout`) or a `Double`. A pointer is `inttoptr` at each use; a `Double`
  is bitcast.

B keeps every value compatible with the `i64` root table without a cast at each safepoint
([compiler-internals.md](compiler-internals.md) §Strings travel as `i64`), and matches the C
runtime, which takes and returns `long long`. This doc is about B.

## 3. Probe

`bench/llvm_remarks/kernels.sprout` holds axpy, an i-k-j matrix multiply and a sum over
`Vector Double`, written so B1 inlines every access and BCE versions the inner loops.
`bench/llvm_remarks/remarks.sh` prints the vectorizer's and Polly's remarks per function;
`--no-ovf` first deletes every overflow-check branch.

In the `$fast` clones, an access is a plain `load` through the vector's data pointer, with no
bounds check. What is left in the loop body: an overflow check on each index sum (`ci + j`), whose
failure branch calls `panic`; the vector handle cast with `inttoptr` and its `data` pointer
reloaded every iteration; and doubles carried as `i64`.

| loop | as emitted | overflow checks deleted |
|---|---|---|
| `mm_j$fast` (matmul inner) | vectorizer: early exit with a store; cannot identify array bounds. Polly: no remark | vectorizer: cannot identify array bounds. Polly: `IntToPtr` |
| `axpy_go$fast` | vectorizer: cannot identify array bounds. Polly: `IntToPtr` | same |
| `sum_go$fast` | reported vectorized, but the output is the loop unrolled 4 times with in-order scalar `fadd`s: a strict FP sum cannot be reordered. Polly: `IntToPtr` | same |

BCE does not version `mm_k` or `mm_i`; why was not examined.

### Blockers, in order

1. **Bounds checks.** BCE stage 1 removes them from self-tail-recursive inner loops
   ([bounds-check-elimination-v0.md](bounds-check-elimination-v0.md)).
2. **Overflow checks on index arithmetic.** Each is an early exit, which the vectorizer refuses
   when the loop also stores. Deleting them by hand clears this blocker. They cannot simply go,
   since `Int` overflow traps. **Unverified:** BCE's entry test bounds each atom of an index form
   (`box_limit` in `bce.sprout`), so a sum of two atoms may be unable to overflow inside `$fast`,
   and BCE could drop those checks there.
3. **Array bounds.** The vectorizer cannot compute the bounds it needs for a run-time alias check.
   The likely cause, not tested here: `data` is reloaded every iteration because an untyped `i64`
   element store may hit the header. The BCE doc (§3) and `BACKLOG.md` "Alias tags (TBAA) on
   inlined runtime-object access" record the same reload.
4. **Polly rejects `inttoptr`.** In these kernels, every region containing a vector access is
   rejected with `IntToPtr`. Not tested: whether hoisting the cast out of the loop is enough.

### Other costs of B

- **Pointer attributes.** LLVM rejects `noalias` and `dereferenceable` on an `i64` (`llvm-as`:
  "Attribute 'noalias' applied to incompatible type!"), so a runtime allocator's `declare` cannot
  say its result is a fresh object. This limits `BACKLOG.md` "The emitted LLVM `declare`s carry no
  attributes" to function attributes.
- **GPR-FP moves.** Before B1, the recognizer's row kernel spent 4 of 22 instructions per element
  on `fmov` between integer and FP registers
  ([phase-d-numeric-fastpath-design-2026-07-11.md](phase-d-numeric-fastpath-design-2026-07-11.md)
  §1).
- **FP constants.** A `Double` constant emitted as an `i64` costs 4 instructions on arm64
  (`BACKLOG.md` "Do NOT re-attempt const-folding Double-literal globals").

No timing was taken. Every finding above is what LLVM reports, not a speed-up.

## 4. Options

1. **Metadata, keeping `i64`.** Hoist the cast and the header loads out of loops, and tag headers,
   elements and bytes apart with TBAA. Local to `ir_lowering`. May clear blockers 3 and 4; does
   nothing for pointer attributes or GPR-FP moves.
2. **Typed values inside a function.** Emit `ptr` for `IRTHeap`, `double` for `Double` and `i64`
   for unknown kinds, converting only at uniform boundaries: polymorphic calls, slot stores, the
   root table and C externs. A is untouched. Touches every lowering arm, `ir_rooting`, the seed,
   every golden IR file, the cost gates and `cpr-declare-check`.
3. **Typed worker signatures.** Give the CPR `_worker` and `_argw` clones `ptr` and `double`
   parameters. Grows one function at a time; helps across calls only, so it needs 1 or 2 inside
   bodies.
4. **Precise GC through LLVM statepoints**
   ([Statepoints](https://llvm.org/docs/Statepoints.html)): typed GC pointers replace the
   shadow-stack root table. Rewrites the rooting model; out of proportion to this problem.

Removing A (specialisation) is a separate decision: [specialisation-v0.md](specialisation-v0.md).

## 5. Next

1. On `mm_j$fast`, hand-apply each of: the header-load hoist with TBAA; the hoisted cast; `ptr`-
   and `double`-typed IR. Re-run the remarks and time each against the same loop in C. If option 1
   gets most of the gain, option 2 is not worth its cost.
2. Check the overflow hypothesis (blocker 2) against `bce.sprout`.
3. Revisit Polly only once the vectorizer works. Polly pays on dense nests (matrix multiply,
   stencils), and it hits every blocker above.
