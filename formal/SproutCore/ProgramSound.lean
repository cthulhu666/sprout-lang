import SproutCore.FnSound
import SproutCore.PlanInv

/-!
# `checkProgram` is sound (docs/lean-model-v0.md §9, 1b step 3b, part 5; T2 for whole programs)

```
theorem checkProgram_sound : p.Surf → checkProgram {} p fuel = .ok () → p.Typed
```
`p.Surf`: every written type of a `fn` signature or a top-level `let` annotation, and every lambda
annotation, is a surface type (`Ty.surf`: no `.mv`, `.skolem`, `.gen`), and every term is a surface
term (`Expr.Surf`). The parser builds nothing else. `Expr.Src` follows from `Expr.Surf`.

`runPlan_sound` walks the plan's groups in order. After each group the environment holds only
closed schemes, so `Env.app θ` of it does not depend on `θ` (`Env.app_closed`); the loop keeps one
context `Γ` and a `Groups` derivation, and each group picks its own grounding (`pick_grounding`) to
turn `inferLet_sound` / `inferFnGroup_sound` into a `GroupTy`. Then each `main` is checked against
§10.10's signature. `preludeEnv` holds `print_str` as an `fnPoly` of arity 1, so `Env.app θ` of it
is `Ctx.prelude`.
-/

namespace SproutCore

open Judgment

set_option linter.unusedVariables false

/-! ### Source conditions -/

/-- A declaration's written types are surface types and its terms are source terms (the parser
builds no metavariable, skolem or `gen`). -/
def Decl.Surf : Decl → Prop
  | .fn n ps sg b => FnInfo.Surf ⟨n, ps, sg, b⟩
  | .letD _ a i => (∀ t, a = some t → t.surf = true) ∧ i.Surf
  | .mainFn _ b => b.Surf

def Program.Surf (p : Program) : Prop := ∀ d ∈ p.decls, Decl.Surf d


/-! ### The prelude -/

theorem Env.app_prelude (θ : Nat → Ty) : Env.app θ preludeEnv = Ctx.prelude := by
  simp only [Env.app, preludeEnv, Ctx.prelude, List.map_cons, List.map_nil, Bind.app]
  rfl

/-! ### Building `Groups` -/

