# Linear containment table — v0

Status: landed (2026-10-31). Compiler-internal; no language change, not in the spec.

## Result

Same heavy compile, stage1 from the branch's base seed against this change, three alternating runs
each, macOS `sample` at 1 ms:

| | before | after |
|---|---|---|
| user time, mean | 3.75 s | 3.26 s (−13%) |
| `fn_linear_gate`, inclusive | 404 / 2919 samples (13.8%) | 42 / 2626 (1.6%) |
| `strcmp`, top of stack | 340 | 235 |

The temporary cross-check (query against walk on every call) ran over the self-compile, `just test`
and all examples with no disagreement before it was removed.

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
| **`LinEnv { env, lin: LinTables, marked: Set }`** | the opened declarations **and** the per-node strings and big-env lookups | 107 `env: Dict types.Scheme` parameters in `linear_check`, plus `LinearGate`, `resolve.head_var_fault` and three `infer` callers | **recommended** |

## Proposal

**1. `LinTables`, built once.** Add a record of small sets keyed by bare names, so no key string is
built:

- `holds: Set` — declared type names whose values hold a linear value;
- `phantom: Dict (List Int)` — for each type name, its phantom argument positions.

Scoped markers for type variables, skolems and `$ex_` ids come and go with scope, so they stay on
the env. Each gate collects them once: `marked: Set` holds the bare suffix of every `@linear:` key
in the gate's env, from one `dict_entries_with_prefix` scan. `linear_check` never adds a
`@linear:` key, so the set is exact for every query in the gate. Builtins such as `String` and
`List` carry no marker and are in neither set.

Build `holds` **by reachability**: `T` is in `holds` iff `first_linear_in(TConst T, scanned_env)` is
`Just`. The walk answers `Just` iff it reaches a `@linear:` name, whatever the visit order. So the
build lists, for each declared `T`, the names the walk reads or opens from `T`'s stored fields (a
constant; an application's head and its non-phantom arguments; a tuple's `Tuple<n>` and its items;
nothing for variables, functions and thunks), and runs one search from the marked names over the
reversed edges. It costs one pass over the declarations, not one walk per name.

Caching the walk's per-name answers *within* one walk would be wrong. In `type A = | A B Res` with
`type B = | B A`, the walk on `A` reads `B` as `Nothing` partway through. Reachability does not
cache answers; it asks only which names reach a leaf.

- **When:** `phantom` is built from the `@phantom:` markers always: they exist in every program, and
  a `where linear` bound runs the checks even where no linear type is declared. `holds` is empty
  while no name carries a `@linear:` mark.
- **Where:** from `scanned_env`, the output of `pre_scan_fn_decls` (~7565 in `infer`), before
  `check_published_linear_types` (~7599) runs. That check's wording already depends on
  `first_linear_in`.
- **Why once is enough:** the pre-scan registers every type, wrap and record declaration. The main
  pass's re-registration (~10718) passes the same alias env and adds nothing new. Only
  `mark_type_multiplicity` marks a type name linear, and it runs only during registration.

**2. The query** checks the type's constructor first and builds no string:

| `t` | Answer |
|---|---|
| `TConst n` | `n ∈ holds` or `n ∈ marked` (a skolem, `$ex_` or `existv<n>` is only in `marked`) |
| `TApp`, spine head `h` | `h` answered as `TConst` or `TVar` would be; if that says no, walk the arguments whose position is not in `phantom[h]` |
| `TTuple` | `Tuple<n> ∈ marked`, as today's walk reads only that mark, then the components |
| `TVar v` | `v ∈ marked` |
| `TFunc`, `TThunk` | `Nothing` |

**3. Witnesses keep today's walk.** When the query says no, the answer is `Nothing`. When it says
yes, today's `first_linear_seen` runs unchanged to find the type the diagnostic names. Witnesses are
used only in messages:

- `linear_check` ~289–298 and ~2194–2207;
- `infer` ~12038–12044;
- `resolve` ~380–387.

Decisions use only `Just` versus `Nothing`.

**4. Threading.**

- `LinEnv { env, lin, marked }` replaces `env: Dict types.Scheme` in `linear_check`; the change
  is mechanical. `make_lin_env(env, lin)` builds `marked` once per gate. The walk that finds a
  witness (`first_linear_witness`) and the env lookups that are not containment keep reading
  `env.env`.
- `build_lin_tables(scanned_env)` runs once, in `typecheck_decls_resolved`. The tables live in
  `unifier.InferState` (`set_lin_tables`, `get_lin_tables`), which already reaches every
  declaration check. `LinearGate` copies them out as `lin`; `linear_gate_error` wraps them with
  its per-gate `bound_env` into one `LinEnv`, shared by `fn_linear_gate` and
  `bound_obligation_error`.
- `resolve` rebuilds them in `build_tables`, from the env `TypedOk` carries, before any
  `$bound:` mark. `TypedOk` has 55 uses, so adding a field to it costs more than one extra build
  per `resolve_program`. `Tables` holds a `LinEnv`; `with_bounded` adds each `$bound:` mark to its
  env and its name to `marked`, with no rescan.
- Callers updated:
  - `fn_linear_gate` and `letdecl_linear_gate`;
  - `infer`'s `untracked_reason`, `mode_pair_fault` and `obligation_fault`;
  - `check_published_linear_types`;
  - `signature_modes_error` and `class_method_mode_error`, which only need the type's shape;
  - `resolve.head_var_fault`;
  - inside `linear_check`, `spine_param_holds_linear`.

## Premises

The table holds facts about declared type names, computed with no scoped markers present. Today's
walk instead reads scoped markers at every node, including inside opened declarations. The two
agree only while no scoped `@linear:` key can equal a name that appears in a stored field:

- `$t…` from `unifier.fresh`, `$sk…` and `$ex_…` from unpacks, and `$bound:…` from #796 all start
  with `$`, which no source identifier can. A declared name can: the entry module's types are
  `$entry.<Name>` and carry a real `@linear:` mark, so a check for "no `$` marks" is wrong. The
  scoped prefixes are `$sk`, `$ex_` and `$bound:`; none appears in `scanned_env`.
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
  across the full test corpus. Removed before landing.
- **Byte-identical:** every `type_error`, `linear*` and published-linear fixture. Then
  `test_linear_containment_cost.spr`, `just test` and `just dod`.
- **Cost:** re-run the profile. Report `fn_linear_gate`'s share (15.5% today) and the walk's
  lookup and allocation samples. The `rooting-cost-gate` and `scc-cost-gate` must not regress.

## Implementation steps

All four landed together (§Result).

1. ~~Semantics tests.~~
2. ~~`LinTables`, `marked` and how they are built, plus the query, checked against the walk on the
   semantics cases.~~
3. ~~Thread `LinEnv` through `linear_check`, `LinearGate`, `infer` and `resolve`, with the temporary
   cross-check at the query.~~
4. ~~Run the corpus cross-check, then remove it.~~ Removing it left `holds_linear` = `lin_holds` and
   `first_linear_in` = `if lin_holds then witness walk else Nothing`.
