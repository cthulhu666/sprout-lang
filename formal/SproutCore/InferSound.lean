import SproutCore.InferHelpers
import SproutCore.SolvesExists

/-!
# `infer` is sound (docs/lean-model-v0.md §9, 1b step 3a; T2 for expressions)

```
theorem infer_sound_run : s.WF → s.NF → Env.WF s.sz env → Env.NF env → e.Src → e.Surf →
    (infer {} env e).go n s = .ok ((t, ε), s') →
    Ext s s' ∧ s'.WF ∧ s'.NF ∧ Ty.WF s'.sz 0 t ∧ t.NF ∧
      ∀ θ, Solves θ s' → HasType (Env.app θ env) e (Ty.app θ t) ε
```
Every grounding that solves the final state types the expression: Naraschewski and Nipkow's
Algorithm W soundness shape. The state also only grew and stayed well formed, the type is in range,
and the state and the type are named-free. `infer_sound_exists` adds `exists_solves`
(`SolvesExists.lean`): some `θ` solves the final state, so an accepted expression has a derivation.

`infer_sound` is the statement behind it, with `Wp`: every successful run satisfies a continuation.
The proof is by induction on the expression, mutual with the helpers `inferPlainArgs`,
`inferLamArgs`, `inferTuple` and `inferSteps`.

The side conditions beyond `Expr.Src` and the well-formedness of the state and environment:
`Expr.Surf` (lambda annotations are surface types, `InferHelpers.lean`), and `CState.NF`,
`Env.NF` (no `.named` in a bound type or a binding; `Ty.app` keeps a `.named`, which has no solved
grounding, so a `.named` would leave the state with no solving `θ`). `infer` keeps them: `Ext`
carries `CState.NF`, and each result type is `Ty.NF`.

A call's arguments are checked in two passes, plain arguments first (spec §7 rule 3), and the
judgment types them in one list. `PlainRel` is what the first pass guarantees, `LamRel` what the
second guarantees, `args_assemble` puts the two lists of types back in argument order.
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

-- environments appear only here: `nf_close` reads their bindings and extends them
local macro_rules
  | `(tactic| nf_rule) => `(tactic| (apply Env.NF.cons <;> nf_close))
local macro_rules
  | `(tactic| nf_rule) => `(tactic| (simp only [Bind.NF]; nf_close))
local macro_rules
  | `(tactic| nf_rule) =>
    `(tactic| (have hb := Env.NF.lookup ‹Env.NF _› ‹List.lookup _ _ = _›;
               simp only [Bind.NF] at hb; assumption))

