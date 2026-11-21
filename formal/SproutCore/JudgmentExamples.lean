import SproutCore.JudgmentLemmas
import SproutCore.Tests

/-!
# What the typing judgment accepts and rejects (docs/lean-model-v0.md §9 1b)

A permissive judgment makes T2 empty, so the judgment has to refuse what the spec refuses. Each
`*_rejected` theorem is `¬ p.Typed` for a concrete program `p`: no partition into groups, no order
of the groups, no choice of types derives it. Each rejected program has an accepted twin
(`*_typed`, the "controls") that differs in the one feature the rule is about, so a rejection
cannot come from the judgment being empty.

| theorem | program | spec |
|---|---|---|
| `r15a_rejected` | `fn f(x: a) -> Int = x` | §7 rule 15 |
| `r15b_rejected` | `fn f(x: a, g: b -> c) -> a = g` | §7 rule 15 |
| `r15c_rejected` | `fn f(x: a, y: b) -> a = y` | §7 rule 15 |
| `r56_rejected` | a local `let id = \x -> x` used at `Int` and at `Bool` | §5.6 |
| `r9a/b/c_rejected` | `() -> T` into `Unit -> T`, the reverse, `k(1)` for `k: () -> T` | §7 r9 |
| `r8arg_rejected` | an `!{IO}` value in a pure argument slot | §7 rule 8 |
| `r8ret_rejected` | an `!{IO}` value returned as a pure one | §7 rule 8 |
| `r8contra_rejected` | a pure-consuming fn where an `!{IO}`-consuming one is due | §7 rule 8 |
| `joinPure_rejected` | `if c then pure_fn else io_fn` into a pure slot, either order | §7 rule 8 |
| `r11param/print_rejected` | an `!{IO}` call in a pure `fn` body | §7 rules 8, 11 |
| `r5let_rejected` | `let boom = print_str("x")` | §5.2 |
| `r5arity_rejected` | `print_str()`: a named `fn` is called with exactly its arity | §5.3 |
| `r5zero_rejected` | `h()` for `h: Int -> Int`: no arguments to an arrow that takes some | §5.3 |
| `okLetAnnIO_typed` | `let f : a -> a !{IO} = \(h: Int -> Int) -> h` (accepted) | §7 rule 9 |
| `r9ret_rejected` | `fn f(x: Int) -> Int -> Int !{IO} = do print_str("a"); \y -> y` | §7 rule 9 |

Rule 15's first example, `fn f(x: a) -> Int = x + 1`, needs `+`, which Core does not have. `x`
returned at `Int` forces `a = Int` in the same way, and is the nearest expressible program.

`okLetGen_typed` uses a top-level `let` at two types, `okFnGen_typed` and `okFnGenW_typed` a `fn`
at two types (omitted and written annotations), `okJoin_typed` a peer join of a pure and an
`!{IO}` function into an `!{IO}` slot, `okContra_typed` the contravariant twin of `r8contra`,
`okMain_typed` a `main` with a `do` block, `okRetIO_typed` the innermost-arrow reading of a
signature's `!{IO}`. The `#guard`s at the end record that the checker gives the same verdict on
every program here, except the one the BACKLOG entry names (`okRetIO`).
-/

namespace SproutCore.Judgment

/-- `fn f(x: a) -> Int = x`. -/
def r15a : Program := ⟨[.fn "f" [("x", some (.named "a"))] (some (.int, .pure)) (.var "x")]⟩

theorem r15a_rejected : ¬ r15a.Typed := by
  intro h
  obtain ⟨σ, τ, sc, hsk, hnd, ps, r, e, tb, eb, hps, hret, hτ, hbody, hflow, heff⟩ :=
    single_fn_inv h
  cases hps with
  | written x a p prs ps' hel hrest =>
    cases hrest
    cases hret with
    | written a' r' e' hel' hn =>
      simp [Ty.elab] at hel hel'
      subst hel'
      simp [bindParams] at hbody
      cases hbody with
      | var _ _ b' _ hl hi =>
        simp [List.lookup] at hl
        subst hl
        simp [Binding.Inst] at hi
        subst hi
        obtain ⟨k, hk⟩ := hsk "a" (by simp [FnD.vars, Ty.vars])
        rw [hel] at hk
        subst hk
        have := hflow.to_int
        simp at this

/-- `fn f(x: a, g: b -> c) -> a = g`. -/
def r15b : Program :=
  ⟨[.fn "f" [("x", some (.named "a")), ("g", some (.arr [.named "b"] (.named "c") .pure))]
    (some (.named "a", .pure)) (.var "g")]⟩

theorem r15b_rejected : ¬ r15b.Typed := by
  intro h
  obtain ⟨σ, τ, sc, hsk, hnd, ps, r, e, tb, eb, hps, hret, hτ, hbody, hflow, heff⟩ :=
    single_fn_inv h
  have hvars : (FnD.mk "f" [("x", some (.named "a")),
      ("g", some (.arr [.named "b"] (.named "c") .pure))]
    (some (.named "a", .pure)) (.var "g")).vars = ["a", "b", "c"] := by decide
  rw [hvars] at hsk
  obtain ⟨ka, hka⟩ := hsk "a" (by simp)
  obtain ⟨kc, hkc⟩ := hsk "c" (by simp)
  cases hps with
  | written x a p prs ps' hel hrest =>
    cases hrest with
    | written x2 a2 p2 prs2 ps2 hel2 hrest2 =>
      cases hrest2
      cases hret with
      | written a' r' e' hel' hn =>
        simp [Ty.elab, Ty.elabList, hkc, mkArr] at hel2
        simp [Ty.elab] at hel hel'
        subst hel'
        simp [bindParams] at hbody
        cases hbody with
        | var _ _ b' _ hl hi =>
          simp [List.lookup] at hl
          subst hl
          simp [Binding.Inst] at hi
          subst hi
          rw [hka] at hflow
          have := hflow.to_skolem
          rw [← hel2.2] at this
          simp at this

/-- `fn f(x: a, y: b) -> a = y`. -/
def r15c : Program :=
  ⟨[.fn "f" [("x", some (.named "a")), ("y", some (.named "b"))] (some (.named "a", .pure))
    (.var "y")]⟩

theorem r15c_rejected : ¬ r15c.Typed := by
  intro h
  obtain ⟨σ, τ, sc, hsk, hnd, ps, r, e, tb, eb, hps, hret, hτ, hbody, hflow, heff⟩ :=
    single_fn_inv h
  have hvars : (FnD.mk "f" [("x", some (.named "a")), ("y", some (.named "b"))]
    (some (.named "a", .pure)) (.var "y")).vars = ["a", "b"] := by decide
  rw [hvars] at hsk hnd
  obtain ⟨ka, hka⟩ := hsk "a" (by simp)
  obtain ⟨kb, hkb⟩ := hsk "b" (by simp)
  cases hps with
  | written x a p prs ps' hel hrest =>
    cases hrest with
    | written x2 a2 p2 prs2 ps2 hel2 hrest2 =>
      cases hrest2
      cases hret with
      | written a' r' e' hel' hn =>
        simp [Ty.elab] at hel hel' hel2
        subst hel'
        simp [bindParams] at hbody
        cases hbody with
        | var _ _ b' _ hl hi =>
          simp [List.lookup] at hl
          subst hl
          simp [Binding.Inst] at hi
          subst hi
          rw [← hel2, hkb, hka] at hflow
          have := hflow.skolem_inv
          simp at this
          subst this
          simp [hka, hkb] at hnd

/-! ### Rule 9: `() -> T` is not `Unit -> T` -/

/-- `fn f(k: () -> Int) -> Unit -> Int = k`. -/
def r9a : Program :=
  ⟨[.fn "f" [("k", some (.arr [] .int .pure))] (some (.arr [.unit] .int .pure, .pure))
    (.var "k")]⟩

