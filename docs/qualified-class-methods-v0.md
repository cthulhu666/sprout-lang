# Module-qualified class methods (v0)

Status: **implemented** (stage 1, §7). The normative rules are spec §3 (Imports) and §8.5; this
file keeps the rationale and the rejected alternatives. Where the build differs from the
proposal, §6 says so.

## 1. Problem

After bundling, a user function is `main.f` and a user class is `main.Rev`, but the class's methods
keep bare names (`bundler.qualify_class_methods` passes the name through). The compiler writes
prelude names bare too, so a user method can be the name built-in syntax reaches:

| Construct | Captured by | Pinned by (`tests/conformance/run/` since landing) |
|---|---|---|
| `[x for x in xs]` | a method `list_reverse`, above its class too | `class_method_captures_comprehension{,_above}.spr` |
| an unfused `try` | a method `branch` | `class_method_captures_try.spr` |

The same bare name causes four more faults:

- **Two classes may declare one method; the last wins.** `infer.register_class_method_over` binds
  each method under its bare name, so `@class:calc` names whichever class came last. Lowering's
  `add_method_class` keys the same table on the short name and keeps the *first*, sorted by class
  name, so the two passes can disagree. The cross-class `blank` shape fails at emit with a
  sentinel and no location (`BACKLOG.md` P1, "An ambiguous class-method reference").
- **The wrapper symbol collides.** `lowering.classmethod_dispatch_name` builds
  `__cm_<ShortClass>_<shortMethod>`, so a file's own `class Eq` and the prelude's share
  `__cm_Eq_eq`. Before landing the clash stopped earlier: both methods were the bare `eq`, and
  `dispatch-verify` rejected a dictionary call of each as ambiguous.
- **A method ignores imports.** It reaches every module checked after its own, imported or not,
  because inference's environment carries it forward. Spec §3 (Imports) says an import is the
  only way to reach another module; methods are the exception.
- **A method is not reachable through an alias.** `import m as m` then `m.method(x)` is unknown
  (`BACKLOG.md` P3).

## 2. Goals and non-goals

**Goals.** A user method has a canonical name like a function's. Built-in syntax cannot be
captured by one. A bare method name means one method or is an error. Methods obey the import
rules. Prelude methods, and so every name the compiler writes, keep their spelling.

**Non-goals.** Qualifying the prelude's own declarations (`prelude-name-identity-v0.md` §4,
option 2). Type-directed choice between same-named methods (Rust's method syntax). Registering
classes in `pre_scan_fn_decls`, which this enables (§7, stage 2). The parser-written names of
spec §3.1 (`Cons`, `dict_set`, ...) and do notation's bare `Maybe`.

## 3. Prior art

| Language | Method's namespace | Same name twice | Naming one from another module | Source |
|---|---|---|---|---|
| Haskell | "Class methods share the top level namespace with variable bindings" | "must not conflict with other top level bindings in scope ... or another class method" | qualified, `M.method`; an instance binds a method by any in-scope name, "it may be a qualified name" | Haskell 2010 Report §4.3.1, §4.3.2 |
| PureScript | the module's | — | "If a type class is exported, then all of its members must also be exported" | PureScript docs, *Modules* |
| Rust | the trait's (`Trait::method`) | legal; a call with "more than one possible method" is "a compiler error" needing "disambiguating function call syntax" | the trait must be in scope | Rust Reference, *Method-call expressions* |

Haskell's and PureScript's model fits Sprout: methods are called as plain functions, not through
a receiver, so there is no type to direct a choice. A method is a module-level value, and the
module rules for values apply to it.

## 4. Design

1. **Spelling.** A method of a class declared in module `m` is `m.method` after bundling, as a
   function is. Prelude methods stay bare, as prelude functions do; the entry module's are
   `$entry.method`, stripped at the render boundary as today.
2. **One name per module.** A method may not share its name with another method or a top-level
   value of its module. The second is rejected, naming both: ``calc is declared by class Loud
   and by class Quiet in this module``. (Decision D1.)
3. **Visibility.** A method is exported if and only if its class is. Importing a class by name
   binds its methods, as importing a type binds its constructors, and the methods take part in the
   one-binding-per-file rule. `import m (method)` still binds one method. `import m as a` reaches
   it as `a.method`. A method of a module the file does not import is unknown. (Decision D2.)
4. **Instances.** An instance's method names resolve through its class, not through scope, so an
   instance of `a.Shape` defines `fn area` without importing `area` (Haskell §4.3.2).

Built-in syntax is unchanged and names the prelude's methods, so rules 1-2 close every capture in
§1. A bare method name now means one method or is an error, which settles the P1 entry's
cross-class shape at its declaration: it is rejected under rule 2, and across modules `a.blank`
and `b.blank` say which.

## 5. Decisions

**D1, same name twice in one module: rejected, as Haskell does** (decided 2026-10-18). Keeping it
legal means a class-qualified spelling (`main.Loud.calc`) and a rule for a bare `calc`. The bundler
cannot choose by type, and "last wins" was the bug. Rejecting it broke three fixtures:
`run/effect_class_method_name_not_unique_ok.spr` is removed (its point, that the effect check is
keyed by class, is `package_resolution/app_class_name_collision.spr`);
`tests/stdlib/test_forwarded_slot_selection.spr` moves `Ident.from_int` to another module; and
`emit_error/ambiguous_forwarded_across_classes.spr` is now a `type_error` with a located message.

**D2, methods follow the class** (decided 2026-10-18). Keeping methods global, as now, keeps the
leak and leaves the spec's import rule with an exception. Importing each method by name is
stricter than needed and lengthens every caller's import list. Binding methods with the class
matches PureScript and Sprout's own type-and-constructors rule.

## 6. Implementation

**Bundler.**
- `add_class_to_symbols`: file each method in `value_locals` as `name → m.name` (and in
  `exported_vals` when the class is exported), and keep `class_methods`, bare class → its
  methods. The flat `method_names` list goes, and with it the bare pass-through in
  `bare_value_name`, `ctx_with_methods`, `try_add_method` and the method arm of
  `import_name_binds`.
- A class import binds its methods: `class_binding` returns the class and one `NsValue` binding per
  method, so `first_import_conflict` sees them.
- `qualify_class_methods` qualifies the signature's name. `qualify_instance_methods` renames each
  method from the instance's qualified class alone (`class_method_canonical`: the class's
  module, then the method), so no bundle-wide table is needed. A name the class lacks becomes
  one more unknown method, which inference reports by its short name as before.
