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

/-- `fn f(x: t) -> Int = match x with arms`. -/
def matchOn (t : Ty) (arms : List (Pat × Expr)) : Decl :=
  .fn "f" [("x", some t)] (some (.int, .pure)) (.matchE (.var "x") arms)

/-- `| true -> 1 | false -> 2 | _ -> 3`: a branch after both `Bool` literals. -/
def witnessUnreachableBool : Program :=
  ⟨[matchOn .bool [(.bool true, .int 1), (.bool false, .int 2), (.wild, .int 3)]]⟩

/-- `| () -> 1 | _ -> 2`: a branch after the unit pattern. -/
def witnessUnreachableUnit : Program := ⟨[matchOn .unit [(.unit, .int 1), (.wild, .int 2)]]⟩

/-- `| (a, b) -> 1 | (c, d) -> 2` on `(Int, Int)`: a branch after an all-catch-all tuple. -/
def witnessUnreachableTuple : Program := ⟨[matchOn (.tup [.int, .int])
  [(.tup [.var "a", .var "b"], .int 1), (.tup [.var "c", .var "d"], .int 2)]]⟩

/-- `type U = | C Unit | D`, `| C () -> 1 | C _ -> 2 | D -> 3`: `C ()` covers `C` completely. -/
def witnessCtorRepeat : Program := ⟨[.typeD "U" [] [("C", [.unit]), ("D", [])],
  matchOn (.adt "U" []) [(.ctor "C" [.unit], .int 1), (.ctor "C" [.wild], .int 2),
    (.ctor "D" [], .int 3)]]⟩

/-- `type U = | C (Int, Int) | D`, `| C (a, b) -> 1 | D -> 2 | _ -> 3`: the `_` comes after every
constructor is covered. -/
def witnessAfterCtors : Program := ⟨[.typeD "U" [] [("C", [.tup [.int, .int]]), ("D", [])],
  matchOn (.adt "U" []) [(.ctor "C" [.tup [.var "a", .var "b"]], .int 1), (.ctor "D" [], .int 2),
    (.wild, .int 3)]]⟩

/-- `type T = | C a`: a field names a type variable the declaration does not bind. -/
def witnessFreeTyVar : Program := ⟨[.typeD "T" [] [("C", [.named "a"])]]⟩

/-- `type P a = | P0 | P1 a`, `type T = | C (P Int Int)`: a field applies a type to too many
arguments. -/
def witnessFieldArity : Program := ⟨[.typeD "P" ["a"] [("P0", []), ("P1", [.named "a"])],
  .typeD "T" [] [("C", [.adt "P" [.int, .int]])]]⟩

/-- `fn f(c: Int, p: Int -> Int !{IO}) -> Int -> Int !{IO} = match c with | 0 -> \x -> x |
1 -> \y -> y | _ -> p`: a match joining two pure lambdas, then an `!{IO}` function. -/
def witnessJoinOrderMatch : Program :=
  ⟨[.fn "f" [("c", some .int), ("p", some (.arr [.int] .int .io))]
    (some (.arr [.int] .int .io, .pure))
    (.matchE (.var "c") [(.int 0, .lam "x" none (.var "x")), (.int 1, .lam "y" none (.var "y")),
      (.wild, .var "p")])]⟩

/-- Witness by allowlist id. -/
def witnesses : List (String × Program) :=
  [("let-rhs-io", witnessLetRhsIO),
   ("value-restriction", witnessValueRestriction),
   ("nongen-call", witnessNonGenCall),
   ("nongen-slot", witnessNonGenSlot),
   ("join-order", witnessJoinOrder),
   ("join-order-match", witnessJoinOrderMatch),
   ("unreachable-bool", witnessUnreachableBool),
   ("unreachable-unit", witnessUnreachableUnit),
   ("unreachable-tuple", witnessUnreachableTuple),
   ("unreachable-ctor-repeat", witnessCtorRepeat),
   ("unreachable-after-ctors", witnessAfterCtors),
   ("free-tyvar", witnessFreeTyVar),
   ("field-type-arity", witnessFieldArity)]

end SproutCore
