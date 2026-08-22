# The `no_prelude` floor: a core the opt-out cannot remove

Status: **proposal, unapproved.** Nothing here is implemented. The boundary in
§4.1 is the decision this document exists to get made; §5–§8 describe what
follows once it is.

Companion to [prelude-scope-v0.md](prelude-scope-v0.md), which made the prelude
unconditional and added the `no_prelude` opt-out. This document answers the
question that one deferred: **what does the opt-out actually remove?**

## 0. What this corrects

Three records in the repo describe this area, and each is wrong in a way that
changed the shape of the work. Correcting them is part of the deliverable.

1. **`BACKLOG.md` §7.2 — "the IR path performs no unbound-name check at all",**
   prescribing a bundle-time validator that rejects a call to a name no bundled
   module declares. **The stated cause is false.** `infer_var`
   (`infer.sprout:972`) rejects an unbound name and always has. Measured on
   `0e1e6fd2`:

   | file | called name | result |
   |---|---|---|
   | normal | `totally_bogus_name` | `ERROR: check: Unknown variable`, exit 1 |
   | `no_prelude` | `totally_bogus_name` | `ERROR: check: Unknown variable`, exit 1 |
   | `no_prelude` | `range_count` | **exit 0, `call @range_count` with no define** |

   Only the prelude's own exports escape the check, and only because they are in
   the checker's seeded environment without being in the bundle. The validator
   that entry asks for would be a scope-aware traversal of every expression form;
   it is not needed and should not be built.

2. **`BACKLOG.md` §Compiler/Stdlib Misc — "`load_prelude_pairs` is now
   redundant … Deleting the injection is therefore both the root-cause fix and a
   code removal."** The diagnosis is right and the prescription is wrong.
   Deleting it is a 11-line removal that turns 31+ clang errors into proper
   diagnostics **and breaks 9 names that work today** (§1). That entry did not
   know about the overlap in §1's second table, so it under-scoped the change as
   mechanical when it is a semantics decision.

3. **`tests/conformance/run/no_prelude_directive.spr` — "`print` is
   special-cased in the compiler rather than declared in the prelude, which is
   what lets a preludeless file produce output at all."** False. `print` is
   declared at `stdlib/prelude.sprout:1292` as `extern fn print(val: a) -> Unit
   !{IO}`, and that declaration is the only source of its *type*. The fixture
   compiles today because the seeded environment supplies it, not because the
   compiler special-cases the scheme — the special-casing is real but lives in
   inference and lowering, never in the type.

