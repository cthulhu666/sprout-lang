import SproutCore.Ground

/-!
# Written types and `inferLet` (docs/lean-model-v0.md §9, 1b step 3b, part 3)

* Facts about the judgment's `Ty.elab`, a written type read through a map `σ`: it keeps
  named-freeness and range, and commutes with grounding (the refusal of an `!{IO}` arrow with an
  arrow result is on the written type, so grounding the map cannot trip it).
* `inferLet_sound`: a successful `inferLet` from a well-formed, named-free state and a closed
  environment types the declaration by `GroupTy.letG`, for every grounding that is fresh above a
  `B` the caller picks after the run. The annotation's written variables are fresh metavariables
  (a top-level `let` generalises them, spec §5.2); the grounding supplies the `σ` of `LetAnn`.
-/

namespace SproutCore

open Judgment

set_option linter.unusedVariables false

/-! ### `Ty.elab` -/

mutual
theorem Ty.elab_nf {σ : String → Ty} (hσ : ∀ s, (σ s).NF) :
    ∀ (t r : Ty), t.elab σ = some r → r.NF
  | .named s, r, h => by
    simp only [Ty.elab, Option.some.injEq] at h
    subst h; exact hσ s
  | .int, r, h => by simp only [Ty.elab, Option.some.injEq] at h; subst h; exact Ty.NF_int
  | .bool, r, h => by simp only [Ty.elab, Option.some.injEq] at h; subst h; exact Ty.NF_bool
  | .str, r, h => by simp only [Ty.elab, Option.some.injEq] at h; subst h; exact Ty.NF_str
  | .unit, r, h => by simp only [Ty.elab, Option.some.injEq] at h; subst h; exact Ty.NF_unit
  | .mv _, r, h => by simp [Ty.elab] at h
  | .skolem _, r, h => by simp [Ty.elab] at h
  | .gen _, r, h => by simp [Ty.elab] at h
  | .tup ts, r, h => by
    simp only [Ty.elab, Option.map_eq_some_iff] at h
    obtain ⟨rs, hrs, rfl⟩ := h
    exact Ty.NF_tup.mpr (Ty.elabList_nf hσ ts rs hrs)
  | .arr ps r0 e, r, h => by
    simp only [Ty.elab] at h
    split at h
    · rename_i ps' r' hps hr'
      split at h
      · cases h
      · simp only [Option.some.injEq] at h
        subst h
        exact Ty.NF_mkArr.mpr ⟨Ty.elabList_nf hσ ps ps' hps, Ty.elab_nf hσ r0 r' hr'⟩
    · cases h
  | .adt _ ts, r, h => by
    simp only [Ty.elab, Option.map_eq_some_iff] at h
    obtain ⟨rs, hrs, rfl⟩ := h
    exact Ty.NF_adt.mpr (Ty.elabList_nf hσ ts rs hrs)

theorem Ty.elabList_nf {σ : String → Ty} (hσ : ∀ s, (σ s).NF) :
    ∀ (ts rs : List Ty), Ty.elabList σ ts = some rs → ∀ r ∈ rs, r.NF
  | [], rs, h => by simp only [Ty.elabList, Option.some.injEq] at h; subst h; simp
  | t :: ts, rs, h => by
    simp only [Ty.elabList] at h
    split at h
    · rename_i t' ts' ht hts
      simp only [Option.some.injEq] at h
      subst h
      intro r hr
      rcases List.mem_cons.mp hr with e | hr
      · rw [e]; exact Ty.elab_nf hσ t t' ht
      · exact Ty.elabList_nf hσ ts ts' hts r hr
    · cases h
end

