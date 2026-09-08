# TUI M4 — the widget set, slice C1: containers and static content

> Status: **implemented** (C1). Non-normative; `docs/spec-v0.md` is
> unaffected. Covers `stdlib/tui/widgets/` — the widget *set*. The widget
> *model* (the box, layout, the pump) is `docs/tui-widgets-v0.md`, and addressed
> delivery is `docs/tui-routing-v0.md`.

## 1. Problem

`stdlib/tui` ships no widget. M0–M3 built the model — `View`, `layout.solve`,
the pump — and M4's routing settled the last `View` field, but an application
still writes every widget it uses, containers included.

`examples/tui_dashboard.sprout` is the measurement. Of its 284 lines, 93 across
15 helpers (`:166-258`, or `:161-258` with the section header) are a `Box`
container, and a further 15 (`:145-159`) are a `label`.
The container implements four traversals that belong to the framework: broadcast
an event to children and concatenate their messages and commands; walk a
`Delivery` down and stop at the first child that claims it; solve the region list;
render children against it pairwise. The delivery walk is now written twice
in-tree, identically — the example and `tests/stdlib/test_tui_route.spr`.

There is a second, quieter problem. **`View.measure` has exactly one caller in
the whole tree, and it is a unit test** (`test_tui_widget.spr:84`). Nothing in any
application or layout path consults it. Symmetrically, `layout.Auto` — the
dimension that carries a widget's own measurement — is constructed nowhere
outside `solve`'s unit tests, always from a literal. M3 built both halves of
content-driven sizing and never joined them, because the thing that joins them is
a container, and there is no container. C1 is where they meet.

## 2. Goals and non-goals

Goals: a container that any application can use instead of writing one; the
first-claimant rule enforced by the framework rather than re-implemented per
application; content-driven sizing reachable *and correct under nesting*;
`tui_dashboard` reduced to the application it is meant to demonstrate.

In scope, and not in the first draft: **one breaking change to `View.measure`**
(§4.3). It is here because the alternative is shipping a sizing rule that renders
a sibling widget into nothing, and because the contract is four constructions old.

Non-goals for C1: focus, and therefore every interactive widget (`button`,
`input`, `list_view`) — that is C2, designed in `docs/tui-focus-v0.md`. It could
not start before the two routing corrections it is written against had landed;
they did, on 2026-09-08, as `widget.route_when` and `widget.namespaced`
(`docs/tui-routing-v0.md` §3.8–3.9). C2a (`ToFocus`, `focus_ring`, `button`)
landed the same day; `input` and `list_view` are C2b. Scrolling, borders, tabs,
tables and `text_area` are C3.
Reflowing text is deferred with a reason (§4.6). No new builtin: the
`vector_remove` question belongs to C3's `text_area`, not here.

## 3. Prior art

Every row verified against the library's own reference, not secondary sources.

| | widget holds state? | who declares a child's size | container is a widget? |
|---|---|---|---|
| **Textual** | yes | the child (CSS), resolved by the parent's layout | yes — `Vertical`, `Horizontal`, `Grid`, plus `Center`/`Middle`/`Right` and `*Scroll` variants |
| **ratatui** | no — `fn render(self, area: Rect, buf: &mut Buffer)` consumes the widget each frame; persistence is the separate `StatefulWidget` | the parent, via `Layout` constraints | no — layout splits a `Rect`, widgets are drawn into the pieces |
| **brick** | no — `data Widget n = Widget { hSize :: Size, vSize :: Size, render :: RenderM n (Result n) }`; state lives in the application | the child, coarsely: `data Size = Fixed \| Greedy`, with `hLimit`/`vLimit` to constrain a greedy one | composition is by combinator (`hBox`/`vBox`), not by a container object |
| **Bubble Tea** | yes — `Update(tea.Msg) (tea.Model, tea.Cmd)` returns a new model | n/a — no layout engine in core | not established (see below) |

The Bubble Tea row is deliberately short. Its `Model` interface —
`Init() tea.Cmd`, `Update(tea.Msg) (tea.Model, tea.Cmd)`, `View() tea.View` — is
verified, and components ship in a separate `bubbles` library; but the reference
read for this table does not describe parent-child composition either way, so
"no container type" would be an inference, not a finding. Nothing in this design
rests on that row.

