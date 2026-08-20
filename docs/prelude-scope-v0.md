# Prelude scope: always on, with an explicit opt-out

**Status: implemented, 2026-08-20.** Supersedes the implicit "no named module ⇒ no
prelude" rule in `bundler.collect_modules`.

The normative rule is `docs/spec-v0.md` §3.1; this document carries the rationale,
the migration, and what implementation found that the design had wrong.

## 0. What implementation corrected

Four things, all of which changed the work rather than merely annotating it.

1. **§4.1's "shadowing already works" was too strong.** It was verified against
   `examples/maybe_map.sprout` — a plain ADT and a plain `map` — and holds for
   that. It does **not** hold for a redefined typeclass or a redefined monad
   family, because two resolutions still key on the *unqualified* name: do
   notation picks the monad family by bare name, and the class-method wrapper is
   mangled `__cm_<Class>_<method>` with no module prefix (the dictionary
   parameter, `__tc_demo.Eq_0_eq`, *is* qualified — only the wrapper is not).
   Both were confirmed pre-existing by reproducing each with a `module demo`
   header. Three conformance fixtures relied on the old carve-out for exactly
   this, and now say `no_prelude`. Recorded as follow-ups in `BACKLOG.md`.

2. **Naming the entry is COUPLED to prepending the prelude.** Step 2 read as an
   independent step. It is not: the synthetic name exists only to keep the entry
   out of the prelude's namespace, so a `no_prelude` file must stay *unqualified*
   — otherwise it loses the prelude *and* the bare names that (1) depends on.
   Applying the rename unconditionally broke `codegen_do_bind` in a way the old
   carve-out did not.

3. **"No in-tree file is affected" (step 3) was false.** `stdlib/compiler/dce.sprout`
   had imports but no `module` header, so its declarations were emitting
   *unqualified* into every bundle — defect (c) live in the compiler's own source,
   one prelude-name collision away from breaking. The new diagnostic found it on
   the first reseed. Fixed by adding the header it should always have had.

4. **`no_prelude` re-opens defect (b), and this is not fixed.** `check_bundled`
   hands the checker `load_prelude_pairs` unconditionally, so a `no_prelude` file
   calling `negate` type-checks and then fails in the IR parser. With the prelude
   now always bundled those pairs are redundant in the normal case, so deleting
   them is both the root-cause fix and a code removal — but there are 9 call sites
   across the batch, LSP, REPL and analysis paths, so it is its own change.
   Tracked in `BACKLOG.md`.

One prediction held exactly: no grammar change, so no 2-step bootstrap. Every
reseed reached a fixed point at iteration 2.

An unplanned dividend: `tests/conformance/run/XFAIL` went from 8 entries to zero.
All 8 shared the root cause this removes. The manifest also claimed `Cons`/`Nil`
"was REMOVED from the language" and that four fixtures needed rewriting — false;
they are prelude List constructors (`stdlib/prelude.sprout:32-34`) and those
fixtures needed only the prelude.

## 1. Problem statement

Whether a Sprout file gets the prelude is inferred from its import graph. In
`stdlib/compiler/bundler.sprout:601-625`:

```sprout
# Self-contained (importless) files are DELIBERATELY not given the
# prelude — they define their own types/functions (see
# examples/maybe_map.sprout, which redefines Maybe and map).
if any_has_module_name(rev_mods) then
  ... Cons(prelude_mod, rev_mods)
else
  rev_mods
```

Three defects follow.

**(a) The trigger is not what it appears to be.** It is not "this file has no
`module` header" — `any_has_module_name` (`:660`) scans the whole transitive
closure, so a headerless file that imports one named module *does* get the
prelude. The property is therefore "headerless **and** importless", and adding an
unrelated `import` silently changes the language available in the file.

**(b) The failure mode is a raw LLVM error, not a diagnostic.** A preludeless
file that calls a prelude function type-checks and emits IR with exit 0, because
the checker is handed the prelude's schemes unconditionally
(`compiler.sprout:310`) while the *bundle* omits its declarations. Measured:

```
$ cat pl.spr
fn main() -> Int !{IO} =
  range_count(range_up(1, 4))

$ compile_driver_bin_stage1 --emit-ir stdlib pl.spr ; echo $?
0

$ clang pl.ll runtime/*.c …
pl.ll:58:19: error: use of undefined value '@range_count'
```