**And the history the fix has to respect.** The injection this touches was added
deliberately. Commit `bd90e4b5` ("refactor: derive tcp/http/runtime builtins
from extern fn declarations", 2026-05-18) says:

> fix compiler.sprout / lower_driver.sprout to pass prelude_pairs to
> typecheck_typed (was Nil, causing "Unknown variable: print" failures for bare
> files via the FullPipeline path)

It was `Nil`; it was changed *to* `prelude_pairs` precisely to fix `Unknown
variable: print`. The same commit **removed** `tcp_ext_entries()` and
`http_ext_entries()` from `checker.sprout`, relocating those schemes into
`extern fn` declarations — establishing the direction this document follows:
**the prelude's `extern fn` block is the single authority for a runtime
builtin's type, and hardcoded scheme tables in the compiler are to be
eliminated, not added.**

What changed since is the *justification*, not the mechanism. The seeding existed
so that **bare** files got `print`, because bare files received no prelude.
`prelude-scope-v0.md` deleted that withholding; the compensation for it was left
behind. Its only remaining job is propping up `no_prelude`.

## 1. Problem statement

`no_prelude` cuts the checker's source of truth for runtime builtins and leaves
the lowerer's untouched. The two were never the same set.

- The checker learns a builtin's **type** from `stdlib/prelude.sprout`'s `extern
  fn` block — 40 declarations, described in that file as "the authoritative type
  signatures for the Sprout type checker".
- The lowerer learns whether a builtin **exists unconditionally** from
  `ir_lowering.is_hardcoded_intrinsic` (`:604`) and the hardcoded `ir_header`
  string (`:472`–`:482`), which are emitted into every program regardless of the
  prelude.

Those sets overlap on 9 names and disagree on 31. Measured on `0e1e6fd2`, in a
`no_prelude` file:

| group | n | today | if the seeding is simply deleted |
|---|---|---|---|
| extern **and** intrinsic — `argv_get char_to_string eprint int_to_string panic print str_compare str_concat str_slice` | 9 | compiles, links, **runs** | **rejected** — regression |
| extern only — `str_len str_find str_starts_with double_to_string char_to_str char_from_codepoint to_double string_concat_many str_char_at vector_* map_* native_set_* ref_*` | 31 | typechecks, then `clang: use of undefined value '@str_len'` | correct diagnostic |
| prelude Sprout `fn` — `range_count`, … | — | typechecks, then `clang: use of undefined value '@range_count'` | correct diagnostic |

Verified end to end, not inferred: `int_to_string` prints `7`, `eprint` prints
`hi`, `str_concat` prints `ab`, `panic` aborts with `runtime error: boom` — all
in `no_prelude` files, all on an unpatched `0e1e6fd2`.

Two consequences worth stating plainly.

**The reported defect is not one bug.** `range_count` escaping to `clang` is one
instance of a systematic disagreement between two tables. Fixing that instance
without fixing the disagreement leaves 31 names in the same state.

**The gates cannot see this.** A prototype that deleted the seeding passed the
full `just test` suite and `ir-golden-diff` 60/60 — while breaking all 9 names in
the first row. No fixture anywhere exercises those names inside a `no_prelude`
file. Whatever lands must include a capability matrix (§9), because the existing
gates' silence here is not evidence of anything.

### 1.1 Who actually needs the opt-out — and what that implies about this design

Sizing a floor requires knowing who stands on it. Measured against the whole
in-repo population of `no_prelude` files, and the reason each one gives *in its
own header comment*:

| | files | why it opts out | permanent? |
|---|---|---|---|
| **A. Absence proofs over emitted IR** | `test_wrap_codegen`, `test_tuple_return_cpr` | "ABSENCE proofs over the whole emitted module — 'this IR contains no call to `@sprout_alloc_obj`' — and the prelude allocates and reads fields all over itself, so a bundled prelude makes such a proof unstatable" | **Yes.** No compiler fix removes this. |
| **B. Masking two known compiler bugs** | `instance_check`, `type_classes` (`invalid redefinition of function '__cm_Eq_eq'`); `codegen_do_bind`, `test_ir_codegen_do_bind_strip` (`Int vs demo.Maybe Int`) | each says: "a `module demo` header reproduces the same failure … That gap is pre-existing and tracked in BACKLOG; the file previously escaped it only by getting no prelude at all" | **No.** Evaporates when the two open `P2` qualification items land. |
| **C. Testing the directive itself** | `no_prelude_directive`, `test_bundle_prelude_scope` | self-referential | Only while the directive exists. |
| **D. Planned but never marked** | `01_noparam`, `02_adtmatch`, `03_strconcat`, `05_lambdaparams` | `prelude-scope-v0.md` §8 assigned these the directive to keep their goldens small, and the disposition was never applied (below) | **Yes.** Their goldens are 12.5k lines for ≤7 lines of source until it is. |

Category A needs nothing from the floor: those two fixtures "use Int, one wrap
and one ADT, and no prelude name at all" — verified, zero occurrences of any
floor name. **Category D needs exactly one: `print`.** Verified across all four,
and `print` is the only floor name any of them mentions.

So the permanent constituency for the floor is category D — four files that were
*designed* to be `no_prelude` and are not yet. Categories B and C also want
`print`, but B is temporary by construction and C exists only to test the
directive. The floor is therefore not, as an earlier draft of this section
claimed, a surface whose consumers are all workarounds; that reading came from
treating the §8 lapse as a decision rather than an oversight (below), which made
category D invisible.

**Two further facts about how the opt-out actually landed,** both of which argue
the same way:

- `prelude-scope-v0.md` §8 projected ~90 fixtures would need marking
  (30 `conformance/run`, 26 `type_error`, 15 `tests/stdlib`, …). The
  implementation marked **7**. Nearly everything simply gained the prelude and
  was fine, so the opt-out's real constituency was an order of magnitude smaller
  than the design expected.
- That table's "keeps goldens tiny" rationale for `tests/smoke_shapes` was **not
  acted on.** §8 predicted outright: *"The 4 smoke shapes stay small because they
  opt out."* Today **0 of 10** smoke shapes carry the directive. The design's "4"
  was exactly right — 4 were small (66, 110, 81, 99 lines) while the other 6
  already had headers or imports and were already ~12.4k — and those same 4 are
  the ones that grew, **356 → 49,981 lines**. `01_noparam.spr` is two lines of
  source (`fn val() -> Int = 42`; `fn main() … = print(val())`) and its golden went
  66 → 12,471 lines, a factor of 189.

  That growth is **outside the design's own budget**: §8 accounted for "+88k lines
  on an 811k-line corpus (~11%)" from the 7 examples, explicitly excluding the
  smoke shapes on the assumption they would opt out. Actual growth exceeded the
  budget by ~50k lines, entirely from the files assumed exempt. §0 ("What
  implementation corrected") lists four corrections and **none of them is this**,
  so it is an unrecorded gap between design and implementation rather than a
  revised decision.

  The mitigation that would have made it moot — wiring DCE into `compile_full` —
  was an explicit non-goal (§2) and is still open as `P3`: "a 7-line `module main`
  that calls one local function still emits ~12.7k lines, including an unused
  `@map`."

  **This has a live cost, not just a tidiness one.** `scripts/ir_golden_diff.sh:55`
  truncates each file's diff at `head -40`. Against a 12.5k-line golden that is at
  most a 0.3% sample, which is exactly the hazard AGENTS.md DoD #12 already warns
  about ("one such change produced a 27,274-line real diff, i.e. the report was a
  2% sample"). Letting the goldens inflate degraded the golden gate's
  readability — the corpus is now 955,705 lines across 60 files.

  **The plan was viable; it was simply not executed.** Measured by marking the
  four in a scratch copy and re-emitting: all four compile, and their IR returns
  to 64 / 108 / 79 / 97 lines — essentially their pre-change sizes. Each uses
  exactly one floor name, `print`. So this is not a case of a disposition that
  turned out to be impossible and was quietly abandoned.

  **This is an oversight, not an accepted trade, and the distinction changes the
  conclusion.** An earlier draft of this section wrote that "the project already
  accepted a 140× inflation rather than use the opt-out for that", and inferred
  that golden size is therefore not a live reason to want the opt-out. That
  inference is invalid. Acceptance would be evidence about what the project
  values; an unnoticed lapse is evidence about what it *checked*. The design did
  value golden size — that is why the row exists — and nothing has revisited it.
  Golden size remains a live and **unadjudicated** reason to want the opt-out, and
  the four affected files are category D above, not an argument against the floor.

  **Executing §8's plan is worth doing on its own**, and the floor is what makes
  it principled: marking those four recovers ~49.6k lines (~5% of the corpus) and
  restores the `head -40` report to something meaningful for them. Today it would
  work only because the scheme leak supplies `print`; under the floor it works
  because `print` is declared. Filed as follow-up, not folded in here — it
  rewrites four goldens and wants its diff read on its own.

## 2. Goals and non-goals

**Goals.**

1. `no_prelude` has a *definable* meaning, stated normatively, rather than an
   accidental one that depends on which of two tables happens to carry a name.
2. A `no_prelude` file calling a prelude **library** function gets a positioned
   Sprout diagnostic, not a `clang` error.
3. No name that works in a `no_prelude` file today stops working, unless dropping
   it is an explicit decision recorded here (§8 — `argv_get` is the only one).
4. One authority per fact. A builtin's type is declared once, in Sprout source.

**Non-goals.**

- Changing what a *normal* file sees. The prelude stays unconditional and its
  contents unchanged.
- Fixing the two open qualification-uniformity items (`__cm_<Class>_<method>`
  collision, do-notation resolving the monad family by bare name). §4.1 is
  designed to **avoid** touching them; see the closure argument there.
- Making the runtime's full surface reachable without imports. `stdlib.fs`,
  `stdlib.net` and friends stay explicit imports, for the reasons already
  recorded in the prelude's own comments.

## 3. Prior-art survey

The question is narrower than the survey in `prelude-scope-v0.md` §3, which
asked *whether* a prelude is injected and *how* it is opted out of. This asks:
**where an opt-out exists, is there a floor it cannot remove, and what is in
it?** Every row below is quoted from the language's own reference or standard.

| Language | Opt-out | Floor the opt-out cannot remove |
|---|---|---|
| **Rust** | `#![no_implicit_prelude]` | **Named explicitly.** The attribute "prevents the standard library prelude, extern prelude, `macro_use` prelude, and the tool prelude from being brought into scope" — and "does **not** affect the language prelude", which carries the built-in types (`bool`, `char`, `str`, the integer and float types). |
| **C** | a *freestanding* implementation (vs hosted) | **Normatively listed.** "A conforming freestanding implementation shall accept any strictly conforming program in which the use of the features specified in the library clause (clause 7) is confined to the contents of the standard headers `<float.h>`, `<iso646.h>`, `<limits.h>`, `<stdalign.h>`, `<stdarg.h>`, `<stdbool.h>`, `<stddef.h>`, `<stdint.h>`, and `<stdnoreturn.h>`." |
| **Haskell** | `NoImplicitPrelude` | **Thinner: syntax only.** "GHC normally imports the `Prelude` module for you. If you'd rather it didn't, then give it a `-XNoImplicitPrelude` option." What survives is built-in *syntax* — lists and tuples are "algebraic datatype[s] … although with special syntax", functions "an abstract type" — while `Bool`, `Maybe`, `Either` are ordinary `data` declarations in the Prelude, so the opt-out does remove them. |
| **Go** | none | Predeclared identifiers live in the universe block, which "encompasses all Go source text". *(row carried from `prelude-scope-v0.md` §3)* |
| **Python** | none | "If the names are not found there, the builtins namespace is searched next." *(row carried from `prelude-scope-v0.md` §3)* |

**Zig** looked like the closest analogue — compiler-provided `@`-prefixed
functions always in scope, standard library behind `@import("std")` — but the
language reference section consulted does not state the scoping rule explicitly,
and a search of `ziglang.org` did not surface one. *No claim made.*

**Two findings decide the design.**

1. **Every surveyed language with an opt-out specifies a floor that survives
   it,** and the floor is always "what the implementation itself provides" —
   never the data-structure library. None of them lets the opt-out leave the
   program unable to express anything.
2. **The field diverges on thickness, and C is the closest match to Sprout's
   situation.** Rust's floor is types only; Haskell's is bare syntax. C's is a
   *specified list of declarations* that a freestanding implementation must still
   provide — which is structurally what §4.1 proposes, and for the same reason:
   these are the things the program cannot implement for itself.

Sprout's proposed floor is thicker than Rust's or Haskell's in that it contains
*functions*. That is a difference in what the language makes primitive, not a
departure from the principle: `str_concat` on a Sprout `String` is not a library
convenience, it is the only way to concatenate one, exactly as `+` on a Rust
`u32` is compiler-provided rather than imported. AGENTS.md §"Builtin vs Stdlib"
already draws that line — a builtin exists only where the feature "is impossible
to implement in Sprout" — and the floor is that line, made visible.

Sources: [Rust Reference — Preludes](https://doc.rust-lang.org/reference/names/preludes.html) ·
[ISO/IEC 9899:2011 (N1570) §4](https://port70.net/~nsz/c/c11/n1570.html#4) ·
[Haskell 2010 Report ch. 6](https://www.haskell.org/onlinereport/haskell2010/haskellch6.html) ·
[GHC User's Guide — NoImplicitPrelude](https://downloads.haskell.org/ghc/latest/docs/users_guide/exts/rebindable_syntax.html) ·
[Go Spec](https://go.dev/ref/spec) ·
[Python Reference — Execution model](https://docs.python.org/3/reference/executionmodel.html)

## 4. Implementation overview

### 4.1 The floor — this is the decision

**The floor is the subset of the prelude's `extern fn` block whose signatures
mention no prelude-defined type.** 15 of the 40:

```
print  eprint  panic
int_to_string  double_to_string  char_to_string  char_to_str
char_from_codepoint  to_double
str_concat  str_len  str_slice  str_find  str_starts_with  str_compare
```

Their signatures range over `String`, `Int`, `Char`, `Double`, `Unit` and bare
type variables — nothing else. The remaining 25 mention `Maybe`, `List`,
`Vector`, `Map`, `NativeSet` or `Ref`:

| family | names | type mentioned |
|---|---|---|
| vectors | `vector_empty vector_length vector_get vector_set vector_append vector_concat vector_from_list` | `Vector`, `Maybe`, `List` |
| maps | `map_empty map_get map_set map_remove map_size map_nth_key map_nth_value` | `Map`, `Maybe` |
| sets | `native_set_empty native_set_insert native_set_member native_set_to_list native_set_size` | `NativeSet`, `List` |
| refs | `ref_new ref_read ref_write` | `Ref` |
| other | `argv_get str_char_at string_concat_many` | `Maybe`, `List` |

**Why the primitive-only cut is the right boundary, and not merely a convenient
one.** Three properties fall out of it, and the third is what makes the change
affordable:

1. **It is type-closed.** A floor of these 15 needs no type declarations at all,
   so the floor is exactly a list of `extern fn`s — no ADTs, no classes, no
   instances, no ordering constraints against the entry module's own decls.
2. **It covers what works today.** 8 of the 9 names in §1's first row are in it.
   The exception is `argv_get` (`-> Maybe String`), addressed in §8.
3. **It does not touch the qualification items.** `collect_modules`
   (`bundler.sprout:~630`) records that naming the entry module is *coupled* to
   prepending the prelude, because "two resolutions still key on the UNQUALIFIED
   name — do notation picks the monad family by it, and the class-method wrapper
   `__cm_<Class>_<method>` is mangled from it. A `no_prelude` file that defines
   its own `Maybe` or `Eq` depends on staying bare." A floor carrying **no types
   and no classes** gives a `no_prelude` file nothing to collide with on either
   axis, so the entry keeps its bare namespace and both open `P2` items stay out
   of scope. An ADT-closed floor would drag `Maybe` in and re-trigger both — that
   is the version of this idea that is expensive, and it is not this one.

The 25 excluded names are the right things to exclude on their own merits:
`Vector`, `Map`, `Set` and `Ref` are data structures, which is what a standard
library is for.

### 4.2 The change

Today `collect_modules` either prepends the prelude or returns the module list
bare:

```
if skip then (Nothing, plain_mods)
else … Cons(prelude_mod, name_entry_module(plain_mods, entry_path))
```

The `skip` arm becomes "prepend the floor" rather than "prepend nothing": the
prelude's `ParsedModule` filtered to the 15 `ExternFnDecl`s, with the entry left
bare (per §4.1 property 3 — the floor introduces no namespace to keep the entry
out of). Sketch, not final:

```
if skip then (Nothing, Cons(core_only(prelude_mod), plain_mods))
else … as today
```

Then, **and only then**, the four `load_prelude_pairs` calls that feed an
already-bundled program can go: `compiler.sprout:339` (`compile_full`), `:417`
(`compile_phase_effects`), `:435` (`check_bundled`), `:510`
(`compile_phase_check_with_cache`). The floor supplies the 15 schemes as ordinary
bundled declarations, so no seeding is needed for them, and every other prelude
name is correctly absent. That is an 11-line removal plus a decl filter — and it
continues `bd90e4b5`'s direction (types come from `extern fn` declarations)
rather than reversing it.

The other three call sites stay: `lower_driver.sprout:49` and
`type_driver.sprout:33` parse a single file without bundling and legitimately
need prelude + import schemes; `analysis_service_driver.sprout:681` calls it only
to pre-warm the cache and discards the result.

Once the floor is one artifact, `is_hardcoded_intrinsic` and the `ir_header`
declare list become derivable from it instead of hand-maintained. That is the
end state that removes this class of bug rather than this instance; it is
follow-up work, not part of this change, and should be filed as such.

### 4.3 Alternatives rejected

**Seed the 9 intrinsic names into `checker.builtin_entries()`.** Prototyped and
measured: it works, fixes the 31, keeps the 9, and passes every gate. Rejected
anyway. It re-adds to `checker.sprout` the kind of hardcoded scheme table
`bd90e4b5` deleted, and creates two sources of truth for those 9 types — the
prelude's `extern fn` and the checker's entry — with nothing gating their
agreement, so they drift silently. It also picks the floor by "which names does
the lowerer happen to hardcode", which is an implementation accident, not a
boundary anyone chose.

**Seed only the `extern fn` schemes in the bundled path.** Filter
`load_prelude_pairs`' output to names the prelude declares `extern`. Zero
regression, fixes `range_count`, no semantics change, ~15 lines. Rejected as an
end state: it leaves 31 names typechecking and then dying at `clang`, and it
preserves a scheme-with-no-declaration path — the original bug's exact shape,
narrowed rather than closed. Viable as an interim if the floor needs more time
than the defect can wait.

**Bundle the whole extern block (all 40) unconditionally.** Not type-closed: 25
of the signatures mention `Maybe`/`List`/`Vector`/`Map`/`NativeSet`/`Ref`, so it
requires those type declarations too, which drags prelude ADTs into `no_prelude`
files and re-triggers both open qualification items (§4.1 property 3). Strictly
more expensive for names that belong in the library anyway.

**Leave it; document that `no_prelude` means no output.** Rejected: three of the
four `run/` fixtures print, and a `run/` fixture that cannot produce output
cannot assert much. It also has no prior art — no surveyed language lets its
opt-out leave a program unable to express anything (§3, finding 1).

## 5. Syntax and semantics impact

No grammar change. `no_prelude` keeps its spelling and its whole-line match rule
(`prelude-scope-v0.md` §11.2).

The directive's meaning becomes normative: **`no_prelude` suppresses the standard
library, not the language's runtime interface.** A file that opts out still has
the 15 core operations; it does not have `Maybe`, `List`, `Vec`, `Dict`, `Set`,
`Ref`, the typeclasses, the instances, or any prelude-defined function.

The entry module of a `no_prelude` file stays **bare** (unqualified), unchanged
from today, and §4.1 property 3 is why that remains sound.

## 6. Type-system impact

None to the type system proper: no new forms, no inference change, no new
constraint. The change is to which declarations are in scope.

One interaction is a genuine tension in this design, not a detail — see §11.1.
The floor's 15 names become declared in a `no_prelude` file, so a file defining
its own `str_len` collides where before it did not. Measured on `0e1e6fd2`: in a
**normal** file, `fn str_len(s: String) -> Int = 99` type-checks fine and the
environment carries both `str_len : String -> Int` (the prelude's) and
`$entry.str_len : String -> Int` (the file's) — exit 0. That is the shadowing
`prelude-scope-v0.md` §4.1 promotes, and it works **because the entry module is
qualified**, which happens only when the prelude is prepended. §4.1 property 3
deliberately keeps the entry *bare* under the floor. Those two facts are in
tension, and §11.1 states the resulting choice.

## 7. Error-message impact

The improvement is the point: a `no_prelude` file calling a library name gets a
positioned diagnostic instead of a `clang` error with no Sprout position.

```
# today
$ compile_driver --emit-ir stdlib demo.spr     # exit 0, IR emitted
$ clang out.ll runtime/*.c
error: use of undefined value '@range_count'

# proposed
demo.spr:26:26: ERROR: check: Unknown variable: range_count in function main
```

**The plain `infer_var` message is not sufficient, and this document originally
got that wrong.** `prelude-scope-v0.md` §7 already specified the diagnostic for
exactly this case, and specified it as a requirement rather than a nicety — "this
closes defect (b), and is the one place `no_prelude` must not silently defer to
clang":

```
ERROR: check: `range_count` is not in scope: this file declares `no_prelude`,
so the prelude is unavailable. Remove the directive, or define `range_count` here.
```

That is a commitment already made in an approved design and not yet implemented,
so it is **in scope here, not optional polish.** It also does strictly more work
than the generic message: the generic one leaves a reader to discover that a
one-word header line is why a name they can see in the prelude is not in scope.

Mechanically it needs the unbound-name error path to know whether the file opted
out — the same `source.has_no_prelude` answer `skip_prelude` already computes —
and a way to tell "the prelude exports this" from "nothing exports this", which
`validate_type_names`' `exporting_module` already does for the type axis and can
be mirrored for values.

## 8. Compatibility and migration

**Normal files: no change.** Verified on the prototype — `ir-golden-diff` reports
60 files, 0 differences with the seeding removed, which is direct evidence that
the seeded schemes were inert for every program in the corpus.

**`no_prelude` files: three changes.**

1. **31 names move from a `clang` error to a Sprout diagnostic.** Not a
   regression: no such program ever produced a working binary.
2. **7 names start working that do not today** — `str_len`, `str_find`,
   `str_starts_with`, `double_to_string`, `char_to_str`, `char_from_codepoint`,
   `to_double`. They are in the floor but have no unconditional lowering, so
   today they typecheck and die at `clang`.
3. **`argv_get` stops working.** It is the one name in §1's first row excluded
   from the floor, because `-> Maybe String` is not type-closed. A `no_prelude`
   file can no longer read `argv`. **This is the one deliberate capability
   removal in this design.** Its in-repo users (`tests/http_client/*.spr`, and
   the many examples whose golden IR references it) are all ordinary preluded
   files, unaffected; no `no_prelude` file calls it. Reading argv from a file that
   has opted out of the standard library is a thin use case, and the alternative —
   admitting `Maybe` to the floor — costs both qualification items, which is not
   a trade worth making for one name.

**In-repo fixtures: all 8 keep working**, and none needs an edit. The three that
print (`no_prelude_directive`, `instance_check`, `codegen_do_bind`) get `print`
from the floor.

`tests/conformance/run/no_prelude_directive.spr`'s comment about `print` being
"special-cased in the compiler" must be corrected (§0, item 3) — under this
design `print` comes from the floor, which is a declaration the file can be
pointed at.

## 9. Tests added/updated

**A capability matrix — the load-bearing one.** §1 records that the full suite
and the golden corpus were both blind to a 9-name regression. The matrix closes
that: for each name in the floor, and a representative of each excluded family, a
`no_prelude` fixture pinning the outcome (runs / rejected-at-check), so the
boundary in §4.1 is asserted rather than assumed. This is what makes the floor a
specification instead of a description.

**Already written, currently failing** (staged before implementation, per
AGENTS.md Definition of Ready #3):
`tests/conformance/type_error/no_prelude_calls_prelude_fn.spr` + `.err` — a
`no_prelude` file calling `range_count` must be rejected at check time. Confirmed
to fail on `0e1e6fd2` for the right reason: the file reports `OK`, exit 0.

**Also needed.**

- The §6 collision: a `no_prelude` file defining its own `str_len`. Whatever the
  measured behaviour is, it gets pinned.
- `argv_get` in a `no_prelude` file must be rejected (§8 item 3) — so the one
  deliberate removal cannot be undone by accident.
- Existing gates that must stay green: `just test`, `just ir-golden-diff`,
  `just compile-examples-stage1`, and the full `just ci-fast-gates`.

Being a compiler-source change, this carries the whole compiler DoD: smoke
shapes, bundle smoke, `just refresh-seed` with the updated
`bootstrap/compile_driver.ll` staged, and golden-IR diffs **read before**
regenerating.

## 10. Spec/docs updated

- `docs/spec-v0.md` — the normative sentence for what `no_prelude` removes, and
  the floor as a named list. This is the change that makes the meaning
  normative rather than emergent.
- `docs/prelude-scope-v0.md` §4.2 step 5 — a pointer here; that document
  introduced the directive without defining its scope.
- `stdlib/prelude.sprout` — the `extern fn` block header comment marks which
  declarations are in the floor, since that file is where the authority lives.
- `README.md` §"Not Yet Supported" — only if the `argv_get` removal is worth a
  user-facing note; probably not.
- `BACKLOG.md` — the three corrections in §0, and the derive-`ir_header`-from-the-floor
  follow-up from §4.2.

## 11. Open questions

1. **The floor and the bare entry cannot both be free. This is the one real cost
   in the design and it needs a decision.**

   Measured (§6): redefining a prelude name works today **because the entry
   module is qualified** — the file's `str_len` becomes `$entry.str_len` and
   coexists with the prelude's. Entry qualification happens only when the prelude
   is prepended, and §4.1 property 3 keeps the entry bare under the floor
   precisely to avoid the two open `P2` qualification items. So the floor's 15
   unqualified `extern fn` names sit in the same flat namespace as a `no_prelude`
   file's own declarations, and a file redefining one of them is a hard collision
   ("… is defined more than once in this module") rather than a shadow.

   Three ways out, in increasing cost:

   - **(a) Accept the collision.** A `no_prelude` file may not redefine one of
     the 15. Cheap, and the diagnostic is already clear. **Measured against the
     whole corpus: no `no_prelude` fixture redefines any of the 15.** What they
     redefine is `Eq` (`instance_check`), `Maybe` (`codegen_do_bind`,
     `test_ir_codegen_do_bind_strip`), and `Color`/`Describable`/`Eq`
     (`type_classes`) — every one a **type or class**, none a value. The floor
     carries no types and no classes, so the overlap is empty by construction,
     not by luck: the axis the corpus contends on is exactly the axis the
     primitive-only cut excludes.
   - **(b) Qualify the entry under the floor too.** Restores shadowing, and
     re-triggers both `P2` items — the cost property 3 was written to avoid. It
     would make those two items prerequisites of this change.
   - **(c) Give the floor its own module namespace** (`core.str_len`), so the
     entry stays bare and cannot collide. Cleanest namespacing, but it changes
     how the 15 are *spelled* in a `no_prelude` file, which breaks all three
     printing fixtures and contradicts goal 3.

   **Resolved: (a).** The fixture check came back empty, so it preserves every
   property in §4.1 and costs only a capability no file in the corpus wants. The
   collision must still be pinned by a test (§9), so that a future fixture
   redefining `str_len` fails loudly rather than mysteriously — and if one ever
   legitimately needs to, this reopens as (b) vs (c), where (b)'s dependency on
   the two `P2` items has to be costed first.

2. **Is `argv_get` worth an exception?** §8 drops it. The alternative is a floor
   that admits `Maybe` alone — cheaper than the full ADT closure but still enough
   to re-trigger the `__cm_`/do-notation items for any file defining its own
   `Maybe`. Recorded here because it is the one place this design removes a
   working capability, and that should be an explicit call rather than a
   consequence.

3. **Should the floor be a marked section of `stdlib/prelude.sprout`, or its own
   file?** A marked section keeps one authority and needs no new module; a
   separate `stdlib/core.sprout` makes the boundary visible in the filesystem and
   removes the need for a decl filter at bundle time, at the cost of splitting a
   file whose header comment currently claims to be the single authority for all
   40 signatures.
