import SproutCore.CheckWF

/-!
# The plan's invariants (docs/lean-model-v0.md §9, 1b step 3b, part 1)

`checkPlan p = .ok pl` gives `Plan.Inv p pl`: the edge array has one entry per node, `pl.nodes`
are `p.nodes` read as declarations, `pl.mains` are the `mainFn` declarations, and `pl.order` is a
list of non-empty groups of node indices whose concatenation is a permutation of
`List.range pl.nodes.size`. The permutation needs the reachability matrix to be transitive, so the
file proves the checker's Warshall loop computes the transitive closure of the edge relation
(`warshall_k`); the groups then partition the nodes (`groups_loop`) and `orderGroups` only moves
whole groups (`orderGroups_perm`). Each loop is read through `forIn_prefix`, a loop invariant that
knows which prefix of the list was processed.
-/

namespace SproutCore

theorem forIn_prefix {α β ε : Type} (f : α → β → Except ε (ForInStep β))
    (I : List α → β → Prop) (l : List α)
    (step : ∀ pre a post b b', pre ++ a :: post = l → I pre b → f a b = .ok (.yield b') →
      I (pre ++ [a]) b')
    (nodone : ∀ pre a post b b', pre ++ a :: post = l → I pre b → f a b ≠ .ok (.done b')) :
    ∀ (rest pre : List α) (b r : β), pre ++ rest = l → I pre b → forIn rest b f = .ok r → I l r := by
  intro rest
  induction rest with
  | nil =>
    intro pre b r hl hI hr
    simp only [List.forIn_nil] at hr
    have h2 : (Except.ok b : Except ε β) = Except.ok r := hr
    cases h2
    simp only [List.append_nil] at hl
    subst hl
    exact hI
  | cons a post ih =>
    intro pre b r hl hI hr
    simp only [List.forIn_cons] at hr
    cases hfa : f a b with
    | error e => rw [hfa] at hr; cases hr
    | ok st =>
      rw [hfa] at hr
      cases st with
      | done b' => exact absurd hfa (nodone pre a post b b' hl hI)
      | yield b' =>
        exact ih (pre ++ [a]) b' r (by simpa using hl) (step pre a post b b' hl hI hfa) hr

/-- A square matrix of side `n`. -/
def Sq (n : Nat) (R : Array (Array Bool)) : Prop :=
  R.size = n ∧ ∀ (i : Nat) (row : Array Bool), R[i]? = some row → row.size = n

theorem Sq.replicate (n : Nat) : Sq n (Array.replicate n (Array.replicate n false)) := by
  refine ⟨by simp, fun i row h => ?_⟩
  rw [Array.getElem?_replicate] at h
  split at h
  · simp only [Option.some.injEq] at h
    subst h; simp
  · simp at h

theorem Sq.row {n : Nat} {R : Array (Array Bool)} (hR : Sq n R) {i : Nat} (hi : i < n) :
    ∃ row, R[i]? = some row ∧ row.size = n := by
  have hi' : i < R.size := hR.1 ▸ hi
  exact ⟨R[i], by simp [hi'], hR.2 i _ (by simp [hi'])⟩

