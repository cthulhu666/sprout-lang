# TUI M4 — the widget set, slice C3b: the text area

> Status: **IMPLEMENTED.** Revision 2 (2026-09-09). Non-normative;
> `docs/spec-v0.md` is unaffected. The single-line sibling is
> `docs/tui-input-v0.md`, focus is `docs/tui-focus-v0.md`, the widget model
> `docs/tui-widgets-v0.md`, the container set `docs/tui-widget-set-v0.md`.

## 1. Problem

C2b shipped one line of editable text. Every larger widget in C3 — `tabs`,
`tree`, `table` — arranges content that already exists; `text_area` is the only
one that *creates* it, and the only widget whose state grows without bound.

Three places in the tree said it was blocked on `mutvec_insert`/`mutvec_remove`
(`docs/tui-input-v0.md` §2, `BACKLOG.md` §4, the M4 section of the TUI/IDE
plan). **All three were wrong**, and this change corrects them.
`View.on_event` and `View.route` are pure and every `mutvec_*` operation is
`!{IO}`, so a widget cannot hold a `MutVec` at all. A pure `text_area` needs
none of them; nothing gated this slice.

The same reasoning reaches further than this widget: `app.sprout`'s `update` is
pure too, so an IDE model holding a `MutVec String` of lines — what the plan's
M5 specified — has nowhere to call a mutating operation from either. The plan
now names `stdlib.tui.buffer` instead.

## 2. Goals and non-goals

Goals: type, delete and move a caret through many lines; Enter and Backspace
join and split them; the document reaches the application; the caret stays
visible on both axes; the buffer is reusable by a future IDE editor pane.

Non-goals: soft wrap (§4.5); selection and clipboard (§4.9); undo; syntax
highlighting, a gutter and line numbers; word motion and the chord family,
deferred for the same reason `input` deferred them — each is a binding an
application may want, and a widget that claimed it would give no way to opt out.

**Which consumer this serves.** A multi-line *input* — a commit message, a note,
a composer — not a code editor. The IDE's editor pane is a **sibling** widget,
not a client of this one: it must own its own `render` for syntax highlighting
and a gutter, and it must be able to read its buffer to feed the compiler, which
the existential forbids. What the two share is everything under `render`: this
buffer, its cluster safety, its paste rules and its goal column. That is why the
buffer is its own module (§6) rather than a private type inside the widget.

## 3. Prior art

Every row verified 2026-09-09 against the library's own source or reference.

| | buffer | caret between lines | how the window follows the caret |
|---|---|---|---|
| **brick** 2.13 (`Data.Text.Zipper`, `Brick.Widgets.Edit`) | `TZ { toLeft :: a, toRight :: a, above :: [a], below :: [a], … }` | no goal column — `moveUp`/`moveDown` clamp to the target line's end | `viewport … Both $ showCursor … $ visibleRegion cursorLoc (atCharWidth, 1) $ draw …` — the child marks a region, the viewport honours it |
| **Textual** (`TextArea`) | a `Document`, plus a `wrapped_document`; caret is a `Location` = `(row, column)` | — | `class TextArea(ScrollView)`; `scroll_cursor_visible()`; the offset lives in the base class |
| **bubbles** (`textarea`) | `value [][]rune` with `row`/`col` | `lastCharOffset`, *"used to maintain state when the cursor is moved vertically such that we can maintain the same navigating position"*, reset on any horizontal move | stores an offset: `repositionView()` calls `ScrollUp`/`ScrollDown` when the cursor leaves the viewport |

Three things follow.

**The zipper is the only caret model that cannot be invalidated.** Textual and
bubbles index into a mutable buffer, which is why bubbles needs a `LineInfo`
splitting `ColumnOffset` from `CharOffset`: an index can land between a base
character and its combining mark. A gap in a cluster list cannot.

**Nobody derives the window from the caret; all three store an offset and nudge
it.** Each nudge compares the caret against the *viewport* — `viewport.Height()`,
the widget's size, the region the viewport resolves at render. §4.6 explains why
none of that is reachable here.

**Two of the three soft-wrap, and both pay a coordinate space for it.** Textual
ships `TextArea.code_editor()` specifically to turn it off.

## 4. Decisions

### 4.1 The buffer is a two-dimensional cluster zipper

`(above: List String, line: Zipper, below: List String)`, `above` reversed so
both directions are head operations, `Zipper` splitting the current line between
clusters as `input` already does.

