# An O(1) byte read on String — v0

**Status — landed 2026-11-02.** Item 1 of [runtime-memory-safety-v0.md](runtime-memory-safety-v0.md)
§Blocked on a missing primitive. Measured in
[results-2026-11-02-str-byte-at.md](../bench/results-2026-11-02-str-byte-at.md). Non-normative:
`spec-v0.md` lists only the `no_prelude` floor, which this builtin is not on.

## 1. Problem

Sprout code cannot read one byte of a `String`. It has three workarounds, and each costs:

- **Copy into `Bytes`, then scan.** `bytes_from_utf8(s)` allocates and copies all of `s`; the scan
  then reads through `bytes_get`. `compiler/source.sprout` did it five times, once to decode only
  the first codepoint (`decode_codepoint_at(bytes_from_utf8(s), 0)`).
- **Probe with a one-byte prefix compare.** `str_starts_with_at_byte(s, i, "7")` asks "is byte `i`
  a `7`" through a string compare. 85 such probes: 66 in `json.sprout`, 11 in the prelude, 4 each
  in `uuid.sprout` and `compiler/source.sprout`. `json.is_num_byte_at` makes up to 15 of them to
  classify one byte.
- **Keep the scan in C.** `str_len`, `str_char_at`, `str_slice`, `str_find`, `split_words` and
  `str_split_lines` stay builtins partly for this reason.

`str_char_at_byte` read a `Char` at a byte offset until 2026-06-28. It was removed (runtime review
F3, `docs/archive/runtime-review-2026-06-27.md`) because it had no upper bound, and a `strlen`
bound made lexing O(n²); the `Bytes` cursor that replaced it cost ~7% of a self-compile. The
obstacle is gone: every String the runtime or compiler produces carries an 8-byte header whose aux
is its byte length ([string-header-invariant-v0.md](string-header-invariant-v0.md)), and
`sprout_cstr_byte_len` reads it in O(1).

## 2. Goals and non-goals

Goals:
- Read byte `i` of a String in O(1), bounds-checked, with no allocation when matched.
- O(1) under `SPROUT_GC_HDRCHECK=1` too, which CI runs every test under.
- At least as fast as the probes and copies it replaces, measured (§6).
- No offset reads outside a String, for every String the guarantee in §4 covers.

Non-goals:
- A `Byte` type. Bytes are `Int` 0–255, as `bytes_get` returns.
- Moving `str_len`, `str_slice` and the rest. Each move still faces the no-slower rule on its own
  (§7).
- An inline lowering. It needs new IR ops shared with `Bytes` reads, so it is designed in
  [inline-byte-reads-v0.md](inline-byte-reads-v0.md) (§4).
- An unchecked read. Bounds-check elimination is [bounds-check-elimination-v0.md](bounds-check-elimination-v0.md)'s job.

## 3. Prior art

| Language | Read | Returns | Out of range | Variants |
|---|---|---|---|---|
| Rust | `s.as_bytes()[i]`, `.get(i)` | `u8`, `Option<&u8>` | `[i]` panics, `get` gives `None` | checked, trapping, unchecked (`get_unchecked`, UB) |
| Go | `s[i]` | `byte` | run-time panic | trapping |
| OCaml | `String.get s i` (`s.[i]`) | `char` | `Invalid_argument` | trapping; `unsafe_get`, "for system use only" |
| Haskell `ByteString` | `index`, `indexMaybe` / `!?` | `Word8` | `index` is partial; the others give `Maybe` | checked, trapping, unchecked (`unsafeIndex`) |
| Haskell `Text` | `index` | `Char` | — | O(n): no O(1) unit read |
| Swift | `s.utf8[idx]` | UTF-8 unit | precondition | `String.Index` only; an integer offset is O(k) |
| Java | `charAt(i)` | UTF-16 unit | `IndexOutOfBoundsException` | trapping |

Sources: doc.rust-lang.org/std/primitive.str.html and primitive.slice.html; go.dev/ref/spec
§Index_expressions; ocaml.org/manual/5.3/api/String.html, and `stdlib/string.mli` (OCaml 5.3) for
`unsafe_get`; hackage.haskell.org bytestring-0.12.2.0 `Data.ByteString` and
`Data.ByteString.Unsafe` (`unsafeIndex`), text-2.1.4 `Data.Text`;
developer.apple.com/documentation/swift/string/utf8view; docs.oracle.com Java SE 21 `String`.
OCaml and `ByteString` document the read as O(1); the others do not say.

Where they diverge: trapping (Go, OCaml, Java) or total (`ByteString.indexMaybe`, Rust `get`).
Sprout's runtime review chose total for the scanner it replaced (`decode_char_at` returns `Maybe`),
and `bytes.get` is total.

## 4. Design

