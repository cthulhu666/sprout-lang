# A non-negative integer type — why not yet

Status: **parked**, with the blockers identified. Not normative.

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

## Three blockers, each verified by compiling a probe

**1. A `wrap`'s constructor cannot be hidden.** `wrap Nat = Int` exported from one module, then
`Nat(-5)` in another: compiles, exit 0. There is no `(..)` marker for a `wrap` and no `opaque type`.
A smart constructor `nat : Int -> Maybe Nat` therefore guards nothing — callers can bypass it.

**2. The enforceable shape is not zero-cost.** `export type Nat = | MkNat Int` *without* `(..)` does
hide its constructor (`Unknown variable: MkNat` across a module boundary), so the smart-constructor
pattern works for an ADT. But spec §5.6.1 makes a `wrap` identity at the IR level — no allocation —
while an ADT constructor is a real `sprout_make1`. An enforced `Nat` heap-allocates per index; in a
lexer loop, per token. Zero-cost **or** enforceable, not both.

**3. No arithmetic.** `Nat(2) + Nat(3)` → `+ needs Int or Double operands`. The prelude declares no
`Num` class; `+`/`-` are compiler primitives over `Int`/`Double`, and spec §8.6 records that lifting
a base type's instances through a `wrap` is unimplemented. There is no numeric-literal polymorphism
either, so even `slice(s, 3, 5)` would need an explicit constructor per literal.

The same wall was already hit for typed paths: `BACKLOG.md` asks for "zero-cost `File`/`Dir` wraps,
smart constructors `file_checked`/`dir_checked`" under **`stdlib.path` — the typed half**.

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
- **`export type` opacity is honoured on sums and silently ignored on records** — adjacent to
  blocker 1; names `opaque type` under `wrap` ergonomics as the related ruling.
- **`wrap` ergonomics follow-ups** — carries the `opaque type` request itself.

Blocker 2 is not separately filed and does not need to be: it dissolves once a `wrap` can hide its
constructor, because then the zero-cost shape is also the enforceable one.