- Unchanged: the `$l_` local rename (`local_name`) covers prelude names only, and a local already
  wins over a user method. Deriving runs after qualification and writes bare prelude names.

**Inference.**
- `register_class_method_over` and the `@class:` markers need no change: their keys are now
  canonical. `check_duplicate_module_values` extends to class methods for rule 2.
- Messages that print a method's raw name print the short one (`method_display`), since the class
  is named beside it: `instance_method_err`, `constraint_list_mismatch`, `class_row_message` (which would read
  `main.Tree.main.branch`) and `instance_report_name`.
- Name-keyed lookups that compare an instance method with its class's signature
  (`class_method_key`, `class_method_mode_error`) work unchanged, because rule 4 makes the two
  spellings equal.

**Lowering and codegen.**
- `add_class_methods` (which absorbs `add_method_class`) and `find_class_of_method` key on the
  full method name, not `after_last_dot`. The first-wins/last-wins disagreement goes with rule 2.
- Dispatch symbols name the class in full and the method short, since a method is unique
  within its class: `__cm_<class>_<method>`, `__tc_<class>_<head>_<method>` and the slot
  parameters. `__cm_` used to drop the class's module; the `__tc_` names already kept it, so
  they are unchanged. Prelude names are bare, so `ast_to_ir`'s literal `__cm_Semigroup_append`
  still matches. LLVM takes the dots unquoted; the golden IR already holds `@$entry.f`.
- The short method also settles the entry-name hazard. `ast_to_ir.is_entry_fn_name` and
  `dce.is_entry_name` accept any name ending in `.main`, which a qualified method `main` would
  have put at the end of `__tc_<class>_<head>_m.main`. It ends in `_main` instead.
- Not fixed here: dispatch KEYS (`typed_ast.constraint_key_from_tc`) still strip the class, so
  two same-short-named classes' instances at one head collide. That is a class-identity bug,
  independent of method names, and has its own `BACKLOG.md` P2 entry.

**Other readers.**
- `compiler.with_class_constraint` (hover) looks up `@class:` by the name the scheme was found
  under, and `unqualify_entry_names` re-exposes a method's marker beside the method.
- `iface_codec.extract_named_schemes` will now list user methods among the module's schemes, as
  the exported values they are. The iface version goes from 12 to 13, and is now one constant,
  `iface_codec.iface_version`, which the driver writes and the decoder checks: the first bump
  changed the driver alone and every new iface failed to decode.
