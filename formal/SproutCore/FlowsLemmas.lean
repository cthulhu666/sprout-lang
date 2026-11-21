import SproutCore.Subst

/-!
# Lemmas about `Flows` and `Peel` (docs/lean-model-v0.md §9, 1b step 3a, part 2)

`Flows` reads an arrow as curried (`Ty.uncons`), whatever its stored parameter count, and the
checker's `flowF` compares stored arrows in three arity cases. This file is the bridge:

* `flows_mkArr_left`, `flows_mkArr_right`: `Flows` does not see a merge. `mkArr ps r e` and
  `.arr ps r e` have the same curried levels, so they flow the same way.
* `flows_arr_arr`, `flows_arr_longer`, `flows_arr_shorter`: the three arity cases of `flowF`, each
  as a `Flows` between two stored arrows.
* `Flows.refl_of_ok`: a solved type flows into itself.
* `peel_mkArr_exact`, `peel_mkArr_rest`, `peel_over_mkArr`: `Peel` on an arrow `mkArr` merged.
-/

namespace SproutCore

open Judgment

theorem Eff.le_refl (e : Eff) : e.le e = true := by cases e <;> rfl
theorem Eff.pure_le (e : Eff) : Eff.pure.le e = true := by cases e <;> rfl
theorem Eff.le_pure {e : Eff} (h : e.le .pure = true) : e = .pure := by
  cases e <;> simp_all [Eff.le]
theorem Eff.le_join_left (a b : Eff) : a.le (a.join b) = true := by cases a <;> cases b <;> rfl
theorem Eff.le_join_right (a b : Eff) : b.le (a.join b) = true := by cases a <;> cases b <;> rfl
theorem Eff.meet_le_left (a b : Eff) : (a.meet b).le a = true := by cases a <;> cases b <;> rfl
theorem Eff.meet_le_right (a b : Eff) : (a.meet b).le b = true := by cases a <;> cases b <;> rfl
theorem Eff.join_pure_left (e : Eff) : Eff.pure.join e = e := by cases e <;> rfl
theorem Eff.join_pure_right (e : Eff) : e.join .pure = e := by cases e <;> rfl
theorem Eff.join_comm (a b : Eff) : a.join b = b.join a := by cases a <;> cases b <;> rfl
theorem Eff.join_assoc (a b c : Eff) : (a.join b).join c = a.join (b.join c) := by
  cases a <;> cases b <;> cases c <;> rfl
theorem Eff.le_pure_iff {e : Eff} : e.le .pure = true ↔ e = .pure :=
  ⟨Eff.le_pure, fun h => h ▸ Eff.le_refl _⟩
theorem Eff.beq_pure {e : Eff} : (e == .pure) = true ↔ e = .pure := by cases e <;> simp

/-! ### Reflexivity -/

theorem flows_arr_refl (r : Ty) (e : Eff) (hr : Flows r r) :
    ∀ ps : List Ty, FlowsAll ps ps → Flows (.arr ps r e) (.arr ps r e)
  | [], _ => Flows.nullary r r e e hr (Eff.le_refl e)
  | [p], h => by
    cases h with
    | cons _ _ _ _ hp _ =>
      exact Flows.arr _ _ p p r r e e rfl rfl hp (Eff.le_refl e) hr
  | p :: q :: qs, h => by
    cases h with
    | cons _ _ _ _ hp hrest =>
      exact Flows.arr _ _ p p (.arr (q :: qs) r e) (.arr (q :: qs) r e) .pure .pure rfl rfl hp
        (Eff.le_refl _) (flows_arr_refl r e hr (q :: qs) hrest)

mutual
theorem Flows.refl_of_ok : ∀ t : Ty, t.ok 0 = true → Flows t t
  | .named _, h => by simp [Ty.ok] at h
  | .mv _, h => by simp [Ty.ok] at h
  | .gen _, h => by simp [Ty.ok] at h
  | .skolem n, _ => Flows.skolem n
  | .int, _ => Flows.int
  | .bool, _ => Flows.bool
  | .str, _ => Flows.str
  | .unit, _ => Flows.unit
  | .tup ts, h => Flows.tup _ _ (FlowsAll.refl_of_ok ts (by simpa [Ty.ok] using h))
  | .arr ps r e, h => by
    simp only [Ty.ok, Bool.and_eq_true] at h
    exact flows_arr_refl r e (Flows.refl_of_ok r h.1.2) ps (FlowsAll.refl_of_ok ps h.1.1)

