import SproutCore.Syntax

/-!
# Shrinking

Candidate smaller programs. Any candidate is a valid Core program, so the oracle accepts a
candidate exactly when both sides still disagree with the same shape.
-/

namespace SproutCore

/-- Replace one element of a list by each of its shrinks. -/
def shrinkAt {α : Type} [Inhabited α] (xs : List α) (f : α → List α) : List (List α) :=
  (List.range xs.length).flatMap fun i =>
    (f xs[i]!).map fun y => xs.set i y

mutual
partial def shrinkStep : Step → List Step
  | .letS x r => (shrinkExpr r).map (.letS x ·)
  | .exprS e => (shrinkExpr e).map .exprS

partial def shrinkExpr : Expr → List Expr
  | .var _ => []
  | .int n => if n == 0 then [] else [.int 0]
  | .bool b => if b then [.bool false] else []
  | .str s => if s == "" then [] else [.str ""]
  | .unit => []
  | .lam x a b => [b] ++ (if a.isSome then [.lam x none b] else []) ++
      (shrinkExpr b).map (.lam x a ·)
  | .app f args => args ++ [f] ++ (shrinkExpr f).map (.app · args) ++
      (shrinkAt args shrinkExpr).map (.app f ·)
  | .letIn x r b => [b, r] ++ (shrinkExpr r).map (.letIn x · b) ++
      (shrinkExpr b).map (.letIn x r ·)
  | .ite c t e => [t, e, c] ++ (shrinkExpr c).map (.ite · t e) ++
      (shrinkExpr t).map (.ite c · e) ++ (shrinkExpr e).map (.ite c t ·)
  | .tup es => es ++ (shrinkAt es shrinkExpr).map .tup
  | .notE e => [e] ++ (shrinkExpr e).map .notE
  | .doB steps final =>
    [final] ++
    (List.range steps.length).map (fun i => Expr.doB (steps.eraseIdx i) final) ++
    (shrinkExpr final).map (.doB steps ·) ++
    (shrinkAt steps shrinkStep).map (.doB · final)
end

def shrinkSig : Option (Ty × Eff) → List (Option (Ty × Eff))
  | some (_, .pure) => [none]
  | _ => []

def shrinkDecl : Decl → List Decl
  | .fn n ps sg b =>
    (shrinkExpr b).map (.fn n ps sg ·) ++
    (shrinkSig sg).map (.fn n ps · b) ++
    (shrinkAt ps fun (x, t) => if t.isSome then [(x, none)] else []).map (.fn n · sg b)
  | .letD n a i =>
    (if a.isSome then [Decl.letD n none i] else []) ++ (shrinkExpr i).map (.letD n a ·)
  | .mainFn sg b => (shrinkExpr b).map (.mainFn sg ·)

def shrinkProgram (p : Program) : List Program :=
  let ds := p.decls
  (List.range ds.length).map (fun i => Program.mk (ds.eraseIdx i)) ++
  (shrinkAt ds shrinkDecl).map Program.mk

end SproutCore
