# TUI M4 — the widget set, slice C2b: the text field

> Status: **implemented**. Non-normative; `docs/spec-v0.md` is unaffected. The
> slice's other widget is `docs/tui-list-view-v0.md`; focus is
> `docs/tui-focus-v0.md`, the widget model `docs/tui-widgets-v0.md`, addressed
> delivery `docs/tui-routing-v0.md`.

## 1. Problem

C2a settled who the keyboard belongs to and shipped one widget that uses it.
`button` claims two keys and declines everything else, so it never exercises the
rules focus was built for: a widget that consumes almost every key, that has a
value the application needs, and that must still leave `Tab` and the
application's chords alone.

## 2. Goals and non-goals

Goals: type, delete and move a caret in one line of text; the value reaches the
application; the caret is visible; a line longer than its region stays usable.

Non-goals: multi-line editing (`text_area`, C3, `docs/tui-text-area-v0.md` —
pure like this one, and needing no `MutVec`); selection and clipboard;
word-motion and the `ctrl-w`/`ctrl-u` chord family — every one of those is a
binding an application may want, and a field that claimed them would take them
away with no way to opt out; validation and masking; undo.

## 3. Prior art

Every claim below is from the named primary source.

| | value storage | how the value leaves | focus in render |
|---|---|---|---|
| **Brick** (`Brick.Widgets.Edit`, brick-2.13) | `editContentsL :: … (TextZipper t1 → f (TextZipper t2)) → …` — a **zipper** | nothing is emitted; the application reads the editor out of its own state | `renderEditor :: … → Bool → Editor t n → Widget n`, and it "will report a cursor position if and only if it has focus" |
| **Textual** (Guide → Input) | widget-owned | messages: `Changed` ("Posted when the value changes"), `Submitted` ("Posted when the enter key is pressed within an Input"), `Blurred` | widget-owned `has_focus` |
| **bubbles** (`textinput`) | widget-owned `value` + `focus bool` | the parent reads the model field | `Update` opens with `if !m.focus { return m, nil }` |

Brick's is the only one that can decline to emit, because it is the only one
where the application can *read* the widget. Ours cannot: `Widget m` erases the
state type, so Textual's shape is not a preference here.

## 4. Decisions

### 4.1 The line is a cluster zipper, not a string and an index

`docs/tui-focus-v0.md` §7 said a caret edit was `string.take` / `string.drop`.
That was wrong, and the reason is a split this repo already made deliberately:
`stdlib.string` counts **codepoints** (`length` is `str_len`), while every
measurement a terminal needs is in **grapheme clusters** — `stdlib/tui/text.sprout`
opens by saying so, because `screen.sprout` places clusters and this module
measures them.

A codepoint index can therefore name a position *inside* a cluster, where the
caret has no column at all. So the state is

```sprout
type Zipper = (before: List (List Int), after: List (List Int))
```

with `before` reversed. A caret is a position *between* clusters, which cannot
be invalid, and both edits at the caret are head operations rather than an O(n)
string copy per keystroke. Brick reaches the same shape from the same problem.

C3b moved this type out to `stdlib/tui/line_zipper.sprout`, unchanged apart from
also blanking U+2028/U+2029, so `text_area` shares one line implementation with
this widget (`docs/tui-text-area-v0.md` §4.2). `input`'s public surface and
behaviour are otherwise the same.

**Insertion re-segments the join.** `keys.sprout:84` emits one `KChar` per
codepoint, so a combining mark arrives as its own key event: appending it as a
new cluster would leave a caret stop inside a character. The last existing
cluster is re-segmented together with what is inserted — no grapheme boundary
rule reaches further back than one cluster, so this is both correct and O(1).

### 4.2 What leaves the widget is the whole value

`on_change: String -> m` fires on every edit, carrying the entire line. A delta
would be smaller and useless: the application cannot read the field, so it would
be left rebuilding the line from keystrokes it did not see.

Caret motion is not an edit and says nothing. Neither is the `initial` value —
construction is not an edit, and announcing it would make every field speak
before the application had drawn a frame.

### 4.3 Enter belongs to the application unless it is asked for

`on_submit` is a `Maybe`. Absent, `KEnter` is declined and falls through to the
application's global bindings, which is what `route_when` exists for
(`docs/tui-routing-v0.md` §3.8) — a form with one field usually wants Enter to
mean *submit the form*, not *submit this field*. Present, it fires with the
value, as Textual's `Submitted` does.

### 4.4 A chord is not typing

`keys.sprout:63` decodes any byte below 32 as `KeyPress(KChar(b + 96), mods_ctrl())`.
**Ctrl-A arrives as `KChar 'a'`.** A field that inserted on any `KChar` would
type "a" *and* swallow the application's chord. Insertion therefore requires
ctrl and alt both clear; shift is excluded from that test, since shift is part
of producing the character. `event.mods_is_chord` names the rule once.

`button` had the same defect in its milder form — alt-Space pressed it — and is
fixed with the same predicate.

### 4.5 The visible window is a function of the caret

A scroll offset kept in state can drift out of step with the caret, and `render`
is `!{IO}` and pure in its state, so it could not correct one anyway. Instead
the window is derived: drop leading clusters until what precedes the caret fits
in the region, leaving the caret on the last column. Nothing to desynchronise,
and `Home` brings the start back with no state to reset.

An unfocused field has no caret to follow and shows the start of its value.

The window reserves the caret cluster's **own width**, not one column. A CJK
ideograph or emoji is two columns, and `text.truncate` drops a wide cluster
whole rather than halving it — so a single reserved column paints neither the
character nor the focus, and the caret silently disappears exactly when it sits
on one. Reserving `cluster_width` scrolls the window one further instead.

### 4.6 The caret is a painted cell

