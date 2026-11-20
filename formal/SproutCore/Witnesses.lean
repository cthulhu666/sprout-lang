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

/-- `let l = let v = false in \(x: Int) -> v`, `fn use(p: Int -> Bool !{IO}) -> Bool = true`,
`fn both() -> Bool = if use(l) then l(0) else false`: passing a non-generalised pure function value
to an `!{IO}` slot, then calling it. -/
def witnessNonGenSlot : Program :=
  ⟨[.letD "l" none (.letIn "v" (.bool false) (.lam "x" (some .int) (.var "v"))),
    .fn "use" [("p", some (.arr [.int] .bool .io))] (some (.bool, .pure)) (.bool true),
    .fn "both" [] (some (.bool, .pure))
      (.ite (.app (.var "use") [.var "l"]) (.app (.var "l") [.int 0]) (.bool false))]⟩

/-- `fn use(p: String -> String !{IO}) -> Unit !{IO} = ()`, then in an `!{IO}` function
`use(if c then (if c then (\x -> x) else (\y -> y)) else p)` with `p: String -> String !{IO}`:
a peer join whose first branch is itself a join, against an `!{IO}` function. -/
def witnessJoinOrder : Program :=
  ⟨[.fn "use" [("p", some (.arr [.str] .str .io))] (some (.unit, .io)) .unit,
    .fn "g" [("p", some (.arr [.str] .str .io))] (some (.unit, .io))
      (.app (.var "use") [.ite (.bool false)
        (.ite (.bool false) (.lam "x" none (.var "x")) (.lam "y" none (.var "y")))
        (.var "p")])]⟩

/-- Witness by allowlist id. -/
def witnesses : List (String × Program) :=
  [("let-rhs-io", witnessLetRhsIO),
   ("value-restriction", witnessValueRestriction),
   ("nongen-call", witnessNonGenCall),
   ("nongen-slot", witnessNonGenSlot),
   ("join-order", witnessJoinOrder)]

end SproutCore
