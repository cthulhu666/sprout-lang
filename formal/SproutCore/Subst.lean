import SproutCore.CheckWF

/-!
# Applying a grounding to checker types (docs/lean-model-v0.md §9, 1b step 3a, part 1)

The soundness theorem (`InferSound.lean`) says: every grounding `θ` that solves the final state
types the expression. This file is the substitution layer under it.

* `Ty.app θ t` replaces each metavariable `?n` by `θ n` and rebuilds each arrow through `mkArr`,
  as `zonk` does, so the result is canonical. `.gen` and `.skolem` are left alone. A `.named` is
  sent to `.unit`: the checker never builds one inside a type it works on (`elabTy` maps every
  written variable away), and this keeps `app θ t` solved without an invariant about it.
* `Solves θ s`: every bound `?n := t` of `s` has `θ n = app θ t`, and every `θ n` is solved.
  Unbound metavariables may be grounded to any solved type.
* `Ext s s'`: the state only grows. Its size does not shrink and no binding is changed or removed.
  `Solves θ s' → Ext s s' → Solves θ s`.

Every checker function that touches the state keeps `Ext`. The leaf lemmas for the primitive ones
(`fresh`, `freshSkolem`, `bindMeta`, `shallow`, `zonk`, `instantiate`) are here; the recursive
ones (`flow`, `joinTy`, `peel`, `infer` and its helpers) prove `Ext` together with their
soundness in `InferSound.lean`, since each step's soundness needs the facts of the steps before it.
-/

namespace SproutCore

/-! ### Applying a grounding -/

mutual
def Ty.app (θ : Nat → Ty) : Ty → Ty
  | .mv n => θ n
  | .named _ => .unit
  | .skolem n => .skolem n
  | .gen n => .gen n
  | .int => .int
  | .bool => .bool
  | .str => .str
  | .unit => .unit
  | .tup ts => .tup (Ty.appList θ ts)
  | .arr ps r e => mkArr (Ty.appList θ ps) (Ty.app θ r) e

def Ty.appList (θ : Nat → Ty) : List Ty → List Ty
  | [] => []
  | t :: ts => Ty.app θ t :: Ty.appList θ ts
end

theorem Ty.appList_eq_map (θ : Nat → Ty) (ts : List Ty) : Ty.appList θ ts = ts.map (Ty.app θ) := by
  induction ts with
  | nil => rfl
  | cons t ts ih => simp [Ty.appList, ih]

theorem Ty.instList_eq_map (us : List Ty) (ts : List Ty) :
    Ty.instList us ts = ts.map (Ty.inst us) := by
  induction ts with
  | nil => rfl
  | cons t ts ih => simp [Ty.instList, ih]

theorem Ty.app_arr (θ : Nat → Ty) (ps : List Ty) (r : Ty) (e : Eff) :
    Ty.app θ (.arr ps r e) = mkArr (ps.map (Ty.app θ)) (Ty.app θ r) e := by
  simp [Ty.app, Ty.appList_eq_map]

theorem Ty.app_tup (θ : Nat → Ty) (ts : List Ty) :
    Ty.app θ (.tup ts) = .tup (ts.map (Ty.app θ)) := by
  simp [Ty.app, Ty.appList_eq_map]

theorem Ty.inst_arr (us : List Ty) (ps : List Ty) (r : Ty) (e : Eff) :
    Ty.inst us (.arr ps r e) = mkArr (ps.map (Ty.inst us)) (Ty.inst us r) e := by
  simp [Ty.inst, Ty.instList_eq_map]

/-! ### `mkArr` -/

@[simp] theorem Eff.pure_beq_pure : (Eff.pure == Eff.pure) = true := rfl
@[simp] theorem Eff.io_beq_pure : (Eff.io == Eff.pure) = false := rfl
@[simp] theorem Eff.pure_beq_io : (Eff.pure == Eff.io) = false := rfl
@[simp] theorem Eff.io_beq_io : (Eff.io == Eff.io) = true := rfl

theorem mergeable_iff {ps : List Ty} {r : Ty} {e : Eff} :
    Ty.mergeable ps r e = true ↔
      ∃ q qs r' e', e = .pure ∧ ps ≠ [] ∧ r = .arr (q :: qs) r' e' := by
  cases e <;> cases ps <;> cases r <;> simp [Ty.mergeable]
  rename_i ps' r' e'
  cases ps' <;> simp

/-- A mergeable arrow is one longer arrow. -/
theorem mkArr_merge {ps : List Ty} {r : Ty} {e : Eff} (h : Ty.mergeable ps r e = true) :
    ∃ q qs r' e', r = .arr (q :: qs) r' e' ∧ e = .pure ∧ ps ≠ [] ∧
      mkArr ps r e = .arr (ps ++ q :: qs) r' e' := by
  obtain ⟨q, qs, r', e', rfl, hp, rfl⟩ := mergeable_iff.mp h
  refine ⟨q, qs, r', e', rfl, rfl, hp, ?_⟩
  cases ps with
  | nil => exact absurd rfl hp
  | cons p ps => simp [mkArr]

