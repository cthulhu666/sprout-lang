## 7. Typing Rules (High Level)

1. Every expression has exactly one type after inference/checking.
2. `if` condition must be `Bool`.
3. Function application requires argument types to unify with parameter types.
   Arguments are checked in **two passes**: the non-lambda arguments are inferred
   and unified against their parameter slots first, then each lambda argument is
   inferred with its parameter types taken from the (now-resolved) slot. Argument
   *evaluation* order is unaffected; only the order in which the checker visits
   them differs. See §5.3.
4. Lambda expressions introduce parameter bindings for their body and infer a
   function type from parameters to body result. In argument position the
   parameter types come from the callee's parameter slot (rule 3).
5. `match` branches must have a unified result type.
6. Pattern-bound variables are scoped to their branch.
7. ADT constructors produce values of their declared type.
8. Effect annotations are checked on function types; omitted annotations mean purity.

   **An instance method may not declare more effect than its class method.** A call
   dispatches through the class signature, so the instance's own annotation is invisible
   to callers. Declaring *less* is legal — a pure implementation of an `!{IO}` method is
   accepted, by the same subsumption that lets a pure body sit under an `!{IO}`
   signature. Purity is below an effect variable, which is below `!{IO}`: a variable on
   the class side is instantiated per call site and so cannot cover a concrete `!{IO}`
   instance, and a variable on the instance side absorbs whatever the body performs and
   so is not covered by a pure class. `!{e}` under `!{e}` is accepted however the two are
   spelled, since both name the class method's own quantified variable.

   This compares two **declarations**. It does not subsume the first sentence, which
   checks an instance method's body against its own annotation, and neither implies the
   other: in every program this rule rejects, the body and its own annotation agree.

   **The guarantee is about the two declarations, not about what runs.** Where both sides
   declare a variable, property 3 below still applies to each of them independently: an
   instance declaring `!{e}` may have a body that performs IO, and a caller may
   instantiate `e` at purity. That is rule 8's standing variable exemption — a plain
   `fn f(x: Int) -> Int !{e}` whose body prints launders the same way, with no class
   involved — and this rule neither widens nor narrows it.

   **A function VALUE may not enter a slot that permits less effect than it performs.**
   Passing an `!{IO}` function where a pure arrow is required is rejected, in an argument
   and in a return. The reverse is accepted: a pure function is usable wherever an `!{IO}`
   one is expected, which is the same subsumption as above. The comparison is
   **directional**, and the direction reverses when descending into an arrow's parameter —
   so a function consuming a pure arrow may not stand where one consuming an `!{IO}` arrow
   is required. An effect *variable* on either side records a **bound** rather than
   rejecting — a floor from an observed value, a ceiling from a written slot — and rejects
   only where the two contradict. That is what keeps `!{e}` combinators usable at both
   purities, while a variable inference has already resolved to `!{IO}` is still caught:
   the bound, not the spelling, decides.

   **A peer join is covered too.** `if c then pure_fn else io_fn` is judged against a fresh
   result type that neither branch owns, so both are compared as values and the verdict does
   not depend on which branch is written first — the same treatment a `match` arm already
   received. A join whose branches differ in effect is rejected wherever the result is
   required to be pure, in either spelling.

   **This holds under a parameter too.** A callback slot is contravariant, so joining an
   `!{IO}` slot with a pure one leaves the slot at the pure callback *both* branches accept,
   not at whichever branch was written first. Declaring such a join to accept an `!{IO}`
   callback is rejected; declaring it to accept a pure one is legal.

   A type ARGUMENT is judged **covariantly**, which is unsound in principle for a mutable
   container: `Ref` should be invariant in its argument. No program reaching it is known —
   four `Ref` shapes that laundered before bounds were added are now rejected, because a
   bound travels through the shared type variable whether or not the descent was
   variance-correct. So the guarantee here rests on bounds, not on variance being right,
   and per-constructor variance (§6.4) remains unimplemented.
