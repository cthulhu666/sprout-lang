# A non-negative integer type — why not yet

Status: **declined for this problem** since 2026-09-13, not merely parked. Blockers 1 and 2 were
cleared by wrap constructor hiding, and blocker 3 turned out not to bind here at all — the verdict
now rests on the combinator argument below. Not normative.

## The question

`string.slice`/`bytes.slice` took a `count` that could be negative, and aborted when it was.
`docs/guidelines.md` #3 ("make illegal states unrepresentable") and #4 ("parse, don't validate")
suggest a better answer than checking at the callee: a type whose values cannot be negative.

```sprout
export fn slice(value: Bytes, start: Nat, count: Nat) -> Bytes
```

This records why that is not buildable in Sprout today, so the next person does not re-derive it.

## The codebase already wants it

The invariant is built by hand at every site. `stdlib/http_server.sprout`, in the header parser:

```sprout
# A header-less request (HTTP/1.0-style) has the terminator right after
# the request line, so `headers_end <= header_start` — return "" rather than slicing
# a negative length (which aborts in str_slice).
fn header_block(raw: String, line_end: Int, headers_end: Int) -> String =
  if headers_end <= header_start then ""
```

Fourteen in-repo call sites compute the count as a subtraction. Each owes a hand proof that the
difference is non-negative; the ones that have it wrote it in prose.

## Three blockers, of which two have since fallen

**Blockers 1 and 2 were cleared on 2026-09-12** by wrap constructor hiding (spec-v0 §5.6.1). They
are kept here, struck through, because the reasoning that follows was built on them and a reader
needs to know which parts still load-bear.

**~~1. A `wrap`'s constructor cannot be hidden.~~** *Cleared.* It was true: `wrap Nat = Int`
exported from one module, then `Nat(-5)` in another, compiled with exit 0, because there was no
`(..)` marker for a `wrap`. There is now — `export wrap Nat = Int` publishes the type alone and
keeps the constructor and the destructor pattern module-private, so `nat : Int -> Maybe Nat` is
enforceable rather than advisory.

**~~2. The enforceable shape is not zero-cost.~~** *Cleared, exactly as the prerequisites section
below predicted.* The dilemma was that hiding a constructor required an ADT (`export type Nat =
| MkNat Int`), which is a real `sprout_make1` allocation, while a `wrap` is identity at the IR
level but unenforceable. Now the zero-cost shape **is** the enforceable one: a hidden wrap
constructor costs nothing, so an enforced `Nat` does not allocate per index.

**3. No arithmetic.** `Nat(2) + Nat(3)` → `+ needs Int or Double operands`. The prelude declares no
`Num` class; `+`/`-` are compiler primitives over `Int`/`Double`, and spec §8.6 records that lifting
a base type's instances through a `wrap` is unimplemented. There is no numeric-literal polymorphism
either, so even `slice(s, 3, 5)` would need an explicit constructor per literal.

The same wall was already hit for typed paths: `BACKLOG.md` asks for `File`/`Dir` wraps reachable
only through validating constructors, under **`stdlib.path` — the typed half**.

## Blocker 3 does not actually bind — and an enforceable `Nat` was built

The earlier verdict said blocker 3 was disqualifying. Probing it in 2026-09-13 showed otherwise: an
abstract `wrap Nat = Int` with a smart constructor, a `slice_nat` taking two of them, and a
cross-module caller all compile and run today, with the invariant enforced and nothing allocated.
Slicing never needs `Nat` *arithmetic* — the caller stays in `Int` and converts at the boundary,
which is exactly what #4 asks for. Blocker 3 blocks `Nat` as a **numeric type**; it never blocked
`Nat` as a **boundary token**, and conflating the two is what made the old verdict look settled.

Hiding the constructor also added a cost the first draft could not have seen, because it predates
the feature: every literal index now needs a call. `nat : Int -> Maybe Nat` puts a refutable binding
at each one; a total `nat_clamp` scatters clamps across call sites, which is strictly worse than the
single documented clamp inside `slice`. So the honest reading is that `Nat` is *available* and still
not *worth it* — for the reason in the next section, which never depended on any of the three.

