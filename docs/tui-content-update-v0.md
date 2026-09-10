# TUI content updates (v0)

Status: **APPROVED — option C (§5.3). Implemented for `list_view`**
(`docs/tui-list-view-v0.md` §4.7); `input`, `text_area` and `scroll_view` remain, and
`BACKLOG.md` §4 carries them. Non-normative; `docs/spec-v0.md` governs the language,
and nothing here proposes a language change.

What landing the first widget confirmed: the decoder needed no new machinery in the
widget at all. Replacement re-uses the existing clamp (`clamped_into`) and the
existing announce rule (`settled`), so "a content change that moves the selection
announces it" and "a move that changes nothing is silent" came out as one rule rather
than two, and the empty-list guard moved from the key path into the shared one.

Revision 3 (2026-09-10), after an independent review. Three factual corrections, all
verified against source: bubbles *preserves* the selection on `SetItems` rather than
resetting it (§4), brick sends an out-of-range index to 0 where this design clamps to
the last row (§4), and revision 2's §5.3 cited an `on_change` field that `list_view`
does not have. The decoder payload gains a selection placement as a result (§5.3),
and §6 re-costs option B, whose revision-2 sketch did not work.

Revision 2 (2026-09-10). Revision 1 claimed the inward channel was closed for every
widget and recommended option B on that basis. It is closed only for a *reusable*
one: an application-local widget reads `ToMsg` today and the dashboard's log pane
does (§3). Option C fills the smaller hole that leaves, and dominates option A.

## 1. Problem

A **reusable** widget's content enters at construction and can never change. The
qualifier is load-bearing and §3 earns it — an application's own widget has a way in
today. Every widget in `stdlib/tui/widgets/` is reusable, so every widget an
application does not write itself has this problem.

```sprout
export fn list_view(id: widget.WidgetId, items: List String,
                    on_select: Int -> m) -> widget.Widget m
```

`items` is the only way in (`list_view.sprout:172`). `text_area` takes an `initial`
the same way, `input` an `initial`, `scroll_view` a child. There is no second
entry point, so an application that wants a list to show different rows has one
move available: build a new widget.

Building a new widget builds new state. The selection index (`Lst.sel`), the caret
(`input`'s and `text_area`'s zipper), the scroll offset (`Sv.across`/`Sv.down`) and
every widget's `has_focus` are fields of its own state record, so they reset. Focus
resets twice over: `focus.Ring` holds `at` — who currently has the keyboard — in
*its* state (`focus.sprout:29`), so a rebuilt tree returns focus to the ring's
starting id.

What must survive is smaller than it looks, because the window is derived rather
than stored almost everywhere: `list_view` computes its first visible row from the
selection (`first_shown`), and `text_area` derives both axes from the caret. Only
`scroll_view`, which has no caret to follow, keeps a position of its own.

The result is that "show different rows" and "keep the user's place" are mutually
exclusive today.

### 1.1 Why this blocks the milestone, not just a widget

Every module M5 names hits it, which is what moves this ahead of the remaining C3
widgets in priority:

| module | content that changes after construction |
|---|---|
| `ide/filetree.sprout` | expanding a directory adds children |
| `ide/palette.sprout` | a command palette *is* a filter box over a list |
| `ide/document.sprout` | opening a file replaces the editor's buffer |
| `ide/pane.sprout` | opening a file adds a tab |

`tree` is the worst widget to build before this is settled: a file browser's
content changes by definition, every time a node expands.

## 2. Goals and non-goals

**Goals.** An application can change what a widget shows while keeping the state
the user built up — selection, caret, scroll position, focus. The mechanism is
typed: no dynamic payload, no downcast. Existing constructions keep working
untouched, so the widget set does not fork into old and new spellings.

