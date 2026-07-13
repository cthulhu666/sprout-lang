# Retro: `vec_sort_by` projection-sort dispatch crash (2026-07-13)

Status: **retrospective** — non-normative. Records the analysis behind the fix in
commit `ad216b8` (PR #176) and the follow-up backlog items it motivated.

## 1. What the bug was

`vec_sort_by(f: a -> k, vec: Vec a)` with **key type ≠ element type** (`k != a`
— e.g. sorting `Vec (Int,Int)` by an `Int` field, or `Vec IntRange` by
`range_start`) SIGSEGV'd. The `Ord k` obligation resolved to `Ord a` (the
**element** type) instead of `Ord k` (the projected **key**), so small `Int`
keys were compared through the tuple `Ord` dictionary and dereferenced as
pointers.

It was pre-existing (the old top-down merge sort crashed identically) and is the
uncovered sibling of the #141 `scan_fwd_markers` fix — the exact "source-name vs
generalized-name marker mismatch" that #141's own follow-up note deferred.

## 2. Did we fix a crux?

**Yes for this bug's family; no for the architectural root.**

The fix (`canonicalize_constrained_markers` in `check_fn_body`) rewrites each
`@constrained_N:name` marker's constraint var from its source name (`k`) to its
canonical post-`s2` generalized name (`apply_subst(s2, prog_to_fresh[source_var])`
— by construction the generalized scheme's ftv for that constraint). The
call-site `dict_get(prog_var, prog_to_fresh)` then hits the existing, tested
"Just" branch and resolves `Ord Int`. It lands at the one point where the body
substitution `s2` is available, and it converged to a self-compile fixed point.
That is a real crux, not a band-aid.

But the **architectural** root is untouched: **type-variable identity is
name-based.** A tyvar is tracked through at least three naming worlds — source
(`k`), instantiation (`$t478`), post-unification (`$t479`) — reconciled by a set
of string-keyed side tables (`prog_to_fresh`, `@fwd`, `@eta_fwd`, `@constrained`).
Every reconciliation point can silently diverge. #141 fixed one (forwarding); this
fixed another (concrete resolution at external call sites). **The fix adds a
fourth reconciliation mechanism rather than removing the need for reconciliation** —
correct scaffolding on a shaky foundation. Until tyvar identity is canonicalized
(see backlog item 4), expect more siblings.

Latent fragility worth noting: the fix stores `$t479` in the marker, stable only
because full-bundle compilation shares one fresh-name counter. If the iface path
(`--emit-iface`) ever becomes a required gate and its decoder renumbers scheme
TVars, this assumption breaks. (`iface_codec.sprout` does not currently serialize
`@constrained` markers, so it is not exercised today.)

## 3. Why it hid so long

Three compounding masks, each a generalizable lesson:

1. **The convenient special case worked.** `vec_sort` (key = element) was fine;
   only the projection (key ≠ element) crashed, and tests covered the special
   case. The tell was loud and ignored: the stability test was *deferred* because
   it needed key ≠ element and hit this crash. **A deferred test that cannot run
   because of a suspected bug is a P1 signal, not a footnote.**

2. **Compile-only gates cannot see dispatch bugs.** A wrong dictionary is a
   *runtime* SIGSEGV, not a type error (dispatch is unenforced). `compile-examples`
   compiled `aoc_2025_day_5` happily; it "passed" only because it never *ran* with
   input, so the sort never compared two elements. **`compile-examples` needs a
   run-with-fixture tier for representative programs.**

3. **Resolution silently guesses instead of failing.** The defect enabler is
   `scan_prog_to_fresh_for_instance`: an order-dependent heuristic that, when the
   precise lookup misses, picks a plausible-but-wrong dict *without complaint*. A
   soundness-critical decision should never have a silent guessing fallback.

## 4. What would have prevented or diagnosed it

Ranked by leverage; tracked as backlog items 1–4 under
"Compiler Internals Follow-Ups → Dispatch Soundness & Diagnostics".

1. **Typed-IR / Core verifier for dictionary passing** (highest prevention
   leverage). The smoking gun was visible in the IR: `Ord (Int,Int)` threaded
   where `Ord Int` was required. The compiler already emits typed IR (the PR11
   campaign). A check at the call boundary — "the dict threaded for `Ord k` has a
   head matching k's resolved type" — turns this *entire family* (this bug, #141,
   the `++`/`mconcat` null-fill, return-type-dispatch bugs) from runtime SIGSEGVs
   into compile errors. This is GHC Core-lint's idea: elaborate dictionaries to
   explicit terms and type-check the elaboration.

2. **Reusable `--trace-dispatch` diagnostic flag** (highest diagnosis leverage).
   This investigation cost two stage-2 rebuilds and hand-rolled `ETA_DBG` panics.
   A permanent flag logging, per constrained call site,
   `(callee, constraint, prog_var, prog_to_fresh keys, path taken, chosen dict)`
   would have printed `Ord k → scan_prog_to_fresh_for_instance → Ord Tuple` in one
   run. The diagnostic built by hand should be a checked-in tool.

3. **Make the heuristic loud, not silent.** Short of full canonical identity: when
   `resolve_obligation` falls through to the order-dependent fallback for a
   constraint that should have resolved precisely, emit a diagnostic (or hard
   error, like the existing `__unresolved_` dict sentinel). Precise-or-loud beats
   precise-or-guess.

4. **Canonical type-variable identity** (the architectural root fix). Unique IDs
   assigned at binding, preserved through instantiate / generalize / unify.
   Removes all four reconciliation tables and the entire bug class. Large,
   bootstrap-critical — the only thing that stops the sibling stream.

## 5. Recommendation

Do **item 1** next: highest value-per-effort, within reach given typed codegen
exists, and it retroactively guards every dispatch fix already made. Pair with
**item 2** so the next investigation is 20 minutes, not an afternoon. **Item 3**
is a cheap interim safety net. **Item 4** is the correct end state but should be a
deliberate project, not a reactive scramble.
