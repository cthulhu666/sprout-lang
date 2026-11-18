import SproutCore.Syntax

/-!
# Witnesses for the oracle's allowlist

Each allowlisted gap (`formal/oracle/ALLOWLIST`, keyed by id) has one fixed program that shows it.
The oracle runs every witness through both sides on every run and calls an entry stale exactly
when its witness no longer disagrees with the entry's shape, so a random sample that happens to
miss a gap never makes its entry stale.
-/

namespace SproutCore

/-- `fn f() -> Unit !{IO} = let y = print_str("m") in ()`: an effectful `let … in` right-hand
side (spec §5.2.1). -/
def witnessLetRhsIO : Program :=
  ⟨[.fn "f" [] (some (.unit, .io))
    (.letIn "y" (.app (.var "print_str") [.str "m"]) .unit)]⟩

/-- `let l = if false then (\x -> "") else (\y -> "")`: a non-value top-level `let` whose type
keeps a free variable, which the compiler's value restriction rejects. -/
def witnessValueRestriction : Program :=
  ⟨[.letD "l" none (.ite (.bool false) (.lam "x" none (.str "")) (.lam "y" none (.str "")))]⟩

/-- `let f = let v = 0 in \(x: Bool) -> x` then `let g = f(false)`: a pure top-level `let`
calling a non-generalised function value. -/
def witnessNonGenCall : Program :=
  ⟨[.letD "f" none (.letIn "v" (.int 0) (.lam "x" (some .bool) (.var "x"))),
    .letD "g" none (.app (.var "f") [.bool false])]⟩

/-- Witness by allowlist id. -/
def witnesses : List (String × Program) :=
  [("let-rhs-io", witnessLetRhsIO),
   ("value-restriction", witnessValueRestriction),
   ("nongen-call", witnessNonGenCall)]

end SproutCore
