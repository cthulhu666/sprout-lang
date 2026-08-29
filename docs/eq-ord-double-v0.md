# `Eq Double` / `Ord Double` — design

Status: **approved and implemented** (2026-08-29). Option **A** with **NaN
greatest** — total `Eq Double` / `Ord Double` in the prelude, comparison
operators untouched.

Supersedes the open question in `numeric-types-v1-draft.md` §7.1, whose framing
rests on a premise that is false for Sprout — see §4.

## 1. Problem

`Double` had exactly one instance in the whole stdlib: `ToString`. Three verified
failures on `bd909062`, the commit this change is built on:

```
type Reading = (label: String, value: Double) deriving (Eq, Ord)
  3:1: ERROR: check: No instance of Eq for Double in instance method eq

member(1.0, [1.0, 2.0])
  4:25: ERROR: check: No instance of Eq for Double in function main.main

minimum([3.0, 1.0, 2.0])
  4:16: ERROR: check: No instance of Ord for Double in function main.main
```

The shape this gives the prelude: **`Double` is the only primitive that can be
filtered and searched but not compared, deduplicated, or extremised.**

The gap also grows on its own. `minimum`/`maximum`/`min_by`/`max_by` landed
2026-08-27 on `where Ord k`; `member` landed 2026-08-29 on `where Eq a`. Nobody
decided to leave either broken on `Double` — every `Eq`/`Ord`-constrained
combinator falls in by default. And `deriving (Eq, Ord)` is impossible for **any**
record or ADT with a `Double` field, which is every user type carrying a
coordinate, a mass, or a score.

Downstream, `uncharted-suns` carries a hand-written
`fn min_by(score: a -> Double, xs: List a)` in `grimward/combat.sprout` whose own
comment names the prelude gap as its reason to exist, and `grimward/mods.sprout`
declines to derive on two types for the same reason, in a module where every
sibling ADT derives `Eq`.

> **Corrected 2026-08-29, after the change landed and the downstream cleanup was
> done.** This paragraph originally claimed **five** hand-folds — adding
> `render_vista`, `stations`, `economy` and `hydrology`. That count came from
> grep hits rather than from reading the code, and only the `combat` one is a
> `min_by`. See §8 for what the other four actually are and why none of them is a
> prelude swap. The `deriving` half of §1 was always the larger consequence, and
> it stands unchanged; the fold count was the weaker argument and was overstated.

## 2. Goals

- `deriving (Eq, Ord)` works for types with `Double` fields.
- The `Eq`/`Ord`-constrained prelude surface (`member`, `minimum`, `maximum`,
  `min_by`, `max_by`, `vec_sort`, `Dict` keys) works on `Double`.
- `compare` on `Double` is **total** — it returns an `Int` for every pair of
  inputs, and the order it defines is consistent.
- The `==`, `!=`, `<`, `<=`, `>`, `>=` operators keep IEEE semantics **exactly**.

## 3. Non-goals

- Changing what any operator means on `Double`. Not a compatible-shape goal — a
  hard constraint.
- A `Numeric`/`Num` class, or anything else from `numeric-types-v1-draft.md`.
- `PartialEq`/`PartialOrd` as language concepts.
- Exact `Double` printing, hex-float literals, or a `%` on `Double`.

## 4. The premise that makes this easier than the backlog says

The backlog and `numeric-types-v1-draft.md` §7.1 both frame this as "any total
`Ord Double` is a lie, because NaN is unordered". That is true when the class and
the operator are the same thing. **In Sprout they are already different code
paths, and neither can reach the other.**

Verified in the compiler source:

- `infer.check_eq` (`infer.sprout:3466-3474`) unifies the operands, then tests
  `is_primitive_eq_type` on the resolved type and returns a `TBinary` directly.
  `is_primitive_eq_type` (`:3393-3399`) lists `Double`. The `Eq` class fallback
  (`eq_via_class_method`) is in the `else` branch and is never reached for
  `Double`.
