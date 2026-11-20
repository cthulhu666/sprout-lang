import SproutCore.Syntax
import SproutCore.Check
import Plausible

/-!
# Program generator (Plausible `Gen`)

Type-directed generation of programs the model should accept, plus mutations that plant one
known-bad shape (docs/lean-model-v0.md §6). Verdicts never rely on the intent: the oracle runs
the model and the compiler on every program, so a mutation that happens to stay valid is still a
valid test case.

Generated programs aim to stay in the region where the stage-1 effect model matches the
compiler's effect bounds. A `fn` parameter of arrow type is always annotated, a call never solves
a type variable to an arrow, and a lambda parameter of arrow type takes its type from a slot. The
one place a variable is solved to an arrow of the generator's choosing is `idJoin`, where every
arrow is pure. The oracle compares verdicts on whatever is generated, so a program that leaves the
region shows up as a disagreement, not as a silent pass.

Arrow shapes: unary, n-ary, `() -> T` (spec §7 rule 9), a `fn` returning an arrow, and an `if` that
joins arrows of different stored arity (`idJoin`).
-/

namespace SproutCore

open Plausible

abbrev G := StateT Nat Gen

def below (n : Nat) : G Nat := do
  let ⟨v, _⟩ ← Gen.choose Nat 0 (n - 1) (Nat.zero_le _)
  pure v

def chance (pct : Nat) : G Bool := do
  return (← below 100) < pct

def elem {α : Type} [Inhabited α] (xs : List α) : G α := do
  return xs[(← below xs.length)]!

def freshName (pfx : String) : G String := do
  let n ← get
  set (n + 1)
  pure s!"{pfx}{n}"

def failG {α : Type} (msg : String) : G α := throw (GenError.genError msg)

/-- Pick one of the weighted generators; if it fails, try another. -/
def firstOf {α : Type} (cands : List (Nat × G α)) : G α := do
  let total := cands.foldl (fun a (w, _) => a + w) 0
  if total == 0 then failG "no candidate"
  for _ in [0:8] do
    let mut r ← below total
    let mut chosen : Option (G α) := none
    for (w, g) in cands do
      if chosen.isNone then
        if r < w then chosen := some g else r := r - w
    match chosen with
    | none => pure ()
    | some g =>
      try
        return (← g)
      catch _ => pure ()
  failG "all candidates failed"

/-! ### Types -/

def genSimpleTy : G Ty :=
  firstOf [(4, pure .int), (3, pure .bool), (3, pure .str), (2, pure .unit)]

partial def genClosedTy (depth : Nat) : G Ty :=
  firstOf [(6, genSimpleTy),
    (if depth > 0 then 2 else 0, do
      let n ← elem [2, 2, 3]
      let ts ← (List.range n).mapM fun _ => genClosedTy (depth - 1)
      pure (.tup ts))]

def genEff : G Eff := firstOf [(3, pure .pure), (2, pure .io)]

def isArr : Ty → Bool
  | .arr .. => true
  | _ => false

/-- An arrow over closed types. `ho` allows an arrow parameter of a unary arrow; `thunks` allows
`() -> T`, which only a zero-parameter `fn` can supply. An `idArr` type, `(q.. -> R) -> q.. -> R`,
is the type `idJoin` builds. -/
def genArrTy (ho : Bool) (thunks : Bool := false) : G Ty :=
  firstOf [
    (6, do
      let p ← firstOf [(if ho then 2 else 0, do
          let q ← genClosedTy 1
          let r ← genClosedTy 1
          pure (.arr [q] r (← genEff))), (4, genClosedTy 1)]
      let r ← genClosedTy 1
      pure (.arr [p] r (← genEff))),
    (2, do
      let n ← elem [2, 2, 3]
      let ps ← (List.range n).mapM fun _ => genClosedTy 1
      pure (.arr ps (← genClosedTy 1) (← genEff))),
    (if thunks then 1 else 0, do
      let r ← firstOf [(3, genClosedTy 1), (2, do
        pure (.arr [← genClosedTy 1] (← genClosedTy 1) (← genEff)))]
      -- a `() -> A -> B !{IO}` keeps its effect on the inner arrow (spec §7 rule 9)
      let e ← genEff
      pure (.arr [] r (if isArr r then .pure else e))),
    (2, do
      let n ← elem [1, 1, 2]
      let rest ← (List.range n).mapM fun _ => genClosedTy 1
      let r ← genClosedTy 1
      pure (.arr (.arr rest r .pure :: rest) r .pure))]