```sprout
# O(1): byte `i` of `s` (0–255), or Nothing when `i` is out of range.
extern fn str_byte_at(s: String, i: Int) -> Maybe Int
```

- **Total**, as `bytes_get`. `str_byte_at` joins `ast_to_ir.unboxed_maybe_extern_name`, so a
  `match`, do-bind or let-else on the result reads the byte inline
  ([inline-byte-reads-v0.md](inline-byte-reads-v0.md)) and builds no `Maybe`.
  A boxed use, such as `with_default(-1, str_byte_at(s, i))`, still allocates the `Maybe`.
  `string.byte_at_or(s, i, fallback)` mirrors `bytes.get_or`.
- **No roots around the call.** `str_byte_at_unboxed` joins `ir_rooting.is_nonallocating_unboxed_read`,
  a hand-kept list that today equals `unboxed_maybe_extern_name`'s. The boxed `str_byte_at` goes
  on no list: its `Just` allocates, as `bytes_get`'s does. The bound byte is a scalar payload
  and is never rooted.
- **C body**, in the runtime's block of unboxed variants beside `bytes_get_unboxed`, whose header
  states the no-allocation rule the rooting list relies on:
  - Load the header at `s - 8`; its kind must be CSTR. `len` is its aux; `i < 0 || i >= len`
    gives Nothing, else `(int64_t)(unsigned char)s[i]`. A String is a `char*`, and `char` is
    signed on macOS and x86-64 Linux, unsigned on Linux arm64: without the cast, `é`'s first byte
    is -61 on one and 195 on the other.
  - A null `s` fails with `"str_byte_at: null input"`, and a String with no CSTR header fails
    with `"str_byte_at: String has no header"`, in every mode. Both go through one cold function
    that never returns (`str_byte_at_fail`).
  - No `strlen`, and so no HDRCHECK cost: `str_byte_len` compares aux with `strlen` under
    HDRCHECK, which made a per-byte json parse quadratic on CI (`json.sprout`, the comment on its
    `len`).
  - A String holds no NUL (spec-v0 §4). The corrupt one `char_to_string` still makes from
    U+0000 (BACKLOG P2, "A String containing U+0000") reads as 0, as `bytes_get` does.
- **Why the body has that shape.** LLVM inlines a body only under a cost threshold (225 here).
  With the null, HDRCHECK and `strlen` paths inline it cost 340 and stayed a call even under the
  whole-program link. And a rare path that *returns* into a loop of reads stops LLVM hoisting the
  header load and kind check out of it, since the call may write memory. A rare path that never
  returns does neither. Row 2 of §6 lost until both held.
- **In the prelude**, beside `str_starts_with_at_byte`, as the byte-surface policy's second
  exception (§5). The prelude's `str_len`, `str_slice` and the rest, which item 1 exists for, can
  only be written over it there. `Maybe` is not a floor type, so it is not on the `no_prelude`
  floor.

**The guarantee and its premises.** No offset reads outside a String whose header was written by
`sprout_str_alloc`, `sprout_str_intern` or the compiler's literals: every String the runtime and
compiled code produce ([string-header-invariant-v0.md](string-header-invariant-v0.md)). A user C
extern that returns a raw `char*` as a String is outside it. `str_byte_at` fails on one, which is
stricter than `str_eq`'s `strlen` fallback; it is still misread when its byte at `s - 8` happens to
equal the CSTR kind. The read also assumes the GC never moves an object, which holds today; a
moving GC must revisit it, as the runtime's note above the unboxed variants says for the pointers
they return.

