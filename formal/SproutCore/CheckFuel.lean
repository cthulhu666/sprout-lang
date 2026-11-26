import SproutCore.Check

/-!
# Fuel monotonicity of the checker (docs/lean-model-v0.md §9, 1b)

`checkProgram_fuel_mono`: more fuel never changes a verdict. A run that does not end in
`CErr.fuel` at budget `n` gives the same result at every `m ≥ n`.

The proof is a refinement relation on checker computations, `Le x y`: wherever `x` finishes at
budget `n` from state `s`, `y` at `m ≥ n` from `s` gives the same result and the same final state.
The state is part of the claim, or `bind` would not compose. The budget is read by the wrappers
(`shallow`, `zonk`, `flow`, `joinTy`, `peel`) and passed on to their `…F` versions, so the `…F`
lemmas relate `F k` to `F j` at two budgets, by induction on `k`; every other function is shown
`Le` to itself by the same closure rules (`Le.bind`, `Le.ite`, `Le.tryCatch`, loops over lists
and ranges), which `le_tac` applies. `Le.tryCatch` needs the handler to re-throw `.fuel`, which is
what `expectBool` does.
-/

namespace SproutCore

/-- Run a checker computation at budget `n` from state `s`. -/
abbrev M.go {α : Type} (x : M α) (n : Nat) (s : CState) : Except CErr (α × CState) :=
  (x.run n).run s

/-- `x ⊑ y`: wherever `x` finishes at budget `n` (no `.fuel`), `y` at a budget `m ≥ n`
gives the same result and the same final state. -/
def Le {α : Type} (x y : M α) : Prop :=
  ∀ n m s, n ≤ m → x.go n s ≠ .error .fuel → y.go m s = x.go n s

theorem go_bind {α β : Type} (x : M α) (f : α → M β) (n : Nat) (s : CState) :
    (x >>= f).go n s = (x.go n s).bind fun p => (f p.1).go n p.2 := by
  rfl

