# Immutable slices (views) — design sketch (v0)

Status: **design note, pre-approval. Nothing here is implemented, and nothing here should be
implemented before §12's measurement gate is satisfied.** Written 2026-09-12, prompted by the
question "do we need slices, Go-style or Rust-style?". The short answer is *neither*; this note
records why, and what the third option would look like if a measurement ever justifies it.

Related: `docs/linear-borrowing-v0.md` (why a Rust-style borrowed slice is out of reach),
`docs/growable-mutvec-v0.md` (the owner half, already shipped), and the `BACKLOG.md` entry
"Mid-string `str_slice` is O(start)" — an independent defect, fixable without any view type.
(Its prefix half is fixed: `str_slice` now walks only to `start + count`.)

Backlog references here are by **entry title, not line number**: `BACKLOG.md` shifts under every
merge, and one of this note's own citations had already rotted by the time it was first reviewed.

## 1. Problem

Every windowing operation in Sprout allocates and copies. Verified in the tree:

| operation | cost | where |
|---|---|---|
| `str_slice(s, from, count)` | copy + O(from + count) codepoint walk | `runtime/sprout_runtime.c` (`str_slice`) |
| `str_slice_bytes(s, off, len)` | copy, O(len) | `runtime/sprout_runtime.c` (`str_slice_bytes`) |
| `bytes_slice(b, from, count)` | fresh `BytesVal` + `memcpy` | `runtime/sprout_runtime.c` (`bytes_slice`) |
| `vec_slice(start, count, v)` | walks the range into a `List`, rebuilds a `Vec` | `stdlib/prelude.sprout` (`vec_slice_from`) |

(Both `str_slice` and `bytes_slice` used to declare their third parameter `to` while the runtime
treated it as a length — two call sites carried `NB` comments about it. Renamed to `count` while
writing this note; the exported `string.slice` / `bytes.slice` wrappers were always correct, so
only stdlib-internal readers were exposed.)

The consequence is not hypothetical. `tcp_write_some(conn, payload, offset)` takes a byte **offset
instead of a re-sliced tail**, and `docs/builtins-reference.md:105` says why in as many words: it is
"what keeps a Sprout-side write loop linear instead of O(n²) in the payload length". That is a
hand-rolled view, threaded through a builtin signature, because the language has no view type.
`docs/browser-preliminary-analysis.md:167` independently asks for "better range or slice" support
for index-heavy tokenizers.

Not every quadratic in the tree is this problem, and the note is careful not to claim them.
The `BACKLOG.md` entry "`bytes_builder_append` is O(n_left + n_right) per call" is a *different*
defect — it copies the chunk pointer array on every append, and its named fix is a rope, not a
window.

So the missing thing is **a cheap window over an immutable sequence**. It is not "a growable
array" — Sprout already has two of those.

## 2. What Sprout has today

- `List a` — cons list; no indexing, no windows.
- `Vec a` — immutable flat array (`VectorVal {len, cap, data}`, `runtime/sprout_runtime.c:91`).
- `MutVec a` — the *same* `VectorVal`, mutated under `!{IO}`, growable (`vector_push`,
  `vector_truncate`). This is Rust's `Vec<T>` and Go's `[]T`-as-buffer. It already exists.
- `Bytes` — immutable `BytesVal {len, data}` (`runtime/sprout_runtime.c:113`).
- `String` — a GC block of kind `SPROUT_HEAP_CSTR`, allocated `len + 1` bytes and
  **NUL-terminated** (`runtime/sprout_runtime.c:1348`).

Sprout therefore already owns the *owner* half of both the Go and the Rust design. What is absent is
the *view* half — and only the view half is worth discussing.

## 3. Goals and non-goals

**Goals.**
1. O(1) construction of a sub-range over an existing immutable `Bytes` (later: `Vec`, `String`).
2. No new unsafety: a view must not be able to observe a mutation, dangle, or read out of bounds.
3. No new type-system machinery — no regions, no lifetimes, no rank-2 anything.
4. The retention cost must be *visible*, with an explicit detaching copy available.

