# A pure byte buffer (v0, 2026-10-18)

**Status:** proposal, options open, not approved. No code change. Not normative until it lands
in [spec-v0.md](spec-v0.md). Comes from Phase 3.1 of
[native-sprout-runtime-v0.md](native-sprout-runtime-v0.md).

## 1. Problem

Phase 3.1 would move `crypto_base64_*` and `json_*` from C to Sprout. Its gate is a port at
parity with C. A pure Sprout base64 encoder, built as a list of string slices and concatenated
once, runs 22–55× slower than C (~25–28 ns per input byte against ~0.5). Its profile: GC and
allocation ~58%, string concatenation ~12%, the encoder's own code and reads ~17%.

Output-building code needs a buffer it can append to without allocating per piece. Sprout has
`MutVec`, but its operations are `!{IO}`, and `base64_encode` is pure. There is no way to hide
`IO`, and the runtime never calls a Sprout closure from C. So the buffer has to be pure, or the
language has to let a pure function mutate something nobody else can see.

## 2. Goals and non-goals

Goals:

- Append-only byte building at C-like speed, from pure functions.
- Purity that holds, not purity by convention.
- A shape that does not block general in-place update later.

Non-goals:

- General mutable arrays (writing at any index). §5 says which options extend to them.
- Reaching parity by the buffer alone. §3 shows the compiler work matters more.

## 3. Measurements

Encoding 64 KB, 400 times, macOS arm64; median of 7–11 interleaved runs; checksums and output
match C. The compiler changes were applied by hand to the emitted IR, not built.

| build | × C |
|---|---:|
| list of string slices, concatenated once (today's best pure form) | 22–55× |
| `MutVec Int` fill, C call per byte read and write | 6.5× |
| same, reads and writes inlined as loads and stores | 2.6× |
| same, plus alias tags (TBAA) separating headers, elements and bytes | 1.6× |
| byte buffer, `push4`, inlined, alias tags, version check, root push/pop around each push | 2.8× |
| same, `push_byte` | 10.7× |
| byte buffer, `push4`, inlined, alias tags, no rooting, with §5.4's version check | 1.86× |
| same, `push_byte` | 2.13× |
| byte buffer, no rooting, no version check (the shape of §5.1–5.3) | 1.80× / 1.90× |

What this says:

- **The buffer choice is worth at most ~10%.** The large steps are compiler work every option
  needs: inline access to runtime data (6.5× → 2.6×), alias tags (→ 1.6×), and not rooting
  around a push (10.7× → 2.1× for one push per byte).
- **Without alias tags LLVM reloads everything.** Every Sprout value is an `i64`, so a store of
  an `Int` element may, as far as LLVM knows, overwrite a vector's header. It reloads the table
  pointer (three dependent loads) and the `data` pointer after every store.
- **Rooting.** The compiler cannot see that a push does not allocate, so it saves live heap
  values on the root stack around each call: two C calls per push, which survive inlining.
  `vector_mutset` is on `ir_rooting.is_nonallocating_read`'s list, which is why the `MutVec` rows
  pay nothing. A buffer that may grow allocates on growth, so a push needs rooting on its slow
  path only.
- **Batching matters little once inlined and unrooted:** `push4` beats `push_byte` by 6–15%.

### 3.1 What closes the rest

The baseline is `crypto_base64_encode`, timed in the same binary and run. Above, the Sprout side
built no `String`; here its `finish` copies into a GC `String`, as the C side does, which costs
~0.3×. Each row removes one kind of check from the best unrooted build, by hand and
unconditionally, so it bounds what a compiler that proves the check redundant could gain. Median
of 11 interleaved runs.

| variant | `push4` | `push_byte` |
|---|---:|---:|
| inlined, alias tags, no rooting | 2.08× | 2.17× |
| no overflow checks | 1.67× | 2.12× |
| no bounds checks | 1.51× | 1.26× |
| alphabet as a constant array, bounds check kept | 1.65× | 1.60× |
| constant array, no overflow checks | **1.25×** | 1.60× |
| all three | **0.99×** | **1.07×** |

Each maps to a change:

- **Constant tables.** A top-level `let` is a mutable global set at startup, so LLVM sees neither
  the table's size nor its contents. As a constant array of 64, a masked index (`& 63`, or a
  24-bit value shifted right 18) is provably in range and LLVM drops the check itself.