Two things follow for Sprout.

**Sprout keeps the Textual/ratatui state and layout shape.** A widget holds its
own state behind an existential and the parent declares each child's size with a
`layout.Dimension`. That was settled in M3 and C1 does not revisit it.

**But nobody makes sizing purely one-sided, and C1 ends up a hybrid.** brick lets
the child say `Fixed` or `Greedy`; ratatui and Textual let the parent decide but
feed the child's content back in (`Constraint::Length` computed from content, CSS
`auto`). Sprout has the parent-declared half already; the child's half is
`measure`, and it was connected to nothing (§1).

C1 adds *two* pieces of vocabulary, not one, and the second was missing from the
first draft of this design. `fit` (§4.2) is the slot that says "size this from
the child". `Grow` (§4.3) is the child's answer to a question `Size` cannot
express — *whether* it wants a size at all, or whatever is left. brick puts that
on the widget as a field separate from any measurement, and §4.3 adopts that
directly. The first draft tried to infer it in the parent instead and got a
layout that renders a sibling into nothing; the inference was available, a
channel to report it through was not.

## 4. Decisions

### 4.1 A container is a widget, generic in the message type

`fn column(kids: List (Slot m)) -> Widget m` — universal in `m`, so one container
serves every application, with its children's states hidden from it and from each
other.

This packs an existential inside a polymorphic function *and* gives the box a
hidden state that mentions the very variable it is universal in (`s = Container m`).
Nothing in the tree did that: the dashboard's `Box` is monomorphic in its `Msg`,
and `widget.map_msgs` packs polymorphically but reuses the incoming skolem.
**Spiked before adopting, for a tuple-shaped slot** — see §11 for what that does
and does not establish.

### 4.2 A slot is a child and its size in one value, and it is opaque

```sprout
export type Slot m          # constructors NOT exported

export fn cells(n: Int, child: Widget m) -> Slot m     # exactly n
export fn fraction(n: Int, child: Widget m) -> Slot m  # weight n of what is left
export fn fit(child: Widget m) -> Slot m               # the child's measurement
```

*Two parallel lists cannot desync.* The dashboard carries `dims` and `kids`
separately; a mismatch silently drops a child or leaves a gap, and nothing
reports it. A slot makes the pairing unrepresentable-if-wrong (guidelines §3).