theorem mkArr_keep {ps : List Ty} {r : Ty} {e : Eff} (h : Ty.mergeable ps r e = false) :
    mkArr ps r e = .arr ps r e := by
  unfold mkArr
  split
  · rename_i ps' r' e'
    split
    · rfl
    · rename_i hne
      exfalso
      have hP : ps ≠ [] := by intro h'; subst h'; simp at hne
      have hP' : ps' ≠ [] := by intro h'; subst h'; simp at hne
      obtain ⟨q, qs, rfl⟩ := List.exists_cons_of_ne_nil hP'
      have : Ty.mergeable ps (.arr (q :: qs) r' e') .pure = true :=
        mergeable_iff.mpr ⟨q, qs, r', e', rfl, hP, rfl⟩
      rw [this] at h
      cases h
  · rfl

/-- Merging commutes with a result that is an arrow: the two ways to join `P`, `P'` and `R`. -/
theorem mkArr_assoc (P P' : List Ty) (R : Ty) (e : Eff) (hP : P ≠ []) (hP' : P' ≠ []) :
    mkArr (P ++ P') R e = mkArr P (mkArr P' R e) .pure := by
  obtain ⟨p, P0, rfl⟩ := List.exists_cons_of_ne_nil hP
  obtain ⟨q, Q0, rfl⟩ := List.exists_cons_of_ne_nil hP'
  cases R with
  | arr Q R2 e2 =>
    cases e with
    | pure => rcases Q with _ | ⟨x, Q0⟩ <;> simp [mkArr]
    | io => simp [mkArr]
  | _ => cases e <;> simp [mkArr]

/-- A map over types that sends an arrow to `mkArr` of the images commutes with `mkArr`. -/
theorem mkArr_hom (F : Ty → Ty) (hF : ∀ ps r e, F (.arr ps r e) = mkArr (ps.map F) (F r) e)
    (ps : List Ty) (r : Ty) (e : Eff) : F (mkArr ps r e) = mkArr (ps.map F) (F r) e := by
  by_cases hm : Ty.mergeable ps r e = true
  · obtain ⟨q, qs, r', e', rfl, rfl, hP, hmk⟩ := mkArr_merge hm
    rw [hmk, hF (ps ++ q :: qs) r' e', hF (q :: qs) r' e', List.map_append,
      mkArr_assoc _ _ _ _ (by simpa using hP) (by simp)]
  · rw [mkArr_keep (by simpa using hm)]
    exact hF _ _ _

theorem Ty.app_mkArr (θ : Nat → Ty) (ps : List Ty) (r : Ty) (e : Eff) :
    Ty.app θ (mkArr ps r e) = mkArr (ps.map (Ty.app θ)) (Ty.app θ r) e :=
  mkArr_hom (Ty.app θ) (Ty.app_arr θ) ps r e

theorem Ty.inst_mkArr (us : List Ty) (ps : List Ty) (r : Ty) (e : Eff) :
    Ty.inst us (mkArr ps r e) = mkArr (ps.map (Ty.inst us)) (Ty.inst us r) e :=
  mkArr_hom (Ty.inst us) (Ty.inst_arr us) ps r e

/-! ### Solved types -/

theorem Ty.okList_iff {n : Nat} {ts : List Ty} :
    Ty.okList n ts = true ↔ ∀ t ∈ ts, t.ok n = true := by
  induction ts with
  | nil => simp [Ty.okList]
  | cons t ts ih => simp [Ty.okList, ih]

mutual
theorem Ty.ok_mono {n m : Nat} (h : n ≤ m) : ∀ t : Ty, t.ok n = true → t.ok m = true
  | .named _, h' => h'
  | .mv _, h' => h'
  | .gen i, h' => by simp [Ty.ok] at *; omega
  | .skolem _, h' => h'
  | .int, h' => h'
  | .bool, h' => h'
  | .str, h' => h'
  | .unit, h' => h'
  | .tup ts, h' => by
    simp only [Ty.ok] at *
    exact Ty.okList_mono h ts h'
  | .arr ps r e, h' => by
    simp only [Ty.ok, Bool.and_eq_true] at *
    exact ⟨⟨Ty.okList_mono h ps h'.1.1, Ty.ok_mono h r h'.1.2⟩, h'.2⟩

theorem Ty.okList_mono {n m : Nat} (h : n ≤ m) :
    ∀ ts : List Ty, Ty.okList n ts = true → Ty.okList m ts = true
  | [], _ => rfl
  | t :: ts, h' => by
    simp only [Ty.okList, Bool.and_eq_true] at *
    exact ⟨Ty.ok_mono h t h'.1, Ty.okList_mono h ts h'.2⟩
end

theorem Ty.okList_append {n : Nat} {ps qs : List Ty} (h1 : Ty.okList n ps = true)
    (h2 : Ty.okList n qs = true) : Ty.okList n (ps ++ qs) = true := by
  rw [Ty.okList_iff] at *
  intro t ht
  rcases List.mem_append.mp ht with ht | ht
  · exact h1 t ht
  · exact h2 t ht

theorem Ty.ok_mkArr {n : Nat} {ps : List Ty} {r : Ty} {e : Eff} (hps : Ty.okList n ps = true)
    (hr : r.ok n = true) : (mkArr ps r e).ok n = true := by
  by_cases hm : Ty.mergeable ps r e = true
  · obtain ⟨q, qs, r', e', rfl, rfl, hp, hmk⟩ := mkArr_merge hm
    rw [hmk]
    simp only [Ty.ok, Bool.and_eq_true, Bool.not_eq_true'] at hr ⊢
    have hmg : Ty.mergeable (ps ++ q :: qs) r' e' = Ty.mergeable (q :: qs) r' e' := by
      obtain ⟨p, ps0, rfl⟩ := List.exists_cons_of_ne_nil hp
      simp [Ty.mergeable]
    refine ⟨⟨Ty.okList_append hps hr.1.1, hr.1.2⟩, ?_⟩
    rw [hmg]; exact hr.2
  · rw [mkArr_keep (by simpa using hm)]
    simp only [Ty.ok, Bool.and_eq_true, Bool.not_eq_true']
    refine ⟨⟨hps, hr⟩, ?_⟩
    simpa using hm

-- The grounding of a type with no `gen` above `g` is `ok g` when the grounding is.
mutual
theorem Ty.app_ok (θ : Nat → Ty) (g : Nat) (hθ : ∀ n, (θ n).ok g = true) :
    ∀ t : Ty, (∀ i ∈ gensOf t, i < g) → (Ty.app θ t).ok g = true
  | .mv n, _ => hθ n
  | .named _, _ => rfl
  | .skolem _, _ => rfl
  | .gen i, h => by simpa [Ty.app, Ty.ok, gensOf] using h i (by simp [gensOf])
  | .int, _ => rfl
  | .bool, _ => rfl
  | .str, _ => rfl
  | .unit, _ => rfl
  | .tup ts, h => by
    simp only [Ty.app, Ty.ok]
    exact Ty.appList_ok θ g hθ ts (by simpa [gensOf] using h)
  | .arr ps r e, h => by
    simp only [Ty.app]
    simp only [gensOf, List.mem_append] at h
    exact Ty.ok_mkArr (Ty.appList_ok θ g hθ ps (fun i hi => h i (Or.inl hi)))
      (Ty.app_ok θ g hθ r (fun i hi => h i (Or.inr hi)))

theorem Ty.appList_ok (θ : Nat → Ty) (g : Nat) (hθ : ∀ n, (θ n).ok g = true) :
    ∀ ts : List Ty, (∀ i ∈ gensOfList ts, i < g) → Ty.okList g (Ty.appList θ ts) = true
  | [], _ => rfl
  | t :: ts, h => by
    simp only [Ty.appList, Ty.okList, Bool.and_eq_true]
    simp only [gensOfList, List.mem_append] at h
    exact ⟨Ty.app_ok θ g hθ t (fun i hi => h i (Or.inl hi)),
      Ty.appList_ok θ g hθ ts (fun i hi => h i (Or.inr hi))⟩
end

theorem Ty.app_solved {θ : Nat → Ty} (hθ : ∀ n, (θ n).Solved) {sz g : Nat} {t : Ty}
    (ht : Ty.WF sz g t) : (Ty.app θ t).ok g = true :=
  Ty.app_ok θ g (fun n => Ty.ok_mono (Nat.zero_le g) _ (hθ n)) t ht.2

/-! ### Instantiation commutes with grounding -/

theorem instTyList_eq_map (ms : Array Ty) (ts : List Ty) :
    instTyList ms ts = ts.map (instTy ms) := by
  induction ts with
  | nil => rfl
  | cons t ts ih => simp [instTyList, ih]

mutual
theorem Ty.inst_of_ok (ts : List Ty) : ∀ t : Ty, t.ok 0 = true → t.inst ts = t
  | .named _, _ => rfl
  | .mv _, _ => rfl
  | .gen i, h => by simp [Ty.ok] at h
  | .skolem _, _ => rfl
  | .int, _ => rfl
  | .bool, _ => rfl
  | .str, _ => rfl
  | .unit, _ => rfl
  | .tup us, h => by
    simp only [Ty.ok] at h
    simp only [Ty.inst]
    rw [Ty.instList_of_ok ts us h]
  | .arr ps r e, h => by
    simp only [Ty.ok, Bool.and_eq_true, Bool.not_eq_true'] at h
    simp only [Ty.inst]
    rw [Ty.instList_of_ok ts ps h.1.1, Ty.inst_of_ok ts r h.1.2]
    exact mkArr_keep (by simpa using h.2)

theorem Ty.instList_of_ok (ts : List Ty) : ∀ us : List Ty, Ty.okList 0 us = true →
    Ty.instList ts us = us
  | [], _ => rfl
  | t :: us, h => by
    simp only [Ty.okList, Bool.and_eq_true] at h
    simp only [Ty.instList]
    rw [Ty.inst_of_ok ts t h.1, Ty.instList_of_ok ts us h.2]
end

-- `instTy` of the checker (fresh variables for the quantified ones), then grounded, is the
-- scheme's grounding instantiated at the images of those variables.
mutual
theorem app_instTy (θ : Nat → Ty) (hθ : ∀ n, (θ n).ok 0 = true) (ms : Array Ty) :
    ∀ t : Ty, (∀ i ∈ gensOf t, i < ms.size) →
      Ty.app θ (instTy ms t) = Ty.inst (ms.toList.map (Ty.app θ)) (Ty.app θ t)
  | .mv n, _ => by
    simp only [instTy, Ty.app]
    exact (Ty.inst_of_ok _ _ (hθ n)).symm
  | .named _, _ => rfl
  | .skolem _, _ => rfl
  | .gen i, h => by
    have hi : i < ms.size := h i (by simp [gensOf])
    rw [instTy_gen_eq ms i hi]
    simp [Ty.app, Ty.inst, hi]
  | .int, _ => rfl
  | .bool, _ => rfl
  | .str, _ => rfl
  | .unit, _ => rfl
  | .tup ts, h => by
    have := app_instTyList θ hθ ms ts (by simpa [gensOf] using h)
    rw [Ty.appList_eq_map, Ty.instList_eq_map, Ty.appList_eq_map] at this
    simp only [instTy, Ty.app_tup, Ty.inst, Ty.instList_eq_map, Ty.tup.injEq]
    simpa [instTyList_eq_map] using this
  | .arr ps r e, h => by
    simp only [gensOf, List.mem_append] at h
    have h1 := app_instTyList θ hθ ms ps (fun i hi => h i (Or.inl hi))
    have h2 := app_instTy θ hθ ms r (fun i hi => h i (Or.inr hi))
    rw [Ty.appList_eq_map, Ty.instList_eq_map, Ty.appList_eq_map] at h1
    simp only [instTyList_eq_map, List.map_map] at h1
    simp only [instTy, Ty.app_arr, Ty.inst_mkArr, instTyList_eq_map, List.map_map]
    rw [h2, h1]

theorem app_instTyList (θ : Nat → Ty) (hθ : ∀ n, (θ n).ok 0 = true) (ms : Array Ty) :
    ∀ ts : List Ty, (∀ i ∈ gensOfList ts, i < ms.size) →
      Ty.appList θ (instTyList ms ts) =
        Ty.instList (ms.toList.map (Ty.app θ)) (Ty.appList θ ts)
  | [], _ => rfl
  | t :: ts, h => by
    simp only [gensOfList, List.mem_append] at h
    simp only [instTyList, Ty.appList, Ty.instList]
    rw [app_instTy θ hθ ms t (fun i hi => h i (Or.inl hi)),
      app_instTyList θ hθ ms ts (fun i hi => h i (Or.inr hi))]
end

/-! ### Solving groundings and growing states -/

/-- `θ` solves `s`: it grounds every binding of `s` consistently, and to solved types. An unbound
metavariable is grounded to any solved type. -/
structure Solves (θ : Nat → Ty) (s : CState) : Prop where
  bound : ∀ (n : Nat) (t : Ty), s.subst[n]? = some (some t) → θ n = Ty.app θ t
  solved : ∀ n, Ty.Solved (θ n)

/-- `s'` extends `s`: the size does not shrink and no binding is changed or removed. Reducible, so
`omega` reads the size fact. -/
@[reducible] def Ext (s s' : CState) : Prop :=
  s.sz ≤ s'.sz ∧ ∀ (n : Nat) (t : Ty), s.subst[n]? = some (some t) → s'.subst[n]? = some (some t)

theorem Ext.refl (s : CState) : Ext s s := ⟨Nat.le_refl _, fun _ _ h => h⟩

theorem Ext.trans {s1 s2 s3 : CState} (h1 : Ext s1 s2) (h2 : Ext s2 s3) : Ext s1 s3 :=
  ⟨Nat.le_trans h1.1 h2.1, fun n t h => h2.2 n t (h1.2 n t h)⟩

theorem Ext.sz {s s' : CState} (h : Ext s s') : s.sz ≤ s'.sz := h.1

/-- A grounding that solves the later state solves the earlier one. -/
theorem Solves.back {θ : Nat → Ty} {s1 s2 : CState} (h : Solves θ s2) (e : Ext s1 s2) :
    Solves θ s1 :=
  ⟨fun n t hn => h.bound n t (e.2 n t hn), h.solved⟩

/-- Close a goal `Solves θ s_i` from `Solves θ s_n` and the chain of `Ext` facts in context. -/
syntax "solves_close" : tactic
macro_rules
  | `(tactic| solves_close) =>
    `(tactic| repeat' (first | assumption | (refine Solves.back ?_ ‹Ext _ _›)))

/-! ### Primitive steps keep `Ext` -/

theorem Wp.and' {α : Type} {x : M α} {Q1 Q2 : α → CState → Prop} {s : CState} (h1 : Wp x Q1 s)
    (h2 : Wp x Q2 s) : Wp x (fun a s' => Q1 a s' ∧ Q2 a s') s :=
  fun n a s' hr => ⟨h1 n a s' hr, h2 n a s' hr⟩

theorem Ext.push {s s' : CState} (h : s'.subst = s.subst.push none) : Ext s s' := by
  refine ⟨by simp [CState.sz, h], fun n t hn => ?_⟩
  rw [h, Array.getElem?_push]
  split
  · simp_all
  · exact hn

theorem Ext.of_subst {s s' : CState} (h : s'.subst = s.subst) : Ext s s' :=
  ⟨by simp [CState.sz, h], fun n t hn => by rw [h]; exact hn⟩

/-- Binding an unbound variable only adds a binding. -/
theorem Ext.set {s s' : CState} {n : Nat} {v : Option Ty} (hu : s.Unbound n)
    (h : s'.subst = s.subst.set! n v) : Ext s s' := by
  refine ⟨by simp [CState.sz, h], fun j t hj => ?_⟩
  rw [h, Array.set!_eq_setIfInBounds, Array.getElem?_setIfInBounds]
  by_cases hnj : n = j
  · subst hnj; exact absurd hj (hu t)
  · simp [hnj, hj]

/-- A pushed entry is unbound, and an unbound variable stays so. -/
theorem Unbound.push {s s' : CState} (h : s'.subst = s.subst.push none) {m : Nat}
    (hu : s.Unbound m) : s'.Unbound m := by
  intro t ht
  rw [h, Array.getElem?_push] at ht
  split at ht
  · simp at ht
  · exact hu t ht

theorem fresh_leaf {Q : Ty → CState → Prop} {s : CState} (hs : s.WF)
    (h : ∀ a s', Ty.WF s'.sz 0 a → (∃ j, a = .mv j) → s.sz + 1 = s'.sz → Ext s s' → s'.WF →
      (∀ m, s.Unbound m → s'.Unbound m) → Q a s') :
    Wp fresh Q s := by
  intro n a s' hr
  have h2 : (Except.ok (.mv s.sz, { s with subst := s.subst.push none }) :
      Except CErr (Ty × CState)) = .ok (a, s') := hr
  cases h2
  refine h _ _ ?_ ⟨_, rfl⟩ ?_ (Ext.push rfl) (hs.push rfl) (fun m hu => Unbound.push rfl hu)
  · simp [CState.sz]
  · simp [CState.sz]

theorem freshSkolem_leaf {Q : Ty → CState → Prop} {s : CState} (hs : s.WF)
    (h : ∀ a s', (∀ sz g, Ty.WF sz g a) → s.sz = s'.sz → Ext s s' → s'.WF → Q a s') :
    Wp freshSkolem Q s := by
  intro n a s' hr
  have h2 : (Except.ok (.skolem s.nextSk, { s with nextSk := s.nextSk + 1 }) :
      Except CErr (Ty × CState)) = .ok (a, s') := hr
  cases h2
  exact h _ _ (fun _ _ => Ty.WF_skolem) rfl (Ext.of_subst rfl) (hs.of_subst rfl)

/-- `shallow`'s result grounds like its argument. -/
theorem shallowF_app : ∀ (k : Nat) (t : Ty) (s : CState),
    Wp (shallowF k t) (fun r s' => s' = s ∧ ∀ θ, Solves θ s → Ty.app θ r = Ty.app θ t) s := by
  intro k
  induction k with
  | zero => intro t s; exact Wp.fuel'
  | succ k ih =>
    intro t s
    cases t with
    | mv n =>
      simp only [shallowF]
      refine Wp.bind (Wp.get' ?_)
      split
      · rename_i t' hb
        refine Wp.mono (ih t' s) ?_
        intro r s' ⟨e, h⟩
        refine ⟨e, fun θ hθ => ?_⟩
        rw [h θ hθ]
        simp only [Ty.app]
        exact (hθ.bound n t' hb).symm
      · exact Wp.pure' ⟨rfl, fun _ _ => rfl⟩
    | _ => simp only [shallowF]; exact Wp.pure' ⟨rfl, fun _ _ => rfl⟩

theorem shallow_leaf {Q : Ty → CState → Prop} {s : CState} {t : Ty} (hs : s.WF)
    (ht : Ty.WF s.sz 0 t)
    (h : ∀ r s', s' = s → Ty.WF s.sz 0 r → (∀ m, r = .mv m → s.Unbound m) →
      (∀ θ, Solves θ s → Ty.app θ r = Ty.app θ t) → Q r s') : Wp (shallow t) Q s := by
  have h1 := shallow_wp
    (Q := fun r s' => s' = s ∧ Ty.WF s.sz 0 r ∧ (∀ m, r = .mv m → s.Unbound m))
    hs ht (fun r s' e hr hu => ⟨e, hr, hu⟩)
  have h2 : Wp (shallow t) (fun r s' => s' = s ∧ ∀ θ, Solves θ s → Ty.app θ r = Ty.app θ t) s := by
    unfold shallow
    exact Wp.bind (Wp.read' fun n => shallowF_app n t s)
  exact Wp.mono (Wp.and' h1 h2) fun r s' ⟨⟨e, hr, hu⟩, _, hf⟩ => h r s' e hr hu hf

/-! ### Loops with a per-element fact -/

/-- Two lists related pointwise (core Lean has no `List.Forall₂`). -/
inductive All2 {α β : Type} (R : α → β → Prop) : List α → List β → Prop
  | nil : All2 R [] []
  | cons {a : α} {b : β} {as : List α} {bs : List β} : R a b → All2 R as bs →
      All2 R (a :: as) (b :: bs)

theorem All2.imp {α β : Type} {R R' : α → β → Prop} (h : ∀ a b, R a b → R' a b) :
    ∀ {as : List α} {bs : List β}, All2 R as bs → All2 R' as bs
  | _, _, All2.nil => All2.nil
  | _, _, All2.cons h1 h2 => All2.cons (h _ _ h1) (All2.imp h h2)

theorem All2.length_eq {α β : Type} {R : α → β → Prop} :
    ∀ {as : List α} {bs : List β}, All2 R as bs → as.length = bs.length
  | _, _, All2.nil => rfl
  | _, _, All2.cons _ h2 => by simp [All2.length_eq h2]

/-- `mapM`, with an invariant on the state and a relation between each input and its result, kept
true as the state grows. -/
theorem Wp.mapM_rel {α β : Type} {f : α → M β} {l : List α} {s : CState}
    (I : CState → Prop) (R : CState → α → β → Prop)
    (hR : ∀ s1 s2 a b, Ext s1 s2 → R s1 a b → R s2 a b) (h0 : I s)
    (hf : ∀ a ∈ l, ∀ s1, I s1 → Ext s s1 →
      Wp (f a) (fun b s2 => I s2 ∧ Ext s1 s2 ∧ R s2 a b) s1) :
    Wp (l.mapM f) (fun bs s2 => I s2 ∧ Ext s s2 ∧ All2 (R s2) l bs) s := by
  induction l generalizing s with
  | nil =>
    simp only [List.mapM_nil]
    exact Wp.pure' ⟨h0, Ext.refl _, All2.nil⟩
  | cons a l ih =>
    simp only [List.mapM_cons]
    refine Wp.bind ?_
    refine Wp.mono (hf a (List.mem_cons_self ..) s h0 (Ext.refl _)) fun b s1 ⟨i1, e1, r1⟩ => ?_
    refine Wp.bind ?_
    refine Wp.mono (ih (s := s1) i1 (fun a ha s2 i2 e2 => ?_)) fun bs s2 ⟨i2, e2, r2⟩ => ?_
    · exact Wp.mono (hf a (List.mem_cons_of_mem _ ha) s2 i2 (e1.trans e2)) fun b s3 ⟨i3, e3, r3⟩ =>
        ⟨i3, e3, r3⟩
    · exact Wp.pure' ⟨i2, e1.trans e2, All2.cons (hR _ _ _ _ e2 r1) r2⟩

/-- A loop over a list whose body is a unit computation, with an invariant on the state and a
fact about each element, kept true as the state grows. -/
theorem Wp.forIn_unit_rel {α : Type} {body : α → M PUnit} {l : List α} {s : CState}
    (I : CState → Prop) (R : CState → α → Prop) (hR : ∀ s1 s2 a, Ext s1 s2 → R s1 a → R s2 a)
    (h0 : I s)
    (hb : ∀ a ∈ l, ∀ s1, I s1 → Ext s s1 →
      Wp (body a) (fun _ s2 => I s2 ∧ Ext s1 s2 ∧ R s2 a) s1) :
    Wp (forIn l PUnit.unit fun x _ => do body x; pure (ForInStep.yield PUnit.unit))
      (fun _ s2 => I s2 ∧ Ext s s2 ∧ ∀ a ∈ l, R s2 a) s := by
  induction l generalizing s with
  | nil =>
    simp only [List.forIn_nil]
    exact Wp.pure' ⟨h0, Ext.refl _, by simp⟩
  | cons a l ih =>
    simp only [List.forIn_cons]
    refine Wp.bind ?_
    refine Wp.bind (Wp.mono (hb a (List.mem_cons_self ..) s h0 (Ext.refl _)) ?_)
    intro u s1 ⟨i1, e1, r1⟩
    refine Wp.pure' ?_
    refine Wp.mono (ih (s := s1) i1 (fun a ha s2 i2 e2 => ?_)) fun u s2 ⟨i2, e2, r2⟩ => ?_
    · exact Wp.mono (hb a (List.mem_cons_of_mem _ ha) s2 i2 (e1.trans e2))
        fun u s3 ⟨i3, e3, r3⟩ => ⟨i3, e3, r3⟩
    · refine ⟨i2, e1.trans e2, ?_⟩
      intro c hc
      rcases List.mem_cons.mp hc with rfl | hc
      · exact hR _ _ _ e2 r1
      · exact r2 c hc

theorem appList_of_forall₂ {θ : Nat → Ty} :
    ∀ {as bs : List Ty}, All2 (fun a b => Ty.app θ b = Ty.app θ a) as bs →
      Ty.appList θ bs = Ty.appList θ as
  | _, _, All2.nil => rfl
  | _, _, All2.cons h hs => by
    simp only [Ty.appList]
    rw [h, appList_of_forall₂ hs]

/-! ### `zonk` -/

theorem zonkF_app : ∀ (k : Nat) (t : Ty) (s : CState),
    Wp (zonkF k t) (fun r s' => s' = s ∧ ∀ θ, Solves θ s → Ty.app θ r = Ty.app θ t) s := by
  intro k
  induction k with
  | zero => intro t s; exact Wp.fuel'
  | succ k ih =>
    intro t s
    simp only [zonkF]
    refine Wp.bind (Wp.mono (Wp.and' (shallowF_eq k t s) (shallowF_app k t s)) ?_)
    intro t' s1 ⟨e1, e1', h1⟩
    subst e1
    have hlist : ∀ ts : List Ty, Wp (ts.mapM (zonkF k)) (fun bs s2 => s2 = s1 ∧
        ∀ θ, Solves θ s1 → Ty.appList θ bs = Ty.appList θ ts) s1 := by
      intro ts
      refine Wp.mono (Wp.mapM_rel (f := zonkF k) (l := ts) (s := s1) (fun s2 => s2 = s1)
        (fun s2 a b => ∀ θ, Solves θ s2 → Ty.app θ b = Ty.app θ a) ?_ rfl ?_) ?_
      · intro s2 s3 a b e h θ hθ; exact h θ (hθ.back e)
      · intro a ha s2 hs2 _
        subst hs2
        refine Wp.mono (ih a s2) ?_
        intro r s' ⟨hs', h⟩
        subst hs'
        exact ⟨rfl, Ext.refl _, h⟩
      · intro bs s2 ⟨i2, _, hr⟩
        subst i2
        exact ⟨rfl, fun θ hθ => appList_of_forall₂ (All2.imp (fun a b h => h θ hθ) hr)⟩
    cases t' with
    | tup ts =>
      dsimp only
      refine Wp.bind (Wp.mono (hlist ts) ?_)
      intro bs s2 ⟨e2, hb⟩
      subst e2
      refine Wp.pure' ⟨rfl, fun θ hθ => ?_⟩
      rw [← h1 θ hθ]
      simp only [Ty.app_tup, ← Ty.appList_eq_map]
      rw [hb θ hθ]
    | arr ps r e =>
      dsimp only
      refine Wp.bind (Wp.mono (hlist ps) ?_)
      intro bs s2 ⟨e2, hb⟩
      subst e2
      refine Wp.bind (Wp.mono (ih r s2) ?_)
      intro r' s3 ⟨e3, hr'⟩
      subst e3
      refine Wp.pure' ⟨rfl, fun θ hθ => ?_⟩
      rw [Ty.app_mkArr, ← Ty.appList_eq_map, hb θ hθ, hr' θ hθ]
      have := h1 θ hθ
      rw [Ty.app_arr, ← Ty.appList_eq_map] at this
      exact this
    | _ => dsimp only; exact Wp.pure' ⟨rfl, h1⟩

theorem zonk_app (t : Ty) (s : CState) :
    Wp (zonk t) (fun r s' => s' = s ∧ ∀ θ, Solves θ s → Ty.app θ r = Ty.app θ t) s := by
  unfold zonk
  exact Wp.bind (Wp.read' fun n => zonkF_app n t s)

theorem zonk_leaf {Q : Ty → CState → Prop} {s : CState} {t : Ty} (hs : s.WF)
    (ht : Ty.WF s.sz 0 t)
    (h : ∀ r s', s' = s → Ty.WF s.sz 0 r → (∀ m ∈ metasOf r, s.Unbound m) →
      (∀ θ, Solves θ s → Ty.app θ r = Ty.app θ t) → Q r s') : Wp (zonk t) Q s := by
  have h1 := zonk_wp
    (Q := fun r s' => s' = s ∧ Ty.WF s.sz 0 r ∧ (∀ m ∈ metasOf r, s.Unbound m))
    hs ht (fun r s' e hr hu => ⟨e, hr, hu⟩)
  exact Wp.mono (Wp.and' h1 (zonk_app t s)) fun r s' ⟨⟨e, hr, hu⟩, _, hf⟩ => h r s' e hr hu hf

theorem bindMeta_leaf {Q : PUnit → CState → Prop} {s : CState} {n : Nat} {t : Ty} (hs : s.WF)
    (hn : n < s.sz) (hu : s.Unbound n) (ht : Ty.WF s.sz 0 t)
    (h : ∀ s', s'.sz = s.sz → Ext s s' → s'.WF → (∀ θ, Solves θ s' → θ n = Ty.app θ t) →
      Q ⟨⟩ s') : Wp (bindMeta n t) Q s := by
  unfold bindMeta
  refine Wp.bind (zonk_leaf hs ht ?_)
  intro t' s1 e1 ht' hu' hz
  subst e1
  wp_tac
  rename_i hocc
  have hn' : n < s1.subst.size := hn
  refine h _ ?_ (Ext.set hu rfl) ?_ ?_
  · simp [CState.sz]
  · exact hs.bind hn ht' hu' (by simpa [occursIn] using hocc) rfl
  · intro θ hθ
    have hb := hθ.bound n t' (by simp [Array.set!_eq_setIfInBounds, hn'])
    rw [hb, hz θ (hθ.back (Ext.set hu rfl))]

/-! ### Instantiation -/

theorem fresh_push_loop_sound {α : Type} (l : List α) :
    ∀ (ms0 : Array Ty) {Q : Array Ty → CState → Prop} {s : CState}, s.WF →
      (∀ u ∈ ms0, ∃ j, u = .mv j) → (∀ t ∈ ms0, Ty.WF s.sz 0 t) →
      (∀ ms s', Ext s s' → s'.WF → ms.size = ms0.size + l.length →
        (∀ u ∈ ms, ∃ j, u = .mv j) → (∀ t ∈ ms, Ty.WF s'.sz 0 t) → Q ms s') →
      Wp (forIn l ms0 fun _ ms => do
        let d ← fresh
        pure (ForInStep.yield (ms.push d))) Q s := by
  induction l with
  | nil =>
    intro ms0 Q s hs hm h0 h
    simp only [List.forIn_nil]
    exact Wp.pure' (h ms0 s (Ext.refl _) hs (by simp) hm h0)
  | cons a l ih =>
    intro ms0 Q s hs hm h0 h
    simp only [List.forIn_cons]
    refine Wp.bind (Wp.bind (fresh_leaf hs ?_))
    intro d s1 hd hdm g1 e1 h1 _
    refine Wp.pure' ?_
    dsimp only
    refine ih (ms0.push d) h1 ?_ ?_ ?_
    · intro u hu
      rcases Array.mem_push.mp hu with hu | rfl
      · exact hm u hu
      · exact hdm
    · intro t ht
      rcases Array.mem_push.mp ht with ht | rfl
      · exact (h0 t ht).mono (by omega) (Nat.le_refl _)
      · exact hd
    · intro ms s2 e2 h2 hsz hm2 hms
      refine h ms s2 (e1.trans e2) h2 ?_ hm2 hms
      simp only [Array.size_push] at hsz
      simp only [List.length_cons]
      omega

theorem instantiate_leaf {Q : Ty → CState → Prop} {s : CState} {n : Nat} {t : Ty} (hs : s.WF)
    (ht : Ty.WF s.sz n t)
    (h : ∀ r s', Ext s s' → s'.WF → Ty.WF s'.sz 0 r →
      (∃ ms : Array Ty, ms.size = n ∧ (∀ u ∈ ms, ∃ j, u = .mv j) ∧ r = instTy ms t) →
      Q r s') : Wp (instantiate n t) Q s := by
  unfold instantiate
  dsimp only
  simp only [Std.Legacy.Range.forIn_eq_forIn_range']
  refine Wp.bind (fresh_push_loop_sound _ #[] hs (by simp) (by simp) ?_)
  intro ms s1 e1 h1 hsz hm hms
  have hsz' : ms.size = n := by simpa using hsz
  refine Wp.pure' (h _ s1 e1 h1 ?_ ⟨ms, hsz', hm, rfl⟩)
  refine instTy_wf (n := n) hsz' hms t (ht.mono e1.sz (Nat.le_refl _))

end SproutCore
