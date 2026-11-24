# Instance resolution termination v0

Status: implemented. Normative text: `docs/spec-v0.md` §8.5 rule 4.

## 1. Problem

`resolve` recurses through each instance's context with no bound
(`check_constraint`, `check_context_subs`, `check_context_constraint_worker`, the
linearity walk and `produce_instance_evidence`). Spec §8.5 rules 1-3 accepted
instances whose context is at least as large as the head, and the compiler died at
`--phase check` with a stack overflow (exit 139) on each of these:

- growth: `instance Foo (List a) where Foo (List (List a))`, used by `foo([1])`
- self-cycle: `instance Foo (List a) where Foo (List a)`
- mutual cycle: `Foo (List a)` under `Bar (List a)`, and `Bar (List a)` under `Foo (List a)`
- growth hidden by an alias: `where Foo (LL a)` with `type alias LL a = List (List a)`
- growth reached only from a polymorphic function: `fn go(x: a) -> Int where Foo a = foo([x])`
- two steps: `Foo (List a)` under `Foo (Maybe (List a))`, with `Foo (Maybe a)` under `Foo a`

An instance that nothing uses compiled, so the failure surfaced at some later use.

## 2. Goals and non-goals

Goals: every accepted program has terminating instance resolution; a bad instance is
rejected at its declaration, with a located message, whether or not it is used.

Non-goals: cyclic superclasses (accepted; superclass expansion is visited-deduped,
`types.class_with_transitive_supers`); an opt-out flag.

## 3. Prior art

| Language | When checked | Rule | Limit |
|---|---|---|---|
| Haskell 2010 | declaration | head `T u1..uk`, distinct variables, no synonym (§4.3.2); a context assertion is `C u` or `C (u t1..tn)`, `u` a type variable (§4.1.3); no size rule | none |
| GHC | declaration and use | Paterson conditions: variable count, size, no type functions; `UndecidableInstances` lifts them | 200 (`mAX_REDUCTION_DEPTH`) |
| Rust | use | depth-limited solving, E0275 | `recursion_limit` 128 |
| Lean 4 | use | tabled resolution, heartbeat budget | `maxHeartbeats` 20000 |
| Scala 3 | use | divergence check | 50000 |
| PureScript | use | work counter, "possibly infinite" | 1000 |

Haskell 2010 forbids a constructor at the top of a context type (`Eq (Maybe a)`).
Sprout allows it and keeps it when it is smaller than the head.

## 4. Head shapes (premise)

A head is `T v1..vn` or a tuple `(v1, .., vn)` over distinct variables, with the effect
wrapper transparent (`instance_head_problem`, `instance_head_args`, `head_arg_problem` in
`infer.sprout`). Aliases in the head and the context are expanded before inference
(`desugar_ctx`, `ast.expand_alias_constraints`), so the check reads the expanded AST and
needs no environment.

## 5. Rule

For each context constraint `C' t'` under the head `C t`:

- (a) no type variable occurs more often in `t'` than in `t`;
- (b) `size(t') < size(t)`.

`size`: a type name or variable is 1; an application is the sum of its parts; a tuple is 1
plus its items; an arrow or thunk is 1 plus its type parts; an effect wrapper is its
inner type. Repetitions count.

A head with no type variables cannot have a context.

Accepted contexts: a bare head variable (`Eq a` under `Eq (List a)`), an applied head
variable (`ToString (f a)` under `Wrap f a`), and a smaller concrete type under a head
with variables (`Eq (Maybe a)` under `Box2 a b`).

The check runs per instance declaration in `check_instance_context_sizes`, right after
`check_instance_head_kinds`, so a malformed head is reported as one. A head with no type
variables is reported first; then, per constraint in source order, (b) before (a); the
first failing constraint wins. Nothing before it calls into `resolve`.

## 6. Termination argument

Premises:

1. A context names only head variables. Rule (a) subsumes spec §8.5 rule 3: a variable
   absent from the head occurs 0 times there.
2. The head and the dispatch type pair at equal spine depth (`check_instance_head_kinds`
   requires the head to have its class's kind, and equal kinds leave equal numbers unapplied),
   so substitution binds exactly the head variables. An unbound variable forwards a
   dictionary; it never recurses.
3. No overlapping instances, functional dependencies or type families.

Resolving `C t0` with `t0` an instance of the head `C t` by substitution `s` replaces
the goal by `C' s(t')` for each context constraint. Sizes are additive under
substitution: `size(s(u)) = size(u) + sum over v of occ(v, u) * (size(s(v)) - 1)`.
By (a), `occ(v, t') <= occ(v, t)` for every `v`; by (b), `size(t') < size(t)`. So
`size(s(t')) < size(s(t)) = size(t0)`. Each step replaces a goal by strictly smaller
ones, and sizes are natural numbers, so resolution ends. Superclass expansion of each
goal is finite (a visited set).

## 7. Cost

Equal size is rejected even across classes: `instance Same (Box a) where Eq (Maybe a)`
fails, because `Maybe a` is as large as `Box a`. The fix is a second head variable
(`Same (Box2 a b)`) or a context on a bare variable. A context with no variables
(`Bar (List Int)`) fails (b) when it is not smaller than the head, and the message then
carries no `where` hint.

## 8. Rejected options

- Use-site depth limit: the error appears far from its cause, an unused looping instance
  is accepted, and the limit is a magic number.
- Paterson conditions plus a depth backstop: the backstop is unreachable.
- Cycle analysis on the (class, head constructor) graph, rejecting only a cycle through
  an edge that breaks (a) or (b); a variable-headed context is an edge to every node of
  its class. It is sound and accepts more (`Same (Box a) where Eq (Maybe a)`), but it
  judges the whole program: an error can name instances in two modules, and adding an
  instance can reject another that compiled before. Rule 4 judges each declaration
  alone. Every instance rule 4 accepts, cycle analysis accepts too, so it can replace
  rule 4 later without breaking a program.

## 9. Census and migration

Four compound-context instance lines existed in `tests/`, `examples/`, `stdlib/` and
`ide/`: two pass (`ToString (f a)` under `Wrap f a`), two fail
(`Same (Box a) where Eq (Maybe a)`). Both failing ones move to a two-variable head:
`tests/stdlib/test_context_dict_identity.spr` and
`tests/conformance/type_error/instance_method_renamed_head_var.spr`. `where` cannot start
a following line (parser), so a grep on the head line is complete.

## 10. Tests

`tests/conformance/type_error/instance_context_*`: growth, self-cycle, mutual cycle,
growth through an alias, growth from a polymorphic function, two steps, repeated variable,
nullary head, equal-size other class, context without variables, hint without an applied
variable.
`tests/conformance/run/instance_context_recursive_type.spr`: a recursive type whose
instance constrains the head's variable resolves and runs.
