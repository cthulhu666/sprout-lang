import SproutCore.InferSound

/-!
# Grounding with fresh skolems (docs/lean-model-v0.md §9, 1b step 3b, part 2)

`Generalises t s` (`Judgment.lean`) wants `t` to be the scheme `s` at *distinct* skolems. The
checker's `generalize [] t` turns every unsolved metavariable and every skolem of the zonked type
into a `.gen`. So a grounding `θ` that sends each unsolved metavariable `?m` to its own skolem
`.skolem (B + m)`, with `B` above every skolem of the zonked type, makes `θ t` that scheme at the
table's skolems, and they are distinct.

* `exists_solves_with` (`SolvesExists.lean`): such a `θ` exists, for any `B`.
* `Ty.SkLt B t`: every skolem of `t` is below `B`. No invariant on the state is needed: the caller
  picks `B` after the run, above the skolems of the finitely many zonked types it generalises.
* `genGo_spec`, `generalize_sound`: the scheme `generalize` builds is `Generalises` for every
  grounding that is fresh above `B`.
-/

namespace SproutCore

open Judgment

set_option linter.unusedVariables false

/-! ### Skolems of a type -/

mutual
def Ty.skOf : Ty → List Nat
  | .skolem k => [k]
  | .tup ts => Ty.skOfList ts
  | .arr ps r _ => Ty.skOfList ps ++ Ty.skOf r
  | _ => []

def Ty.skOfList : List Ty → List Nat
  | [] => []
  | t :: ts => Ty.skOf t ++ Ty.skOfList ts
end

theorem mem_skOfList {k : Nat} {ts : List Ty} : k ∈ Ty.skOfList ts ↔ ∃ t ∈ ts, k ∈ Ty.skOf t := by
  induction ts with
  | nil => simp [Ty.skOfList]
  | cons t ts ih => simp [Ty.skOfList, ih]

/-- Every skolem of `t` is below `B`. -/
def Ty.SkLt (B : Nat) (t : Ty) : Prop := ∀ k ∈ t.skOf, k < B

theorem Ty.exists_skLt_list (l : List Nat) : ∃ B, ∀ k ∈ l, k < B := by
  induction l with
  | nil => exact ⟨0, by simp⟩
  | cons a l ih =>
    obtain ⟨B, hB⟩ := ih
    refine ⟨max (a + 1) B, fun k hk => ?_⟩
    rcases List.mem_cons.mp hk with rfl | hk
    · exact Nat.lt_of_lt_of_le (Nat.lt_succ_self _) (Nat.le_max_left _ _)
    · exact Nat.lt_of_lt_of_le (hB k hk) (Nat.le_max_right _ _)

theorem Ty.exists_skLt (t : Ty) : ∃ B, t.SkLt B := Ty.exists_skLt_list t.skOf

theorem Ty.SkLt.mono {B B' : Nat} {t : Ty} (h : t.SkLt B) (hB : B ≤ B') : t.SkLt B' :=
  fun k hk => Nat.lt_of_lt_of_le (h k hk) hB

@[simp] theorem Ty.SkLt_skolem {B k : Nat} : (Ty.skolem k).SkLt B ↔ k < B := by
  simp [Ty.SkLt, Ty.skOf]

theorem Ty.SkLt_tup {B : Nat} {ts : List Ty} :
    (Ty.tup ts).SkLt B ↔ ∀ t ∈ ts, t.SkLt B := by
  simp only [Ty.SkLt, Ty.skOf, mem_skOfList]
  constructor
  · intro h t ht k hk; exact h k ⟨t, ht, hk⟩
  · rintro h k ⟨t, ht, hk⟩; exact h t ht k hk

