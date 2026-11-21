import SproutCore.FlowSound

/-!
# Pieces of the `infer` soundness proof (docs/lean-model-v0.md §9, 1b step 3a, part 3)

* `Env.app θ env`: the judgment's context for a checker environment under a grounding.
* `Ty.surf`, `Expr.Surf`: the annotations of a source term are surface types: no `.mv`, `.skolem`
  or `.gen`. `Expr.Src` (`CheckWF.lean`) only says no `.mv` and no `.gen`. The judgment's `Ty.elab`
  reads none of the three, so a lambda annotation holding a `.skolem` is accepted by the checker
  and has no derivation. The parser never builds one.
* `elabTy_elab`: the checker's `elabTy` returns what the judgment's `Ty.elab` reads.
* `lamParam_sound`, `arityCheck_sound`, `bindLocal_leaf`: the small checker functions.
-/

namespace SproutCore

open Judgment

set_option linter.unusedVariables false

/-! ### The judgment's context for a checker environment -/

/-- A checker binding under a grounding. A group member inside its group is `fnMono` (or `fnPoly`
when its signature is complete): `FnWit.entryIn` gives it `.fn arity ⟨0, τ⟩` (or its scheme) and
`entryOut` gives `.fn arity s` after the group, as `fnPoly`. -/
def Bind.app (θ : Nat → Ty) : Bind → Binding
  | .mono t => .local (Ty.app θ t)
  | .poly n t => .val ⟨n, Ty.app θ t⟩
  | .fnMono k t => .fn k ⟨0, Ty.app θ t⟩
  | .fnPoly k n t => .fn k ⟨n, Ty.app θ t⟩

def Env.app (θ : Nat → Ty) (env : Env) : Ctx :=
  env.map fun p => (p.1, Bind.app θ p.2)

theorem Env.app_cons (θ : Nat → Ty) (x : String) (b : Bind) (env : Env) :
    Env.app θ ((x, b) :: env) = (x, Bind.app θ b) :: Env.app θ env := rfl

theorem Env.app_lookup (θ : Nat → Ty) (x : String) :
    ∀ env : Env, (Env.app θ env).lookup x = (env.lookup x).map (Bind.app θ)
  | [] => rfl
  | (y, b) :: env => by
    simp only [Env.app_cons, List.lookup_cons]
    split
    · rfl
    · exact Env.app_lookup θ x env

/-! ### Surface types -/

mutual
def Ty.surf : Ty → Bool
  | .named _ => true
  | .mv _ => false
  | .skolem _ => false
  | .gen _ => false
  | .int => true
  | .bool => true
  | .str => true
  | .unit => true
  | .tup ts => Ty.surfList ts
  | .arr ps r _ => Ty.surfList ps && r.surf

def Ty.surfList : List Ty → Bool
  | [] => true
  | t :: ts => t.surf && Ty.surfList ts
end

mutual
def Expr.Surf : Expr → Prop
  | .var _ => True
  | .int _ => True
  | .bool _ => True
  | .str _ => True
  | .unit => True
  | .lam _ ann b => (∀ t, ann = some t → Ty.surf t = true) ∧ b.Surf
  | .app f as => f.Surf ∧ Expr.SurfList as
  | .letIn _ r b => r.Surf ∧ b.Surf
  | .ite c t e => c.Surf ∧ t.Surf ∧ e.Surf
  | .tup es => Expr.SurfList es
  | .notE e => e.Surf
  | .doB steps f => Step.SurfList steps ∧ f.Surf

def Expr.SurfList : List Expr → Prop
  | [] => True
  | e :: es => e.Surf ∧ Expr.SurfList es

def Step.SurfList : List Step → Prop
  | [] => True
  | .letS _ r :: rest => r.Surf ∧ Step.SurfList rest
  | .exprS e :: rest => e.Surf ∧ Step.SurfList rest
end

/-! ### Reading written types -/

mutual
theorem Ty.vars_eq_namedIn : ∀ t : Ty, t.vars = namedIn t
  | .named _ => rfl
  | .mv _ => rfl
  | .skolem _ => rfl
  | .gen _ => rfl
  | .int => rfl
  | .bool => rfl
  | .str => rfl
  | .unit => rfl
  | .tup ts => by simp only [Ty.vars, namedIn]; exact Ty.varsList_eq_namedInList ts
  | .arr ps r _ => by
    simp only [Ty.vars, namedIn]
    rw [Ty.varsList_eq_namedInList ps, Ty.vars_eq_namedIn r]

theorem Ty.varsList_eq_namedInList : ∀ ts : List Ty, Ty.varsList ts = namedInList ts
  | [] => rfl
  | t :: ts => by
    simp only [Ty.varsList, namedInList]
    rw [Ty.vars_eq_namedIn t, Ty.varsList_eq_namedInList ts]