theorem reachAt_eq {R : Array (Array Bool)} {a b : Nat} :
    reachAt R a b = true ↔ ∃ row, R[a]? = some row ∧ row[b]? = some true := by
  unfold reachAt
  cases hA : R[a]? with
  | none => simp
  | some row =>
    simp only [Option.bind_some, Option.some.injEq, exists_eq_left']
    cases hB : row[b]? with
    | none => simp
    | some v => simp

theorem reachAt_lt {n : Nat} {R : Array (Array Bool)} (hR : Sq n R) {a b : Nat}
    (h : reachAt R a b = true) : a < n ∧ b < n := by
  obtain ⟨row, h1, h2⟩ := reachAt_eq.mp h
  have h3 := (Array.getElem?_eq_some_iff.mp h1).1
  have h4 := (Array.getElem?_eq_some_iff.mp h2).1
  exact ⟨hR.1 ▸ h3, hR.2 a row h1 ▸ h4⟩

theorem reachSet_getElem? (R : Array (Array Bool)) (i j k : Nat) :
    (reachSet R i j)[k]? = if k = i then R[i]?.map (fun row => row.set! j true) else R[k]? := by
  unfold SproutCore.reachSet
  rw [Array.set!_eq_setIfInBounds, Array.getElem?_setIfInBounds]
  by_cases hki : k = i
  · subst hki
    by_cases hlt : k < R.size
    · simp [hlt]
    · have : R[k]? = none := Array.getElem?_eq_none (by omega)
      simp [hlt]
  · simp [hki, Ne.symm hki]

theorem Sq.reachSet {n : Nat} {R : Array (Array Bool)} (hR : Sq n R) (i j : Nat) :
    Sq n (reachSet R i j) := by
  refine ⟨by simpa [SproutCore.reachSet] using hR.1, fun k row h => ?_⟩
  rw [reachSet_getElem?] at h
  split at h
  · cases hr : R[i]? with
    | none => simp [hr] at h
    | some r0 =>
      simp only [hr, Option.map_some, Option.some.injEq] at h
      subst h
      simpa using hR.2 i r0 hr
  · exact hR.2 k row h

theorem reachAt_reachSet {n : Nat} {R : Array (Array Bool)} (hR : Sq n R) {i j : Nat} (hi : i < n)
    (hj : j < n) (a b : Nat) :
    reachAt (reachSet R i j) a b = true ↔ reachAt R a b = true ∨ (a = i ∧ b = j) := by
  obtain ⟨ri, hri, hrs⟩ := hR.row hi
  simp only [reachAt_eq, reachSet_getElem?]
  by_cases hai : a = i
  · subst hai
    simp only [ite_true, hri, Option.map_some, Option.some.injEq, exists_eq_left']
    simp only [Array.set!_eq_setIfInBounds, Array.getElem?_setIfInBounds]
    by_cases hbj : b = j
    · subst hbj
      simp [hrs, hj]
    · simp [hbj, Ne.symm hbj]
  · simp [hai]

theorem bind_ok_iff {ε α β : Type} {x : Except ε α} {f : α → Except ε β} {b : β} :
    (x >>= f) = .ok b ↔ ∃ a, x = .ok a ∧ f a = .ok b := by
  cases x <;> simp [bind, Except.bind]

/-! ### Graph -/

section Graph

variable (E : Nat → Nat → Prop)

inductive TC : Nat → Nat → Prop
  | base {a b : Nat} : E a b → TC a b
  | trans {a b c : Nat} : TC a b → TC b c → TC a c

inductive PVS (S : List Nat) : Nat → Nat → Prop
  | base {a b : Nat} : E a b → PVS S a b
  | step {a m b : Nat} : PVS S a m → m ∈ S → PVS S m b → PVS S a b

variable {E}

theorem PVS.tc {S : List Nat} {a b : Nat} (h : PVS E S a b) : TC E a b := by
  induction h with
  | base h => exact TC.base h
  | step _ _ _ ih1 ih2 => exact TC.trans ih1 ih2

theorem PVS.snoc {S : List Nat} {k a b : Nat} (h : PVS E (S ++ [k]) a b) :
    PVS E S a b ∨ (PVS E S a k ∧ PVS E S k b) := by
  induction h with
  | base h => exact Or.inl (PVS.base h)
  | @step a m b h1 hm h2 ih1 ih2 =>
    rcases List.mem_append.mp hm with hm | hm
    · rcases ih1 with ih1 | ⟨i1, i2⟩ <;> rcases ih2 with ih2 | ⟨j1, j2⟩
      · exact Or.inl (PVS.step ih1 hm ih2)
      · exact Or.inr ⟨PVS.step ih1 hm j1, j2⟩
      · exact Or.inr ⟨i1, PVS.step i2 hm ih2⟩
      · exact Or.inr ⟨i1, j2⟩
    · have hmk : m = k := by simpa using hm
      subst hmk
      have l1 : PVS E S a m := by
        rcases ih1 with i | ⟨i1, _⟩
        · exact i
        · exact i1
      have l2 : PVS E S m b := by
        rcases ih2 with i | ⟨_, j2⟩
        · exact i
        · exact j2
      exact Or.inr ⟨l1, l2⟩

theorem TC.lt {n : Nat} (hE : ∀ a b, E a b → a < n ∧ b < n) {a b : Nat} (h : TC E a b) :
    a < n ∧ b < n := by
  induction h with
  | base h => exact hE _ _ h
  | trans _ _ ih1 ih2 => exact ⟨ih1.1, ih2.2⟩

theorem TC.pvs {n : Nat} (hE : ∀ a b, E a b → a < n ∧ b < n) {a b : Nat} (h : TC E a b) :
    PVS E (List.range n) a b := by
  induction h with
  | base h => exact PVS.base h
  | @trans a m c h1 h2 ih1 ih2 =>
    have : m < n := (TC.lt hE h1).2
    exact PVS.step ih1 (List.mem_range.mpr this) ih2

/-- The inner loop invariant of one `k` step. -/
def Cl (n : Nat) (E : Nat → Nat → Prop) (k : Nat) (R0 : Array (Array Bool)) (pi : List Nat)
    (R : Array (Array Bool)) : Prop :=
  Sq n R ∧ (∀ a b, reachAt R0 a b = true → reachAt R a b = true) ∧
    (∀ a b, reachAt R a b = true → TC E a b) ∧
    (∀ a ∈ pi, ∀ b, reachAt R0 a k = true → reachAt R0 k b = true → reachAt R a b = true)

theorem Cl.step {n : Nat} {E : Nat → Nat → Prop} {k : Nat} {R0 : Array (Array Bool)}
    {pi : List Nat} {R : Array (Array Bool)} (h : Cl n E k R0 pi R) {i j : Nat} (hi : i < n)
    (hj : j < n) (hc : (reachAt R i k && reachAt R k j) = true) :
    Cl n E k R0 pi (reachSet R i j) := by
  obtain ⟨hs, hm, hsound, hd⟩ := h
  have hc' := Bool.and_eq_true_iff.mp hc
  refine ⟨hs.reachSet i j, fun a b hab => ?_, fun a b hab => ?_, fun a ha b h1 h2 => ?_⟩
  · exact (reachAt_reachSet hs hi hj a b).mpr (Or.inl (hm a b hab))
  · rcases (reachAt_reachSet hs hi hj a b).mp hab with h | ⟨rfl, rfl⟩
    · exact hsound a b h
    · exact TC.trans (hsound _ _ hc'.1) (hsound _ _ hc'.2)
  · exact (reachAt_reachSet hs hi hj a b).mpr (Or.inl (hd a ha b h1 h2))

theorem Cl.mono' {n : Nat} {E : Nat → Nat → Prop} {k : Nat} {R0 : Array (Array Bool)}
    {pi : List Nat} {R : Array (Array Bool)} (h : Cl n E k R0 pi R) {i j : Nat} (hi : i < n)
    (hj : j < n) {a b : Nat} (hab : reachAt R a b = true) :
    reachAt (reachSet R i j) a b = true :=
  (reachAt_reachSet h.1 hi hj a b).mpr (Or.inl hab)

/-- The `j` loop of one row. -/
theorem warshall_j {n : Nat} {E : Nat → Nat → Prop} {k i : Nat} {R0 : Array (Array Bool)}
    {pi : List Nat} (hi : i < n) {R R' : Array (Array Bool)} (hR : Cl n E k R0 pi R)
    (h : (forIn (List.range' 0 n 1) R (fun j R => if (reachAt R i k && reachAt R k j) = true then
      pure (ForInStep.yield (reachSet R i j)) else pure (ForInStep.yield R)) :
        Except CErr (Array (Array Bool))) = .ok R') :
    Cl n E k R0 (pi ++ [i]) R' := by
  let I : List Nat → Array (Array Bool) → Prop := fun pj R =>
    Cl n E k R0 pi R ∧ ∀ b ∈ pj, reachAt R0 i k = true → reachAt R0 k b = true →
      reachAt R i b = true
  have hI : I (List.range' 0 n 1) R' := by
    refine forIn_prefix _ I (List.range' 0 n 1) ?_ ?_ (List.range' 0 n 1) [] R R' (by simp)
      ⟨hR, by simp⟩ h
    · intro pre j post R1 R2 hpre ⟨hcl, hrow⟩ hfj
      have hj : j < n := by
        have : j ∈ List.range' 0 n 1 := by rw [← hpre]; simp
        simpa using this
      try dsimp only at hfj
      split at hfj
      · rename_i hc
        simp only [pure, Except.pure, Except.ok.injEq, ForInStep.yield.injEq] at hfj
        subst hfj
        refine ⟨hcl.step hi hj hc, fun b hb h1 h2 => ?_⟩
        rcases List.mem_append.mp hb with hb | hb
        · exact hcl.mono' hi hj (hrow b hb h1 h2)
        · have : b = j := by simpa using hb
          subst this
          exact (reachAt_reachSet hcl.1 hi hj i b).mpr (Or.inr ⟨rfl, rfl⟩)
      · rename_i hc
        simp only [pure, Except.pure, Except.ok.injEq, ForInStep.yield.injEq] at hfj
        subst hfj
        refine ⟨hcl, fun b hb h1 h2 => ?_⟩
        rcases List.mem_append.mp hb with hb | hb
        · exact hrow b hb h1 h2
        · have : b = j := by simpa using hb
          subst this
          exfalso
          apply hc
          simp only [Bool.and_eq_true]
          exact ⟨hcl.2.1 _ _ h1, hcl.2.1 _ _ h2⟩
    · intro pre j post R1 R2 hpre _ hfj
      try dsimp only at hfj
      split at hfj <;> simp [pure, Except.pure] at hfj
  obtain ⟨⟨hs, hm, hsound, hd⟩, hrow⟩ := hI
  refine ⟨hs, hm, hsound, fun a ha b h1 h2 => ?_⟩
  rcases List.mem_append.mp ha with ha | ha
  · exact hd a ha b h1 h2
  · have : a = i := by simpa using ha
    subst this
    apply hrow b _ h1 h2
    have := (reachAt_lt hs (hm _ _ h2)).2
    simpa using this


theorem forIn_range_eq {m : Type → Type} [Monad m] {β : Type} (n : Nat) (init : β)
    (f : Nat → β → m (ForInStep β)) : forIn [:n] init f = forIn (List.range' 0 n 1) init f := by
  rw [Std.Legacy.Range.forIn_eq_forIn_range']
  simp [Std.Legacy.Range.size]

/-- The `i` loop of one `k` step. -/
theorem warshall_i {n : Nat} {E : Nat → Nat → Prop} {k : Nat} {R0 : Array (Array Bool)}
    (h0 : Cl n E k R0 [] R0) {R' : Array (Array Bool)}
    (h : (forIn (List.range' 0 n 1) R0 (fun i R =>
      forIn [:n] R (fun j R => if (reachAt R i k && reachAt R k j) = true then
        pure (ForInStep.yield (reachSet R i j)) else pure (ForInStep.yield R)) >>= fun R =>
      pure (ForInStep.yield R)) : Except CErr (Array (Array Bool))) = .ok R') :
    Cl n E k R0 (List.range' 0 n 1) R' := by
  refine forIn_prefix _ (fun pi R => Cl n E k R0 pi R) (List.range' 0 n 1) ?_ ?_
    (List.range' 0 n 1) [] R0 R' (by simp) h0 h
  · intro pre i post R1 R2 hpre hcl hfi
    have hi : i < n := by
      have : i ∈ List.range' 0 n 1 := by rw [← hpre]; simp
      simpa using this
    try dsimp only at hfi
    rw [bind_ok_iff] at hfi
    obtain ⟨R3, hj, hy⟩ := hfi
    simp only [pure, Except.pure, Except.ok.injEq, ForInStep.yield.injEq] at hy
    subst hy
    rw [forIn_range_eq] at hj
    exact warshall_j hi hcl hj
  · intro pre i post R1 R2 hpre _ hfi
    try dsimp only at hfi
    rw [bind_ok_iff] at hfi
    obtain ⟨R3, hj, hy⟩ := hfi
    simp [pure, Except.pure] at hy

theorem warshall_k {n : Nat} {E : Nat → Nat → Prop} (hE : ∀ a b, E a b → a < n ∧ b < n)
    {R0 : Array (Array Bool)} (hs0 : Sq n R0) (hsound0 : ∀ a b, reachAt R0 a b = true → TC E a b)
    (hc0 : ∀ a b, E a b → reachAt R0 a b = true) {R : Array (Array Bool)}
    (h : (forIn [:n] R0 (fun k R =>
      forIn [:n] R (fun i R =>
        forIn [:n] R (fun j R => if (reachAt R i k && reachAt R k j) = true then
          pure (ForInStep.yield (reachSet R i j)) else pure (ForInStep.yield R)) >>= fun R =>
        pure (ForInStep.yield R)) >>= fun R =>
      pure (ForInStep.yield R)) : Except CErr (Array (Array Bool))) = .ok R) :
    Sq n R ∧ (∀ a b, reachAt R a b = true → TC E a b) ∧ (∀ a b, TC E a b → reachAt R a b = true) := by
  rw [forIn_range_eq] at h
  let I : List Nat → Array (Array Bool) → Prop := fun S R =>
    Sq n R ∧ (∀ a b, reachAt R a b = true → TC E a b) ∧
      (∀ a b, PVS E S a b → reachAt R a b = true)
  have hI : I (List.range' 0 n 1) R := by
    refine forIn_prefix _ I (List.range' 0 n 1) ?_ ?_ (List.range' 0 n 1) [] R0 R (by simp)
      ⟨hs0, hsound0, ?_⟩ h
    · intro pre k post R1 R2 hpre ⟨hs1, hsound1, hcomp1⟩ hfk
      have hk : k < n := by
        have : k ∈ List.range' 0 n 1 := by rw [← hpre]; simp
        simpa using this
      try dsimp only at hfk
      rw [bind_ok_iff] at hfk
      obtain ⟨R3, hi, hy⟩ := hfk
      simp only [pure, Except.pure, Except.ok.injEq, ForInStep.yield.injEq] at hy
      subst hy
      rw [forIn_range_eq] at hi
      have h0 : Cl n E k R1 [] R1 := ⟨hs1, fun _ _ h => h, hsound1, by simp⟩
      obtain ⟨hs3, hm3, hsound3, hd3⟩ := warshall_i h0 hi
      refine ⟨hs3, hsound3, fun a b hab => ?_⟩
      rcases hab.snoc with h1 | ⟨h1, h2⟩
      · exact hm3 a b (hcomp1 a b h1)
      · have r1 := hcomp1 a k h1
        have r2 := hcomp1 k b h2
        refine hd3 a ?_ b r1 r2
        have := (reachAt_lt hs1 r1).1
        simpa using this
    · intro pre k post R1 R2 hpre _ hfk
      try dsimp only at hfk
      rw [bind_ok_iff] at hfk
      obtain ⟨R3, hi, hy⟩ := hfk
      simp [pure, Except.pure] at hy
    · intro a b hab
      cases hab with
      | base h => exact hc0 a b h
      | step _ hm _ => simp at hm
  obtain ⟨hs, hsound, hcomp⟩ := hI
  refine ⟨hs, hsound, fun a b hab => hcomp a b ?_⟩
  have := hab.pvs hE
  simpa [List.range_eq_range'] using this

end Graph

def EdgeRel (edges : Array (List Nat)) (a b : Nat) : Prop :=
  ∃ es, edges[a]? = some es ∧ b ∈ es

theorem EdgeRel.lt {n : Nat} {edges : Array (List Nat)} (hsz : edges.size = n)
    (hb : ∀ es ∈ edges.toList, ∀ j ∈ es, j < n) {a b : Nat} (h : EdgeRel edges a b) :
    a < n ∧ b < n := by
  obtain ⟨es, h1, h2⟩ := h
  obtain ⟨hlt, rfl⟩ := Array.getElem?_eq_some_iff.mp h1
  exact ⟨hsz ▸ hlt, hb _ (Array.mem_toList_iff.mpr (Array.getElem_mem hlt)) b h2⟩

theorem reach_init {n : Nat} {edges : Array (List Nat)} (hsz : edges.size = n)
    (hb : ∀ es ∈ edges.toList, ∀ j ∈ es, j < n) {R : Array (Array Bool)}
    (h : (forIn [:n] (Array.replicate n (Array.replicate n false)) (fun i R =>
        match edges[i]? with
        | some es => do
          let R ← forIn es R fun j R => pure (ForInStep.yield (reachSet R i j))
          pure (ForInStep.yield R)
        | _ => do
          throw (CErr.reject "internal" (toString "no edges for node " ++ toString i))
          pure (ForInStep.yield R)) : Except CErr (Array (Array Bool))) = .ok R) :
    Sq n R ∧ (∀ a b, reachAt R a b = true → EdgeRel edges a b) ∧
      (∀ a b, EdgeRel edges a b → reachAt R a b = true) := by
  rw [forIn_range_eq] at h
  let I : List Nat → Array (Array Bool) → Prop := fun pi R =>
    Sq n R ∧ (∀ a b, reachAt R a b = true → EdgeRel edges a b) ∧
      (∀ a ∈ pi, ∀ b, EdgeRel edges a b → reachAt R a b = true)
  have hI : I (List.range' 0 n 1) R := by
    refine forIn_prefix _ I (List.range' 0 n 1) ?_ ?_ (List.range' 0 n 1) [] _ R (by simp)
      ⟨Sq.replicate n, ?_, by simp⟩ h
    · intro pre i post R1 R2 hpre ⟨hs1, hsound1, hdone1⟩ hfi
      have hi : i < n := by
        have : i ∈ List.range' 0 n 1 := by rw [← hpre]; simp
        simpa using this
      try dsimp only at hfi
      cases hes : edges[i]? with
      | none => simp [hes, throw, throwThe, MonadExcept.throw, bind, Except.bind] at hfi
      | some es =>
        simp only [hes] at hfi
        rw [bind_ok_iff] at hfi
        obtain ⟨R3, hj, hy⟩ := hfi
        simp only [pure, Except.pure, Except.ok.injEq, ForInStep.yield.injEq] at hy
        subst hy
        let J : List Nat → Array (Array Bool) → Prop := fun pj R =>
          Sq n R ∧ (∀ a b, reachAt R a b = true → EdgeRel edges a b) ∧
            (∀ a ∈ pre, ∀ b, EdgeRel edges a b → reachAt R a b = true) ∧
            ∀ b ∈ pj, reachAt R i b = true
        have hJ : J es R3 := by
          refine forIn_prefix _ J es ?_ ?_ es [] R1 R3 (by simp) ⟨hs1, hsound1, hdone1, by simp⟩ hj
          · intro pre' j post R4 R5 hpre' ⟨hs4, hsound4, hdone4, hrow4⟩ hfj
            have hjn : j < n := hb es (Array.mem_toList_iff.mpr (Array.mem_of_getElem? hes))
              j (by rw [← hpre']; simp)
            simp only [pure, Except.pure, Except.ok.injEq, ForInStep.yield.injEq] at hfj
            subst hfj
            refine ⟨hs4.reachSet i j, fun a b hab => ?_, fun a ha b hab => ?_, fun b hb' => ?_⟩
            · rcases (reachAt_reachSet hs4 hi hjn a b).mp hab with h | ⟨rfl, rfl⟩
              · exact hsound4 a b h
              · exact ⟨es, hes, by rw [← hpre']; simp⟩
            · exact (reachAt_reachSet hs4 hi hjn a b).mpr (Or.inl (hdone4 a ha b hab))
            · rcases List.mem_append.mp hb' with hb' | hb'
              · exact (reachAt_reachSet hs4 hi hjn i b).mpr (Or.inl (hrow4 b hb'))
              · have : b = j := by simpa using hb'
                subst this
                exact (reachAt_reachSet hs4 hi hjn i b).mpr (Or.inr ⟨rfl, rfl⟩)
          · intro pre' j post R4 R5 hpre' _ hfj
            simp [pure, Except.pure] at hfj
        obtain ⟨hs3, hsound3, hdone3, hrow3⟩ := hJ
        refine ⟨hs3, hsound3, fun a ha b hab => ?_⟩
        rcases List.mem_append.mp ha with ha | ha
        · exact hdone3 a ha b hab
        · have : a = i := by simpa using ha
          subst this
          obtain ⟨es', hes', hb'⟩ := hab
          rw [hes] at hes'
          cases hes'
          exact hrow3 b hb'
    · intro pre i post R1 R2 hpre _ hfi
      try dsimp only at hfi
      cases hes : edges[i]? with
      | none => simp [hes, throw, throwThe, MonadExcept.throw, bind, Except.bind] at hfi
      | some es =>
        simp only [hes] at hfi
        rw [bind_ok_iff] at hfi
        obtain ⟨R3, hj, hy⟩ := hfi
        simp [pure, Except.pure] at hy
    · intro a b hab
      exfalso
      obtain ⟨row, h1, h2⟩ := reachAt_eq.mp hab
      rw [Array.getElem?_replicate] at h1
      split at h1
      · simp only [Option.some.injEq] at h1
        subst h1
        rw [Array.getElem?_replicate] at h2
        split at h2 <;> simp at h2
      · simp at h1
  obtain ⟨hs, hsound, hdone⟩ := hI
  refine ⟨hs, hsound, fun a b hab => hdone a ?_ b hab⟩
  have := (EdgeRel.lt hsz hb hab).1
  simpa using this


/-! ### `orderGroups` keeps the groups' elements -/

theorem orderGroups_perm (edges : Array (List Nat)) (n : Nat) :
    ∀ (b : Nat) (pending order : List (List Nat)) (res : List (List Nat)),
      (order.flatten ++ pending.flatten).Perm (List.range n) → pending.Nodup →
      (∀ g ∈ pending, g ≠ []) → (∀ g ∈ order, g ≠ []) →
      orderGroups edges pending order b = .ok res →
      res.flatten.Perm (List.range n) ∧ ∀ g ∈ res, g ≠ []
  | b, pending, order, res, hp, hn, hne, hne', h => by
    have done : pending.isEmpty = true → res = order → res.flatten.Perm (List.range n) ∧
        ∀ g ∈ res, g ≠ [] := by
      intro hemp hr
      subst hr
      have : pending = [] := List.isEmpty_iff.mp hemp
      subst this
      exact ⟨by simpa using hp, hne'⟩
    cases b with
    | zero =>
      unfold orderGroups at h
      split at h
      · rename_i hemp
        simp only [pure, Except.pure, Except.ok.injEq] at h
        exact done hemp h.symm
      · simp [throw, throwThe, MonadExcept.throw] at h
    | succ b =>
      unfold orderGroups at h
      split at h
      · rename_i hemp
        simp only [pure, Except.pure, Except.ok.injEq] at h
        exact done hemp h.symm
      · split at h
        · simp [throw, throwThe, MonadExcept.throw] at h
        · dsimp only at h
          split at h
          · simp [throw, throwThe, MonadExcept.throw] at h
          · rename_i g rest hready
            have hgmem : g ∈ pending := by
              have hm : g ∈ g :: rest := List.mem_cons_self ..
              rw [← hready] at hm
              exact (List.mem_filter.mp hm).1
            obtain ⟨P1, P2, hsplit⟩ := List.append_of_mem hgmem
            have hnd := hn
            rw [hsplit] at hnd
            have hg1 : g ∉ P1 := by
              intro hg
              have := (List.nodup_append.mp hnd).2.2 g hg g (by simp)
              exact this rfl
            have hg2 : g ∉ P2 := by
              have := (List.nodup_append.mp hnd).1
              exact fun hg => (List.nodup_cons.mp (List.nodup_append.mp hnd).2.1).1 hg
            have hfilt : pending.filter (· != g) = P1 ++ P2 := by
              rw [hsplit, List.filter_append, List.filter_cons]
              have e1 : P1.filter (· != g) = P1 := by
                apply List.filter_eq_self.mpr
                intro a ha
                simp only [bne_iff_ne, ne_eq]
                exact fun e => hg1 (e ▸ ha)
              have e2 : P2.filter (· != g) = P2 := by
                apply List.filter_eq_self.mpr
                intro a ha
                simp only [bne_iff_ne, ne_eq]
                exact fun e => hg2 (e ▸ ha)
              simp [e1, e2]
            rw [hfilt] at h
            refine orderGroups_perm edges n b (P1 ++ P2) (order ++ [g]) res ?_ ?_ ?_ ?_ h
            · rw [hsplit] at hp
              refine List.Perm.trans ?_ hp
              rw [List.perm_iff_count]
              intro a
              simp [List.count_append]
              omega
            · rw [List.nodup_append] at hnd ⊢
              refine ⟨hnd.1, (List.nodup_cons.mp hnd.2.1).2, ?_⟩
              intro a ha b hb
              exact hnd.2.2 a ha b (List.mem_cons_of_mem _ hb)
            · intro g' hg'
              apply hne
              rw [hsplit]
              rcases List.mem_append.mp hg' with h | h
              · exact List.mem_append_left _ h
              · exact List.mem_append_right _ (List.mem_cons_of_mem _ h)
            · intro g' hg'
              rcases List.mem_append.mp hg' with h | h
              · exact hne' g' h
              · have : g' = g := by simpa using h
                rw [this]
                exact hne g hgmem


/-! ### The groups partition the nodes -/

theorem groups_loop {n : Nat} {R : Array (Array Bool)}
    (htrans : ∀ a b c, reachAt R a b = true → reachAt R b c = true → reachAt R a c = true)
    {res : List (List Nat) × List Nat}
    (h : (forIn [:n] (([] : List (List Nat)), ([] : List Nat)) (fun i s =>
      if s.snd.contains i = true then pure (ForInStep.yield (s.fst, s.snd))
      else
        pure (ForInStep.yield (s.fst ++ [List.filter (fun j => j == i ||
              reachAt R i j && reachAt R j i) (List.range n)],
          s.snd ++ List.filter (fun j => j == i || reachAt R i j && reachAt R j i)
            (List.range n)))) : Except CErr (List (List Nat) × List Nat)) = .ok res) :
    res.2 = res.1.flatten ∧ res.1.flatten.Nodup ∧ res.1.Nodup ∧ (∀ x ∈ res.1.flatten, x < n) ∧
      (∀ g ∈ res.1, g ≠ []) ∧ ∀ i, i < n → i ∈ res.1.flatten := by
  rw [forIn_range_eq] at h
  let I : List Nat → List (List Nat) × List Nat → Prop := fun pi s =>
    s.2 = s.1.flatten ∧ s.2.Nodup ∧ s.1.Nodup ∧ (∀ x ∈ s.2, x < n) ∧ (∀ g ∈ s.1, g ≠ []) ∧
      (∀ i ∈ pi, i ∈ s.2) ∧
      ∀ j ∈ s.2, ∀ x, x < n → reachAt R j x = true → reachAt R x j = true → x ∈ s.2
  have hI : I (List.range' 0 n 1) res := by
    refine forIn_prefix _ I (List.range' 0 n 1) ?_ ?_ (List.range' 0 n 1) [] _ res (by simp)
      ⟨rfl, List.nodup_nil, List.nodup_nil, by simp, by simp, by simp, by simp⟩ h
    · intro pre i post s1 s2 hpre ⟨hflat, hnd, hgnd, hlt, hne, hcov, hcl⟩ hfi
      have hi : i < n := by
        have : i ∈ List.range' 0 n 1 := by rw [← hpre]; simp
        simpa using this
      try dsimp only at hfi
      split at hfi
      · rename_i hc
        simp only [pure, Except.pure, Except.ok.injEq, ForInStep.yield.injEq] at hfi
        subst hfi
        have hcm : i ∈ s1.2 := List.contains_iff_mem.mp hc
        refine ⟨hflat, hnd, hgnd, hlt, hne, fun i' hi' => ?_, hcl⟩
        rcases List.mem_append.mp hi' with hi' | hi'
        · exact hcov i' hi'
        · have : i' = i := by simpa using hi'
          rw [this]; exact hcm
      · rename_i hc
        simp only [pure, Except.pure, Except.ok.injEq, ForInStep.yield.injEq] at hfi
        subst hfi
        have hcm : i ∉ s1.2 := fun hm => hc (List.contains_iff_mem.mpr hm)
        have hmemg : ∀ x, x ∈ List.filter (fun j => j == i || reachAt R i j && reachAt R j i)
            (List.range n) ↔ x < n ∧ (x = i ∨ (reachAt R i x = true ∧ reachAt R x i = true)) := by
          intro x
          simp [List.mem_filter, List.mem_range]
        have hgi : i ∈ List.filter (fun j => j == i || reachAt R i j && reachAt R j i)
            (List.range n) := (hmemg i).mpr ⟨hi, Or.inl rfl⟩
        refine ⟨?_, ?_, ?_, ?_, ?_, ?_, ?_⟩
        · simp [hflat]
        · rw [List.nodup_append]
          refine ⟨hnd, List.Pairwise.sublist (List.filter_sublist) List.nodup_range, ?_⟩
          intro x hx y hy hxy
          subst hxy
          obtain ⟨hxn, hx'⟩ := (hmemg x).mp hy
          rcases hx' with rfl | ⟨h1, h2⟩
          · exact hcm hx
          · exact hcm (hcl x hx i hi h2 h1)
        · refine List.nodup_append.mpr ⟨hgnd, List.nodup_cons.mpr ⟨by simp, List.nodup_nil⟩, ?_⟩
          intro x hx y hy
          have : y = List.filter (fun j => j == i || reachAt R i j && reachAt R j i) (List.range n) := by
            simpa using hy
          subst this
          intro hxy
          rw [hxy] at hx
          apply hcm
          rw [hflat]
          exact List.mem_flatten.mpr ⟨_, hx, hgi⟩
        · intro x hx
          rcases List.mem_append.mp hx with hx | hx
          · exact hlt x hx
          · exact ((hmemg x).mp hx).1
        · intro g hg
          rcases List.mem_append.mp hg with hg | hg
          · exact hne g hg
          · have : g = List.filter (fun j => j == i || reachAt R i j && reachAt R j i)
              (List.range n) := by simpa using hg
            subst this
            exact List.ne_nil_of_mem hgi
        · intro i' hi'
          rcases List.mem_append.mp hi' with hi' | hi'
          · exact List.mem_append_left _ (hcov i' hi')
          · have : i' = i := by simpa using hi'
            rw [this]
            exact List.mem_append_right _ hgi
        · intro j hj x hxn h1 h2
          rcases List.mem_append.mp hj with hj | hj
          · exact List.mem_append_left _ (hcl j hj x hxn h1 h2)
          · apply List.mem_append_right
            obtain ⟨hjn, hj'⟩ := (hmemg j).mp hj
            refine (hmemg x).mpr ⟨hxn, ?_⟩
            rcases hj' with rfl | ⟨a1, a2⟩
            · exact Or.inr ⟨h1, h2⟩
            · exact Or.inr ⟨htrans _ _ _ a1 h1, htrans _ _ _ h2 a2⟩
    · intro pre i post s1 s2 hpre _ hfi
      try dsimp only at hfi
      split at hfi <;> simp [pure, Except.pure] at hfi
  obtain ⟨h1, h2, h3, h4, h5, h6, _⟩ := hI
  refine ⟨h1, h1 ▸ h2, h3, h1 ▸ h4, h5, fun i hi => ?_⟩
  have := h6 i (by simpa using hi)
  rwa [h1] at this


/-! ### The plan -/

def Node.toDecl : Node → Decl
  | .fnN f => .fn f.name f.params f.sig f.body
  | .letN n a i => .letD n a i

/-- What `checkPlan` guarantees of the plan it returns. -/
structure Plan.Inv (p : Program) (pl : Plan) : Prop where
  edges_size : pl.edges.size = pl.nodes.size
  nodes_eq : pl.nodes.toList.map Node.toDecl = p.nodes
  mains_eq : ∀ sg body, Decl.mainFn sg body ∈ p.decls ↔ ∃ pos, (pos, sg, body) ∈ pl.mains
  order_lt : ∀ g ∈ pl.order, ∀ i ∈ g, i < pl.nodes.size
  order_ne : ∀ g ∈ pl.order, g ≠ []
  order_perm : pl.order.flatten.Perm (List.range pl.nodes.size)

/-- `runPlan`'s index guard holds for every group of an invariant plan. -/
theorem Plan.Inv.guard_true {p : Program} {pl : Plan} (h : Plan.Inv p pl) :
    ∀ g ∈ pl.order, g.all (fun i => i < pl.nodes.size && i < pl.edges.size) = true := by
  intro g hg
  rw [List.all_eq_true]
  intro i hi
  have := h.order_lt g hg i hi
  simp [this, h.edges_size]

theorem checkPlan_inv {p : Program} {pl : Plan} (h : checkPlan p = .ok pl) : Plan.Inv p pl := by
  unfold checkPlan at h
  rw [bind_ok_iff] at h
  obtain ⟨st, h1, h⟩ := h
  rw [bind_ok_iff] at h
  obtain ⟨u, h2, h⟩ := h
  split at h
  · simp [throw, throwThe, MonadExcept.throw, bind, Except.bind] at h
  rename_i hmains
  -- the nodes
  have hnodes : st.1.toList.map Node.toDecl = p.nodes ∧
      ∀ sg body, Decl.mainFn sg body ∈ p.decls ↔ ∃ pos, (pos, sg, body) ∈ st.2 := by
    let I : List Decl → Array Node × List (Nat × Option (Ty × Eff) × Expr) → Prop := fun pre s =>
      s.1.toList.map Node.toDecl = pre.filter (fun d => match d with | .mainFn .. => false | _ => true) ∧
        ∀ sg body, Decl.mainFn sg body ∈ pre ↔ ∃ pos, (pos, sg, body) ∈ s.2
    have := forIn_prefix _ I p.decls ?_ ?_ p.decls [] _ st (by simp) ⟨by simp, by simp⟩ h1
    · exact this
    · intro pre d post s1 s2 hpre ⟨hn, hm⟩ hf
      cases d with
      | fn n ps sg b =>
        simp only [pure, Except.pure, Except.ok.injEq, ForInStep.yield.injEq] at hf
        subst hf
        refine ⟨by simp [hn, Node.toDecl], fun sg' body' => ?_⟩
        simp [hm]
      | letD n a i =>
        simp only [pure, Except.pure, Except.ok.injEq, ForInStep.yield.injEq] at hf
        subst hf
        refine ⟨by simp [hn, Node.toDecl], fun sg' body' => ?_⟩
        simp [hm]
      | mainFn sg b =>
        simp only [pure, Except.pure, Except.ok.injEq, ForInStep.yield.injEq] at hf
        subst hf
        refine ⟨by simpa using hn, fun sg' body' => ?_⟩
        simp only [List.mem_append, List.mem_singleton, Decl.mainFn.injEq, hm]
        constructor
        · rintro (⟨pos, hp⟩ | ⟨rfl, rfl⟩)
          · exact ⟨pos, Or.inl hp⟩
          · exact ⟨s1.1.size, Or.inr rfl⟩
        · rintro ⟨pos, hp | hp⟩
          · exact Or.inl ⟨pos, hp⟩
          · simp only [Prod.mk.injEq] at hp
            exact Or.inr ⟨hp.2.1, hp.2.2⟩
    · intro pre d post s1 s2 hpre _ hf
      cases d <;> simp [pure, Except.pure] at hf
  dsimp only at h
  rw [bind_ok_iff] at h
  obtain ⟨edges, h3, h⟩ := h
  -- the edges
  have hedges : edges.size = st.1.size ∧ ∀ es ∈ edges.toList, ∀ j ∈ es, j < st.1.size := by
    let I : List (Node × Nat) → Array (List Nat) → Prop := fun pre s =>
      s.size = pre.length ∧ ∀ es ∈ s.toList, ∀ j ∈ es, j < st.1.size
    have := forIn_prefix _ I st.1.toList.zipIdx ?_ ?_ st.1.toList.zipIdx [] _ edges (by simp)
      ⟨by simp, by simp⟩ h3
    · simpa [I] using this
    · intro pre x post s1 s2 hpre ⟨hsz, hb⟩ hf
      obtain ⟨nd, i⟩ := x
      try dsimp only at hf
      rw [bind_ok_iff] at hf
      obtain ⟨es, hes, hy⟩ := hf
      simp only [pure, Except.pure, Except.ok.injEq, ForInStep.yield.injEq] at hy
      subst hy
      have hesb : ∀ j ∈ es, j < st.1.size := by
        refine forIn_prefix _ (fun _ es => ∀ j ∈ es, j < st.1.size)
          (Node.refs (List.map Node.name st.1.toList) nd) ?_ ?_
          (Node.refs (List.map Node.name st.1.toList) nd) [] [] es (by simp) (by simp) hes
        · intro pre' r post' es1 es2 hpre' hes1 hfr
          try dsimp only at hfr
          split at hfr
          · rename_i j hidx
            split at hfr
            · rename_i tgt htgt
              have hj : j < st.1.size := (Array.getElem?_eq_some_iff.mp htgt).1
              have hy : es2 = es1 ++ [j] := by
                split at hfr <;> (try split at hfr) <;>
                  first
                  | (simp [pure, Except.pure, throw, throwThe, MonadExcept.throw, bind,
                      Except.bind] at hfr; exact hfr.symm)
                  | (simp [pure, Except.pure, throw, throwThe, MonadExcept.throw, bind,
                      Except.bind] at hfr)
              subst hy
              intro j' hj'
              rcases List.mem_append.mp hj' with hj' | hj'
              · exact hes1 j' hj'
              · have : j' = j := by simpa using hj'
                rw [this]; exact hj
            · simp [pure, Except.pure, throw, throwThe, MonadExcept.throw, bind, Except.bind] at hfr
          · simp [pure, Except.pure, throw, throwThe, MonadExcept.throw, bind, Except.bind] at hfr
        · intro pre' r post' es1 es2 hpre' _ hfr
          try dsimp only at hfr
          split at hfr
          · split at hfr
            · split at hfr <;> (try split at hfr) <;>
                simp [pure, Except.pure, throw, throwThe, MonadExcept.throw, bind, Except.bind] at hfr
            · simp [pure, Except.pure, throw, throwThe, MonadExcept.throw, bind, Except.bind] at hfr
          · simp [pure, Except.pure, throw, throwThe, MonadExcept.throw, bind, Except.bind] at hfr
      refine ⟨by simp [hsz], ?_⟩
      intro es' hes' j hj
      rw [Array.toList_push] at hes'
      rcases List.mem_append.mp hes' with h' | h'
      · exact hb es' h' j hj
      · have : es' = es := by simpa using h'
        rw [this] at hj
        exact hesb j hj
    · intro pre x post s1 s2 hpre _ hf
      obtain ⟨nd, i⟩ := x
      try dsimp only at hf
      rw [bind_ok_iff] at hf
      obtain ⟨es, hes, hy⟩ := hf
      simp [pure, Except.pure] at hy
  rw [bind_ok_iff] at h
  obtain ⟨u2, h4, h⟩ := h
  rw [bind_ok_iff] at h
  obtain ⟨R0, h5, h⟩ := h
  rw [bind_ok_iff] at h
  obtain ⟨R1, h6, h⟩ := h
  rw [bind_ok_iff] at h
  obtain ⟨gs, h7, h⟩ := h
  rw [bind_ok_iff] at h
  obtain ⟨order, h8, h⟩ := h
  simp only [pure, Except.pure, Except.ok.injEq] at h
  subst h
  have hE : ∀ a b, EdgeRel edges a b → a < st.1.size ∧ b < st.1.size :=
    fun a b hab => EdgeRel.lt hedges.1 hedges.2 hab
  obtain ⟨hs0, hsound0, hcomp0⟩ := reach_init hedges.1 hedges.2 h5
  obtain ⟨hs1, hsound1, hcomp1⟩ := warshall_k hE hs0 (fun a b h => TC.base (hsound0 a b h)) hcomp0 h6
  have htrans : ∀ a b c, reachAt R1 a b = true → reachAt R1 b c = true → reachAt R1 a c = true :=
    fun a b c h1 h2 => hcomp1 a c (TC.trans (hsound1 a b h1) (hsound1 b c h2))
  obtain ⟨g1, g2, g3, g4, g5, g6⟩ := groups_loop htrans h7
  have hperm0 : (([] : List (List Nat)).flatten ++ gs.1.flatten).Perm (List.range st.1.size) := by
    rw [List.perm_ext_iff_of_nodup (by simpa using g2) List.nodup_range]
    intro x
    simp only [List.flatten_nil, List.nil_append, List.mem_range]
    exact ⟨g4 x, g6 x⟩
  obtain ⟨hperm, hne⟩ := orderGroups_perm edges st.1.size (st.1.size + 1) gs.1 [] order hperm0 g3 g5
    (by simp) h8
  refine ⟨hedges.1, hnodes.1, hnodes.2, ?_, hne, hperm⟩
  intro g hg i hi
  have : i ∈ order.flatten := List.mem_flatten.mpr ⟨g, hg, hi⟩
  simpa using hperm.mem_iff.mp this

end SproutCore
