# Argument CPR: a `Maybe` passed straight to its consumer (v0, 2026-10-25)

Status: landed 2026-10-25 (§8 has the measurement). A compiler optimisation: no syntax,
semantics, typing or diagnostic change, so nothing here is normative and `docs/spec-v0.md` is
untouched. It implements the argument half of `docs/scalar-replacement-v0.md` §4 Stage 2.

## 1. Problem

The CPR peephole returns a `Maybe` as two words (tag, payload) when a `match` consumes the call in
place: `match vec_get(i, v) with` calls `@vec_get_worker` and allocates nothing. Passed as an
argument, the same call builds the `Just`: `maybe_with_default(0, vec_get(i, v))` costs +17.5
ns/read, and 70–90x the in-place form with 100k values live
(`bench/results-2026-09-28-vec-box-tax.md`). Outside `tests/`, 22 sites pass a call straight to
`maybe_with_default`, `result_with_default`, `result_from_maybe` or `is_just`; 17 of them call
something other than the `map` method (grep, 2026-10-25). `docs/idiomatic-sprout.md` teaches
users to avoid the shape by hand.

## 2. Goals and non-goals

Goals:
1. A call lowered by `translate_call` whose argument is a CPR-routable call, at a parameter its
   callee only matches, allocates no wrapper.
2. It works for any such consumer: the prelude's `maybe_with_default`, `result_with_default`,
   `is_just`, `result_from_maybe`, and user functions.
3. The boxed function stays for every other caller. No existing symbol changes ABI.

Non-goals, each a follow-up:
- A consumer call reached by another path: as a `match` scrutinee
  (`match maybe_or_else(vec_get(i, v), fb) with`, lowered by `translate_unboxed_maybe_match`), in a
  worker's tail chain (`translate_tail_chain`), or as an argument of a routed producer. All three
  go through `translate_args`. Covering them needs a worker unboxed on both sides,
  `@f_argw_worker`.
- A recursive consumer's own loop. `count(n - 1, vec_get(x, v))` inside `count` stays boxed (§4,
  *Recursive groups*); only calls from outside `count`'s group route. Unboxing the loop needs
  self-TCO on `@f_argw`.
- A lambda's `Maybe` result (`list_filter_map(\n -> f(n), xs)`): the callee is unknown at the call
  site (BACKLOG P3 **A `Maybe` returned by a lambda is boxed**).
- A `Maybe` already held in a variable: it is on the heap already.

## 3. Alternatives, by layer

- **Caller:** keep the hand rule and add the lint already filed (BACKLOG P3 **Nothing lints the
  pessimised spelling**). Loses: the natural spelling stays 12–90x slower, every user must learn a
  codegen detail, and the lint cannot tell a top-level callee from a closure call.
- **Function, by inlining:** rewrite `f(a, g(x))` to `let d = a in match g(x) with <f's arms>`
  before `ast_to_ir` (`docs/accessor-inliner-design-2026-07-11.md`, never approved). Loses: code
  grows at every site, so arm size needs a cap; no help for big consumers; it needs
  capture-avoiding renaming and callee bodies in `ProgEnv`; `scalar-replacement-v0` §2 fences off a
  general inliner.
- **Function, by an argument worker:** this design. One extra function per routed consumer, and a
  call stays unless LLVM inlines it.
- **Representation:** a `Maybe` as two words everywhere, as Rust's `Option<T>` is a value. Loses:
  it changes the uniform one-word ABI that generics, the runtime and the GC rely on.

## 4. Design

**Consumer.** A top-level function `f` gets an argument worker for parameter `k` when:
- its body is `match p_k with <arms>`, with nothing around the `match`;
- every arm passes `is_simple_width2_arm`, the rule result workers use, and at least one arm is a
  constructor, so `p_k` is a width-2 ADT (`Maybe`, `Result`, or a user type whose constructors
  carry at most one field);
