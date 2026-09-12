# The Sprout IDE (`ide/`) — v0

Status: experimental. Not normative; `docs/spec-v0.md` governs the language.

## 1. What it is, and why it is in this repo

An editor written in Sprout, on `stdlib.tui`. It exists for three reasons, in order:

1. It is the framework's first real application. A widget set with no application is a set of
   unit tests.
2. It is where `stdlib.compiler` stops being a batch pipeline and becomes something interactive.
3. It is a maturity test. Where the IDE hits a language or stdlib wall, the wall gets fixed —
   §10 lists the ones v0 hit.

`ide/` is a top-level directory, not part of `stdlib/` and not in `examples/`. Modules are `ide.*`,
resolved with `--package-root <repo-root>`, the same contract `uncharted-suns` uses. It is laid out
so it can be lifted into its own repository whole: it imports `stdlib.*` and never the reverse, it
has its own `just` recipes and its own `BACKLOG.md` section.

Not `examples/` for a concrete reason: that directory is the golden-IR corpus, and every file added
there fails `just ir-golden-diff` with `MISSING GOLDEN` until snapshotted.

## 2. Goals and non-goals for v0

**Goals.** Open a file from a project tree, edit it, write it back. Be driven end to end by a gate.
Establish the module seams the later milestones fill in.

**Non-goals.** Splits, tabs, a command palette, syntax highlighting, diagnostics, search, undo.
Each is named in §9 or already in `BACKLOG.md`; none is started.

## 3. The shape, and the one constraint that fixes it

`app.App` has no model:

```sprout
update: m -> widget.Widget m -> (widget.Widget m, Flow, List (Cmd m))
```

The widget tree is the only state the loop threads, and `Widget m` erases a widget's state type. So
`update` cannot read anything: not the text in the editor, not which file is open, not whether it
has been edited. This is deliberate (`stdlib/tui/app.sprout:44`), and everything below follows from
it.

Two consequences shape `ide/`:

- **Every piece of state lives in the widget that owns it**, including state that is not visibly a
  widget's. The status line is a widget because `update` has nowhere to keep a string between
  messages.
- **`update` gets at state by ASKING.** `widget.deliver` is a pure function, so `update` can send an
  addressed message, read the messages that come back, and act on them within the same step. §5.2
  is the one place this matters.

```
ide/app.sprout        wiring, the message type, `update`, the save handshake   (§3)
ide/document.sprout   what is known about an open file apart from its text     (§4)
ide/editor.sprout     the editor pane                                          (§5)
ide/keymap.sprout     key + modifiers -> command                               (§6)
ide/filetree.sprout   label paths -> filesystem paths, and the reading         (§7)
```

## 4. `ide.document` — a file's identity and its dirt

`Document = (file: Maybe FilePath, dirty: Bool)`. No text: see §5.1.

`wrap FilePath = String`, declared here because this module owns a file's identity, and
`filetree.joined` is the seam where a path first becomes one. Where a file *lives* and what is *in
it* are both `String` and they travel together — `SaveTo(path, text, ver)` — so a swap would write
a file's own name into it. The wrap is zero-cost and makes that a compile error
(`docs/guidelines.md` §7).

`file` is a `Maybe` because a buffer can exist before it has anywhere to go, and `target` returns
that `Maybe` unchanged. Saving a pathless buffer needs somewhere to ask for a name, which v0 has
not got; making the absence a value is what keeps `update` total over it rather than inventing a
path or writing a file called `""`.

`touched` is idempotent, because the pane announces the *edge* into dirtiness and a repeat must not
be able to mean anything else.

## 5. `ide.editor` — the pane

### 5.1 A sibling of `text_area`, not a client of it

`text_area` announces its whole document on every keystroke (`on_change: String -> m`), because an
application that cannot read a widget has to mirror what it hears — its header says exactly that.
For an IDE that mirror is quadratic in the wrong place: `buffer_text` is
`join("\n", reverse(above) ++ [line] ++ below)`, so a 1000-line file rebuilds and copies ~40 KB per
character typed, twice — once to build the message, once for the application to store it.

So the pane owns its document instead. What it puts on the loop is a short **label** when the label
changes, and the text exactly once: when it is asked, on a save. The plan justified the sibling on
highlighting grounds (the pane must own `render`, which it does — §5.3); the cost above is the
stronger reason.

The window itself is not duplicated. `stdlib.tui.widgets.viewport` is the extraction of
`text_area`'s window math — which lines a region shows, how far they shift, where the caret sits —
and both widgets paint through it.

### 5.2 The two handshakes

Opening and saving have the same shape: the pane is asked, mints a receipt, and `update` ferries
that receipt through the IO and back without reading it. Neither first hop is a round trip through
the loop — `widget.deliver` is pure.

```
ctrl-s -> keys widget -> Bound(Save) -> update
update: widget.deliver(w, editor, ToMsg(HandOver))   <- pure, synchronous
        pane replies [Saved(SaveTo(path, text, ver))], [Saved(Unnamed)]
        or [Saved(Busy)] if a write of its own has not answered yet
update: cmd_to(editor, write)  ->  Written(WroteTo(Stamped ver path)) back
        pane marks itself clean and announces the label without the `*`

Enter -> tree -> Choose(segs) -> update
update: widget.deliver(w, editor, ToMsg(Choose segs))
        pane mints a receipt, replies [Reading(ReadFrom(path, ver))]
update: cmd_to(editor, read)   ->  Loaded(Stamped ver (path, body)) addressed back
        pane adopts path and text together, and announces the new label
```