theorem Groups.snoc_perm {Γ0 Γ1 Γ2 : Ctx} {ds g' gd : List Decl} (h : Groups Γ0 ds Γ1)
    (hg : GroupTy Γ1 g' Γ2) (hp : gd.Perm g') : Groups Γ0 (ds ++ gd) Γ2 := by
  induction h with
  | nil Γ =>
    exact Groups.step Γ Γ2 Γ2 gd g' [] (by simpa using hp) hg (Groups.nil _)
  | step Γ Γ₁ Γ₂' ds' g rest hperm hg' hrest ih =>
    refine Groups.step Γ Γ₁ Γ2 (ds' ++ gd) g (rest ++ gd) ?_ hg' (ih hg)
    rw [← List.append_assoc]
    exact hperm.append_right gd

/-- Pick the grounding: some `θ` solves the state and sends each unsolved variable of the zonked
types to its own skolem above `B`. -/
theorem pick_grounding {s : CState} (hs : s.WF) (hnf : s.NF) {zts : List Ty}
    (hu : ∀ zt ∈ zts, ∀ m ∈ metasOf zt, s.Unbound m) {P : (Nat → Ty) → Prop}
    (hz : ∀ B, (∀ zt ∈ zts, zt.SkLt B) → ∀ θ, Solves θ s →
      (∀ zt ∈ zts, ∀ m ∈ metasOf zt, θ m = .skolem (B + m)) → P θ) : ∃ θ, P θ := by
  obtain ⟨B, hB0⟩ := Ty.exists_skLt_list (Ty.skOfList zts)
  have hB : ∀ zt ∈ zts, zt.SkLt B := fun zt hzt k hk => hB0 k (mem_skOfList.mpr ⟨zt, hzt, hk⟩)
  obtain ⟨θ, hθ, hfresh⟩ := exists_solves_with hs hnf (fun m => .skolem (B + m)) (fun _ => rfl)
  exact ⟨θ, hz B hB θ hθ (fun zt hzt m hm => hfresh m (hu zt hzt m hm))⟩


/-- The declaration node `i` stands for. -/
def Plan.declAt (pl : Plan) (i : Nat) : Decl := (pl.nodes[i]?.getD default).toDecl

theorem Plan.declAt_mem {p : Program} {pl : Plan} (hinv : Plan.Inv p pl) {i : Nat}
    (hi : i < pl.nodes.size) : pl.declAt i ∈ p.decls := by
  have hmem : pl.declAt i ∈ pl.nodes.toList.map Node.toDecl := by
    refine List.mem_map.mpr ⟨pl.nodes[i], Array.mem_toList_iff.mpr (Array.getElem_mem hi), ?_⟩
    simp [Plan.declAt, hi]
  rw [hinv.nodes_eq] at hmem
  exact (List.mem_filter.mp hmem).1

/-- A node list whose `fn` nodes are all of it is a list of `fnN`. -/
theorem exists_fnN : ∀ ms : List Node,
    (ms.filterMap (fun nd => match nd with | Node.fnN f => some f | _ => none)).length =
      ms.length → ∃ fs : List FnInfo, ms = fs.map Node.fnN
  | [], _ => ⟨[], rfl⟩
  | nd0 :: rest, hl => by
    cases nd0 with
    | letN nm a i =>
      simp only [List.filterMap_cons, List.length_cons] at hl
      have := List.length_filterMap_le (fun nd => match nd with | Node.fnN f => some f | _ => none) rest
      omega
    | fnN f =>
      simp only [List.filterMap_cons, List.length_cons, Nat.add_right_cancel_iff] at hl
      obtain ⟨fs, rfl⟩ := exists_fnN rest hl
      exact ⟨f :: fs, rfl⟩

theorem Judgment.Groups.perm {Γ Γ' : Ctx} {ds ds' : List Decl} (h : Groups Γ ds Γ') (hp : ds.Perm ds') :
    Groups Γ ds' Γ' := by
  induction h generalizing ds' with
  | nil Γ =>
    have : ds' = [] := (List.Perm.eq_nil hp.symm)
    subst this
    exact Groups.nil _
  | step Γ Γ₁ Γ₂ ds'' g rest hperm hg hrest ih =>
    exact Groups.step Γ Γ₁ Γ₂ ds' g rest (hp.symm.trans hperm) hg hrest

theorem flatMap_map_decl (h : Nat → Decl) :
    ∀ L : List (List Nat), L.flatMap (fun g => g.map h) = L.flatten.map h
  | [] => rfl
  | g :: L => by simp [List.flatMap_cons, List.flatten_cons, List.map_append, flatMap_map_decl h L]

theorem declAt_range {p : Program} {pl : Plan} (hinv : Plan.Inv p pl) :
    (List.range pl.nodes.size).map pl.declAt = p.nodes := by
  rw [← hinv.nodes_eq]
  apply List.ext_getElem
  · simp
  · intro i h1 h2
    simp only [List.getElem_map, List.getElem_range, Plan.declAt]
    have hi : i < pl.nodes.size := by simpa using h2
    simp [hi]

theorem runPlan_sound {p : Program} {pl : Plan} (hinv : Plan.Inv p pl) (hp : p.Surf) :
    Wp (runPlan {} pl) (fun _ _ => ∃ Γ, Groups Ctx.prelude p.nodes Γ ∧
      ∀ sg body, Decl.mainFn sg body ∈ p.decls → MainOk Γ sg body) {} := by
  have hord : ∀ g ∈ pl.order, ∀ i ∈ g, Decl.Surf (pl.declAt i) := fun g hg i hi =>
    hp _ (Plan.declAt_mem hinv (hinv.order_lt g hg i hi))
  unfold runPlan
  dsimp only
  refine Wp.bind (Wp.forIn_prefix
    (fun (pre : List (List Nat)) (env : Env) s1 => Ext {} s1 ∧ s1.WF ∧ Env.Closed env ∧
      ∃ Γ, (∀ θ, Env.app θ env = Γ) ∧
        Groups Ctx.prelude (pre.flatMap fun g => g.map pl.declAt) Γ)
    pl.order preludeEnv ⟨Ext.refl _, CState.WF.init, Env.Closed.prelude, Ctx.prelude,
      Env.app_prelude, Groups.nil _⟩ ?_ ?_)
  · intro pre g post env s1 hpre ⟨e1, h1, hcl, Γ, hΓ, hgr⟩
    have hgm : g ∈ pl.order := by rw [← hpre]; simp
    have hnf1 : s1.NF := e1.nf CState.NF.init
    split
    · rename_i nm a i heq
      split
      · exact Wp.bind (Wp.reject' _ _)
      · obtain ⟨i0, hg0⟩ : ∃ i0, g = [i0] := by
          cases g with
          | nil => simp at heq
          | cons i0 rest =>
            cases rest with
            | nil => exact ⟨i0, rfl⟩
            | cons j rest' => simp at heq
        subst hg0
        have hnode : pl.nodes[i0]?.getD default = Node.letN nm a i := by simpa using heq
        have hsurf : Decl.Surf (Decl.letD nm a i) := by
          have := hord [i0] hgm i0 (by simp)
          simpa [Plan.declAt, hnode, Node.toDecl] using this
        refine Wp.bind (inferLet_sound h1 hnf1 hcl hsurf.1 hsurf.2 ?_)
        intro env' s' ex w hcl' ⟨zt, hu, hz⟩
        refine Wp.pure' ⟨_, rfl, e1.trans ex, w, hcl', ?_⟩
        have hnf' : s'.NF := ex.nf hnf1
        obtain ⟨θ, hθg⟩ := pick_grounding (zts := [zt]) w hnf' (by simpa using hu)
          (P := fun θ => GroupTy (Env.app θ env) [Decl.letD nm a i] (Env.app θ env'))
          (fun B hB θ hθ hm => hz B (hB zt (by simp)) θ hθ (hm zt (by simp)))
        refine ⟨Env.app θ env', fun θ' => Env.app_closed hcl' θ' θ, ?_⟩
        rw [hΓ θ] at hθg
        have := Groups.snoc_perm hgr hθg (gd := [Decl.letD nm a i]) (List.Perm.refl _)
        simpa [List.flatMap_append, Plan.declAt, hnode, Node.toDecl] using this
    · rename_i hnotlet
      split
      · exact Wp.bind (Wp.reject' _ _)
      · rename_i hlen'
        simp only [bne_iff_ne, ne_eq, Decidable.not_not] at hlen'
        have hlen := hlen'
        have hne : (List.filterMap (fun nd => match nd with | Node.fnN f => some f | _ => none)
            (List.map (fun i => pl.nodes[i]?.getD default) g)) ≠ [] := by
          intro h0
          have h3 := congrArg List.length h0
          have hpos : (List.map (fun i => pl.nodes[i]?.getD default) g).length ≠ 0 := by
            intro h
            exact hinv.order_ne g hgm (List.length_eq_zero_iff.mp (by simpa using h))
          exact hpos (hlen.symm.trans h3)
        have hfsurf : ∀ f ∈ (List.filterMap (fun nd => match nd with | Node.fnN f => some f | _ => none)
            (List.map (fun i => pl.nodes[i]?.getD default) g)), f.Surf := by
          intro f hf
          obtain ⟨nd, hnd, hfe⟩ := List.mem_filterMap.mp hf
          obtain ⟨i1, hi1, rfl⟩ := List.mem_map.mp hnd
          have hs1 := hord g hgm i1 hi1
          cases hnd' : pl.nodes[i1]?.getD default with
          | letN nm a i => rw [hnd'] at hfe; simp at hfe
          | fnN f' =>
            rw [hnd'] at hfe
            simp only [Option.some.injEq] at hfe
            subst hfe
            simpa [Plan.declAt, hnd', Node.toDecl, Decl.Surf] using hs1
        refine Wp.bind (inferFnGroup_sound h1 hnf1 hcl hne hfsurf ?_)
        intro env' s' ex w hcl' ⟨zts, hu, hz⟩
        refine Wp.pure' ⟨_, rfl, e1.trans ex, w, hcl', ?_⟩
        have hnf' : s'.NF := ex.nf hnf1
        obtain ⟨θ, hθg⟩ := pick_grounding (zts := zts) w hnf' hu
          (P := fun θ => GroupTy (Env.app θ env) _ (Env.app θ env')) hz
        refine ⟨Env.app θ env', fun θ' => Env.app_closed hcl' θ' θ, ?_⟩
        rw [hΓ θ] at hθg
        have hperm : (g.map pl.declAt).Perm (List.map FnInfo.toDecl (List.filterMap
            (fun nd => match nd with | Node.fnN f => some f | _ => none)
            (List.map (fun i => pl.nodes[i]?.getD default) g)).reverse) := by
          rw [List.map_reverse]
          refine List.Perm.trans ?_ (List.reverse_perm _).symm
          obtain ⟨fs, hfs⟩ := exists_fnN _ hlen
          have hd : g.map pl.declAt =
              (List.map (fun i => pl.nodes[i]?.getD default) g).map Node.toDecl := by
            rw [List.map_map]; rfl
          rw [hd, hfs]
          simp only [List.filterMap_map, Function.comp_def, List.filterMap_some, List.map_map]
          exact List.Perm.refl _
        have := Groups.snoc_perm hgr hθg hperm
        simpa [List.flatMap_append] using this
  · intro env s1 ⟨e1, h1, hcl, Γ, hΓ, hgr⟩
    have hnf1 : s1.NF := e1.nf CState.NF.init
    have hgr' : Groups Ctx.prelude p.nodes Γ := by
      refine hgr.perm ?_
      rw [flatMap_map_decl, ← declAt_range hinv]
      exact hinv.order_perm.map _
    have hmsurf : ∀ m ∈ pl.mains, m.2.2.Surf := by
      intro m hm
      obtain ⟨pos, sg, body⟩ := m
      have := hp _ ((hinv.mains_eq sg body).mpr ⟨pos, hm⟩)
      exact this
    refine Wp.bind (Wp.forIn_prefix
      (fun (pre2 : List (Nat × Option (Ty × Eff) × Expr)) (_ : PUnit) s2 => Ext s1 s2 ∧ s2.WF ∧
        ∀ m ∈ pre2, MainOk Γ m.2.1 m.2.2) pl.mains PUnit.unit ⟨Ext.refl _, h1, by simp⟩ ?_ ?_)
    · intro pre2 m post u s2 hpre2 ⟨e2, h2, hdone⟩
      have hmm : m ∈ pl.mains := by rw [← hpre2]; simp
      obtain ⟨pos, sg, body⟩ := m
      have hbs := hmsurf _ hmm
      have hnf2 : s2.NF := e2.nf hnf1
      cases sg with
      | none => exact Wp.bind (Wp.reject' _ _)
      | some re =>
        obtain ⟨r, e⟩ := re
        dsimp only
        split
        · rename_i hr
          split
          · rename_i he
            have hr' : r = Ty.unit ∨ r = Ty.int := by
              simp only [Bool.or_eq_true] at hr
              rcases hr with h | h
              · exact Or.inl (eq_of_beq h)
              · exact Or.inr (eq_of_beq h)
            have he' : e = Eff.io := Eff.eq_of_beq he
            refine Wp.bind (checkBody_sound (params := []) h2 hnf2 (hcl.wf _) hcl.nf
              (fun p hp => by cases hp) (fun p hp => by cases hp) ?_ ?_
              (Expr.src_of_surf _ hbs) hbs ?_)
            · rcases hr' with rfl | rfl <;> simp
            · rcases hr' with rfl | rfl <;> simp
            · intro s' ex w hty
              obtain ⟨θ, hθ⟩ := exists_solves w (ex.nf hnf2)
              obtain ⟨tb, eb, h1', h2', _⟩ := hty θ hθ
              refine Wp.pure' ⟨_, rfl, e2.trans ex, w, ?_⟩
              intro m hm
              rcases List.mem_append.mp hm with hm | hm
              · exact hdone m hm
              · have : m = (pos, some (r, e), body) := by simpa using hm
                subst this
                have hrapp : Ty.app θ r = r := by rcases hr' with rfl | rfl <;> rfl
                refine ⟨r, by rw [he'], hr', tb, eb, ?_, ?_⟩
                · have : bindParams (Env.app θ env) (([] : List (String × Ty)).map
                      fun p => (p.1, Ty.app θ p.2)) = Γ := by
                    simp [bindParams, hΓ θ]
                  rw [this] at h1'
                  exact h1'
                · rw [hrapp] at h2'; exact h2'
          · exact Wp.bind (Wp.reject' _ _)
        · exact Wp.bind (Wp.reject' _ _)
    · intro u s2 ⟨e2, h2, hdone⟩
      refine Wp.pure' ⟨Γ, hgr', ?_⟩
      intro sg body hm
      obtain ⟨pos, hpos⟩ := (hinv.mains_eq sg body).mp hm
      exact hdone _ hpos

/-- **T2 for whole programs.** If the checker accepts a program whose written types are surface
types and whose terms are surface terms (what the parser builds: no metavariable, skolem or `gen`),
the declarative judgment types it. -/
theorem checkProgram_sound {p : Program} {fuel : Nat} (hp : p.Surf)
    (h : checkProgram {} p fuel = .ok ()) : p.Typed := by
  unfold checkProgram at h
  rw [bind_ok_iff] at h
  obtain ⟨pl, hpl, h⟩ := h
  have hinv := checkPlan_inv hpl
  cases hrun : ((runPlan {} pl).run fuel).run {} with
  | error e => simp [hrun] at h
  | ok a =>
    obtain ⟨u, s'⟩ := a
    exact runPlan_sound hinv hp fuel u s' hrun

end SproutCore
