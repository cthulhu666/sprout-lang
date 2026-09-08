# TUI M4 — the widget set, slice C2: focus and the interactive set

> Status: **C2a implemented** (`ToFocus`, `focus_ring`, `button`); C2b is
> `docs/tui-input-v0.md` — `input` implemented, `list_view` designed.
> Non-normative;
> `docs/spec-v0.md` is unaffected. The widget *model* is
> `docs/tui-widgets-v0.md`, addressed delivery is `docs/tui-routing-v0.md`, and
> the containers this builds on are `docs/tui-widget-set-v0.md`.

## 1. Problem

`Delivery` has carried a `ToEvent` since M4 routing, and nothing constructs one.
`app.step` hands every event to `widget.on_event`, which broadcasts to the whole
tree, so every widget sees every keystroke and none of them owns it.

That is what blocks the interactive set. Two `input`s on one screen would both
consume the same character; a `button` would fire on an Enter meant for its
neighbour. The widgets are not the hard part — deciding *who the keyboard
belongs to* is, and nothing in the framework can express it yet.

## 2. Goals and non-goals

Goals: exactly one widget receives keyboard input at a time; `Tab`/`Shift-Tab`
move between the widgets an application nominates; a widget knows whether it is
focused, so it can paint a caret or a highlight; a key the focused widget does
not use still reaches the application's global bindings.

Non-goals for C2: click-to-focus — containers discard the solved region list
after painting, so there is no hit-test tree to ask, and building one is its own
slice. Real terminal-cursor placement (`app.run` hides the cursor; a focused
widget paints its own caret cell). Focus *trapping*, i.e. a widget that consumes
`Tab` itself — §4.5 takes the opposite rule deliberately. Nested rings.

## 3. Prior art

Every claim below is from the named primary source.

| | where focus lives | how the widget learns it is focused |
|---|---|---|
| **Brick** (`Brick.Focus`, brick-2.13) | application state: `focusRing :: [n] -> FocusRing n`, with `focusNext`/`focusPrev`/`focusGetCurrent :: FocusRing n -> Maybe n` | `withFocusRing :: (Eq n, Named a n) => FocusRing n -> (Bool -> a -> b) -> a -> b` — a `Bool` passed to the *rendering* function |
| **bubbles** (`textinput`, Bubble Tea) | inside the widget: a `focus bool` field on the model | the parent calls `Focus()` / `Blur()`; `Update` opens with `if !m.focus { return m, nil }` |
| **Textual** (Guide → Input) | one focused widget per screen; `can_focus` marks eligibility | the widget receives a `Focus` event on gaining and a `Blur` event on losing it; `Tab` / `Shift+Tab` move |

Two established shapes: focus **passed to render** (Brick), or focus **stored in
the widget and set by notification** (bubbles' method call, Textual's event).

All three take the ring's membership and order as an **explicit list of names**,
not derived from the widget tree.

## 4. Decisions

### 4.1 The widget stores its own focus, told by an addressed notification

Brick's shape is not available here, and the reason is structural rather than a
preference. `render` is

```sprout
render: s -> geometry.Region -> screen.Screen -> Unit !{IO}
```

with no place for a `Bool`, and `Widget m` is `exists s. Widget (View s m)` — the
state type is erased, so nothing outside a widget can reach in and hand it
anything. Brick can do it because `Named a n` keeps the widget's own type visible
to the caller; Sprout's existential is what makes a heterogeneous `List (Widget m)`
possible in the first place, and this is its price.

So the widget holds a `has_focus: Bool` and is told when it changes, as bubbles
and Textual both do.

### 4.2 The notification is a `Delivery`, not an `Event`

```sprout
export type Delivery m (..) =
  | ToMsg m
  | ToEvent event.Event
  | ToFocus Bool
```

The cheaper-looking alternative is a `FocusGained`/`FocusLost` variant on
`event.Event`, delivered through the existing `ToEvent`. It needs no new match
arm anywhere. It is still wrong: `event.Event` is also `on_event`'s broadcast
vocabulary, so that spelling makes "everyone gains focus" a representable value.
Because `s` is erased, a broadcast recipient cannot tell whether a signal was
meant for it — so *any* focus signal that can travel by broadcast is unusable by
construction, not merely untidy. The same argument §3.2 of the routing design
used against the decline-flag: keep the bad state out of the type.

