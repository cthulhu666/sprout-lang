# `stdlib/unicode` — width and grapheme clusters — v0

Status: **implemented.** Split out of TUI milestone M2 because it is a stdlib capability in its own
right, not a TUI detail. Non-normative: adds no syntax, no typing rule and no builtin.

Two decisions below changed during implementation, both because the language does not offer what the
design assumed; §3.6 records them.

Pinned to **Unicode 17.0.0** (the latest published; 18.0.0 is not up as of 2026-09-06).

## 1. Problem

Sprout has no Unicode property data at all. The runtime validates UTF-8 (`utf8_validate`) and can
build a `Char` from a codepoint, and that is the whole of it. Nothing in the tree can answer the two
questions any terminal renderer or text editor must answer per character:

- **How many columns does this occupy?** Not all codepoints are one column. CJK ideographs and most
  emoji take two; combining marks take zero.
- **Which codepoints form one user-perceived character?** `é` may be one codepoint or two (`e` +
  U+0301); a flag is two regional indicators; 👨‍👩‍👧 is three emoji joined by ZWJ.

Without the first, a renderer's column arithmetic silently desynchronises: one wide character
displaces everything after it on the line, including in the diff engine that decides where to move
the cursor. Without the second, a cluster is split across cells that can be independently
overwritten.

## 2. Goals and non-goals

**Goals.** Column width per character and extended grapheme cluster segmentation, both **pure**, both
driven by generated tables, validated against the Unicode Consortium's own conformance data. (Both
are keyed on `Int` codepoints rather than `Char`; §3.6.)

**Non-goals.** Normalization, case mapping, collation, bidi, and word/sentence/line breaking. Each
is its own UAX with its own tables; none is needed by a terminal renderer. This module is
deliberately the two properties a fixed-width grid requires and nothing else.

## 3. Decisions

### 3.1 Which codepoints are two columns

From UAX #11, quoting the definitions:

- **Wide (W)** — *"All other characters that are always wide… (such as the Unified Han Ideographs or
  Squared Katakana Symbols)."*
- **Fullwidth (F)** — *"characters that are defined as Fullwidth… by having a compatibility
  decomposition of type `<wide>`"*.
- **Halfwidth (H)** — *"having a compatibility decomposition of type `<narrow>`"*.
- **Narrow (Na)** — *"All other characters that are always narrow"*.

So **two columns is exactly W ∪ F**. Halfwidth is *narrow* despite the name — worth stating,
because it is the obvious thing to get wrong (an early summary of this same report asserted H was
double-width; the definition above is what settles it).

**Ambiguous (A) is one column.** The report: *"Ambiguous characters require additional information
not contained in the character code to further resolve their width"*, defaulting to narrow when
context cannot be established. A library cannot establish that context, so it does not try.

**Zero columns** is not an East Asian Width question at all — it is general category `Mn`, `Me`
(combining marks) and `Cf` (format). This follows the conventional `wcwidth(3)` treatment.

**Control characters return 0**, and the renderer is expected to filter them. `wcwidth` returns −1
here, but Sprout has `Maybe` and an error channel for one arm of a three-valued function is worse
than a documented convention; a control character does not advance the cursor, so 0 is also true.

### 3.2 Grapheme clusters follow UAX #29 exactly, GB9c included

The full extended rule set — GB1–GB9c, GB11–GB13, GB999 (GB10 and GB14–GB998 do not exist):

```
GB1   sot ÷ Any                    GB9   × (Extend | ZWJ)
GB2   Any ÷ eot                    GB9a  × SpacingMark
GB3   CR × LF                      GB9b  Prepend ×
GB4   (Control | CR | LF) ÷        GB9c  InCB=Consonant [InCB=Extend InCB=Linker]*
GB5   ÷ (Control | CR | LF)              InCB=Linker [InCB=Extend InCB=Linker]*
GB6   L × (L | V | LV | LVT)              × InCB=Consonant
GB7   (LV | V) × (V | T)           GB11  ExtPict Extend* ZWJ × ExtPict
GB8   (LVT | T) × T                GB12  sot (RI RI)* RI × RI
GB999 Any ÷ Any                    GB13  [^RI] (RI RI)* RI × RI
```