theorem r9a_rejected : ¬ r9a.Typed := by
  intro h
  obtain ⟨σ, τ, sc, hsk, hnd, ps, r, e, tb, eb, hps, hret, hτ, hbody, hflow, heff⟩ :=
    single_fn_inv h
  cases hps with
  | written x a p prs ps' hel hrest =>
    cases hrest
    cases hret with
    | written a' r' e' hel' hn =>
      have e1 : (Ty.arr [] .int .pure).elab σ = some (.arr [] .int .pure) := rfl
      have e2 : (Ty.arr [.unit] .int .pure).elab σ = some (.arr [.unit] .int .pure) := rfl
      rw [e1] at hel
      rw [e2] at hel'
      obtain rfl := Option.some.inj hel
      obtain rfl := Option.some.inj hel'
      simp [bindParams] at hbody
      cases hbody with
      | var _ _ b' _ hl hi =>
        simp [List.lookup] at hl
        subst hl
        simp [Binding.Inst] at hi
        subst hi
        cases hflow with
        | arr _ _ _ _ _ _ _ _ h1 => simp [Ty.uncons] at h1

/-- `fn f(u: Unit -> Int) -> () -> Int = u`. -/
def r9b : Program :=
  ⟨[.fn "f" [("u", some (.arr [.unit] .int .pure))] (some (.arr [] .int .pure, .pure))
    (.var "u")]⟩

theorem r9b_rejected : ¬ r9b.Typed := by
  intro h
  obtain ⟨σ, τ, sc, hsk, hnd, ps, r, e, tb, eb, hps, hret, hτ, hbody, hflow, heff⟩ :=
    single_fn_inv h
  cases hps with
  | written x a p prs ps' hel hrest =>
    cases hrest
    cases hret with
    | written a' r' e' hel' hn =>
      have e1 : (Ty.arr [.unit] .int .pure).elab σ = some (.arr [.unit] .int .pure) := rfl
      have e2 : (Ty.arr [] .int .pure).elab σ = some (.arr [] .int .pure) := rfl
      rw [e1] at hel
      rw [e2] at hel'
      obtain rfl := Option.some.inj hel
      obtain rfl := Option.some.inj hel'
      simp [bindParams] at hbody
      cases hbody with
      | var _ _ b' _ hl hi =>
        simp [List.lookup] at hl
        subst hl
        simp [Binding.Inst] at hi
        subst hi
        cases hflow with
        | arr _ _ _ _ _ _ _ _ _ h2 => simp [Ty.uncons] at h2

/-- `fn f(k: () -> Int) -> Int = k(1)`: a `() -> T` takes the empty argument list. -/
def r9c : Program :=
  ⟨[.fn "f" [("k", some (.arr [] .int .pure))] (some (.int, .pure)) (.app (.var "k") [.int 1])]⟩

theorem r9c_rejected : ¬ r9c.Typed := by
  intro h
  obtain ⟨σ, τ, sc, hsk, hnd, ps, r, e, tb, eb, hps, hret, hτ, hbody, hflow, heff⟩ :=
    single_fn_inv h
  cases hps with
  | written x a p prs ps' hel hrest =>
    cases hrest
    have e1 : (Ty.arr [] .int .pure).elab σ = some (.arr [] .int .pure) := rfl
    rw [e1] at hel
    obtain rfl := Option.some.inj hel
    simp [bindParams] at hbody
    cases hbody with
    | app _ _ _ tf ef ts ss res ce εa hf harity hpeel hargs hfl =>
      cases hf with
      | var _ _ b' _ hl hi =>
        simp [List.lookup] at hl
        subst hl
        simp [Binding.Inst] at hi
        subst hi
        simp at hpeel
        cases hpeel <;> simp_all

/-! ### Rule 8: an `!{IO}` function value in a pure slot -/

/-- `fn q(p: (String -> Unit) -> Unit, f: String -> Unit !{IO}) -> Unit = p(f)`: an argument. -/
def r8arg : Program :=
  ⟨[.fn "q" [("p", some (.arr [.arr [.str] .unit .pure] .unit .pure)),
      ("f", some (.arr [.str] .unit .io))] (some (.unit, .pure)) (.app (.var "p") [.var "f"])]⟩

theorem r8arg_rejected : ¬ r8arg.Typed := by
  intro h
  obtain ⟨σ, τ, sc, hsk, hnd, ps, r, e, tb, eb, hps, hret, hτ, hbody, hflow, heff⟩ :=
    single_fn_inv h
  cases hps with
  | written x a p prs ps' hel hrest =>
    cases hrest with
    | written x2 a2 p2 prs2 ps2 hel2 hrest2 =>
      cases hrest2
      have e1 : (Ty.arr [.arr [.str] .unit .pure] .unit .pure).elab σ =
          some (.arr [.arr [.str] .unit .pure] .unit .pure) := rfl
      have e2 : (Ty.arr [.str] .unit .io).elab σ = some (.arr [.str] .unit .io) := rfl
      rw [e1] at hel
      rw [e2] at hel2
      obtain rfl := Option.some.inj hel
      obtain rfl := Option.some.inj hel2
      simp [bindParams] at hbody
      cases hbody with
      | app _ _ _ tf ef ts ss res ce εa hf harity hpeel hargs hfl =>
        cases hf with
        | var _ _ b' _ hl hi =>
          simp [List.lookup] at hl
          subst hl
          simp [Binding.Inst] at hi
          subst hi
          simp at hpeel
          cases hpeel with
          | exact =>
            cases hargs with
            | cons _ _ _ t' ts' _ _ ha hrest =>
              cases ha with
              | var _ _ b'' _ hl' hi' =>
                simp [List.lookup] at hl'
                subst hl'
                simp [Binding.Inst] at hi'
                subst hi'
                cases hfl with
                | cons _ _ _ _ hfl1 _ =>
                  cases hfl1 with
                  | arr _ _ _ _ _ _ _ _ h1 h2 hp he hr =>
                    simp [Ty.uncons] at h1 h2
                    obtain ⟨_, rfl, _⟩ := h1
                    obtain ⟨_, rfl, _⟩ := h2
                    simp [Eff.le] at he
          | rest _ _ _ _ _ hn => simp at hn
          | over _ _ _ _ _ _ _ hne hlt _ => simp at hlt

/-- `fn r(f: String -> Unit !{IO}) -> (String -> Unit) = f`: a return. -/
def r8ret : Program :=
  ⟨[.fn "r" [("f", some (.arr [.str] .unit .io))] (some (.arr [.str] .unit .pure, .pure))
    (.var "f")]⟩

theorem r8ret_rejected : ¬ r8ret.Typed := by
  intro h
  obtain ⟨σ, τ, sc, hsk, hnd, ps, r, e, tb, eb, hps, hret, hτ, hbody, hflow, heff⟩ :=
    single_fn_inv h
  cases hps with
  | written x a p prs ps' hel hrest =>
    cases hrest
    cases hret with
    | written a' r' e' hel' hn =>
      have e1 : (Ty.arr [.str] .unit .io).elab σ = some (.arr [.str] .unit .io) := rfl
      have e2 : (Ty.arr [.str] .unit .pure).elab σ = some (.arr [.str] .unit .pure) := rfl
      rw [e1] at hel
      rw [e2] at hel'
      obtain rfl := Option.some.inj hel
      obtain rfl := Option.some.inj hel'
      simp [bindParams] at hbody
      obtain ⟨b', hl, hi, -⟩ := hbody.var_inv
      simp [List.lookup] at hl
      subst hl
      simp [Binding.Inst] at hi
      subst hi
      cases hflow with
      | arr _ _ _ _ _ _ _ _ h1 h2 hp he hr =>
        simp [Ty.uncons] at h1 h2
        obtain ⟨_, rfl, _⟩ := h1
        obtain ⟨_, rfl, _⟩ := h2
        simp [Eff.le] at he

/-- `fn c(k: (String -> Unit) -> Unit) -> ((String -> Unit !{IO}) -> Unit) = k`: a function that
consumes a pure arrow does not stand where one consuming an `!{IO}` arrow is required. -/
def r8contra : Program :=
  ⟨[.fn "c" [("k", some (.arr [.arr [.str] .unit .pure] .unit .pure))]
    (some (.arr [.arr [.str] .unit .io] .unit .pure, .pure)) (.var "k")]⟩

