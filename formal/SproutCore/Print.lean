import SproutCore.Syntax

/-!
# Printer: Sprout Core to Sprout surface syntax

Layout follows spec §2.1: every expression is one line except a `do` block, which is a block of
steps one indentation level deeper. A `do` that is not a whole function body is parenthesised, so
the lines inside it are not compared against the enclosing block. The printer keeps declaration
order (the generator puts `let`s first). It is total over Core.
-/

namespace SproutCore

/-- `bug` plants a printer defect (the oracle self-test): tuples lose their closing paren. -/
structure PrintOpts where
  bug : Bool := false

def escapeStr (s : String) : String :=
  s.foldl (fun acc c =>
    acc ++ (match c with
      | '"' => "\\\""
      | '\\' => "\\\\"
      | '\n' => "\\n"
      | c => c.toString)) ""

/-- A type. `top` says an arrow needs no parentheses here. -/
partial def pTy (top : Bool) : Ty → String
  | .named s => s
  | .mv n => s!"?{n}"
  | .skolem n => s!"!{n}"
  | .gen n => s!"#{n}"
  | .int => "Int"
  | .bool => "Bool"
  | .str => "String"
  | .unit => "Unit"
  | .tup ts => "(" ++ ", ".intercalate (ts.map (pTy false)) ++ ")"
  | .arr ps r e =>
    -- `()` is only an empty parameter list, spelled directly before `->` (spec §7 rule 9)
    let ps' := if ps.isEmpty then "()" else " -> ".intercalate (ps.map (pTy false))
    let r' := match r with
      | .arr .. => pTy false r
      | _ => pTy true r
    let s := ps' ++ " -> " ++ r' ++ (if e == .io then " !{IO}" else "")
    if top then s else "(" ++ s ++ ")"

def pad (n : Nat) : String := "".pushn ' ' n

def isAtom : Expr → Bool
  | .var _ | .int _ | .bool _ | .str _ | .unit | .tup _ | .app .. => true
  | _ => false

mutual
/-- An expression on a line indented `ind`. -/
partial def pExpr (o : PrintOpts) (ind : Nat) : Expr → String
  | .var x => x
  | .int n => toString n
  | .bool b => if b then "true" else "false"
  | .str s => "\"" ++ escapeStr s ++ "\""
  | .unit => "()"
  | .lam x ann body =>
    let head := match ann with
      | some t => s!"\\({x}: {pTy true t})"
      | none => s!"\\{x}"
    head ++ " -> " ++ pExpr o ind body
  | .app f args =>
    let callee := if isAtom f then pExpr o ind f else "(" ++ pExpr o ind f ++ ")"
    callee ++ "(" ++ ", ".intercalate (args.map (pExpr o ind)) ++ ")"
  | .letIn x rhs body =>
    s!"let {x} = " ++ pExpr o ind rhs ++ " in " ++ pExpr o ind body
  | .ite c t e =>
    "if " ++ pAtomish o ind c ++ " then " ++ pAtomish o ind t ++ " else " ++ pAtomish o ind e
  | .tup es =>
    "(" ++ ", ".intercalate (es.map (pExpr o ind)) ++ (if o.bug then "" else ")")
  | .notE e => "!" ++ pAtomish o ind e
  | .doB steps final =>
    "(" ++ pDo o ind steps final ++ ")"

/-- A subexpression that must not bleed into its context. -/
partial def pAtomish (o : PrintOpts) (ind : Nat) (e : Expr) : String :=
  match e with
  | .lam .. | .ite .. | .letIn .. | .notE .. => "(" ++ pExpr o ind e ++ ")"
  | _ => pExpr o ind e

partial def pStep (o : PrintOpts) (ind : Nat) : Step → String
  | .letS x rhs => pad ind ++ s!"let {x} = " ++ pExpr o ind rhs
  | .exprS e => pad ind ++ pExpr o ind e

/-- `do` and its steps, one indentation level (2) deeper than `ind`. -/
partial def pDo (o : PrintOpts) (ind : Nat) (steps : List Step) (final : Expr) : String :=
  let inner := ind + 2
  let lines := steps.map (pStep o inner) ++ [pad inner ++ pExpr o inner final]
  "do\n" ++ "\n".intercalate lines
end

/-- A function body: a `do` block is not parenthesised here. -/
def pBody (o : PrintOpts) (ind : Nat) : Expr → String
  | .doB steps final => pDo o ind steps final
  | e => pExpr o ind e

def pSig : Option (Ty × Eff) → String
  | none => ""
  | some (r, e) => " -> " ++ pTy true r ++ (if e == .io then " !{IO}" else "")

def pDecl (o : PrintOpts) : Decl → String
  | .fn name params sg body =>
    let ps := ", ".intercalate (params.map fun (x, t) =>
      match t with
      | some t => s!"{x}: {pTy true t}"
      | none => x)
    s!"fn {name}({ps})" ++ pSig sg ++ " =\n  " ++ pBody o 2 body
  | .letD name ann init =>
    let a := match ann with
      | some t => s!" : {pTy true t}"
      | none => ""
    s!"let {name}{a} = " ++ pExpr o 0 init
  | .mainFn sg body =>
    "fn main()" ++ pSig sg ++ " =\n  " ++ pBody o 2 body

def printProgram (o : PrintOpts := {}) (p : Program) : String :=
  "\n\n".intercalate (p.decls.map (pDecl o)) ++ "\n"

end SproutCore