theorem Ty.SkLt_arr {B : Nat} {ps : List Ty} {r : Ty} {e : Eff} :
    (Ty.arr ps r e).SkLt B ↔ (∀ t ∈ ps, t.SkLt B) ∧ r.SkLt B := by
  simp only [Ty.SkLt, Ty.skOf, mem_skOfList, List.mem_append]
  constructor
  · intro h
    exact ⟨fun t ht k hk => h k (Or.inl ⟨t, ht, hk⟩), fun k hk => h k (Or.inr hk)⟩
  · rintro ⟨h1, h2⟩ k (⟨t, ht, hk⟩ | hk)
    · exact h1 t ht k hk
    · exact h2 k hk

/-! ### The generalisation table -/

/-- The skolem a table entry stands for: a metavariable `?m` gets `B + m`, a skolem itself. -/
def skId (B : Nat) : Ty → Nat
  | .mv m => B + m
  | .skolem k => k
  | _ => 0

/-- Entries are metavariables or skolems below `B`, and the ids they stand for are distinct. -/
def TabOK (B : Nat) (tab : Array Ty) : Prop :=
  (∀ u ∈ tab.toList, (∃ m, u = .mv m) ∨ ∃ k, u = .skolem k ∧ k < B) ∧
    (tab.toList.map (skId B)).Nodup

theorem TabOK.empty (B : Nat) : TabOK B #[] := by
  simp [TabOK]

theorem skId_inj {B : Nat} {u t : Ty}
    (hu : (∃ m, u = .mv m) ∨ ∃ k, u = .skolem k ∧ k < B)
    (ht : (∃ m, t = .mv m) ∨ ∃ k, t = .skolem k ∧ k < B) (h : skId B u = skId B t) : u = t := by
  rcases hu with ⟨a, rfl⟩ | ⟨a, rfl, ha⟩ <;> rcases ht with ⟨b, rfl⟩ | ⟨b, rfl, hb⟩ <;>
    simp only [skId] at h
  · congr 1; omega
  · omega
  · omega
  · rw [h]