theorem r8contra_rejected : ¬ r8contra.Typed := by
  intro h
  obtain ⟨σ, τ, sc, hsk, hnd, ps, r, e, tb, eb, hps, hret, hτ, hbody, hflow, heff⟩ :=
    single_fn_inv h
  cases hps with
  | written x a p prs ps' hel hrest =>
    cases hrest
    cases hret with
    | written a' r' e' hel' hn =>
      have e1 : (Ty.arr [.arr [.str] .unit .pure] .unit .pure).elab σ =
          some (.arr [.arr [.str] .unit .pure] .unit .pure) := rfl
      have e2 : (Ty.arr [.arr [.str] .unit .io] .unit .pure).elab σ =
          some (.arr [.arr [.str] .unit .io] .unit .pure) := rfl
      rw [e1] at hel
      rw [e2] at hel'
      obtain rfl := Option.some.inj hel
      obtain rfl := Option.some.inj hel'
      simp [bindParams] at hbody
      obtain ⟨b', hl, hi, -⟩ := hbody.var_inv
      simp [List.lookup] at hl
      subst hl
      simp [Binding.Inst] at hi
      subst hi
      cases hflow with
      | arr _ _ _ _ _ _ _ _ h1 h2 hp he hr =>
        simp [Ty.uncons] at h1 h2
        obtain ⟨rfl, _, _⟩ := h1
        obtain ⟨rfl, _, _⟩ := h2
        cases hp with
        | arr _ _ _ _ _ _ _ _ g1 g2 gp ge gr =>
          simp [Ty.uncons] at g1 g2
          obtain ⟨_, rfl, _⟩ := g1
          obtain ⟨_, rfl, _⟩ := g2
          simp [Eff.le] at ge

/-- A peer join of a pure and an `!{IO}` function into a pure slot, in either branch order
(spec §7 rule 8: "in either spelling"). -/
def joinPure (io? : Bool) : Program :=
  ⟨[.fn "j" [("c", some .bool), ("f", some (.arr [.str] .unit .pure)),
      ("g", some (.arr [.str] .unit .io))] (some (.arr [.str] .unit .pure, .pure))
    (if io? then .ite (.var "c") (.var "g") (.var "f")
      else .ite (.var "c") (.var "f") (.var "g"))]⟩

/-- No upper bound of an `!{IO}` arrow flows into a pure arrow. -/
theorem io_arrow_not_below_pure {A B C D r : Ty}
    (h1 : Flows (.arr [A] B .io) r) (h2 : Flows r (.arr [C] D .pure)) : False := by
  cases h1 with
  | arr _ _ _ _ _ _ ea1 ee1 u1 u2 _ he1 _ =>
    cases h2 with
    | arr _ _ _ _ _ _ ea2 ee2 w1 w2 _ he2 _ =>
      simp [Ty.uncons] at u1 w2
      have h := u2.symm.trans w1
      simp at h
      obtain ⟨_, h3, _⟩ := h
      obtain ⟨_, h4, _⟩ := u1
      obtain ⟨_, h5, _⟩ := w2
      subst h3 h4 h5
      cases ee1 <;> simp_all [Eff.le]

theorem joinPure_rejected (io? : Bool) : ¬ (joinPure io?).Typed := by
  intro h
  obtain ⟨σ, τ, sc, hsk, hnd, ps, r, e, tb, eb, hps, hret, hτ, hbody, hflow, heff⟩ :=
    single_fn_inv h
  cases hps with
  | written x a p prs ps' hel hrest =>
    cases hrest with
    | written x2 a2 p2 prs2 ps2 hel2 hrest2 =>
      cases hrest2 with
      | written x3 a3 p3 prs3 ps3 hel3 hrest3 =>
        cases hrest3
        cases hret with
        | written a' r' e' hel' hn =>
          have e2 : (Ty.arr [.str] .unit .pure).elab σ = some (.arr [.str] .unit .pure) := rfl
          have e3 : (Ty.arr [.str] .unit .io).elab σ = some (.arr [.str] .unit .io) := rfl
          rw [e2] at hel2 hel'
          rw [e3] at hel3
          obtain rfl := Option.some.inj hel2
          obtain rfl := Option.some.inj hel3
          obtain rfl := Option.some.inj hel'
          simp [bindParams] at hbody
          cases io? <;> simp only [Bool.false_eq_true, ↓reduceIte] at hbody
          · obtain ⟨ec, ea, eb', ta, tb', hc, ha, hb, hs, hj, -⟩ := hbody.ite_inv
            obtain ⟨b1, l1, i1, -⟩ := ha.var_inv
            obtain ⟨b2, l2, i2, -⟩ := hb.var_inv
            simp [List.lookup] at l1 l2
            subst l1 l2
            simp [Binding.Inst] at i1 i2
            subst i1 i2
            cases hj with
            | mk _ f2 => exact io_arrow_not_below_pure f2 hflow
          · obtain ⟨ec, ea, eb', ta, tb', hc, ha, hb, hs, hj, -⟩ := hbody.ite_inv
            obtain ⟨b1, l1, i1, -⟩ := ha.var_inv
            obtain ⟨b2, l2, i2, -⟩ := hb.var_inv
            simp [List.lookup] at l1 l2
            subst l1 l2
            simp [Binding.Inst] at i1 i2
            subst i1 i2
            cases hj with
            | mk f1 _ => exact io_arrow_not_below_pure f1 hflow

/-! ### Rules 8 and 11: an `!{IO}` call in a pure body, an `!{IO}` top-level `let` -/

/-- `fn f(g: String -> Unit !{IO}) -> Unit = g("x")`: the callee is a parameter. -/
def r11param : Program :=
  ⟨[.fn "f" [("g", some (.arr [.str] .unit .io))] (some (.unit, .pure))
    (.app (.var "g") [.str "x"])]⟩

theorem r11param_rejected : ¬ r11param.Typed := by
  intro h
  obtain ⟨σ, τ, sc, hsk, hnd, ps, r, e, tb, eb, hps, hret, hτ, hbody, hflow, heff⟩ :=
    single_fn_inv h
  cases hps with
  | written x a p prs ps' hel hrest =>
    cases hrest
    cases hret with
    | written a' r' e' hel' hn =>
      have e1 : (Ty.arr [.str] .unit .io).elab σ = some (.arr [.str] .unit .io) := rfl
      rw [e1] at hel
      obtain rfl := Option.some.inj hel
      simp [bindParams] at hbody
      obtain ⟨tf, ef, ts, ss, ce, εa, hf, -, hpeel, hargs, -, hε⟩ := hbody.app_inv
      obtain ⟨b', hl, hi, rfl⟩ := hf.var_inv
      simp [List.lookup] at hl
      subst hl
      simp [Binding.Inst] at hi
      subst hi
      simp at hpeel
      cases hpeel with
      | exact => simp [Eff.join] at hε; subst hε; simp [Eff.le] at heff
      | rest _ _ _ _ _ hn => simp at hn
      | over _ _ _ _ _ _ _ _ hlt _ => simp at hlt

/-- `fn f() -> Unit = print_str("x")`: the callee is the prelude's `print_str`. -/
def r11print : Program :=
  ⟨[.fn "f" [] (some (.unit, .pure)) (.app (.var "print_str") [.str "x"])]⟩

theorem r11print_rejected : ¬ r11print.Typed := by
  intro h
  obtain ⟨σ, τ, sc, hsk, hnd, ps, r, e, tb, eb, hps, hret, hτ, hbody, hflow, heff⟩ :=
    single_fn_inv h
  cases hps
  cases hret
  simp [bindParams] at hbody
  obtain ⟨tf, ef, ts, ss, ce, εa, hf, -, hpeel, hargs, -, hε⟩ := hbody.app_inv
  obtain ⟨b', hl, hi, rfl⟩ := hf.var_inv
  simp [List.lookup, FnWit.entryIn, Ctx.prelude] at hl
  subst hl
  simp [Binding.Inst, Scheme.Inst, Ty.inst, Ty.instList, mkArr] at hi
  subst hi
  simp at hpeel
  cases hpeel with
  | exact => simp [Eff.join] at hε; subst hε; simp [Eff.le] at heff
  | rest _ _ _ _ _ hn => simp at hn
  | over _ _ _ _ _ _ _ _ hlt _ => simp at hlt

/-- `let boom = print_str("x")`: a top-level `let` must be pure (spec §5.2). -/
def r5let : Program := ⟨[.letD "boom" none (.app (.var "print_str") [.str "x"])]⟩

