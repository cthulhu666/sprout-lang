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

/-- A generated `type` declaration: fields are written types, a parameter as `.named`. -/
structure AdtDecl where
  name : String
  params : List String
  ctors : List (String × List Ty)
  deriving Inhabited, Repr

/-- The generator's state: the fresh-name counter and the program's `type` declarations, which
every type and value generator reads. -/
structure GSt where
  n : Nat := 0
  adts : List AdtDecl := []

abbrev G := StateT GSt Gen

def below (n : Nat) : G Nat := do
  let ⟨v, _⟩ ← Gen.choose Nat 0 (n - 1) (Nat.zero_le _)
  pure v

def chance (pct : Nat) : G Bool := do
  return (← below 100) < pct

def elem {α : Type} [Inhabited α] (xs : List α) : G α := do
  return xs[(← below xs.length)]!

def freshName (pfx : String) : G String := do
  let st ← get
  set { st with n := st.n + 1 }
  pure s!"{pfx}{st.n}"

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

/-! ### ADTs -/

partial def substTy (m : List (String × Ty)) : Ty → Ty
  | .named v => (m.lookup v).getD (.named v)
  | .tup ts => .tup (ts.map (substTy m))
  | .arr ps r e => .arr (ps.map (substTy m)) (substTy m r) e
  | .adt n ts => .adt n (ts.map (substTy m))
  | t => t

def adtOf (adts : List AdtDecl) (n : String) : Option AdtDecl := adts.find? (·.name == n)

/-- A constructor's field types at the type arguments `args`. -/
def instFields (ad : AdtDecl) (args : List Ty) (fs : List Ty) : List Ty :=
  fs.map (substTy (ad.params.zip args))

/-- `genExpr` at fuel `d` can build a value of `t` from literals and constructors: a tuple's
elements and a constructor's fields one fuel lower, only a nullary constructor at fuel 0, so a
recursive type always ends. A written type variable has no literal. -/
partial def canBuild (adts : List AdtDecl) (d : Nat) : Ty → Bool
  | .int | .bool | .str | .unit | .arr .. => true
  | .tup ts => ts.all (canBuild adts (d - 1))
  | .adt n args => match adtOf adts n with
    | none => false
    | some ad => ad.ctors.any fun (_, fs) =>
      fs.isEmpty || (d > 0 && (instFields ad args fs).all (canBuild adts (d - 1)))
  | _ => false

/-! ### Types -/

def genSimpleTy : G Ty :=
  firstOf [(4, pure .int), (3, pure .bool), (3, pure .str), (2, pure .unit)]

/-- A declared type at simple arguments whose values `genExpr` builds at fuel 1. -/
def genAdtTy : G Ty := do
  let adts := (← get).adts
  for _ in [0:4] do
    let ad ← elem adts
    let t := Ty.adt ad.name (← ad.params.mapM fun _ => genSimpleTy)
    if canBuild adts 1 t then return t
  failG "no buildable type"

partial def genClosedTy (depth : Nat) : G Ty := do
  let adts := (← get).adts
  firstOf [(6, genSimpleTy),
    (if depth > 0 then 2 else 0, do
      let n ← elem [2, 2, 3]
      let ts ← (List.range n).mapM fun _ => genClosedTy (depth - 1)
      pure (.tup ts)),
    (if adts.isEmpty then 0 else 3, genAdtTy)]

def genEff : G Eff := firstOf [(3, pure .pure), (2, pure .io)]

def isArr : Ty → Bool
  | .arr .. => true
  | _ => false

