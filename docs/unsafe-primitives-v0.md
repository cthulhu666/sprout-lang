# Unsafe primitives v0: `unsafe_pure`

Status: **design, awaiting approval.** Experimental once landed (stdlib-internal; spec §6 note).
BACKLOG P1 "Unsafe primitives: trusted Sprout in place of C".

## 1. Problem

`stdlib.linvec` is pure over in-place writes (docs/persistent-array-v0.md §5.3). Its writes are
three C builtins that exist only to carry a pure type: `linvec_set`, `linvec_push` and
`linvec_truncate` (`runtime/sprout_runtime.c`, each marked "deleted once unsafe primitives exist"
in `runtime/APPROVED_BUILTINS`). Each wraps an existing `!{IO}` extern — `vector_mutset`,
`vector_push`, `vector_truncate` — and returns the vector. Sprout cannot write them itself:

- A pure body may not call an `!{IO}` function (spec §7 rule 11), and nothing dismisses an effect.
- A redeclared extern keeps its signature, effects included (spec §3, "Externs are outside the
  module system"), so linvec cannot redeclare `vector_push` as pure.

Every future structure of this kind (a linear hash map, `bytes.build`, docs/byte-buffer-v0.md §5.3)
would add more such builtins, against "fewest, generic builtins".

One more fact shapes the design. Declaring an extern is already unchecked trust: a module may
declare any extern with any signature when the canonical declaration is not in its build. A probe
compiled a user file declaring `extern fn vector_mutset(...) -> Unit` (pure); DLE then deleted the
call, leaving no call to `vector_mutset` in the IR. A hidden effect whose result is unused is
deleted, so whatever hides an effect must return the thing it changed.

## 2. Goals and non-goals

Goals:
- Delete `linvec_set`, `linvec_push` and `linvec_truncate` with no change to linvec's API.
- One generic primitive, usable only in modules the compiler lists, never in user code.
- No closure, no allocation: a `linvec` write is one call to the C writer (`just linvec-smoke`).
- The optimiser stays sound, with the rules it must keep written down.

Non-goals:
- User-level unsafe code, or any `trusted` marker users could write.
- Defending against a deliberate spoof. A module's name is its header
  (`bundler.scan_module_name`), and nothing compares it with the import path that loaded the file,
  so any file headed `module stdlib.linvec` or `module stdlib.unsafe`, entry or imported, passes
  the gate. Such a user could already declare any C function pure through an extern.
- Gating extern declarations. User code declares externs (uncharted-suns `loam/gfx.sprout`,
  `loam/audio.sprout`, `loam/digest.sprout`, `game/wire_json.sprout`; sprout-sqlite's FFI), and a
  user extern can still state a C function pure. That hole is unchanged (§8).
- `bytes.build` (byte-buffer-v0 §5.3) and raw loads/stores (native-sprout-runtime-v0 Phase 4).
  Both can use this primitive later; neither ships here.
- Bounds-check elimination over `LinVec` writes (persistent-array-v0 §5.3, the third table).

## 3. Prior art

Every quote checked against the source named.

| Language | Mark | Where | Who may use it | Optimiser caveat |
|---|---|---|---|---|
| Koka | `unsafe-total(action: () -> e a) : a`, module `std/core/unsafe` | use site (name) | anyone | "pretends it did not have any effect at all" |
| GHC | `unsafePerformIO`; `{-# LANGUAGE Trustworthy #-}` | defining module | `-XSafe` + `-fpackage-trust` limit it to trusted packages | "Use {-# NOINLINE foo #-}", "-fno-cse", no let-floating |
| linear-base | `Unsafe.toLinear` + `runRW#` | use site, by convention | anyone ("Do not use this unless you have to", `Unsafe/Linear.hs`) | `{-# NOINLINE set #-} -- prevents the runRW# effect from being reordered` |
| Rust | `unsafe fn` / `unsafe {}` | both | `forbid(unsafe_code)`, own crate only | compiler assumes all values "valid" |
| Swift 6.2 | `@unsafe`, `unsafe` expr, `@safe` | both | opt-in warnings per module | diagnostics only |
| Agda | `postulate` | definition | `--safe`, coinfective over imports | — |

Sources: koka `lib/std/core/unsafe.kk`; GHC `System.IO.Unsafe` haddock and the Safe Haskell chapter
of the users guide; linear-base `src/Data/Array/Mutable/Unlifted/Linear.hs` ("made possible by
using linear types to make sure array references are single threaded") and `src/Unsafe/Linear.hs`;
Rust reference "Behavior considered undefined"; SE-0458; Agda "Safe Agda".

Where they diverge: Koka and linear-base gate by name only; GHC and Agda let a consumer forbid it,
GHC by package name. Sprout takes Koka's shape (one effect-dismissing function) with a fixed list
of module names in the compiler, since the only intended users are stdlib modules. linear-base is
the same use: a linear, pure array over an effectful write. GHC's NOINLINE and `-fno-cse` guard
against passes Sprout does not have; §4.4 turns them into a rule for later passes.

Koka warns: "Do _not_ dismiss `:io` since it has the `:exn` effect that should be handled". That
does not carry over: Sprout's `IO` has no handlers and carries no exception effect; a failure is a
`Result` or a trap.

## 4. Design

### 4.1 The primitive

```sprout
extern fn unsafe_pure(action: local Unit -> a !{IO}) -> a
```

Runs `action(())` and gives its result a pure type. Contract, which the caller proves: **apart from
a trap, the action's effects are observable only through its result.** In linvec the result is the
`LinVec` whose vector the action wrote, and linearity means no one else holds that vector.

It is a compiler intrinsic with no C symbol, like `print` and the `stdlib.bits` operations: the
compiler replaces each call with inline code (`ast_to_ir.is_call_site_intrinsic`). Two restrictions
make it free and greppable:

- **The argument must be a lambda literal.** `unsafe_pure(\_ -> body)` lowers as the directly
  applied lambda `(\_ -> body)(())`, which `translate_call` already inlines without a closure (the
  IIFE path that `let … in` and `where` use). `unsafe_pure(f)` for a variable `f` is an error.
- **No first-class reference.** `map(unsafe_pure, xs)` is an error. The other intrinsics are
  allowed in value position: `synthesize_intrinsic_eta_closure` builds a wrapper whose body lowers
  the operation. For `unsafe_pure` that wrapper would take the action as a parameter, which the
  first restriction forbids.

Both are type rules in `infer`, not lowering checks, so `--phase check`, `just check` and the IDE
report them.

A local binding named `unsafe_pure` is called as the local, never lowered as the intrinsic:
`translate_call` checks captures and parameters before `is_call_site_intrinsic`
(`tests/conformance/run/local_named_like_intrinsic.spr`). Otherwise any user could dismiss `IO`
through a parameter, with no declaration. §7 adds an `unsafe_pure` case.

`local` on `action` lets the lambda capture a borrowed value, as at any `local` parameter
(spec "Non-escaping closure parameters"). linvec does not need it today. The gate does not rest on
it (§4.2).

`IO` is the only effect label, so it is the only one dismissed.

### 4.2 The gate

`stdlib/unsafe.sprout` declares `unsafe_pure` once, private, with the contract as its comment.
`UNSAFE_PURE_USERS`, a hand-written list of module names in compiler source, says who may call it
(first entry: `stdlib.linvec`). Two bundler rules enforce it, both by module name:

1. **Reference.** `guard_private_extern` already marks a reference to a private extern from a
   module that does not declare it, and `infer` reports the mark. It leaves a reference to
   `unsafe_pure` unmarked when the referring module is listed. `ResolveCtx` carries no module name
   today; it gains one. From any other module the reference
   is marked, and the error is `` `unsafe_pure` is reserved for selected stdlib modules ``.
2. **Declaration.** Only `stdlib.unsafe` may declare `unsafe_pure`, and `export` on it is an error.
   Rule 1 alone is bypassed by a module declaring its own copy, since an extern is identified by
   its name, not its module. The export ban is stated here rather than left to spec §3's `local`
   rule, so relaxing that rule cannot make `unsafe_pure` exportable.

A listed module writes `import stdlib.unsafe`, which puts the declaration in the build and shows at
the top of the file that it uses `unsafe_pure`. The import is not the gate. Imports do not control
externs: an exported one is reachable from every module once its declarer is in the build (spec §3,
"Externs are outside the module system"), and a linvec user has `stdlib.unsafe` in its build. A user
may also import it directly. Every reference is checked against the list.

Why not the alternatives:
- **An exported Sprout wrapper** (`export fn unsafe_pure(f) = …`) would be gated by imports, but
  its body passes the parameter `f`, not a lambda literal: inline lowering fails and a closure is
  built.
- **A declaration in each listed module** works, but repeats the signature and the contract per
  module.
- **A name known only to the compiler** needs no declaration, but then the signature and the
  contract live nowhere a Sprout reader looks.

Adding a user is a reviewed compiler edit plus a reseed, as for `ir_rooting.is_nonallocating_read`.

### 4.3 linvec

linvec declares its writers itself, private, with `stdlib.mutable`'s signatures. Spec §3 allows an
identical redeclaration and `infer.extern_decl_ok` checks it; linvec imports `stdlib.mutable`, so
both are always in the build. `scripts/check_extern_signatures.sh` gets an exception for these
three symbols in `stdlib/linvec.sprout`. `stdlib.mutable` keeps them private, so importing it still
does not let a caller write through a borrowed `Vector`.

The three writes stay private helpers with today's names, between `# oracle-begin` and
`# oracle-end` markers, and `set`, `push`, `pop`, `scatter` and `accum` keep calling them:

```sprout
import stdlib.unsafe

extern fn vector_mutset(v: Vector a, i: Int, val: a) -> Unit !{IO}
extern fn vector_push(v: Vector a, val: a) -> Unit !{IO}
extern fn vector_truncate(v: Vector a, n: Int) -> Unit !{IO}

# oracle-begin
fn linvec_push(x: a, v: LinVec a) -> LinVec a =
  match v with
  | LinVec raw -> unsafe_pure(\_ -> do
      vector_push(raw, x)
      LinVec(raw))
# oracle-end
```

`linvec_set` drops its own bounds check: `vector_mutset` traps on the same indices, so a set stays
one C call. The trap message becomes `vector_mutset: index out of bounds`. The trust-boundary
comment in linvec.sprout stays: a raw `Vector` must not alias.

### 4.4 Rules for the optimiser

The three rules of persistent-array-v0 §5.3 hold for every `unsafe_pure` call, with one more:

4. **No pass changes how many times an action runs, or its order relative to other calls on the
   same value.** DLE may delete a call whose result is unused: by the contract, nothing else saw it.
   Copying code is allowed when each run still runs each action once, as BCE's `$fast` clones do.

Today no pass moves, merges or repeats a call (opt-passes-v0: CSE declined, LICM open). Rule 4 is
GHC's NOINLINE plus `-fno-cse` stated as a rule rather than a pragma. It constrains passes that do
not exist yet, so no test can check it now; a pass that moves or merges calls must test it.

**Bounds-check elimination.** `bce.sprout` treats a pure function as unable to change a length
(`decl_node` gives it no node). linvec's push and pop do change one, behind a pure type, now in
Sprout rather than C. They stay out of BCE's reach because a `LinVec`'s vector is fresh, never
aliases a vector BCE analyses, and `freeze` hands out an immutable `Vec`. BCE does not analyse
`linvec_set` itself: `analyze` looks only at self-recursive functions over vector accesses, and
`wrapper_of` only at a body that is a direct `vector_mutset` call.

The comment above `safe_io_externs` states the premise. `just linvec-smoke` replaces its grep with
a check, through `SPROUT_TRACE_BCE=1`, that BCE versions no loop in a `stdlib.linvec.*` or
`stdlib.linheap.*` declaration. That checks linvec's own loops, not the alias premise, which rests
on linearity and the abstract type. BCE over `LinVec` writes would break the premise and must
first teach the summary to see `unsafe_pure`.

**Rooting.** `linvec_set`, `linvec_push` and `linvec_truncate` leave `is_nonallocating_read`;
`vector_mutset`, `vector_push` and `vector_truncate` are already on it. `recognize_vector_op` (B1)
needs a static `Vector Double`, which linvec's polymorphic `raw` never is.

## 5. Impact

- **Syntax:** none.
- **Typing:** a new intrinsic; its argument is checked as an `!{IO}` lambda, its result is pure.
- **Diagnostics:** new errors for a reference from an unlisted module, a declaration outside
  `stdlib.unsafe` (or one written `export`), a non-lambda argument and a first-class reference. A
  `linvec.set` out of range now reports `vector_mutset: index out of bounds`.
- **Runtime:** deletes three C functions. A `linvec` write calls `vector_mutset`, `vector_push` or
  `vector_truncate` directly.

## 6. Compatibility and migration

No API change. The deleted builtins were private to `stdlib.linvec`; downstream repos
(uncharted-suns, repbit, sprout-pg, sprout-sqlite) name none of them, nor `unsafe_pure`. Golden IR
for linvec users changes (the `declare` lines and the calls).

## 7. Tests

- Conformance `type_error/`: a user module that imports `stdlib.unsafe` and calls `unsafe_pure`; a
  user module declaring `extern fn unsafe_pure`; an entry file headed `module stdlib.unsafe` that
  writes `export extern fn unsafe_pure` (rule 2's message, not §3's).
- Conformance `run/`: a user module that imports `stdlib.unsafe` and never references
  `unsafe_pure` compiles and runs; a user function whose parameter is named `unsafe_pure` calls
  the parameter.
- The two shape rules (a non-lambda argument, a first-class reference) and their accepted form, in
  a patched stdlib root as `scripts/linvec_smoke.sh` builds its oracle: a user module cannot reach
  them past the reference gate.
- `type_error/linvec_set_private`: `linvec_set` becomes a private Sprout function, so the `.spr`
  expects the error for a name the module does not export, and the `.fixed.spr` calls `linvec.set`.
  `private_extern_reached_from_importer` already covers `vector_mutset`.
- `test_linvec.spr` and `test_linheap.spr` unchanged, passing.
- `just linvec-smoke`:
  - the alloc check, unchanged (a set allocates nothing);
  - an IR check that `@stdlib.linvec.set` calls `@vector_mutset` and builds no closure;
  - the oracle deletes the marked region and appends the copying helpers;
  - the trap expectations move to the `vector_mutset` message;
  - the BCE check of §4.4.
- `test_ir_rooting.spr`: drop T19b and T19c.
- `just refresh-iface` and `just check stdlib/linvec.sprout` pass.
- Golden IR read, then regenerated; `just o2-codegen-smoke`; GC stress on linvec and linheap.

## 8. Spec and docs

- spec §6 (builtins): one paragraph — `unsafe_pure` exists, is reserved for listed stdlib
  modules, and is the second place the effect discipline rests on an assertion. Experimental.
- persistent-array-v0 §5.3 and §12 step 17: the builtins are gone; rule 4.
- bounds-check-elimination-v0, builtins-reference, stdlib-reference (`stdlib.unsafe`),
  APPROVED_BUILTINS, gates.md.
- BACKLOG: delete "Unsafe primitives". File "user externs can declare a C function pure"
  (§2 non-goal) if no entry covers it.

## 9. Implementation

1. Failing tests for the gate and the two shape rules.
2. `stdlib/unsafe.sprout`; the intrinsic: the shape rules in `infer`, a module name in
   `ResolveCtx`, the bundler's reference and declaration rules, lowering through the IIFE path.
3. linvec over `unsafe_pure`; the extern-signature exception for its three writers.
4. Delete the three builtins and their table entries; linvec-smoke and rooting tests.
5. Docs, BACKLOG, reseed, golden IR, gates.
