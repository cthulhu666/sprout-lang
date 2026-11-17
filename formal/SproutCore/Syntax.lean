/-!
# Sprout Core, stage 1 syntax (docs/lean-model-v0.md §4, §9)

Types, terms and declarations of the stage-1 fragment: Int/Bool/String/Unit, tuples, arrows
carrying an effect in {pure, IO}, lambdas, application, local `let`, `if`, `do` blocks and the
`!{IO}` primitive `print_str`. No ADTs, no linearity, no effect variables.
-/

namespace SproutCore

/-- Effects form the order `pure < IO` (spec §7 rule 8). -/
inductive Eff where
  | pure
  | io
  deriving BEq, Repr, Inhabited

def Eff.le : Eff → Eff → Bool
  | .io, .pure => false
  | _, _ => true

def Eff.join : Eff → Eff → Eff
  | .pure, .pure => .pure
  | _, _ => .io

def Eff.meet : Eff → Eff → Eff
  | .io, .io => .io
  | _, _ => .pure

/-- Types. `named` is a written type variable, only in annotations; `mv`, `skolem` and `gen`
are internal to the checker. An arrow is n-ary and its effect sits on the innermost arrow, so the
result of an arrow is not an arrow unless the arrow's own effect is pure and the result was not
yet resolved (spec §7 rule 9: "an annotation attaches to the innermost arrow"). -/
inductive Ty where
  | named (s : String)
  | mv (n : Nat)
  | skolem (n : Nat)
  | gen (n : Nat)
  | int
  | bool
  | str
  | unit
  | tup (ts : List Ty)
  | arr (ps : List Ty) (r : Ty) (e : Eff)
  deriving BEq, Repr, Inhabited

mutual

/-- Terms. `print_str` is the prelude's `String -> Unit !{IO}`, reached as an ordinary `var`. -/
inductive Expr where
  | var (x : String)
  | int (n : Nat)
  | bool (b : Bool)
  | str (s : String)
  | unit
  | lam (x : String) (ann : Option Ty) (body : Expr)
  | app (f : Expr) (args : List Expr)
  | letIn (x : String) (rhs body : Expr)
  | ite (c t e : Expr)
  | tup (es : List Expr)
  | notE (e : Expr)
  | doB (steps : List Step) (final : Expr)
  deriving BEq, Repr, Inhabited

/-- A non-final `do` step: a `let` or a bare expression whose value is discarded. -/
inductive Step where
  | letS (x : String) (rhs : Expr)
  | exprS (e : Expr)
  deriving BEq, Repr, Inhabited

end

/-- Declarations. A `fn` with `sig = none` omits its return type and is pure; with
`sig = some (r, e)` it writes `-> r` and `!{IO}` when `e = .io`. `main` is the zero-parameter
entry point and is never referenced. -/
inductive Decl where
  | fn (name : String) (params : List (String × Option Ty)) (sig : Option (Ty × Eff)) (body : Expr)
  | letD (name : String) (ann : Option Ty) (init : Expr)
  | mainFn (sig : Option (Ty × Eff)) (body : Expr)
  deriving BEq, Repr, Inhabited

/-- A program is its declarations in source order. The printer keeps that order. -/
structure Program where
  decls : List Decl
  deriving BEq, Repr, Inhabited

end SproutCore
