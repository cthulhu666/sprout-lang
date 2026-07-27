# Monad class + Maybe combinators — Design (v0)

**Status:** Experimental (alongside spec §8.5). Landed 2026-07-27.

## 1. Problem

Sprout's stdlib typeclass tower stops at **Functor → Applicative**
(`stdlib/prelude.sprout`). `Result` has a full free-fn combinator family
(`result_map`/`result_and_then`/`result_with_default`/…); `Maybe` has only
`fmap` (via its Functor instance). A census of the codebase found **239**
hand-rolled 2-arm `Maybe`/`Result` match blocks that a combinator would replace,
**191 (80%) inside the self-hosted compiler** — of which **103** are
`Nothing -> Nothing` / `Just x -> <match…>` monadic-bind chains.

## 2. Goals / non-goals

**Goals.** Provide a generic monadic bind (one `and_then`, not per-type
`maybe_and_then`), completing the tower; reach `Maybe`/`Result` combinator parity
on the axes no class covers.

**Non-goals.** Not touching the `staircase-of-doom` sites (they need binding-`else`
/ effectful `let..else`, deferred — see `docs/let-else-and-monadic-binding-plan.md`).
Not wiring `Monad` into `do`. Not a built-in `?` propagation (Tier 2/3 there).
Not a `Validation`/error-accumulating type. Not user-facing left-biased List choice.

**Honest framing.** `and_then` does not unlock the impossible: `do { x <- m; … }`
already binds `Maybe`/`Result` structurally (the Applicative instances are written
that way). Its value is **ergonomic** (one-line single binds, point-free `|>`
pipelines, code generic over the monad) **plus** being the tower rung the deferred
monadic-propagation work builds on.

## 3. Prior-art survey (verified against primary sources, 2026-07-27)

The load-bearing decision is whether `List` gets an `Alternative`/`or_else`
instance, and with what semantics.

| Language | `Alternative` for lists | `Alternative` for Maybe/Option | Source |
|---|---|---|---|
| **Haskell** | `Alternative []`: `empty = []`, `(<|>) = (++)` — **concatenation** | left-biased (`Just x <\|> _ = Just x`) | `base` `Control.Applicative` |
| **Scala (Cats)** | `SemigroupK[List].combineK = ++` — concatenation; docs note it equals `Semigroup`'s `combine` | `SemigroupK[Option]` keeps the first `Some` (left-biased) | typelevel/cats `SemigroupK` |
| **PureScript** | `Alt Array`: `alt = append` — concatenation | left-biased | purescript `Data.Alt` |
| **Rust / OCaml** | no `Alternative` typeclass (no HKT / not in stdlib) | n/a | — |

**Reading.** Every language that gives lists an `Alternative` uses **`++`**, never
left-biased choice. Left-biased choice is universally the *Maybe/Option*
behaviour. There is also a **correctness** reason: the MonadPlus left-distribution
law `(a <|> b) >>= k == (a >>= k) <|> (b >>= k)` holds for `++` but is **broken**
by left-biased list choice (take `a = [1]`, `k = \_ -> []`: LHS `= []`, RHS
`= b >>= k`). So in a Monad+Alternative tower, left-biased List would be an
*unlawful* instance.

## 4. Decision

1. **Add a `Monad` class** (superclass `Applicative`), method `flat_map`;
   instances `Maybe`, `Result e`, `List`. Expose generic **`and_then`** (free fn
   delegating to `flat_map`, per the `fmap`/`map` house idiom).
2. **No `Alternative` class.** The only lawful `List` instance (`++`) duplicates
   the existing `Semigroup (List a)`; with `List` excluded the class is a single
   `Maybe` instance — ceremony with no dispatch payoff (cf. the `Validation`
   single-instance note in prelude). Ship `Maybe`'s fallback as a free fn.
3. **`Maybe` free fns:** `maybe_with_default`, `maybe_or_else` (mirroring
   `result_with_default` and a left-biased choice, respectively). No `maybe_map` —
   the generic `map` (`Functor`) already covers it.

## 5. Syntax / semantics / type-system impact

`class Monad m where Applicative m { fn flat_map(f: a -> m b, xs: m a) -> m b }`.
No new syntax. `flat_map` is the first prelude class method to place the class's
own type constructor in a function-typed argument's return position; it resolves
because dispatch keys on `xs : m a`, not the arrow (spiked before buildout). No
error-message impact. No compatibility/migration concerns — all new names, and a
codebase-wide sweep confirmed `and_then`/`or_else`/`flat_map`/`with_default`/
`maybe_*`/`list_flat_map` were previously undefined.

## 6. Tests

- `tests/stdlib/test_typeclass_laws.spr` — Monad laws (left/right identity,
  associativity) for `Maybe`, `Result e`, `List` (Result reduced to `Int` via
  `result_with_default` to avoid an `Eq (Result …)` dependency).
- `tests/stdlib/test_monad.spr` — behavioral: `and_then` short-circuit on
  `Nothing`/`Err`/`Nil`, list flatten, `maybe_with_default`, `maybe_or_else`.

## 7. Spec/docs

`docs/spec-v0.md` §8.5 gains a `Monad` subsection and a combinator-free-fn table,
and records the `Alternative`-deferred rationale. Status: Experimental.

## 8. Follow-ups (deferred)

- `Alternative`/generic `or_else` once a second lawful instance exists (e.g. a
  parser type).
- Monad-generic `do` and a built-in `?` propagation form
  (`docs/let-else-and-monadic-binding-plan.md`, Tiers 2–3).
- Adoption sweep: replace the ~103 hand-rolled bind chains + parity sites with the
  new combinators (separate, staged; compiler edits cost a reseed).