GB9c (Indic conjunct break, needing the `InCB` property from `DerivedCoreProperties.txt`) is
included rather than skipped. It is the newest and most skippable rule, and skipping it would mean
failing conformance cases we are otherwise in a position to pass.

GB12/GB13 need a count of preceding regional indicators, and GB11 needs "was there a ZWJ preceded by
Extended_Pictographic"; both make this a small state machine over the string rather than a pure
pairwise predicate.

### 3.3 Hangul LV/LVT are computed, not tabled

`GraphemeBreakProperty.txt` spends **399 ranges on LV and 399 on LVT** — more than half the file —
enumerating the Hangul syllable block. They are algorithmic: within `AC00..D7A3`, a syllable is LV
when `(cp − 0xAC00) % 28 == 0` and LVT otherwise. Computing them removes ~800 of ~1,430 ranges.

### 3.4 Tables are string literals, searched in place

This is forced by a measured compiler limit, filed in `BACKLOG.md` (*"A large list/`Vec` literal is
not a usable way to ship a data table"*): a literal of N `Int`s costs O(N²) compiler memory
(2,000 elements → 4.6 GB) and stops compiling entirely at ~6,000 with `GC root pool exhausted`. The
same data as a string constant is **218 IR lines regardless of size**.

Encoding, chosen so the table can be searched **without decoding it**:

- Each codepoint is **4 characters, base-62**, digits then uppercase then lowercase — an alphabet
  whose ASCII order matches numeric order, so lexicographic comparison *is* numeric comparison.
- Each entry is `lo(4) + hi(4) + value(1)` = 9 characters, entries sorted by `lo`.
- Lookup is a binary search over entry index, slicing 4 bytes per probe. **Pure** — no decode step,
  no cached mutable state, so `codepoint_width` stays a pure function.

A decode-once-into-a-`MutVec` cache would be faster per lookup but would make every caller `!{IO}`,
which would spread into the TUI's pure rendering path. The cost of not caching is ~11 four-byte
slices per non-ASCII lookup, and an **ASCII fast path** removes even that for the overwhelming
majority of real text.

The fast path is `0x20 <= cp < 0x7F`, *not* the `cp < 0x0300` this section originally proposed. The
wider bound is wrong: U+00AD SOFT HYPHEN is `Cf` and U+0000..001F are `Cc`, so both are zero-width
and both sit below U+0300. Corrected during implementation, before it could ship.

The encoder and the search share one module (`stdlib/unicode/lookup.sprout`) and the *generator
imports it* rather than reimplementing base-62. A drifting pair would emit a table that looks
well-formed and answers wrongly, which no test of the output alone would catch.

Tables are **chunked across several literals of a few KB each**, because lexing one large string
literal is itself superlinear (measured: 80 KB source → 1.9 GB compile RSS; 8 KB → 49 MB).

### 3.5 The UCD is not vendored; the generated tables are

The generator is written **in Sprout**, reading the UCD files with M1's `stdlib.fs` — dogfooding,
and no new toolchain dependency.

Committed: the generator, the generated `tables.sprout`, and the generated conformance test.
Not committed: the ~10 MB of UCD source files. `scripts/fetch_ucd.sh` pins the version, the URLs and
the **SHA-256 of each of the six inputs**, so regeneration is reproducible without the repo carrying
the data — a checksum is proof, where a versioned URL is only a promise. `just gen-unicode-tables`
fetches, verifies, builds the generator and rewrites both generated files; a fresh fetch reproduced
both **byte for byte**.

Moving to a new Unicode version means updating the pins in `scripts/fetch_ucd.sh` and the version
here together, then rerunning the recipe.

CI stays hermetic because the *tests* are committed, not because the inputs are.

### 3.6 The API is over codepoints, and `graphemes` cannot carry U+0000

Two changes the language forced, both found by the data rather than by reading:

**No `Char -> Int`.** Sprout has `char_from_codepoint` and no inverse anywhere, so
`char_width : Char -> Int` as §2 originally specified is not implementable: a `Char` can be compared
but not measured. The primary API is therefore `codepoint_width : Int -> Int` and
`cluster_sizes : List Int -> List Int`, with `stdlib/unicode/utf8.sprout` decoding a `String`'s bytes
to codepoints. Filed as a language gap in `BACKLOG.md`.

**A Sprout `String` cannot hold U+0000.** It is a NUL-terminated C string, so the conformance case
`0D 00` (CR, NUL) silently became a one-codepoint string. This was caught because the *stub*
implementation — which splits every codepoint unconditionally — reported one cluster where two were
expected, an answer no algorithm could produce. Had the suite been written against
`graphemes : String -> List String`, every NUL case would have tested a shorter string than intended
and passed. `graphemes` is kept as a documented-lossy convenience over the codepoint API.

## 4. Modules

| Module | Contents |
|---|---|
| `stdlib/unicode/tables.sprout` | **generated** — the five encoded property tables |
| `stdlib/unicode/lookup.sprout` | base-62 encoding + the binary search; imported by the generator too |
| `stdlib/unicode/utf8.sprout` | `codepoints : String -> List Int`, `from_codepoints : List Int -> String` |
| `stdlib/unicode/width.sprout` | `codepoint_width : Int -> Int`, `string_width : String -> Int` |
| `stdlib/unicode/grapheme.sprout` | `cluster_sizes : List Int -> List Int`, `graphemes : String -> List String`, `gcb : Int -> String` |
| `tools/gen_unicode_tables.sprout` | the generator |
| `scripts/fetch_ucd.sh` | pinned, checksum-verified UCD download |

## 5–7. Syntax, type-system and error-message impact

None. Ordinary Sprout over existing prelude, `stdlib.string` and `stdlib.fs` surface.

## 8. Compatibility

Purely additive; nothing existing changes behaviour. No builtin, so `runtime/APPROVED_BUILTINS` is
untouched, and no seed refresh was needed — `stdlib/unicode` is outside `compile_driver`'s import
closure. That was stated as a prediction to be settled by measurement, not assumed, because the same
prediction was wrong for `stdlib.fs` in M1; here it held. `just verify-bootstrap-fixed-point` passes
with the seed untouched, and `just ir-golden-diff` reports no differences.

## 9. Tests

- **`tests/stdlib/test_unicode_grapheme_conformance.spr` — generated from `GraphemeBreakTest.txt`,
  766 cases, all passing.** This is the point of the milestone: the Unicode Consortium's own
  conformance data, not hand-picked examples. Cases are encoded as strings, not list literals, for
  the same compiler reason as §3.4. A stub that broke between every codepoint scored 248/766, so the
  suite discriminates.
- `tests/stdlib/test_unicode_width.spr` — 41 cases, each labelled with the UCD line that decides it:
  wide (U+4E00), fullwidth (U+FF21), halfwidth (U+FF66 — *narrow*, the case most likely to regress),
  ambiguous (U+00A1 — narrow), combining (U+0301 — zero), format (U+200B — zero), `Mc` vs `Mn` on the
  adjacent U+11000/U+11001, the W∩Mn overlap of §3.1, block boundaries either side of U+FF61, and the
  ASCII fast path.
- `tests/stdlib/test_unicode_tables.spr` — 23 cases over the shipped table, not over the generator's
  intent: base-62 round-trips and stays order-preserving across the range and at every carry point,
  and each of the five tables is sorted, non-empty, disjoint and in range. Verified to discriminate
  by swapping one entry's `lo`/`hi` in `tables.sprout`, which turned the scan red and named the
  offending index.

## 10. Follow-ups

- Terminals disagree on Ambiguous width and some allow configuring it; §3.1 fixes it at 1. Revisit
  only with a concrete consumer.
- The kitty keyboard/graphics protocols and `wcwidth` disagree on some emoji ZWJ sequences' *rendered*
  width. §3.2 segments them correctly; the total-width-of-a-cluster question is deferred to the
  renderer.