`Vec String` + `(row, col)` is legal in a pure handler — `vec_get` is pure and
O(1) — but `vector_set` allocates a fresh backing array and `memcpy`s the whole
spine, so an edit costs an allocation and O(lines) per keystroke against the
zipper's one cons. It also reintroduces the between-clusters caret. A mutable
container fixes the copy and nothing else: it wins only on random access, which
nothing does per keystroke, and a handle inside a value-semantics box means the
old and new widget alias one buffer.

Reading line *n* is a walk. That is affordable because the only lines anyone
renders are the ones around the caret (§4.6), and a future jump-to-line re-roots
the zipper once per jump rather than once per keystroke.

### 4.2 The line zipper is shared with `input`

`rejoined`/`regrown` — re-segmenting the last cluster when a combining mark
arrives as its own key event — is subtle enough that two copies will drift. It
moves to `stdlib/tui/line_zipper.sprout` and `input` is converted in this slice,
with every existing assertion in `tests/stdlib/test_tui_input.spr` left as
written — the proof that the extraction preserved behaviour.

The seam is the flatten: `input` blanks U+000A, `text_area` splits on it. It
resolves without a parameter, which revision 1 expected to need — `buffer`
normalises CRLF and lone CR to LF and splits *before* flattening, so every
segment reaching the zipper already holds no terminator and one unparameterised
`flatten` serves both widgets. Only visible with both consumers in front of
you, which is why the extraction happens here rather than ahead of time.

### 4.3 Enter breaks the line

`input` lets Enter fall through unless `on_submit` is set. A text area inverts
that unconditionally, with no submit option, because `keys.sprout:58` decodes
`b0 == 13 || b0 == 10` to `plain(event.KEnter, …)`: Shift+Enter and Enter are
the same event under the legacy encoding, so a submitting text area would have
no key left to type a newline with. Neither multi-line prior art has a submit
mode. Adding `on_submit` to the opts record later is additive, and the kitty
keyboard protocol (`BACKLOG.md` §4) is what would make it usable.

### 4.4 A paste splits on line terminators, and blanks the rest

Split on LF, CRLF and lone CR. Blank every other Cc, and U+0085 NEL and
U+2028/U+2029 with them.

CRLF is the one that must be right on day one: blanking CR as a control
character puts a trailing space on every line of every Windows paste. Lone CR
costs three lines and closes the set. NEL and LS/PS are blanked rather than
split so a paste cannot sprout lines the user never typed.

**This changes `input`.** U+2028/U+2029 are Zl/Zp, not Cc, so `input.blanked`
passes them through today and paints them as one cell each — a gamble on a
character whose Unicode purpose is line separation. The shared flatten blanks
them for both widgets, with its own regression test.

### 4.5 No soft wrap, and positions are document space

A wrapping widget's height depends on its width, and `container.sprout:95`
measures every child against the whole region before `layout.row` divides it, so
in a row the answer would be computed at the wrong width. `Measured` carries one
size and cannot say otherwise.

The part that costs something later is the coordinate model, not the wrap.
**Rule: any position crossing the widget boundary is `(logical line, cluster
column)` — never a screen row or column.** Nothing crosses today (`on_change`
carries a `String`), so this is vacuous now and load-bearing the moment §4.10's
control vocabulary or M6's spans arrive. With it, wrap stays an internal render
concern plus a revised Up/Down.

### 4.6 The window is derived from the caret, in `render`

Both axes, generalising `input` §4.5: drop lines and clusters until what
precedes the caret fits. No stored offset, so nothing can drift out of step with
the caret.

The window lives in `stdlib.tui.widgets.viewport`, not in this widget: the IDE's
editor pane paints the same document beside a gutter (`docs/ide-v0.md` §5.1) and
is a sibling of the area rather than a client of it, so both paint through one
implementation.

Every visible line shifts by the same column offset, and a wide cluster the
offset cuts in half is **blanked, not dropped** — dropping it slides the rest of
that line one column left of every other line. Single-line `input` cannot hit
this: with nothing to align against, a partial cluster at the left edge does not
arise.

This has no prior art (§3) and is a degradation, not a preference. All three
references compare the caret against the viewport; our handler is pure and is
given no region, and `render` receives the region but returns `Unit`, so an
offset could be held but never correctly updated. That is exactly the shape of
`scroll_view`'s overshoot bug. Composing with `scroll_view` fails twice over
anyway: `sv_plain` claims Up/Down/Left/Right/Home/End, the caret's whole
vocabulary, and the mechanism brick uses to make the composition work — which
`docs/tui-scroll-view-v0.md` §2 defers under its other name, `visible` — is not
there to compose with.

