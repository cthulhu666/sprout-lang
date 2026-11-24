import SproutCore.Tests

/-!
# Build-time regression checks: ADTs and `match` (docs/lean-model-v0.md §9, 2a)

Each case pins the model's verdict and rejection key. A `-- compiler:` line, or the case's doc
comment where the two differ, gives the compiler's verdict on the printed program
(`compile_driver_bin_stage1 --phase check`). Where they differ the model keeps the spec's reading;
the oracle's part of stage 2a triages those.
-/

namespace SproutCore.TestsAdt

open SproutCore.Tests

/-- `type Maybe a = | Nothing | Just a`. -/
def maybeD : Decl := .typeD "Maybe" ["a"] [("Nothing", []), ("Just", [.named "a"])]

def maybeOf (t : Ty) : Ty := .adt "Maybe" [t]

/-- `type OZ = | O | Z`. -/
def ozD : Decl := .typeD "OZ" [] [("O", []), ("Z", [])]

def oz : Ty := .adt "OZ" []

/-- `type Color = | Red | Green | Blue`. -/
def colorD : Decl := .typeD "Color" [] [("Red", []), ("Green", []), ("Blue", [])]

/-- `fn f(x: t) -> Int = match x with arms`. -/
def matchFn (t : Ty) (arms : List (Pat × Expr)) : Decl :=
  .fn "f" [("x", some t)] (some (.int, .pure)) (.matchE (.var "x") arms)

def pJust (p : Pat) : Pat := .ctor "Just" [p]
def pNothing : Pat := .ctor "Nothing" []

/-! ### The spec's examples (§10.5, §10.6, §10.11, §10.12) -/

/-- §10.5: `fn with_default(m: Maybe Int, d: Int) -> Int = match m with | Just x -> x |
Nothing -> d`. -/
def ex10_5 : Program := ⟨[maybeD,
  .fn "with_default" [("m", some (maybeOf .int)), ("d", some .int)] (some (.int, .pure))
    (.matchE (.var "m") [(pJust (.var "x"), .var "x"), (pNothing, .var "d")])]⟩

-- compiler: accepts
#guard accepts ex10_5

/-- §10.6: `fn swap(pair: (Int, Bool)) -> (Bool, Int) = match pair with | (x, y) -> (y, x)`. -/
def ex10_6 : Program := ⟨[
  .fn "swap" [("pair", some (.tup [.int, .bool]))] (some (.tup [.bool, .int], .pure))
    (.matchE (.var "pair") [(.tup [.var "x", .var "y"], .tup [.var "y", .var "x"])])]⟩

-- compiler: accepts
#guard accepts ex10_6

/-- §10.6 (the second one): `fn map(f: a -> b, m: Maybe a) -> Maybe b = match m with
| Just x -> Just(f(x)) | Nothing -> Nothing`. -/
def ex10_6map : Program := ⟨[maybeD,
  .fn "map" [("f", some (.arr [.named "a"] (.named "b") .pure)), ("m", some (maybeOf (.named "a")))]
    (some (maybeOf (.named "b"), .pure))
    (.matchE (.var "m") [
      (pJust (.var "x"), .app (.ctor "Just") [.app (.var "f") [.var "x"]]),
      (pNothing, .ctor "Nothing")])]⟩

-- compiler: accepts
#guard accepts ex10_6map

/-- §10.11: `fn bad(m: Maybe Int) -> Int = match m with | Just x -> x`. -/
def ex10_11 : Program := ⟨[maybeD, matchFn (maybeOf .int) [(pJust (.var "x"), .var "x")]]⟩

-- compiler: rejects
#guard rejectKey ex10_11 == "non-exhaustive"

/-- §10.12: `| Just x -> x | Nothing -> 0 | _ -> 1`: the `_` comes after every constructor. -/
def ex10_12 : Program := ⟨[maybeD, matchFn (maybeOf .int)
  [(pJust (.var "x"), .var "x"), (pNothing, .int 0), (.wild, .int 1)]]⟩