**Link setups.** Under the whole-program link (`just compile-native`, `sproutd`, through
`scripts/link_whole_program.sh`) the C body can inline into the caller. Under separate units
(`just test`'s binaries, the compiler bootstrap, `clang prog.ll runtime/*.c`) every read is a full
C call ([cross-tu-inlining-v0.md](cross-tu-inlining-v0.md) §5.2); the compiler's lexer runs there.
The read is therefore lowered inline there, with `IRLoadWord` and `IRLoadU8`, for this read and
`bytes_get_unboxed` alike ([inline-byte-reads-v0.md](inline-byte-reads-v0.md)); the C bodies serve a
boxed or function-value use.

## 5. Alternatives

- **Caller: keep copying into `Bytes`.** An O(n) allocation per scanned String, and the 85 probes
  stay.
- **Representation: an O(1) `Bytes` view of a String.** `BytesVal` is `{len, data*}`, so a view
  could point into the String. It still allocates a 16-byte object per view, and the GC would have
  to trace an interior pointer from `data`, which it does not today.
- **Trapping `-> Int`.** One branch less, and on the floor. It is partial, which is what F3
  removed.
- **Placement: `stdlib.string`, as the prelude's byte-surface comment asks.** The prelude cannot
  import it, so `str_len`, `str_slice` and the rest could never move, and the prelude's 11 probes
  stay. That is item 1's purpose, so the prelude gets a second exception.
- **`str_starts_with_at_byte` in Sprout, over `str_byte_at`.** Its `long` row is a 4096-byte
  compare in 106 ns, about 0.03 ns per byte (`bench/results-2026-11-01-str-starts-with.md`). §6
  row 3 prices a Sprout byte loop against C; unless it is near that, this stays C.

## 6. Measurements before code

On an uncommitted prototype (the C body and its extern), ≥ 15 interleaved rounds, each row under
both link setups (§4). "Wins" means the candidate's median ≤ the baseline's median × (1 + the
baseline's spread), the no-slower rule's bound.

| Row | Baseline | Candidate | Decides |
|---|---|---|---|
| 1 | `json.is_num_byte_at` by probes | one `str_byte_at`, integer compares | the builtin |
| 2 | byte count over 64 KB: `bytes_from_utf8` + `bytes_get` | `str_byte_at` loop | the builtin |
| 3 | codepoint count by C `str_len` | `str_byte_at` loop | §7, not the builtin |
| 4 | prelude `parse_int` by probes | `parse_int` over `str_byte_at` | the builtin |

**Result:** rows 1, 2 and 4 win under both setups; row 3 is 1.4–1.5× C under the whole-program
link and 4.3–4.4× under separate units ([results](../bench/results-2026-11-02-str-byte-at.md)).

The builtin goes ahead if rows 1, 2 and 4 win under both setups. If one loses under separate units
only, the builtin waits for P2. If one loses under both, it is dropped and the numbers become its
recorded reason, as for `vector_empty`. Row 3 cannot stop the builtin; it says whether §7's moves
are within reach.

## 7. What this unlocks

Callers that probed or copied: `json`, `uuid`, the prelude's `parse_int`/`parse_double`,
`compiler/source.sprout`. The first three now read with `str_byte_at`
([results-2026-11-07-byte-probes.md](../bench/results-2026-11-07-byte-probes.md)).
`compiler/source.sprout`, `bundler` and `iface_codec` read the String directly too; no copy
remains.

For `str_len`, `str_char_at`, `str_slice`, `str_find`, `split_words` and `str_split_lines` it
removes the copy, not the rule: a Sprout loop must still match a C loop. The byte buffer's
measurements ([byte-buffer-v0.md](byte-buffer-v0.md) §3) put a fill loop with inlined reads and
writes, without alias tags, at 2.6× C. These moves likely wait for P2, alias tags and bounds-check
elimination. §6 row 3 says by how much.

## 8. Spec, docs, tests and gates

Compatibility: a new name; no caller changes, no migration. Diagnostics: none new.

- `spec-v0.md`: unchanged. It lists only the `no_prelude` floor, and `str_byte_at` is off it. No
  syntax or typing change.
- `stdlib/prelude.sprout`: the extern, and the byte-surface comment says "two exceptions" and why.
- `stdlib/string.sprout`: `byte_at_or`.
- `runtime/sprout_runtime.c`: `str_byte_at` and `str_byte_at_unboxed`, the latter in the unboxed
  variants block.
- `runtime/APPROVED_BUILTINS`: both names, each with a justification. `str_starts_with_at_byte`'s
  changes from "the O(N) remaining-text slice" to the no-slower rule (§5).
- `ast_to_ir.unboxed_maybe_extern_name` and `ir_rooting.is_nonallocating_unboxed_read`: one name
  each.
- `ir_lowering.sprout`: a `decl("i128", "str_byte_at_unboxed", "i64, i64")` line in the hand-kept
  i128 list. `just cpr-declare-check` pins it to the C definition.
- `tests/stdlib/test_ir_codegen_cpr_maybe_externs.spr`: seven names, not six.
- `tests/ctor_tag_fold/reads.sprout`: a String loop, so `scripts/ctor_tag_fold_check.sh` sees the
  new read's tag fold under the whole-program link.
- `tests/conformance/type_error/no_prelude_excludes_str_byte_at`, as for `str_starts_with`.
- Docs: `builtins-reference.md`; `runtime-memory-safety-v0.md` item 1 rewritten as landed, with
  the moves pending §7.
- Golden IR: the new `declare` changes all 75 golden files; read the diff, then snapshot.
- Tests: in range, `-1`, `len`, a huge offset, an empty String, `"é"` reading exactly 0xC3 then
  0xA9, agreement with `bytes_get`, `byte_at_or`; no NUL case, since that String is corrupt
  and HDRCHECK aborts on it; an IR test that a `match`
  reads the byte inline with no root push around the read; a `str_byte_at` probe in
  `tests/stdlib/test_byte_offset_cost.spr`, so an O(n) read fails CI under HDRCHECK.
