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
  | .error e => (e.splitOn ":").head!

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

end SproutCore.Tests