theorem FlowsAll.refl_of_ok : ∀ ts : List Ty, Ty.okList 0 ts = true → FlowsAll ts ts
  | [], _ => FlowsAll.nil
  | t :: ts, h => by
    simp only [Ty.okList, Bool.and_eq_true] at h
    exact FlowsAll.cons _ _ _ _ (Flows.refl_of_ok t h.1) (FlowsAll.refl_of_ok ts h.2)
end

/-! ### `Flows` does not see a merge -/

theorem flows_left_step {a a' : Ty} {pa ra ra' : Ty} {ea : Eff}
    (ha : a.uncons = some (pa, ea, ra)) (ha' : a'.uncons = some (pa, ea, ra'))
    (hna : ∀ x y, a ≠ .arr [] x y) (hr : ∀ Y, Flows ra Y → Flows ra' Y) {X : Ty}
    (h : Flows a X) : Flows a' X := by
  cases h with
  | arr a0 e0 pa0 pe0 ra0 re0 ea0 ee0 h1 h2 h3 h4 h5 =>
    rw [ha] at h1
    cases h1
    exact Flows.arr _ _ _ _ _ _ _ _ ha' h2 h3 h4 (hr _ h5)
  | nullary ra re ea ee h1 h2 => exact absurd rfl (hna _ _)
  | _ => simp [Ty.uncons] at ha

theorem flows_right_step {e e' : Ty} {pe re re' : Ty} {ee : Eff}
    (he : e.uncons = some (pe, ee, re)) (he' : e'.uncons = some (pe, ee, re'))
    (hne : ∀ x y, e ≠ .arr [] x y) (hr : ∀ Y, Flows Y re → Flows Y re') {X : Ty}
    (h : Flows X e) : Flows X e' := by
  cases h with
  | arr a0 e0 pa0 pe0 ra0 re0 ea0 ee0 h1 h2 h3 h4 h5 =>
    rw [he] at h2
    cases h2
    exact Flows.arr _ _ _ _ _ _ _ _ h1 he' h3 h4 (hr _ h5)
  | nullary ra re ea ee h1 h2 => exact absurd rfl (hne _ _)
  | _ => simp [Ty.uncons] at he

theorem flows_merge_left (q : Ty) (qs : List Ty) (r' : Ty) (e' : Eff) :
    ∀ (ps : List Ty), ps ≠ [] → ∀ X : Ty,
      (Flows (.arr (ps ++ q :: qs) r' e') X ↔ Flows (.arr ps (.arr (q :: qs) r' e') .pure) X)
  | [], h, _ => absurd rfl h
  | [p], _, X => by
    have e1 : (Ty.arr ([p] ++ q :: qs) r' e').uncons = some (p, .pure, .arr (q :: qs) r' e') := by
      simp [Ty.uncons]
    have e2 : (Ty.arr [p] (.arr (q :: qs) r' e') .pure).uncons =
        some (p, .pure, .arr (q :: qs) r' e') := by simp [Ty.uncons]
    exact ⟨flows_left_step e1 e2 (by simp) (fun _ => id),
      flows_left_step e2 e1 (by simp) (fun _ => id)⟩
  | p :: p2 :: ps, _, X => by
    have ih := flows_merge_left q qs r' e' (p2 :: ps) (by simp)
    have e1 : (Ty.arr ((p :: p2 :: ps) ++ q :: qs) r' e').uncons =
        some (p, .pure, .arr ((p2 :: ps) ++ q :: qs) r' e') := by simp [Ty.uncons]
    have e2 : (Ty.arr (p :: p2 :: ps) (.arr (q :: qs) r' e') .pure).uncons =
        some (p, .pure, .arr (p2 :: ps) (.arr (q :: qs) r' e') .pure) := by simp [Ty.uncons]
    exact ⟨flows_left_step e1 e2 (by simp) (fun Y => (ih Y).mp),
      flows_left_step e2 e1 (by simp) (fun Y => (ih Y).mpr)⟩

theorem flows_merge_right (q : Ty) (qs : List Ty) (r' : Ty) (e' : Eff) :
    ∀ (ps : List Ty), ps ≠ [] → ∀ X : Ty,
      (Flows X (.arr (ps ++ q :: qs) r' e') ↔ Flows X (.arr ps (.arr (q :: qs) r' e') .pure))
  | [], h, _ => absurd rfl h
  | [p], _, X => by
    have e1 : (Ty.arr ([p] ++ q :: qs) r' e').uncons = some (p, .pure, .arr (q :: qs) r' e') := by
      simp [Ty.uncons]
    have e2 : (Ty.arr [p] (.arr (q :: qs) r' e') .pure).uncons =
        some (p, .pure, .arr (q :: qs) r' e') := by simp [Ty.uncons]
    exact ⟨flows_right_step e1 e2 (by simp) (fun _ => id),
      flows_right_step e2 e1 (by simp) (fun _ => id)⟩
  | p :: p2 :: ps, _, X => by
    have ih := flows_merge_right q qs r' e' (p2 :: ps) (by simp)
    have e1 : (Ty.arr ((p :: p2 :: ps) ++ q :: qs) r' e').uncons =
        some (p, .pure, .arr ((p2 :: ps) ++ q :: qs) r' e') := by simp [Ty.uncons]
    have e2 : (Ty.arr (p :: p2 :: ps) (.arr (q :: qs) r' e') .pure).uncons =
        some (p, .pure, .arr (p2 :: ps) (.arr (q :: qs) r' e') .pure) := by simp [Ty.uncons]
    exact ⟨flows_right_step e1 e2 (by simp) (fun Y => (ih Y).mp),
      flows_right_step e2 e1 (by simp) (fun Y => (ih Y).mpr)⟩

theorem flows_mkArr_left (ps : List Ty) (r : Ty) (e : Eff) (X : Ty) :
    Flows (mkArr ps r e) X ↔ Flows (.arr ps r e) X := by
  by_cases hm : Ty.mergeable ps r e = true
  · obtain ⟨q, qs, r', e', rfl, rfl, hp, hmk⟩ := mkArr_merge hm
    rw [hmk]
    exact flows_merge_left q qs r' e' ps hp X
  · rw [mkArr_keep (by simpa using hm)]

theorem flows_mkArr_right (ps : List Ty) (r : Ty) (e : Eff) (X : Ty) :
    Flows X (mkArr ps r e) ↔ Flows X (.arr ps r e) := by
  by_cases hm : Ty.mergeable ps r e = true
  · obtain ⟨q, qs, r', e', rfl, rfl, hp, hmk⟩ := mkArr_merge hm
    rw [hmk]
    exact flows_merge_right q qs r' e' ps hp X
  · rw [mkArr_keep (by simpa using hm)]

/-! ### The three arity cases of `flowF`, as `Flows` between stored arrows -/

theorem flows_arr_arr (Ar Br : Ty) (ae be : Eff) (hr : Flows Ar Br) (he : ae.le be = true) :
    ∀ (As Bs : List Ty), As.length = Bs.length → FlowsAll Bs As →
      Flows (.arr As Ar ae) (.arr Bs Br be)
  | [], [], _, _ => Flows.nullary Ar Br ae be hr he
  | [p], [q], _, h => by
    cases h with
    | cons _ _ _ _ hq _ => exact Flows.arr _ _ p q Ar Br ae be rfl rfl hq he hr
  | p :: p2 :: As, q :: q2 :: Bs, hl, h => by
    cases h with
    | cons _ _ _ _ hq hrest =>
      refine Flows.arr _ _ p q (.arr (p2 :: As) Ar ae) (.arr (q2 :: Bs) Br be) .pure .pure rfl rfl
        hq (Eff.le_refl _) ?_
      exact flows_arr_arr Ar Br ae be hr he (p2 :: As) (q2 :: Bs) (by simpa using hl) hrest
  | [], _ :: _, hl, _ => by simp at hl
  | _ :: _, [], hl, _ => by simp at hl
  | [_], _ :: _ :: _, hl, _ => by simp at hl
  | _ :: _ :: _, [_], hl, _ => by simp at hl

/-- The actual arrow has more stored parameters: after the expected ones, its rest flows into the
expected result (`flowF`'s `aps.length > eps.length` case). -/
theorem flows_arr_longer (Ar Br : Ty) (ae be : Eff) (A2 : List Ty) (hA2 : A2 ≠ [])
    (hr : Flows (.arr A2 Ar ae) Br) :
    ∀ (A1 Bs : List Ty), A1.length = Bs.length → Bs ≠ [] → FlowsAll Bs A1 →
      Flows (.arr (A1 ++ A2) Ar ae) (.arr Bs Br be)
  | [], _, hl, hB, _ => by
    exfalso; apply hB; cases ‹List Ty› <;> simp_all
  | [p], [q], _, _, h => by
    obtain ⟨x, xs, rfl⟩ := List.exists_cons_of_ne_nil hA2
    cases h with
    | cons _ _ _ _ hq _ =>
      exact Flows.arr _ _ p q (.arr (x :: xs) Ar ae) Br .pure be (by simp [Ty.uncons])
        rfl hq (Eff.pure_le _) hr
  | p :: p2 :: A1, q :: q2 :: Bs, hl, _, h => by
    cases h with
    | cons _ _ _ _ hq hrest =>
      refine Flows.arr _ _ p q (.arr ((p2 :: A1) ++ A2) Ar ae) (.arr (q2 :: Bs) Br be) .pure .pure
        (by simp [Ty.uncons]) rfl hq (Eff.le_refl _) ?_
      exact flows_arr_longer Ar Br ae be A2 hA2 hr (p2 :: A1) (q2 :: Bs) (by simpa using hl)
        (by simp) hrest
  | [_], [], hl, _, _ => by simp at hl
  | [_], _ :: _ :: _, hl, _, _ => by simp at hl
  | _ :: _ :: _, [], hl, _, _ => by simp at hl
  | _ :: _ :: _, [_], hl, _, _ => by simp at hl

/-- The expected arrow has more stored parameters: the actual result flows into the expected rest
(`flowF`'s `aps.length < eps.length` case; the actual arrow's effect must be pure). -/
theorem flows_arr_shorter (Ar Br : Ty) (be : Eff) (B2 : List Ty) (hB2 : B2 ≠ [])
    (hr : Flows Ar (.arr B2 Br be)) :
    ∀ (As B1 : List Ty), As.length = B1.length → As ≠ [] → FlowsAll B1 As →
      Flows (.arr As Ar .pure) (.arr (B1 ++ B2) Br be)
  | [], _, _, hA, _ => absurd rfl hA
  | [p], [q], _, _, h => by
    obtain ⟨x, xs, rfl⟩ := List.exists_cons_of_ne_nil hB2
    cases h with
    | cons _ _ _ _ hq _ =>
      exact Flows.arr _ _ p q Ar (.arr (x :: xs) Br be) .pure .pure rfl (by simp [Ty.uncons])
        hq (Eff.le_refl _) hr
  | p :: p2 :: As, q :: q2 :: B1, hl, _, h => by
    cases h with
    | cons _ _ _ _ hq hrest =>
      refine Flows.arr _ _ p q (.arr (p2 :: As) Ar .pure) (.arr ((q2 :: B1) ++ B2) Br be)
        .pure .pure rfl (by simp [Ty.uncons]) hq (Eff.le_refl _) ?_
      exact flows_arr_shorter Ar Br be B2 hB2 hr (p2 :: As) (q2 :: B1) (by simpa using hl)
        (by simp) hrest
  | [_], [], hl, _, _ => by simp at hl
  | [_], _ :: _ :: _, hl, _, _ => by simp at hl
  | _ :: _ :: _, [], hl, _, _ => by simp at hl
  | _ :: _ :: _, [_], hl, _, _ => by simp at hl

/-! ### `Peel` through a merge -/

theorem Peel.length_slots {t : Ty} {n : Nat} {ss : List Ty} {r : Ty} {e : Eff}
    (h : Peel t n ss r e) : ss.length = n := by
  induction h with
  | exact ps r e => rfl
  | rest ps r e n h0 hn => simp; omega
  | over ps r e n s2 r2 e2 hp hl _ ih => simp [ih]; omega

theorem notMergeable_of_ok {q : Ty} {qs : List Ty} {r' : Ty} {e' : Eff}
    (h : (Ty.arr (q :: qs) r' e').ok 0 = true) : Ty.mergeable (q :: qs) r' e' = false := by
  simp only [Ty.ok, Bool.and_eq_true, Bool.not_eq_true'] at h
  exact h.2

theorem peel_mkArr_exact {ps : List Ty} {r : Ty} {e : Eff} (hr : r.ok 0 = true) :
    Peel (mkArr ps r e) ps.length ps r e := by
  by_cases hm : Ty.mergeable ps r e = true
  · obtain ⟨q, qs, r', e', rfl, rfl, hp, hmk⟩ := mkArr_merge hm
    rw [hmk]
    have h1 := Peel.rest (ps ++ q :: qs) r' e' ps.length
      (by cases ps with | nil => contradiction | cons _ _ => simp) (by simp)
    rw [List.take_left, List.drop_left, mkArr_keep (notMergeable_of_ok hr)] at h1
    exact h1
  · rw [mkArr_keep (by simpa using hm)]
    exact Peel.exact ps r e

theorem peel_mkArr_rest {ps : List Ty} {r : Ty} {e : Eff} {n : Nat} (hr : r.ok 0 = true)
    (hn : 0 < n) (hlt : n < ps.length) :
    Peel (mkArr ps r e) n (ps.take n) (mkArr (ps.drop n) r e) .pure := by
  by_cases hm : Ty.mergeable ps r e = true
  · obtain ⟨q, qs, r', e', rfl, rfl, hp, hmk⟩ := mkArr_merge hm
    rw [hmk]
    have h1 := Peel.rest (ps ++ q :: qs) r' e' n hn (by simp; omega)
    rw [List.take_append_of_le_length (by omega), List.drop_append_of_le_length (by omega)] at h1
    have hd : ps.drop n ≠ [] := by
      intro h; have := congrArg List.length h; simp at this; omega
    rw [mkArr_assoc _ _ _ _ hd (by simp), mkArr_keep (notMergeable_of_ok hr)] at h1
    exact h1
  · rw [mkArr_keep (by simpa using hm)]
    exact Peel.rest ps r e n hn hlt

theorem peel_merge_arr {ps : List Ty} {q : Ty} {qs : List Ty} {r' : Ty}
    {e' : Eff} {m : Nat} {S : List Ty} {r2 : Ty} {e2 : Eff}
    (h : Peel (.arr (q :: qs) r' e') m S r2 e2) :
    Peel (.arr (ps ++ q :: qs) r' e') (ps.length + m) (ps ++ S) r2 e2 := by
  cases h with
  | exact =>
    have := Peel.exact (ps ++ q :: qs) r' e'
    simpa using this
  | rest _ _ _ _ h0 hn =>
    have h1 := Peel.rest (ps ++ q :: qs) r' e' (ps.length + m) (by omega) (by simp at hn ⊢; omega)
    rw [List.take_append, List.drop_append] at h1
    simpa [List.take_of_length_le, List.drop_of_length_le] using h1
  | over _ _ _ _ s2 _ _ hp hl hrest =>
    have h1 := Peel.over (ps ++ q :: qs) r' e' (ps.length + m) s2 r2 _ (by simp)
      (by simp at hl ⊢; omega)
      (by
        have e : ps.length + m - (ps ++ q :: qs).length = m - (q :: qs).length := by
          simp; omega
        rw [e]; exact hrest)
    simpa [List.append_assoc] using h1

theorem peel_over_mkArr {ps : List Ty} {r : Ty} {e : Eff} {n : Nat} {s2 : List Ty} {r2 : Ty}
    {e2 : Eff} (hr : r.ok 0 = true) (hps : ps ≠ []) (hlt : ps.length < n)
    (h : Peel r (n - ps.length) s2 r2 e2) :
    Peel (mkArr ps r e) n (ps ++ s2) r2 (e.join e2) := by
  by_cases hm : Ty.mergeable ps r e = true
  · obtain ⟨q, qs, r', e', rfl, rfl, hp, hmk⟩ := mkArr_merge hm
    rw [hmk, Eff.join_pure_left]
    have := peel_merge_arr (ps := ps) h
    rwa [Nat.add_sub_cancel' (Nat.le_of_lt hlt)] at this
  · rw [mkArr_keep (by simpa using hm)]
    exact Peel.over ps r e n s2 r2 e2 hps hlt h

end SproutCore