**Non-goals.**
1. **No views over `MutVec`.** Aliased mutable memory with no borrow checker is exactly Go's
   footgun, and Sprout has none of Go's defences (§4).
2. No growth through a view. Growth stays with `MutVec`.
3. No change to `Vec`, `List`, `Bytes` or `String` value semantics.
4. Not a general "span over arbitrary memory" abstraction (C#'s `Span<T>` also covers native and
   stack memory; Sprout has no use for that).

## 4. Prior art

Every row verified against a primary source; quotes are verbatim.

| language | view type | owner | mutation through view | how lifetime is made safe |
|---|---|---|---|---|
| Go | `[]T` — *fused with the owner* | `[]T` | yes, silently | nothing; convention |
| Rust | `&[T]` / `&mut [T]`, `{ptr, len}` | `Vec<T>`, `Box<[T]>` | only via exclusive `&mut` | borrow checker + lifetimes |
| Zig | `[]T`, "a fat pointer … and a length" | allocator-owned | yes | bounds checks only |
| Swift | `Substring`, `ArraySlice` | `String`, `Array` | copy-on-write | value semantics + COW |
| C# | `ReadOnlySpan<T>` | `T[]`, `Memory<T>` | yes (`Span<T>`) | *restrict where a view may live* |
| Java | **none — removed** | `String` | n/a | gave up; `substring` copies |

**Go** (spec, *Slice types*): a slice is "a descriptor for a contiguous segment of an underlying
array"; it "shares storage with its array and with other slices of the same array … changing an
element of a slice will change that element in the underlying array for all slices that share the
array". The header is "a pointer to the array, the length of the segment, and its capacity"
(*Go Slices: usage and internals*). The sharp edge is that `append` aliases **conditionally** —
"If it has sufficient capacity, the destination is resliced … If it does not, a new underlying
array will be allocated" (`builtin` docs) — so whether a write through the result is visible to
the original depends on a runtime capacity check. Go 1.2 added `a[low:high:max]` to cap the
capacity and blunt this; a language adding syntax to *restrict* a capability is conceding the
capability was over-broad. Retention is documented, with a manual `copy` as the fix.

**Rust** (Reference): `[T]` is "a dynamically sized type representing a 'view' into a sequence of
elements", reached as `&[T]` / `&mut [T]` / `Box<[T]>`. DST pointers "have twice the size of
pointers to sized types, since they also store metadata … Pointers to slices store the number of
elements" — so `&[T]` is `{ptr, len}`: **no capacity, no growth, no ownership.** Safety comes from
the borrow checker, which costs lifetimes in every signature.

**Zig** (Reference): `[]T` is "a fat pointer, which contains a pointer of type `[*]T` and a length";
safety is bounds checking only — "this is one reason we prefer slices to pointers".

**Swift** (TSPL, *Substrings*): "A substring can reuse part of the memory that's used to store the
original string." And the hazard, stated outright: "Substrings aren't suitable for long-term
storage — because they reuse the storage of the original string, the entire original string must
be kept in memory as long as any of its substrings are being used." The remedy is a conversion to
`String`.

**C#** (`System.Span<T>` docs): motivated *precisely* by the copy this note is about — `Substring`
"Creates a new string to hold the substring. Copies a subset of the characters", and "this
allocation and copy operation can be eliminated by using either `Span<T>` or `ReadOnlySpan<T>`".
Its lifetime answer is neither lifetimes nor GC: `Span<T>` is a `ref struct` that "is allocated on
the stack rather than on the managed heap", and cannot be boxed, cannot be a "field in a reference
type", and cannot be "used across `await` and `yield` boundaries". Heap-livable views need the
separate `Memory<T>` / `ReadOnlyMemory<T>`.

