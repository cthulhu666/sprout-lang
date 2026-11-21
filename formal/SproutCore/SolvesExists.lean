import SproutCore.Subst

/-!
# A well-formed, named-free state is solved by some grounding (docs/lean-model-v0.md §9, 1b step 3a)

`infer_sound` quantifies over every `θ` with `Solves θ s'`. This file shows the quantifier is not
empty: a state with `CState.WF` and `CState.NF` has a solving grounding. It is built by well-founded recursion
along `CState.Dep` (the acyclicity half of `CState.WF`): an unbound variable goes to `.unit`, a
bound one to the grounding of its type. That type must be named-free (`CState.NF`): `Ty.app` keeps
a `.named`, which has no solved grounding. Without this, the soundness theorem could hold by there
being no `θ`.

`exists_solves_with` also chooses the grounding of the unbound variables, any family of solved
types `d n`. Step 3b picks `d n = .skolem (B + n)`: `generalize` turns each unbound variable into a
`.gen`, and `Generalises` wants it back as a distinct skolem (`Ground.lean`).
-/

namespace SproutCore

mutual
theorem Ty.app_congr {θ1 θ2 : Nat → Ty} :
    ∀ t : Ty, (∀ m ∈ metasOf t, θ1 m = θ2 m) → Ty.app θ1 t = Ty.app θ2 t
  | .mv n, h => by simpa [Ty.app, metasOf] using h n (by simp [metasOf])
  | .named _, _ => rfl
  | .skolem _, _ => rfl
  | .gen _, _ => rfl
  | .int, _ => rfl
  | .bool, _ => rfl
  | .str, _ => rfl
  | .unit, _ => rfl
  | .tup ts, h => by
    simp only [Ty.app, metasOf] at *
    rw [Ty.appList_congr ts h]
  | .arr ps r e, h => by
    simp only [Ty.app, metasOf, List.mem_append] at *
    rw [Ty.appList_congr ps (fun m hm => h m (Or.inl hm)),
      Ty.app_congr r (fun m hm => h m (Or.inr hm))]

theorem Ty.appList_congr {θ1 θ2 : Nat → Ty} :
    ∀ ts : List Ty, (∀ m ∈ metasOfList ts, θ1 m = θ2 m) → Ty.appList θ1 ts = Ty.appList θ2 ts
  | [], _ => rfl
  | t :: ts, h => by
    simp only [Ty.appList, metasOfList, List.mem_append] at *
    rw [Ty.app_congr t (fun m hm => h m (Or.inl hm)),
      Ty.appList_congr ts (fun m hm => h m (Or.inr hm))]
end

/-- One step of the recursion: an unbound variable is `d n`, a bound one is its type, grounded by
the values already chosen for the variables it mentions. -/
noncomputable def solveStep (d : Nat → Ty) (s : CState) (n : Nat) (ih : ∀ m, s.Dep m n → Ty) : Ty :=
  match hb : s.subst[n]? with
  | some (some t) => Ty.app (fun m => if hm : m ∈ metasOf t then ih m ⟨t, hb, hm⟩ else .unit) t
  | _ => d n

/-- A solving grounding that sends every unbound variable to `d n`, any family of solved types. -/
theorem exists_solves_with {s : CState} (hs : s.WF) (hn : s.NF) (d : Nat → Ty)
    (hd : ∀ n, (d n).ok 0 = true) : ∃ θ, Solves θ s ∧ ∀ n, s.Unbound n → θ n = d n := by
  have wf : WellFounded s.Dep := ⟨hs.acyc⟩
  let θ : Nat → Ty := fun n => wf.fix (solveStep d s) n
  have heq : ∀ n, θ n = solveStep d s n (fun m _ => θ m) := fun n => wf.fix_eq (solveStep d s) n
  have hsolved : ∀ n, (θ n).ok 0 = true := by
    intro n
    induction n using wf.induction with
    | _ n ih =>
      rw [heq n]
      unfold solveStep
      split
      · rename_i t hb
        refine Ty.app_solved (g := 0) (sz := s.sz) (fun m => ?_) (hs.range n t hb) (hn n t hb)
        by_cases hm : m ∈ metasOf t
        · simp only [hm, dite_true]; exact ih m ⟨t, hb, hm⟩
        · simp only [hm, dite_false]; rfl
      · exact hd n
  refine ⟨θ, ⟨?_, hsolved⟩, ?_⟩
  · intro n t hb
    rw [heq n]
    unfold solveStep
    split
    · rename_i t' hb'
      rw [hb] at hb'
      cases hb'
      refine Ty.app_congr t (fun m hm => ?_)
      simp [hm]
    · rename_i hne
      exact absurd hb (hne t)
  · intro n hu
    rw [heq n]
    unfold solveStep
    split
    · rename_i t' hb'
      exact absurd hb' (hu t')
    · rfl

theorem exists_solves {s : CState} (hs : s.WF) (hn : s.NF) : ∃ θ, Solves θ s :=
  (exists_solves_with hs hn (fun _ => .unit) (fun _ => rfl)).imp fun _ h => h.1

end SproutCore
