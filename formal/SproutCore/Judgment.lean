import SproutCore.Check

/-!
# A declarative typing judgment for Sprout Core, stage 1 (docs/lean-model-v0.md §9 1b)

Written from the spec text, not from `Check.lean`. `Check.lean` is the algorithm; this file says
what a program must satisfy for the spec to call it well-typed, so that T2 (soundness: whatever
the checker accepts is derivable here) and the rejection proofs at the end mean something. Every
rule carries a `-- spec §…` tag, or `-- structural` when it only builds a list or a derivation's
empty case and no spec clause says more; `formal/spec-snapshot/` pins the text the tags cite.

Nothing here changes `Check.lean`, and the checker is not an input to the judgment: the only
definition taken from it is `mkArr`, the one arrow constructor. `JudgmentExamples.lean` runs the
checker on every program it proves about, to show the two agree.

## Design

* **Solved types.** A derivation never mentions a metavariable. `Ty.Solved` excludes `.mv`,
  `.named` and `.gen`; a written type variable is a rigid `.skolem` (rule 15); a scheme is
  `∀ n. t` with `.gen i`, `i < n`, and no skolem. Where the algorithm solves a variable, the
  judgment *chooses* a solved type, so an omitted annotation is "some type", not a unification
  variable. (Written type variables inside an annotation of a top-level `let` are chosen too:
  they are ordinary variables there, spec §5.2.)
* **One arrow form.** Arrows are n-ary with the effect on the innermost one, as in `Check.lean`.
  Every rule that forms an arrow uses `mkArr`, and every type a derivation may choose must satisfy
  `Ty.Solved`, which includes "no mergeable arrow" (a pure arrow with a non-empty parameter list
  whose result is an arrow with a non-empty parameter list is one longer arrow; an empty
  parameter list never merges, spec §7 rule 9). So a derivation contains no non-canonical arrow.
  I chose `mkArr` plus a predicate, not a `Canon` wrapper: `mkArr` is what the checker's `zonk`
  applies, and the predicate keeps guessed types honest.
* **Syntax-directed.** One rule per `Expr` constructor, so `cases` on a derivation is inversion.
  Effect subsumption appears in exactly two relations, the places spec §7 rule 8 puts it:
  `Flows` (a value flowing into a written slot; the direction reverses under a parameter) and
  `Join` (the two branches of an `if`, spec: "A peer join is covered too"). A function body's
  effect is compared with the declared one (rule 11) and nowhere else.
* **`Flows` reads an n-ary arrow as curried.** Spec §5.1: `fn add(x: Int, y: Int) -> Int` has type
  `Int -> Int -> Int`. So one rule compares the first curried level of two arrows (`Ty.uncons`),
  whatever their stored parameter counts. `() -> T` is not a curried level: it flows only into
  `() -> U` (spec §7 rule 9). Because levels, not stored arrows, are compared, the three arity
  cases of the checker's `flow` are one rule here.
* **`Join a b r`** holds when `r` is an upper bound of both branch types: both flow into `r`
  ("judged against a fresh result type that neither branch owns, so both are compared as values").
  `r` is chosen, not computed; the checker's `joinTy` computes the least one. Every use of `r` is
  a `Flows` out of it, so I expect a larger `r` never helps and the choice adds no accepts. That
  is not proved here. T2 needs only the other direction: `joinTy`'s result is such an `r`.
* **Contexts.** A binding is a `local` (a lambda parameter, a local `let`, a `do` `let`, a
  function parameter: monomorphic, no declared arity), a `val` (a top-level `let`: a scheme, no
  declared arity) or a `fn` (a top-level `fn`: a scheme and a declared arity). Spec §5.3:
  *applying a function-typed value is checked at run time*, so a call to a `local` or a `val`
  is typed as the rest of the arrow when it supplies fewer arguments (`Peel.rest`); a call to a
  named `fn` must supply exactly its declared arity (`ArityOk`).
* **Local `let` is monomorphic.** `letIn` and a `do` `let` bind a `local`, whose type is used
  as is (spec §5.6: "a local `let` is monomorphic"). The judgment covers the default rules
  (`rules := {}`) only.
