import Lean

/-!
# `axiom-check`: no theorem rests on an axiom beyond Lean's standard three

docs/lean-model-v0.md §7. Imports a root module (default `SproutCore`) and walks every constant
declared in a module of its package, the modules being those the environment lists under the
root's first name component, so a theorem cannot escape by sitting in a file a hand-kept list
forgot. `Lean.collectAxioms` runs on each theorem, definition, opaque and axiom. An unfinished
proof is `sorryAx`, a `native_decide` is `Lean.ofReduceBool`: both fail. A source file under
`--src` that no import reaches fails too: its theorems would not be checked.

    axiom-check [--src <dir>] [--lib-path <dir>]… [<Root>]
-/

open Lean

def allowedAxioms : List Name := [``propext, ``Classical.choice, ``Quot.sound]

structure Cfg where
  root : Name := `SproutCore
  src : Option System.FilePath := none
  libPaths : List System.FilePath := []

def parseArgs : List String → Cfg → Except String Cfg
  | [], c => .ok c
  | "--src" :: v :: r, c => parseArgs r { c with src := some ⟨v⟩ }
  | "--lib-path" :: v :: r, c => parseArgs r { c with libPaths := c.libPaths ++ [⟨v⟩] }
  | a :: r, c =>
    if a.startsWith "--" then .error s!"unknown argument {a}"
    else parseArgs r { c with root := a.toName }

/-- The modules a source tree `<src>/<Root>/**.lean` defines. -/
partial def sourceModules (dir : System.FilePath) (m : Name) : IO (List Name) := do
  let mut out := []
  for e in (← dir.readDir) do
    if (← e.path.isDir) then
      out := out ++ (← sourceModules e.path (m.str e.fileName))
    else if e.path.extension == some "lean" then
      out := out ++ [m.str (e.path.fileStem.getD "")]
  return out

/-- `enableInitializersExecution` is unsafe: it lets imported `initialize` blocks run. -/
unsafe def main (args : List String) : IO UInt32 := do
  let cfg ← match parseArgs args {} with
    | .ok c => pure c
    | .error e => do IO.eprintln e; return 2
  initSearchPath (← findSysroot) cfg.libPaths
  enableInitializersExecution
  let env ← importModules #[{ module := cfg.root }] {} (loadExts := true)
  let pkg := cfg.root.getRoot
  let mods := env.header.moduleNames.filter (·.getRoot == pkg)
  let mut failed := false
  if let some src := cfg.src then
    let dir := src / pkg.toString
    if ← dir.pathExists then
      for m in ← sourceModules dir pkg do
        unless mods.contains m do
          IO.println s!"axiom-check: {m} is not imported by {cfg.root}, so its theorems escape"
          failed := true
  let mut nThm := 0
  let mut nDef := 0
  let mut used : NameSet := {}
  let mut bad : Array (Name × Array Name) := #[]
  let ctx : Core.Context := { fileName := "<axiom-check>", fileMap := default }
  for (n, ci) in env.constants.map₁.toList do
    let some idx := env.getModuleIdxFor? n | continue
    unless pkg == env.header.moduleNames[idx]!.getRoot do continue
    match ci with
    | .thmInfo _ => nThm := nThm + 1
    | .defnInfo _ | .opaqueInfo _ | .axiomInfo _ => nDef := nDef + 1
    | _ => continue
    let (axs, _) ← (collectAxioms n : CoreM _).toIO ctx { env }
    used := axs.foldl (·.insert ·) used
    let off := axs.filter (!allowedAxioms.contains ·)
    unless off.isEmpty do bad := bad.push (n, off)
  IO.println s!"axiom-check: {pkg}: {mods.size} modules, {nThm} theorems, {nDef} definitions; \
    axioms used: {used.toList}"
  if nThm == 0 then
    IO.println s!"axiom-check: {pkg} has no theorem: nothing was checked"
    failed := true
  for (n, off) in bad.qsort (·.1.lt ·.1) do
    IO.println s!"axiom-check: {n} uses {off.toList}"
  if !bad.isEmpty then
    IO.println s!"axiom-check FAILED: {bad.size} constants use an axiom outside {allowedAxioms}"
    failed := true
  return if failed then 1 else 0
