# TUI `tree` (v0)

Status: **IMPLEMENTED — C3c** (`stdlib/tui/widgets/tree.sprout`). Non-normative;
`docs/spec-v0.md` governs the language and nothing here proposes a language change.

Sibling designs: `docs/tui-list-view-v0.md` (the closest widget),
`docs/tui-content-update-v0.md` (the channel §4.3 depends on),
`docs/tui-focus-v0.md`, `docs/tui-widget-set-v0.md`.

## 1. Problem

M5's `ide/filetree.sprout` is a project browser, and nothing in
`stdlib/tui/widgets/` shows a hierarchy. `list_view` is the nearest thing and is
flat by construction: its content is a `List String` and what leaves it is an
`Int` index.

`tree` was deliberately held until content updates landed.
`docs/tui-content-update-v0.md` §1.1 says why:

> `tree` is the worst widget to build before this is settled: a file browser's
> content changes by definition, every time a node expands.

That is now settled on all four stateful widgets, so the channel §4.3 needs
exists.

## 2. Goals and non-goals

**Goals.** A hierarchy of labelled nodes with one selected. Expand and collapse
by keyboard. Children that are not known at construction — a directory is read
only when it is opened. The window follows the selection, as `list_view`'s does.
Nothing new in the widget contract: `View` gains no field.

**Non-goals.** Per-item widget rendering — already deferred for `list_view`
(`BACKLOG.md`, **TUI `list_view` and `tree` — per-item rendering**) and it should
not be re-opened here. Multi-select and check-boxes, which `brick-filetree` has and no
M5 module needs. Mouse, deferred with the hit-test tree. Filtering, which is the
command palette's job over a flat list. Subtree-granular content updates (§8).
Icons, columns and horizontal scrolling.

## 3. Prior art

**No mainstream TUI framework ships a general tree widget.** Two of the three
surveyed ship a *file browser* instead, which is the concrete need behind the
abstract one. Verified against each project's own reference documentation.

| framework | tree widget | what it ships instead |
|---|---|---|
| **brick** (Haskell) | none built in | `Brick.Widgets.FileBrowser` — **one directory at a time**, no expansion, no hierarchy. `brick-filetree` is a separate package. |
| **ratatui** (Rust) | none built in | trees live in the third-party `tui-tree-widget` crate. |
| **bubbles** (Go) | none | `filepicker` — directory navigation. |

The one real tree, `tui-tree-widget`, answers this design's three questions:

- a node is named by **`Vec<Identifier>`, a path from the root** — `TreeState`'s
  `selected()`, `open()`, `close()` and `toggle()` all take one;
- expansion lives in the **widget state**, not the data: `TreeState::opened()`
  returns `&HashSet<Vec<Identifier>>`, while the `TreeItem`s stay immutable;
- children are supplied **eagerly** — a `TreeItem` holds its whole subtree.

brick's `FileBrowser` agrees on the third point from the other direction:
`setWorkingDirectory` scans the directory immediately, in `IO`.

**Where Sprout cannot follow, and why it is not a preference.** Reading a
directory is `!{IO}`, and `View.route` and `View.on_event` are pure. Every
surveyed framework loads eagerly from a context where IO is available; Sprout has
no such context inside a handler. This is the same wall that stopped `text_area`
using `mutvec_*` (`docs/tui-text-area-v0.md`) and stopped `list_view` measuring
its own page height (`docs/tui-list-view-v0.md` §4.5). §4.3 is the answer, and it
is only available because content updates landed first.

## 4. Decisions

### 4.1 A path names a node, never a row index

`list_view` §4.1 sends an `Int` because the application holds the items and an
index names one of them. That does not lift: a tree's visible rows are a
*flattening*, so every row index below an expanded node shifts the moment it
opens. An index captured in one frame names a different node in the next.

A node is therefore named by its **path of labels from the root**,
`List String` — which is `tui-tree-widget`'s answer with the identifier type
fixed to the one the caller already has. For `ide/filetree` the path is the file
path: fold `fs.path.join` over it and the result is what `read_dir` takes. Not
`string.join("/", …)` — the separator is `stdlib.fs.path`'s business, not a
widget's.

**The constraint this carries:** two siblings sharing a label are
indistinguishable. A filesystem cannot produce that, which is why it is
acceptable here; a general tree over arbitrary data can, and such a caller must
make its labels unique or wait for §8's identifier option.

### 4.2 Three states for children, because "not yet read" is not "empty"

An unexpanded directory has no children *known*, which is not the same as having
none. Collapsing the two makes an unread directory render as a leaf, with no
way back.

```sprout
export type Children (..) =
  | NoChildren              # a leaf — nothing to expand
  | Unread                  # expandable, children not yet supplied
  | Loaded (List Node)      # expandable, children known (possibly Nil)
```

Three constructors rather than a `Maybe (List Node)` plus a `Bool`, so the
illegal fourth state cannot be written (`docs/guidelines.md` §3).

The distinction is **behavioural, not visual**: an open `Unread` and an open
`Loaded(Nil)` paint identically, because they are in the same visible state —
expanded, showing nothing. What differs is that opening the first asks the
application for children and opening the second does not (§4.4). Writing §7's
tests is what established that; the first draft of this section claimed a
rendering difference that does not exist.

### 4.3 Expansion state is the widget's; node data is the application's

Following `tui-tree-widget`'s split. The application supplies the `Node` tree;
the widget remembers which paths are open and which is selected. The reason is
the same one that makes content updates announce a clamp: a replacement must not
silently collapse a tree the user spent ten keystrokes opening.