theorem Le.bind {α β : Type} {x x' : M α} {f f' : α → M β} (hx : Le x x')
    (hf : ∀ a, Le (f a) (f' a)) : Le (x >>= f) (x' >>= f') := by
  intro n m s hnm h
  rw [go_bind] at h ⊢
  rw [go_bind]
  cases hxn : x.go n s with
  | error e =>
    rw [hxn] at h
    have : e ≠ .fuel := by rintro rfl; exact h rfl
    rw [hx n m s hnm (by rw [hxn]; simpa using this), hxn]
    rfl
  | ok p =>
    rw [hxn] at h
    rw [hx n m s hnm (by rw [hxn]; simp), hxn]
    exact hf p.1 n m p.2 hnm h

theorem Le.pure {α : Type} (a : α) : Le (pure a : M α) (pure a) := by
  intro n m s _ _; rfl

theorem Le.get : Le (get : M CState) get := by
  intro n m s _ _; rfl

theorem Le.set (s' : CState) : Le (set s' : M PUnit) (set s') := by
  intro n m s _ _; rfl

theorem Le.modify (f : CState → CState) : Le (modify f : M PUnit) (modify f) := by
  intro n m s _ _; rfl

theorem Le.reject {α : Type} (key detail : String) :
    Le (reject key detail : M α) (reject key detail) := by
  intro n m s _ _; rfl

theorem Le.fuel {α : Type} (y : M α) : Le (throw .fuel : M α) y := by
  intro n m s _ h; exact absurd rfl h

theorem Le.ite {α : Type} {c : Prop} [Decidable c] {x x' y y' : M α} (hx : Le x x')
    (hy : Le y y') :
    Le (if c then x else y) (if c then x' else y') := by
  by_cases h : c <;> simp [*]

theorem Le.mapM {α β : Type} {l : List α} {f f' : α → M β} (hf : ∀ a, Le (f a) (f' a)) :
    Le (l.mapM f) (l.mapM f') := by
  induction l with
  | nil => exact Le.pure _
  | cons a l ih =>
    simp only [List.mapM_cons]
    exact Le.bind (hf a) fun b => Le.bind ih fun bs => Le.pure _

/-! ### The metavariable walks -/

theorem shallowF_le : ∀ k j, k ≤ j → ∀ t, Le (shallowF k t) (shallowF j t) := by
  intro k
  induction k with
  | zero => intro j _ t; exact Le.fuel _
  | succ k ih =>
    intro j hj t
    obtain ⟨j, rfl⟩ : ∃ j', j = j' + 1 := ⟨j - 1, by omega⟩
    cases t <;> simp only [shallowF] <;> try exact Le.pure _
    refine Le.bind Le.get fun s => ?_
    split
    · exact ih j (by omega) _
    · exact Le.pure _

theorem Le.read_wrap {β : Type} {F : Nat → M β} (h : ∀ k j, k ≤ j → Le (F k) (F j)) :
    Le (read >>= F) (read >>= F) := by
  intro n m s hnm hn
  exact h n m hnm n m s hnm hn

theorem shallow_le (t : Ty) : Le (shallow t) (shallow t) :=
  Le.read_wrap fun k j hkj => shallowF_le k j hkj t

theorem zonkF_le : ∀ k j, k ≤ j → ∀ t, Le (zonkF k t) (zonkF j t) := by
  intro k
  induction k with
  | zero => intro j _ t; exact Le.fuel _
  | succ k ih =>
    intro j hj t
    obtain ⟨j, rfl⟩ : ∃ j', j = j' + 1 := ⟨j - 1, by omega⟩
    simp only [zonkF]
    refine Le.bind (shallowF_le k j (by omega) t) fun t' => ?_
    split
    · exact Le.bind (Le.mapM (fun a => ih j (by omega) a)) fun _ => Le.pure _
    · exact Le.bind (Le.mapM (fun a => ih j (by omega) a)) fun _ =>
        Le.bind (ih j (by omega) _) fun _ => Le.pure _
    · exact Le.bind (Le.mapM (fun a => ih j (by omega) a)) fun _ => Le.pure _
    · exact Le.pure _

theorem zonk_le (t : Ty) : Le (zonk t) (zonk t) :=
  Le.read_wrap fun k j hkj => zonkF_le k j hkj t

theorem bindMeta_le (n : Nat) (t : Ty) : Le (bindMeta n t) (bindMeta n t) := by
  unfold bindMeta
  refine Le.bind (zonk_le t) fun t' => ?_
  dsimp only
  apply Le.ite
  · exact Le.bind (Le.reject _ _) fun _ => Le.modify _
  · exact Le.modify _

theorem Le.forIn_list {α β : Type} {f f' : α → β → M (ForInStep β)}
    (hf : ∀ a b, Le (f a b) (f' a b)) (l : List α) (init : β) :
    Le (ForIn.forIn l init f) (ForIn.forIn l init f') := by
  induction l generalizing init with
  | nil => simp only [List.forIn_nil]; exact Le.pure _
  | cons a l ih =>
    simp only [List.forIn_cons]
    refine Le.bind (hf a init) fun r => ?_
    cases r with
    | done b => exact Le.pure _
    | yield b => exact ih b

theorem Le.forIn_range {β : Type} {f f' : Nat → β → M (ForInStep β)}
    (hf : ∀ a b, Le (f a b) (f' a b)) (r : Std.Legacy.Range) (init : β) :
    Le (ForIn.forIn r init f) (ForIn.forIn r init f') := by
  simp only [Std.Legacy.Range.forIn_eq_forIn_range']
  exact Le.forIn_list hf _ _

/-! ### Proof automation

`le_step` takes one structural step on a goal `Le x y`; later `macro_rules` add leaf lemmas, and the
most recent alternative is tried first. -/

syntax "le_step" : tactic
syntax "le_tac" : tactic

macro_rules
  | `(tactic| le_tac) => `(tactic| repeat' le_step)

macro_rules
  | `(tactic| le_step) => `(tactic| split)
macro_rules
  | `(tactic| le_step) => `(tactic| with_reducible refine Le.bind ?_ (fun _ => ?_))
macro_rules
  | `(tactic| le_step) => `(tactic| with_reducible apply Le.ite)
macro_rules
  | `(tactic| le_step) => `(tactic| (apply Le.forIn_range; intro _ _; try dsimp only))
macro_rules
  | `(tactic| le_step) => `(tactic| (apply Le.forIn_list; intro _ _; try dsimp only))
macro_rules
  | `(tactic| le_step) => `(tactic| with_reducible exact Le.pure _)
macro_rules
  | `(tactic| le_step) => `(tactic| with_reducible exact Le.get)
macro_rules
  | `(tactic| le_step) => `(tactic| with_reducible exact Le.set _)
macro_rules
  | `(tactic| le_step) => `(tactic| with_reducible exact Le.modify _)
macro_rules
  | `(tactic| le_step) => `(tactic| with_reducible exact Le.reject _ _)
macro_rules
  | `(tactic| le_step) => `(tactic| with_reducible exact Le.fuel _)
macro_rules
  | `(tactic| le_step) => `(tactic| with_reducible exact bindMeta_le _ _)
macro_rules
  | `(tactic| le_step) => `(tactic| with_reducible exact shallow_le _)
macro_rules
  | `(tactic| le_step) => `(tactic| with_reducible exact zonk_le _)
macro_rules
  | `(tactic| le_step) => `(tactic| (with_reducible apply_assumption <;> omega))

theorem flowF_le : ∀ k j, k ≤ j → ∀ a e, Le (flowF k a e) (flowF j a e) := by
  intro k
  induction k with
  | zero => intro j _ a e; exact Le.fuel _
  | succ k ih =>
    intro j hj a e
    obtain ⟨j, rfl⟩ : ∃ j', j = j' + 1 := ⟨j - 1, by omega⟩
    simp only [flowF]
    refine Le.bind (shallowF_le k j (by omega) a) fun a' => ?_
    refine Le.bind (shallowF_le k j (by omega) e) fun e' => ?_
    le_tac

macro_rules
  | `(tactic| le_step) => `(tactic| (apply Le.mapM; intro _; try dsimp only))

theorem joinTyF_le :
    ∀ k j, k ≤ j → ∀ pos a b, Le (joinTyF k pos a b) (joinTyF j pos a b) := by
  intro k
  induction k with
  | zero => intro j _ pos a b; exact Le.fuel _
  | succ k ih =>
    intro j hj pos a b
    obtain ⟨j, rfl⟩ : ∃ j', j = j' + 1 := ⟨j - 1, by omega⟩
    simp only [joinTyF]
    refine Le.bind (shallowF_le k j (by omega) a) fun a' => ?_
    refine Le.bind (shallowF_le k j (by omega) b) fun b' => ?_
    le_tac

theorem fresh_le : Le fresh fresh := by
  unfold fresh; le_tac

theorem freshSkolem_le : Le freshSkolem freshSkolem := by
  unfold freshSkolem; le_tac

macro_rules
  | `(tactic| le_step) => `(tactic| with_reducible exact fresh_le)
macro_rules
  | `(tactic| le_step) => `(tactic| with_reducible exact freshSkolem_le)

theorem peelF_le : ∀ k j, k ≤ j → ∀ t n, Le (peelF k t n) (peelF j t n) := by
  intro k
  induction k with
  | zero => intro j _ t n; exact Le.fuel _
  | succ k ih =>
    intro j hj t n
    obtain ⟨j, rfl⟩ : ∃ j', j = j' + 1 := ⟨j - 1, by omega⟩
    simp only [peelF]
    refine Le.bind (shallowF_le k j (by omega) t) fun t' => ?_
    le_tac

/-! ### The wrappers, which read the budget -/

theorem flow_le (a e : Ty) : Le (flow a e) (flow a e) :=
  Le.read_wrap fun k j hkj => flowF_le k j hkj a e

theorem joinTy_le (pos : Bool) (a b : Ty) : Le (joinTy pos a b) (joinTy pos a b) :=
  Le.read_wrap fun k j hkj => joinTyF_le k j hkj pos a b

theorem peel_le (t : Ty) (n : Nat) : Le (peel t n) (peel t n) :=
  Le.read_wrap fun k j hkj => peelF_le k j hkj t n

macro_rules
  | `(tactic| le_step) => `(tactic| with_reducible exact flow_le _ _)
macro_rules
  | `(tactic| le_step) => `(tactic| with_reducible exact joinTy_le _ _ _)
macro_rules
  | `(tactic| le_step) => `(tactic| with_reducible exact peel_le _ _)

theorem Le.tryCatch {α : Type} {x x' : M α} {h h' : CErr → M α} (hx : Le x x')
    (hh : ∀ k d, Le (h (.reject k d)) (h' (.reject k d))) (hf : h .fuel = throw .fuel) :
    Le (tryCatch x h) (tryCatch x' h') := by
  intro n m s hnm hn
  change (MonadExcept.tryCatch (x.go n s) fun e => (h e).go n s) ≠ _ at hn
  change (MonadExcept.tryCatch (x'.go m s) fun e => (h' e).go m s) =
    MonadExcept.tryCatch (x.go n s) fun e => (h e).go n s
  cases hxn : x.go n s with
  | ok p =>
    have h1 := hx n m s hnm (by rw [hxn]; simp)
    rw [hxn] at h1
    rw [h1]; rfl
  | error e =>
    cases e with
    | fuel =>
      rw [hxn] at hn
      exact absurd (show (h .fuel).go n s = _ by rw [hf]; rfl) hn
    | reject k d =>
      have h1 := hx n m s hnm (by rw [hxn]; simp)
      rw [hxn] at h1
      rw [hxn] at hn
      rw [h1]
      exact hh _ _ n m s hnm hn

macro_rules
  | `(tactic| le_step) => `(tactic| (refine Le.tryCatch ?_ (fun _ _ => ?_) rfl; try dsimp only))

theorem expectBool_le (t : Ty) (key detail : String) :
    Le (expectBool t key detail) (expectBool t key detail) := by
  unfold expectBool
  le_tac

macro_rules
  | `(tactic| le_step) => `(tactic| with_reducible exact expectBool_le _ _ _)

theorem instantiate_le (n : Nat) (t : Ty) : Le (instantiate n t) (instantiate n t) := by
  unfold instantiate
  le_tac

theorem generalize_le (keep : List Nat) (t : Ty) : Le (generalize keep t) (generalize keep t) := by
  unfold generalize
  le_tac

theorem envMetas_le (env : Env) : Le (envMetas env) (envMetas env) := by
  unfold envMetas
  le_tac

mutual
theorem elabTy_le (f : String → Ty) : ∀ t, Le (elabTy f t) (elabTy f t)
  | .named s => by unfold elabTy; le_tac
  | .tup ts => by
    unfold elabTy
    refine Le.bind (elabTyList_le f ts) fun _ => Le.pure _
  | .arr ps r e => by
    unfold elabTy
    refine Le.bind (elabTy_le f r) fun r' => ?_
    split
    · exact Le.reject _ _
    · exact Le.bind (elabTyList_le f ps) fun _ => Le.pure _
  | .mv _ => by unfold elabTy; le_tac
  | .skolem _ => by unfold elabTy; le_tac
  | .gen _ => by unfold elabTy; le_tac
  | .int => by unfold elabTy; le_tac
  | .bool => by unfold elabTy; le_tac
  | .str => by unfold elabTy; le_tac
  | .unit => by unfold elabTy; le_tac
  | .adt _ ts => by
    unfold elabTy
    refine Le.bind (elabTyList_le f ts) fun _ => Le.pure _

theorem elabTyList_le (f : String → Ty) : ∀ ts, Le (elabTyList f ts) (elabTyList f ts)
  | [] => by unfold elabTyList; le_tac
  | t :: ts => by
    unfold elabTyList
    exact Le.bind (elabTy_le f t) fun _ => Le.bind (elabTyList_le f ts) fun _ => Le.pure _
end

macro_rules
  | `(tactic| le_step) => `(tactic| with_reducible exact instantiate_le _ _)
macro_rules
  | `(tactic| le_step) => `(tactic| with_reducible exact generalize_le _ _)
macro_rules
  | `(tactic| le_step) => `(tactic| with_reducible exact envMetas_le _)
macro_rules
  | `(tactic| le_step) => `(tactic| with_reducible exact elabTy_le _ _)

theorem bindLocal_le (rules : Rules) (env : Env) (x : String) (t : Ty) :
    Le (bindLocal rules env x t) (bindLocal rules env x t) := by
  unfold bindLocal
  le_tac

macro_rules
  | `(tactic| le_step) => `(tactic| with_reducible exact bindLocal_le _ _ _ _)

theorem lamParam_le (slot : Option Ty) (ann : Option Ty) :
    Le (lamParam slot ann) (lamParam slot ann) := by
  unfold lamParam
  dsimp only
  le_tac

theorem arityCheck_le (env : Env) (f : Expr) (n : Nat) :
    Le (arityCheck env f n) (arityCheck env f n) := by
  unfold arityCheck
  le_tac

theorem ctorArity_le (tab : Table) (f : Expr) (n : Nat) :
    Le (ctorArity tab f n) (ctorArity tab f n) := by
  unfold ctorArity
  le_tac

theorem freshN_le (n : Nat) : Le (freshN n) (freshN n) := by
  unfold freshN
  le_tac

macro_rules
  | `(tactic| le_step) => `(tactic| with_reducible exact freshN_le _)

mutual
theorem checkPat_le (tab : Table) : ∀ (p : Pat) (t : Ty), Le (checkPat tab p t) (checkPat tab p t)
  | .wild, t => by unfold checkPat; le_tac
  | .var _, t => by unfold checkPat; le_tac
  | .int _, t => by unfold checkPat; le_tac
  | .bool _, t => by unfold checkPat; le_tac
  | .str _, t => by unfold checkPat; le_tac
  | .unit, t => by unfold checkPat; le_tac
  | .tup ps, t => by
    have hs : ∀ ts, Le (checkPats tab ps ts) (checkPats tab ps ts) := checkPats_le tab ps
    unfold checkPat
    le_tac
  | .ctor _ ps, t => by
    have hs : ∀ ts, Le (checkPats tab ps ts) (checkPats tab ps ts) := checkPats_le tab ps
    unfold checkPat
    le_tac

theorem checkPats_le (tab : Table) :
    ∀ (ps : List Pat) (ts : List Ty), Le (checkPats tab ps ts) (checkPats tab ps ts)
  | p :: ps, t :: ts => by
    have h1 : Le (checkPat tab p t) (checkPat tab p t) := checkPat_le tab p t
    have h2 : Le (checkPats tab ps ts) (checkPats tab ps ts) := checkPats_le tab ps ts
    unfold checkPats
    le_tac
  | [], _ => by unfold checkPats; le_tac
  | _ :: _, [] => by unfold checkPats; le_tac
end

macro_rules
  | `(tactic| le_step) => `(tactic| with_reducible exact lamParam_le _ _)
macro_rules
  | `(tactic| le_step) => `(tactic| with_reducible exact ctorArity_le _ _ _)
macro_rules
  | `(tactic| le_step) => `(tactic| with_reducible exact checkPat_le _ _ _)
macro_rules
  | `(tactic| le_step) => `(tactic| with_reducible exact arityCheck_le _ _ _)

/-! ### Expressions -/

mutual
theorem infer_le (rules : Rules) :
    ∀ (env : Env) (e : Expr), Le (infer rules env e) (infer rules env e)
  | env, .var x => by
    unfold infer
    le_tac
  | env, .int _ => by unfold infer; le_tac
  | env, .bool _ => by unfold infer; le_tac
  | env, .str _ => by unfold infer; le_tac
  | env, .unit => by unfold infer; le_tac
  | env, .lam x ann body => by
    have hb : ∀ env', Le (infer rules env' body) (infer rules env' body) :=
      fun env' => infer_le rules env' body
    unfold infer
    le_tac
  | env, .app f args => by
    have hf : ∀ env', Le (infer rules env' f) (infer rules env' f) :=
      fun env' => infer_le rules env' f
    have hp : ∀ slots eff, Le (inferPlainArgs rules env args slots eff)
        (inferPlainArgs rules env args slots eff) := fun slots eff =>
      inferPlainArgs_le rules env args slots eff
    have hl : ∀ slots eff, Le (inferLamArgs rules env args slots eff)
        (inferLamArgs rules env args slots eff) := fun slots eff =>
      inferLamArgs_le rules env args slots eff
    unfold infer
    le_tac
  | env, .letIn x rhs body => by
    have hr : ∀ env', Le (infer rules env' rhs) (infer rules env' rhs) :=
      fun env' => infer_le rules env' rhs
    have hb : ∀ env', Le (infer rules env' body) (infer rules env' body) :=
      fun env' => infer_le rules env' body
    unfold infer
    le_tac
  | env, .ite c t e => by
    have hc : ∀ env', Le (infer rules env' c) (infer rules env' c) :=
      fun env' => infer_le rules env' c
    have ht : ∀ env', Le (infer rules env' t) (infer rules env' t) :=
      fun env' => infer_le rules env' t
    have he : ∀ env', Le (infer rules env' e) (infer rules env' e) :=
      fun env' => infer_le rules env' e
    unfold infer
    le_tac
  | env, .tup es => by
    have hs : Le (inferTuple rules env es) (inferTuple rules env es) := inferTuple_le rules env es
    unfold infer
    le_tac
  | env, .notE e => by
    have he : ∀ env', Le (infer rules env' e) (infer rules env' e) :=
      fun env' => infer_le rules env' e
    unfold infer
    le_tac
  | env, .doB steps final => by
    have hs : Le (inferSteps rules env steps) (inferSteps rules env steps) :=
      inferSteps_le rules env steps
    have hf : ∀ env', Le (infer rules env' final) (infer rules env' final) :=
      fun env' => infer_le rules env' final
    unfold infer
    le_tac
  | env, .ctor _ => by unfold infer; le_tac
  | env, .matchE scrut arms => by
    have hs : Le (infer rules env scrut) (infer rules env scrut) := infer_le rules env scrut
    have ha : ∀ st acc eff, Le (inferArms rules env st arms acc eff)
        (inferArms rules env st arms acc eff) := fun st acc eff =>
      inferArms_le rules env st arms acc eff
    unfold infer
    repeat' (first | le_step | dsimp only)

theorem inferArms_le (rules : Rules) (env : Env) (st : Ty) :
    ∀ (arms : List (Pat × Expr)) (acc : Option Ty) (eff : Eff),
      Le (inferArms rules env st arms acc eff) (inferArms rules env st arms acc eff)
  | [], acc, eff => by unfold inferArms; le_tac
  | (p, e) :: arms, acc, eff => by
    have he : ∀ env', Le (infer rules env' e) (infer rules env' e) :=
      fun env' => infer_le rules env' e
    have hr : ∀ acc eff, Le (inferArms rules env st arms acc eff)
        (inferArms rules env st arms acc eff) := fun acc eff =>
      inferArms_le rules env st arms acc eff
    unfold inferArms
    le_tac

theorem inferPlainArgs_le (rules : Rules) (env : Env) :
    ∀ (args : List Expr) (slots : List Ty) (eff : Eff),
      Le (inferPlainArgs rules env args slots eff) (inferPlainArgs rules env args slots eff)
  | a :: as, s :: ss, eff => by
    have ha : Le (infer rules env a) (infer rules env a) := infer_le rules env a
    have hr : ∀ ss eff, Le (inferPlainArgs rules env as ss eff)
        (inferPlainArgs rules env as ss eff) :=
      fun ss eff => inferPlainArgs_le rules env as ss eff
    unfold inferPlainArgs
    le_tac
  | [], _, eff => by unfold inferPlainArgs; le_tac
  | _ :: _, [], eff => by unfold inferPlainArgs; le_tac

theorem inferLamArgs_le (rules : Rules) (env : Env) :
    ∀ (args : List Expr) (slots : List Ty) (eff : Eff),
      Le (inferLamArgs rules env args slots eff) (inferLamArgs rules env args slots eff)
  | a :: as, s :: ss, eff => by
    have hr : ∀ ss eff, Le (inferLamArgs rules env as ss eff)
        (inferLamArgs rules env as ss eff) :=
      fun ss eff => inferLamArgs_le rules env as ss eff
    cases a with
    | lam x ann body =>
      have hb : ∀ env', Le (infer rules env' body) (infer rules env' body) :=
        fun env' => infer_le rules env' body
      simp only [inferLamArgs]
      le_tac
    | _ => simp only [inferLamArgs]; le_tac
  | [], _, eff => by simp only [inferLamArgs]; le_tac
  | _ :: _, [], eff => by simp only [inferLamArgs]; le_tac

theorem inferTuple_le (rules : Rules) (env : Env) :
    ∀ (es : List Expr), Le (inferTuple rules env es) (inferTuple rules env es)
  | [] => by unfold inferTuple; le_tac
  | e :: es => by
    have he : Le (infer rules env e) (infer rules env e) := infer_le rules env e
    have hr : Le (inferTuple rules env es) (inferTuple rules env es) := inferTuple_le rules env es
    unfold inferTuple
    le_tac

theorem inferSteps_le (rules : Rules) (env : Env) :
    ∀ (steps : List Step), Le (inferSteps rules env steps) (inferSteps rules env steps)
  | [] => by unfold inferSteps; le_tac
  | .letS x rhs :: rest => by
    have hr : Le (infer rules env rhs) (infer rules env rhs) := infer_le rules env rhs
    have hs : ∀ env', Le (inferSteps rules env' rest) (inferSteps rules env' rest) :=
      fun env' => inferSteps_le rules env' rest
    unfold inferSteps
    le_tac
  | .exprS e :: rest => by
    have he : Le (infer rules env e) (infer rules env e) := infer_le rules env e
    have hs : Le (inferSteps rules env rest) (inferSteps rules env rest) :=
      inferSteps_le rules env rest
    unfold inferSteps
    le_tac
end

macro_rules
  | `(tactic| le_step) => `(tactic| with_reducible exact infer_le _ _ _)

/-! ### Declarations -/

theorem build_le (f : FnInfo) (m : String → Ty) : Le (f.build m) (f.build m) := by
  unfold FnInfo.build
  dsimp only
  le_tac

macro_rules
  | `(tactic| le_step) => `(tactic| with_reducible exact build_le _ _)

theorem checkBody_le (rules : Rules) (env : Env) (params : List (String × Ty)) (ret : Ty)
    (eff : Eff) (body : Expr) : Le (checkBody rules env params ret eff body)
      (checkBody rules env params ret eff body) := by
  unfold checkBody
  dsimp only
  le_tac

theorem scheme_le (f : FnInfo) : Le f.scheme f.scheme := by
  unfold FnInfo.scheme
  dsimp only
  le_tac

macro_rules
  | `(tactic| le_step) => `(tactic| with_reducible exact checkBody_le _ _ _ _ _ _)
macro_rules
  | `(tactic| le_step) => `(tactic| with_reducible exact scheme_le _)

theorem Le.map' {α β : Type} {x x' : M α} (f : α → β) (hx : Le x x') :
    Le (f <$> x) (f <$> x') := by
  rw [map_eq_pure_bind, map_eq_pure_bind]
  exact Le.bind hx fun a => Le.pure _

macro_rules
  | `(tactic| le_step) => `(tactic| (with_reducible apply Le.map'))

theorem inferFnGroup_le (rules : Rules) (base : Env) (fns : List FnInfo) :
    Le (inferFnGroup rules base fns) (inferFnGroup rules base fns) := by
  unfold inferFnGroup
  dsimp only
  le_tac

theorem inferLet_le (rules : Rules) (base : Env) (name : String) (ann : Option Ty) (init : Expr) :
    Le (inferLet rules base name ann init) (inferLet rules base name ann init) := by
  unfold inferLet
  dsimp only
  le_tac

macro_rules
  | `(tactic| le_step) => `(tactic| with_reducible exact inferFnGroup_le _ _ _)
macro_rules
  | `(tactic| le_step) => `(tactic| with_reducible exact inferLet_le _ _ _ _ _)

theorem runPlan_le (rules : Rules) (pl : Plan) : Le (runPlan rules pl) (runPlan rules pl) := by
  unfold runPlan
  dsimp only
  le_tac

/-! ### The theorem -/

/-- More fuel never changes a verdict: a run that finishes within `n` gives the same result at any
`m ≥ n`. Out-of-fuel at `n` is the one case it says nothing about. -/
theorem checkProgram_fuel_mono (rules : Rules) (p : Program) {n m : Nat} (h : n ≤ m)
    (hn : checkProgram rules p n ≠ .error .fuel) :
    checkProgram rules p m = checkProgram rules p n := by
  unfold checkProgram at hn ⊢
  cases ht : checkTypes p with
  | error e => rfl
  | ok tab =>
  cases hp : checkPlan p with
  | error e => simp [Bind.bind, Except.bind]
  | ok pl =>
    let rules' := { rules with tab := tab }
    have key := runPlan_le rules' pl n m {} h
    rw [ht, hp] at hn
    simp only [Bind.bind, Except.bind] at hn ⊢
    change (match (runPlan rules' pl).go m {} with | .ok _ => pure () | .error e => throw e) =
      (match (runPlan rules' pl).go n {} with | .ok _ => pure () | .error e => throw e)
    change (match (runPlan rules' pl).go n {} with | .ok _ => pure () | .error e => throw e) ≠ _
      at hn
    cases hr : (runPlan rules' pl).go n {} with
    | ok v =>
      rw [hr] at hn
      rw [key (by rw [hr]; simp), hr]
    | error e =>
      rw [hr] at hn
      cases e with
      | fuel => exact absurd rfl hn
      | reject k d => rw [key (by rw [hr]; simp), hr]

end SproutCore
