# Undo for `tui.buffer` — v0

Status: implemented 2026-09-22. Normative for `stdlib/tui/`, not for the language.

## 1. The problem

`docs/ide-save-v0.md` §8 named this as the cost of turning autosave on, and the backlog carries it
as a `P1`:

> With `Manually`, a mistyped key is undone by not saving: the file on disk is the floor. With
> `idle:1000` that floor is one second thick, and git is the only recovery left.

Autosave landed before undo, which inverted the safe order. `buffer.Buffer` holds one document and
no history, so nothing takes a keystroke back — and `stdlib/tui/widgets/text_area.sprout` has the
same gap, because it holds a `buffer.Buffer` too (`text_area.sprout:35`). One fix serves both.

The floor being replaced was real but accidental: an unsaved buffer *was* the undo stack. Removing
it without putting something underneath is the regression.

## 2. Goals and non-goals

Goals:

- A reader can take back what they typed, past several edits, and put it back again.
- `text_area` and `ide/editor` both get it from one place, with no new type for callers to learn.
- A step is a unit a reader would recognise — not one keystroke at a time.
- History costs memory proportional to what changed, not to the document.

Non-goals for v0, each with a reason rather than a deferral:

- **Selection-aware undo.** Selection is itself unimplemented (`tui-text-area-v0.md` §4.9); undo
  must not invent a representation the editor has not chosen yet.
