# Prelude names the compiler writes, and `prelude.X` (v0)

**Status: implemented 2026-10-08.** The normative rule is spec §3.1. Prerequisite for `try`
(`docs/try-propagate-v0.md` §9 Q9).

Implementation changed two things:

1. **The parser half was dropped** (§5, *Not done*). Writing `prelude.Cons` for list sugar broke
   `no_prelude` files, whose literals must use their own `List`, and every path that type-checks a
   module without bundling it (`module_loader`, `type_driver`, `compiler.compile_source`), where
   nothing resolves the name. `module_loader` drops a failed check silently, so the IDE's prelude
   schemes would have vanished with one unit test as the only sign.
2. **One more capture**: a linear parameter named `to_string` was reported as "never used",
   because its uses were read as the class method's. Renaming fixes it;
   `tests/conformance/type_error/renamed_local_in_diagnostic.spr` pins it.

## 1. Problem

The compiler writes references to prelude names as bare strings: list literals and patterns
(`Cons`, `Nil`), dict literals (`dict_empty`, `dict_set`), string templates (`to_string`,
`string_concat_many`), `Vec` literals (`vec_from_list`), comprehensions (`list_reverse`), and
soon `try` (`branch`, `Continue`, `Break`). User code captures them. Each row below was compiled
on 2026-10-08:

| Construct | Captured by | Result |
|---|---|---|
| `[1, 2]`, `[x \| _]` | the module's own `Cons`/`Nil` | type error |
| `{a: 1}` | the module's own `dict_empty`/`dict_set` | type error |
| `` `${n}` `` | a parameter named `to_string` | **wrong output**: the parameter is called |
| a `Vec` literal | a parameter named `vec_from_list` | type error |
| `[x for x in xs]` | a parameter named `list_reverse` | type error |
| `do` with a user `Just`/`Nothing`; `deriving (Enum)` | — | correct |

Two causes:

1. **Module level.** The parser writes `Cons` before the bundler runs, and the bundler resolves a
   bare name to the module's own declaration first. Names written after bundling are safe from
   this: the module's `Cons` is `main.Cons` by then.
2. **Locals.** After bundling, user globals are qualified (`main.f`) and prelude names are not.
   Locals are not either, so a local and a prelude name can be the same string. Every later pass
   resolves names by string, locals first.

A third gap: a module that declares its own `Continue` cannot name the prelude's at all, so it
cannot write a `Propagate` instance (`stdlib/repl.sprout`, `stdlib/tui/app.sprout`).

## 2. Goals and non-goals

Goals: every name the compiler writes reaches the prelude's declaration, whatever the user
declares or binds; a user can name a shadowed prelude declaration; no change for code that
does not collide.

Non-goals: `RebindableSyntax`-style rebinding; the two limits spec §3.1 already lists (do
notation picks the monad family by bare name; the class-method wrapper symbol); qualifying the
prelude's own declarations (§4, option 2).

## 3. Prior art

Checked against primary sources on 2026-10-08.

| Language | Built-in syntax resolves to | Naming a shadowed library item | Source |
|---|---|---|---|
| Haskell / GHC | the Prelude's names: "the literal "1" means "`Prelude.fromInteger 1`", which is what the Haskell Report specifies"; `RebindableSyntax` switches to "whatever is in scope" | `Prelude.null`: the implicit `import Prelude` brings in qualified and unqualified names (§5.3.2, §5.6.1) | GHC User's Guide, *Rebindable syntax*; Haskell 2010 Report ch. 5 |
| OCaml | — | `Stdlib.x`: Stdlib "is automatically opened at the beginning of each compilation", and its components keep their qualified names | OCaml manual, module `Stdlib` |
| Rust | — | `$crate::path` in a macro refers to the defining crate's items, whatever is in scope at the call | Rust Reference, *Macros by example*, `$crate` |

All three keep library syntax independent of the user's scope, and all give the library a name
that cannot be shadowed by a local. Sprout follows Haskell: built-in syntax means the prelude's
names, and `prelude.X` is the qualified spelling.

