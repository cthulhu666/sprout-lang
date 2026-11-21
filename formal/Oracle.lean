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
  /-- `no-local-let-mono`, `printer-bug` or `low-fuel`: the self-test's planted defects. -/
  plant : String := ""
  /-- The model's fuel budget per `shallow`/`zonk`/`flow`/`joinTy`/`peel` call. -/
  fuel : Nat := defaultFuel
  /-- `disagreement`, `harness-error` or `fuel-error`: succeed only if the planted defect shows. -/
  expect : String := ""
  /-- With `--expect disagreement`: some new disagreement shape must contain this text. -/
  expectShape : String := ""
  /-- A file for one line per program: `<seed> <model verdict> <compiler verdict>`. -/
  dump : String := ""

inductive CV where
  | accept
  | reject (msg : String)
  | harness (msg : String)

def firstLineWith (s sub : String) : String :=
  ((s.splitOn "\n").find? (fun l => l.contains sub)).getD ""

def runCompiler (cfg : Cfg) (file : String) : IO CV := do
  let o ← IO.Process.output { cmd := cfg.driver, args := #["--phase", "check", cfg.stdlib, file] }
  let out := o.stdout ++ o.stderr
  if out.contains "bundle: " then
    return .harness (firstLineWith out "bundle: ")
  else if o.exitCode == 0 && ((out.splitOn "\n").any (· == "OK")) && !(out.contains "ERROR:") then
    return .accept
  else if o.exitCode != 0 && out.contains "ERROR:" then
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
  let cut := (cuts.foldl (fun (s : String) c => (s.splitOn c).head!) (blankQuoted after)).trimAscii.toString
  -- keep the effect-slot reason beside the prefix: the prefix alone puts an unrelated `If`
  -- mismatch under the same shape, the reason alone puts call sites and joins together
  if msg.contains "cannot be used where a pure one is required" then
    cut ++ ": IO function where a pure one is required"
  else cut

/-- A model run that ran out of fuel has no verdict, so it has no shape either. -/
def shapeOf (m : Except CErr Unit) (c : CV) : Option String :=
  match m, c with
  | .ok _, .reject msg => some s!"model-accepts/compiler-rejects: {normMsg msg}"
  | .error (.reject key _), .accept => some s!"model-rejects/compiler-accepts: {key}"
  | _, _ => none

def isFuel (m : Except CErr Unit) : Bool :=
  match m with
  | .error .fuel => true
  | _ => false

def verdictStr (m : Except CErr Unit) : String :=
  match m with
  | .ok _ => "accept"
  | .error .fuel => "HARNESS ERROR (fuel: the model's run was cut off)"
  | .error e => s!"reject ({e.render})"

/-- A verdict for the dump: `accept`, `reject <key>`, or `harness`. -/
def modelDump (m : Except CErr Unit) : String :=
  match m with
  | .ok _ => "accept"
  | .error (.reject key _) => s!"reject {key}"
  | .error .fuel => "harness"

def cvDump : CV → String
  | .accept => "accept"
  | .reject m => s!"reject {normMsg m}"
  | .harness _ => "harness"

def cvStr : CV → String
  | .accept => "accept"
  | .reject m => s!"reject ({m})"
  | .harness m => s!"HARNESS ERROR ({m})"

structure Setup where
  cfg : Cfg
  rules : Rules
  fuel : Nat
  popts : PrintOpts
  work : String

def compileProg (s : Setup) (p : Program) : IO CV := do
  let file := s.work ++ "/p.spr"
  IO.FS.writeFile file (printProgram s.popts p)
  runCompiler s.cfg file

def evalProg (s : Setup) (p : Program) : IO (Except CErr Unit × CV) := do
  return (checkProgram s.rules p s.fuel, ← compileProg s p)

/-- Greedy shrink: take the first smaller program with the same disagreement shape. `p` comes
with its two verdicts, and the result does too. -/
partial def shrinkLoop (s : Setup) (shape : String) (p : Program) (m : Except CErr Unit)
    (c : CV) (budget : Nat) : IO (Program × Except CErr Unit × CV) := do
  if budget == 0 then return (p, m, c)
  let mut budget := budget
  for cand in shrinkProgram p do
    if budget == 0 then break
    budget := budget - 1
    -- the shape fixes the model verdict a candidate needs, so skip the compiler on a miss
    let m' := checkProgram s.rules cand s.fuel
    -- a cut-off run is no verdict, so it cannot keep the shape
    if isFuel m' then continue
    if m'.toBool != shape.startsWith "model-accepts" then continue
    let c' ← compileProg s cand
    if shapeOf m' c' == some shape then
      return (← shrinkLoop s shape cand m' c' budget)
  return (p, m, c)

/-- The allowlist: `(id, shape, reason)` per entry. -/
def parseAllow (path : String) : IO (List (String × String × String)) := do
  if !(← System.FilePath.pathExists path) then return []
  let txt ← IO.FS.readFile path
  let mut out := []
  for l in txt.splitOn "\n" do
    let l := l.trimAscii.toString
    if l == "" || l.startsWith "#" then continue
    match l.splitOn " | " with
    | id :: shape :: reason@(_ :: _) =>
      out := out ++ [(id.trimAscii.toString, shape.trimAscii.toString,
        " | ".intercalate reason |>.trimAscii.toString)]
    | _ => throw (IO.userError s!"ALLOWLIST: expected `id | shape | reason`: {l}")
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
  | "--fuel" :: v :: r, c => parseArgs r { c with fuel := v.toNat! }
  | "--dump-verdicts" :: v :: r, c => parseArgs r { c with dump := v }
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
  -- the planted low budget cuts off most runs; the self-test needs them reported, not decided
  let fuel := if cfg.plant == "low-fuel" then 1 else cfg.fuel
  let s : Setup := { cfg, rules, fuel, popts, work := work.toString }
  let allow ← parseAllow cfg.allowlist
  for (id, _, _) in allow do
    if (witnesses.lookup id).isNone then
      IO.FS.removeDirAll work
      IO.eprintln s!"ALLOWLIST: entry `{id}` has no witness in formal/SproutCore/Witnesses.lean"
      return 2
  let mut nAccept := 0
  let mut nReject := 0
  let mut nFuel := 0
  let mut harness : List String := []
  let mut bad : List String := []
  let mut allowed : List String := []
  let mut disagreements := 0
  let mut dump : List String := []
  for i in [0:cfg.count] do
    let seed := cfg.seed + i
    let p := programOfSeed seed
    let (m, c) ← evalProg s p
    if cfg.dump != "" then dump := dump ++ [s!"{seed} {modelDump m} {cvDump c}"]
    match m with
    | .ok _ => nAccept := nAccept + 1
    | .error (.reject ..) => nReject := nReject + 1
    | .error .fuel => nFuel := nFuel + 1
    -- running out of fuel is never a verdict: a harness error, whatever the compiler said
    if isFuel m then
      harness := harness ++ [s!"seed {seed}: (fuel: the model's run was cut off at {fuel})"]
      IO.FS.writeFile (cfg.out ++ s!"/harness-{seed}.spr") (printProgram popts p)
    else
    match c with
    | .harness msg =>
      harness := harness ++ [s!"seed {seed}: {msg}"]
      IO.FS.writeFile (cfg.out ++ s!"/harness-{seed}.spr") (printProgram popts p)
    | _ =>
      match shapeOf m c with
      | none => pure ()
      | some shape =>
        disagreements := disagreements + 1
        let (small, m', c') ← shrinkLoop s shape p m c 400
        let src := printProgram popts small
        IO.FS.writeFile (cfg.out ++ s!"/disagree-{seed}.spr") src
        IO.FS.writeFile (cfg.out ++ s!"/disagree-{seed}.txt")
          s!"seed: {seed}\nshape: {shape}\nmodel: {verdictStr m'}\ncompiler: {cvStr c'}\n"
        if allow.any (·.2.1 == shape) then
          allowed := allowed ++ [shape]
        else
          bad := bad ++ [s!"seed {seed}: {shape}"]
          IO.println s!"DISAGREEMENT seed {seed}: {shape}\n  model:    {verdictStr m'}\n  compiler: {cvStr c'}\n{src}"
  -- An entry is stale when its fixed witness no longer disagrees with the entry's shape, whatever
  -- the random sample held.
  let mut stale : List String := []
  unless planted do
    for (id, shape, _) in allow do
      match witnesses.lookup id with
      | none => pure ()
      | some w =>
        let (m, c) ← evalProg s w
        if shapeOf m c != some shape then
          stale := stale ++ [s!"{id}: {shape} (witness: model {verdictStr m}, compiler {cvStr c})"]
  IO.FS.removeDirAll work
  if cfg.dump != "" then IO.FS.writeFile cfg.dump ("\n".intercalate dump ++ "\n")
  IO.println s!"programs {cfg.count} (model accept {nAccept}, reject {nReject}); disagreements {disagreements} (allowlisted {allowed.length}); harness errors {harness.length}"
  for h in harness.take 5 do IO.println s!"HARNESS ERROR {h}"
  match cfg.expect with
  | "disagreement" =>
    if !bad.isEmpty && harness.isEmpty && (bad.any fun b => b.contains cfg.expectShape) then
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
  | "fuel-error" =>
    -- every harness error is a fuel cut-off, and none became a verdict or a disagreement
    if nFuel > 0 && harness.length == nFuel && bad.isEmpty then
      IO.println s!"self-test ok: {nFuel} runs ran out of fuel, none gave a verdict"
      return 0
    IO.println "self-test FAILED: expected fuel harness errors, no verdict from a fuel-out"
    return 1
  | _ =>
    for st in stale do
      IO.println s!"STALE ALLOWLIST ENTRY (its witness no longer disagrees): {st}"
    if bad.isEmpty && harness.isEmpty && stale.isEmpty then
      IO.println "oracle ok"
      return 0
    return 1