/-- The shape `idJoin` builds: `P :: rest -> R` with `P` the pure arrow `rest -> R`. -/
def isIdArr : Ty → Bool
  | .arr (.arr rest r .pure :: rest') r' .pure => !rest.isEmpty && rest == rest' && r == r'
  | _ => false

/-- A type over the type variables `tvs`; an arrow only at parameter depth. -/
partial def genTyWith (tvs : List String) (depth : Nat) (arrow : Bool) : G Ty :=
  firstOf [(4, genSimpleTy),
    (if tvs.isEmpty then 0 else 6, do pure (.named (← elem tvs))),
    (if depth > 0 then 2 else 0, do
      let n ← elem [2, 2, 3]
      pure (.tup (← (List.range n).mapM fun _ => genTyWith tvs (depth - 1) false))),
    (if arrow && !tvs.isEmpty then 3 else 0, do
      let p ← genTyWith tvs 0 false
      let r ← genTyWith tvs 0 false
      pure (.arr [p] r (← genEff)))]

partial def hasArrow : Ty → Bool
  | .arr .. => true
  | .tup ts => ts.any hasArrow
  | _ => false

/-! ### Signatures and environments -/

/-- A callable: a top-level `fn`, or a polymorphic top-level `let` bound to a lambda. -/
structure FnSig where
  name : String
  tvs : List String
  params : List Ty
  ret : Ty
  eff : Eff
  deriving Inhabited, Repr

structure GEnv where
  vars : List (String × Ty) := []
  fns : List FnSig := []
  canIO : Bool := true

/-- An effect-subsumed match of a value's type against what is wanted. -/
def subTy : Ty → Ty → Bool
  | .arr ps r e, .arr ps' r' e' => ps == ps' && r == r' && e.le e'
  | a, b => a == b

partial def matchTy (tvs : List String) : Ty → Ty → List (String × Ty) → Option (List (String × Ty))
  | .named v, t, acc =>
    if tvs.contains v then
      match acc.lookup v with
      | some t' => if t' == t then some acc else none
      | none => some (acc ++ [(v, t)])
    else if t == .named v then some acc else none
  | .tup ps, .tup ts, acc =>
    if ps.length == ts.length then
      (ps.zip ts).foldl (fun a (p, t) => a.bind fun a => matchTy tvs p t a) (some acc)
    else none
  | p, t, acc => if p == t then some acc else none

partial def substTy (m : List (String × Ty)) : Ty → Ty
  | .named v => (m.lookup v).getD (.named v)
  | .tup ts => .tup (ts.map (substTy m))
  | .arr ps r e => .arr (ps.map (substTy m)) (substTy m r) e
  | t => t

def wordList : List String := ["a", "hi", "x y", "", "ok", "sprout"]

/-! ### Expressions -/

mutual
/-- An expression of type `ty` whose effect is within `env.canIO`. -/
partial def genExpr (env : GEnv) (fuel : Nat) (ty : Ty) : G Expr := do
  let rec_ := fuel > 0
  let vars := env.vars.filter fun (_, t) => subTy t ty
  let varCands : List (Nat × G Expr) := vars.map fun (x, _) => (5, pure (.var x))
  let fnVals := env.fns.filter fun f => f.tvs.isEmpty &&
    (f.eff.le (match ty with | .arr _ _ e => e | _ => .pure)) &&
    subTy (mkArr f.params f.ret f.eff) ty
  let fnCands : List (Nat × G Expr) := fnVals.map fun f => (4, pure (.var f.name))
  let literal : List (Nat × G Expr) :=
    match ty with
    | .int => [(4, do pure (.int (← below 50)))]
    | .bool => [(4, do pure (.bool (← chance 50)))]
    | .str => [(4, do pure (.str (← elem wordList)))]
    | .unit => [(3, pure .unit)]
    | .tup ts => [(4, do pure (.tup (← ts.mapM (genExpr env (fuel - 1)))))]
    | .arr (p :: rest) r e => [(6, genLam env fuel p rest r e)]
    | _ => []
  let extra : List (Nat × G Expr) :=
    if rec_ then
      let calls := env.fns.filter fun f =>
        (f.eff.le (if env.canIO then .io else .pure)) &&
        -- stage 1 has no effect variables: never solve a type variable to an arrow
        ((matchTy f.tvs f.ret ty []).map fun m => m.all fun (_, t) => !hasArrow t).getD false
      let callCands := calls.map fun f => (6, genCall env fuel f ty)
      let ioOk (e : Eff) : Bool := e.le (if env.canIO then .io else .pure)
      let hoCalls := env.vars.filter fun (_, t) => match t with
        | .arr _ r e => r == ty && ioOk e
        | _ => false
      let hoCands := hoCalls.map fun (x, t) => (4, do
        match t with
        | .arr ps _ _ => pure (.app (.var x) (← ps.mapM (genExpr env (fuel - 1))))
        | _ => failG "ho")
      -- `x(..)(a)`: a variable whose result is an arrow, applied again
      let hoCalls2 := env.vars.filter fun (_, t) => match t with
        | .arr _ (.arr [_] r e') e => r == ty && ioOk (e.join e')
        | _ => false
      let hoCands2 := hoCalls2.map fun (x, t) => (3, do
        match t with
        | .arr ps (.arr [q] _ _) _ =>
          pure (.app (.app (.var x) (← ps.mapM (genExpr env (fuel - 1)))) [← genExpr env (fuel - 1) q])
        | _ => failG "ho2")
      -- `f(..)(a)`: a `fn` returning an arrow, called and applied again; `f()(a)` for no parameters
      let retCalls := env.fns.filter fun f => f.tvs.isEmpty &&
        (match f.ret with | .arr [_] r e => r == ty && ioOk e | _ => false)
      let retCands := retCalls.map fun f => (4, do
        match f.ret with
        | .arr [q] _ _ => pure (.app (← genCall env fuel f f.ret) [← genExpr env (fuel - 1) q])
        | _ => failG "retcall")
      -- spec §7 rule 8: an identity joined with an arrow of other stored arity. The local `let`
      -- is monomorphic, so the join solves its variable to an arrow; optionally a call does so first.
      let idJoin : (Nat × G Expr) := (if isIdArr ty then 4 else 0, do
        let .arr (p :: _) _ _ := ty | failG "idJoin"
        let g ← freshName "v"
        let x ← freshName "x"
        let c ← genExpr env (fuel - 1) .bool
        let other ← genExpr env (fuel - 1) ty
        let joined := Expr.ite c (.var g) other
        let body ← if (← chance 50) then do
            let u ← freshName "v"
            let arg ← genExpr { env with canIO := false } (fuel - 1) p
            pure (Expr.letIn u (.app (.var g) [arg]) joined)
          else pure joined
        pure (.letIn g (.lam x none (.var x)) body))
      let ite : (Nat × G Expr) := (3, do
        pure (.ite (← genExpr env (fuel - 1) .bool) (← genExpr env (fuel - 1) ty)
          (← genExpr env (fuel - 1) ty)))
      let letIn : (Nat × G Expr) := (3, do
        let x ← freshName "v"
        let t ← firstOf [(5, genClosedTy 1), (2, genArrTy false true)]
        let rhs ← genExpr { env with canIO := false } (fuel - 1) t
        let body ← genExpr { env with vars := (x, t) :: env.vars } (fuel - 1) ty
        pure (.letIn x rhs body))
      let doB : (Nat × G Expr) := (if isArr ty then 0 else 3, genDo env fuel ty)
      let notE : (Nat × G Expr) :=
        (if ty == .bool then 2 else 0, do pure (.notE (← genExpr env (fuel - 1) .bool)))
      let io : List (Nat × G Expr) :=
        if ty == .unit && env.canIO then
          [(6, do pure (.app (.var "print_str") [← genExpr env (fuel - 1) .str]))]
        else []
      callCands ++ hoCands ++ hoCands2 ++ retCands ++ [ite, letIn, doB, notE, idJoin] ++ io
    else []
  firstOf (varCands ++ fnCands ++ literal ++ extra)

partial def genCall (env : GEnv) (fuel : Nat) (f : FnSig) (ty : Ty) : G Expr := do
  let some m := matchTy f.tvs f.ret ty [] | failG "call does not match"
  let mut m := m
  for v in f.tvs do
    if (m.lookup v).isNone then m := m ++ [(v, ← genSimpleTy)]
  let args ← f.params.mapM fun p => genExpr env (fuel - 1) (substTy m p)
  pure (.app (.var f.name) args)

/-- `\x -> body`, or for more parameters (`rest`) nested lambdas: the outer ones are values, so
their bodies perform no IO, and the effect belongs to the innermost lambda (spec §7 rule 9). -/
partial def genLam (env : GEnv) (fuel : Nat) (p : Ty) (rest : List Ty) (r : Ty) (e : Eff) :
    G Expr := do
  let x ← freshName "x"
  let body ← match rest with
    | [] => genExpr { env with vars := (x, p) :: env.vars, canIO := e == .io } (fuel - 1) r
    | _ => genExpr { env with vars := (x, p) :: env.vars, canIO := false } (fuel - 1) (.arr rest r e)
  -- a parameter type with a type variable is never annotated; closed ones sometimes are
  let ann ← if hasNamed p then pure none else do
    pure (if (← chance 40) then some p else none)
  pure (.lam x ann body)

partial def genDo (env : GEnv) (fuel : Nat) (ty : Ty) : G Expr := do
  let n ← elem [0, 1, 1, 2]
  let mut env' := env
  let mut steps : List Step := []
  for _ in [0:n] do
    let kind ← firstOf [(if env.canIO then 4 else 0, pure 0), (if env.canIO then 2 else 0, pure 1),
      (3, pure 2), (1, pure 3)]
    match kind with
    | 0 =>
      steps := steps ++ [.exprS (.app (.var "print_str") [← genExpr env' (fuel - 1) .str])]
    | 1 => steps := steps ++ [.exprS (← genExpr env' (fuel - 1) .unit)]
    | 2 =>
      let x ← freshName "v"
      let t ← genClosedTy 1
      let rhs ← genExpr { env' with canIO := false } (fuel - 1) t
      env' := { env' with vars := (x, t) :: env'.vars }
      steps := steps ++ [.letS x rhs]
    | _ =>
      let t ← genClosedTy 1
      steps := steps ++ [.exprS (← genExpr { env' with canIO := false } (fuel - 1) t)]
  let final ← genExpr env' (fuel - 1) ty
  pure (.doB steps final)
end

end SproutCore
