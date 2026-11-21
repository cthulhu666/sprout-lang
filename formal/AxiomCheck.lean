import Lean

/-!
# `axiom-check`: no theorem rests on an axiom beyond Lean's standard three

docs/lean-model-v0.md §7. Imports a root module (default `SproutCore`) and walks every constant
declared in a module of its package, the modules being those the environment lists under the
root's first name component, so a theorem cannot escape by sitting in a file a hand-kept list
forgot. Every theorem, definition, opaque and axiom is traced to the axioms it rests on. An
unfinished proof is `sorryAx`; under Lean 4.35 each `native_decide` is a per-use axiom named
`<decl>._native.native_decide.ax_*`: both fail. A source file under `--src` that no import
reaches fails too: its theorems would not be checked. Exit 2 is a usage or setup error.

The import loads constants only (`loadExts := false`), so no imported `initialize` runs. Running
them would need `enableInitializersExecution` and, on Linux, an executable that exports the Lean
runtime's symbols to the interpreter (`supportInterpreter`), or the first `initialize` of `Init`
or `Plausible` fails with a missing native symbol. Nothing here reads an environment extension.
`axiomsOf` is the traversal of `Lean.collectAxioms` without the extension that caches imported
results, and with one cache for the whole run.

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
def sourceModules (src : System.FilePath) (pkg : Name) : IO (List Name) := do
  let mut out := []
  for f in ← (src / pkg.toString).walkDir do
    if f.extension == some "lean" && !(← f.isDir) then
      out := out ++ [← moduleNameOfFileName f (some src)]
  return out

/-- The walk's state: the axiom sets known to be complete, and the constants being visited with
their depth in the walk. -/
structure Walk where
  done : NameMap (Array Name) := {}
  active : NameMap Nat := {}

/-- The axioms `c` rests on, sorted, and the depth of the shallowest constant being visited that
the walk ran into below `c` (`depth` or more when it ran into none). A constant met again while
it is being visited adds nothing, which ends a cycle (inductive and constructor, mutual
recursion). The sets of a cycle's members are then incomplete, so they are cached only when the
walk met no constant above them: a shared cache that held an incomplete set would hand it to a
later theorem and hide an axiom, which `Lean.collectAxioms` avoids by starting afresh. -/
partial def axiomsOf (env : Environment) (c : Name) (depth : Nat) :
    StateM Walk (Array Name × Nat) := do
  if let some r := (← get).done.find? c then return (r, depth)
  if let some d := (← get).active.find? c then return (#[], d)
  modify fun w => { w with active := w.active.insert c depth }
  let (own, deps) : Array Name × Array Name := match env.find? c with
    | some (.axiomInfo v) => (#[c], v.type.getUsedConstants)
    | some (.defnInfo v) => (#[], v.type.getUsedConstants ++ v.value.getUsedConstants)
    | some (.thmInfo v) => (#[], v.type.getUsedConstants ++ v.value.getUsedConstants)
    | some (.opaqueInfo v) => (#[], v.type.getUsedConstants ++ v.value.getUsedConstants)
    | some (.ctorInfo v) => (#[], v.type.getUsedConstants)
    | some (.recInfo v) => (#[], v.type.getUsedConstants)
    | some (.inductInfo v) => (#[], v.type.getUsedConstants ++ v.ctors.toArray)
    | some (.quotInfo _) | none => (#[], #[])
  let mut axs : NameSet := own.foldl (·.insert ·) {}
  let mut low := depth
  for d in deps do
    let (as, l) ← axiomsOf env d (depth + 1)
    for a in as do
      axs := axs.insert a
    low := min low l
  let r := axs.toArray.qsort Name.lt
  modify fun w => { w with active := w.active.erase c }
  if low ≥ depth then modify fun w => { w with done := w.done.insert c r }
  return (r, low)

def main (args : List String) : IO UInt32 := do
  let cfg ← match parseArgs args {} with
    | .ok c => pure c
    | .error e => do IO.eprintln e; return 2
  let pkg := cfg.root.getRoot
  if let some src := cfg.src then
    unless ← (src / pkg.toString).isDir do
      IO.eprintln s!"axiom-check: --src {src}: no directory {src / pkg.toString}"
      return 2
  initSearchPath (← findSysroot) cfg.libPaths
  let env ← importModules #[{ module := cfg.root }] {}
  -- the private view, so that every body is visible
  let env := env.setExporting false
  let pkgIdxs := (List.range env.header.moduleNames.size).filter fun i =>
    env.header.moduleNames[i]!.getRoot == pkg
  let mods := pkgIdxs.map (env.header.moduleNames[·]!)
  let mut failed := false
  if let some src := cfg.src then
    for m in ← sourceModules src pkg do
      unless mods.contains m do
        IO.println s!"axiom-check: {m} is not imported by {cfg.root}, so its theorems escape"
        failed := true
  let mut nThm := 0
  let mut nDef := 0
  let mut used : NameSet := {}
  let mut bad : Array (Name × Array Name) := #[]
  let mut walk : Walk := {}
  -- a module's `constNames` can list a name twice (`_sparseCasesOn_*.else_eq` do)
  let mut done : NameSet := {}
  for i in pkgIdxs do
    for n in env.header.moduleData[i]!.constNames do
      if done.contains n then continue
      done := done.insert n
      match env.find? n with
      | some (.thmInfo _) => nThm := nThm + 1
      | some (.defnInfo _) | some (.opaqueInfo _) | some (.axiomInfo _) => nDef := nDef + 1
      | _ => continue
      let ((axs, _), walk') := (axiomsOf env n 0).run walk
      walk := walk'
      used := axs.foldl (·.insert ·) used
      let off := axs.filter (!allowedAxioms.contains ·)
      unless off.isEmpty do bad := bad.push (n, off)
  IO.println s!"axiom-check: {pkg}: {mods.length} modules, {nThm} theorems, {nDef} definitions; \
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