Open paths are a `Dict Bool` keyed by the joined path, not a `List (List
String)`: flattening asks "is this open?" once per visited node, and linear
membership would make a repaint O(visible × open).

### 4.4 Expanding is a conversation, not a call

The widget cannot read a directory, so opening an `Unread` node does two things:
it marks the path open, and it **says so**, through an opts handler the
application supplies.

```sprout
on_expand: Maybe (List String -> m)
```

The application does the IO in `update` and hands the tree back through
`on_content`, exactly as `list_view` takes new rows. v0 replaces the **whole**
tree rather than splicing one subtree — it reuses the landed channel unchanged
and needs no second inbound path. §8 keeps the granular version.

A node whose children are already `Loaded` expands with no message: it is not a
request, and re-reading a directory the user merely collapsed and reopened would
make a keystroke hit the disk.

### 4.5 The window is a function of the selection

Inherited from `list_view` §4.4 rather than re-decided: `render` is pure in the
state, so a stored offset could never be updated by a pure handler and would
drift out of step with the selection. The first visible row is derived from the
selected row's position in the flattening.

### 4.6 `tree` does not wrap `list_view`

It would have to own a `Widget m` whose state it cannot read — `Widget m` is
existential, so the tree could neither ask the list what is selected nor set it.
The two share a *pattern* (a selected row, a derived window, three styles), not
code. This is the same conclusion M5 reaches for the editor pane, which is a
sibling of `text_area` rather than a client, for the same reason.

`ListStyle` is reused as-is: a blurred tree still has a selection, so the
three-style split (`normal`, `selected`, `selected_focused`) applies unchanged.

### 4.7 Keys

Claimed when focused and unchorded, and modelled on `tui-tree-widget`'s
`key_up`/`key_down`/`key_left`/`key_right`:

| key | effect |
|---|---|
| Up / Down | previous / next **visible** row |
| Right | open the selected node; if already open, move to its first child |
| Left | close the selected node; if already closed or a leaf, move to its parent |
| Enter | choose — sends `on_select` with the path |
| Home / End | first / last visible row |
| PageUp / PageDown | by `opts.page`, the number the caller gives (`list_view` §4.5) |

Everything else declines, Tab and Esc included, for `list_view`'s reasons.
Left-on-a-leaf moving to the parent is the behaviour every file browser has; it
is what makes a deep tree escapable without reaching for Up.

### 4.8 Moving the selection is silent unless asked

`on_highlight` mirrors `list_view` §4.2: moving and choosing are different
events, and a tree that announced every arrow key would make a held-down Down
into a message storm. A collapse that hides the selected node moves it to the
nearest visible ancestor, and that move **is** announced when subscribed — the
caller did not ask for it and cannot predict it, which is
`docs/tui-content-update-v0.md` §9.1's rule.

## 5. Surface

```sprout
export type Children (..) = | NoChildren | Unread | Loaded (List Node)

export type Node = (label: String, children: Children)

export type TreeOpts m = (start: List String, page: Int, look: ListStyle,
                          on_highlight: Maybe (List String -> m),
                          on_expand: Maybe (List String -> m),
                          on_content: Maybe (m -> Maybe (List Node)))

export fn tree_opts() -> TreeOpts m

export fn tree(id: widget.WidgetId, roots: List Node,
               on_select: List String -> m) -> widget.Widget m

export fn tree_with(id: widget.WidgetId, roots: List Node,
                    on_select: List String -> m,
                    opts: TreeOpts m) -> widget.Widget m
```

`roots` is a `List Node`, not one `Node`: a project browser shows the contents of
a directory, not a box named after it.

## 6. Compatibility

Purely additive — a new module, no existing signature changes. `ListStyle` is
imported from `list_view` rather than copied, which makes a theme that styles one
style the other.

## 7. Tests

`tests/stdlib/test_tui_tree.spr`, using the addressed-delivery harness the four
content-update suites share (`told_all`/`says`, with a `Noted` neighbour probe so
a declined key is visible as a broadcast rather than as silence).

- Flattening: a closed node hides its subtree; opening a loaded one shows its
  children, indented; a leaf carries no expander glyph.
- Motion over the flattening, including Down stepping *into* an open subtree
  rather than over it — the case a flat list cannot exercise.
- Right/Left on each of leaf, `Unread`, open and closed, and Left on a root.
- `on_expand` fires for `Unread` and **not** for `Loaded` (§4.4's second half).
- Content replacement keeps the open set and the selection; a replacement that
  removes the selected path moves the selection and announces it (§4.8).
- §4.1's ambiguity, both halves: the walk resolves a repeated sibling label to
  the **first**, and because the open set is keyed by path, opening one sibling
  opens every sibling sharing the label. The two fixture nodes differ in *kind*
  — first-versus-last is otherwise unobservable and the case would pass
  whichever the walk picked.
- Declining: chords, Tab, Esc, an unrecognised message, and a key while unfocused.

**Not testable, and deliberately so:** an `Unread` node that is open renders
identically to an open `Loaded(Nil)` — both are expanded and show nothing. They
*are* in the same visible state; the difference is a read in flight, which is
transient and not the reader's business. The distinction that matters is
behavioural and is covered above: one asks for children, the other does not.

## 8. Deferred, filed in `BACKLOG.md`

- **A replacement rebuilds the whole forest.** v0 swaps the lot; splicing one
  path's children is what a large project wants.
- **Sibling labels must be unique** — §4.1's constraint, lifted by
  `tui-tree-widget`'s caller-chosen identifier.
- **Per-item rendering** — folded into `list_view`'s entry, not a second one.