A focus change is addressed to exactly one widget, always. `Delivery` is the
addressed channel.

Cost is one arm in `map_msgs`'s `narrow`, where it passes through untouched like
`ToEvent` — a focus change carries no message, so the prism's backward half does
not apply to it.

### 4.3 The ring is a widget that holds the tree

```sprout
export fn focus_ring(ids: List WidgetId, child: Widget m) -> Widget m
```

Focus changes over time, and `App` has nowhere to keep it. Its `update` is

```sprout
update: m -> Widget m -> (Widget m, Flow, List (Cmd m))
```

which would have to return a focus as well — breaking every application, and
handing every application a job the framework can do. As a wrapper the ring
costs nothing, nests, and composes with `namespaced` and `map_msgs` like any
other widget. It forwards `route`, `render` and `measure` to its child
unchanged, so wrapping the root leaves addressed delivery exactly as it was.

`ids` is explicit, as in all three prior-art systems, and here it could not be
otherwise: the framework cannot see through `Widget m` to discover whether a
child is focusable or even that it exists.

### 4.4 Keyboard input goes to the focused widget first and falls through

The ring's `on_event`, for a key, tries `deliver(child, focused, ToEvent(ev))`
and broadcasts only if that answers `Nothing`. This is the whole reason
`route_when` was landed before this slice (`docs/tui-routing-v0.md` §3.8): under
`route_if` a focused widget claims every key whether or not it used one, and the
application's global bindings would never fire again.

**Keyboard input is focused; everything else is broadcast.** `KeyPress` and
`Paste` go to the focused widget first; `TickEvent`, `ResizeEvent` and the mouse
events broadcast as they do today. A tick is not addressed to anybody, and the
mouse belongs to whatever is under the pointer — which is the hit-testing §2
defers.

### 4.5 The ring interprets `Tab` before the focused widget sees it

The alternative — offer the key to the focused widget first, and treat `Tab` as
a ring binding only when it declines — is more flexible and can wedge the
application: a widget that claims every key traps focus with no way out. C2 takes
the safe rule, so `Tab` and `Shift-Tab` (`KTab`, `KBackTab`) always move focus.
A widget that genuinely wants a literal tab needs focus trapping, which is a
deferred feature with an explicit opt-in, not a silent consequence of a handler
being greedy.

### 4.6 A stale or unfocusable id makes keys fall through, and is not an error

The ring moves `at` to the next id in its list whether or not the corresponding
widget claims the `ToFocus`. An id naming nothing in the tree therefore parks
focus on nobody, and keys broadcast until the next `Tab`. The list is the
author's claim about the tree, and the framework cannot check it — the
existential again. Failing loudly would mean crashing an application over a
typo'd name in a list the compiler has no way to verify; falling through is the
direction that keeps the application usable and the bug visible.

Two sizes fall out of the same rule. An **empty** ring focuses nothing and has
nowhere to send focus, so it does not take `Tab` either — `focus_ring([], w)` is
the identity on input, ordinary keys and `Tab` alike, which is exactly M3's
behaviour. Swallowing `Tab` there would cost an application its global binding
whenever the ring it computes from its model happened to come out empty. A ring
of **one** wraps to the widget already focused: the key is still the ring's, but
nothing is re-announced, because blurring a widget only to focus it again would
reset one that commits on blur.

### 4.7 The ring is the only thing that sets focus inside itself

`route` forwards every delivery to the child except `ToFocus`, which it
declines. An outside focus notification — from application code calling
`deliver`, or from an enclosing ring — would light a second widget while `at`
still named the first, leaving two widgets that each believe they hold the
keyboard and a `Tab` that blurs the wrong one. §4.2 says a focus change is
addressed to exactly one widget; this is what holds that at the ring's boundary.
Nested rings are a non-goal (§2), so declining is the whole rule.

## 5. Surface (C2a)

