# Effect-polymorphism policy — v0

**Status: normative for stdlib API design.** This document does not change any typing rule. It
decides *where* the language's existing `!{e}` annotation may be written in a public signature, so
that the answer stops being case-by-case.

## 1. Problem

Sprout's stdlib is inconsistent about effect polymorphism and gives false reasons for it.

`list_fold` and `list_each` carry `!{e}` on their callback. `list_map`, `list_fold_while`,
`vec_fold`, `Foldable.fold_values` and `Filterable.filter_values` do not. Nothing recorded why, and
the two places that tried to explain it are both wrong:

- `docs/filterable-v0.md` §6 attributed it to `BACKLOG` B2 — "a class method that calls itself from
  inside its own instance body fails dictionary resolution".
- `stdlib/prelude.sprout:830-832` attributed it to the dispatcher: "an effectful predicate would need
  an effect row on the class method, which the dispatcher does not carry."

Both are disproved by a fifteen-line probe (§7), and both places are rewritten in the change that
adds this document — the quotes above are what they said. Both are also the wrong *kind* of reason:
they cite an implementation limit for what is a contract decision. If the limit were lifted
tomorrow, neither tells you what the signature should then be.

## 2. Goals / non-goals

**Goals.** A rule that decides any new callback slot without a judgement call; that is stated over
the *contract* rather than the current implementation, so it does not rot; and that says plainly what
choosing `!{e}` costs.

**Non-goals.** Not a change to effect inference, unification, or enforcement — see
`docs/effect-enforcement-v0.md` and `docs/effect-subsumption-v0.md`. Not a decision about effect rows
with more than one label. Not a licence to add `!{e}` retroactively wherever it type-checks.

## 3. The rule

> **A callback slot may be effect-polymorphic exactly when the operation's published contract already
> fixes both the order and the multiplicity of that slot's invocation.**

- **Order** — which element the callback is applied to first, and in what sequence.
- **Multiplicity** — how many times it runs per element: exactly once, or a specified early stop.

Both must be pinned. If either is free, the slot stays pure.

The rule grades **per slot**, not per function. One operation may take an effectful `step` and a pure
`cond` (§5).

## 4. Why

An effect is an observable event in time. Ordering is what makes it meaningful: `print(1)` then
`print(2)` is a different program from the reverse. So a signature admitting effects into a slot is
implicitly claiming that the operation *has* a sequence to place them in.

Two failure modes follow from admitting effects where the contract does not supply one.

**Unspecified behaviour.** If the contract leaves order free, two conforming implementations run the
caller's side effects in different orders and both are correct. The caller has no way to reason.

**An accidental promise.** More likely in practice: today's implementation *does* have an order, so
the effectful version works, and the order silently becomes part of the API by observation. Every
future instance is now bound to it — including instances over containers that have no natural order
at all (§8).

The converse cost is real too and is the reason this is a rule rather than a default. Writing `!{e}`
**freezes** the order into the contract. No future instance may fold in parallel, chunk, or reorder,
because a caller may now observe the difference. Effect polymorphism is not free generality; it is a
narrowing of the implementation space, paid for by callers gaining the ability to sequence.

So the rule is: charge that cost only where it was already paid.

## 5. Verdicts for Sprout's current surface

| operation | slot | order | multiplicity | verdict |
|---|---|---|---|---|
| `Foldable.fold_values` | `step` | left fold (`prelude:758`) | once per element | **`!{e}`** |
| `Foldable.fold_while_values` | `step` | left fold, specified stop | once per visited element | **`!{e}`** |
| `Foldable.fold_while_values` | `cond` | left fold | **open by law** (`prelude:823-826`) | **pure** |
| `Functor.fmap` | `f` | **unspecified by the class** | unstated | **pure** |
| `Filterable.partition_values` | `pred` | unstated | once per element (`prelude:785-786`) | pure until order is stated |
| `Filterable.filter_values` | `pred` | unstated | unstated | pure |
| `Filterable.filter_map_values` | `f` | unstated | unstated | pure |

Two entries deserve comment.

**`fold_while_values` splits.** The prelude states, as law: "`cond` is pure and may be asked more
than once about equal values, because a non-linear instance re-checks it between substructures." That
is a deliberate freedom for tree-shaped instances, not an oversight. Its multiplicity is therefore
open and `cond` stays pure — while `step`, in the same signature, may take `!{e}`. This is the
clearest evidence the rule is doing work: it was not the pre-existing intuition.

**`Filterable` is blocked on a missing sentence, not on a decision.** `prelude:830` says filter keeps
elements "in order", but that describes the *result*, not when `pred` runs. `partition_values` pins
multiplicity ("exactly once per element") and not order. The rule cannot fire until the contract says
what it guarantees. Writing that sentence is the prerequisite, and it is a real choice — pinning
predicate order forecloses a parallel `Vec` filter.

