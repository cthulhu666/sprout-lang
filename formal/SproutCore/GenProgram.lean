import SproutCore.Gen

/-!
# Whole programs and mutations

`genBase` builds a program the model should accept. `mutate` plants one known-bad shape
(docs/lean-model-v0.md §6). The generator keeps `let`s first, so a program is printed in the
order the spec's binding-order rule allows (spec §7 rule 16).
-/

namespace SproutCore

open Plausible

structure Info where
  sigs : List FnSig := []
  polyLets : List FnSig := []
  letVars : List (String × Ty) := []

/-! ### Type declarations -/

/-- A field type for the declaration at index `i` of `decls` (name, parameters). A reference to
another declared type is to one before `i` and inhabited, or with `rec_` to any of them, itself
included. A recursive reference applies its own parameters in order, so no type grows. -/
def genFieldTy (decls : List (String × List String)) (i : Nat) (ps : List String) (rec_ : Bool)
    (empty : List String) : G Ty := do
  let refs := (decls.zipIdx.filter fun ((n, _), j) =>
    rec_ || (j < i && !empty.contains n)).map (·.1)
  let atom : G Ty := firstOf [(4, genSimpleTy), (if ps.isEmpty then 0 else 3, do
    pure (.named (← elem ps)))]
  firstOf [(5, genSimpleTy),
    (2, do pure (.tup [← atom, ← atom])),
    (if ps.isEmpty then 0 else 4, do pure (.named (← elem ps))),
    (if refs.isEmpty then 0 else 3, do
      let (n, qs) ← elem refs
      if n == (decls[i]!).1 && (← chance 80) then pure (.adt n (ps.map .named))
      else pure (.adt n (← qs.mapM fun _ => atom)))]

/-- 1-3 `type` declarations: 0-2 parameters, 1-3 constructors of 0-3 fields each. Fields refer to
primitives, tuples, parameters and the declared types, forward and mutually. Each type has a
constructor with no reference to a type after it or to itself, so it has values, except a rare
uninhabited one, `type T = | C T`. -/
def genTypes : G (List Decl) := do
  let nt ← elem [1, 1, 2, 2, 3]
  let mut decls : List (String × List String) := []
  for _ in [0:nt] do
    decls := decls ++ [(← freshName "Q", ← elem [[], [], ["a"], ["a", "b"]])]
  let mut empty : List String := []
  let mut out : List AdtDecl := []
  for ((n, ps), i) in decls.zipIdx do
    if ← chance 8 then
      -- uninhabited: every constructor refers to the type itself
      let self := Ty.adt n (ps.map .named)
      let k ← elem [1, 1, 2]
      let mut cs := []
      for _ in [0:k] do
        cs := cs ++ [(← freshName "K", self :: (← if ← chance 50 then pure [] else
          pure [← genFieldTy decls i ps false empty]))]
      empty := empty ++ [n]
      out := out ++ [⟨n, ps, cs⟩]
    else
      let nc ← elem [1, 2, 2, 3]
      let mut cs := []
      for j in [0:nc] do
        let nf ← elem [0, 1, 1, 2, 3]
        let fs ← (List.range nf).mapM fun _ => genFieldTy decls i ps (j > 0) empty
        -- a one-constructor type may name its constructor after itself (spec §5.6)
        let c ← if nc == 1 && (← chance 20) then pure n else freshName "K"
        cs := cs ++ [(c, fs)]
      if ← chance 30 then cs := cs.reverse
      out := out ++ [⟨n, ps, cs⟩]
  modify fun st => { st with adts := out }
  return out.map fun ad => .typeD ad.name ad.params ad.ctors

/-- The declared types `genExpr` can build no value of. -/
def emptyTypes : G (List Ty) := do
  let adts := (← get).adts
  return (adts.map fun ad => Ty.adt ad.name (ad.params.map fun _ => .int)).filter
    (!canBuild adts 6 ·)

