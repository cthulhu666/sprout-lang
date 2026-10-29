# NonEmpty — a list with at least one element

Status: **proposal, deferred** (2026-10-29). Refreshed 2026-10-28 after list toolkit steps 1, 2
and 4 landed. Nothing here is implemented. §4.2 is decided; where the names live (§4.4) is open.

## 1. Problem

Code that knows a list is non-empty has no way to say so. It pays one of three ways:

- **A panic on an arm that cannot run.** `infer.build_comp_level` and `infer.comp_fold_name` panic
  on `Nil` because a comprehension always has a generator. In uncharted-suns, `game.sim.slot0` and
  `game.sim.zone0` panic on an empty roster that the host and the tick make impossible.
- **A dead arm with an invented answer.** `tui.text.cluster_width` returns `1` and
  `tui.text.cluster_is` returns `false` for an empty cluster, which their own comment says cannot
  exist. `grimward.gear.slot_of` returns `MainHand` for an empty slot list, pinned by a test rather
  than by the type.
- **A seed taken by hand.** In a survey of 31 LeetCode problems, the solutions to 53 and 121 seed
  their fold with `let Just first = vec_get(0, nums) else 0`: an invented `0` for an input the
  problem rules out (`examples/leetcode/p0053_maximum_subarray.sprout`, `p0121_…`).

In-tree, that is all (surveyed 2026-10-29). The parser already rejects an empty `do`, `match`,
`let…in` and comprehension, but the AST holds each as a `List`. Of the 142 compiler functions that
walk those lists, the ones read end their recursion at `Nil` (`translate_match_arms` emits the
no-arm-matched abort there), so `NonEmpty` would not remove the arm. `translate_do`'s "empty
do-block" arm may be dead; not checked. Most of the gain is in application code and in the toolkit
signatures (§9).

`docs/guidelines.md` §3 already names `NonEmpty a` as the example of an illegal state made
unrepresentable. It does not exist.

It also blocks the rest of the list toolkit: `scanl` and `group_by` want `NonEmpty` in their
signatures (§9). `zip_with` shipped as the `Zip` class, so a `NonEmpty` instance only adds.

## 2. Goals and non-goals

Goals:
- A prelude type `NonEmpty a` whose values cannot be empty, by construction.
- `head`, `tail`, `last` and `reduce` that are total, and exist **only** for `NonEmpty` (§4.2).
- The core in the prelude, the rest in `stdlib.non_empty`, as `List` splits with `stdlib.list`.
- The existing generic functions (`map`, `fold`, `any`, `count`, `traverse`, `++`, `==`, …) work on
  it unchanged, through class instances.
- O(1) conversion both ways: `List a -> Maybe (NonEmpty a)` and `NonEmpty a -> List a`.

Non-goals (v0):
- `NonEmptyVec`. Its invariant is not structural, so it would need representation opacity.
- `Applicative`/`Monad` instances.
- `NonEmpty` versions of functions the classes already cover. In particular no `NonEmpty`
  `maximum`/`minimum`/`max_by`/`min_by`: the prelude's `Foldable` ones keep those names and return
  `Maybe`; the total form is `ne.reduce(max, xs)` (§4.4).