- `verify_dispatch` keys its signature table on the method name. Its skip for a function of the
  same name (`collect_fn_names`) is dead under rule 2 and goes.

## 7. Staging

1. **This change**, one PR: rules 1-4, the fixtures in §5 and §9, spec and docs. The compiler
   declares no classes, so the seed moves only by its code. Golden IR moves in four files, by the
   `__cm_` wrapper name alone (`typeclass_collections_demo`, `existential_shapes`, and
   `json_demo`/`sentry_api` through `JsonEncode`); a `no_prelude` entry stays bare, so
   `no_prelude_core` is unchanged.
2. **Later, its own PR** (`BACKLOG.md` P3): register classes in `pre_scan_fn_decls`. The
   `AtModuleStart` placement existed because a bare method registered early would shadow the
   prelude for the prelude; qualified, it cannot.

## 8. Impact

- **Syntax.** None.
- **Semantics.** Rule 2 rejects programs that compile today; §5 lists the in-tree ones. A
  `no_prelude` file's own `class Eq` is no longer reached by `==` on a non-primitive type. That
  was a capture; no in-tree `no_prelude` file relies on it (`run/type_classes.spr` and
  `run/instance_check.spr` call `eq` directly).
- **Types.** None.
- **Errors.** New: rule 2's duplicate, and an unknown method from an unimported module. Changed:
  the P1 entry's emit-time sentinel becomes rule 2's located error.
- **Compatibility.** Measured 2026-10-18: repbit, repbit-cards, sprout-postgres and
  uncharted-suns declare no classes. In sprout_lang, `stdlib/json.sprout`'s `JsonEncode` is the
  only class outside the prelude and tests, and `encode_json` is called bare only inside its
  module. No file uses a bare method that rule 3 would leave unbound (a static scan of `stdlib/`,
  `tests/`, `examples/`, `testsupport/`, `ide/` and `bench/`, 2026-10-18). Five imported modules
  declare classes; the one cross-module use, `draw` in `test_existential_cross_module.spr`, is a
  selective import. The `demo.enc_*` fixtures only define instances, which rule 4 covers.

## 9. Tests

- The three capture fixtures moved to `run/`.
- Run: `class_method_named_eq_beside_deriving` (a method `eq` beside `deriving (Eq)`);
  `class_named_like_prelude_class_dispatch` (a file's own `class Eq` and the prelude's, each
  called through a dictionary). `tests/stdlib/test_class_method_visibility.spr`: `a.method`, a
  class import that binds its methods, first-class use, a method named `main`.
  `test_forwarded_slot_selection.spr` moves `Ident` to `testsupport/ident_class.sprout`.
- Type errors: `class_method_name_shared_by_two_classes` (was
  `emit_error/ambiguous_forwarded_across_classes`), `class_method_name_shared_with_fn`,
  `class_method_name_shared_with_let`. Rule 2 covers `let` too, so a `fn` and a `let` of one name
  are now "defined more than once" as two `fn`s were.
  Package-resolution gate: a method of a bundled but unimported module; a class import beside a
  function import of one of its method names.
- Hover on a user method shows its class constraint (`test_expr_type_in_source.spr`).
- Unchanged: `package_resolution/app_class_name_collision.spr` and
  `app_cross_module_class_reject.spr` (instances of two modules' `Enc`, rule 4), whose message
  must still name the method short.

## 10. Spec and docs on landing

- Spec §3.1: the class-method limit is gone. §3, Imports: naming a class imports its methods.
  §8.5: rules 1-4. §7's effect note no longer says two classes may share a method. §7's "a
  method named like a prelude function names the method throughout its module" still holds.
- `prelude-name-identity-v0.md` §5 and `effect-subsumption-v0.md` point here.
- `BACKLOG.md`: the P2 capture and P3 alias entries are deleted; the P2 wrapper entry became the
  dispatch-key entry; the P1 ambiguity entry keeps its two identity shapes; stage 2 is a P3.

## 11. Rejected alternatives

- **Class-qualified names** (`main.Rev.list_reverse`). This keeps same-named methods legal, but a
  bare reference still needs a rule to pick one, and only a type can pick. That is Rust's
  receiver-directed lookup, which Sprout's call syntax does not have.
- **Rename only user methods that collide with a prelude name.** It fixes §1's table and none of
  the other four faults, and any later prelude name reopens it.
- **Fix each construct's lookup** (comprehension, `try`, deriving). The same option 3 that
  `prelude-name-identity-v0.md` §4 declined: many passes, and the next construct repeats it.