theorem r5let_rejected : ¬ r5let.Typed := by
  intro h
  obtain ⟨t, sc, hinit, -, -⟩ := single_let_inv h
  generalize hE : Eff.pure = E at hinit
  obtain ⟨tf, ef, ts, ss, ce, εa, hf, -, hpeel, hargs, -, hε⟩ := hinit.app_inv
  obtain ⟨b', hl, hi, rfl⟩ := hf.var_inv
  simp [Ctx.prelude] at hl
  subst hl
  simp [Binding.Inst, Scheme.Inst, Ty.inst, Ty.instList, mkArr] at hi
  subst hi
  simp at hpeel
  cases hpeel with
  | exact => simp [Eff.join] at hε; subst hε; cases hE
  | rest _ _ _ _ _ hn => simp at hn
  | over _ _ _ _ _ _ _ _ hlt _ => simp at hlt

/-! ### Spec §5.6: a local `let` is monomorphic -/

/-- `fn f() -> (Int, Bool) = let id = \x -> x in (id(1), id(true))`. -/
def r56 : Program :=
  ⟨[.fn "f" [] (some (.tup [.int, .bool], .pure))
    (.letIn "id" (.lam "x" none (.var "x"))
      (.tup [.app (.var "id") [.int 1], .app (.var "id") [.bool true]]))]⟩

theorem r56_rejected : ¬ r56.Typed := by
  intro h
  obtain ⟨σ, τ, sc, hsk, hnd, ps, r, e, tb, eb, hps, hret, hτ, hbody, hflow, heff⟩ :=
    single_fn_inv h
  cases hps
  simp [bindParams] at hbody
  obtain ⟨tr, -, hb⟩ := hbody.letIn_inv
  obtain ⟨ts, -, hts⟩ := hb.tup_inv
  cases hts with
  | cons _ e1 es1 t1 ts1 ε1 ε2 h1 hrest =>
    cases hrest with
    | cons _ e2 es2 t2 ts2 ε3 ε4 h2 _ =>
      obtain ⟨tf1, ef1, targs1, ss1, ce1, εa1, hf1, -, hp1, ha1, hfl1, -⟩ := h1.app_inv
      obtain ⟨tf2, ef2, targs2, ss2, ce2, εa2, hf2, -, hp2, ha2, hfl2, -⟩ := h2.app_inv
      obtain ⟨b1, l1, i1, -⟩ := hf1.var_inv
      obtain ⟨b2, l2, i2, -⟩ := hf2.var_inv
      simp [List.lookup] at l1 l2
      subst l1 l2
      simp [Binding.Inst] at i1 i2
      subst i1 i2
      simp at hp1 hp2
      obtain ⟨hss, -, -⟩ := hp1.det hp2
      subst hss
      have hlen : ss1.length = 1 := hp1.length
      obtain ⟨s0, rfl⟩ := List.length_eq_one_iff.mp hlen
      cases ha1 with
      | cons _ _ _ t' ts' _ _ ha1' hrest' =>
        cases ha2 with
        | cons _ _ _ t'' ts'' _ _ ha2' hrest'' =>
          cases ha1' ; cases ha2'
          cases hfl1 with
          | cons _ _ _ _ g1 _ =>
            cases hfl2 with
            | cons _ _ _ _ g2 _ =>
              have := g1.int_inv
              have := g2.bool_inv
              simp_all

/-! ## Accepted programs -/

/-- A call `id(a)` of a one-argument identity binding, at type `t`. -/
theorem idCall {Γ : Ctx} {b : Binding} {a : Expr} {t : Ty} (hb : Γ.lookup "id" = some b)
    (har : ∀ k s, b = .fn k s → k = 1) (hs : b.Inst (.arr [t] t .pure))
    (ha : HasType Γ a t .pure) (hf : Flows t t) : HasType Γ (.app (.var "id") [a]) t .pure :=
  HasType.app _ _ _ (.arr [t] t .pure) .pure [t] [t] t .pure .pure (HasType.var _ _ b _ hb hs)
    (by
      intro x k s hx hl
      injection hx with hx
      subst hx
      rw [hb] at hl
      injection hl with hl
      exact har k s hl)
    (Peel.exact [t] t .pure) (HasExprs.cons _ _ _ _ _ _ _ ha (HasExprs.nil _))
    (FlowsAll.cons _ _ _ _ hf FlowsAll.nil)

/-- `let id = \x -> x` and `let p = (id(1), id(true))`: a top-level `let` generalises, so `id` is
used at two types (spec §7 rule 16). -/
def okLetGen : Program :=
  ⟨[.letD "id" none (.lam "x" none (.var "x")),
    .letD "p" none (.tup [.app (.var "id") [.int 1], .app (.var "id") [.bool true]])]⟩

theorem okLetGen_typed : okLetGen.Typed := by
  refine Program.typed_nomain (hm := by simp [okLetGen])
    (Groups.cons1
      (GroupTy.letG _ "id" none _ (mkArr [.skolem 0] (.skolem 0) .pure)
        ⟨1, .arr [.gen 0] (.gen 0) .pure⟩ ?lam trivial ⟨rfl, rfl, [0], rfl, by decide, rfl⟩)
      (Groups.cons1
        (GroupTy.letG _ "p" none _ (.tup [.int, .bool]) ⟨0, .tup [.int, .bool]⟩
          ?pair trivial ⟨rfl, rfl, [], rfl, by decide, rfl⟩)
        (Groups.nil _)))
  case lam =>
    exact HasType.lam _ "x" none _ (.skolem 0) (.skolem 0) .pure rfl
      (HasType.var _ _ (.local (.skolem 0)) _ rfl rfl)
  case pair =>
    have hs : ∀ t : Ty, t.Solved → mkArr [t] t .pure = .arr [t] t .pure →
        (Binding.val ⟨1, .arr [.gen 0] (.gen 0) .pure⟩).Inst (.arr [t] t .pure) :=
      fun t h1 h2 =>
        ⟨[t], rfl, by simpa using h1, by simpa [Ty.inst, Ty.instList] using h2.symm⟩
    exact HasType.tup _ _ [.int, .bool] .pure
      (HasExprs.cons _ _ _ _ _ _ _
        (idCall (b := .val ⟨1, .arr [.gen 0] (.gen 0) .pure⟩) rfl (by simp) (hs .int rfl rfl)
          (HasType.int _ 1) Flows.int)
        (HasExprs.cons _ _ _ _ _ _ _
          (idCall (b := .val ⟨1, .arr [.gen 0] (.gen 0) .pure⟩) rfl (by simp) (hs .bool rfl rfl)
            (HasType.bool _ true) Flows.bool)
          (HasExprs.nil _)))

/-- `fn id(x) = x` and `fn f() -> (Int, Bool) = (id(1), id(true))`: an omitted annotation is one
type inside the declaration, generalised after it (spec §7 rule 16). -/
def okFnGen : Program :=
  ⟨[.fn "id" [("x", none)] none (.var "x"),
    .fn "f" [] (some (.tup [.int, .bool], .pure))
      (.tup [.app (.var "id") [.int 1], .app (.var "id") [.bool true]])]⟩

/-- The witness for `fn id(x) = x`: `x : !0`, result `!0`, scheme `∀a. a -> a`. -/
def idWit : FnWit :=
  ⟨⟨"id", [("x", none)], none, .var "x"⟩, fun _ => .unit, .arr [.skolem 0] (.skolem 0) .pure,
    ⟨1, .arr [.gen 0] (.gen 0) .pure⟩⟩