The bundler comment claims this is "a hard error (`codegen.emit_named_call`)".
That backend was deleted in `5f29b9da`; no equivalent check exists on the
Sprout-IR path. The quarantined `'@lcompose'` and `'@Err'` conformance xfails are
this same hole, which is why they are labelled `link:`.

**(c) It is inconsistent for non-entry files.** Imports resolve by *path*
(`module_loader.resolve_module_path`) with no check that the file declares a
matching name, so an imported file may also be headerless. Its declarations then
emit unqualified and collide with the prelude:

```
$ compile_driver_bin_stage1 --emit-ir stdlib --package-root pkg pkg/entry.sprout
1:8: ERROR: check: `range_count` is defined more than once in this module
```

So the carve-out protects exactly one shape — entry, headerless, importless — and
every other headerless shape is either broken or diagnosed inconsistently.

## 2. Goals and non-goals

**Goals**

1. The prelude is available in every file, unconditionally.
2. A file may still shadow a prelude name, and doing so is not an error.
3. Opting out of the prelude is possible, **explicit**, and local to the file.
4. No user-visible text ever shows a compiler-synthesised name.
5. No grammar change, hence no 2-step bootstrap.

**Non-goals**

- Selective hiding (`import Prelude hiding(null)`). Whole-file opt-out only.
- Fixing the pre-existing leak of *real* module names into ctor display strings
  and diagnostics (`main.Apple(3)`). Tracked separately in `BACKLOG.md §7.2`;
  goal 4 only requires the *synthetic* name never leak.
- Wiring DCE into `compile_full`. Separate, and would reduce this change's golden
  churn; also in `BACKLOG.md §7.2`.

## 3. Prior-art survey

Every claim below is from the language's own reference.

| Language | Prelude injected | Opt-out | Collision with a prelude name |
|---|---|---|---|
| **Rust** | "automatically brought into scope of **every module** in a crate" | `#![no_implicit_prelude]`, per-crate or per-module | "The prelude names **may be shadowed by declarations in a module**" — module items win silently |
| **Haskell** | "imported automatically into **all** modules … **if and only if** it is not imported with an explicit import declaration" | write an explicit `import Prelude …` | Legal, but ambiguous at use sites; author must write `import Prelude hiding(null)` |
| **Go** | predeclared identifiers scoped to the *universe block*, which "encompasses **all** Go source text" | none | Inner declarations shadow; allowed |
| **Python** | globals searched first; "If the names are not found there, the builtins namespace is searched next" | none | Ordinary scoping — the module-level name wins |
| **Kotlin** | `kotlin.*`, `kotlin.collections.*`, … imported into every file | none documented | *not covered by the source consulted; no claim made* |

Two findings decide the design:

1. **No surveyed language conditions the prelude on the file's import list.** It
   is unconditional everywhere. Sprout's inference has no precedent.
2. **Opt-out, where it exists, is an explicit marker the author writes** — an
   attribute or an explicit import — never inferred.

On collisions the field splits: precedence-silently-wins (Rust, Go, Python)
versus require-explicit-disambiguation (Haskell). Sprout takes the majority
position (goal 2), because it already implements the mechanism — see §4.1.

