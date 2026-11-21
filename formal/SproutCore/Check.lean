import SproutCore.Syntax

/-!
# The Sprout Core checker (stage 1)

Implements the spec's rules, not textbook HM. Every rule carries a tag
`-- spec §<heading>: <keyword>`; `formal/spec-snapshot/` pins the spec text those tags cite
(docs/lean-model-v0.md §7).

Scope of the effect model (stage 1, no effect variables): an arrow's effect is `pure` or `IO`.
A type variable solved to an arrow keeps that arrow's effect, so the checker is faithful where no
variable is solved to arrows of differing effects. The generator aims to stay out of that region
(`Gen.lean`), and the oracle reports a program that leaves it as a disagreement; stage 4 replaces
the model with effect variables and bounds.
-/

namespace SproutCore

/-- Rule switches. All on is the model; the oracle self-test plants a model with one off. -/
structure Rules where
  /-- spec §5.6 existentials: "a local `let` is monomorphic". -/
  localLetMono : Bool := true
  deriving Repr

structure CState where
  subst : Array (Option Ty) := #[]
  nextSk : Nat := 0

/-- A checker failure. `fuel` is not a verdict: it means a run was cut off, and every consumer
reports it as a harness error. `expectBool` is the only handler and it re-throws `fuel`. -/
inductive CErr where
  /-- A rejection; `key` is what the oracle's allowlist names. -/
  | reject (key detail : String)
  | fuel
  deriving Repr, Inhabited

/-- The message of a rejection, `<key>: <detail>`. -/
def CErr.render : CErr → String
  | .reject key detail => s!"{key}: {detail}"
  | .fuel => "fuel exhausted"

/-- The rejection key, or `none` for running out of fuel. -/
def CErr.key? : CErr → Option String
  | .reject key _ => some key
  | .fuel => none

/-- The depth budget of one `shallow`/`zonk`/`flow`/`joinTy`/`peel` call. It is read from the
reader, not kept in `CState`, which `try` rolls back; each such call starts afresh from it. -/
def defaultFuel : Nat := 10000

/-- The budget is the reader; the state rolls back under `try`, the error is `CErr`. -/
abbrev M := ReaderT Nat (StateT CState (Except CErr))

/-- Reject with `<key>: <detail>`; the key is what the oracle's allowlist names. -/
def reject {α : Type} (key detail : String) : M α := throw (CErr.reject key detail)

def fresh : M Ty := do
  let s ← get
  set { s with subst := s.subst.push none }
  return .mv s.subst.size

def freshSkolem : M Ty := do
  let s ← get
  set { s with nextSk := s.nextSk + 1 }
  return .skolem s.nextSk