9. Function types may quantify a singleton effect variable `!{e}`; use sites
   instantiate it with either purity or a concrete closed effect supported in v0.
   **Singleton is a limit, not a description**: a signature may name at most one
   effect variable. `f: a -> Unit !{e}` beside `g: b -> Unit !{d}` is rejected
   whether or not the body ever combines them — the limit is on the signature, so
   an unused second variable violates it just as a used one does. Write the same
   variable on every effect-polymorphic parameter instead.

   An annotation is a single concrete effect (`!{IO}`), a single effect variable
   (`!{e}`), the empty row `!{}`, or omitted. The last two both mean purity:
   `!{}` spells it explicitly and an omitted annotation is sugar for it. A
   multi-label row — `!{IO, e}`, `!{a, b}` — is not a form this section defines
   and is rejected wherever it is written, including on a parameter's arrow.
   Note `!{IO, e}` names only one variable and so satisfies the singleton limit
   above; it is rejected under this sentence rather than that one.

   **A label that is not a concrete effect this version defines is ill-formed**,
   not an effect variable. `IO` is the only such label in v0. A lowercase name is
   a variable; anything else — `!{NOPE}` — is rejected **where the annotation is
   read**, which is one place, so every position that can carry one is covered:
   a return type, a parameter, a *lambda* parameter, a record field, a
   constructor payload, an alias, a `where` constraint and a class method
   signature alike. A decoded module interface is checked separately, at its own
   boundary. Without this the rule above has a one-token opt-out: an unrecognised
   label would parse as a variable, bind against everything, and carry IO through
   a pure signature unchallenged.

   **An annotation attaches to the innermost arrow, and parentheses do not move
   it.** In `a -> b -> C !{IO}` the effect belongs to `b -> C`, and
   `(a -> b -> C) !{IO}` denotes that same type. An annotation followed by a
   further `->` must itself be enclosed: write `((a -> b) !{IO}) -> C`, not
   `(a -> b) !{IO} -> C`, which does not parse. On a type with no arrow —
   `Int !{IO}` — there is no effect slot and the annotation carries no meaning.

   **A zero-parameter function has an arrow.** `fn f() -> T` has type `() -> T`,
   not `T`: an empty parameter list is an arrow with no parameter, and it carries
   the declared effect like any other. `fn main() -> Unit !{IO}` therefore has
   type `() -> Unit !{IO}`.

   `()` is not itself a type — it is only an empty parameter list, and is
   accepted in type position only directly before `->`. `() -> T` and
   `Unit -> T` are distinct types: the first names a function taking no
   arguments, the second one taking a single `Unit`. Neither is assignable to
   the other, and calling a `() -> T` requires the empty argument list `f()`.

   > **Not yet enforced (2026-09-07).** At most one annotation applies to an
   > arrow. Writing two — by nesting, `(a -> b !{e}) !{IO}`, or by annotating an
   > alias that already carries one, `type alias H = Int -> Int !{IO}` used as
   > `H !{e}` — is not a form this section defines, and is a rejection case. The
   > compiler does not reject it: the outer annotation silently replaces the
   > inner one, and in the alias spelling the replaced annotation is not visible
   > at the use site. Tracked in `BACKLOG.md`. Do not read the placement rule
   > above as licensing a second annotation.

   **An effect variable may appear only in a function type.** Writing one in a
   *stored* position — a record field, a constructor payload, or the type a
   `wrap` names — is rejected.
   The declaration quantifies the variable and then has nowhere to record what it
   was bound to, so construction discards it and reading the field back yields a
   fresh, unconstrained variable, which rule 8 admits; the effect is therefore
   unenforceable and IO can escape a pure signature. A concrete `!{IO}` in the
   same position is unaffected and remains the way to store an effectful
   function.
10. `main` must use a concrete effect annotation when effectful; it may not be
    effect-polymorphic.
11. A pure function body may not call `!{IO}` functions unless it is allowed by
    the surrounding singleton effect variable instantiation.
12. Tuple expressions and tuple patterns use structural, exact-arity typing.
13. In a function-local `where` block, each binding is checked in order using the
    function parameters and earlier local bindings; later bindings are not in scope.
    Each binding's type comes from its right-hand side and is then available to the
    body — information flows right-hand-side to binding, never body to binding
    (§5.1). `where` and `let … in` are required to type identically.