theorem okFnGen_typed : okFnGen.Typed := by
  refine Program.typed_nomain (hm := by simp [okFnGen])
    (Groups.cons1
      (GroupTy.fn1_novars (Γ := Ctx.prelude) idWit (by decide)
        ⟨rfl, rfl, [0], rfl, by decide, rfl⟩ ?id)
      (Groups.cons1
        (GroupTy.fn1_novars (Γ := [idWit.entryOut] ++ Ctx.prelude)
          ⟨⟨"f", [], some (.tup [.int, .bool], .pure),
            .tup [.app (.var "id") [.int 1], .app (.var "id") [.bool true]]⟩,
            fun _ => .unit, .arr [] (.tup [.int, .bool]) .pure,
            ⟨0, .arr [] (.tup [.int, .bool]) .pure⟩⟩
          (by decide)
          ⟨rfl, rfl, [], rfl, by decide, rfl⟩ ?f)
        (Groups.nil _)))
  case id =>
    exact ⟨[.skolem 0], .skolem 0, .pure, .skolem 0, .pure,
      ParamsTy.omitted "x" _ _ _ rfl ParamsTy.nil, RetTy.omitted _ rfl, rfl,
      HasType.var _ _ (.local (.skolem 0)) _ rfl rfl, Flows.skolem 0, rfl⟩
  case f =>
    have hs : ∀ t : Ty, t.Solved → mkArr [t] t .pure = .arr [t] t .pure →
        (Binding.fn 1 ⟨1, .arr [.gen 0] (.gen 0) .pure⟩).Inst (.arr [t] t .pure) :=
      fun t h1 h2 =>
        ⟨[t], rfl, by simpa using h1, by simpa [Ty.inst, Ty.instList] using h2.symm⟩
    refine ⟨[], .tup [.int, .bool], .pure, .tup [.int, .bool], .pure, ParamsTy.nil,
      RetTy.written _ _ _ rfl (by simp), rfl, ?_, ?_, rfl⟩
    · exact HasType.tup _ _ [.int, .bool] .pure
        (HasExprs.cons _ _ _ _ _ _ _
          (idCall (b := .fn 1 ⟨1, .arr [.gen 0] (.gen 0) .pure⟩) rfl (by simp) (hs .int rfl rfl)
            (HasType.int _ 1) Flows.int)
          (HasExprs.cons _ _ _ _ _ _ _
            (idCall (b := .fn 1 ⟨1, .arr [.gen 0] (.gen 0) .pure⟩) rfl (by simp)
              (hs .bool rfl rfl)
              (HasType.bool _ true) Flows.bool)
            (HasExprs.nil _)))
    · exact Flows.tup _ _
        (FlowsAll.cons _ _ _ _ Flows.int (FlowsAll.cons _ _ _ _ Flows.bool FlowsAll.nil))

/-- A call `print_str(a)`. -/
theorem printCall {Γ : Ctx} {a : Expr}
    (hb : Γ.lookup "print_str" = some (.fn 1 ⟨0, .arr [.str] .unit .io⟩))
    (ha : HasType Γ a .str .pure) : HasType Γ (.app (.var "print_str") [a]) .unit .io :=
  HasType.app Γ _ [a] (.arr [.str] .unit .io) .pure [.str] [.str] .unit .io .pure
    (HasType.var _ _ _ _ hb ⟨[], rfl, fun u hu => by simp at hu, rfl⟩)
    (by
      intro x k s hx hl
      injection hx with hx
      subst hx
      rw [hb] at hl
      injection hl with hl
      injection hl with hl
      exact hl.symm)
    (Peel.exact [.str] .unit .io) (HasExprs.cons _ _ _ _ _ _ _ ha (HasExprs.nil _))
    (FlowsAll.cons _ _ _ _ Flows.str FlowsAll.nil)

/-- `fn pick(c: Bool, f: String -> Unit, g: String -> Unit !{IO}) -> (String -> Unit !{IO}) =
if c then f else g`, and a `main`: a peer join of a pure and an `!{IO}` function, into an `!{IO}`
slot (spec §7 rule 8). -/
def okJoin : Program :=
  ⟨[.fn "pick" [("c", some .bool), ("f", some (.arr [.str] .unit .pure)),
      ("g", some (.arr [.str] .unit .io))] (some (.arr [.str] .unit .io, .pure))
      (.ite (.var "c") (.var "f") (.var "g")),
    .mainFn (some (.unit, .io)) (.app (.var "print_str") [.str "go"])]⟩

def pickWit : FnWit :=
  ⟨⟨"pick", [("c", some .bool), ("f", some (.arr [.str] .unit .pure)),
      ("g", some (.arr [.str] .unit .io))], some (.arr [.str] .unit .io, .pure),
      .ite (.var "c") (.var "f") (.var "g")⟩, fun _ => .unit,
    mkArr [.bool, .arr [.str] .unit .pure, .arr [.str] .unit .io] (.arr [.str] .unit .io) .pure,
    ⟨0, mkArr [.bool, .arr [.str] .unit .pure, .arr [.str] .unit .io] (.arr [.str] .unit .io)
      .pure⟩⟩

theorem okJoin_typed : okJoin.Typed := by
  refine ⟨[pickWit.entryOut] ++ Ctx.prelude, ?_, ?_⟩
  · have hn : okJoin.nodes = [pickWit.f.toDecl] := rfl
    rw [hn]
    refine Groups.cons1 (GroupTy.fn1_novars (Γ := Ctx.prelude) pickWit (by decide)
      ⟨by decide, by decide, [], rfl, by decide, rfl⟩ ?_) (Groups.nil _)
    refine ⟨[.bool, .arr [.str] .unit .pure, .arr [.str] .unit .io], .arr [.str] .unit .io, .pure,
      .arr [.str] .unit .io, .pure,
      ParamsTy.written _ _ _ _ _ rfl (ParamsTy.written _ _ _ _ _ rfl
        (ParamsTy.written _ _ _ _ _ rfl ParamsTy.nil)),
      RetTy.written _ _ _ rfl (by simp), rfl, ?_,
      Flows.arr _ _ _ _ _ _ _ _ rfl rfl Flows.str rfl Flows.unit, rfl⟩
    have flowFG : Flows (.arr [.str] .unit .pure) (.arr [.str] .unit .io) :=
      Flows.arr _ _ _ _ _ _ _ _ rfl rfl Flows.str rfl Flows.unit
    have flowGG : Flows (.arr [.str] .unit .io) (.arr [.str] .unit .io) :=
      Flows.arr _ _ _ _ _ _ _ _ rfl rfl Flows.str rfl Flows.unit
    exact HasType.ite _ _ _ _ _ _ _ _ _ _
      (HasType.var _ _ (.local .bool) _ rfl rfl) (HasType.var _ _ (.local _) _ rfl rfl)
      (HasType.var _ _ (.local _) _ rfl rfl) rfl (Join.mk _ _ _ flowFG flowGG)
  · intro sg body hm
    simp [okJoin] at hm
    obtain ⟨rfl, rfl⟩ := hm
    refine ⟨.unit, rfl, Or.inl rfl, .unit, .io, ?_, Flows.unit⟩
    exact printCall rfl (HasType.str _ _)

/-- `fn c(k: (String -> Unit !{IO}) -> Unit) -> ((String -> Unit) -> Unit) = k`: a function that
consumes an `!{IO}` arrow stands where one consuming a pure arrow is required (the reverse of
`r8contra`). -/
def okContra : Program :=
  ⟨[.fn "c" [("k", some (.arr [.arr [.str] .unit .io] .unit .pure))]
    (some (.arr [.arr [.str] .unit .pure] .unit .pure, .pure)) (.var "k")]⟩

def contraWit : FnWit :=
  ⟨⟨"c", [("k", some (.arr [.arr [.str] .unit .io] .unit .pure))],
      some (.arr [.arr [.str] .unit .pure] .unit .pure, .pure), .var "k"⟩, fun _ => .unit,
    mkArr [.arr [.arr [.str] .unit .io] .unit .pure] (.arr [.arr [.str] .unit .pure] .unit .pure)
      .pure,
    ⟨0, mkArr [.arr [.arr [.str] .unit .io] .unit .pure]
      (.arr [.arr [.str] .unit .pure] .unit .pure) .pure⟩⟩

theorem okContra_typed : okContra.Typed := by
  refine Program.typed_single_fn contraWit rfl (by simp [okContra]) (by decide)
    ⟨by decide, by decide, [], rfl, by decide, rfl⟩ ?_
  have flowFG : Flows (.arr [.str] .unit .pure) (.arr [.str] .unit .io) :=
    Flows.arr _ _ _ _ _ _ _ _ rfl rfl Flows.str rfl Flows.unit
  refine ⟨[.arr [.arr [.str] .unit .io] .unit .pure], .arr [.arr [.str] .unit .pure] .unit .pure,
    .pure, .arr [.arr [.str] .unit .io] .unit .pure, .pure,
    ParamsTy.written _ _ _ _ _ rfl ParamsTy.nil, RetTy.written _ _ _ rfl (by simp), rfl,
    HasType.var _ _ (.local _) _ rfl rfl,
    Flows.arr _ _ _ _ _ _ _ _ rfl rfl flowFG rfl Flows.unit, rfl⟩

/-- `fn main() -> Unit !{IO} = do let s = "hi"; print_str(s); print_str("bye")`. -/
def okMain : Program :=
  ⟨[.mainFn (some (.unit, .io))
    (.doB [.letS "s" (.str "hi"), .exprS (.app (.var "print_str") [.var "s"])]
      (.app (.var "print_str") [.str "bye"]))]⟩