-- compiler: rejects
#guard rejectKey ex10_12 == "unreachable"

/-! ### Exhaustiveness (spec §5.5) -/

/-- A constructor missing: `match x with | Red -> 1 | Green -> 2`. -/
def missingCtor : Program := ⟨[colorD, matchFn (.adt "Color" [])
  [(.ctor "Red" [], .int 1), (.ctor "Green" [], .int 2)]]⟩

-- compiler: rejects
#guard rejectKey missingCtor == "non-exhaustive"

/-- `Bool` without `false`: `match x with | true -> 1`. -/
def boolNoFalse : Program := ⟨[matchFn .bool [(.bool true, .int 1)]]⟩

-- compiler: rejects
#guard rejectKey boolNoFalse == "non-exhaustive"

/-- An `Int` column with no catch-all: `match x with | Just 1 -> 1 | Nothing -> 0`. -/
def intColumn : Program := ⟨[maybeD, matchFn (maybeOf .int)
  [(pJust (.int 1), .int 1), (pNothing, .int 0)]]⟩

-- compiler: rejects
#guard rejectKey intColumn == "non-exhaustive"

/-- Nested: `type W = | W OZ`, `match x with | W O -> 1 | W Z -> 2`. -/
def nestedOZ : Program := ⟨[ozD, .typeD "W" [] [("W", [oz])],
  matchFn (.adt "W" []) [(.ctor "W" [.ctor "O" []], .int 1), (.ctor "W" [.ctor "Z" []], .int 2)]]⟩

-- compiler: accepts
#guard accepts nestedOZ

/-- `match x with | (O, O) -> 1 | (Z, Z) -> 2 | _ -> 3` on `(OZ, OZ)`: a tuple is not an ADT, so
the trailing `_` is not flagged. -/
def tupleTrailingWild : Program := ⟨[ozD, matchFn (.tup [oz, oz])
  [(.tup [.ctor "O" [], .ctor "O" []], .int 1), (.tup [.ctor "Z" [], .ctor "Z" []], .int 2),
    (.wild, .int 3)]]⟩

-- compiler: accepts
#guard accepts tupleTrailingWild

/-- The per-column gap (spec §5.5): `(true, true) | (false, false)` on `(Bool, Bool)`. -/
def boolPairGap : Program := ⟨[matchFn (.tup [.bool, .bool])
  [(.tup [.bool true, .bool true], .int 1), (.tup [.bool false, .bool false], .int 2)]]⟩

-- compiler: accepts
#guard accepts boolPairGap

/-- F9 (type-system-review-2026-08-13): `(Nothing, _) | (_, Nothing)` on
`(Maybe Int, Maybe Int)`. -/
def maybePairGap : Program := ⟨[maybeD, matchFn (.tup [maybeOf .int, maybeOf .int])
  [(.tup [pNothing, .wild], .int 1), (.tup [.wild, pNothing], .int 2)]]⟩

-- compiler: accepts
#guard accepts maybePairGap

/-- Uninhabited: `type T = | C T`, `match x with | C _ -> 1`. -/
def uninhabited : Program := ⟨[.typeD "T" [] [("C", [.adt "T" []])],
  matchFn (.adt "T" []) [(.ctor "C" [.wild], .int 1)]]⟩

-- compiler: accepts
#guard accepts uninhabited

/-- `Unit`: `match x with | () -> 1`. -/
def unitMatch : Program := ⟨[matchFn .unit [(.unit, .int 1)]]⟩

-- compiler: accepts
#guard accepts unitMatch

/-! ### Unreachable branches (spec §5.5) -/

/-- After `_`: `match x with | _ -> 1 | Nothing -> 2`. -/
def afterWild : Program :=
  ⟨[maybeD, matchFn (maybeOf .int) [(.wild, .int 1), (pNothing, .int 2)]]⟩

-- compiler: rejects
#guard rejectKey afterWild == "unreachable"

/-- After a variable: `match x with | y -> 1 | 2 -> 2`. -/
def afterVar : Program := ⟨[matchFn .int [(.var "y", .int 1), (.int 2, .int 2)]]⟩