- `infer.check_compare` (`:3362-3391`) unifies the operand type against `Int`,
  then `Char`, then `Double`, and hard-errors otherwise. It has no class fallback
  at all, for any type.

Verified in emitted IR, from one compile with both instances in scope
(§9 spike). Same file, adjacent functions:

```llvm
define i64 @$entry.op_nan_eq() {              ; the == operator
  %t$2 = fcmp oeq double %t$2$fa, %t$2$fb     ; no call; instance ignored
}
define i64 @$entry.cls_nan_eq() {             ; the eq class method
  %t$2 = call i64 @__tc_Eq_Double_eq(i64 %t$0, i64 %t$1)
}
```

`stdlib/test.sprout:103-108` already records this split in prose, as the reason
`check_approx` exists.

So the question is not "should `Double` be ordered". `<` already orders it, with
IEEE semantics, and that does not change. The question is **what the currently
empty class slot should contain** — and a class slot whose method is
`compare : Int` cannot hold a partial order, because there is no third answer to
return.

## 5. Prior art

Every row verified against a primary source; see §5.1 for the exact ones.

| Language | Operator `==` / `<` | Class / named comparison | Where NaN sits |
|---|---|---|---|
| **Haskell** (GHC base) | IEEE | **same** `Ord Double` | `compare` falls through to `GT` |
| **Java** | IEEE | `Double.compareTo`, total | equal to itself, **greatest** |
| **OCaml** | IEEE | `Float.compare`, total | equal to itself, **least** |
| **Julia** | IEEE | `isless` (what `sort!` uses), total | **after** regular values |
| **Rust** | IEEE (`PartialOrd`) | no `Ord`; opt-in `f64::total_cmp` | IEEE totalOrder, by sign bit |
| **Swift** | IEEE (`Comparable`) | `isTotallyOrdered(belowOrEqualTo:)` | total, incl. signed zeros |

Two things this settles.

