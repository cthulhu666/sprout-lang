# `print` through `ToString` — v0

**Status — approved; landed (§10 steps 1–5).** Changes the normative spec: `print`'s
type, its output for `Bool`, `List`, `Vec` and any type with a hand-written instance, and the
`no_prelude` floor.

## 1. Problem

`print(val: a) -> Unit !{IO}` is a compiler intrinsic. Its type says it works the same for every
`a`. Its lowering does not: `translate_print_call` (`ast_to_ir.sprout`) picks one of three paths
from the argument's static type.

| Static type at the call | Lowering                                  |
|-------------------------|-------------------------------------------|
| `Char`                  | `char_to_string`, then `print_str`        |
| `String`                | `print_str`                               |
| anything else           | `print_value`, the runtime's erased renderer |

`infer_print_call` (`infer.sprout`) adds a fourth path: a tuple argument is rewritten to
`print(to_string(t))`.

`print_value` (`print_inline_value` in `runtime/sprout_runtime.c`) sees only a 64-bit word. It
asks `sprout_heap_lookup` whether the word is a heap block, then prints a C string, a tuple or a
constructor, or else the word as a decimal integer. So it cannot tell an `Int` from a `Char`, a
`Bool` or a `Double`, and it cannot find a string literal, which lives in the data segment.
Measured with the current compiler:

| Program                              | Prints                                   |
|--------------------------------------|------------------------------------------|
| `print(true)`                        | `1`                                      |
| `print(1.5)`                         | `4609434218613702656`                    |
| `show_it('z')`, `show_it(x: a) = print(x)` | `122`                              |
| `show_it("hi")`                      | an address                               |
| `print(Just('z'))`                   | `Just(122)`                              |
| `print(Err("boom"))`                 | `Err(<address>)`                         |
| `print(Pair('q', "str", 0.5))`       | `Pair(113, <address>, <bits>)`           |
| `print(vec_from_list([1, 2]))`       | `Vec(35184372089048)`                    |
| `print(Red)`, `ToString Color` gives `"red"` | `Red`: the instance is ignored     |

The renderer also prints at most 7 constructor fields. Any `Int` whose value equals the address of
a live block prints as that block.

Two consequences beyond wrong output:

- **The type lies.** Inlining or specialising a function changes what its `print` writes, because
  `show_it('z')` prints `122` while `print('z')` prints `z`. `docs/specialisation-v0.md` §2
  *Prerequisite* (PR #747) blocks specialisation on this.
- **Two renderers.** The spec names both: "the runtime rendering used by `print`, and the string a
  derived `ToString` produces" (§8.6, constructor names). They disagree on `Bool`, `List`, `Vec`
  and every type with a hand-written instance.

## 2. Goals and non-goals

Goals:

- `print(x)` writes `to_string(x)` and a newline, for every `x`, at every call site.
- `print` is an ordinary prelude function: its type states what it needs, and it can be passed as
  a value.
- No runtime renderer on `print`'s path.
- `no_prelude` files can still write a line to stdout.

Non-goals:

- `eprint` stays `String -> Unit !{IO}`. Generalising it is a separate, compatible change.
- No change to how `ToString` instances render, apart from the spec-table fix in §8.
- No run-time type information. Values stay untagged words.

## 3. Prior art

| Language   | Print a value                              | A user type's rendering            |
|------------|--------------------------------------------|------------------------------------|
| Haskell    | `print :: Show a => a -> IO ()`            | `deriving Show` or hand-written    |
| Lean 4     | `IO.println [ToString α] (s : α)`          | hand-written; no `ToString` deriver |
| Rust       | `{}` needs `Display`, `{:?}` needs `Debug` | `#[derive(Debug)]`; `Display` by hand |
| PureScript | `logShow :: Show a => a -> Effect Unit`    | `genericShow` over derived `Generic` |
| OCaml      | `print_string`, `print_int`, …             | none: no polymorphic print         |
| Go         | `fmt.Println(a ...any)`                    | automatic, by reflection           |
| Swift      | `print(_ items: Any...)`                   | automatic, by runtime metadata     |
| Kotlin     | `println(message: Any?)`                   | automatic `toString()`, `data class` |
| Elm        | `Debug.toString : a -> String`             | automatic; not under `--optimize`  |

Haskell defines `print x = putStrLn (show x)`. Rust: "`Display` … cannot be derived". Elm's
`Debug.toString` is also unavailable to published packages.

Languages with type classes and erased values (Haskell, Lean, Rust, PureScript) put a constraint on
`print`. Languages that print any value without a constraint carry run-time type information (Go,
Swift, the JVM's `Object.toString` under Kotlin and Scala). Sprout has type classes and erased
values. Lean is the closest match: `ToString` on `String` is the identity, so `print("hi")` writes
`hi`. Haskell's `show` quotes strings.

Sources: Haskell 2010 Report ch. 9 and 11; `Init/System/IO.lean` and `Lean/Elab/Deriving/` in the
Lean 4 repository; `alloc/src/fmt.rs` and `core/src/fmt/mod.rs` in the Rust repository;
PureScript `language/Type-Classes.md`, `guides/Type-Class-Deriving.md` and
`purescript-console` `src/Effect/Console.purs`; OCaml `stdlib.mli`; pkg.go.dev/fmt; Swift
`stdlib/public/core/Print.swift`; kotlinlang.org data classes and `kotlin/io/Console.kt`; Elm core
`src/Debug.elm`.

## 4. Design

### 4.1 `print` is a prelude function

```sprout
export fn print(value: a) -> Unit !{IO} where linear a, ToString a =
  print_str(to_string(value))
```

`extern fn print` is removed. Each call passes a `ToString` dictionary like any other constrained
call: chosen statically at a concrete type, forwarded from the caller under a type variable.
Inside `print` the `to_string` call is indirect, through that dictionary, until specialisation
lands (`docs/specialisation-v0.md`).

`linear a` admits a linear argument. The body uses `value` once, so the bound is sound, and it
matches the prelude's own `ToString` instances over `Maybe`, `Result` and tuples. Today's
`extern fn print(val: a)` rejects a linear argument only because it carries no `where linear a`;
an extern may take one (spec, *Type variables and `where linear`*). The bound is kept, not
claimed as a gain of this design.

### 4.2 The floor gets `print_str`

`print_str(s: String) -> Unit !{IO}` replaces `print` in the `no_prelude` floor (spec §3.1). It is
the existing runtime symbol `print_str`, already in `runtime/APPROVED_BUILTINS`; no runtime
function is added. It is a plain extern, declared `i64` like `eprint`, with no special lowering
(step 5, §10). It is monomorphic, so no type-directed lowering remains. No file in the four
repositories uses the name.

### 4.3 What the compiler loses

- ~~`infer_print_call` and its tuple rewrite (`infer.sprout`).~~
- ~~The `print` arms of `is_call_site_intrinsic` and `translate_print_call` (`ast_to_ir.sprout`);
  the `eprint` arm stays, and `print_str` joins it.~~
- ~~`"print"` in the no-runtime-symbol set and the `print_value` declare (`ir_lowering.sprout`).~~
- ~~`"print"` and `"print_value"` in the effectful-call list in `bce.sprout`.~~
- ~~The `IRPrintStr` comment in `sprout_ir.sprout`, which routes Int/Bool to `print_value`.~~
- ~~`print_value`, `print_value_part` and `print_inline_value` in the runtime, once nothing emits
  them, with their `APPROVED_BUILTINS` lines.~~ Landed in §10 step 4.
- ~~The REPL's `print(to_string(question))` (`eval_main_body`, `compiler.sprout`) becomes
  `print(question)`.~~

All landed: the compiler items in §10 step 3, the runtime item in step 4.

`print` in value position moves from `synthesize_intrinsic_eta_closure` to the ordinary path for a
constrained function used as a value. Under a type variable that path captures the dictionary:
`fn f(xs: List a) -> Unit !{IO} where ToString a = list_each(print, xs)` becomes legal, where
today it is rejected with "'print' cannot be used as a first-class value".

### 4.4 User types

A user ADT, record or `wrap` prints only if it has a `ToString` instance, written or derived
(`deriving (ToString)`, spec §8.6). A function value, `Unit`, and a 6-tuple or larger have no
instance, so printing one is a compile error.

This removes a capability. Today `print` renders any ADT structurally with no declaration; the
spec's own example `print(Just(3))` on a user-declared `Maybe` (§3.1) relies on it. After this
change, `type Color = | Red | Green` followed by `print(Red)` is a compile error until `Color`
derives `ToString`. §9 says why instances are not automatic.

### 4.5 Fixes at other layers, rejected

- **Caller: `print` takes only `String`**, and every caller writes `print(to_string(x))`. Honest
  and simple; it moves the same constraint to every call site and makes `print(42)` a type error.
- **Function: keep the intrinsic, rewrite in `infer_print_call`.** Extend today's tuple rewrite to
  every argument (the §5 proxy). No floor, REPL, completion or eta change. But `print`'s type
  still says `a -> Unit` with no constraint, a function cannot pass `print` under a type
  variable, and an intrinsic stays where a prelude function does the job.
- **Representation: tag values** so the runtime renderer can tell `Int` from `Char`. Needs several
  tag bits (an `Int` loses range, every arithmetic op untags) or boxed scalars, which undoes the
  unboxing work. It still ignores hand-written instances.

## 5. Measured impact

Measured on a throwaway prototype of the design, built as §10 stages it: §10 step 2, then §4.1
and §4.3 compiled by that compiler, then rebuilt twice more. The stock compiler is the baseline.

- **Bootstrap.** Every stage builds. The compiler's own sources needed no edit: every `print`
  in `stdlib/compiler/` has its instance. The IR the second, third and fourth compilers emit for
  `compile_driver.sprout` is byte-identical, so `refresh-seed` reaches its fixed point on its
  second iteration.
- **Type check.** `--phase check` on every `.spr` / `.sprout` file that calls `print(` in the four
  repositories, nested `.claude/worktrees` copies excluded. Of 535 files that check today, 16
  fail:

| Cause                                       | Files | Fix                                  |
|---------------------------------------------|-------|--------------------------------------|
| `no_prelude`: `Unknown variable: print`      | 7     | `print_str(int_to_string(…))` etc.   |
| user ADT with no instance (`Answers`, a user `Maybe`) | 9 | `deriving (ToString)`          |

  None is in uncharted-suns, repbit or sprout-postgres. The 857 files that fail today, mostly
  rejection fixtures, fail with the same first error both ways (up to type-variable numbering),
  including `type_error/lambda_annotation_mismatch_arg.spr` and `ctor_partial_application.spr`,
  which pass `print` as a value.
- **Package resolution.** Three `app_no_prelude_*` fixtures, run with
  `scripts/package_resolution_gate.sh`'s package root, break the same way (`ctor_clash`,
  `floor_extern`, `then_import`). The fourth fails earlier, as its gate expects. With the 7
  above, 10 `no_prelude` files break.
- **Output.** Of 252 `tests/conformance/run` fixtures, 245 write identical output, 5 are the
  failures above, and 2 change: `intrinsic_as_value` (`1`/`0` → `true`/`false`) and
  `local_named_like_intrinsic` (`Cons(3, Nil)` → `[3]`). `examples/fizzbuzz.sprout` and the
  `ctor_as_function_value*` and `lambda_annotation_slot_agreement` fixtures, which pass `print`
  as a value, write identical output.
- **Value position.** `show_all(xs: List a) where ToString a = list_each(print, xs)` compiles and
  prints `a`, `b` and `1.5`; the stock compiler rejects it.
- **Linear.** `print` on a linear value with a `ToString` instance is accepted; a second use is
  rejected as "used more than once".
- **Effects.** `tests/effects/canaries.spr` reports the same gaps, `each_named` included. It no
  longer tests its stated channel (§7), which the report cannot show.

Not measured: `just test`, golden IR, and the downstream suites' outputs.

## 6. Semantics, typing and diagnostics

**Output changes** for an existing program:

| Value                       | Before                      | After              |
|-----------------------------|-----------------------------|--------------------|
| `true`                      | `1`                         | `true`             |
| `[1, 2]`                    | `Cons(1, Cons(2, Nil))`     | `[1, 2]`           |
| `vec_from_list([1, 2])`     | `Vec(<number>)`             | `[1, 2]`           |
| type with a written instance | structural                 | the instance       |
| `Char`/`String`/`Double` inside a value | number, address, bits | the value      |

**Typing.** `print : a -> Unit !{IO} where ToString a`. A function that prints a value of its own
type variable now needs `where ToString a`. The checker already reports that, prototyped with
`print` as a prelude function:

```
`show_it` needs a ToString instance for type variable `a`, but declares no `where` constraint
for it, so one instance was chosen at definition instead of per call — add `where ToString a`
to its signature
```

An unannotated top-level `let p = print` gets the existing "ambiguous typeclass binding … add a
type annotation that fixes it". `list_each(print, xs)` works at a concrete element type, and now
also under `where ToString a` (§4.3). A linear value with a `ToString` instance can be printed
(§4.1).

**Diagnostics.** One new hint. Today a missing instance reads `No instance of ToString for Answers
in function main`. For a type declared in the program it should add "add `deriving (ToString)` to
`Answers`".

## 7. Compatibility and migration

- 20 files, all in this repository. 11 `no_prelude` files switch to `print_str`: the 10 that break
  (§5), and `app_no_prelude_entry_extern_stays_private.spr`, which fails earlier by design but
  still names `print`. 9 add `deriving (ToString)`.
- Tests whose premise goes away:
  - `tests/stdlib/compiler/test_intrinsic_eta.spr` asserts `print` as a value emits `@print_str` /
    `@print_value` and rejects a free type variable. Re-point it at `to_double`, which stays an
    intrinsic (`eprint` is a plain extern after step 5).
  - `tests/stdlib/compiler/test_repl_completion_surface.spr` treats `print` as a prelude extern
    (offered as a global, not public). Both checks flip for an exported `fn`.
  - `tests/conformance/run/intrinsic_as_value.spr`: its header states the intrinsic premise, and
    its `.out` records `1`/`0` for `Bool`s.
  - `tests/stdlib/test_ir_print_dispatch.spr` checks that `print` dispatches by argument type to
    `@print_value` / `@print_str`. Its premise is gone; it is rewritten for `print_str` or deleted.
  - `tests/conformance/run/local_named_like_intrinsic.spr` says `print` is not a case; it gains a
    `print_str` parameter case. Its `.out` holds a `Cons(…)` that becomes `[…]`.
  - `tests/effects/canaries.spr::each_named` is "THE canary" for an effect entering through a
    function passed by name, bypassing `infer_lambda`. A constrained `print` passed by name is
    eta-expanded into a lambda (`value_ref_eta`, `infer.sprout`), so the canary would keep
    passing without testing that channel. Re-point it at `eprint`, which stays an extern.
  - ~~`tests/c_runtime/read_file_error_print.c` links `print_value` directly.~~ Landed: replaced by
    `tests/c_runtime/fs_io_strings.c` (§10 step 4).
- Expected outputs that record `1`/`0` for a `Bool`, `Cons(…)` for a list, or a number for a
  nested `Char` change. Each one is read, not regenerated blind.
- Downstream: no file fails to check. Their test outputs are re-run under the new compiler before
  landing, since output is not measured (§5).
- Cost per call: `print(n)` builds `int_to_string(n)` before writing it, where `print_value`
  called `printf` directly, so one string allocation. Every call, `print("literal")` included,
  also makes one indirect `to_string` call through the dictionary, where today a `String` is one
  direct `@print_str`. Measured in the golden IR: `print` roots its two arguments around that
  call, and the caller roots the value around the call to `print` (`examples__hello`). Each
  instance method passed as a dictionary gets one eta wrapper per program, shared by every call
  site. The 75 snapshots gain 7725 lines and lose 6711.

## 8. Spec and docs

- §3.1 floor list: `print` → `print_str`. Its `print(Just(3))` example on a user `Maybe` gains
  `deriving (ToString)`.
- §8.1.1 and §8.1.2, "Like `print` and `to_double` … compiler intrinsics": drop `print`. The
  §8.1.2 sentence listing names a local binding may shadow gains `print_str`.
- §8.6 constructor-name rule: one rendering surface, not two.
- *`ToString` instances* table: add `Char`, `Vec`, `Dict`, `Set`, `Result`, `IntRange`, which the
  prelude has; `Maybe` renders `Just(s)`, not `Just s`, so the table is wrong today.
- §8.3 item 3, "prints like an ADT": `print(1..4)` writes the instance's text.
- README §Not Yet Supported: the floor list names `print`.
- `docs/no-prelude-core-v0.md`: says the floor exists so a preludeless file can `print`.
- `docs/idiomatic-sprout.md`: the one `print(to_string(x))` becomes `print(x)`.
- `BACKLOG.md`: the `type_kind.unit_result` note calls it "`print_value`'s result", and the
  `is_hardcoded_intrinsic` entry lists `print`. Both are updated when the change lands.

All of these are normative edits. Sections already marked Experimental stay so.

## 9. Decided: no automatic instances for user types

A user type prints only through `deriving (ToString)` or a written instance, and the
missing-instance error names the fix (§6).

- It matches every other class in Sprout: `Eq`, `Ord` and `Enum` are never automatic either.
  Printing is the exception today only because `print` bypasses `ToString`.
- It matches the type-class languages in §3: Haskell, Lean, Rust and PureScript all require a
  declaration. Automatic printing comes with run-time type information (Go, Swift, the JVM).
- An automatic instance would be conditional: a type with a function field has none, so the rule
  needs an exception that a user cannot see in the declaration.
- Measured cost: 9 files, none downstream.

The cost that stays: a beginner's first `type Color = | Red | Green` and `print(Red)` fails until
they add `deriving (ToString)`. The error names that fix.

The alternative, automatic `ToString` for every ADT whose fields all have one, keeps `print(Red)`
working with no declaration (Kotlin's data classes, Swift). It adds a rule not visible in the
declaration, and needs a rule for which instance wins when the user also writes one.

## 10. Plan

1. ~~Tests first (§11), committed failing. The first is the value-position test: no test in the
   tree passes a constrained function as a value under a type variable, so §2's "can be passed
   as a value" rests on it. It must fail today with "'print' cannot be used as a first-class
   value".~~ Landed.
2. ~~**`print_str` only.** Declare `extern fn print_str` in the prelude and add it to
   `is_call_site_intrinsic` and `translate_print_call`, lowering to `IRPrintStr`. Reseed.~~
   Landed. The seed before it treated `print_str` as an ordinary extern and would have emitted
   `call @print_str(i64)` against `declare @print_str(ptr)`, which `opt --passes=verify` rejects.
   So the seed had to know the new lowering before the prelude calls it.
3. ~~Prelude and floor (§4.1–4.2), compiler (§4.3), the files and tests in §7, expected
   outputs read and updated, the missing-instance hint. `print`'s type and its lowering land
   together. Reseed, `just test`, golden IR, `just compile-examples-stage1`, downstream suites.
   The step-2 seed still lowers `print` by name, ahead of the prelude function, so its stage 2
   differs from the stage 3 it builds. `refresh-seed` needs two iterations here (measured, §5);
   that is expected, not a broken fixed point.~~ Landed.
4. ~~Remove the runtime renderer once nothing emits `print_value` (§4.3), with
   `tests/c_runtime/read_file_error_print.c` (§7). Separate, small.~~ Landed. `print_int`,
   `print_text` and `print_newline` went with it (also unreferenced). The test became
   `tests/c_runtime/fs_io_strings.c`.
5. ~~**Plain externs for the String sinks.** `print_str` and `eprint` were call-site intrinsics
   because the compiler declared `print_str` and `eprint_str` with a `ptr` parameter
   (`runtime_declares`) and cast at each call (`IRPrintStr`). Ten other externs whose C takes
   `const char*`, among them `env_get`, `tcp_write` and `regex_is_match`, are plain externs
   declared `i64`. Make `print_str` one, and change its C parameter to `long long` so the
   declare matches the define. Bind the prelude's `eprint` to the runtime's unused
   `long long eprint(long long)`, and rewrite its `runtime/APPROVED_BUILTINS` line, which calls
   it a bridge. After step 3 only these two reach `translate_print_call`, so it goes, with
   `IRPrintStr` and its `ir_rooting` arms, the `ptr` declares, the `extern_skip_names` entries,
   `eprint_str`, and the print names in `is_call_site_intrinsic` and spec §8.1.2. Add both to
   `is_nonallocating_read`: an unknown extern is a GC trigger (`op_triggers_gc_summary`), so
   without it every print becomes a rooting point. Run time is otherwise unchanged. One reseed;
   for one iteration the old seed's `declare @print_str(ptr)` meets a `long long` define, the
   same register on x86-64 and arm64, as for the ten above.~~ Landed, with two reseeds, not
   one. The compiler calls `eprint` itself, and the old seed lowers that to `@eprint_str`, so
   `refresh-seed`'s first stage 1 links only while the runtime still defines it. The first
   reseed ran with `eprint_str` in place; it was deleted after, and the second reseed proved the
   new seed builds without it.

On landing, these `BACKLOG.md` entries are deleted: "Make compiled `print` dispatch through
`ToString`", "`print` writes a string literal inside an ADT as a number" and "An unannotated
top-level `let p = print` type-checks but cannot be compiled".

## 11. Tests

- Executable: each row of the §1 table prints its `to_string`, at a concrete type and through a
  `where ToString a` function, including nested `Char`, `String` and `Double` fields.
- A hand-written instance is used by `print` (`Red` prints `red`).
- Rejections: `print` on an unconstrained type variable; on a type with no instance (with the
  `deriving` hint); on a function value; unannotated top-level `let p = print`.
- `no_prelude`: `print_str` works, also as a value; `print` is `Unknown variable`.
- A local binding named `print_str` shadows the intrinsic (`local_named_like_intrinsic.spr`).
- The effect canary `each_named`, re-pointed at `eprint`, is still flagged.
- Value position under a type variable: `list_each(print, xs)` inside a `where ToString a`
  function prints each element.
- REPL: an evaluated expression prints through its instance (`eval_main_body`).
- Linear: a linear value with a `ToString` instance prints; using it again afterwards is rejected.
- Generic-vs-direct agreement: `show_it(x)` and `print(x)` write the same text for every type in
  the first test. This is the property specialisation needs.