Sources: [Rust Reference — Preludes](https://doc.rust-lang.org/reference/names/preludes.html),
[Scopes](https://doc.rust-lang.org/reference/names/scopes.html) ·
[Haskell 2010 Report §5.6](https://www.haskell.org/onlinereport/haskell2010/haskellch5.html) ·
[Go Spec](https://go.dev/ref/spec) ·
[Python Reference — Execution model](https://docs.python.org/3/reference/executionmodel.html) ·
[Kotlin — Packages and imports](https://kotlinlang.org/docs/packages.html)

## 4. Implementation overview

### 4.1 Shadowing already works; the bug is the empty module name

`bundler.qualified_name` (`:202`):

```sprout
if mn == "" then rn else mn ++ "." ++ rn
```

An empty module name means *no qualification* — and `""` is exactly the name the
prelude carries (`:73`: *"module_name : "" for prelude (no module header)"*). A
headerless file and the prelude therefore share one flat namespace and clash.
A file that *does* declare a name has no such problem: its declarations become
`@main.map` while the prelude keeps `@map`.

This was verified by simulation, with no compiler change — adding a `module`
header is precisely what flips `any_has_module_name`. `examples/maybe_map.sprout`
verbatim, whose `Just`/`Nothing` collide with the prelude's including in match
patterns, plus one header line:

```
$ compile_driver_bin_stage1 --emit-ir stdlib mm_named.spr   # exit 0
$ ./mm_named
main.Just(3)
```

So the fix is not to teach the resolver precedence; it is to stop giving
headerless files the prelude's own name.

### 4.2 The change

1. **Always prepend the prelude.** Delete the `any_has_module_name` branch in
   `collect_modules`. `ambient_type_names` (`:1678`) — the workaround that
   re-reads the prelude's exported names to compensate for its absence — becomes
   dead and is deleted with it. Net: the change *removes* code.

2. **Give a headerless entry file a synthetic module name.** Its declarations
   then qualify like every other module's. The name must be unwritable by a user
   so it can never collide with a declared one; Sprout identifiers are
   `[A-Za-z_][A-Za-z0-9_]*` (`string.is_ident_start`), so a `$` prefix is
   unforgeable, and `$` is legal in LLVM identifiers (the emitted `%t$0`
   temporaries already rely on that). Proposed: **`$entry`**.

   A single fixed name is sound because only the entry may be headerless — see
   step 3.

3. **Require a module header on any imported file.** Today a headerless imported
   file is diagnosed only if it happens to collide, and reported as
   ``…is defined more than once in this module``, which does not name the cause.
   Replace with a direct diagnostic at bundle time (§7). No in-tree file is
   affected: `stdlib/prelude.sprout` is the only headerless file under `stdlib/`,
   and it is prepended rather than imported.

4. **Never display the synthetic name — but strip it at RENDER time only, never
   at registration.** This is goal 4: `print(Just(2))` must print `Just(3)`, not
   `$entry.Just(3)`, and a type error must read `Maybe Int`, not
   `$entry.Maybe Int`. Real module names keep their prefix, which is useful
   disambiguation.

   > **Revised 2026-08-19.** This step previously said only "`$entry.` is
   > stripped wherever a qualified name reaches the user: constructor display
   > strings and diagnostic text", which reads as a cosmetic change. It is not.
   > `CtorMeta.name` is a **single field serving both display and lookup**, so
   > stripping it at registration is a wrong-tag bug, not a rendering choice:
   >
   > - `find_ctor_tag_by_name` (`runtime/sprout_runtime.c:2660`) is a **first-wins
   >   linear scan** over `g_ctor_meta`. Register the entry file's `Just` as bare
   >   `Just` and there are two entries under that name — the prelude's and the
   >   entry's, with different tags and possibly different arities. The runtime
   >   calls this function *itself* (`env_get` at :2843, the `stdlib.regex.Match`
   >   construction), so it would silently take whichever registered first.
   > - `sprout_make0` (`:4887`) keys its `g_nothing_singleton` cache on
   >   `strcmp(name, "Nothing")`. A stripped entry-file `Nothing` would match and
   >   be handed the cached singleton **carrying the prelude's tag**. Left as
   >   `$entry.Nothing` it simply misses the fast path — a lost allocation
   >   optimization, not a correctness bug, which is the right failure direction.
   >
   > Note the exact-match-then-suffix order in `find_ctor_tag_by_name` means the
   > *unstripped* design is already safe: a lookup for `"Just"` hits the prelude's
   > exact entry and never reaches the suffix fallback.
   >
   > **Two render surfaces, and they live in different languages.** The C
   > printers — `print_inline_obj` (`:2680`, what `print` of an ADT reaches) and
   > the stderr debug printer (`:4853`) — both do `printf("%s", meta->name)` and
   > are where stripping belongs on the runtime side. Compiler **diagnostic** text
   > is Sprout-side, in error rendering, and is a separate edit. No conformance
   > fixture pins a function name (`grep "in function" tests/conformance/*/*.err`
   > returns nothing), so no `.err` file gates this half — it is a UX requirement,
   > not a test-driven one.
   >
   > **Out of scope, recorded:** `json_ctor_is` (`:6168`) dispatches JSON encoding
   > on a ctor-name suffix match, so it treats `$entry.X` exactly as it already
   > treats `main.X`. Pre-existing class; this change does not widen it.

   **Entry-point detection needs no change** — verified, not assumed.
   `is_entry_fn_name` (`ast_to_ir.sprout:5915`, mirrored in `infer.is_main_decl_name`
   and `codegen.sprout:1906`) accepts `name == "main" || name` ending in `".main"`.
   `$entry.main` satisfies the suffix arm, so the synthetic name routes through the
   existing qualified-main path that `module main` files already use.

5. **Add an explicit opt-out header.** A file whose header block contains the
   directive gets no prelude. Because headers are stripped *before* `tokenize`
   (`source.strip_headers`, whose `is_header_line_byte` already recognises
   `module ` / `import ` / `#` / blank), this needs **no grammar change and no
   parser change** — one arm in `is_header_line_byte` plus a scan in the bundler
   alongside `scan_module_name`. Proposed spelling: a bare header line

   ```
   no_prelude
   ```

   Rust's `#![no_implicit_prelude]` cannot be borrowed literally: `#` opens a
   comment in Sprout, so the directive would be invisible.

### 4.3 Alternatives rejected

**Rename the colliding names in examples** (`Maybe` → `MyMaybe`). Works, and is a
defensible *pedagogical* choice, but as a mechanism it discards shadowing that
Rust, Go and Python all provide and that Sprout already implements (§4.1). Kept
as an open question for `examples/maybe_map.sprout` specifically — see §11.

**Teach the resolver precedence instead of qualifying** (prelude loses to the
entry file on collision). Rejected: the prelude's own internal callers must still
resolve to the prelude's definition, so one of the two must be renamed. The
prelude cannot be — its unqualified symbols are baked into `bootstrap/compile_driver.ll`
and into all 60 goldens. Qualifying the entry is therefore forced.

**Keep the capability but only fix the diagnostic** (leave the default as-is, add
a real error for the dangling call). Cheaper, but leaves defect (a) — the
import-graph inference — in place.

## 5. Syntax and semantics impact

- New header directive `no_prelude`, valid only in the header block (before the
  first declaration), like `module` and `import`.
- No change to expression, declaration or type syntax.
- Semantics: prelude names are in scope in every file that does not opt out. A
  top-level declaration of a prelude name shadows it for that file, silently.
- The entry file's declarations become qualified internally. Observable only in
  emitted symbol names, which are not a stability surface.

## 6. Type-system impact

None to the rules. One simplification: the checker is already handed prelude
schemes unconditionally, so `ambient_type_names` — which exists purely to
reconcile the checker's view with the bundle's — is deleted. Checker and bundle
agree on the prelude by construction rather than by compensation.

## 7. Error-message impact

**New diagnostic** — an imported file with no module header:

```
ERROR: bundle: imported file `pkgx/hless.sprout` declares no module;
add `module pkgx.hless` at the top. Only the file being compiled may omit it.
```

replacing today's misleading ``check: `range_count` is defined more than once in
this module``.

**New diagnostic** — a call to a prelude name from a file that opted out. This
closes defect (b), and is the one place `no_prelude` must not silently defer to
clang:

```
ERROR: check: `range_count` is not in scope: this file declares `no_prelude`,
so the prelude is unavailable. Remove the directive, or define `range_count` here.
```

**Unchanged text** required by goal 4: `$entry.` never appears. Verified by test,
not by inspection — §9.

## 8. Compatibility and migration

Every headerless-and-importless file changes meaning. Counted in-tree:

| Location | Files | Disposition |
|---|---|---|
| `examples/*.sprout` | 7 | **Gain the prelude.** User-facing teaching code should have it. |
| `tests/smoke_shapes/*.spr` | 4 | `no_prelude` — keeps goldens tiny |
| `tests/stdlib/*.spr` | 15 | `no_prelude` — preserves what they test |
| `tests/conformance/run/*.spr` | 30 | `no_prelude` |
| `tests/conformance/type_error/*.spr` | 26 | `no_prelude` — protects exact `.err` text |
| `tests/conformance/parse_error/*.spr` | 4 | `no_prelude` |
| other `tests/conformance/*/` | ~11 | `no_prelude` |

`no_prelude` on the fixtures is not merely expedient: several of them
*deliberately* exercise the preludeless path, and the property is currently
invisible. `tests/stdlib/test_ir_codegen_append_parity.spr` says so in its own
header — *"When no imports are in scope the typeclass lowering pass emits no dict
witness"*. Marking them makes an accidental property explicit, which is the
migration's main defensible benefit beyond the examples.

**Simulated blast radius.** Prepending a module header to all 15 headerless
`tests/stdlib` files: 14 still compile, 1 breaks —
`test_ir_codegen_do_bind_strip.spr`, with
`+ needs matching numeric operands: Type mismatch: simmod.Maybe Int vs Int`.
That single break is itself the argument for marking rather than migrating the
fixtures: the file's subject is do-bind stripping *without* a prelude Maybe in
scope, and giving it one deletes the case under test.

**Golden IR.** The 7 examples grow from ~70–150 lines to ~12.7k each, roughly
**+88k lines on an 811k-line corpus (~11%)**, and every future prelude change
then perturbs 7 more goldens. The 4 smoke shapes stay small because they opt out.
Wiring DCE into `compile_full` (`BACKLOG.md §7.2`) would shrink all goldens and
make this nearly free, but it rewrites the whole corpus at once and is gated
separately.

**Bootstrap.** `bundler.sprout` is compiler source, so `just refresh-seed` and a
staged `bootstrap/compile_driver.ll` are required (DoD #9). No 2-step bootstrap:
headers are stripped pre-parse, so the grammar the seed's parser must handle is
unchanged.

**REPL and analysis service: unaffected, and a precedent.** Both enter through
`bundle_source` with an overlay buffer, so they looked at risk. They are not:
`stdlib/compiler.sprout:53` assembles every session buffer under a synthesised
`module app.session` header, so they already always get the prelude. They have to
— a REPL that could not call prelude functions would be useless, and a headerless
buffer gets no prelude bodies.

This is the strongest evidence available that §4.2 step 2 is sound: the one
interactive consumer of this path **already invented the synthetic module name**,
locally and by hand, for exactly this reason. The proposal generalises a mechanism
that is already in production rather than introducing one.

## 9. Tests added/updated

Landed, 2026-08-20. Numbering follows the plan; deviations are noted.

1. **TDD, defect (a)** — `tests/conformance/run/prelude_in_headerless_file.spr`:
   a headerless file calling `negate` compiles, links and runs, printing `-7`.
   Confirmed failing first, as `clang: use of undefined value '@negate'`.
2. **TDD, defect (c)** — moved to `scripts/package_resolution_gate.sh` plus
   `tests/conformance/package_resolution/{app_headerless.spr,roots/demo/headerless.sprout}`.
   It needs a real package tree (a directory per dotted segment), which no
   `tests/stdlib` suite can build — `stdlib.fs` has `read_text`/`write_text` and no
   `mkdir` — and that gate already owns such a tree. Asserts the message names both
   the file and the cause, not just a nonzero exit.
3. **Shadowing** — `tests/stdlib/compiler/test_bundle_prelude_scope.spr` (new, 16
   assertions), asserting on the BUNDLE rather than on a run: whether the prelude's
   declarations are present, and what module name the entry's own carry. Covers a
   headerless file, a named module, a file redefining `Maybe`, `no_prelude`, a name
   that merely *starts* with the directive, and the prelude as its own entry.
4. **Goal 4 regression, both surfaces** — `examples/maybe_map.sprout` prints
   `Just(3)` (its golden IR is in the corpus, so a leak moves it), and
   `tests/conformance/type_error/headerless_error_hides_entry_module.spr` pins a
   message naming a *function* — the surface §4.2 step 4 flagged as uncovered.
5. **Opt-out** — `tests/conformance/run/no_prelude_directive.spr` compiles and
   runs, printing `42`. The second half is **not** asserted: a `no_prelude` file
   calling a prelude name still fails in the IR parser rather than as a diagnostic,
   because `load_prelude_pairs` is unconditional. See §0 item 4 and `BACKLOG.md`.
6. **Conformance** — not added. `no_prelude` in a non-header position is an
   ordinary identifier, which is the correct behaviour rather than an error: the
   directive is recognised only in the leading header block (`source.has_no_prelude`
   stops at the first non-header line, the same boundary `strip_headers` uses). The
   property worth testing is that a *lookalike* name does not opt out, which test 3
   covers.

Not planned, required by the change:

7. **Five suites re-scoped for a bundled prelude.** `test_wrap_codegen` and
   `test_tuple_return_cpr` make ABSENCE assertions over the whole emitted module
   ("no call to `@sprout_alloc_obj`", "allocates no tuple"), which a bundled prelude
   makes unstatable — their fixtures now say `no_prelude`. `test_compiler`'s
   cache-sharing test relied on a preludeless bundle to make an empty
   `stdlib_root` work, same fix. `test_ir_codegen_do_bind_strip` redefines `Maybe`
   and binds it with `<-` (§0 item 1). `test_borrow_erasure`'s presence guards now
   match the `$entry.`-qualified spellings.
8. **`compiler.env_scheme`** — hover and `:type` looked the sentinel up by bare
   name, so on any headerless buffer both lookups missed and the answer was
   silently `Nothing`. Caught by `test_expr_type_in_source` (10 failures), which is
   why that suite existed.

## 10. Spec/docs updated

Done, 2026-08-20:

- `docs/spec-v0.md` **§3.1** — the normative rule: prelude unconditional,
  shadowing, the header requirement on imported files, `no_prelude`, and the two
  known shadowing limits. The §5.6 `IntRange` note, which described the
  admit-the-prelude's-type-names workaround, is corrected: that workaround is
  deleted along with the condition that motivated it.
- `README.md` — the "a file with no `module` header gets no prelude" gotcha is
  replaced by the `no_prelude` opt-out and its two caveats.
- `tests/conformance/README.md` — the fixture-authoring instruction said a bare
  `.spr` must define everything it uses, which is what filled `run/XFAIL`. Two
  other documents cited it by line number, so this was the load-bearing one.
- `docs/ranges-v0.md` — two claims narrowed: the `reversed_literal_range` fixture's
  stated reason for calling no prelude function has expired, and Package C's
  "bare-file regression" objection now applies only to `no_prelude` files (while
  its ctor-tag-renumbering objection got *worse*, since the prelude now bundles
  into every program).
- `BACKLOG.md` — three pre-existing gaps this surfaced, each with a `module demo`
  reproduction proving it is not a regression; the 8-xfail note in the Model-C
  status entry marked cleared.
- This document: §0, recording the four things implementation corrected.

## 11. Open questions

1. ~~**`examples/maybe_map.sprout`** — rename to `MyMaybe`, or keep the
   shadowing?~~ **Resolved: keep it unchanged.** The file stays verbatim and
   becomes a live demonstration that shadowing a prelude name works, which this
   design promotes from accident to documented guarantee (goal 2). It therefore
   doubles as the user-facing regression test for §4.2 step 4 — if the synthetic
   prefix ever leaks, this example prints `$entry.Just(3)` and the golden moves.
2. ~~**Directive spelling.** `no_prelude` as a bare header line, versus attaching
   it to the header (`module main no_prelude`), versus a punctuation-led form.~~
   **Resolved: the bare header line.** Attaching it to `module` would have made it
   unavailable to the files that most want it — the entry files, which are
   headerless by definition — and a punctuation-led form has nowhere to go, since
   `#` opens a comment in Sprout (which is why Rust's `#![no_implicit_prelude]`
   could not be borrowed). The bare line also needs no grammar change: header
   lines are stripped before `tokenize`, so one arm in `is_header_line_byte` plus
   a scan bounded to the leading block is the whole mechanism.

   Implementation added one requirement the design did not state: the directive
   must match the WHOLE line. `no_prelude_lookalike` is an ordinary identifier and
   a prefix match would have silently un-preluded any file declaring one.
   Asserted in `test_bundle_prelude_scope.spr`.

3. ~~**`$entry` as the synthetic name.** Unforgeable and never displayed, so the
   spelling is invisible; confirming there is no objection to `$` in emitted
   symbol names.~~ **Resolved: `$entry`, and `$` is fine in emitted symbols.**
   Verified rather than assumed, before any code was written: `opt --passes=verify`
   accepts `define i64 @$entry.f()` written bare (round-tripping it as
   `@"$entry.f"`), and a module defining `@$entry.main` links and runs under clang
   on Mach-O. `$` is in LLVM's identifier charset — the same charset that makes
   the `.` in `@examples.collections_demo.half` an ordinary character rather than
   structure. A synthetic name only has to be illegal *upstream*, which `$` is:
   `string.is_ident_start` rejects it.
