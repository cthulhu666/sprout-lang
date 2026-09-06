# TUI M3 — widget model, layout, app loop

**Status:** PARTIAL. The pure half (`stdlib/tui/text.sprout`, `stdlib/tui/layout.sprout`,
the `geometry` additions) is implemented; the widget model and the app loop follow in a
second change. Builds on `docs/tui-core-v0.md` (M2), which owns `geometry`, `style`,
`event`, `keys` and `screen`.

## 1. Problem

M2 delivered a frame: a `Region`, a `Style`, a decoded `Event`, and a double-buffered
`Screen` that emits minimal ANSI. What it has no answer for is **who writes into which
part of the screen, and what happens when the terminal is resized**.

Three specific gaps:

1. **No composition.** `screen_write` takes absolute `(col, row)`. Two panes side by side
   means every widget computing absolute coordinates by hand, and a resize means
   recomputing all of them at every call site.
2. **No heterogeneous widget list.** A text input and a file tree hold different state
   types. Sprout can express this (`docs/gadts-v0.md` §5.1) but nothing in the tree does.
3. **`screen_write` stops at the right edge and does not wrap** — deliberately, because
   M2 had no `Region` to wrap *within* (`docs/tui-core-v0.md` §7, `BACKLOG.md` §4). The
   layout engine supplies one, so wrapping lands here.

## 2. Goals and non-goals

**Goals.** A widget model where per-widget state is private and the message vocabulary is
shared. A layout engine that turns one `Region` into many, exactly, with no lost cell. Text
wrapping and measurement in terminal *columns*. An event loop that multiplexes input,
timers and worker tasks.

**Non-goals.** TCSS stylesheets (M9 — nothing to style until layout is proven). A widget
library (M4). Scrolling viewports beyond the `Region` arithmetic that supports them. Any
constraint solver.

## 3. Decisions

### 3.1 The widget box hides its state and exposes its message type

```sprout
type View s m = (
  state:    s,
  on_event: s -> Event -> (s, List m),
  render:   s -> Region -> Screen -> Unit !{IO},
  measure:  s -> Size -> Size
)

type Widget m = | exists s. Widget (View s m)
```

The two type variables play **opposite** roles, and that is the whole design:

- `s` is *existential*. A text input (`s = EditorState`) and a tree (`s = TreeState`) sit in
  one `List (Widget AppMsg)` with neither able to see or unify the other's state.
- `m` is *universal*. Every widget in an application must agree on the vocabulary it can
  speak back in, so the app declares `type AppMsg = | Quit | FileOpened String` and the
  pump is typed end to end.

A single stdlib-owned `Message` type — the shape the milestone was originally sketched with
— cannot work: stdlib cannot know an application's messages, so `Message` would have to
degrade to `String` or a `MsgString | MsgInt | …` grab bag with the app parsing its own
messages back out. That discards the exhaustiveness check which is the main reason to have
a sum type at all.

`exists` rather than `any C`, because the hidden variable spans four fields and `any C` is
single-field sugar (`docs/gadts-v0.md` §5.1).

**Verified before designing on it.** `BACKLOG.md` flagged the cross-module case as untested
and `docs/gadts-v0.md` §6 named a widget layer as the enabler this was built for, so it was
probed first rather than assumed: a box declared in an imported module, constructed and
unpacked in the importer at two hidden state types in one list, repacked after a state
update, plus `any imported.Class`. All work — `tests/stdlib/test_existential_cross_module.spr`.

Designing this is also what surfaced the bug fixed in "a record's type parameters are
positional, in declaration order": `View s m` is the tree's first two-parameter record, and
they were being registered backwards.

### 3.2 Three dimension kinds, and an explicit shrink priority

```sprout
type Dimension (..) =
  | Cells Int      # exactly n columns/rows
  | Fraction Int   # weight n over whatever is left
  | Auto Int       # the item's own measured natural size
```

**Prior art** (each verified against its primary source):