- **Overflow checks that cannot fire.** `bit_shl` and `bit_or` wrap and lower to plain `shl` and
  `or`, so packing four output bytes with shifts instead of `*` and `+` needs no compiler work.
  Only `push4` gains, because only it packs. Index arithmetic (`g * 3`) needs range analysis.
- **Bounds checks in loops.** With `g < len / 3`, reads at `3g .. 3g + 2` are in range, and a
  pre-sized output holds every push. LLVM keeps the checks at `-O2`, and its loop passes do not
  remove them in the sort either. Checking once per loop call and running an unchecked clone
  does: with constant tables and no overflow checks, 0.98× (push4) and 1.07× (push_byte), the
  same as deleting every check. [bounds-check-elimination-v0.md](bounds-check-elimination-v0.md).

So the 1.3× gate needs constant tables and shift-based packing; parity also needs bounds-check
elimination. The table here must be a byte table: B1
([phase-d-numeric-fastpath-design-2026-07-11.md](phase-d-numeric-fastpath-design-2026-07-11.md))
inlines only `Vector Double`, since generic wrappers such as `vec_get_or` erase other element
types. `Bytes` and a `ByteBuf` hold only bytes and avoid that.

## 4. Prior art

Checked against each language's reference or library source, 2026-10-18; the Koka and Baker rows
and the last three, 2026-10-19.

| language | mechanism | purity comes from |
|---|---|---|
| Haskell | `runST :: (forall s. ST s a) -> a` (`Control.Monad.ST`) | a rank-2 type: a reference's type mentions `s`, the result cannot |
| Koka | `local-scope(action: forall<h> () -> <local<h>\|e> a) : e a`; `run` for heap refs (`std/core/types.kk`) | the same rank-2 shape; the doc cites "State in Haskell" |
| Haskell `bytestring` | `unsafeCreate` over `unsafeDupablePerformIO`, "creating ByteStrings outside the IO monad" | the library author's word |
| Clean | unique arrays (`*{#Char}`) updated in place | uniqueness typing |
| Swift | `~Copyable` (SE-0390); generic parameters are `Copyable` unless they opt out (SE-0427) | the type checker, once generics carry the bound |
| Rust | `String::from_utf8(Vec<u8>)` takes the `Vec` by value, no copy | ownership |
| Lean 4 | `Array`/`ByteArray` updated in place when the reference count is 1 | a run-time count |
| Erlang | appending to a binary reserves room (twice the size, at least 256); "only the binary returned from the latest append operation will support further cheap append operations" (Efficiency Guide, *Constructing and Matching Binaries*) | a run-time check; older versions copy |
| Koka | Perceus reference counting; reuse analysis gives "functional but in-place" (FBIP) updates (PLDI 2021) | a run-time count, made exact by the compiler |
| Baker / Haskell `DiffArray` | newest version holds the array; older versions are diff chains, rerooted on access | a run-time check; old versions slow down |
| Go | `strings.Builder`: mutable; `String()` makes no copy; a copied Builder panics | not pure |
| OCaml | `Buffer.contents` copies; `Bytes.unsafe_to_string` relies on convention | not pure |
| Scala (research) | `@local` second-class values (Osvald et al., OOPSLA 2016, "Gentrification Gone too Far?"): "Second-class values cannot be stored in mutable variables, they cannot be returned from functions, and they cannot be accessed by first-class (named or anonymous) functions through free variables" | a binding mode, not a type: "parametric polymorphism does not pose major difficulties" |
| Effekt | "values (that can be returned and stored)" against "computation (that can be passed, but not returned)" (language tour, *Captures*) | second-class blocks; boxing tracks captures |
| OxCaml | `local` parameters on any value, not only closures: "a promise by a function not to let a particular argument escape the region where it belongs" (*Stack allocation*) | a mode, used for stack allocation |

Lean's approach does not fit: Sprout's GC traces, it does not count references.

## 5. Options

### 5.1 Linear `ByteBuf`