**Separating the operator from the total order is the consensus, 5 of 6.** Only
Haskell conflates them, and GHC's own source comments concede the result is
non-lawful: `Eq Double` "does not satisfy reflexivity" and "does not satisfy
substitutivity". Its `compare` is literally `if x <## y then LT else if x ==## y
then EQ else GT`, so `compare NaN x` returns `GT` by falling off the end. That is
the outcome to avoid, and it is the one the backlog was worried about — but it is
Haskell's *structure* that causes it, not the existence of a total order.

**Where the total order lives is the real divergence.** Rust and Swift put it in
a standalone function and ship no class instance; Java, OCaml and Julia put it in
the class/interface that sorting uses. Sprout's `compare : a -> a -> Int` is
Java's `compareTo` shape, and `vec_sort` already dispatches through `Ord`, so the
Java/OCaml/Julia placement is the one that fits without inventing machinery.

NaN's position is genuinely unsettled: greatest (Java, Julia), least (OCaml), by
sign bit (Rust, Swift). No consensus to defer to.

### 5.1 Sources

- Haskell — GHC `libraries/ghc-internal/src/GHC/Internal/Classes.hs`,
  `instance Eq Double` / `instance Ord Double` and their doc comments.
- Java — `java.lang.Double` API docs (JDK 21), `compareTo`: "imposes a total
  order … A NaN is *unordered* with respect to other values … This method chooses
  to define `Double.NaN` to be equal to itself and greater than all other
  `double` values"; and "positive zero … to be greater than negative zero".
- OCaml — `Stdlib.Float` (manual 5.3), `compare`: "treats `nan` as equal to
  itself and less than any other float value". `min`/`max`: "returns `nan` when
  `x` or `y` is `nan`".
- Julia — `Base` docs, `isless`: "Values that are normally unordered, such as
  `NaN`, are ordered after regular values"; "the default comparison used by
  `sort!`". `isequal`: "treats all floating-point `NaN` values as equal to each
  other, treats `-0.0` as unequal to `0.0`".
- Rust — `std::primitive::f64`: `PartialEq`/`PartialOrd` only, no `Eq`/`Ord`;
  `total_cmp` (stable 1.62); `max`/`min` return the non-NaN operand.
- Swift — SE-0067 "Enhanced floating point protocols": "NaN compares not equal to
  anything, including itself"; `isTotallyOrdered` "provides a total order on all
  values of type `Self`, including non-canonical encodings, signed zeros, and
  NaNs", separated because it "is used much less frequently than the usual
  comparisons".

## 6. Options

**A — Total `Eq`/`Ord Double` in the class; operators untouched.** The
Java/OCaml/Julia placement. Two prelude instances, ~12 lines, no compiler change.

**B — Add a `PartialOrd` class as a superclass of `Ord`.** The stated preference
in `numeric-types-v1-draft.md:384`. **This does not deliver the request.** It
formalises the exclusion: `Double` gets `PartialOrd`, `Ord` still has no
instance, so `minimum`/`maximum`/`min_by`/`max_by` still reject `Double` unless
all four are re-signed to `where PartialOrd k` — at which point they need an
answer for NaN anyway, so the NaN question is asked a second time with a new
class in the way. It also costs a new class, a superclass edge, ten instance
re-declarations, and a `deriving` decision. Option B is worth doing if Sprout
ever wants to express "this type has no total order" in the type system. It is
not a route to min/max on `Double`.

**C — Do nothing.** Every future `Eq`/`Ord`-constrained combinator keeps
inheriting the hole silently, and `deriving` stays impossible for a large class
of ordinary user records.

**Chosen: A**, with NaN greatest (§7). B stays available as its own goal if the
type system ever needs to *express* "this type has no total order"; it was
rejected only as a route to comparison-based APIs on `Double`.

## 7. Semantics as shipped

One rule, which decides every case:

> The class **agrees with the operator wherever the operator gives an answer**,
> and fills in only the hole IEEE leaves — NaN.

Consequences, none of them separate decisions:

- `eq(x, y)` is `x == y` for non-NaN. `-0.0` and `+0.0` are **equal**, because
  `==` says so. (Java and Julia distinguish them; both define equality by bit
  pattern, and Sprout's `==` does not. Following them here would make `eq`
  disagree with `==` on values the operator handles perfectly well.)
- `eq(nan, nan)` is `true`. Required: `compare` must return `0` for some pair, and
  `Ord a` has `Eq a` as a superclass, so an `eq` that said `false` while `compare`
  said `0` would be an inconsistent pair in the prelude.
- `compare(x, y)` is the usual −1/0/1 for non-NaN.
- **NaN is greatest**, equal to itself, above `inf`. Java's and Julia's choice,
  against OCaml's. The practical argument: `minimum` is the more common query
  (nearest by distance, cheapest by cost — four of the five downstream sites), and
  NaN-greatest keeps `minimum` returning a real number when a NaN contaminates the
  list, rather than returning the contamination. It also matches "NaN sorts last",
  which is what `vec_sort` users expect.

As shipped (`stdlib/prelude.sprout`). `double_is_nan` is duplicated from
`stdlib.math.is_nan` rather than imported: the prelude is bundled into every
program and cannot import a module that imports it. The `oeq` lowering of `==` is
what makes `! (x == x)` a correct NaN test — an ordered compare is false when
either side is NaN.

```sprout
fn double_is_nan(x: Double) -> Bool = ! (x == x)

instance Eq Double
  fn eq(left: Double, right: Double) -> Bool =
    if left == right then true
    else double_is_nan(left) && double_is_nan(right)

instance Ord Double
  fn compare(left: Double, right: Double) -> Int =
    if left < right then -1
    else if left > right then 1
    else if left == right then 0
    else if double_is_nan(left) then (if double_is_nan(right) then 0 else 1)
    else -1