/-- A function type. The effect sits on the innermost arrow, so a pure arrow whose result is an
arrow is one longer arrow (spec §7 rule 9: "an annotation attaches to the innermost arrow").
An empty parameter list is an arrow of its own, never merged (spec §7 rule 9: "`fn f() -> T` has
type `() -> T`", and `() -> T` is not `Unit -> T`). -/
def mkArr (ps : List Ty) (r : Ty) (e : Eff) : Ty :=
  match r, e with
  | .arr ps' r' e', .pure => if ps.isEmpty || ps'.isEmpty then .arr ps r e else .arr (ps ++ ps') r' e'
  | _, _ => .arr ps r e

/-- Follow metavariable bindings. Each step spends one unit of fuel; `0` throws `.fuel`. -/
def shallowF : Nat → Ty → M Ty
  | 0, _ => throw .fuel
  | k + 1, .mv n => do
    match (← get).subst[n]? with
    | some (some t) => shallowF k t
    | _ => pure (.mv n)
  | _ + 1, t => pure t

def shallow (t : Ty) : M Ty := do shallowF (← read) t

/-- Substitute under the type. Fuel bounds the depth, binding chains included. -/
def zonkF : Nat → Ty → M Ty
  | 0, _ => throw .fuel
  | k + 1, t => do
    match ← shallowF k t with
    | .tup ts => return .tup (← ts.mapM (zonkF k))
    | .arr ps r e => return mkArr (← ps.mapM (zonkF k)) (← zonkF k r) e
    | t => return t

def zonk (t : Ty) : M Ty := do zonkF (← read) t

mutual
def metasOf : Ty → List Nat
  | .mv n => [n]
  | .tup ts => metasOfList ts
  | .arr ps r _ => metasOfList ps ++ metasOf r
  | _ => []

def metasOfList : List Ty → List Nat
  | [] => []
  | t :: ts => metasOf t ++ metasOfList ts
end

def occursIn (n : Nat) (t : Ty) : Bool := (metasOf t).contains n

def bindMeta (n : Nat) (t : Ty) : M Unit := do
  let t ← zonk t
  if occursIn n t then reject "occurs" "infinite type"
  modify fun s => { s with subst := s.subst.set! n (some t) }

/-- `flow actual expected`: shape unification plus the directional effect comparison of
spec §7 rule 8. The direction reverses under an arrow's parameter. -/
def flowF : Nat → Ty → Ty → M Unit
  | 0, _, _ => throw .fuel
  | k + 1, a, e => do
  let a ← shallowF k a
  let e ← shallowF k e
  match a, e with
  | .mv n, .mv m => if n == m then pure () else bindMeta n (.mv m)
  | .mv n, t => bindMeta n t
  | t, .mv m => bindMeta m t
  | .int, .int => pure ()
  | .bool, .bool => pure ()
  | .str, .str => pure ()
  | .unit, .unit => pure ()
  | .skolem n, .skolem m =>
    -- spec §7 rule 15: a written type variable is rigid
    if n == m then pure () else reject "rigid" "two written type variables merged"
  | .tup as, .tup es =>
    if as.length == es.length then
      for (x, y) in as.zip es do flowF k x y
    else reject "mismatch" "tuple arity"
  | .arr aps ar ae, .arr eps er ee =>
    -- spec §7 rule 9: `() -> T` and `Unit -> T` are distinct, and neither is assignable to the other
    if aps.isEmpty != eps.isEmpty then reject "mismatch" "arrow arity" else
    let m := min aps.length eps.length
    for (ap, ep) in (aps.take m).zip (eps.take m) do flowF k ep ap
    if aps.length == eps.length then
      flowF k ar er
      -- spec §7 rule 8: a function VALUE may not enter a slot that permits less effect
      unless ae.le ee do reject "effect-slot" "IO function where a pure one is required"
    else if aps.length > eps.length then
      -- the expected arrow's result is itself the rest of the actual arrow; the effect sits on
      -- the innermost arrow, so the outer one is pure and this inner `flow` compares the effect
      flowF k (.arr (aps.drop m) ar ae) er
    else
      unless ae.le .pure do reject "effect-slot" "IO function where a pure one is required"
      flowF k ar (.arr (eps.drop m) er ee)
  | _, _ => reject "mismatch" "type mismatch"

def flow (a e : Ty) : M Unit := do flowF (← read) a e

/-- Peer join, with the polarity that reverses under a parameter: the least upper bound at
positive depth, the greatest lower bound at negative depth (spec §7 rule 8, "A peer join is
covered too"). -/
def joinTyF : Nat → Bool → Ty → Ty → M Ty
  | 0, _, _, _ => throw .fuel
  | k + 1, pos, a, b => do
  let a ← shallowF k a
  let b ← shallowF k b
  match a, b with
  | .mv n, .mv m => if n == m then pure a else do bindMeta n b; pure b
  | .mv n, t => do bindMeta n t; pure t
  | t, .mv n => do bindMeta n t; pure t
  | .int, .int => pure a
  | .bool, .bool => pure a
  | .str, .str => pure a
  | .unit, .unit => pure a
  | .skolem n, .skolem m =>
    if n == m then pure a else reject "rigid" "two written type variables merged"
  | .tup as, .tup bs =>
    if as.length == bs.length then
      return .tup (← (as.zip bs).mapM fun (x, y) => joinTyF k pos x y)
    else reject "mismatch" "tuple arity"
  | .arr aps ar ae, .arr bps br be =>
    if aps.length == bps.length then
      let ps ← (aps.zip bps).mapM fun (x, y) => joinTyF k (!pos) x y
      let r ← joinTyF k pos ar br
      return mkArr ps r (if pos then ae.join be else ae.meet be)
    else if aps.isEmpty || bps.isEmpty then
      -- spec §7 rule 9: `() -> T` is not `Unit -> T`
      reject "mismatch" "arrow arity"
    else
      -- the stored arities differ when a result variable was later solved to an arrow: split the
      -- longer arrow, as `flow` does. The shorter one's result joins the longer one's rest, which
      -- is an arrow whose outer effect is pure.
      let m := min aps.length bps.length
      let ps ← ((aps.take m).zip (bps.take m)).mapM fun (x, y) => joinTyF k (!pos) x y
      if aps.length > bps.length then
        let r ← joinTyF k pos (.arr (aps.drop m) ar ae) br
        return mkArr ps r (if pos then be else .pure)
      else
        let r ← joinTyF k pos ar (.arr (bps.drop m) br be)
        return mkArr ps r (if pos then ae else .pure)
  | _, _ => reject "mismatch" "branch types differ"

def joinTy (pos : Bool) (a b : Ty) : M Ty := do joinTyF (← read) pos a b

/-! ### Environments and schemes -/

inductive Bind where
  /-- A local, or a group member's own type inside its group (monomorphic). -/
  | mono (t : Ty)
  /-- A generalised top-level binding: `gen 0 .. n-1` are its quantified variables. -/
  | poly (n : Nat) (t : Ty)
  /-- A top-level `fn` inside its own group, monomorphic; `arity` is its parameter count. -/
  | fnMono (arity : Nat) (t : Ty)
  | fnPoly (arity : Nat) (n : Nat) (t : Ty)

abbrev Env := List (String × Bind)

mutual
def instTy (ms : Array Ty) : Ty → Ty
  | .gen i => ms[i]!
  | .tup ts => .tup (instTyList ms ts)
  | .arr ps r e => .arr (instTyList ms ps) (instTy ms r) e
  | t => t

def instTyList (ms : Array Ty) : List Ty → List Ty
  | [] => []
  | t :: ts => instTy ms t :: instTyList ms ts
end

def instantiate (n : Nat) (t : Ty) : M Ty := do
  let mut ms : Array Ty := #[]
  for _ in [0:n] do ms := ms.push (← fresh)
  return instTy ms t

/-- The `gen` index of `t` in `tab`, adding it when new. -/
def genSlot (tab : Array Ty) (t : Ty) : Array Ty × Ty :=
  match tab.findIdx? (· == t) with
  | some i => (tab, .gen i)
  | none => (tab.push t, .gen tab.size)

mutual
/-- Replace the metas not in `keep` and every skolem by `gen` variables. -/
def genGo (keep : List Nat) (tab : Array Ty) : Ty → Array Ty × Ty
  | t@(.mv n) => if keep.contains n then (tab, t) else genSlot tab t
  | t@(.skolem _) => genSlot tab t
  | .tup ts =>
    let (tab, ts') := genGoList keep tab ts
    (tab, .tup ts')
  | .arr ps r e =>
    let (tab, ps') := genGoList keep tab ps
    let (tab, r') := genGo keep tab r
    (tab, .arr ps' r' e)
  | t => (tab, t)

def genGoList (keep : List Nat) (tab : Array Ty) : List Ty → Array Ty × List Ty
  | [] => (tab, [])
  | t :: ts =>
    let (tab, t') := genGo keep tab t
    let (tab, ts') := genGoList keep tab ts
    (tab, t' :: ts')
end

def generalize (keep : List Nat) (t : Ty) : M (Nat × Ty) := do
  let t ← zonk t
  let (tab, t') := genGo keep #[] t
  return (tab.size, t')

def envMetas (env : Env) : M (List Nat) := do
  let mut acc := []
  for (_, b) in env do
    match b with
    | .mono t | .fnMono _ t => acc := acc ++ metasOf (← zonk t)
    | _ => pure ()
  return acc

/-! ### Written types -/

mutual
def namedIn : Ty → List String
  | .named s => [s]
  | .tup ts => namedInList ts
  | .arr ps r _ => namedInList ps ++ namedIn r
  | _ => []

def namedInList : List Ty → List String
  | [] => []
  | t :: ts => namedIn t ++ namedInList ts
end

def hasNamed (t : Ty) : Bool := !(namedIn t).isEmpty

mutual
/-- Read a written type, mapping each type variable through `f`, and normalise arrows. -/
def elabTy (f : String → Ty) : Ty → M Ty
  | .named s => pure (f s)
  | .tup ts => return .tup (← elabTyList f ts)
  | .arr ps r e => do
    let r' ← elabTy f r
    -- spec §7 rule 9: the annotation attaches to the innermost arrow; an `!{IO}` outer arrow
    -- with an arrow result has no spelling.
    match r', e with
    | .arr .., .io => reject "ill-formed" "effect on an arrow whose result is an arrow"
    | _, _ => pure (mkArr (← elabTyList f ps) r' e)
  | t => pure t

def elabTyList (f : String → Ty) : List Ty → M (List Ty)
  | [] => pure []
  | t :: ts => do
    let t' ← elabTy f t
    return t' :: (← elabTyList f ts)
end

/-! ### Free variables -/

/-- The names a `do` block's steps bind, added to `bound` (the scope of its final expression). -/
def stepBound : List String → List Step → List String
  | bound, [] => bound
  | bound, .letS x _ :: rest => stepBound (x :: bound) rest
  | bound, .exprS _ :: rest => stepBound bound rest

mutual
def fvStep (bound : List String) : List Step → List String
  | [] => []
  | .letS x rhs :: rest => fv bound rhs ++ fvStep (x :: bound) rest
  | .exprS e :: rest => fv bound e ++ fvStep bound rest

def fvList (bound : List String) : List Expr → List String
  | [] => []
  | e :: es => fv bound e ++ fvList bound es

def fv (bound : List String) : Expr → List String
  | .var x => if bound.contains x then [] else [x]
  | .lam x _ b => fv (x :: bound) b
  | .app f as => fv bound f ++ fvList bound as
  | .letIn x r b => fv bound r ++ fv (x :: bound) b
  | .ite c t e => fv bound c ++ fv bound t ++ fv bound e
  | .tup es => fvList bound es
  | .notE e => fv bound e
  | .doB steps final => fvStep bound steps ++ fv (stepBound bound steps) final
  | _ => []
end

/-! ### Expressions -/

def isLam : Expr → Bool
  | .lam .. => true
  | _ => false

/-- The prelude name the stage-1 fragment can reach. -/
def preludeEnv : Env := [("print_str", .poly 0 (.arr [.str] .unit .io))]

/-- Bind a local `let`. -/
def bindLocal (rules : Rules) (env : Env) (x : String) (t : Ty) : M Env := do
  if rules.localLetMono then
    -- spec §5.6 existentials: "a local `let` is monomorphic"
    return (x, .mono t) :: env
  else
    let keep ← envMetas env
    let (n, t') ← generalize keep t
    return (x, .poly n t') :: env

/-- `t` flows into `Bool`, or reject with `key`. -/
def expectBool (t : Ty) (key detail : String) : M Unit := do
  let ok ← try flow t .bool; pure true catch
    | .reject .. => pure false
    | .fuel => throw .fuel
  unless ok do reject key detail

/-- The slots of a call, its result type and the effect the call itself performs. -/
def peelF : Nat → Ty → Nat → M (List Ty × Ty × Eff)
  | 0, _, _ => throw .fuel
  | k + 1, t, n => do
  match ← shallowF k t with
  | .arr ps r e =>
    if n == ps.length then pure (ps, r, e)
    -- spec §7 rule 9: calling a `() -> T` requires the empty argument list
    else if ps.isEmpty then reject "mismatch" "call of a function taking no arguments with some"
    -- spec §5.3, §7 rule 9: no arrow but `() -> T` takes zero arguments, so the run-time count
    -- check of a value call can never pass (the compiler: "requires arguments but was called with
    -- none")
    else if n == 0 then reject "arity" "call of a function requiring arguments with none"
    else if n < ps.length then
      -- spec §5.3: applying a function-typed value is checked at run time; typed as the rest
      pure (ps.take n, mkArr (ps.drop n) r e, .pure)
    else
      let (s2, r2, e2) ← peelF k r (n - ps.length)
      pure (ps ++ s2, r2, e.join e2)
  | .mv m => do
    let mut ps := []
    for _ in [0:n] do ps := ps ++ [← fresh]
    let r ← fresh
    bindMeta m (.arr ps r .pure)
    pure (ps, r, .pure)
  | _ => reject "mismatch" "call of a non-function"

def peel (t : Ty) (n : Nat) : M (List Ty × Ty × Eff) := do peelF (← read) t n

/-- A call of a known `fn` passes exactly its parameter count (spec §5.3). -/
def arityCheck (env : Env) (f : Expr) (n : Nat) : M Unit :=
  match f with
  | .var x =>
    match env.lookup x with
    | some (.fnMono k _) | some (.fnPoly k _ _) =>
      if k != n then reject "arity" s!"{x} expects {k} arguments" else pure ()
    | _ => pure ()
  | _ => pure ()

/-- A lambda's parameter type: the annotation, else fresh. In argument position it is then tied
to the callee's slot. -/
def lamParam (slot : Option Ty) (ann : Option Ty) : M Ty := do
  let pt ← match ann with
    | some t =>
      -- spec §5.3: a type variable in a lambda parameter annotation is rejected in v0
      if hasNamed t then reject "lam-ann-var" "type variable in a lambda parameter annotation"
      elabTy (fun _ => .unit) t
    | none => fresh
  -- spec §7 rule 4: in argument position the parameter type comes from the callee's slot
  if let some s := slot then
    match ← shallow s with
    | .arr (p :: _) _ _ => flow p pt
    | _ => pure ()
  pure pt

mutual
def infer (rules : Rules) (env : Env) : Expr → M (Ty × Eff)
  | .var x =>
    match env.lookup x with
    | none => reject "unknown-var" s!"unknown variable {x}"
    | some (.mono t) => pure (t, .pure)
    | some (.fnMono _ t) => pure (t, .pure)
    | some (.poly n t) => return (← instantiate n t, .pure)
    | some (.fnPoly _ n t) => return (← instantiate n t, .pure)
  | .int _ => pure (.int, .pure)
  | .bool _ => pure (.bool, .pure)
  | .str _ => pure (.str, .pure)
  | .unit => pure (.unit, .pure)
  | .lam x ann body => do
    let pt ← lamParam none ann
    let (tb, eb) ← infer rules ((x, .mono pt) :: env) body
    -- a lambda is a value: it is pure, and its arrow carries the body's effect
    pure (mkArr [pt] tb eb, .pure)
  | .app f args => do
    -- spec §5.3: a call saturates a known function; under- and over-application is an error
    arityCheck env f args.length
    let (tf, ef) ← infer rules env f
    let (slots, res, callEff) ← peel tf args.length
    -- spec §7 rule 3: non-lambda arguments are unified against their slots first
    let eff ← inferPlainArgs rules env args slots (ef.join callEff)
    -- spec §7 rule 3: then each lambda is inferred with its parameter type from the slot
    let eff ← inferLamArgs rules env args slots eff
    pure (res, eff)
  | .letIn x rhs body => do
    let (t, e) ← infer rules env rhs
    -- spec §5.2.1: the right-hand side of a `let … in` must be pure
    unless e == .pure do reject "let-rhs-io" "effectful right-hand side"
    let env' ← bindLocal rules env x t
    infer rules env' body
  | .ite c t e => do
    -- spec §7 rule 2: `if` condition must be Bool
    let (tc, ec) ← infer rules env c
    expectBool tc "if-cond" "condition is not Bool"
    let (tt, et) ← infer rules env t
    let (te, ee) ← infer rules env e
    -- spec §5.4: both branches have a common type neither owns; spec §7 rule 8: peer join
    let ty ← joinTy true tt te
    pure (ty, ec.join (et.join ee))
  | .tup es => do
    -- spec §7 rule 12: tuples are structural, exact arity
    let (ts, eff) ← inferTuple rules env es
    pure (.tup ts, eff)
  | .notE e => do
    -- spec §5.3b: the operand of `!` must be Bool
    let (t, eff) ← infer rules env e
    expectBool t "not-bool" "operand of ! is not Bool"
    pure (.bool, eff)
  | .doB steps final => do
    let (env', eff) ← inferSteps rules env steps
    let (t, ef) ← infer rules env' final
    pure (t, eff.join ef)

/-- The non-lambda arguments against their slots, in order, joining their effects into `eff`. -/
def inferPlainArgs (rules : Rules) (env : Env) : List Expr → List Ty → Eff → M Eff
  | a :: as, s :: ss, eff => do
    if isLam a then inferPlainArgs rules env as ss eff else
    let (ta, ea) ← infer rules env a
    flow ta s
    inferPlainArgs rules env as ss (eff.join ea)
  | _, _, eff => pure eff

/-- The lambda arguments, in order, each inferred with its slot. -/
def inferLamArgs (rules : Rules) (env : Env) : List Expr → List Ty → Eff → M Eff
  | .lam x ann body :: as, s :: ss, eff => do
    let pt ← lamParam (some s) ann
    let (tb, eb) ← infer rules ((x, .mono pt) :: env) body
    flow (mkArr [pt] tb eb) s
    inferLamArgs rules env as ss eff
  | _ :: as, _ :: ss, eff => inferLamArgs rules env as ss eff
  | _, _, eff => pure eff

/-- The element types of a tuple, left to right, and their joined effect. -/
def inferTuple (rules : Rules) (env : Env) : List Expr → M (List Ty × Eff)
  | [] => pure ([], .pure)
  | e :: es => do
    let (t, ef) ← infer rules env e
    let (ts, eff) ← inferTuple rules env es
    pure (t :: ts, ef.join eff)

/-- The steps of a `do` block: the environment for its final expression, and the effect of its
bare expressions. -/
def inferSteps (rules : Rules) (env : Env) : List Step → M (Env × Eff)
  | [] => pure (env, .pure)
  | .letS x rhs :: rest => do
    let (t, e) ← infer rules env rhs
    -- spec §5.2.1a: a `do` let binds like `let … in`; its right-hand side must be pure
    unless e == .pure do reject "let-rhs-io" "effectful right-hand side"
    let env' ← bindLocal rules env x t
    inferSteps rules env' rest
  | .exprS e :: rest => do
    let (_, ef) ← infer rules env e
    let (env', eff) ← inferSteps rules env rest
    pure (env', ef.join eff)
end

/-! ### Declarations -/

structure FnInfo where
  name : String
  params : List (String × Option Ty)
  sig : Option (Ty × Eff)
  body : Expr
  deriving Inhabited

def FnInfo.complete (f : FnInfo) : Bool :=
  f.sig.isSome && f.params.all (·.2.isSome)

def FnInfo.writtenVars (f : FnInfo) : List String :=
  let vs := (f.params.flatMap fun (_, t) => match t with | some t => namedIn t | none => []) ++
    (match f.sig with | some (t, _) => namedIn t | none => [])
  vs.eraseDups

/-- The member's type under `f`: written variables map through `m`; omitted types are fresh. -/
def FnInfo.build (f : FnInfo) (m : String → Ty) : M (List Ty × Ty × Eff × Ty) := do
  let mut ps := []
  for (_, t) in f.params do
    match t with
    | some t => ps := ps ++ [← elabTy m t]
    | none => ps := ps ++ [← fresh]
  let (r, e) ← match f.sig with
    | some (t, e) => do pure (← elabTy m t, e)
    | none => do pure (← fresh, Eff.pure)
  match r, e with
  | .arr .., .io => reject "ill-formed" "effect on a function returning a function"
  | _, _ => pure ()
  pure (ps, r, e, mkArr ps r e)

/-- A body is checked against the declared return type and effect. -/
def checkBody (rules : Rules) (env : Env) (params : List (String × Ty)) (ret : Ty) (eff : Eff)
    (body : Expr) : M Unit := do
  let env' := params.foldl (fun env (x, t) => (x, Bind.mono t) :: env) env
  let (tb, eb) ← infer rules env' body
  flow tb ret
  -- spec §7 rule 8, rule 11: a pure body may not call `!{IO}` functions
  unless eb.le eff do reject "io-in-pure" "performs IO but is declared pure"

/-- The scheme of a complete signature: written variables become `gen`. -/
def FnInfo.scheme (f : FnInfo) : M (Nat × Ty) := do
  let vs := f.writtenVars
  let m := fun s => match vs.findIdx? (· == s) with | some i => Ty.gen i | none => Ty.unit
  let (_, _, _, t) ← f.build m
  pure (vs.length, t)

def inferFnGroup (rules : Rules) (base : Env) (fns : List FnInfo) : M Env := do
  -- spec §7 rule 16: a group is typed monomorphically, then generalised at the boundary.
  -- Members with a complete signature are visible to peers already quantified (rule 16).
  let mut groupEnv : Env := []
  -- the scheme is `some` for a complete signature, computed once
  let mut built : List (FnInfo × List Ty × Ty × Eff × Ty × Option (Nat × Ty)) := []
  for f in fns do
    let vs := f.writtenVars
    let mut sks := []
    for v in vs do sks := sks ++ [(v, ← freshSkolem)]
    let m := fun s => (sks.lookup s).getD .unit
    let (ps, r, e, t) ← f.build m
    let sch ← if f.complete then some <$> f.scheme else pure none
    built := built ++ [(f, ps, r, e, t, sch)]
    match sch with
    | some (n, sc) => groupEnv := (f.name, .fnPoly f.params.length n sc) :: groupEnv
    | none => groupEnv := (f.name, .fnMono f.params.length t) :: groupEnv
  let env := groupEnv ++ base
  for (f, ps, r, e, _, _) in built do
    let params := (f.params.map (·.1)).zip ps
    checkBody rules env params r e f.body
  let mut out : Env := []
  for (f, _, _, _, t, sch) in built do
    match sch with
    | some (n, sc) => out := (f.name, .fnPoly f.params.length n sc) :: out
    | none =>
      let (n, t') ← generalize [] t
      out := (f.name, .fnPoly f.params.length n t') :: out
  return out ++ base

/-- A top-level `let`: pure initialiser, generalised. -/
def inferLet (rules : Rules) (base : Env) (name : String) (ann : Option Ty) (init : Expr) :
    M Env := do
  let (t, e) ← infer rules base init
  -- spec §5.2: top-level `let` initialisers must be pure
  unless e == .pure do reject "let-io" "top-level `let` performs IO"
  if let some a := ann then
    -- spec §5.2: the annotation is unified with the initialiser; its variables are ordinary
    let mut sks : List (String × Ty) := []
    for v in (namedIn a).eraseDups do
      sks := sks ++ [(v, ← fresh)]
    let a' ← elabTy (fun s => (sks.lookup s).getD .unit) a
    flow t a'
  let (n, t') ← generalize [] t
  return (name, .poly n t') :: base

inductive Node where
  | fnN (f : FnInfo)
  | letN (name : String) (ann : Option Ty) (init : Expr)
  deriving Inhabited

def Node.name : Node → String
  | .fnN f => f.name
  | .letN n _ _ => n

def Node.isLet : Node → Bool
  | .letN .. => true
  | .fnN _ => false

def Node.refs (names : List String) : Node → List String
  | .fnN f => (fv (f.params.map (·.1)) f.body).filter names.contains
  | .letN _ _ i => (fv [] i).filter names.contains

/-- Group order, dependencies first: repeatedly take the first group whose references are its own
members or already placed. The last argument bounds the recursion: a group goes in per step,
so `#groups + 1` never runs out with groups pending. -/
def orderGroups (edges : Array (List Nat)) (pending order : List (List Nat)) :
    Nat → Except CErr (List (List Nat))
  | 0 => if pending.isEmpty then pure order else throw (.reject "internal" "no group order")
  | b + 1 =>
    if pending.isEmpty then pure order else
    let ready := pending.filter fun g =>
      g.all fun i => (edges[i]?.getD []).all fun j => g.contains j || order.any (·.contains j)
    match ready with
    | [] => throw (.reject "internal" "no group order")
    | g :: _ => orderGroups edges (pending.filter (· != g)) (order ++ [g]) b

/-- What `checkPlan` hands to the type-checking run: the nodes, their reference edges, each `main`
with the number of nodes declared above it, and the group order. -/
structure Plan where
  nodes : Array Node
  edges : Array (List Nat)
  mains : List (Nat × Option (Ty × Eff) × Expr)
  order : List (List Nat)

/-- `reach[i][j]`; `false` when either index is out of range. -/
def reachAt (reach : Array (Array Bool)) (i j : Nat) : Bool :=
  (reach[i]?.bind (·[j]?)).getD false

/-- Set `reach[i][j]`; out of range, nothing changes. -/
def reachSet (reach : Array (Array Bool)) (i j : Nat) : Array (Array Bool) :=
  reach.set! i ((reach[i]?.getD #[]).set! j true)

/-- The part of the checker that spends no fuel: duplicate and declaration-order rules, the
reference graph and the binding groups in dependency order. Every array read is total (`getD`):
the indices are in range, but nothing here depends on that. -/
def checkPlan (p : Program) : Except CErr Plan := do
  let mut nodes : Array Node := #[]
  -- each `main` with the number of nodes declared above it
  let mut mains : List (Nat × Option (Ty × Eff) × Expr) := []
  for d in p.decls do
    match d with
    | .fn n ps sg b => nodes := nodes.push (.fnN ⟨n, ps, sg, b⟩)
    | .letD n a i => nodes := nodes.push (.letN n a i)
    | .mainFn sg b => mains := mains ++ [(nodes.size, sg, b)]
  let names := nodes.toList.map Node.name
  -- spec §5.1: a name may be declared at most once at a module's top level
  for (n, i) in names.zipIdx do
    if names.zipIdx.any (fun (m, j) => m == n && j != i) then
      throw (.reject "duplicate" s!"{n} is defined more than once")
  if mains.length > 1 then throw (.reject "duplicate" "main is defined more than once")
  let idx (n : String) : Option Nat := names.findIdx? (· == n)
  -- reference edges, and the declaration-order rules
  let mut edges : Array (List Nat) := #[]
  for (nd, i) in nodes.toList.zipIdx do
    let rs := nd.refs names
    let mut es := []
    for r in rs do
      let j := (idx r).getD 0
      match nd, nodes[j]?.getD default with
      | .fnN _, .letN .. =>
        -- spec §7 rule 16: a `fn` may not be moved above a top-level `let` it depends on
        if j > i then throw (.reject "fn-before-let" s!"{nd.name} precedes the `let` {r} it uses")
      | .letN .., .letN .. =>
        -- spec §7 rule 16: a `let` must already be in scope where it is used
        if j ≥ i then
          throw (.reject "unknown-var" s!"let {nd.name} uses {r} before its declaration")
      | _, _ => pure ()
      es := es ++ [j]
    edges := edges.push es
  for (pos, _, body) in mains do
    for r in (fv [] body).filter names.contains do
      -- spec §7 rule 16: `main` is a `fn`, so it may not sit above a top-level `let` it uses
      if (idx r).getD 0 ≥ pos && ((nodes[(idx r).getD 0]?.getD default).isLet) then
        throw (.reject "fn-before-let" s!"main precedes the `let` {r} it uses")
  -- reachability, to find binding groups (minimal mutually dependent sets)
  let n := nodes.size
  let mut reach : Array (Array Bool) := Array.replicate n (Array.replicate n false)
  for i in [0:n] do
    for j in edges[i]?.getD [] do
      reach := reachSet reach i j
  for k in [0:n] do
    for i in [0:n] do
      for j in [0:n] do
        if reachAt reach i k && reachAt reach k j then
          reach := reachSet reach i j
  let mut groups : List (List Nat) := []
  let mut seen : List Nat := []
  for i in [0:n] do
    unless seen.contains i do
      let g := (List.range n).filter fun j => j == i || (reachAt reach i j && reachAt reach j i)
      seen := seen ++ g
      groups := groups ++ [g]
  -- groups in dependency order: a group runs after every group it reaches
  let order ← orderGroups edges groups [] (n + 1)
  return { nodes, edges, mains, order }

/-- The part of the checker that runs the inference, group by group, then each `main`. -/
def runPlan (rules : Rules) (pl : Plan) : M Unit := do
  let mut env := preludeEnv
  for g in pl.order do
    let members := g.map fun i => pl.nodes[i]?.getD default
    let fnMembers := members.filterMap fun nd => match nd with | .fnN f => some f | _ => none
    match members with
    | [Node.letN nm a i] =>
      if (pl.edges[g.headD 0]?.getD []).contains (g.headD 0) then
        reject "unknown-var" s!"let {nm} refers to itself"
      env ← inferLet rules env nm a i
    | _ =>
      if fnMembers.length != members.length then
        reject "let-in-group" "a top-level `let` in a recursive group"
      env ← inferFnGroup rules env fnMembers
  for (_, sg, body) in pl.mains do
    -- spec §10.10 / §7 rule 10: `main` is `fn main() -> Unit !{IO}` (or `Int !{IO}`), written out
    match sg with
    | none => reject "main-sig" "main must declare its return type and effect"
    | some (r, e) =>
      unless r == .unit || r == .int do reject "main-sig" "main must return Unit or Int"
      unless e == .io do reject "main-sig" "main must declare the IO effect"
      checkBody rules env [] r e body

/-- The checker. `accept` is `ok ()`; a rejection carries `<key>: <detail>`, and `fuel` means a
run was cut off at `fuel` (never a verdict). -/
def checkProgram (rules : Rules := {}) (p : Program) (fuel : Nat := defaultFuel) :
    Except CErr Unit := do
  let pl ← checkPlan p
  match ((runPlan rules pl).run fuel).run {} with
  | .ok _ => pure ()
  | .error e => throw e

end SproutCore
