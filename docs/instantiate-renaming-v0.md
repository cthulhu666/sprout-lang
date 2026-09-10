# Scheme instantiation is a simultaneous renaming (v0)

Status: implemented. Normative typing rules are unchanged — this is a correctness
fix to how `unifier.instantiate` applies a renaming, not a change to what types
mean.

## 1. Problem

`instantiate`, `instantiate_with_vars` and `instantiate_ctor_pattern` each build a
map from the scheme's binders to fresh variables (`build_type_repl`,
`build_effect_repl`, `build_ctor_pattern_repl`) and then apply it with
`apply_full_subst`.

`apply_full_subst` **chases**: after a lookup succeeds it re-applies the map to its
own result. That is correct for the unifier's substitution, where `α := β` and
`β := Int` must compose. It is wrong for a renaming, which is *simultaneous*:
every binder is replaced exactly once, by its own fresh variable.

The difference is invisible while the fresh names lie outside the map's domain.
Nothing made that true. `generalize` names binders from `ftv`, so an
inference-generated binder is spelled `$t<n>`; `fresh` mints `$t<n>` from a counter
that `unifier.new_state` starts at 0 for every check. A scheme minted by one check
and instantiated by another therefore collides by construction.

## 2. Evidence

Measured against the stage-1 binary, calling `unifier.instantiate` directly on a
constructed scheme with a state whose counter is at 0:

| scheme | map built | result |
|---|---|---|
| `forall $t2 $t0. $t2 -> $t0` | `$t2 ↦ $t0`, `$t0 ↦ $t1` | `$t1 -> $t1` — two independent variables fused |
| `forall $t0. $t0 -> $t0` | `$t0 ↦ $t0` | never returns (10s timeout, exit 124) |
| `forall $t1 $t0. $t1 -> $t0` | `$t1 ↦ $t0`, `$t0 ↦ $t1` | never returns |

Effect variables have the identical defect through `apply_effect_subst`'s own
chasing lookup, so a two-cycle over `$e<n>` binders hangs the same way.

Fusion is the more informative half: same root cause, no hang, and a wrong answer
that no timeout would ever surface. `forall a b. a -> b` instantiated as `a -> a`
is a type the scheme does not have.

## 3. What the earlier diagnosis got wrong

`BACKLOG.md` recorded this as a cyclic *unifier substitution* — "consistent with a
binding `α := … α …` that an occurs check should make impossible, though that is
not proven". The reasoning was sound from the symptom alone (flat RSS, unbounded
time, a stack cycling `apply_full_subst`), but it pointed at the wrong map. The
cycle is in the *renaming*, which never passes through `bind_var` and which no
occurs check ever inspects. The two maps are distinguishable only by looking at
what is in them, which is what the probes above did.

`tests/stdlib/compiler/test_fresh_tvar_collision.spr` already described this exact
failure — "`apply_full_subst`'s transitive lookup fuses two type variables the user
declared as independent" — and closed it for *user* identifiers by prefixing fresh
names with `$`. That invariant is about a namespace. The one actually needed is
about a single map, and a namespace prefix cannot supply it: both colliding names
are compiler-generated.

## 4. Fix

Mint a renaming's targets outside its own domain:

```sprout
fn fresh_outside(state: InferState, domain: List String) -> String !{IO} =
  do
    name <- fresh(state)
    if list_member(name, domain) then fresh_outside(state, domain) else name
```

`build_type_repl`, `build_effect_repl` and `build_ctor_pattern_repl` thread the
whole binder list as `domain` (not the shrinking tail — a target minted for the
first binder must dodge the last one too) and mint through `fresh_outside` /
`fresh_effect_outside`.

With range ∩ domain = ∅ every lookup terminates in one step, and chasing then
*equals* simultaneous substitution. `fresh_outside` terminates because the counter
only rises and the domain is finite.

Skolems are unaffected: `instantiate_ctor_pattern` maps an existential binder to a
`TConst` named `$sk<n>`, which `apply_full_subst` never looks up.

### Alternatives rejected

- **A visited-set guard inside `apply_full_subst`.** Stops the hang and leaves the
  fusion, since fusion needs no cycle. It also puts a special case on the one walk
  that the unifier's real substitution shares.
- **A separate non-chasing `apply_renaming` walk.** Correct, and states the intent
  in the code, but duplicates the traversal to buy what the disjointness
  precondition already guarantees.

## 5. Tests

- `tests/stdlib/compiler/test_instantiate_renaming.spr` — the fusion cases across
  all three entry points, plus the effect-variable twin and a source-spelled-binder
  control. All terminate before and after the fix, so they belong in `just test`:
  before it, four of the five fail.
- `just test-renaming-termination` (`tests/renaming_smoke/cyclic_renaming.spr`) —
  the cycle cases, run under a 30s alarm. These cannot be in-process assertions:
  the red signal is a hang, and an in-process `.spr` test would hang `just test`
  rather than fail it.

## 6. Reachability

The user-visible incident was an LSP session that stopped answering: a `didOpen` on
`examples/concurrent_fetch.sprout` never returned, and the server's loop is
single-threaded (`docs/module-surface-authority-v0.md` §7.1).

That path is `module_loader.load_module`, and it collides structurally rather than
by accident. Each module is checked by `checker.check_program_with_env`, which
calls `unifier.new_state` — a counter from 0 — and the schemes it returns are
handed to the *next* module's `check_program_with_env`, which starts its own
counter at 0 and instantiates them. Every import boundary on that path is a fresh
counter meeting binders minted by an earlier one. `bundler.LoadEnv`'s scheme memo
widens the same window across checks.

Retiring the env path (§7 of that doc) removed the route the editor took, which is
why the entry was filed as latent. Nothing else made it unreachable: the memo
still exists, and any future caller that instantiates a scheme it did not itself
generalize re-opens the hole. The fix is in the renaming, so it holds regardless
of which caller supplies the scheme.