**Java** is the negative result and the most relevant row. `String` once carried `offset`/`count`
and `substring` returned a view. JDK-4513622 — *"(str) keeping a substring of a field prevents GC
for object"* — is the retention hazard as a filed bug; JDK-6924259, *"Remove
String.count/String.offset/String.hashcode"*, removed the fields in 7u6, making `substring` an O(n)
copy. The follow-up regression report JDK-7197183 measured `substring` "approximately 4x slower" and
was closed **Won't Fix**. A mature GC'd language weighed exactly this trade and chose the copy.

**The shape of the field.** Nobody disputes that a view is the right *representation*; they diverge
only on who pays for the lifetime problem: Rust puts it in the type system, C# restricts where a
view may be stored, Swift and Go document the hazard and provide a detaching copy, Java refused the
whole thing. Sprout, being GC'd with a non-moving collector, can have the Swift/Go answer for
free — **as long as the backing store is immutable**, which is why §3's non-goal #1 is a non-goal.

## 5. The constraint that decides the design

Two runtime facts, verified, fix the shape of anything we build:

1. **`String` is NUL-terminated.** `sprout_gc_alloc_cstr` allocates `len + 1` and every string
   builtin receives a `const char*` (`runtime/sprout_runtime.c:1348`). A sub-range of a string is
   *not* NUL-terminated at its end, so a `String` view cannot be passed to a single existing string
   builtin without copying. A `String` view therefore costs either a parallel `{ptr,len}` API across
   the whole string surface, or a change to `String`'s representation. **This is the dominant cost,
   and it is why `String` is not the first target.**
2. **Backing stores are plain `malloc` and are freed at sweep.** `sprout_release_payload_extras`
   does `free(v->data)` for both `SPROUT_HEAP_VECTOR` and `SPROUT_HEAP_BYTES`
   (`runtime/sprout_runtime.c:2076`). A view holding a raw interior pointer would dangle. It must
   hold the **backing handle** as a traced GC child, which is what keeps it alive — and is also
   exactly the retention hazard Swift and Java document.

`Bytes` is unencumbered by (1): `BytesVal` is `{len, data}` with no terminator contract. That makes
`Bytes` the natural first and possibly only target.

## 6. Options

**A. Go-style fused slice.** Rejected. It imports silent aliasing into a language whose sequences
are values, and `Vec`'s value semantics — relied on everywhere — would no longer hold.

**B. Rust-style borrowed slice.** Rejected as out of reach, not as undesirable.
`docs/linear-borrowing-v0.md` is explicit that Sprout is **rank-1 Hindley–Milner** and that
first-class regioned references (its Option A) were deliberately **not** taken; what shipped is
Swift-style `borrowing`/`consuming` parameter modifiers (spec §5.8). A borrowed span would drag in
that entire parked region system. And the prize is smaller here than in Rust: under a GC, the win is
"skip the allocation", not "skip the allocation *and* prove the free is safe".

**C. Immutable view value (recommended shape).** A view is a GC object holding
`{backing_handle, offset, len}`. It needs **no** borrow checking, because the backing is immutable
and cannot be observed changing; it needs **no** lifetimes, because the GC keeps the backing alive
through the traced child. Cost: Go/Swift/Java's retention hazard, mitigated by an explicit detaching
copy and a documented rule.

**D. Do nothing; keep passing offsets.** The status quo — `tcp_write_some`'s `offset` parameter.
Cheap, already proven, and it does not cost a type. Its defect is that it does not compose: every
function that wants a window must grow an offset+length pair, and the discipline is unenforced.

**Recommendation: D until §12's measurement says otherwise, then C scoped to `Bytes` only.**

## 7. Proposed design (option C, `Bytes` first)

A new stdlib type in `stdlib/bytes.sprout`; **not** a language-core type, so `docs/spec-v0.md` is
untouched — the same status `MutVec` has.