- **An undo *tree* (Vim's `g-`/`g+`).** Branches matter once undo-then-edit loses work. It only
  loses redo, and §5's redo stack is the standard answer. Revisit if a reader asks for it.
- **Persistence across process restarts** (Vim's `'undofile'`). In-memory history restores the floor
  autosave removed; a durable one is §9's question, answered there, and now its own backlog entry.
- **Undo of caret motion alone.** §5 explains why moving is not an edit.

## 3. Prior art

Two questions decide the design: what counts as one step, and what sits *under* undo when undo is
not enough. Every row below was checked against the primary source named; unverified behaviour is
omitted rather than guessed.

### 3.1 What is one undo step

| editor | one step is | knob |
|---|---|---|
| Vim | "One undo command normally undoes a typed command, no matter how many changes that command makes." A whole insert-mode session is one command, so one `u` removes everything typed since `i`. | `CTRL-G u` starts a new block mid-insert, "to make long insertions undoable in parts, such as per sentence" |
| Emacs | "The editor command loop automatically calls `undo-boundary` just before executing each key sequence, so that each undo normally undoes the effects of one command." Typing is special-cased: `self-insert-command` and `delete-char` are *amalgamating*, so "a boundary is inserted only every 20th command, allowing the changes to be undone as a group." | `amalgamating-undo-limit`; "If this variable is 1, no changes are amalgamated." |

Sources: [Vim `undo.txt`](https://vimhelp.org/undo.txt.html); [GNU Emacs Lisp Reference,
Undo](https://www.gnu.org/software/emacs/manual/html_node/elisp/Undo.html).

The two disagree about *how* a run ends and agree completely that one keystroke is the wrong unit.
Vim ends it on a mode change, which Sprout has no equivalent of; Emacs ends it on a count, or on any
non-amalgamating command. §5 follows Emacs, because a modeless editor has to.

Both bound the history. Vim's `'undolevels'` is "the number of changes that are remembered", default
**1000** — read out of the shipped binary (`vim -esn --cmd 'verbose set undolevels?'`, Vim 9.1)
rather than the manual, which states the semantics but not the default. Emacs bounds by *bytes*,
with a soft limit (`undo-limit`: "the change group at which this size is exceeded is the last one
kept") and a hard one (`undo-strong-limit`: that group "is discarded itself, along with all older
change groups").

Source: [GNU Emacs Lisp Reference, Maintaining
Undo](https://www.gnu.org/software/emacs/manual/html_node/elisp/Maintaining-Undo.html).

### 3.2 What sits under undo

Every editor in this survey has a second floor, and it is never the undo stack:

| editor | the floor | what it is |
|---|---|---|
| Emacs | auto-save files | "auto-saving is done in a different file called the *auto-save file*, and the visited file is changed only when you request saving explicitly"; the name is made "by appending `#` to the front and rear of the visited file name" |
| Vim | persistent undo | "Vim will automatically save your undo history when you write a file and restore undo history when you edit the file again" (`'undofile'`) |
| VS Code | Local History | "every time you save an editor, a new entry is added"; `workbench.localHistory.enabled` defaults to `true`, `maxFileEntries` to 50, and `mergeWindow` to 10s, "the interval during which consecutive changes are combined into a single entry" |
| JetBrains | Local History | "automatically records your project's state as you edit code… maintains revisions for all meaningful changes made both from the IDE and externally", kept for the last 5 working days, and works "even if no version control is enabled for your project yet" |

Sources: [Emacs Auto-Save
Files](https://www.gnu.org/software/emacs/manual/html_node/emacs/Auto-Save-Files.html); [Vim
`undo.txt`](https://vimhelp.org/undo.txt.html); [VS Code 1.66 release
notes](https://code.visualstudio.com/updates/v1_66); [JetBrains Local
History](https://www.jetbrains.com/help/idea/local-history.html).

The pattern worth noticing: **Emacs is the only one whose autosave does not touch the visited
file.** Sprout's does: `editor.sprout:281` saves to `document.target`, the file being edited. That
puts Sprout in the VS Code and JetBrains camp, and both of those ship a local history *by default*.
§9 takes that up.

## 4. Where the history lives

Not in `Fields`. That is where the existing extension note points —

> Opaque: a single-constructor ADT declared without `(..)`, so `Fields` can grow a selection anchor
> later without breaking a caller (§4.9).

— and it is right for a selection anchor, which is document state. History is not: it is a *stack
of document states*. `Fields` holding `past: List Fields` makes every snapshot carry its own
history, so a snapshot of a snapshot nests. The only way to use it is to remember to blank the
history at each of the 13 `Fields(…)` construction sites, and forgetting one loses history
silently. That is an illegal state left representable, against `docs/guidelines.md` #3.

So the document and its history are separate types, and the internals only ever see the document:

```sprout
# What every edit and motion works on. Unchanged from today's `Buffer`.
type Doc = | Buf Fields

# What a caller holds. Opaque for the same reason `Doc` is.
export type Buffer = | Hist Doc History

type History = (past: List Doc, future: List Doc, run: Run)
```

An internal edit takes `Fields` and returns `Doc`. It cannot record a step and it cannot drop one,
because it never holds a `History`. Only the ~25 exported functions do, each a thin wrapper over the
machinery that exists today.

**Snapshots, not an invertible edit log.** The usual objection is memory, and it does not apply:
`Fields` is `(above: List String, line: Zipper, below: List String, goal)`, and typing on the
current line leaves `above` and `below` untouched. A persistent list shares them wholesale, so a
step costs the changed line, not the document. An edit log would buy nothing and would need every
operation to know its own inverse.

## 5. What closes a step

Following Emacs, because Sprout's editor is modeless and has no `i` to leave:

- Consecutive single-character insertions **coalesce** into one run.
- A run is closed by any of: a caret motion, a newline, a backspace or delete, a multi-character
  insert (a paste is its own step), or reaching a cap on the run's length — **20**, Emacs's own
  `amalgamating-undo-limit` default. A closer that is itself a keystroke opens the next run; a
  newline or a paste opens nothing, because what follows it starts over.
- **Motion alone records nothing.** Moving the caret closes a run but does not push a step, so
  undoing never walks the reader back through their own cursor movements. This is the one place the
  design says "no" to something Emacs says yes to, and it is deliberate: Emacs records motion only
  because its undo list is a buffer-modification log, and Sprout's is not.

`Run` in §4 is what tracks whether an insertion continues the current run or starts a new one.

**Undo restores the caret with the text**, because the snapshot is the whole `Fields`. Landing the
caret where the edit happened is why snapshot-based undo feels right and a text-only one does not.

**Bounded, following both references.** A cap on steps, in Vim's units rather than Emacs's bytes —
Sprout has no cheap way to size a `Fields`, and a step count is the number a reader can reason
about. 1000 is Vim's default and a sensible starting constant; the cap is a named binding, not a
literal, so moving it is a one-line change with a test.

**Redo** is a second stack, refilled by undo and **cleared by any new edit** — the behaviour §2
takes in place of an undo tree.

## 6. The API

```sprout
# stdlib.tui.buffer
buffer_undo(b: Buffer) -> Maybe Buffer
buffer_redo(b: Buffer) -> Maybe Buffer
```

`Maybe` for the same reason every edit already returns one: `Nothing` means nothing changed, which
is the signal `text_area` uses to decide whether to announce a value. Nothing to undo, nothing to
redo, and a buffer holding only its opening state all answer `Nothing`.

No other export changes shape. `buffer_open` starts with empty history, and the opened document is
the floor — undo stops there rather than emptying the buffer.

## 7. Binding it

`text_area` declines every chord by contract:

```sprout
fn area_key(a: Area m, k: event.Key, mods: event.Mods) -> Reply m =
  if event.mods_is_chord(mods) then Nothing else area_plain(a, k)
```

So binding is the application's job, and in the IDE that means `ide/keymap.sprout` gains `Undo` and
`Redo` beside `Save`, routed to the pane the way `Save` is.

ctrl-z is available: the TUI clears `ISIG` (`term_raw_enter` in
`runtime/sprout_runtime.c`), so ctrl-z arrives as a key rather than `SIGTSTP` — the
same line that makes ctrl-C bindable, for the same stated reason
("an editor has to be able to bind it").

**Redo is ctrl-y, not ctrl-shift-z.** The legacy encoding sends one control character for both, so
`event.Mods` cannot tell them apart; the kitty protocol is what would, and it is filed. ctrl-y is
VS Code's own redo on Windows and Linux
([default keybindings](https://code.visualstudio.com/docs/reference/default-keybindings)).

**The seam is a prism, not a `Delivery` arm**, following `tui-text-area-v0.md` §4.10: the pane takes
`on_step: Maybe (m -> Maybe Step)` where `Step` is `Backward` or `Forward`, and `ide/app.sprout`
decodes its own `Bound Undo` into one. The binding is the application's and the history is the
pane's, so the key is decoded there and the walk happens here — on the one `buffer.Buffer` there is.

`text_area` gets the history with the type and has no way to be asked for it: it binds no chord and
takes no such prism. Filed in `BACKLOG.md` §4.

**An undo must mark the pane dirty.** Otherwise autosave leaves the pre-undo text on disk and the
feature fails at exactly the moment it was built for. It goes through `editor.edited`, which is what
already marks a keystroke dirty, so the two cannot drift apart.

## 8. Impact

- **Syntax, typing, evaluation order, diagnostics:** none. No language change; `docs/spec-v0.md` is
  untouched.
- **Callers:** source-compatible. `Buffer` is opaque, so no caller can name `Buf`; the constructor
  gaining a field is invisible outside the module. Every existing signature keeps its type.
- **Migration:** none for callers. Inside `buffer.sprout`, 16 internal signatures rename `Buffer` to
  `Doc` and the exports gain a recording wrapper.
- **Cost:** one extra allocation per recorded step, and retained memory proportional to changed
  lines. Motion, which is the common case, records nothing.

## 9. The open question: does a local history belong in v0?

§3.2 is the argument for yes: Sprout's autosave writes the visited file, like VS Code's and
JetBrains', and both of those ship a local history on by default. Undo restores the floor *within a
session*; it does nothing for a file closed and reopened, and Sprout has no `#foo#` and no
`'undofile'`.

The argument for no is that it is a different feature — durable state, a place on disk to put it,
a retention policy, and a way to browse it — and none of that is on the path to taking back a
keystroke. Shipping undo first is not a half-measure; it closes the regression `ide-save-v0.md` §8
named, and leaves a gap that predates it.

**Decided: not in v0.** Undo landed on its own, and the local history is filed as its own entry
with §3.2's table as the survey (`BACKLOG.md` §4.5).

## 10. Verification

Pinned in `tests/stdlib/test_tui_buffer.spr`:

- a typed run undoes as one step, and restores the caret with the text;
- each of caret motion, newline, and backspace closes a run — so one undo reaches the boundary,
  not the empty document;
- two undos reach two boundaries back;
- `Nothing` for: a freshly opened buffer, undoing past the opening state, redo with nothing undone,
  and a caret move on its own;
- redo replays the step and its caret, and a new edit clears the redo stack;
- undo stops at the opened document rather than emptying it;
- a line split and a backspace-join each undo to the document they came from;
- a run longer than the run cap becomes two steps;
- 1001 steps leave 1000, and the oldest is gone.

In `tests/ide/test_ide_editor.spr`: ctrl-z reaches the pane, and an undo marks it dirty so the next
autosave writes the undone text — the §7 failure that would make the feature pointless.

`just ide-smoke` is not extended. It pins what only a real binary can show, and undo has no terminal
behaviour the unit tests cannot reach.
