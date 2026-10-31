# Linear containment table — v0

Status: proposed (2026-10-30). Compiler-internal; no language change, not in the spec.

## Problem

Once a bundle declares any linear type, `infer.fn_linear_gate` runs `linear_check.check_fn_linear`
on **every** function. Its main question is `first_linear_in(t, env)`: does type `t` hold a linear
value? `check_fn_linear` asks it for every binder, parameter, pattern and field.

`first_linear_seen` answers it by walking `t`. Each query starts from an empty `seen` memo. At every
node:

- it builds `"@linear:" ++ head_type_name(t)`, using `type_to_string` text for a `TFunc` or `TThunk`
  node;
- it looks that key up in the type environment, which has tens of thousands of entries;
- for each type argument, it builds a `@phantom:T:i` key and looks that up too;
- at each declared head, it opens the declaration's `@stores:` fields and walks them as well.

Measured on one heavy compile (`tests/stdlib/compiler/test_pattern_error_span.spr`, stage1 from the
seed at `962bc7ae`, macOS `sample`, 6519 samples):

| | |
|---|---|
| `fn_linear_gate`, inclusive | 1008 samples, **15.5%** of the compile |
| the walk (outermost `first_linear_seen`) | 678 samples |
| … of which inside `first_linear_in_stored` (declarations opened) | 218, about **a third** |
| `Dict String` lookup samples under `check_fn_linear` | 308: 72 inside an opened declaration, 236 on the queried type itself |
| `@linear:` env lookups | 1,755,965, of which **1,929 distinct** keys and **125 hits** |
| top repeated keys | `@linear:String` 511,663; `@linear:List` 227,045; `…source.SourcePos` 190,453 |

There are two costs:

- **Declarations opened again on every query:** about a third.
- **The per-node cost on the queried type itself:** most of the rest. Each node builds key strings,
  which also means allocation and garbage collection, and looks them up in the big environment.

The number of queries is not the lever. A per-query memo would need a key for a whole type, and
building that key costs as much as walking the type.

A runtime-only change (skipping known-equal key bytes in `bst_get`) bought 3.7%.

## Goals and non-goals

- Answer "does declared type `T` hold a linear value" once per program.
- Answer each query without building strings or looking anything up in the big environment.
- Accept and reject exactly the same programs, with byte-identical diagnostics. The premises this
  rests on are listed under §Premises.
- Not a goal: changing the `Dict String` runtime structure, or moving the other marker families
  (`@fwd`, `@eta_fwd` and `@super` stay where they are).

## Background: bound identities (#796)

`resolve.with_bounded` used to mark `where linear` variables as `@linear:<written name>`.
Declaration type variables in `@stores:` are kept verbatim, so inside `where linear a` every type
whose declaration stores an `a` (such as `List`) looked linear. #796 marks them as
`$bound:<written>` instead, and builds the dictionary type with that name.

## Alternatives

| Option | What it removes | Diff | Verdict |
|---|---|---|---|
| Per-query memo inside `check_fn_linear` | nothing per node; a hit still needs a key for the whole type | small | no: building that key costs as much as the walk |
| `@holds:T` presence markers in the env | the opened declarations, about a third | no signature changes | not enough alone: per-node strings and big-env lookups stay |
| **`LinEnv { env, lin: LinTables }`** | the opened declarations **and** the per-node strings and big-env lookups | 107 `env: Dict types.Scheme` parameters in `linear_check`, plus `LinearGate`, `resolve.head_var_fault` and three `infer` callers | **recommended** |

## Proposal

**1. `LinTables`, built once.** Add a record of small sets keyed by bare names, so no key string is
built:

- `holds: Set` — declared type names whose values hold a linear value;
- `phantom: Dict (Set Int)` — for each type name, its phantom argument positions;
- `scoped` stays on the env, as today's `@linear:` markers for type variables, skolems and `$ex_`
  ids, because those come and go with scope.

Build `holds` **by running today's walk once per declared type name**: `T` is in `holds` iff
`first_linear_in(TConst T, scanned_env)` is `Just`. Each name starts from an empty `seen`, exactly as
a query does today, so the table matches today's answers by construction. It needs no new fixed-point
code.

Caching the walk's per-name answers *within* one walk would be wrong. In `type A = | A B Res` with
`type B = | B A`, the walk on `A` reads `B` as `Nothing` partway through. A table built per name from
fresh walks never does this.

