# TUI M4 — the widget set, slice C3a: clipping and the scroll view

> Status: **implemented**. Non-normative; `docs/spec-v0.md` is unaffected. The core
> screen is `docs/tui-core-v0.md`, the widget model `docs/tui-widgets-v0.md`,
> containers `docs/tui-widget-set-v0.md`, focus `docs/tui-focus-v0.md`.

## 1. Problem

Nothing in the tree can show content taller than the space it was given, except
`list_view`, which manages it by owning its items and stopping the paint loop
(`list_view.sprout` `painted_rows`). A widget that wraps *someone else's* widget
cannot do that, and it is what `tabs`, `tree`, `table` and `text_area` all want.

The obstacle is that region clipping is a **convention**, not a rule.
`screen_write` clips at the screen edge only (`screen.sprout` `in_bounds`);
staying inside your region means choosing to call `paint.line`/`paint.at`, which
do the test themselves. A scroll view has to hand its child a region taller than
the visible area and shifted up, and the child then paints correctly *inside its
own region* — onto the cells of the widget above.

## 2. Goals and non-goals

Goals: a widget that clips, whatever its child does; a `scroll_view` that scrolls
any child on either axis; the scrollable extent derived from what the child
already reports; painting outside your region made impossible rather than
discouraged.

Non-goals: scrollbars — an indicator needs a style vocabulary of its own, and the
`Ambiguous`-width question in `BACKLOG.md` §4 touches the glyphs it would use.
Auto-scrolling to a focused child (Brick's `visible`), which needs the child's
solved position and so waits on the retained hit-test tree that
`docs/tui-focus-v0.md` §2 defers. Smooth or animated scrolling.

## 3. Prior art

Every claim below is from the named primary source.

| | clipping | scrollable extent | what drives the offset |
|---|---|---|---|
| **Brick** (`Brick.Widgets.Core.viewport`, brick-2.13) | the viewport clips, so that widgets can "be scrolled without being scrolling-aware"; "The viewport renders its contents anew each time the viewport is rendered." | the child is rendered, then cropped | the event handler: "the `EventM` monad provides primitives to scroll viewports created by this function if `visible` is not what you want", or a child's `visible` request |
| **Textual** (Guide → Widgets, `ScrollView`) | the widget renders only what shows: `render_line` generates strips "for the visible area of the widget, taking into account the current position of the scrollbars" | declared, not derived — "The `ScrollView` class requires a _virtual size_" | the widget's own bindings |
| **bubbles** (`viewport`, Go) | the model holds the content and renders a slice of it | `SetContent` plus `Height`/`Width` | `YOffset`, moved by the default keymap: Up/Down, PageUp/PageDown, half-page; "horizontal scrolling is disabled by default in v1" |

Two shapes. Brick clips a scroll-**unaware** child, which is the only one that
composes: `viewport` takes any widget. Textual and bubbles make the *widget*
scroll-aware, which is what our `list_view` already is — and a scroll-aware
widget cannot scroll something it did not author.

## 4. Decisions

### 4.1 The clip belongs to the screen

A `Ref geometry.Region` on `Screen`, initialised to the whole screen, and
`screen_clipped(s, region, act)` to narrow it for the duration of `act`. Writes
outside it are dropped.

This is Brick's answer, and the reason it is affordable here is that `View.render`
is already `Unit !{IO}` over a mutable `Screen`: the clip is a field, not a
parameter, so the widget contract does not change. `Ref` is a prelude builtin
(`prelude.sprout` `ref_new`), so nothing new is needed in the runtime.

The alternative — render the child into an offscreen `Screen` and blit the
window — costs a second full grid per scroll view per frame, and buys nothing
the clip does not.

### 4.2 Nesting narrows and never widens

`screen_clipped` intersects with the clip already in force and restores it when
`act` returns. A child cannot widen its way out to a region its parent excluded,
which is the property that makes the clip a *guarantee* rather than a request.
Restoring in the caller's frame is what makes a stack unnecessary.

### 4.3 The drop happens at the cell, not at the string

Only `set_raw` consults the clip. `screen_put` keeps reporting the columns a
cluster *occupies* whether or not it painted, so a string that starts left of the
clip still advances into it and paints the part that shows. Gating the write at
`screen_put` instead would stop the walk at the first hidden cluster and paint
nothing.

The screen's own left edge had the same bug and is fixed with it: `screen_put`
returned 0 for *any* out-of-bounds column, and `screen_write` stops walking when
a cluster reports 0, so a string starting at a negative column painted nothing at
all. The two edges are not symmetric — past the right edge there is no row left,
while left of column 0 there is — so a cluster before column 0 is now counted and
not painted. Without this a scroll view could not move sideways at all: its child
is shifted into negative columns by construction.

### 4.4 A wide cluster straddling the clip edge becomes a space

The rule `screen.sprout` already applies at the screen's last column — "Wide
runes that are printed in the last column will be replaced with a single width
space on output" (xterm, and tcell after it). A two-column cluster whose right
half is outside the clip would otherwise leave a `Cell` claiming two columns with
no `Continuation` beside it, and the renderer would emit a character that runs
into the neighbour.

### 4.5 Every child render is clipped

`children.render_zip` wraps each child in its own region's clip, so all four
containers get it and every widget in the tree becomes unable to paint outside
its region — the hole `paint.sprout`'s header describes is closed at the source.

`paint.*` stays as it is: it also *truncates text* to the region's width, which
the clip cannot do (a clipped cluster still consumes a column). The two are
complementary, and `paint` remains the shorter way to write a line.

### 4.6 Scrolling is keys while focused

Brick drives scrolling from `EventM`, and we cannot: `Delivery m`'s only
application-facing arm is `ToMsg m`, and `m` is the application's own type, so a
polymorphic widget in the stdlib has no way to recognise a "scroll by 3" it was
sent. Claiming keys while focused is the mechanism `list_view` already uses and
the ring already serves.

`KUp`/`KDown` by `step`, `KPageUp`/`KPageDown` by `page`, `KLeft`/`KRight`
sideways by `step`. `KHome`/`KEnd` are **vertical only** — in a terminal they
read as "the start" and "the end" of the content, and a horizontal jump has no
name a user would guess. Chords fall through, as everywhere else.

### 4.7 A focused view claims its keys whether or not it can move

The first draft of this section said the opposite — that a view whose content
fits should decline, so the application's bindings keep working through it. It is
not implementable: "does the content fit?" compares the extent against the
*viewport*, and a handler is pure and is given no region. Only `render` sees one.

So the rule is `list_view`'s: focused, the view claims its scroll keys, and a
claim that cannot move is silent. An application that wants those keys keeps the
scroll view out of its focus ring.

### 4.8 The far end is the renderer's to enforce

The same wall, in the same place: clamping to "the last screenful" needs the
viewport, so a handler cannot do it. The position on each axis is therefore
`FromTop n | FromBottom k`, and `render` resolves it against the extent it
measures — `KEnd` is `FromBottom 0`, exact without anyone having computed it.

A handler clamps only the near end of whichever anchor is in force, which is the
only end it knows: `FromTop n` cannot go below 0, `FromBottom k` cannot go below
0. The *far* end of each is unbounded, and both directions overshoot — pressing
`KDown` past the bottom grows `n`, and pressing `KUp` past the top under a
`FromBottom` anchor grows `k`. Either way the view is painted correctly and the
next keypress in the other direction does nothing visible until the stored
presses are used up. `KHome` and `KEnd` always land exactly, so recovery is one
key. Filed in `BACKLOG.md` §4; the root cause is the pure-handler contract,
which the effect fork in `docs/tui-widgets-v0.md` would settle.

### 4.9 The extent comes from `Grow`, not from an unbounded measure

`Measured` already reports `Fixed` or `Greedy` per axis. A `Fixed` axis reports
its natural extent, which is exactly the scrollable size; a `Greedy` axis has no
natural extent — it means "whatever I am given" — so that axis does not scroll
and the child is given the viewport. No sentinel "very large" size is measured
against, and Textual's separately-declared virtual size is unnecessary.

This only works because a measurement is now the *unclamped* ask. `box_measure`
and `grid_measure` used to clamp theirs to `avail`, which made every container
child report exactly the viewport and left `scroll_view` with nothing to scroll —
found by code review, since every test here used a single `text.static`, the one
child shape that dodged the clamp. The clamp was redundant: `layout.solve` caps
what an ask is *given*, which is where the two meanings belong apart.

The extent is measured every frame, so a child that shrinks cannot leave the view
parked past its end: the resolution in §4.8 clamps against the size measured
*now*, not against one stored when the key was pressed.

## 5. Surface

```sprout
# stdlib/tui/screen.sprout
export fn screen_clip(s: Screen) -> geometry.Region !{IO}
export fn screen_clipped(s: Screen, r: geometry.Region,
                         act: Unit -> Unit !{IO}) -> Unit !{IO}

# stdlib/tui/widgets/scroll_view.sprout
export type ScrollOpts = (step: Int, page: Int)
export fn scroll_opts() -> ScrollOpts
export fn scroll_view(id: WidgetId, child: Widget m) -> Widget m
export fn scroll_view_with(id: WidgetId, child: Widget m,
                           opts: ScrollOpts) -> Widget m
```

`Screen`'s constructor gains a third field, the clip. It is
`export type Screen (..)`, so that is a visible change; no code outside
`stdlib/tui` builds one, and `screen_new` remains the only way to.

`screen_put` changes behaviour left of column 0 (§4.3): it counts the cluster
instead of reporting 0, so `screen_write` walks on. Off the right edge it still
reports 0 and the walk still stops.

Measures `Greedy` on both axes — a scroll view takes what it is given, which is
the whole point of one. Its natural size is its child's.

## 6. Compatibility

A container's `Measured.size` is no longer clamped to `avail` (§4.9). Layout
results are unchanged — `layout.solve` already clamped every ask it pays — and
the assertion that pinned the old contract is now the opposite one.

Additive apart from `Screen`'s arity. Behaviour changes in one way: a widget that
painted outside its region used to succeed and now does not. That is the fix, and
no widget in the tree does it — `paint.*` was written to prevent exactly this.

## 7. Tests

`tests/stdlib/test_tui_screen.spr` gains the clip cases — a write outside the
clip paints nothing; one straddling the edge keeps its visible half in the column
it was written to; a nested clip narrows and cannot widen; the clip is restored
when the action returns; a wide cluster straddling the edge becomes a space; a
clip of no rows drops everything; a clear inside a clip spares the cells outside
it — and the left-edge case from §4.3: a write starting at a negative column
paints what reaches the screen and reports the column past its last cluster.

`tests/stdlib/test_tui_container.spr` gains the §4.5 guarantee: a child that
deliberately paints one row above the region it was given, *after* its sibling
painted there, cannot reach it.

`tests/stdlib/test_tui_scroll_view.spr`: the window starts at the top and moves
by step, page, `Home` and `End`; a step past the end stays on the last screenful;
the other axis moves and comes back; a claimed key is silent and a view whose
content fits claims one anyway without moving; a chord and an unused key fall
through to a neighbour probe; an unfocused view declines a key addressed to it,
claims a focus notification, and passes an address it does not own to its child.

The container-child cases are the ones code review's finding is filed under: a
`ct.column` of five labels starts at the top, steps, and reaches its last
screenful. Every other case uses a `text.static`, which is exactly why the clamp
went unnoticed.

Three guarantees were checked by mutation, not just by going green: with the clip
removed from `sv_render` the child paints two rows past its viewport, with it
removed from `render_zip` the rude child overwrites its sibling, and without the
left-edge blanking a wide cluster straddling column 0 leaves the old glyph
showing under a column it owns.

## 8. Deferred, filed in `BACKLOG.md` §4

- **Scrollbars**, and the indicator style vocabulary they need.
- **Auto-scroll to the focused child** — Brick's `visible`, which needs the
  retained hit-test tree that focus §2 defers, and is filed with it.
- **Overshoot costs dead presses** (§4.8), because a pure handler cannot see the
  far end.