end

mutual
/-- A surface type read with an `ok` map is `ok`. -/
theorem Ty.elab_ok {σ : String → Ty} (hσ : ∀ s, (σ s).ok 0 = true) :
    ∀ (t r : Ty), t.elab σ = some r → r.ok 0 = true
  | .named s, r, h => by
    simp only [Ty.elab, Option.some.injEq] at h
    subst h; exact hσ s
  | .int, r, h => by simp only [Ty.elab, Option.some.injEq] at h; subst h; rfl
  | .bool, r, h => by simp only [Ty.elab, Option.some.injEq] at h; subst h; rfl
  | .str, r, h => by simp only [Ty.elab, Option.some.injEq] at h; subst h; rfl
  | .unit, r, h => by simp only [Ty.elab, Option.some.injEq] at h; subst h; rfl
  | .mv _, r, h => by simp [Ty.elab] at h
  | .skolem _, r, h => by simp [Ty.elab] at h
  | .gen _, r, h => by simp [Ty.elab] at h
  | .tup ts, r, h => by
    simp only [Ty.elab, Option.map_eq_some_iff] at h
    obtain ⟨rs, hrs, rfl⟩ := h
    simp only [Ty.ok]
    exact Ty.elabList_ok hσ ts rs hrs
  | .arr ps r0 e, r, h => by
    simp only [Ty.elab] at h
    split at h
    · rename_i ps' r' hps hr'
      split at h
      · cases h
      · simp only [Option.some.injEq] at h
        subst h
        exact Ty.ok_mkArr (Ty.elabList_ok hσ ps ps' hps) (Ty.elab_ok hσ r0 r' hr')
    · cases h

theorem Ty.elabList_ok {σ : String → Ty} (hσ : ∀ s, (σ s).ok 0 = true) :
    ∀ (ts rs : List Ty), Ty.elabList σ ts = some rs → Ty.okList 0 rs = true
  | [], rs, h => by simp only [Ty.elabList, Option.some.injEq] at h; subst h; rfl
  | t :: ts, rs, h => by
    simp only [Ty.elabList] at h
    split at h
    · rename_i t' ts' ht hts
      simp only [Option.some.injEq] at h
      subst h
      simp only [Ty.okList, Bool.and_eq_true]
      exact ⟨Ty.elab_ok hσ t t' ht, Ty.elabList_ok hσ ts ts' hts⟩
    · cases h
end

mutual
theorem elabTy_elab (f : String → Ty) : ∀ (t : Ty) (Q : Ty → CState → Prop) (s : CState),
    t.surf = true →
    (∀ r s', s' = s → t.elab f = some r → Q r s') → Wp (elabTy f t) Q s
  | .named x, Q, s, _, h => by
    unfold elabTy; exact Wp.pure' (h _ _ rfl (by simp [Ty.elab]))
  | .tup ts, Q, s, hsf, h => by
    unfold elabTy
    refine Wp.bind (elabTyList_elab f ts _ s (by simpa [Ty.surf] using hsf) ?_)
    intro bs s1 e1 hb
    subst e1
    exact Wp.pure' (h _ _ rfl (by simp [Ty.elab, hb]))
  | .arr ps r e, Q, s, hsf, h => by
    simp only [Ty.surf, Bool.and_eq_true] at hsf
    unfold elabTy
    refine Wp.bind (elabTy_elab f r _ s hsf.2 ?_)
    intro r' s1 e1 hr'
    subst e1
    split
    · exact Wp.reject' _ _
    · rename_i hnot
      refine Wp.bind (elabTyList_elab f ps _ s1 hsf.1 ?_)
      intro bs s2 e2 hb
      subst e2
      refine Wp.pure' (h _ _ rfl ?_)
      simp only [Ty.elab, hb, hr']
      have hb2 : (r'.isArr && e == Eff.io) = false := by
        cases r' <;> cases e <;> simp_all [Ty.isArr]
      simp [hb2]
  | .mv _, Q, s, hsf, h => by simp [Ty.surf] at hsf
  | .skolem _, Q, s, hsf, h => by simp [Ty.surf] at hsf
  | .gen _, Q, s, hsf, h => by simp [Ty.surf] at hsf
  | .int, Q, s, _, h => by unfold elabTy; exact Wp.pure' (h _ _ rfl (by simp [Ty.elab]))
  | .bool, Q, s, _, h => by unfold elabTy; exact Wp.pure' (h _ _ rfl (by simp [Ty.elab]))
  | .str, Q, s, _, h => by unfold elabTy; exact Wp.pure' (h _ _ rfl (by simp [Ty.elab]))
  | .unit, Q, s, _, h => by unfold elabTy; exact Wp.pure' (h _ _ rfl (by simp [Ty.elab]))

theorem elabTyList_elab (f : String → Ty) :
    ∀ (ts : List Ty) (Q : List Ty → CState → Prop) (s : CState), Ty.surfList ts = true →
    (∀ rs s', s' = s → Ty.elabList f ts = some rs → Q rs s') → Wp (elabTyList f ts) Q s
  | [], Q, s, _, h => by
    unfold elabTyList; exact Wp.pure' (h _ _ rfl (by simp [Ty.elabList]))
  | t :: ts, Q, s, hsf, h => by
    simp only [Ty.surfList, Bool.and_eq_true] at hsf
    unfold elabTyList
    refine Wp.bind (elabTy_elab f t _ s hsf.1 ?_)
    intro t' s1 e1 ht'
    subst e1
    refine Wp.bind (elabTyList_elab f ts _ s1 hsf.2 ?_)
    intro rs s2 e2 hrs
    subst e2
    exact Wp.pure' (h _ _ rfl (by simp [Ty.elabList, ht', hrs]))
end

/-! ### Solved types are closed -/

mutual
theorem Ty.app_of_ok (θ : Nat → Ty) : ∀ t : Ty, t.ok 0 = true → Ty.app θ t = t
  | .named _, h => by simp [Ty.ok] at h
  | .mv _, h => by simp [Ty.ok] at h
  | .skolem _, _ => rfl
  | .gen _, h => by simp [Ty.ok] at h
  | .int, _ => rfl
  | .bool, _ => rfl
  | .str, _ => rfl
  | .unit, _ => rfl
  | .tup ts, h => by
    simp only [Ty.ok] at h
    simp only [Ty.app, Ty.appList_of_ok θ ts h]
  | .arr ps r e, h => by
    simp only [Ty.ok, Bool.and_eq_true, Bool.not_eq_true'] at h
    simp only [Ty.app, Ty.appList_of_ok θ ps h.1.1, Ty.app_of_ok θ r h.1.2]
    exact mkArr_keep (by simpa using h.2)

theorem Ty.appList_of_ok (θ : Nat → Ty) : ∀ ts : List Ty, Ty.okList 0 ts = true →
    Ty.appList θ ts = ts
  | [], _ => rfl
  | t :: ts, h => by
    simp only [Ty.okList, Bool.and_eq_true] at h
    simp only [Ty.appList, Ty.app_of_ok θ t h.1, Ty.appList_of_ok θ ts h.2]
end

mutual
theorem Ty.mg_of_ok : ∀ t : Ty, t.ok 0 = true → metasOf t = [] ∧ gensOf t = []
  | .named _, _ => by simp [metasOf, gensOf]
  | .mv _, h => by simp [Ty.ok] at h
  | .skolem _, _ => by simp [metasOf, gensOf]
  | .gen _, h => by simp [Ty.ok] at h
  | .int, _ => by simp [metasOf, gensOf]
  | .bool, _ => by simp [metasOf, gensOf]
  | .str, _ => by simp [metasOf, gensOf]
  | .unit, _ => by simp [metasOf, gensOf]
  | .tup ts, h => by
    simp only [Ty.ok] at h
    simpa [metasOf, gensOf] using Ty.mgList_of_ok ts h
  | .arr ps r e, h => by
    simp only [Ty.ok, Bool.and_eq_true, Bool.not_eq_true'] at h
    have h1 := Ty.mgList_of_ok ps h.1.1
    have h2 := Ty.mg_of_ok r h.1.2
    simp [metasOf, gensOf, h1.1, h1.2, h2.1, h2.2]

theorem Ty.mgList_of_ok : ∀ ts : List Ty, Ty.okList 0 ts = true →
    metasOfList ts = [] ∧ gensOfList ts = []
  | [], _ => by simp [metasOfList, gensOfList]
  | t :: ts, h => by
    simp only [Ty.okList, Bool.and_eq_true] at h
    have h1 := Ty.mg_of_ok t h.1
    have h2 := Ty.mgList_of_ok ts h.2
    simp [metasOfList, gensOfList, h1.1, h1.2, h2.1, h2.2]
end

theorem Ty.wf_of_ok {t : Ty} (h : t.ok 0 = true) (sz g : Nat) : Ty.WF sz g t := by
  obtain ⟨h1, h2⟩ := Ty.mg_of_ok t h
  exact ⟨fun m hm => by simp [h1] at hm, fun i hi => by simp [h2] at hi⟩

/-! ### Lambda parameters, arity, local bindings -/

theorem elabTy_unit_leaf {Q : Ty → CState → Prop} {s : CState} {a : Ty} (hsf : Ty.surf a = true)
    (h : ∀ r s', s' = s → Ty.elab (fun _ => Ty.unit) a = some r → r.ok 0 = true →
      Ty.WF s.sz 0 r → Q r s') :
    Wp (elabTy (fun _ => .unit) a) Q s :=
  elabTy_elab _ a Q s hsf fun r s' e he =>
    h r s' e he (Ty.elab_ok (fun _ => rfl) a r he)
      (Ty.wf_of_ok (Ty.elab_ok (fun _ => rfl) a r he) _ _)

macro_rules
  | `(tactic| wp_step) => `(tactic| leaf elabTy_unit_leaf)

set_option hygiene false in
macro_rules
  | `(tactic| wf_close) =>
    `(tactic| (show Ty.surf _ = true; exact hann _ rfl))

theorem lamParam_sound {Q : Ty → CState → Prop} {s : CState} {slot ann : Option Ty}
    (hs : s.WF) (hslot : ∀ t, slot = some t → Ty.WF s.sz 0 t)
    (hann : ∀ t, ann = some t → Ty.surf t = true)
    (h : ∀ r s', Ext s s' → s'.WF → Ty.WF s'.sz 0 r →
      (∀ θ, Solves θ s' → LamParam ann (Ty.app θ r)) → Q r s') :
    Wp (lamParam slot ann) Q s := by
  rcases ann with _ | a0 <;> rcases slot with _ | s0 <;> unfold lamParam <;> dsimp only
  all_goals (try have := hann a0 rfl)
  all_goals (try have := hslot s0 rfl)
  all_goals repeat' wp_step
  all_goals refine h _ _ (by ext_close) ‹CState.WF _› (by wf_close) ?_
  all_goals intro θ hθ
  · exact Ty.app_solved hθ.solved ‹Ty.WF _ 0 _›
  · exact Ty.app_solved hθ.solved ‹Ty.WF _ 0 _›
  · exact Ty.app_solved hθ.solved ‹Ty.WF _ 0 _›
  all_goals (
    simp only [LamParam]
    rw [Ty.app_of_ok θ _ ‹Ty.ok 0 _ = true›]
    refine ⟨?_, ‹Ty.elab _ _ = some _›⟩
    rw [Ty.vars_eq_namedIn]
    simpa [hasNamed] using ‹¬hasNamed _ = true›)

/-- A call of a known `fn` passes exactly its parameter count: nothing the checker accepts
contradicts the judgment's `ArityOk`. -/
def ArityGood (env : Env) (f : Expr) (n : Nat) : Prop :=
  ∀ x k, f = .var x → ((∃ t, env.lookup x = some (.fnMono k t)) ∨
    (∃ m t, env.lookup x = some (.fnPoly k m t))) → k = n

theorem arityCheck_sound {Q : PUnit → CState → Prop} {s : CState} {env : Env} {f : Expr}
    {n : Nat} (hs : s.WF)
    (h : ∀ s', s' = s → ArityGood env f n → Q ⟨⟩ s') : Wp (arityCheck env f n) Q s := by
  unfold arityCheck
  split
  · rename_i x
    split
    · rename_i k t hl
      split
      · exact Wp.reject' _ _
      · rename_i hk
        refine Wp.pure' (h _ rfl ?_)
        intro y k' hy hb
        cases hy
        have hk' : k = n := by simpa using hk
        rcases hb with ⟨t', ht'⟩ | ⟨m, t', ht'⟩
        · rw [hl] at ht'; cases ht'; exact hk'
        · rw [hl] at ht'; cases ht'
    · rename_i k m t hl
      split
      · exact Wp.reject' _ _
      · rename_i hk
        refine Wp.pure' (h _ rfl ?_)
        intro y k' hy hb
        cases hy
        have hk' : k = n := by simpa using hk
        rcases hb with ⟨t', ht'⟩ | ⟨m', t', ht'⟩
        · rw [hl] at ht'; cases ht'
        · rw [hl] at ht'; cases ht'; exact hk'
    · rename_i hn1 hn2
      refine Wp.pure' (h _ rfl ?_)
      intro y k' hy hb
      cases hy
      rcases hb with ⟨t', ht'⟩ | ⟨m', t', ht'⟩
      · exact absurd ht' (hn1 _ _)
      · exact absurd ht' (hn2 _ _ _)
  · rename_i hf
    refine Wp.pure' (h _ rfl ?_)
    intro y k' hy
    exact absurd hy (hf y)

/-- `bindLocal` under `localLetMono` is a cons. -/
theorem bindLocal_leaf {Q : Env → CState → Prop} {s : CState} {rules : Rules} {env : Env}
    {x : String} {t : Ty} (hr : rules.localLetMono = true)
    (h : ∀ env' s', s' = s → env' = (x, .mono t) :: env → Q env' s') :
    Wp (bindLocal rules env x t) Q s := by
  unfold bindLocal
  simp only [hr, ite_true]
  exact Wp.pure' (h _ _ rfl rfl)

end SproutCore
