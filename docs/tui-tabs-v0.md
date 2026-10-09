# TUI `tabs` (v0)

Status: **IMPLEMENTED — C3d** (`stdlib/tui/widgets/tabs.sprout`). Non-normative;
`docs/spec-v0.md` is unaffected. Tests: `tests/stdlib/test_tui_tabs.spr`.

## 1. Problem

The IDE shows one file. `ide/app.sprout` wires exactly one editor pane, so opening a second file
replaces the first (`docs/ide-v0.md` §9). A pane that holds several files, and a palette that
overlays one, both want a widget that holds several children and shows one. `stdlib/tui` has none:
it was the last unbuilt piece of C3 besides `table`.

## 2. Goals and non-goals

**Goals.** A reusable container: several children, one shown, a one-row bar naming them. Tabs can
be opened, selected and closed while the app runs. A hidden child keeps receiving what is addressed
to it and what is broadcast.

**Non-goals for v0.** Retitling a tab (the IDE's dirty `*`); a bar wider than the screen scrolling
to keep the active title visible — today it is cut at the edge; the mouse; reordering tabs. Each is
additive over the shipped surface and filed in `BACKLOG.md` §4.

## 3. Prior art

Every row checked against the library's own reference.

| | widget | holds content? | add/remove at runtime | tab named by | who switches |
|---|---|---|---|---|---|
| Textual | `TabbedContent` | yes — "only a single child … is visible at once" | `add_pane`, `remove_pane(pane_id)`, `clear_panes` | string id; assign `active` to switch | the application, or its inner `Tabs` |
| Textual | `Tabs` | no — a row of tabs | `add_tab`, `remove_tab`, `clear` | string id (`active`) | itself, Left/Right |
| ratatui | `Tabs` | no — the bar only; `select(index)` each frame | n/a: rebuilt every frame | index | the application |
| Bubble Tea | none in `bubbles`; `examples/tabs` keeps `Tabs []string`, `TabContent []string` and `activeTab int` in the model | — | — | index | the model's `Update` |
| brick | no tabs module among `Brick.Widgets.*` | — | — | — | — |

**Sprout takes Textual's `TabbedContent` shape — a container — and not the bar-only shape.**
ratatui and Bubble Tea can leave "which tab is shown" to the application because the application has
a model. `app.App` has none (`docs/ide-v0.md` §3): `update` cannot keep an index between messages.
A bar-only widget would leave that number nowhere, so the container holds it.

**Names, not indices**, as Textual does. An index means something else after a close; a key does
not.

## 4. Decisions

### 4.1 Surface

```sprout
export type Tab m (..) = (key: widget.WidgetId, title: String, child: widget.Widget m)
export type Change m (..) = Open (Tab m) | Select WidgetId | Close WidgetId | Next | Prev
export type TabsOpts m (..) = (bar: Style, active: Style,
                               on_change: Maybe (m -> Maybe (Change m)),
                               on_switch: Maybe (WidgetId -> m))
export fn tabs_opts() -> TabsOpts m
export fn tabs(id, ts) / tabs_with(id, ts, opts) -> widget.Widget m
```

The first tab starts shown. `on_change` is a decoder like every other `on_content`
(`docs/tui-content-update-v0.md` §5.3): the application's message stays data, and the decoder is
where an `Open` builds the child widget. `on_switch` hears every change of the shown tab, whatever
caused it — a close moves the selection without being asked to.

### 4.2 A tab's key is the id its child answers to

That is the whole addressing rule, and it is what lets the container forward without seeing inside
a child. A delivery addressed:

- **to the tabs' own id** — a `ToMsg` the decoder reads is a change. Anything else (an undecoded
  message, a key, focus) goes to the shown tab, delivered to its key. So the focus ring lists the
  tabs' id, and "save" or "undo" addressed to the tabs reach whichever file is on screen.
- **to any other id** — first claimant over **all** tabs, shown or not. A command's answer is
  addressed to the tab that asked, and it can land after that tab was hidden. Except `ToFocus`,
  which is declined: focus is the container's to hand out, as it is the ring's
  (`focus.sprout`'s `ring_route`).

A broadcast reaches every tab too. A hidden editor counts ticks toward its autosave.

### 4.3 Changes

- `Open` on a key already present **selects that tab and drops the new child**. Opening a file the
  IDE already shows is "show it", and the decoder cannot know what is open, so the container must
  decide. Textual raises on a duplicate id instead.
- `Open` on a new key appends it and shows it.
- `Close` on the shown tab shows its right-hand neighbour, or its left-hand one when it was last.
  `Close` on a hidden tab changes nothing else.
- `Next` and `Prev` wrap.
- `Select` or `Close` of a key that is not there is claimed and silent.
- A key repeated in the list given to `tabs_with` resolves to the first, as `deliver_first` does;
  the later one is painted but never reached.

### 4.4 No keys of its own

The tabs bind nothing. Textual's `Tabs` takes Left and Right because the bar itself holds focus;
here the shown child does, and an editor needs both arrows. Switching is the application's binding
and the history is the widget's — the split `docs/ide-v0.md` §5.3 made for undo.

### 4.5 Focus

The tabs remember whether the ring lit them, and pass focus to the shown child. Switching while
focused blurs the old child before lighting the new one, so two are never lit at once. Closing the
shown tab blurs it on its way out: a pane that saves on blur (`ed.WhenUnfocused`) gets to. A tabs
that is focused while empty lights the next tab opened.

### 4.6 Painting and size

The bar is one row: each title as ` title `, back to back, the shown one in `opts.active` (reverse
video by default) whether or not the tabs are focused. Titles past the right edge are cut. The body
is the rest, and only the shown child is painted, clipped to it.

Size is the bar over the shown child: the wider of the two, one row taller, growing as the child
does. Only the shown child is measured, so a switch can change a `fit` slot's size; a pane meant to
fill its space should sit in a `fraction`.

## 5. What the IDE pane will meet

Found while designing this; recorded so the pane's design starts from them.

- **Every pane answers to one id.** `ide/app.sprout` sends every read and write to the fixed
  `editor_id`. Several panes need the pane's id in `ed.Opening` and `ed.Saving`, or the answer
  cannot be addressed back to the pane that asked — and two panes' receipts can hold equal
  versions, so the stale-reply check would not catch a misdelivery.
- **Enter on a directory** reaches `Choose`. Today the read fails and the pane keeps its file; with
  tabs it would open an empty tab first.
- **Closing a tab with unsaved edits** needs a rule. With autosave the blur of §4.5 covers
  `WhenUnfocused` only.

## 6. Tests

`tests/stdlib/test_tui_tabs.spr`. Every child is a probe that writes down what reached it, so the
shown tab, a hidden tab and nobody give three different transcripts. Covers the bar and body, the
highlight, the cut, measuring; forwarding to the shown tab, to a hidden tab by key, and declining;
each `Change` including the duplicate open and both close neighbours; focus moving with a switch
and a close; a key through the ring; a tick reaching hidden tabs.