| System | Exact | Flexible | Content-driven | When it does not fit |
|---|---|---|---|---|
| [CSS Grid](https://www.w3.org/TR/css-grid-1/#fr-unit) | `<length>` track | `fr` — "a fraction of the leftover space in the grid container", distributed "after all non-flexible track sizing functions have reached their maximum" | `auto`, `min-content`, `max-content` | flexible tracks are squeezed |
| [Textual](https://textual.textualize.io/css_types/scalar/) | cells (unitless) | `fr` — "the proportion of space the widget should occupy" | `auto` — "tries to compute the optimal size to fit without scrolling" | `dock` removes a widget from flow entirely |
| [ratatui](https://docs.rs/ratatui/latest/ratatui/layout/enum.Constraint.html) | `Length` | `Fill` — "expand or fill into excess available space, proportionally matching other Fill elements"; also `Percentage`, `Ratio` | `Min` / `Max` bounds | an explicit list: "Min, Max, Length, Percentage, Ratio, Fill" |

They **agree** on the three tiers, and all three name the flexible unit for a share of the
*leftover* space computed after the fixed ones. They **diverge** on overflow: CSS squeezes
proportionally, ratatui publishes a priority order, Textual sidesteps it with `dock`.

Sprout takes ratatui's answer — an explicit, documented priority — for two reasons. There is
no constraint solver in the tree and adding one (ratatui uses Cassowary) is far out of
proportion. And a terminal is integer cells, so a proportional squeeze produces a rounding
question at every step, whereas a priority order is total and exactly testable.

**The order is `Fraction` yields, then `Auto`, then `Cells`.** The rationale is what each
constructor *means*: `Cells 1` is an author's demand for a one-row status bar and honouring
it is the point; `Auto` is a request derived from content, so it is the natural thing to
compress; `Fraction` is by definition "whatever is left", so when nothing is left it gets
nothing. Within a tier, allocation runs in declaration order and a starved item gets zero
rather than a negative extent.

### 3.3 Fractions round by largest remainder, so the parts sum to the whole

CSS computes in fractional lengths. A terminal cannot: three `Fraction 1` items in 10
columns are 3.33 each, and truncating gives 9 — a visible unpainted column, and worse, a
column that moves as the terminal resizes.

`layout` distributes the floor to each item and then hands the leftover units to the items
with the largest fractional remainders, ties broken by declaration order. Three equal
fractions in 10 columns give `[4, 3, 3]`. **The allocation always sums to exactly the
available extent**, which is the invariant the tests assert directly and the one that keeps
`diff_to_ansi` from painting a stale column forever.

### 3.4 `dock` consumes from a region and returns the remainder

Textual's `dock` takes a widget out of the document flow. There is no document flow here —
layout is explicit calls — so the same idea becomes a function shape:

```sprout
dock(r: Region, edge: Edge, extent: Int) -> (Region, Region)   # (docked, remaining)
```

Folding that over a list of `(Edge, Int)` gives sticky headers, footers and sidebars, with
the body region falling out as the accumulator. This needs `split_right` and `split_bottom`
alongside M2's `split_left`/`split_top`; they are added to `geometry`, which already owns
half-open edge arithmetic, rather than duplicated here.

### 3.5 Wrapping measures columns, which is why it is not in `stdlib.string`

`wrap_to` breaks at spaces, hard-breaks a word longer than the line, and treats an existing
newline as a hard break. It is `wrap_to` and not `wrap` because `wrap` is the newtype
keyword (`wrap Meters = Int`) and so cannot name a function — the same reason the module
is `text` rather than the more obvious `string`, which is taken.

The reason it lives in `stdlib/tui/` is that a line's length is its
**column** width — a CJK ideograph is two columns and a combining mark is zero — so the
measurement is `stdlib.unicode.width` over grapheme clusters, exactly as `screen.sprout`
already places them.

That shared need drives a small refactor: the cluster segmentation currently private inside
`screen.sprout` (`cluster_list`, `split_clusters`, `take_n`, `drop_n`, `cluster_width`)
moves into `text.sprout` and is exported, and `screen.sprout` imports it. Behaviour is
unchanged; this is deduplication, not a redesign, and it keeps one definition of "how wide
is this text" for the placer and the wrapper to share.

### 3.6 The pump is one sum-typed channel

`chan_select` is recv-only, same-typed, and has no `default` arm (`stdlib/chan.sprout`), so
several differently-typed sources cannot be selected across. The shape that works today is
one `Chan Event` fed by an input task, a timer task and any worker tasks, with the main loop
recving from it. `Ref AppState` holds application state — read-modify-write is atomic
without a lock because the green scheduler only preempts at I/O, channel and sleep parks,
the same reasoning `docs/http-stateful-server-v0.md` records.

This is a **workaround for a `chan_select` limitation**, not a preference, and is recorded
as such in `BACKLOG.md` §4 so the pump's shape is attributed to its cause.

### 3.7 IDs are strings

`wrap WidgetId = String`. `Dict` and `Set` are String-keyed only
(`stdlib/prelude.sprout`), so every widget ID, keybinding and plugin key is a String
underneath. Deciding it up front rather than retrofitting; polymorphic-keyed `Dict` is
tracked separately and is far too large to bundle here.

## 4. Modules

| Module | Contents | Lands in |
|---|---|---|
| `stdlib/tui/text.sprout` | cluster segmentation, `width`, `wrap_to`, `truncate` | pure half |
| `stdlib/tui/layout.sprout` | `Dimension`, `Edge`, `solve`, `row`, `column`, `grid`, `dock` | pure half |
| `stdlib/tui/geometry.sprout` | `split_right`, `split_bottom` added | pure half |
| `stdlib/tui/widget.sprout` | `View`, `Widget`, `WidgetId` | app-loop half |
| `stdlib/tui/app.sprout` | the message pump and render loop | app-loop half |

The split is at the pure/IO seam: everything in the first three is a total function over
values, unit-testable with `run_suite`/`check_eq` and no terminal.

## 5. Syntax, type-system and error-message impact

None. No new syntax, no typing rule, no diagnostic, no builtin — so
`runtime/APPROVED_BUILTINS` is unchanged.

The widget model is the first *use* of a two-parameter record and of a cross-module
existential, but both are existing language features; the record bug that fell out was a
defect in an already-specified feature, not a new capability.

## 6. Compatibility

Purely additive apart from the `screen.sprout` refactor in §3.5, which moves private
helpers to a new module without changing any exported signature or any emitted byte.

## 7. Deferred

1. **Percentage and viewport units.** Textual has `%`, `w`, `h`, `vw`, `vh`. `Fraction`
   covers the cases a code-first API actually reaches for; percentages earn their place with
   the TCSS layer (M9) that has a stylesheet to spell them in.
2. **`Min`/`Max` bounds** (ratatui has both). They are the part of ratatui's model that
   genuinely wants a solver, since a `Min` can force a re-solve of everything already
   allocated.
3. **Bidirectional and vertical text.** Wrapping assumes left-to-right, top-to-bottom.

## 8. Tests

All pure, `run_suite`/`check_eq`, no terminal and no fixture process.

- `tests/stdlib/test_tui_text.spr` — width and wrapping: ASCII, wide (CJK) clusters,
  combining marks, a word longer than the line, existing newlines, degenerate widths.
- `tests/stdlib/test_tui_layout.spr` — the sum invariant at many extents, each shrink tier,
  largest-remainder distribution and its tie-break, `dock` folds, `grid`.