-- compiler: rejects
#guard rejectKey afterVar == "unreachable"

/-- After every constructor: `match x with | Red -> 1 | Green -> 2 | Blue -> 3 | c -> 4`. -/
def afterAllCtors : Program := ⟨[colorD, matchFn (.adt "Color" [])
  [(.ctor "Red" [], .int 1), (.ctor "Green" [], .int 2), (.ctor "Blue" [], .int 3),
    (.var "c", .int 4)]]⟩

-- compiler: rejects
#guard rejectKey afterAllCtors == "unreachable"

/-- A repeated literal: `match x with | 1 -> 1 | 1 -> 2 | _ -> 3`. -/
def repeatedLit : Program :=
  ⟨[matchFn .int [(.int 1, .int 1), (.int 1, .int 2), (.wild, .int 3)]]⟩

-- compiler: rejects
#guard rejectKey repeatedLit == "unreachable"

/-- A repeated unit pattern: `match x with | () -> 1 | () -> 2`. -/
def repeatedUnit : Program := ⟨[matchFn .unit [(.unit, .int 1), (.unit, .int 2)]]⟩

-- compiler: rejects
#guard rejectKey repeatedUnit == "unreachable"

/-- A constructor an earlier branch covers completely: `| Just _ -> 1 | Just 2 -> 2 |
Nothing -> 0`. -/
def repeatedCtor : Program := ⟨[maybeD, matchFn (maybeOf .int)
  [(pJust .wild, .int 1), (pJust (.int 2), .int 2), (pNothing, .int 0)]]⟩

-- compiler: rejects
#guard rejectKey repeatedCtor == "unreachable"

/-- A repeat of a constructor the earlier branch does not cover completely is reachable:
`| Just 1 -> 1 | Just _ -> 2 | Nothing -> 0`. -/
def partialCtor : Program := ⟨[maybeD, matchFn (maybeOf .int)
  [(pJust (.int 1), .int 1), (pJust .wild, .int 2), (pNothing, .int 0)]]⟩

-- compiler: accepts
#guard accepts partialCtor

/-- `true | false | _`: `Bool` is not an ADT, so none of the four cases flags the `_`.
Compiler: rejects ("Unreachable match branch"); it treats `Bool` like an ADT. The model follows
the spec's four cases; a spec gap, allowlisted as `unreachable-bool`. -/
def boolThenWild : Program := ⟨[matchFn .bool
  [(.bool true, .int 1), (.bool false, .int 2), (.wild, .int 3)]]⟩

#guard accepts boolThenWild