```sprout
type BytesView                                    # opaque, immutable, GC'd

export fn view(b: Bytes) -> BytesView             # O(1), whole
export fn view_slice(v: BytesView, from: Int, count: Int) -> BytesView   # O(1), see Bounds below
export fn view_length(v: BytesView) -> Int        # O(1)
export fn view_get(v: BytesView, i: Int) -> Maybe Int                    # O(1), bounds-checked
export fn view_to_bytes(v: BytesView) -> Bytes    # O(count) — the DETACHING copy

export fn view_eq(a: BytesView, b: BytesView) -> Bool                    # O(min len), in Sprout
```

Five builtins and one stdlib function: `view_eq` needs no runtime support, since it is a
`view_get` loop and is O(n) whichever side implements it.

Semantics:

- **Immutability.** `Bytes` is immutable, so two views of the same backing can never disagree. This
  is the property that removes the need for a borrow checker; it is load-bearing, not incidental.
- **Bounds.** `view_get` returns `Maybe`, so no view can read outside its backing. For
  `view_slice`, match `bytes_slice` exactly (in `runtime/sprout_runtime.c`): an over-long **or
  negative** `from`/`count` **clamps** to the backing. `bytes_slice` aborted on a negative index
  until the clamp landed; `docs/nat-refinement-v0.md` records why it clamps rather than returning
  `Maybe`, and why a `Nat` parameter was rejected for now.
- **Composition.** Slicing a view is O(1) and never nests: the result points at the *original*
  backing with an adjusted offset, so a loop that re-slices n times retains one buffer, not n.
- **Retention.** A view keeps its whole backing alive. `view_to_bytes` is the documented escape,
  and the rule to write down is Swift's, in Sprout's words: *a view is for the span of a
  computation; store a `Bytes`, not a `BytesView`.*
- **No mutation, ever.** There is no `view_set`. `MutVec` gets no views (§3).

## 8. Impact

**Syntax.** None. No new keywords, no slicing operator. (A `b[i:j]` sugar is deliberately excluded:
it would invite the Go reading, where the same syntax also grows and aliases.)

**Semantics.** None to existing constructs. `BytesView` values are ordinary immutable values with
structural equality over their contents (`view_eq` compares the spans, not the offsets — two views
of different backings with equal bytes are equal).

**Type system.** None. `BytesView` is a nullary opaque type; no variance, no regions, no new
constraint forms, no effect-system interaction (all operations are pure — the backing cannot
change, so reading a view needs no `!{IO}`).

**Error messages.** One new class, from `view_to_bytes` being absent where a `Bytes` is wanted:
`expected Bytes, found BytesView`. The fix is mechanical and nameable, so the diagnostic should
suggest `view_to_bytes` by name rather than only reporting the mismatch.

**Compatibility and migration.** Purely additive. Nothing existing changes type or cost.
`bytes_slice` stays exactly as it is — the copying version remains correct and stays the default.
Existing offset-threading APIs (`tcp_write_some`) are *not* migrated in v0; if views prove out,
the follow-up is to add a view-taking overload and leave the offset form in place.

## 9. Implementation overview

**Runtime.** One new heap kind, `SPROUT_HEAP_SLICE`, whose payload is
`{long long backing; size_t offset; size_t len;}`. Five switches over the kind must gain a case
(`runtime/sprout_runtime.c:1074`, `:1919`, `:1949`, `:2083`, `:2443`):

| switch | new case |
|---|---|
| payload size | `sizeof(SliceVal)` |
| child count | `1` — the backing handle |
| child value | index 0 → `backing` |
| release extras | nothing (no owned `malloc`) |
| live-object census | its own counter |

Plus §7's five builtins (everything but `view_eq`). Each is O(1) except `view_to_bytes`, which is
O(count). Note this is a **new
builtin family and needs explicit approval** under AGENTS.md §Builtin vs Stdlib — it cannot be
written in Sprout, since it is a representation, not a function.