`push_byte(buf: ByteBuf, x: Int) -> ByteBuf`, pure, updated in place because a linear value has
one owner. Needs the hole in linear types closed first: a generic `fn dup(x: a) -> (a, a)`
accepts a linear value today (BACKLOG P3 **Linearity bound on a type parameter**).

- **For:** checked statically; no run-time check, no copy; the only option that extends to
  writing at any index, so linear `MutVec` and `Dict` with pure APIs follow, and so do sound
  resource handles.
- **Against:** closing the hole is breaking: every generic function must say whether it accepts
  a linear type, across the four repos (Swift's SE-0427 model). Linear code must consume the
  buffer on every path. Weeks of work in `infer` and the linear checker before base64 gains
  anything.

### 5.2 A `Local` effect

A third effect level, `pure < Local < IO`. `ByteBuf` operations are `!{Local}`, and only
`build_bytes(f: ByteBuf -> Unit !{Local}) -> Bytes`, which is pure, creates one. Because the
levels stay a chain, no multi-label rows are needed, and an `!{e}` combinator such as `list_each`
instantiates at `Local` as it does at `IO`.

The buffer cannot escape without rank-2 types, for reasons specific to this one type: the
callback returns `Unit`; every mutable cell needs `IO`, which the callback cannot do;
`task_spawn` takes `Unit -> Unit !{IO}`; and a `ByteBuf` holds bytes, not references. A
`build` returning `(a, Bytes)` would be unsound (`a` could be a closure over the buffer), and so
would a generic `LocalVec a` (a buffer could be stored in an outer one and used after its
builder ends). Haskell and Koka both use rank-2 types for exactly this.

- **For:** purity checked by the type checker; small runtime.
- **Against:** a new effect level touches `unify_effects_applied`, `merge_effects`, the
  enforcement rules, method subsumption (spec §7 rule 8), diagnostics and the printer. It
  covers one type and never becomes general local state without rank-2 types or 5.5's
  second-class parameters. When the handlers draft
  ([effect-system-handlers-draft.md](effect-system-handlers-draft.md)) adds rows, `Local` would
  be rebuilt as an ordinary label.

### 5.3 A private unsafe creator

One extern that runs an `!{IO}` callback and returns pure `Bytes`, like `bytestring`'s
`unsafeCreate`. It can be kept inside `stdlib/bytes.sprout`: an extern whose signature mentions
`local` "is reachable only from the module that declares it" (spec, *Externs are outside the
module system*).

- **For:** days of work.
- **Against:** a pure `build` hides any `IO` its callback does, not only the buffer's. Purity
  rests on the stdlib author. Nothing later builds on it.

### 5.4 Erlang-style append

Two objects: a shared, mutable `Storage { cap, fill, data }` that no Sprout code sees, and an
immutable `ByteBuf` value `{ storage, len }` whose bytes are `data[0 .. len)`.
`push_byte(v, x) -> ByteBuf` is pure:

1. `v.len == fill` and there is room: write `data[fill]`, add 1 to `fill`, return
   `{ storage, len + 1 }`.
2. `v.len == fill`, no room: grow the storage and write.
3. `v.len < fill`: `v` is an older version. Copy `data[0 .. v.len)` to a new storage, then write.

Writes only ever land at `fill`, which is at or past every value's `len`, so no value's bytes
change after it is made. Nothing can observe the sharing.

```
b1 = push_byte(empty, 'A')    fill=1   b1 = "A"
b2 = push_byte(b1, 'B')       fill=2   b2 = "AB"
b3 = push_byte(b1, 'C')       b1.len=1 < fill=2: copy   b3 = "AC", b2 still "AB"
```

- **For:** pure by construction, with no language change. A `ByteBuf` is an ordinary value:
  it can be returned, stored, or used in generic code. Days of runtime work. The version check
  costs 3–12% (§3).
- **Against:** a silent O(n²) cliff when code keeps pushing onto an old version; Erlang
  documents the same trap, and no diagnostic can catch it. Append-only: writing at an index
  would break older versions (Haskell's `DiffArray` needs an undo log for that). Each push
  returns a new pair, an allocation unless the loop carries `(Storage, Int)` unboxed. Doubling
  storage is `malloc` memory the GC trigger does not count (BACKLOG P2 **GC trigger is
  object-count-blind**). If threads ever share a heap, `fill` needs a compare-and-swap; the
  concurrency plan is share-nothing
  ([concurrency-design-exploration-2026-07-13.md](concurrency-design-exploration-2026-07-13.md)).
- **`finish` must copy.** Each `Bytes` owns its `data`, and `sprout_gc_sweep` frees it. Handing
  the storage to a `Bytes` without a copy would free memory older versions still read. Erlang
  can share because its binaries are reference-counted. Zero-copy needs `Bytes` able to point
  into a `Storage`.

#### Beyond append

The rule underneath is "write only where no version reads". It covers more than bytes:

- **Append and prepend.** A buffer can grow at both ends; prepend fills from the back. Go's
  `append` reuses spare room without the version check ("Otherwise, `append` re-uses the
  underlying array", spec, *Appending to and copying slices*), so two appends to one slice
  overwrite each other. The `len == fill` check is what makes the same reuse pure.
- **Pop, then push** always copies: the longer version still reads the slot. A stack used this
  way pays a copy per push.
- **Writes at an index** have no spare room to use. Baker's persistent arrays handle them: the
  newest version holds the array and is written in place; the old version becomes a diff node
  (index, old value, newer version). Old versions read correctly but slower, "proportional to
  ... the number of updates", until rerooting reverses the chain toward the version read
  (Conchon & Filliâtre, *A Persistent Union-Find Data Structure*, §2.3). Haskell's `DiffArray`
  is the same: the old array "stores a link to the new current array along with the
  difference".

A Baker array over `Ref` and `MutVec Int`, written in Sprout (three allocations per set, every
step a C call), against today's copying `vector_set` and an in-place `mutvec_set`. ns per
operation, random indices, one process per row, median of 5, macOS arm64:

| elements | set: Baker | set: `vector_set` | set: `mutvec_set` | get: Baker | get: `vector_get` |
|---:|---:|---:|---:|---:|---:|
| 100 | 60 | 121 | 1 | 12 | 1 |
| 1,000 | 74 | 1,071 | 1 | 12 | 1 |
| 10,000 | 67 | 6,040 | 1 | 12 | 2 |
| 100,000 | 98 | 70,372 | 2 | 57 | 10 |

Alternating reads between two versions `d` sets apart reroots `d` steps each way: 106 ns per
pair at `d` = 1, 0.9 µs at 10, 11.6 µs at 100, 156 µs at 1,000.

- Set stays flat while the copy grows with size; Baker is ahead at 100 elements and up. It is
  50–75× slower than mutation here, and a read about 10×. A runtime version could make a set one allocation (the old
  handle turns into the diff node in place); that is not measured.
- Every read checks whether its version holds the array, and a fast loop needs that check
  hoisted before bounds-check elimination applies.
- Rerooting writes into old objects, so a generational GC needs a barrier there, and a shared
  heap would need locking.
- Lean and Koka reach in-place updates by reference counts instead (an update writes in place
  when the count is 1). That needs counts, which a tracing GC does not keep.

### 5.5 A `Local` effect over second-class buffers

5.2 with its escape argument replaced by a general one. A data parameter may be declared
`local`, and the buffer reaches code only through one:

```
fn run_local(f: (local LocalVec a) -> b !{Local}) -> b       # pure
fn local_set(v: local LocalVec a, i: Int, x: a) -> Unit !{Local}
```

The rules are Osvald et al.'s (§4), and Sprout's callee check for `local` closures already
enforces them by name, whatever the type (`linear_check.escape_honesty`): a `local` parameter may
be passed at another `local` position, never returned, stored, bound with `let`, passed at an
ordinary position, or captured by a lambda outside a `local` position. So:

- `b` cannot carry the buffer: returning it, or a closure over it, is an escape.
- A buffer cannot be stored in another: `local_set`'s `x` is not `local`.
- Nested runs stay separate. `f` is not a `local` slot, so a lambda passed to an inner
  `run_local` cannot capture the outer buffer, and discharging `Local` there hides only the inner
  buffer's writes.

This is general local state, writes at any index included, without rank-2 types.

- **For:** checked statically; extends to `LocalVec`, local maps, any local cell. Opt-in and not
  breaking: a plain value is accepted at a `local` slot, as a closure is today, so a generic
  function accepts a local argument once its parameter says `local`.
- **Against:** 5.2's effect work, plus three checker changes:
  - `local` on data parameters and type variables. Both are rejected today; spec *Non-escaping
    closure parameters* gives `once a`'s reason, but the escape walk is by name and would hold
    for any type. The real limit is that `local` licenses only closures capturing borrowed values.
  - `local` pushed down onto a lambda's own parameter (`\buf -> …` at `f`), with the escape walk
    run over the lambda's body. Today it is rejected because the walk covers declared functions
    only.
  - The walk counts any non-call use as an escape, including a field read or an operand. A data
    `local` needs reads that return a first-class value to pass.

  Every combinator a local buffer flows through must declare `local` (BACKLOG P2 **Mark the
  prelude combinators' function arguments `local`** covers closures only); until then, loops
  are written as recursion. Nothing proves it sound in Sprout's terms: the argument here is a
  reading of the paper's rules.

## 6. How they combine

- **5.4 then 5.1.** 5.4's `Storage` is 5.1's runtime half. A linear wrapper over it makes the
  version check a branch that never fails.
- 5.2 and 5.1 compete: both check purity statically, and 5.1 is more general.
- 5.5 is 5.2 made general, and competes with 5.1 for general in-place update: 5.1 asks every
  generic function to opt out of linearity (breaking), 5.5 asks a function to opt in to `local`.
  5.5 needs no run-time check, so it could replace 5.4 later; it is not built on it.
- 5.3 is the fallback if nothing else ships.
- **Bounds-check elimination.** Under 5.1, 5.2, 5.3 and 5.5, types prove that a loop body cannot
  change the buffer, so a loop's checks can move to one entry test. Under 5.4 they cannot, since
  `push_byte` is pure: lifting the version check out of a loop needs an alias rule
  ([bounds-check-elimination-v0.md](bounds-check-elimination-v0.md) §6).

## 7. Recommendation

5.4 now, shaped for 5.1. An opaque `ByteBuf`; `push_byte`, `push4` and `finish` (copying);
internal loops carry the unboxed pair; a debug counter for copies caused by old versions, and a
cost test. Closing the linear-generics hole stays its own milestone, not a prerequisite. A second
opinion reached the same recommendation independently.

Against it: in a language that leads with static guarantees, it relies on a run-time check, and
its cliff shows up only in a profiler.

None of this reaches the 1.3× gate alone. That needs the compiler work in §3, which Phase 3.3
needs anyway.

## 8. Impact (for 5.4)

- **Syntax:** none.
- **Semantics:** a new opaque type with pure operations. No change to existing types.
- **Types:** none.
- **Errors:** none new; the cliff is a performance trap, not an error.
- **Runtime:** a new heap kind for `Storage` and new builtins (`empty`, `push_byte`, `push4`,
  `push_str`, `length`, `to_bytes`, `to_string`). Needs approval (`AGENTS.md` §Builtin vs Stdlib).

## 9. Tests

- Version semantics: after pushing onto an older version, every version still reads what it read
  before; pushes at, past and below `fill`; growth past `cap`.
- Differential: Sprout base64 and C base64 agree over a corpus, including every length mod 3.
- Cost: pushing n bytes in sequence copies nothing (debug counter), as `test_byte_offset_cost.spr`
  does for byte offsets.

## 10. Spec and docs

`spec-v0.md` gains the type and its operations, normative when it lands. `stdlib-reference.md`
documents the cliff. `idiomatic-sprout.md`: build output with a `ByteBuf`, never by repeated `++`.

## 11. Open questions

- 5.5: is the escape argument sound? It rests on Osvald et al.'s rules as the `local` walk
  implements them, with no proof in Sprout's own terms.
- Should `Bytes` be able to share a `Storage`, so `finish` need not copy?
- Is `push4` worth its API once pushes are inlined and unrooted (6–15%)?
- Which of §3's compiler steps lands first: inline access, alias tags, or rooting only on slow
  paths?