/-- `(a, b) | (c, d)`: a tuple pattern is no case of the four. Compiler: rejects ("Unreachable
match branch"); it flags a branch after an all-catch-all tuple. The model follows the spec; a spec
gap, allowlisted as `unreachable-tuple`. -/
def tupleRepeat : Program := ⟨[matchFn (.tup [.int, .int])
  [(.tup [.var "a", .var "b"], .int 1), (.tup [.var "c", .var "d"], .int 2)]]⟩

#guard accepts tupleRepeat

/-- `() | _`: the same for `Unit`. Compiler: rejects; allowlisted as `unreachable-unit`. -/
def unitThenWild : Program := ⟨[matchFn .unit [(.unit, .int 1), (.wild, .int 2)]]⟩

#guard accepts unitThenWild

/-- `type U = | C Unit | D`, `| C () -> 1 | C _ -> 2 | D -> 3`: `C ()` covers `C` completely, so
`C _` repeats it. Compiler: accepts, since it counts only an all-catch-all `C` branch; a compiler
bug, allowlisted as `unreachable-ctor-repeat`. -/
def ctorRepeatUnit : Program := ⟨[.typeD "U" [] [("C", [.unit]), ("D", [])],
  matchFn (.adt "U" []) [(.ctor "C" [.unit], .int 1), (.ctor "C" [.wild], .int 2),
    (.ctor "D" [], .int 3)]]⟩

#guard rejectKey ctorRepeatUnit == "unreachable"

/-- `(a, a)` binds `a` twice; the spec says nothing about it, so the model reads it as two
bindings, the later one in scope. Compiler: accepts. -/
def patBindsTwice : Program :=
  ⟨[matchFn (.tup [.int, .int]) [(.tup [.var "a", .var "a"], .var "a")]]⟩

#guard accepts patBindsTwice

/-- A reachable `_` after a per-column-covered constructor is not flagged:
`type P = | P (Maybe Int) (Maybe Int)`, `| P Nothing _ | P _ Nothing | _` (`P (Just 1) (Just 2)`
reaches the `_`). Compiler: accepts. -/
def pairCtorGap : Program := ⟨[maybeD, .typeD "P" [] [("P", [maybeOf .int, maybeOf .int])],
  matchFn (.adt "P" []) [(.ctor "P" [pNothing, .wild], .int 1),
    (.ctor "P" [.wild, pNothing], .int 2), (.wild, .int 3)]]⟩

#guard accepts pairCtorGap

/-! ### Constructors (spec §5.3, §7 rule 7) -/

/-- An unknown constructor in a pattern: `| Jus x -> x | Nothing -> 0`. -/
def unknownCtorPat : Program := ⟨[maybeD, matchFn (maybeOf .int)
  [(.ctor "Jus" [.var "y"], .var "y"), (pNothing, .int 0)]]⟩

-- compiler: rejects
#guard rejectKey unknownCtorPat == "unknown-ctor"

/-- An unknown constructor in an expression: `fn g() -> Maybe Int = Jus(1)`. -/
def unknownCtorExpr : Program := ⟨[maybeD,
  .fn "g" [] (some (maybeOf .int, .pure)) (.app (.ctor "Jus") [.int 1])]⟩

-- compiler: rejects
#guard rejectKey unknownCtorExpr == "unknown-ctor"

/-- A constructor call with too many arguments: `Just(1, 2)`. -/
def ctorCallArity : Program := ⟨[maybeD,
  .fn "g" [] (some (maybeOf .int, .pure)) (.app (.ctor "Just") [.int 1, .int 2])]⟩

-- compiler: rejects
#guard rejectKey ctorCallArity == "arity"

/-- A constructor pattern with too many sub-patterns: `| Just a b -> a | Nothing -> 0`. The
compiler's message is "Tuple pattern arity mismatch". -/
def ctorPatArity : Program := ⟨[maybeD, matchFn (maybeOf .int)
  [(.ctor "Just" [.var "a", .var "b"], .var "a"), (pNothing, .int 0)]]⟩

-- compiler: rejects
#guard rejectKey ctorPatArity == "ctor-arity"

/-- A saturated call types as the declared result, a bare constructor is a function value:
`fn g(h: Int -> Maybe Int) -> Maybe Int = h(1)`, `fn k() -> Maybe Int = g(Just)`. -/
def bareCtor : Program := ⟨[maybeD,
  .fn "g" [("h", some (.arr [.int] (maybeOf .int) .pure))] (some (maybeOf .int, .pure))
    (.app (.var "h") [.int 1]),
  .fn "k" [] (some (maybeOf .int, .pure)) (.app (.var "g") [.ctor "Just"])]⟩

-- compiler: accepts
#guard accepts bareCtor

/-- A nullary constructor is a plain value, not a function: `Nothing()`. -/
def nullaryCall : Program := ⟨[maybeD,
  .fn "g" [] (some (maybeOf .int, .pure)) (.app (.ctor "Nothing") [])]⟩

-- compiler: rejects
#guard rejectKey nullaryCall == "mismatch"

/-- A nullary constructor is a value of the declared type: `fn g() -> Maybe Int = Nothing`. -/
def nullaryValue : Program :=
  ⟨[maybeD, .fn "g" [] (some (maybeOf .int, .pure)) (.ctor "Nothing")]⟩

-- compiler: accepts
#guard accepts nullaryValue

/-- A constructor builds its declared type only: `fn g() -> Maybe Int = Just(true)`. -/
def ctorArgMismatch : Program := ⟨[maybeD,
  .fn "g" [] (some (maybeOf .int, .pure)) (.app (.ctor "Just") [.bool true])]⟩

-- compiler: rejects
#guard rejectKey ctorArgMismatch == "mismatch"

/-! ### Patterns and match typing (spec §7 rules 5, 6, 12) -/

/-- A pattern of the wrong type: `match x with | 1 -> 1 | _ -> 2` on `Maybe Int`. -/
def patMismatch : Program :=
  ⟨[maybeD, matchFn (maybeOf .int) [(.int 1, .int 1), (.wild, .int 2)]]⟩

-- compiler: rejects
#guard rejectKey patMismatch == "mismatch"

/-- Arms of different types: `match x with | true -> 1 | false -> "a"`. -/
def armMismatch : Program := ⟨[.fn "f" [("x", some .bool)] none
  (.matchE (.var "x") [(.bool true, .int 1), (.bool false, .str "a")])]⟩

-- compiler: rejects
#guard rejectKey armMismatch == "mismatch"

/-- A pattern variable is scoped to its arm: `| Just y -> y | Nothing -> y`. -/
def patScope : Program := ⟨[maybeD, matchFn (maybeOf .int)
  [(pJust (.var "y"), .var "y"), (pNothing, .var "y")]]⟩

-- compiler: rejects
#guard rejectKey patScope == "unknown-var"

/-- An arm's effect is the match's: a pure `fn` whose arm prints. -/
def armIO : Program := ⟨[.fn "f" [("x", some .bool)] (some (.unit, .pure))
  (.matchE (.var "x") [(.bool true, .app (.var "print_str") [.str "a"]), (.bool false, .unit)])]⟩

-- compiler: rejects
#guard rejectKey armIO == "io-in-pure"

/-- An inferred scrutinee: `fn f(x) = match x with | Just 1 -> 1 | _ -> 0`. -/
def inferredScrut : Program := ⟨[maybeD, .fn "f" [("x", none)] none
  (.matchE (.var "x") [(pJust (.int 1), .int 1), (.wild, .int 0)])]⟩

-- compiler: accepts
#guard accepts inferredScrut

/-- A recursive, parameterised type: `type L a = | N | C a (L a)`, `fn len(l: L a) -> Int =
match l with | N -> 0 | C _ t -> len(t)`. -/
def listLen : Program := ⟨[
  .typeD "L" ["a"] [("N", []), ("C", [.named "a", .adt "L" [.named "a"]])],
  .fn "len" [("l", some (.adt "L" [.named "a"]))] (some (.int, .pure))
    (.matchE (.var "l") [(.ctor "N" [], .int 0),
      (.ctor "C" [.wild, .var "t"], .app (.var "len") [.var "t"])])]⟩

-- compiler: accepts
#guard accepts listLen

/-! ### Declarations (spec §5.1, §5.6) -/

/-- A constructor name declared twice: `type A = | C | D`, `type B = | C`. -/
def dupCtor : Program := ⟨[.typeD "A" [] [("C", []), ("D", [])], .typeD "B" [] [("C", [])]]⟩

-- compiler: rejects
#guard rejectKey dupCtor == "dup-ctor"

/-- A function named like a constructor: `type A = | C | D`, `fn C() -> Int = 1`. -/
def fnNamedCtor : Program := ⟨[.typeD "A" [] [("C", []), ("D", [])],
  .fn "C" [] (some (.int, .pure)) (.int 1)]⟩

-- compiler: rejects
#guard rejectKey fnNamedCtor == "dup-ctor"

/-- An unknown type in a field: `type T = | C Foo`. -/
def unknownField : Program := ⟨[.typeD "T" [] [("C", [.adt "Foo" []])]]⟩

-- compiler: rejects, in the bundler (`bundle:`), which the oracle counts a harness error
#guard rejectKey unknownField == "unknown-type"

/-- A type variable the declaration does not bind: `type T = | C a`. Compiler: accepts. Spec
§5.6 allows only "a lowercase type variable … bound by the enclosing type declaration's parameter
list"; the model follows it. A compiler bug, allowlisted as `free-tyvar`. -/
def freeVarField : Program := ⟨[.typeD "T" [] [("C", [.named "a"])]]⟩

#guard rejectKey freeVarField == "unknown-type"

/-- A field applies a type to the wrong number of arguments: `type T = | C (Maybe Int Int)`.
Compiler: accepts. §5.6 names the checks on a field's type names, not their argument count; the
model rejects, since such a field has no type to instantiate. A spec gap, allowlisted as
`field-type-arity`. -/
def fieldArity : Program := ⟨[maybeD, .typeD "T" [] [("C", [.adt "Maybe" [.int, .int]])]]⟩

#guard rejectKey fieldArity == "type-arity"

/-- Forward and mutual references: `type A = | A0 | A1 B`, `type B = | B0 A`. -/
def mutualTypes : Program := ⟨[.typeD "A" [] [("A0", []), ("A1", [.adt "B" []])],
  .typeD "B" [] [("B0", [.adt "A" []])]]⟩

-- compiler: accepts
#guard accepts mutualTypes

/-- One constructor named after another type of the module: `type Bar = | B`,
`type Box = | Bar Int`. -/
def ctorIsType : Program := ⟨[.typeD "Bar" [] [("B", [])], .typeD "Box" [] [("Bar", [.int])]]⟩

-- compiler: rejects, in the bundler (`bundle:`), which the oracle counts a harness error
#guard rejectKey ctorIsType == "ctor-is-type"

/-- ... and one named after its own type is fine: `type Box = | Box Int`. -/
def ctorOwnName : Program := ⟨[.typeD "Box" [] [("Box", [.int])]]⟩

-- compiler: accepts
#guard accepts ctorOwnName

/-- The programs above, for printing and the compiler run. -/
def all : List (String × Program) := [
  ("ex10_5", ex10_5), ("ex10_6", ex10_6), ("ex10_6map", ex10_6map), ("ex10_11", ex10_11),
  ("ex10_12", ex10_12), ("missingCtor", missingCtor), ("boolNoFalse", boolNoFalse),
  ("intColumn", intColumn), ("nestedOZ", nestedOZ), ("tupleTrailingWild", tupleTrailingWild),
  ("boolPairGap", boolPairGap), ("maybePairGap", maybePairGap), ("uninhabited", uninhabited),
  ("unitMatch", unitMatch), ("afterWild", afterWild), ("afterVar", afterVar),
  ("afterAllCtors", afterAllCtors), ("repeatedLit", repeatedLit), ("repeatedUnit", repeatedUnit),
  ("repeatedCtor", repeatedCtor), ("partialCtor", partialCtor), ("boolThenWild", boolThenWild),
  ("tupleRepeat", tupleRepeat), ("unitThenWild", unitThenWild),
  ("ctorRepeatUnit", ctorRepeatUnit), ("patBindsTwice", patBindsTwice),
  ("pairCtorGap", pairCtorGap), ("unknownCtorPat", unknownCtorPat),
  ("unknownCtorExpr", unknownCtorExpr), ("ctorCallArity", ctorCallArity),
  ("ctorPatArity", ctorPatArity), ("bareCtor", bareCtor), ("nullaryCall", nullaryCall),
  ("nullaryValue", nullaryValue), ("ctorArgMismatch", ctorArgMismatch),
  ("patMismatch", patMismatch), ("armMismatch", armMismatch), ("patScope", patScope),
  ("armIO", armIO), ("inferredScrut", inferredScrut), ("listLen", listLen), ("dupCtor", dupCtor),
  ("fnNamedCtor", fnNamedCtor), ("unknownField", unknownField), ("freeVarField", freeVarField),
  ("fieldArity", fieldArity), ("mutualTypes", mutualTypes), ("ctorIsType", ctorIsType),
  ("ctorOwnName", ctorOwnName)]

end SproutCore.TestsAdt