```sprout
# stdlib/tui/widget.sprout
export type Delivery m (..) = ToMsg m | ToEvent event.Event | ToFocus Bool

# stdlib/tui/widgets/focus.sprout
# Focus starts on the head of `ids`, so an application is keyboard-usable
# without a first Tab.
export fn focus_ring(ids: List WidgetId, child: Widget m) -> Widget m

# The same, starting somewhere else — restoring a remembered focus. An id not
# in `ids` starts the ring focused on nothing, per §4.6.
export fn focus_ring_at(ids: List WidgetId, start: WidgetId,
                        child: Widget m) -> Widget m

# stdlib/tui/widgets/focus.sprout — the normal/focused pair. Two bare `Style`
# arguments would swap silently, inverting which state looks focused; a record
# makes that a compile error, per guidelines §7's multi-field case. C2b moved
# it here from `button`, since every focusable widget wants it.
export type FocusStyle = (normal: Style, focused: Style)
export fn focus_style_default() -> FocusStyle

# stdlib/tui/widgets/button.sprout
# Fires `on_press` on Enter or Space while focused and unmodified, and claims
# nothing else — so an unused key still reaches the application.
export fn button(id: WidgetId, label: String, on_press: m) -> Widget m
export fn button_styled(id: WidgetId, look: FocusStyle, label: String,
                        on_press: m) -> Widget m
```

`button` renders as `[ label ]`, reverse-video when focused, `Fixed` on both
axes.

## 6. Type-system, error-message and compatibility impact

No syntax, typing-rule or evaluation-order change; `docs/spec-v0.md` is
untouched. Everything is additive except the `Delivery` variant, which makes
existing exhaustive matches on it fail to compile — loudly, at every site, which
is the point. In-tree that is three files and four match sites.

`route_if`, `route_when`, `map_msgs`, `namespaced`, `deliver` and every C1
container are unchanged.

## 7. C2b

`input` and `list_view` are plain consumers of the contract above: state a
`focused: Bool` plus their own, `route_when` on their id, claim the keys they
use and decline the rest. Designed in **`docs/tui-input-v0.md`**.

This section previously said a caret edit was `string.take` / `string.drop`.
That was wrong — those count codepoints, and a caret between them can land
inside a grapheme cluster, where it has no column. See that document §4.1. The
conclusion it was supporting still holds: no builtin and no language work. The
`mutvec_insert` / `mutvec_remove` question in `BACKLOG.md` §4 belongs to C3's
`text_area`, which edits many lines, and is not reached by this slice.

## 8. Tests

Written failing first, against `focus_ring` stubbed to focus nothing and
`button` stubbed to fire unconditionally.

Focus dispatch: a key reaches only the focused widget; a key the focused widget
declines falls through to broadcast; a non-key event broadcasts without
consulting focus, the mouse included, since hit-testing is a non-goal; an empty
ring broadcasts everything, `Tab` included (§4.6);
a ring of one leaves its widget alone on `Tab`; a `ToFocus` delivered past the
ring is declined (§4.7).

Focus movement: `Tab` advances and wraps at the end; `Shift-Tab` retreats and
wraps at the start — over a ring of **three**, since a pair cannot tell the two
directions apart and the assertion would pass on a forward walk; a move blurs the old widget and focuses the new one, in that
order; `focus_ring_at` starts where it is told; an id naming nothing parks focus
on nobody and keys fall through (§4.6).

Pass-through: an addressed `ToMsg` still reaches a child through the ring;
`measure` and `render` are the child's.

`button`: fires on Enter and on Space while focused; fires on neither while
unfocused; declines `Tab`, so §4.5's rule is what moves focus rather than the
button's silence; claims a `ToFocus` and changes what it paints;
`button_styled` paints the style it was handed and not the default.

## 9. Deferred, filed in `BACKLOG.md` §4 with the change

- **Click-to-focus**, which needs a retained hit-test tree (§2).
- **Terminal cursor placement.** Brick has `focusRingCursor` feeding
  `appChooseCursor`; `app.run` currently hides the cursor for the whole session,
  so a focused `input` paints its own caret cell instead. Cosmetic until an
  application wants the terminal's own cursor shape.
- **Focus trapping**, the opt-in §4.5 declines to make implicit.
- **`can_focus`-style eligibility.** Textual has it; here the ring's `ids` list
  is the eligibility statement, and a second mechanism would only be needed if
  something wanted to disable a widget without editing the list.
