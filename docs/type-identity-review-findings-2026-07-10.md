# Code-review findings — module-qualified type identity + opaque TypeId (2026-07-10)

Review of branch `feat/module-qualified-type-identity` (PR #156): T7 module-qualified
type identity + G6 opaque `TypeId` (`TConst String → TConst TypeId`, ~137 sites).
High-effort, recall-biased: 5 finder angles → adversarial empirical verification.

**Bottom line: no active bug. The branch is correct as shipped** (verified end-to-end:
full suite, compile-examples, smokes, self-compile fixed point, plus two targeted probes
below). Findings 1–4 are hardening / cleanup; none rejects a valid program or miscompiles.

**Resolution:** findings 1–4 applied in commit `8f506b5` (finding 5 — comments — left as
present-tense rationale). Full suite + fixed point green. Finding 1 added
`tests/stdlib/compiler/test_type_identity_dispatch_paths.spr` for the previously-untested
constrained-function and return-type dispatch paths.

## Headline correction (important)

All three correctness finder angles (line-by-line, removed-behavior, cross-file)
independently flagged a **HIGH** bug: *`concrete_typeexpr`/`type_to_typeexpr` strip the
module prefix when building dispatch TDict heads, so a module-qualified concrete type with
a typeclass instance would fail to resolve → spurious "No instance" / `__unresolved_`
rejection.*

**This was empirically REFUTED.** Two probes constructed the exact named triggers:
- **Constrained-function dispatch** — `type Box a = MkBox a` + `instance ToString (Box a)`
  + `fn describe(x: a) -> String where ToString a = to_string(x)` → prints `BOX`.
- **Return-type dispatch** — `type Box = MkBox Int` + `class Zero a { fn zero() -> a }` +
  `instance Zero Box` + `fn get_zero() -> Box = zero()` → prints `7`.

Why it doesn't reproduce: the `@inst` lookup uses the **full** `head_str`
(`infer.sprout:1400/1414`), and lowering's `ctx_inst` re-derives the effective dispatch
key from the runtime type via `first_concrete_head_str` (full). The stripped head stored
in the TDict is **vestigial** — never the effective key on any reachable path (self-compile
exercises heavy qualified-type dispatch and is a fixed point).

Lesson: the three finders were primed with the same risk model, so their convergence was a
shared bias, not independent confirmation. Static traces of this dispatch pipeline are
unreliable; the two probes settled it in seconds.

## Findings

### 1 — Stripped dispatch-head converters are inconsistent + untested (Medium, latent)
`stdlib/compiler/infer.sprout:986` (`type_to_typeexpr`) and `:995` (`concrete_typeexpr`)
build dispatch TDict heads with `type_id_display` (stripped), while the fixed sibling
`type_to_typeexpr_with_prog_vars` (`:1065`) uses `type_id_name` (full). All callers of the
two are dispatch-head feeders (verified — no display caller), so aligning them to
`type_id_name` is safe and removes the latent desync. The constrained-function and
return-type dispatch paths also had **no regression test** (the RED test exercises
`build_ctor_map`/exhaustiveness, not EvInstance dispatch on a module-local type).
**Action:** switch both to `type_id_name`; commit the two probes as regression tests.

### 2 — `is_type_var_name` copies lack the dot-guard (Low, latent)
`stdlib/compiler/resolve.sprout:644` and `stdlib/compiler/lowering.sprout:1450` inspect
only char 0; `infer.is_lowercase_name` (`infer.sprout:196`) carries `str_find(name,".")==-1`.
Reachable with a module-qualified parametric type + constrained instance
(`instance Show (main.Tree a) with Show a`): `is_type_var_name("main.Tree")` returns true
(leading `m`). Benign today — `substitute_type_expr` does keyed `dict_get`, the spurious
entry is an identity binding never looked up — but unguarded. A dotted name is never a type
variable. **Action:** add the `str_find(name,".")==-1` guard to both copies.

### 3 — `TypeId` API partially dead / not adopted (Low, cleanup)
`stdlib/compiler/types.sprout:948` (`type_id_is_concrete`) and `:957` (`type_id_symbol`)
have **zero call sites**; the code hand-rolls equivalents: `lowering.qualified_head` (=
`type_id_symbol`), `resolve.head_is_concrete` + `resolve.starts_upper` (= `type_id_is_concrete`).
The API was introduced to centralize identity handling; hand-rolled copies are how the next
display-vs-name desync creeps in. **Action:** call the API from those sites (wrap is
zero-cost), or drop the dead functions.

### 4 — `type_id_is_concrete` reimplements `type_id_display` (Low, cleanup)
`types.sprout:948` inlines `after_last_dot(type_id_name(id))`, verbatim the body of its
sibling `type_id_display` (`:943`). **Action:** `type_id_starts_upper(type_id_display(id))`.

### 5 — Comments narrate history (Trivial, convention — memory rule)
`infer.sprout:248,362`, `unifier.sprout:1012`, and the T7 blocks in `codegen`/`resolve`
narrate history ("Stripping here collapsed…", "silently unified…"), against the memory
rule `feedback_short_comments_no_history` ("Describe what the code IS, not the story of how
it got there"). Caveat: memory-feedback rule, not a checked-in `AGENTS.md`/`guidelines.md`
rule. **Action (optional):** trim to present-tense statements of the invariant.

## Cleared (verified NOT bugs)
- All prelude-literal compares (`type_id_name(name) == "Int"/"Maybe"/"Unit"/…`) — those
  literals are the prelude's unqualified canonical names; full-name compare is correct.
- `resolve.sprout:382` (`type_id_display`) — used only as a Just/Nothing presence guard.
- `resolve.sprout:422` (`type_id_display == "Unit"`) — `"Unit"` is unqualified; short==full.
- `unifier.sprout:190` exact `type_id_eq` — the intended T7 fix.
- `iface_codec` encode(`type_id_name`)/decode(`type_id`) round-trip — consistent.
- Ordering: `wrap TypeId` precedes `type Type`; `str_char_at` available; string helper arg
  orders match.
