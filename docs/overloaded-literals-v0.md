# Overloaded literals & defaulting (v0)

**Status: DRAFT / pre-approval. Not normative, not started.** `docs/spec-v0.md`
remains the source of truth. This doc records a design and its one unresolved
decision; implementation waits on §12.

Supersedes the deferred `IsList`-generalization note in
`docs/coercions-and-literals-v1-draft.md` §5.A, and absorbs the `IsTemplate`
question filed as "Revisit string-interpolation type-directed dispatch".

## 1. Problem

Sprout ships exactly two coercions, and both are hand-written branches of one
pre-typecheck pass:

| coercion | fires on | inserted call |
|---|---|---|
| `StringTemplate → String` | backtick-template literal syntax | `template_to_string` |
| `List → Vec` | a `Cons`-headed call or bare `Nil` (spec §5.5) | `vec_from_list` |

Both live in `desugar_ctx.sprout` (619 lines), reached from `checker.sprout:378`
before inference runs. The pass threads an expected type *name* — a bare
`String`, never an inferred type — down the AST and rewrites by AST shape
(`ctx_is_active` gates exactly the two names, `desugar_ctx.sprout:138`).

Three costs:

1. **Not extensible.** A third target — `Set`, `Dict`, `Bytes`, a user
   container, a `wrap` over `String` — is a compiler edit, not a library one.
2. **Structurally capped at literals.** Running before inference, the pass can
   only key on syntax. That boundary is *sound and deliberate* (a `List`-typed
   variable carries no pre-inference proof;
   `docs/coercions-and-literals-v1-draft.md` §4.A), but it means the mechanism
   can never grow past literal shapes.
3. **Duplicated, not shared.** The two rules share a traversal, not a concept.
   Each new one re-threads context through `if`/`match` arms by hand.

A fourth consideration, for whoever implements: the Vec-context computation has
a **second consumer** — `lint_rules.sprout:329` calls
`desugar_ctx.find_redundant_vec_wraps`. Any replacement must keep that lint
working or retire it deliberately.

## 2. Goals and non-goals

**Goals.**
- One mechanism that subsumes both existing coercions, and deletes the
  expected-type-name threading rather than generalizing it.
- User-extensible: a library adds a literal target by writing an instance.
- Every existing program keeps its current meaning, unannotated.

**Non-goals.**
- Coercion of non-literal expressions (a `List`-typed variable in a `Vec` slot
  stays an error).
- **Existential injection** — not a literal form; it is any conforming value at
  a known expected type, so it needs a typed coercion site and is untouched by
  this design (`docs/existential-injection-v0.md`).
- `wrap`↔base coercion (rejected in `coercions-and-literals-v1-draft.md` §4.B;
  the ergonomic answer there is instance lifting, open in `BACKLOG.md`).
- `Int → Double` widening — closed for v0 by spec §8.
- A multi-parameter `Coerce from to` class (see §4).

## 3. The observation that unifies the two rules

Neither existing coercion converts a *value*. One fires on template literal
syntax; the other on a `Cons`/`Nil` head. Every shape that has ever passed the
test is a literal form.

So the general feature is not "coercion" — it is **overloaded literals resolved
at the expected type**, which is what Haskell (`OverloadedLists`) and Swift
(`ExpressibleByArrayLiteral`) both ship. `coercions-and-literals-v1-draft.md`
§3.A identified that consensus and then took the syntactic shortcut. This doc
proposes taking the consensus.

## 4. Design

```
class FromList f
  fn from_list(xs: List a) -> f a

class FromTemplate a
  fn from_template(t: StringTemplate) -> a
```

with `instance FromList List` = identity, `instance FromList Vec` =
`vec_from_list`, `instance FromTemplate StringTemplate` = identity, and
`instance FromTemplate String` = `template_to_string`.

Every list literal elaborates **unconditionally** to `from_list([…])`; every
template literal to `from_template(…)`. The expected type then selects the
instance through ordinary unification and dictionary resolution.

The consequence is the point: **there are no coercion sites and no coercion
pass.** A constrained polymorphic term already takes its instance from context
everywhere context exists, so `ctx_is_active`, the fn-signature index and the
arm-threading all go. The template→`string_concat_many` lowering stays; that is
real work, not context.

