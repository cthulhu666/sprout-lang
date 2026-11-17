import SproutCore.Syntax

/-!
# The Sprout Core checker (stage 1)

Implements the spec's rules, not textbook HM. Every rule carries a tag
`-- spec §<heading>: <keyword>`; `formal/spec-snapshot/` pins the spec text those tags cite
(docs/lean-model-v0.md §7).

Scope of the effect model (stage 1, no effect variables): an arrow's effect is `pure` or `IO`.
A type variable solved to an arrow keeps that arrow's effect, so the checker is faithful where no
variable is solved to arrows of differing effects. The generator stays out of that region; stage 4
replaces it with effect variables and bounds.
-/

namespace SproutCore

/-- Rule switches. All on is the model; the oracle self-test plants a model with one off. -/
structure Rules where
  /-- spec §5.6 existentials: "a local `let` is monomorphic". -/
  localLetMono : Bool := true
  deriving Repr

structure CState where
  subst : Array (Option Ty) := #[]
  nextSk : Nat := 0

abbrev M := StateT CState (Except String)

/-- Reject with `<key>: <detail>`; the key is what the oracle's allowlist names. -/
def reject {α : Type} (key detail : String) : M α := throw s!"{key}: {detail}"

def fresh : M Ty := do
  let s ← get
  set { s with subst := s.subst.push none }
  return .mv s.subst.size

def freshSkolem : M Ty := do
  let s ← get
  set { s with nextSk := s.nextSk + 1 }
  return .skolem s.nextSk

/-- A function type. The effect sits on the innermost arrow, so a pure arrow whose result is an
arrow is one longer arrow (spec §7 rule 9: "an annotation attaches to the innermost arrow"). -/
def mkArr (ps : List Ty) (r : Ty) (e : Eff) : Ty :=
  match r, e with
  | .arr ps' r' e', .pure => .arr (ps ++ ps') r' e'
  | _, _ => .arr ps r e

partial def shallow : Ty → M Ty
  | .mv n => do
    match (← get).subst[n]? with
    | some (some t) => shallow t
    | _ => pure (.mv n)
  | t => pure t

partial def zonk (t : Ty) : M Ty := do
  match ← shallow t with
  | .tup ts => return .tup (← ts.mapM zonk)
  | .arr ps r e => return mkArr (← ps.mapM zonk) (← zonk r) e
  | t => return t

partial def occursIn (n : Nat) : Ty → Bool
  | .mv m => n == m
  | .tup ts => ts.any (occursIn n)
  | .arr ps r _ => ps.any (occursIn n) || occursIn n r
  | _ => false

def bindMeta (n : Nat) (t : Ty) : M Unit := do
  let t ← zonk t
  if occursIn n t then reject "occurs" "infinite type"
  modify fun s => { s with subst := s.subst.set! n (some t) }

/-- `flow actual expected`: shape unification plus the directional effect comparison of
spec §7 rule 8. The direction reverses under an arrow's parameter. -/
partial def flow (a e : Ty) : M Unit := do
  let a ← shallow a
  let e ← shallow e
  match a, e with
  | .mv n, .mv m => if n == m then pure () else bindMeta n (.mv m)
  | .mv n, t => bindMeta n t
  | t, .mv m => bindMeta m t
  | .int, .int => pure ()
  | .bool, .bool => pure ()
  | .str, .str => pure ()
  | .unit, .unit => pure ()
  | .skolem n, .skolem m =>
    -- spec §7 rule 15: a written type variable is rigid
    if n == m then pure () else reject "rigid" "two written type variables merged"
  | .tup as, .tup es =>
    if as.length == es.length then
      for (x, y) in as.zip es do flow x y
    else reject "mismatch" "tuple arity"
  | .arr aps ar ae, .arr eps er ee =>
    let k := min aps.length eps.length
    for (ap, ep) in (aps.take k).zip (eps.take k) do flow ep ap
    if aps.length == eps.length then
      flow ar er
      -- spec §7 rule 8: a function VALUE may not enter a slot that permits less effect
      unless ae.le ee do reject "effect-slot" "IO function where a pure one is required"
    else if aps.length > eps.length then
      -- the expected arrow's result is itself the rest of the actual arrow
      unless ae.le ee do reject "effect-slot" "IO function where a pure one is required"
      flow (.arr (aps.drop k) ar ae) er
    else
      unless ae.le .pure do reject "effect-slot" "IO function where a pure one is required"
      flow ar (.arr (eps.drop k) er ee)
  | _, _ => reject "mismatch" "type mismatch"

