# `++` / Semigroup Append Dispatch — Bug + De-hardcoding Analysis

**Date:** 2026-07-12
**Status:** Analysis + design fork awaiting a decision. Non-normative until a direction is chosen.
**Branch:** `semigroup-append-dispatch`

---

## 1. The bug (silent corruption)

`a ++ b` on any concrete `Semigroup` type **other than `String` or `List`** compiles to a
null constant and crashes when the result is read.

Confirmed empirically (2026-07-12):

| Expression | Result |
|---|---|
| `"ab" ++ "cd"` | ✅ works (`IRStrConcat`) |
| `[1,2] ++ [3,4]` | ✅ works (`@list_append`) |
| `vecA ++ vecB` | ❌ `sprout_tag: null pointer` → SIGABRT |
| `dictA ++ dictB` | ❌ `sprout_tag: null pointer` → SIGABRT |

The `Vec` and `Dict` `++` instances are documented as working in
`docs/builtins-reference.md` (:619, :622) — both are actually broken. This is
previously-undocumented silent corruption: the type checker accepts the program, the
instance function is emitted, and codegen quietly substitutes null.

## 2. Root cause

`a ++ b` desugars (in the parser) to `CallExpr("append", [a, b])`, where `append` is the
`Semigroup` class method — `append : Semigroup a => a -> a -> a` (the emitted generic
`@append(left, right, __tc_Semigroup_0_append)` is the dictionary-passing form).

Active codegen is `ast_to_ir.sprout` (the typed `--emit-ir` path). Two problems compound:

1. **`translate_append_call` (`ast_to_ir.sprout:4223`) discards the resolved dictionary.**
   It matches both the witness-present `[TDict …, left, right]` and witness-absent
   `[left, right]` shapes, but in *both* cases calls `translate_append_operands(left, right)`
   — the `TDict` (the Semigroup evidence the checker resolved) is thrown away.

2. **`translate_append_operands` (`:4233`) is a hardcoded type switch that null-fills the
   default:**
   ```
   if   is_string_type(...) then IRStrConcat        # :4247
   else if is_list_type(...) then @list_append      # :4268
   else IRConst(..., 0)                             # :4284  <-- null for everything else
   ```
   The `else` comment says it is for "polymorphic append sites (type is a TVar)", but
   **every concrete non-String/non-List type also lands here** — `Vec`, `Dict`, and any
   user-defined `Semigroup` instance — and gets `i64 0`.

The correct instance function `@__tc_Semigroup_Dict_v_append` **is emitted** (define-only)
but is **never referenced**. The dispatch machinery exists; `++` bypasses it.

### The deeper diagnosis

This is an **incoherent hybrid**: `++` is hardcoded to a closed set (like Elm — see §4) but
the language *also* exposes a general `Semigroup` class with `instance Semigroup (Dict v)`,
`instance Semigroup (Vec a)`, etc. The type system says `dict ++ dict` is well-typed; codegen
only knows two types. The mismatch is the bug.

### Second codegen path

`ast_to_ir.sprout` comments say this "mirrors `codegen.sprout` `emit_append_operands`
(~2674)". That is the **direct backend, which is retiring** (the `--emit-ir` typed path is
active; direct codegen is not on the active compile path). It carries the same defect. Any
fix targets `ast_to_ir.sprout`; `codegen.sprout` should be fixed in parallel only if it is
still built, else left to the retirement.

## 3. The decision is a language-semantics fork

The "less hardcoded" goal presupposes direction **B** below, but a complete survey must put
both coherent endpoints on the table. The current code is neither — it is the broken middle.

### Option A — `++` is closed built-in sugar (Elm-style)

`++` is magic over a fixed built-in set (`String`, `List`, maybe `Vec`). There is **no**
general user-facing `Semigroup` append via `++`.

- **Then the honest move is to remove the lying instances** (`Semigroup (Dict v)`,
  `Semigroup (Vec a)`) — or at least stop `++` from resolving to them — so the type system
  rejects `dict ++ dict` at compile time instead of miscompiling it.
- **Pros:** least code; makes the mismatch honest; simplest mental model; fits a
  beginner-friendly language; keeps the String/List fast paths trivially.
- **Cons:** no general append; users must call `dict_append`/`append`-like helpers by name;
  gives up ad-hoc extensibility the class system otherwise promises.
- **Precedent:** Elm's `++` is exactly this (a closed `appendable` built-in over String and
  List, with no general Semigroup). It is coherent *because it exposes no false instances*.