theorem okMain_typed : okMain.Typed := by
  refine ⟨Ctx.prelude, Groups.nil _, ?_⟩
  intro sg body hm
  simp [okMain] at hm
  obtain ⟨rfl, rfl⟩ := hm
  refine ⟨.unit, rfl, Or.inl rfl, .unit, .io, ?_, Flows.unit⟩
  exact HasType.doB _ (("s", .local .str) :: Ctx.prelude) _ _ _ .io .io
    (HasSteps.letS _ _ _ _ _ _ _ (HasType.str _ _)
      (HasSteps.exprS _ _ _ _ _ _ _
        (printCall rfl (HasType.var _ _ (.local .str) _ rfl rfl)) (HasSteps.nil _)))
    (printCall rfl (HasType.str _ _))

/-- `fn id(x: a) -> a = x` and `fn f() -> (Int, Bool) = (id(1), id(true))`: a written type variable
is rigid inside `id` and free at each call (spec §7 rule 15). -/
def okFnGenW : Program :=
  ⟨[.fn "id" [("x", some (.named "a"))] (some (.named "a", .pure)) (.var "x"),
    .fn "f" [] (some (.tup [.int, .bool], .pure))
      (.tup [.app (.var "id") [.int 1], .app (.var "id") [.bool true]])]⟩

def idWitW : FnWit :=
  ⟨⟨"id", [("x", some (.named "a"))], some (.named "a", .pure), .var "x"⟩, fun _ => .skolem 0,
    .arr [.skolem 0] (.skolem 0) .pure, ⟨1, .arr [.gen 0] (.gen 0) .pure⟩⟩

theorem okFnGenW_typed : okFnGenW.Typed := by
  refine Program.typed_nomain (hm := by simp [okFnGenW])
    (Groups.cons1
      (GroupTy.fn1 (Γ := Ctx.prelude) idWitW (fun v _ => ⟨0, rfl⟩)
        (by have hv : idWitW.f.vars = ["a"] := by decide
            rw [hv]; simp)
        ⟨rfl, rfl, [0], rfl, by decide, rfl⟩ ?id)
      (Groups.cons1
        (GroupTy.fn1_novars (Γ := [idWitW.entryOut] ++ Ctx.prelude)
          ⟨⟨"f", [], some (.tup [.int, .bool], .pure),
            .tup [.app (.var "id") [.int 1], .app (.var "id") [.bool true]]⟩,
            fun _ => .unit, .arr [] (.tup [.int, .bool]) .pure,
            ⟨0, .arr [] (.tup [.int, .bool]) .pure⟩⟩
          (by decide) ⟨rfl, rfl, [], rfl, by decide, rfl⟩ ?f)
        (Groups.nil _)))
  case id =>
    exact ⟨[.skolem 0], .skolem 0, .pure, .skolem 0, .pure,
      ParamsTy.written _ _ _ _ _ rfl ParamsTy.nil, RetTy.written _ _ _ rfl (by simp), rfl,
      HasType.var _ _ (.local (.skolem 0)) _ rfl rfl, Flows.skolem 0, rfl⟩
  case f =>
    have hs : ∀ t : Ty, t.Solved → mkArr [t] t .pure = .arr [t] t .pure →
        (Binding.fn 1 ⟨1, .arr [.gen 0] (.gen 0) .pure⟩).Inst (.arr [t] t .pure) :=
      fun t h1 h2 =>
        ⟨[t], rfl, by simpa using h1, by simpa [Ty.inst, Ty.instList] using h2.symm⟩
    refine ⟨[], .tup [.int, .bool], .pure, .tup [.int, .bool], .pure, ParamsTy.nil,
      RetTy.written _ _ _ rfl (by simp), rfl, ?_, ?_, rfl⟩
    · exact HasType.tup _ _ [.int, .bool] .pure
        (HasExprs.cons _ _ _ _ _ _ _
          (idCall (b := .fn 1 ⟨1, .arr [.gen 0] (.gen 0) .pure⟩) rfl (by simp) (hs .int rfl rfl)
            (HasType.int _ 1) Flows.int)
          (HasExprs.cons _ _ _ _ _ _ _
            (idCall (b := .fn 1 ⟨1, .arr [.gen 0] (.gen 0) .pure⟩) rfl (by simp)
              (hs .bool rfl rfl)
              (HasType.bool _ true) Flows.bool)
            (HasExprs.nil _)))
    · exact Flows.tup _ _
        (FlowsAll.cons _ _ _ _ Flows.int (FlowsAll.cons _ _ _ _ Flows.bool FlowsAll.nil))

/-! ### Spec §5.3: a named `fn` is called with exactly its arity -/

/-- `fn main() -> Unit !{IO} = do print_str(); ()`. The prelude's `print_str` is a named `fn` of
arity 1, so the call is an error twice over: `ArityOk` and `Peel` (zero arguments to an arrow that
takes some). -/
def r5arity : Program :=
  ⟨[.mainFn (some (.unit, .io)) (.doB [.exprS (.app (.var "print_str") [])] .unit)]⟩

theorem r5arity_rejected : ¬ r5arity.Typed := by
  intro h
  obtain ⟨Γ, hg, hm⟩ := h
  have hΓ : Γ = Ctx.prelude := Groups.nil_inv hg
  subst hΓ
  obtain ⟨r, -, -, tb, eb, hb, -⟩ :=
    hm (some (.unit, .io)) (.doB [.exprS (.app (.var "print_str") [])] .unit) (by simp [r5arity])
  obtain ⟨Γ', ε₁, ε₂, hs, -, -⟩ := hb.doB_inv
  cases hs with
  | exprS _ _ _ _ t' _ _ h1 h2 =>
    obtain ⟨tf, ef, ts, ss, ce, εa, hf, har, hpeel, hargs, -, hε1⟩ := h1.app_inv
    have := har "print_str" 1 ⟨0, .arr [.str] .unit .io⟩ rfl rfl
    simp at this

/-- `fn g(h: Int -> Int) -> Int = h()`: a value call with no arguments. Spec §7 rule 9 makes
`() -> T` the only arrow taking none, so the run-time count check of §5.3 cannot pass. The
compiler rejects it ("requires arguments but was called with none"). -/
def r5zero : Program :=
  ⟨[.fn "g" [("h", some (.arr [.int] .int .pure))] (some (.int, .pure)) (.app (.var "h") [])]⟩

theorem r5zero_rejected : ¬ r5zero.Typed := by
  intro h
  obtain ⟨σ, τ, sc, hsk, hnd, ps, r, e, tb, eb, hps, hret, hτ, hbody, hflow, heff⟩ :=
    single_fn_inv h
  cases hps with
  | written x a p prs ps' hel hrest =>
    cases hrest
    have e1 : (Ty.arr [.int] .int .pure).elab σ = some (.arr [.int] .int .pure) := rfl
    rw [e1] at hel
    obtain rfl := Option.some.inj hel
    simp [bindParams] at hbody
    obtain ⟨tf, ef, ts, ss, ce, εa, hf, -, hpeel, hargs, -, hε⟩ := hbody.app_inv
    obtain ⟨b', hl, hi, rfl⟩ := hf.var_inv
    simp [List.lookup] at hl
    subst hl
    simp [Binding.Inst] at hi
    subst hi
    simp at hpeel
    cases hpeel with
    | rest _ _ _ _ hn => simp at hn
    | over _ _ _ _ _ _ _ _ hlt _ => simp at hlt

/-! ### Spec §7 rule 9: `!{IO}` on a function-typed result belongs to its innermost arrow -/

/-- `fn f(x: Int) -> Int -> Int !{IO} = do print_str("a"); \y -> y`: the call `f(1)` itself
performs the `print_str`, but `f`'s own effect is pure, because `!{IO}` sits on the returned arrow.
The compiler rejects it (`f` performs IO but is declared pure). -/
def r9ret : Program :=
  ⟨[.fn "f" [("x", some .int)] (some (.arr [.int] .int .pure, .io))
    (.doB [.exprS (.app (.var "print_str") [.str "a"])] (.lam "y" none (.var "y")))]⟩