theorem genSlot_spec (B : Nat) (tab : Array Ty) (t : Ty) (hT : TabOK B tab)
    (ht : (∃ m, t = .mv m) ∨ ∃ k, t = .skolem k ∧ k < B) :
    (∃ extra, (genSlot tab t).1.toList = tab.toList ++ extra) ∧ TabOK B (genSlot tab t).1 ∧
      ∃ i, (genSlot tab t).2 = .gen i ∧ ∃ h : i < (genSlot tab t).1.size,
        (genSlot tab t).1[i] = t := by
  unfold genSlot
  split
  · rename_i i hi
    obtain ⟨hlt, hbeq, _⟩ := Array.findIdx?_eq_some_iff_getElem.mp hi
    exact ⟨⟨[], by simp⟩, hT, i, rfl, hlt, beq_iff_eq.mp hbeq⟩
  · rename_i hnone
    have hne : ∀ x ∈ tab, x ≠ t := by
      intro x hx hxt
      have := Array.findIdx?_eq_none_iff.mp hnone x hx
      subst hxt
      simp at this
    refine ⟨⟨[t], by simp⟩, ⟨?_, ?_⟩, tab.size, rfl, by simp, by simp⟩
    · intro u hu
      rw [Array.toList_push] at hu
      rcases List.mem_append.mp hu with hu | hu
      · exact hT.1 u hu
      · have := List.mem_singleton.mp hu
        subst this; exact ht
    · rw [Array.toList_push, List.map_append]
      refine List.nodup_append.mpr ⟨hT.2, by simp, ?_⟩
      intro a ha b hb hab
      have hb' : b = skId B t := by simpa using hb
      obtain ⟨u, hu, rfl⟩ := List.mem_map.mp ha
      have := skId_inj (hT.1 u hu) ht (hab.trans hb')
      exact hne u (Array.mem_toList_iff.mp hu) this


theorem inst_table (B : Nat) (tab : Array Ty) (i : Nat) (h : i < tab.size) (extra : List Nat) :
    Ty.inst (((tab.toList.map (skId B)) ++ extra).map Ty.skolem) (.gen i) =
      .skolem (skId B tab[i]) := by
  simp only [Ty.inst, List.getD_eq_getElem?_getD]
  rw [List.getElem?_map, List.getElem?_append_left (by simp [h]), List.getElem?_map]
  simp [h]

theorem genSlot_gen (tab : Array Ty) (t : Ty) : ∃ i, (genSlot tab t).2 = .gen i := by
  unfold genSlot
  split
  · exact ⟨_, rfl⟩
  · exact ⟨_, rfl⟩

theorem genGoList_len (keep : List Nat) :
    ∀ (tab : Array Ty) (ts : List Ty), (genGoList keep tab ts).2.length = ts.length
  | _, [] => rfl
  | tab, t :: ts => by
    simp only [genGoList, List.length_cons]
    rw [genGoList_len keep _ ts]

theorem genGo_mv (tab : Array Ty) (n : Nat) : genGo [] tab (.mv n) = genSlot tab (.mv n) := by
  simp [genGo]

theorem genGo_skolem (tab : Array Ty) (n : Nat) :
    genGo [] tab (.skolem n) = genSlot tab (.skolem n) := by
  simp [genGo]

theorem genGo_isArr (tab : Array Ty) (r : Ty)
    (h : ∃ q qs r2 e2, (genGo [] tab r).2 = .arr (q :: qs) r2 e2) :
    ∃ q qs r2 e2, r = .arr (q :: qs) r2 e2 := by
  cases r with
  | arr ps r0 e =>
    obtain ⟨q, qs, r2, e2, h⟩ := h
    simp only [genGo, Ty.arr.injEq] at h
    obtain ⟨hps, _⟩ := h
    have hl := genGoList_len [] tab ps
    rcases ps with _ | ⟨p, ps⟩
    · rw [hps] at hl; simp at hl
    · exact ⟨p, ps, r0, e, rfl⟩
  | mv n =>
    obtain ⟨i, hi⟩ := genSlot_gen tab (.mv n)
    obtain ⟨q, qs, r2, e2, h⟩ := h
    rw [genGo_mv, hi] at h; cases h
  | skolem n =>
    obtain ⟨i, hi⟩ := genSlot_gen tab (.skolem n)
    obtain ⟨q, qs, r2, e2, h⟩ := h
    rw [genGo_skolem, hi] at h; cases h
  | named _ => obtain ⟨q, qs, r2, e2, h⟩ := h; simp [genGo] at h
  | gen _ => obtain ⟨q, qs, r2, e2, h⟩ := h; simp [genGo] at h
  | int => obtain ⟨q, qs, r2, e2, h⟩ := h; simp [genGo] at h
  | bool => obtain ⟨q, qs, r2, e2, h⟩ := h; simp [genGo] at h
  | str => obtain ⟨q, qs, r2, e2, h⟩ := h; simp [genGo] at h
  | unit => obtain ⟨q, qs, r2, e2, h⟩ := h; simp [genGo] at h
  | tup ts => obtain ⟨q, qs, r2, e2, h⟩ := h; simp [genGo] at h


/-- What `genGo` leaves for a type `t`: the table only grew and is still `TabOK`; the result is a
scheme body over the table, has no skolem, and at the table's skolems (or any longer list) it is
the grounding of `t`. -/
def GenRes (B : Nat) (θ : Nat → Ty) (tab : Array Ty) (t : Ty) (r : Array Ty × Ty) : Prop :=
  (∃ extra, r.1.toList = tab.toList ++ extra) ∧ TabOK B r.1 ∧ r.2.ok r.1.size = true ∧
    r.2.noSk = true ∧ ∀ ids, (∃ extra, ids = r.1.toList.map (skId B) ++ extra) →
      Ty.inst (ids.map Ty.skolem) r.2 = Ty.app θ t

def GenResList (B : Nat) (θ : Nat → Ty) (tab : Array Ty) (ts : List Ty)
    (r : Array Ty × List Ty) : Prop :=
  (∃ extra, r.1.toList = tab.toList ++ extra) ∧ TabOK B r.1 ∧ Ty.okList r.1.size r.2 = true ∧
    Ty.noSkList r.2 = true ∧ ∀ ids, (∃ extra, ids = r.1.toList.map (skId B) ++ extra) →
      Ty.instList (ids.map Ty.skolem) r.2 = Ty.appList θ ts

theorem GenRes.base {B : Nat} {θ : Nat → Ty} {tab : Array Ty} {t : Ty} (hT : TabOK B tab)
    (hok : t.ok tab.size = true) (hsk : t.noSk = true) (hi : ∀ ts, Ty.inst ts t = Ty.app θ t) :
    GenRes B θ tab t (tab, t) :=
  ⟨⟨[], by simp⟩, hT, hok, hsk, fun ids _ => hi _⟩

mutual
theorem genGo_spec (B : Nat) (θ : Nat → Ty) :
    ∀ (t : Ty) (tab : Array Ty), (∀ m ∈ metasOf t, θ m = .skolem (B + m)) → gensOf t = [] →
      Ty.NF t → t.Canon → t.SkLt B → TabOK B tab → GenRes B θ tab t (genGo [] tab t)
  | .mv n, tab, hm, hg, hn, hc, hk, hT => by
    rw [genGo_mv]
    obtain ⟨⟨extra, hex⟩, hT', i, hi, hlt, hent⟩ := genSlot_spec B tab (.mv n) hT (Or.inl ⟨n, rfl⟩)
    refine ⟨⟨extra, hex⟩, hT', ?_, ?_, ?_⟩
    · rw [hi]; simp [Ty.ok, hlt]
    · rw [hi]; rfl
    · rintro ids ⟨ex, rfl⟩
      rw [hi, inst_table B _ i hlt ex, hent]
      simp [Ty.app, skId, hm n (by simp [metasOf])]
  | .skolem k, tab, hm, hg, hn, hc, hk, hT => by
    rw [genGo_skolem]
    have hk' : k < B := Ty.SkLt_skolem.mp hk
    obtain ⟨⟨extra, hex⟩, hT', i, hi, hlt, hent⟩ :=
      genSlot_spec B tab (.skolem k) hT (Or.inr ⟨k, rfl, hk'⟩)
    refine ⟨⟨extra, hex⟩, hT', ?_, ?_, ?_⟩
    · rw [hi]; simp [Ty.ok, hlt]
    · rw [hi]; rfl
    · rintro ids ⟨ex, rfl⟩
      rw [hi, inst_table B _ i hlt ex, hent]
      simp [Ty.app, skId]
  | .named s, tab, hm, hg, hn, hc, hk, hT => absurd hn Ty.not_NF_named
  | .gen i, tab, hm, hg, hn, hc, hk, hT => by simp [gensOf] at hg
  | .int, tab, hm, hg, hn, hc, hk, hT =>
    GenRes.base hT (by simp [Ty.ok]) (by simp [Ty.noSk]) (fun _ => rfl)
  | .bool, tab, hm, hg, hn, hc, hk, hT =>
    GenRes.base hT (by simp [Ty.ok]) (by simp [Ty.noSk]) (fun _ => rfl)
  | .str, tab, hm, hg, hn, hc, hk, hT =>
    GenRes.base hT (by simp [Ty.ok]) (by simp [Ty.noSk]) (fun _ => rfl)
  | .unit, tab, hm, hg, hn, hc, hk, hT =>
    GenRes.base hT (by simp [Ty.ok]) (by simp [Ty.noSk]) (fun _ => rfl)
  | .tup ts, tab, hm, hg, hn, hc, hk, hT => by
    have hsp := genGoList_spec B θ ts tab (by simpa [metasOf] using hm) (by simpa [gensOf] using hg)
      (Ty.NF_tup.mp hn) hc (Ty.SkLt_tup.mp hk) hT
    simp only [genGo]
    rcases hp : genGoList [] tab ts with ⟨tab1, ts'⟩
    rw [hp] at hsp
    obtain ⟨hex, hT1, hok, hsk, hin⟩ := hsp
    refine ⟨hex, hT1, by simpa [Ty.ok] using hok, by simpa [Ty.noSk] using hsk, ?_⟩
    intro ids hids
    simp only [Ty.inst, Ty.app]
    rw [hin ids hids]
  | .arr ps r e, tab, hm, hg, hn, hc, hk, hT => by
    obtain ⟨hcps, hcr, hmg⟩ := hc
    have hsp1 := genGoList_spec B θ ps tab
      (fun m hm' => hm m (by simp [metasOf, List.mem_append, hm']))
      (by simpa [gensOf] using (List.append_eq_nil_iff.mp (by simpa [gensOf] using hg)).1)
      (Ty.NF_arr.mp hn).1 hcps (Ty.SkLt_arr.mp hk).1 hT
    simp only [genGo]
    rcases hp1 : genGoList [] tab ps with ⟨tab1, ps'⟩
    rw [hp1] at hsp1
    obtain ⟨⟨ex1, hex1⟩, hT1, hok1, hsk1, hin1⟩ := hsp1
    have hsp2 := genGo_spec B θ r tab1
      (fun m hm' => hm m (by simp [metasOf, List.mem_append, hm']))
      (by simpa [gensOf] using (List.append_eq_nil_iff.mp (by simpa [gensOf] using hg)).2)
      (Ty.NF_arr.mp hn).2 hcr (Ty.SkLt_arr.mp hk).2 hT1
    rcases hp2 : genGo [] tab1 r with ⟨tab2, r'⟩
    rw [hp2] at hsp2
    obtain ⟨⟨ex2, hex2⟩, hT2, hok2, hsk2, hin2⟩ := hsp2
    simp only
    refine ⟨⟨ex1 ++ ex2, by rw [hex2, hex1, List.append_assoc]⟩, hT2, ?_, ?_, ?_⟩
    · have hlen : tab1.size ≤ tab2.size := by
        have := congrArg List.length hex2
        simp at this
        omega
      simp only [Ty.ok, Bool.and_eq_true, Bool.not_eq_true']
      refine ⟨⟨Ty.okList_mono hlen _ hok1, hok2⟩, ?_⟩
      cases hmm : Ty.mergeable ps' r' e
      · rfl
      · exfalso
        obtain ⟨q, qs, r2, e2, he, hps', hr'⟩ := mergeable_iff.mp hmm
        obtain ⟨q', qs', r3, e3, hr⟩ := genGo_isArr tab1 r ⟨q, qs, r2, e2, by rw [hp2] ; exact hr'⟩
        have hpsne : ps ≠ [] := by
          intro h0
          subst h0
          have := genGoList_len [] tab []
          rw [hp1] at this
          simp at this
          exact hps' this
        have : Ty.mergeable ps r e = true := mergeable_iff.mpr ⟨q', qs', r3, e3, he, hpsne, hr⟩
        rw [hmg] at this; cases this
    · simp only [Ty.noSk, Bool.and_eq_true]
      exact ⟨hsk1, hsk2⟩
    · intro ids ⟨ex, hids⟩
      simp only [Ty.inst, Ty.app]
      have h1 := hin1 ids ⟨ex2.map (skId B) ++ ex, by rw [hids, hex2]; simp⟩
      have h2 := hin2 ids ⟨ex, hids⟩
      rw [h1, h2]

theorem genGoList_spec (B : Nat) (θ : Nat → Ty) :
    ∀ (ts : List Ty) (tab : Array Ty), (∀ m ∈ metasOfList ts, θ m = .skolem (B + m)) →
      gensOfList ts = [] → (∀ t ∈ ts, Ty.NF t) → Ty.CanonList ts → (∀ t ∈ ts, t.SkLt B) →
      TabOK B tab → GenResList B θ tab ts (genGoList [] tab ts)
  | [], tab, hm, hg, hn, hc, hk, hT => by
    refine ⟨⟨[], by simp [genGoList]⟩, ?_, ?_, ?_, ?_⟩ <;> simp [genGoList, hT, Ty.okList, Ty.noSkList, Ty.instList, Ty.appList]
  | t :: ts, tab, hm, hg, hn, hc, hk, hT => by
    have hg' := List.append_eq_nil_iff.mp (by simpa [gensOfList] using hg)
    have hsp1 := genGo_spec B θ t tab
      (fun m hm' => hm m (by simp [metasOfList, List.mem_append, hm']))
      hg'.1 (hn t (List.mem_cons_self ..)) hc.1 (hk t (List.mem_cons_self ..)) hT
    simp only [genGoList]
    rcases hp1 : genGo [] tab t with ⟨tab1, t'⟩
    rw [hp1] at hsp1
    obtain ⟨⟨ex1, hex1⟩, hT1, hok1, hsk1, hin1⟩ := hsp1
    have hsp2 := genGoList_spec B θ ts tab1
      (fun m hm' => hm m (by simp [metasOfList, List.mem_append, hm']))
      hg'.2 (fun u hu => hn u (List.mem_cons_of_mem _ hu)) hc.2
      (fun u hu => hk u (List.mem_cons_of_mem _ hu)) hT1
    rcases hp2 : genGoList [] tab1 ts with ⟨tab2, ts'⟩
    rw [hp2] at hsp2
    obtain ⟨⟨ex2, hex2⟩, hT2, hok2, hsk2, hin2⟩ := hsp2
    simp only
    refine ⟨⟨ex1 ++ ex2, by rw [hex2, hex1, List.append_assoc]⟩, hT2, ?_, ?_, ?_⟩
    · have hlen : tab1.size ≤ tab2.size := by
        have := congrArg List.length hex2
        simp at this
        omega
      simp only [Ty.okList, Bool.and_eq_true]
      exact ⟨Ty.ok_mono hlen _ hok1, hok2⟩
    · simp only [Ty.noSkList, Bool.and_eq_true]
      exact ⟨hsk1, hsk2⟩
    · intro ids ⟨ex, hids⟩
      simp only [Ty.instList, Ty.appList]
      have h1 := hin1 ids ⟨ex2.map (skId B) ++ ex, by rw [hids, hex2]; simp⟩
      have h2 := hin2 ids ⟨ex, hids⟩
      rw [h1, h2]