- A `Filterable` instance (§4.3); `ne.filter`, `ne.filter_map` and `ne.partition` return `List`.
- A fold over `NonEmpty` of linear values. `Foldable`'s methods declare no `linear` bound, so the
  instance cannot add one (spec: an instance method restates the class method's bound).
- List-pattern syntax (`[x | rest]`) on a `NonEmpty` scrutinee.

## 3. Prior art

The type:

| Language | Type | From a list | Notes |
|---|---|---|---|
| Haskell (`base` ≥ 4.9) | `data NonEmpty a = a :\| [a]` | `nonEmpty :: [a] -> Maybe (NonEmpty a)` | total `head :: NonEmpty a -> a`; `groupBy :: … -> f a -> [NonEmpty a]`; `scanl :: Foldable f => … -> f a -> NonEmpty b` |
| Scala (cats) | `case class NonEmptyList[+A](head: A, tail: List[A])` | `fromList[A](l: List[A]): Option[NonEmptyList[A]]` | `reduceLeft` total; `groupBy` → `SortedMap[B, NonEmptyList[A]]` |
| PureScript (`lists`) | `newtype NonEmptyList a = NonEmptyList (NonEmpty List a)` | `fromList :: List a -> Maybe (NonEmptyList a)` | `Semigroup` instance, no `Monoid` |
| Rust (std) | none | — | non-emptiness stays a runtime check |

All three languages with the type agree on its shape: a head plus an ordinary list tail, a
`Maybe`-returning constructor from a list, and `Semigroup` without `Monoid`. They differ on how far
the API mirrors the list API; Haskell's module mirrors most of `Data.List`.

The first element of a plain list:

| Language | `head` on a list |
|---|---|
| Haskell | `Data.List.head :: [a] -> a`, partial; GHC 9.8.1 added an `x-partial` warning to `Data.List.{head,tail}` |
| Elm | `head : List a -> Maybe a`, `tail : List a -> Maybe (List a)` |
| Rust | `first(&self) -> Option<&T>`, `split_first(&self) -> Option<(&T, &[T])>` |

## 4. Design

### 4.1 The type

```sprout
export type NonEmpty a =
  | NonEmpty a (List a)
```

The head and tail representation enforces itself: no value of this type has no element, so the
constructor stays public and every `match` on it is total. Type and constructor share a name, as
`Vec` does. `NonEmpty(x, [])` is the one-element value; `NonEmpty(x, ne.to_list(xs))` prepends.

`non_empty` is not the only way in, for three reasons:
- Hiding the constructor protects nothing, since no value of the type is empty.
- With `non_empty` alone, every value comes back in a `Maybe`. `non_empty([1, 2, 3])` would make the
  caller handle a `Nothing` that cannot happen. Until a literal rule exists (§4.5), the constructor
  is the only total way to build a value.
- The prelude cannot hide a constructor anyway: spec §3.1 puts its constructors in scope everywhere.

Haskell (`:|`), cats (a public case class) and PureScript (`NonEmptyList(..)`) all export theirs.

### 4.2 No `head`, `tail` or `last` on `List`

`head`, `tail` and `last` are defined for `NonEmpty` only. A list gets no partial version (guideline
§2 rules that out) and no `Maybe` version either:

- The first element of a list is a pattern: `let [x | rest] = xs else fallback`, or a `match`. It
  is as short as unwrapping a `Maybe` and names the fallback where it is used.
- With one meaning per name, `head` needs no prefix or module.
- `non_empty(xs)` is the one conversion. `xs |> non_empty |> map(head)` gives `Maybe a` where a
  pipeline needs it.

This closes the BACKLOG entry "Prelude has no `head`" by decision.

A list passed to `head` is a type mismatch, so the hint carries the guidance: the `List`/`NonEmpty`
pair gets a hint in `unifier.sprout`, next to the `List`/`Vec` one (`list_vec_hint`):

```
`List` and `NonEmpty` are distinct types; match a list with `let [x | rest] = xs else …`,
or convert it with `non_empty(xs)`, which returns `Maybe (NonEmpty a)`
```

### 4.3 Instances

`Functor`, `Foldable`, `Traversable`, `Semigroup`, `Eq`, `Ord`, `ToString` and `Zip` in the
prelude; `Sort` in `stdlib.sort`, which owns that class. Each walks the head and then the tail with
the `List` code; none is slower than the `List` instance. `Zip` keeps the result non-empty: both
heads pair, then the tails zip as lists. `Sort` sorts the list and splits off the new head.

Not `Monoid`: there is no empty value. Not `Filterable`: `filter` can remove every element, so its
result is a `List`, which the class's `f a -> f a` cannot express. `stdlib.non_empty` has `filter`,
`filter_map` and `partition` returning `List` instead, as Haskell's `Data.List.NonEmpty.filter ::
(a -> Bool) -> NonEmpty a -> [a]` and cats' `NonEmptyList.filter(p): List[A]` do. `any`, `all`,
`count` and `find` already work through `Foldable`.

`to_string` prints `[1, 2, 3]`, the same as the list, because the value is a list to the reader.

### 4.4 Functions

| Function | Type | Cost | Where |
|---|---|---|---|
| `non_empty` | `List a -> Maybe (NonEmpty a)` | O(1) | prelude |
| `head` | `NonEmpty a -> a` | O(1) | prelude |
| `tail` | `NonEmpty a -> List a` | O(1) | prelude |
| `to_list` | `NonEmpty a -> List a` | O(1), one cell | `stdlib.non_empty` |
| `last` | `NonEmpty a -> a` | O(n) | `stdlib.non_empty` |
| `reduce` | `(a -> a -> a) -> NonEmpty a -> a` | O(n), left to right | `stdlib.non_empty` |
| `filter` | `(a -> Bool) -> NonEmpty a -> List a` | O(n), one pass | `stdlib.non_empty` |
| `filter_map` | `(a -> Maybe b) -> NonEmpty a -> List b` | O(n), one pass | `stdlib.non_empty` |
| `partition` | `(a -> Bool) -> NonEmpty a -> (List a, List a)` | O(n), one pass | `stdlib.non_empty` |

The prelude holds the type, its instances, the way in (`non_empty`) and the two names that take it
apart (`head`, `tail`). The rest is imported as `import stdlib.non_empty as ne`, and reads
`ne.to_list`, `ne.last`, `ne.reduce`. Names the prelude may later want for something more general
stay out of it: a `Foldable`-generic `to_list` would want that name.

Always import it with the alias. Without one, the prefix is the last segment, `non_empty`
(spec §Imports), which is also the prelude function's name. `non_empty.last(xs)` still resolves to
the module, since the bundler reads an import alias before a module-level value. But a local binder
named `non_empty` comes first, and turns `non_empty.last(xs)` into a field read on that local,
whose error names a field rather than the import. The compiler does not enforce the alias; the
stdlib reference and the examples use it everywhere.

**Open.** Where the names live is not decided. The options: as above; `head` and `tail` in
`stdlib.non_empty` too (`ne.head`), leaving the prelude the type, its instances and
`non_empty`; or all of it there, as Haskell keeps `NonEmpty` in `Data.List.NonEmpty` and out of
its Prelude. Sprout has no orphan-instance rule (spec §5.6.4), so the instances can sit in any
module. Prelude names cost the §6 renames; a module name costs an import, and `head(list)` then
reports an unknown name instead of the §4.2 hint.

`ne.reduce(max, xs)` and `ne.reduce(min, xs)` are the total maximum and minimum for any `Ord`. A
hot `Int` loop passes `imath.max`/`imath.min` (`import stdlib.math.int as imath`) instead, as the
generic pair dispatches through `Ord` (`docs/list-toolkit-v0.md` §4.7).

The three `List`-returning names (`filter`, `filter_map`, `partition`) are the prelude's generic
ones. Used as `ne.filter` they collide with nothing; a selective `import stdlib.non_empty (filter)`
would hide the prelude's `filter` in that file, so the alias rule above matters doubly here.

### 4.5 Literals (not in v0)

v0 builds a value with the constructor only: `NonEmpty(1, [2, 3])`. A literal rule is added later
only if real code shows the constructor is a burden; adding it then only accepts more programs, so
it breaks nothing. This section records what that rule would be.

Spec §5.5.1 rewrites a list literal to `vec_from_list(…)` in a `Vec` position. The same
pre-typecheck pass (`desugar_ctx`) could rewrite `[e1, e2, …, en]`, n ≥ 1, to
`NonEmpty(e1, [e2, …, en])` in a `NonEmpty` position. It would fire in exactly the positions
§5.5.1 lists for `Vec`: an argument for a parameter declared `NonEmpty …` of a named top-level
function, and the body of a function or annotated top-level `let` declared to return one. It would
**not** fire in record fields, constructor arguments, lambda bodies, generic positions, or through a
type alias. The pass keys on the bare head name, so a module that declares its own `NonEmpty` type
would get the rewrite too; `Vec` has the same limit today.

The pass has no error channel. `[]` and a comprehension in a `NonEmpty` position stay unrewritten
and reach the unifier as a `List`, where they get the §4.2 hint. Shape-specific errors ("an empty
list cannot be a NonEmpty") would need a checker-side pre-scan.

Scala cats has no literal form either: `NonEmptyList.of(1, 2, 3)` takes `of[A](head: A, tail: A*)`,
whose head parameter rules out the empty case as Sprout's constructor does. Haskell's
`OverloadedLists` extension (`instance IsList (NonEmpty a)`) is opt-in per module.

## 5. Alternatives at each layer

- **Caller**: each site keeps a `(first, rest)` pair or matches `[x | rest]` with `let..else`. No
  new type, but the invariant lives in comments, the four panicking or dead arms stay, and the
  toolkit has no type for "the result has at least one element".
- **Function**: total helpers per site (`slot0` returning the first slot). Same objection: nothing
  carries the fact from producer to consumer.
- **Representation** (this design): one prelude type. It is the only layer at which `scanl`,
  `group_by` and `reduce` can state the guarantee in their signatures (§9).
- **`wrap` over `List a`**: converts to a list for free, but construction must check for `[]`,
  only representation opacity stops a caller from wrapping `[]`, and opacity is not available in
  the prelude. The `match` would not be total.

## 6. Impact

- **Syntax**: none.
- **Semantics**: one ADT, its instances and three functions in the prelude; a new module
  `stdlib.non_empty` with three more.
- **Type system**: none.
- **Errors**: one unifier hint (§4.2).
- **Generated code**: unused prelude code is dropped (`examples/hello.sprout` emits three
  functions). But the bundler renames every local binder that shares a prelude value's name to
  `$l_<name>` (`bundler.local_name`, `docs/prelude-name-identity-v0.md` §5), and parameter
  names reach IR. So every binder named `head`, `tail` or `non_empty` (`| Cons head tail ->` and
  the like; the words occur in 276 files, 33 of them in the compiler, counted 2026-10-28) changes
  its IR name. The
  seed and golden IR move by name only; nothing else changes.

## 7. Compatibility and migration

No existing name changes meaning; every addition is new. Only the three prelude names can collide.
A local definition with one of them shadows the prelude one inside its file, which is legal but can
misdirect an error there:

- sprout_lang: `fn head` in `tests/stdlib/compiler/test_types_shared_helpers.spr` and
  `test_typed_ast_traverse_children.spr`; `fn tail` in `tests/cost/render_frame.sprout` and
  `tests/conformance/run/local_named_like_intrinsic.spr`; `fn non_empty` in
  `tests/stdlib/test_ir_codegen_string_eq.spr`. All are tests; none is in `examples/` or `stdlib/`.
- uncharted-suns, repbit, sprout-postgres: none.

Local binders of these names are renamed, not rejected (§6).

The module's names collide with nothing while they are used qualified. A selective import does not
get that protection: `import stdlib.non_empty (last)` into a file that defines its own `last` is
silently shadowed by the local one (`bundler.bare_value_name` reads the module's own names first),
and a call then reports an arity or type error that names neither. The existing `fn last`
(sprout_lang `tests/stdlib/test_linear_borrowing.spr`, uncharted-suns `fray/tools/evolve.sprout`
and `meadow/sys/farming/solo.sprout`), `fn to_list` (`tests/stdlib/test_mutvec_sort.spr`,
`examples/tree_set.sprout`) and `reduce` (`stdlib/math.sprout`, `stdlib/math/modular.sprout`) stay
as they are.

## 8. Spec and docs

- `docs/spec-v0.md`: the prelude section gains the type, instances, `non_empty`, `head` and
  `tail`. (A later §4.5
  rule would also touch §5.5.1 and §3.1, "Built-in syntax means the prelude's names".)
- `docs/stdlib-reference.md`: a `stdlib.non_empty` entry.
- `docs/idiomatic-sprout.md`: "the first element" — `let [x | rest] = xs else …`, or `NonEmpty`.
- `BACKLOG.md`: delete "No `NonEmpty a`" and "Prelude has no `head`" on landing.
- Status: normative on landing, as the prelude's other types are.

## 9. The list toolkit on top of it

| Toolkit function | Signature | Reason |
|---|---|---|
| `scanl` | `(b -> a -> b) -> b -> f a -> NonEmpty b where Foldable f` | the result always holds `init` |
| `group_by` | groups are `NonEmpty a` | no group is empty |
| `zip_with` on `NonEmpty` | `NonEmpty` in, `NonEmpty` out | length is the shorter input's, at least 1 |
| `map_adjacent` | `List b` result | one element gives no pair |

The toolkit adds no `head`, `first` or other `Maybe`-returning first-element function (§4.2).
`reduce` is this design's, not the toolkit's.

## 10. Implementation steps

1. Prelude: type, instances (`Zip` included), `non_empty`, `head`, `tail`, the §4.2 unifier hint.
   `stdlib.sort`: `instance Sort NonEmpty`. New `stdlib/non_empty.sprout`: `to_list`, `last`,
   `reduce`, `filter`, `filter_map`, `partition`. Tests. Reseed (the prelude is in the
   compiler's closure) and run the golden IR gate; its diffs are the §6 renames.
2. Comprehensions: `ComprehensionExpr` carries its generators as `NonEmpty`, and
   `check_comprehension_gens` builds `kinds` as `NonEmpty CompKind`, so both `infer` panics go. The
   sweep is the compiler, not `tests/`: `ast`, `parser` (`collect_comprehension_gens`),
   `desugar_ctx`, `bundler`, `lint_pattern`, `lint_rules`, `infer`. Reseed; the compiler-bundle
   golden snapshot moves too.
3. uncharted-suns: `game.sim` rosters and `grimward.gear.slot_of`.

Not in v0: `tui.text` clusters. Their producer is `grapheme.clusters`, and `List (List Int)` runs
through `tui.text`, `line_zipper`, `screen`, `paint`, `viewport`, `input`, `buffer` and four test
files. That migration is its own change.

## 11. Tests

`tests/stdlib/test_non_empty.spr`:
- `non_empty([])` is `Nothing`; `non_empty([1, 2])` round-trips through `ne.to_list`.
- `head`, `tail`, `ne.last`, `ne.reduce` on one and on many elements; `ne.reduce` runs left to
  right.
- `ne.reduce(max, xs)` and `ne.reduce(min, xs)`.
- `ne.filter`, `ne.filter_map` and `ne.partition`: keep all, keep none (an empty `List`), mixed;
  `partition` calls `pred` once per element.
- `zip_with` on two `NonEmpty`s of unequal length; `sort` and `sort_by` keep stability and return
  a `NonEmpty`.
- A file with `| Cons head tail ->` and a parameter named `tail` still compiles and runs (§6).
- An unaliased `import stdlib.non_empty`: `non_empty.last(xs)` reaches the module and
  `non_empty(ys)` the prelude function, in the same file (§4.4).
- Each instance: `map`, `fold`, `any`, `traverse` (one `Nothing` fails the whole), `++`, `==`,
  `compare`, `to_string` prints `[1, 2, 3]`.

Failure tests: `head` applied to a `List`, and a `List` passed where `NonEmpty` is expected, each
showing the §4.2 hint.
