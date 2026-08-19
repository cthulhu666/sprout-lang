# Prelude scope: always on, with an explicit opt-out

**Status:** proposed design, not yet implemented. Supersedes the implicit
"no named module ⇒ no prelude" rule in `bundler.collect_modules`.

Normative outcome, once implemented, belongs in `docs/spec-v0.md`; this document
carries the rationale and the migration.

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

4. **Never display the synthetic name.** `$entry.` is stripped wherever a
   qualified name reaches the user: constructor display strings and diagnostic
   text. This is goal 4, and it is what keeps `print(Just(2))` printing `Just(3)`
   rather than `$entry.Just(3)`, and keeps a type error reading `Maybe Int`
   rather than `$entry.Maybe Int`. Real module names keep their prefix — for a
   named module the prefix is useful disambiguation.

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

**Must verify before implementing.** The REPL and the analysis service enter
through `bundle_source` with an overlay buffer that may have no module header.
`repl.sprout:360-363` states its completion logic depends on the prelude being
"inlined rather than imported", so these paths are sensitive to this change and
their current behaviour has not yet been established.

## 9. Tests added/updated

1. **TDD, defect (a)** — a headerless file calling a prelude function compiles,
   links and runs. Fails today at clang.
2. **TDD, defect (c)** — a headerless *imported* file produces the §7 diagnostic,
   not "defined more than once".
3. **Shadowing** — `examples/maybe_map.sprout`'s shape as a `tests/stdlib` case:
   local `Maybe`/`Just`/`Nothing` with the prelude present, asserted to run.
4. **Goal 4 regression, both surfaces** — a headerless file printing a local
   constructor asserts exactly `Just(3)`, and a headerless file with a type error
   asserts the message contains no `$entry`. These are the tests that would have
   caught the `main.Just(3)` leak.
5. **Opt-out** — a `no_prelude` file compiles and runs using no prelude name; and
   one calling a prelude name yields the §7 scope diagnostic rather than a clang
   error.
6. **Conformance** — `no_prelude` in a non-header position is a parse or bundle
   error, not silently accepted.

## 10. Spec/docs updated

- `docs/spec-v0.md`: a normative section on prelude scope — currently silent on
  the subject. States the unconditional rule, shadowing, and `no_prelude`.
- `README.md` §Not Yet Supported: drop anything implying importless files are
  special.
- This document: mark implemented, with the normative statement pointing at the
  spec.
- `BACKLOG.md`: the preludeless-diagnostic P2 entry in §7.2 is closed by §7 here.

## 11. Open questions

1. **`examples/maybe_map.sprout`.** Under this design it works unchanged, keeping
   `Maybe`/`Just`/`Nothing`. But the example exists to teach ADTs, and shadowing
   three prelude names to do so may teach the wrong lesson — Haskell's
   require-it-to-be-explicit stance is the beginner-friendly datapoint, and
   Sprout's stated goals lean that way. Rename to `MyMaybe`, or keep the
   shadowing as a demonstration that shadowing works?
2. **Directive spelling.** `no_prelude` as a bare header line, versus attaching it
   to the header (`module main no_prelude`), versus a punctuation-led form.
3. **`$entry` as the synthetic name.** Unforgeable and never displayed, so the
   spelling is invisible; confirming there is no objection to `$` in emitted
   symbol names.