/-- `infer_sound` at the subterm `e`, with the environment left open. -/
abbrev InferSoundAt (rules : Rules) (e : Expr) : Prop :=
  ∀ {env : Env} {Q : Ty × Eff → CState → Prop} {s : CState} (hs : s.WF) (hnf : s.NF)
    (he : Env.WF s.sz env) (hen : Env.NF env) (hb : e.Src) (hsb : e.Surf)
    (h : ∀ t ε s', Ext s s' → s'.WF → Ty.WF s'.sz 0 t → t.NF →
      (∀ θ, Solves θ s' → HasType (Env.app θ env) e (Ty.app θ t) ε) → Q (t, ε) s'),
    Wp (infer rules env e) Q s

mutual
theorem infer_sound (rules : Rules) (hr : rules.localLetMono = true) :
    ∀ (e : Expr) (env : Env) (Q : Ty × Eff → CState → Prop) (s : CState), s.WF → s.NF →
    Env.WF s.sz env → Env.NF env → e.Src → e.Surf →
    (∀ t ε s', Ext s s' → s'.WF → Ty.WF s'.sz 0 t → t.NF →
      (∀ θ, Solves θ s' → HasType (Env.app θ env) e (Ty.app θ t) ε) → Q (t, ε) s') →
    Wp (infer rules env e) Q s
  | .var x, env, Q, s, hs, hnf, he, hen, _, _, h => by
    unfold infer
    split
    all_goals repeat' sound_step
    · rename_i _ t heq
      refine h t .pure s (Ext.refl _) hs (by simpa [Bind.WF] using he.lookup heq)
        (by simpa [Bind.NF] using hen.lookup heq) ?_
      intro θ hθ
      exact HasType.var _ x (.local (Ty.app θ t)) _ (by rw [Env.app_lookup, heq]; rfl) rfl
    · rename_i _ k t heq
      have hb : Ty.WF s.sz 0 t := by simpa [Bind.WF] using he.lookup heq
      have hbn : Ty.NF t := by simpa [Bind.NF] using hen.lookup heq
      refine h t .pure s (Ext.refl _) hs hb hbn ?_
      intro θ hθ
      refine HasType.var _ x (.fn k ⟨0, Ty.app θ t⟩) _ (by rw [Env.app_lookup, heq]; rfl) ?_
      exact ⟨[], rfl, by simp, (Ty.inst_of_ok [] _ (Ty.app_solved hθ.solved hb hbn)).symm⟩
    · rename_i _ n t heq r s' e hw hwr _ hex
      obtain ⟨ms, hsz, hmv, rfl⟩ := hex
      have hb : Ty.WF s.sz n t := by simpa [Bind.WF] using he.lookup heq
      refine h _ .pure s' e hw hwr (by nf_close) ?_
      intro θ hθ
      refine HasType.var _ x (.val ⟨n, Ty.app θ t⟩) _ (by rw [Env.app_lookup, heq]; rfl) ?_
      refine ⟨ms.toList.map (Ty.app θ), by simp [hsz], ?_, ?_⟩
      · intro u hu
        obtain ⟨v, hv, rfl⟩ := List.mem_map.mp hu
        obtain ⟨j, rfl⟩ := hmv v (Array.mem_toList_iff.mp hv)
        exact hθ.solved j
      · exact app_instTy θ hθ.solved ms t (fun i hi => hsz ▸ hb.2 i hi)
    · rename_i _ k n t heq r s' e hw hwr _ hex
      obtain ⟨ms, hsz, hmv, rfl⟩ := hex
      have hb : Ty.WF s.sz n t := by simpa [Bind.WF] using he.lookup heq
      refine h _ .pure s' e hw hwr (by nf_close) ?_
      intro θ hθ
      refine HasType.var _ x (.fn k ⟨n, Ty.app θ t⟩) _ (by rw [Env.app_lookup, heq]; rfl) ?_
      refine ⟨ms.toList.map (Ty.app θ), by simp [hsz], ?_, ?_⟩
      · intro u hu
        obtain ⟨v, hv, rfl⟩ := List.mem_map.mp hu
        obtain ⟨j, rfl⟩ := hmv v (Array.mem_toList_iff.mp hv)
        exact hθ.solved j
      · exact app_instTy θ hθ.solved ms t (fun i hi => hsz ▸ hb.2 i hi)
  | .int _, env, Q, s, hs, hnf, he, hen, _, _, h => by
    unfold infer
    exact Wp.pure' (h _ _ _ (Ext.refl _) hs Ty.WF_int Ty.NF_int
      fun θ hθ => HasType.int _ _)
  | .bool _, env, Q, s, hs, hnf, he, hen, _, _, h => by
    unfold infer
    exact Wp.pure' (h _ _ _ (Ext.refl _) hs Ty.WF_bool Ty.NF_bool
      fun θ hθ => HasType.bool _ _)
  | .str _, env, Q, s, hs, hnf, he, hen, _, _, h => by
    unfold infer
    exact Wp.pure' (h _ _ _ (Ext.refl _) hs Ty.WF_str Ty.NF_str
      fun θ hθ => HasType.str _ _)
  | .unit, env, Q, s, hs, hnf, he, hen, _, _, h => by
    unfold infer
    exact Wp.pure' (h _ _ _ (Ext.refl _) hs Ty.WF_unit Ty.NF_unit
      fun θ hθ => HasType.unit _)
  | .lam x ann body, env, Q, s, hs, hnf, he, hen, hsrc, hsurf, h => by
    obtain ⟨hann0, hbody0⟩ := hsrc
    obtain ⟨hann, hbody⟩ := hsurf
    have inferB : InferSoundAt rules body :=
      fun hs hnf he hen hb hsb h => infer_sound rules hr body _ _ _ hs hnf he hen hb hsb h
    unfold infer
    refine Wp.bind (lamParam_sound hs hnf (fun t ht => by cases ht) (fun t ht => by cases ht) hann ?_)
    intro pt s1 e1 w1 wpt _ hpt
    repeat' (first | sleaf inferB | sound_step)
    rename_i t ε s2 e2 w2 wt _ ht
    refine h (mkArr [pt] t ε) .pure s2 (e1.trans e2) w2 (by wf_close) (by nf_close) ?_
    intro θ hθ
    rw [Ty.app_mkArr]
    exact HasType.lam _ x ann body (Ty.app θ pt) (Ty.app θ t) ε (hpt θ (hθ.back e2)) (ht θ hθ)
  | .app f args, env, Q, s, hs, hnf, he, hen, hsrc, hsurf, h => by
    obtain ⟨hf1, hargs1⟩ := hsrc
    obtain ⟨hsf, hsargs⟩ := hsurf
    have inferF : InferSoundAt rules f :=
      fun hs hnf he hen hb hsb h => infer_sound rules hr f _ _ _ hs hnf he hen hb hsb h
    have plainK : ∀ {slots : List Ty} {eff : Eff} {Q : Eff → CState → Prop} {s : CState}
        (hs : s.WF) (hnf : s.NF) (he : Env.WF s.sz env) (hen : Env.NF env)
        (hargs : Expr.SrcList args) (hsargs : Expr.SurfList args)
        (hsl : ∀ t ∈ slots, Ty.WF s.sz 0 t) (hsln : ∀ t ∈ slots, t.NF)
        (h : ∀ eff' s', Ext s s' → s'.WF →
          (∀ θ, Solves θ s' → args.length = slots.length →
            ∃ os εp, PlainRel (Env.app θ env) args (slots.map (Ty.app θ)) os εp ∧
              eff' = eff.join εp) → Q eff' s'),
        Wp (inferPlainArgs rules env args slots eff) Q s :=
      fun hs hnf he hen ha hsa hsl hsln h =>
        inferPlainArgs_sound rules hr env args _ _ _ _ hs hnf he hen ha hsa hsl hsln h
    have lamK : ∀ {slots : List Ty} {eff : Eff} {Q : Eff → CState → Prop} {s : CState}
        (hs : s.WF) (hnf : s.NF) (he : Env.WF s.sz env) (hen : Env.NF env)
        (hargs : Expr.SrcList args) (hsargs : Expr.SurfList args)
        (hsl : ∀ t ∈ slots, Ty.WF s.sz 0 t) (hsln : ∀ t ∈ slots, t.NF)
        (h : ∀ eff' s', Ext s s' → s'.WF → eff' = eff →
          (∀ θ, Solves θ s' → args.length = slots.length →
            ∃ os, LamRel (Env.app θ env) args (slots.map (Ty.app θ)) os) → Q eff' s'),
        Wp (inferLamArgs rules env args slots eff) Q s :=
      fun hs hnf he hen ha hsa hsl hsln h =>
        inferLamArgs_sound rules hr env args _ _ _ _ hs hnf he hen ha hsa hsl hsln h
    unfold infer
    repeat' (first | sleaf inferF | sleaf plainK | sleaf lamK | sleaf arityCheck_sound | sound_step)
    rename_i hag s0 τ ε s1 e1 w1 wτ _ hf ps r ce s2 e2 w2 wps _ wr _ hpeel s3 e3 w3 eff s4 e4
      w4 hlam hplain
    refine h r eff s4 (e1.trans (e2.trans (e3.trans e4))) w4
      (wr.mono (by omega) (Nat.le_refl _)) (by nf_close) ?_
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
  | .letIn x rhs body, env, Q, s, hs, hnf, he, hen, hsrc, hsurf, h => by
    obtain ⟨hr1, hb1⟩ := hsrc
    obtain ⟨hs1, hsb1⟩ := hsurf
    have inferR : InferSoundAt rules rhs :=
      fun hs hnf he hen hb hsb h => infer_sound rules hr rhs _ _ _ hs hnf he hen hb hsb h
    have inferB : InferSoundAt rules body :=
      fun hs hnf he hen hb hsb h => infer_sound rules hr body _ _ _ hs hnf he hen hb hsb h
    unfold infer
    repeat' (first | sleaf inferR | sleaf inferB | sleaf bindLocal_leaf | sound_step)
    rename_i t1 ε1 _ hε s1 e1 w1 wt1 ht1 t ε s2 e2 w2 wt _ ht
    refine h t ε s2 (e1.trans e2) w2 wt (by nf_close) ?_
    intro θ hθ
    have hε' : ε1 = .pure := Eff.beq_pure.mp hε
    subst hε'
    exact HasType.letIn _ x rhs body (Ty.app θ t1) (Ty.app θ t) ε (ht1 θ (hθ.back e2)) (ht θ hθ)
  | .ite c t e, env, Q, s, hs, hnf, he, hen, hsrc, hsurf, h => by
    obtain ⟨hc1, ht1, he1⟩ := hsrc
    obtain ⟨hsc, hst, hse⟩ := hsurf
    have inferC : InferSoundAt rules c :=
      fun hs hnf he hen hb hsb h => infer_sound rules hr c _ _ _ hs hnf he hen hb hsb h
    have inferT : InferSoundAt rules t :=
      fun hs hnf he hen hb hsb h => infer_sound rules hr t _ _ _ hs hnf he hen hb hsb h
    have inferE : InferSoundAt rules e :=
      fun hs hnf he hen hb hsb h => infer_sound rules hr e _ _ _ hs hnf he hen hb hsb h
    unfold infer
    repeat' (first | sleaf inferC | sleaf inferT | sleaf inferE | sound_step)
    rename_i tc ec s1 e1 w1 wtc _ htc s2 e2 w2 hbool tt et s3 e3 w3 wtt _ htt te ee s4 e4 w4
      wte _ hte r s5 e5 w5 wr _ hj
    refine h r (ec.join (et.join ee)) s5 ((((e1.trans e2).trans e3).trans e4).trans e5)
      w5 wr (by nf_close) ?_
    intro θ hθ
    have hb : Ty.app θ tc = .bool := hbool θ (hθ.back (e3.trans (e4.trans e5)))
    have hc : HasType (Env.app θ env) c .bool ec := by
      have := htc θ (hθ.back (e2.trans (e3.trans (e4.trans e5))))
      rwa [hb] at this
    have hj' := hj θ hθ
    simp only [JoinR, ite_true] at hj'
    exact HasType.ite _ c t e ec et ee (Ty.app θ tt) (Ty.app θ te) (Ty.app θ r) hc
      (htt θ (hθ.back (e4.trans e5))) (hte θ (hθ.back e5)) (Ty.app_solved hθ.solved wr (by nf_close))
      (Join.mk _ _ _ hj'.1 hj'.2)
  | .tup es, env, Q, s, hs, hnf, he, hen, hsrc, hsurf, h => by
    have tupleK : ∀ {Q : List Ty × Eff → CState → Prop} {s : CState} (hs : s.WF) (hnf : s.NF)
        (he : Env.WF s.sz env) (hen : Env.NF env) (hes : Expr.SrcList es)
        (hses : Expr.SurfList es)
        (h : ∀ ts eff s', Ext s s' → s'.WF → (∀ t ∈ ts, Ty.WF s'.sz 0 t) → (∀ t ∈ ts, t.NF) →
          (∀ θ, Solves θ s' → HasExprs (Env.app θ env) es (ts.map (Ty.app θ)) eff) →
          Q (ts, eff) s'),
        Wp (inferTuple rules env es) Q s :=
      fun hs hnf he hen hes hses h => inferTuple_sound rules hr env es _ _ hs hnf he hen hes hses h
    unfold infer
    repeat' (first | sleaf tupleK | sound_step)
    rename_i ts eff s1 e1 w1 wts _ hts
    refine h (.tup ts) eff s1 e1 w1 (Ty.WF_tup.mpr wts) (by nf_close) ?_
    intro θ hθ
    rw [Ty.app_tup]
    exact HasType.tup _ es _ eff (hts θ hθ)
  | .notE e, env, Q, s, hs, hnf, he, hen, hsrc, hsurf, h => by
    have inferE : InferSoundAt rules e :=
      fun hs hnf he hen hb hsb h => infer_sound rules hr e _ _ _ hs hnf he hen hb hsb h
    unfold infer
    repeat' (first | sleaf inferE | sound_step)
    rename_i t ε s1 e1 w1 wt _ ht s2 e2 w2 hb
    refine h .bool ε s2 (e1.trans e2) w2 Ty.WF_bool Ty.NF_bool ?_
    intro θ hθ
    have ht' := ht θ (hθ.back e2)
    rw [hb θ hθ] at ht'
    exact HasType.notE _ e ε ht'
  | .doB steps final, env, Q, s, hs, hnf, he, hen, hsrc, hsurf, h => by
    obtain ⟨hst1, hfin1⟩ := hsrc
    obtain ⟨hsst, hsfin⟩ := hsurf
    have stepsK : ∀ {Q : Env × Eff → CState → Prop} {s : CState} (hs : s.WF) (hnf : s.NF)
        (he : Env.WF s.sz env) (hen : Env.NF env) (hst : Step.SrcList steps)
        (hsst : Step.SurfList steps)
        (h : ∀ env' eff s', Ext s s' → s'.WF → Env.WF s'.sz env' → Env.NF env' →
          (∀ θ, Solves θ s' → HasSteps (Env.app θ env) steps (Env.app θ env') eff) →
          Q (env', eff) s'),
        Wp (inferSteps rules env steps) Q s :=
      fun hs hnf he hen hst hsst h => inferSteps_sound rules hr env steps _ _ hs hnf he hen hst hsst h
    have inferF : InferSoundAt rules final :=
      fun hs hnf he hen hb hsb h => infer_sound rules hr final _ _ _ hs hnf he hen hb hsb h
    unfold infer
    repeat' (first | sleaf stepsK | sleaf inferF | sound_step)
    rename_i env' eff s1 e1 w1 we _ hst t ε s2 e2 w2 wt _ ht
    refine h t (eff.join ε) s2 (e1.trans e2) w2 wt (by nf_close) ?_
    intro θ hθ
    exact HasType.doB _ _ steps final (Ty.app θ t) eff ε (hst θ (hθ.back e2)) (ht θ hθ)

theorem inferPlainArgs_sound (rules : Rules) (hr : rules.localLetMono = true) (env : Env) :
    ∀ (args : List Expr) (slots : List Ty) (eff : Eff) (Q : Eff → CState → Prop) (s : CState),
    s.WF → s.NF → Env.WF s.sz env → Env.NF env → Expr.SrcList args → Expr.SurfList args →
    (∀ t ∈ slots, Ty.WF s.sz 0 t) → (∀ t ∈ slots, t.NF) →
    (∀ eff' s', Ext s s' → s'.WF →
      (∀ θ, Solves θ s' → args.length = slots.length →
        ∃ os εp, PlainRel (Env.app θ env) args (slots.map (Ty.app θ)) os εp ∧
          eff' = eff.join εp) → Q eff' s') →
    Wp (inferPlainArgs rules env args slots eff) Q s
  | a :: as, s0 :: ss, eff, Q, s, hs, hnf, he, hen, hsrc, hsurf, hsl, hsln, h => by
    obtain ⟨ha, has⟩ := hsrc
    obtain ⟨hsa, hsas⟩ := hsurf
    have inferA : InferSoundAt rules a :=
      fun hs hnf he hen hb hsf h => infer_sound rules hr a _ _ _ hs hnf he hen hb hsf h
    have plainK : ∀ {eff : Eff} {Q : Eff → CState → Prop} {s : CState} (hs : s.WF) (hnf : s.NF)
        (he : Env.WF s.sz env) (hen : Env.NF env) (has : Expr.SrcList as)
        (hsas : Expr.SurfList as) (hsl : ∀ t ∈ ss, Ty.WF s.sz 0 t) (hsln : ∀ t ∈ ss, t.NF)
        (h : ∀ eff' s', Ext s s' → s'.WF →
          (∀ θ, Solves θ s' → as.length = ss.length →
            ∃ os εp, PlainRel (Env.app θ env) as (ss.map (Ty.app θ)) os εp ∧
              eff' = eff.join εp) → Q eff' s'),
        Wp (inferPlainArgs rules env as ss eff) Q s :=
      fun hs hnf he hen has hsas hsl hsln h =>
        inferPlainArgs_sound rules hr env as ss _ _ _ hs hnf he hen has hsas hsl hsln h
    have hs0 : Ty.WF s.sz 0 s0 := hsl s0 (List.mem_cons_self ..)
    have hs0n : s0.NF := hsln s0 (List.mem_cons_self ..)
    have hss : ∀ t ∈ ss, Ty.WF s.sz 0 t := fun t ht => hsl t (List.mem_cons_of_mem _ ht)
    have hssn : ∀ t ∈ ss, t.NF := fun t ht => hsln t (List.mem_cons_of_mem _ ht)
    unfold inferPlainArgs
    split
    · rename_i hlam
      refine plainK hs hnf he hen has hsas hss hssn ?_
      intro eff' s' e w hq
      refine h eff' s' e w ?_
      intro θ hθ hlen
      obtain ⟨os, εp, hp, heq⟩ := hq θ hθ (by simpa using hlen)
      exact ⟨none :: os, εp, PlainRel.lam a as _ _ os εp hlam hp, heq⟩
    · rename_i hlam
      repeat' (first | sleaf inferA | sleaf plainK | sound_step)
      rename_i t ε s1 e1 w1 wt _ ht s2 e2 w2 hfl eff' s3 e3 w3 hq
      refine h eff' s3 ((e1.trans e2).trans e3) w3 ?_
      intro θ hθ hlen
      obtain ⟨os, εp, hp, heq⟩ := hq θ hθ (by simpa using hlen)
      refine ⟨some (Ty.app θ t) :: os, ε.join εp, ?_, ?_⟩
      · exact PlainRel.plain a as _ _ (Ty.app θ t) ε os εp (by simpa using hlam)
          (ht θ (hθ.back (e2.trans e3))) (hfl θ (hθ.back e3)) hp
      · rw [heq, Eff.join_assoc]
  | [], slots, eff, Q, s, hs, hnf, he, hen, hsrc, hsurf, hsl, hsln, h => by
    unfold inferPlainArgs
    refine Wp.pure' (h _ _ (Ext.refl _) hs ?_)
    intro θ hθ hlen
    cases slots with
    | nil => exact ⟨[], .pure, PlainRel.nil, by simp [Eff.join_pure_right]⟩
    | cons _ _ => simp at hlen
  | _ :: _, [], eff, Q, s, hs, hnf, he, hen, hsrc, hsurf, hsl, hsln, h => by
    unfold inferPlainArgs
    refine Wp.pure' (h _ _ (Ext.refl _) hs ?_)
    intro θ hθ hlen
    simp at hlen

theorem inferLamArgs_sound (rules : Rules) (hr : rules.localLetMono = true) (env : Env) :
    ∀ (args : List Expr) (slots : List Ty) (eff : Eff) (Q : Eff → CState → Prop) (s : CState),
    s.WF → s.NF → Env.WF s.sz env → Env.NF env → Expr.SrcList args → Expr.SurfList args →
    (∀ t ∈ slots, Ty.WF s.sz 0 t) → (∀ t ∈ slots, t.NF) →
    (∀ eff' s', Ext s s' → s'.WF → eff' = eff →
      (∀ θ, Solves θ s' → args.length = slots.length →
        ∃ os, LamRel (Env.app θ env) args (slots.map (Ty.app θ)) os) → Q eff' s') →
    Wp (inferLamArgs rules env args slots eff) Q s
  | a :: as, s0 :: ss, eff, Q, s, hs, hnf, he, hen, hsrc, hsurf, hsl, hsln, h => by
    obtain ⟨ha, has⟩ := hsrc
    obtain ⟨hsa, hsas⟩ := hsurf
    have lamK : ∀ {eff : Eff} {Q : Eff → CState → Prop} {s : CState} (hs : s.WF) (hnf : s.NF)
        (he : Env.WF s.sz env) (hen : Env.NF env) (has : Expr.SrcList as)
        (hsas : Expr.SurfList as) (hsl : ∀ t ∈ ss, Ty.WF s.sz 0 t) (hsln : ∀ t ∈ ss, t.NF)
        (h : ∀ eff' s', Ext s s' → s'.WF → eff' = eff →
          (∀ θ, Solves θ s' → as.length = ss.length →
            ∃ os, LamRel (Env.app θ env) as (ss.map (Ty.app θ)) os) → Q eff' s'),
        Wp (inferLamArgs rules env as ss eff) Q s :=
      fun hs hnf he hen has hsas hsl hsln h =>
        inferLamArgs_sound rules hr env as ss _ _ _ hs hnf he hen has hsas hsl hsln h
    have hs0 : Ty.WF s.sz 0 s0 := hsl s0 (List.mem_cons_self ..)
    have hs0n : s0.NF := hsln s0 (List.mem_cons_self ..)
    have hss : ∀ t ∈ ss, Ty.WF s.sz 0 t := fun t ht => hsl t (List.mem_cons_of_mem _ ht)
    have hssn : ∀ t ∈ ss, t.NF := fun t ht => hsln t (List.mem_cons_of_mem _ ht)
    cases a with
    | lam x ann body =>
      obtain ⟨hann, hbody⟩ := hsa
      obtain ⟨hann0, hbody0⟩ := ha
      have inferK : InferSoundAt rules body :=
        fun hs hnf he hen hb hsb h => infer_sound rules hr body _ _ _ hs hnf he hen hb hsb h
      simp only [inferLamArgs]
      refine Wp.bind (lamParam_sound hs hnf
        (fun t ht => by have h1 := Option.some.inj ht; subst h1; exact hs0)
        (fun t ht => by have h1 := Option.some.inj ht; subst h1; exact hs0n) hann ?_)
      intro pt s1 e1 w1 wpt _ hpt
      repeat' (first | sleaf inferK | sleaf lamK | sound_step)
      rename_i t ε s2 e2 w2 wt _ ht s3 e3 w3 hfl eff' s4 e4 w4 hq
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
      refine lamK hs hnf he hen has hsas hss hssn ?_
      intro eff' s' e w heq hq
      refine h eff' s' e w heq ?_
      intro θ hθ hlen
      obtain ⟨os, hos⟩ := hq θ hθ (by simpa using hlen)
      exact ⟨none :: os, LamRel.plain _ as _ _ os rfl hos⟩
  | [], slots, eff, Q, s, hs, hnf, he, hen, hsrc, hsurf, hsl, hsln, h => by
    simp only [inferLamArgs]
    refine Wp.pure' (h _ _ (Ext.refl _) hs rfl ?_)
    intro θ hθ hlen
    cases slots with
    | nil => exact ⟨[], LamRel.nil⟩
    | cons _ _ => simp at hlen
  | _ :: _, [], eff, Q, s, hs, hnf, he, hen, hsrc, hsurf, hsl, hsln, h => by
    simp only [inferLamArgs]
    refine Wp.pure' (h _ _ (Ext.refl _) hs rfl ?_)
    intro θ hθ hlen
    simp at hlen

theorem inferTuple_sound (rules : Rules) (hr : rules.localLetMono = true) (env : Env) :
    ∀ (es : List Expr) (Q : List Ty × Eff → CState → Prop) (s : CState), s.WF → s.NF →
    Env.WF s.sz env → Env.NF env → Expr.SrcList es → Expr.SurfList es →
    (∀ ts eff s', Ext s s' → s'.WF → (∀ t ∈ ts, Ty.WF s'.sz 0 t) → (∀ t ∈ ts, t.NF) →
      (∀ θ, Solves θ s' → HasExprs (Env.app θ env) es (ts.map (Ty.app θ)) eff) →
      Q (ts, eff) s') →
    Wp (inferTuple rules env es) Q s
  | [], Q, s, hs, hnf, he, hen, hsrc, hsurf, h => by
    unfold inferTuple
    exact Wp.pure' (h _ _ _ (Ext.refl _) hs (by simp) (by simp)
      fun θ hθ => HasExprs.nil _)
  | e :: es, Q, s, hs, hnf, he, hen, hsrc, hsurf, h => by
    obtain ⟨he', hes⟩ := hsrc
    obtain ⟨hse, hses⟩ := hsurf
    have inferE : InferSoundAt rules e :=
      fun hs hnf he hen hb hsf h => infer_sound rules hr e _ _ _ hs hnf he hen hb hsf h
    have tupleK : ∀ {Q : List Ty × Eff → CState → Prop} {s : CState} (hs : s.WF) (hnf : s.NF)
        (he : Env.WF s.sz env) (hen : Env.NF env) (hes : Expr.SrcList es)
        (hses : Expr.SurfList es)
        (h : ∀ ts eff s', Ext s s' → s'.WF → (∀ t ∈ ts, Ty.WF s'.sz 0 t) → (∀ t ∈ ts, t.NF) →
          (∀ θ, Solves θ s' → HasExprs (Env.app θ env) es (ts.map (Ty.app θ)) eff) →
          Q (ts, eff) s'),
        Wp (inferTuple rules env es) Q s :=
      fun hs hnf he hen hes hses h => inferTuple_sound rules hr env es _ _ hs hnf he hen hes hses h
    unfold inferTuple
    repeat' (first | sleaf inferE | sleaf tupleK | sound_step)
    rename_i t ε s1 e1 w1 wt _ ht ts eff s2 e2 w2 wts _ hts
    refine h (t :: ts) (ε.join eff) s2 (e1.trans e2) w2
      (Ty.WF.list_cons (wt.mono (by omega) (Nat.le_refl _)) wts) (by nf_close) ?_
    intro θ hθ
    have := HasExprs.cons (Env.app θ env) e es (Ty.app θ t) (ts.map (Ty.app θ)) ε eff
      (ht θ (hθ.back e2)) (hts θ hθ)
    simpa using this

theorem inferSteps_sound (rules : Rules) (hr : rules.localLetMono = true) (env : Env) :
    ∀ (steps : List Step) (Q : Env × Eff → CState → Prop) (s : CState), s.WF → s.NF →
    Env.WF s.sz env → Env.NF env → Step.SrcList steps → Step.SurfList steps →
    (∀ env' eff s', Ext s s' → s'.WF → Env.WF s'.sz env' → Env.NF env' →
      (∀ θ, Solves θ s' → HasSteps (Env.app θ env) steps (Env.app θ env') eff) →
      Q (env', eff) s') →
    Wp (inferSteps rules env steps) Q s
  | [], Q, s, hs, hnf, he, hen, hsrc, hsurf, h => by
    unfold inferSteps
    exact Wp.pure' (h _ _ _ (Ext.refl _) hs he hen fun θ hθ => HasSteps.nil _)
  | .letS x rhs :: rest, Q, s, hs, hnf, he, hen, hsrc, hsurf, h => by
    obtain ⟨hr1, hrest⟩ := hsrc
    obtain ⟨hs1, hsrest⟩ := hsurf
    have inferR : InferSoundAt rules rhs :=
      fun hs hnf he hen hb hsf h => infer_sound rules hr rhs _ _ _ hs hnf he hen hb hsf h
    have stepsK : ∀ {env : Env} {Q : Env × Eff → CState → Prop} {s : CState} (hs : s.WF)
        (hnf : s.NF) (he : Env.WF s.sz env) (hen : Env.NF env) (hrest : Step.SrcList rest)
        (hsrest : Step.SurfList rest)
        (h : ∀ env' eff s', Ext s s' → s'.WF → Env.WF s'.sz env' → Env.NF env' →
          (∀ θ, Solves θ s' → HasSteps (Env.app θ env) rest (Env.app θ env') eff) →
          Q (env', eff) s'),
        Wp (inferSteps rules env rest) Q s :=
      fun hs hnf he hen hrest hsrest h => inferSteps_sound rules hr _ rest _ _ hs hnf he hen hrest hsrest h
    unfold inferSteps
    repeat' (first | sleaf inferR | sleaf stepsK | sleaf bindLocal_leaf | sound_step)
    rename_i t ε _ hε s1 e1 w1 wt ht env' eff s2 e2 w2 we _ hst
    refine h env' eff s2 (e1.trans e2) w2 we (by nf_close) ?_
    intro θ hθ
    have hε' : ε = .pure := Eff.beq_pure.mp hε
    subst hε'
    exact HasSteps.letS _ _ x rhs rest (Ty.app θ t) eff (ht θ (hθ.back e2)) (hst θ hθ)
  | .exprS e :: rest, Q, s, hs, hnf, he, hen, hsrc, hsurf, h => by
    obtain ⟨he1, hrest⟩ := hsrc
    obtain ⟨hse, hsrest⟩ := hsurf
    have inferE : InferSoundAt rules e :=
      fun hs hnf he hen hb hsf h => infer_sound rules hr e _ _ _ hs hnf he hen hb hsf h
    have stepsK : ∀ {Q : Env × Eff → CState → Prop} {s : CState} (hs : s.WF) (hnf : s.NF)
        (he : Env.WF s.sz env) (hen : Env.NF env) (hrest : Step.SrcList rest)
        (hsrest : Step.SurfList rest)
        (h : ∀ env' eff s', Ext s s' → s'.WF → Env.WF s'.sz env' → Env.NF env' →
          (∀ θ, Solves θ s' → HasSteps (Env.app θ env) rest (Env.app θ env') eff) →
          Q (env', eff) s'),
        Wp (inferSteps rules env rest) Q s :=
      fun hs hnf he hen hrest hsrest h => inferSteps_sound rules hr _ rest _ _ hs hnf he hen hrest hsrest h
    unfold inferSteps
    repeat' (first | sleaf inferE | sleaf stepsK | sound_step)
    rename_i t ε s1 e1 w1 wt _ ht env' eff s2 e2 w2 we _ hst
    refine h env' (ε.join eff) s2 (e1.trans e2) w2 we (by nf_close) ?_
    intro θ hθ
    exact HasSteps.exprS _ _ e rest (Ty.app θ t) ε eff (ht θ (hθ.back e2)) (hst θ hθ)
end

/-- **T2 for expressions.** A successful run of `infer` under the default rules, from a well-formed
named-free state and environment, on a source expression, yields a type and an effect that every
grounding solving the final state turns into a derivation. The state also only grew, stayed well
formed and named-free, and the type is in range and named-free. -/
theorem infer_sound_run {s : CState} {env : Env} {e : Expr} {n : Nat} {t : Ty} {ε : Eff}
    {s' : CState} (hs : s.WF) (hnf : s.NF) (he : Env.WF s.sz env) (hen : Env.NF env)
    (hsrc : e.Src) (hsurf : e.Surf)
    (hrun : (infer {} env e).go n s = .ok ((t, ε), s')) :
    Ext s s' ∧ s'.WF ∧ s'.NF ∧ Ty.WF s'.sz 0 t ∧ t.NF ∧
      ∀ θ, Solves θ s' → HasType (Env.app θ env) e (Ty.app θ t) ε :=
  infer_sound {} rfl e env
    (fun r s2 => Ext s s2 ∧ s2.WF ∧ s2.NF ∧ Ty.WF s2.sz 0 r.1 ∧ r.1.NF ∧
      ∀ θ, Solves θ s2 → HasType (Env.app θ env) e (Ty.app θ r.1) r.2)
    s hs hnf he hen hsrc hsurf
    (fun t ε s' ex hw ht nt hty => ⟨ex, hw, ex.nf hnf, ht, nt, hty⟩) n (t, ε) s' hrun

/-- **An accepted expression is typed.** With `exists_solves`: the grounding the theorem quantifies
over exists, so some `θ` types the expression under `Env.app θ env`. -/
theorem infer_sound_exists {s : CState} {env : Env} {e : Expr} {n : Nat} {t : Ty} {ε : Eff}
    {s' : CState} (hs : s.WF) (hnf : s.NF) (he : Env.WF s.sz env) (hen : Env.NF env)
    (hsrc : e.Src) (hsurf : e.Surf)
    (hrun : (infer {} env e).go n s = .ok ((t, ε), s')) :
    ∃ θ, HasType (Env.app θ env) e (Ty.app θ t) ε := by
  obtain ⟨_, hw, hn, _, _, hty⟩ := infer_sound_run hs hnf he hen hsrc hsurf hrun
  obtain ⟨θ, hθ⟩ := exists_solves hw hn
  exact ⟨θ, hty θ hθ⟩

end SproutCore