**Non-goals.** Per-item widget rendering (brick's `Bool -> e -> Widget n`), filed
as **TUI `list_view` — per-item rendering** — adjacent, separable, and it should not
ride along. Mouse selection, already deferred with the hit-test tree. Incremental
diffing of content: replacement is the operation, and a terminal repaint is cheap.

## 3. Root cause — the way in is closed by REUSABILITY, not by the box

`View s m` has two ways in from outside. One is closed absolutely; the other is
closed only for a widget that wants to work in more than one application, which is
the whole of `stdlib/tui/widgets/`.

```sprout
export type View s m = (
  state: s,
  on_event: s -> event.Event -> (s, List m, List (Cmd m)),
  route:    s -> WidgetId -> Delivery m -> Maybe(s, List m, List (Cmd m)),
  render:   s -> geometry.Region -> screen.Screen -> Unit !{IO},
  measure:  s -> geometry.Size -> Measured
)

export type Widget m (..) = | exists s. Widget (View s m)
export type Delivery m (..) = | ToMsg m | ToEvent event.Event | ToFocus Bool
```

- **`s` is existential, and that is absolute.** Once boxed, the state type is
  unnameable from outside, so no function the application can write may touch a
  widget's state. Nothing recovers this.
- **`m` is universal only for a REUSABLE widget.** A stdlib widget is polymorphic in
  the application's message type, so it cannot inspect an `m`, which is why
  `list_view` writes `| widget.ToMsg _ -> Nothing` (`list_view.sprout:64`) — not
  laziness, the only inhabitant of that type.

**But an application-local widget is monomorphic in its own message type, and reads
`ToMsg` today.** The dashboard's log pane is the working proof:

```sprout
fn log_route(ls: List String, d: widget.Delivery Msg) -> (List String, Says, Asks) =
  match d with
  | widget.ToMsg m -> (Cons(entry(m), ls), Nil, Nil)     # tui_dashboard.sprout:92
  # ToEvent and ToFocus arms elided
```

Its content — a `List String` that grows on every message — arrives entirely through
the channel this document is about, addressed by `deliver(w, log_id(), ToMsg(m))`
(`tui_dashboard.sprout:210`). So the delivery path works, is exercised in-tree, and
needs nothing built.

What a reusable widget lacks is not the channel but a way to *decode* what arrives
on it. That is a smaller hole than "no channel exists", and §5.3 fills it with the
projection pattern the widget set already uses in the other direction.

## 4. Prior art

Every comparable framework keeps the persistent state **in a value the application
owns**, which is how replacement is expressible in each of them — not the only way it
could be, as §5.3 shows. Signatures verified against each project's own reference
documentation.

| framework | where the state lives | changing content | keeps the selection? |
|---|---|---|---|
| **brick** (Haskell) | `GenericList n t e`, held in the app's state | `listReplace :: t e -> Maybe Int -> …` | caller names the new index |
| **ratatui** (Rust) | caller-owned `StatefulWidget::State`; the widget is per-frame | build a new widget each frame | yes — state is a separate value |
| **bubbles / Bubble Tea** (Go) | `list.Model`, a field of the application's own model | `func (m *Model) SetItems([]Item) tea.Cmd` | yes — the index is preserved, the *page* clamped |

```haskell
renderList :: (…) => (Bool -> e -> Widget n) -> Bool -> GenericList n t e -> Widget n
```

```rust
pub trait StatefulWidget {
    type State: ?Sized;
    fn render(self, area: Rect, buf: &mut Buffer, state: &mut Self::State);
}
```

Two things to take from this. First, **nobody boxes widget state existentially** —
Sprout is the outlier. That is what stops an application *reading* a widget's state,
and it is why option B has to move the state out to give it back; it is not what
stops content going *in* (§3). Second, **none of the three resets the selection, and
the one with a dedicated replacement call still lets the caller override it.**
`listReplace` takes a `Maybe Int`; bubbles' `SetItems` keeps the index and
`updatePagination` re-derives the page around it. A filter wants the selection reset
or moved, a refresh wants it preserved, and only the caller knows which — so a
replacement mechanism that cannot ask the caller is under-powered, which is the one
place revision 2's option C was wrong. §5.3 carries the placement.

Two divergences worth naming rather than leaving implied. brick sends an
out-of-range index to **0** (`inBoundsOrZero`), where §5.3 clamps to the last row —
`list_view` already has exactly one clamp rule and `opts.start` goes through it
(`clamped_into`, `list_view.sprout:105`), so internal consistency wins over parity
here. And bubbles clamps the *page* but not the cursor, so a shrink can leave its
cursor past the end of the visible items; §5.3 clamps the index itself.

Sources: `hackage.haskell.org/package/brick-2.13/docs/Brick-Widgets-List.html`,
`docs.rs/ratatui/latest/ratatui/widgets/trait.StatefulWidget.html`,
`pkg.go.dev/github.com/charmbracelet/bubbles/list`. The selection-behaviour column is
not from the reference docs, which do not state it — `listReplace`,
`SetItems` and `updatePagination` were read in each project's source, since revision
2 got this row wrong by inferring it.

## 5. Options

### 5.1 Option A — state echo (small)

Each stateful widget publishes its interaction state as a public type and announces
it when it changes; the application stores the latest value and hands it back when
it rebuilds.

```sprout
export type ListState = (sel: Int, has_focus: Bool)
export fn list_view_from(id: WidgetId, items: List String, st: ListState,
                         on_select: Int -> m) -> Widget m
# opts gain: on_change: Maybe (ListState -> m)
```

`sel` and `has_focus`, not a window offset — `list_view` derives the visible range
from the selection, so there is nothing else to carry.

Touches no shared type — not `View`, not `Widget`, not `Delivery`, not `App`. Each
widget is a separate, independently landable change.

The cost is that it makes every application do bookkeeping the framework could do
for it, and it is easy to get subtly wrong: an application that forgets to store an
echo silently reverts the user's position on the next rebuild, with nothing in the
types to catch it. It also leaves focus broken — `Ring`'s `at` would need the same
treatment, and the ring is not a widget the application usually names.

**And there is nowhere to put the echo.** `App m` is `(root, update, tick_ms, boot)`
and `update: m -> Widget m -> (Widget m, Flow, …)` threads the tree and nothing else,
so an application holding a `ListState` between messages must smuggle it into some
widget's state — which means writing a custom root widget whose state is a model in
all but name. Option A is therefore option B, hand-rolled once per application and
unchecked. That is what makes it dominated rather than merely smaller.

### 5.2 Option B — model and view split (the prior-art shape)

`App` grows a model type; `update` transforms the model rather than the tree; a
`view` projects the model into a fresh `Widget m` tree each frame.

```sprout
export type App model m = (
  init: model,
  view: model -> widget.Widget m,
  update: m -> model -> (model, Flow, List (widget.Cmd m)),
  tick_ms: Int,
  boot: Scope -> Chan (Signal m) -> Unit !{IO}
)
```

Widget interaction state becomes a public typed value the model holds — `ListState`,
`InputState`, `TextAreaState`, `FocusState` — and boxing happens late, inside `view`,
where the concrete `View s m` is still in scope. Rebuilding the tree every frame
stops being destructive, because nothing that must survive lives in the tree.

This is Bubble Tea's shape exactly, and brick's and ratatui's in substance. It
removes the problem class rather than routing around it, and it fixes focus for
free: `at` becomes a model field like any other.

The cost is larger than that sketch admits, and revision 2 understated it. As
written, B does not work: `on_event` still returns the new state *inside the
existential box*, and `view(model)` rebuilds from the model next frame, so a
selection the user just moved is discarded — nothing wrote it back, and the box is
what stops the loop harvesting it out. Making B work needs one of two further moves:

1. every widget also **announces** its state whenever it changes, so `update` can
   store it — which is option A's echo, meaning B-as-sketched contains A; or
2. event handling leaves the widgets: each exports pure state transitions
   (`list_state_down : ListState -> ListState`) that the application plumbs per
   widget — the full Elm/ratatui inversion, rewriting every widget's event surface,
   the focus ring and the routing design.

Either way B reverses a decision `app.sprout:46-48` states outright — *"widget-local
state lives inside the widget … there is deliberately no separate `Ref AppState`"* —
and re-derives `text.widest` and `list_length` every frame, the cost `list_view`'s
INVARIANTS comment says its cache exists to avoid. So `App m` becoming `App model m`
is the beginning of B, not the whole of it.

### 5.3 Option C — a content decoder supplied at construction

The application hands the widget a function that recognises its own message and
extracts the payload. The widget keeps owning its state; content arrives on the
delivery path §3 shows already working.

```sprout
# in ListOpts m
on_content: Maybe (m -> Maybe (List String, Maybe Int))

# in the route handler
| ToMsg msg ->
    match decoded(l.opts.on_content, msg) with
    | Just (rows, place) -> Just(replaced(l, rows, place))
    | Nothing -> Nothing          # not ours; falls through to `update`
```

The `Maybe Int` is brick's `listReplace` argument (§4) and it is what makes the
mechanism usable by the modules that motivate it. Preserving `sel` preserves a
*position*, not an item: under a narrowing filter index 3 names a different row after
every keystroke, a command palette conventionally wants the selection back at the top
per keystroke, and expanding a `tree` node shifts every index below it. `Nothing`
means "keep the user's place" and is the common case; `Just i` is the caller taking
the decision. Both go through `clamped_into`, so neither can leave the list
unselectable.

This is the projection the widget set already uses, run backwards. Every widget
today takes `content -> m` (`on_select: Int -> m`, `on_change: String -> m`); this
takes `m -> Maybe content`. Same shape, same typing, opposite direction — and a
decoder that does not recognise a message returns `Nothing`, which `route_when`
already turns into "not mine", so an unrelated message still reaches `update`.

**The reason to prefer it over A: it removes the rebuild rather than making rebuilds
survivable.** Nothing is reconstructed, so selection, caret, scroll offset, `has_focus`
and `Ring.at` all survive because nothing touched them. A leaves `Ring.at` broken —
the ring is not a widget an application usually names, so it has no echo to hand back.

Two details it must pin, neither hard:

- **A selection that falls off the end.** Both paths run through `clamped_into`
  (`list_view.sprout:105`), the file's single existing clamp rule, so the last row is
  the answer when the index is past the end and 0 when the list is empty.
- **A clamp that moves the selection is announced.** `replaced` routes the new index
  through `settled` (`list_view.sprout:113`), which already emits `on_highlight` when
  the index changes and stays silent when it does not. So an application mirroring
  the selection cannot silently desynchronise, and no new option field is needed —
  note that `list_view` has no `on_change`, which is `input`'s and `text_area`'s.
  A replacement that leaves the index alone but changes the row *under* it announces
  nothing. That is correct **only when the application has the rows** — see the
  limitation below.
- **A claimed content message does not reach `update`.** `app.delivered` routes to
  `update` on `Nothing` only; on `Just` it runs `stepped`, which applies `update` to
  what the widget *said*. So content that arrives by an addressed command —
  `cmd_to(list_id, …)`, which `app.sprout:250` turns into `SigTo` — is seen by the
  widget and by nobody else, and the later `Chose i` names a row the application
  cannot resolve. `App m` has no model to keep it in, which is exactly the gap
  option B closes.

  The idiom that avoids it: send content through `update`, not by return address.
  An unaddressed `cmd` becomes `SigMsg` and lands in `update`, which forwards it
  with `deliver(w, list_id, ToMsg(rows))` and can recompute the rows when the
  selection comes back. A palette recomputes them from the query each keystroke
  anyway. Reach for `cmd_to` only when the widget is the sole consumer.
- **Content identity.** Replacing rows with an equal list still clamps and repaints.
  Harmless, and cheaper than an equality constraint on the element type.

### 5.4 Rejected

- **A dynamic payload on `Delivery`** (a `String`- or tag-encoded content message the
  widget decodes). Restores the channel by discarding the type system at the seam,
  and turns a mismatch into a runtime decode failure. This is the one option that
  makes the framework less safe than the language it is written in. Not to be
  confused with option C, which decodes too — but through a function the application
  supplies at construction, so the payload type is checked there and a mismatch is a
  compile error rather than a decode that fails at runtime.
- **Type-tagged transplant** — the loop carrying old state across a rebuild when ids
  match. Needs runtime type equality across two skolems; Sprout has no such witness,
  and inventing one for this is a language change to solve a library problem.

## 6. Recommendation

**Option C**, before the remaining C3 widgets. **Option A is dominated and should not
be chosen** — C costs the same, fixes strictly more, and asks the application for no
bookkeeping.

| | A | B | C |
|---|---|---|---|
| shared types changed | none | `App`, every application | none |
| migration | per widget | ~20 files, and an event-model change (§5.2) | per widget, additive |
| keeps focus across a content change | ✗ | ✓ | ✓ |
| application bookkeeping | an echo per widget | a model | none |
| application can *read* widget state | ✓ | ✓ | only what a widget announces |

So the live choice is C against B, and it turns on one question: **does an
application need to read a widget's state, or only to change its content?** Today,
only to change its content. C is right until that stops being true.

If it stops being true — an IDE restoring a cursor position into a file it reopens is
the case to watch — B is the answer, and B is not made harder by having done C first:
C adds a decoder to opts and touches no shared type, so it is additive to the model
split rather than an obstacle in front of it.

B remains costed here because that day may come. Revision 2 gave the table below as
"its whole surface"; it is the **migration** surface only, and the implementation
rows were missing:

| what changes | files |
|---|---|
| the `App` signature — every application and app-level test | `examples/tui_dashboard.sprout`, `test_tui_app.spr`, `test_tui_route.spr`, `test_tui_cmd.spr`, `tui_widgets.spr`, `resize_probe.spr` |
| a stateful widget's constructor — its own tests | `test_tui_list_view.spr`, `test_tui_text_area.spr`, `test_tui_input.spr`, `test_tui_scroll_view.spr` |
| **the implementation** | `app.sprout` (the loop), `widgets/focus.sprout` (the ring), and a public state record in each of `list_view`, `input`, `text_area`, `scroll_view` |
| **the docs** | `docs/tui-widgets-v0.md` §3.6 and each per-widget doc |
| **downstream** | **none** — `uncharted-suns` does not import `stdlib.tui` |

So roughly twenty files rather than ten, no downstream consumer, and regenerating
`tests/golden/ir/examples__tui_dashboard.sprout.ll` is a gate step rather than an
edit. The file count is still not what rules B out — §5.2's two further moves are.
Doubling the count and reversing a documented architectural decision to solve a
problem C solves with one optional field is the argument, and it did not depend on
the miscount.

Either way the sequencing holds: settle this before `tabs`, `tree` and `table`, since
M5's seven modules and those three widgets would otherwise each be written against a
shape that is about to change.

## 7. Impact

**Language, syntax, types, evaluation order:** none, under any option. This is stdlib
written in existing surface — an optional record field holding a pure `m -> Maybe c`
under C, an ordinary two-parameter record type under B.

**Compatibility.** C is additive: `on_content` defaults to `Nothing`, so every
existing construction keeps compiling and behaving identically, and no test needs
touching to stay green. Its migration cost is zero, which is why §6 does not table
one. B's is the ten files above.

**Errors.** Under C a decoder with the wrong payload type is a type error at the
construction site. The failure C cannot catch statically is an application that never
sends the content message — the widget simply keeps its rows, which is the same
silence as today rather than a new one. Under B a forgotten model field is a type
error at the `view` call.

**Effects.** `on_content` is a pure `m -> Maybe c` stored in a record. Deliberately
not effect-polymorphic: an `!{e}` field is erased at construction and would let IO run
inside a handler the checker believes is pure — the reason `Cmd` is a newtype over a
concrete `!{IO}` arrow (`widget.sprout:46`).

**Spec:** no change. `docs/spec-v0.md` says nothing about the TUI.

**Docs:** under C, each per-widget doc gains its `on_content` section and
`docs/tui-widget-set-v0.md` gains the pattern once; under B, `docs/tui-widgets-v0.md`
§3.6 (the loop) and every per-widget doc gain a state type. The backlog entry **TUI
content cannot change after construction** is deleted on landing either way, since it
is this problem.

## 8. Tests

For C, all six currently unwriteable:

- Content arriving by message keeps the selection, the caret, the scroll offset and
  `has_focus` — one assertion per stateful widget, and the point of the whole change.
- Focus survives a content change *at the ring*: `Ring.at` still names the same widget
  afterwards, which is the half option A cannot deliver.
- A narrowing filter clamps a selection that has fallen off the end, and an emptied
  list ends with no selection rather than an index naming nothing.
- A message the decoder does not recognise is declined, reaches `update`, and leaves
  the widget's content and state untouched — the `Nothing` path, which is what keeps
  an unrelated message from being swallowed by a widget that happens to be addressed.
- A decoder returning `Just i` puts the selection at `i`, and an `i` past the end
  clamps to the last row rather than to 0 — the deliberate divergence from brick
  (§4), which needs a test naming it or it reads as an accident later.
- A clamp or a placement that *moves* the selection announces it through
  `on_highlight`; one that leaves the index where it was announces nothing. The
  silent case is the regression risk, since an application mirroring the selection
  desynchronises invisibly if the announcement is dropped.

The default matters as much as the feature: a widget constructed without
`on_content` must behave exactly as it does today, which the existing per-widget
suites already assert and must keep passing unchanged.