- `p_k` occurs in no arm body, so it is used only as the scrutinee and never escapes.

**The worker.** `@f_argw` takes `f`'s parameters with `p_k` replaced by two: `(p_k$tag,
IRTScalar)` and `(p_k$val, IRTUnknown)`. The tag is never a heap value; the payload may be, so it
is rooted as any unknown parameter is. Its `owned` set is `owned_param_ssas` of `f`'s other
parameters, as `translate_user_fn` builds it, so in-place reuse of those still fires. Its body is
the arms, lowered by `translate_unboxed_maybe_arms` with the two parameters as the tag and payload
SSAs: the code that lowers `match g(x) with` today, fed by parameters instead of a call. It
returns `f`'s result boxed, as `f` does.

**Call site.** `f(a_0, …, g(b…), …, a_n)`, with `g(b…)` at position `k`, lowers to: the arguments
before `k`, `g`'s arguments, `IRCallUnboxed2(tag, val, IRTUnknown, g_worker, …)`, the arguments
after `k`, then `call @f_argw(…, tag, val, …)`. That is the boxed call's left-to-right order.
`ir_rooting.op_produces_simple_heap` reports the payload of `IRCallUnboxed2` by its kind, so a heap
payload stays rooted while later arguments are evaluated. No rooting rule changes.

**Two layers, as result workers have.**
- `argw_shape(call) -> Maybe (f, k, g)`, shape only: `f` is top-level with an argument worker for
  `k`, the call passes exactly `f`'s arity (so `maybe_with_default(0)` is refused), argument `k`
  is a direct call `g(b…)`, and `g` is a Tier-1 `_unboxed` extern or passes `cpr_result_known`.
  Collection (`scan_workers`, the same pass that finds matched callees) uses it to add `g` to the
  result-worker set and `(f, k)` to the argument-worker set.
  It over-collects, which is harmless. A site whose `@f_argw` is already a top-level definition,
  or whose Tier-2 `@g_worker` is, is not a site: both layers drop it and the call stays boxed.
  The site records how `g` arrives, as an extern's `_unboxed` symbol or as `@g_worker`.
- The router uses `argw_shape`, and also requires that neither `f` nor `g` is shadowed by a
  parameter, capture, `let` or constructor (`callee_name_shadowed`), and the group rule below. A
  parameter named `maybe_with_default` holding a closure then stays an `IRApplyClosure`.
This is the split `worker_callee_routable` already documents: every router checks the same filter
collection applies, so it never calls an un-emitted symbol.

**Recursive groups.** A call whose callee `f` is in the calling function's own recursive group
(self or mutual) is not routed. `tco_rewrite` and mutual contification find a loop by the callee's
name, and a call to `f_argw` is not a call to `f`, so routing would turn a loop into recursion.
- `TxEnv` gains the calling function's source group, a set of names. `translate_user_fn` sets it
  to `f`'s group. The `@f_argw` emitter sets it to `f`'s group too, since its body is `f`'s arms:
  a self-call there goes to the boxed `f`, which loops.
- The group comes from the typed-AST call graph (`scc.sprout`, as mutual-TCO Phase B's
  `pb_hetero_sccs` builds it), not from the IR's: the IR graph is built after translation, and
  routing renames the edges it would read.
- The result-worker tail chain obeys the same rule. A worker never chains a tail call into a
  worker of its own group: `@h_worker` and `@g_worker` of a mutual pair would recurse into each
  other, and the stack grows. The call stays boxed, and the boxed fns loop.

**Naming.** `@f_argw` shares the symbol namespace, as `@f_worker` does. A colliding site is not
routed (above), so no guard fires for it; `emit_worker_for`'s `_worker` guard still covers a
matched callee.

## 5. Prior art

- GHC: `-fworker-wrapper` splits a function into a wrapper and a worker that unboxes strict
  arguments; `-fcpr-anal` does the same for a result. Sprout's result workers follow the second,
  and this design the first. The user guide does not say whether it unboxes sum-type arguments,
  so no claim is made that GHC does this case.
- Rust: `Option<T>` is a value, never boxed, so it has no box to remove.
- OCaml: `Some x` is a heap block (manual, *Interfacing C with OCaml*). Flambda inlines a
  non-recursive function below a size threshold (`-inline`), which removes the block at that site.

## 6. Impact

- Syntax, semantics, types, diagnostics: none. Behaviour is identical; only allocation changes.
- Code size: one extra function per consumer that some call site routes to.
- Docs: `docs/compiler-internals.md` CPR section. `docs/idiomatic-sprout.md` §"Reach for a
  combinator on a single `Maybe`/`Result`" drops "NOT on a call" for top-level callees (a closure
  call still boxes). `docs/lint-rules-v0.md` §7 guard 4, including its claim that
  `cpr_result_known` declines a polymorphic `Maybe a` (it declines only a bare type variable).
  `docs/scalar-replacement-v0.md` §4 Stage 2 points here.
- BACKLOG: delete the P2 **The CPR peephole unboxes a `Maybe` that a `match` consumes** entry.
  Narrow the P3 lint entry to closure calls. File the three non-goal follow-ups.

## 7. Tests

IR shape, `tests/stdlib/test_ir_codegen_cpr_argw.spr`, written first and confirmed failing. The
IR-shape harness typechecks without the prelude (`test_ir_codegen_cpr_maybe_externs.spr`), so each
fixture declares its own consumer and producer. Golden IR covers the prelude's.
1. `with_default(0, get(i, v))` emits `IRCallUnboxed2` for `get` and a call to `with_default_argw`,
   and no boxed `get` call.
2. Mirror: `with_default(0, m)` with `m` a held value calls the boxed function.
3. A consumer over `Result`.
4. A consumer whose parameter occurs in an arm body gets no argument worker.
5. A parameter that shadows the consumer's name stays a closure call.
6. A partial application `with_default(0)` is not routed.
7. Self- and mutual recursion: a call to `f` from inside `f`'s group stays boxed, and the group's
   TCO is unchanged.
8. Scrutinee position, pinning the non-goal: `match or_else(get(i, v), fb) with` stays boxed.
9. Mutual tail recursion: the result workers of a pair `h` <-> `g` do not call each other, and a
   chain outside any group (`a` tail-calling `b`) still does.
10. A user fn named `check_argw`, or `p_worker`, beside `check(p(i))`: the program translates and
    the call stays boxed.

Behaviour, `tests/stdlib/`:
11. A `Maybe String` payload, with a later argument that allocates, under `SPROUT_GC_STRESS=1`.
12. A self-recursive consumer, entered through a routed call and recursing with a call argument,
    runs at depth 1,000,000.
13. Mutual tail recursion between two fns that both have result workers runs at depth 3,000,000,
    entered through an argument site and through a `match`.

Also: `test_bytes_byte_at_unboxed.spr` pinned the old rule (a `Maybe` handed to a helper is
boxed) and now pins the new one, for a Tier-1 extern producer.

## 8. Measured

`bench/vec_box`, ns/read, best of 3 runs, `-O2`, one machine; master's compiler (built from its
seed) against this one, the same stdlib source:

| size | `escaped`, master | `escaped`, argument CPR | `in_place` |
|---:|---:|---:|---:|
| 3 | 22.4 | 2.3 | 2.2 |
| 1,000 | 22.5 | 2.1 | 1.9 |
| 100,000 | 42.3 | 2.1 | 1.9 |

`-O2` inlines `@maybe_with_default_argw`: the binary has no call to it. The 0.2–0.3 ns left over
`in_place` is not a call. Probed on the prelude: `is_just(dict_get(..))` and
`maybe_with_default(.., dict_get(..))` route; `map(trim, dict_get(..))` and
`and_then(parse_int, dict_get(..))` do not, since `map` and `and_then` are class methods.