What it gives up is scrolling away from the caret to look elsewhere. A later
hybrid — a stored offset that `render` clamps to keep the caret visible — is an
internal state change the existential hides.

`measure` stays cheap: `Greedy` on both axes with the available size as its
natural size. It runs on every frame for a `fit` child
(`box_render` → `regions_of` → `dim_of` → `widget.measure`), so `widest ×
line_count` there would be a document walk per frame. `input` gets away with
remeasuring because it is one line.

### 4.7 Vertical motion keeps a goal column

A `goal: Maybe Int` in **display columns**, resolved to the nearest cluster
boundary at or before it on the target line. Set on the first vertical move,
cleared by any edit or horizontal move.

Clamping instead — brick's behaviour, and this document's first draft — breaks
on the *empty* line rather than the short one: Down through one blank line
clamps to column 0 and every subsequent Down continues from there, so vertical
traversal collapses within a few keystrokes. Blank lines are everywhere.
bubbles stores the goal as a character offset; display columns keep the caret
visually aligned where a cluster is two cells wide, which is what the painted
caret occupies.

### 4.8 The whole document leaves on every edit

`on_change: String -> m`, lines joined with `\n`, **no trailing terminator**, an
empty buffer is `""`.

Textual posts a `Changed` carrying a reference the handler reads back; brick
emits nothing and the application owns the editor. `Widget m` erases the state,
so neither is available and something must leave on every keystroke. Every
application mirrors this value, so the join is pinned here: a trailing-newline
ambiguity becomes N incompatible mirrors.

`List String` would be cheaper — O(lines) conses sharing the existing strings,
against O(bytes) for the join — and was rejected for consistency with `input`
and because every simple consumer would have to join it back. At the scale this
widget targets (§2) the difference does not signify.

### 4.9 The buffer type is opaque

`Buffer` is a single-constructor ADT declared **without `(..)`** around a
private record. Verified 2026-09-09: a sum without the marker hides its
constructors (`inner.A` → `Unknown variable`), while a record is unconditionally
transparent — both `r.x` and `inner.Rec(x = 3, y = 4)` type-check across a
module boundary whatever the declaration says.

The point is selection. An `anchor` field is not shipped — there is no clipboard
and no selection behaviour — but typing-replaces-selection rewrites every
editing arm, and the representation must not be the thing standing in the way.
Opacity buys that without shipping a field that is always `Nothing`.

That `(..)` is honoured on sums and silently ignored on records is a language
gap, filed in `BACKLOG.md` §1. The wrapper is the workaround for it, not an
endorsement.

### 4.10 A control vocabulary is named, not built

No application can tell any stdlib widget to do anything: every leaf answers
`ToMsg _ -> Nothing` and the containers only forward it (`focus.sprout:158`),
because `ToMsg m` carries the application's type and a widget generic in `m`
cannot inspect it. `text_area` has no live need — `opts.initial` covers
construction and §4.3 leaves nothing to clear — but M6's
jump-to-a-diagnostic needs "put the caret on line *n*", and the mechanism chosen
by the first widget to need one becomes the convention.

**The intended shape is an opts-supplied prism**, `ctl: Maybe (m -> Maybe
TextAreaCtl)`: the application supplies the decoder because only it knows its
own constructors. `widget.map_msgs(f: m -> n, unf: n -> Maybe m, …)` already
carries the same backward half for embedding, and `narrow` already lets
`ToEvent`/`ToFocus` cross untouched while only `ToMsg` consults `unf`. Recorded
here so the first real consumer extends one widget's opts rather than adding a
`Delivery` arm that touches every `View` in the tree.

### 4.11 Tab belongs to the focus ring

The key declines, as `input` does — Textual's `tab_behavior` defaults to
`"focus"` for the same reason. A tab character in a paste is blanked to one
space like every other Cc, which loses the indentation of pasted code; expanding
to tab stops is additive later and needs column arithmetic in the paste path.

## 5. Surface

```sprout
export type TextAreaOpts m = (initial: String, look: focus.FocusStyle,
                              on_content: Maybe (m -> Maybe (String,
                                                             Maybe buffer.Caret)))

export fn text_area_opts() -> TextAreaOpts m

export fn text_area(id: widget.WidgetId,
                    on_change: String -> m) -> widget.Widget m

export fn text_area_with(id: widget.WidgetId, on_change: String -> m,
                         opts: TextAreaOpts m) -> widget.Widget m
```