/-- A signature, drawn so that a body of the return type certainly exists. -/
def genFnSig (name : String) : G FnSig := do
  for _ in [0:10] do
    let poly ← chance 30
    let tvs ← if poly then elem [["a"], ["a"], ["a", "b"]] else pure []
    -- a zero-parameter `fn` has type `() -> T` (spec §7 rule 9)
    let n ← if poly then elem [1, 1, 1, 2, 2, 3] else elem [0, 1, 1, 1, 2, 2, 3]
    let mut ps : List Ty := []
    for _ in [0:n] do
      let t ← if poly then genTyWith tvs 1 true
        else firstOf [(5, genClosedTy 1), (2, genArrTy false true), (1, genArrTy true)]
      ps := ps ++ [t]
    -- a parameter of a type with no values: its body can only match on it
    let empty ← emptyTypes
    if !poly && !empty.isEmpty && (← chance 15) then ps := ps ++ [← elem empty]
    -- a returned arrow carries its own effect, so the `fn` itself is pure then
    let ret ← if poly then genTyWith tvs 1 false
      else firstOf [(6, genClosedTy 1), (2, genArrTy false)]
    let eff ← if isArr ret then pure .pure else genEff
    let sig : FnSig := ⟨name, tvs, ps, ret, eff⟩
    -- a body must exist from the parameters alone
    let xs := (List.range ps.length).map fun i => (s!"t{i}", ps[i]!)
    try
      let _ ← genExpr { vars := xs, canIO := eff == .io } 1 ret
      return sig
    catch _ => pure ()
  pure ⟨name, [], [.int], .int, .pure⟩

def genFnDecl (info : Info) (sig : FnSig) : G Decl := do
  let pnames ← sig.params.mapM fun _ => freshName "p"
  let poly := !sig.tvs.isEmpty
  let mut anns : List (Option Ty) := []
  for t in sig.params do
    let a ← if poly || isArr t then pure true else chance 80
    anns := anns ++ [if a then some t else none]
  let annR ← if poly || sig.eff == .io || isArr sig.ret then pure true else chance 70
  let env : GEnv := {
    vars := info.letVars ++ pnames.zip sig.params
    fns := info.polyLets ++ info.sigs
    canIO := sig.eff == .io }
  let body ← try genExpr env 3 sig.ret
    catch _ => genExpr { env with fns := [] } 1 sig.ret
  pure (.fn sig.name (pnames.zip anns) (if annR then some (sig.ret, sig.eff) else none) body)

def genLets (n : Nat) : G (List Decl × Info) := do
  let mut decls : List Decl := []
  let mut info : Info := {}
  for _ in [0:n] do
    let name ← freshName "l"
    let kind ← firstOf [(2, pure 0), (5, pure 1), (3, pure 2)]
    let env : GEnv := { vars := info.letVars, fns := info.polyLets, canIO := false }
    match kind with
    | 0 =>
      let x ← freshName "x"
      let a : Ty := .named "a"
      let (body, ret) ← firstOf [(3, pure (Expr.var x, a)),
        (2, pure (Expr.tup [.var x, .var x], Ty.tup [a, a]))]
      decls := decls ++ [.letD name none (.lam x none body)]
      info := { info with polyLets := info.polyLets ++ [⟨name, ["a"], [a], ret, .pure⟩] }
    | _ =>
      -- a value of a closed type (1), or of an arrow type (2)
      let t ← if kind == 1 then genClosedTy 1 else genArrTy false
      let init ← genExpr env 2 t
      let ann ← chance 30
      decls := decls ++ [.letD name (if ann then some t else none) init]
      info := { info with letVars := info.letVars ++ [(name, t)] }
  pure (decls, info)

def genBase : G (List Decl × Info) := do
  let types ← if ← chance 50 then genTypes else pure []
  let nl ← elem [0, 0, 1, 1, 2, 3]
  let (lets, info0) ← genLets nl
  let nf ← elem [1, 2, 2, 3, 4]
  let mut sigs : List FnSig := []
  for _ in [0:nf] do
    sigs := sigs ++ [← genFnSig (← freshName "f")]
  let info := { info0 with sigs := sigs }
  let mut fns : List Decl := []
  for s in sigs do
    fns := fns ++ [← genFnDecl info s]
  let mut decls := types ++ lets ++ fns
  if ← chance 70 then
    let env : GEnv := { vars := info.letVars, fns := info.polyLets ++ info.sigs, canIO := true }
    let body ← genExpr env 3 .unit
    decls := decls ++ [.mainFn (some (.unit, .io)) body]
  pure (decls, info)