mutual
theorem Ty.elab_wf {σ : String → Ty} {sz g : Nat} (hσ : ∀ s, Ty.WF sz g (σ s)) :
    ∀ (t r : Ty), t.elab σ = some r → Ty.WF sz g r
  | .named s, r, h => by
    simp only [Ty.elab, Option.some.injEq] at h
    subst h; exact hσ s
  | .int, r, h => by simp only [Ty.elab, Option.some.injEq] at h; subst h; exact Ty.WF_int
  | .bool, r, h => by simp only [Ty.elab, Option.some.injEq] at h; subst h; exact Ty.WF_bool
  | .str, r, h => by simp only [Ty.elab, Option.some.injEq] at h; subst h; exact Ty.WF_str
  | .unit, r, h => by simp only [Ty.elab, Option.some.injEq] at h; subst h; exact Ty.WF_unit
  | .mv _, r, h => by simp [Ty.elab] at h
  | .skolem _, r, h => by simp [Ty.elab] at h
  | .gen _, r, h => by simp [Ty.elab] at h
  | .tup ts, r, h => by
    simp only [Ty.elab, Option.map_eq_some_iff] at h
    obtain ⟨rs, hrs, rfl⟩ := h
    exact Ty.WF_tup.mpr (Ty.elabList_wf hσ ts rs hrs)
  | .arr ps r0 e, r, h => by
    simp only [Ty.elab] at h
    split at h
    · rename_i ps' r' hps hr'
      split at h
      · cases h
      · simp only [Option.some.injEq] at h
        subst h
        rw [Ty.WF_mkArr]
        exact Ty.WF_arr.mpr ⟨Ty.elabList_wf hσ ps ps' hps, Ty.elab_wf hσ r0 r' hr'⟩
    · cases h
  | .adt _ ts, r, h => by
    simp only [Ty.elab, Option.map_eq_some_iff] at h
    obtain ⟨rs, hrs, rfl⟩ := h
    exact Ty.WF_adt.mpr (Ty.elabList_wf hσ ts rs hrs)

theorem Ty.elabList_wf {σ : String → Ty} {sz g : Nat} (hσ : ∀ s, Ty.WF sz g (σ s)) :
    ∀ (ts rs : List Ty), Ty.elabList σ ts = some rs → ∀ r ∈ rs, Ty.WF sz g r
  | [], rs, h => by simp only [Ty.elabList, Option.some.injEq] at h; subst h; simp
  | t :: ts, rs, h => by
    simp only [Ty.elabList] at h
    split at h
    · rename_i t' ts' ht hts
      simp only [Option.some.injEq] at h
      subst h
      intro r hr
      rcases List.mem_cons.mp hr with e | hr
      · rw [e]; exact Ty.elab_wf hσ t t' ht
      · exact Ty.elabList_wf hσ ts ts' hts r hr
    · cases h
end

