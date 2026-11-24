import SproutCore.CheckFuel
import SproutCore.Judgment

/-!
# Well-formedness of the checker's state and environment (docs/lean-model-v0.md §9, 1b)

The invariants the soundness proof (stage 3a) runs under, and the lemmas that make `Check.lean`'s
`!` accessors safe. Nothing here changes the checker.

* `Ty.WF sz g t`: every metavariable of `t` is below `sz` and every `gen` below `g`. A type the
  checker works on is `Ty.WF s.sz 0`: in range, no `gen` (those live in schemes only).
* `CState.WF s`: every bound entry of `s.subst` is such a type, and the bindings are **acyclic**:
  `Dep s m n` ("`n` is bound to a type that mentions `m`") has no infinite descending chain. That is
  the occurs-check invariant; `bindMeta` keeps it because the type it binds is zonked, so mentions
  only unbound variables, and an unbound variable has nothing below it.
* `Env.WF sz env`: a `mono`/`fnMono` type is `Ty.WF sz 0`, a `poly n`/`fnPoly _ n` type is
  `Ty.WF sz n`.
* `Expr.Src`, `FnInfo.Src`, `Plan.Src`: a program's written types are `Ty.WF 0 0` (no `mv`, no
  `gen`), which is what the parser produces and what an end-to-end theorem assumes.

Preservation is stated with `Wp`, a weakest-precondition predicate over `M`. A leaf lemma has the
shape `pre → (∀ a s', post → Q a s') → Wp x Q s`, so a proof walks a `do` block forward and the
facts about earlier states stay in the context; `repeat' wp_step` does the walking, and `wf_close`
closes the side goals. Every function of the checker that touches the state has a lemma: `fresh`,
`freshSkolem`, `bindMeta`, `shallowF`, `zonkF`, `flowF`, `joinTyF`, `peelF`, `instantiate`,
`generalize`, `envMetas`, `elabTy`, `lamParam`, `bindLocal`, `infer` and its helpers, `build`,
`scheme`, `checkBody`, `inferLet`, `inferFnGroup`, `runPlan`. Each keeps `CState.WF`, never
shrinks the state, and returns types that are `Ty.WF` at the final size (and environments that are
`Env.WF` there).
-/

namespace SproutCore

/-! ### Ranges of metavariables and generic variables -/

mutual
def gensOf : Ty → List Nat
  | .gen i => [i]
  | .tup ts => gensOfList ts
  | .arr ps r _ => gensOfList ps ++ gensOf r
  | .adt _ ts => gensOfList ts
  | _ => []

def gensOfList : List Ty → List Nat
  | [] => []
  | t :: ts => gensOf t ++ gensOfList ts
end

theorem mem_metasOfList {m : Nat} {ts : List Ty} :
    m ∈ metasOfList ts ↔ ∃ t ∈ ts, m ∈ metasOf t := by
  induction ts with
  | nil => simp [metasOfList]
  | cons t ts ih => simp [metasOfList, ih]

theorem mem_gensOfList {m : Nat} {ts : List Ty} :
    m ∈ gensOfList ts ↔ ∃ t ∈ ts, m ∈ gensOf t := by
  induction ts with
  | nil => simp [gensOfList]
  | cons t ts ih => simp [gensOfList, ih]

/-- Every metavariable of `t` is below `sz`, every `gen` below `g`. -/
def Ty.WF (sz g : Nat) (t : Ty) : Prop :=
  (∀ m ∈ metasOf t, m < sz) ∧ (∀ i ∈ gensOf t, i < g)