/-! ### Mutations -/

def wrongLit (t : Ty) : G Expr := do
  let cands : List (Ty × Expr) := [(.int, .int 1), (.bool, .bool true), (.str, .str "w"), (.unit, .unit)]
  let ok := cands.filter fun (c, _) => c != t
  pure (ok[(← below ok.length)]!).2

def Decl.isMain : Decl → Bool
  | .mainFn .. => true
  | _ => false

/-! ### Arm mutations -/

mutual
partial def countMatches : Expr → Nat
  | .lam _ _ b | .notE b => countMatches b
  | .app f args => countMatches f + (args.map countMatches).sum
  | .letIn _ r b => countMatches r + countMatches b
  | .ite c t e => countMatches c + countMatches t + countMatches e
  | .tup es => (es.map countMatches).sum
  | .doB steps final => (steps.map countStep).sum + countMatches final
  | .matchE sc arms => 1 + countMatches sc + (arms.map (countMatches ·.2)).sum
  | _ => 0

partial def countStep : Step → Nat
  | .letS _ r => countMatches r
  | .exprS e => countMatches e
end

mutual
/-- Apply `f` to the arms of the `k`-th `match`, counting in the state. -/
partial def editMatch (k : Nat) (f : List (Pat × Expr) → List (Pat × Expr)) :
    Expr → StateM Nat Expr
  | .lam x a b => return .lam x a (← editMatch k f b)
  | .notE b => return .notE (← editMatch k f b)
  | .app g args => return .app (← editMatch k f g) (← args.mapM (editMatch k f))
  | .letIn x r b => return .letIn x (← editMatch k f r) (← editMatch k f b)
  | .ite c t e => return .ite (← editMatch k f c) (← editMatch k f t) (← editMatch k f e)
  | .tup es => return .tup (← es.mapM (editMatch k f))
  | .doB steps final => return .doB (← steps.mapM (editStep k f)) (← editMatch k f final)
  | .matchE sc arms => do
    let i ← get
    set (i + 1)
    let sc ← editMatch k f sc
    let arms ← arms.mapM fun (p, e) => do pure (p, ← editMatch k f e)
    return .matchE sc (if i == k then f arms else arms)
  | e => pure e

partial def editStep (k : Nat) (f : List (Pat × Expr) → List (Pat × Expr)) :
    Step → StateM Nat Step
  | .letS x r => return .letS x (← editMatch k f r)
  | .exprS e => return .exprS (← editMatch k f e)
end

def Decl.body? : Decl → Option Expr
  | .fn _ _ _ b | .mainFn _ b | .letD _ _ b => some b
  | .typeD .. => none

def editDecls (k : Nat) (f : List (Pat × Expr) → List (Pat × Expr)) (ds : List Decl) :
    List Decl :=
  let step (d : Decl) : StateM Nat Decl := match d with
    | .fn n ps sg b => return .fn n ps sg (← editMatch k f b)
    | .letD n a i => return .letD n a (← editMatch k f i)
    | .mainFn sg b => return .mainFn sg (← editMatch k f b)
    | d => pure d
  (ds.mapM step).run' 0

/-- Remove arm `r` (spec §5.5: a missing arm is a non-exhaustive match). -/
def removeArm (r : Nat) (arms : List (Pat × Expr)) : List (Pat × Expr) :=
  if arms.length < 2 then arms else arms.eraseIdx (r % arms.length)

/-- Move an arm to an earlier position (spec §5.5: a catch-all moved up shadows the rest). -/
def moveArmEarlier (r1 r2 : Nat) (arms : List (Pat × Expr)) : List (Pat × Expr) :=
  if arms.length < 2 then arms else
  let i := 1 + r1 % (arms.length - 1)
  let j := r2 % i
  (arms.eraseIdx i).insertIdx j arms[i]!

/-- Repeat an arm right after itself. -/
def dupArm (r : Nat) (arms : List (Pat × Expr)) : List (Pat × Expr) :=
  if arms.isEmpty then arms else
  let i := r % arms.length
  arms.insertIdx (i + 1) arms[i]!

