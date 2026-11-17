import SproutCore
import Plausible

/-!
# `sprout-oracle`: the model against `compile_driver_bin_stage1 --phase check`

For N programs from a seed: print each to a `.spr`, run the compiler's check phase, compare with
the model's verdict. Three compiler verdicts (docs/lean-model-v0.md §6): accept, reject, and a
harness error (`bundle: …` — the printer wrote something the parser refused). A disagreement is
shrunk, written to the output directory, and fails the run unless its shape is in the allowlist.
-/

open SproutCore

structure Cfg where
  driver : String := "build/compile_driver_bin_stage1"
  stdlib : String := "stdlib"
  out : String := "build/lean-oracle"
  allowlist : String := "formal/oracle/ALLOWLIST"
  count : Nat := 500
  seed : Nat := 20261117
  /-- `no-local-let-mono` or `printer-bug`: the self-test's planted defects. -/
  plant : String := ""
  /-- `disagreement` or `harness-error`: succeed only if the planted defect is seen. -/
  expect : String := ""
  /-- With `--expect disagreement`: some new disagreement shape must contain this text. -/
  expectShape : String := ""

inductive CV where
  | accept
  | reject (msg : String)
  | harness (msg : String)

def has (s sub : String) : Bool := (s.splitOn sub).length > 1

def firstLineWith (s sub : String) : String :=
  ((s.splitOn "\n").find? (fun l => has l sub)).getD ""