* **Program level is existential** (spec §7 rule 16). A program is typed if *some* partition of
  its top-level `fn` and `let` declarations into groups, and *some* order of the groups, types
  each group with its members monomorphic, then generalises them. That is weaker than rule 16's
  *minimal* groups: nothing here says a group is minimal. I expect no extra accepts (mutual
  references force a cycle into one group, since a name must be bound before it is used, and a
  larger group is only more monomorphic); that is not proved. A member with a complete signature
  is visible to its group already quantified (polymorphic recursion); the others are monomorphic
  inside the group. A top-level `let` is pure and is a group of its own. `main` is typed last,
  in the final context, against §10.10's signature.
* **Not modelled**, because they steer the search (spec §7 rules 3 and 4): the two passes over
  a call's arguments, and fixing an argument lambda's parameter from the callee's slot. The
  judgment lets a lambda's parameter be any solved type from which the lambda flows into its slot.
  I expect that adds no accepts (a larger parameter type only makes the body's uses harder); not
  proved. `Ty.elab` rejects an `!{IO}` arrow whose WRITTEN result is an arrow, as `Check.elabTy`
  does: no surface syntax spells it. The refusal is on the written form, so a type variable that a
  grounding later solves to an arrow is fine (`let f : a -> a !{IO} = \(h: Int -> Int) -> h`), and
  `mkArr ps r' .io` with an arrow `r'` stays one unmerged arrow (`mkArr` merges only a pure one).
* **A signature's `!{IO}` on a function-typed result** (`fn f(x: Int) -> Int -> Int !{IO}`) goes on
  the innermost arrow, and the call itself is pure (spec §7 rule 9). `Check.FnInfo.build` rejects
  the form and the compiler accepts it; the judgment follows the spec (`RetTy.writtenInner`).
* **Outside the judgment**, as in the checker, and only removing accepts: the declaration-order
  rejects (a `fn` above a `let` it uses, a duplicate name, a second `main`, a `let` using itself).
  A judgment without them accepts a superset; T2 only needs the checker's accepts inside it.
-/

namespace SproutCore

/-! ### Helpers over types -/

mutual
/-- The written type variables of an annotation, in reading order, with repeats. -/
def Ty.vars : Ty → List String
  | .named s => [s]
  | .tup ts => Ty.varsList ts
  | .arr ps r _ => Ty.varsList ps ++ r.vars
  | _ => []
def Ty.varsList : List Ty → List String
  | [] => []
  | t :: ts => t.vars ++ Ty.varsList ts
end

def Ty.isArr : Ty → Bool
  | .arr .. => true
  | _ => false

/-- A pure arrow with parameters whose result is an arrow with parameters: `mkArr` merges these. -/
def Ty.mergeable (ps : List Ty) (r : Ty) (e : Eff) : Bool :=
  e == .pure && !ps.isEmpty && (match r with | .arr (_ :: _) _ _ => true | _ => false)

mutual
/-- Solved and canonical, with `gen i` allowed for `i < n`: no `.mv`, no `.named`, no mergeable
arrow. -/
def Ty.ok (n : Nat) : Ty → Bool
  | .named _ => false
  | .mv _ => false
  | .gen i => decide (i < n)
  | .skolem _ => true
  | .int => true
  | .bool => true
  | .str => true
  | .unit => true
  | .tup ts => Ty.okList n ts
  | .arr ps r e => Ty.okList n ps && r.ok n && !Ty.mergeable ps r e
def Ty.okList (n : Nat) : List Ty → Bool
  | [] => true
  | t :: ts => t.ok n && Ty.okList n ts
end

/-- A solved type: what a derivation may mention outside a scheme. -/
def Ty.Solved (t : Ty) : Prop := t.ok 0 = true

mutual
def Ty.noSk : Ty → Bool
  | .skolem _ => false
  | .tup ts => Ty.noSkList ts
  | .arr ps r _ => Ty.noSkList ps && r.noSk
  | _ => true
def Ty.noSkList : List Ty → Bool
  | [] => true
  | t :: ts => t.noSk && Ty.noSkList ts
end

mutual
/-- Read a written type with its variables mapped through `σ`. An arrow goes through `mkArr`; an
`!{IO}` arrow whose WRITTEN result is an arrow has no spelling and is not read (spec §7 rule 9,
as in `Check.elabTy`); the test is on the written `r`, not on `σ`'s reading of it. A type a
programmer cannot write (`.mv`, `.skolem`, `.gen`) is not read. -/
def Ty.elab (σ : String → Ty) : Ty → Option Ty
  | .named s => some (σ s)
  | .int => some .int
  | .bool => some .bool
  | .str => some .str
  | .unit => some .unit
  | .mv _ => none
  | .skolem _ => none
  | .gen _ => none
  | .tup ts => (Ty.elabList σ ts).map .tup
  | .arr ps r e =>
    match Ty.elabList σ ps, r.elab σ with
    | some ps', some r' => if r.isArr && e == .io then none else some (mkArr ps' r' e)
    | _, _ => none
def Ty.elabList (σ : String → Ty) : List Ty → Option (List Ty)
  | [] => some []
  | t :: ts =>
    match t.elab σ, Ty.elabList σ ts with
    | some t', some ts' => some (t' :: ts')
    | _, _ => none
end

mutual
/-- Instantiate `gen i` with `ts[i]`, re-forming every arrow through `mkArr`, so instantiating a
variable with an arrow merges exactly as the canonical form says. -/
def Ty.inst (ts : List Ty) : Ty → Ty
  | .gen i => ts.getD i (.gen i)
  | .tup us => .tup (Ty.instList ts us)
  | .arr ps r e => mkArr (Ty.instList ts ps) (r.inst ts) e
  | t => t
def Ty.instList (ts : List Ty) : List Ty → List Ty
  | [] => []
  | t :: us => t.inst ts :: Ty.instList ts us
end

/-- The first curried level of an arrow, `(parameter, effect of this level, rest)`. Only the last
level carries the arrow's effect (spec §7 rule 9: "an annotation attaches to the innermost
arrow"). `() -> T` has no curried level. -/
def Ty.uncons : Ty → Option (Ty × Eff × Ty)
  | .arr [] _ _ => none
  | .arr [p] r e => some (p, e, r)
  | .arr (p :: q :: ps) r e => some (p, .pure, .arr (q :: ps) r e)
  | _ => none

/-- Put `!{IO}` on the innermost arrow of a function type (spec §7 rule 9: "An annotation attaches
to the innermost arrow, and parentheses do not move it"). `none` when that arrow already carries an
effect: two annotations on one arrow are not a form the spec defines. -/
def Ty.attachIO : Ty → Option Ty
  | .arr ps r e =>
    if r.isArr then (r.attachIO).map fun r' => mkArr ps r' e
    else if e == .pure then some (.arr ps r .io) else none
  | _ => none

namespace Judgment

/-! ### Contexts -/

/-- `∀ n. t`: `t` mentions `.gen i`, `i < n`, and no skolem. -/
structure Scheme where
  n : Nat
  t : Ty

inductive Binding where
  /-- A lambda or function parameter, a local `let`: monomorphic, no declared arity. -/
  | local (t : Ty)
  /-- A top-level `let`: a scheme, no declared arity. -/
  | val (s : Scheme)
  /-- A top-level `fn`: a scheme and the number of parameters it was declared with. -/
  | fn (arity : Nat) (s : Scheme)

abbrev Ctx := List (String × Binding)

/-- The only prelude name stage 1 reaches: an extern `fn print_str(s: String) -> Unit !{IO}`
(docs/lean-model-v0.md §4), so a named `fn` of arity 1. -/
def Ctx.prelude : Ctx := [("print_str", .fn 1 ⟨0, .arr [.str] .unit .io⟩)]

/-- `s` instantiated at some solved types. -/
def Scheme.Inst (s : Scheme) (t : Ty) : Prop :=
  ∃ ts : List Ty, ts.length = s.n ∧ (∀ u ∈ ts, u.Solved) ∧ t = s.t.inst ts

def Binding.Inst : Binding → Ty → Prop
  | .local t, u => u = t
  | .val s, u => s.Inst u
  | .fn _ s, u => s.Inst u

/-- spec §7 rule 16: `s` generalises the solved type `t` over all of its skolems: `t` is `s` at
distinct skolems ("the declaration generalizes over whatever remains free in its own type"). The
context at a top-level group holds only closed schemes, so nothing stays free. -/
def Generalises (t : Ty) (s : Scheme) : Prop :=
  s.t.ok s.n = true ∧ s.t.noSk = true ∧
    ∃ ids : List Nat, ids.length = s.n ∧ ids.Nodup ∧ s.t.inst (ids.map Ty.skolem) = t

/-! ### Effect-aware comparison of types (spec §7 rule 8) -/

mutual
/-- spec §7 rule 8: `Flows a e`: a value of type `a` may stand in a slot of type `e`. Directional:
an `!{IO}` arrow is refused where a pure one is required, and the direction reverses under a
parameter. -/
inductive Flows : Ty → Ty → Prop
  -- spec §7 rule 1: a base type flows only into itself (`int`, `bool`, `str`, `unit`)
  | int : Flows .int .int
  | bool : Flows .bool .bool
  | str : Flows .str .str
  | unit : Flows .unit .unit
  /-- A written type variable is rigid: it flows only into itself (spec §7 rule 15). -/
  | skolem (n : Nat) : Flows (.skolem n) (.skolem n)
  /-- Tuples are structural with exact arity (spec §7 rule 12). -/
  | tup (as bs : List Ty) : FlowsAll as bs → Flows (.tup as) (.tup bs)
  /-- One curried level: the parameter reverses, the effect of the level is compared, the rest
  flows (spec §7 rule 8: "the direction reverses when descending into an arrow's parameter"). -/
  | arr (a e pa pe ra re : Ty) (ea ee : Eff) :
      a.uncons = some (pa, ea, ra) → e.uncons = some (pe, ee, re) →
      Flows pe pa → ea.le ee = true → Flows ra re → Flows a e
  /-- `() -> T` and `Unit -> T` are distinct and neither flows into the other (spec §7 rule 9). -/
  | nullary (ra re : Ty) (ea ee : Eff) :
      Flows ra re → ea.le ee = true → Flows (.arr [] ra ea) (.arr [] re ee)

inductive FlowsAll : List Ty → List Ty → Prop
  -- spec §7 rules 3, 12: pointwise, so the two lists have one length
  | nil : FlowsAll [] []
  | cons (a b : Ty) (as bs : List Ty) :
      Flows a b → FlowsAll as bs → FlowsAll (a :: as) (b :: bs)
end

/-- spec §7 rule 8: `Join a b r`: `r` is a result type neither branch owns, and both branches flow
into it ("A peer join is covered too"; "joining an `!{IO}` slot with a pure one leaves the
slot at the pure callback both branches accept" is the contravariant half of `Flows`). -/
inductive Join : Ty → Ty → Ty → Prop
  | mk (a b r : Ty) : Flows a r → Flows b r → Join a b r

/-! ### Calls (spec §5.3, §7 rule 9) -/

/-- spec §5.3: `Peel t n ss r e`: applying a value of type `t` to `n` arguments has slots `ss`,
result `r` and the call itself performs `e`. A saturating call performs the arrow's effect. Fewer
arguments than parameters, but at least one, is typed as the rest of the arrow with no effect,
because the count is checked at run time (spec §5.3). More arguments continue into the result. A
`() -> T` takes exactly zero (spec §7 rule 9: "calling a `() -> T` requires the empty argument
list"). Zero arguments to any other arrow is no call: spec §7 rule 9 makes `() -> T` the only type
taking zero arguments, so the run-time count check of §5.3 can never pass. The spec does not say
this is a compile error; the compiler rejects it ("requires arguments but was called with none")
and so do the checker and this rule (BACKLOG, "The spec does not say a zero-argument call of a
non-`()` arrow value is an error"). -/
inductive Peel : Ty → Nat → List Ty → Ty → Eff → Prop
  -- spec §5.3: a call saturates the arrow and performs its effect
  | exact (ps : List Ty) (r : Ty) (e : Eff) : Peel (.arr ps r e) ps.length ps r e
  -- spec §5.3: a value applied to fewer, but some, arguments is typed as the rest (checked at
  -- run time); spec §7 rule 9: no arrow but `() -> T` takes none
  | rest (ps : List Ty) (r : Ty) (e : Eff) (n : Nat) :
      0 < n → n < ps.length → Peel (.arr ps r e) n (ps.take n) (mkArr (ps.drop n) r e) .pure
  -- spec §5.3: more arguments continue into the result, whose effect joins in
  | over (ps : List Ty) (r : Ty) (e : Eff) (n : Nat) (s2 : List Ty) (r2 : Ty) (e2 : Eff) :
      ps ≠ [] → ps.length < n → Peel r (n - ps.length) s2 r2 e2 →
      Peel (.arr ps r e) n (ps ++ s2) r2 (e.join e2)

/-- spec §5.3: a named `fn` must be called with exactly its declared number of arguments
("Under-application of a known function … is a compile error"); a value has no declared arity. -/
def ArityOk (Γ : Ctx) (f : Expr) (n : Nat) : Prop :=
  ∀ x k s, f = .var x → Γ.lookup x = some (.fn k s) → k = n

/-- spec §5.3: the type of a lambda parameter. Unannotated: any solved type. Annotated: the
annotation, with no type variable in it ("A type variable in a lambda parameter annotation is
rejected in v0"). Spec §5.3 / §7 rule 4 also fix an argument lambda's parameter to the callee's
slot; the judgment does not (see the header). -/
def LamParam (ann : Option Ty) (pt : Ty) : Prop :=
  match ann with
  | none => pt.Solved
  | some a => a.vars = [] ∧ a.elab (fun _ => .unit) = some pt

/-! ### Expressions -/

mutual
/-- `HasType Γ e t ε`: under `Γ`, `e` has type `t` and evaluating it performs effect `ε`. -/
inductive HasType : Ctx → Expr → Ty → Eff → Prop
  /-- spec §7 rule 16: a generalised declaration "is usable at any type thereafter"; a local is
  used at its one type. Naming a function performs nothing. -/
  | var (Γ : Ctx) (x : String) (b : Binding) (t : Ty) :
      Γ.lookup x = some b → b.Inst t → HasType Γ (.var x) t .pure
  -- spec §7 rule 1: each literal has one type
  | int (Γ : Ctx) (n : Nat) : HasType Γ (.int n) .int .pure
  | bool (Γ : Ctx) (b : Bool) : HasType Γ (.bool b) .bool .pure
  | str (Γ : Ctx) (s : String) : HasType Γ (.str s) .str .pure
  | unit (Γ : Ctx) : HasType Γ .unit .unit .pure
  /-- spec §7 rule 4 and §5.3: a lambda has an arrow from its parameter to its body; it is a
  value, so evaluating it is pure and its arrow carries the body's effect. -/
  | lam (Γ : Ctx) (x : String) (ann : Option Ty) (body : Expr) (pt tb : Ty) (eb : Eff) :
      LamParam ann pt → HasType ((x, .local pt) :: Γ) body tb eb →
      HasType Γ (.lam x ann body) (mkArr [pt] tb eb) .pure
  /-- spec §5.3: a call saturates a known function; spec §7 rule 3: each argument flows into its
  slot (the checker's two passes only choose the order of the search). The call performs the
  callee's evaluation, the arrow's effect and the arguments' effects. -/
  | app (Γ : Ctx) (f : Expr) (args : List Expr) (tf : Ty) (ef : Eff) (ts ss : List Ty)
      (res : Ty) (ce εa : Eff) :
      HasType Γ f tf ef → ArityOk Γ f args.length → Peel tf args.length ss res ce →
      HasExprs Γ args ts εa → FlowsAll ts ss →
      HasType Γ (.app f args) res (ef.join (ce.join εa))
  /-- spec §5.2.1: the right-hand side of `let … in` is pure; spec §5.6: "a local `let` is
  monomorphic", so the body sees `x` at the one type `t`. -/
  | letIn (Γ : Ctx) (x : String) (rhs body : Expr) (t tb : Ty) (eb : Eff) :
      HasType Γ rhs t .pure → HasType ((x, .local t) :: Γ) body tb eb →
      HasType Γ (.letIn x rhs body) tb eb
  /-- spec §7 rule 2: the condition is `Bool`; spec §5.4: the branches have a common type neither
  owns; spec §7 rule 8: that join compares effects. -/
  | ite (Γ : Ctx) (c a b : Expr) (ec ea eb : Eff) (ta tb r : Ty) :
      HasType Γ c .bool ec → HasType Γ a ta ea → HasType Γ b tb eb →
      r.Solved → Join ta tb r → HasType Γ (.ite c a b) r (ec.join (ea.join eb))
  /-- spec §7 rule 12: tuples are structural, exact arity. -/
  | tup (Γ : Ctx) (es : List Expr) (ts : List Ty) (ε : Eff) :
      HasExprs Γ es ts ε → HasType Γ (.tup es) (.tup ts) ε
  /-- spec §5.3b: the operand of `!` is `Bool`, and so is the result. -/
  | notE (Γ : Ctx) (e : Expr) (ε : Eff) : HasType Γ e .bool ε → HasType Γ (.notE e) .bool ε
  /-- spec §5.2.1a: the steps run in order, each `let` scopes over the rest. -/
  | doB (Γ Γ' : Ctx) (steps : List Step) (final : Expr) (t : Ty) (ε₁ ε₂ : Eff) :
      HasSteps Γ steps Γ' ε₁ → HasType Γ' final t ε₂ →
      HasType Γ (.doB steps final) t (ε₁.join ε₂)

/-- Each expression of a list has the type at its position; the effect is the join. -/
inductive HasExprs : Ctx → List Expr → List Ty → Eff → Prop
  -- structural: no expressions, no effect
  | nil (Γ : Ctx) : HasExprs Γ [] [] .pure
  -- structural: one typing per expression; the effect is the join
  | cons (Γ : Ctx) (e : Expr) (es : List Expr) (t : Ty) (ts : List Ty) (ε₁ ε₂ : Eff) :
      HasType Γ e t ε₁ → HasExprs Γ es ts ε₂ →
      HasExprs Γ (e :: es) (t :: ts) (ε₁.join ε₂)

/-- The non-final steps of a `do` block, the context after them and their effect. -/
inductive HasSteps : Ctx → List Step → Ctx → Eff → Prop
  -- structural: no steps, no effect
  | nil (Γ : Ctx) : HasSteps Γ [] Γ .pure
  /-- spec §5.2.1a: a `do` `let` binds like `let … in`: pure right-hand side, monomorphic. -/
  | letS (Γ Γ' : Ctx) (x : String) (rhs : Expr) (rest : List Step) (t : Ty) (ε : Eff) :
      HasType Γ rhs t .pure → HasSteps ((x, .local t) :: Γ) rest Γ' ε →
      HasSteps Γ (.letS x rhs :: rest) Γ' ε
  /-- spec §5.8: a non-final `do` statement's value is dropped; only a linear one is refused, and
  stage 1 has none. It need not be `Unit`: the discard form of §5.9 drops a whole `Maybe` or
  `Result` and continues, and §6 rule 11 takes the block's value from its last statement. Its
  effect is not dropped. The checker accepts any type here. -/
  | exprS (Γ Γ' : Ctx) (e : Expr) (rest : List Step) (t : Ty) (ε₁ ε₂ : Eff) :
      HasType Γ e t ε₁ → HasSteps Γ rest Γ' ε₂ →
      HasSteps Γ (.exprS e :: rest) Γ' (ε₁.join ε₂)
end

/-! ### Declarations -/

/-- A top-level `fn`. -/
structure FnD where
  name : String
  params : List (String × Option Ty)
  sig : Option (Ty × Eff)
  body : Expr

def FnD.toDecl (f : FnD) : Decl := .fn f.name f.params f.sig f.body

/-- The written type variables of a signature, each once. -/
def FnD.vars (f : FnD) : List String :=
  ((f.params.flatMap fun (_, t) => match t with | some t => t.vars | none => []) ++
    (match f.sig with | some (t, _) => t.vars | none => [])).eraseDups

/-- spec §7 rule 16: every parameter and the return type are written ("a complete … signature").
-/
def FnD.complete (f : FnD) : Bool := f.sig.isSome && f.params.all (·.2.isSome)

/-- spec §7 rule 16: parameter types. A written one is read through `σ`; an omitted one is "one
type", here any solved type. -/
inductive ParamsTy (σ : String → Ty) : List (String × Option Ty) → List Ty → Prop
  | nil : ParamsTy σ [] []
  | written (x : String) (a p : Ty) (prs : List (String × Option Ty)) (ps : List Ty) :
      a.elab σ = some p → ParamsTy σ prs ps → ParamsTy σ ((x, some a) :: prs) (p :: ps)
  | omitted (x : String) (p : Ty) (prs : List (String × Option Ty)) (ps : List Ty) :
      p.Solved → ParamsTy σ prs ps → ParamsTy σ ((x, none) :: prs) (p :: ps)

/-- spec §5.1, §7 rule 9: the declared return type `r` and the effect `e` the function itself
performs when called with its declared parameters. Omitted: the function is pure and the return
type is "one type" (rule 16). Written `-> T !{IO}`: `!{IO}` is the function's own effect, unless
`T` is a function type; then spec §7 rule 9 puts it on `T`'s innermost arrow (`-> A -> B !{IO}` is
`A -> B !{IO}` returned by a pure call), and the call itself is pure. The two rules split on the
WRITTEN `T` (`a.isArr`), as `Ty.elab` and `Check.elabTy` do, not on `σ`'s reading of it. The
checker rejects that second form (`Check.FnInfo.build`) and the compiler accepts it (BACKLOG,
"The Lean checker rejects `fn f(x: Int) -> Int -> Int !{IO}`"); the judgment follows the spec. -/
inductive RetTy (σ : String → Ty) : Option (Ty × Eff) → Ty → Eff → Prop
  | written (a r : Ty) (e : Eff) :
      a.elab σ = some r → ¬ (a.isArr = true ∧ e = .io) → RetTy σ (some (a, e)) r e
  | writtenInner (a r0 r : Ty) :
      a.isArr = true → a.elab σ = some r0 → r0.attachIO = some r →
      RetTy σ (some (a, .io)) r .pure
  | omitted (r : Ty) : r.Solved → RetTy σ none r .pure

/-- Parameters in scope in a body. Later parameters shadow earlier ones, as in `Check.checkBody`. -/
def bindParams (Γ : Ctx) (ps : List (String × Ty)) : Ctx :=
  ps.foldl (fun Γ (x, t) => (x, Binding.local t) :: Γ) Γ

/-- What is chosen for one member of a `fn` group. -/
structure FnWit where
  f : FnD
  /-- Written variable ↦ its skolem. -/
  σ : String → Ty
  /-- The member's type inside the group (monomorphic). -/
  τ : Ty
  /-- Its generalisation. -/
  s : Scheme

def FnWit.entryIn (w : FnWit) : String × Binding :=
  (w.f.name, .fn w.f.params.length (if w.f.complete then w.s else ⟨0, w.τ⟩))

def FnWit.entryOut (w : FnWit) : String × Binding := (w.f.name, .fn w.f.params.length w.s)

/-- A member's body, in the group's context `Δ`. spec §7 rule 15: the written variables are
skolems, so the body leaves them abstract; rule 11 and rule 8: the body's effect is at most the
declared one, and its type flows into the declared return type; rule 16: `τ` is the arrow the
parameters and the return type make. -/
def MemberOk (Δ : Ctx) (w : FnWit) : Prop :=
  ∃ (ps : List Ty) (r : Ty) (e : Eff) (tb : Ty) (eb : Eff),
    ParamsTy w.σ w.f.params ps ∧ RetTy w.σ w.f.sig r e ∧ w.τ = mkArr ps r e ∧
    HasType (bindParams Δ ((w.f.params.map (·.1)).zip ps)) w.f.body tb eb ∧
    Flows tb r ∧ eb.le e = true

/-- spec §5.2: the annotation of a top-level `let`. Free lowercase names are ordinary type
variables, so they are chosen; the initialiser's type must flow into the written type. The binding
keeps the initialiser's type (the compiler agrees: `let f : Int -> Int !{IO} = \x -> x` stays
pure). -/
def LetAnn (ann : Option Ty) (t : Ty) : Prop :=
  match ann with
  | none => True
  | some a =>
    ∃ (σ : String → Ty) (a' : Ty),
      (∀ s, (σ s).Solved) ∧ a.elab σ = some a' ∧ Flows t a'

/-- One binding group. A top-level `let` is a group of its own. -/
inductive GroupTy : Ctx → List Decl → Ctx → Prop
  /-- spec §5.2: a top-level `let` initialiser is pure; spec §7 rule 16: the binding generalises.
  -/
  | letG (Γ : Ctx) (name : String) (ann : Option Ty) (init : Expr) (t : Ty) (s : Scheme) :
      HasType Γ init t .pure → LetAnn ann t → Generalises t s →
      GroupTy Γ [.letD name ann init] ((name, .val s) :: Γ)
  /-- spec §7 rule 16: a group is typed monomorphically (a complete signature is visible to its
  group already quantified), then each member is generalised. Spec §7 rule 15: distinct written
  variables, in one member or across members, are distinct skolems. -/
  | fnG (Γ : Ctx) (ws : List FnWit) :
      ws ≠ [] →
      (∀ w ∈ ws, ∀ v ∈ w.f.vars, ∃ n, w.σ v = .skolem n) →
      (ws.flatMap fun w => w.f.vars.map w.σ).Nodup →
      (∀ w ∈ ws, Generalises w.τ w.s) →
      (∀ w ∈ ws, MemberOk (ws.map FnWit.entryIn ++ Γ) w) →
      GroupTy Γ (ws.map fun w => w.f.toDecl) (ws.map FnWit.entryOut ++ Γ)

/-- spec §7 rule 16: some order of some groups covering the declarations. -/
inductive Groups : Ctx → List Decl → Ctx → Prop
  -- structural: no declarations left
  | nil (Γ : Ctx) : Groups Γ [] Γ
  | step (Γ Γ₁ Γ₂ : Ctx) (ds g rest : List Decl) :
      ds.Perm (g ++ rest) → GroupTy Γ g Γ₁ → Groups Γ₁ rest Γ₂ → Groups Γ ds Γ₂

/-- spec §10.10: `main` is `fn main() -> Unit !{IO}` or `-> Int !{IO}`, written out, never
effect-polymorphic; its body is typed against that. -/
def MainOk (Γ : Ctx) (sg : Option (Ty × Eff)) (body : Expr) : Prop :=
  ∃ r, sg = some (r, .io) ∧ (r = .unit ∨ r = .int) ∧
    ∃ (tb : Ty) (eb : Eff), HasType Γ body tb eb ∧ Flows tb r

end Judgment

open Judgment

/-- The `fn` and `let` declarations, in source order. -/
def Program.nodes (p : Program) : List Decl :=
  p.decls.filter fun d => match d with | .mainFn .. => false | _ => true

/-- The program is well-typed. -/
def Program.Typed (p : Program) : Prop :=
  ∃ Γ, Groups Ctx.prelude p.nodes Γ ∧
    ∀ sg body, Decl.mainFn sg body ∈ p.decls → MainOk Γ sg body

end SproutCore
