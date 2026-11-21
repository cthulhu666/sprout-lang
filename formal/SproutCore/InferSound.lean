import SproutCore.InferHelpers

/-!
# `infer` is sound (docs/lean-model-v0.md §9, 1b step 3a; T2 for expressions)

```
theorem infer_sound : s.WF → Env.WF s.sz env → e.Src → e.Surf →
    (infer {} env e).go n s = .ok ((t, ε), s') →
    ∀ θ, Solves θ s' → HasType (Env.app θ env) e (Ty.app θ t) ε
```
stated, as the rest of the layer is, with `Wp`: every successful run satisfies the continuation.
It is Naraschewski and Nipkow's Algorithm W soundness shape: every grounding that solves the final
state types the expression. The proof is by induction on the expression, mutual with the helpers
`inferPlainArgs`, `inferLamArgs`, `inferTuple` and `inferSteps`.

`Expr.Surf` is the one side condition beyond `Expr.Src` and the well-formedness of the state and
environment: lambda annotations are surface types (`InferHelpers.lean`).

A call's arguments are checked in two passes, plain arguments first (spec §7 rule 3), and the
judgment types them in one list. `PlainRel` is what the first pass guarantees, `LamRel` what the
second guarantees, `Merge` puts the two lists of types back in argument order.
-/

namespace SproutCore

open Judgment

set_option linter.unusedVariables false

/-! ### The two passes over a call's arguments, as relations -/

/-- What the first pass guarantees. A lambda is skipped (`none`); a plain argument has a type that
flows into its slot, and the effects of the plain arguments are joined. -/
inductive PlainRel (Γ : Ctx) : List Expr → List Ty → List (Option Ty) → Eff → Prop
  | nil : PlainRel Γ [] [] [] .pure
  | lam (a : Expr) (as : List Expr) (s : Ty) (ss : List Ty) (os : List (Option Ty)) (εp : Eff) :
      isLam a = true → PlainRel Γ as ss os εp → PlainRel Γ (a :: as) (s :: ss) (none :: os) εp
  | plain (a : Expr) (as : List Expr) (s : Ty) (ss : List Ty) (t : Ty) (ε : Eff)
      (os : List (Option Ty)) (εp : Eff) :
      isLam a = false → HasType Γ a t ε → Flows t s → PlainRel Γ as ss os εp →
      PlainRel Γ (a :: as) (s :: ss) (some t :: os) (ε.join εp)

/-- What the second pass guarantees: each lambda has a type that flows into its slot. -/
inductive LamRel (Γ : Ctx) : List Expr → List Ty → List (Option Ty) → Prop
  | nil : LamRel Γ [] [] []
  | plain (a : Expr) (as : List Expr) (s : Ty) (ss : List Ty) (os : List (Option Ty)) :
      isLam a = false → LamRel Γ as ss os → LamRel Γ (a :: as) (s :: ss) (none :: os)
  | lam (a : Expr) (as : List Expr) (s : Ty) (ss : List Ty) (t : Ty) (os : List (Option Ty)) :
      isLam a = true → HasType Γ a t .pure → Flows t s → LamRel Γ as ss os →
      LamRel Γ (a :: as) (s :: ss) (some t :: os)