theorem r9ret_rejected : ¬ r9ret.Typed := by
  intro h
  obtain ⟨σ, τ, sc, hsk, hnd, ps, r, e, tb, eb, hps, hret, hτ, hbody, hflow, heff⟩ :=
    single_fn_inv h
  cases hps
  cases hret with
  | written a' r' e' hel' hn =>
    have e1 : (Ty.arr [.int] .int .pure).elab σ = some (.arr [.int] .int .pure) := rfl
    rw [e1] at hel'
    obtain rfl := Option.some.inj hel'
    exact hn ⟨rfl, rfl⟩
  | writtenInner a' r0 r' hel' hatt =>
    simp [bindParams] at hbody
    obtain ⟨Γ', ε₁, ε₂, hsteps, -, hε⟩ := hbody.doB_inv
    cases hsteps with
    | exprS _ _ _ _ t' _ _ h1 h2 =>
      cases h2
      obtain ⟨tf, ef, ts, ss, ce, εa, hf, -, hpeel, hargs, -, hε1⟩ := h1.app_inv
      obtain ⟨b', hl, hi, rfl⟩ := hf.var_inv
      simp [List.lookup, FnWit.entryIn, Ctx.prelude] at hl
      subst hl
      simp [Binding.Inst, Scheme.Inst, Ty.inst, Ty.instList, mkArr] at hi
      subst hi
      simp at hpeel
      cases hpeel with
      | exact =>
        simp [Eff.join] at hε1
        subst hε1
        simp [Eff.join] at hε
        subst hε
        simp [Eff.le] at heff
      | rest _ _ _ _ _ hn => simp at hn
      | over _ _ _ _ _ _ _ _ hlt _ => simp at hlt

