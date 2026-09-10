# TUI M4 — the widget set, slice C2b: the list

> Status: **implemented**. Non-normative; `docs/spec-v0.md` is unaffected. The
> field is `docs/tui-input-v0.md`, focus is `docs/tui-focus-v0.md`, the widget
> model `docs/tui-widgets-v0.md`, addressed delivery `docs/tui-routing-v0.md`.

## 1. Problem

`input` showed a widget that owns almost every key. Nothing yet shows one whose
state is a *position* rather than a value, and no example in the tree is
interactive at all: `examples/tui_dashboard.sprout` had two widgets of its own
and nothing focusable, so the ring, `Tab` and `ToFocus` were exercised by tests
only.

## 2. Goals and non-goals

Goals: move a selection with the arrows, `Home`/`End` and page keys; choose an
item; the application learns both; a list longer than its region stays usable;
a list that has lost focus still shows what is selected.

Non-goals: per-item rendering (Brick's `Bool -> e -> Widget n`): items here are
`List String`. Multiple selection. Mouse selection, which needs the hit-test
tree `docs/tui-focus-v0.md` §2 defers. Horizontal scrolling.

Items that change after construction were a non-goal here and are §4.7 now:
`docs/tui-content-update-v0.md` settled the shape and `list_view` is where it
landed first.

## 3. Prior art

Every claim below is from the named primary source.

| | what leaves the widget | selection styling | page motion |
|---|---|---|---|
| **Brick** (`Brick.Widgets.List`, brick-2.13) | nothing; the application holds the list and reads `listSelectedElement` | three attributes: `listAttr` "The top-level attribute used for the entire list.", `listSelectedAttr` "…when the list does not have focus. Extends `listAttr`.", `listSelectedFocusedAttr` "…when the list has focus. Extends `listSelectedAttr`." | `listMoveByPages :: … m -> EventM n (GenericList n t e) ()` — an event-monad action, not a pure function of the list |
| **Textual** (Guide → ListView) | two messages: `ListView.Highlighted` "Posted when the highlighted item changes.", `ListView.Selected` "Posted when a list item is selected, e.g. when you press the enter key on it." | widget-owned, via CSS | `PageUp`/`PageDown` bindings |
| **bubbles** (`list`) | the parent reads `SelectedItem()` off the model | widget-owned styles | a `Paginator` the model carries |

Brick's and bubbles' shape needs the application to read the widget. Ours
cannot: `Widget m` erases the state type, so Textual's two messages are not a
preference here — they are the only channel.

## 4. Decisions

### 4.1 An index leaves the widget, not the line

`on_select: Int -> m`. The application supplied the items, so it still has
them, and an index names one of them unambiguously — two identical lines are
different rows, and a string cannot tell them apart. It is also what a richer
application needs: its own model is rarely `List String`, and the index is the
join back to it.

This is the one place the field's rule (`docs/tui-input-v0.md` §4.2, "what
leaves is the whole value") inverts, and for the same reason: what the
application cannot reconstruct is what has to be sent. A field's text is
unknowable to it; a list's items are its own.

### 4.2 Highlighting and choosing are two messages

`on_select` is required and `on_highlight` is a `Maybe`, as Textual posts both
`Highlighted` and `Selected`. Moving through a list is not picking from it — a
preview pane wants every move, a form wants only the commit — and a widget that
sent one message for both would make them indistinguishable.

Without an `on_highlight`, a move is **claimed and silent**: the key is the
list's whether or not anybody listens, so `Down` must not fall through to the
application's bindings just because nothing subscribed.

### 4.3 Three styles, because a blurred list still has a selection

`focus.FocusStyle`'s two cannot express it: a list that has lost the keyboard
still has a selected row, and painting it as ordinary loses the state, while
painting it as focused lies about where the keyboard is. `ListStyle` has
`normal`, `selected` and `selected_focused`, which is Brick's split above.

The default is reverse video where the keyboard is and bold where it is not —
the two attributes every terminal has.

### 4.4 The window is a function of the selection

The rule `input` takes for its caret (`docs/tui-input-v0.md` §4.5): the top of
the list until the selection passes the last row, and the selection on that row
from then on. A scroll offset kept in state can drift out of step with the
selection, and `render` is pure in the state, so it could not correct one.

### 4.5 A page is a number the caller gives

`ListOpts.page`, default 10. A page *should* be the rendered height, and the
widget cannot know it: `render` receives the region but is pure in the state,
so there is nowhere to put the height for a later key handler to read. Brick
does not have this problem because `listMoveByPages` is an `EventM` action
rather than a pure function of the list.

The alternative is to drop the page keys, which costs a long list its only fast
motion. A number named beside the region the caller already chose is the honest
version of the same thing.

### 4.6 Every move clamps, and an empty list is silent

`Down` at the end, `PageDown` past it and a `start` outside the list all clamp
rather than failing or wrapping: the index is the caller's claim about a list
the framework cannot check, and an unselectable list is a worse answer than a
clamped one. A move that lands where it started reports nothing — there is no
new selection.

An **empty** list has no index to name. Every key it would use is still claimed
— the application must not see a stray `Down` because the list happened to be
empty this frame — and nothing is announced.

The selection is **kept** while the list is empty, not reset, so a list emptied
and refilled (§4.7) comes back where the user left it. Resetting it would move
the selection without announcing it — and §4.2's silent move is silent because
*nobody subscribed*, whereas this one would be invisible to a subscriber too,
leaving the application's mirror of the selection wrong with nothing to show it.

### 4.7 Content arrives as a message the caller teaches it to read

`on_content: Maybe (m -> Maybe (List String, Maybe Int))`. A reusable widget is
polymorphic in `m` and so cannot decode a message on its own; the decoder is the
application's, supplied at construction, and a `Nothing` from it declines the
delivery so unrelated traffic still reaches `update`. Design and the options
rejected: `docs/tui-content-update-v0.md`.

A claimed message stops there — `app.delivered` gives `update` only what the
widget declined — so content sent by an addressed `cmd_to` is seen by the list
and by nobody else, and the `Chose i` that follows names a row the application
never held. Send content through `update` (an unaddressed `cmd`, forwarded with
`deliver`) whenever the index has to be resolvable.

The `Maybe Int` is brick's `listReplace` argument. Keeping `sel` keeps a
*position*, not an item — under a narrowing filter the same index names a
different row every keystroke — so the caller can name where the selection
belongs. Both paths run through §4.6's clamp, which is the divergence from
brick worth naming: brick sends an out-of-range index to 0, this to the last
row, because one clamp rule for the widget beats parity with another framework.

## 5. Surface

```sprout
# stdlib/tui/widgets/list_view.sprout
export type ListStyle = (normal: Style, selected: Style,
                         selected_focused: Style)
export fn list_style_default() -> ListStyle

export type ListOpts m = (start: Int, on_highlight: Maybe (Int -> m),
                          page: Int, look: ListStyle,
                          on_content: Maybe (m -> Maybe (List String, Maybe Int)))
export fn list_opts() -> ListOpts m
export fn list_view(id: WidgetId, items: List String,
                    on_select: Int -> m) -> Widget m
export fn list_view_with(id: WidgetId, items: List String,
                         on_select: Int -> m, opts: ListOpts m) -> Widget m

# stdlib/tui/text.sprout — was private to the `text` widget
export fn widest(ls: List String) -> Int
```

Keys claimed while focused: `KUp`, `KDown`, `KPageUp`, `KPageDown`, `KHome`,
`KEnd`, `KEnter`. Everything else declines, `KTab` included — `docs/tui-focus-v0.md`
§4.5 is what moves focus, not the list's silence — and so does any of those
with ctrl or alt held, which is an application's chord.

Measures `Greedy` on both axes: a list scrolls rather than demanding the height
its item count would ask for. Its natural size is its widest item by its count.

## 6. Compatibility

Additive. `text.widest` is a promotion of a private helper in
`stdlib/tui/widgets/text.sprout`, which now calls it instead of its own copy.

`examples/tui_dashboard.sprout` is rewritten: its key-log widget read the
keyboard on the broadcast, which under a focus ring sees only what the focused
widget declined. It becomes a log fed by addressed messages, and Esc replaces
`q` as the quit key — a field would type a `q`.

## 7. Tests

`tests/stdlib/test_tui_list_view.spr`, written failing first against a stub
that claims nothing and paints nothing — 27 of its first 40 assertions red.

Moving: `Down` and `Up` report the new index; `Up` at the start and `Down` at
the end are *claimed and silent*, which a neighbour probe beside the list
distinguishes from falling through; `Home` and `End`; a move with no
`on_highlight` is silent but still claimed.

Pages: `PageDown` by the given page; `PageUp` back; a page past either end
stops at the last item and, at the start, says nothing.

Choosing: `Enter` reports the highlighted index; a `start` is where it begins,
and one past either end clamps; an empty list has nothing to choose and nothing
to move to.

Declining: a ctrl chord and an alt chord both reach the neighbour; `Tab` is the
ring's; an unused key falls through; an unfocused list ignores a key, declines
one addressed to it directly, claims a focus notification, and declines a
message.

Content (§4.7): rows replace and the selection survives; one past the end of
shorter content clamps and says so; an emptied list neither chooses nor
announces; a named index lands, and one past the end clamps to the last row; a
replacement that does not move the selection is silent; an unrecognised message
is declined, and so is every message when no decoder was given. Painting and
`measure` follow the new rows, which is what catches a replacement that moves
`items` without `count` and `width`.

Painting: the items; an item wider than the region is cut to it; the selected
row carries the focused style and the others do not; an unfocused list marks
its selection without the focused style; the window follows the selection off
the bottom and `Home` brings the top back; a region of *no rows* paints
nothing; a supplied style paints the selection and the default does not.

## 8. Deferred, filed in `BACKLOG.md` §4

- **Items that change after construction** — landed (§4.7), here first and then
  on `input`, `text_area` and `scroll_view`. Designed in
  `docs/tui-content-update-v0.md`.
- **Per-item rendering.** Brick's `renderList` takes `Bool -> e -> Widget n`,
  so an item can be any widget; here an item is a `String`.
- **Mouse selection**, which needs the hit-test tree `docs/tui-focus-v0.md` §2
  defers, and is filed with it.
