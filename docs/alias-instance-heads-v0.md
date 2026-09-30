# Alias-Headed Instances (v0)

Status: **LANDED**. Normative rule in `spec-v0.md` §8.5.

## 1. The defect

An instance whose head was a `type alias` was accepted and could never be selected:

```sprout
type alias Name = String
instance Label Name          # accepted
fn use_it() -> String = label("hi")   # ERROR: No instance of Label for String
```

A `type alias` is transparent (`spec-v0.md` §5.6.2), so `Name` and `String` are one type and that
instance is an instance at `String`. Two further consequences: the error named `String`, a type the
author never wrote, and `instance Label Name` did **not** overlap `instance Label String` — both were
accepted, for one type.

Not a soundness bug. With both present the aliased-type instance is the one that dispatched, so
nothing was mis-selected; the alias-headed one was unreachable.

## 2. Cause

An instance key is `@inst:{Class}:{head}`, built by four independent paths:

| Path | Builds the head from | Expanded aliases |
|---|---|---|
| `infer.instance_key_and_class`, `infer.register_instance_marker` | `type_from_ast(te, dict_empty())` | no |
| `infer` lookups (~22 sites) | the **inferred** type | **yes** |
| `resolve.type_expr_head_name` | the AST `TypeExpr` | no |
| `lowering.constraint_key_from_tc` | the AST type args | no |

Three of the four read the head off the AST; the only path that expanded was the lookup side, because
inference expands aliases to typecheck at all. Registration and lookup therefore computed different
strings for one type, and because the key is a string a mismatch can only be a silent miss.

## 3. Fix

`ast.expand_alias_constraints` rewrites each `InstanceDecl`'s head and `where` context, and every
function's and method's `where` clause at every depth (a slot key spells the whole constraint), substituting
an alias's parameters into its RHS and re-applying any extra arguments, iterating for alias-of-alias
under a fuel bound. It is called from `desugar_ctx.desugar_program`, which **both** checker entry points
(`check_program_with_env`, `typecheck_typed_with_effects`) reach before any later phase — so all four
paths above see the expansion and cannot drift. A per-path fix would leave four normalizations to keep
in step.

Chosen over rejecting alias heads, which `tests/stdlib/test_instance_head_arity.spr` accepts
deliberately, and which `instance-head-kinds-v0.md` §11 anticipated making work rather than banning.

**`wrap` is not expanded.** It declares a distinct nominal type, so merging it with its representation
would turn two instances into one. Guarded by
`tests/conformance/run/wrap_instance_head_stays_nominal.spr`.

**Cross-module works by construction**: the bundler qualifies both an `AliasDecl`'s name and its RHS
(`bundler.sprout`), and this pass runs after bundling, so table keys and head references are qualified
consistently.

**An under-applied alias is left unexpanded** — there is nothing to substitute, and the existing
instance-head arity diagnostic is the right one to fire.

## 4. Consequences

- The instance-head arity check now sees the **expansion**, which resolves
  `instance-head-kinds-v0.md` §11's deferred "type-alias instance heads, once they dispatch": an
  alias's own parameter count was never its residual arity, and the expansion's is.
- `check_overlapping_instances` now rejects an alias-headed instance against the aliased type's.
- Golden IR: inserting the `AliasHead` type into `ast.sprout` shifts every later constructor tag by
  one, so `tests/golden/ir/tests__smoke_shapes__11_compiler_bundle.spr.ll` changes in 554 lines across
  five mechanical categories (`alloc_obj` tags, `cname`/`cfkinds` constants, `register_ctor` arities,
  nullary tag immediates, one new global). No `define`, `declare`, `__tc_` or `__cm_` line changed — no
  dispatch or codegen difference.

## 5. Tests

| Fixture | Asserts |
|---|---|
| `tests/conformance/run/alias_instance_head_dispatches.spr` | an alias-headed instance dispatches |
| `tests/conformance/type_error/alias_instance_overlaps_expansion.spr` | it overlaps the aliased type's instance |
| `tests/conformance/run/wrap_instance_head_stays_nominal.spr` | `wrap` stays distinct (over-reach guard) |
| `tests/stdlib/test_instance_head_arity.spr` | pre-existing `instance Boxed (Half k)` still accepted |

The first two were confirmed failing on unmodified source before the fix.