`app.run` hides the terminal's cursor for the session, so the caret is one cell
in the focused style — the cluster it sits on, or a blank at the end of the
line. This is what needs `paint.at`: painting *part* of a line at a column
offset, clipped to the region, which every widget with an internal highlight
will want and which `paint.line` could not express.

Real cursor placement stays deferred (`docs/tui-focus-v0.md` §9).

### 4.7 Options go in a record

Sprout has no default arguments, and `input(id, initial, on_change, normal, focused)`
is four positional slots two of which are the same type. `InputOpts` is built by
`input_opts()` and overridden with `with`, so every call site names what it
changes. Same argument as `ButtonStyle` in C2a — which this change generalises
into `focus.FocusStyle`, since the normal/focused pair belongs to focus rather
than to any one widget.

### 4.8 A single line cannot hold a control character

Text enters the line at two points — `initial` and every insertion — and both
flatten Unicode's Cc category (U+0000–U+001F, U+007F–U+009F) to a space, so the
zipper never holds one. The C1 half is not optional: U+0085 is a line
terminator to a terminal, and `codepoint_width` gives it an ordinary cell.

This is not tidiness. `cluster_width` reports 1 for U+000A, `screen` stores it
as an ordinary cell, and `diff_to_ansi` emits cell text in runs with no
per-cell cursor move — so a pasted newline is written to the terminal verbatim,
the real cursor drops a row, every later cell of that run lands on the wrong
one, and `commit_rows` then marks them clean, so nothing repaints the damage.

Replaced rather than dropped, as bubbles' `textinput` does
(`runeutil.NewSanitizer(ReplaceTabs(" "), ReplaceNewlines(" "))`): losing the
rest of a paste is worse than flattening it, and the user can see what arrived.

## 5. Surface

```sprout
# stdlib/tui/widgets/focus.sprout
export type FocusStyle = (normal: Style, focused: Style)
export fn focus_style_default() -> FocusStyle          # reverse video when focused

# stdlib/tui/widgets/input.sprout
export type InputOpts m = (initial: String, on_submit: Maybe (String -> m),
                           look: FocusStyle)
export fn input_opts() -> InputOpts m
export fn input(id: WidgetId, on_change: String -> m) -> Widget m
export fn input_with(id: WidgetId, on_change: String -> m,
                     opts: InputOpts m) -> Widget m

# stdlib/tui/widgets/paint.sprout
export fn at(screen: Screen, region: Region, row: Int, col: Int,
             content: String, style: Style) -> Unit !{IO}

# stdlib/tui/text.sprout — both were private
export fn from_clusters(cs: List (List Int)) -> String
export fn clusters_width(cs: List (List Int)) -> Int
```

Keys claimed while focused: printable `KChar` (unmodified), `KBackspace`,
`KDelete`, `KLeft`, `KRight`, `KHome`, `KEnd`, `Paste`, and `KEnter` only with
an `on_submit`. Everything else declines, `KTab` included — §4.5 of the focus
design is what moves focus, not the field's silence.

Measures `Greedy` on columns and `Fixed` at one row.

## 6. Compatibility

Additive except `button.ButtonStyle`, which becomes `focus.FocusStyle`; two
in-tree references. `button` now declines a modified Enter or Space, which is a
behaviour change and is the point of §4.4.

## 7. `list_view`

The slice's other widget, designed and implemented in
**`docs/tui-list-view-v0.md`**. It takes §4.5's derived window and inverts
§4.2: what leaves a list is an *index*, because the application supplied the
items and a line cannot name a row when two are identical.

## 8. Tests

`tests/stdlib/test_tui_input.spr`, written failing first against a stub that
claims nothing — 24 of the first 31 red.

Editing: each character says the new value; backspace and delete either side of
the caret; caret motion says nothing; typing at a moved caret inserts there;
`Home`/`End`; a paste arrives whole; an initial value is unannounced. Backspace
at the start, delete at the end and `Right` at the end are *claimed and silent*
— a neighbour probe sits beside the field so "declined" and "said nothing" are
different transcripts.

`Right` is asserted as `Left`-then-`Right`-then-type, since with the function a
no-op every other motion case still passes: at the end of a line, moving left
and typing puts the character in the same place either way.

Declining: a ctrl chord and an alt chord both reach the neighbour; `Tab` is the
ring's; Enter falls through without an `on_submit` and submits with one; an
unfocused field ignores a key, declines one addressed to it directly, claims a
focus notification, and declines a message.

Clusters: a combining mark joins the character before it; backspace removes the
whole cluster; one `Left` steps over a cluster rather than a codepoint; a pasted
cluster is one caret stop.

Painting: the value; the caret cell after the last character, on the character
it sits on, and at the start of an empty field; no caret when unfocused; a value
wider than the region shows the caret's end, and `Home` brings the start back; a
region of one column holds the caret alone and one of *zero* columns terminates
and paints nothing; a supplied `look` paints the caret and the default does not;
a caret on a double-width cluster is painted, the window scrolling to make room,
and in a one-column region it paints nothing, since half a character is not one.

Control characters: a pasted newline and tab become spaces, a C1 control does
too, and an `initial` value is flattened the same way (§4.8). Each was checked
against the unflattened code, since a control character is invisible in an
assertion's own output — the C1 case reads as `Changed(ab)` when it fails.

## 9. Deferred, filed in `BACKLOG.md` §4

- **Word motion and the chord family** (`ctrl-w`, `ctrl-u`, ctrl-arrows), §2.
- **Selection and clipboard**, §2 — needs a selection anchor beside the caret
  and a terminal clipboard protocol.
- **A cluster join across a deletion.** Removing the cluster between two
  neighbours that would themselves combine leaves them as two clusters. Brick's
  zipper has the same behaviour; reaching it requires deleting from between a
  base character and a mark.