## 4. Options

1. **Bundler hygiene (chosen).** `prelude.X` resolves to the prelude's `X`, and the bundler
   renames a local whose name is a prelude value, so no local equals a prelude name after
   bundling. One pass changes, and every rewrite written after bundling, present or future, is
   correct by construction.
2. **Qualify the prelude's declarations** (`prelude.Cons` everywhere). The root cause, but it
   moves every type name in messages, every IR symbol, and every place that matches `"Just"`,
   `"Cons"` or `"to_string"` by string.
3. **Fix each construct's lookup.** Many passes, no writable spelling, and the next construct
   repeats the bug.

## 5. Design

**`prelude.X`.** In the bundler, a dotted name whose head is `prelude` resolves to the prelude's
`X`, in value, constructor-pattern and type positions, skipping the module's own declarations and
its imports. A local named `prelude` and an import alias `prelude` win over it, as they do for any
dotted head. `prelude.X` where the prelude has no `X` stays unresolved, and inference reports it
as an unknown name. Under `no_prelude` only the floor's names resolve. Only the bundler knows
the spelling: a module checked without bundling reports `prelude.X` as unknown.

**Locals.** The bundler renames a binder whose name is a prelude value, class method or extern,
and every use of it, to `$l_<name>` (§5.1). The rename is a pure function of the name, so
nested shadowing keeps its structure and no map is needed. Binders: function and lambda
parameters, instance-method parameters, `VarPattern` in `match`, `let`, `do` and comprehension
patterns, `do`-`let` names. A dotted name whose head is a renamed local renames the head.

### 5.1 The renamed spelling

`$` makes it unforgeable (spec §2: identifiers are `[A-Za-z_][A-Za-z0-9_]*`), as the entry
module's `$entry` does, and LLVM accepts it. No dot, since a dotted local reads as a field chain
in inference. Spelling: `$l_<name>`. `source.strip_entry_names`, the render-boundary strip,
also removes `$l_`, so no diagnostic shows it.

**Not done**, each listed in spec §3.1 and `BACKLOG.md`:

- **Names the parser writes.** List and dict literals, list patterns and `>>`/`<<` use bare
  `Cons`, `Nil`, `dict_empty`, `dict_set`, `rcompose` and `lcompose`, written before the bundler
  runs, so a module's own declaration or a local of that name captures them. Pinned by
  `tests/conformance/type_error/own_cons_captures_list_literal.spr`. Lifting it needs a spelling
  the unbundled checking paths can resolve.
- **User class methods** keep bare names after bundling, so a method named like a prelude
  function captures names written later: a method `list_reverse` breaks comprehensions, and one
  named `branch` would capture `try`. Pinned by
  `tests/conformance/type_error/class_method_captures_comprehension.spr`.

## 6. Impact

- **Syntax.** `prelude` becomes a reserved qualifier, not a keyword: `prelude` is still a legal
  local or alias, and then wins. The four repos use `prelude` only as a local (4 test files).
- **Semantics.** Code that does not collide is unchanged. A local that collided either failed to
  compile or, for templates, was silently called; the construct now gets the prelude's name.
- **Types.** None.
- **Diagnostics.** Renamed locals never appear. `prelude.nosuch` is reported as unknown.
- **IR.** Renamed locals change local names in IR, so the seed and golden IR move by name only.

## 7. Tests

`tests/stdlib/test_prelude_name_identity.spr`: the three local rows of §1, a local named like an
extern, `do`-`let` and `let..else` residual binders, a field chain on a renamed local, `prelude.X`
in an expression, a pattern, a type and a constraint, an extern through `prelude.X`, an instance
using `prelude.Continue` beside a user `Continue`, a local named `prelude`. Conformance:
`prelude.nosuch` rejected; a diagnostic about a renamed local shows its source name; an import
alias named `prelude` wins; both limits in §5.
