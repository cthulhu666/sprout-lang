# Module-qualified class methods (v0)

Status: **proposed**, not implemented; decisions D1 and D2 taken. Spec §3.1 lists bare method
names as a known limit; that stays normative until this lands. On landing, the spec carries
rules 1-4 and this file keeps the rationale and the rejected alternatives.

## 1. Problem

After bundling, a user function is `main.f` and a user class is `main.Rev`, but the class's methods
keep bare names (`bundler.qualify_class_methods` passes the name through). The compiler writes
prelude names bare too, so a user method can be the name built-in syntax reaches:

| Construct | Captured by | Pinned by (`tests/conformance/type_error/`) |
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
  `__cm_<ShortClass>_<shortMethod>`, so a file's own `class Eq` and the prelude's both emit
  `@__cm_Eq_eq` and the IR is rejected (`BACKLOG.md` P2).
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
cannot choose by type, and "last wins" is today's bug. Rejecting it breaks three fixtures, which
move to two modules: `run/effect_class_method_name_not_unique_ok.spr` (its point, that the effect
check is keyed by class, survives as two modules), `tests/stdlib/test_forwarded_slot_selection.spr`
(`Make.from_int`, `Ident.from_int`) and `emit_error/ambiguous_forwarded_across_classes.spr`, which
becomes a `type_error` with a located message.

**D2, methods follow the class** (decided 2026-10-18). Keeping methods global, as now, keeps the
leak and leaves the spec's import rule with an exception. Importing each method by name is
stricter than needed and lengthens every caller's import list. Binding methods with the class
matches PureScript and Sprout's own type-and-constructors rule.

## 6. Implementation

**Bundler.**
- `add_class_to_symbols`: file each method in `value_locals` as `name → m.name` (and in
  `exported_vals` when the class is exported), and keep a class → methods map. The flat
  `method_names` list goes, and with it the bare pass-through in `bare_value_name`,
  `ctx_with_methods` and the method arm of `import_name_binds`. `try_add_method` becomes the
  ordinary selective value import.
- A class import binds its methods: `class_binding` returns the class and one `NsValue` binding per
  method, so `first_import_conflict` sees them.
- `qualify_class_methods` qualifies the signature's name. `qualify_instance_methods` renames each
  method to the canonical name of the instance class's method with that short name; an unknown
  one is left alone, and inference reports it as today.
- Unchanged: the `$l_` local rename (`local_name`) covers prelude names only, and a local already
  wins over a user method. Deriving runs after qualification and writes bare prelude names.

**Inference.**
- `register_class_method_over` and the `@class:` markers need no change: their keys are now
  canonical. `check_duplicate_fn_decls` extends to class methods for rule 2.
- Messages that print a method's raw name print the short one, since the class is named beside
  it: `instance_method_err`, `constraint_list_mismatch`, `class_row_message` (which would read
  `main.Tree.main.branch`) and `instance_report_name`.
- Name-keyed lookups that compare an instance method with its class's signature
  (`class_method_key`, `class_method_mode_error`) work unchanged, because rule 4 makes the two
  spellings equal.

**Lowering and codegen.**
- `add_method_class` and `find_class_of_method` key on the full method name, not
  `after_last_dot`. The first-wins/last-wins disagreement goes with rule 2.
- `classmethod_dispatch_name` keeps the module: `__cm_<class>_<method>`, both in full. Prelude
  names are bare, so `ast_to_ir`'s literal `__cm_Semigroup_append` still matches. LLVM takes the
  dots unquoted; the golden IR already holds `@$entry.f`.
- `ast_to_ir.is_entry_fn_name` and `dce.is_entry_name` accept any name ending in `.main`. A method
  `main` would make `__tc_<class>_<head>_m.main` an entry point. Exclude `__`-prefixed names.

**Other readers.**
- `compiler.with_class_constraint` (hover) looks up `@class:` by the bare name. It retries with
  the entry prefix, as `env_scheme` does.
- `iface_codec.extract_named_schemes` will now list user methods among the module's schemes, as
  the exported values they are. Bump the iface version (12 to 13).
- `verify_dispatch` keys its signature table on the method name. Its skip for a function of the
  same name (`collect_fn_names`) is dead under rule 2 and goes.

## 7. Staging

1. **This change**, one PR: rules 1-4, the fixtures in §5 and §9, spec and docs. The compiler
   declares no classes, so the seed moves only by its code. Golden IR moves by symbol name in the
   examples that declare or use one (`typeclass_collections_demo`, `existential_shapes`,
   `no_prelude_core`, and `json_demo`/`sentry_api` through `JsonEncode`).
2. **Later, its own PR**: register classes in `pre_scan_fn_decls`. The `AtModuleStart` placement
   exists because a bare method registered early would shadow the prelude for the prelude
   (`group_plan`'s comment); qualified, it cannot.

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

- The three capture fixtures move to `run/`, as their comments say.
- Run: a method named like a prelude function beside a `deriving (Eq)` type in its module;
  `a.method` through an alias; a class import that brings its methods; an instance of an imported
  class that does not import its methods; a method named `main`; two modules each declaring a
  class `Eq`, with a first-class use of each (the wrapper collision).
- Type errors: two classes sharing a method; a method and a `fn` sharing a name; a method from an
  unimported module; a class import beside a function import of one of its method names.
- Hover on a user method still shows its class constraint.
- Unchanged: `package_resolution/app_class_name_collision.spr` and
  `app_cross_module_class_reject.spr` (instances of two modules' `Enc`, rule 4), whose message
  must still name the method short.

## 10. Spec and docs on landing

- Spec §3.1: drop the class-method limit. §3, Imports: add rule 3 beside "naming a type already
  imports its constructors".
  §7: the "named like a prelude function" sentence goes, and the effect note's "two classes may
  declare one" becomes rule 2.
- `prelude-name-identity-v0.md` §5: the user-class-methods bullet points here.
- `BACKLOG.md`: delete the P2 capture entry, the P2 wrapper entry and the P3 alias entry; narrow
  the P1 ambiguity entry to its two identity shapes.

## 11. Rejected alternatives

- **Class-qualified names** (`main.Rev.list_reverse`). This keeps same-named methods legal, but a
  bare reference still needs a rule to pick one, and only a type can pick. That is Rust's
  receiver-directed lookup, which Sprout's call syntax does not have.
- **Rename only user methods that collide with a prelude name.** It fixes §1's table and none of
  the other four faults, and any later prelude name reopens it.
- **Fix each construct's lookup** (comprehension, `try`, deriving). The same option 3 that
  `prelude-name-identity-v0.md` §4 declined: many passes, and the next construct repeats it.