end


/-- `generalize [] t`, soundly. The type it generalises is the zonk of `t`; `h` gets that zonk, so
the caller can pick `B` above its skolems. For every grounding that is fresh above such a `B` (a
solution of the state that sends each unsolved metavariable of the zonk to its own skolem
`B + m`), the scheme `generalize` returns is `Generalises` the grounded `t`. The scheme is closed
(`ok n`) whatever the grounding: some fresh grounding exists (`exists_solves_with`). -/
theorem generalize_sound {Q : Nat × Ty → CState → Prop} {s : CState} {t : Ty}
    (hs : s.WF) (hnf : s.NF) (ht : Ty.WF s.sz 0 t) (htn : t.NF)
    (h : ∀ n t' zt, t'.ok n = true → (∀ m ∈ metasOf zt, s.Unbound m) →
      (∀ B, zt.SkLt B → ∀ θ, Solves θ s → (∀ m ∈ metasOf zt, θ m = .skolem (B + m)) →
        Generalises (Ty.app θ t) ⟨n, t'⟩) → Q (n, t') s) :
    Wp (generalize [] t) Q s := by
  unfold generalize
  refine Wp.bind (Wp.mono (Wp.and' (zonk_leaf
    (Q := fun r s' => s' = s ∧ Ty.WF s.sz 0 r ∧ r.NF ∧ (∀ m ∈ metasOf r, s.Unbound m) ∧
      ∀ θ, Solves θ s → Ty.app θ r = Ty.app θ t) hs hnf ht htn
    (fun r s' e w n u a => ⟨e, w, n, u, a⟩)) (zonk_canon t s)) ?_)
  intro zt s1 ⟨⟨e, w, n, u, a⟩, hc, _⟩
  subst e
  have hg : gensOf zt = [] := List.eq_nil_iff_forall_not_mem.mpr fun i hi => by
    have := w.2 i hi; omega
  rcases hgg : genGo [] #[] zt with ⟨tab, t'⟩
  have key : ∀ B, zt.SkLt B → ∀ θ, Solves θ s1 → (∀ m ∈ metasOf zt, θ m = .skolem (B + m)) →
      Generalises (Ty.app θ t) ⟨tab.size, t'⟩ := by
    intro B hB θ hθ hm
    have hsp := genGo_spec B θ zt #[] hm hg n hc hB (TabOK.empty B)
    rw [hgg] at hsp
    obtain ⟨_, hT, hok, hsk, hin⟩ := hsp
    refine ⟨hok, hsk, tab.toList.map (skId B), by simp, hT.2, ?_⟩
    rw [← a θ hθ]
    exact hin _ ⟨[], by simp⟩
  obtain ⟨B, hB⟩ := Ty.exists_skLt zt
  obtain ⟨θ, hθ, hfresh⟩ := exists_solves_with hs hnf (fun m => .skolem (B + m)) (fun _ => rfl)
  have hok := (key B hB θ hθ (fun m hm => hfresh m (u m hm))).1
  exact Wp.pure' (h tab.size t' zt hok u key)

/-! ### Closed schemes -/

mutual
/-- A scheme body with no metavariable is fixed by any grounding. -/
theorem Ty.app_of_ok_gen (θ : Nat → Ty) {g : Nat} : ∀ t : Ty, t.ok g = true → Ty.app θ t = t
  | .named _, h => by simp [Ty.ok] at h
  | .mv _, h => by simp [Ty.ok] at h
  | .skolem _, _ => rfl
  | .gen _, _ => rfl
  | .int, _ => rfl
  | .bool, _ => rfl
  | .str, _ => rfl
  | .unit, _ => rfl
  | .tup ts, h => by
    simp only [Ty.ok] at h
    simp only [Ty.app, Ty.appList_of_ok_gen θ ts h]
  | .arr ps r e, h => by
    simp only [Ty.ok, Bool.and_eq_true, Bool.not_eq_true'] at h
    simp only [Ty.app, Ty.appList_of_ok_gen θ ps h.1.1, Ty.app_of_ok_gen θ r h.1.2]
    exact mkArr_keep (by simpa using h.2)

theorem Ty.appList_of_ok_gen (θ : Nat → Ty) {g : Nat} :
    ∀ ts : List Ty, Ty.okList g ts = true → Ty.appList θ ts = ts
  | [], _ => rfl
  | t :: ts, h => by
    simp only [Ty.okList, Bool.and_eq_true] at h
    simp only [Ty.appList, Ty.app_of_ok_gen θ t h.1, Ty.appList_of_ok_gen θ ts h.2]
end

/-- A closed environment's context does not depend on the grounding. -/
theorem Env.app_closed {env : Env} (h : Env.Closed env) (θ θ' : Nat → Ty) :
    Env.app θ env = Env.app θ' env := by
  unfold Env.app
  apply List.map_congr_left
  intro p hp
  have := h p hp
  obtain ⟨x, b⟩ := p
  cases b with
  | mono t => exact False.elim this
  | poly n t => simp [Bind.app, Ty.app_of_ok_gen θ t this, Ty.app_of_ok_gen θ' t this]
  | fnMono k t => exact False.elim this
  | fnPoly k n t => simp [Bind.app, Ty.app_of_ok_gen θ t this, Ty.app_of_ok_gen θ' t this]

end SproutCore