theorem Ty.WF.mono {sz g sz' g' : Nat} {t : Ty} (h : Ty.WF sz g t) (hs : sz ≤ sz')
    (hg : g ≤ g') : Ty.WF sz' g' t :=
  ⟨fun m hm => Nat.lt_of_lt_of_le (h.1 m hm) hs, fun i hi => Nat.lt_of_lt_of_le (h.2 i hi) hg⟩

@[simp] theorem Ty.WF_mv {sz g n : Nat} : Ty.WF sz g (.mv n) ↔ n < sz := by
  simp [Ty.WF, metasOf, gensOf]

@[simp] theorem Ty.WF_gen {sz g n : Nat} : Ty.WF sz g (.gen n) ↔ n < g := by
  simp [Ty.WF, metasOf, gensOf]

@[simp] theorem Ty.WF_skolem {sz g n : Nat} : Ty.WF sz g (.skolem n) := by
  simp [Ty.WF, metasOf, gensOf]

@[simp] theorem Ty.WF_named {sz g : Nat} {s : String} : Ty.WF sz g (.named s) := by
  simp [Ty.WF, metasOf, gensOf]

@[simp] theorem Ty.WF_int {sz g : Nat} : Ty.WF sz g .int := by simp [Ty.WF, metasOf, gensOf]
@[simp] theorem Ty.WF_bool {sz g : Nat} : Ty.WF sz g .bool := by simp [Ty.WF, metasOf, gensOf]
@[simp] theorem Ty.WF_str {sz g : Nat} : Ty.WF sz g .str := by simp [Ty.WF, metasOf, gensOf]
@[simp] theorem Ty.WF_unit {sz g : Nat} : Ty.WF sz g .unit := by simp [Ty.WF, metasOf, gensOf]

@[simp] theorem Ty.WF_tup {sz g : Nat} {ts : List Ty} :
    Ty.WF sz g (.tup ts) ↔ ∀ t ∈ ts, Ty.WF sz g t := by
  simp only [Ty.WF, metasOf, gensOf, mem_metasOfList, mem_gensOfList]
  constructor
  · intro h t ht
    exact ⟨fun m hm => h.1 m ⟨t, ht, hm⟩, fun i hi => h.2 i ⟨t, ht, hi⟩⟩
  · intro h
    exact ⟨fun m ⟨t, ht, hm⟩ => (h t ht).1 m hm, fun i ⟨t, ht, hi⟩ => (h t ht).2 i hi⟩

@[simp] theorem Ty.WF_arr {sz g : Nat} {ps : List Ty} {r : Ty} {e : Eff} :
    Ty.WF sz g (.arr ps r e) ↔ (∀ t ∈ ps, Ty.WF sz g t) ∧ Ty.WF sz g r := by
  simp only [Ty.WF, metasOf, gensOf, List.mem_append, mem_metasOfList, mem_gensOfList]
  constructor
  · intro h
    exact ⟨fun t ht => ⟨fun m hm => h.1 m (Or.inl ⟨t, ht, hm⟩),
        fun i hi => h.2 i (Or.inl ⟨t, ht, hi⟩)⟩,
      fun m hm => h.1 m (Or.inr hm), fun i hi => h.2 i (Or.inr hi)⟩
  · rintro ⟨h1, h2, h3⟩
    exact ⟨fun m hm => hm.elim (fun ⟨t, ht, hm⟩ => (h1 t ht).1 m hm) (h2 m),
      fun i hi => hi.elim (fun ⟨t, ht, hi⟩ => (h1 t ht).2 i hi) (h3 i)⟩

@[simp] theorem Ty.WF_adt {sz g : Nat} {n : String} {ts : List Ty} :
    Ty.WF sz g (.adt n ts) ↔ ∀ t ∈ ts, Ty.WF sz g t := by
  simp only [Ty.WF, metasOf, gensOf, mem_metasOfList, mem_gensOfList]
  constructor
  · intro h t ht
    exact ⟨fun m hm => h.1 m ⟨t, ht, hm⟩, fun i hi => h.2 i ⟨t, ht, hi⟩⟩
  · intro h
    exact ⟨fun m ⟨t, ht, hm⟩ => (h t ht).1 m hm, fun i ⟨t, ht, hi⟩ => (h t ht).2 i hi⟩

/-! ### Weakest preconditions over the checker monad -/

abbrev CState.sz (s : CState) : Nat := s.subst.size

/-- Every successful run of `x` from `s`, at any budget, ends in a state and with a result that
satisfy `Q`. Failure is not constrained. -/
def Wp {α : Type} (x : M α) (Q : α → CState → Prop) (s : CState) : Prop :=
  ∀ n a s', x.go n s = .ok (a, s') → Q a s'

theorem Wp.mono {α : Type} {x : M α} {Q Q' : α → CState → Prop} {s : CState} (h : Wp x Q s)
    (hq : ∀ a s', Q a s' → Q' a s') : Wp x Q' s :=
  fun n a s' hr => hq a s' (h n a s' hr)

theorem Wp.bind {α β : Type} {x : M α} {f : α → M β} {Q : β → CState → Prop} {s : CState}
    (h : Wp x (fun a s1 => Wp (f a) Q s1) s) : Wp (x >>= f) Q s := by
  intro n b s2 hr
  rw [go_bind] at hr
  cases hx : x.go n s with
  | error e => rw [hx] at hr; cases hr
  | ok p =>
    rw [hx] at hr
    exact h n p.1 p.2 hx n b s2 hr

theorem Wp.pure' {α : Type} {a : α} {Q : α → CState → Prop} {s : CState} (h : Q a s) :
    Wp (pure a : M α) Q s := by
  intro n b s' hr
  have h2 : (Except.ok (a, s) : Except CErr (α × CState)) = .ok (b, s') := hr
  cases h2; exact h

theorem Wp.get' {Q : CState → CState → Prop} {s : CState} (h : Q s s) :
    Wp (get : M CState) Q s := by
  intro n b s' hr
  have h2 : (Except.ok (s, s) : Except CErr (CState × CState)) = .ok (b, s') := hr
  cases h2; exact h

theorem Wp.read' {Q : Nat → CState → Prop} {s : CState} (h : ∀ n, Q n s) :
    Wp (read : M Nat) Q s := by
  intro n b s' hr
  have h2 : (Except.ok (n, s) : Except CErr (Nat × CState)) = .ok (b, s') := hr
  cases h2; exact h n

theorem Wp.set' {Q : PUnit → CState → Prop} {s s' : CState} (h : Q ⟨⟩ s') :
    Wp (set s' : M PUnit) Q s := by
  intro n b t hr
  have h2 : (Except.ok (⟨⟩, s') : Except CErr (PUnit × CState)) = .ok (b, t) := hr
  cases h2; exact h

theorem Wp.modify' {f : CState → CState} {Q : PUnit → CState → Prop} {s : CState}
    (h : Q ⟨⟩ (f s)) : Wp (modify f : M PUnit) Q s := by
  intro n b t hr
  have h2 : (Except.ok (⟨⟩, f s) : Except CErr (PUnit × CState)) = .ok (b, t) := hr
  cases h2; exact h

theorem Wp.reject' {α : Type} (key detail : String) {Q : α → CState → Prop} {s : CState} :
    Wp (reject key detail : M α) Q s := by
  intro n b s' hr; cases hr

theorem Wp.fuel' {α : Type} {Q : α → CState → Prop} {s : CState} :
    Wp (throw .fuel : M α) Q s := by
  intro n b s' hr; cases hr

theorem Wp.tryCatch' {α : Type} {x : M α} {h : CErr → M α} {Q : α → CState → Prop} {s : CState}
    (hx : Wp x Q s) (hh : ∀ k d, Wp (h (.reject k d)) Q s) (hf : h .fuel = throw .fuel) :
    Wp (tryCatch x h) Q s := by
  intro n a s' hr
  change (MonadExcept.tryCatch (x.go n s) fun e => (h e).go n s) = _ at hr
  cases hxn : x.go n s with
  | ok p =>
    rw [hxn] at hr
    have h2 : (Except.ok p : Except CErr (α × CState)) = .ok (a, s') := hr
    cases h2
    exact hx n _ _ hxn
  | error e =>
    rw [hxn] at hr
    cases e with
    | fuel =>
      have h2 : (h .fuel).go n s = .ok (a, s') := hr
      rw [hf] at h2; cases h2
    | reject k d => exact hh k d n a s' hr

theorem Wp.map' {α β : Type} {x : M α} (f : α → β) {Q : β → CState → Prop} {s : CState}
    (h : Wp x (fun a s' => Q (f a) s') s) : Wp (f <$> x) Q s := by
  rw [map_eq_pure_bind]
  exact Wp.bind (Wp.mono h fun a s' hq => Wp.pure' hq)

/-- A loop over a list whose invariant also sees the prefix already processed. A step that ends
the loop (`done b'`) must reach the goal `Q` outright; a step that goes on (`yield b'`) must keep
the invariant on the longer prefix. -/
theorem Wp.forIn_prefix_done {α β : Type} {f : α → β → M (ForInStep β)}
    {Q : β → CState → Prop} (I : List α → β → CState → Prop) (l : List α) (init : β) {s : CState}
    (h0 : I [] init s)
    (hf : ∀ pre a post b s1, pre ++ a :: post = l → I pre b s1 →
      Wp (f a b) (fun r s2 => match r with
        | .done b' => Q b' s2
        | .yield b' => I (pre ++ [a]) b' s2) s1)
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
      intro r s2 hr
      cases r with
      | done b' => exact Wp.pure' hr
      | yield b' => exact ih (pre ++ [a]) b' s2 (by simpa using hl) hr
  exact gen l [] init s (by simp) h0

/-- `Wp.forIn_prefix_done` for a loop no step of which ends early (`done` is not reachable). -/
theorem Wp.forIn_prefix {α β : Type} {f : α → β → M (ForInStep β)} {Q : β → CState → Prop}
    (I : List α → β → CState → Prop) (l : List α) (init : β) {s : CState} (h0 : I [] init s)
    (hf : ∀ pre a post b s1, pre ++ a :: post = l → I pre b s1 →
      Wp (f a b) (fun r s2 => ∃ b', r = ForInStep.yield b' ∧ I (pre ++ [a]) b' s2) s1)
    (hq : ∀ b s1, I l b s1 → Q b s1) : Wp (ForIn.forIn l init f) Q s :=
  Wp.forIn_prefix_done I l init h0
    (fun pre a post b s1 hl hI => Wp.mono (hf pre a post b s1 hl hI)
      fun r s2 ⟨b', hr, hI'⟩ => by subst hr; exact hI')
    hq

/-- A loop over a list, with an invariant on the loop variable and the state. -/
theorem Wp.forIn_list {α β : Type} {f : α → β → M (ForInStep β)} {Q : β → CState → Prop}
    (I : β → CState → Prop) (l : List α) (init : β) {s : CState} (h0 : I init s)
    (hf : ∀ a ∈ l, ∀ b s1, I b s1 →
      Wp (f a b) (fun r s2 => match r with | .done b' => I b' s2 | .yield b' => I b' s2) s1)
    (hq : ∀ b s1, I b s1 → Q b s1) :
    Wp (ForIn.forIn l init f) Q s :=
  Wp.forIn_prefix_done (fun _ => I) l init h0
    (fun pre a _ b s1 hl hI => Wp.mono
      (hf a (hl ▸ List.mem_append_right pre (List.mem_cons_self ..)) b s1 hI)
      fun r _ hr => match r, hr with
        | .done _, hr => hq _ _ hr
        | .yield _, hr => hr)
    hq

theorem Wp.forIn_range {β : Type} {f : Nat → β → M (ForInStep β)} {Q : β → CState → Prop}
    (I : β → CState → Prop) (r : Std.Legacy.Range) (init : β) {s : CState} (h0 : I init s)
    (hf : ∀ a b s1, I b s1 →
      Wp (f a b) (fun r s2 => match r with | .done b' => I b' s2 | .yield b' => I b' s2) s1)
    (hq : ∀ b s1, I b s1 → Q b s1) :
    Wp (ForIn.forIn r init f) Q s := by
  simp only [Std.Legacy.Range.forIn_eq_forIn_range']
  exact Wp.forIn_list I _ init h0 (fun a _ => hf a) hq

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

theorem All2.forall_right {α β : Type} {R : α → β → Prop} {P : β → Prop}
    (h : ∀ a b, R a b → P b) : ∀ {as : List α} {bs : List β}, All2 R as bs → ∀ b ∈ bs, P b
  | _, _, All2.nil, _, hb => by simp at hb
  | _, _, All2.cons h1 h2, b, hb => by
    rcases List.mem_cons.mp hb with rfl | hb
    · exact h _ _ h1
    · exact All2.forall_right h h2 b hb

/-- `mapM`, with an invariant `I` on the state and a relation `R` between each input and its result,
kept true along `Step`, a reflexive and transitive relation on states: `sz ≤` for the invariants of
this file, `Ext` for the soundness layer. -/
theorem Wp.mapM_rel {α β : Type} {f : α → M β} {l : List α} {s : CState}
    (Step : CState → CState → Prop) (hrefl : ∀ s, Step s s)
    (htrans : ∀ a b c, Step a b → Step b c → Step a c)
    (I : CState → Prop) (R : CState → α → β → Prop)
    (hR : ∀ s1 s2 a b, Step s1 s2 → R s1 a b → R s2 a b) (h0 : I s)
    (hf : ∀ a ∈ l, ∀ s1, I s1 → Step s s1 →
      Wp (f a) (fun b s2 => I s2 ∧ Step s1 s2 ∧ R s2 a b) s1) :
    Wp (l.mapM f) (fun bs s2 => I s2 ∧ Step s s2 ∧ All2 (R s2) l bs) s := by
  induction l generalizing s with
  | nil =>
    simp only [List.mapM_nil]
    exact Wp.pure' ⟨h0, hrefl _, All2.nil⟩
  | cons a l ih =>
    simp only [List.mapM_cons]
    refine Wp.bind ?_
    refine Wp.mono (hf a (List.mem_cons_self ..) s h0 (hrefl _)) fun b s1 ⟨i1, e1, r1⟩ => ?_
    refine Wp.bind ?_
    refine Wp.mono (ih (s := s1) i1 (fun a ha s2 i2 e2 => ?_)) fun bs s2 ⟨i2, e2, r2⟩ => ?_
    · exact Wp.mono (hf a (List.mem_cons_of_mem _ ha) s2 i2 (htrans _ _ _ e1 e2))
        fun b s3 ⟨i3, e3, r3⟩ => ⟨i3, e3, r3⟩
    · exact Wp.pure' ⟨i2, htrans _ _ _ e1 e2, All2.cons (hR _ _ _ _ e2 r1) r2⟩

/-- `mapM`, with an invariant on the state and a predicate on each result. -/
theorem Wp.mapM {α β : Type} {f : α → M β} {l : List α} {s : CState}
    (I : CState → Prop) (P : CState → β → Prop)
    (hP : ∀ s1 s2 b, s1.sz ≤ s2.sz → P s1 b → P s2 b) (h0 : I s)
    (hf : ∀ a ∈ l, ∀ s1, I s1 → s.sz ≤ s1.sz →
      Wp (f a) (fun b s2 => I s2 ∧ s1.sz ≤ s2.sz ∧ P s2 b) s1) :
    Wp (l.mapM f) (fun bs s2 => I s2 ∧ s.sz ≤ s2.sz ∧ ∀ b ∈ bs, P s2 b) s :=
  Wp.mono (Wp.mapM_rel (fun a b : CState => a.sz ≤ b.sz) (fun _ => Nat.le_refl _)
    (fun _ _ _ => Nat.le_trans) I (fun s1 _ b => P s1 b) (fun s1 s2 _ b g => hP s1 s2 b g) h0 hf)
    fun _ s2 ⟨i, g, r⟩ => ⟨i, g, All2.forall_right (P := P s2) (fun _ _ h => h) r⟩

/-- A loop over a list whose body is a unit computation, with an invariant `I` on the state and a
fact `R` about each element, kept true along `Step` (as in `Wp.mapM_rel`). -/
theorem Wp.forIn_unit_rel {α : Type} {body : α → M PUnit} {l : List α} {s : CState}
    (Step : CState → CState → Prop) (hrefl : ∀ s, Step s s)
    (htrans : ∀ a b c, Step a b → Step b c → Step a c)
    (I : CState → Prop) (R : CState → α → Prop) (hR : ∀ s1 s2 a, Step s1 s2 → R s1 a → R s2 a)
    (h0 : I s)
    (hb : ∀ a ∈ l, ∀ s1, I s1 → Step s s1 →
      Wp (body a) (fun _ s2 => I s2 ∧ Step s1 s2 ∧ R s2 a) s1) :
    Wp (forIn l PUnit.unit fun x _ => do body x; pure (ForInStep.yield PUnit.unit))
      (fun _ s2 => I s2 ∧ Step s s2 ∧ ∀ a ∈ l, R s2 a) s := by
  induction l generalizing s with
  | nil =>
    simp only [List.forIn_nil]
    exact Wp.pure' ⟨h0, hrefl _, by simp⟩
  | cons a l ih =>
    simp only [List.forIn_cons]
    refine Wp.bind ?_
    refine Wp.bind (Wp.mono (hb a (List.mem_cons_self ..) s h0 (hrefl _)) ?_)
    intro u s1 ⟨i1, e1, r1⟩
    refine Wp.pure' ?_
    refine Wp.mono (ih (s := s1) i1 (fun a ha s2 i2 e2 => ?_)) fun u s2 ⟨i2, e2, r2⟩ => ?_
    · exact Wp.mono (hb a (List.mem_cons_of_mem _ ha) s2 i2 (htrans _ _ _ e1 e2))
        fun u s3 ⟨i3, e3, r3⟩ => ⟨i3, e3, r3⟩
    · refine ⟨i2, htrans _ _ _ e1 e2, ?_⟩
      intro c hc
      rcases List.mem_cons.mp hc with rfl | hc
      · exact hR _ _ _ e2 r1
      · exact r2 c hc

/-! ### The state invariant -/

/-- `n` is bound to a type that mentions `m`. -/
def CState.Dep (s : CState) (m n : Nat) : Prop :=
  ∃ t, s.subst[n]? = some (some t) ∧ m ∈ metasOf t

/-- `m` has no binding. -/
def CState.Unbound (s : CState) (m : Nat) : Prop := ∀ t, s.subst[m]? ≠ some (some t)

/-- The state invariant. `range`: a bound entry is a type in range, with no `gen`. `acyc`: following
bindings from any variable terminates, which is the occurs check's invariant (Naraschewski and
Nipkow's fresh-variable invariant for Algorithm W): `Dep s m n` has no infinite descent. -/
structure CState.WF (s : CState) : Prop where
  range : ∀ (n : Nat) (t : Ty), s.subst[n]? = some (some t) → Ty.WF s.sz 0 t
  acyc : ∀ n : Nat, Acc s.Dep n

theorem CState.WF.init : CState.WF {} := by
  refine ⟨fun n t h => by simp at h, fun n => Acc.intro n fun m ⟨t, h, _⟩ => by simp at h⟩

/-- Only the substitution matters: the skolem counter is free. -/
theorem CState.WF.of_subst {s s' : CState} (hs : s.WF) (h : s'.subst = s.subst) : s'.WF := by
  obtain ⟨sub, sk⟩ := s
  obtain ⟨sub', sk'⟩ := s'
  simp only at h
  subst h
  exact ⟨fun n t h => hs.range n t h, fun n => hs.acyc n⟩

/-- A fresh variable keeps the invariant: the new entry is unbound. -/
theorem CState.WF.push {s s' : CState} (hs : s.WF) (h : s'.subst = s.subst.push none) :
    s'.WF := by
  have hget : ∀ (n : Nat) (t : Ty), s'.subst[n]? = some (some t) → s.subst[n]? = some (some t) := by
    intro n t ht
    rw [h, Array.getElem?_push] at ht
    split at ht <;> simp_all
  have hsz : s'.sz = s.sz + 1 := by simp [CState.sz, h]
  refine ⟨fun n t ht => ?_, fun n => ?_⟩
  · exact (hs.range n t (hget n t ht)).mono (by omega) (Nat.le_refl _)
  · have hdep : ∀ m k, s'.Dep m k → s.Dep m k := fun m k ⟨t, ht, hm⟩ => ⟨t, hget k t ht, hm⟩
    have hn := hs.acyc n
    induction hn with
    | intro x _ ih => exact Acc.intro x fun m hm => ih m (hdep m x hm)

/-- Binding `n` to `t` keeps the invariant when `t` mentions only unbound variables other than `n`:
the new entry then points only at variables with nothing below them. -/
theorem CState.WF.bind {s s' : CState} (hs : s.WF) {n : Nat} {t : Ty} (hn : n < s.sz)
    (ht : Ty.WF s.sz 0 t) (hu : ∀ m ∈ metasOf t, s.Unbound m) (hocc : n ∉ metasOf t)
    (h : s'.subst = s.subst.set! n (some t)) : s'.WF := by
  have hget : ∀ j : Nat, s'.subst[j]? = if n = j then some (some t) else s.subst[j]? := by
    intro j
    rw [h, Array.set!_eq_setIfInBounds, Array.getElem?_setIfInBounds]
    simp [hn]
  have hsz : s'.sz = s.sz := by simp [CState.sz, h]
  have hunb : ∀ m ∈ metasOf t, ∀ t0, ¬ s'.subst[m]? = some (some t0) := by
    intro m hm t0 h0
    rw [hget] at h0
    have hne : n ≠ m := fun e => hocc (e ▸ hm)
    simp only [hne, ite_false] at h0
    exact hu m hm t0 h0
  have hacc : ∀ m ∈ metasOf t, Acc s'.Dep m := fun m hm =>
    Acc.intro m fun y ⟨t0, ht0, _⟩ => absurd ht0 (hunb m hm t0)
  refine ⟨fun j t0 hj => ?_, fun x => ?_⟩
  · rw [hget] at hj
    split at hj
    · cases hj; exact ht.mono (by omega) (Nat.le_refl _)
    · exact (hs.range j t0 hj).mono (by omega) (Nat.le_refl _)
  · have hx := hs.acyc x
    induction hx with
    | intro x _ ih =>
      by_cases hxn : n = x
      · subst hxn
        refine Acc.intro n fun y ⟨t0, ht0, hy⟩ => ?_
        simp only [hget, ite_true, Option.some.injEq] at ht0
        subst ht0
        exact hacc y hy
      · refine Acc.intro x fun y ⟨t0, ht0, hy⟩ => ih y ⟨t0, ?_, hy⟩
        simp only [hget, hxn, ite_false] at ht0
        exact ht0

/-! ### Proof automation

`wp_tac` walks a `do` block forward. A leaf lemma is
`pre → (∀ a s', post → Q a s') → Wp x Q s`, its continuation hypothesis named `h`; `wp_step`
applies one, closes the preconditions with `wf_close`, and introduces the postconditions. Later
`macro_rules` add leaf lemmas. `wf_close` is tried before the continuation is introduced: a
pre-goal's tag can carry the prefix `h`, which `case' h` would take for the continuation. -/

syntax "wf_close" : tactic
syntax "wp_step" : tactic
syntax "wp_tac" : tactic

macro_rules
  | `(tactic| wp_tac) => `(tactic| repeat' wp_step)

macro_rules
  | `(tactic| wf_close) => `(tactic| assumption)
macro_rules
  | `(tactic| wf_close) => `(tactic| omega)
macro_rules
  | `(tactic| wf_close) => `(tactic| (refine Ty.WF.mono ‹_› ?_ ?_ <;> omega))

macro_rules
  | `(tactic| wp_step) => `(tactic| split)
macro_rules
  | `(tactic| wp_step) => `(tactic| dsimp only)
macro_rules
  | `(tactic| wp_step) => `(tactic| exact Wp.reject' _ _)
macro_rules
  | `(tactic| wp_step) => `(tactic| exact Wp.fuel')
macro_rules
  | `(tactic| wp_step) => `(tactic| with_reducible refine Wp.pure' ?_)
macro_rules
  | `(tactic| wp_step) => `(tactic| with_reducible refine Wp.modify' ?_)
macro_rules
  | `(tactic| wf_close) => `(tactic| exact Ty.WF_bool)
macro_rules
  | `(tactic| wf_close) => `(tactic| exact Ty.WF_int)
macro_rules
  | `(tactic| wf_close) => `(tactic| exact Ty.WF_str)
macro_rules
  | `(tactic| wf_close) => `(tactic| exact Ty.WF_unit)
macro_rules
  | `(tactic| wf_close) => `(tactic| exact Ty.WF_skolem)
macro_rules
  | `(tactic| wf_close) => `(tactic| exact Ty.WF_named)
macro_rules
  | `(tactic| wf_close) => `(tactic| exact Ty.WF_mv.mp ‹_›)
macro_rules
  | `(tactic| wf_close) => `(tactic| (simp only [Ty.WF_mv] at *; omega))
macro_rules
  | `(tactic| wp_step) => `(tactic| with_reducible refine Wp.bind ?_)
macro_rules
  | `(tactic| wp_step) => `(tactic| with_reducible refine Wp.map' _ ?_)

/-- `leaf lem`: apply the leaf lemma, close its preconditions, introduce its postconditions. -/
macro "leaf " lem:term : tactic =>
  `(tactic| ((with_reducible apply $lem);
             (all_goals first | wf_close | (case' h => (intro _ _; intros; try subst_vars)))))

/-! The pieces of a type in range, at a larger size. The conclusion fixes the part of the arrow
or tuple, so `‹_›` finds the hypothesis to take it from. -/

theorem Ty.WF.arr_res {sz sz' : Nat} {ps : List Ty} {r : Ty} {e : Eff}
    (h : Ty.WF sz 0 (.arr ps r e)) (hs : sz ≤ sz') : Ty.WF sz' 0 r :=
  ((Ty.WF_arr.mp h).2).mono hs (Nat.le_refl _)

theorem Ty.WF.arr_drop {sz sz' : Nat} {ps : List Ty} {r : Ty} {e : Eff}
    (h : Ty.WF sz 0 (.arr ps r e)) (hs : sz ≤ sz') (m : Nat) :
    Ty.WF sz' 0 (.arr (ps.drop m) r e) :=
  Ty.WF_arr.mpr
    ⟨fun t ht => ((Ty.WF_arr.mp h).1 t (List.mem_of_mem_drop ht)).mono hs (Nat.le_refl _),
      ((Ty.WF_arr.mp h).2).mono hs (Nat.le_refl _)⟩

theorem Ty.WF.arr_take {sz sz' : Nat} {ps : List Ty} {r : Ty} {e : Eff}
    (h : Ty.WF sz 0 (.arr ps r e)) (hs : sz ≤ sz') (m : Nat) :
    ∀ t ∈ ps.take m, Ty.WF sz' 0 t :=
  fun t ht => ((Ty.WF_arr.mp h).1 t (List.mem_of_mem_take ht)).mono hs (Nat.le_refl _)

theorem Ty.WF.arr_params {sz sz' : Nat} {ps : List Ty} {r : Ty} {e : Eff}
    (h : Ty.WF sz 0 (.arr ps r e)) (hs : sz ≤ sz') : ∀ t ∈ ps, Ty.WF sz' 0 t :=
  fun t ht => ((Ty.WF_arr.mp h).1 t ht).mono hs (Nat.le_refl _)

theorem Ty.WF.tup_params {sz sz' : Nat} {ts : List Ty} (h : Ty.WF sz 0 (.tup ts))
    (hs : sz ≤ sz') : ∀ t ∈ ts, Ty.WF sz' 0 t :=
  fun t ht => ((Ty.WF_tup.mp h) t ht).mono hs (Nat.le_refl _)

macro_rules
  | `(tactic| wf_close) => `(tactic| (refine Ty.WF.arr_res ‹_› ?_; omega))
macro_rules
  | `(tactic| wf_close) => `(tactic| (refine Ty.WF.arr_drop ‹_› ?_ _; omega))
macro_rules
  | `(tactic| wf_close) => `(tactic| (refine Ty.WF.arr_take ‹_› ?_ _; omega))
macro_rules
  | `(tactic| wf_close) => `(tactic| (refine Ty.WF.arr_params ‹_› ?_; omega))
theorem Ty.WF.adt_params {sz sz' : Nat} {n : String} {ts : List Ty}
    (h : Ty.WF sz 0 (.adt n ts)) (hs : sz ≤ sz') : ∀ t ∈ ts, Ty.WF sz' 0 t :=
  fun t ht => ((Ty.WF_adt.mp h) t ht).mono hs (Nat.le_refl _)

macro_rules
  | `(tactic| wf_close) => `(tactic| (refine Ty.WF.tup_params ‹_› ?_; omega))
macro_rules
  | `(tactic| wf_close) => `(tactic| (refine Ty.WF.adt_params ‹_› ?_; omega))

/-- A loop whose body is a unit computation that preserves the invariant. -/
theorem Wp.forIn_unit {α : Type} {body : α → M PUnit} {l : List α} {Q : PUnit → CState → Prop}
    {s : CState} (hs : s.WF)
    (hb : ∀ a ∈ l, ∀ s1, s1.WF → s.sz ≤ s1.sz →
      Wp (body a) (fun _ s2 => s2.WF ∧ s1.sz ≤ s2.sz) s1)
    (h : ∀ s', s.sz ≤ s'.sz → s'.WF → Q ⟨⟩ s') :
    Wp (forIn l PUnit.unit fun x _ => do body x; pure (ForInStep.yield PUnit.unit)) Q s :=
  Wp.mono (Wp.forIn_unit_rel (fun a b : CState => a.sz ≤ b.sz) (fun _ => Nat.le_refl _)
    (fun _ _ _ => Nat.le_trans) (fun s1 => s1.WF) (fun _ _ => True) (fun _ _ _ _ _ => trivial) hs
    (fun a ha s1 i1 g1 => Wp.mono (hb a ha s1 i1 g1) fun _ _ ⟨w, g⟩ => ⟨w, g, trivial⟩))
    fun _ s2 ⟨i, g, _⟩ => h s2 g i

/-! ### Named-free types

`Ty.NF t`: no `.named` anywhere in `t`. The checker never builds one: `elabTy` maps each written
variable away, and every other function builds types from the ones it is given. A `.named` in the
start state or environment is kept, and `Ty.ok` rejects it, so no grounding of it is solved. The
soundness layer (`Subst.lean` on) takes `NF` of the start state and environment as hypotheses and
proves that `infer` keeps it. -/

def Ty.NF (t : Ty) : Prop := namedIn t = []

/-- Every bound entry of the substitution is named-free. -/
def CState.NF (s : CState) : Prop :=
  ∀ (n : Nat) (t : Ty), s.subst[n]? = some (some t) → Ty.NF t

theorem CState.NF.init : CState.NF {} := fun n t h => by simp at h

theorem namedInList_eq_nil {ts : List Ty} : namedInList ts = [] ↔ ∀ t ∈ ts, namedIn t = [] := by
  induction ts with
  | nil => simp [namedInList]
  | cons t ts ih => simp [namedInList, ih]

theorem namedInList_append (a b : List Ty) :
    namedInList (a ++ b) = namedInList a ++ namedInList b := by
  induction a with
  | nil => simp [namedInList]
  | cons t a ih => simp [namedInList, ih, List.append_assoc]

@[simp] theorem Ty.not_NF_named {s : String} : ¬ Ty.NF (.named s) := by simp [Ty.NF, namedIn]
@[simp] theorem Ty.NF_mv {n : Nat} : Ty.NF (.mv n) := rfl
@[simp] theorem Ty.NF_skolem {n : Nat} : Ty.NF (.skolem n) := rfl
@[simp] theorem Ty.NF_int : Ty.NF .int := rfl
@[simp] theorem Ty.NF_bool : Ty.NF .bool := rfl
@[simp] theorem Ty.NF_str : Ty.NF .str := rfl
@[simp] theorem Ty.NF_unit : Ty.NF .unit := rfl

@[simp] theorem Ty.NF_tup {ts : List Ty} : Ty.NF (.tup ts) ↔ ∀ t ∈ ts, Ty.NF t := by
  simp [Ty.NF, namedIn, namedInList_eq_nil]

@[simp] theorem Ty.NF_arr {ps : List Ty} {r : Ty} {e : Eff} :
    Ty.NF (.arr ps r e) ↔ (∀ t ∈ ps, Ty.NF t) ∧ Ty.NF r := by
  simp [Ty.NF, namedIn, namedInList_eq_nil]

@[simp] theorem Ty.NF_adt {n : String} {ts : List Ty} :
    Ty.NF (.adt n ts) ↔ ∀ t ∈ ts, Ty.NF t := by
  simp [Ty.NF, namedIn, namedInList_eq_nil]

theorem namedIn_mkArr (ps : List Ty) (r : Ty) (e : Eff) :
    namedIn (mkArr ps r e) = namedIn (.arr ps r e) := by
  unfold mkArr
  split
  · split
    · rfl
    · simp [namedIn, namedInList_append, List.append_assoc]
  · rfl

theorem Ty.NF_mkArr {ps : List Ty} {r : Ty} {e : Eff} :
    Ty.NF (mkArr ps r e) ↔ (∀ t ∈ ps, Ty.NF t) ∧ Ty.NF r := by
  simp only [Ty.NF, namedIn_mkArr]
  exact Ty.NF_arr

/-! The pieces of a named-free arrow or tuple (as `Ty.WF.arr_res` and the others, for `Ty.WF`). -/

theorem Ty.NF.arr_res {ps : List Ty} {r : Ty} {e : Eff} (h : Ty.NF (.arr ps r e)) : Ty.NF r :=
  (Ty.NF_arr.mp h).2

theorem Ty.NF.arr_params {ps : List Ty} {r : Ty} {e : Eff} (h : Ty.NF (.arr ps r e)) :
    ∀ t ∈ ps, Ty.NF t :=
  (Ty.NF_arr.mp h).1

theorem Ty.NF.arr_take {ps : List Ty} {r : Ty} {e : Eff} (h : Ty.NF (.arr ps r e)) (m : Nat) :
    ∀ t ∈ ps.take m, Ty.NF t :=
  fun t ht => (Ty.NF_arr.mp h).1 t (List.mem_of_mem_take ht)

theorem Ty.NF.arr_drop {ps : List Ty} {r : Ty} {e : Eff} (h : Ty.NF (.arr ps r e)) (m : Nat) :
    Ty.NF (.arr (ps.drop m) r e) :=
  Ty.NF_arr.mpr ⟨fun t ht => (Ty.NF_arr.mp h).1 t (List.mem_of_mem_drop ht), (Ty.NF_arr.mp h).2⟩

theorem Ty.NF.of_mv {t : Ty} (h : ∃ j, t = .mv j) : Ty.NF t := by
  obtain ⟨j, rfl⟩ := h
  exact Ty.NF_mv

theorem Ty.NF.tup_params {ts : List Ty} (h : Ty.NF (.tup ts)) : ∀ t ∈ ts, Ty.NF t :=
  Ty.NF_tup.mp h

theorem Ty.NF.adt_params {n : String} {ts : List Ty} (h : Ty.NF (.adt n ts)) :
    ∀ t ∈ ts, Ty.NF t :=
  Ty.NF_adt.mp h

theorem Ty.NF.list_cons {t : Ty} {ts : List Ty} (h1 : Ty.NF t) (h2 : ∀ u ∈ ts, Ty.NF u) :
    ∀ u ∈ t :: ts, Ty.NF u := by
  intro u hu
  rcases List.mem_cons.mp hu with rfl | hu
  · exact h1
  · exact h2 u hu

/-! ### Growing states

`Ext s s'`: the state only grows. This is the relation the soundness layer (`Subst.lean` on) keeps
along every step; `fresh_leaf` and the loops over it carry it, and `fresh_wp`'s `sz ≤` is its size
half. -/

/-- `s'` extends `s`: the size does not shrink, no binding is changed or removed, and a named-free
substitution stays named-free. Reducible, so `omega` reads the size fact. -/
@[reducible] def Ext (s s' : CState) : Prop :=
  s.sz ≤ s'.sz ∧
    (∀ (n : Nat) (t : Ty), s.subst[n]? = some (some t) → s'.subst[n]? = some (some t)) ∧
    (s.NF → s'.NF)

theorem Ext.refl (s : CState) : Ext s s := ⟨Nat.le_refl _, fun _ _ h => h, id⟩

theorem Ext.trans {s1 s2 s3 : CState} (h1 : Ext s1 s2) (h2 : Ext s2 s3) : Ext s1 s3 :=
  ⟨Nat.le_trans h1.1 h2.1, fun n t h => h2.2.1 n t (h1.2.1 n t h), fun h => h2.2.2 (h1.2.2 h)⟩

theorem Ext.sz {s s' : CState} (h : Ext s s') : s.sz ≤ s'.sz := h.1

theorem Ext.bound {s s' : CState} (h : Ext s s') {n : Nat} {t : Ty}
    (hn : s.subst[n]? = some (some t)) : s'.subst[n]? = some (some t) := h.2.1 n t hn

theorem Ext.nf {s s' : CState} (h : Ext s s') : s.NF → s'.NF := h.2.2

theorem Ext.push {s s' : CState} (h : s'.subst = s.subst.push none) : Ext s s' := by
  have hget : ∀ (n : Nat) (t : Ty), s'.subst[n]? = some (some t) → s.subst[n]? = some (some t) := by
    intro n t ht
    rw [h, Array.getElem?_push] at ht
    split at ht
    · simp at ht
    · exact ht
  refine ⟨by simp [CState.sz, h], fun n t hn => ?_, fun hnf n t ht => hnf n t (hget n t ht)⟩
  rw [h, Array.getElem?_push]
  split
  · simp_all
  · exact hn

theorem Ext.of_subst {s s' : CState} (h : s'.subst = s.subst) : Ext s s' :=
  ⟨by simp [CState.sz, h], fun n t hn => by rw [h]; exact hn,
    fun hnf n t ht => hnf n t (by rw [h] at ht; exact ht)⟩

/-- Binding an unbound variable only adds a binding; it stays named-free when the bound type is
(given that the old substitution was). -/
theorem Ext.set {s s' : CState} {n : Nat} {v : Option Ty} (hu : s.Unbound n)
    (hv : s.NF → ∀ t, v = some t → Ty.NF t)
    (h : s'.subst = s.subst.set! n v) : Ext s s' := by
  refine ⟨by simp [CState.sz, h], fun j t hj => ?_, fun hnf j t hj => ?_⟩
  · rw [h, Array.set!_eq_setIfInBounds, Array.getElem?_setIfInBounds]
    by_cases hnj : n = j
    · subst hnj; exact absurd hj (hu t)
    · simp [hnj, hj]
  · rw [h, Array.set!_eq_setIfInBounds, Array.getElem?_setIfInBounds] at hj
    by_cases hnj : n = j
    · subst hnj
      simp only [ite_true] at hj
      split at hj
      · exact hv hnf t (Option.some.inj hj)
      · cases hj
    · simp only [hnj, ite_false] at hj
      exact hnf j t hj

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

/-- `Wp.mapM_rel` along `Ext`. -/
theorem Wp.mapM_ext {α β : Type} {f : α → M β} {l : List α} {s : CState}
    (I : CState → Prop) (R : CState → α → β → Prop)
    (hR : ∀ s1 s2 a b, Ext s1 s2 → R s1 a b → R s2 a b) (h0 : I s)
    (hf : ∀ a ∈ l, ∀ s1, I s1 → Ext s s1 →
      Wp (f a) (fun b s2 => I s2 ∧ Ext s1 s2 ∧ R s2 a b) s1) :
    Wp (l.mapM f) (fun bs s2 => I s2 ∧ Ext s s2 ∧ All2 (R s2) l bs) s :=
  Wp.mapM_rel Ext Ext.refl (fun _ _ _ => Ext.trans) I R hR h0 hf

/-- `Wp.forIn_unit_rel` along `Ext`. -/
theorem Wp.forIn_unit_ext {α : Type} {body : α → M PUnit} {l : List α} {s : CState}
    (I : CState → Prop) (R : CState → α → Prop) (hR : ∀ s1 s2 a, Ext s1 s2 → R s1 a → R s2 a)
    (h0 : I s)
    (hb : ∀ a ∈ l, ∀ s1, I s1 → Ext s s1 →
      Wp (body a) (fun _ s2 => I s2 ∧ Ext s1 s2 ∧ R s2 a) s1) :
    Wp (forIn l PUnit.unit fun x _ => do body x; pure (ForInStep.yield PUnit.unit))
      (fun _ s2 => I s2 ∧ Ext s s2 ∧ ∀ a ∈ l, R s2 a) s :=
  Wp.forIn_unit_rel Ext Ext.refl (fun _ _ _ => Ext.trans) I R hR h0 hb

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

/-- `n` fresh variables, collected. -/
theorem fresh_loop_sound (n : Nat) {Q : List Ty → CState → Prop} {s : CState} (hs : s.WF)
    (h : ∀ ps s', Ext s s' → s'.WF → ps.length = n →
      (∀ t ∈ ps, Ty.WF s'.sz 0 t) → (∀ t ∈ ps, ∃ j, t = .mv j) →
      (∀ m, s.Unbound m → s'.Unbound m) → Q ps s') :
    Wp (forIn [0:n] ([] : List Ty) fun _ acc => do
      let d ← fresh
      pure (ForInStep.yield (acc ++ [d]))) Q s := by
  simp only [Std.Legacy.Range.forIn_eq_forIn_range']
  have gen : ∀ (l : List Nat) (acc : List Ty) (s : CState), s.WF → (∀ t ∈ acc, Ty.WF s.sz 0 t) →
      (∀ t ∈ acc, ∃ j, t = .mv j) →
      (∀ ps s', Ext s s' → s'.WF → ps.length = acc.length + l.length →
        (∀ t ∈ ps, Ty.WF s'.sz 0 t) → (∀ t ∈ ps, ∃ j, t = .mv j) →
        (∀ m, s.Unbound m → s'.Unbound m) → Q ps s') →
      Wp (forIn l acc fun _ acc => do
        let d ← fresh
        pure (ForInStep.yield (acc ++ [d]))) Q s := by
    intro l
    induction l with
    | nil =>
      intro acc s hs ha hm h
      simp only [List.forIn_nil]
      exact Wp.pure' (h acc s (Ext.refl _) hs (by simp) ha hm (fun _ hu => hu))
    | cons a l ih =>
      intro acc s hs ha hm h
      simp only [List.forIn_cons]
      refine Wp.bind (Wp.bind (fresh_leaf hs ?_))
      intro d s1 hd hdm g1 e1 h1 hu1
      refine Wp.pure' ?_
      dsimp only
      refine ih (acc ++ [d]) s1 h1 ?_ ?_ ?_
      · intro t ht
        rcases List.mem_append.mp ht with ht | ht
        · exact (ha t ht).mono (by omega) (Nat.le_refl _)
        · rcases List.mem_singleton.mp ht with rfl; exact hd
      · intro t ht
        rcases List.mem_append.mp ht with ht | ht
        · exact hm t ht
        · rcases List.mem_singleton.mp ht with rfl; exact hdm
      · intro ps s2 e2 h2 hl hps hpm hu2
        refine h ps s2 (e1.trans e2) h2 ?_ hps hpm (fun m hu => hu2 m (hu1 m hu))
        simp only [List.length_append, List.length_cons, List.length_nil] at hl ⊢
        omega
  exact gen _ [] s hs (by simp) (by simp) fun ps s' e h' hl hp hm hu =>
    h ps s' e h' (by simpa using hl) hp hm hu

/-! ### Fresh variables and binding -/

theorem fresh_wp {Q : Ty → CState → Prop} {s : CState} (hs : s.WF)
    (h : ∀ a s', Ty.WF s'.sz 0 a → s.sz + 1 = s'.sz → s'.WF → Q a s') : Wp fresh Q s := by
  intro n a s' hr
  have h2 : (Except.ok (.mv s.sz, { s with subst := s.subst.push none }) :
      Except CErr (Ty × CState)) = .ok (a, s') := hr
  cases h2
  refine h _ _ ?_ ?_ ?_
  · simp [CState.sz]
  · simp [CState.sz]
  · exact hs.push rfl

theorem freshSkolem_wp {Q : Ty → CState → Prop} {s : CState} (hs : s.WF)
    (h : ∀ a s', (∀ sz g, Ty.WF sz g a) → s.sz = s'.sz → s'.WF → Q a s') :
    Wp freshSkolem Q s := by
  intro n a s' hr
  have h2 : (Except.ok (.skolem s.nextSk, { s with nextSk := s.nextSk + 1 }) :
      Except CErr (Ty × CState)) = .ok (a, s') := hr
  cases h2
  exact h _ _ (fun _ _ => Ty.WF_skolem) rfl (hs.of_subst rfl)

macro_rules
  | `(tactic| wp_step) => `(tactic| leaf fresh_wp)
macro_rules
  | `(tactic| wp_step) => `(tactic| leaf freshSkolem_wp)

/-! ### Arrows -/

theorem metasOfList_append (a b : List Ty) :
    metasOfList (a ++ b) = metasOfList a ++ metasOfList b := by
  induction a with
  | nil => simp [metasOfList]
  | cons t a ih => simp [metasOfList, ih, List.append_assoc]

theorem gensOfList_append (a b : List Ty) :
    gensOfList (a ++ b) = gensOfList a ++ gensOfList b := by
  induction a with
  | nil => simp [gensOfList]
  | cons t a ih => simp [gensOfList, ih, List.append_assoc]

theorem metasOf_mkArr (ps : List Ty) (r : Ty) (e : Eff) :
    metasOf (mkArr ps r e) = metasOf (.arr ps r e) := by
  unfold mkArr
  split
  · split
    · rfl
    · simp [metasOf, metasOfList_append, List.append_assoc]
  · rfl

theorem gensOf_mkArr (ps : List Ty) (r : Ty) (e : Eff) :
    gensOf (mkArr ps r e) = gensOf (.arr ps r e) := by
  unfold mkArr
  split
  · split
    · rfl
    · simp [gensOf, gensOfList_append, List.append_assoc]
  · rfl

theorem Ty.WF_mkArr {sz g : Nat} {ps : List Ty} {r : Ty} {e : Eff} :
    Ty.WF sz g (mkArr ps r e) ↔ Ty.WF sz g (.arr ps r e) := by
  simp only [Ty.WF, metasOf_mkArr, gensOf_mkArr]

/-! ### Following bindings -/

theorem shallowF_wp {Q : Ty → CState → Prop} {s : CState} (hs : s.WF) :
    ∀ (k : Nat) (t : Ty), Ty.WF s.sz 0 t →
      (∀ r s', s' = s → Ty.WF s.sz 0 r → (∀ m, r = .mv m → s.Unbound m) → Q r s') →
      Wp (shallowF k t) Q s := by
  intro k
  induction k with
  | zero => intro t _ _; exact Wp.fuel'
  | succ k ih =>
    intro t ht h
    cases t with
    | mv n =>
      simp only [shallowF]
      refine Wp.bind (Wp.get' ?_)
      split
      · rename_i t' hb
        exact ih t' (hs.range n t' hb) h
      · rename_i hb
        refine Wp.pure' (h _ _ rfl ht ?_)
        intro m hm t0 h0
        cases hm
        exact hb t0 h0 |>.elim
    | _ => simp only [shallowF]; exact Wp.pure' (h _ _ rfl ht (by simp))

theorem shallow_wp {Q : Ty → CState → Prop} {s : CState} {t : Ty} (hs : s.WF)
    (ht : Ty.WF s.sz 0 t)
    (h : ∀ r s', s' = s → Ty.WF s.sz 0 r → (∀ m, r = .mv m → s.Unbound m) → Q r s') :
    Wp (shallow t) Q s := by
  unfold shallow
  exact Wp.bind (Wp.read' fun n => shallowF_wp hs n t ht h)

/-- `zonk`'s result is in range, mentions only unbound variables, and leaves the state alone. -/
theorem zonkF_wp {Q : Ty → CState → Prop} {s : CState} (hs : s.WF) :
    ∀ (k : Nat) (t : Ty), Ty.WF s.sz 0 t →
      (∀ r s', s' = s → Ty.WF s.sz 0 r → (∀ m ∈ metasOf r, s.Unbound m) → Q r s') →
      Wp (zonkF k t) Q s := by
  intro k
  induction k generalizing Q with
  | zero => intro t _ _; exact Wp.fuel'
  | succ k ih =>
    intro t ht h
    simp only [zonkF]
    refine Wp.bind (shallowF_wp hs k t ht ?_)
    intro t' s1 hs1 ht' hu
    subst hs1
    have hlist : ∀ ts : List Ty, (∀ t ∈ ts, Ty.WF s1.sz 0 t) →
        Wp (ts.mapM (zonkF k)) (fun bs s2 => s2 = s1 ∧ s1.sz ≤ s2.sz ∧ ∀ b ∈ bs,
          Ty.WF s1.sz 0 b ∧ ∀ m ∈ metasOf b, s1.Unbound m) s1 := by
      intro ts hts
      refine Wp.mono (Wp.mapM (f := zonkF k) (l := ts) (s := s1) (fun s2 => s2 = s1)
        (fun _ b => Ty.WF s1.sz 0 b ∧ ∀ m ∈ metasOf b, s1.Unbound m)
        (fun _ _ _ _ hb => hb) rfl ?_) fun bs s2 hq => hq
      intro a ha s2 hs2 _
      subst hs2
      exact ih a (hts a ha) fun r s' hs' hr hu => by subst hs'; exact ⟨rfl, Nat.le_refl _, hr, hu⟩
    cases t' with
    | tup ts =>
      dsimp only
      have hts : ∀ t ∈ ts, Ty.WF s1.sz 0 t := Ty.WF_tup.mp ht'
      refine Wp.bind (Wp.mono (hlist ts hts) ?_)
      · intro bs s2 ⟨e2, g2, hb⟩
        subst e2
        refine Wp.pure' (h _ _ rfl ?_ ?_)
        · exact Ty.WF_tup.mpr fun b hb' => (hb b hb').1
        · intro m hm
          simp only [metasOf, mem_metasOfList] at hm
          obtain ⟨b, hb', hm⟩ := hm
          exact (hb b hb').2 m hm
    | arr ps r e =>
      dsimp only
      have hps : ∀ t ∈ ps, Ty.WF s1.sz 0 t := (Ty.WF_arr.mp ht').1
      have hr : Ty.WF s1.sz 0 r := (Ty.WF_arr.mp ht').2
      refine Wp.bind (Wp.mono (hlist ps hps) ?_)
      · intro bs s2 ⟨e2, g2, hb⟩
        subst e2
        refine Wp.bind (ih r hr ?_)
        intro r' s3 e3 hr' hu'
        subst e3
        refine Wp.pure' (h _ _ rfl ?_ ?_)
        · rw [Ty.WF_mkArr]
          exact Ty.WF_arr.mpr ⟨fun b hb' => (hb b hb').1, hr'⟩
        · intro m hm
          rw [metasOf_mkArr] at hm
          simp only [metasOf, List.mem_append, mem_metasOfList] at hm
          rcases hm with ⟨b, hb', hm⟩ | hm
          · exact (hb b hb').2 m hm
          · exact hu' m hm
    | adt n ts =>
      dsimp only
      have hts : ∀ t ∈ ts, Ty.WF s1.sz 0 t := Ty.WF_adt.mp ht'
      refine Wp.bind (Wp.mono (hlist ts hts) ?_)
      · intro bs s2 ⟨e2, g2, hb⟩
        subst e2
        refine Wp.pure' (h _ _ rfl ?_ ?_)
        · exact Ty.WF_adt.mpr fun b hb' => (hb b hb').1
        · intro m hm
          simp only [metasOf, mem_metasOfList] at hm
          obtain ⟨b, hb', hm⟩ := hm
          exact (hb b hb').2 m hm
    | mv n =>
      dsimp only
      refine Wp.pure' (h _ _ rfl ht' ?_)
      intro m hm
      simp only [metasOf, List.mem_singleton] at hm
      subst hm
      exact hu _ rfl
    | _ => dsimp only; exact Wp.pure' (h _ _ rfl ht' (by simp [metasOf]))

theorem zonk_wp {Q : Ty → CState → Prop} {s : CState} {t : Ty} (hs : s.WF)
    (ht : Ty.WF s.sz 0 t)
    (h : ∀ r s', s' = s → Ty.WF s.sz 0 r → (∀ m ∈ metasOf r, s.Unbound m) → Q r s') :
    Wp (zonk t) Q s := by
  unfold zonk
  exact Wp.bind (Wp.read' fun n => zonkF_wp hs n t ht h)

theorem bindMeta_wp {Q : PUnit → CState → Prop} {s : CState} {n : Nat} {t : Ty} (hs : s.WF)
    (hn : n < s.sz) (ht : Ty.WF s.sz 0 t) (h : ∀ s', s'.sz = s.sz → s'.WF → Q ⟨⟩ s') :
    Wp (bindMeta n t) Q s := by
  unfold bindMeta
  refine Wp.bind (zonk_wp hs ht ?_)
  intro t' s1 e1 ht' hu
  subst e1
  wp_tac
  rename_i hocc
  refine h _ ?_ ?_
  · simp [CState.sz]
  · refine hs.bind hn ht' hu ?_ rfl
    simpa [occursIn] using hocc

macro_rules
  | `(tactic| wp_step) => `(tactic| leaf bindMeta_wp)

theorem flowF_wp : ∀ (k : Nat) (a e : Ty) (Q : PUnit → CState → Prop) (s : CState), s.WF →
    Ty.WF s.sz 0 a → Ty.WF s.sz 0 e → (∀ s', s.sz ≤ s'.sz → s'.WF → Q ⟨⟩ s') →
    Wp (flowF k a e) Q s := by
  intro k
  induction k with
  | zero => intros; exact Wp.fuel'
  | succ k ih =>
    intro a e Q s hs ha he h
    simp only [flowF]
    refine Wp.bind (shallowF_wp hs k a ha ?_)
    intro a' s1 e1 ha' hua
    subst e1
    refine Wp.bind (shallowF_wp hs k e he ?_)
    intro e' s2 e2 he' hue
    subst e2
    have flowk : ∀ {Q : PUnit → CState → Prop} {s : CState} {x y : Ty} (hs : s.WF)
        (hx : Ty.WF s.sz 0 x) (hy : Ty.WF s.sz 0 y)
        (h : ∀ s', s.sz ≤ s'.sz → s'.WF → Q ⟨⟩ s'), Wp (flowF k x y) Q s :=
      fun hs hx hy h => ih _ _ _ _ hs hx hy h
    have flowzip : ∀ {xs ys : List Ty} {Q : PUnit → CState → Prop} {s : CState} (hs : s.WF)
        (hxs : ∀ t ∈ xs, Ty.WF s.sz 0 t) (hys : ∀ t ∈ ys, Ty.WF s.sz 0 t)
        (h : ∀ s', s.sz ≤ s'.sz → s'.WF → Q ⟨⟩ s'),
        Wp (forIn (xs.zip ys) PUnit.unit fun x _ => do
          flowF k x.fst x.snd; pure (ForInStep.yield PUnit.unit)) Q s := by
      intro xs ys Q s hs hxs hys h
      refine Wp.forIn_unit hs ?_ h
      intro p hp s1 h1 g1
      obtain ⟨x, y⟩ := p
      obtain ⟨hx, hy⟩ := List.of_mem_zip hp
      exact Wp.mono (flowk h1 ((hxs x hx).mono g1 (Nat.le_refl _))
        ((hys y hy).mono g1 (Nat.le_refl _)) fun s' g' h' => ⟨h', g'⟩) fun _ _ hq => hq
    have flowzip' : ∀ {xs ys : List Ty} {Q : PUnit → CState → Prop} {s : CState} (hs : s.WF)
        (hxs : ∀ t ∈ xs, Ty.WF s.sz 0 t) (hys : ∀ t ∈ ys, Ty.WF s.sz 0 t)
        (h : ∀ s', s.sz ≤ s'.sz → s'.WF → Q ⟨⟩ s'),
        Wp (forIn (xs.zip ys) PUnit.unit fun x _ => do
          flowF k x.snd x.fst; pure (ForInStep.yield PUnit.unit)) Q s := by
      intro xs ys Q s hs hxs hys h
      refine Wp.forIn_unit hs ?_ h
      intro p hp s1 h1 g1
      obtain ⟨x, y⟩ := p
      obtain ⟨hx, hy⟩ := List.of_mem_zip hp
      exact Wp.mono (flowk h1 ((hys y hy).mono g1 (Nat.le_refl _))
        ((hxs x hx).mono g1 (Nat.le_refl _)) fun s' g' h' => ⟨h', g'⟩) fun _ _ hq => hq
    split
    all_goals repeat' (first | wp_step | leaf flowk | leaf flowzip | leaf flowzip')
    all_goals (refine h _ ?_ ?_ <;> wf_close)

theorem flow_wp {Q : PUnit → CState → Prop} {s : CState} {a e : Ty} (hs : s.WF)
    (ha : Ty.WF s.sz 0 a) (he : Ty.WF s.sz 0 e)
    (h : ∀ s', s.sz ≤ s'.sz → s'.WF → Q ⟨⟩ s') : Wp (flow a e) Q s := by
  unfold flow
  exact Wp.bind (Wp.read' fun n => flowF_wp n a e Q s hs ha he h)

macro_rules
  | `(tactic| wp_step) => `(tactic| leaf flow_wp)

theorem Ty.WF.list_mono {sz sz' g : Nat} {l : List Ty} (h : ∀ t ∈ l, Ty.WF sz g t)
    (hs : sz ≤ sz') : ∀ t ∈ l, Ty.WF sz' g t :=
  fun t ht => (h t ht).mono hs (Nat.le_refl _)

macro_rules
  | `(tactic| wf_close) => `(tactic| (refine Ty.WF.list_mono ‹_› ?_; omega))

macro_rules
  | `(tactic| wf_close) => `(tactic| (apply Ty.WF_tup.mpr; wf_close))
macro_rules
  | `(tactic| wf_close) => `(tactic| (refine Ty.WF_adt.mpr ?_; wf_close))
macro_rules
  | `(tactic| wf_close) => `(tactic| (apply Ty.WF_arr.mpr; constructor <;> wf_close))
macro_rules
  | `(tactic| wf_close) => `(tactic| (rw [Ty.WF_mkArr]; wf_close))

/-- `mapM` of a binary type operation over two lists, zipped. -/
theorem Wp.mapM_zip {f : Ty × Ty → M Ty} {xs ys : List Ty} {Q : List Ty → CState → Prop}
    {s : CState} (hs : s.WF) (hxs : ∀ t ∈ xs, Ty.WF s.sz 0 t) (hys : ∀ t ∈ ys, Ty.WF s.sz 0 t)
    (hf : ∀ x y s1, s1.WF → s.sz ≤ s1.sz → Ty.WF s1.sz 0 x → Ty.WF s1.sz 0 y →
      Wp (f (x, y)) (fun r s2 => s2.WF ∧ s1.sz ≤ s2.sz ∧ Ty.WF s2.sz 0 r) s1)
    (h : ∀ bs s', s.sz ≤ s'.sz → s'.WF → (∀ b ∈ bs, Ty.WF s'.sz 0 b) → Q bs s') :
    Wp ((xs.zip ys).mapM f) Q s := by
  refine Wp.mono (Wp.mapM (f := f) (l := xs.zip ys) (s := s) (fun s1 => s1.WF)
    (fun s1 b => Ty.WF s1.sz 0 b) (fun _ _ _ g hb => hb.mono g (Nat.le_refl _)) hs ?_) ?_
  · intro p hp s1 h1 g1
    obtain ⟨x, y⟩ := p
    obtain ⟨hx, hy⟩ := List.of_mem_zip hp
    exact Wp.mono (hf x y s1 h1 g1 ((hxs x hx).mono g1 (Nat.le_refl _))
      ((hys y hy).mono g1 (Nat.le_refl _))) fun _ _ ⟨a, b, c⟩ => ⟨a, b, c⟩
  · intro bs s' ⟨h1, g1, hb⟩
    exact h bs s' g1 h1 hb

theorem joinTyF_wp : ∀ (k : Nat) (pos : Bool) (a b : Ty) (Q : Ty → CState → Prop) (s : CState),
    s.WF → Ty.WF s.sz 0 a → Ty.WF s.sz 0 b →
    (∀ r s', s.sz ≤ s'.sz → s'.WF → Ty.WF s'.sz 0 r → Q r s') →
    Wp (joinTyF k pos a b) Q s := by
  intro k
  induction k with
  | zero => intros; exact Wp.fuel'
  | succ k ih =>
    intro pos a b Q s hs ha hb h
    simp only [joinTyF]
    refine Wp.bind (shallowF_wp hs k a ha ?_)
    intro a' s1 e1 ha' hua
    subst e1
    refine Wp.bind (shallowF_wp hs k b hb ?_)
    intro b' s2 e2 hb' hub
    subst e2
    have joink : ∀ {pos : Bool} {Q : Ty → CState → Prop} {s : CState} {x y : Ty} (hs : s.WF)
        (hx : Ty.WF s.sz 0 x) (hy : Ty.WF s.sz 0 y)
        (h : ∀ r s', s.sz ≤ s'.sz → s'.WF → Ty.WF s'.sz 0 r → Q r s'),
        Wp (joinTyF k pos x y) Q s := fun hs hx hy h => ih _ _ _ _ _ hs hx hy h
    have joinzip : ∀ {pos : Bool} {xs ys : List Ty} {Q : List Ty → CState → Prop} {s : CState}
        (hs : s.WF) (hxs : ∀ t ∈ xs, Ty.WF s.sz 0 t) (hys : ∀ t ∈ ys, Ty.WF s.sz 0 t)
        (h : ∀ bs s', s.sz ≤ s'.sz → s'.WF → (∀ b ∈ bs, Ty.WF s'.sz 0 b) → Q bs s'),
        Wp ((xs.zip ys).mapM fun (x, y) => joinTyF k pos x y) Q s := by
      intro pos xs ys Q s hs hxs hys h
      refine Wp.mapM_zip hs hxs hys ?_ h
      intro x y s1 h1 g1 hx hy
      exact joink h1 hx hy fun r s' g' h' hr => ⟨h', g', hr⟩
    split
    all_goals repeat' (first | wp_step | leaf joink | leaf joinzip)
    all_goals (refine h _ _ ?_ ?_ ?_ <;> wf_close)

theorem joinTy_wp {Q : Ty → CState → Prop} {s : CState} {pos : Bool} {a b : Ty} (hs : s.WF)
    (ha : Ty.WF s.sz 0 a) (hb : Ty.WF s.sz 0 b)
    (h : ∀ r s', s.sz ≤ s'.sz → s'.WF → Ty.WF s'.sz 0 r → Q r s') :
    Wp (joinTy pos a b) Q s := by
  unfold joinTy
  exact Wp.bind (Wp.read' fun n => joinTyF_wp n pos a b Q s hs ha hb h)

macro_rules
  | `(tactic| wp_step) => `(tactic| leaf joinTy_wp)

macro_rules
  | `(tactic| wp_step) => `(tactic| leaf fresh_loop_sound)

theorem peelF_wp : ∀ (k : Nat) (t : Ty) (n : Nat) (Q : List Ty × Ty × Eff → CState → Prop)
    (s : CState), s.WF → Ty.WF s.sz 0 t →
    (∀ ps r e s', s.sz ≤ s'.sz → s'.WF → (∀ t ∈ ps, Ty.WF s'.sz 0 t) → Ty.WF s'.sz 0 r →
      Q (ps, r, e) s') →
    Wp (peelF k t n) Q s := by
  intro k
  induction k with
  | zero => intros; exact Wp.fuel'
  | succ k ih =>
    intro t n Q s hs ht h
    simp only [peelF]
    refine Wp.bind (shallowF_wp hs k t ht ?_)
    intro t' s1 e1 ht' hu
    subst e1
    have peelk : ∀ {Q : List Ty × Ty × Eff → CState → Prop} {s : CState} {t : Ty} {n : Nat}
        (hs : s.WF) (ht : Ty.WF s.sz 0 t)
        (h : ∀ ps r e s', s.sz ≤ s'.sz → s'.WF → (∀ t ∈ ps, Ty.WF s'.sz 0 t) →
          Ty.WF s'.sz 0 r → Q (ps, r, e) s'),
        Wp (peelF k t n) Q s := fun hs ht h => ih _ _ _ _ hs ht h
    split
    all_goals repeat' (first | wp_step | leaf peelk)
    all_goals (refine h _ _ _ _ ?_ ?_ ?_ ?_ <;>
      first
        | wf_close
        | (intro t ht
           rcases List.mem_append.mp ht with ht | ht
           · refine Ty.WF.arr_params ‹_› ?_ t ht
             omega
           · exact ‹∀ t ∈ _, Ty.WF _ 0 t› t ht))

theorem peel_wp {Q : List Ty × Ty × Eff → CState → Prop} {s : CState} {t : Ty} {n : Nat}
    (hs : s.WF) (ht : Ty.WF s.sz 0 t)
    (h : ∀ ps r e s', s.sz ≤ s'.sz → s'.WF → (∀ t ∈ ps, Ty.WF s'.sz 0 t) → Ty.WF s'.sz 0 r →
      Q (ps, r, e) s') : Wp (peel t n) Q s := by
  unfold peel
  exact Wp.bind (Wp.read' fun k => peelF_wp k t n Q s hs ht h)

macro_rules
  | `(tactic| wp_step) => `(tactic| leaf peel_wp)
macro_rules
  | `(tactic| wp_step) => `(tactic| (refine Wp.tryCatch' ?_ (fun _ _ => ?_) rfl; try dsimp only))

theorem expectBool_wp {Q : PUnit → CState → Prop} {s : CState} {t : Ty} {key detail : String}
    (hs : s.WF) (ht : Ty.WF s.sz 0 t) (h : ∀ s', s.sz ≤ s'.sz → s'.WF → Q ⟨⟩ s') :
    Wp (expectBool t key detail) Q s := by
  unfold expectBool
  wp_tac
  all_goals (refine h _ ?_ ?_ <;> wf_close)

macro_rules
  | `(tactic| wp_step) => `(tactic| leaf expectBool_wp)

/-! ### Instantiation -/

theorem instTy_gen_eq (ms : Array Ty) (i : Nat) (h : i < ms.size) : instTy ms (.gen i) = ms[i] := by
  simp only [instTy]
  exact getElem!_pos ms i h

mutual
theorem instTy_wf {ms : Array Ty} {sz n : Nat} (hm : ms.size = n)
    (hms : ∀ t ∈ ms, Ty.WF sz 0 t) : ∀ t : Ty, Ty.WF sz n t → Ty.WF sz 0 (instTy ms t)
  | .gen i, h => by
    have hi : i < ms.size := hm ▸ Ty.WF_gen.mp h
    rw [instTy_gen_eq ms i hi]
    exact hms _ (Array.getElem_mem hi)
  | .tup ts, h => by
    simp only [instTy]
    exact Ty.WF_tup.mpr (instTyList_wf hm hms ts (Ty.WF_tup.mp h))
  | .arr ps r e, h => by
    simp only [instTy]
    exact Ty.WF_arr.mpr ⟨instTyList_wf hm hms ps (Ty.WF_arr.mp h).1,
      instTy_wf hm hms r (Ty.WF_arr.mp h).2⟩
  | .mv n, h => by simpa [instTy] using h
  | .named _, _ => by simp [instTy]
  | .skolem _, _ => by simp [instTy]
  | .int, _ => by simp [instTy]
  | .bool, _ => by simp [instTy]
  | .str, _ => by simp [instTy]
  | .unit, _ => by simp [instTy]
  | .adt _ ts, h => by
    simp only [instTy]
    exact Ty.WF_adt.mpr (instTyList_wf hm hms ts (Ty.WF_adt.mp h))

theorem instTyList_wf {ms : Array Ty} {sz n : Nat} (hm : ms.size = n)
    (hms : ∀ t ∈ ms, Ty.WF sz 0 t) : ∀ ts : List Ty, (∀ t ∈ ts, Ty.WF sz n t) →
      ∀ t ∈ instTyList ms ts, Ty.WF sz 0 t
  | [], _ => by simp [instTyList]
  | t :: ts, h => by
    simp only [instTyList, List.mem_cons]
    rintro u (rfl | hu)
    · exact instTy_wf hm hms t (h t (List.mem_cons_self ..))
    · exact instTyList_wf hm hms ts (fun t ht => h t (List.mem_cons_of_mem _ ht)) u hu
end

theorem instantiate_wp {Q : Ty → CState → Prop} {s : CState} {n : Nat} {t : Ty} (hs : s.WF)
    (ht : Ty.WF s.sz n t)
    (h : ∀ r s', s.sz ≤ s'.sz → s'.WF → Ty.WF s'.sz 0 r → Q r s') :
    Wp (instantiate n t) Q s := by
  unfold instantiate
  dsimp only
  simp only [Std.Legacy.Range.forIn_eq_forIn_range']
  refine Wp.bind (fresh_push_loop_sound _ #[] hs (by simp) (by simp) ?_)
  intro ms s1 e1 h1 hsz hm hms
  refine Wp.pure' (h _ s1 e1.sz h1 ?_)
  refine instTy_wf (n := n) ?_ hms t (ht.mono e1.sz (Nat.le_refl _))
  simpa using hsz

macro_rules
  | `(tactic| wp_step) => `(tactic| leaf instantiate_wp)
macro_rules
  | `(tactic| wp_step) => `(tactic| leaf shallow_wp)
macro_rules
  | `(tactic| wp_step) => `(tactic| leaf zonk_wp)

/-! ### Generalisation -/

theorem genSlot_wf {sz : Nat} (tab : Array Ty) (t : Ty) :
    tab.size ≤ (genSlot tab t).1.size ∧ Ty.WF sz (genSlot tab t).1.size (genSlot tab t).2 := by
  unfold genSlot
  split
  · rename_i i hi
    have := (Array.findIdx?_eq_some_iff_findIdx_eq.mp hi).1
    exact ⟨Nat.le_refl _, Ty.WF_gen.mpr this⟩
  · simp [Ty.WF_gen]

mutual
theorem genGo_wf (keep : List Nat) {sz : Nat} : ∀ (t : Ty) (tab : Array Ty),
    Ty.WF sz 0 t →
    tab.size ≤ (genGo keep tab t).1.size ∧
      Ty.WF sz (genGo keep tab t).1.size (genGo keep tab t).2
  | .mv n, tab, h => by
    simp only [genGo]
    split
    · exact ⟨Nat.le_refl _, by simpa using h⟩
    · exact genSlot_wf tab _
  | .skolem _, tab, _ => by simp only [genGo]; exact genSlot_wf tab _
  | .tup ts, tab, h => by
    simp only [genGo]
    obtain ⟨hg, hl⟩ := genGoList_wf keep ts tab (Ty.WF_tup.mp h)
    exact ⟨hg, Ty.WF_tup.mpr hl⟩
  | .arr ps r e, tab, h => by
    simp only [genGo]
    obtain ⟨hg1, hl⟩ := genGoList_wf keep ps tab (Ty.WF_arr.mp h).1
    obtain ⟨hg2, hr⟩ := genGo_wf keep r _ (Ty.WF_arr.mp h).2
    exact ⟨Nat.le_trans hg1 hg2, Ty.WF_arr.mpr ⟨fun t ht => (hl t ht).mono (Nat.le_refl _) hg2, hr⟩⟩
  | .named _, tab, h => by simp [genGo]
  | .gen _, tab, h => absurd (Ty.WF_gen.mp h) (Nat.not_lt_zero _)
  | .int, tab, h => by simp [genGo]
  | .bool, tab, h => by simp [genGo]
  | .str, tab, h => by simp [genGo]
  | .unit, tab, h => by simp [genGo]
  | .adt _ ts, tab, h => by
    simp only [genGo]
    obtain ⟨hg, hl⟩ := genGoList_wf keep ts tab (Ty.WF_adt.mp h)
    exact ⟨hg, Ty.WF_adt.mpr hl⟩

theorem genGoList_wf (keep : List Nat) {sz : Nat} : ∀ (ts : List Ty) (tab : Array Ty),
    (∀ t ∈ ts, Ty.WF sz 0 t) →
    tab.size ≤ (genGoList keep tab ts).1.size ∧
      ∀ t ∈ (genGoList keep tab ts).2, Ty.WF sz (genGoList keep tab ts).1.size t
  | [], tab, _ => by simp [genGoList]
  | t :: ts, tab, h => by
    simp only [genGoList]
    obtain ⟨hg1, h1⟩ := genGo_wf keep t tab (h t (List.mem_cons_self ..))
    obtain ⟨hg2, h2⟩ := genGoList_wf keep ts _ (fun u hu => h u (List.mem_cons_of_mem _ hu))
    refine ⟨Nat.le_trans hg1 hg2, ?_⟩
    intro u hu
    rcases List.mem_cons.mp hu with rfl | hu
    · exact h1.mono (Nat.le_refl _) hg2
    · exact h2 u hu
end

theorem generalize_wp {Q : Nat × Ty → CState → Prop} {s : CState} {keep : List Nat} {t : Ty}
    (hs : s.WF) (ht : Ty.WF s.sz 0 t)
    (h : ∀ n t' s', s.sz ≤ s'.sz → s'.WF → Ty.WF s'.sz n t' → Q (n, t') s') :
    Wp (generalize keep t) Q s := by
  unfold generalize
  refine Wp.bind (zonk_wp hs ht ?_)
  intro t1 s1 e1 ht1 _
  subst e1
  have hw := (genGo_wf keep t1 #[] ht1).2
  rcases hg : genGo keep #[] t1 with ⟨tab, t'⟩
  rw [hg] at hw
  exact Wp.pure' (h _ _ _ (Nat.le_refl _) hs hw)

macro_rules
  | `(tactic| wp_step) => `(tactic| leaf generalize_wp)

/-! ### Written types -/

mutual
theorem elabTy_wp (f : String → Ty) (g : Nat) : ∀ (t : Ty) (Q : Ty → CState → Prop) (s : CState),
    (∀ x, Ty.WF s.sz g (f x)) → Ty.WF s.sz g t →
    (∀ r s', s' = s → Ty.WF s.sz g r → Q r s') → Wp (elabTy f t) Q s
  | .named x, Q, s, hf, _, h => by
    unfold elabTy; exact Wp.pure' (h _ _ rfl (hf x))
  | .tup ts, Q, s, hf, ht, h => by
    unfold elabTy
    refine Wp.bind (elabTyList_wp f g ts _ s hf (Ty.WF_tup.mp ht) ?_)
    intro bs s1 e hb
    subst e
    exact Wp.pure' (h _ _ rfl (Ty.WF_tup.mpr hb))
  | .arr ps r e, Q, s, hf, ht, h => by
    unfold elabTy
    refine Wp.bind (elabTy_wp f g r _ s hf (Ty.WF_arr.mp ht).2 ?_)
    intro r' s1 e1 hr'
    subst e1
    split
    · exact Wp.reject' _ _
    · refine Wp.bind (elabTyList_wp f g ps _ s1 hf (Ty.WF_arr.mp ht).1 ?_)
      intro bs s2 e2 hb
      subst e2
      refine Wp.pure' (h _ _ rfl ?_)
      rw [Ty.WF_mkArr]
      exact Ty.WF_arr.mpr ⟨hb, hr'⟩
  | .mv _, Q, s, hf, ht, h => by unfold elabTy; exact Wp.pure' (h _ _ rfl ht)
  | .skolem _, Q, s, hf, ht, h => by unfold elabTy; exact Wp.pure' (h _ _ rfl ht)
  | .gen _, Q, s, hf, ht, h => by unfold elabTy; exact Wp.pure' (h _ _ rfl ht)
  | .int, Q, s, hf, ht, h => by unfold elabTy; exact Wp.pure' (h _ _ rfl ht)
  | .bool, Q, s, hf, ht, h => by unfold elabTy; exact Wp.pure' (h _ _ rfl ht)
  | .str, Q, s, hf, ht, h => by unfold elabTy; exact Wp.pure' (h _ _ rfl ht)
  | .unit, Q, s, hf, ht, h => by unfold elabTy; exact Wp.pure' (h _ _ rfl ht)
  | .adt _ ts, Q, s, hf, ht, h => by
    unfold elabTy
    refine Wp.bind (elabTyList_wp f g ts _ s hf (Ty.WF_adt.mp ht) ?_)
    intro bs s1 e hb
    subst e
    exact Wp.pure' (h _ _ rfl (Ty.WF_adt.mpr hb))

theorem elabTyList_wp (f : String → Ty) (g : Nat) :
    ∀ (ts : List Ty) (Q : List Ty → CState → Prop) (s : CState),
    (∀ x, Ty.WF s.sz g (f x)) → (∀ t ∈ ts, Ty.WF s.sz g t) →
    (∀ rs s', s' = s → (∀ r ∈ rs, Ty.WF s.sz g r) → Q rs s') → Wp (elabTyList f ts) Q s
  | [], Q, s, hf, ht, h => by
    unfold elabTyList; exact Wp.pure' (h _ _ rfl (by simp))
  | t :: ts, Q, s, hf, ht, h => by
    unfold elabTyList
    refine Wp.bind (elabTy_wp f g t _ s hf (ht t (List.mem_cons_self ..)) ?_)
    intro t' s1 e1 hr
    subst e1
    refine Wp.bind (elabTyList_wp f g ts _ s1 hf (fun u hu => ht u (List.mem_cons_of_mem _ hu)) ?_)
    intro rs s2 e2 hrs
    subst e2
    refine Wp.pure' (h _ _ rfl ?_)
    intro r hr'
    rcases List.mem_cons.mp hr' with rfl | hr'
    · exact hr
    · exact hrs r hr'
end

theorem elabTy_leaf {f : String → Ty} {g : Nat} {t : Ty} {Q : Ty → CState → Prop} {s : CState}
    (hf : ∀ x, Ty.WF s.sz g (f x)) (ht : Ty.WF s.sz g t)
    (h : ∀ r s', s' = s → Ty.WF s.sz g r → Q r s') : Wp (elabTy f t) Q s :=
  elabTy_wp f g t Q s hf ht h

theorem elabTy_leaf0 {f : String → Ty} {t : Ty} {Q : Ty → CState → Prop} {s : CState}
    (hf : ∀ x, Ty.WF s.sz 0 (f x)) (ht : Ty.WF s.sz 0 t)
    (h : ∀ r s', s' = s → Ty.WF s.sz 0 r → Q r s') : Wp (elabTy f t) Q s :=
  elabTy_wp f 0 t Q s hf ht h

macro_rules
  | `(tactic| wp_step) => `(tactic| leaf elabTy_leaf)
macro_rules
  | `(tactic| wp_step) => `(tactic| leaf elabTy_leaf0)

/-! ### Environments -/

/-- A binding in range: monomorphic types have no `gen`, a scheme's `gen`s are below its width. -/
def Bind.WF (sz : Nat) : Bind → Prop
  | .mono t => Ty.WF sz 0 t
  | .poly n t => Ty.WF sz n t
  | .fnMono _ t => Ty.WF sz 0 t
  | .fnPoly _ n t => Ty.WF sz n t

def Env.WF (sz : Nat) (env : Env) : Prop := ∀ p ∈ env, Bind.WF sz p.2

theorem Bind.WF.mono {sz sz' : Nat} {b : Bind} (h : Bind.WF sz b) (hs : sz ≤ sz') :
    Bind.WF sz' b := by
  cases b <;> exact Ty.WF.mono h hs (Nat.le_refl _)

theorem Env.WF.mono {sz sz' : Nat} {env : Env} (h : Env.WF sz env) (hs : sz ≤ sz') :
    Env.WF sz' env := fun p hp => (h p hp).mono hs

theorem Env.WF.cons {sz : Nat} {env : Env} {x : String} {b : Bind} (h : Env.WF sz env)
    (hb : Bind.WF sz b) : Env.WF sz ((x, b) :: env) := by
  intro p hp
  rcases List.mem_cons.mp hp with rfl | hp
  · exact hb
  · exact h p hp

theorem Env.WF.append {sz : Nat} {e1 e2 : Env} (h1 : Env.WF sz e1) (h2 : Env.WF sz e2) :
    Env.WF sz (e1 ++ e2) := by
  intro p hp
  rcases List.mem_append.mp hp with hp | hp
  · exact h1 p hp
  · exact h2 p hp

theorem Env.WF.lookup {sz : Nat} {env : Env} {x : String} {b : Bind} (h : Env.WF sz env)
    (hl : env.lookup x = some b) : Bind.WF sz b := by
  obtain ⟨l1, l2, rfl, _⟩ := List.lookup_eq_some_iff.mp hl
  exact h (x, b) (by simp)

/-! ### Named-free environments (see `Ty.NF`) -/

def Bind.NF : Bind → Prop
  | .mono t => Ty.NF t
  | .poly _ t => Ty.NF t
  | .fnMono _ t => Ty.NF t
  | .fnPoly _ _ t => Ty.NF t

def Env.NF (env : Env) : Prop := ∀ p ∈ env, Bind.NF p.2

theorem Env.NF.cons {env : Env} {x : String} {b : Bind} (h : Env.NF env) (hb : Bind.NF b) :
    Env.NF ((x, b) :: env) := by
  intro p hp
  rcases List.mem_cons.mp hp with rfl | hp
  · exact hb
  · exact h p hp

theorem Env.NF.lookup {env : Env} {x : String} {b : Bind} (h : Env.NF env)
    (hl : env.lookup x = some b) : Bind.NF b := by
  obtain ⟨l1, l2, rfl, _⟩ := List.lookup_eq_some_iff.mp hl
  exact h (x, b) (by simp)

/-! ### Closed environments

What a group leaves behind: closed schemes only. A closed environment is well formed at every
size and named-free, so the prelude's `WF` and `NF` follow from its being closed. -/

mutual
theorem Ty.wf_of_okn {n : Nat} : ∀ (t : Ty), t.ok n = true → ∀ sz, Ty.WF sz n t
  | .named _, h, _ => by simp [Ty.ok] at h
  | .mv _, h, _ => by simp [Ty.ok] at h
  | .gen i, h, _ => by simpa [Ty.ok] using h
  | .skolem _, _, _ => Ty.WF_skolem
  | .int, _, _ => Ty.WF_int
  | .bool, _, _ => Ty.WF_bool
  | .str, _, _ => Ty.WF_str
  | .unit, _, _ => Ty.WF_unit
  | .tup ts, h, sz => by
    simp only [Ty.ok] at h
    exact Ty.WF_tup.mpr (Ty.wfList_of_okn ts h sz)
  | .arr ps r e, h, sz => by
    simp only [Ty.ok, Bool.and_eq_true, Bool.not_eq_true'] at h
    exact Ty.WF_arr.mpr ⟨Ty.wfList_of_okn ps h.1.1 sz, Ty.wf_of_okn r h.1.2 sz⟩
  | .adt _ ts, h, sz => by
    simp only [Ty.ok] at h
    exact Ty.WF_adt.mpr (Ty.wfList_of_okn ts h sz)

theorem Ty.wfList_of_okn {n : Nat} : ∀ (ts : List Ty), Ty.okList n ts = true → ∀ sz,
    ∀ t ∈ ts, Ty.WF sz n t
  | [], _, _ => by simp
  | t :: ts, h, sz => by
    simp only [Ty.okList, Bool.and_eq_true] at h
    intro u hu
    rcases List.mem_cons.mp hu with e | hu
    · rw [e]; exact Ty.wf_of_okn t h.1 sz
    · exact Ty.wfList_of_okn ts h.2 sz u hu
end

-- A type with no `.named`: closed under `n` scheme variables, which `ok n` says.
mutual
theorem Ty.nf_of_ok {n : Nat} : ∀ t : Ty, t.ok n = true → Ty.NF t
  | .named _, h => by simp [Ty.ok] at h
  | .mv _, _ => Ty.NF_mv
  | .gen _, _ => by simp [Ty.NF, namedIn]
  | .skolem _, _ => Ty.NF_skolem
  | .int, _ => Ty.NF_int
  | .bool, _ => Ty.NF_bool
  | .str, _ => Ty.NF_str
  | .unit, _ => Ty.NF_unit
  | .tup ts, h => by
    simp only [Ty.ok] at h
    exact Ty.NF_tup.mpr (Ty.nfList_of_ok ts h)
  | .arr ps r e, h => by
    simp only [Ty.ok, Bool.and_eq_true, Bool.not_eq_true'] at h
    exact Ty.NF_arr.mpr ⟨Ty.nfList_of_ok ps h.1.1, Ty.nf_of_ok r h.1.2⟩
  | .adt _ ts, h => by
    simp only [Ty.ok] at h
    exact Ty.NF_adt.mpr (Ty.nfList_of_ok ts h)

theorem Ty.nfList_of_ok {n : Nat} : ∀ ts : List Ty, Ty.okList n ts = true → ∀ t ∈ ts, Ty.NF t
  | [], _ => by simp
  | t :: ts, h => by
    simp only [Ty.okList, Bool.and_eq_true] at h
    have h1 := Ty.nf_of_ok t h.1
    have h2 := Ty.nfList_of_ok ts h.2
    intro u hu
    rcases List.mem_cons.mp hu with e | hu
    · rw [e]; exact h1
    · exact h2 u hu
end

/-- A top-level binding after its group: a closed scheme. -/
def Bind.Closed : Bind → Prop
  | .mono _ => False
  | .poly n t => t.ok n = true
  | .fnMono _ _ => False
  | .fnPoly _ n t => t.ok n = true

/-- The environment of a group's run holds only closed schemes. -/
def Env.Closed (env : Env) : Prop := ∀ p ∈ env, Bind.Closed p.2

theorem Env.Closed.nil : Env.Closed [] := by intro p hp; cases hp

theorem Env.Closed.cons {env : Env} {x : String} {b : Bind} (h : Env.Closed env)
    (hb : Bind.Closed b) : Env.Closed ((x, b) :: env) := by
  intro p hp
  rcases List.mem_cons.mp hp with rfl | hp
  · exact hb
  · exact h p hp

theorem Env.Closed.append {e1 e2 : Env} (h1 : Env.Closed e1) (h2 : Env.Closed e2) :
    Env.Closed (e1 ++ e2) := by
  intro p hp
  rcases List.mem_append.mp hp with hp | hp
  · exact h1 p hp
  · exact h2 p hp

theorem Bind.Closed.wf {b : Bind} (h : b.Closed) (sz : Nat) : Bind.WF sz b := by
  cases b with
  | mono t => exact False.elim h
  | poly n t => exact Ty.wf_of_okn _ h sz
  | fnMono k t => exact False.elim h
  | fnPoly k n t => exact Ty.wf_of_okn _ h sz

theorem Bind.Closed.nf {b : Bind} (h : b.Closed) : Bind.NF b := by
  cases b with
  | mono t => exact False.elim h
  | poly n t => exact Ty.nf_of_ok _ h
  | fnMono k t => exact False.elim h
  | fnPoly k n t => exact Ty.nf_of_ok _ h

theorem Env.Closed.wf {env : Env} (h : Env.Closed env) (sz : Nat) : Env.WF sz env :=
  fun p hp => (h p hp).wf sz

theorem Env.Closed.nf {env : Env} (h : Env.Closed env) : Env.NF env :=
  fun p hp => (h p hp).nf

theorem Env.Closed.prelude : Env.Closed preludeEnv := by
  intro p hp
  simp only [preludeEnv, List.mem_singleton] at hp
  subst hp
  show Ty.ok 0 (.arr [.str] .unit .io) = true
  decide

theorem Env.WF.prelude {sz : Nat} : Env.WF sz preludeEnv := Env.Closed.prelude.wf sz

theorem Env.NF.prelude : Env.NF preludeEnv := Env.Closed.prelude.nf

/-! ### Source terms: the types a program writes mention no metavariable and no `gen`. -/

mutual
def Expr.Src : Expr → Prop
  | .var _ => True
  | .int _ => True
  | .bool _ => True
  | .str _ => True
  | .unit => True
  | .lam _ ann b => (∀ t, ann = some t → Ty.WF 0 0 t) ∧ b.Src
  | .app f as => f.Src ∧ Expr.SrcList as
  | .letIn _ r b => r.Src ∧ b.Src
  | .ite c t e => c.Src ∧ t.Src ∧ e.Src
  | .tup es => Expr.SrcList es
  | .notE e => e.Src
  | .doB steps f => Step.SrcList steps ∧ f.Src
  -- stage 2a: the invariants cover programs without constructors and `match` until 2c
  | .ctor _ => False
  | .matchE _ _ => False

def Expr.SrcList : List Expr → Prop
  | [] => True
  | e :: es => e.Src ∧ Expr.SrcList es

def Step.SrcList : List Step → Prop
  | [] => True
  | .letS _ r :: rest => r.Src ∧ Step.SrcList rest
  | .exprS e :: rest => e.Src ∧ Step.SrcList rest
end

macro_rules
  | `(tactic| wf_close) => `(tactic| (intro _; exact Ty.WF_unit))
macro_rules
  | `(tactic| wf_close) =>
    `(tactic| (refine Ty.WF.arr_params ‹_› ?_ _ (List.mem_cons_self ..); omega))
macro_rules
  | `(tactic| wf_close) => `(tactic| (refine Env.WF.mono ‹_› ?_; omega))
macro_rules
  | `(tactic| wf_close) => `(tactic| (apply Env.WF.cons <;> wf_close))
macro_rules
  | `(tactic| wf_close) => `(tactic| (simp only [Bind.WF]; wf_close))

theorem arityCheck_wp {Q : PUnit → CState → Prop} {s : CState} {env : Env} {f : Expr} {n : Nat}
    (hs : s.WF) (h : ∀ s', s.sz ≤ s'.sz → s'.WF → Q ⟨⟩ s') : Wp (arityCheck env f n) Q s := by
  unfold arityCheck
  split
  all_goals repeat' wp_step
  all_goals (refine h _ ?_ ?_ <;> wf_close)

macro_rules
  | `(tactic| wp_step) => `(tactic| leaf arityCheck_wp)

theorem envMetas_wp {Q : List Nat → CState → Prop} {s : CState} {env : Env} (hs : s.WF)
    (he : Env.WF s.sz env) (h : ∀ ms s', s.sz ≤ s'.sz → s'.WF → Q ms s') :
    Wp (envMetas env) Q s := by
  unfold envMetas
  dsimp only
  refine Wp.bind (Wp.forIn_list (Q := fun acc s1 => Wp (pure acc) Q s1)
    (fun (_ : List Nat) s1 => s1.WF ∧ s.sz ≤ s1.sz) env [] ⟨hs, Nat.le_refl _⟩ ?_ ?_)
  · intro a ha b s1 ⟨h1, g1⟩
    obtain ⟨x, bd⟩ := a
    have hb : Bind.WF s.sz bd := he _ ha
    cases bd <;> simp only [Bind.WF] at hb <;> dsimp only <;> repeat' wp_step
    all_goals (constructor <;> wf_close)
  · intro acc s1 ⟨h1, g1⟩
    exact Wp.pure' (h _ _ g1 h1)

macro_rules
  | `(tactic| wp_step) => `(tactic| leaf envMetas_wp)

theorem bindLocal_wp {Q : Env → CState → Prop} {s : CState} {rules : Rules} {env : Env}
    {x : String} {t : Ty} (hs : s.WF) (he : Env.WF s.sz env) (ht : Ty.WF s.sz 0 t)
    (h : ∀ env' s', s.sz ≤ s'.sz → s'.WF → Env.WF s'.sz env' → Q env' s') :
    Wp (bindLocal rules env x t) Q s := by
  unfold bindLocal
  split
  all_goals repeat' wp_step
  all_goals (refine h _ _ ?_ ?_ ?_ <;> wf_close)

theorem lamParam_wp {Q : Ty → CState → Prop} {s : CState} {slot ann : Option Ty} (hs : s.WF)
    (hslot : ∀ t, slot = some t → Ty.WF s.sz 0 t) (hann : ∀ t, ann = some t → Ty.WF 0 0 t)
    (h : ∀ r s', s.sz ≤ s'.sz → s'.WF → Ty.WF s'.sz 0 r → Q r s') :
    Wp (lamParam slot ann) Q s := by
  rcases ann with _ | a0 <;> rcases slot with _ | s0 <;> unfold lamParam <;> dsimp only
  all_goals (try have := hann a0 rfl)
  all_goals (try have := hslot s0 rfl)
  all_goals repeat' wp_step
  all_goals (refine h _ _ ?_ ?_ ?_ <;> wf_close)

macro_rules
  | `(tactic| wp_step) => `(tactic| leaf lamParam_wp)

macro_rules
  | `(tactic| wp_step) => `(tactic| leaf bindLocal_wp)

macro_rules
  | `(tactic| wf_close) =>
    `(tactic| (have hb := Env.WF.lookup ‹_› ‹List.lookup _ _ = _›; simp only [Bind.WF] at hb;
               first | assumption | (refine Ty.WF.mono ‹_› ?_ ?_ <;> omega)))
macro_rules
  | `(tactic| wf_close) => `(tactic| (intro _ h; cases h; done))
macro_rules
  | `(tactic| wf_close) =>
    `(tactic| (intro t ht; have h1 := List.mem_singleton.mp ht; subst h1; wf_close))
macro_rules
  | `(tactic| wf_close) =>
    `(tactic| (intro t ht; have h1 := Option.some.inj ht; subst h1; wf_close))

theorem Ty.WF.list_cons {sz : Nat} {t : Ty} {ts : List Ty} (h1 : Ty.WF sz 0 t)
    (h2 : ∀ u ∈ ts, Ty.WF sz 0 u) : ∀ u ∈ t :: ts, Ty.WF sz 0 u := by
  intro u hu
  rcases List.mem_cons.mp hu with rfl | hu
  · exact h1
  · exact h2 u hu

macro_rules
  | `(tactic| wf_close) => `(tactic| (refine Ty.WF.list_cons ?_ ?_ <;> wf_close))

/-! ### Expressions -/

mutual
theorem infer_wp (rules : Rules) : ∀ (e : Expr) (env : Env) (Q : Ty × Eff → CState → Prop)
    (s : CState), s.WF → Env.WF s.sz env → e.Src →
    (∀ t eff s', s.sz ≤ s'.sz → s'.WF → Ty.WF s'.sz 0 t → Q (t, eff) s') →
    Wp (infer rules env e) Q s
  | .var x, env, Q, s, hs, he, _, h => by
    unfold infer
    split
    all_goals repeat' wp_step
    all_goals (refine h _ _ _ ?_ ?_ ?_ <;> wf_close)
  | .int _, env, Q, s, hs, he, _, h => by
    unfold infer; exact Wp.pure' (h _ _ _ (Nat.le_refl _) hs Ty.WF_int)
  | .bool _, env, Q, s, hs, he, _, h => by
    unfold infer; exact Wp.pure' (h _ _ _ (Nat.le_refl _) hs Ty.WF_bool)
  | .str _, env, Q, s, hs, he, _, h => by
    unfold infer; exact Wp.pure' (h _ _ _ (Nat.le_refl _) hs Ty.WF_str)
  | .unit, env, Q, s, hs, he, _, h => by
    unfold infer; exact Wp.pure' (h _ _ _ (Nat.le_refl _) hs Ty.WF_unit)
  | .lam x ann body, env, Q, s, hs, he, hsrc, h => by
    obtain ⟨hann, hbody⟩ := hsrc
    have inferK : ∀ {env : Env} {Q : Ty × Eff → CState → Prop} {s : CState} (hs : s.WF)
        (he : Env.WF s.sz env) (hbody : body.Src)
        (h : ∀ t eff s', s.sz ≤ s'.sz → s'.WF → Ty.WF s'.sz 0 t → Q (t, eff) s'),
        Wp (infer rules env body) Q s := fun hs he hb h => infer_wp rules body _ _ _ hs he hb h
    unfold infer
    repeat' (first | wp_step | leaf inferK)
    all_goals (refine h _ _ _ ?_ ?_ ?_ <;> wf_close)
  | .app f args, env, Q, s, hs, he, hsrc, h => by
    obtain ⟨hf, hargs⟩ := hsrc
    have inferF : ∀ {env : Env} {Q : Ty × Eff → CState → Prop} {s : CState} (hs : s.WF)
        (he : Env.WF s.sz env) (hf : f.Src)
        (h : ∀ t eff s', s.sz ≤ s'.sz → s'.WF → Ty.WF s'.sz 0 t → Q (t, eff) s'),
        Wp (infer rules env f) Q s := fun hs he hb h => infer_wp rules f _ _ _ hs he hb h
    have plainK : ∀ {slots : List Ty} {eff : Eff} {Q : Eff → CState → Prop} {s : CState}
        (hs : s.WF) (he : Env.WF s.sz env) (hargs : Expr.SrcList args)
        (hsl : ∀ t ∈ slots, Ty.WF s.sz 0 t)
        (h : ∀ eff' s', s.sz ≤ s'.sz → s'.WF → Q eff' s'),
        Wp (inferPlainArgs rules env args slots eff) Q s :=
      fun hs he ha hsl h => inferPlainArgs_wp rules env args _ _ _ _ hs he ha hsl h
    have lamK : ∀ {slots : List Ty} {eff : Eff} {Q : Eff → CState → Prop} {s : CState}
        (hs : s.WF) (he : Env.WF s.sz env) (hargs : Expr.SrcList args)
        (hsl : ∀ t ∈ slots, Ty.WF s.sz 0 t)
        (h : ∀ eff' s', s.sz ≤ s'.sz → s'.WF → Q eff' s'),
        Wp (inferLamArgs rules env args slots eff) Q s :=
      fun hs he ha hsl h => inferLamArgs_wp rules env args _ _ _ _ hs he ha hsl h
    unfold infer
    repeat' (first | wp_step | leaf inferF | leaf plainK | leaf lamK)
    all_goals (refine h _ _ _ ?_ ?_ ?_ <;> wf_close)
  | .letIn x rhs body, env, Q, s, hs, he, hsrc, h => by
    obtain ⟨hr, hb⟩ := hsrc
    have inferR : ∀ {env : Env} {Q : Ty × Eff → CState → Prop} {s : CState} (hs : s.WF)
        (he : Env.WF s.sz env) (hr : rhs.Src)
        (h : ∀ t eff s', s.sz ≤ s'.sz → s'.WF → Ty.WF s'.sz 0 t → Q (t, eff) s'),
        Wp (infer rules env rhs) Q s := fun hs he hb h => infer_wp rules rhs _ _ _ hs he hb h
    have inferB : ∀ {env : Env} {Q : Ty × Eff → CState → Prop} {s : CState} (hs : s.WF)
        (he : Env.WF s.sz env) (hb : body.Src)
        (h : ∀ t eff s', s.sz ≤ s'.sz → s'.WF → Ty.WF s'.sz 0 t → Q (t, eff) s'),
        Wp (infer rules env body) Q s := fun hs he hb h => infer_wp rules body _ _ _ hs he hb h
    unfold infer
    repeat' (first | wp_step | leaf inferR | leaf inferB)
    all_goals (refine h _ _ _ ?_ ?_ ?_ <;> wf_close)
  | .ite c t e, env, Q, s, hs, he, hsrc, h => by
    obtain ⟨hc, ht, he'⟩ := hsrc
    have inferC : ∀ {env : Env} {Q : Ty × Eff → CState → Prop} {s : CState} (hs : s.WF)
        (he : Env.WF s.sz env) (hc : c.Src)
        (h : ∀ t eff s', s.sz ≤ s'.sz → s'.WF → Ty.WF s'.sz 0 t → Q (t, eff) s'),
        Wp (infer rules env c) Q s := fun hs he hb h => infer_wp rules c _ _ _ hs he hb h
    have inferT : ∀ {env : Env} {Q : Ty × Eff → CState → Prop} {s : CState} (hs : s.WF)
        (he : Env.WF s.sz env) (ht : t.Src)
        (h : ∀ t eff s', s.sz ≤ s'.sz → s'.WF → Ty.WF s'.sz 0 t → Q (t, eff) s'),
        Wp (infer rules env t) Q s := fun hs he hb h => infer_wp rules t _ _ _ hs he hb h
    have inferE : ∀ {env : Env} {Q : Ty × Eff → CState → Prop} {s : CState} (hs : s.WF)
        (he : Env.WF s.sz env) (he' : e.Src)
        (h : ∀ t eff s', s.sz ≤ s'.sz → s'.WF → Ty.WF s'.sz 0 t → Q (t, eff) s'),
        Wp (infer rules env e) Q s := fun hs he hb h => infer_wp rules e _ _ _ hs he hb h
    unfold infer
    repeat' (first | wp_step | leaf inferC | leaf inferT | leaf inferE)
    all_goals (refine h _ _ _ ?_ ?_ ?_ <;> wf_close)
  | .tup es, env, Q, s, hs, he, hsrc, h => by
    have tupleK : ∀ {Q : List Ty × Eff → CState → Prop} {s : CState} (hs : s.WF)
        (he : Env.WF s.sz env) (hes : Expr.SrcList es)
        (h : ∀ ts eff s', s.sz ≤ s'.sz → s'.WF → (∀ t ∈ ts, Ty.WF s'.sz 0 t) → Q (ts, eff) s'),
        Wp (inferTuple rules env es) Q s :=
      fun hs he hes h => inferTuple_wp rules env es _ _ hs he hes h
    unfold infer
    repeat' (first | wp_step | leaf tupleK)
    all_goals (refine h _ _ _ ?_ ?_ ?_ <;> wf_close)
  | .notE e, env, Q, s, hs, he, hsrc, h => by
    have inferE : ∀ {env : Env} {Q : Ty × Eff → CState → Prop} {s : CState} (hs : s.WF)
        (he : Env.WF s.sz env) (he' : e.Src)
        (h : ∀ t eff s', s.sz ≤ s'.sz → s'.WF → Ty.WF s'.sz 0 t → Q (t, eff) s'),
        Wp (infer rules env e) Q s := fun hs he hb h => infer_wp rules e _ _ _ hs he hb h
    unfold infer
    repeat' (first | wp_step | leaf inferE)
    all_goals (refine h _ _ _ ?_ ?_ ?_ <;> wf_close)
  | .doB steps final, env, Q, s, hs, he, hsrc, h => by
    obtain ⟨hst, hfin⟩ := hsrc
    have stepsK : ∀ {Q : Env × Eff → CState → Prop} {s : CState} (hs : s.WF)
        (he : Env.WF s.sz env) (hst : Step.SrcList steps)
        (h : ∀ env' eff s', s.sz ≤ s'.sz → s'.WF → Env.WF s'.sz env' → Q (env', eff) s'),
        Wp (inferSteps rules env steps) Q s :=
      fun hs he hst h => inferSteps_wp rules env steps _ _ hs he hst h
    have inferF : ∀ {env : Env} {Q : Ty × Eff → CState → Prop} {s : CState} (hs : s.WF)
        (he : Env.WF s.sz env) (hfin : final.Src)
        (h : ∀ t eff s', s.sz ≤ s'.sz → s'.WF → Ty.WF s'.sz 0 t → Q (t, eff) s'),
        Wp (infer rules env final) Q s := fun hs he hb h => infer_wp rules final _ _ _ hs he hb h
    unfold infer
    repeat' (first | wp_step | leaf stepsK | leaf inferF)
    all_goals (refine h _ _ _ ?_ ?_ ?_ <;> wf_close)
  | .ctor _, _, _, _, _, _, hsrc, _ => hsrc.elim
  | .matchE _ _, _, _, _, _, _, hsrc, _ => hsrc.elim

theorem inferPlainArgs_wp (rules : Rules) (env : Env) :
    ∀ (args : List Expr) (slots : List Ty) (eff : Eff) (Q : Eff → CState → Prop) (s : CState),
    s.WF → Env.WF s.sz env → Expr.SrcList args → (∀ t ∈ slots, Ty.WF s.sz 0 t) →
    (∀ eff' s', s.sz ≤ s'.sz → s'.WF → Q eff' s') →
    Wp (inferPlainArgs rules env args slots eff) Q s
  | a :: as, s0 :: ss, eff, Q, s, hs, he, hsrc, hsl, h => by
    obtain ⟨ha, has⟩ := hsrc
    have inferA : ∀ {Q : Ty × Eff → CState → Prop} {s : CState} (hs : s.WF)
        (he : Env.WF s.sz env) (ha : a.Src)
        (h : ∀ t eff s', s.sz ≤ s'.sz → s'.WF → Ty.WF s'.sz 0 t → Q (t, eff) s'),
        Wp (infer rules env a) Q s := fun hs he hb h => infer_wp rules a env _ _ hs he hb h
    have plainK : ∀ {eff : Eff} {Q : Eff → CState → Prop} {s : CState}
        (hs : s.WF) (he : Env.WF s.sz env) (has : Expr.SrcList as)
        (hsl : ∀ t ∈ ss, Ty.WF s.sz 0 t)
        (h : ∀ eff' s', s.sz ≤ s'.sz → s'.WF → Q eff' s'),
        Wp (inferPlainArgs rules env as ss eff) Q s :=
      fun hs he ha hsl h => inferPlainArgs_wp rules env as ss _ _ _ hs he ha hsl h
    have hs0 : Ty.WF s.sz 0 s0 := hsl s0 (List.mem_cons_self ..)
    have hss : ∀ t ∈ ss, Ty.WF s.sz 0 t := fun t ht => hsl t (List.mem_cons_of_mem _ ht)
    unfold inferPlainArgs
    split
    · exact plainK hs he has hss h
    · repeat' (first | wp_step | leaf inferA | leaf plainK)
      all_goals (refine h _ _ ?_ ?_ <;> wf_close)
  | [], _, eff, Q, s, hs, he, hsrc, hsl, h => by
    unfold inferPlainArgs; exact Wp.pure' (h _ _ (Nat.le_refl _) hs)
  | _ :: _, [], eff, Q, s, hs, he, hsrc, hsl, h => by
    unfold inferPlainArgs; exact Wp.pure' (h _ _ (Nat.le_refl _) hs)

theorem inferLamArgs_wp (rules : Rules) (env : Env) :
    ∀ (args : List Expr) (slots : List Ty) (eff : Eff) (Q : Eff → CState → Prop) (s : CState),
    s.WF → Env.WF s.sz env → Expr.SrcList args → (∀ t ∈ slots, Ty.WF s.sz 0 t) →
    (∀ eff' s', s.sz ≤ s'.sz → s'.WF → Q eff' s') →
    Wp (inferLamArgs rules env args slots eff) Q s
  | a :: as, s0 :: ss, eff, Q, s, hs, he, hsrc, hsl, h => by
    obtain ⟨ha, has⟩ := hsrc
    have lamK : ∀ {eff : Eff} {Q : Eff → CState → Prop} {s : CState}
        (hs : s.WF) (he : Env.WF s.sz env) (has : Expr.SrcList as)
        (hsl : ∀ t ∈ ss, Ty.WF s.sz 0 t)
        (h : ∀ eff' s', s.sz ≤ s'.sz → s'.WF → Q eff' s'),
        Wp (inferLamArgs rules env as ss eff) Q s :=
      fun hs he ha hsl h => inferLamArgs_wp rules env as ss _ _ _ hs he ha hsl h
    have hs0 : Ty.WF s.sz 0 s0 := hsl s0 (List.mem_cons_self ..)
    have hss : ∀ t ∈ ss, Ty.WF s.sz 0 t := fun t ht => hsl t (List.mem_cons_of_mem _ ht)
    cases a with
    | lam x ann body =>
      obtain ⟨hann, hbody⟩ := ha
      have inferK : ∀ {env : Env} {Q : Ty × Eff → CState → Prop} {s : CState} (hs : s.WF)
          (he : Env.WF s.sz env) (hbody : body.Src)
          (h : ∀ t eff s', s.sz ≤ s'.sz → s'.WF → Ty.WF s'.sz 0 t → Q (t, eff) s'),
          Wp (infer rules env body) Q s := fun hs he hb h => infer_wp rules body _ _ _ hs he hb h
      simp only [inferLamArgs]
      repeat' (first | wp_step | leaf inferK | leaf lamK)
      all_goals (refine h _ _ ?_ ?_ <;> wf_close)
    | _ =>
      simp only [inferLamArgs]
      exact lamK hs he has hss h
  | [], _, eff, Q, s, hs, he, hsrc, hsl, h => by
    simp only [inferLamArgs]; exact Wp.pure' (h _ _ (Nat.le_refl _) hs)
  | _ :: _, [], eff, Q, s, hs, he, hsrc, hsl, h => by
    simp only [inferLamArgs]; exact Wp.pure' (h _ _ (Nat.le_refl _) hs)

theorem inferTuple_wp (rules : Rules) (env : Env) :
    ∀ (es : List Expr) (Q : List Ty × Eff → CState → Prop) (s : CState),
    s.WF → Env.WF s.sz env → Expr.SrcList es →
    (∀ ts eff s', s.sz ≤ s'.sz → s'.WF → (∀ t ∈ ts, Ty.WF s'.sz 0 t) → Q (ts, eff) s') →
    Wp (inferTuple rules env es) Q s
  | [], Q, s, hs, he, hsrc, h => by
    unfold inferTuple; exact Wp.pure' (h _ _ _ (Nat.le_refl _) hs (by simp))
  | e :: es, Q, s, hs, he, hsrc, h => by
    obtain ⟨he', hes⟩ := hsrc
    have inferE : ∀ {Q : Ty × Eff → CState → Prop} {s : CState} (hs : s.WF)
        (he : Env.WF s.sz env) (he' : e.Src)
        (h : ∀ t eff s', s.sz ≤ s'.sz → s'.WF → Ty.WF s'.sz 0 t → Q (t, eff) s'),
        Wp (infer rules env e) Q s := fun hs he hb h => infer_wp rules e env _ _ hs he hb h
    have tupleK : ∀ {Q : List Ty × Eff → CState → Prop} {s : CState} (hs : s.WF)
        (he : Env.WF s.sz env) (hes : Expr.SrcList es)
        (h : ∀ ts eff s', s.sz ≤ s'.sz → s'.WF → (∀ t ∈ ts, Ty.WF s'.sz 0 t) → Q (ts, eff) s'),
        Wp (inferTuple rules env es) Q s :=
      fun hs he hes h => inferTuple_wp rules env es _ _ hs he hes h
    unfold inferTuple
    repeat' (first | wp_step | leaf inferE | leaf tupleK)
    all_goals (refine h _ _ _ ?_ ?_ ?_ <;> wf_close)

theorem inferSteps_wp (rules : Rules) (env : Env) :
    ∀ (steps : List Step) (Q : Env × Eff → CState → Prop) (s : CState),
    s.WF → Env.WF s.sz env → Step.SrcList steps →
    (∀ env' eff s', s.sz ≤ s'.sz → s'.WF → Env.WF s'.sz env' → Q (env', eff) s') →
    Wp (inferSteps rules env steps) Q s
  | [], Q, s, hs, he, hsrc, h => by
    unfold inferSteps; exact Wp.pure' (h _ _ _ (Nat.le_refl _) hs he)
  | .letS x rhs :: rest, Q, s, hs, he, hsrc, h => by
    obtain ⟨hr, hrest⟩ := hsrc
    have inferR : ∀ {env : Env} {Q : Ty × Eff → CState → Prop} {s : CState} (hs : s.WF)
        (he : Env.WF s.sz env) (hr : rhs.Src)
        (h : ∀ t eff s', s.sz ≤ s'.sz → s'.WF → Ty.WF s'.sz 0 t → Q (t, eff) s'),
        Wp (infer rules env rhs) Q s := fun hs he hb h => infer_wp rules rhs _ _ _ hs he hb h
    have stepsK : ∀ {env : Env} {Q : Env × Eff → CState → Prop} {s : CState} (hs : s.WF)
        (he : Env.WF s.sz env) (hrest : Step.SrcList rest)
        (h : ∀ env' eff s', s.sz ≤ s'.sz → s'.WF → Env.WF s'.sz env' → Q (env', eff) s'),
        Wp (inferSteps rules env rest) Q s :=
      fun hs he hst h => inferSteps_wp rules _ rest _ _ hs he hst h
    unfold inferSteps
    repeat' (first | wp_step | leaf inferR | leaf stepsK)
    all_goals (refine h _ _ _ ?_ ?_ ?_ <;> wf_close)
  | .exprS e :: rest, Q, s, hs, he, hsrc, h => by
    obtain ⟨he', hrest⟩ := hsrc
    have inferE : ∀ {env : Env} {Q : Ty × Eff → CState → Prop} {s : CState} (hs : s.WF)
        (he : Env.WF s.sz env) (he' : e.Src)
        (h : ∀ t eff s', s.sz ≤ s'.sz → s'.WF → Ty.WF s'.sz 0 t → Q (t, eff) s'),
        Wp (infer rules env e) Q s := fun hs he hb h => infer_wp rules e _ _ _ hs he hb h
    have stepsK : ∀ {env : Env} {Q : Env × Eff → CState → Prop} {s : CState} (hs : s.WF)
        (he : Env.WF s.sz env) (hrest : Step.SrcList rest)
        (h : ∀ env' eff s', s.sz ≤ s'.sz → s'.WF → Env.WF s'.sz env' → Q (env', eff) s'),
        Wp (inferSteps rules env rest) Q s :=
      fun hs he hst h => inferSteps_wp rules _ rest _ _ hs he hst h
    unfold inferSteps
    repeat' (first | wp_step | leaf inferE | leaf stepsK)
    all_goals (refine h _ _ _ ?_ ?_ ?_ <;> wf_close)
end

theorem infer_leaf {Q : Ty × Eff → CState → Prop} {s : CState} {rules : Rules} {env : Env}
    {e : Expr} (hs : s.WF) (he : Env.WF s.sz env) (hsrc : e.Src)
    (h : ∀ t eff s', s.sz ≤ s'.sz → s'.WF → Ty.WF s'.sz 0 t → Q (t, eff) s') :
    Wp (infer rules env e) Q s := infer_wp rules e env Q s hs he hsrc h

macro_rules
  | `(tactic| wp_step) => `(tactic| leaf infer_leaf)

/-! ### Declarations -/

/-- A function's written types mention no metavariable and no `gen`. -/
def FnInfo.Src (f : FnInfo) : Prop :=
  (∀ p ∈ f.params, ∀ t, p.2 = some t → Ty.WF 0 0 t) ∧
  (∀ r e, f.sig = some (r, e) → Ty.WF 0 0 r) ∧ f.body.Src

def Node.Src : Node → Prop
  | .fnN f => f.Src
  | .letN _ ann init => (∀ a, ann = some a → Ty.WF 0 0 a) ∧ init.Src

/-- What `runPlan` needs of a plan: its nodes and its `main`s are source terms. -/
def Plan.Src (pl : Plan) : Prop :=
  (∀ nd ∈ pl.nodes.toList, Node.Src nd) ∧
  (∀ m ∈ pl.mains, (∀ r e, m.2.1 = some (r, e) → Ty.WF 0 0 r) ∧ m.2.2.Src)

theorem Node.Src_default : Node.Src (default : Node) := by
  show Node.Src (Node.fnN default)
  have h1 : (default : FnInfo).params = [] := rfl
  have h2 : (default : FnInfo).sig = none := rfl
  have h3 : (default : FnInfo).body = Expr.var "" := rfl
  refine ⟨?_, ?_, ?_⟩
  · intro p hp; rw [h1] at hp; cases hp
  · intro r e h; rw [h2] at h; cases h
  · rw [h3]; trivial

theorem build_wp {Q : List Ty × Ty × Eff × Ty → CState → Prop} {s : CState} {f : FnInfo}
    {m : String → Ty} {g : Nat} (hs : s.WF) (hm : ∀ x, Ty.WF s.sz g (m x)) (hf : f.Src)
    (h : ∀ ps r e t s', s.sz ≤ s'.sz → s'.WF → (∀ x ∈ ps, Ty.WF s'.sz g x) → Ty.WF s'.sz g r →
      Ty.WF s'.sz g t → Q (ps, r, e, t) s') :
    Wp (f.build m) Q s := by
  unfold FnInfo.build
  dsimp only
  refine Wp.bind (Wp.forIn_list (Q := fun ps s1 => Wp (do
      let (r, e) ← match f.sig with
        | some (t, e) => do pure (← elabTy m t, e)
        | none => do pure (← fresh, Eff.pure)
      match f.sig with
      | some (.arr .., .io) => reject "ill-formed" "effect on a function returning a function"
      | _ => pure ()
      pure (ps, r, e, mkArr ps r e)) Q s1)
    (fun (ps : List Ty) s1 => s1.WF ∧ s.sz ≤ s1.sz ∧ ∀ x ∈ ps, Ty.WF s1.sz g x)
    f.params [] ⟨hs, Nat.le_refl _, by simp⟩ ?_ ?_)
  · intro a ha ps s1 ⟨h1, g1, hp⟩
    obtain ⟨x, t⟩ := a
    have hm1 : ∀ x, Ty.WF s1.sz g (m x) := fun x => (hm x).mono g1 (Nat.le_refl _)
    have hann : ∀ t0, t = some t0 → Ty.WF 0 0 t0 := hf.1 (x, t) ha
    cases t with
    | none =>
      dsimp only
      repeat' wp_step
      all_goals ((try dsimp only); refine ⟨by wf_close, by wf_close, ?_⟩; intro y hx
                 rcases List.mem_append.mp hx with hx | hx
                 · exact (hp y hx).mono (by omega) (Nat.le_refl _)
                 · have := List.mem_singleton.mp hx; subst this; wf_close)
    | some t0 =>
      have ht0 := hann t0 rfl
      dsimp only
      repeat' wp_step
      all_goals ((try dsimp only); refine ⟨by wf_close, by wf_close, ?_⟩; intro y hx
                 rcases List.mem_append.mp hx with hx | hx
                 · exact (hp y hx).mono (by omega) (Nat.le_refl _)
                 · have := List.mem_singleton.mp hx; subst this; wf_close)
  · intro ps s1 ⟨h1, g1, hp⟩
    have hm1 : ∀ x, Ty.WF s1.sz g (m x) := fun x => (hm x).mono g1 (Nat.le_refl _)
    rcases hsig : f.sig with _ | ⟨t0, e0⟩
    · dsimp only
      repeat' wp_step
      all_goals (refine h _ _ _ _ _ ?_ ?_ ?_ ?_ ?_ <;> wf_close)
    · have ht0 := hf.2.1 t0 e0 hsig
      dsimp only
      repeat' wp_step
      all_goals (refine h _ _ _ _ _ ?_ ?_ ?_ ?_ ?_ <;> wf_close)

theorem build_leaf0 {Q : List Ty × Ty × Eff × Ty → CState → Prop} {s : CState} {f : FnInfo}
    {m : String → Ty} (hs : s.WF) (hm : ∀ x, Ty.WF s.sz 0 (m x)) (hf : f.Src)
    (h : ∀ ps r e t s', s.sz ≤ s'.sz → s'.WF → (∀ x ∈ ps, Ty.WF s'.sz 0 x) → Ty.WF s'.sz 0 r →
      Ty.WF s'.sz 0 t → Q (ps, r, e, t) s') :
    Wp (f.build m) Q s := build_wp hs hm hf h

macro_rules
  | `(tactic| wp_step) => `(tactic| leaf build_wp)
macro_rules
  | `(tactic| wp_step) => `(tactic| leaf build_leaf0)

theorem writtenMap_wf (vs : List String) (sz : Nat) (x : String) :
    Ty.WF sz vs.length
      (match vs.findIdx? (· == x) with | some i => Ty.gen i | none => Ty.unit) := by
  split
  · rename_i i hi
    exact Ty.WF_gen.mpr (List.findIdx?_eq_some_iff_findIdx_eq.mp hi).1
  · exact Ty.WF_unit

theorem scheme_wp {Q : Nat × Ty → CState → Prop} {s : CState} {f : FnInfo} (hs : s.WF)
    (hf : f.Src)
    (h : ∀ n t s', s.sz ≤ s'.sz → s'.WF → Ty.WF s'.sz n t → Q (n, t) s') :
    Wp f.scheme Q s := by
  unfold FnInfo.scheme
  dsimp only
  refine Wp.bind (build_wp (g := f.writtenVars.length) hs (writtenMap_wf _ _) hf ?_)
  intro ps r e t s' g' h' hps hr ht
  exact Wp.pure' (h _ _ _ g' h' ht)

macro_rules
  | `(tactic| wp_step) => `(tactic| leaf scheme_wp)

theorem foldl_wf {sz : Nat} : ∀ (params : List (String × Ty)) (env : Env), Env.WF sz env →
    (∀ p ∈ params, Ty.WF sz 0 p.2) →
    Env.WF sz (params.foldl (fun env (x, t) => (x, Bind.mono t) :: env) env)
  | [], env, he, _ => he
  | (x, t) :: ps, env, he, hp => by
    simp only [List.foldl_cons]
    exact foldl_wf ps _ (he.cons (x := x) (b := .mono t) (hp (x, t) (List.mem_cons_self ..)))
      (fun p hp' => hp p (List.mem_cons_of_mem _ hp'))

theorem checkBody_wp {Q : PUnit → CState → Prop} {s : CState} {rules : Rules} {env : Env}
    {params : List (String × Ty)} {ret : Ty} {eff : Eff} {body : Expr} (hs : s.WF)
    (he : Env.WF s.sz env) (hp : ∀ p ∈ params, Ty.WF s.sz 0 p.2) (hr : Ty.WF s.sz 0 ret)
    (hb : body.Src) (h : ∀ s', s.sz ≤ s'.sz → s'.WF → Q ⟨⟩ s') :
    Wp (checkBody rules env params ret eff body) Q s := by
  unfold checkBody
  dsimp only
  have hfold := foldl_wf params env he hp
  repeat' wp_step
  all_goals (refine h _ ?_ ?_ <;> wf_close)

macro_rules
  | `(tactic| wp_step) => `(tactic| leaf checkBody_wp)

theorem lookupD_wf {sz sz0 g : Nat} {sks : List (String × Ty)}
    (h : ∀ q ∈ sks, Ty.WF sz0 g q.2) (hs : sz0 ≤ sz) (x : String) :
    Ty.WF sz g ((sks.lookup x).getD .unit) := by
  cases hl : sks.lookup x with
  | none => exact Ty.WF_unit
  | some b =>
    obtain ⟨l1, l2, hsks, _⟩ := List.lookup_eq_some_iff.mp hl
    exact (h (x, b) (by rw [hsks]; simp)).mono hs (Nat.le_refl _)

macro_rules
  | `(tactic| wf_close) => `(tactic| (refine lookupD_wf ‹_› ?_; omega))

/-- One fresh variable per name (a top-level `let` annotation's type variables). -/
theorem fresh_pairs_wp {vs : List String} {Q : List (String × Ty) → CState → Prop} {s : CState}
    (hs : s.WF)
    (h : ∀ sks s', s.sz ≤ s'.sz → s'.WF → (∀ q ∈ sks, Ty.WF s'.sz 0 q.2) → Q sks s') :
    Wp (forIn vs ([] : List (String × Ty)) fun v sks => do
      let d ← fresh
      pure (ForInStep.yield (sks ++ [(v, d)]))) Q s := by
  refine Wp.forIn_list (fun (sks : List (String × Ty)) s1 =>
    s1.WF ∧ s.sz ≤ s1.sz ∧ ∀ q ∈ sks, Ty.WF s1.sz 0 q.2) vs [] ⟨hs, Nat.le_refl _, by simp⟩ ?_ ?_
  · intro a ha sks s1 ⟨h1, g1, hp⟩
    refine Wp.bind (fresh_wp h1 ?_)
    intro d s2 hd g2 h2
    refine Wp.pure' ⟨h2, by omega, ?_⟩
    intro q hq
    rcases List.mem_append.mp hq with hq | hq
    · exact (hp q hq).mono (by omega) (Nat.le_refl _)
    · have := List.mem_singleton.mp hq
      subst this
      exact hd
  · intro sks s1 ⟨h1, g1, hp⟩
    exact h sks s1 g1 h1 hp

macro_rules
  | `(tactic| wp_step) => `(tactic| leaf fresh_pairs_wp)

theorem inferLet_wp {Q : Env → CState → Prop} {s : CState} {rules : Rules} {base : Env}
    {name : String} {ann : Option Ty} {init : Expr} (hs : s.WF) (hbase : Env.WF s.sz base)
    (hann : ∀ a, ann = some a → Ty.WF 0 0 a) (hinit : init.Src)
    (h : ∀ env' s', s.sz ≤ s'.sz → s'.WF → Env.WF s'.sz env' → Q env' s') :
    Wp (inferLet rules base name ann init) Q s := by
  unfold inferLet
  dsimp only
  refine Wp.bind (infer_leaf hs hbase hinit ?_)
  intro t eff s1 g1 h1 ht
  dsimp only
  rcases ann with _ | a0
  · repeat' wp_step
    all_goals (refine h _ _ ?_ ?_ ?_ <;> wf_close)
  · have ha0 := hann a0 rfl
    repeat' wp_step
    all_goals (refine h _ _ ?_ ?_ ?_ <;> wf_close)

macro_rules
  | `(tactic| wp_step) => `(tactic| leaf inferLet_wp)

/-- One fresh skolem per written type variable. -/
theorem freshSkolem_pairs_wp {vs : List String} {Q : List (String × Ty) → CState → Prop}
    {s : CState} (hs : s.WF)
    (h : ∀ sks s', s.sz ≤ s'.sz → s'.WF → (∀ q ∈ sks, ∀ sz g, Ty.WF sz g q.2) → Q sks s') :
    Wp (forIn vs ([] : List (String × Ty)) fun v sks => do
      let d ← freshSkolem
      pure (ForInStep.yield (sks ++ [(v, d)]))) Q s := by
  refine Wp.forIn_list (fun (sks : List (String × Ty)) s1 =>
    s1.WF ∧ s.sz ≤ s1.sz ∧ ∀ q ∈ sks, ∀ sz g, Ty.WF sz g q.2) vs []
    ⟨hs, Nat.le_refl _, by simp⟩ ?_ ?_
  · intro a ha sks s1 ⟨h1, g1, hp⟩
    refine Wp.bind (freshSkolem_wp h1 ?_)
    intro d s2 hd g2 h2
    refine Wp.pure' ⟨h2, by omega, ?_⟩
    intro q hq
    rcases List.mem_append.mp hq with hq | hq
    · exact hp q hq
    · have := List.mem_singleton.mp hq
      subst this
      exact fun sz g => hd sz g
  · intro sks s1 ⟨h1, g1, hp⟩
    exact h sks s1 g1 h1 hp

macro_rules
  | `(tactic| wp_step) => `(tactic| leaf freshSkolem_pairs_wp)

theorem lookupD_wf_sk {sz g : Nat} {sks : List (String × Ty)}
    (h : ∀ q ∈ sks, ∀ sz g, Ty.WF sz g q.2) (x : String) :
    Ty.WF sz g ((sks.lookup x).getD .unit) :=
  lookupD_wf (sz0 := sz) (fun q hq => h q hq sz g) (Nat.le_refl _) x

macro_rules
  | `(tactic| wf_close) => `(tactic| exact lookupD_wf_sk ‹_›)

theorem zip_snd_wf {sz sz0 : Nat} {ps : List Ty} {names : List String}
    (h : ∀ x ∈ ps, Ty.WF sz0 0 x) (hs : sz0 ≤ sz) :
    ∀ p ∈ names.zip ps, Ty.WF sz 0 p.2 := by
  intro p hp
  obtain ⟨a, b⟩ := p
  exact (h b (List.of_mem_zip hp).2).mono hs (Nat.le_refl _)

macro_rules
  | `(tactic| wf_close) => `(tactic| (refine zip_snd_wf ‹_› ?_; omega))
macro_rules
  | `(tactic| wf_close) => `(tactic| (apply Env.WF.append <;> wf_close))

/-- What `inferFnGroup` keeps of a member while it types the group. -/
abbrev BuiltT := FnInfo × List Ty × Ty × Eff × Ty × Option (Nat × Ty)

@[reducible] def BuiltT.OK (sz : Nat) (b : BuiltT) : Prop :=
  (∀ p ∈ b.2.1, Ty.WF sz 0 p) ∧ Ty.WF sz 0 b.2.2.1 ∧ Ty.WF sz 0 b.2.2.2.2.1 ∧
    (∀ n sc, b.2.2.2.2.2 = some (n, sc) → Ty.WF sz n sc)

theorem BuiltT.OK.mono {sz sz' : Nat} {b : BuiltT} (h : b.OK sz) (hs : sz ≤ sz') : b.OK sz' :=
  ⟨fun p hp => (h.1 p hp).mono hs (Nat.le_refl _), h.2.1.mono hs (Nat.le_refl _),
    h.2.2.1.mono hs (Nat.le_refl _), fun n sc e => (h.2.2.2 n sc e).mono hs (Nat.le_refl _)⟩

theorem inferFnGroup_wp {Q : Env → CState → Prop} {s : CState} {rules : Rules} {base : Env}
    {fns : List FnInfo} (hs : s.WF) (hbase : Env.WF s.sz base) (hfns : ∀ f ∈ fns, f.Src)
    (h : ∀ env' s', s.sz ≤ s'.sz → s'.WF → Env.WF s'.sz env' → Q env' s') :
    Wp (inferFnGroup rules base fns) Q s := by
  unfold inferFnGroup
  dsimp only
  refine Wp.bind (Wp.forIn_list
    (fun (st : Env × List BuiltT) s1 => s1.WF ∧ s.sz ≤ s1.sz ∧ Env.WF s1.sz st.1 ∧
      ∀ b ∈ st.2, b.1 ∈ fns ∧ b.OK s1.sz) fns ([], [])
    ⟨hs, Nat.le_refl _, by simp [Env.WF], by simp⟩
    ?_ ?_)
  · intro f hf st s1 ⟨h1, g1, hE, hB⟩
    have hfs := hfns f hf
    repeat' wp_step
    all_goals (
      (try dsimp only)
      refine ⟨by wf_close, by wf_close, ?_, ?_⟩
      · apply Env.WF.cons <;> wf_close
      · intro b hb
        rcases List.mem_append.mp hb with hb | hb
        · exact ⟨(hB b hb).1, (hB b hb).2.mono (by omega)⟩
        · have hb' := List.mem_singleton.mp hb
          subst hb'
          refine ⟨hf, ?_, ?_, ?_, ?_⟩
          · wf_close
          · wf_close
          · wf_close
          · intro n sc e
            first | (cases e; wf_close) | cases e)
  · intro st s1 ⟨h1, g1, hE, hB⟩
    refine Wp.bind (Wp.forIn_unit h1 ?_ ?_)
    · intro b hb s2 h2 g2
      obtain ⟨hfn, hok⟩ := hB b hb
      have hsrc := (hfns _ hfn).2.2
      refine Wp.mono (checkBody_wp h2 ?_ ?_ ?_ hsrc (fun s' g' h' => ⟨h', g'⟩))
        (fun _ _ q => q)
      · exact (hE.mono (by omega)).append (hbase.mono (by omega))
      · exact zip_snd_wf hok.1 (by omega)
      · exact hok.2.1.mono (by omega) (Nat.le_refl _)
    · intro s3 g3 h3
      refine Wp.bind (Wp.forIn_list
        (fun (out : Env) s4 => s4.WF ∧ s3.sz ≤ s4.sz ∧ Env.WF s4.sz out) st.2 []
        ⟨h3, Nat.le_refl _, by simp [Env.WF]⟩ ?_ ?_)
      · intro b hb out s4 ⟨h4, g4, hO⟩
        obtain ⟨hfn, hok⟩ := hB b hb
        have hok' := hok.mono (show s1.sz ≤ s4.sz by omega)
        obtain ⟨hps, hr, ht, hsc⟩ := hok'
        rcases hsch : b.2.2.2.2.2 with _ | ⟨n, sc⟩
        · dsimp only
          repeat' wp_step
          all_goals ((try dsimp only); refine ⟨by wf_close, by omega, ?_⟩)
          apply Env.WF.cons
          · wf_close
          · simp only [Bind.WF]; wf_close
        · dsimp only
          refine Wp.pure' ⟨h4, g4, Env.WF.cons hO ?_⟩
          simp only [Bind.WF]
          exact hsc n sc hsch
      · intro out s4 ⟨h4, g4, hO⟩
        refine Wp.pure' (h _ _ (by omega) h4 ?_)
        exact hO.append (hbase.mono (by omega))

macro_rules
  | `(tactic| wp_step) => `(tactic| leaf inferFnGroup_wp)

theorem nodes_src {nodes : Array Node} (h : ∀ nd ∈ nodes.toList, Node.Src nd) (i : Nat) :
    Node.Src (nodes[i]?.getD default) := by
  cases hg : nodes[i]? with
  | none => exact Node.Src_default
  | some nd =>
    obtain ⟨hi, rfl⟩ := Array.getElem?_eq_some_iff.mp hg
    exact h _ (Array.mem_toList_iff.mpr (Array.getElem_mem hi))

theorem runPlan_wp {Q : PUnit → CState → Prop} {s : CState} {rules : Rules} {pl : Plan}
    (hs : s.WF) (hpl : pl.Src) (h : ∀ s', s.sz ≤ s'.sz → s'.WF → Q ⟨⟩ s') :
    Wp (runPlan rules pl) Q s := by
  unfold runPlan
  dsimp only
  refine Wp.bind (Wp.forIn_list
    (fun (env : Env) s1 => s1.WF ∧ s.sz ≤ s1.sz ∧ Env.WF s1.sz env) pl.order preludeEnv
    ⟨hs, Nat.le_refl _, Env.WF.prelude⟩ ?_ ?_)
  · intro g hg env s1 ⟨h1, g1, hE⟩
    have hmem : ∀ nd ∈ g.map (fun i => pl.nodes[i]?.getD default), Node.Src nd := by
      intro nd hnd
      obtain ⟨i, _, rfl⟩ := List.mem_map.mp hnd
      exact nodes_src hpl.1 i
    split
    · rename_i nm a i heq
      have hl := hmem (Node.letN nm a i) (by rw [heq]; simp)
      obtain ⟨hann, hinit⟩ := hl
      repeat' wp_step
      all_goals ((try dsimp only); refine ⟨by wf_close, by omega, by wf_close⟩)
    · have hfn : ∀ f ∈ List.filterMap (fun nd => match nd with | Node.fnN f => some f | _ => none)
          (g.map fun i => pl.nodes[i]?.getD default), f.Src := by
        intro f hf
        obtain ⟨nd, hnd, hf'⟩ := List.mem_filterMap.mp hf
        cases nd with
        | fnN f' =>
          simp only [Option.some.injEq] at hf'
          subst hf'
          exact hmem _ hnd
        | letN _ _ _ => simp at hf'
      repeat' wp_step
      all_goals ((try dsimp only); refine ⟨by wf_close, by omega, by wf_close⟩)
  · intro env s1 ⟨h1, g1, hE⟩
    refine Wp.bind (Wp.forIn_list (fun (_ : PUnit) s2 => s2.WF ∧ s1.sz ≤ s2.sz) pl.mains
      PUnit.unit ⟨h1, Nat.le_refl _⟩ ?_ ?_)
    · intro m hm u s2 ⟨h2, g2⟩
      obtain ⟨hsig, hbody⟩ := hpl.2 m hm
      obtain ⟨n, sg, body⟩ := m
      rcases hsg : sg with _ | ⟨r, e⟩
      · dsimp only
        repeat' wp_step
      · have hr := hsig r e hsg
        dsimp only
        repeat' wp_step
        all_goals ((try dsimp only); refine ⟨by wf_close, by omega⟩)
    · intro u s2 ⟨h2, g2⟩
      exact Wp.pure' (h s2 (by omega) h2)

macro_rules
  | `(tactic| wp_step) => `(tactic| leaf runPlan_wp)

/-- The whole run: from the empty state, every state the checker reaches satisfies the invariant,
whatever the budget. -/
theorem runPlan_wf (rules : Rules) (pl : Plan) (hpl : pl.Src) (n : Nat) (u : PUnit)
    (s' : CState) (h : (runPlan rules pl).go n {} = .ok (u, s')) : s'.WF :=
  runPlan_wp (Q := fun _ s' => s'.WF) CState.WF.init hpl (fun _ _ h' => h') n u s' h

/-! ### The `!` accessors of the state code

Every `!` index in `Check.lean`'s state code is in range under the invariants, so the unchecked
access is the checked one. The facts, by site:

* `bindMeta`: `s.subst.set! n _` is `set` because `n < s.subst.size`. Every caller passes the index
  of a type that is `Ty.WF s.sz 0`: `bindMeta_wp` takes `n < s.sz`, and `flowF_wp`, `joinTyF_wp`
  and `peelF_wp` discharge it from the well-formedness of a `.mv n` they matched.
* `instTy`: `ms[i]!` for `.gen i` is `ms[i]`, because a scheme `∀ n. t` is `Ty.WF sz n t` (so
  `i < n`) and `instantiate` builds exactly `n` entries (`instTy_wf`, `instantiate_wp`). The
  schemes the checker builds are `Ty.WF` at their width: `generalize_wp`, `scheme_wp`. -/

theorem set!_eq_set {α : Type} (a : Array α) (i : Nat) (v : α) (h : i < a.size) :
    a.set! i v = a.set i v h := by
  rw [Array.set!_eq_setIfInBounds]
  simp [Array.setIfInBounds, h]

theorem bindMeta_set_eq {s : CState} {n : Nat} (h : Ty.WF s.sz 0 (.mv n)) (v : Option Ty) :
    ∃ hn : n < s.subst.size, s.subst.set! n v = s.subst.set n v hn :=
  ⟨Ty.WF_mv.mp h, set!_eq_set _ _ _ _⟩

theorem instTy_idx {ms : Array Ty} {sz n i : Nat} (hm : ms.size = n) (h : Ty.WF sz n (.gen i)) :
    ∃ hi : i < ms.size, instTy ms (.gen i) = ms[i] :=
  ⟨hm ▸ Ty.WF_gen.mp h, instTy_gen_eq ms i (hm ▸ Ty.WF_gen.mp h)⟩

/-! ### The remaining accessors

`checkPlan`, `orderGroups` and `runPlan` read arrays and lists by total functions (`getD`,
`headD`, `reachAt`), not `!`: safety needs no index proof. The range invariants of the plan it
builds are `Plan.Inv`, proved by `checkPlan_inv` (PlanInv.lean). The reads that select a node or an
edge list in `checkPlan` and `orderGroups` are not silent: an index out of range there is an
`internal` rejection, because a default node would be an `fn` that passes the declaration-order
rules. What stays total is `reachAt`/`reachSet` (indices are `[0:n]` loop bounds or edge targets
already checked as nodes) and `runPlan`'s reads, which have no guard. On in-range indices these
are the `!` they replaced (`getElem!_eq_getD`, `head!_eq_headD`); the per-program verdicts of the
oracle were diffed before and after.
`set!` is `setIfInBounds`: it never panics. -/

theorem getElem!_eq_getD {α : Type} [Inhabited α] (a : Array α) (i : Nat) :
    a[i]! = a[i]?.getD default := by
  simp only [getElem!_def]
  cases a[i]? <;> rfl

theorem head!_eq_headD {α : Type} [Inhabited α] (l : List α) : l.head! = l.headD default := by
  cases l <;> rfl

/-! ### Canonical arrows

One arrow form (spec §7 rule 9, `Judgment.lean`'s `Ty.mergeable`): a pure arrow with parameters
whose result is an arrow with parameters is one longer arrow, and `mkArr` merges it; an empty
parameter list never merges. `Ty.Canon t` says no arrow inside `t` is mergeable. -/

mutual
def Ty.Canon : Ty → Prop
  | .tup ts => Ty.CanonList ts
  | .arr ps r e => Ty.CanonList ps ∧ r.Canon ∧ Ty.mergeable ps r e = false
  | .adt _ ts => Ty.CanonList ts
  | _ => True

def Ty.CanonList : List Ty → Prop
  | [] => True
  | t :: ts => t.Canon ∧ Ty.CanonList ts
end

theorem Ty.CanonList.append {ps qs : List Ty} (h1 : Ty.CanonList ps) (h2 : Ty.CanonList qs) :
    Ty.CanonList (ps ++ qs) := by
  induction ps with
  | nil => simpa [Ty.CanonList] using h2
  | cons t ps ih => exact ⟨h1.1, ih h1.2⟩

theorem Ty.CanonList.mem {ts : List Ty} (h : Ty.CanonList ts) : ∀ t ∈ ts, t.Canon := by
  induction ts with
  | nil => simp
  | cons t ts ih =>
    intro u hu
    rcases List.mem_cons.mp hu with rfl | hu
    · exact h.1
    · exact ih h.2 u hu

theorem Ty.CanonList.of_mem {ts : List Ty} (h : ∀ t ∈ ts, t.Canon) : Ty.CanonList ts := by
  induction ts with
  | nil => trivial
  | cons t ts ih =>
    exact ⟨h t (List.mem_cons_self ..), ih fun u hu => h u (List.mem_cons_of_mem _ hu)⟩

/-- `mkArr` of canonical parts is canonical. -/
theorem mkArr_canon {ps : List Ty} {r : Ty} {e : Eff} (hps : Ty.CanonList ps) (hr : r.Canon) :
    (mkArr ps r e).Canon := by
  unfold mkArr
  split
  · rename_i ps' r' e'
    obtain ⟨hps', hr', hm'⟩ := hr
    split
    · rename_i hemp
      refine ⟨hps, ⟨hps', hr', hm'⟩, ?_⟩
      rcases ps with _ | ⟨p, ps⟩
      · rfl
      · rcases ps' with _ | ⟨q, qs⟩
        · simp [Ty.mergeable]
        · simp at hemp
    · rename_i hne
      have hps'ne : ps'.isEmpty = false := by
        rcases ps' with _ | ⟨q, qs⟩
        · simp at hne
        · rfl
      refine ⟨hps.append hps', hr', ?_⟩
      have hne' : (ps ++ ps').isEmpty = false := by
        rcases ps with _ | ⟨p, ps⟩
        · simp at hne
        · rfl
      simpa [Ty.mergeable, hne', hps'ne] using hm'
  · rename_i hn
    refine ⟨hps, hr, ?_⟩
    cases r with
    | arr ps' r' e' =>
      cases e with
      | pure => exact (hn ps' r' e' rfl rfl).elim
      | io => unfold Ty.mergeable; rfl
    | _ => simp [Ty.mergeable]

theorem shallowF_eq :
    ∀ (k : Nat) (t : Ty) (s : CState), Wp (shallowF k t) (fun _ s' => s' = s) s := by
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
      · exact ih _ s
      · exact Wp.pure' rfl
    | _ => simp only [shallowF]; exact Wp.pure' rfl

/-- `zonk`'s result is canonical, whatever the state: every arrow it builds goes through `mkArr`,
and the other types it returns are atoms. -/
theorem zonkF_canon : ∀ (k : Nat) (t : Ty) (s : CState),
    Wp (zonkF k t) (fun r s' => r.Canon ∧ s' = s) s := by
  intro k
  induction k with
  | zero => intro t s; exact Wp.fuel'
  | succ k ih =>
    intro t s
    simp only [zonkF]
    refine Wp.bind (Wp.mono (shallowF_eq k t s) ?_)
    intro t' s1 e1
    subst e1
    have hlist : ∀ ts : List Ty, Wp (ts.mapM (zonkF k))
        (fun bs s2 => s2 = s1 ∧ Ty.CanonList bs) s1 := by
      intro ts
      refine Wp.mono (Wp.mapM (f := zonkF k) (l := ts) (s := s1) (fun s2 => s2 = s1)
        (fun _ b => b.Canon) (fun _ _ _ _ hb => hb) rfl ?_)
        fun bs s2 ⟨e2, _, hb⟩ => ⟨e2, Ty.CanonList.of_mem hb⟩
      intro a ha s2 hs2 _
      subst hs2
      exact Wp.mono (ih a s2) fun r s' ⟨hr, hs'⟩ => ⟨hs', by subst hs'; exact Nat.le_refl _, hr⟩
    cases t' with
    | tup ts =>
      dsimp only
      refine Wp.bind (Wp.mono (hlist ts) ?_)
      intro bs s2 ⟨e2, hb⟩
      subst e2
      exact Wp.pure' ⟨hb, rfl⟩
    | arr ps r e =>
      dsimp only
      refine Wp.bind (Wp.mono (hlist ps) ?_)
      intro bs s2 ⟨e2, hb⟩
      subst e2
      refine Wp.bind (Wp.mono (ih r s2) ?_)
      intro r' s3 ⟨hr', e3⟩
      subst e3
      exact Wp.pure' ⟨mkArr_canon hb hr', rfl⟩
    | adt n ts =>
      dsimp only
      refine Wp.bind (Wp.mono (hlist ts) ?_)
      intro bs s2 ⟨e2, hb⟩
      subst e2
      exact Wp.pure' ⟨hb, rfl⟩
    | _ => dsimp only; exact Wp.pure' ⟨trivial, rfl⟩

theorem zonk_canon (t : Ty) (s : CState) : Wp (zonk t) (fun r s' => r.Canon ∧ s' = s) s := by
  unfold zonk
  exact Wp.bind (Wp.read' fun n => zonkF_canon n t s)

end SproutCore