14. Function-local `where` bindings may destructure tuples, but constructor, literal,
    and general pattern bindings are not part of v0. A tuple pattern binds the
    right-hand side's element types positionally; the elements are not unified with
    one another, so `(a, n) = (x * 1.5, 4)` binds `a : Double` and `n : Int`.
15. **Signature rigidity.** A type variable *written* in a function's or an instance
    method's signature is
    universally quantified: the caller, not the body, chooses what it stands for.
    The body must therefore leave it abstract, and a body that constrains it is a
    compile error — "Signature too general for its body". A written variable is
    constrained when it resolves to a type that is not a type variable
    (`fn f(x: a) -> Int = x + 1` forces `a` to `Int`; `fn f(x: a, g: b -> c) -> a = g`
    forces `a` to `b -> c`), or when two written variables resolve to the *same*
    variable (`fn f(x: a, y: b) -> a = y` forces `a = b`) — an equation the signature
    did not state. Both are rejected: a caller may still instantiate the variable at
    anything, so accepting either would let the caller and the body disagree about a
    value's representation. The rule applies only to variables the programmer wrote;
    an **omitted** parameter or return type is an inference request, not a promise,
    and inference is free to specialize it.
16. **Omitted annotations are monomorphic until inferred.** An omitted parameter
    or return type stands for **one** type variable belonging to that declaration,
    shared by every use of it. It is *not* universally quantified before the
    declaration's body is inferred, so two callers cannot resolve it to two
    different types: the first use to constrain it commits it, and a second use
    that disagrees is a compile error. Once the body is inferred, the declaration
    generalizes over whatever remains free in its own type — so
    `fn id(x) = x` is `forall a. a -> a`, usable at any type thereafter.

    This is Hindley-Milner's rule for a declaration group (Haskell 2010 §4.5.2):
    infer without universal quantification, then generalize at the boundary. It
    is what makes unannotated **mutual recursion** infer without annotations —
    each member sees the other's shared variable rather than a fresh copy — and
    it is why an unannotated declaration can no longer be given one type by a
    caller and a different one by its own body.

    **Declaration order is not significant.** Top-level declarations are grouped
    into binding groups — each a minimal set of mutually dependent declarations —
    and the groups are typed in dependency order, so a declaration is always
    typed after the declarations it refers to. Moving a declaration within a file
    cannot change whether the program is accepted, nor which declaration a
    conflict is reported against. A `record`'s fields are in scope throughout
    its module, as its type name is: a `fn` above the declaration may read,
    construct and update it. A `class` and an `instance` are in scope throughout
    their module too: a declaration above a class may call its methods and
    require it in a `where` clause, one above an instance may use it, and an
    instance's methods may use the instance itself, so a recursive type's `Eq`
    compares its children with the instance being declared. An instance in a
    module that imports this one is not in scope here: an imported module is
    checked first. A method named like
    a prelude function names the method throughout its module, and the function
    in the prelude. An alias is in scope throughout its module (§5.6.2). A `fn` may not be moved above a top-level
    `let` it depends on: a `let` must already be in scope where it is used.

    **Polymorphic recursion requires a complete, constraint-free signature.** A
    function may call itself at a *different* type than the one it was called with
    only when every parameter is annotated, the return type is annotated, and the
    declaration has no `where` clause; then its own name is bound, inside its own
    body, to its declared quantified scheme. Omit any annotation, or add a
    constraint, and the name is bound monomorphically inside its own body — a
    self-call must then have the enclosing declaration's exact type. The
    restriction is not a v0 limitation: inference for polymorphic recursion is
    undecidable, so an annotation is required here as it is in Haskell (Report
    §4.4.1) and OCaml, while Standard ML declines the construct entirely. Mutual
    recursion is included: a group member with a complete signature is visible to
    its peers already quantified. See `docs/binding-group-inference-v0.md` §8 for
    the constraint-free condition, which is a dictionary-forwarding requirement
    rather than a typing one.

