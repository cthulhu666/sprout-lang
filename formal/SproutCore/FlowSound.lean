import SproutCore.FlowsLemmas

/-!
# `flow`, `joinTy`, `peel` are sound (docs/lean-model-v0.md §9, 1b step 3a, part 2)

Each statement has the shape `Wp (f …) Q s` with a continuation `h`: the state grows (`Ext`), stays
well formed, and for every grounding `θ` that solves the new state the judgment's relation holds
between the grounded arguments and result. The continuation form lets `sound_step` walk a `do` block
forward (`CheckWF.lean`), the facts of earlier steps staying in context.

* `flow a e` ok ⇒ `Flows (app θ a) (app θ e)`.
* `joinTy true a b` returning `r` ⇒ both of `a`, `b` flow into `r`; `joinTy false a b` ⇒ `r` flows
  into both.
* `peel t n` returning `(ss, r, ε)` ⇒ `Peel (app θ t) n (ss.map (app θ)) (app θ r) ε`.
* `expectBool t` ok ⇒ `app θ t = .bool`.
-/

namespace SproutCore

open Judgment

set_option linter.unusedVariables false

-- the unbound-variable facts `shallow` hands back (named `hua`, `hue` by the proofs below); local,
-- since it names hypotheses of this file's proofs
set_option hygiene false in
local macro_rules
  | `(tactic| wf_close) =>
    `(tactic| (show CState.Unbound _ _;
               first | exact hua _ rfl | exact hue _ rfl | exact hub _ rfl))