/-- `fn f(x: Int) -> Int -> Int !{IO} = \y -> y`: the type is `Int -> Int -> Int !{IO}`. The
compiler accepts it; the checker does not (BACKLOG, "The Lean checker rejects
`fn f(x: Int) -> Int -> Int !{IO}`"). -/
def okRetIO : Program :=
  ⟨[.fn "f" [("x", some .int)] (some (.arr [.int] .int .pure, .io)) (.lam "y" none (.var "y"))]⟩

def retIOWit : FnWit :=
  ⟨⟨"f", [("x", some .int)], some (.arr [.int] .int .pure, .io), .lam "y" none (.var "y")⟩,
    fun _ => .unit, mkArr [.int] (.arr [.int] .int .io) .pure,
    ⟨0, mkArr [.int] (.arr [.int] .int .io) .pure⟩⟩

theorem okRetIO_typed : okRetIO.Typed := by
  refine Program.typed_single_fn retIOWit rfl (by simp [okRetIO]) (by decide)
    ⟨by decide, by decide, [], rfl, by decide, rfl⟩ ?_
  refine ⟨[.int], .arr [.int] .int .io, .pure, mkArr [.int] .int .pure, .pure,
    ParamsTy.written _ _ _ _ _ rfl ParamsTy.nil,
    RetTy.writtenInner _ (.arr [.int] .int .pure) _ rfl rfl, rfl,
    HasType.lam _ "y" none _ .int .int .pure rfl (HasType.var _ _ (.local .int) _ rfl rfl),
    Flows.arr _ _ _ _ _ _ _ _ rfl rfl Flows.int rfl Flows.int, rfl⟩

/-! ### Controls: each rejected program above has a near twin the judgment accepts -/

/-- A call of a local at one type: the callee is a `local`, so no arity is declared. -/
theorem localCall {Γ : Ctx} {f : Expr} {x : String} {args : List Expr} {ts ss : List Ty}
    {tf res : Ty} {ce εa : Eff} (hx : f = .var x) (hl : Γ.lookup x = some (.local tf))
    (hp : Peel tf args.length ss res ce) (ha : HasExprs Γ args ts εa) (hf : FlowsAll ts ss) :
    HasType Γ (.app f args) res (Eff.pure.join (ce.join εa)) := by
  subst hx
  refine HasType.app Γ _ args tf .pure ts ss res ce εa (HasType.var _ _ (.local tf) _ hl rfl) ?_
    hp ha hf
  intro y k s hy hk
  injection hy with hy
  subst hy
  rw [hl] at hk
  cases hk

/-- `fn f(k: () -> Int) -> Int = k()`: the twin of `r9c`. -/
def c9 : Program :=
  ⟨[.fn "f" [("k", some (.arr [] .int .pure))] (some (.int, .pure)) (.app (.var "k") [])]⟩

def c9Wit : FnWit :=
  ⟨⟨"f", [("k", some (.arr [] .int .pure))], some (.int, .pure), .app (.var "k") []⟩,
    fun _ => .unit,
    mkArr [.arr [] .int .pure] .int .pure, ⟨0, mkArr [.arr [] .int .pure] .int .pure⟩⟩

theorem c9_typed : c9.Typed := by
  refine Program.typed_single_fn c9Wit rfl (by simp [c9]) (by decide)
    ⟨by decide, by decide, [], rfl, by decide, rfl⟩ ?_
  exact ⟨[.arr [] .int .pure], .int, .pure, .int, .pure,
    ParamsTy.written _ _ _ _ _ rfl ParamsTy.nil, RetTy.written _ _ _ rfl (by simp), rfl,
    localCall (x := "k") rfl rfl (Peel.exact [] .int .pure) (HasExprs.nil _) FlowsAll.nil,
    Flows.int, rfl⟩

/-- `fn f(g: String -> Unit !{IO}) -> Unit !{IO} = g("x")`: the twin of `r11param`. -/
def c11 : Program :=
  ⟨[.fn "f" [("g", some (.arr [.str] .unit .io))] (some (.unit, .io))
    (.app (.var "g") [.str "x"])]⟩

def c11Wit : FnWit :=
  ⟨⟨"f", [("g", some (.arr [.str] .unit .io))], some (.unit, .io),
      .app (.var "g") [.str "x"]⟩,
    fun _ => .unit, mkArr [.arr [.str] .unit .io] .unit .io,
    ⟨0, mkArr [.arr [.str] .unit .io] .unit .io⟩⟩

theorem c11_typed : c11.Typed := by
  refine Program.typed_single_fn c11Wit rfl (by simp [c11]) (by decide)
    ⟨by decide, by decide, [], rfl, by decide, rfl⟩ ?_
  exact ⟨[.arr [.str] .unit .io], .unit, .io, .unit, .io,
    ParamsTy.written _ _ _ _ _ rfl ParamsTy.nil,
    RetTy.written _ _ _ rfl (fun h => by simp [Ty.isArr] at h), rfl,
    localCall (x := "g") rfl rfl (Peel.exact [.str] .unit .io)
      (HasExprs.cons _ _ _ _ _ _ _ (HasType.str _ _) (HasExprs.nil _))
      (FlowsAll.cons _ _ _ _ Flows.str FlowsAll.nil),
    Flows.unit, rfl⟩

/-- `fn f(x) -> Int = x`: an omitted parameter type is an inference request and may be `Int`. The
twin of `r15a`, where the written `a` may not (spec §7 rule 15). -/
def c15 : Program := ⟨[.fn "f" [("x", none)] (some (.int, .pure)) (.var "x")]⟩

def c15Wit : FnWit :=
  ⟨⟨"f", [("x", none)], some (.int, .pure), .var "x"⟩, fun _ => .unit,
    mkArr [.int] .int .pure, ⟨0, mkArr [.int] .int .pure⟩⟩

theorem c15_typed : c15.Typed := by
  refine Program.typed_single_fn c15Wit rfl (by simp [c15]) (by decide)
    ⟨by decide, by decide, [], rfl, by decide, rfl⟩ ?_
  exact ⟨[.int], .int, .pure, .int, .pure, ParamsTy.omitted _ _ _ _ rfl ParamsTy.nil,
    RetTy.written _ _ _ rfl (by simp), rfl, HasType.var _ _ (.local .int) _ rfl rfl, Flows.int,
    rfl⟩

/-- `fn q(p: (String -> Unit) -> Unit, f: String -> Unit) -> Unit = p(f)`: the twin of `r8arg`. -/
def c8 : Program :=
  ⟨[.fn "q" [("p", some (.arr [.arr [.str] .unit .pure] .unit .pure)),
      ("f", some (.arr [.str] .unit .pure))] (some (.unit, .pure)) (.app (.var "p") [.var "f"])]⟩

def c8Wit : FnWit :=
  ⟨⟨"q", [("p", some (.arr [.arr [.str] .unit .pure] .unit .pure)),
      ("f", some (.arr [.str] .unit .pure))], some (.unit, .pure), .app (.var "p") [.var "f"]⟩,
    fun _ => .unit,
    mkArr [.arr [.arr [.str] .unit .pure] .unit .pure, .arr [.str] .unit .pure] .unit .pure,
    ⟨0, mkArr [.arr [.arr [.str] .unit .pure] .unit .pure, .arr [.str] .unit .pure] .unit
      .pure⟩⟩

theorem c8_typed : c8.Typed := by
  refine Program.typed_single_fn c8Wit rfl (by simp [c8]) (by decide)
    ⟨by decide, by decide, [], rfl, by decide, rfl⟩ ?_
  exact ⟨[.arr [.arr [.str] .unit .pure] .unit .pure, .arr [.str] .unit .pure], .unit, .pure,
    .unit,
    .pure, ParamsTy.written _ _ _ _ _ rfl (ParamsTy.written _ _ _ _ _ rfl ParamsTy.nil),
    RetTy.written _ _ _ rfl (by simp), rfl,
    localCall (x := "p") rfl rfl (Peel.exact [.arr [.str] .unit .pure] .unit .pure)
      (HasExprs.cons _ _ _ _ _ _ _ (HasType.var _ _ (.local _) _ rfl rfl) (HasExprs.nil _))
      (FlowsAll.cons _ _ _ _ (Flows.arr _ _ _ _ _ _ _ _ rfl rfl Flows.str rfl Flows.unit)
        FlowsAll.nil),
    Flows.unit, rfl⟩

/-- `fn f() -> (Int, Int) = let id = \x -> x in (id(1), id(2))`: the twin of `r56`. -/
def c56 : Program :=
  ⟨[.fn "f" [] (some (.tup [.int, .int], .pure))
    (.letIn "id" (.lam "x" none (.var "x"))
      (.tup [.app (.var "id") [.int 1], .app (.var "id") [.int 2]]))]⟩

def c56Wit : FnWit :=
  ⟨⟨"f", [], some (.tup [.int, .int], .pure),
    .letIn "id" (.lam "x" none (.var "x"))
      (.tup [.app (.var "id") [.int 1], .app (.var "id") [.int 2]])⟩, fun _ => .unit,
    .arr [] (.tup [.int, .int]) .pure, ⟨0, .arr [] (.tup [.int, .int]) .pure⟩⟩

theorem c56_typed : c56.Typed := by
  refine Program.typed_single_fn c56Wit rfl (by simp [c56]) (by decide)
    ⟨by decide, by decide, [], rfl, by decide, rfl⟩ ?_
  have call : ∀ n : Nat,
      HasType (("id", Binding.local (.arr [.int] .int .pure)) :: [c56Wit.entryIn] ++ Ctx.prelude)
      (.app (.var "id") [.int n]) .int .pure := fun n =>
    localCall (x := "id") rfl rfl (Peel.exact [.int] .int .pure)
      (HasExprs.cons _ _ _ _ _ _ _ (HasType.int _ n) (HasExprs.nil _))
      (FlowsAll.cons _ _ _ _ Flows.int FlowsAll.nil)
  refine ⟨[], .tup [.int, .int], .pure, .tup [.int, .int], .pure, ParamsTy.nil,
    RetTy.written _ _ _ rfl (by simp), rfl, ?_, ?_, rfl⟩
  · exact HasType.letIn _ _ _ _ (.arr [.int] .int .pure) _ _
      (HasType.lam _ "x" none _ .int .int .pure rfl (HasType.var _ _ (.local .int) _ rfl rfl))
      (HasType.tup _ _ [.int, .int] .pure
        (HasExprs.cons _ _ _ _ _ _ _ (call 1)
          (HasExprs.cons _ _ _ _ _ _ _ (call 2) (HasExprs.nil _))))
  · exact Flows.tup _ _
      (FlowsAll.cons _ _ _ _ Flows.int (FlowsAll.cons _ _ _ _ Flows.int FlowsAll.nil))

/-- `let shout = \s -> print_str(s)`: binding a function that performs IO is pure; the effect
stays on its arrow (spec §5.2). The twin of `r5let`. -/
def c5 : Program :=
  ⟨[.letD "shout" none (.lam "s" none (.app (.var "print_str") [.var "s"]))]⟩

theorem c5_typed : c5.Typed := by
  refine Program.typed_nomain (hm := by simp [c5])
    (Groups.cons1
      (GroupTy.letG _ "shout" none _ (mkArr [.str] .unit .io) ⟨0, mkArr [.str] .unit .io⟩ ?lam
        trivial
        ⟨rfl, rfl, [], rfl, by decide, rfl⟩)
      (Groups.nil _))
  exact HasType.lam _ "s" none _ .str .unit .io rfl
    (printCall rfl (HasType.var _ _ (.local .str) _ rfl rfl))

/-! ### The checker agrees on every program above, except one -/

/-- `let f : a -> a !{IO} = \(h: Int -> Int) -> h` and a unit `main`. The initialiser forces the
annotation's variable `a` to `Int -> Int`, so the written `a -> a !{IO}` reads as an `!{IO}` arrow
whose result is an arrow. `Ty.elab` refuses an `!{IO}` arrow whose WRITTEN result is an arrow, not
one that a variable is solved to (spec §7 rule 9 is a rule about spelling). The checker and the
compiler accept it, and the judgment once did not: the regression test for that change. -/
def okLetAnnIO : Program :=
  ⟨[.letD "f" (some (.arr [.named "a"] (.named "a") .io))
      (.lam "h" (some (.arr [.int] .int .pure)) (.var "h")),
    .mainFn (some (.unit, .io)) .unit]⟩

theorem okLetAnnIO_typed : okLetAnnIO.Typed := by
  have hA : Flows (.arr [.int] .int .pure) (.arr [.int] .int .pure) :=
    Flows.arr _ _ _ _ _ _ _ _ rfl rfl Flows.int rfl Flows.int
  refine ⟨("f", .val ⟨0, .arr [.arr [.int] .int .pure, .int] .int .pure⟩) :: Ctx.prelude, ?_, ?_⟩
  · refine Groups.cons1
      (GroupTy.letG _ "f" _ _ (.arr [.arr [.int] .int .pure, .int] .int .pure)
        ⟨0, .arr [.arr [.int] .int .pure, .int] .int .pure⟩ ?lam ?ann
        ⟨rfl, rfl, [], rfl, by decide, rfl⟩)
      (Groups.nil _)
    case lam =>
      exact HasType.lam _ "h" _ _ (.arr [.int] .int .pure) (.arr [.int] .int .pure) .pure
        ⟨rfl, rfl⟩ (HasType.var _ _ (.local _) _ rfl rfl)
    case ann =>
      refine ⟨fun _ => .arr [.int] .int .pure,
        .arr [.arr [.int] .int .pure] (.arr [.int] .int .pure) .io, fun _ => rfl, rfl, ?_⟩
      exact Flows.arr _ _ _ _ _ _ _ _ rfl rfl hA rfl hA
  · intro sg body hm
    simp [okLetAnnIO] at hm
    obtain ⟨rfl, rfl⟩ := hm
    exact ⟨.unit, rfl, Or.inl rfl, .unit, .pure, HasType.unit _, Flows.unit⟩

section agree

open SproutCore.Tests

#guard rejects r15a
#guard rejects r15b
#guard rejects r15c
#guard rejects r9a
#guard rejects r9b
#guard rejects r9c
#guard rejects r8arg
#guard rejects r8ret
#guard rejects r8contra
#guard rejects (joinPure true)
#guard rejects (joinPure false)
#guard rejects r11param
#guard rejects r11print
#guard rejects r5let
#guard rejects r56

#guard accepts okLetGen
#guard accepts okLetAnnIO
#guard accepts okFnGen
#guard accepts okFnGenW
#guard accepts okJoin
#guard accepts okContra
#guard accepts okMain
#guard accepts c9
#guard accepts c11
#guard accepts c8
#guard accepts c56
#guard accepts c5
#guard accepts c15
#guard rejects r9ret

#guard rejects r5arity
#guard rejects r5zero

-- The one divergence. The judgment accepts `-> Int -> Int !{IO}` (spec §7 rule 9, and the
-- compiler), `Check.FnInfo.build` rejects it as ill-formed (BACKLOG).
#guard rejects okRetIO

end agree

end SproutCore.Judgment