```

## 8. Impact

**Syntax**: none.

**Type system**: none. Two instance declarations; no new class, no superclass
edge, no inference change.

**Error messages**: three diagnostics stop being emitted (§1). None gains a new
form.

> **Correction.** The pre-decision draft of this section said "no `.err`
> conformance fixture matches on them (checked)". That was wrong —
> `tests/conformance/type_error/missing_instance_in_applied_lambda.err`
> contained exactly `No instance of Eq for Double`. The check that produced the
> claim looked at whether any fixture would newly *fail to match*; it did not
> ask whether a fixture's whole point was the message's existence. See §11.

**Compatibility**: no *compiling* program changes meaning. The class is reachable
today only from code that **fails to compile**, so there is no program whose
behaviour shifts, and the operators are untouched by construction (§4).

The one way this change can break a compiling program is a **duplicate
instance**: a downstream module defining its own `instance Eq Double` or
`instance Ord Double` now collides, since two instances may not share a head
constructor (spec §8.5). Checked against `uncharted-suns`, the only real
consumer: it defines neither. It does define a local `fn min_by` over `Double`
keys (`grimward/combat.sprout:546`), but that shadows a prelude *function*, not
an instance, and shadowing was already the situation before this change.

**Interaction with the comparison-operator backlog item** (`BACKLOG.md`, `P2`,
2026-08-29): that item proposes giving `<` an `Ord` fallback so `"a" < "b"` works.
If both land, the `Double` short-circuit in `check_compare` **must stay** — a `<`
that routed `Double` through `Ord` would silently change meaning at NaN, which is
precisely Haskell's footgun. Adopting A makes that constraint explicit rather
than accidental.

**Unblocks**, none of it done here:

- `Double` `min`/`max`/`sign` in `stdlib.math` (`BACKLOG` `P3`). Deliberately left
  open, because the ordering does not settle them: C99 and Rust's `f64::max`
  **discard** a NaN operand, while a `compare`-based `max` under §7 would
  **propagate** it (NaN being greatest), and OCaml's `Float.max` propagates too.
  Two defensible definitions; whichever is written must say which and why.
- `grimward/combat`'s own `min_by` in `uncharted-suns` — a true duplicate of the
  prelude's, tie rule and all. Deleted downstream; behaviour-neutral including at
  NaN, since a NaN key loses under both a strict `<` and a total order that sorts
  NaN greatest.

  > **Correction.** §1 and the `BACKLOG` entry that motivated this work claimed
  > **five** downstream hand-folds would be retired. That count came from grep
  > hits and is wrong; reading the code, it is **one**. `game.economy.argmax5`
  > keeps the FIRST of equal keys while the prelude's `max_by` keeps the LAST
  > (`ord_gte`), so it is not behaviour-neutral. `game.stations.habitable_go` is a
  > *filtered* argmin returning an index with a sentinel, a shape `min_by` does
  > not have. `loam.hydrology`'s are effectful `MutMatrix` neighbourhood scans,
  > and `Foldable.fold_values` has no effect row, so they could not be `min_by`
  > at any point. `render_vista`'s pick fold is `!{IO}` for the same reason.
  > None of this weakens the case for the change — the `deriving` consequence was
  > always the larger half — but a motivating count should be counted.
- `deriving` on downstream types that carry a `Double` — e.g.
  `grimward/mods.sprout:29`, whose comment names this gap as the reason it does
  not derive.
- `check_eq` on `Double` for exact assertions. `check_approx` stays: a tolerance
  is still the right assertion for a computed value, and it remains IEEE
  throughout, so a NaN actual fails there where `check_eq` would pass it.

**Risk worth naming**: `eq(nan, nan)` is `true` while `nan == nan` is `false`, in
the same language. That is Java's situation exactly, and Java documents it rather
than resolving it. It is the unavoidable cost of having a total order at all;
option C is the only way to avoid it, and it pays for that with §1.

## 9. Validation

The §4 IR excerpt and the §11 golden-IR number come from a throwaway spike run
**before** the decision, so the option was chosen against measurements rather
than predictions. The spike added the §7 instances to the prelude and ran
`tests/stdlib/test_double_ord.spr`: **19 passed, 0 failed**, including `vec_sort`
over a list containing NaN. It was reverted before the design was presented; the
shipped implementation is the same code, and the same 19 pass.

## 10. Tests

`tests/stdlib/test_double_ord.spr` — written, confirmed RED on `bd909062` with
`No instance of Eq for Double in instance method eq`. Three groups:

1. **Operators must not move** — `nan == nan`, `nan < 1.0`, `nan > 1.0` all
   `false`. These passed before the change too, and are the regression cover for
   the non-goal in §3.
2. **The class is total** — `eq(nan, nan)`, `compare` at NaN vs number, number vs
   NaN, NaN vs NaN, NaN vs `inf`, and the signed-zero pair. Every one of these is
   asserted on **both** `eq` and `compare`, deliberately: `Ord a` has `Eq a` as a
   superclass, so a prelude where `eq` said `false` and `compare` said `0` for the
   same pair would be internally inconsistent, and a single-method test could not
   see it.
3. **Consumers** — `member`, `minimum`, `maximum`, `min_by` on a `Double` key,
   `deriving (Eq, Ord)` over a record with a `Double` field, and `vec_sort`
   placing NaN last.

Also probed by hand, not as a suite case: a user program declaring its own
top-level `fn double_is_nan` alongside the private prelude helper of that name.
It compiles, links, and runs, with the user's function used at their call site
and the prelude's instance still using its own — no collision, no misdispatch.
Worth checking because this change adds a new top-level name to the prelude,
which is bundled into every program.

## 11. Notes from landing it

**The bootstrap seed does not move.**
`just refresh-seed` reached its fixed point and left `bootstrap/compile_driver.ll`
byte-identical, because `compile_driver` never reaches `Eq`/`Ord Double` and the
cross-module DCE from `5db647d4` drops both instances from its IR. So this took
the `verify-bootstrap-fixed-point` + `just seed-fp-ack` path, not a reseed
commit.

That is worth distinguishing from the `AGENTS.md` caveat it superficially
resembles. That caveat — "a new prelude `extern fn` is NOT an IR-unchanged edit"
— is about **`extern` declarations**, which `ir_lowering.lower_extern_decls`
emits for every bundled prelude extern unconditionally, DCE or no DCE. An
`instance` is an ordinary definition and is subject to DCE. Adding to the prelude
is therefore not one category: externs always move the seed, definitions move it
only if the compiler uses them.

A second one, found while landing it: the negative conformance fixture
`tests/conformance/type_error/missing_instance_in_applied_lambda.spr` was
anchored on `Double` having no `Eq` instance, so this change made it **compile**
and silently stop testing anything. It is now anchored on a local type declared
without `deriving`. The general lesson is worth keeping — a negative test
anchored on a gap expires when the gap closes, and does so quietly.

**Golden IR moves exactly one file.** `just ir-golden-diff`
reports **1 difference across 60 files** — `examples/sentry_api.sprout`, which
gains `double_is_nan`, `__tc_Eq_Double_eq` and `__tc_Ord_Double_compare`: 106
insertions, **0 deletions**, three new `define`s and nothing else. The
cross-module DCE that landed in `5db647d4` drops both instances from the other
59, so an unused prelude instance costs nothing in emitted IR. `sentry_api` is
the one corpus member that imports `stdlib.json`, whose `Json` numbers are
`Double`.

The zero-deletion count is the load-bearing part, per the `AGENTS.md` triage
rule: in a purely additive change the only removed lines should be `@.str.N`
declarations and their references, so *any* other removal is existing code that
moved and needs explaining. There were none of either.
