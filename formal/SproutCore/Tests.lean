import SproutCore.Check

/-!
# Build-time regression checks

`#guard` fails `lake build` if a verdict changes. Each case is a program the compiler's check
phase gave the verdict named here (`compile_driver_bin_stage1 --phase check`), so a regression
in the model breaks `just lean-model` without a compiler or an oracle run.
-/

namespace SproutCore.Tests

def accepts (p : Program) : Bool :=
  match checkProgram {} p with
  | .ok _ => true
  | .error _ => false

/-- The rejection key (the text before the first colon), or `""` for an accept. -/
def rejectKey (p : Program) : String :=
  match checkProgram {} p with
  | .ok _ => ""
  | .error e => msgKey e

private def printStr (s : String) : Expr := .app (.var "print_str") [.str s]

/-- `fn ap(f: a -> b, x: a) -> b = f(x)`. -/
private def apDecl : Decl :=
  .fn "ap" [("f", some (.arr [.named "a"] (.named "b") .pure)), ("x", some (.named "a"))]
    (some (.named "b", .pure)) (.app (.var "f") [.var "x"])

/-- `fn g(a: String, b: String) -> Unit !{IO} = print_str(a)`. -/
private def gDecl : Decl :=
  .fn "g" [("a", some .str), ("b", some .str)] (some (.unit, .io)) (printStr "a")

-- An n-ary `!{IO}` arrow has a pure outer arrow (effect on the innermost one, spec §7 rule 9),
-- so `ap(g, "a")` passes `g` where `String -> (String -> Unit !{IO})` is expected. Accepted.
-- `fn m() -> String -> Unit !{IO} = ap(g, "a")`
#guard accepts ⟨[apDecl, gDecl,
  .fn "m" [] (some (.arr [.str] .unit .io, .pure)) (.app (.var "ap") [.var "g", .str "a"])]⟩

-- The effect still counts at the innermost arrow: a pure slot refuses the same `g`.
-- `fn m() -> String -> Unit = ap(g, "a")`
#guard rejectKey ⟨[apDecl, gDecl,
  .fn "m" [] (some (.arr [.str] .unit .pure, .pure)) (.app (.var "ap") [.var "g", .str "a"])]⟩
  == "effect-slot"

-- `main` follows the declaration-order rule too (spec §7 rule 16): it may not use a `let`
-- declared below it. `fn main() -> Unit !{IO} = print_str(l)` above `let l = "a"`.
#guard rejectKey ⟨[.mainFn (some (.unit, .io)) (.app (.var "print_str") [.var "l"]),
  .letD "l" none (.str "a")]⟩ == "fn-before-let"

-- ... and a `let` above it is fine.
#guard accepts ⟨[.letD "l" none (.str "a"),
  .mainFn (some (.unit, .io)) (.app (.var "print_str") [.var "l"])]⟩

/-! ### A zero-parameter function has an arrow (spec §7 rule 9) -/

/-- `fn k() -> Int -> Int = \x -> x`: type `() -> Int -> Int`, not `Int -> Int`. -/
private def kDecl : Decl :=
  .fn "k" [] (some (.arr [.int] .int .pure, .pure)) (.lam "x" none (.var "x"))

private def fnY (body : Expr) : Decl :=
  .fn "f" [("y", some .int)] (some (.int, .pure)) body

-- `fn f(y: Int) -> Int = let g = k in g(y)`: `g` takes no argument. Rejected.
#guard rejectKey ⟨[kDecl, fnY (.letIn "g" (.var "k") (.app (.var "g") [.var "y"]))]⟩ == "mismatch"

-- `fn f(y: Int) -> Int = k()(y)`. Accepted.
#guard accepts ⟨[kDecl, fnY (.app (.app (.var "k") []) [.var "y"])]⟩

-- `fn ap0(h: () -> Int -> Int, y: Int) -> Int = h()(y)` and `ap0(k, y)`. Accepted.
private def ap0Decl : Decl :=
  .fn "ap0" [("h", some (.arr [] (.arr [.int] .int .pure) .pure)), ("y", some .int)]
    (some (.int, .pure)) (.app (.app (.var "h") []) [.var "y"])

#guard accepts ⟨[kDecl, ap0Decl, fnY (.app (.var "ap0") [.var "k", .var "y"])]⟩

-- `fn ap1(h: Int -> Int, y: Int) -> Int = h(y)` and `ap1(k, y)`: `() -> Int -> Int` is no
-- `Int -> Int`. Rejected.
#guard rejectKey ⟨[kDecl,
  .fn "ap1" [("h", some (.arr [.int] .int .pure)), ("y", some .int)] (some (.int, .pure))
    (.app (.var "h") [.var "y"]),
  fnY (.app (.var "ap1") [.var "k", .var "y"])]⟩ == "mismatch"

-- `Int -> () -> Int` keeps its inner empty arrow: `fn k0() -> Int = 1`,
-- `fn k2(x: Int) -> () -> Int = k0`, `ap2(h: Int -> () -> Int, y: Int) -> Int = h(y)()`.
-- Accepted.
#guard accepts ⟨[
  .fn "k0" [] (some (.int, .pure)) (.int 1),
  .fn "k2" [("x", some .int)] (some (.arr [] .int .pure, .pure)) (.var "k0"),
  .fn "ap2" [("h", some (.arr [.int] (.arr [] .int .pure) .pure)), ("y", some .int)]
    (some (.int, .pure)) (.app (.app (.var "h") [.var "y"]) []),
  fnY (.app (.var "ap2") [.var "k2", .var "y"])]⟩

/-! ### A peer join of arrows of different stored arity (spec §7 rule 8) -/

private def lam2 (b : Expr) : Expr := .lam "x" none (.lam "y" none b)

-- `let id = \x -> x`, `fn h(c: Bool) = if c then id else (\x -> \y -> 1)`. Accepted: `id`'s result
-- variable is solved to the other branch's rest.
#guard accepts ⟨[.letD "id" none (.lam "x" none (.var "x")),
  .fn "h" [("c", some .bool)] none (.ite (.var "c") (.var "id") (lam2 (.int 1)))]⟩

-- A result variable solved to an arrow later keeps its short stored arrow. Accepted:
-- `let g = \y -> y in let u = g(\a -> \b -> a) in let h = if true then g else
-- (\p -> \q -> \r -> q) in n`.
#guard accepts ⟨[.fn "k" [("n", some .int)] (some (.int, .pure))
  (.letIn "g" (.lam "y" none (.var "y"))
    (.letIn "u" (.app (.var "g") [.lam "a" none (.lam "b" none (.var "a"))])
      (.letIn "h" (.ite (.bool true) (.var "g")
          (.lam "p" none (.lam "q" none (.lam "r" none (.var "q")))))
        (.var "n"))))]⟩

-- Arrows that really differ in arity still clash:
-- `fn h(c: Bool) = if c then (\x -> 1) else (\x -> \y -> 1)`. Rejected.
#guard rejectKey ⟨[.fn "h" [("c", some .bool)] none
  (.ite (.var "c") (.lam "x" none (.int 1)) (lam2 (.int 1)))]⟩ == "mismatch"

-- `() -> T` and `Unit -> T` do not join:
-- `fn k0() -> Int = 1`, `fn h(c: Bool) = if c then k0 else (\x -> 1)`. Rejected.
#guard rejectKey ⟨[.fn "k0" [] (some (.int, .pure)) (.int 1),
  .fn "h" [("c", some .bool)] none (.ite (.var "c") (.var "k0") (.lam "x" none (.int 1)))]⟩
  == "mismatch"

end SproutCore.Tests