/-- One sub-pattern too many or too few in the first constructor or tuple pattern from arm `r`. -/
def arityArm (r : Nat) (arms : List (Pat × Expr)) : List (Pat × Expr) :=
  let n := arms.length
  let idx := (List.range n).map (fun k => (r + k) % n) |>.find? fun i =>
    match arms[i]!.1 with
    | .ctor .. | .tup _ => true
    | _ => false
  match idx with
  | none => arms
  | some i =>
    let (p, e) := arms[i]!
    let p' := match p with
      | .ctor c ps =>
        if r % 2 == 0 || ps.isEmpty then Pat.ctor c (ps ++ [.wild]) else .ctor c ps.dropLast
      | .tup ps => .tup (ps ++ [.wild])
      | p => p
    arms.set i (p', e)

def mutate (info : Info) (decls : List Decl) : G (List Decl) := do
  let env : GEnv := { vars := info.letVars, fns := info.polyLets ++ info.sigs, canIO := true }
  let ioSigs := info.sigs.filter fun s => s.eff == .io && s.tvs.isEmpty
  let monoSigs := info.sigs.filter fun s => s.tvs.isEmpty
  let k ← below 23
  let m ← freshName "m"
  let adts := (← get).adts
  match k with
  | 0 =>
    -- a pure function whose body performs IO (spec §7 rules 8, 11)
    let body ← firstOf [(3, pure (Expr.app (.var "print_str") [.str "m"])),
      (3, genExpr env 3 .unit)]
    let sg ← elem [none, some (Ty.unit, Eff.pure)]
    pure (decls ++ [.fn m [] sg body])
  | 1 =>
    -- an IO function value in a pure arrow slot (spec §7 rule 8)
    let p ← genSimpleTy
    let r ← genSimpleTy
    let h ← freshName "h"
    let helper := Decl.fn h [("f", some (.arr [p] r .pure)), ("a", some p)] (some (r, .pure))
      (.app (.var "f") [.var "a"])
    let x ← freshName "x"
    let io := Expr.lam x none (.doB [.exprS (.app (.var "print_str") [.str "m"])]
      (← genExpr { env with canIO := false } 1 r))
    let arg ← genExpr { env with canIO := false } 1 p
    pure (decls ++ [helper, .fn m [] (some (r, .io)) (.app (.var h) [io, arg])])
  | 2 =>
    -- a peer join of a pure and an IO function (spec §7 rule 8)
    let inner ← elem [Eff.pure, Eff.io]
    let u ← freshName "u"
    let w ← freshName "w"
    let a := Expr.lam u none .unit
    let b := Expr.lam w none (.app (.var "print_str") [.str "m"])
    let (t, e) ← elem [(a, b), (b, a)]
    pure (decls ++ [.fn m [("c", some .bool)] (some (.arr [.unit] .unit inner, .pure))
      (.ite (.var "c") t e)])
  | 3 =>
    -- a local `let`-bound lambda used at two types (spec §5.6: a local `let` is monomorphic)
    let t1 ← genSimpleTy
    let t2 ← genSimpleTy
    let a ← genExpr { env with canIO := false } 1 t1
    let b ← genExpr { env with canIO := false } 1 t2
    pure (decls ++ [.fn m [] (some (.tup [t1, t2], .pure))
      (.letIn "f" (.lam "x" none (.var "x")) (.tup [.app (.var "f") [a], .app (.var "f") [b]]))])
  | 4 =>
    -- an `if` condition that is not Bool (spec §7 rule 2)
    let c ← elem [Ty.int, .str, .unit]
    let t ← genSimpleTy
    pure (decls ++ [.fn m [] (some (t, .pure))
      (.ite (← genExpr { env with canIO := false } 1 c) (← genExpr { env with canIO := false } 1 t)
        (← genExpr { env with canIO := false } 1 t))])
  | 5 =>
    -- an argument of the wrong type (spec §7 rule 3)
    if monoSigs.isEmpty then pure decls else
    let s ← elem monoSigs
    let i ← below s.params.length
    let mut args := []
    for (p, j) in s.params.zipIdx do
      if j == i then args := args ++ [← wrongLit p]
      else args := args ++ [← genExpr { env with canIO := false } 1 p]
    pure (decls ++ [.fn m [] (some (s.ret, s.eff)) (.app (.var s.name) args)])
  | 6 =>
    -- a top-level `let` that performs IO (spec §5.2)
    let init ← if ioSigs.isEmpty then pure (Expr.app (.var "print_str") [.str "m"]) else do
      let s ← elem ioSigs
      let args ← s.params.mapM fun p => genExpr { env with canIO := false } 1 p
      firstOf [(1, pure (Expr.app (.var "print_str") [.str "m"])), (2, pure (.app (.var s.name) args))]
    pure (.letD m none init :: decls)
  | 7 =>
    -- order: a `fn` above a `let` it uses, or a `let` above one it uses (spec §7 rule 16)
    let l ← freshName "l"
    if (← chance 50) then
      pure (decls ++ [.fn m [] (some (.int, .pure)) (.var l), .letD l none (.int 1)])
    else
      pure (.letD m none (.var l) :: .letD l none (.int 1) :: decls)
  | 8 =>
    -- polymorphic recursion: needs a complete signature (spec §7 rule 16)
    let complete ← chance 50
    let x := "x"
    pure (decls ++ [.fn m [("n", some .int), (x, if complete then some (.named "a") else none)]
      (some (.int, .pure)) (.ite (.bool true) (.app (.var m) [.var "n", .tup [.var x, .var x]]) (.int 1))])
  | 9 =>
    -- rigid type variables (spec §7 rule 15)
    let d ← elem [
      Decl.fn m [("x", some (.named "a")), ("y", some (.named "b"))] (some (.named "a", .pure)) (.var "y"),
      Decl.fn m [("x", some (.named "a"))] (some (.int, .pure)) (.var "x"),
      Decl.fn m [("x", some (.named "a")), ("y", some (.named "b"))]
        (some (.tup [.named "a", .named "b"], .pure)) (.tup [.var "x", .var "y"])]
    pure (decls ++ [d])
  | 10 =>
    -- too few or too many arguments to a known function (spec §5.3)
    if monoSigs.isEmpty then pure decls else
    let s ← elem monoSigs
    let mut args ← s.params.mapM fun p => genExpr { env with canIO := false } 1 p
    if (← chance 50) && !args.isEmpty then args := args.dropLast
    else args := args ++ [.int 1]
    pure (decls ++ [.fn m [] (some (s.ret, s.eff)) (.app (.var s.name) args)])
  | 11 =>
    -- a malformed `main` (spec §10.10, §7 rule 10)
    let sg ← elem [none, some (Ty.unit, Eff.pure), some (.str, .io), some (.unit, .io), some (.int, .io)]
    let body ← match sg with
      | some (t, _) => genExpr env 1 t
      | none => pure Expr.unit
    pure (decls.filter (!·.isMain) ++ [.mainFn sg body])
  | 12 =>
    -- a name declared twice (spec §5.1)
    let names := info.sigs.map (·.name) ++ info.letVars.map (·.1)
    if names.isEmpty then pure decls else
    let n ← elem names
    pure (decls ++ [.fn n [("x", some .int)] (some (.int, .pure)) (.var "x")])
  | 13 =>
    -- an effectful right-hand side in a `let` (spec §5.2.1, §5.2.1a)
    let rhs := Expr.app (.var "print_str") [.str "m"]
    let body ← elem [
      Expr.doB [.letS "y" rhs] .unit,
      Expr.letIn "y" rhs .unit]
    pure (decls ++ [.fn m [] (some (.unit, .io)) body])
  | 14 =>
    -- an unknown variable
    pure (decls ++ [.fn m [] (some (.int, .pure)) (.var "zz")])
  | 15 =>
    -- `main` above a top-level `let` it uses: `main` is a `fn` too (spec §7 rule 16)
    pure (decls.filter (!·.isMain) ++ [.mainFn (some (.unit, .io)) (.app (.var "print_str") [.var m]),
      .letD m none (.str "m")])
  | 16 =>
    -- a peer join under a parameter, and a second function value in a slot (spec §7 rule 8)
    let h ← freshName "h"
    let eff ← elem [Eff.pure, Eff.io]
    let cb := Ty.arr [.unit] .unit eff
    let helper := Decl.fn h [("g", some (.arr [cb] .unit .pure))] (some (.unit, .pure))
      (.app (.var "g") [.lam "u" none .unit])
    pure (decls ++ [helper])
  | 18 | 19 | 20 | 21 =>
    -- an arm removed, moved earlier, repeated, or a pattern of the wrong arity (spec §5.5, §5.3),
    -- in a `match` of the program or in a new one
    let r1 ← below 1000
    let r2 ← below 1000
    let f := match k with
      | 18 => removeArm r1
      | 19 => moveArmEarlier r1 r2
      | 20 => dupArm r1
      | _ => arityArm r1
    let n := (decls.filterMap Decl.body?).foldl (· + countMatches ·) 0
    if n > 0 && (← chance 60) then return editDecls (← below n) f decls
    let t ← firstOf [(if adts.isEmpty then 0 else 5, do
        let ad ← elem adts
        pure (Ty.adt ad.name (ad.params.map fun _ => .int))),
      (3, genClosedTy 1), (1, pure .bool)]
    let pats ← genPats t 2
    let arms ← pats.mapM fun (p, _) => do pure (p, Expr.int (← below 9))
    pure (decls ++ [.fn m [("x", some t)] (some (.int, .pure)) (.matchE (.var "x") (f arms))])
  | 22 =>
    -- an ill-formed declaration or a constructor call of the wrong arity (spec §5.6, §5.3)
    let q ← freshName "Q"
    let c ← freshName "K"
    let withParams := adts.filter (!·.params.isEmpty)
    firstOf [
      (1, pure (decls ++ [.typeD q [] [(c, [.named "z"])]])),
      (if withParams.isEmpty then 0 else 1, do
        let ad ← elem withParams
        let args ← if ← chance 50 then pure ((ad.params.map fun _ => Ty.int) ++ [.int]) else
          pure (ad.params.dropLast.map fun _ => Ty.int)
        pure (decls ++ [.typeD q [] [(c, [.adt ad.name args])]])),
      (if adts.isEmpty then 0 else 3, do
        let ad ← elem adts
        let some (kc, fs) := ad.ctors.find? (!·.2.isEmpty) | failG "no ctor with fields"
        let t := Ty.adt ad.name (ad.params.map fun _ => .int)
        let fs := instFields ad (ad.params.map fun _ => .int) fs
        let args ← fs.mapM (genExpr { env with canIO := false } 1)
        let args := if ← chance 50 then args ++ [.int 1] else args.dropLast
        pure (decls ++ [.fn m [] (some (t, .pure)) (.app (.ctor kc) args)]))]
  | _ =>
    -- a zero-parameter function used as a function of one argument (spec §7 rule 9): bound to a
    -- local and called with an argument, or passed to a unary slot
    let thunks := info.sigs.filter fun s => s.params.isEmpty && s.tvs.isEmpty
    if thunks.isEmpty then pure decls else
    let s ← elem thunks
    let y ← freshName "y"
    let g ← freshName "g"
    let h ← freshName "h"
    let body ← elem [
      Expr.letIn g (.var s.name) (.app (.var g) [.var y]),
      Expr.app (.var h) [.var s.name, .var y]]
    let ap := Decl.fn h [("h", some (.arr [.int] .int .pure)), ("a", some .int)]
      (some (.int, .pure)) (.app (.var "h") [.var "a"])
    pure (decls ++ [ap, .fn m [(y, some .int)] (some (.int, .pure)) body])

/-- A whole program: a base, with one or two mutations. -/
def genProgram : G Program := do
  let (decls, info) ← genBase
  let nm ← firstOf [(5, pure 0), (4, pure 1), (1, pure 2)]
  let mut ds := decls
  for _ in [0:nm] do
    ds ← mutate info ds
  pure ⟨ds⟩

/-- Generate the program for `seed` (deterministic), retrying on a generation failure. -/
def programOfSeed (seed : Nat) (size : Nat := 8) : Program := Id.run do
  for attempt in [0:20] do
    let g := (genProgram.run' {})
    match (runRandWith (seed * 7919 + attempt) g).run ⟨size⟩ with
    | .ok p => return p
    | .error _ => pure ()
  return ⟨[.mainFn (some (.unit, .io)) .unit]⟩

end SproutCore