theorem shallowF_leaf {Q : Ty → CState → Prop} {s : CState} {k : Nat} {t : Ty} (hs : s.WF)
    (ht : Ty.WF s.sz 0 t)
    (h : ∀ r s', s' = s → Ty.WF s.sz 0 r → (∀ m, r = .mv m → s.Unbound m) →
      (∀ θ, Solves θ s → Ty.app θ r = Ty.app θ t) → Q r s') : Wp (shallowF k t) Q s := by
  have h1 := shallowF_wp hs k t ht
    (Q := fun r s' => s' = s ∧ Ty.WF s.sz 0 r ∧ (∀ m, r = .mv m → s.Unbound m))
    (fun r s' e hr hu => ⟨e, hr, hu⟩)
  exact Wp.mono (Wp.and' h1 (shallowF_app k t s)) fun r s' ⟨⟨e, hr, hu⟩, _, hf⟩ => h r s' e hr hu hf

macro_rules
  | `(tactic| sound_step) => `(tactic| leaf shallowF_leaf)
macro_rules
  | `(tactic| sound_step) => `(tactic| leaf shallow_leaf)
macro_rules
  | `(tactic| sound_step) => `(tactic| leaf zonk_leaf)
macro_rules
  | `(tactic| sound_step) => `(tactic| leaf bindMeta_leaf)
macro_rules
  | `(tactic| sound_step) => `(tactic| leaf instantiate_leaf)

/-- Pointwise `Flows` between zipped lists gives `FlowsAll` of the images. -/
theorem flowsAll_of_zip {f : Ty → Ty} :
    ∀ {xs ys : List Ty}, xs.length = ys.length →
      (∀ p ∈ xs.zip ys, Flows (f p.1) (f p.2)) → FlowsAll (xs.map f) (ys.map f)
  | [], [], _, _ => FlowsAll.nil
  | x :: xs, y :: ys, hl, h => by
    refine FlowsAll.cons _ _ _ _ (h (x, y) (by simp)) (flowsAll_of_zip (by simpa using hl) ?_)
    intro p hp
    exact h p (List.mem_cons_of_mem _ hp)
  | [], _ :: _, hl, _ => by simp at hl
  | _ :: _, [], hl, _ => by simp at hl

/-- `flowF` over zipped lists, in the same direction: every pair flows. -/
theorem flowzip_sound {k : Nat} {xs ys : List Ty} {Q : PUnit → CState → Prop} {s : CState}
    (flowk : ∀ {Q : PUnit → CState → Prop} {s : CState} {x y : Ty} (hs : s.WF)
      (hx : Ty.WF s.sz 0 x) (hy : Ty.WF s.sz 0 y)
      (h : ∀ s', Ext s s' → s'.WF →
        (∀ θ, Solves θ s' → Flows (Ty.app θ x) (Ty.app θ y)) → Q ⟨⟩ s'),
      Wp (flowF k x y) Q s)
    (hs : s.WF) (hxs : ∀ t ∈ xs, Ty.WF s.sz 0 t) (hys : ∀ t ∈ ys, Ty.WF s.sz 0 t)
    (h : ∀ s', Ext s s' → s'.WF →
      (∀ p ∈ xs.zip ys, ∀ θ, Solves θ s' → Flows (Ty.app θ p.1) (Ty.app θ p.2)) → Q ⟨⟩ s') :
    Wp (forIn (xs.zip ys) PUnit.unit fun x _ => do
      flowF k x.fst x.snd; pure (ForInStep.yield PUnit.unit)) Q s := by
  refine Wp.mono (Wp.forIn_unit_ext (s := s) (fun s1 => s1.WF)
    (fun s1 p => ∀ θ, Solves θ s1 → Flows (Ty.app θ p.1) (Ty.app θ p.2)) ?_ hs ?_) ?_
  · intro s1 s2 p e r θ hθ; exact r θ (hθ.back e)
  · intro p hp s1 h1 e1
    obtain ⟨x, y⟩ := p
    obtain ⟨hx, hy⟩ := List.of_mem_zip hp
    exact flowk h1 ((hxs x hx).mono e1.sz (Nat.le_refl _))
      ((hys y hy).mono e1.sz (Nat.le_refl _)) fun s' e' h' f => ⟨h', e', f⟩
  · intro u s2 ⟨i2, e2, r2⟩
    exact h s2 e2 i2 r2

/-- `flowF` over zipped lists with the direction reversed: the second of each pair flows into the
first (a parameter). -/
theorem flowzip'_sound {k : Nat} {xs ys : List Ty} {Q : PUnit → CState → Prop} {s : CState}
    (flowk : ∀ {Q : PUnit → CState → Prop} {s : CState} {x y : Ty} (hs : s.WF)
      (hx : Ty.WF s.sz 0 x) (hy : Ty.WF s.sz 0 y)
      (h : ∀ s', Ext s s' → s'.WF →
        (∀ θ, Solves θ s' → Flows (Ty.app θ x) (Ty.app θ y)) → Q ⟨⟩ s'),
      Wp (flowF k x y) Q s)
    (hs : s.WF) (hxs : ∀ t ∈ xs, Ty.WF s.sz 0 t) (hys : ∀ t ∈ ys, Ty.WF s.sz 0 t)
    (h : ∀ s', Ext s s' → s'.WF →
      (∀ p ∈ xs.zip ys, ∀ θ, Solves θ s' → Flows (Ty.app θ p.2) (Ty.app θ p.1)) → Q ⟨⟩ s') :
    Wp (forIn (xs.zip ys) PUnit.unit fun x _ => do
      flowF k x.snd x.fst; pure (ForInStep.yield PUnit.unit)) Q s := by
  refine Wp.mono (Wp.forIn_unit_ext (s := s) (fun s1 => s1.WF)
    (fun s1 p => ∀ θ, Solves θ s1 → Flows (Ty.app θ p.2) (Ty.app θ p.1)) ?_ hs ?_) ?_
  · intro s1 s2 p e r θ hθ; exact r θ (hθ.back e)
  · intro p hp s1 h1 e1
    obtain ⟨x, y⟩ := p
    obtain ⟨hx, hy⟩ := List.of_mem_zip hp
    exact flowk h1 ((hys y hy).mono e1.sz (Nat.le_refl _))
      ((hxs x hx).mono e1.sz (Nat.le_refl _)) fun s' e' h' f => ⟨h', e', f⟩
  · intro u s2 ⟨i2, e2, r2⟩
    exact h s2 e2 i2 r2

/-- Pointwise `Flows` between zipped lists, the second flowing into the first, gives `FlowsAll` of
the images the other way round. -/
theorem flowsAll_of_zip_swap {f : Ty → Ty} :
    ∀ {xs ys : List Ty}, xs.length = ys.length →
      (∀ p ∈ xs.zip ys, Flows (f p.2) (f p.1)) → FlowsAll (ys.map f) (xs.map f)
  | [], [], _, _ => FlowsAll.nil
  | x :: xs, y :: ys, hl, h => by
    refine FlowsAll.cons _ _ _ _ (h (x, y) (by simp)) (flowsAll_of_zip_swap (by simpa using hl) ?_)
    intro p hp
    exact h p (List.mem_cons_of_mem _ hp)
  | [], _ :: _, hl, _ => by simp at hl
  | _ :: _, [], hl, _ => by simp at hl

theorem arr_params_ne_of_gt {aps eps : List Ty} (h1 : ¬(aps.isEmpty != eps.isEmpty) = true)
    (hgt : aps.length > eps.length) : eps ≠ [] := by
  intro h; subst h
  cases aps with
  | nil => simp at hgt
  | cons _ _ => simp at h1

theorem arr_params_ne_of_lt {aps eps : List Ty} (h1 : ¬(aps.isEmpty != eps.isEmpty) = true)
    (hlt : aps.length < eps.length) : aps ≠ [] := by
  intro h; subst h
  cases eps with
  | nil => simp at hlt
  | cons _ _ => simp at h1

theorem ne_right_of_not_or {aps eps : List Ty} (h : ¬(aps.isEmpty || eps.isEmpty) = true) :
    eps ≠ [] := by
  intro e; subst e; simp at h

theorem ne_left_of_not_or {aps eps : List Ty} (h : ¬(aps.isEmpty || eps.isEmpty) = true) :
    aps ≠ [] := by
  intro e; subst e; simp at h

/-- Close `Ext s s_n` from the chain of `Ext` facts in context. -/
syntax "ext_close" : tactic
macro_rules
  | `(tactic| ext_close) =>
    `(tactic| repeat' (first | exact Ext.refl _ | assumption | (refine Ext.trans ‹Ext _ _› ?_)))

/-- `flowF`'s arrow case, equal stored arity. -/
theorem flow_arr_eq (θ : Nat → Ty) (aps eps : List Ty) (ar er : Ty) (ae ee : Eff)
    (hl : aps.length = eps.length)
    (hp : ∀ p ∈ (aps.take (min aps.length eps.length)).zip (eps.take (min aps.length eps.length)),
      Flows (Ty.app θ p.2) (Ty.app θ p.1))
    (hr : Flows (Ty.app θ ar) (Ty.app θ er)) (he : ae.le ee = true) :
    Flows (Ty.app θ (.arr aps ar ae)) (Ty.app θ (.arr eps er ee)) := by
  have hm : min aps.length eps.length = aps.length := by omega
  rw [hm, List.take_length] at hp
  rw [hl, List.take_length] at hp
  rw [Ty.app_arr, Ty.app_arr, flows_mkArr_left, flows_mkArr_right]
  exact flows_arr_arr _ _ ae ee hr he _ _ (by simp [hl])
    (flowsAll_of_zip_swap (f := Ty.app θ) hl hp)

/-- `flowF`'s arrow case, the actual arrow has more stored parameters. -/
theorem flow_arr_gt (θ : Nat → Ty) (aps eps : List Ty) (ar er : Ty) (ae ee : Eff)
    (hgt : aps.length > eps.length) (hne : eps ≠ [])
    (hp : ∀ p ∈ (aps.take (min aps.length eps.length)).zip (eps.take (min aps.length eps.length)),
      Flows (Ty.app θ p.2) (Ty.app θ p.1))
    (hr : Flows (Ty.app θ (.arr (aps.drop (min aps.length eps.length)) ar ae)) (Ty.app θ er)) :
    Flows (Ty.app θ (.arr aps ar ae)) (Ty.app θ (.arr eps er ee)) := by
  have hm : min aps.length eps.length = eps.length := by omega
  rw [hm] at hp hr
  rw [List.take_length] at hp
  rw [Ty.app_arr, Ty.app_arr, flows_mkArr_left, flows_mkArr_right]
  rw [Ty.app_arr, flows_mkArr_left] at hr
  have hsplit : aps.map (Ty.app θ) =
      (aps.take eps.length).map (Ty.app θ) ++ (aps.drop eps.length).map (Ty.app θ) := by
    rw [← List.map_append, List.take_append_drop]
  have hlen : (aps.take eps.length).length = eps.length := by simp; omega
  rw [hsplit]
  refine flows_arr_longer _ _ ae ee _ ?_ hr _ _ (by simp; omega) (by simpa using hne)
    (flowsAll_of_zip_swap (f := Ty.app θ) hlen hp)
  intro h
  have := congrArg List.length h
  simp at this
  omega

/-- `flowF`'s arrow case, the expected arrow has more stored parameters. -/
theorem flow_arr_lt (θ : Nat → Ty) (aps eps : List Ty) (ar er : Ty) (ae ee : Eff)
    (hlt : aps.length < eps.length) (hne : aps ≠ []) (hae : ae.le .pure = true)
    (hp : ∀ p ∈ (aps.take (min aps.length eps.length)).zip (eps.take (min aps.length eps.length)),
      Flows (Ty.app θ p.2) (Ty.app θ p.1))
    (hr : Flows (Ty.app θ ar) (Ty.app θ (.arr (eps.drop (min aps.length eps.length)) er ee))) :
    Flows (Ty.app θ (.arr aps ar ae)) (Ty.app θ (.arr eps er ee)) := by
  have hm : min aps.length eps.length = aps.length := by omega
  rw [hm] at hp hr
  rw [List.take_length] at hp
  rw [Ty.app_arr, Ty.app_arr, flows_mkArr_left, flows_mkArr_right]
  rw [Ty.app_arr, flows_mkArr_right] at hr
  have hsplit : eps.map (Ty.app θ) =
      (eps.take aps.length).map (Ty.app θ) ++ (eps.drop aps.length).map (Ty.app θ) := by
    rw [← List.map_append, List.take_append_drop]
  have hlen : (eps.take aps.length).length = aps.length := by simp; omega
  rw [hsplit, Eff.le_pure hae]
  refine flows_arr_shorter _ _ ee _ ?_ hr _ _ (by simp; omega) (by simpa using hne)
    (flowsAll_of_zip_swap (f := Ty.app θ) hlen.symm hp)
  intro h
  have := congrArg List.length h
  simp at this
  omega

theorem flowF_sound : ∀ (k : Nat) (a e : Ty) (Q : PUnit → CState → Prop) (s : CState), s.WF →
    Ty.WF s.sz 0 a → Ty.WF s.sz 0 e →
    (∀ s', Ext s s' → s'.WF →
      (∀ θ, Solves θ s' → Flows (Ty.app θ a) (Ty.app θ e)) → Q ⟨⟩ s') →
    Wp (flowF k a e) Q s := by
  intro k
  induction k with
  | zero => intros; exact Wp.fuel'
  | succ k ih =>
    intro a e Q s hs ha he h
    simp only [flowF]
    refine Wp.bind (shallowF_leaf hs ha ?_)
    intro a' s1 e1 ha' hua hsa
    subst e1
    refine Wp.bind (shallowF_leaf hs he ?_)
    intro e' s2 e2 he' hue hse
    subst e2
    have flowk : ∀ {Q : PUnit → CState → Prop} {s : CState} {x y : Ty} (hs : s.WF)
        (hx : Ty.WF s.sz 0 x) (hy : Ty.WF s.sz 0 y)
        (h : ∀ s', Ext s s' → s'.WF →
          (∀ θ, Solves θ s' → Flows (Ty.app θ x) (Ty.app θ y)) → Q ⟨⟩ s'),
        Wp (flowF k x y) Q s :=
      fun hs hx hy h => ih _ _ _ _ hs hx hy h
    have flowzip : ∀ {xs ys : List Ty} {Q : PUnit → CState → Prop} {s : CState} (hs : s.WF)
        (hxs : ∀ t ∈ xs, Ty.WF s.sz 0 t) (hys : ∀ t ∈ ys, Ty.WF s.sz 0 t)
        (h : ∀ s', Ext s s' → s'.WF →
          (∀ p ∈ xs.zip ys, ∀ θ, Solves θ s' → Flows (Ty.app θ p.1) (Ty.app θ p.2)) →
            Q ⟨⟩ s'),
        Wp (forIn (xs.zip ys) PUnit.unit fun x _ => do
          flowF k x.fst x.snd; pure (ForInStep.yield PUnit.unit)) Q s :=
      fun hs hxs hys h => flowzip_sound flowk hs hxs hys h
    have flowzip' : ∀ {xs ys : List Ty} {Q : PUnit → CState → Prop} {s : CState} (hs : s.WF)
        (hxs : ∀ t ∈ xs, Ty.WF s.sz 0 t) (hys : ∀ t ∈ ys, Ty.WF s.sz 0 t)
        (h : ∀ s', Ext s s' → s'.WF →
          (∀ p ∈ xs.zip ys, ∀ θ, Solves θ s' → Flows (Ty.app θ p.2) (Ty.app θ p.1)) →
            Q ⟨⟩ s'),
        Wp (forIn (xs.zip ys) PUnit.unit fun x _ => do
          flowF k x.snd x.fst; pure (ForInStep.yield PUnit.unit)) Q s :=
      fun hs hxs hys h => flowzip'_sound flowk hs hxs hys h
    split
    all_goals repeat' (first | sound_step | leaf flowk | leaf flowzip | leaf flowzip')
    all_goals refine h _ (by ext_close) ‹CState.WF _› ?_
    all_goals intro θ hθ
    all_goals have hs2 : Solves θ s2 := by solves_close
    all_goals rw [← hsa θ hs2, ← hse θ hs2]
    · have hnm := Nat.eq_of_beq_eq_true ‹(_ == _) = true›
      subst hnm
      exact Flows.refl_of_ok _ (Ty.app_solved hθ.solved ha')
    · have e := ‹∀ θ, Solves θ _ → θ _ = Ty.app θ _› θ hθ
      simp only [Ty.app] at e ⊢
      rw [e]
      exact Flows.refl_of_ok _ (hθ.solved _)
    · have e := ‹∀ θ, Solves θ _ → θ _ = Ty.app θ _› θ hθ
      simp only [Ty.app] at e ⊢
      rw [e]
      exact Flows.refl_of_ok _ (Ty.app_solved hθ.solved he')
    · have e := ‹∀ θ, Solves θ _ → θ _ = Ty.app θ _› θ hθ
      simp only [Ty.app] at e ⊢
      rw [e]
      exact Flows.refl_of_ok _ (Ty.app_solved hθ.solved ha')
    · simp only [Ty.app]; exact Flows.int
    · simp only [Ty.app]; exact Flows.bool
    · simp only [Ty.app]; exact Flows.str
    · simp only [Ty.app]; exact Flows.unit
    · have hnm := Nat.eq_of_beq_eq_true ‹(_ == _) = true›
      subst hnm
      simp only [Ty.app]; exact Flows.skolem _
    · have hl := Nat.eq_of_beq_eq_true ‹(_ == _) = true›
      have hp := ‹∀ (p : Ty × Ty), p ∈ _ → ∀ (θ : Nat → Ty), Solves θ _ → Flows _ _›
      rw [Ty.app_tup, Ty.app_tup]
      exact Flows.tup _ _ (flowsAll_of_zip (f := Ty.app θ) hl (fun p hp' => hp p hp' θ hθ))
    · rename_i _ _ aps ar ae eps er ee hne hlen hle s1 e1 _ hp s3 e3 _ hr
      exact flow_arr_eq θ aps eps ar er ae ee (Nat.eq_of_beq_eq_true hlen)
        (fun p hp' => hp p hp' θ (hθ.back e3)) (hr θ hθ) hle
    · rename_i _ _ aps ar ae eps er ee hne hlen hgt s1 e1 _ hp s3 e3 _ hr
      exact flow_arr_gt θ aps eps ar er ae ee hgt (arr_params_ne_of_gt hne hgt)
        (fun p hp' => hp p hp' θ (hθ.back e3)) (hr θ hθ)
    · rename_i _ _ aps ar ae eps er ee hne hlen hgt hae s1 e1 _ hp s3 e3 _ hr
      have hlt : aps.length < eps.length := by simp at hlen hgt; omega
      exact flow_arr_lt θ aps eps ar er ae ee hlt (arr_params_ne_of_lt hne hlt) hae
        (fun p hp' => hp p hp' θ (hθ.back e3)) (hr θ hθ)

theorem flow_sound {Q : PUnit → CState → Prop} {s : CState} {a e : Ty} (hs : s.WF)
    (ha : Ty.WF s.sz 0 a) (he : Ty.WF s.sz 0 e)
    (h : ∀ s', Ext s s' → s'.WF →
      (∀ θ, Solves θ s' → Flows (Ty.app θ a) (Ty.app θ e)) → Q ⟨⟩ s') :
    Wp (flow a e) Q s := by
  unfold flow
  exact Wp.bind (Wp.read' fun n => flowF_sound n a e Q s hs ha he h)

macro_rules
  | `(tactic| sound_step) => `(tactic| leaf flow_sound)

/-! ### `joinTy` -/

/-- `r` is the join of `x` and `y` (both flow into it) at positive polarity, the meet (it flows
into both) at negative. -/
def JoinR (pos : Bool) (x y r : Ty) : Prop :=
  if pos then Flows x r ∧ Flows y r else Flows r x ∧ Flows r y

theorem JoinR.refl_of_ok (pos : Bool) (t : Ty) (h : t.ok 0 = true) : JoinR pos t t t := by
  cases pos <;> simp only [JoinR, ite_true, Bool.false_eq_true, ite_false] <;>
    exact ⟨Flows.refl_of_ok t h, Flows.refl_of_ok t h⟩

theorem flows_arr_app (θ : Nat → Ty) (ps : List Ty) (r : Ty) (e : Eff) (X : Ty) :
    Flows (Ty.app θ (.arr ps r e)) X ↔ Flows (.arr (ps.map (Ty.app θ)) (Ty.app θ r) e) X := by
  rw [Ty.app_arr, flows_mkArr_left]

theorem flows_app_arr (θ : Nat → Ty) (ps : List Ty) (r : Ty) (e : Eff) (X : Ty) :
    Flows X (Ty.app θ (.arr ps r e)) ↔ Flows X (.arr (ps.map (Ty.app θ)) (Ty.app θ r) e) := by
  rw [Ty.app_arr, flows_mkArr_right]

/-- Pointwise joins of zipped lists, read as `FlowsAll` of the images. -/
theorem joinAll_of_all2 (pos : Bool) (f : Ty → Ty) :
    ∀ (xs ys rs : List Ty), xs.length = ys.length →
      All2 (fun p r => JoinR pos (f p.1) (f p.2) (f r)) (xs.zip ys) rs →
      (if pos then FlowsAll (xs.map f) (rs.map f) ∧ FlowsAll (ys.map f) (rs.map f)
       else FlowsAll (rs.map f) (xs.map f) ∧ FlowsAll (rs.map f) (ys.map f))
  | [], [], rs, _, h => by
    simp only [List.zip_nil_left] at h
    cases h
    cases pos <;> simp [FlowsAll.nil]
  | x :: xs, y :: ys, rs, hl, h => by
    simp only [List.zip_cons_cons] at h
    cases h with
    | cons h1 h2 =>
      have ih := joinAll_of_all2 pos f xs ys _ (by simpa using hl) h2
      cases pos
      · simp only [JoinR, Bool.false_eq_true, ite_false] at h1 ih ⊢
        exact ⟨FlowsAll.cons _ _ _ _ h1.1 ih.1, FlowsAll.cons _ _ _ _ h1.2 ih.2⟩
      · simp only [JoinR, ite_true] at h1 ih ⊢
        exact ⟨FlowsAll.cons _ _ _ _ h1.1 ih.1, FlowsAll.cons _ _ _ _ h1.2 ih.2⟩
  | [], _ :: _, _, hl, _ => by simp at hl
  | _ :: _, [], _, hl, _ => by simp at hl

theorem join_tup (pos : Bool) (θ : Nat → Ty) (as es bs : List Ty) (hl : as.length = es.length)
    (h : All2 (fun p r => JoinR pos (Ty.app θ p.1) (Ty.app θ p.2) (Ty.app θ r)) (as.zip es) bs) :
    JoinR pos (Ty.app θ (.tup as)) (Ty.app θ (.tup es)) (Ty.app θ (.tup bs)) := by
  have := joinAll_of_all2 pos (Ty.app θ) as es bs hl h
  rw [Ty.app_tup, Ty.app_tup, Ty.app_tup]
  cases pos
  · simp only [JoinR, Bool.false_eq_true, ite_false] at this ⊢
    exact ⟨Flows.tup _ _ this.1, Flows.tup _ _ this.2⟩
  · simp only [JoinR, ite_true] at this ⊢
    exact ⟨Flows.tup _ _ this.1, Flows.tup _ _ this.2⟩

/-- `joinTyF`'s arrow case, equal stored arity. -/
theorem join_arr_eq (pos : Bool) (θ : Nat → Ty) (aps bps ps : List Ty) (ar br r : Ty)
    (ae be : Eff) (hl : aps.length = bps.length)
    (hps : All2 (fun p q => JoinR (!pos) (Ty.app θ p.1) (Ty.app θ p.2) (Ty.app θ q))
      (aps.zip bps) ps)
    (hr : JoinR pos (Ty.app θ ar) (Ty.app θ br) (Ty.app θ r)) :
    JoinR pos (Ty.app θ (.arr aps ar ae)) (Ty.app θ (.arr bps br be))
      (Ty.app θ (mkArr ps r (if pos then ae.join be else ae.meet be))) := by
  have hall := joinAll_of_all2 (!pos) (Ty.app θ) aps bps ps hl hps
  have hlen : ps.length = aps.length := by simpa [hl] using (All2.length_eq hps).symm
  rw [Ty.app_mkArr]
  have hl' : (aps.map (Ty.app θ)).length = (ps.map (Ty.app θ)).length := by simp [hlen]
  have hl'' : (bps.map (Ty.app θ)).length = (ps.map (Ty.app θ)).length := by simp [hlen, hl]
  cases pos
  · simp only [JoinR, Bool.not_false, Bool.false_eq_true, ite_false, ite_true] at hall hr ⊢
    refine ⟨?_, ?_⟩
    · rw [flows_mkArr_left, flows_app_arr]
      exact flows_arr_arr _ _ _ ae hr.1 (Eff.meet_le_left _ _) _ _ hl'.symm hall.1
    · rw [flows_mkArr_left, flows_app_arr]
      exact flows_arr_arr _ _ _ be hr.2 (Eff.meet_le_right _ _) _ _ hl''.symm hall.2
  · simp only [JoinR, Bool.not_true, Bool.false_eq_true, ite_false, ite_true] at hall hr ⊢
    refine ⟨?_, ?_⟩
    · rw [flows_mkArr_right, flows_arr_app]
      exact flows_arr_arr _ _ ae _ hr.1 (Eff.le_join_left _ _) _ _ hl' hall.1
    · rw [flows_mkArr_right, flows_arr_app]
      exact flows_arr_arr _ _ be _ hr.2 (Eff.le_join_right _ _) _ _ hl'' hall.2

/-- `joinTyF`'s arrow case, the left arrow has more stored parameters. -/
theorem join_arr_gt (pos : Bool) (θ : Nat → Ty) (aps bps ps : List Ty) (ar br r : Ty)
    (ae be : Eff) (hgt : aps.length > bps.length) (hne : bps ≠ [])
    (hps : All2 (fun p q => JoinR (!pos) (Ty.app θ p.1) (Ty.app θ p.2) (Ty.app θ q))
      ((aps.take (min aps.length bps.length)).zip (bps.take (min aps.length bps.length))) ps)
    (hr : JoinR pos (Ty.app θ (.arr (aps.drop (min aps.length bps.length)) ar ae))
      (Ty.app θ br) (Ty.app θ r)) :
    JoinR pos (Ty.app θ (.arr aps ar ae)) (Ty.app θ (.arr bps br be))
      (Ty.app θ (mkArr ps r (if pos then be else .pure))) := by
  have hm : min aps.length bps.length = bps.length := by omega
  rw [hm] at hps hr
  rw [List.take_length] at hps
  have hlenA1 : (aps.take bps.length).length = bps.length := by simp; omega
  have hall := joinAll_of_all2 (!pos) (Ty.app θ) (aps.take bps.length) bps ps hlenA1 hps
  have hlen : ps.length = bps.length := by
    have := (All2.length_eq hps).symm
    simpa [hlenA1] using this
  have hsplit : aps.map (Ty.app θ) =
      (aps.take bps.length).map (Ty.app θ) ++ (aps.drop bps.length).map (Ty.app θ) := by
    rw [← List.map_append, List.take_append_drop]
  have hA2 : (aps.drop bps.length).map (Ty.app θ) ≠ [] := by
    intro h
    have := congrArg List.length h
    simp only [List.length_map, List.length_drop, List.length_nil] at this
    omega
  have hPs : ps.map (Ty.app θ) ≠ [] := by
    intro h
    have h2 : ps = [] := List.map_eq_nil_iff.mp h
    rw [h2] at hlen
    exact hne (List.length_eq_zero_iff.mp hlen.symm)
  have hl1 : (ps.map (Ty.app θ)).length = ((aps.take bps.length).map (Ty.app θ)).length := by
    simp; omega
  have hl2 : (ps.map (Ty.app θ)).length = (bps.map (Ty.app θ)).length := by simp [hlen]
  rw [Ty.app_mkArr]
  cases pos
  · simp only [JoinR, Bool.not_false, Bool.false_eq_true, ite_false, ite_true] at hall hr ⊢
    have hr1 := hr.1
    rw [flows_app_arr] at hr1
    refine ⟨?_, ?_⟩
    · rw [flows_mkArr_left, flows_app_arr, hsplit]
      exact flows_arr_shorter _ _ ae _ hA2 hr1 _ _ hl1 hPs hall.1
    · rw [flows_mkArr_left, flows_app_arr]
      exact flows_arr_arr _ _ _ be hr.2 (Eff.pure_le _) _ _ hl2 hall.2
  · simp only [JoinR, Bool.not_true, Bool.false_eq_true, ite_false, ite_true] at hall hr ⊢
    have hr1 := hr.1
    rw [flows_arr_app] at hr1
    refine ⟨?_, ?_⟩
    · rw [flows_mkArr_right, flows_arr_app, hsplit]
      exact flows_arr_longer _ _ ae be _ hA2 hr1 _ _ hl1.symm hPs hall.1
    · rw [flows_mkArr_right, flows_arr_app]
      exact flows_arr_arr _ _ be be hr.2 (Eff.le_refl _) _ _ hl2.symm hall.2

/-- `joinTyF`'s arrow case, the right arrow has more stored parameters. -/
theorem join_arr_lt (pos : Bool) (θ : Nat → Ty) (aps bps ps : List Ty) (ar br r : Ty)
    (ae be : Eff) (hlt : aps.length < bps.length) (hne : aps ≠ [])
    (hps : All2 (fun p q => JoinR (!pos) (Ty.app θ p.1) (Ty.app θ p.2) (Ty.app θ q))
      ((aps.take (min aps.length bps.length)).zip (bps.take (min aps.length bps.length))) ps)
    (hr : JoinR pos (Ty.app θ ar)
      (Ty.app θ (.arr (bps.drop (min aps.length bps.length)) br be)) (Ty.app θ r)) :
    JoinR pos (Ty.app θ (.arr aps ar ae)) (Ty.app θ (.arr bps br be))
      (Ty.app θ (mkArr ps r (if pos then ae else .pure))) := by
  have hm : min aps.length bps.length = aps.length := by omega
  rw [hm] at hps hr
  rw [List.take_length] at hps
  have hlenB1 : (bps.take aps.length).length = aps.length := by simp; omega
  have hall := joinAll_of_all2 (!pos) (Ty.app θ) aps (bps.take aps.length) ps hlenB1.symm hps
  have hlen : ps.length = aps.length := by
    have := (All2.length_eq hps).symm
    simpa [hlenB1] using this
  have hsplit : bps.map (Ty.app θ) =
      (bps.take aps.length).map (Ty.app θ) ++ (bps.drop aps.length).map (Ty.app θ) := by
    rw [← List.map_append, List.take_append_drop]
  have hB2 : (bps.drop aps.length).map (Ty.app θ) ≠ [] := by
    intro h
    have := congrArg List.length h
    simp only [List.length_map, List.length_drop, List.length_nil] at this
    omega
  have hPs : ps.map (Ty.app θ) ≠ [] := by
    intro h
    have h2 : ps = [] := List.map_eq_nil_iff.mp h
    rw [h2] at hlen
    exact hne (List.length_eq_zero_iff.mp hlen.symm)
  have hl1 : (ps.map (Ty.app θ)).length = (aps.map (Ty.app θ)).length := by simp [hlen]
  have hl2 : (ps.map (Ty.app θ)).length = ((bps.take aps.length).map (Ty.app θ)).length := by
    simp; omega
  rw [Ty.app_mkArr]
  cases pos
  · simp only [JoinR, Bool.not_false, Bool.false_eq_true, ite_false, ite_true] at hall hr ⊢
    have hr2 := hr.2
    rw [flows_app_arr] at hr2
    refine ⟨?_, ?_⟩
    · rw [flows_mkArr_left, flows_app_arr]
      exact flows_arr_arr _ _ _ ae hr.1 (Eff.pure_le _) _ _ hl1 hall.1
    · rw [flows_mkArr_left, flows_app_arr, hsplit]
      exact flows_arr_shorter _ _ be _ hB2 hr2 _ _ hl2 hPs hall.2
  · simp only [JoinR, Bool.not_true, Bool.false_eq_true, ite_false, ite_true] at hall hr ⊢
    have hr2 := hr.2
    rw [flows_arr_app] at hr2
    refine ⟨?_, ?_⟩
    · rw [flows_mkArr_right, flows_arr_app]
      exact flows_arr_arr _ _ ae ae hr.1 (Eff.le_refl _) _ _ hl1.symm hall.1
    · rw [flows_mkArr_right, flows_arr_app, hsplit]
      exact flows_arr_longer _ _ be ae _ hB2 hr2 _ _ hl2.symm hPs hall.2

/-- A mapped loop of `joinTyF` over zipped lists. -/
theorem joinzip_sound {k : Nat} {pos : Bool} {xs ys : List Ty} {Q : List Ty → CState → Prop}
    {s : CState}
    (joink : ∀ {pos : Bool} {Q : Ty → CState → Prop} {s : CState} {x y : Ty} (hs : s.WF)
      (hx : Ty.WF s.sz 0 x) (hy : Ty.WF s.sz 0 y)
      (h : ∀ r s', Ext s s' → s'.WF → Ty.WF s'.sz 0 r →
        (∀ θ, Solves θ s' → JoinR pos (Ty.app θ x) (Ty.app θ y) (Ty.app θ r)) → Q r s'),
      Wp (joinTyF k pos x y) Q s)
    (hs : s.WF) (hxs : ∀ t ∈ xs, Ty.WF s.sz 0 t) (hys : ∀ t ∈ ys, Ty.WF s.sz 0 t)
    (h : ∀ bs s', Ext s s' → s'.WF → (∀ b ∈ bs, Ty.WF s'.sz 0 b) →
      (∀ θ, Solves θ s' →
        All2 (fun p r => JoinR pos (Ty.app θ p.1) (Ty.app θ p.2) (Ty.app θ r)) (xs.zip ys) bs) →
      Q bs s') :
    Wp ((xs.zip ys).mapM fun (x, y) => joinTyF k pos x y) Q s := by
  refine Wp.mono (Wp.mapM_ext (f := fun (p : Ty × Ty) => joinTyF k pos p.1 p.2) (l := xs.zip ys)
    (s := s) (fun s1 => s1.WF)
    (fun s1 p r => Ty.WF s1.sz 0 r ∧
      ∀ θ, Solves θ s1 → JoinR pos (Ty.app θ p.1) (Ty.app θ p.2) (Ty.app θ r)) ?_ hs ?_) ?_
  · intro s1 s2 p r e ⟨hw, hj⟩
    exact ⟨hw.mono e.sz (Nat.le_refl _), fun θ hθ => hj θ (hθ.back e)⟩
  · intro p hp s1 h1 e1
    obtain ⟨x, y⟩ := p
    obtain ⟨hx, hy⟩ := List.of_mem_zip hp
    exact joink h1 ((hxs x hx).mono e1.sz (Nat.le_refl _)) ((hys y hy).mono e1.sz (Nat.le_refl _))
      fun r s' e' h' hr hj => ⟨h', e', hr, hj⟩
  · intro bs s2 ⟨i2, e2, r2⟩
    refine h bs s2 e2 i2 ?_ ?_
    · exact All2.forall_right (fun a b h => h.1) r2
    · intro θ hθ
      exact All2.imp (fun a b h => h.2 θ hθ) r2

-- the tuple and arrow cases, run on the goals the walk leaves; hypothesis names are the proof's
set_option hygiene false in
local macro "join_close" : tactic => `(tactic| (
  · rename_i _ _ as es hl bs s1 _ _ _ hall
    exact join_tup _ θ as es bs (Nat.eq_of_beq_eq_true hl) (hall θ hθ)
  · rename_i _ _ aps ar ae eps er ee hlen bs s1 e1 _ _ hps r s3 e3 _ _ hr
    exact join_arr_eq _ θ aps eps bs ar er r ae ee (Nat.eq_of_beq_eq_true hlen)
      (hps θ (hθ.back e3)) (hr θ hθ)
  · rename_i _ _ aps ar ae eps er ee hlen hemp hgt bs s1 e1 _ _ hps r s3 e3 _ _ hr
    exact join_arr_gt _ θ aps eps bs ar er r ae ee hgt (ne_right_of_not_or hemp)
      (hps θ (hθ.back e3)) (hr θ hθ)
  · rename_i _ _ aps ar ae eps er ee hlen hemp hgt bs s1 e1 _ _ hps r s3 e3 _ _ hr
    have hlt : aps.length < eps.length := by simp at hlen hgt; omega
    exact join_arr_lt _ θ aps eps bs ar er r ae ee hlt (ne_left_of_not_or hemp)
      (hps θ (hθ.back e3)) (hr θ hθ)))

theorem joinTyF_sound : ∀ (k : Nat) (pos : Bool) (a b : Ty) (Q : Ty → CState → Prop) (s : CState),
    s.WF → Ty.WF s.sz 0 a → Ty.WF s.sz 0 b →
    (∀ r s', Ext s s' → s'.WF → Ty.WF s'.sz 0 r →
      (∀ θ, Solves θ s' → JoinR pos (Ty.app θ a) (Ty.app θ b) (Ty.app θ r)) → Q r s') →
    Wp (joinTyF k pos a b) Q s := by
  intro k
  induction k with
  | zero => intros; exact Wp.fuel'
  | succ k ih =>
    intro pos a b Q s hs ha hb h
    simp only [joinTyF]
    refine Wp.bind (shallowF_leaf hs ha ?_)
    intro a' s1 e1 ha' hua hsa
    subst e1
    refine Wp.bind (shallowF_leaf hs hb ?_)
    intro b' s2 e2 hb' hub hsb
    subst e2
    have joink : ∀ {pos : Bool} {Q : Ty → CState → Prop} {s : CState} {x y : Ty} (hs : s.WF)
        (hx : Ty.WF s.sz 0 x) (hy : Ty.WF s.sz 0 y)
        (h : ∀ r s', Ext s s' → s'.WF → Ty.WF s'.sz 0 r →
          (∀ θ, Solves θ s' → JoinR pos (Ty.app θ x) (Ty.app θ y) (Ty.app θ r)) → Q r s'),
        Wp (joinTyF k pos x y) Q s :=
      fun hs hx hy h => ih _ _ _ _ _ hs hx hy h
    have joinzip : ∀ {pos : Bool} {xs ys : List Ty} {Q : List Ty → CState → Prop} {s : CState}
        (hs : s.WF) (hxs : ∀ t ∈ xs, Ty.WF s.sz 0 t) (hys : ∀ t ∈ ys, Ty.WF s.sz 0 t)
        (h : ∀ bs s', Ext s s' → s'.WF → (∀ b ∈ bs, Ty.WF s'.sz 0 b) →
          (∀ θ, Solves θ s' →
            All2 (fun p r => JoinR pos (Ty.app θ p.1) (Ty.app θ p.2) (Ty.app θ r))
              (xs.zip ys) bs) → Q bs s'),
        Wp ((xs.zip ys).mapM fun (x, y) => joinTyF k pos x y) Q s :=
      fun hs hxs hys h => joinzip_sound joink hs hxs hys h
    cases pos
    all_goals simp only [Bool.false_eq_true, ite_false, ite_true, Bool.not_false,
      Bool.not_true] at *
    all_goals split
    all_goals repeat' (first | sound_step | leaf joink | leaf joinzip)
    all_goals refine h _ _ (by ext_close) ‹CState.WF _› (by wf_close) ?_
    all_goals intro θ hθ
    all_goals have hs2 : Solves θ s2 := by solves_close
    all_goals rw [← hsa θ hs2, ← hsb θ hs2]
    all_goals (first
      | (have hnm := Nat.eq_of_beq_eq_true ‹(_ == _) = true›
         subst hnm
         simp only [Ty.app]
         exact JoinR.refl_of_ok _ _ (by first | rfl | exact Ty.app_solved hθ.solved ‹_›))
      | (have e := ‹∀ θ, Solves θ _ → θ _ = Ty.app θ _› θ hθ
         simp only [Ty.app] at e ⊢
         rw [e]
         exact JoinR.refl_of_ok _ _
           (by first | exact hθ.solved _ | exact Ty.app_solved hθ.solved ‹_›))
      | (simp only [Ty.app]; exact JoinR.refl_of_ok _ _ rfl)
      | skip)
    join_close
    join_close

theorem joinTy_sound {Q : Ty → CState → Prop} {s : CState} {pos : Bool} {a b : Ty} (hs : s.WF)
    (ha : Ty.WF s.sz 0 a) (hb : Ty.WF s.sz 0 b)
    (h : ∀ r s', Ext s s' → s'.WF → Ty.WF s'.sz 0 r →
      (∀ θ, Solves θ s' → JoinR pos (Ty.app θ a) (Ty.app θ b) (Ty.app θ r)) → Q r s') :
    Wp (joinTy pos a b) Q s := by
  unfold joinTy
  exact Wp.bind (Wp.read' fun n => joinTyF_sound n pos a b Q s hs ha hb h)

macro_rules
  | `(tactic| sound_step) => `(tactic| leaf joinTy_sound)

/-! ### `peel` -/

/-- The `.mv` case of `peelF`: a call of an unsolved variable solves it to a pure arrow of `n`
fresh parameters. -/
theorem peel_mv_leaf {Q : List Ty × Ty × Eff → CState → Prop} {s : CState} {m n : Nat}
    (hs : s.WF) (hm : Ty.WF s.sz 0 (.mv m)) (hu : s.Unbound m)
    (h : ∀ ps r s', Ext s s' → s'.WF → (∀ t ∈ ps, Ty.WF s'.sz 0 t) →
      Ty.WF s'.sz 0 r → ps.length = n →
      (∀ θ, Solves θ s' → θ m = Ty.app θ (.arr ps r .pure)) → Q (ps, r, .pure) s') :
    Wp (do
      let mut ps := []
      for _ in [0:n] do ps := ps ++ [← fresh]
      let r ← fresh
      bindMeta m (.arr ps r .pure)
      pure (ps, r, .pure) : M (List Ty × Ty × Eff)) Q s := by
  refine Wp.bind (fresh_loop_sound n hs ?_)
  intro ps s1 e1 h1 hl hps hun1
  refine Wp.bind (fresh_leaf h1 ?_)
  intro r s2 hr _ g2 e2 h2 hun2
  have hm2 : Ty.WF s2.sz 0 (.mv m) := hm.mono (by omega) (Nat.le_refl _)
  have hu2 : s2.Unbound m := hun2 _ (hun1 _ hu)
  have hw : Ty.WF s2.sz 0 (.arr ps r .pure) :=
    Ty.WF_arr.mpr ⟨fun t ht => (hps t ht).mono (by omega) (Nat.le_refl _), hr⟩
  refine Wp.bind (bindMeta_leaf h2 (Ty.WF_mv.mp hm2) hu2 hw ?_)
  intro s3 hsz e3 h3 hb
  exact Wp.pure' (h ps r s3 (e1.trans (e2.trans e3)) h3
    (fun t ht => (hps t ht).mono (by omega) (Nat.le_refl _)) (hr.mono (by omega) (Nat.le_refl _))
    hl hb)

macro_rules
  | `(tactic| sound_step) => `(tactic| leaf peel_mv_leaf)

theorem peelF_sound : ∀ (k : Nat) (t : Ty) (n : Nat) (Q : List Ty × Ty × Eff → CState → Prop)
    (s : CState), s.WF → Ty.WF s.sz 0 t →
    (∀ ps r e s', Ext s s' → s'.WF → (∀ t ∈ ps, Ty.WF s'.sz 0 t) →
      Ty.WF s'.sz 0 r →
      (∀ θ, Solves θ s' → Peel (Ty.app θ t) n (ps.map (Ty.app θ)) (Ty.app θ r) e) →
      Q (ps, r, e) s') →
    Wp (peelF k t n) Q s := by
  intro k
  induction k with
  | zero => intros; exact Wp.fuel'
  | succ k ih =>
    intro t n Q s hs ht h
    simp only [peelF]
    refine Wp.bind (shallowF_leaf hs ht ?_)
    intro t' s1 e1 ht' hua hsa
    subst e1
    have peelk : ∀ {Q : List Ty × Ty × Eff → CState → Prop} {s : CState} {t : Ty} {n : Nat}
        (hs : s.WF) (ht : Ty.WF s.sz 0 t)
        (h : ∀ ps r e s', Ext s s' → s'.WF → (∀ t ∈ ps, Ty.WF s'.sz 0 t) →
          Ty.WF s'.sz 0 r →
          (∀ θ, Solves θ s' → Peel (Ty.app θ t) n (ps.map (Ty.app θ)) (Ty.app θ r) e) →
          Q (ps, r, e) s'),
        Wp (peelF k t n) Q s := fun hs ht h => ih _ _ _ _ hs ht h
    split
    all_goals repeat' (first | sound_step | leaf peelk)
    · rename_i _ ps r e hn
      refine h ps r e s1 (Ext.refl _) hs (Ty.WF.arr_params ht' (Nat.le_refl _))
        (Ty.WF.arr_res ht' (Nat.le_refl _)) ?_
      intro θ hθ
      rw [← hsa θ hθ, Ty.app_arr, Nat.eq_of_beq_eq_true hn]
      have := peel_mkArr_exact (ps := ps.map (Ty.app θ)) (e := e)
        (Ty.app_solved hθ.solved (Ty.WF.arr_res ht' (Nat.le_refl _)))
      rwa [List.length_map] at this
    · rename_i _ ps r e hne hemp hn0 hlt
      refine h (ps.take n) (mkArr (ps.drop n) r e) .pure s1 (Ext.refl _) hs
        (Ty.WF.arr_take ht' (Nat.le_refl _) n)
        (Ty.WF_mkArr.mpr (Ty.WF.arr_drop ht' (Nat.le_refl _) n)) ?_
      intro θ hθ
      rw [← hsa θ hθ, Ty.app_arr, Ty.app_mkArr, List.map_take, List.map_drop]
      have hn0' : 0 < n := by simp at hn0; omega
      exact peel_mkArr_rest (Ty.app_solved hθ.solved (Ty.WF.arr_res ht' (Nat.le_refl _))) hn0'
        (by simpa using hlt)
    · rename_i _ ps r e hne hemp hn0 hlt ps2 r2 e2 s' e1 w1 hps2 hr2 hrec
      refine h (ps ++ ps2) r2 (e.join e2) s' e1 w1 ?_ hr2 ?_
      · intro t ht
        rcases List.mem_append.mp ht with ht | ht
        · exact (Ty.WF.arr_params ht' (Nat.le_refl _) t ht).mono (by omega) (Nat.le_refl _)
        · exact hps2 t ht
      · intro θ hθ
        rw [← hsa θ (hθ.back e1), Ty.app_arr, List.map_append]
        have hlt' : ps.length < n := by simp at hne hlt; omega
        have hps : ps.map (Ty.app θ) ≠ [] := by
          intro hnil
          have := List.map_eq_nil_iff.mp hnil
          subst this
          simp at hemp
        refine peel_over_mkArr
          (Ty.app_solved hθ.solved (Ty.WF.arr_res ht' (Nat.le_refl _))) hps (by simpa using hlt') ?_
        rw [List.length_map]
        exact hrec θ hθ
    · rename_i _ m ps r s' e1 w1 hps hr hfact
      refine h ps r .pure s' e1 w1 hps hr ?_
      intro θ hθ
      rw [← hsa θ (hθ.back e1)]
      show Peel (θ m) _ _ _ _
      rw [hfact θ hθ, Ty.app_arr]
      have := peel_mkArr_exact (ps := ps.map (Ty.app θ)) (e := .pure)
        (Ty.app_solved hθ.solved hr)
      rwa [List.length_map] at this

theorem peel_sound {Q : List Ty × Ty × Eff → CState → Prop} {s : CState} {t : Ty} {n : Nat}
    (hs : s.WF) (ht : Ty.WF s.sz 0 t)
    (h : ∀ ps r e s', Ext s s' → s'.WF → (∀ t ∈ ps, Ty.WF s'.sz 0 t) →
      Ty.WF s'.sz 0 r →
      (∀ θ, Solves θ s' → Peel (Ty.app θ t) n (ps.map (Ty.app θ)) (Ty.app θ r) e) →
      Q (ps, r, e) s') : Wp (peel t n) Q s := by
  unfold peel
  exact Wp.bind (Wp.read' fun k => peelF_sound k t n Q s hs ht h)

macro_rules
  | `(tactic| sound_step) => `(tactic| leaf peel_sound)

/-! ### `expectBool` -/

theorem expectBool_sound {Q : PUnit → CState → Prop} {s : CState} {t : Ty} {key detail : String}
    (hs : s.WF) (ht : Ty.WF s.sz 0 t)
    (h : ∀ s', Ext s s' → s'.WF → (∀ θ, Solves θ s' → Ty.app θ t = .bool) →
      Q ⟨⟩ s') :
    Wp (expectBool t key detail) Q s := by
  unfold expectBool
  refine Wp.bind (Wp.mono (Wp.tryCatch' (Q := fun (ok : Bool) s' => ok = true →
      (Ext s s' ∧ s'.WF ∧ ∀ θ, Solves θ s' → Ty.app θ t = .bool)) ?_
      (fun _ _ => ?_) rfl) fun ok s1 hq => ?_)
  · refine Wp.bind (flow_sound hs ht Ty.WF_bool ?_)
    intro s' e hw hf
    exact Wp.pure' fun _ => ⟨e, hw, fun θ hθ => Flows.to_bool (hf θ hθ)⟩
  · exact Wp.pure' fun hf => by cases hf
  · cases ok with
    | false => exact Wp.reject' _ _
    | true =>
      obtain ⟨e, hw, hf⟩ := hq rfl
      exact Wp.pure' (h s1 e hw hf)

macro_rules
  | `(tactic| sound_step) => `(tactic| leaf expectBool_sound)

end SproutCore
