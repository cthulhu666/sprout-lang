import SproutCore.Judgment

/-!
# Inversion lemmas for the typing judgment

Small, general facts the proofs in `JudgmentExamples.lean` use: what a flow into or out of a base
type or a skolem must be, that a call's slot list has one slot per argument and is determined by
the callee's type, and what a typed one-declaration program says about its declaration.
-/

namespace SproutCore.Judgment

theorem Flows.int_inv {s : Ty} (h : Flows .int s) : s = .int := by
  cases h with
  | int => rfl
  | arr _ _ _ _ _ _ _ _ h1 => simp [Ty.uncons] at h1

theorem Flows.bool_inv {s : Ty} (h : Flows .bool s) : s = .bool := by
  cases h with
  | bool => rfl
  | arr _ _ _ _ _ _ _ _ h1 => simp [Ty.uncons] at h1

theorem Flows.skolem_inv {n : Nat} {s : Ty} (h : Flows (.skolem n) s) : s = .skolem n := by
  cases h with
  | skolem => rfl
  | arr _ _ _ _ _ _ _ _ h1 => simp [Ty.uncons] at h1

/-- Only a type variable flows into a type variable. -/
theorem Flows.to_skolem {n : Nat} {a : Ty} (h : Flows a (.skolem n)) : a = .skolem n := by
  cases h with
  | skolem => rfl
  | arr _ _ _ _ _ _ _ _ _ h2 => simp [Ty.uncons] at h2

theorem Flows.to_int {a : Ty} (h : Flows a .int) : a = .int := by
  cases h with
  | int => rfl
  | arr _ _ _ _ _ _ _ _ _ h2 => simp [Ty.uncons] at h2

theorem Flows.to_bool {a : Ty} (h : Flows a .bool) : a = .bool := by
  cases h with
  | bool => rfl
  | arr _ _ _ _ _ _ _ _ _ h2 => simp [Ty.uncons] at h2

theorem Peel.length {t : Ty} {n : Nat} {ss : List Ty} {r : Ty} {e : Eff}
    (h : Peel t n ss r e) : ss.length = n := by
  induction h with
  | exact => rfl
  | rest ps r e n hn => simp; omega
  | over ps r e n s2 r2 e2 hps hlt _ ih => simp [ih]; omega