- **When:** only when `@any_linear` is present; otherwise the sets are empty.
- **Where:** from `scanned_env`, the output of `pre_scan_fn_decls` (~7565 in `infer`), before
  `check_published_linear_types` (~7599) runs. That check's wording already depends on
  `first_linear_in`.
- **Why once is enough:** the pre-scan registers every type, wrap and record declaration. The main
  pass's re-registration (~10718) passes the same alias env and adds nothing new. Only
  `mark_type_multiplicity` marks a type name linear, and it runs only during registration.

**2. The query** checks the type's constructor first and builds no string:

| `t` | Answer |
|---|---|
| `TConst n`, `n` declared | `n ∈ holds` |
| `TConst n`, anything else (skolem, `$ex_`, an `existv<n>` reached through a stored field) | the scoped `@linear:n` lookup, as today |
| `TApp`, spine head `h` | `h` answered as `TConst` or `TVar` would be; if that says no, walk the arguments whose position is not in `phantom[h]` |
| `TTuple` | `Tuple<n> ∈ holds` (a declared `Tuple2` behaves as today), then the components |
| `TVar v` | the scoped `@linear:v` lookup |
| `TFunc`, `TThunk` | `Nothing` |

**3. Witnesses keep today's walk.** When the query says no, the answer is `Nothing`. When it says
yes, today's `first_linear_seen` runs unchanged to find the type the diagnostic names. Witnesses are
used only in messages:

- `linear_check` ~289–298 and ~2194–2207;
- `infer` ~12038–12044;
- `resolve` ~380–387.

Decisions use only `Just` versus `Nothing`.

**4. Threading.**

- `LinEnv` replaces `env: Dict types.Scheme` in `linear_check`; the change is mechanical.
- `LinearGate` carries `LinTables`, and `linear_gate_error` wraps them with the per-gate
  `bound_env`.
- `resolve` receives the scheme env through `TypedOk`. It either gets `LinTables` alongside, or
  rebuilds them from the same markers; to be decided while implementing.
- Callers to update:
  - `fn_linear_gate` and `letdecl_linear_gate`;
  - `infer`'s `untracked_reason`, `mode_pair_fault` and `obligation_fault`;
  - `check_published_linear_types`;
  - `resolve.head_var_fault`;
  - inside `linear_check`, `spine_param_holds_linear`.

## Premises

The table holds facts about declared type names, computed with no scoped markers present. Today's
walk instead reads scoped markers at every node, including inside opened declarations. The two
agree only while no scoped `@linear:` key can equal a name that appears in a stored field:

- `$t…` from `unifier.fresh`, `$sk…` and `$ex_…` from unpacks, and `$bound:…` from #796 all start
  with `$`, which no source identifier can.
- **One exception:** `infer.signature_modes_error` (~11851) marks *written* names, including a
  class's own parameters. It is out of reach today only because its one consumer,
  `first_bad_modifier` → `ownership_fault`, looks at the head alone and never at containment. The
  deferred "containment in parameter modes" (`linear_check` ~2116) would reach it. A test pins the
  present behaviour: a class method with `borrowing Box Int`, where `type Box t = | Box t`.

## Tests

- **Semantics:** each case runs once to establish today's behaviour, and the expected outputs are
  then frozen.
  - the `A`/`B` cycle;
  - a record holding a linear field;
  - a wrap of a linear type;
  - `| Outer (Chan Res)` does not hold (`Chan`'s argument is phantom);
  - `| Outer (List Res)` holds;
  - a tuple component inside a stored field;
  - an existential field inside a non-linear wrapper;
  - a skolem head (`match p with | Pack x -> let b = Just(x) in …` still reports `x`);
  - a type-variable spine head (`f a` under `where linear f`);
  - the class-method case under §Premises.
- **Cross-check, temporary:** compare the query with the walk's `Just`/`Nothing` on every call,
  across the full test corpus. Remove it before landing.
- **Byte-identical:** every `type_error`, `linear*` and published-linear fixture. Then
  `test_linear_containment_cost.spr`, `just test` and `just dod`.
- **Cost:** re-run the profile. Report `fn_linear_gate`'s share (15.5% today) and the walk's
  lookup and allocation samples. The `rooting-cost-gate` and `scc-cost-gate` must not regress.

## Implementation steps

1. Semantics tests.
2. `LinTables` and how they are built, plus the query and the temporary cross-check. `linear_check`
   still reads the env.
3. Thread `LinEnv` through `linear_check`, `LinearGate`, `infer` and `resolve`. This step is
   mechanical.
4. Run the corpus cross-check, then remove it. Reseed, `just dod`, and profile before and after.