`on_content` replaces the document — opening a file into the pane. The
`Maybe buffer.Caret` is the caret to restore, since a caret read out of one
document means nothing in another and only the caller knows where it belongs;
`Nothing` opens at the end, as `buffer_open` does, and a named one is clamped
by `buffer_goto`. The replacement is **silent**, for the reason
`docs/tui-content-update-v0.md` §9.1 gives: `on_change` carries the text, which
the application just sent. **Unless opening normalised it** — §4.4's rules turn
tabs into spaces and fold CRLF to LF — because then the document is one the
application has never seen. Reopening a source file is exactly where that bites.

Keys claimed when focused and unchorded: printable characters, Enter,
Backspace, Delete, Left, Right, Up, Down, Home, End, and `Paste`. Everything
else declines — Tab, Esc, the function keys, PageUp/PageDown (paging needs the
viewport, the same wall as §4.6) and every chord.

`stdlib/tui/buffer.sprout` exports `Buffer` opaquely with construction, the
editing operations, the caret queries (`buffer_row`, `buffer_col`,
`buffer_line`), `buffer_text`, and the `Caret` pair `buffer_caret`/`buffer_goto`
— a record rather than two adjacent `Int`s, which swap silently and would place
a plausible caret in the wrong spot since both axes clamp. `buffer_goto` clamps
both axes, over `line_zipper.zipper_at` for the column.
`zipper_at` walks in CLUSTERS, the unit `zipper_col` reports;
`zipper_to_display_col` beside it is the same walk in cells, which is what a
goal column needs and what a restored caret must not use. `buffer_line` keeps
the caret query off the document walk — it reads the current line directly
rather than indexing
`buffer_lines`. `render` still calls `buffer_lines`, so it is O(document) per
frame where O(region rows) is reachable; filed in `BACKLOG.md` §4 rather than
guessed at, since the shape depends on what the IDE pane needs.
`line_zipper.sprout` exports `Zipper` and the single-line operations `input`
needs.

Argument order is data-last (`guidelines.md` §6): the buffer or zipper is the
final parameter, so `buffer_insert` and `zipper_to_display_col` compose under
`|>`. Revision 1 proposed data-first to match `text.sprout`; the nested calls
that produced in the tests are exactly what §6's rationale describes.

## 6. Modules

```
stdlib/tui/line_zipper.sprout        one line, cluster-safe   ← input, buffer
stdlib/tui/buffer.sprout             lines, caret, goal col   ← text_area, ide
stdlib/tui/widgets/viewport.sprout   §4.6's window            ← text_area, ide
stdlib/tui/widgets/text_area.sprout  the widget
```

One addition to an existing module: `text.truncate_left`, the longest suffix
fitting in a column budget. `input` and `text_area` both need it to place a
caret at the right-hand edge, and a second copy of a width-budget loop whose
termination depends on a negative budget is the kind that drifts. Data-last
like the new modules, though `truncate` beside it is data-first: guidelines §6
binds a new public API, and matching a neighbour is not one of its exceptions.

## 7. Syntax, types and errors

No language change, no new builtin, no runtime change, no spec change. Golden IR
moves: three new stdlib modules and one new export change the public name
surface, so `just ir-golden-diff` reports diffs that must be read before
regenerating.

## 8. Compatibility and migration

`input`'s public surface is unchanged. Two behaviour changes inside it: it now
shares the extracted zipper (§4.2), and U+2028/U+2029 are blanked rather than
painted (§4.4). `test_tui_input.spr` stays as written apart from a new
assertion for the second.

## 9. Tests

`tests/stdlib/test_tui_buffer.spr` — the buffer without a screen: split and
join at every boundary, Backspace at column 0, Delete at end of line, the goal
column across short and empty lines, a paste containing LF/CRLF/CR/NEL, a
combining mark arriving after a line break, and the document round-trip
(`buffer_text` after each edit).

`tests/stdlib/test_tui_text_area.spr` — the widget, in `test_tui_input.spr`'s
idiom: a transcript of what it says, a neighbour probe so a declined key is
visible as a broadcast, and render assertions for the window following the
caret on both axes, an unfocused area showing its start, and the caret cell
under a wide cluster.

`tests/stdlib/test_tui_input.spr` — unchanged, as the extraction's proof, plus
the U+2028 assertion.

## 10. Deferred, filed in `BACKLOG.md` §4

Soft wrap; selection, clipboard and undo; word motion and the chord family; the
control vocabulary's first real consumer; tab-stop expansion on paste; a
`Delivery`-level or opts-level jump-to-line; peeking away from the caret.