**Two classes, not one.** `FromList` ranges over `f : * -> *` and `FromTemplate`
over `a : *`. Collapsing them needs kind polymorphism or a multi-param class;
`ast.TypeConstraint String (List TypeExpr)` can *represent* multi-param, but the
resolution paths sampled key on a single head type, so treat multi-param as
unproven machinery. One class per literal form is the buildable shape.

**Alternative considered — table-driven desugar.** Collapse the two branches
into one `(literal shape, expected type head, conversion fn)` table inside
`desugar_ctx`. Near-zero risk, no type-system change. Rejected: it tidies the
duplication and solves neither §1.1 (extensibility) nor §1.2 (the literal cap),
which are the stated problems.

**Why not the Rust route.** `Deref` was the starting inspiration and does not
transfer. It is a *pointer retarget* (`&T → &U`), and the std docs' own
implement-it test requires "the implementation of the deref function is cheap".
Sprout has no places, so the analogue is a value conversion, and `Vec → List` is
O(n) plus an allocation — it fails that test, while `&Vec<T> → &[T]` is a
pointer and a length. Rust's enumerated-coercion-sites model is the other
credible way to build this; we are not taking it, because overloaded literals
need no sites at all.

## 5. Defaulting — the actual feature

Defaulting is a rule that picks a type when inference leaves a class
constraint's variable unpinned, instead of reporting an ambiguity.

This design needs one, because it creates open constraints where none existed:
`let xs = [1, 2, 3]` becomes `FromList f => f Int` with nothing fixing `f`.

### 5.1 What Sprout has today

- **No typeclass defaulting**, stated outright at `infer.sprout:9181`: "Sprout
  has no defaulting, so an undetermined variable has to be an error rather than
  a silent choice." Ambiguity is reported (`infer.sprout:9193`).
- **One numeric default, implicit and unstated.** `check_arith`
  (`infer.sprout:3846`) unifies the operands with each other, then tries `Int`
  and falls back to `Double`. A free operand variable is silently bound to `Int`
  by the `Int` attempt. It is ordered unification, not a declared rule, and it
  appears nowhere in the spec.
- Its known failure mode is already filed: "Numeric defaulting fires before a
  deferred field obligation is discharged" — `Double` fields under arithmetic
  draw a spurious `Int vs Double`. The lowercase-annotation entry in
  `BACKLOG.md` is the same mechanism at a distance of ~1700 lines.

### 5.2 Design rules

- **D1 — ordering.** Defaulting runs *last*, after every deferred obligation is
  discharged. The existing `check_arith` bug is the empirical argument: a
  default that fires early pins a variable that later evidence would have
  determined correctly.
- **D2 — resolve, never suppress.** Defaulting must pick an instance and commit.
  It must not disable `first_ambiguous_dict_class` (`infer.sprout:9051`), which
  is a soundness rule about per-binding generalization, not a convenience guard
  — the comment above it records the `let labeller = label` SIGSEGV it exists to
  prevent.
- **D3 — the rule.** Unresolved `FromList f` ⇒ `f := List`. Unresolved
  `FromTemplate a` ⇒ `a := String`.
- **D4 — closed scope.** Only classes on an explicit list are defaultable.
  Haskell restricts defaulting to constraints where "at least one of these
  classes is a numeric class" and "all of these classes are defined in the
  Prelude or a standard library" for exactly this reason.

D3 is what makes the change conservative: list literals are `List` and templates
are `String` today, so the defaults preserve the meaning of every file in the
tree, the self-hosted compiler included. Without it, the same feature is a mass
annotation tax.

### 5.3 Open decision — a silent default that changes asymptotics

Defaulting resolves with **no diagnostic at the site where the compiler chose**.
Haskell bounds the damage by defaulting numerics only, where the choice moves
precision. Sprout's case differs in kind: `List` vs `Vec` is O(n) vs O(1)
indexing, so a silent default landing on `List` in a hot path is a performance
bug the compiler declined to mention.

Options: (a) silent, as Haskell; (b) a lint-level warning at the default site;
(c) require an annotation in modules that opt in to `Vec` literals. **Undecided
— this is the call that gates implementation.**

## 6. Prior art

Verified in this pass, against primary sources:

| Language | Mechanism | Detail |
|---|---|---|
| **Haskell 2010** | defaulting, §4.3.4 | Defaultable when `v` appears only in constraints `C v`, at least one class is numeric, and all are Prelude/standard-library. `default (Integer, Double)` absent a declaration. Ambiguity example: `let x = read "..." in show x`. |
| **Rust** | enumerated coercion sites | `let` with annotation, call args, struct fields, function results, assignment RHS, and propagating sub-expressions. Transitive coercion is listed but "note that this is not fully supported yet." |
| **Rust** | `Deref` | `type Target: ?Sized`; implement only when "a value of the type transparently behaves like a value of the target type", "the implementation of the deref function is cheap", and users "will not be surprised". |
| **Go** | embedding | Promotion is *name resolution*: promoted methods enter the outer type's method set, with no implicit conversion to the embedded type. |
| **Kotlin** | `by` delegation | "the compiler will generate all the methods of `Base` that forward to `b`". |

Verified earlier and recorded in `coercions-and-literals-v1-draft.md` §3.A:
GHC `OverloadedLists`/`IsList` (routes through a standard list, paying the same
intermediate cost), Swift `ExpressibleByArrayLiteral` (literal takes its type
from context), Rust's absence of a context-directed collection literal (`vec!`).

The Go and Kotlin rows are here as the *contrast*: they solve
punch-names-through without any conversion, which is the shape the `wrap`
instance-lifting backlog item wants — a different feature, not this one.

## 7. Syntax and semantics impact

No surface syntax change. Evaluation order is unchanged: `from_list` is an
ordinary strict call on an already-built list, exactly as the inserted
`vec_from_list` is today, so the cost of `[…]` in a `Vec` slot stays identical
to hand-written `vec_from_list([…])`. `instance FromList List` is the identity,
so `List` literals lower as they do now once the dictionary is erased (§10).

## 8. Type-system impact

- Two new classes and their instances, in the prelude.
- A new defaulting phase, ordered per D1, interacting with generalization and
  with the ambiguity check per D2.
- A dictionary at every list literal unless devirtualization erases it.

## 9. Error-message impact

- **Must be preserved:** a wrong-element literal in a `Vec` slot reports the
  element-level `Int vs String`, not a bare `List`/`Vec` mismatch (fixture
  `tests/conformance/type_error/vec_literal_element_mismatch`). Element types
  still flow through `from_list`'s application, so this should survive — verify,
  do not assume.
- **Unchanged:** a `List`-typed *variable* in a `Vec` slot. Its type is `List a`
  with no `from_list` involved, so unification still reports `Vec vs List`
  (fixture `vec_nonliteral_list_not_coerced`).
- **New failure mode:** an unresolved `FromList` where today there is no error
  at all. D3 should make this unreachable in practice; if it is reachable, D3 is
  wrong.

## 10. Compatibility and migration

Conservative by construction via D3 — no existing program changes meaning. Two
real costs:

- **Blast radius.** Every list literal's lowering changes: full golden-IR churn
  plus a reseed (Definition of Done #9, #12).
- **Perf on the compiler itself.** The self-hosted source is dense with list
  literals. `instance FromList List` being the identity should let
  devirtualization erase the dictionary, but that is a measurement, not an
  assumption, and it is a gate rather than an afterthought.

## 11. Tests to add

1. Existing fixtures stay green (`test_vec_literal_coercion.spr`, both
   `type_error` fixtures above).
2. Unannotated `let xs = [1, 2, 3]` defaults to `List` — executable.
3. Annotated `Vec` context picks `instance FromList Vec`, including return
   position, call-arg position and the empty `[]`.
4. A **user-defined** instance in a test module targets a third container,
   proving extensibility (§1.1) without a compiler edit.
5. Genuine ambiguity is still rejected — a constraint that D3 does not cover.
6. **Regression for D1:** a `Double`-typed obligation discharged after the
   default would have fired, in the shape of the known `check_arith` bug.
7. Perf: stage-2 build time and IR size before/after, per §10.

## 12. Decision gate

Not started, and not to be started until all three hold:

1. §5.3 is decided (silent vs warned vs annotation-required).
2. The dictionary cost on the compiler's own literals is measured, not assumed.
3. Confirmation that no multi-param class is needed (§4).

## 13. Spec/docs updates on acceptance

- Replace the spec §5.5 clause describing the syntactic `List`-literal → `Vec`
  lowering with the class-based rule.
- Add a **normative** spec section on defaulting — covering D3/D4 *and* writing
  down the existing `check_arith` numeric default, which is currently unstated
  behaviour.
- Mark `coercions-and-literals-v1-draft.md` §5.A superseded by this doc.
- Retire the `IsTemplate` backlog entry this doc absorbs.