`list_fold` / `list_each` already carry `!{e}` and are consistent with the rule: `List` fixes both.
The inconsistency was never those two; it was the generic layer above them.

## 6. Prior art

Three languages, each verified against a primary source. The first two look like they disagree.

**Haskell** — `Functor` abstracts over containers with no order (`Map k`, `Set`, trees), so `fmap`
cannot sequence effects and is pure. The ordered version was not an effectful `fmap`; it was a new
class whose defining content *is* the order. `Data.Traversable` (base-4.22.0.0): "Functors
representing data structures that can be transformed to structures of the same shape by performing an
`Applicative` (or, therefore, `Monad`) action on each element **from left to right**", and `traverse`
"evaluate these actions from left to right".

**Swift** — `Sequence` is defined by sequential iteration, so the order is in the protocol already,
and `map` is effect-polymorphic directly (`stdlib/public/core/Sequence.swift`):

```swift
public func map<T, E>(_ transform: (Element) throws(E) -> T) throws(E) -> [T]
public func filter<E: Error>(_ isIncluded: (Element) throws(E) -> Bool) throws(E) -> [Element]
public func forEach(_ body: (Element) throws -> Void) rethrows
```

`forEach` is documented "Calls the given closure on each element in the sequence in the same order as
a `for`-`in` loop."

The two agree. Haskell's `fmap` is pure because `Functor` promises no order; Swift's `map` is
effectful because `Sequence` promises one. Same rule, different abstractions.

**Rust** — confirms the consequence in §8 rather than the rule: `HashSet::iter` is documented "An
iterator visiting all elements in **arbitrary order**", and the `into_iter` example is annotated
"Will print in an arbitrary order." (Rust's `Iterator::map` takes `FnMut` and so admits arbitrary
effects, but the method's own docs do not state a call order, so it is not evidence either way.)

## 7. The false reasons, disproved

Both claims in §1 predict that an effect-polymorphic class method cannot work. It does:

```sprout
export class Walk f
  fn walk_each(g: a -> Unit !{e}, xs: f a) -> Unit !{e}

instance Walk List
  fn walk_each(g: a -> Unit !{e}, xs: List a) -> Unit !{e} = list_each(g, xs)

fn shout(n: Int) -> Unit !{IO} = print(int_to_string(n))
fn main() -> Unit !{IO} = walk_each(shout, [1, 2, 3])
```

Compiles, links and prints `1 2 3`. The B2 attribution is a misreading: B2 is about an instance body
calling *its own method*, and these instances **delegate** to a standalone helper — the shape
`Foldable List.fold_values` already uses. The dispatcher claim is simply false.

The enforcing direction holds too, which is what makes `!{e}` a contract rather than a hole. Swapping
in a caller that declares itself pure:

```
14:1: ERROR: check: `main.sneaky` performs IO but is declared pure
       — add `!{IO}` after its return type (spec-v0.md §7 rule 8)
```

## 8. Why this matters now: `Dict` and `Set`

Every `Functor` instance in the prelude today — `List`, `Maybe`, `Result`, `Vec` — happens to be
ordered. An effect-polymorphic `fmap` would work right now and look harmless.

`docs/filterable-v0.md` §7 and `BACKLOG.md` already plan `Dict`/`Set` instances. A set has no element
order; Rust documents exactly this for `HashSet` (§6). The day that instance lands, an `!{e}` `fmap`
is either non-deterministic or an accidental promise that `Set` iterates in a fixed order — and by
then it is a breaking change to take back.

The class contract is the only place that decision can live, and it has to be made before the
instance exists, not after.

## 9. Evidence

| claim | how established |
|---|---|
| `!{e}` class method compiles and runs | executed, stage-1 (§7) |
| `!{e}` class method enforces at the caller | executed, `--phase check` (§7) |
| B2 / dispatcher reasons are false | executed (§7) |
| `cond` multiplicity is open | read from `prelude:823-826`, which states it as law |
| Haskell, Swift, Rust rows | primary sources, quoted (§6) |

## 10. Follow-on work

Applying the rule to `Foldable` is a 13-signature change (measured: `fold_values`,
`fold_while_values`, both instances, the `fold`/`fold_while` combinators, and the `list_`/`vec_`
helpers they delegate to). Measured breakage across 127 in-tree and 199 downstream files: zero.

That change is **not** made here, and should land after `docs/effect-subsumption-v0.md`, which closes
the escapes that let a declared effect be dropped when a function is passed as a value. Tracked in
`BACKLOG.md`.

`Filterable` needs its order sentence written before the rule can decide it (§5).