**Stdlib.** `stdlib/bytes.sprout` gains the externs and a thin export surface. No prelude change.

**Phasing.**
1. Runtime kind + §7's five builtins.
2. `stdlib/bytes.sprout` surface, `view_eq`, and tests.
3. *Only then*, and only if measured: consider `Vec a` views (same shape, `VectorVal` backing) and
   `String` (which first requires resolving §5's NUL problem — treat as a separate design note).

**GC note.** The backing is reachable only through the view's child slot, so the view must be
rooted at every safepoint like any other managed value; `docs/compiler-internals.md`'s type-aware
rooting rules apply unchanged. Worth flagging against `BACKLOG.md`'s open item on the
object-count-blind GC trigger: a view is one *tiny* object pinning an arbitrarily large `malloc`
block the trigger already cannot see, so views make that existing mis-accounting measurably worse.
That item should be fixed first or the retention hazard compounds silently.

## 10. Tests

TDD, per AGENTS.md §Code and Testing. Written and confirmed failing before any runtime edit:

1. `view_slice` of a view is O(1) and yields the same bytes as the equivalent `bytes_slice`
   (differential against the existing copying implementation, over a table of ranges).
2. Bounds: zero-length, past-the-end, whole-backing and negative ranges clamp identically to
   `bytes_slice`.
3. Retention: a view of a large backing, held across a forced collection, still reads correctly —
   the regression test for "the backing was swept out from under a view".
4. `view_to_bytes` round-trips, and the result is independent (the backing becoming unreachable does
   not affect it).
5. Equality: views of different backings with equal spans compare equal; equal offsets over
   different content compare unequal.
6. A GC-safety test in the shape `stdlib/compiler/` uses: allocate in a loop while holding views, to
   catch a missing root.

## 11. Docs and spec

`docs/spec-v0.md` is **not** touched — `BytesView` is a stdlib type, exactly like `MutVec`
(`docs/growable-mutvec-v0.md` records the same reasoning). What changes:
`docs/builtins-reference.md` gains the five builtins with their complexities, this document gets a
"what shipped" section, and the retention rule from §7 goes in both. Status on arrival:
**experimental**, not normative.

## 12. The measurement gate

This note exists so the design is ready if it is ever needed. It is not needed yet, and shipping it
speculatively would be building an optimisation without a measurement:

- The hottest suspected case is **already fixed**. `stdlib/compiler/lexer.sprout:10` takes token
  text with `str_slice_bytes`, the O(len) byte-indexed form — not the codepoint-indexed
  `str_slice`. The lexer is not paying the cost this note is about.
- The remaining 42 `str_slice` call sites across `stdlib/compiler/` are **short-input** work, not
  bulk text: suffix/prefix stripping on identifiers and on `@fwd:` / `@super:` / alias dict keys
  (`infer.sprout`, 11 sites), module path handling (`module_loader.sprout`, 7), and line/column
  handling (`source.sprout`, 4). Some sit inside constraint solving and so run often, but
  `source_len` there is an identifier, not a file — which is the case *against* a view: it would
  replace a short `memcpy` with an allocation plus a retained backing.

**Before any of §9 is written**, hand-apply the offset-threading trick (option D) to one measured
hot path, and show a real improvement. Only if D's win is real *and* D's lack of composability is
what blocks the next step does C earn its runtime kind.

The honest reading of the evidence above is that **no such path has been found yet**. The one
place a view would plainly pay — a large `Bytes` payload windowed repeatedly — is the write loop,
and that one is already solved by an `offset` parameter. So the measurement to run is not "how
slow is slicing" but "is there any site where the offset trick is unavailable or unbearable". If
the answer stays no, this document's conclusion is **do nothing**, and that is a fine outcome for
it. The `str_slice` codepoint walk should be fixed on its own terms — its backlog entry already
proposes leaving it as the documented-cost convenience — not used as a pretext for a view type.