mutual
/-- Reading a written type commutes with grounding the map. -/
theorem Ty.elab_app (θ : Nat → Ty) {σ : String → Ty} :
    ∀ (t r : Ty), t.elab σ = some r → t.elab (fun v => Ty.app θ (σ v)) = some (Ty.app θ r)
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
  | .tup ts, r, h => by
    simp only [Ty.elab, Option.map_eq_some_iff] at h
    obtain ⟨rs, hrs, rfl⟩ := h
    have := Ty.elabList_app θ ts rs hrs
    simp only [Ty.elab, this, Option.map_some, Ty.app_tup]
  | .arr ps r0 e, r, h => by
    simp only [Ty.elab] at h
    split at h
    · rename_i ps' r' hps hr'
      split at h
      · cases h
      · rename_i hnot
        simp only [Option.some.injEq] at h
        subst h
        have h1 := Ty.elabList_app θ ps ps' hps
        have h2 := Ty.elab_app θ r0 r' hr'
        have hnot' : (r0.isArr && e == Eff.io) = false := by simpa using hnot
        simp only [Ty.elab, h1, h2, hnot']
        rw [Ty.app_mkArr]
        simp
    · cases h
  | .adt n ts, r, h => by
    simp only [Ty.elab, Option.map_eq_some_iff] at h
    obtain ⟨rs, hrs, rfl⟩ := h
    have := Ty.elabList_app θ ts rs hrs
    simp only [Ty.elab, this, Option.map_some, Ty.app_adt]

theorem Ty.elabList_app (θ : Nat → Ty) {σ : String → Ty} :
    ∀ (ts rs : List Ty), Ty.elabList σ ts = some rs →
      Ty.elabList (fun v => Ty.app θ (σ v)) ts = some (rs.map (Ty.app θ))
  | [], rs, h => by simp only [Ty.elabList, Option.some.injEq] at h; subst h; rfl
  | t :: ts, rs, h => by
    simp only [Ty.elabList] at h
    split at h
    · rename_i t' ts' ht hts
      simp only [Option.some.injEq] at h
      subst h
      simp only [Ty.elabList, Ty.elab_app θ t t' ht, Ty.elabList_app θ ts ts' hts, List.map_cons]
    · cases h
end


/-! ### The fresh variables of an annotation -/

theorem fresh_pairs_sound {vs : List String} {Q : List (String × Ty) → CState → Prop}
    {s : CState} (hs : s.WF)
    (h : ∀ sks s', Ext s s' → s'.WF → (∀ q ∈ sks, ∃ j, q.2 = .mv j ∧ j < s'.sz) → Q sks s') :
    Wp (forIn vs ([] : List (String × Ty)) fun v sks => do
      let d ← fresh
      pure (ForInStep.yield (sks ++ [(v, d)]))) Q s := by
  refine Wp.forIn_list (fun (sks : List (String × Ty)) s1 =>
    Ext s s1 ∧ s1.WF ∧ ∀ q ∈ sks, ∃ j, q.2 = .mv j ∧ j < s1.sz) vs [] ⟨Ext.refl _, hs, by simp⟩ ?_ ?_
  · intro a ha sks s1 ⟨e1, h1, hp⟩
    refine Wp.bind (fresh_leaf h1 ?_)
    intro d s2 hd ⟨j, hj⟩ hsz e2 h2 _
    refine Wp.pure' ⟨e1.trans e2, h2, ?_⟩
    intro q hq
    rcases List.mem_append.mp hq with hq | hq
    · obtain ⟨j', hj', hlt⟩ := hp q hq
      exact ⟨j', hj', by have := e2.sz; omega⟩
    · have := List.mem_singleton.mp hq
      subst this
      have hd' := Ty.WF_mv.mp (hj ▸ hd)
      exact ⟨j, hj, hd'⟩
  · intro sks s1 ⟨e1, h1, hp⟩
    exact h sks s1 e1 h1 hp

theorem lookupD_mv {sz : Nat} {sks : List (String × Ty)}
    (h : ∀ q ∈ sks, ∃ j, q.2 = .mv j ∧ j < sz) (x : String) :
    ((sks.lookup x).getD .unit = .unit) ∨ ∃ j, (sks.lookup x).getD .unit = .mv j ∧ j < sz := by
  cases hl : sks.lookup x with
  | none => exact Or.inl rfl
  | some b =>
    obtain ⟨l1, l2, hsks, _⟩ := List.lookup_eq_some_iff.mp hl
    obtain ⟨j, hj, hlt⟩ := h (x, b) (by rw [hsks]; simp)
    exact Or.inr ⟨j, hj, hlt⟩

/-- `elabTy` with a map that gives metavariables or `unit`: what the judgment's `Ty.elab` reads, in
range and named-free. -/
theorem elabTy_map_leaf {Q : Ty → CState → Prop} {s : CState} {a : Ty} {f : String → Ty}
    (hf : ∀ x, (f x = .unit) ∨ ∃ j, f x = .mv j ∧ j < s.sz) (hsf : Ty.surf a = true)
    (h : ∀ r, a.elab f = some r → Ty.WF s.sz 0 r → r.NF → Q r s) : Wp (elabTy f a) Q s := by
  refine elabTy_elab f a Q s hsf fun r s' e he => ?_
  subst e
  refine h r he (Ty.elab_wf (fun x => ?_) a r he) (Ty.elab_nf (fun x => ?_) a r he)
  · rcases hf x with e | ⟨j, e, hj⟩
    · simp [e]
    · simp [e, hj]
  · rcases hf x with e | ⟨j, e, hj⟩ <;> simp [e]


theorem inferLet_sound {Q : Env → CState → Prop} {s : CState} {base : Env} {name : String}
    {ann : Option Ty} {init : Expr} (hs : s.WF) (hnf : s.NF) (hbase : Env.Closed base)
    (hann : ∀ a, ann = some a → Ty.surf a = true) (hsurf : init.Surf)
    (h : ∀ env' s', Ext s s' → s'.WF → Env.Closed env' →
      (∃ zt, (∀ m ∈ metasOf zt, s'.Unbound m) ∧ ∀ B, zt.SkLt B → ∀ θ, Solves θ s' →
        (∀ m ∈ metasOf zt, θ m = .skolem (B + m)) →
        GroupTy (Env.app θ base) [.letD name ann init] (Env.app θ env')) → Q env' s') :
    Wp (inferLet {} base name ann init) Q s := by
  unfold inferLet
  dsimp only
  refine Wp.bind (infer_sound {} rfl init base _ s hs hnf (hbase.wf _) hbase.nf
    (Expr.src_of_surf _ hsurf) hsurf ?_)
  intro t ε s1 e1 w1 wt nt ht
  dsimp only
  split
  · rename_i hε
    have hε' : ε = .pure := Eff.beq_pure.mp hε
    subst hε'
    have hnf1 : s1.NF := e1.nf hnf
    have fin : ∀ (n : Nat) (t' : Ty) (s' : CState), Ext s1 s' → s'.WF → t'.ok n = true →
        (∃ zt, (∀ m ∈ metasOf zt, s'.Unbound m) ∧ ∀ B, zt.SkLt B → ∀ θ, Solves θ s' →
          (∀ m ∈ metasOf zt, θ m = .skolem (B + m)) →
          (∃ t0, HasType (Env.app θ base) init t0 .pure ∧ LetAnn ann t0 ∧
            Generalises t0 ⟨n, t'⟩)) →
        Q ((name, Bind.poly n t') :: base) s' := by
      intro n t' s' ex hw hok ⟨zt, hu, hz⟩
      refine h _ s' (e1.trans ex) hw (hbase.cons hok) ⟨zt, hu, ?_⟩
      intro B hB θ hθ hm
      obtain ⟨t0, h1, h2, h3⟩ := hz B hB θ hθ hm
      have : Env.app θ ((name, Bind.poly n t') :: base) = (name, Binding.val ⟨n, t'⟩) :: Env.app θ base := by
        simp only [Env.app_cons, Bind.app, Ty.app_of_ok_gen θ t' hok]
      rw [this]
      exact GroupTy.letG _ name ann init t0 ⟨n, t'⟩ h1 h2 h3
    cases ann with
    | none =>
      refine Wp.bind (generalize_sound w1 hnf1 wt nt ?_)
      intro n t' zt hok hu hgen
      refine Wp.pure' (fin n t' s1 (Ext.refl _) w1 hok ⟨zt, hu, fun B hB θ hθ hm => ?_⟩)
      exact ⟨Ty.app θ t, ht θ hθ, trivial, hgen B hB θ hθ hm⟩
    | some a0 =>
      have hsf := hann a0 rfl
      refine Wp.bind (fresh_pairs_sound w1 ?_)
      intro sks s2 e2 w2 hsks
      have hf := lookupD_mv hsks
      refine Wp.bind (elabTy_map_leaf (f := fun s => (sks.lookup s).getD .unit) hf hsf ?_)
      intro a' hel hwa hna
      refine Wp.bind (flow_sound w2 (e2.nf hnf1) (wt.mono e2.sz (Nat.le_refl _)) nt hwa hna ?_)
      intro s3 e3 w3 hfl
      refine Wp.bind (generalize_sound w3 (e3.nf (e2.nf hnf1)) (wt.mono (by have := e2.sz; have := e3.sz; omega) (Nat.le_refl _)) nt ?_)
      intro n t' zt hok hu hgen
      refine Wp.pure' (fin n t' s3 (e2.trans e3) w3 hok ⟨zt, hu, fun B hB θ hθ hm => ?_⟩)
      refine ⟨Ty.app θ t, ht θ (hθ.back ((e2.trans e3))), ?_, hgen B hB θ hθ hm⟩
      refine ⟨fun v => Ty.app θ ((sks.lookup v).getD .unit), Ty.app θ a', ?_, Ty.elab_app θ a0 a' hel, hfl θ hθ⟩
      intro v
      rcases hf v with e | ⟨j, e, _⟩
      · simp only [e, Ty.app]; rfl
      · simp only [e, Ty.app]; exact hθ.solved j
  · exact Wp.reject' _ _

end SproutCore
