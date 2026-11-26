# Record scalar replacement: inline record builders, split record lets (v0)

Status: stages 1 and 2 landed (PR #915); follow-ups in §8. Issue #913. A compiler optimisation: no syntax, semantics, typing or diagnostic change, so
nothing here is normative and `docs/spec-v0.md` is untouched.

## 1. Problem

A record built and consumed at once is heap-allocated. `stdlib.linalg.vec3.Vec3` is
`(x: Double, y: Double, z: Double)`; every `vec3` op builds a fresh one. Stage1 IR today
(`--emit-ir`, verified):

- `vec3.add(a, b)` loads 6 fields with `sprout_field`, then calls `vec3.new`, which calls
  `sprout_alloc_obj(16, 3)` and stores 3 words.
- uncharted-suns `loam/quat.sprout`:
  ```
  q_mul(a, b) = Quat(w = (a.w * b.w) - vec3.dot(a.v, b.v),
                     v = vec3.add(vec3.add(vec3.scale(a.w, b.v), vec3.scale(b.w, a.v)),
                                  vec3.cross(a.v, b.v)))
  q_rot(q, u) = let t = vec3.scale(2.0, vec3.cross(q.v, u))
                in vec3.add(vec3.add(u, vec3.scale(q.w, t)), vec3.cross(q.v, t))
  ```
  `q_mul` allocates 5 `Vec3` and keeps 1; `q_rot` allocates 6 and keeps 1. Each intermediate is
  held in a GC root across the next call.

Shipped scalar replacement (`scalar-replacement-v0`, `arg-cpr-v0`) covers tuples and width-2
ADTs consumed by `match`. Records are consumed by `.field` and by being passed on, and
`cpr_result_known` rejects every record, so none of it fires.

**Measured.** Local bench (2M iterations of `q_mul` + `q_rot`, `-O2`, stage1): boxed 400–495 ms,
hand-expanded 110–119 ms, identical output. uncharted-suns `bench_body_stack.spr`: 281 µs boxed,
194 µs with those two hand-expanded (issue #913).

## 2. Goals and non-goals

Goals:
1. `q_mul` and `q_rot`, unchanged, allocate only the records they return.
2. Local bench within 15% of hand-expanded; uncharted-suns bench at or below 194 µs.
3. No IR op, ABI, rooting or runtime change. Every rewrite is typed AST to typed AST.
4. Inline only where a record allocation goes away. No other call site changes.

Non-goals (follow-ups, each with a negative test):
- A general inliner.
- `do`-block `let` (`TDoLetStep`). v0 handles `let..in` / `where` only.
- Splitting through `if`: a builder whose tail is not a single record literal does not inline.
- Records that cross a call that does not inline, or are stored in a structure.
- Generic or constrained callees.

## 3. Alternatives, by layer

- **Caller:** hand-expand (the 194 µs figure). Loses: unreadable; every user learns a codegen rule.
- **Function, by field-wise worker copies** (`f$flat`, drafted and reviewed first). Loses: a
  second symbol per function that every router must agree on (the #631 class), and
  `cpr_result_known` rejects records, so the shared gate has to be rebuilt.
- **Function, by extending the do-block SRA map** in `ast_to_ir` (`translate_do`, `sra_core_eligible`)
  to records. Loses: it only splits a binding whose producer is a routable worker call, so it
  needs record workers anyway (the previous bullet).
- **Function, by a demand-driven inliner + let splitting** (this design). Costs code growth at
  sites where an allocation goes away; nowhere else.
- **Representation:** records as values everywhere (C/Rust structs; OCaml stores an all-`float`
  record flat). Loses: breaks the one-word uniform ABI that generics, the GC and the runtime use.

## 4. Design

New module `stdlib/compiler/rec_inline.sprout`, one entry `expand_program`, called in
`ir_pipeline.compile_program_streaming` **after** `dce.elim_unreachable` (the program is ~98%
dead before it; a builder that no longer has callers stays emitted, which costs only size).
Gated by `opt_config.Pass` `RecInline` (`rec_inline`, on by default), so
`SPROUT_OPT_OFF=rec_inline` turns it off for A/B and bisecting.

### 4.1 Eligible callee

A top-level `TFnDecl f` is *eligible* when all hold:
- the name need not be dotted: a prelude callee inlines too. The pass runs on any program,
  bundled or not, and does not lean on the bundler's local rename
  (`docs/prelude-name-identity-v0.md` §5): that rename leaves a `no_prelude` entry's own names
  bare, and an unbundled program has none. Instead each callee records its *free names*, the
  top-level names its prepared body reads (through the calls already inlined into it), and the
  walk keeps `locals`, every name a binder above it binds (declaration parameters, lets, match
  arms, lambda parameters, `do` steps). A call whose callee has a free name in `locals` stays a
  call, since the copy would read the local. A let whose builder call stays is not flat (§4.2);
- every parameter and the return are annotated, and the annotations name closed types (no type
  variable); no parameter is a lowered dictionary (`__tc_`);
- pure: no effect row, or an empty one (`vec_wrappers.is_pure_decl`);
- not in a recursive group (`scc.call_graph`, the graph `scc.recursive_groups` indexes) and not
  mentioning itself;
- its result type is a non-linear single-constructor record, or `Int`/`Double`/`Bool`/`Char`.
  Never an ADT or tuple: CPR routes those by the shape of the call, and an inlined call is no
  longer a call;
- its body, after its own calls are prepared (§4.5), is zero or more lets followed by a *simple
  tail*: `TVar`, literal, `TBinary`, `TUnary`, `TGetField`, `TRecord`, or `TCall` of a top-level
  name; no lambda, `match` (other than a let), `do`, `if` or record update; at most `K = 64` nodes.

A *builder* is an eligible callee whose tail is one `TRecord`.

A parameter is *field-only* when each occurrence in the body is the base of a `TGetField`, or an
argument at a field-only parameter of an eligible callee. Computed callee-first, so
`length(v) = sqrt(length_squared(v))` and `length_squared(v) = dot(v, v)` both have `v`
field-only.

### 4.2 Demand-driven trigger

The walk carries a demand, `OnlyFields` (the value is only read by field) or `AsValue`, and a
set of *flat* variables: those bound by a let that is about to split.
- `let x = e in b`, where `e` is a record literal or a builder call and `x` is *splittable* in `b`:
  `x` is flat. Splittable (`splittable`) is a syntactic check on `b` that allocates nothing and
  does not depend on what inlines: every occurrence of `x` is a `TGetField` base or an argument
  at a field-only parameter of an eligible callee that gets every parameter. Walk order: `e`
  walks first, under `OnlyFields`; then `b`, with `x` flat only if the walked `e` ends in a record
  literal (past pass-made lets), since a builder call the budget kept from inlining leaves `x` an
  allocated result and nothing may inline on it; then the split (§4.4) runs on what the walk
  produced. A call in `b` that takes the flat `x` at a field-only parameter inlines by the
  next rule. This breaks the cycle "`t` splits iff `scale(q.w, t)` inlines iff `t` is flat"
  with one walk. A let that is not expected to split walks `e` under `AsValue`, and `x` is not
  flat in `b`.
- `f(args)` under `OnlyFields`, `f` a builder: inline.
- `f(args)`, `f` eligible, under either demand: inline if some field-only parameter `k` gets a
  *flat argument*: a builder call, a literal of a non-linear record, or a flat variable.
- Either way the inline needs the budget (§4.5).
- When `f` inlines, each argument that is not a variable or literal is let-bound first (§4.3).
  That let is an ordinary let: the walk decides its demand by the first rule, so a builder-call
  argument at a field-only parameter is walked under `OnlyFields` and its let splits, and any
  other argument is walked as a value.

Nothing else inlines. `dot(a.v, b.v)` stays a call (its arguments are field loads, not flat).
`add(a, b).x` stays a call (v0 does not fold a projection of a call).

### 4.3 Inlining a call

`f(a_1..a_n)` becomes the body with each parameter replaced:
- an argument that is a variable or literal substitutes directly. This keeps a split variable
  visible to §4.4 (`scale(q.w, t)` puts `t.x` in the body, not `let v = t`);
- any other argument is let-bound first, in argument order, to a fresh `p_i$N`.

**Every binder in the copied body is renamed** to a fresh `$N` name per site; `N` is a pass-wide
counter and `$` cannot occur in a user identifier, so no copy can capture or be captured.
Arguments evaluate once each, left to right, before the body: the call's order. Inlined nodes
keep the callee's source positions, so a trap reports the same line as before.

### 4.4 Splitting a record let

`let x = e in b` splits when:
- `e`, after the walk, is zero or more lets then `R(f_1 = e_1, …)`, `R` a non-linear
  single-constructor record. Leading lets float out first:
  `let x = (let y = e0 in r) in b` → `let y = e0 in let x = r in b`. Safe because the floated
  binder is fresh (§4.3); a user-named inner let does not float and blocks the split;
- every free occurrence of `x` in `b` is the base of a `TGetField`, or an argument at a field-only
  parameter of a call §4.2 will inline. The walk is scope-aware: it stops below a pattern,
  lambda parameter or `do` step that rebinds `x`.

The split emits `let x$f$N$ri = e_f` for each field **in declaration order** (the order
`record_exprs_in_decl_order` lowers a literal in today), with a fresh `N` per split, and
rewrites each `TGetField x f` to `TVar x$f$N$ri` at the field's type. A lambda that read `x.f` now
captures the scalar; closure conversion handles it like any local. User lets split too
(`q_rot`'s `t`).

### 4.5 Order and budget

Callee bodies are prepared callee-first (their own eligible calls processed) and cached. A callee
whose result is a record is prepared with `OnlyFields` demand, so a tail call to a builder inlines
(`vec3.add`'s tail `new(..)` becomes its record literal). Each caller body is walked once: §4.2
decides, §4.3 inlines, §4.4 floats and splits on the way back up.

The copy of an inlined body is walked again, so the callees it calls inline in turn, and a chain
of callees that each call the next several times grows exponentially: with `h1 = h0 + h0 + h0`
down to `h6`, one flat call to `h6` would become 3^6 copies of `h0`. Growth is therefore bounded
by a budget per declaration (and per instance method, and per callee preparation) of
`inline_budget = 256` nodes. An inline spends the node count of the callee's prepared body; a
call the remaining budget cannot cover does not inline and stays a call. A let that expected the
call to inline then keeps a bare use, so its record stays boxed: always correct, and the budget
only costs the allocation it would have removed. `q_mul`, `q_rot` and `distance` fit.

A callee whose raw body exceeds `2 * inline_limit` nodes is not prepared: the walk shrinks a body
only a little (a split drops a record literal and its field reads), so it would stay over
`inline_limit` and be refused anyway.

### 4.6 What it does to the targets

`q_rot`: `t` is used as `scale(q.w, t)` and `cross(q.v, t)`, both field-only, so `t` is
splittable and `scale(2.0, cross(q.v, u))` is walked with `OnlyFields`: `scale` inlines (builder),
its `v` argument `cross(..)` inlines (builder under `OnlyFields`). The outer `add` gets the flat
`add(u, scale(q.w, t))` and `cross(q.v, t)`; both inline, and so does the outer `add`. Its tail
`Vec3(..)` is the one result. 6 → 1.

`q_mul`: the outer `add`'s arguments are builder calls, so it inlines, and so do both inner
`scale`s, the inner `add` and `cross`. `dot` stays a call. The final `Vec3` literal is the
`Quat` field. 5 `Vec3` → 1.

`vec3.distance(a, b) = length(sub(a, b))`: `length` is eligible, `v` field-only, `sub(..)` is a
builder: inlines, no allocation.

## 5. Prior art (verified against primary sources)

- HotSpot C2 (`src/hotspot/share/opto/escape.hpp`, `macro.cpp`): an object is `NoEscape` when it
  "does not escape method or thread and it is not passed to call. It could be replaced with
  scalar"; one passed to a call is `ArgEscape`. `PhaseMacroExpand::eliminate_allocate_node` runs
  `scalar_replacement` only for an allocation escape analysis left scalar-replaceable. This
  design's order is the same: remove the call (inline), then split.
- GHC: `-fcpr-anal` "enables the worker/wrapper transformation … to unbox the result of a
  function"; `-fworker-wrapper` covers "unboxing strict arguments" (user's guide,
  using-optimisation). The worker route §3 rejects.
- OCaml: "records whose fields all have static type float are represented as arrays of
  floating-point numbers" (manual, *Interfacing C with OCaml* §3.2). The representation route.

## 6. Impact

- Syntax, typing, semantics, diagnostics: none. Field values and arguments keep their evaluation
  order (§4.3, §4.4), so traps and effects happen in the same order with the same text.
  Linearity is checked in inference only; linear records are excluded.
- Code size: grows only where a split happens.
- Compiler self-compile: the compiler builds `SourcePos` and other small records, so expect some
  seed change, at sites where a record no longer allocates. Read the seed diff; run
  `rooting-cost-gate` and `scc-cost-gate`.
- Docs: this file as `docs/record-sra-v0.md`; `docs/opt-passes-v0.md` row; `opt_config`
  (`pass_name`, `pass_from_name`, `all_passes`, `implemented`); `docs/compiler-internals.md`
  pass list; `docs/prelude-name-identity-v0.md` §5 (a post-bundling pass now writes `$` locals);
  `scalar-replacement-v0` follow-ups.
- BACKLOG: narrow P2 **Unbox small fixed-shape numeric records** to what stays boxed; file
  `do`-let splitting and splitting through `if`.

## 7. Tests (written first, confirmed failing)

IR shape runs through the bundler (a fixture under `tests/`, `--emit-ir`). Capture by a local that
shares a top-level's name: `tests/conformance/run/rec_inline_local_shadows_top_level.spr` (a
`no_prelude` entry, where the names are bare) and, on typed ASTs of an unbundled program,
`tests/stdlib/compiler/test_rec_inline_pass.spr`.
- `q_mul`/`q_rot` copies: exactly one `sprout_alloc_obj(_, 3)` each, no call to
  `vec3.add/scale/cross`; `dot` still called. `distance`: no allocation.
- No change: a builder whose result escapes (`let v = add(a, b) in [v]`), `add(a, b).x`,
  `match g(add(a, b))` with `g -> Maybe Double` (still routes to `@g_worker`).

Behaviour, results identical with `SPROUT_OPT_OFF=rec_inline`:
- nested ops; a user let used twice; used under a lambda;
- shadowing: by a match arm, by a lambda parameter, **by a same-name let**
  (`let t = V(..) in let t = V(x = t.y, ..) in t.x`);
- a literal written out of declaration order whose fields trap differently;
- an effectful argument's order; a trap inside an inlined body reports the callee's line;
- nested callees past the budget (`h6` calling `h5` three times, down to `h0`): the result is
  exact and the caller's IR stays small (`b1-gate`, `fixture_rec_inline_blow.spr`);
- a `do` step that rebinds `t` hides it from the steps after it only (`fixture_rec_inline_do.spr`);
- a builder with an empty effect row `!{}` inlines.

Negatives (still a call): recursive, unannotated generic, constrained (dict param), effectful,
over `K`, `if`-tailed builder, linear record, partial application, `do`-let. `specialise` runs
before this pass, so a constrained function called at a concrete type is a closed copy with no
dictionary and does inline; the constrained negative needs a polymorphic caller.

Then smoke shape, golden IR (read the diff), GC stress, reseed, `just dod`, bench.

## 8. Stages

1. ~~**Split only** (§4.4 without inlining): user `let`s bound to a record literal.~~ Landed.
2. ~~**Inliner** (§4.1–4.3, 4.5) on top: reaches `q_mul`, `q_rot`, `distance`.~~
   Landed. `q_mul`'s prepared body is over `K`, so it is not itself a callee. The bench ran
   (2M iterations of `q_mul` + `q_rot`, -O2, 9 interleaved runs, best/median) 374/383 ms with
   `SPROUT_OPT_OFF=rec_inline`, 125/127 ms with the pass, 104/104 ms hand-expanded. The compiler's
   self-compile time does not move outside noise (4.1–4.3 s either way; IR +0.15%). The gap is
   repeated `q.v` loads: `sprout_field` was an opaque call, so LLVM could not merge them.
   Declaring it `memory(read)` in the emitted IR gave 87.6 ms for both.
3. Follow-ups: `do`-let and `if`-tailed builders, now the BACKLOG entry **`rec_inline` gaps**;
   callees compiled though every call inlined, BACKLOG **Fully inlined callees are still
   compiled**. The `q.v` reload gap is closed: field reads lower to an inline load, which LLVM
   merges. Same bench: 126/128 → 62/64 ms with the pass, hand-expanded 104/106 → 62/64 ms.
