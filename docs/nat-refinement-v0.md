# A non-negative integer type — why not yet

Status: **parked**, on blocker 3 alone since 2026-09-12 — blockers 1 and 2 were cleared by wrap
constructor hiding. Not normative.

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

## Why it stays parked: blocker 3, and the relocation argument below

Blocker 3 alone is disqualifying — a `Nat` you cannot add to or subtract from is not a numeric
type — and the section after it is independent of all three blockers. Both survive the change
above, so the verdict is unchanged; only two of its three supports are gone.

## Even with all three, `Nat` relocates the check rather than removing it

`Nat` is not closed under subtraction, and every hazardous site here *is* a subtraction —
`bytes.length(raw) - body_start`. So `Nat - Nat` must be one of:

| Choice | Prior art | Verdict |
|---|---|---|
| Truncating (monus): `5 - 7 = 0` | Agda, Idris `Nat` | Clamping in a costume — silent, and harder to see than a clamp inside `slice` |
| `-> Maybe Nat` | Rust `usize::checked_sub` | Honest; the check lands at the subtraction, where the caller has context |
| `-> Int`, with `nat` to convert back | — | A partial conversion at the call site; where we started |

Only the second is real, and it is the same number of checks as today, moved earlier. That is the
point of #4 — a boundary check once, then a type that cannot be invalid — but it is placement, not
elimination, and it should be argued on that basis.

## What was done instead

1. **`str_slice`/`bytes_slice` clamp a negative `start` or `count` to empty**, matching `vec_slice`,
   which already did. Only a null input still aborts. An index is caller input, not a violated
   internal invariant, which is the line `docs/guidelines.md` #2 draws around `panic`.
2. **`bytes.take`/`bytes.drop` added**, mirroring the `string` pair. Most hazardous sites were
   `drop` spelled longhand as `slice(v, n, length(v) - n)` because `stdlib.bytes` had no `drop`.
   Removing the subtraction is #3 achieved with today's language: the count cannot go negative
   because the caller never computes one.

Remaining: the `string`-side sites that still spell `drop` as a subtraction (`stdlib/tui/keys.sprout`,
`stdlib/repl.sprout`) can move to `string.drop`. Behaviour-preserving, not urgent.

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