## The real reason: `Nat` relocates the check, and a combinator removes it

`Nat` is not closed under subtraction, and every hazardous site here *is* a subtraction —
`bytes.length(raw) - body_start`. So `Nat - Nat` must be one of:

| Choice | Prior art | Verdict |
|---|---|---|
| Truncating (monus): `5 - 7 = 0` | Agda, Idris `Nat` | Clamping in a costume — silent, and harder to see than a clamp inside `slice` |
| `-> Maybe Nat` | Rust `usize::checked_sub` | Honest; the check lands at the subtraction, where the caller has context |
| `-> Int`, with `nat` to convert back | — | A partial conversion at the call site; where we started |

Only the second is real, and it is the same number of checks as today, moved earlier. That is the
point of #4 — a boundary check once, then a type that cannot be invalid — but it is placement, not
elimination.

Classifying every subtraction that feeds a `slice` in `stdlib/` and `ide/` shows what elimination
looks like. There are three shapes, and a named window covers each:

| Shape | Sites | What deletes the subtraction |
|---|---|---|
| `slice(v, n, length(v) - n)` | ~8 | `take` / `drop` |
| `slice(s, start, stop - start)` | ~4 | `slice_between(s, start, stop)` |
| `slice(s, length(s) - k, k)` | ~2 | `take_last` / `drop_last` |

`Nat` helps none of them, and on the middle shape it *hurts*: `stop - start` over two `Nat`s
reinstates the monus-vs-`Maybe` choice above, while `slice_between(s, start, stop)` has no
subtraction to get wrong. Removing the computation that can produce an illegal value is strictly
stronger than typing that computation's result.

The decisive evidence is that the codebase reinvented these privately four times before they
existed — `template.slice_between`, `lexer.slice_between`, `repl.drop_last`/`drop_last_count` and
`http.str_drop` — and never once reached for a `Nat`.

## What was done instead

1. **`str_slice`/`bytes_slice` clamp a negative `start` or `count` to empty**, matching `vec_slice`,
   which already did. Only a null input still aborts. An index is caller input, not a violated
   internal invariant, which is the line `docs/guidelines.md` #2 draws around `panic`.
2. **`bytes.take`/`bytes.drop` added**, mirroring the `string` pair. Most hazardous sites were
   `drop` spelled longhand as `slice(v, n, length(v) - n)` because `stdlib.bytes` had no `drop`.
   Removing the subtraction is #3 achieved with today's language: the count cannot go negative
   because the caller never computes one.

3. **`slice_between`, `take_last` and `drop_last` added to both `stdlib.string` and `stdlib.bytes`**
   (2026-09-13), and the private reinventions and subtraction sites migrated onto them. Two of the
   windows they name were guards in disguise: `http_server.header_block`'s `headers_end <=
   header_start` test and the `keep` clamp in its read loop both vanished, because an inverted or
   over-long window is already the empty or whole result.

Deliberately not migrated: `stdlib/http.sprout` and `stdlib/regex.sprout` reach prelude externs by
bare name and import no `stdlib.string`, so adopting these would be a dependency change; the
compiler-side copies are in `BACKLOG.md` behind a reseed.

## Prerequisites, if this is revisited

All three are already filed in `BACKLOG.md`, under these titles:

- **`wrap` instance lifting — reuse the base type's instances as the wrap type** — blocker 3. Note
  its own out-of-scope line: mixed `age + 1` with a bare literal needs numeric-literal polymorphism,
  which is a separate piece (`docs/coercions-and-literals-v1-draft.md` Case B).
- ~~**`export type` opacity …**~~ and ~~**`wrap` ergonomics follow-ups**~~ — blocker 1's
  prerequisite. **Met 2026-09-12**; both entries remain open for their other halves (record
  opacity, the auto-accessor, `opaque type`), neither of which blocks `Nat`.

Blocker 2 was never separately filed, on the reasoning that it would dissolve once a `wrap` could
hide its constructor. That is what happened — recorded here because a prediction that came true is
worth more to the next reader than one that was merely plausible.

So the live prerequisite list is one item: blocker 3.