### Option B — `++` is sugar for `Semigroup.append` (Haskell/Rust/Scala-style)

`++` requires a `Semigroup` constraint and dispatches uniformly through the resolved
instance. `String`/`List` (and `Vec`) survive only as an **optional peephole optimization**,
never as the sole dispatch mechanism.

Two implementation shapes, in increasing alignment with the codebase's stated
dict-resolution direction:

- **B1 (consume the discarded witness).** Change `translate_append_call` to *use* the
  `TDict` it currently discards: emit a call to the instance's `__tc_Semigroup_<T>_append`
  from the resolved evidence. Small if the witness is reliably attached; but witness
  attachment appears gated on the `Semigroup` class being in scope, so B1 alone may not
  cover the common case.
- **B2 (the north-star fix).** `++` *implies* the `Semigroup` constraint → the **checker
  attaches the evidence** (independent of whether the user imported the class) → **lowering
  consumes it** and calls the instance. String/List/Vec remain peepholes. This is the
  "resolution lives in the checker, lowering is a printer" architecture the dict-resolution
  north star already commits to.

> **Not an option:** extending the `translate_append_operands` type switch with `Vec`/`Dict`
> cases (a "B0" point-fix). It unblocks the crash but is *more* hardcoding in the exact layer
> that resolution is supposed to leave — the opposite of the north star. Acceptable only as a
> temporary stop-the-bleed if a full fix is deferred, and it should be labeled as such.

## 4. Prior art

How comparable languages dispatch an overloaded append / `<>` / `++`:

| Language | Mechanism | Null-fallback? |
|---|---|---|
| **Haskell** | `<>` is an ordinary `Semigroup` class method; uniform dictionary dispatch. Concrete types are made fast by the optimizer (SPECIALIZE / inlining / rewrite rules), not by hardcoding in the desugarer. | No |
| **Rust** | No `++`. `String + &str` via the `Add` trait; generic code uses trait bounds; **monomorphization** specializes each concrete type (zero-cost). | No |
| **Scala** | `++` is a method on the collection type; overloaded append resolves through method/typeclass dispatch. | No |
| **OCaml / SML** | **Separate operators per type** — `@` (list), `^` (string). No ad-hoc polymorphism in the core, so no overloaded append to dispatch. | N/A (no overloading) |
| **Elm** | `++` is a closed built-in over the `appendable` set (String, List) only; **no general Semigroup**. | No (no false instances) |
| **Sprout (today)** | Hardcoded String/List switch **plus** a general `Semigroup` class with instances codegen ignores. | **Yes — the bug** |

Consensus: languages *with* ad-hoc polymorphism (Haskell, Rust-traits, Scala) dispatch
append uniformly through the instance and lean on the optimizer for per-type speed — never a
hardcoded switch with a null default. Languages *without* it (OCaml, SML) avoid the problem
by not overloading. Elm deliberately keeps `++` closed and honest. Sprout is the only one in
the incoherent middle.

**Reading for Sprout:** Option B matches every language that has typeclasses/traits. Option A
matches the one language (Elm) with the same beginner-friendly ethos Sprout targets. Both are
coherent; the status quo is not.

## 5. Open question for approval

Which semantics does Sprout want for `++`?

- **A** — closed built-in sugar; remove the general `Semigroup (Dict/Vec)` instances so the
  type system rejects `dict ++ dict`.
- **B** — sugar for `Semigroup.append`; dispatch uniformly through the instance (B2 preferred:
  checker attaches evidence, lowering consumes it), String/List/Vec as peepholes.

Regardless of A/B, the **silent null-fill must stop** — under A it becomes a *type error*,
under B a *correct dispatch*. The one thing that must not survive is a well-typed program that
compiles to `i64 0` and crashes.

## Appendix: verification log (2026-07-12)

- Scope: `scope.spr` — String/List `++` pass; `Vec ++` crashes (`sprout_tag: null pointer`).
- Dict: `append2.spr`/`append3.spr` — `dict ++ dict` crashes with both literal and
  variable operands; IR shows the merge as `%t$1 = add i64 0, 0`.
- Instance exists but unused: `@__tc_Semigroup_Dict_v_append` is define-only in the emitted
  IR; generic `@append` is never called.
- Root cause: `ast_to_ir.sprout:4223` (witness discarded), `:4284` (`else → IRConst 0`).