*Opaque, because the obvious spelling leaks a junk state.* Exporting
`Sized layout.Dimension (Widget m) | Fit (Widget m)` embeds all of `Dimension`,
and `layout.Auto` is a public constructor — so `Sized(layout.Auto(5), w)` stays
writable. It means "`Cells 5`, but at a lower shrink priority", which no author
intends and which ignores the child's `measure` entirely: four representable
states for three meanings. Constructor functions give exactly three
(parse-don't-validate, guidelines §4). They also keep `layout` out of application
imports, and let `fit_max` (§10) arrive additively rather than as a fourth
constructor every user match has to grow a case for.

`grid` takes `Dimension` templates rather than slots: a grid cell's size comes
from the row and column templates, not from the child in it, so `fit` has no
meaning there. The two templates are tagged `Cols`/`Rows` (guidelines §7) —
they are the same type, sit side by side, and a swap silently transposes the
grid rather than failing. Verified the tag bites: passing them the other way
round is `Call type mismatch: … Cols vs Rows`.

### 4.3 `measure` reports how a widget grows, not just how big it is

**This changes the `View` contract**, which is why it is a decision and not an
implementation note:

```sprout
export type Grow (..) deriving (Eq, ToString) = | Fixed | Greedy

export type Measured = (size: Size, cols: Grow, rows: Grow)

# was: measure: s -> Size -> Size
measure: s -> Size -> Measured
```

The first draft of this design had no `Grow`. It said a container holding any
`Fraction` child is "greedy" and reports `avail` from `measure`. **That is
wrong, and wrong in the composition it was written for.** The parent turns a
`fit` slot into `layout.Auto(measured)`, and `solve` pays `Auto` *before*
fractions (`layout.sprout:44-55`), so reporting `avail` produces a maximal
high-priority demand — the opposite of yielding. Run against the real solver:

```
solve(24, [Auto(24), Fraction(1)])    -> [24, 0]     a fit panel starves its sibling
solve(24, [Auto(30), Fraction(1)])    -> [24, 0]     so does an over-tall static
solve(24, [Fraction(1), Fraction(1)]) -> [12, 12]    fractions share correctly
```

The sibling renders into a zero-row region with no error and no diagnostic. The
second line matters as much as the first: with reflow deferred (§4.6), content
taller than its region is ordinary, not exotic.

The category error is that **"greedy" and "large fixed ask" are opposites, and a
`Size` can only express the second.** The vocabulary already has a word for
"whatever is left" — `Fraction` — and no way for a child to ask for it. `Grow` is
that channel. A container resolving a `fit` slot now emits
`layout.Fraction(1)` for a child greedy along the axis and
`layout.Auto(measured)` for a fixed one.

A container's own `Measured` follows from its slots: size is the sum of the
children's asks along the axis and the largest across it, clamped to `avail`;
`Grow` is `Greedy` along the axis if any slot is a `fraction` or any `fit` child
is itself greedy, and `Fixed` otherwise. Each child is measured against the
container's full `avail` — measuring against a running remainder would make a
child's reported size depend on its position among its siblings.

This is brick's `data Size = Fixed | Greedy` (§3), adopted rather than
re-derived.

Note precisely what changed, because the paragraph above still infers a
container's `Grow` from its own slots: **the inference was never the problem.**
The first draft computed the same fact correctly and then had nowhere to put it —
`measure` returns a `Size`, every number in that vocabulary reads as a demand, so
"I yield" had to be encoded as "I demand everything". `Grow` does not replace the
inference; it gives its result somewhere to go, and lets a *leaf* widget report
the same thing without inventing a slot to infer from.

*Why change the contract now.* §1's argument for writing the widget set after
routing was that every `View` construction breaks on a field change, so the
contract must settle first. The same argument applies with the sign flipped:
there are four `View` constructions in the tree today and there will be twenty
after C2 and C3. If `measure` is wrong, it is wrong at its cheapest right now.

### 4.4 A container is not addressable, and the routing semantics move verbatim

`on_event` broadcasts to every child in order, concatenating what each said and
asked for. `route` tries each child in order and stops at the first claimant,
answering `Nothing` when none claimed — which lets an unclaimed answer reach
`update`, the fallback `docs/tui-routing-v0.md` §3.4 guarantees.

The container itself claims nothing and holds no id. Addressable containers, and
the id-namespacing that makes two copies of a widget distinguishable, are C2's
(`BACKLOG.md` §4, the ids-become-addressable entry). C1 must not change routing
behaviour: it moves the
dashboard's walk into stdlib unchanged, so the existing route tests keep their
meaning and any behaviour change in C2 is visible as a C2 diff.

Stopping at the first claimant is what makes a duplicated id resolve by tree
order instead of delivering one answer twice. Today that rule is a convention
each application re-implements; shipping the container is what makes it uniform.

### 4.5 Clipping is the framework's job, on both axes

`screen_write` stops at the *screen* edge, not at the region's — so a widget that
paints a string wider than its region overwrites its neighbour's cells, and the
frame diff shows no error. Every widget author therefore needs the same clipped
write, and `tui_dashboard` already hand-rolls it (`:40-44`).

`stdlib/tui/widgets/paint.sprout` exports it once. It clips on **both** axes: the
example's helper clips columns but not rows, so a row index past the region's
height paints outside it. That is a latent bug in the example, fixed by adopting
the shared helper rather than by patching the copy.

### 4.6 `label` and `static` do not reflow

`label(text)` is one line; `static(lines)` is a list of lines already split.
Neither wraps, though `text.wrap_to` exists.

Wrapping makes measured height a function of width. `measure` receives `avail`,
so a wrapping widget *can* measure — but the width it is finally rendered at is
the width the solver returned, which for a child in a `row` is not the width it
measured against. The result is a widget whose reported height is right in a
`column` and quietly wrong in a `row`. A reflowing `paragraph` belongs in C3
with a stated rule for which case it supports; until then wrapping stays
explicit at the call site (`static(text.wrap_to(t, cols))`), where the author
knows the width.

Style is a separate constructor rather than a parameter, since Sprout has no
default arguments: `label` / `label_styled`, `static` / `static_styled`.

## 5. Surface

```sprout
module stdlib.tui.widget          # CHANGED — see §4.3

export type Grow (..) deriving (Eq, ToString) = | Fixed | Greedy
export type Measured = (size: geometry.Size, cols: Grow, rows: Grow)

export type View s m = ( … , measure: s -> geometry.Size -> Measured )

# The two leaf spellings, so a widget with nothing to say about growth says it
# once. `greedy_size` was not in the first draft; the dashboard's log pane
# needed it, and under the old contract it said the same thing by returning
# `avail` — the spelling §4.3 removes.
export fn fixed_size(sz: geometry.Size) -> Measured
export fn greedy_size(sz: geometry.Size) -> Measured
```

```sprout
module stdlib.tui.widgets.container

export type Slot m                # opaque; §4.2

export fn cells(n: Int, child: Widget m) -> Slot m
export fn fraction(n: Int, child: Widget m) -> Slot m
export fn fit(child: Widget m) -> Slot m

# A grid's two templates are both `List layout.Dimension` and adjacent, so a
# swap silently transposes the layout. Tagged, per guidelines §7.
export wrap Cols = List layout.Dimension
export wrap Rows = List layout.Dimension

export fn row(slots: List (Slot m)) -> Widget m
export fn column(slots: List (Slot m)) -> Widget m
export fn grid(cols: Cols, rows: Rows, kids: List (Widget m)) -> Widget m
```

```sprout
module stdlib.tui.widgets.children

# The four traversals, over a bare child list, so every future child-holding
# widget reuses them instead of writing a fifth copy.
export fn broadcast(evt: Event,
                    kids: List (Widget m)) -> (List (Widget m), List m, List (Cmd m))
export fn deliver_first(target: WidgetId, d: Delivery m,
                        kids: List (Widget m)) -> Maybe (List (Widget m), List m, List (Cmd m))
export fn render_zip(regions: List Region, screen: Screen,
                     kids: List (Widget m)) -> Unit !{IO}
export fn measure_all(avail: Size, kids: List (Widget m)) -> List Measured
```

```sprout
module stdlib.tui.widgets.text

export fn label(t: String) -> Widget m
export fn label_styled(st: style.Style, t: String) -> Widget m
export fn static(ls: List String) -> Widget m
export fn static_styled(st: style.Style, ls: List String) -> Widget m

# Paints nothing, measures zero, declines everything. Needed the moment anyone
# writes the three-slot centring idiom in §10, which has to pad with something.
export fn spacer() -> Widget m
```

`static` is a legal name: `lexer.is_keyword` lists twenty words and that is not
one of them. The name follows Textual, where `Static` is "a widget to display
simple static content" and `Label` is derived from it.

```sprout
module stdlib.tui.widgets.paint

# Clipped on both axes: a widget never paints outside the region it was handed.
export fn line(screen: Screen, region: Region, row: Int, content: String,
               style: Style) -> Unit !{IO}
export fn lines(screen: Screen, region: Region, from_row: Int,
                content: List String, style: Style) -> Unit !{IO}
```

Receiver-first rather than data-last, which is a deviation from guidelines §6
and the only one in this change. These two wrap `screen.screen_write` and are
called beside it in every `render`; neither is ever `|>`-chained, since both
return `Unit`. Matching the module they wrap reads better than matching the
convention.

Grid children fill cells row-major. A child with no cell is rendered into an
empty region rather than dropped, so `geometry.is_empty` remains the single
predicate for "nothing to paint" and the child still receives events and
deliveries — being off-screen is not the same as being gone.

## 6. Modules

Four new files under `stdlib/tui/widgets/` — `container`, `children`, `text`,
`paint` — and **one changed M3 module**, `stdlib/tui/widget.sprout`, for the
`measure` contract (§4.3). The first draft claimed C1 only adds; the `Grow`
decision gives that up deliberately, and §8 carries the migration it costs.

These are the tree's first four-segment module names
(`stdlib.tui.widgets.container`). `module_loader.replace_dots_with_slash` maps
every dot to a slash with no depth limit, so no loader change is needed —
verified by reading `stdlib/compiler/module_loader.sprout:170-185`, and confirmed
by the spike compiling. Nothing deeper than three segments exists today, so the
first C1 commit is also the first exercise of that path.

## 7. Syntax, type-system and error-message impact

None on any of the three. C1 is stdlib written in existing language surface: no
new syntax, no inference change, no new diagnostic. `View.measure`'s new return
type (§4.3) is a stdlib signature change, not a type-system one.

The one type-system question it leans on — an existential packed inside a
polymorphic function whose hidden state mentions the outer variable — is
demonstrated rather than assumed, though only for a tuple so far; §11 says what
that leaves open and §9 closes it.

## 8. Compatibility and migration

**Breaking, once, at its cheapest point.** `View.measure` changes return type
(§4.3), so every `View` construction in the tree must be touched:

| site | change |
|---|---|
| `examples/tui_dashboard.sprout` | two surviving `measure` fns — `fixed_size` for the clock, `greedy_size` for the log; the other two went with the hand-written `Box` and `label` |
| `tests/stdlib/test_tui_widget.spr` | two constructions plus the `measure` assertion |
| `tests/stdlib/test_tui_route.spr` | leaf and container constructions |
| `tests/stdlib/test_tui_cmd.spr` | one construction plus a `measure` reader |
| `tests/stdlib/test_tui_app.spr` | one construction plus a `measure` reader |
| `tests/conformance/run/tui_widgets.spr` | three constructions plus two `measure` readers |
| `tests/tui_smoke/resize_probe.spr` | one construction |
| `docs/tui-widgets-v0.md` §3.1 | the `View` record it prints |

**Sweep a breaking signature change by symbol, not by directory.** `View`
constructions live in four trees — `examples/`, `tests/stdlib/`,
`tests/conformance/run/`, `tests/tui_smoke/` — and scoping to the first two put
this table at three, then five. The seven are one repo-wide grep for
`widget.View(`/`View(state`, minus three hits
(`examples/existential_widget.sprout`, `tests/stdlib/test_parametric_records.spr`,
`tests/stdlib/test_existential_cross_module.spr`) that declare their own
`WidgetView` with no `measure` field.

`widget.greedy_size` was added alongside `fixed_size` for the reason the
dashboard needed it — a log pane genuinely does yield, and under the old
contract it said so by returning `avail`, the exact spelling §4.3 removes.

`widget.fixed_size` exists so the common leaf case is one call rather than a
record literal, keeping the diff mechanical. Nothing outside `stdlib/tui` and its
tests constructs a `View`, and `uncharted-suns` — the only downstream consumer of
this repo — contains no reference to `stdlib.tui` at all (grepped 2026-09-07), so
downstream impact is nil.

`examples/tui_dashboard.sprout` is rewritten onto the new modules, and that is
the acceptance test: its `Box` section (`:161-258`) and its `label` (`:145-159`)
disappear — 113 lines traded for two imports and the call sites that replace
them. If the example does not lose that section, C1 has not delivered the
abstraction.

## 9. Tests

- `tests/stdlib/test_tui_container.spr` — new. Region splitting for `row`,
  `column` and `grid`; broadcast reaches every child and concatenates in order;
  the delivery walk claims at the first child and answers `Nothing` when none
  claims; a duplicated id resolves by tree order; `fit` resolves through
  `widget.measure`; a `fit` container of `fit` slots measures as the sum and
  reports `Fixed`.
- **The starvation regression** (§4.3), written first and confirmed failing
  against the pre-`Grow` rule: `column([fit(panel), fraction(1, main)])` where
  `panel` holds a `fraction`, asserted to give `main` a non-empty region. The
  same for a `static` taller than the region. These are the two cases the first
  draft got wrong, so they are the two that pin the fix.
- **The prism over a container.** `map_msgs` (`widget.sprout:192-198`) applied to
  a stdlib `column`: a delivery reaches a child through it and a command is
  retargeted on the way back. Genericity in `m` exists precisely so a container
  can be embedded across vocabularies, and nothing in-tree exercises a prism
  wrapping a repacking generic container — the existing widget tests are all
  monomorphic.
- **The spike's shape, on the real type.** §11's evidence used a tuple; `Slot`
  is an opaque generic ADT holding the existential in a constructor payload, a
  different lowering path. No separate test was needed in the end — every
  assertion in `test_tui_container.spr` runs through `ct.cells`/`fraction`/`fit`,
  so the real shape is exercised twenty-seven times over.
- `tests/stdlib/test_tui_text.spr` — extend, or a new `test_tui_widgets_text.spr`:
  `label` and `static` measure their content; both clip to a region narrower and
  shorter than the content, asserted by reading cells back with `screen.text_at`
  rather than by inspecting the widget.
- `tests/stdlib/test_tui_route.spr` — **kept its own hand-written container**,
  against the plan above. Replacing it would have made the routing suite depend
  on the container it is meant to be independent evidence for; what it exercises
  is `widget.deliver` and the `Maybe` decline directly, which nothing else does.
  The verbatim-move claim (§4.4) is carried instead by the walk's own assertions
  in `test_tui_container.spr` — first claimant, decline, duplicate-id order.
- Golden IR: adding a container to `examples/` does not add a corpus file, but
  rewriting `tui_dashboard` changes its golden. Per AGENTS.md #12 the diff is
  read before regenerating.

## 10. Deferred, to be filed in `BACKLOG.md` §4 with the change

- **`fit_max(n, w)` — fit to content, but at most `n`.** Every library in §3 has
  it (brick `vLimit`, ratatui `Min`/`Max`, Textual `max-height`) and C1 cannot
  say it: `fit` takes no bound and `cells(n, w)` ignores the measurement. A
  bounded log pane wants it immediately. Deferred only because §4.2's opaque
  `Slot` makes it a pure addition — one constructor function, no user match to
  grow. M3 deferred `Min`/`Max` in the *solver* for a related reason
  (`docs/tui-widgets-v0.md` §7.2); this is the slot-level half.
- A reflowing `paragraph` widget, with the measured-width rule §4.6 defers.
- Alignment containers (Textual's `Center`/`Middle`/`Right`) — expressible with
  `spacer()` as `row([fraction(1, spacer()), fit(w), fraction(1, spacer())])`, so
  a widget for it is convenience, not capability.
- A grid child with no cell renders into an empty region; whether that should
  instead be a construction-time error depends on whether an application ever
  wants a deliberately over-full grid.

**Committed now, so C2 does not have to break C1's signatures.** `row`/`column`/
`grid` take no id and no options, and Sprout has no default arguments — so C2's
addressable containers and focus config must not arrive as parameters on these
functions, or the surface goes combinatorial (`row`/`row_with_id`/…, times
styled variants). The committed spelling is a **wrapper widget**:
`addressable(id, w) -> Widget m`, which claims deliveries for `id` and forwards
everything else. It composes with any widget, not just containers, and it leaves
every C1 signature untouched.

## 11. Verification

The one genuinely new shape (§4.1) was spiked before this document was written:
a container generic in `m`, holding `List (Sizing, Widget m)`, packing the
existential, with `Fit` resolved by calling `widget.measure`. It typechecks,
emits IR, links and runs — `column` inferring as
`forall a. List (Sizing, Widget a) -> Widget a`, and a three-slot column in a
20×10 region splitting to rows `[1, 8, 1]`: the fixed slot took 1, the
content-sized slot resolved through `measure` to 1, and the fraction took the
remaining 8.

**What the spike does not establish.** It used `List (Sizing, Widget m)` — a
tuple. `Slot` is an opaque generic ADT carrying the existential in a constructor
payload, a different lowering path, and calling that "the same shape" would be an
assumption. §9 makes re-running it on the real type the first C1 test rather than
a claim made here.

It also predates §4.3: the spike's container measured greedily, which is the rule
this revision removes. Its `[1, 8, 1]` result stands — that column had no nested
fraction, which is exactly the case the old rule got right.

The starvation itself was confirmed by running `layout.solve` directly, not by
reading it; the four numbers in §4.3 are that run's output.