/-- An arrow over closed types. `ho` allows an arrow parameter of a unary arrow; `thunks` allows
`() -> T`, which only a zero-parameter `fn` can supply. An `idArr` type, `(q.. -> R) -> q.. -> R`,
is the type `idJoin` builds. With declared types, also a constructor's type. -/
def genArrTy (ho : Bool) (thunks : Bool := false) : G Ty := do
  let adts := (← get).adts
  firstOf [
    -- a constructor's own type, which a bare constructor fills (spec §5.3)
    (if adts.isEmpty then 0 else 2, do
      let t ← genAdtTy
      let .adt n args := t | failG "not an ADT"
      let some ad := adtOf adts n | failG "unknown ADT"
      let cs := ad.ctors.filter (!·.2.isEmpty)
      if cs.isEmpty then failG "no constructor with fields"
      let (_, fs) ← elem cs
      pure (.arr (instFields ad args fs) t (← genEff))),
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
partial def genTyWith (tvs : List String) (depth : Nat) (arrow : Bool) : G Ty := do
  let adts := (← get).adts
  firstOf [(4, genSimpleTy),
    (if adts.isEmpty then 0 else 2, do
      let ad ← elem adts
      pure (.adt ad.name (← ad.params.mapM fun _ => genTyWith tvs 0 false))),
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
  | .adt _ ts => ts.any hasArrow
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
  | .adt n ps, .adt m ts, acc =>
    if n == m && ps.length == ts.length then
      (ps.zip ts).foldl (fun a (p, t) => a.bind fun a => matchTy tvs p t a) (some acc)
    else none
  | p, t, acc => if p == t then some acc else none

def wordList : List String := ["a", "hi", "x y", "", "ok", "sprout"]

/-! ### Patterns -/

/-- Patterns with the variables each binds, left to right. -/
abbrev Arms := List (Pat × List (String × Ty))

/-- One catch-all pattern: `_` or a fresh variable. -/
def genCatch (t : Ty) : G (Pat × List (String × Ty)) := do
  if ← chance 50 then return (.wild, [])
  let y ← freshName "y"
  return (.var y, [(y, t)])

def joinRow (ps : List (Pat × List (String × Ty))) : List Pat × List (String × Ty) :=
  (ps.map (·.1), ps.flatMap (·.2))

/-- `k` distinct items, in drawing order. -/
def distinct {α : Type} [BEq α] [Inhabited α] (k : Nat) (xs : List α) : G (List α) := do
  let mut out : List α := []
  for _ in [0:k] do
    let x ← elem xs
    unless out.contains x do out := out ++ [x]
  pure out

mutual
/-- Patterns for one column of type `t`, usually covering it (spec §5.5): `Bool` by both
literals, `Unit` by `()`, `Int` and `String` by literals and a catch-all, a tuple by splitting one
or two of its columns (sometimes diagonally, the per-column gap's shape), an ADT by its
constructors in some order, each with catch-all fields or one field split again, or some of them
and a catch-all. `depth` bounds the nesting. -/
partial def genPats (t : Ty) (depth : Nat) : G Arms := do
  let adts := (← get).adts
  let one : G Arms := do pure [← genCatch t]
  match t with
  | .bool => firstOf [(1, one), (5, do
      let b ← chance 50
      firstOf [(4, pure [(.bool b, []), (.bool !b, [])]),
        (2, do pure ((.bool b, []) :: (← one)))])]
  | .unit => firstOf [(1, one), (4, pure [(.unit, [])])]
  | .int => firstOf [(1, one), (4, do
      let ns ← distinct (← elem [1, 1, 2, 3]) (List.range 20)
      pure (ns.map (fun n => (Pat.int n, [])) ++ (← one)))]
  | .str => firstOf [(1, one), (3, do
      let ws ← distinct (← elem [1, 1, 2]) wordList
      pure (ws.map (fun w => (Pat.str w, [])) ++ (← one)))]
  | .tup ts => firstOf [(1, one), (2, do
      let (ps, bs) := joinRow (← ts.mapM genCatch)
      pure [(.tup ps, bs)]),
    (if depth > 0 then 5 else 0, do
      let i ← below ts.length
      let col ← genPats ts[i]! (depth - 1)
      col.mapM fun pb => tupRow ts [(i, pb)]),
    (if depth > 0 && ts.length ≥ 2 then 3 else 0, do
      -- two columns: their product, or the diagonal (covered per column, maybe not as a whole)
      let i ← below (ts.length - 1)
      let j := i + 1 + (← below (ts.length - 1 - i))
      let ci ← genPats ts[i]! (depth - 1)
      let cj ← genPats ts[j]! (depth - 1)
      if ci.length * cj.length ≤ 6 && (← chance 60) then
        (ci.flatMap fun a => cj.map fun b => (a, b)).mapM fun (a, b) => tupRow ts [(i, a), (j, b)]
      else
        let n := max ci.length cj.length
        (List.range n).mapM fun k =>
          tupRow ts [(i, ci[k % ci.length]!), (j, cj[k % cj.length]!)])]
  | .adt n args =>
    match adtOf adts n with
    | none => one
    | some ad => firstOf [(1, one), (6, do
        let cs ← firstOf [(5, pure ad.ctors), (2, pure ad.ctors.reverse)]
        -- every constructor, or the first few and a catch-all
        let k ← if ← chance 75 then pure cs.length else below cs.length
        let rows ← (cs.take k).flatMapM fun (c, fs) => ctorRows c (instFields ad args fs) depth
        if k < cs.length then pure (rows ++ (← one)) else pure rows)]
  | _ => one

/-- A tuple pattern with the given columns fixed and a catch-all in every other one. -/
partial def tupRow (ts : List Ty) (fixed : List (Nat × (Pat × List (String × Ty)))) :
    G (Pat × List (String × Ty)) := do
  let cols ← ts.zipIdx.mapM fun (t, i) => match fixed.lookup i with
    | some pb => pure pb
    | none => genCatch t
  let (ps, bs) := joinRow cols
  pure (.tup ps, bs)

/-- The rows for one constructor: catch-all fields, or one field split. -/
partial def ctorRows (c : String) (fs : List Ty) (depth : Nat) : G Arms := do
  if fs.isEmpty then return [(.ctor c [], [])]
  firstOf [(3, do
      let (ps, bs) := joinRow (← fs.mapM genCatch)
      pure [(.ctor c ps, bs)]),
    (if depth > 0 then 3 else 0, do
      let i ← below fs.length
      let col ← genPats fs[i]! (depth - 1)
      col.mapM fun pb => do
        let cols ← fs.zipIdx.mapM fun (t, j) => if j == i then pure pb else genCatch t
        let (ps, bs) := joinRow cols
        pure (.ctor c ps, bs))]
end

/-! ### Expressions -/

/-- A type a `match` can take apart: not an arrow and not a rigid variable. -/
def matchable : Ty → Bool
  | .arr .. | .named _ => false
  | _ => true

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
  let adts := (← get).adts
  -- a constructor whose instantiated fields are `ps`, named bare as a function value (spec §5.3)
  let bareCtors (ps : List Ty) (r : Ty) : List (Nat × G Expr) :=
    match r with
    | .adt n args => match adtOf adts n with
      | some ad => (ad.ctors.filter fun (_, fs) => !fs.isEmpty && instFields ad args fs == ps).map
          fun (c, _) => (3, pure (.ctor c))
      | none => []
    | _ => []
  let literal : List (Nat × G Expr) :=
    match ty with
    | .int => [(4, do pure (.int (← below 50)))]
    | .bool => [(4, do pure (.bool (← chance 50)))]
    | .str => [(4, do pure (.str (← elem wordList)))]
    | .unit => [(3, pure .unit)]
    | .tup ts => [(4, do pure (.tup (← ts.mapM (genExpr env (fuel - 1)))))]
    | .arr ps@(p :: rest) r e => (6, genLam env fuel p rest r e) :: bareCtors ps r
    -- a nullary constructor is a value; any other is called saturated (spec §5.3)
    | .adt n args => match adtOf adts n with
      | some ad => (ad.ctors.filter fun (_, fs) =>
            let fs := instFields ad args fs
            fs.isEmpty || (fuel > 0 && fs.all (canBuild adts (fuel - 1)))).map fun (c, fs) =>
          let fs := instFields ad args fs
          (if fs.isEmpty then 3 else 4, do
            if fs.isEmpty then pure (.ctor c)
            else pure (.app (.ctor c) (← fs.mapM (genExpr env (fuel - 1)))))
      | none => []
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
      let matchE : (Nat × G Expr) := (3, genMatch env fuel ty)
      callCands ++ hoCands ++ hoCands2 ++ retCands ++ [ite, letIn, doB, notE, idJoin, matchE] ++ io
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

/-- `match` on a variable in scope or a fresh value, with arms from `genPats`, sometimes a
trailing catch-all after them. -/
partial def genMatch (env : GEnv) (fuel : Nat) (ty : Ty) : G Expr := do
  let scrutVars := env.vars.filter fun (_, t) => matchable t
  let (scrut, st) ← firstOf [
    (if scrutVars.isEmpty then 0 else 6, do
      let (x, t) ← elem scrutVars
      pure (Expr.var x, t)),
    (4, do
      let t ← firstOf [(5, genClosedTy 1), (2, pure .int), (1, pure .str)]
      pure (← genExpr env (fuel - 1) t, t))]
  let mut pats ← genPats st 2
  if ← chance 8 then pats := pats ++ [← genCatch st]
  let arms ← pats.mapM fun (p, bs) => do
    pure (p, ← genExpr { env with vars := bs ++ env.vars } (fuel - 1) ty)
  pure (.matchE scrut arms)

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
