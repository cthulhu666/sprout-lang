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

/-- A signature, drawn so that a body of the return type certainly exists. -/
def genFnSig (name : String) : G FnSig := do
  for _ in [0:10] do
    let poly ← chance 30
    let tvs ← if poly then elem [["a"], ["a"], ["a", "b"]] else pure []
    let n ← elem [1, 1, 1, 2, 2, 3]
    let mut ps : List Ty := []
    for _ in [0:n] do
      let t ← if poly then genTyWith tvs 1 true
        else firstOf [(5, genClosedTy 1), (2, genArrTy false), (1, genArrTy true)]
      ps := ps ++ [t]
    let ret ← if poly then genTyWith tvs 1 false else genClosedTy 1
    let eff ← genEff
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
  let annR ← if poly || sig.eff == .io then pure true else chance 70
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
    | 1 =>
      let t ← genClosedTy 1
      let init ← genExpr env 2 t
      let ann ← chance 30
      decls := decls ++ [.letD name (if ann then some t else none) init]
      info := { info with letVars := info.letVars ++ [(name, t)] }
    | _ =>
      let t ← genArrTy false
      let init ← genExpr env 2 t
      let ann ← chance 30
      decls := decls ++ [.letD name (if ann then some t else none) init]
      info := { info with letVars := info.letVars ++ [(name, t)] }
  pure (decls, info)

def genBase : G (List Decl × Info) := do
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
  let mut decls := lets ++ fns
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

def mutate (info : Info) (decls : List Decl) : G (List Decl) := do
  let env : GEnv := { vars := info.letVars, fns := info.polyLets ++ info.sigs, canIO := true }
  let ioSigs := info.sigs.filter fun s => s.eff == .io && s.tvs.isEmpty
  let monoSigs := info.sigs.filter fun s => s.tvs.isEmpty
  let k ← below 17
  let m ← freshName "m"
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
    let isMain : Decl → Bool := fun d => match d with | .mainFn .. => true | _ => false
    pure (decls.filter (!isMain ·) ++ [.mainFn sg body])
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
    -- a pure function value in a non-value top-level `let`, called from another top-level `let`
    -- (spec §5.2: a top-level `let` must be pure)
    let p ← genSimpleTy
    let r ← genSimpleTy
    let closed : GEnv := { canIO := false }
    let a ← genLam closed 0 p r .pure
    let b ← genLam closed 0 p r .pure
    let c ← genExpr closed 1 .bool
    let arg ← genExpr closed 1 p
    let l ← freshName "l"
    pure (.letD l none (.ite c a b) :: .letD m none (.app (.var l) [arg]) :: decls)
  | _ =>
    -- a peer join under a parameter, and a second function value in a slot (spec §7 rule 8)
    let h ← freshName "h"
    let eff ← elem [Eff.pure, Eff.io]
    let cb := Ty.arr [.unit] .unit eff
    let helper := Decl.fn h [("g", some (.arr [cb] .unit .pure))] (some (.unit, .pure))
      (.app (.var "g") [.lam "u" none .unit])
    pure (decls ++ [helper])

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
    let g := (genProgram.run' 0)
    match (runRandWith (seed * 7919 + attempt) g).run ⟨size⟩ with
    | .ok p => return p
    | .error _ => pure ()
  return ⟨[.mainFn (some (.unit, .io)) .unit]⟩

end SproutCore