theorem Peel.det {t : Ty} {n : Nat} {ss : List Ty} {r : Ty} {e : Eff}
    (h : Peel t n ss r e) :
    ∀ {ss' r' e'}, Peel t n ss' r' e' → ss = ss' ∧ r = r' ∧ e = e' := by
  induction h with
  | exact ps r e =>
    intro ss' r' e' h'
    cases h' with
    | exact => simp
    | rest _ _ _ _ hn => omega
    | over _ _ _ _ _ _ _ _ hlt _ => omega
  | rest ps r e n hn =>
    intro ss' r' e' h'
    cases h' with
    | exact => omega
    | rest => simp
    | over _ _ _ _ _ _ _ _ hlt _ => omega
  | over ps r e n s2 r2 e2 hps hlt _ ih =>
    intro ss' r' e' h'
    cases h' with
    | exact => omega
    | rest _ _ _ _ hn => omega
    | over _ _ _ _ s2' r2' e2' _ _ h2' =>
      obtain ⟨h1, h2, h3⟩ := ih h2'
      subst h1 h2 h3
      simp

theorem GroupTy.ne_nil {Γ Γ' : Ctx} {g : List Decl} (h : GroupTy Γ g Γ') : g ≠ [] := by
  cases h <;> simp_all

/-- No declarations, no groups. -/
theorem Groups.nil_inv {Γ Γ' : Ctx} (h : Groups Γ [] Γ') : Γ' = Γ := by
  cases h
  · rfl
  · rename_i Γ₁ g rest hg hp hr
    have hl := hp.length_eq
    have hg0 := hg.ne_nil
    cases g with
    | nil => exact absurd rfl hg0
    | cons _ _ => simp at hl

/-- A group that types the only declaration is a group of it. -/
theorem Groups.single {Γ Γ' : Ctx} {d : Decl} (h : Groups Γ [d] Γ') :
    ∃ Γ₁, GroupTy Γ [d] Γ₁ := by
  cases h
  rename_i Γ₁ g rest hg hp hr
  have hl := hp.length_eq
  have hg0 := hg.ne_nil
  have hrest : rest = [] := by
    cases g with
    | nil => exact absurd rfl hg0
    | cons _ gs =>
      cases rest with
      | nil => rfl
      | cons _ _ => simp at hl
  subst hrest
  have hgd : g = [d] := by simpa using List.perm_singleton.mp hp.symm
  subst hgd
  exact ⟨Γ₁, hg⟩

/-! ### Inversion, one lemma per construct -/

theorem HasType.var_inv {Γ : Ctx} {x : String} {t : Ty} {ε : Eff} (h : HasType Γ (.var x) t ε) :
    ∃ b, Γ.lookup x = some b ∧ b.Inst t ∧ ε = .pure := by
  cases h with
  | var _ _ b _ hl hi => exact ⟨b, hl, hi, rfl⟩

theorem HasType.app_inv {Γ : Ctx} {f : Expr} {args : List Expr} {t : Ty} {ε : Eff}
    (h : HasType Γ (.app f args) t ε) :
    ∃ (tf : Ty) (ef : Eff) (ts ss : List Ty) (ce εa : Eff),
      HasType Γ f tf ef ∧ ArityOk Γ f args.length ∧ Peel tf args.length ss t ce ∧
      HasExprs Γ args ts εa ∧ FlowsAll ts ss ∧ ε = ef.join (ce.join εa) := by
  cases h with
  | app _ _ _ tf ef ts ss _ ce εa h1 h2 h3 h4 h5 =>
    exact ⟨tf, ef, ts, ss, ce, εa, h1, h2, h3, h4, h5, rfl⟩

theorem HasType.ite_inv {Γ : Ctx} {c a b : Expr} {r : Ty} {ε : Eff}
    (h : HasType Γ (.ite c a b) r ε) :
    ∃ (ec ea eb : Eff) (ta tb : Ty),
      HasType Γ c .bool ec ∧ HasType Γ a ta ea ∧ HasType Γ b tb eb ∧ r.Solved ∧
      Join ta tb r ∧
      ε = ec.join (ea.join eb) := by
  cases h with
  | ite _ _ _ _ ec ea eb ta tb _ h1 h2 h3 h4 h5 =>
    exact ⟨ec, ea, eb, ta, tb, h1, h2, h3, h4, h5, rfl⟩

theorem HasType.letIn_inv {Γ : Ctx} {x : String} {rhs body : Expr} {t : Ty} {ε : Eff}
    (h : HasType Γ (.letIn x rhs body) t ε) :
    ∃ tr, HasType Γ rhs tr .pure ∧ HasType ((x, .local tr) :: Γ) body t ε := by
  cases h with
  | letIn _ _ _ _ tr _ _ h1 h2 => exact ⟨tr, h1, h2⟩

theorem HasType.doB_inv {Γ : Ctx} {steps : List Step} {final : Expr} {t : Ty} {ε : Eff}
    (h : HasType Γ (.doB steps final) t ε) :
    ∃ (Γ' : Ctx) (ε₁ ε₂ : Eff),
      HasSteps Γ steps Γ' ε₁ ∧ HasType Γ' final t ε₂ ∧ ε = ε₁.join ε₂ := by
  cases h with
  | doB _ Γ' _ _ _ ε₁ ε₂ h1 h2 => exact ⟨Γ', ε₁, ε₂, h1, h2, rfl⟩

theorem HasType.tup_inv {Γ : Ctx} {es : List Expr} {t : Ty} {ε : Eff}
    (h : HasType Γ (.tup es) t ε) : ∃ ts, t = .tup ts ∧ HasExprs Γ es ts ε := by
  cases h with
  | tup _ _ ts _ h1 => exact ⟨ts, rfl, h1⟩

/-- What a typed program of one `fn` says about it: a witness whose skolems for the written
variables are distinct, and whose body checks in the context holding itself and the prelude. -/
theorem single_fn_inv {name : String} {ps : List (String × Option Ty)} {sg : Option (Ty × Eff)}
    {b : Expr} (h : Program.Typed ⟨[.fn name ps sg b]⟩) :
    ∃ (σ : String → Ty) (τ : Ty) (sc : Scheme),
      (∀ v ∈ (FnD.mk name ps sg b).vars, ∃ k, σ v = .skolem k) ∧
      ((FnD.mk name ps sg b).vars.map σ).Nodup ∧
      MemberOk ([FnWit.entryIn ⟨⟨name, ps, sg, b⟩, σ, τ, sc⟩] ++ Ctx.prelude)
        ⟨⟨name, ps, sg, b⟩, σ, τ, sc⟩ := by
  obtain ⟨Γ, hg, _⟩ := h
  obtain ⟨Γ₁, hgt⟩ := Groups.single (d := .fn name ps sg b) hg
  generalize hd : [Decl.fn name ps sg b] = g at hgt
  cases hgt with
  | letG => simp at hd
  | fnG ws hne hsk hnd hgen hmem =>
    cases ws with
    | nil => exact absurd rfl hne
    | cons w ws' =>
      simp only [List.map_cons, List.cons.injEq] at hd
      obtain ⟨hw, hws'⟩ := hd
      have hws0 : ws' = [] := List.map_eq_nil_iff.mp hws'.symm
      subst hws0
      obtain ⟨⟨n', p', s', b'⟩, σ, τ, sc⟩ := w
      simp only [FnD.toDecl, Decl.fn.injEq] at hw
      obtain ⟨rfl, rfl, rfl, rfl⟩ := hw
      refine ⟨σ, τ, sc, ?_, ?_, ?_⟩
      · exact fun v hv => hsk _ (List.mem_singleton.mpr rfl) v hv
      · simpa using hnd
      · exact hmem _ (List.mem_singleton.mpr rfl)

/-- What a typed program of one top-level `let` says about it. -/
theorem single_let_inv {name : String} {ann : Option Ty} {init : Expr}
    (h : Program.Typed ⟨[.letD name ann init]⟩) :
    ∃ (t : Ty) (s : Scheme),
      HasType Ctx.prelude init t .pure ∧ LetAnn ann t ∧ Generalises t s := by
  obtain ⟨Γ, hg, _⟩ := h
  obtain ⟨Γ₁, hgt⟩ := Groups.single (d := .letD name ann init) hg
  generalize hd : [Decl.letD name ann init] = g at hgt
  cases hgt with
  | letG name' ann' init' t s h1 h2 h3 =>
    simp only [List.cons.injEq, Decl.letD.injEq, and_true] at hd
    obtain ⟨rfl, rfl, rfl⟩ := hd
    exact ⟨t, s, h1, h2, h3⟩
  | fnG ws hne hsk hnd hgen hmem =>
    cases ws with
    | nil => exact absurd rfl hne
    | cons w ws' =>
      simp only [List.map_cons, List.cons.injEq] at hd
      obtain ⟨hw, _⟩ := hd
      simp [FnD.toDecl] at hw

/-! ### Building derivations -/

/-- A program of singleton groups, in the order written. -/
theorem Groups.cons1 {Γ Γ₁ Γ₂ : Ctx} {d : Decl} {rest : List Decl}
    (hg : GroupTy Γ [d] Γ₁) (hr : Groups Γ₁ rest Γ₂) : Groups Γ (d :: rest) Γ₂ :=
  Groups.step Γ Γ₁ Γ₂ (d :: rest) [d] rest (List.Perm.refl _) hg hr

/-- A group of one `fn`. -/
theorem GroupTy.fn1 {Γ : Ctx} (w : FnWit)
    (hsk : ∀ v ∈ w.f.vars, ∃ n, w.σ v = .skolem n) (hnd : (w.f.vars.map w.σ).Nodup)
    (hgen : Generalises w.τ w.s) (hmem : MemberOk ([w.entryIn] ++ Γ) w) :
    GroupTy Γ [w.f.toDecl] ([w.entryOut] ++ Γ) := by
  refine GroupTy.fnG Γ [w] (by simp) ?_ ?_ ?_ ?_
  · intro w' hw' v hv
    simp at hw'
    subst hw'
    exact hsk v hv
  · simpa using hnd
  · intro w' hw'
    simp at hw'
    subst hw'
    exact hgen
  · intro w' hw'
    simp at hw'
    subst hw'
    simpa using hmem

/-- A group of one `fn` with no written type variable. -/
theorem GroupTy.fn1_novars {Γ : Ctx} (w : FnWit) (hv : w.f.vars = [])
    (hgen : Generalises w.τ w.s) (hmem : MemberOk ([w.entryIn] ++ Γ) w) :
    GroupTy Γ [w.f.toDecl] ([w.entryOut] ++ Γ) :=
  GroupTy.fn1 w (by simp [hv]) (by simp [hv]) hgen hmem

/-- No `main`, and every declaration in the groups: the program is typed. -/
theorem Program.typed_nomain {p : Program} {Γ : Ctx} (hg : Groups Ctx.prelude p.nodes Γ)
    (hm : ∀ sg body, Decl.mainFn sg body ∉ p.decls) : p.Typed :=
  ⟨Γ, hg, fun sg body h => absurd h (hm sg body)⟩

end SproutCore.Judgment