> **Enforcement of the effect rules.** Rules 8, 9, 10 and 11 are all **enforced** as of
> 2026-08-16. `fn shout(s: String) -> Unit = print(s)` is a compile error; an effect
> annotation is a checked contract. This replaces a note that stood for the whole of v0
> saying the opposite.
>
> > **A correction stood here from 2026-09-07 to 2026-09-10 saying that declared purity was
> > not enforced once a function became a value.** That was true and is no longer:
> >
> > ```sprout
> > fn pure_map(xs: List Int) -> List Int = list_map(shout, xs)   # now a compile error
> > ```
> >
> > Rule 8's arrow-position paragraph is what rejects it, and a function value entering a
> > slot — argument, return, record field, element — is checked in every one of those
> > positions. **Closed 2026-09-10** (`docs/effect-subsumption-v0.md` part 1).
> >
> > One boundary of that correction remains, verified by running: **a call through an
> > effect-variable parameter under a pure declaration.**
> > `fn pure_apply(g: Int -> Int !{e}, n: Int) -> Int = g(n)` is accepted, and passing an
> > `!{IO}` function runs the IO. The same exemption reached directly — a declared `!{e}`
> > whose body performs concrete IO, `fn sneak(n: Int) -> Int !{e} = shout(n)` — is
> > accepted too. Both are rule 8's standing **variable exemption**, not an arrow-position
> > gap: a signature's own effect variables are not rigid in its body, and effects have no
> > rigidity mechanism at all (the type-variable analogue, `unifier.fresh_skolem`, does
> > exist). `docs/effect-subsumption-v0.md` §8 costs both.
> >
> > **Closed 2026-10-26:** a top-level `let` initializer is checked for purity (§5.2).
> > `let seeded = shout(41)` is a compile error, and `--phase effects` enumerates it.
> >
> > **Closed 2026-09-09:** an instance method could declare an effect its class signature
> > does not, so callers dispatching through the class inherited the class's weaker claim.
> > Rule 8 now compares the two declarations. This one needed no effect variable and no
> > higher-order type — it was plain typeclass code
> > (`docs/effect-subsumption-v0.md` §6.1a).
> >
> > Adjacent rather than a boundary, and **closed 2026-09-09**: an unrecognised effect
> > label used to parse as an effect *variable*, so `!{NOPE}` type-checked and laundered
> > through the variable exemption. Rule 9 now rejects it where the annotation is read, in
> > every position one may be written.
> >
> > A further boundary escaped when this correction was first written (2026-09-07): a
> > zero-arg call on a local callee, `let t = io_thunk in t()`. The nullary type collapse
> > fix closed it — a zero-parameter function now has a `() -> T` arrow that carries its
> > effect — and the probe is rejected under rule 8 (`docs/nullary-type-collapse-v0.md`).
> >
> > All four parts of `docs/effect-subsumption-v0.md` have landed. Per-constructor variance
> > (§6.4) has not, and no program reaching it is known.
> >
> > **State the guarantee positively now, but not further than it goes.** A declaration is
> > checked against the effects its own body's calls infer *and* against every function
> > value crossing a slot boundary in either direction, joins included. **One channel is
> > still unchecked: an effect arriving through an effect variable**, in the two shapes
> > named above.
> >
> > A **class dispatch** is no longer on that list as such: an instance may no longer
> > declare more effect than its class. It can still carry that one remaining channel,
> > since a class method's arrow parameter is an arrow like any other.
>
> Which check covers which rule:
>
> - **8** and **11** are one check. Rule 11 ("a pure function body may not call `!{IO}`
>   functions…") is rule 8 stated operationally — a body calling an `!{IO}` function infers
>   `!{IO}`, which a pure signature does not admit. Its escape clause is honoured: where the
>   body's effect resolves to a variable rather than to `!{IO}`, the declaration is accepted.
>   Rule 8's instance/class sentence is a **second, separate** check: two declared effects,
>   no body and no inference. It runs as a whole-program pass over the declaration list, so
>   that an instance declared above its class is checked like any other — §16's "declaration
>   order is not significant" applies to it. It is keyed by class and method, so two
>   modules' same-named classes are judged separately. A multi-label **row** is never reported as a
>   mismatch by it, because a row is outside the order it compares. On the *instance* side
>   the row is left to rule 9, which names the instance's own signature. A row in a *class
>   method* signature is rejected by a separate scan over the class declarations and
>   reported at the class, because nothing else reaches one — see rule 9 below.
> - **9** has three clauses and three checks, all read from the **declared signature** and
>   never from what the body infers. The *singleton* clause — a signature may name at most
>   one effect variable, and may not write a multi-label row. Its row half also runs over
>   **class method signatures**, from rule 8's scan above, since those record no effect
>   report; its two-variable half does not, and a class method naming two variables with no
>   instance is still accepted. The *stored-position* clause —
>   an effect variable may not sit in a record field, constructor payload or `wrap` body.
>   And the *well-formed-label* clause — a label that is not a concrete effect this version
>   defines is ill-formed rather than a variable. Unlike the other two it is decided where
>   the annotation is READ rather than from a declared signature, so it covers positions no
>   declaration walk reaches: a lambda parameter, and a class method signature, which
>   records no effect report at all. The signature-not-body distinction is normative for the
>   first two clauses, because the two questions
>   have different answers in both directions: a signature naming two variables whose body
>   never combines them infers no row and would pass an inference-side check, while two
>   fresh instantiations of a *single* variable — `fn f(n: Int) -> Unit !{e}` called twice —
>   do infer a row and would fail one, despite naming exactly one variable and being
>   conformant. Rule 9 constrains what the author wrote, so that is what is read.
> - **10** (`main` may not be effect-polymorphic) was already enforced, syntactically.
>
> Three properties of the check are normative, not implementation detail:
>
> 1. **The rule is subsumption, inferred ⊑ declared — not equality.** Over-declaring is
>    accepted: a pure body under an `!{IO}` signature is legal and is how a function
>    states that its result is not a function of its arguments alone. Only
>    *under*-declaration is rejected.
> 2. **An arrow's effect is compared, and the comparison is directional.** Where one side
>    is *expected* — a written slot — an `!{IO}` arrow is rejected and a pure one accepted,
>    the direction reversing under a parameter. Effect **variables** bind on either path,
>    carrying bounds that reject only where they contradict, so acceptance does not depend
>    on effect inference reaching a particular answer mid-way: **element order, argument
>    order and branch order do not decide it.** A construct that joins peers — `if`, a
>    `match` arm — introduces a fresh result type that none of the peers owns, so no peer
>    becomes the contract for the others by being written first.
>
>    This replaces "unification of an arrow's effect is total… rejection happens at the
>    declaration boundary and nowhere else", which stood until 2026-09-10 and was the
>    normative reason a function value could launder its effect into a pure slot.
> 3. **An unresolved effect variable is accepted.** `!{e}` is neither satisfied nor
>    violated until instantiation; where the checker does not know, it accepts. Every
>    imprecision in effect inference must therefore fail towards accepting a program, not
>    rejecting one.
>
>    This licenses nothing about which labels may be *written*. An effect label that is
>    not `IO` is ill-formed (rule 9) and is rejected where the annotation is read —
>    otherwise the acceptance above is an opt-out, since an unrecognised label would
>    parse as a variable and inherit it.
>
> Enforcement runs as a pass over the whole program rather than aborting at the first
> offending declaration, so a codebase adopting it sees every gap in one compile.
> `compile_driver --phase effects` prints the same declared-vs-inferred census **without**
> rejecting, and shares the checker's gap predicate, so the report is an exact preview of
> what will be rejected.
>
> Note this note earlier said effects were "carried on `TFunc`" and "never unified"; both
> were misleading. They were carried on `Scheme` with the arrows hardcoded pure, and the
> problem was never a missing call to `unify_effects` — that is the wrong operation at a
> declaration boundary, since it accepts both directions and the rule is one-directional.
> See `docs/effect-enforcement-v0.md`.