/-- Peer join, with the polarity that reverses under a parameter: the least upper bound at
positive depth, the greatest lower bound at negative depth (spec §7 rule 8, "A peer join is
covered too"). -/
partial def joinTy (pos : Bool) (a b : Ty) : M Ty := do
  let a ← shallow a
  let b ← shallow b
  match a, b with
  | .mv n, .mv m => if n == m then pure a else do bindMeta n b; pure b
  | .mv n, t => do bindMeta n t; pure t
  | t, .mv n => do bindMeta n t; pure t
  | .int, .int => pure a
  | .bool, .bool => pure a
  | .str, .str => pure a
  | .unit, .unit => pure a
  | .skolem n, .skolem m =>
    if n == m then pure a else reject "rigid" "two written type variables merged"
  | .tup as, .tup bs =>
    if as.length == bs.length then
      return .tup (← (as.zip bs).mapM fun (x, y) => joinTy pos x y)
    else reject "mismatch" "tuple arity"
  | .arr aps ar ae, .arr bps br be =>
    if aps.length == bps.length then
      let ps ← (aps.zip bps).mapM fun (x, y) => joinTy (!pos) x y
      let r ← joinTy pos ar br
      return .arr ps r (if pos then ae.join be else ae.meet be)
    else reject "mismatch" "arrow arity"
  | _, _ => reject "mismatch" "branch types differ"

/-! ### Environments and schemes -/

inductive Bind where
  /-- A local, or a group member's own type inside its group (monomorphic). -/
  | mono (t : Ty)
  /-- A generalised top-level binding: `gen 0 .. n-1` are its quantified variables. -/
  | poly (n : Nat) (t : Ty)
  /-- A top-level `fn` inside its own group, monomorphic; `arity` is its parameter count. -/
  | fnMono (arity : Nat) (t : Ty)
  | fnPoly (arity : Nat) (n : Nat) (t : Ty)

abbrev Env := List (String × Bind)

partial def instTy (ms : Array Ty) : Ty → Ty
  | .gen i => ms[i]!
  | .tup ts => .tup (ts.map (instTy ms))
  | .arr ps r e => .arr (ps.map (instTy ms)) (instTy ms r) e
  | t => t

def instantiate (n : Nat) (t : Ty) : M Ty := do
  let mut ms : Array Ty := #[]
  for _ in [0:n] do ms := ms.push (← fresh)
  return instTy ms t

/-- Replace the metas not in `keep` and every skolem by `gen` variables. -/
partial def genGo (keep : List Nat) (tab : Array Ty) : Ty → Array Ty × Ty
  | t@(.mv n) => if keep.contains n then (tab, t) else slot tab t
  | t@(.skolem _) => slot tab t
  | .tup ts =>
    let (tab, ts') := ts.foldl (fun (tab, acc) t =>
      let (tab, t') := genGo keep tab t; (tab, acc ++ [t'])) (tab, [])
    (tab, .tup ts')
  | .arr ps r e =>
    let (tab, ps') := ps.foldl (fun (tab, acc) t =>
      let (tab, t') := genGo keep tab t; (tab, acc ++ [t'])) (tab, [])
    let (tab, r') := genGo keep tab r
    (tab, .arr ps' r' e)
  | t => (tab, t)
where
  slot (tab : Array Ty) (t : Ty) : Array Ty × Ty :=
    match tab.findIdx? (· == t) with
    | some i => (tab, .gen i)
    | none => (tab.push t, .gen tab.size)

def generalize (keep : List Nat) (t : Ty) : M (Nat × Ty) := do
  let t ← zonk t
  let (tab, t') := genGo keep #[] t
  return (tab.size, t')

partial def metasOf : Ty → List Nat
  | .mv n => [n]
  | .tup ts => ts.flatMap metasOf
  | .arr ps r _ => ps.flatMap metasOf ++ metasOf r
  | _ => []

def envMetas (env : Env) : M (List Nat) := do
  let mut acc := []
  for (_, b) in env do
    match b with
    | .mono t | .fnMono _ t => acc := acc ++ metasOf (← zonk t)
    | _ => pure ()
  return acc

/-! ### Written types -/

partial def hasNamed : Ty → Bool
  | .named _ => true
  | .tup ts => ts.any hasNamed
  | .arr ps r _ => ps.any hasNamed || hasNamed r
  | _ => false

partial def namedIn : Ty → List String
  | .named s => [s]
  | .tup ts => ts.flatMap namedIn
  | .arr ps r _ => ps.flatMap namedIn ++ namedIn r
  | _ => []

/-- Read a written type, mapping each type variable through `f`, and normalise arrows. -/
partial def elabTy (f : String → Ty) : Ty → M Ty
  | .named s => pure (f s)
  | .tup ts => return .tup (← ts.mapM (elabTy f))
  | .arr ps r e => do
    let r' ← elabTy f r
    -- spec §7 rule 9: the annotation attaches to the innermost arrow; an `!{IO}` outer arrow
    -- with an arrow result has no spelling.
    match r', e with
    | .arr .., .io => reject "ill-formed" "effect on an arrow whose result is an arrow"
    | _, _ => pure (mkArr (← ps.mapM (elabTy f)) r' e)
  | t => pure t

/-! ### Free variables -/

mutual
partial def fvStep (bound : List String) : List Step → Expr → List String
  | [], final => fv bound final
  | .letS x rhs :: rest, final => fv bound rhs ++ fvStep (x :: bound) rest final
  | .exprS e :: rest, final => fv bound e ++ fvStep bound rest final

partial def fv (bound : List String) : Expr → List String
  | .var x => if bound.contains x then [] else [x]
  | .lam x _ b => fv (x :: bound) b
  | .app f as => fv bound f ++ as.flatMap (fv bound)
  | .letIn x r b => fv bound r ++ fv (x :: bound) b
  | .ite c t e => fv bound c ++ fv bound t ++ fv bound e
  | .tup es => es.flatMap (fv bound)
  | .notE e => fv bound e
  | .doB steps final => fvStep bound steps final
  | _ => []
end

/-! ### Expressions -/

def isLam : Expr → Bool
  | .lam .. => true
  | _ => false

/-- The prelude name the stage-1 fragment can reach. -/
def preludeEnv : Env := [("print_str", .poly 0 (.arr [.str] .unit .io))]

/-- Bind a local `let`. -/
def bindLocal (rules : Rules) (env : Env) (x : String) (t : Ty) : M Env := do
  if rules.localLetMono then
    -- spec §5.6 existentials: "a local `let` is monomorphic"
    return (x, .mono t) :: env
  else
    let keep ← envMetas env
    let (n, t') ← generalize keep t
    return (x, .poly n t') :: env

/-- The slots of a call, its result type and the effect the call itself performs. -/
partial def peel (t : Ty) (n : Nat) : M (List Ty × Ty × Eff) := do
  match ← shallow t with
  | .arr ps r e =>
    if n == ps.length then pure (ps, r, e)
    else if n < ps.length then
      -- spec §5.3: applying a function-typed value is checked at run time; typed as the rest
      pure (ps.take n, mkArr (ps.drop n) r e, .pure)
    else
      let (s2, r2, e2) ← peel r (n - ps.length)
      pure (ps ++ s2, r2, e.join e2)
  | .mv m => do
    let mut ps := []
    for _ in [0:n] do ps := ps ++ [← fresh]
    let r ← fresh
    bindMeta m (.arr ps r .pure)
    pure (ps, r, .pure)
  | _ => reject "mismatch" "call of a non-function"

mutual
partial def infer (rules : Rules) (env : Env) : Expr → M (Ty × Eff)
  | .var x =>
    match env.lookup x with
    | none => reject "unknown-var" s!"unknown variable {x}"
    | some (.mono t) => pure (t, .pure)
    | some (.fnMono _ t) => pure (t, .pure)
    | some (.poly n t) => return (← instantiate n t, .pure)
    | some (.fnPoly _ n t) => return (← instantiate n t, .pure)
  | .int _ => pure (.int, .pure)
  | .bool _ => pure (.bool, .pure)
  | .str _ => pure (.str, .pure)
  | .unit => pure (.unit, .pure)
  | .lam x ann body => inferLam rules env none x ann body
  | .app f args => do
    -- spec §5.3: a call saturates a known function; under- and over-application is an error
    let (tf, ef) ← match f with
      | .var x =>
        match env.lookup x with
        | some (.fnMono k _) | some (.fnPoly k _ _) =>
          if k != args.length then reject "arity" s!"{x} expects {k} arguments" else infer rules env f
        | _ => infer rules env f
      | _ => infer rules env f
    let (slots, res, callEff) ← peel tf args.length
    let mut eff := ef.join callEff
    -- spec §7 rule 3: non-lambda arguments are unified against their slots first
    for (a, s) in args.zip slots do
      unless isLam a do
        let (ta, ea) ← infer rules env a
        flow ta s
        eff := eff.join ea
    -- spec §7 rule 3: then each lambda is inferred with its parameter type from the slot
    for (a, s) in args.zip slots do
      match a with
      | .lam x ann body =>
        let (ta, ea) ← inferLam rules env (some s) x ann body
        flow ta s
        eff := eff.join ea
      | _ => pure ()
    pure (res, eff)
  | .letIn x rhs body => do
    let (t, e) ← infer rules env rhs
    -- spec §5.2.1: the right-hand side of a `let … in` must be pure
    unless e == .pure do reject "let-rhs-io" "effectful right-hand side"
    let env' ← bindLocal rules env x t
    infer rules env' body
  | .ite c t e => do
    -- spec §7 rule 2: `if` condition must be Bool
    let (tc, ec) ← infer rules env c
    unless (← try flow tc .bool; pure true catch _ => pure false) do
      reject "if-cond" "condition is not Bool"
    let (tt, et) ← infer rules env t
    let (te, ee) ← infer rules env e
    -- spec §5.4: both branches have a common type neither owns; spec §7 rule 8: peer join
    let ty ← joinTy true tt te
    pure (ty, ec.join (et.join ee))
  | .tup es => do
    -- spec §7 rule 12: tuples are structural, exact arity
    let mut ts := []
    let mut eff := Eff.pure
    for e in es do
      let (t, ef) ← infer rules env e
      ts := ts ++ [t]
      eff := eff.join ef
    pure (.tup ts, eff)
  | .notE e => do
    -- spec §5.3b: the operand of `!` must be Bool
    let (t, eff) ← infer rules env e
    unless (← try flow t .bool; pure true catch _ => pure false) do
      reject "not-bool" "operand of ! is not Bool"
    pure (.bool, eff)
  | .doB steps final => do
    let mut env' := env
    let mut eff := Eff.pure
    for st in steps do
      match st with
      | .letS x rhs =>
        let (t, e) ← infer rules env' rhs
        -- spec §5.2.1a: a `do` let binds like `let … in`; its right-hand side must be pure
        unless e == .pure do reject "let-rhs-io" "effectful right-hand side"
        env' ← bindLocal rules env' x t
      | .exprS e =>
        let (_, ef) ← infer rules env' e
        eff := eff.join ef
    let (t, ef) ← infer rules env' final
    pure (t, eff.join ef)

partial def inferLam (rules : Rules) (env : Env) (slot : Option Ty) (x : String)
    (ann : Option Ty) (body : Expr) : M (Ty × Eff) := do
  let pt ← match ann with
    | some t =>
      -- spec §5.3: a type variable in a lambda parameter annotation is rejected in v0
      if hasNamed t then reject "lam-ann-var" "type variable in a lambda parameter annotation"
      elabTy (fun _ => .unit) t
    | none => fresh
  -- spec §7 rule 4: in argument position the parameter type comes from the callee's slot
  if let some s := slot then
    match ← shallow s with
    | .arr (p :: _) _ _ => flow p pt
    | _ => pure ()
  let (tb, eb) ← infer rules ((x, .mono pt) :: env) body
  -- a lambda is a value: it is pure, and its arrow carries the body's effect
  pure (mkArr [pt] tb eb, .pure)
end

/-! ### Declarations -/

structure FnInfo where
  name : String
  params : List (String × Option Ty)
  sig : Option (Ty × Eff)
  body : Expr
  deriving Inhabited

def FnInfo.complete (f : FnInfo) : Bool :=
  f.sig.isSome && f.params.all (·.2.isSome)

def FnInfo.writtenVars (f : FnInfo) : List String :=
  let vs := (f.params.flatMap fun (_, t) => match t with | some t => namedIn t | none => []) ++
    (match f.sig with | some (t, _) => namedIn t | none => [])
  vs.foldl (fun acc v => if acc.contains v then acc else acc ++ [v]) []

/-- The member's type under `f`: written variables map through `m`; omitted types are fresh. -/
def FnInfo.build (f : FnInfo) (m : String → Ty) : M (List Ty × Ty × Eff × Ty) := do
  let mut ps := []
  for (_, t) in f.params do
    match t with
    | some t => ps := ps ++ [← elabTy m t]
    | none => ps := ps ++ [← fresh]
  let (r, e) ← match f.sig with
    | some (t, e) => do pure (← elabTy m t, e)
    | none => do pure (← fresh, Eff.pure)
  match r, e with
  | .arr .., .io => reject "ill-formed" "effect on a function returning a function"
  | _, _ => pure ()
  pure (ps, r, e, mkArr ps r e)

/-- A body is checked against the declared return type and effect. -/
def checkBody (rules : Rules) (env : Env) (params : List (String × Ty)) (ret : Ty) (eff : Eff)
    (body : Expr) : M Unit := do
  let env' := params.foldl (fun env (x, t) => (x, Bind.mono t) :: env) env
  let (tb, eb) ← infer rules env' body
  flow tb ret
  -- spec §7 rule 8, rule 11: a pure body may not call `!{IO}` functions
  unless eb.le eff do reject "io-in-pure" "performs IO but is declared pure"

/-- The scheme of a complete signature: written variables become `gen`. -/
def FnInfo.scheme (f : FnInfo) : M (Nat × Ty) := do
  let vs := f.writtenVars
  let m := fun s => match vs.findIdx? (· == s) with | some i => Ty.gen i | none => Ty.unit
  let (_, _, _, t) ← f.build m
  pure (vs.length, t)

def inferFnGroup (rules : Rules) (base : Env) (fns : List FnInfo) : M Env := do
  -- spec §7 rule 16: a group is typed monomorphically, then generalised at the boundary.
  -- Members with a complete signature are visible to peers already quantified (rule 16).
  let mut groupEnv : Env := []
  let mut built : List (FnInfo × List Ty × Ty × Eff × Ty) := []
  for f in fns do
    let vs := f.writtenVars
    let mut sks := []
    for v in vs do sks := sks ++ [(v, ← freshSkolem)]
    let m := fun s => (sks.lookup s).getD .unit
    let (ps, r, e, t) ← f.build m
    built := built ++ [(f, ps, r, e, t)]
    if f.complete then
      let (n, sch) ← f.scheme
      groupEnv := (f.name, .fnPoly f.params.length n sch) :: groupEnv
    else
      groupEnv := (f.name, .fnMono f.params.length t) :: groupEnv
  let env := groupEnv ++ base
  for (f, ps, r, e, _) in built do
    let params := (f.params.map (·.1)).zip ps
    checkBody rules env params r e f.body
  let mut out : Env := []
  for (f, _, _, _, t) in built do
    if f.complete then
      let (n, sch) ← f.scheme
      out := (f.name, .fnPoly f.params.length n sch) :: out
    else
      let (n, t') ← generalize [] t
      out := (f.name, .fnPoly f.params.length n t') :: out
  return out ++ base

/-- A top-level `let`: pure initialiser, generalised. -/
def inferLet (rules : Rules) (base : Env) (name : String) (ann : Option Ty) (init : Expr) :
    M Env := do
  let (t, e) ← infer rules base init
  -- spec §5.2: top-level `let` initialisers must be pure
  unless e == .pure do reject "let-io" "top-level `let` performs IO"
  if let some a := ann then
    -- spec §5.2: the annotation is unified with the initialiser; its variables are ordinary
    let mut sks : List (String × Ty) := []
    for v in namedIn a do
      unless sks.any (·.1 == v) do sks := sks ++ [(v, ← fresh)]
    let a' ← elabTy (fun s => (sks.lookup s).getD .unit) a
    flow t a'
  let (n, t') ← generalize [] t
  return (name, .poly n t') :: base

inductive Node where
  | fnN (f : FnInfo)
  | letN (name : String) (ann : Option Ty) (init : Expr)
  deriving Inhabited

def Node.name : Node → String
  | .fnN f => f.name
  | .letN n _ _ => n

def Node.refs (names : List String) : Node → List String
  | .fnN f => (fv (f.params.map (·.1)) f.body).filter names.contains
  | .letN _ _ i => (fv [] i).filter names.contains

/-- The checker. `accept` is `ok ()`; a rejection carries `<key>: <detail>`. -/
def checkProgram (rules : Rules := {}) (p : Program) : Except String Unit := do
  let mut nodes : Array Node := #[]
  let mut mains : List (Option (Ty × Eff) × Expr) := []
  for d in p.decls do
    match d with
    | .fn n ps sg b => nodes := nodes.push (.fnN ⟨n, ps, sg, b⟩)
    | .letD n a i => nodes := nodes.push (.letN n a i)
    | .mainFn sg b => mains := mains ++ [(sg, b)]
  let names := nodes.toList.map Node.name
  -- spec §5.1: a name may be declared at most once at a module's top level
  for (n, i) in names.zipIdx do
    if names.zipIdx.any (fun (m, j) => m == n && j != i) then
      throw s!"duplicate: {n} is defined more than once"
  if mains.length > 1 then throw "duplicate: main is defined more than once"
  let idx (n : String) : Option Nat := names.findIdx? (· == n)
  -- reference edges, and the declaration-order rules
  let mut edges : Array (List Nat) := #[]
  for (nd, i) in nodes.toList.zipIdx do
    let rs := nd.refs names
    let mut es := []
    for r in rs do
      let j := (idx r).getD 0
      match nd, nodes[j]! with
      | .fnN _, .letN .. =>
        -- spec §7 rule 16: a `fn` may not be moved above a top-level `let` it depends on
        if j > i then throw s!"fn-before-let: {nd.name} precedes the `let` {r} it uses"
      | .letN .., .letN .. =>
        -- spec §7 rule 16: a `let` must already be in scope where it is used
        if j ≥ i then throw s!"unknown-var: let {nd.name} uses {r} before its declaration"
      | _, _ => pure ()
      es := es ++ [j]
    edges := edges.push es
  -- reachability, to find binding groups (minimal mutually dependent sets)
  let n := nodes.size
  let mut reach : Array (Array Bool) := Array.replicate n (Array.replicate n false)
  for i in [0:n] do
    for j in edges[i]! do
      reach := reach.set! i (reach[i]!.set! j true)
  for k in [0:n] do
    for i in [0:n] do
      for j in [0:n] do
        if reach[i]![k]! && reach[k]![j]! then
          reach := reach.set! i (reach[i]!.set! j true)
  let mut groups : List (List Nat) := []
  let mut seen : List Nat := []
  for i in [0:n] do
    unless seen.contains i do
      let g := (List.range n).filter fun j => j == i || (reach[i]![j]! && reach[j]![i]!)
      seen := seen ++ g
      groups := groups ++ [g]
  -- groups in dependency order: a group runs after every group it reaches
  let mut order : List (List Nat) := []
  let mut pending := groups
  let mut guardN := n + 1
  while !pending.isEmpty && guardN > 0 do
    guardN := guardN - 1
    let ready := pending.filter fun g =>
      g.all fun i => (edges[i]!).all fun j => g.contains j || order.any (·.contains j)
    match ready with
    | [] => throw "internal: no group order"
    | g :: _ =>
      order := order ++ [g]
      pending := pending.filter (· != g)
  let act : M Unit := do
    let mut env := preludeEnv
    for g in order do
      let members := g.map fun i => nodes[i]!
      let fnMembers := members.filterMap fun nd => match nd with | .fnN f => some f | _ => none
      match members with
      | [Node.letN nm a i] =>
        if (edges[g.head!]!).contains g.head! then
          reject "unknown-var" s!"let {nm} refers to itself"
        env ← inferLet rules env nm a i
      | _ =>
        if fnMembers.length != members.length then
          reject "let-in-group" "a top-level `let` in a recursive group"
        env ← inferFnGroup rules env fnMembers
    for (sg, body) in mains do
      -- spec §10.10 / §7 rule 10: `main` is `fn main() -> Unit !{IO}` (or `Int !{IO}`), written out
      match sg with
      | none => reject "main-sig" "main must declare its return type and effect"
      | some (r, e) =>
        unless r == .unit || r == .int do reject "main-sig" "main must return Unit or Int"
        unless e == .io do reject "main-sig" "main must declare the IO effect"
        checkBody rules env [] r e body
  match (act.run {}) with
  | .ok _ => pure ()
  | .error msg => throw msg

end SproutCore