def runCompiler (cfg : Cfg) (file : String) : IO CV := do
  let o ← IO.Process.output { cmd := cfg.driver, args := #["--phase", "check", cfg.stdlib, file] }
  let out := o.stdout ++ o.stderr
  if has out "bundle: " then
    return .harness (firstLineWith out "bundle: ")
  else if o.exitCode == 0 && ((out.splitOn "\n").any (· == "OK")) && !(has out "ERROR:") then
    return .accept
  else if o.exitCode != 0 && has out "ERROR:" then
    return .reject (firstLineWith out "ERROR:")
  else
    return .harness s!"unclassified (exit {o.exitCode}): {(out.splitOn "\n").head!}"

/-- Replace each backtick-quoted name by `_`. -/
def blankQuoted (s : String) : String :=
  match s.splitOn "`" with
  | [] => s
  | first :: rest =>
    let rec go : List String → String
      | _ :: after :: more => "_" ++ after ++ go more
      | _ => ""
    first ++ go rest

/-- Strip a compiler message down to its rule: no positions, names or types. -/
def normMsg (msg : String) : String :=
  let after := match msg.splitOn "ERROR: " with
    | _ :: rest => "ERROR: ".intercalate rest
    | [] => msg
  let after := if after.startsWith "check: " then (after.drop 7).toString else after
  let cuts := [" in function", " in ", ":", " —", ";", " ("]
  let cut := cuts.foldl (fun (s : String) c => (s.splitOn c).head!) (blankQuoted after)
  cut.trimAscii.toString

def shapeOf (m : Except String Unit) (c : CV) : Option String :=
  match m, c with
  | .ok _, .reject msg => some s!"model-accepts/compiler-rejects: {normMsg msg}"
  | .error e, .accept => some s!"model-rejects/compiler-accepts: {(e.splitOn ":").head!}"
  | _, _ => none

def verdictStr (m : Except String Unit) : String :=
  match m with
  | .ok _ => "accept"
  | .error e => s!"reject ({e})"

def cvStr : CV → String
  | .accept => "accept"
  | .reject m => s!"reject ({m})"
  | .harness m => s!"HARNESS ERROR ({m})"

structure Setup where
  cfg : Cfg
  rules : Rules
  popts : PrintOpts
  work : String

def evalProg (s : Setup) (p : Program) : IO (Except String Unit × CV) := do
  let file := s.work ++ "/p.spr"
  IO.FS.writeFile file (printProgram s.popts p)
  let c ← runCompiler s.cfg file
  return (checkProgram s.rules p, c)

/-- Greedy shrink: take the first smaller program with the same disagreement shape. -/
partial def shrinkLoop (s : Setup) (shape : String) (p : Program) (budget : Nat) : IO Program := do
  if budget == 0 then return p
  let mut budget := budget
  for cand in shrinkProgram p do
    if budget == 0 then break
    budget := budget - 1
    let (m, c) ← evalProg s cand
    if shapeOf m c == some shape then
      return (← shrinkLoop s shape cand budget)
  return p

def parseAllow (path : String) : IO (List (String × String)) := do
  if !(← System.FilePath.pathExists path) then return []
  let txt ← IO.FS.readFile path
  let mut out := []
  for l in txt.splitOn "\n" do
    let l := l.trimAscii.toString
    if l == "" || l.startsWith "#" then continue
    match l.splitOn " | " with
    | [shape, reason] => out := out ++ [(shape.trimAscii.toString, reason.trimAscii.toString)]
    | _ => throw (IO.userError s!"ALLOWLIST: expected `shape | reason`: {l}")
  return out

def parseArgs : List String → Cfg → Except String Cfg
  | [], c => .ok c
  | "--driver" :: v :: r, c => parseArgs r { c with driver := v }
  | "--stdlib" :: v :: r, c => parseArgs r { c with stdlib := v }
  | "--out" :: v :: r, c => parseArgs r { c with out := v }
  | "--allowlist" :: v :: r, c => parseArgs r { c with allowlist := v }
  | "--count" :: v :: r, c => parseArgs r { c with count := v.toNat!}
  | "--seed" :: v :: r, c => parseArgs r { c with seed := v.toNat! }
  | "--plant" :: v :: r, c => parseArgs r { c with plant := v }
  | "--expect" :: v :: r, c => parseArgs r { c with expect := v }
  | "--expect-shape" :: v :: r, c => parseArgs r { c with expectShape := v }
  | a :: _, _ => .error s!"unknown argument {a}"

def main (args : List String) : IO UInt32 := do
  let cfg ← match parseArgs args {} with
    | .ok c => pure c
    | .error e => do IO.eprintln e; return 2
  if !(← System.FilePath.pathExists cfg.driver) then
    IO.eprintln s!"driver not found: {cfg.driver} (run `just bootstrap-from-seed`)"
    return 2
  let planted := cfg.plant != ""
  let rules : Rules := { localLetMono := cfg.plant != "no-local-let-mono" }
  let popts : PrintOpts := { bug := cfg.plant == "printer-bug" }
  IO.FS.createDirAll cfg.out
  let work ← IO.FS.createTempDir
  let s : Setup := { cfg, rules, popts, work := work.toString }
  let allow ← parseAllow cfg.allowlist
  let mut nAccept := 0
  let mut nReject := 0
  let mut harness : List String := []
  let mut bad : List String := []
  let mut allowed : List String := []
  let mut disagreements := 0
  for i in [0:cfg.count] do
    let seed := cfg.seed + i
    let p := programOfSeed seed
    let (m, c) ← evalProg s p
    match m with
    | .ok _ => nAccept := nAccept + 1
    | .error _ => nReject := nReject + 1
    match c with
    | .harness msg =>
      harness := harness ++ [s!"seed {seed}: {msg}"]
      IO.FS.writeFile (cfg.out ++ s!"/harness-{seed}.spr") (printProgram popts p)
    | _ =>
      match shapeOf m c with
      | none => pure ()
      | some shape =>
        disagreements := disagreements + 1
        let small ← shrinkLoop s shape p 400
        let (m', c') ← evalProg s small
        let src := printProgram popts small
        IO.FS.writeFile (cfg.out ++ s!"/disagree-{seed}.spr") src
        IO.FS.writeFile (cfg.out ++ s!"/disagree-{seed}.txt")
          s!"seed: {seed}\nshape: {shape}\nmodel: {verdictStr m'}\ncompiler: {cvStr c'}\n"
        if allow.any (·.1 == shape) then
          allowed := allowed ++ [shape]
        else
          bad := bad ++ [s!"seed {seed}: {shape}"]
          IO.println s!"DISAGREEMENT seed {seed}: {shape}\n  model:    {verdictStr m'}\n  compiler: {cvStr c'}\n{src}"
  IO.FS.removeDirAll work
  let stale := allow.filter fun (sh, _) => !(allowed.contains sh)
  IO.println s!"programs {cfg.count} (model accept {nAccept}, reject {nReject}); disagreements {disagreements} (allowlisted {allowed.length}); harness errors {harness.length}"
  for h in harness.take 5 do IO.println s!"HARNESS ERROR {h}"
  match cfg.expect with
  | "disagreement" =>
    if !bad.isEmpty && harness.isEmpty && (bad.any fun b => has b cfg.expectShape) then
      IO.println "self-test ok: the planted model defect was found"
      return 0
    IO.println "self-test FAILED: expected a new disagreement and no harness error"
    return 1
  | "harness-error" =>
    if !harness.isEmpty && bad.isEmpty then
      IO.println "self-test ok: the planted printer defect gave a harness error"
      return 0
    IO.println "self-test FAILED: expected a harness error and no disagreement"
    return 1
  | _ =>
    for (sh, _) in stale do
      IO.println s!"STALE ALLOWLIST ENTRY (no longer disagrees): {sh}"
    if bad.isEmpty && harness.isEmpty && (planted || stale.isEmpty) then
      IO.println "oracle ok"
      return 0
    return 1