Handing the text over is **not** a write, and nothing is marked clean by it — a write can fail.
`on_wrote` is what cleans, after the write happened. Being asked does not advance `ver` either:
the receipt has to name the pane the text came from.

**Every answer is checked, never adopted.** `app.dispatched` spawns a task per command, so an
answer can land any number of loop steps later — after another file has been opened, after more
typing. `stamped.fresh` is the check and it cannot be skipped: the payload has no other accessor.
A pane that adopted the answer's path instead would aim the next ctrl-s at a file it is no longer
showing and write the document it *is* showing over that one. Full rationale, including why the
check is equality and why the version can never repeat: `docs/stale-replies-v0.md`.

**One write outstanding.** A `Cmd` runs in its own task, so two writes of one file can be on their
way to the disk at once and arrive in either order: save, type, save again, and the older text can
land last while the pane shows clean. `stamped.fresh` cannot see that — the losing write is not a
stale *reply*, it is a live *effect*. So the pane refuses a second handover while one is unanswered
(`Saving.Busy`) and the document stays dirty, which is what says so.

The flag is cleared on the answer's **arrival**, not on its relevance, and that is why a write
answers with a two-armed `Wrote` rather than a stamped path. A failed write and a stale answer both
mean the write is over; only a fresh `WroteTo` also means the document is clean. A pane that cleared
its flag only on a fresh success would jam shut the first time a write failed, and never save again.

**A claim mints a receipt and changes nothing else.** It exists so the body arriving later has
something to be checked against; it is not an open. A read is slow and a read can fail — Enter on a
*directory* reaches here too, since `tree.chosen` fires `on_select` for any selected row — so until
the bytes arrive the pane is still showing, and still saving, the file it had. A pane that took the
name at claim time would hold a save target it had never read, and ctrl-s in that window would write
its empty buffer over the file. Path and text are therefore adopted **together**, in `begun`, or not
at all.

This is also why a write's answer cannot *name* a pathless buffer: `confirmed` never reads the answer's
payload, and `document.written` clears dirt and nothing else. Naming one needs a save-as, which is
§9's deferred work; letting an answer do it is the same door the stale-path bug came through.

`app.step_to` looks like the right tool for the interrogation and is not: when nothing claims the
delivery it sends the message to `update`, and an `update` that answers by interrogating again loops
forever. `ide/app.sprout` calls `widget.deliver` directly and handles `Nothing` itself. Filed in
`BACKLOG.md` §4.5.

### 5.3 What it paints

A gutter of line numbers, right-aligned, one space clear of the text, as wide as the largest number
in the document; the document beside it, through `viewport`. Past the last line there is no number —
a numbered blank row would claim the document is longer than it is.

Every chord falls through, which is what makes ctrl-s the application's rather than something the
focused pane swallows. So do Tab, Esc, the function keys and paging, exactly as `text_area` does.

## 6. `ide.keymap`

A table, not a `match` inside `update`: the bindings are then readable in one place and testable
without a terminal, and it is the seam a configuration file plugs into later.

Only chords and Esc are bound. A bare letter belongs to whichever pane holds the keyboard — binding
one would make the editor unusable the moment it had focus. `ctrl` alone, not any modifier, so
alt-chords stay free rather than being swallowed silently.

## 7. `ide.filetree`

The tree names a node by its path of **labels**, so this module owns the translation into a path the
filesystem takes (`path.join` from the root, never `"/"` by hand) and the reading at the end of it.
The widget is `stdlib.tui.widgets.tree` unchanged: nothing about a project browser needs a tree of
its own.

A directory that cannot be read becomes an empty one rather than an error pane — a tree node has
nowhere to put a message, and "opened, holding nothing" is something the user can act on.

## 8. Verification

- `tests/ide/*.spr`, run by `just test-ide` (in `just test`, and its own CI step). `_test-stdlib`
  takes explicit directories, so a new directory needs the recipe; it is separate from
  `tests/stdlib` so it lifts out with `ide/`.
- `just ide-smoke` drives the real binary: Enter opens the fixture's file, Tab moves the keyboard,
  a character is typed, ctrl-s writes, Esc quits. **The assertion is the file's content on disk.**
  The pane's text lives inside an existential, so a file with the right bytes in it is the only
  proof `update` got the right bytes out. Verified to fail when the save wiring is removed.

## 9. Deferred, and what each is waiting on

| Deferred | Waiting on |
|---|---|
| `ide/pane.sprout` — splits and tabs | the `tabs` widget (`BACKLOG.md` §4, C3) |
| `ide/palette.sprout` — commands, save-as | the same, plus a text input in an overlay |
| Reopening at the remembered caret | `text_area`/`viewport` announcing a `Caret` outward |
| Highlighting, diagnostics | M6 spans, then M7's plugin interface |

## 10. What v0 hit, and what it did about it

- **No model in `app.App`.** Not worked around: the status line became a widget, and `update`
  interrogates. §3.
- **The mirror is quadratic.** Avoided by owning the document in the pane. §5.1.
- **`step_to` can recurse forever.** Avoided locally, filed. §5.2.
- **Two writes of one file race to the disk.** Outside what a stale-reply check can see; the pane
  keeps one write outstanding instead. §5.2.
- **The window math lived inside one widget.** Extracted to `widgets.viewport` rather than copied.
