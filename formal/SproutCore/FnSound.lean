import SproutCore.LetSound

/-!
# `inferFnGroup` (docs/lean-model-v0.md §9, 1b step 3b, part 4)

`inferFnGroup_sound`: a successful `inferFnGroup {}` from a well-formed, named-free state and a
closed environment types the group by `GroupTy.fnG`, for every grounding fresh above a `B` the
caller picks after the run. One `FnWit` per member, in the reverse of the member order (the checker
conses `groupEnv` and `out`): `σ` is the member's skolem map, `τ` the grounded member type, `s` the
scheme (the complete signature's, or what `generalize` built).

* Pure facts: a written type read through a map (`Ty.elab`) commutes with instantiation and keeps
  skolem-freeness; `ParamsTy` is functional and commutes with instantiation for written parameters.
* Leaf lemmas: `skolemLoop_sound`, `build_sound`, `scheme_sound`, `checkBody_sound`.
* `MemInv` is what the first loop knows of a member, `BodyOK` what the second types, `OutRel` what
  the third records. `member_wit` builds one witness, `fnWitnesses` the list, `group_assemble` the
  `GroupTy`.
-/

namespace SproutCore

open Judgment

set_option linter.unusedVariables false

theorem noSkList_iff {ts : List Ty} : Ty.noSkList ts = true ↔ ∀ t ∈ ts, t.noSk = true := by
  induction ts with
  | nil => simp [Ty.noSkList]
  | cons t ts ih => simp [Ty.noSkList, ih]

theorem noSk_mkArr {ps : List Ty} {r : Ty} {e : Eff} (hps : Ty.noSkList ps = true)
    (hr : r.noSk = true) : (mkArr ps r e).noSk = true := by
  unfold mkArr
  split
  · rename_i ps' r' e'
    split
    · simp only [Ty.noSk, Bool.and_eq_true] at hr ⊢
      exact ⟨hps, hr⟩
    · simp only [Ty.noSk, Bool.and_eq_true] at hr ⊢
      refine ⟨?_, hr.2⟩
      rw [noSkList_iff] at hps hr ⊢
      intro t ht
      rcases List.mem_append.mp ht with ht | ht
      · exact hps t ht
      · exact hr.1 t ht
  · simp [Ty.noSk, hps, hr]

mutual
theorem Ty.elab_noSk {σ : String → Ty} (hσ : ∀ s, (σ s).noSk = true) :
    ∀ (t r : Ty), t.elab σ = some r → r.noSk = true
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
    simp only [Ty.noSk]
    exact noSkList_iff.mpr (Ty.elabList_noSk hσ ts rs hrs)
  | .arr ps r0 e, r, h => by
    simp only [Ty.elab] at h
    split at h
    · rename_i ps' r' hps hr'
      split at h
      · cases h
      · simp only [Option.some.injEq] at h
        subst h
        exact noSk_mkArr (noSkList_iff.mpr (Ty.elabList_noSk hσ ps ps' hps)) (Ty.elab_noSk hσ r0 r' hr')
    · cases h

theorem Ty.elabList_noSk {σ : String → Ty} (hσ : ∀ s, (σ s).noSk = true) :
    ∀ (ts rs : List Ty), Ty.elabList σ ts = some rs → ∀ r ∈ rs, r.noSk = true
  | [], rs, h => by simp only [Ty.elabList, Option.some.injEq] at h; subst h; simp
  | t :: ts, rs, h => by
    simp only [Ty.elabList] at h
    split at h
    · rename_i t' ts' ht hts
      simp only [Option.some.injEq] at h
      subst h
      intro r hr
      rcases List.mem_cons.mp hr with e | hr
      · rw [e]; exact Ty.elab_noSk hσ t t' ht
      · exact Ty.elabList_noSk hσ ts ts' hts r hr
    · cases h
end

mutual
/-- Reading a written type commutes with instantiating the map's values. -/
theorem Ty.elab_inst (ts : List Ty) {σ : String → Ty} :
    ∀ (t r : Ty), t.elab σ = some r → t.elab (fun v => Ty.inst ts (σ v)) = some (Ty.inst ts r)
  | .named s, r, h => by
    simp only [Ty.elab, Option.some.injEq] at h
    subst h; rfl
  | .int, r, h => by simp only [Ty.elab, Option.some.injEq] at h; subst h; rfl
  | .bool, r, h => by simp only [Ty.elab, Option.some.injEq] at h; subst h; rfl
  | .str, r, h => by simp only [Ty.elab, Option.some.injEq] at h; subst h; rfl
  | .unit, r, h => by simp only [Ty.elab, Option.some.injEq] at h; subst h; rfl
  | .mv _, r, h => by simp [Ty.elab] at h
  | .skolem _, r, h => by simp [Ty.elab] at h
  | .gen _, r, h => by simp [Ty.elab] at h
  | .tup ts', r, h => by
    simp only [Ty.elab, Option.map_eq_some_iff] at h
    obtain ⟨rs, hrs, rfl⟩ := h
    have := Ty.elabList_inst ts ts' rs hrs
    simp only [Ty.elab, this, Option.map_some, Ty.inst, Ty.instList_eq_map]
  | .arr ps r0 e, r, h => by
    simp only [Ty.elab] at h
    split at h
    · rename_i ps' r' hps hr'
      split at h
      · cases h
      · rename_i hnot
        simp only [Option.some.injEq] at h
        subst h
        have h1 := Ty.elabList_inst ts ps ps' hps
        have h2 := Ty.elab_inst ts r0 r' hr'
        have hnot' : (r0.isArr && e == Eff.io) = false := by simpa using hnot
        simp only [Ty.elab, h1, h2, hnot']
        rw [Ty.inst_mkArr]
        simp
    · cases h

theorem Ty.elabList_inst (ts : List Ty) {σ : String → Ty} :
    ∀ (tl rs : List Ty), Ty.elabList σ tl = some rs →
      Ty.elabList (fun v => Ty.inst ts (σ v)) tl = some (rs.map (Ty.inst ts))
  | [], rs, h => by simp only [Ty.elabList, Option.some.injEq] at h; subst h; rfl
  | t :: tl, rs, h => by
    simp only [Ty.elabList] at h
    split at h
    · rename_i t' ts' ht hts
      simp only [Option.some.injEq] at h
      subst h
      simp only [Ty.elabList, Ty.elab_inst ts t t' ht, Ty.elabList_inst ts tl ts' hts, List.map_cons]
    · cases h
end

/-- `Ty.elab` depends only on the map at the written variables; two maps equal there agree. -/
theorem Ty.elab_congr {σ σ' : String → Ty} (h : ∀ v, σ v = σ' v) :
    ∀ t : Ty, t.elab σ = t.elab σ' := by
  have : σ = σ' := funext h
  subst this
  intro t; rfl

/-! ### `ParamsTy` and `RetTy` for a complete signature -/

/-- No omitted parameter. -/
def AllWritten (prs : List (String × Option Ty)) : Prop := ∀ p ∈ prs, p.2.isSome = true

theorem ParamsTy.snoc {σ : String → Ty} {prs : List (String × Option Ty)} {ps : List Ty}
    (h : ParamsTy σ prs ps) (x : String) (o : Option Ty) (p : Ty)
    (hp : match o with | some a => a.elab σ = some p | none => p.Solved) :
    ParamsTy σ (prs ++ [(x, o)]) (ps ++ [p]) := by
  induction h with
  | nil =>
    cases o with
    | some a => exact ParamsTy.written x a p [] [] hp ParamsTy.nil
    | none => exact ParamsTy.omitted x p [] [] hp ParamsTy.nil
  | written x' a p' prs' ps' hel hrest ih =>
    exact ParamsTy.written x' a p' _ _ hel ih
  | omitted x' p' prs' ps' hs hrest ih =>
    exact ParamsTy.omitted x' p' _ _ hs ih

theorem ParamsTy.length_eq {σ : String → Ty} {prs : List (String × Option Ty)} {ps : List Ty}
    (h : ParamsTy σ prs ps) : ps.length = prs.length := by
  induction h with
  | nil => rfl
  | written _ _ _ _ _ _ _ ih => simp [ih]
  | omitted _ _ _ _ _ _ ih => simp [ih]

/-- For written parameters, the types are what the written types read as. -/
theorem ParamsTy.det {σ : String → Ty} {prs : List (String × Option Ty)} {ps qs : List Ty}
    (hw : AllWritten prs) (h1 : ParamsTy σ prs ps) (h2 : ParamsTy σ prs qs) : ps = qs := by
  induction h1 generalizing qs with
  | nil => cases h2; rfl
  | written x a p prs' ps' hel hrest ih =>
    cases h2 with
    | written _ _ q _ qs' hel' hrest' =>
      rw [hel] at hel'
      cases hel'
      rw [ih (fun p hp => hw p (List.mem_cons_of_mem _ hp)) hrest']
  | omitted x p prs' ps' hs hrest ih =>
    have := hw (x, none) (List.mem_cons_self ..)
    simp at this

theorem ParamsTy.inst {σ σ' : String → Ty} (ts : List Ty)
    (hσ : ∀ v, Ty.inst ts (σ v) = σ' v) {prs : List (String × Option Ty)} {ps : List Ty}
    (hw : AllWritten prs) (h : ParamsTy σ prs ps) : ParamsTy σ' prs (ps.map (Ty.inst ts)) := by
  induction h with
  | nil => exact ParamsTy.nil
  | written x a p prs' ps' hel hrest ih =>
    refine ParamsTy.written x a _ prs' _ ?_ (ih fun p hp => hw p (List.mem_cons_of_mem _ hp))
    have := Ty.elab_inst ts a p hel
    rwa [Ty.elab_congr hσ] at this
  | omitted x p prs' ps' hs hrest ih =>
    have := hw (x, none) (List.mem_cons_self ..)
    simp at this


/-! ### Loops that know their prefix -/

/-- A loop over a list whose invariant also sees the prefix already processed. No step ends the
loop early (`done` is not reachable). -/
theorem Wp.forIn_prefix {α β : Type} {f : α → β → M (ForInStep β)} {Q : β → CState → Prop}
    (I : List α → β → CState → Prop) (l : List α) (init : β) {s : CState} (h0 : I [] init s)
    (hf : ∀ pre a post b s1, pre ++ a :: post = l → I pre b s1 →
      Wp (f a b) (fun r s2 => ∃ b', r = ForInStep.yield b' ∧ I (pre ++ [a]) b' s2) s1)
    (hq : ∀ b s1, I l b s1 → Q b s1) : Wp (ForIn.forIn l init f) Q s := by
  have gen : ∀ (rest pre : List α) (b : β) (s : CState), pre ++ rest = l → I pre b s →
      Wp (ForIn.forIn rest b f) Q s := by
    intro rest
    induction rest with
    | nil =>
      intro pre b s hl hI
      simp only [List.forIn_nil]
      simp only [List.append_nil] at hl
      subst hl
      exact Wp.pure' (hq b s hI)
    | cons a post ih =>
      intro pre b s hl hI
      simp only [List.forIn_cons]
      refine Wp.bind (Wp.mono (hf pre a post b s hl hI) ?_)
      rintro r s2 ⟨b', rfl, hI'⟩
      exact ih (pre ++ [a]) b' s2 (by simpa using hl) hI'
  exact gen l [] init s (by simp) h0

/-! ### Fresh skolems -/

theorem Ty.skolem_inj {a b : Nat} (h : Ty.skolem a = Ty.skolem b) : a = b := by cases h; rfl

theorem freshSkolem_spec {s : CState} :
    Wp freshSkolem (fun a s' => a = .skolem s.nextSk ∧ s'.nextSk = s.nextSk + 1 ∧
      s'.subst = s.subst) s := by
  intro n a s' hr
  have h2 : (Except.ok (.skolem s.nextSk, { s with nextSk := s.nextSk + 1 }) :
      Except CErr (Ty × CState)) = .ok (a, s') := hr
  cases h2
  exact ⟨rfl, rfl, rfl⟩

/-- `freshSkolem` for each variable: keys are the variables, values are skolems numbered from the
counter, pairwise distinct, below the final counter. The substitution is untouched. -/
theorem skolemLoop_sound {vs : List String} {Q : List (String × Ty) → CState → Prop} {s : CState}
    (hs : s.WF)
    (h : ∀ sks s', Ext s s' → s'.WF → s'.subst = s.subst → sks.map Prod.fst = vs →
      (∀ q ∈ sks, ∃ k, q.2 = .skolem k ∧ s.nextSk ≤ k ∧ k < s'.nextSk) →
      (sks.map Prod.snd).Nodup → s.nextSk ≤ s'.nextSk → Q sks s') :
    Wp (forIn vs ([] : List (String × Ty)) fun v sks => do
      let d ← freshSkolem
      pure (ForInStep.yield (sks ++ [(v, d)]))) Q s := by
  refine Wp.forIn_prefix (fun (pre : List String) (sks : List (String × Ty)) s1 =>
    Ext s s1 ∧ s1.WF ∧ s1.subst = s.subst ∧ sks.map Prod.fst = pre ∧
      (∀ q ∈ sks, ∃ k, q.2 = .skolem k ∧ s.nextSk ≤ k ∧ k < s1.nextSk) ∧
      (sks.map Prod.snd).Nodup ∧ s.nextSk ≤ s1.nextSk) vs [] ⟨Ext.refl _, hs, rfl, rfl, by simp,
        List.nodup_nil, Nat.le_refl _⟩ ?_ ?_
  · intro pre v post sks s1 hpre ⟨e1, h1, hsub, hkeys, hr, hnd, hle⟩
    refine Wp.bind (Wp.mono (Wp.and' (freshSkolem_leaf (Q := fun _ s' => Ext s1 s' ∧ s'.WF) h1
      (fun a s' _ _ e w => ⟨e, w⟩)) (freshSkolem_spec (s := s1))) ?_)
    rintro d s2 ⟨⟨e2, h2⟩, hd, hnk, hsub2⟩
    refine Wp.pure' ⟨_, rfl, e1.trans e2, h2, hsub2.trans hsub, by simp [hkeys], ?_, ?_, ?_⟩
    · intro q hq
      rcases List.mem_append.mp hq with hq | hq
      · obtain ⟨k, hk, h1', h2'⟩ := hr q hq
        exact ⟨k, hk, h1', by omega⟩
      · have := List.mem_singleton.mp hq
        subst this
        exact ⟨s1.nextSk, hd, by omega, by omega⟩
    · rw [List.map_append]
      refine List.nodup_append.mpr ⟨hnd, by simp, ?_⟩
      intro a ha b hb hab
      obtain ⟨q, hq, rfl⟩ := List.mem_map.mp ha
      have hb' : b = d := by simpa using hb
      obtain ⟨k, hk, _, hlt⟩ := hr q hq
      rw [hb', hd, hk] at hab
      have := Ty.skolem_inj hab
      omega
    · omega
  · intro sks s1 ⟨e1, h1, hsub, hkeys, hr, hnd, hle⟩
    exact h sks s1 e1 h1 hsub hkeys hr hnd hle


/-! ### `FnInfo.build` -/

theorem fresh_leaf_sk {Q : Ty → CState → Prop} {s : CState} (hs : s.WF)
    (h : ∀ a s', Ty.WF s'.sz 0 a → (∃ j, a = .mv j) → Ext s s' → s'.WF → s'.nextSk = s.nextSk →
      Q a s') : Wp fresh Q s := by
  intro n a s' hr
  have h2 : (Except.ok (.mv s.sz, { s with subst := s.subst.push none }) :
      Except CErr (Ty × CState)) = .ok (a, s') := hr
  cases h2
  refine h _ _ ?_ ⟨_, rfl⟩ (Ext.push rfl) (hs.push rfl) rfl
  simp [CState.sz]

/-- `elabTy` with a map of in-range, named-free, `ok g` values that are not arrows. -/
theorem elabTy_leaf_gen {Q : Ty → CState → Prop} {s : CState} {a : Ty} {f : String → Ty} {g : Nat}
    (hArr : ∀ x, (f x).isArr = false) (hwf : ∀ x, Ty.WF s.sz g (f x)) (hnf : ∀ x, (f x).NF)
    (hok : ∀ x, (f x).ok g = true) (hsf : Ty.surf a = true)
    (h : ∀ r, a.elab f = some r → Ty.WF s.sz g r → r.NF → r.ok g = true → Q r s) :
    Wp (elabTy f a) Q s := by
  refine elabTy_elab f hArr a Q s hsf fun r s' e he => ?_
  subst e
  exact h r he (Ty.elab_wf hwf a r he) (Ty.elab_nf hnf a r he) (Ty.elab_ok hok a r he)

/-- The facts `build` keeps. `RetB` is the return type's relation to the signature. -/
def RetB (m : String → Ty) (sig : Option (Ty × Eff)) (r : Ty) (e : Eff) : Prop :=
  match sig with
  | some (a, e0) => a.elab m = some r ∧ e = e0 ∧ ¬ (r.isArr = true ∧ e0 = .io)
  | none => e = .pure ∧ ∃ j, r = .mv j

theorem build_sound {Q : List Ty × Ty × Eff × Ty → CState → Prop} {s : CState} {f : FnInfo}
    {m : String → Ty} {g : Nat} (hs : s.WF)
    (hArr : ∀ x, (m x).isArr = false) (hwf : ∀ x, Ty.WF s.sz g (m x)) (hnf : ∀ x, (m x).NF)
    (hok : ∀ x, (m x).ok g = true)
    (hsp : ∀ p ∈ f.params, ∀ t, p.2 = some t → t.surf = true)
    (hsr : ∀ r e, f.sig = some (r, e) → r.surf = true)
    (h : ∀ ps r e t s', Ext s s' → s'.WF → s'.nextSk = s.nextSk →
      (∀ x ∈ ps, Ty.WF s'.sz g x ∧ x.NF) →
      (Ty.WF s'.sz g r ∧ r.NF) → t = mkArr ps r e → ps.length = f.params.length →
      (∀ θ, Solves θ s' → ParamsTy m f.params (ps.map (Ty.app θ))) →
      RetB m f.sig r e → (¬ (r.isArr = true ∧ e = .io)) →
      (f.complete = true → s' = s ∧ ParamsTy m f.params ps ∧ (∀ x ∈ ps, x.ok g = true) ∧
        r.ok g = true) →
      Q (ps, r, e, t) s') :
    Wp (f.build m) Q s := by
  unfold FnInfo.build
  dsimp only
  refine Wp.bind (Wp.forIn_prefix
    (fun (pre : List (String × Option Ty)) (ps : List Ty) s1 =>
      Ext s s1 ∧ s1.WF ∧ s1.nextSk = s.nextSk ∧ ps.length = pre.length ∧
        (∀ x ∈ ps, Ty.WF s1.sz g x ∧ x.NF) ∧
        (∀ θ, Solves θ s1 → ParamsTy m pre (ps.map (Ty.app θ))) ∧
        (AllWritten pre → s1 = s ∧ ParamsTy m pre ps ∧ ∀ x ∈ ps, x.ok g = true))
    f.params [] ⟨Ext.refl _, hs, rfl, rfl, by simp, fun θ _ => ParamsTy.nil,
      fun _ => ⟨rfl, ParamsTy.nil, by simp⟩⟩ ?_ ?_)
  · intro pre a post ps s1 hpre ⟨e1, h1, hnk, hlen, hr, hP1, hP2⟩
    obtain ⟨x, o⟩ := a
    have hmem : (x, o) ∈ f.params := by rw [← hpre]; simp
    cases o with
    | some t0 =>
      have hsf := hsp (x, some t0) hmem t0 rfl
      dsimp only
      refine Wp.bind (elabTy_leaf_gen (g := g) hArr (fun x => (hwf x).mono e1.sz (Nat.le_refl _))
        hnf hok hsf ?_)
      intro p hel hwp hnp hokp
      refine Wp.pure' ⟨_, rfl, e1, h1, hnk, by simp [hlen], ?_, ?_, ?_⟩
      · intro y hy
        rcases List.mem_append.mp hy with hy | hy
        · exact hr y hy
        · rw [List.mem_singleton.mp hy]; exact ⟨hwp, hnp⟩
      · intro θ hθ
        rw [List.map_append]
        refine ParamsTy.snoc (hP1 θ hθ) x (some t0) _ ?_
        rw [Ty.app_of_ok_gen θ p hokp]
        exact hel
      · intro hw
        have hw' : AllWritten pre := fun q hq => hw q (List.mem_append_left _ hq)
        obtain ⟨e', hP, hok'⟩ := hP2 hw'
        refine ⟨e', ParamsTy.snoc hP x (some t0) p hel, ?_⟩
        intro y hy
        rcases List.mem_append.mp hy with hy | hy
        · exact hok' y hy
        · rw [List.mem_singleton.mp hy]; exact hokp
    | none =>
      dsimp only
      refine Wp.bind (fresh_leaf_sk h1 ?_)
      intro d s2 hd ⟨j, hj⟩ e2 h2 hnk2
      subst hj
      have hj2 : j < s2.sz := Ty.WF_mv.mp hd
      refine Wp.pure' ⟨_, rfl, e1.trans e2, h2, hnk2.trans hnk, by simp [hlen], ?_, ?_, ?_⟩
      · intro y hy
        rcases List.mem_append.mp hy with hy | hy
        · exact ⟨((hr y hy).1).mono e2.sz (Nat.le_refl _), (hr y hy).2⟩
        · rw [List.mem_singleton.mp hy]; exact ⟨Ty.WF_mv.mpr hj2, Ty.NF_mv⟩
      · intro θ hθ
        rw [List.map_append]
        refine ParamsTy.snoc (hP1 θ (hθ.back e2)) x none _ ?_
        exact hθ.solved j
      · intro hw
        have := hw (x, none) (List.mem_append_right _ (List.mem_singleton_self _))
        simp at this
  · intro ps s1 ⟨e1, h1, hnk, hlen, hr, hP1, hP2⟩
    rcases hsig : f.sig with _ | ⟨a0, e0⟩
    · refine Wp.bind (fresh_leaf_sk h1 ?_)
      intro d s2 hd ⟨j, hj⟩ e2 h2 hnk2
      subst hj
      have hj2 : j < s2.sz := Ty.WF_mv.mp hd
      refine Wp.bind (Wp.pure' (a := ((Ty.mv j, Eff.pure) : Ty × Eff)) ?_)
      dsimp only
      refine Wp.pure' (h ps (.mv j) .pure _ s2 (e1.trans e2) h2 (hnk2.trans hnk) ?_ ⟨hd.mono (Nat.le_refl _) (Nat.zero_le g), Ty.NF_mv⟩ rfl hlen ?_ ?_
        (by simp [Ty.isArr]) ?_)
      · intro y hy
        exact ⟨((hr y hy).1).mono e2.sz (Nat.le_refl _), (hr y hy).2⟩
      · intro θ hθ
        exact hP1 θ (hθ.back e2)
      · rw [hsig]; unfold RetB; exact ⟨rfl, j, rfl⟩
      · intro hc
        simp [FnInfo.complete, hsig] at hc
    · refine Wp.bind (elabTy_leaf_gen (g := g) hArr
        (fun x => (hwf x).mono e1.sz (Nat.le_refl _)) hnf hok
        (hsr a0 e0 hsig) ?_)
      intro r hel hwr hnr hokr
      refine Wp.bind (Wp.pure' (a := ((r, e0) : Ty × Eff)) ?_)
      dsimp only
      split
      · exact Wp.bind (Wp.reject' _ _)
      · rename_i hne
        have hno : ¬ (r.isArr = true ∧ e0 = .io) := by
          rintro ⟨hA, hI⟩
          cases r <;> simp [Ty.isArr] at hA
          exact hne _ _ _ rfl hI
        refine Wp.pure' (h ps r e0 _ s1 e1 h1 hnk hr ⟨hwr, hnr⟩ rfl hlen hP1 ?_ hno ?_)
        · rw [hsig]; unfold RetB; exact ⟨hel, rfl, hno⟩
        · intro hc
          simp only [FnInfo.complete, hsig, Bool.and_eq_true, List.all_eq_true] at hc
          obtain ⟨_, hall⟩ := hc
          obtain ⟨e', hP, hok'⟩ := hP2 (fun p hp => by simpa using hall p hp)
          exact ⟨e', hP, hok', hokr⟩


theorem ParamsTy.noSk {σ : String → Ty} (hσ : ∀ s, (σ s).noSk = true)
    {prs : List (String × Option Ty)} {ps : List Ty} (hw : AllWritten prs)
    (h : ParamsTy σ prs ps) : ∀ p ∈ ps, p.noSk = true := by
  induction h with
  | nil => simp
  | written x a p prs' ps' hel hrest ih =>
    intro q hq
    rcases List.mem_cons.mp hq with e | hq
    · rw [e]; exact Ty.elab_noSk hσ a p hel
    · exact ih (fun p hp => hw p (List.mem_cons_of_mem _ hp)) q hq
  | omitted x p prs' ps' hs hrest ih =>
    have := hw (x, none) (List.mem_cons_self ..)
    simp at this

/-! ### `FnInfo.scheme` -/

/-- The map `scheme` reads the signature through: written variable to its `gen` index. -/
def FnInfo.gmap (f : FnInfo) : String → Ty :=
  fun s => match f.writtenVars.findIdx? (· == s) with | some i => Ty.gen i | none => Ty.unit

theorem gmap_props (f : FnInfo) (sz : Nat) :
    (∀ x, (f.gmap x).isArr = false) ∧ (∀ x, Ty.WF sz f.writtenVars.length (f.gmap x)) ∧
      (∀ x, (f.gmap x).NF) ∧ (∀ x, (f.gmap x).ok f.writtenVars.length = true) ∧
      (∀ x, (f.gmap x).noSk = true) := by
  refine ⟨fun x => ?_, fun x => writtenMap_wf _ _ x, fun x => ?_, fun x => ?_, fun x => ?_⟩ <;>
    unfold FnInfo.gmap <;> split <;> rename_i hi
  all_goals first
    | rfl
    | (have := (List.findIdx?_eq_some_iff_findIdx_eq.mp hi).1
       simp [Ty.ok, this])
    | simp [Ty.NF, namedIn, Ty.ok, Ty.noSk, Ty.isArr]

theorem scheme_sound {Q : Nat × Ty → CState → Prop} {s : CState} {f : FnInfo} (hs : s.WF)
    (hsp : ∀ p ∈ f.params, ∀ t, p.2 = some t → t.surf = true)
    (hsr : ∀ r e, f.sig = some (r, e) → r.surf = true) (hc : f.complete = true)
    (h : ∀ n sc, n = f.writtenVars.length → sc.ok n = true → sc.noSk = true →
      (∀ (m : String → Ty) (ts : List Ty), (∀ v, Ty.inst ts (f.gmap v) = m v) →
        ∀ ps r e, ParamsTy m f.params ps → RetB m f.sig r e → Ty.inst ts sc = mkArr ps r e) →
      Q (n, sc) s) :
    Wp f.scheme Q s := by
  unfold FnInfo.scheme
  dsimp only
  obtain ⟨hA, hW, hN, hO, hS⟩ := gmap_props f s.sz
  refine Wp.bind (build_sound (g := f.writtenVars.length) (m := f.gmap) hs hA hW hN hO hsp hsr ?_)
  intro ps r e t s' e1 w1 hnk hps hr ht hlen hP1 hRB hno hcomp
  obtain ⟨rfl, hP, hokps, hokr⟩ := hcomp hc
  have hw : AllWritten f.params := by
    simp only [FnInfo.complete, Bool.and_eq_true, List.all_eq_true] at hc
    exact fun p hp => by simpa using hc.2 p hp
  obtain ⟨a0, e0, hsig⟩ : ∃ a e0, f.sig = some (a, e0) := by
    simp only [FnInfo.complete, Bool.and_eq_true] at hc
    cases hsg : f.sig with
    | none => simp [hsg] at hc
    | some p => exact ⟨p.1, p.2, rfl⟩
  rw [hsig] at hRB
  unfold RetB at hRB
  obtain ⟨hel, rfl, _⟩ := hRB
  refine Wp.pure' (h _ _ rfl ?_ ?_ ?_)
  · rw [ht]; exact Ty.ok_mkArr (Ty.okList_iff.mpr hokps) hokr
  · rw [ht]
    exact noSk_mkArr (noSkList_iff.mpr (ParamsTy.noSk hS hw hP)) (Ty.elab_noSk hS a0 r hel)
  · intro m ts hm ps' r' e' hP' hRB'
    rw [hsig] at hRB'
    unfold RetB at hRB'
    obtain ⟨hel', rfl, _⟩ := hRB'
    rw [ht, Ty.inst_mkArr]
    have hps : ps' = ps.map (Ty.inst ts) := ParamsTy.det hw hP' (ParamsTy.inst ts hm hw hP)
    have hr' : r' = Ty.inst ts r := by
      have := Ty.elab_inst ts a0 r hel
      rw [Ty.elab_congr hm] at this
      rw [hel'] at this
      exact Option.some.inj this
    rw [hps, hr']


/-! ### `checkBody` -/

theorem foldl_nf : ∀ (params : List (String × Ty)) (env : Env), Env.NF env →
    (∀ p ∈ params, p.2.NF) →
    Env.NF (params.foldl (fun env (x, t) => (x, Bind.mono t) :: env) env)
  | [], env, he, _ => he
  | (x, t) :: ps, env, he, hp => by
    simp only [List.foldl_cons]
    exact foldl_nf ps _ (he.cons (x := x) (b := .mono t) (hp (x, t) (List.mem_cons_self ..)))
      (fun p hp' => hp p (List.mem_cons_of_mem _ hp'))

theorem Env.app_foldl (θ : Nat → Ty) : ∀ (params : List (String × Ty)) (env : Env),
    Env.app θ (params.foldl (fun env (x, t) => (x, Bind.mono t) :: env) env) =
      bindParams (Env.app θ env) (params.map fun p => (p.1, Ty.app θ p.2))
  | [], env => rfl
  | (x, t) :: ps, env => by
    simp only [List.foldl_cons, List.map_cons]
    rw [Env.app_foldl θ ps]
    simp [bindParams, Env.app_cons, Bind.app]

theorem checkBody_sound {Q : PUnit → CState → Prop} {s : CState} {env : Env}
    {params : List (String × Ty)} {ret : Ty} {eff : Eff} {body : Expr} (hs : s.WF) (hnf : s.NF)
    (he : Env.WF s.sz env) (hen : Env.NF env) (hp : ∀ p ∈ params, Ty.WF s.sz 0 p.2)
    (hpn : ∀ p ∈ params, p.2.NF) (hr : Ty.WF s.sz 0 ret) (hrn : ret.NF) (hsrc : body.Src)
    (hsurf : body.Surf)
    (h : ∀ s', Ext s s' → s'.WF →
      (∀ θ, Solves θ s' → ∃ tb eb, HasType (bindParams (Env.app θ env)
          (params.map fun p => (p.1, Ty.app θ p.2))) body tb eb ∧ Flows tb (Ty.app θ ret) ∧
        eb.le eff = true) → Q ⟨⟩ s') :
    Wp (checkBody {} env params ret eff body) Q s := by
  unfold checkBody
  dsimp only
  refine Wp.bind (infer_sound {} rfl body _ _ s hs hnf (foldl_wf params env he hp)
    (foldl_nf params env hen hpn) hsrc hsurf ?_)
  intro tb eb s1 e1 w1 wtb ntb hty
  refine Wp.bind (flow_sound w1 (e1.nf hnf) wtb ntb (hr.mono e1.sz (Nat.le_refl _)) hrn ?_)
  intro s2 e2 w2 hfl
  split
  · rename_i hle
    refine Wp.pure' (h s2 (e1.trans e2) w2 ?_)
    intro θ hθ
    refine ⟨Ty.app θ tb, eb, ?_, hfl θ hθ, hle⟩
    have := hty θ (hθ.back e2)
    rwa [Env.app_foldl] at this
  · exact Wp.reject' _ _


/-! ### One group -/

def BuiltT.f (b : BuiltT) : FnInfo := b.1
def BuiltT.ps (b : BuiltT) : List Ty := b.2.1
def BuiltT.r (b : BuiltT) : Ty := b.2.2.1
def BuiltT.e (b : BuiltT) : Eff := b.2.2.2.1
def BuiltT.t (b : BuiltT) : Ty := b.2.2.2.2.1
def BuiltT.sch (b : BuiltT) : Option (Nat × Ty) := b.2.2.2.2.2

/-- The map `inferFnGroup` reads a member's written types through. -/
def skMap (sks : List (String × Ty)) : String → Ty := fun s => (sks.lookup s).getD .unit

theorem skMap_props {sks : List (String × Ty)} (h : ∀ q ∈ sks, ∃ k, q.2 = .skolem k) (sz g : Nat) :
    (∀ x, (skMap sks x).isArr = false) ∧ (∀ x, Ty.WF sz g (skMap sks x)) ∧
      (∀ x, (skMap sks x).NF) ∧ (∀ x, (skMap sks x).ok g = true) := by
  have key : ∀ x, skMap sks x = .unit ∨ ∃ k, skMap sks x = .skolem k := by
    intro x
    unfold skMap
    cases hl : sks.lookup x with
    | none => exact Or.inl rfl
    | some b =>
      obtain ⟨l1, l2, hsks, _⟩ := List.lookup_eq_some_iff.mp hl
      obtain ⟨k, hk⟩ := h (x, b) (by rw [hsks]; simp)
      exact Or.inr ⟨k, by simpa using hk⟩
  refine ⟨fun x => ?_, fun x => ?_, fun x => ?_, fun x => ?_⟩ <;> rcases key x with e | ⟨k, e⟩ <;>
    simp [e, Ty.isArr, Ty.NF, namedIn, Ty.ok]

theorem lookup_findIdx : ∀ (sks : List (String × Ty)) (v : String) (i : Nat),
    (sks.map Prod.fst).findIdx? (· == v) = some i →
      ∃ h : i < sks.length, sks.lookup v = some sks[i].2
  | [], v, i, h => by simp at h
  | (k, y) :: rest, v, i, h => by
    simp only [List.map_cons, List.findIdx?_cons] at h
    by_cases hkv : (k == v) = true
    · simp only [hkv, ite_true, Option.some.injEq] at h
      subst h
      refine ⟨by simp, ?_⟩
      simp [List.lookup, beq_iff_eq.mp hkv]
    · simp only [hkv, Bool.false_eq_true, ite_false, Option.map_eq_some_iff] at h
      obtain ⟨j, hj, rfl⟩ := h
      obtain ⟨hlt, hl⟩ := lookup_findIdx rest v j hj
      refine ⟨by simpa using hlt, ?_⟩
      have hne : k ≠ v := fun e => hkv (by simp [e])
      have : (v == k) = false := beq_false_of_ne (Ne.symm hne)
      simpa [List.lookup, this] using hl

theorem lookup_none : ∀ (sks : List (String × Ty)) (v : String),
    (sks.map Prod.fst).findIdx? (· == v) = none → sks.lookup v = none
  | [], v, _ => rfl
  | (k, y) :: rest, v, h => by
    simp only [List.map_cons, List.findIdx?_cons] at h
    by_cases hkv : (k == v) = true
    · simp [hkv] at h
    · simp only [hkv, Bool.false_eq_true, ite_false, Option.map_eq_none_iff] at h
      have hne : k ≠ v := fun e => hkv (by simp [e])
      have : (v == k) = false := beq_false_of_ne (Ne.symm hne)
      simpa [List.lookup, this] using lookup_none rest v h

theorem inst_gmap (f : FnInfo) (sks : List (String × Ty)) (hk : sks.map Prod.fst = f.writtenVars)
    (v : String) : Ty.inst (sks.map Prod.snd) (f.gmap v) = skMap sks v := by
  unfold FnInfo.gmap skMap
  rw [← hk]
  cases hi : (sks.map Prod.fst).findIdx? (· == v) with
  | none => simp [Ty.inst, lookup_none sks v hi]
  | some i =>
    obtain ⟨hlt, hl⟩ := lookup_findIdx sks v i hi
    simp only [Ty.inst, hl, Option.getD_some, List.getD_eq_getElem?_getD, List.getElem?_map]
    simp [hlt]

theorem All2.snoc {α β : Type} {R : α → β → Prop} : ∀ {as : List α} {bs : List β},
    All2 R as bs → ∀ {a : α} {b : β}, R a b → All2 R (as ++ [a]) (bs ++ [b])
  | _, _, All2.nil, a, b, h => All2.cons h All2.nil
  | _, _, All2.cons h1 hs, a, b, h => All2.cons h1 (All2.snoc hs h)

/-- A function's written types are surface types. -/
def FnInfo.Surf (f : FnInfo) : Prop :=
  (∀ p ∈ f.params, ∀ t, p.2 = some t → t.surf = true) ∧
  (∀ r e, f.sig = some (r, e) → r.surf = true) ∧ f.body.Surf

/-- The declaration a `fn` is. -/
def FnInfo.toDecl (f : FnInfo) : Decl := .fn f.name f.params f.sig f.body


def BuiltT.entry (b : BuiltT) : String × Bind :=
  (b.f.name, match b.sch with
    | some (n, sc) => Bind.fnPoly b.f.params.length n sc
    | none => Bind.fnMono b.f.params.length b.t)

/-- What the first loop of `inferFnGroup` knows of a member. -/
structure MemInv (s : CState) (b : BuiltT) (sks : List (String × Ty)) : Prop where
  keys : sks.map Prod.fst = b.f.writtenVars
  sk : ∀ q ∈ sks, ∃ k, q.2 = .skolem k
  ps_wf : ∀ x ∈ b.ps, Ty.WF s.sz 0 x ∧ x.NF
  r_wf : Ty.WF s.sz 0 b.r ∧ b.r.NF
  len : b.ps.length = b.f.params.length
  t_eq : b.t = mkArr b.ps b.r b.e
  par : ∀ θ, Solves θ s → ParamsTy (skMap sks) b.f.params (b.ps.map (Ty.app θ))
  ret : RetB (skMap sks) b.f.sig b.r b.e
  rok : b.f.sig.isSome = true → b.r.ok 0 = true
  comp : b.f.complete = true →
    ParamsTy (skMap sks) b.f.params b.ps ∧ ∀ x ∈ b.ps, x.ok 0 = true
  sch_some : b.sch.isSome = b.f.complete
  sch : ∀ n sc, b.sch = some (n, sc) → n = sks.length ∧ sc.ok n = true ∧ sc.noSk = true ∧
    Ty.inst (sks.map Prod.snd) sc = b.t

theorem MemInv.mono {s s' : CState} {b : BuiltT} {sks : List (String × Ty)} (h : MemInv s b sks)
    (e : Ext s s') : MemInv s' b sks :=
  { h with
    ps_wf := fun x hx => ⟨(h.ps_wf x hx).1.mono e.sz (Nat.le_refl _), (h.ps_wf x hx).2⟩
    r_wf := ⟨h.r_wf.1.mono e.sz (Nat.le_refl _), h.r_wf.2⟩
    par := fun θ hθ => h.par θ (hθ.back e) }

/-- The state of the first loop after `pre`. -/
def GInv1 (s : CState) (pre : List FnInfo) (st : Env × List BuiltT) (s1 : CState) : Prop :=
  Ext s s1 ∧ s1.WF ∧ st.2.map BuiltT.f = pre ∧
    (∃ sksL : List (List (String × Ty)), All2 (MemInv s1) st.2 sksL ∧
      (sksL.flatMap (fun l => l.map Prod.snd)).Nodup ∧
      ∀ l ∈ sksL, ∀ q ∈ l, ∃ k, q.2 = .skolem k ∧ k < s1.nextSk) ∧
    s.nextSk ≤ s1.nextSk ∧
    st.1 = (st.2.map BuiltT.entry).reverse ∧ Env.WF s1.sz st.1 ∧ Env.NF st.1

theorem GInv1.init {s : CState} (hs : s.WF) : GInv1 s [] ([], []) s :=
  ⟨Ext.refl _, hs, rfl, ⟨[], All2.nil, by simp, by simp⟩, Nat.le_refl _, rfl, by simp [Env.WF],
    by simp [Env.NF]⟩


theorem All2.exists_left {α β : Type} {R : α → β → Prop} :
    ∀ {as : List α} {bs : List β}, All2 R as bs → ∀ b ∈ bs, ∃ a, R a b
  | _, _, All2.nil, b, hb => by simp at hb
  | _, _, All2.cons h hs, b, hb => by
    rcases List.mem_cons.mp hb with rfl | hb
    · exact ⟨_, h⟩
    · exact All2.exists_left hs b hb

theorem All2.of_mem_zip {α β : Type} {R : α → β → Prop} :
    ∀ {as : List α} {bs : List β}, All2 R as bs → ∀ a b, (a, b) ∈ as.zip bs → R a b
  | _, _, All2.nil, a, b, h => by simp at h
  | _, _, All2.cons h hs, a, b, hm => by
    simp only [List.zip_cons_cons, List.mem_cons, Prod.mk.injEq] at hm
    rcases hm with ⟨rfl, rfl⟩ | hm
    · exact h
    · exact All2.of_mem_zip hs a b hm

theorem zip_map_snd {α β γ : Type} (g : β → γ) :
    ∀ (l1 : List α) (l2 : List β), (l1.zip l2).map (fun p => (p.1, g p.2)) = l1.zip (l2.map g)
  | [], _ => rfl
  | _ :: _, [] => rfl
  | a :: l1, b :: l2 => by simp [zip_map_snd g l1 l2]

theorem All2.exists_right {α β : Type} {R : α → β → Prop} :
    ∀ {as : List α} {bs : List β}, All2 R as bs → ∀ a ∈ as, ∃ b, R a b
  | _, _, All2.nil, a, ha => by simp at ha
  | _, _, All2.cons h hs, a, ha => by
    rcases List.mem_cons.mp ha with rfl | ha
    · exact ⟨_, h⟩
    · exact All2.exists_right hs a ha

/-- A member's body, typed in the group's context under a grounding. -/
def BodyOK (θ : Nat → Ty) (env : Env) (b : BuiltT) : Prop :=
  ∃ tb eb, HasType (bindParams (Env.app θ env)
      ((b.f.params.map fun x => x.1).zip (b.ps.map (Ty.app θ)))) b.f.body tb eb ∧
    Flows tb (Ty.app θ b.r) ∧ eb.le b.e = true

/-- What the third loop records for a member: its scheme, and for a member without a complete
signature the zonked type it generalised. -/
def OutRel (s : CState) (b : BuiltT) (o : Nat × Ty × Option Ty) : Prop :=
  (b.sch = some (o.1, o.2.1) ∧ o.2.2 = none) ∨
    (b.sch = none ∧ ∃ zt, o.2.2 = some zt ∧ o.2.1.ok o.1 = true ∧
      (∀ m ∈ metasOf zt, s.Unbound m) ∧ ∀ B, zt.SkLt B → ∀ θ, Solves θ s →
        (∀ m ∈ metasOf zt, θ m = .skolem (B + m)) →
        Generalises (Ty.app θ b.t) ⟨o.1, o.2.1⟩)

def outEntry (b : BuiltT) (o : Nat × Ty × Option Ty) : String × Bind :=
  (b.f.name, Bind.fnPoly b.f.params.length o.1 o.2.1)

theorem eraseDupsLoop_nodup {α : Type} [BEq α] [LawfulBEq α] :
    ∀ (as bs : List α), bs.Nodup → (List.eraseDupsBy.loop (· == ·) as bs).Nodup
  | [], bs, h => by
    simp only [List.eraseDupsBy.loop]
    exact (List.reverse_perm bs).nodup_iff.mpr h
  | a :: as, bs, h => by
    simp only [List.eraseDupsBy.loop]
    split
    · exact eraseDupsLoop_nodup as bs h
    · rename_i hany
      refine eraseDupsLoop_nodup as (a :: bs) (List.nodup_cons.mpr ⟨?_, h⟩)
      intro ha
      have : (bs.any ((· == ·) a)) = true := List.any_eq_true.mpr ⟨a, ha, by simp⟩
      simp_all

theorem eraseDups_nodup {α : Type} [BEq α] [LawfulBEq α] (l : List α) : l.eraseDups.Nodup :=
  eraseDupsLoop_nodup l [] List.nodup_nil

theorem nodup_of_map {α β : Type} (f : α → β) {l : List α} (h : (l.map f).Nodup) : l.Nodup := by
  rw [List.Nodup, List.pairwise_map] at h
  exact h.imp fun {a b} hne e => hne (congrArg f e)

theorem nodup_flatMap_mem {α β : Type} (g : α → List β) :
    ∀ {L : List α}, (L.flatMap g).Nodup → ∀ x ∈ L, (g x).Nodup
  | [], _, x, hx => by simp at hx
  | a :: L, h, x, hx => by
    simp only [List.flatMap_cons] at h
    rcases List.mem_cons.mp hx with rfl | hx
    · exact (List.nodup_append.mp h).1
    · exact nodup_flatMap_mem g (List.nodup_append.mp h).2.1 x hx

def skolemId : Ty → Nat
  | .skolem k => k
  | _ => 0

theorem ids_map (sks : List (String × Ty)) (h : ∀ q ∈ sks, ∃ k, q.2 = .skolem k) :
    (sks.map fun q => skolemId q.2).map Ty.skolem = sks.map Prod.snd := by
  rw [List.map_map]
  apply List.map_congr_left
  intro q hq
  obtain ⟨k, hk⟩ := h q hq
  simp [hk, skolemId]

theorem skMap_keys : ∀ (sks : List (String × Ty)), (sks.map Prod.fst).Nodup →
    (sks.map Prod.fst).map (skMap sks) = sks.map Prod.snd
  | [], _ => rfl
  | (k, y) :: rest, h => by
    simp only [List.map_cons] at h ⊢
    obtain ⟨hk, hr⟩ := List.nodup_cons.mp h
    have hy : skMap ((k, y) :: rest) k = y := by simp [skMap, List.lookup]
    have hrest : (rest.map Prod.fst).map (skMap ((k, y) :: rest)) =
        (rest.map Prod.fst).map (skMap rest) := by
      apply List.map_congr_left
      intro v hv
      have hne : v ≠ k := fun e => hk (e ▸ hv)
      simp [skMap, List.lookup, beq_false_of_ne hne]
    rw [hy, hrest, skMap_keys rest hr]

theorem skMap_mem (sks : List (String × Ty)) (h : ∀ q ∈ sks, ∃ k, q.2 = .skolem k) :
    ∀ v ∈ sks.map Prod.fst, ∃ k, skMap sks v = .skolem k := by
  induction sks with
  | nil => simp
  | cons q rest ih =>
    intro v hv
    obtain ⟨k0, y⟩ := q
    by_cases hvk : v = k0
    · subst hvk
      obtain ⟨k, hk⟩ := h (v, y) (List.mem_cons_self ..)
      exact ⟨k, by simpa [skMap, List.lookup] using hk⟩
    · have hv' : v ∈ rest.map Prod.fst := by
        simp only [List.map_cons, List.mem_cons] at hv
        exact hv.resolve_left hvk
      obtain ⟨k, hk⟩ := ih (fun q hq => h q (List.mem_cons_of_mem _ hq)) v hv'
      exact ⟨k, by simpa [skMap, List.lookup, beq_false_of_ne hvk] using hk⟩

def FnInfo.toFnD (f : FnInfo) : FnD := ⟨f.name, f.params, f.sig, f.body⟩

theorem FnInfo.vars_eq (f : FnInfo) : f.toFnD.vars = f.writtenVars := by
  simp only [FnD.vars, FnInfo.writtenVars, FnInfo.toFnD, Ty.vars_eq_namedIn]
  rfl

theorem BuiltT.ok_t {s : CState} {b : BuiltT} {sks} (mem : MemInv s b sks)
    (hc : b.f.complete = true) : b.t.ok 0 = true := by
  have hsig : b.f.sig.isSome = true := by
    simp only [FnInfo.complete, Bool.and_eq_true] at hc; exact hc.1
  rw [mem.t_eq]
  exact Ty.ok_mkArr (Ty.okList_iff.mpr (mem.comp hc).2) (mem.rok hsig)

theorem member_wit {s : CState} {θ : Nat → Ty} {B : Nat} {E : Env} (hθ : Solves θ s)
    {b : BuiltT} {sks : List (String × Ty)} {o : Nat × Ty × Option Ty}
    (mem : MemInv s b sks) (hndk : (sks.map Prod.snd).Nodup) (rel : OutRel s b o)
    (hbody : BodyOK θ E b)
    (hz : ∀ zt, o.2.2 = some zt → zt.SkLt B ∧ ∀ m ∈ metasOf zt, θ m = .skolem (B + m)) :
    ∃ w : FnWit, w.f.toDecl = b.f.toDecl ∧
      w.entryIn = (b.f.name, Bind.app θ b.entry.2) ∧
      w.entryOut = (b.f.name, Bind.app θ (outEntry b o).2) ∧ Generalises w.τ w.s ∧
      (∀ v ∈ w.f.vars, ∃ n, w.σ v = .skolem n) ∧ w.f.vars.map w.σ = sks.map Prod.snd ∧
      MemberOk (Env.app θ E) w := by
  have hoksc : o.2.1.ok o.1 = true := by
    rcases rel with ⟨hb, _⟩ | ⟨_, zt, _, hok, _⟩
    · exact (mem.sch o.1 o.2.1 hb).2.1
    · exact hok
  have happ : Ty.app θ o.2.1 = o.2.1 := Ty.app_of_ok_gen θ _ hoksc
  have hvars : (FnInfo.toFnD b.f).vars = b.f.writtenVars := FnInfo.vars_eq b.f
  refine ⟨⟨b.f.toFnD, skMap sks, Ty.app θ b.t, ⟨o.1, o.2.1⟩⟩, rfl, ?_, ?_, ?_, ?_, ?_, ?_⟩
  · -- entryIn
    unfold FnWit.entryIn
    show (b.f.name, Binding.fn b.f.params.length (if b.f.complete = true then
        ⟨o.1, o.2.1⟩ else ⟨0, Ty.app θ b.t⟩)) = _
    rcases rel with ⟨hb, _⟩ | ⟨hb, _⟩
    · have hc : b.f.complete = true := by rw [← mem.sch_some, hb]; rfl
      simp only [hc, ite_true, BuiltT.entry, hb, Bind.app, happ]
    · have hc : b.f.complete = false := by rw [← mem.sch_some, hb]; rfl
      simp only [hc, Bool.false_eq_true, ite_false, BuiltT.entry, hb, Bind.app]
  · unfold FnWit.entryOut outEntry
    simp only [Bind.app, happ]
    rfl
  · -- Generalises
    rcases rel with ⟨hb, _⟩ | ⟨_, zt, hzt, _, hu, hgen⟩
    · obtain ⟨hn, hok, hnsk, hinst⟩ := mem.sch o.1 o.2.1 hb
      have hc : b.f.complete = true := by rw [← mem.sch_some, hb]; rfl
      have hbt : Ty.app θ b.t = b.t := Ty.app_of_ok_gen θ _ (b.ok_t mem hc)
      have hnd : ((sks.map fun q => skolemId q.2)).Nodup :=
        nodup_of_map Ty.skolem (by rw [ids_map sks mem.sk]; exact hndk)
      refine ⟨hok, hnsk, sks.map fun q => skolemId q.2, by simp [hn], hnd, ?_⟩
      show Ty.inst _ o.2.1 = Ty.app θ b.t
      rw [ids_map sks mem.sk, hbt]
      exact hinst
    · obtain ⟨hB, hm⟩ := hz zt hzt
      exact hgen B hB θ hθ hm
  · intro v hv
    rw [hvars, ← mem.keys] at hv
    exact skMap_mem sks mem.sk v hv
  · show (FnInfo.toFnD b.f).vars.map (skMap sks) = _
    rw [hvars, ← mem.keys]
    exact skMap_keys sks (by rw [mem.keys]; exact eraseDups_nodup _)
  · obtain ⟨tb, eb, h1, h2, h3⟩ := hbody
    refine ⟨b.ps.map (Ty.app θ), Ty.app θ b.r, b.e, tb, eb, mem.par θ hθ, ?_, ?_, h1, h2, h3⟩
    · have hr := mem.ret
      cases hsg : b.f.sig with
      | none =>
        rw [hsg] at hr
        obtain ⟨he, j, hj⟩ := hr
        have : (FnInfo.toFnD b.f).sig = none := hsg
        show RetTy (skMap sks) (FnInfo.toFnD b.f).sig _ _
        rw [this, he]
        refine RetTy.omitted _ ?_
        rw [hj]
        exact hθ.solved j
      | some p =>
        rw [hsg] at hr
        obtain ⟨hel, he, hno⟩ := hr
        have hok := mem.rok (by simp [hsg])
        have : (FnInfo.toFnD b.f).sig = some p := hsg
        show RetTy (skMap sks) (FnInfo.toFnD b.f).sig _ _
        rw [this, Ty.app_of_ok_gen θ _ hok]
        rw [he]
        refine RetTy.written p.1 b.r p.2 hel ?_
        exact hno
    · show Ty.app θ b.t = _
      rw [mem.t_eq, Ty.app_mkArr]

theorem fnWitnesses {s : CState} {θ : Nat → Ty} {B : Nat} {E : Env} (hθ : Solves θ s) :
    ∀ (bs : List BuiltT) (sksL : List (List (String × Ty))) (outS : List (Nat × Ty × Option Ty)),
    All2 (MemInv s) bs sksL → All2 (OutRel s) bs outS →
    (sksL.flatMap fun l => l.map Prod.snd).Nodup → (∀ b ∈ bs, BodyOK θ E b) →
    (∀ o ∈ outS, ∀ zt, o.2.2 = some zt → zt.SkLt B ∧ ∀ m ∈ metasOf zt, θ m = .skolem (B + m)) →
    ∃ ws : List FnWit, ws.map (fun w => w.f.toDecl) = bs.map (fun b => b.f.toDecl) ∧
      ws.map FnWit.entryIn = Env.app θ (bs.map BuiltT.entry) ∧
      ws.map FnWit.entryOut = Env.app θ ((bs.zip outS).map fun p => outEntry p.1 p.2) ∧
      (∀ w ∈ ws, Generalises w.τ w.s) ∧ (∀ w ∈ ws, ∀ v ∈ w.f.vars, ∃ n, w.σ v = .skolem n) ∧
      (ws.flatMap fun w => w.f.vars.map w.σ) = sksL.flatMap (fun l => l.map Prod.snd) ∧
      (∀ w ∈ ws, MemberOk (Env.app θ E) w) := by
  intro bs
  induction bs with
  | nil =>
    intro sksL outS h1 h2 _ _ _
    cases h1; cases h2
    exact ⟨[], rfl, rfl, rfl, by simp, by simp, rfl, by simp⟩
  | cons b rest ih =>
    intro sksL outS h1 h2 hnd hbody hz
    cases h1 with
    | cons mem hrest1 =>
      cases h2 with
      | cons rel hrest2 =>
        rename_i sks sksL' o outS'
        simp only [List.flatMap_cons] at hnd
        obtain ⟨hndk, hndr, _⟩ := List.nodup_append.mp hnd
        obtain ⟨ws, e1, e2, e3, g1, g2, g3, g4⟩ := ih sksL' outS' hrest1 hrest2 hndr
          (fun b' hb' => hbody b' (List.mem_cons_of_mem _ hb'))
          (fun o' ho' => hz o' (List.mem_cons_of_mem _ ho'))
        obtain ⟨w, w1, w2, w3, w4, w5, w6, w7⟩ := member_wit (B := B) hθ mem hndk rel
          (hbody b (List.mem_cons_self ..)) (hz o (List.mem_cons_self ..))
        refine ⟨w :: ws, ?_, ?_, ?_, ?_, ?_, ?_, ?_⟩
        · simp [e1, w1]
        · simp only [List.map_cons, e2, w2, List.map_cons]
          simp [Env.app, BuiltT.entry, BuiltT.f]
        · simp only [List.zip_cons_cons, List.map_cons, e3, w3]
          simp [Env.app, outEntry, BuiltT.f]
        · intro w' hw'
          rcases List.mem_cons.mp hw' with rfl | hw'
          · exact w4
          · exact g1 w' hw'
        · intro w' hw'
          rcases List.mem_cons.mp hw' with rfl | hw'
          · exact w5
          · exact g2 w' hw'
        · simp only [List.flatMap_cons, g3, w6]
        · intro w' hw'
          rcases List.mem_cons.mp hw' with rfl | hw'
          · exact w7
          · exact g4 w' hw'

theorem Env.app_append (θ : Nat → Ty) (a b : Env) :
    Env.app θ (a ++ b) = Env.app θ a ++ Env.app θ b := List.map_append

theorem Env.app_reverse (θ : Nat → Ty) (a : Env) :
    Env.app θ a.reverse = (Env.app θ a).reverse := List.map_reverse

theorem group_assemble {s : CState} {base : Env} {bs : List BuiltT}
    {sksL : List (List (String × Ty))} {outS : List (Nat × Ty × Option Ty)} {genv : Env}
    (hne : bs ≠ [])
    (hall : All2 (MemInv s) bs sksL)
    (hnd : (sksL.flatMap (fun l => l.map Prod.snd)).Nodup)
    (hout : All2 (OutRel s) bs outS) (hgenv : genv = (bs.map BuiltT.entry).reverse)
    (hbody : ∀ b ∈ bs, ∀ θ, Solves θ s → BodyOK θ (genv ++ base) b) :
    ∀ B, (∀ zt ∈ outS.filterMap (fun o => o.2.2), zt.SkLt B) → ∀ θ, Solves θ s →
      (∀ zt ∈ outS.filterMap (fun o => o.2.2), ∀ m ∈ metasOf zt, θ m = .skolem (B + m)) →
      GroupTy (Env.app θ base) ((bs.map BuiltT.f).reverse.map FnInfo.toDecl)
        (Env.app θ (((bs.zip outS).map fun p => outEntry p.1 p.2).reverse ++ base)) := by
  intro B hB θ hθ hm
  obtain ⟨ws, e1, e2, e3, g1, g2, g3, g4⟩ := fnWitnesses (B := B) (E := genv ++ base) hθ bs sksL outS
    hall hout hnd (fun b hb => hbody b hb θ hθ)
    (fun o ho zt hz => ⟨hB zt (List.mem_filterMap.mpr ⟨o, ho, hz⟩),
      hm zt (List.mem_filterMap.mpr ⟨o, ho, hz⟩)⟩)
  have hlen : ws.length = bs.length := by
    have := congrArg List.length e1
    simpa using this
  have hwsne : ws.reverse ≠ [] := by
    intro h0
    have : ws = [] := List.reverse_eq_nil_iff.mp h0
    rw [this] at hlen
    exact hne (List.length_eq_zero_iff.mp hlen.symm)
  have hΔ : ws.reverse.map FnWit.entryIn ++ Env.app θ base = Env.app θ (genv ++ base) := by
    rw [Env.app_append, hgenv, Env.app_reverse, ← e2, ← List.map_reverse]
  have hmem : ∀ w ∈ ws.reverse, MemberOk (ws.reverse.map FnWit.entryIn ++ Env.app θ base) w := by
    intro w hw
    rw [hΔ]
    exact g4 w (List.mem_reverse.mp hw)
  have hnd' : (ws.reverse.flatMap fun w => w.f.vars.map w.σ).Nodup := by
    refine ((List.Perm.flatMap_right _ (List.reverse_perm ws)).nodup_iff).mpr ?_
    rw [g3]; exact hnd
  have key := GroupTy.fnG (Env.app θ base) ws.reverse hwsne
    (fun w hw => g2 w (List.mem_reverse.mp hw)) hnd'
    (fun w hw => g1 w (List.mem_reverse.mp hw)) hmem
  have d1 : ws.reverse.map (fun w => w.f.toDecl) = (bs.map BuiltT.f).reverse.map FnInfo.toDecl := by
    rw [List.map_reverse, e1, List.map_reverse, List.map_map]
    rfl
  have d2 : Env.app θ (((bs.zip outS).map fun p => outEntry p.1 p.2).reverse ++ base) =
      ws.reverse.map FnWit.entryOut ++ Env.app θ base := by
    rw [Env.app_append, Env.app_reverse, ← e3, List.map_reverse]
  rw [d1] at key
  rw [d2]
  exact key

theorem inferFnGroup_sound {Q : Env → CState → Prop} {s : CState} {base : Env}
    {fns : List FnInfo} (hs : s.WF) (hnf : s.NF) (hbase : Env.Closed base) (hne : fns ≠ [])
    (hsrc : ∀ f ∈ fns, f.body.Src) (hfns : ∀ f ∈ fns, f.Surf)
    (h : ∀ env' s', Ext s s' → s'.WF → Env.Closed env' →
      (∃ zts : List Ty, (∀ zt ∈ zts, ∀ m ∈ metasOf zt, s'.Unbound m) ∧
        ∀ B, (∀ zt ∈ zts, zt.SkLt B) → ∀ θ, Solves θ s' →
          (∀ zt ∈ zts, ∀ m ∈ metasOf zt, θ m = .skolem (B + m)) →
          GroupTy (Env.app θ base) (fns.reverse.map FnInfo.toDecl) (Env.app θ env')) →
        Q env' s') :
    Wp (inferFnGroup {} base fns) Q s := by
  unfold inferFnGroup
  dsimp only
  refine Wp.bind (Wp.forIn_prefix (GInv1 s) fns ([], []) (GInv1.init hs) ?_ ?_)
  · intro pre f post st s1 hpre ⟨e1, h1, hpf, ⟨sksL, hall, hnd, hrng⟩, hnk, hst, hwf, hnfe⟩
    have hfs : f.Surf := hfns f (by rw [← hpre]; simp)
    refine Wp.bind (skolemLoop_sound h1 ?_)
    intro sks s2 e2 h2 hsub hkeys hrng2 hnd2 hle2
    have hsks : ∀ q ∈ sks, ∃ k, q.2 = .skolem k := fun q hq => by
      obtain ⟨k, hk, _⟩ := hrng2 q hq; exact ⟨k, hk⟩
    obtain ⟨hA, hW, hN, hO⟩ := skMap_props hsks s2.sz 0
    refine Wp.bind (build_sound (m := skMap sks) (g := 0) h2 hA hW hN hO hfs.1 hfs.2.1 ?_)
    intro ps r e t s3 e3 h3 hnk3 hps hr ht hlen hP1 hRB hno hcomp
    have fin : ∀ sch : Option (Nat × Ty), sch.isSome = f.complete →
        (∀ n sc, sch = some (n, sc) → n = sks.length ∧ sc.ok n = true ∧ sc.noSk = true ∧
          Ty.inst (sks.map Prod.snd) sc = t) →
        GInv1 s (pre ++ [f]) (BuiltT.entry (f, ps, r, e, t, sch) :: st.1,
          st.2 ++ [((f, ps, r, e, t, sch) : BuiltT)]) s3 := by
      intro sch hsome hsch
      have e23 : Ext s2 s3 := e3
      have hmem : MemInv s3 (f, ps, r, e, t, sch) sks :=
        { keys := hkeys, sk := hsks, ps_wf := hps, r_wf := hr, len := hlen, t_eq := ht,
          par := hP1, ret := hRB
          rok := fun hsome => by
            cases hsg : f.sig with
            | none =>
              have hsome' : f.sig.isSome = true := hsome
              rw [hsg] at hsome'; simp at hsome'
            | some p =>
              have := hRB
              rw [hsg] at this
              unfold RetB at this
              exact Ty.elab_ok hO p.1 r this.1
          comp := fun hc => ⟨(hcomp hc).2.1, (hcomp hc).2.2.1⟩
          sch_some := hsome, sch := hsch }
      refine ⟨e1.trans (e2.trans e3), h3, by simp [List.map_append, hpf, BuiltT.f], ⟨sksL ++ [sks], ?_, ?_, ?_⟩, ?_, ?_, ?_, ?_⟩
      · exact All2.snoc (All2.imp (fun b l hb => hb.mono (e2.trans e3)) hall) hmem
      · simp only [List.flatMap_append, List.flatMap_cons, List.flatMap_nil, List.append_nil]
        refine List.nodup_append.mpr ⟨hnd, hnd2, ?_⟩
        intro x hx y hy hxy
        obtain ⟨l, hl, hxl⟩ := List.mem_flatMap.mp hx
        obtain ⟨q, hq, rfl⟩ := List.mem_map.mp hxl
        obtain ⟨q', hq', rfl⟩ := List.mem_map.mp hy
        obtain ⟨k, hk, hk1⟩ := hrng l hl q hq
        obtain ⟨k', hk', hk2, _⟩ := hrng2 q' hq'
        rw [hk, hk'] at hxy
        have := Ty.skolem_inj hxy
        omega
      · intro l hl q hq
        rcases List.mem_append.mp hl with hl | hl
        · obtain ⟨k, hk, hk1⟩ := hrng l hl q hq
          exact ⟨k, hk, by omega⟩
        · have : l = sks := by simpa using hl
          subst this
          obtain ⟨k, hk, _, hk2⟩ := hrng2 q hq
          exact ⟨k, hk, by omega⟩
      · omega
      · simp [hst, List.map_append, List.reverse_append]
      · refine Env.WF.cons (hwf.mono (Nat.le_trans e2.sz e3.sz)) ?_
        cases sch with
        | none =>
          show Ty.WF s3.sz 0 t
          rw [ht, Ty.WF_mkArr]
          exact Ty.WF_arr.mpr ⟨fun x hx => (hps x hx).1, hr.1⟩
        | some p =>
          obtain ⟨n, sc⟩ := p
          show Ty.WF s3.sz n sc
          exact Ty.wf_of_okn sc (hsch n sc rfl).2.1 _
      · refine Env.NF.cons hnfe ?_
        cases sch with
        | none =>
          show Ty.NF t
          rw [ht, Ty.NF_mkArr]
          exact ⟨fun x hx => (hps x hx).2, hr.2⟩
        | some p =>
          obtain ⟨n, sc⟩ := p
          show Ty.NF sc
          exact Ty.nf_of_okn sc (hsch n sc rfl).2.1
    split
    · rename_i hc
      refine Wp.bind (Wp.map' _ (scheme_sound h3 hfs.1 hfs.2.1 hc ?_))
      intro n sc hn hok hnsk hrel
      refine Wp.pure' ⟨_, rfl, fin (some (n, sc)) (by simp [hc]) ?_⟩
      intro n' sc' h'
      cases h'
      have hlen' : sks.length = f.writtenVars.length := by rw [← hkeys]; simp
      refine ⟨hn.trans hlen'.symm, hok, hnsk, ?_⟩
      rw [ht]
      exact hrel (skMap sks) (sks.map Prod.snd) (inst_gmap f sks hkeys) ps r e (hcomp hc).2.1 hRB
    · rename_i hc
      refine Wp.bind (Wp.pure' (a := (none : Option (Nat × Ty))) ?_)
      refine Wp.pure' ⟨_, rfl, fin none (by simpa using hc) (by intro n sc h; cases h)⟩
  · intro st s1 ⟨e1, h1, hpf, ⟨sksL, hall, hnd, hrng⟩, hnk, hst, hwf, hnfe⟩
    refine Wp.bind (Wp.forIn_prefix
      (fun (pre2 : List BuiltT) (_ : PUnit) s2 => Ext s1 s2 ∧ s2.WF ∧
        ∀ b ∈ pre2, ∀ θ, Solves θ s2 → BodyOK θ (st.1 ++ base) b) st.2 PUnit.unit
      ⟨Ext.refl _, h1, by simp⟩ ?_ ?_)
    · intro pre2 b post u s2 hpre2 ⟨e2, h2, hdone⟩
      have hbm : b ∈ st.2 := by rw [← hpre2]; simp
      obtain ⟨sks, hmem⟩ := All2.exists_right hall b hbm
      have hmem2 := hmem.mono e2
      have hfs : b.f.Surf := hfns b.f (by rw [← hpf]; exact List.mem_map_of_mem hbm)
      have hnf2 : s2.NF := e2.nf (e1.nf hnf)
      refine Wp.bind (checkBody_sound h2 hnf2 ?_ ?_ ?_ ?_ hmem2.r_wf.1 hmem2.r_wf.2
        (hsrc b.f (by rw [← hpf]; exact List.mem_map_of_mem hbm)) hfs.2.2 ?_)
      · exact (hwf.mono e2.sz).append (hbase.wf _)
      · intro p hp
        rcases List.mem_append.mp hp with hp | hp
        · exact hnfe p hp
        · exact hbase.nf p hp
      · exact zip_snd_wf (sz0 := s2.sz) (fun x hx => (hmem2.ps_wf x hx).1) (Nat.le_refl _)
      · intro p hp
        exact (hmem2.ps_wf _ (List.of_mem_zip hp).2).2
      · intro s' e' w' hty
        refine Wp.pure' ⟨_, rfl, e2.trans e', w', ?_⟩
        intro b' hb' θ hθ
        rcases List.mem_append.mp hb' with hb' | hb'
        · exact hdone b' hb' θ (hθ.back e')
        · have : b' = b := by simpa using hb'
          subst this
          obtain ⟨tb, eb, h1', h2', h3'⟩ := hty θ hθ
          refine ⟨tb, eb, ?_, h2', h3'⟩
          rw [zip_map_snd] at h1'
          exact h1'
    · intro u s2 ⟨e2, h2, hdone⟩
      have hnf2 : s2.NF := e2.nf (e1.nf hnf)
      refine Wp.bind (Wp.forIn_prefix
        (fun (pre3 : List BuiltT) (out : Env) s3 => s3 = s2 ∧ ∃ outS : List (Nat × Ty × Option Ty),
          All2 (OutRel s2) pre3 outS ∧
            out = ((pre3.zip outS).map fun p => outEntry p.1 p.2).reverse) st.2 []
        ⟨rfl, [], All2.nil, rfl⟩ ?_ ?_)
      · intro pre3 b post out s3 hpre3 ⟨hs3, outS, hallO, hout⟩
        subst hs3
        have hbm : b ∈ st.2 := by rw [← hpre3]; simp
        obtain ⟨sks, hmem⟩ := All2.exists_right hall b hbm
        have hmem2 := hmem.mono e2
        have hlen : pre3.length = outS.length := hallO.length_eq
        cases hsch : b.sch with
        | some p =>
          obtain ⟨n, sc⟩ := p
          have hb : b.2.2.2.2.2 = some (n, sc) := hsch
          simp only [hb]
          refine Wp.pure' ⟨_, rfl, rfl, outS ++ [(n, sc, none)], ?_, ?_⟩
          · exact All2.snoc hallO (Or.inl ⟨hsch, rfl⟩)
          · rw [List.zip_append hlen]
            simp [hout, outEntry, BuiltT.f]
        | none =>
          have hb : b.2.2.2.2.2 = none := hsch
          simp only [hb]
          have hteq := hmem2.t_eq
          refine Wp.bind (generalize_sound (t := b.2.2.2.2.1) h2 hnf2 ?_ ?_ ?_)
          · show Ty.WF s3.sz 0 b.t
            rw [hteq, Ty.WF_mkArr]
            exact Ty.WF_arr.mpr ⟨fun x hx => (hmem2.ps_wf x hx).1, hmem2.r_wf.1⟩
          · show Ty.NF b.t
            rw [hteq, Ty.NF_mkArr]
            exact ⟨fun x hx => (hmem2.ps_wf x hx).2, hmem2.r_wf.2⟩
          · intro n t' zt hok hu hgen
            refine Wp.pure' ⟨_, rfl, rfl, outS ++ [(n, t', some zt)], ?_, ?_⟩
            · exact All2.snoc hallO (Or.inr ⟨hsch, zt, rfl, hok, hu, fun B hB θ hθ hm => hgen B hB θ hθ hm⟩)
            · rw [List.zip_append hlen]
              simp [hout, outEntry, BuiltT.f]
      · intro out s3 ⟨hs3, outS, hallO, hout⟩
        subst hs3
        refine Wp.pure' (h _ _ (e1.trans e2) h2 ?_ ?_)
        · rw [hout]
          refine Env.Closed.append ?_ hbase
          intro p hp
          obtain ⟨q, hq, rfl⟩ := List.mem_map.mp (List.mem_reverse.mp hp)
          obtain ⟨b, o⟩ := q
          have hrel := hallO.of_mem_zip b o hq
          have hbm : b ∈ st.2 := (List.of_mem_zip hq).1
          obtain ⟨sks, hmem⟩ := All2.exists_right hall b hbm
          show (o.2.1.ok o.1 = true)
          rcases hrel with ⟨hb, _⟩ | ⟨_, zt, _, hok, _⟩
          · exact ((hmem.sch o.1 o.2.1 hb).2.1)
          · exact hok
        · refine ⟨outS.filterMap (fun o => o.2.2), ?_, ?_⟩
          · intro zt hzt m hm
            obtain ⟨o, ho, hoz⟩ := List.mem_filterMap.mp hzt
            obtain ⟨b, hb⟩ := hallO.exists_left o ho
            rcases hb with ⟨_, hz⟩ | ⟨_, zt', hz, _, hu, _⟩
            · rw [hz] at hoz; cases hoz
            · rw [hz] at hoz
              have := Option.some.inj hoz
              subst this
              exact hu m hm
          · have := group_assemble (by intro h0; rw [h0] at hpf; exact hne hpf.symm)
              (All2.imp (fun b l hb => hb.mono e2) hall) hnd
              hallO hst (fun b hb θ hθ => hdone b hb θ hθ)
            rw [hpf] at this
            rw [← hout] at this
            exact this

end SproutCore
