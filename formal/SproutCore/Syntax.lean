/-!
# Sprout Core syntax (docs/lean-model-v0.md §4, §9)

Types, terms and declarations of stages 1 and 2: Int/Bool/String/Unit, tuples, arrows carrying an
effect in {pure, IO}, user ADTs, lambdas, application, local `let`, `if`, `do` blocks, the `!{IO}`
primitive `print_str`, constructors and `match`. No linearity, no effect variables, no records,
`wrap` or existentials.
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
  /-- A user type applied to its arguments: `Maybe Int` is `.adt "Maybe" [.int]`. -/
  | adt (name : String) (args : List Ty)
  deriving Repr, Inhabited

/-! `deriving BEq` on a nested inductive is an `opaque` constant: no theorem can say what it
returns. The checker's `genSlot` and `main` signature test compare types with `==`, so soundness
needs a `BEq` it can unfold. This one is written out and proved lawful. -/

mutual
def Ty.beq : Ty → Ty → Bool
  | .named a, .named b => a == b
  | .mv a, .mv b => a == b
  | .skolem a, .skolem b => a == b
  | .gen a, .gen b => a == b
  | .int, .int => true
  | .bool, .bool => true
  | .str, .str => true
  | .unit, .unit => true
  | .tup as, .tup bs => Ty.beqList as bs
  | .arr ps r e, .arr qs s f => Ty.beqList ps qs && Ty.beq r s && e == f
  | .adt n as, .adt m bs => n == m && Ty.beqList as bs
  | _, _ => false

def Ty.beqList : List Ty → List Ty → Bool
  | [], [] => true
  | a :: as, b :: bs => Ty.beq a b && Ty.beqList as bs
  | _, _ => false
end

instance : BEq Ty := ⟨Ty.beq⟩

theorem Eff.eq_of_beq {e f : Eff} (h : (e == f) = true) : e = f := by
  cases e <;> cases f <;> first | rfl | (exfalso; revert h; decide)

mutual
theorem Ty.eq_of_beq (a b : Ty) (h : Ty.beq a b = true) : a = b := by
  cases a <;> cases b <;> first
    | (exfalso; revert h; simp [Ty.beq]; done)
    | (simp [Ty.beq] at h; simp [h]; done)
    | skip
  · rename_i as bs
    simp only [Ty.beq] at h
    rw [Ty.eq_of_beqList as bs h]
  · rename_i ps r e qs s f
    simp only [Ty.beq, Bool.and_eq_true] at h
    obtain ⟨⟨h1, h2⟩, h3⟩ := h
    rw [Ty.eq_of_beqList ps qs h1, Ty.eq_of_beq r s h2, Eff.eq_of_beq h3]
  · rename_i n as m bs
    simp only [Ty.beq, Bool.and_eq_true] at h
    obtain ⟨h1, h2⟩ := h
    rw [beq_iff_eq.mp h1, Ty.eq_of_beqList as bs h2]

theorem Ty.eq_of_beqList (as bs : List Ty) (h : Ty.beqList as bs = true) : as = bs := by
  cases as <;> cases bs <;> first
    | (exfalso; revert h; simp [Ty.beqList]; done)
    | rfl
    | skip
  · rename_i a as b bs
    simp only [Ty.beqList, Bool.and_eq_true] at h
    rw [Ty.eq_of_beq a b h.1, Ty.eq_of_beqList as bs h.2]
end

mutual
theorem Ty.beq_refl (a : Ty) : Ty.beq a a = true := by
  cases a <;> first | (simp [Ty.beq]; done) | skip
  · rename_i as
    simp only [Ty.beq]
    exact Ty.beqList_refl as
  · rename_i ps r e
    simp only [Ty.beq, Bool.and_eq_true]
    refine ⟨⟨Ty.beqList_refl ps, Ty.beq_refl r⟩, ?_⟩
    cases e <;> decide
  · rename_i n as
    simp only [Ty.beq, Bool.and_eq_true]
    exact ⟨beq_self_eq_true n, Ty.beqList_refl as⟩

theorem Ty.beqList_refl (as : List Ty) : Ty.beqList as as = true := by
  cases as <;> first | (simp [Ty.beqList]; done) | skip
  · rename_i a as
    simp only [Ty.beqList, Bool.and_eq_true]
    exact ⟨Ty.beq_refl a, Ty.beqList_refl as⟩
end

instance : LawfulBEq Ty where
  eq_of_beq h := Ty.eq_of_beq _ _ h
  rfl := Ty.beq_refl _

/-- Patterns (spec §5.5). `int` is a non-negative literal, as `Expr.int` is. -/
inductive Pat where
  | wild
  | var (x : String)
  | int (n : Nat)
  | bool (b : Bool)
  | str (s : String)
  | unit
  | tup (ps : List Pat)
  | ctor (name : String) (args : List Pat)
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
  /-- A constructor named in value position: nullary, a value; else a function value (§5.3).
  A saturated call is `.app (.ctor c) args`. -/
  | ctor (name : String)
  | matchE (scrut : Expr) (arms : List (Pat × Expr))
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
  /-- `type name params = | c fields | …`; a field's type variables are `.named` params. -/
  | typeD (name : String) (params : List String) (ctors : List (String × List Ty))
  deriving BEq, Repr, Inhabited

/-- A program is its declarations in source order. The printer keeps that order. -/
structure Program where
  decls : List Decl
  deriving BEq, Repr, Inhabited

end SproutCore