theorem args_assemble {Γ : Ctx} {args : List Expr} {ss : List Ty} {os : List (Option Ty)}
    {εp : Eff} (h : PlainRel Γ args ss os εp) :
    ∀ {os' : List (Option Ty)}, LamRel Γ args ss os' →
      ∃ ts, HasExprs Γ args ts εp ∧ FlowsAll ts ss := by
  induction h with
  | nil =>
    intro os' hl
    exact ⟨[], HasExprs.nil Γ, FlowsAll.nil⟩
  | lam a as s ss os εp hlam hrest ih =>
    intro os' hl
    cases hl with
    | plain _ _ _ _ _ hf _ => simp_all
    | lam _ _ _ _ t os'' _ ha hfl hrest' =>
      obtain ⟨ts', h1, h2⟩ := ih hrest'
      refine ⟨t :: ts', ?_, FlowsAll.cons _ _ _ _ hfl h2⟩
      have := HasExprs.cons Γ a as t ts' .pure εp ha h1
      rwa [Eff.join_pure_left] at this
  | plain a as s ss t ε os εp hlam ha hfl hrest ih =>
    intro os' hl
    cases hl with
    | lam _ _ _ _ _ _ hl' => simp_all
    | plain _ _ _ _ os'' _ hrest' =>
      obtain ⟨ts', h1, h2⟩ := ih hrest'
      exact ⟨t :: ts', HasExprs.cons Γ a as t ts' ε εp ha h1, FlowsAll.cons _ _ _ _ hfl h2⟩

/-- The checker's arity test gives the judgment's `ArityOk` under any grounding. -/
theorem arityOk_of_good (θ : Nat → Ty) {env : Env} {f : Expr} {n : Nat}
    (h : ArityGood env f n) : ArityOk (Env.app θ env) f n := by
  intro x k sc hfx hl
  rw [Env.app_lookup] at hl
  cases hb : env.lookup x with
  | none => rw [hb] at hl; cases hl
  | some b =>
    rw [hb] at hl
    cases b with
    | mono t => cases hl
    | poly m t => cases hl
    | fnMono k' t =>
      simp only [Option.map_some, Bind.app, Option.some.injEq, Binding.fn.injEq] at hl
      obtain ⟨hk, _⟩ := hl
      rw [← hk]
      exact h x k' hfx (Or.inl ⟨t, hb⟩)
    | fnPoly k' m t =>
      simp only [Option.map_some, Bind.app, Option.some.injEq, Binding.fn.injEq] at hl
      obtain ⟨hk, _⟩ := hl
      rw [← hk]
      exact h x k' hfx (Or.inr ⟨m, t, hb⟩)

/-! ### The theorem -/

set_option hygiene false in
macro_rules
  | `(tactic| wf_close) =>
    `(tactic| (show ∀ t, _ = some t → Ty.surf t = true; exact hann))

/-- `infer_sound` at the subterm `e`, with the environment left open. -/
abbrev InferSoundAt (rules : Rules) (e : Expr) : Prop :=
  ∀ {env : Env} {Q : Ty × Eff → CState → Prop} {s : CState} (hs : s.WF) (he : Env.WF s.sz env)
    (hb : e.Src) (hsb : e.Surf)
    (h : ∀ t ε s', Ext s s' → s'.WF → Ty.WF s'.sz 0 t →
      (∀ θ, Solves θ s' → HasType (Env.app θ env) e (Ty.app θ t) ε) → Q (t, ε) s'),
    Wp (infer rules env e) Q s

mutual
theorem infer_sound (rules : Rules) (hr : rules.localLetMono = true) :
    ∀ (e : Expr) (env : Env) (Q : Ty × Eff → CState → Prop) (s : CState), s.WF →
    Env.WF s.sz env → e.Src → e.Surf →
    (∀ t ε s', Ext s s' → s'.WF → Ty.WF s'.sz 0 t →
      (∀ θ, Solves θ s' → HasType (Env.app θ env) e (Ty.app θ t) ε) → Q (t, ε) s') →
    Wp (infer rules env e) Q s
  | .var x, env, Q, s, hs, he, _, _, h => by
    unfold infer
    split
    all_goals repeat' wp_step
    · rename_i _ t heq
      refine h t .pure s (Ext.refl _) hs (by simpa [Bind.WF] using he.lookup heq) ?_
      intro θ hθ
      exact HasType.var _ x (.local (Ty.app θ t)) _ (by rw [Env.app_lookup, heq]; rfl) rfl
    · rename_i _ k t heq
      have hb : Ty.WF s.sz 0 t := by simpa [Bind.WF] using he.lookup heq
      refine h t .pure s (Ext.refl _) hs hb ?_
      intro θ hθ
      refine HasType.var _ x (.fn k ⟨0, Ty.app θ t⟩) _ (by rw [Env.app_lookup, heq]; rfl) ?_
      exact ⟨[], rfl, by simp, (Ty.inst_of_ok [] _ (Ty.app_solved hθ.solved hb)).symm⟩
    · rename_i _ n t heq r s' e hw hwr hex
      obtain ⟨ms, hsz, hmv, rfl⟩ := hex
      have hb : Ty.WF s.sz n t := by simpa [Bind.WF] using he.lookup heq
      refine h _ .pure s' e hw hwr ?_
      intro θ hθ
      refine HasType.var _ x (.val ⟨n, Ty.app θ t⟩) _ (by rw [Env.app_lookup, heq]; rfl) ?_
      refine ⟨ms.toList.map (Ty.app θ), by simp [hsz], ?_, ?_⟩
      · intro u hu
        obtain ⟨v, hv, rfl⟩ := List.mem_map.mp hu
        obtain ⟨j, rfl⟩ := hmv v (Array.mem_toList_iff.mp hv)
        exact hθ.solved j
      · exact app_instTy θ hθ.solved ms t (fun i hi => hsz ▸ hb.2 i hi)
    · rename_i _ k n t heq r s' e hw hwr hex
      obtain ⟨ms, hsz, hmv, rfl⟩ := hex
      have hb : Ty.WF s.sz n t := by simpa [Bind.WF] using he.lookup heq
      refine h _ .pure s' e hw hwr ?_
      intro θ hθ
      refine HasType.var _ x (.fn k ⟨n, Ty.app θ t⟩) _ (by rw [Env.app_lookup, heq]; rfl) ?_
      refine ⟨ms.toList.map (Ty.app θ), by simp [hsz], ?_, ?_⟩
      · intro u hu
        obtain ⟨v, hv, rfl⟩ := List.mem_map.mp hu
        obtain ⟨j, rfl⟩ := hmv v (Array.mem_toList_iff.mp hv)
        exact hθ.solved j
      · exact app_instTy θ hθ.solved ms t (fun i hi => hsz ▸ hb.2 i hi)
  | .int _, env, Q, s, hs, he, _, _, h => by
    unfold infer
    exact Wp.pure' (h _ _ _ (Ext.refl _) hs Ty.WF_int
      fun θ hθ => HasType.int _ _)
  | .bool _, env, Q, s, hs, he, _, _, h => by
    unfold infer
    exact Wp.pure' (h _ _ _ (Ext.refl _) hs Ty.WF_bool
      fun θ hθ => HasType.bool _ _)
  | .str _, env, Q, s, hs, he, _, _, h => by
    unfold infer
    exact Wp.pure' (h _ _ _ (Ext.refl _) hs Ty.WF_str
      fun θ hθ => HasType.str _ _)
  | .unit, env, Q, s, hs, he, _, _, h => by
    unfold infer
    exact Wp.pure' (h _ _ _ (Ext.refl _) hs Ty.WF_unit
      fun θ hθ => HasType.unit _)
  | .lam x ann body, env, Q, s, hs, he, hsrc, hsurf, h => by
    obtain ⟨hann0, hbody0⟩ := hsrc
    obtain ⟨hann, hbody⟩ := hsurf
    have inferB : InferSoundAt rules body :=
      fun hs he hb hsb h => infer_sound rules hr body _ _ _ hs he hb hsb h
    unfold infer
    refine Wp.bind (lamParam_sound hs (fun t ht => by cases ht) hann ?_)
    intro pt s1 e1 w1 wpt hpt
    repeat' (first | leaf inferB | wp_step)
    rename_i t ε s2 e2 w2 wt ht
    refine h (mkArr [pt] t ε) .pure s2 (e1.trans e2) w2 (by wf_close) ?_
    intro θ hθ
    rw [Ty.app_mkArr]
    exact HasType.lam _ x ann body (Ty.app θ pt) (Ty.app θ t) ε (hpt θ (hθ.back e2)) (ht θ hθ)
  | .app f args, env, Q, s, hs, he, hsrc, hsurf, h => by
    obtain ⟨hf1, hargs1⟩ := hsrc
    obtain ⟨hsf, hsargs⟩ := hsurf
    have inferF : InferSoundAt rules f :=
      fun hs he hb hsb h => infer_sound rules hr f _ _ _ hs he hb hsb h
    have plainK : ∀ {slots : List Ty} {eff : Eff} {Q : Eff → CState → Prop} {s : CState}
        (hs : s.WF) (he : Env.WF s.sz env) (hargs : Expr.SrcList args)
        (hsargs : Expr.SurfList args) (hsl : ∀ t ∈ slots, Ty.WF s.sz 0 t)
        (h : ∀ eff' s', Ext s s' → s'.WF →
          (∀ θ, Solves θ s' → args.length = slots.length →
            ∃ os εp, PlainRel (Env.app θ env) args (slots.map (Ty.app θ)) os εp ∧
              eff' = eff.join εp) → Q eff' s'),
        Wp (inferPlainArgs rules env args slots eff) Q s :=
      fun hs he ha hsa hsl h => inferPlainArgs_sound rules hr env args _ _ _ _ hs he ha hsa hsl h
    have lamK : ∀ {slots : List Ty} {eff : Eff} {Q : Eff → CState → Prop} {s : CState}
        (hs : s.WF) (he : Env.WF s.sz env) (hargs : Expr.SrcList args)
        (hsargs : Expr.SurfList args) (hsl : ∀ t ∈ slots, Ty.WF s.sz 0 t)
        (h : ∀ eff' s', Ext s s' → s'.WF → eff' = eff →
          (∀ θ, Solves θ s' → args.length = slots.length →
            ∃ os, LamRel (Env.app θ env) args (slots.map (Ty.app θ)) os) → Q eff' s'),
        Wp (inferLamArgs rules env args slots eff) Q s :=
      fun hs he ha hsa hsl h => inferLamArgs_sound rules hr env args _ _ _ _ hs he ha hsa hsl h
    unfold infer
    repeat' (first | leaf inferF | leaf plainK | leaf lamK | leaf arityCheck_sound | wp_step)
    rename_i s0 hag τ ε s1 e1 w1 wτ hf ps r ce s2 e2 w2 wps wr hpeel s3 e3 w3 eff s4 e4
      w4 hlam hplain
    refine h r eff s4 (e1.trans (e2.trans (e3.trans e4))) w4
      (wr.mono (by omega) (Nat.le_refl _)) ?_
    intro θ hθ
    have hpl := hpeel θ (hθ.back (e3.trans e4))
    have hlen : args.length = ps.length := by
      have := Peel.length hpl
      simpa using this.symm
    obtain ⟨os, εp, hP, heq⟩ := hplain θ (hθ.back e4) hlen
    obtain ⟨os', hL⟩ := hlam θ hθ hlen
    obtain ⟨ts, hts, hfl⟩ := args_assemble hP hL
    have hf' := hf θ (hθ.back (e2.trans (e3.trans e4)))
    rw [heq, Eff.join_assoc]
    exact HasType.app _ f args (Ty.app θ τ) ε ts (ps.map (Ty.app θ)) (Ty.app θ r) ce εp hf'
      (arityOk_of_good θ hag) hpl hts hfl
  | .letIn x rhs body, env, Q, s, hs, he, hsrc, hsurf, h => by
    obtain ⟨hr1, hb1⟩ := hsrc
    obtain ⟨hs1, hsb1⟩ := hsurf
    have inferR : InferSoundAt rules rhs :=
      fun hs he hb hsb h => infer_sound rules hr rhs _ _ _ hs he hb hsb h
    have inferB : InferSoundAt rules body :=
      fun hs he hb hsb h => infer_sound rules hr body _ _ _ hs he hb hsb h
    unfold infer
    repeat' (first | leaf inferR | leaf inferB | leaf bindLocal_leaf | wp_step)
    rename_i t1 ε1 hε s1 e1 w1 wt1 ht1 t ε s2 e2 w2 wt ht
    refine h t ε s2 (e1.trans e2) w2 wt ?_
    intro θ hθ
    have hε' : ε1 = .pure := Eff.beq_pure.mp hε
    subst hε'
    exact HasType.letIn _ x rhs body (Ty.app θ t1) (Ty.app θ t) ε (ht1 θ (hθ.back e2)) (ht θ hθ)
  | .ite c t e, env, Q, s, hs, he, hsrc, hsurf, h => by
    obtain ⟨hc1, ht1, he1⟩ := hsrc
    obtain ⟨hsc, hst, hse⟩ := hsurf
    have inferC : InferSoundAt rules c :=
      fun hs he hb hsb h => infer_sound rules hr c _ _ _ hs he hb hsb h
    have inferT : InferSoundAt rules t :=
      fun hs he hb hsb h => infer_sound rules hr t _ _ _ hs he hb hsb h
    have inferE : InferSoundAt rules e :=
      fun hs he hb hsb h => infer_sound rules hr e _ _ _ hs he hb hsb h
    unfold infer
    repeat' (first | leaf inferC | leaf inferT | leaf inferE | wp_step)
    rename_i tc ec s1 e1 w1 wtc htc s2 e2 w2 hbool tt et s3 e3 w3 wtt htt te ee s4 e4 w4
      wte hte r s5 e5 w5 wr hj
    refine h r (ec.join (et.join ee)) s5 ((((e1.trans e2).trans e3).trans e4).trans e5)
      w5 wr ?_
    intro θ hθ
    have hb : Ty.app θ tc = .bool := hbool θ (hθ.back (e3.trans (e4.trans e5)))
    have hc : HasType (Env.app θ env) c .bool ec := by
      have := htc θ (hθ.back (e2.trans (e3.trans (e4.trans e5))))
      rwa [hb] at this
    have hj' := hj θ hθ
    simp only [JoinR, ite_true] at hj'
    exact HasType.ite _ c t e ec et ee (Ty.app θ tt) (Ty.app θ te) (Ty.app θ r) hc
      (htt θ (hθ.back (e4.trans e5))) (hte θ (hθ.back e5)) (Ty.app_solved hθ.solved wr)
      (Join.mk _ _ _ hj'.1 hj'.2)
  | .tup es, env, Q, s, hs, he, hsrc, hsurf, h => by
    have tupleK : ∀ {Q : List Ty × Eff → CState → Prop} {s : CState} (hs : s.WF)
        (he : Env.WF s.sz env) (hes : Expr.SrcList es) (hses : Expr.SurfList es)
        (h : ∀ ts eff s', Ext s s' → s'.WF → (∀ t ∈ ts, Ty.WF s'.sz 0 t) →
          (∀ θ, Solves θ s' → HasExprs (Env.app θ env) es (ts.map (Ty.app θ)) eff) →
          Q (ts, eff) s'),
        Wp (inferTuple rules env es) Q s :=
      fun hs he hes hses h => inferTuple_sound rules hr env es _ _ hs he hes hses h
    unfold infer
    repeat' (first | leaf tupleK | wp_step)
    rename_i ts eff s1 e1 w1 wts hts
    refine h (.tup ts) eff s1 e1 w1 (Ty.WF_tup.mpr wts) ?_
    intro θ hθ
    rw [Ty.app_tup]
    exact HasType.tup _ es _ eff (hts θ hθ)
  | .notE e, env, Q, s, hs, he, hsrc, hsurf, h => by
    have inferE : InferSoundAt rules e :=
      fun hs he hb hsb h => infer_sound rules hr e _ _ _ hs he hb hsb h
    unfold infer
    repeat' (first | leaf inferE | wp_step)
    rename_i t ε s1 e1 w1 wt ht s2 e2 w2 hb
    refine h .bool ε s2 (e1.trans e2) w2 Ty.WF_bool ?_
    intro θ hθ
    have ht' := ht θ (hθ.back e2)
    rw [hb θ hθ] at ht'
    exact HasType.notE _ e ε ht'
  | .doB steps final, env, Q, s, hs, he, hsrc, hsurf, h => by
    obtain ⟨hst1, hfin1⟩ := hsrc
    obtain ⟨hsst, hsfin⟩ := hsurf
    have stepsK : ∀ {Q : Env × Eff → CState → Prop} {s : CState} (hs : s.WF)
        (he : Env.WF s.sz env) (hst : Step.SrcList steps) (hsst : Step.SurfList steps)
        (h : ∀ env' eff s', Ext s s' → s'.WF → Env.WF s'.sz env' →
          (∀ θ, Solves θ s' → HasSteps (Env.app θ env) steps (Env.app θ env') eff) →
          Q (env', eff) s'),
        Wp (inferSteps rules env steps) Q s :=
      fun hs he hst hsst h => inferSteps_sound rules hr env steps _ _ hs he hst hsst h
    have inferF : InferSoundAt rules final :=
      fun hs he hb hsb h => infer_sound rules hr final _ _ _ hs he hb hsb h
    unfold infer
    repeat' (first | leaf stepsK | leaf inferF | wp_step)
    rename_i env' eff s1 e1 w1 we hst t ε s2 e2 w2 wt ht
    refine h t (eff.join ε) s2 (e1.trans e2) w2 wt ?_
    intro θ hθ
    exact HasType.doB _ _ steps final (Ty.app θ t) eff ε (hst θ (hθ.back e2)) (ht θ hθ)

theorem inferPlainArgs_sound (rules : Rules) (hr : rules.localLetMono = true) (env : Env) :
    ∀ (args : List Expr) (slots : List Ty) (eff : Eff) (Q : Eff → CState → Prop) (s : CState),
    s.WF → Env.WF s.sz env → Expr.SrcList args → Expr.SurfList args →
    (∀ t ∈ slots, Ty.WF s.sz 0 t) →
    (∀ eff' s', Ext s s' → s'.WF →
      (∀ θ, Solves θ s' → args.length = slots.length →
        ∃ os εp, PlainRel (Env.app θ env) args (slots.map (Ty.app θ)) os εp ∧
          eff' = eff.join εp) → Q eff' s') →
    Wp (inferPlainArgs rules env args slots eff) Q s
  | a :: as, s0 :: ss, eff, Q, s, hs, he, hsrc, hsurf, hsl, h => by
    obtain ⟨ha, has⟩ := hsrc
    obtain ⟨hsa, hsas⟩ := hsurf
    have inferA : InferSoundAt rules a :=
      fun hs he hb hsf h => infer_sound rules hr a _ _ _ hs he hb hsf h
    have plainK : ∀ {eff : Eff} {Q : Eff → CState → Prop} {s : CState} (hs : s.WF)
        (he : Env.WF s.sz env) (has : Expr.SrcList as) (hsas : Expr.SurfList as)
        (hsl : ∀ t ∈ ss, Ty.WF s.sz 0 t)
        (h : ∀ eff' s', Ext s s' → s'.WF →
          (∀ θ, Solves θ s' → as.length = ss.length →
            ∃ os εp, PlainRel (Env.app θ env) as (ss.map (Ty.app θ)) os εp ∧
              eff' = eff.join εp) → Q eff' s'),
        Wp (inferPlainArgs rules env as ss eff) Q s :=
      fun hs he has hsas hsl h => inferPlainArgs_sound rules hr env as ss _ _ _ hs he has hsas hsl h
    have hs0 : Ty.WF s.sz 0 s0 := hsl s0 (List.mem_cons_self ..)
    have hss : ∀ t ∈ ss, Ty.WF s.sz 0 t := fun t ht => hsl t (List.mem_cons_of_mem _ ht)
    unfold inferPlainArgs
    split
    · rename_i hlam
      refine plainK hs he has hsas hss ?_
      intro eff' s' e w hq
      refine h eff' s' e w ?_
      intro θ hθ hlen
      obtain ⟨os, εp, hp, heq⟩ := hq θ hθ (by simpa using hlen)
      exact ⟨none :: os, εp, PlainRel.lam a as _ _ os εp hlam hp, heq⟩
    · rename_i hlam
      repeat' (first | leaf inferA | leaf plainK | wp_step)
      rename_i t ε s1 e1 w1 wt ht s2 e2 w2 hfl eff' s3 e3 w3 hq
      refine h eff' s3 ((e1.trans e2).trans e3) w3 ?_
      intro θ hθ hlen
      obtain ⟨os, εp, hp, heq⟩ := hq θ hθ (by simpa using hlen)
      refine ⟨some (Ty.app θ t) :: os, ε.join εp, ?_, ?_⟩
      · exact PlainRel.plain a as _ _ (Ty.app θ t) ε os εp (by simpa using hlam)
          (ht θ (hθ.back (e2.trans e3))) (hfl θ (hθ.back e3)) hp
      · rw [heq, Eff.join_assoc]
  | [], slots, eff, Q, s, hs, he, hsrc, hsurf, hsl, h => by
    unfold inferPlainArgs
    refine Wp.pure' (h _ _ (Ext.refl _) hs ?_)
    intro θ hθ hlen
    cases slots with
    | nil => exact ⟨[], .pure, PlainRel.nil, by simp [Eff.join_pure_right]⟩
    | cons _ _ => simp at hlen
  | _ :: _, [], eff, Q, s, hs, he, hsrc, hsurf, hsl, h => by
    unfold inferPlainArgs
    refine Wp.pure' (h _ _ (Ext.refl _) hs ?_)
    intro θ hθ hlen
    simp at hlen

theorem inferLamArgs_sound (rules : Rules) (hr : rules.localLetMono = true) (env : Env) :
    ∀ (args : List Expr) (slots : List Ty) (eff : Eff) (Q : Eff → CState → Prop) (s : CState),
    s.WF → Env.WF s.sz env → Expr.SrcList args → Expr.SurfList args →
    (∀ t ∈ slots, Ty.WF s.sz 0 t) →
    (∀ eff' s', Ext s s' → s'.WF → eff' = eff →
      (∀ θ, Solves θ s' → args.length = slots.length →
        ∃ os, LamRel (Env.app θ env) args (slots.map (Ty.app θ)) os) → Q eff' s') →
    Wp (inferLamArgs rules env args slots eff) Q s
  | a :: as, s0 :: ss, eff, Q, s, hs, he, hsrc, hsurf, hsl, h => by
    obtain ⟨ha, has⟩ := hsrc
    obtain ⟨hsa, hsas⟩ := hsurf
    have lamK : ∀ {eff : Eff} {Q : Eff → CState → Prop} {s : CState} (hs : s.WF)
        (he : Env.WF s.sz env) (has : Expr.SrcList as) (hsas : Expr.SurfList as)
        (hsl : ∀ t ∈ ss, Ty.WF s.sz 0 t)
        (h : ∀ eff' s', Ext s s' → s'.WF → eff' = eff →
          (∀ θ, Solves θ s' → as.length = ss.length →
            ∃ os, LamRel (Env.app θ env) as (ss.map (Ty.app θ)) os) → Q eff' s'),
        Wp (inferLamArgs rules env as ss eff) Q s :=
      fun hs he has hsas hsl h => inferLamArgs_sound rules hr env as ss _ _ _ hs he has hsas hsl h
    have hs0 : Ty.WF s.sz 0 s0 := hsl s0 (List.mem_cons_self ..)
    have hss : ∀ t ∈ ss, Ty.WF s.sz 0 t := fun t ht => hsl t (List.mem_cons_of_mem _ ht)
    cases a with
    | lam x ann body =>
      obtain ⟨hann, hbody⟩ := hsa
      obtain ⟨hann0, hbody0⟩ := ha
      have inferK : InferSoundAt rules body :=
        fun hs he hb hsb h => infer_sound rules hr body _ _ _ hs he hb hsb h
      simp only [inferLamArgs]
      refine Wp.bind (lamParam_sound hs
        (fun t ht => by have h1 := Option.some.inj ht; subst h1; exact hs0) hann ?_)
      intro pt s1 e1 w1 wpt hpt
      repeat' (first | leaf inferK | leaf lamK | wp_step)
      rename_i t ε s2 e2 w2 wt ht s3 e3 w3 hfl eff' s4 e4 w4 hq
      refine h eff' s4 (((e1.trans e2).trans e3).trans e4) w4 rfl ?_
      intro θ hθ hlen
      obtain ⟨os, hos⟩ := hq θ hθ (by simpa using hlen)
      refine ⟨some (Ty.app θ (mkArr [pt] t ε)) :: os, ?_⟩
      refine LamRel.lam (.lam x ann body) as _ _ _ os rfl ?_ (hfl θ (hθ.back e4)) hos
      rw [Ty.app_mkArr]
      exact HasType.lam _ x ann body (Ty.app θ pt) (Ty.app θ t) ε
        (hpt θ (hθ.back (e2.trans (e3.trans e4)))) (ht θ (hθ.back (e3.trans e4)))
    | _ =>
      simp only [inferLamArgs]
      refine lamK hs he has hsas hss ?_
      intro eff' s' e w heq hq
      refine h eff' s' e w heq ?_
      intro θ hθ hlen
      obtain ⟨os, hos⟩ := hq θ hθ (by simpa using hlen)
      exact ⟨none :: os, LamRel.plain _ as _ _ os rfl hos⟩
  | [], slots, eff, Q, s, hs, he, hsrc, hsurf, hsl, h => by
    simp only [inferLamArgs]
    refine Wp.pure' (h _ _ (Ext.refl _) hs rfl ?_)
    intro θ hθ hlen
    cases slots with
    | nil => exact ⟨[], LamRel.nil⟩
    | cons _ _ => simp at hlen
  | _ :: _, [], eff, Q, s, hs, he, hsrc, hsurf, hsl, h => by
    simp only [inferLamArgs]
    refine Wp.pure' (h _ _ (Ext.refl _) hs rfl ?_)
    intro θ hθ hlen
    simp at hlen

theorem inferTuple_sound (rules : Rules) (hr : rules.localLetMono = true) (env : Env) :
    ∀ (es : List Expr) (Q : List Ty × Eff → CState → Prop) (s : CState), s.WF →
    Env.WF s.sz env → Expr.SrcList es → Expr.SurfList es →
    (∀ ts eff s', Ext s s' → s'.WF → (∀ t ∈ ts, Ty.WF s'.sz 0 t) →
      (∀ θ, Solves θ s' → HasExprs (Env.app θ env) es (ts.map (Ty.app θ)) eff) →
      Q (ts, eff) s') →
    Wp (inferTuple rules env es) Q s
  | [], Q, s, hs, he, hsrc, hsurf, h => by
    unfold inferTuple
    exact Wp.pure' (h _ _ _ (Ext.refl _) hs (by simp)
      fun θ hθ => HasExprs.nil _)
  | e :: es, Q, s, hs, he, hsrc, hsurf, h => by
    obtain ⟨he', hes⟩ := hsrc
    obtain ⟨hse, hses⟩ := hsurf
    have inferE : InferSoundAt rules e :=
      fun hs he hb hsf h => infer_sound rules hr e _ _ _ hs he hb hsf h
    have tupleK : ∀ {Q : List Ty × Eff → CState → Prop} {s : CState} (hs : s.WF)
        (he : Env.WF s.sz env) (hes : Expr.SrcList es) (hses : Expr.SurfList es)
        (h : ∀ ts eff s', Ext s s' → s'.WF → (∀ t ∈ ts, Ty.WF s'.sz 0 t) →
          (∀ θ, Solves θ s' → HasExprs (Env.app θ env) es (ts.map (Ty.app θ)) eff) →
          Q (ts, eff) s'),
        Wp (inferTuple rules env es) Q s :=
      fun hs he hes hses h => inferTuple_sound rules hr env es _ _ hs he hes hses h
    unfold inferTuple
    repeat' (first | leaf inferE | leaf tupleK | wp_step)
    rename_i t ε s1 e1 w1 wt ht ts eff s2 e2 w2 wts hts
    refine h (t :: ts) (ε.join eff) s2 (e1.trans e2) w2
      (Ty.WF.list_cons (wt.mono (by omega) (Nat.le_refl _)) wts) ?_
    intro θ hθ
    have := HasExprs.cons (Env.app θ env) e es (Ty.app θ t) (ts.map (Ty.app θ)) ε eff
      (ht θ (hθ.back e2)) (hts θ hθ)
    simpa using this

theorem inferSteps_sound (rules : Rules) (hr : rules.localLetMono = true) (env : Env) :
    ∀ (steps : List Step) (Q : Env × Eff → CState → Prop) (s : CState), s.WF →
    Env.WF s.sz env → Step.SrcList steps → Step.SurfList steps →
    (∀ env' eff s', Ext s s' → s'.WF → Env.WF s'.sz env' →
      (∀ θ, Solves θ s' → HasSteps (Env.app θ env) steps (Env.app θ env') eff) →
      Q (env', eff) s') →
    Wp (inferSteps rules env steps) Q s
  | [], Q, s, hs, he, hsrc, hsurf, h => by
    unfold inferSteps
    exact Wp.pure' (h _ _ _ (Ext.refl _) hs he fun θ hθ => HasSteps.nil _)
  | .letS x rhs :: rest, Q, s, hs, he, hsrc, hsurf, h => by
    obtain ⟨hr1, hrest⟩ := hsrc
    obtain ⟨hs1, hsrest⟩ := hsurf
    have inferR : InferSoundAt rules rhs :=
      fun hs he hb hsf h => infer_sound rules hr rhs _ _ _ hs he hb hsf h
    have stepsK : ∀ {env : Env} {Q : Env × Eff → CState → Prop} {s : CState} (hs : s.WF)
        (he : Env.WF s.sz env) (hrest : Step.SrcList rest) (hsrest : Step.SurfList rest)
        (h : ∀ env' eff s', Ext s s' → s'.WF → Env.WF s'.sz env' →
          (∀ θ, Solves θ s' → HasSteps (Env.app θ env) rest (Env.app θ env') eff) →
          Q (env', eff) s'),
        Wp (inferSteps rules env rest) Q s :=
      fun hs he hrest hsrest h => inferSteps_sound rules hr _ rest _ _ hs he hrest hsrest h
    unfold inferSteps
    repeat' (first | leaf inferR | leaf stepsK | leaf bindLocal_leaf | wp_step)
    rename_i t ε hε s1 e1 w1 wt ht env' eff s2 e2 w2 we hst
    refine h env' eff s2 (e1.trans e2) w2 we ?_
    intro θ hθ
    have hε' : ε = .pure := Eff.beq_pure.mp hε
    subst hε'
    exact HasSteps.letS _ _ x rhs rest (Ty.app θ t) eff (ht θ (hθ.back e2)) (hst θ hθ)
  | .exprS e :: rest, Q, s, hs, he, hsrc, hsurf, h => by
    obtain ⟨he1, hrest⟩ := hsrc
    obtain ⟨hse, hsrest⟩ := hsurf
    have inferE : InferSoundAt rules e :=
      fun hs he hb hsf h => infer_sound rules hr e _ _ _ hs he hb hsf h
    have stepsK : ∀ {Q : Env × Eff → CState → Prop} {s : CState} (hs : s.WF)
        (he : Env.WF s.sz env) (hrest : Step.SrcList rest) (hsrest : Step.SurfList rest)
        (h : ∀ env' eff s', Ext s s' → s'.WF → Env.WF s'.sz env' →
          (∀ θ, Solves θ s' → HasSteps (Env.app θ env) rest (Env.app θ env') eff) →
          Q (env', eff) s'),
        Wp (inferSteps rules env rest) Q s :=
      fun hs he hrest hsrest h => inferSteps_sound rules hr _ rest _ _ hs he hrest hsrest h
    unfold inferSteps
    repeat' (first | leaf inferE | leaf stepsK | wp_step)
    rename_i t ε s1 e1 w1 wt ht env' eff s2 e2 w2 we hst
    refine h env' (ε.join eff) s2 (e1.trans e2) w2 we ?_
    intro θ hθ
    exact HasSteps.exprS _ _ e rest (Ty.app θ t) ε eff (ht θ (hθ.back e2)) (hst θ hθ)
end

/-- **T2 for expressions.** A successful run of `infer` under the default rules, from a well-formed
state and environment, on a source expression, yields a type and an effect that every grounding
solving the final state turns into a derivation. The state also only grew, stayed well formed, and
the type is in range. -/
theorem infer_sound_run {s : CState} {env : Env} {e : Expr} {n : Nat} {t : Ty} {ε : Eff}
    {s' : CState} (hs : s.WF) (he : Env.WF s.sz env) (hsrc : e.Src) (hsurf : e.Surf)
    (hrun : (infer {} env e).go n s = .ok ((t, ε), s')) :
    Ext s s' ∧ s'.WF ∧ Ty.WF s'.sz 0 t ∧
      ∀ θ, Solves θ s' → HasType (Env.app θ env) e (Ty.app θ t) ε :=
  infer_sound {} rfl e env
    (fun r s2 => Ext s s2 ∧ s2.WF ∧ Ty.WF s2.sz 0 r.1 ∧
      ∀ θ, Solves θ s2 → HasType (Env.app θ env) e (Ty.app θ r.1) r.2)
    s hs he hsrc hsurf (fun t ε s' ex hw ht hty => ⟨ex, hw, ht, hty⟩) n (t, ε) s' hrun

end SproutCore
