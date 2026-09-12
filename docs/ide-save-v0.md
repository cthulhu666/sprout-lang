# When the IDE saves (`ide.editor` save strategy) — v0

Status: implemented. Normative for `ide/`, not for the language.

## 1. The problem

ctrl-s is the only way a file reaches the disk. That is a defensible v0 — an unsaved buffer is its
own undo, which matters more than usual here because the pane has no undo stack — but it is not how
the editors this is measured against behave. RubyMine saves continuously; nobody presses ctrl-s in
it. An IDE that needs an explicit save teaches a habit its own reference points have dropped.

What the pane already has is enough to fix it. `TickEvent` reaches it on the broadcast channel, and
`ToFocus false` reaches it addressed when the focus ring moves on. What it does not have is a way to
*start* a save: `on_demand` decodes the request and the reply's constructor out of the same message,
so a pane with no message in hand cannot name what it would answer with.

## 2. Goals and non-goals

Goals:

- The IDE saves without being asked, on a schedule the person running it chooses.
- The strategy is a value in `EditorOpts`, so an embedder picks one per pane, and a flag on the
  binary, so a user picks one per run.
- A trigger nobody asked for is SILENT about refusals. Explicit ctrl-s keeps saying why.
- ctrl-s works under every strategy, including the one that adds no trigger at all.

Non-goals for v0:

- Saving on application deactivation. The terminal does not report it — see §3.
- A config file. The flag is the whole surface; a `.sproutide` is a separate piece of work.
- Save-as. A pathless buffer still has nowhere to go, and an unprompted save must not say so.
- Per-file strategies. One pane, one `SaveWhen`.

## 3. Prior art

| Editor | Triggers | Idle save on by default? | Delay |
| --- | --- | --- | --- |
| VS Code | `files.autoSave`: `off`, `afterDelay`, `onFocusChange`, `onWindowChange` | no — `off`, explicit save required | `files.autoSaveDelay`, 1000 ms |
| JetBrains | Two independent checkboxes: save on idle for N seconds; save on frame deactivation | no — `autoSaveIfInactive` is `false`; `autoSaveFiles` (frame deactivation) is `true` | `inactiveTimeout` 15 s, range 1–300 |
| Emacs | `auto-save-visited-mode` writes the visited file; plain auto-save writes `#foo#` beside it | no — visited-mode is opt-in | `auto-save-visited-interval`, 5 s |

Four things come out of this.

**Nobody idle-saves the real file by default.** All three ship with the timer off. The reputation
RubyMine has for never needing ctrl-s comes from its frame-deactivation save and from a set of
predefined triggers its own reference calls unconfigurable — running, compiling, closing a tab,
version control actions — not from the idle timer, which is the one part a user has to switch on.

A terminal IDE has none of those. There is no frame to deactivate (see below), no run
configuration, no tab to close. So the idle timer is not one trigger among many here; it is the only
continuous one available, and reproducing the EFFECT asked for means enabling the mechanism all
three of these leave off. §7 takes that decision and §8 says what it costs.

**A sum, not a set of checkboxes.** JetBrains lets both triggers run at once; VS Code makes them
exclusive. Exclusive is chosen here, because the two triggers overlap almost completely in a
terminal: losing focus inside the application is a keystroke, and a keystroke is also what stops the
idle clock. A pane that saves on both would save twice for one Tab.

**`onWindowChange` is unreachable.** It needs the terminal to report that the application lost the
screen, which is DECSET 1004 focus reporting. Nothing in `stdlib/tui` emits it and
`stdlib.tui.event.Event` has no variant for it. `onFocusChange`'s in-application half is reachable,
because the focus ring already tells a widget it was blurred.

**Emacs is the dissent worth recording.** It declines to write the visited file by default and
auto-saves to a separate `#foo#`, precisely so that an automatic write cannot destroy the last
version the user chose to keep. Sprout's pane has no undo stack, so it is taking the bet Emacs
refuses. §8 says what that costs.

## 4. Design

### 4.1 `SaveWhen`

```sprout
export type SaveWhen (..) deriving (Eq, ToString) =
  | Manually
  | WhenIdle Int
  | WhenUnfocused
```

`Manually` is not "no saving" — it is "no trigger beyond the person". ctrl-s is the application's
binding and reaches the pane as an ordinary request under all three.

### 4.2 Splitting `on_demand`

Before, one option answered two questions at once:

```sprout
on_demand: Maybe (m -> Maybe(Saving -> m))
```

"Is this message a save request?" and "what do I answer with?". The second has no message to be
read out of when a tick is what started the save, so it becomes standing:

```sprout
on_demand: Maybe (m -> Bool),
on_saving: Maybe (Saving -> m),
```

A pane with `on_saving = Nothing` cannot answer a save at all, which is the read-only viewer that
`editor_opts()` already describes. The split also removes a small duplication at the call site: the
application named `Saved` inside its own decoder before, and names it once now.

### 4.3 Where each trigger lives

`WhenIdle` is counted in `pane_on_event`, the BROADCAST handler, not in `pane_event`. Two reasons.
A tick is addressed to nobody — `focus.ring_on_event` sends `TickEvent` to `broadcast` explicitly —
so it never arrives through `route`. And an unfocused pane with unsaved text is exactly the pane
that most wants saving, so gating the trigger on focus would disable it where it matters.

`WhenUnfocused` is handled in the `ToFocus` arm of `pane_handler`, on the true→false EDGE. The
messages it returns do reach the loop: `focus.move_focus` blurs first and appends what the blurred
widget said to what the newly focused one says. The one exception is documented in `focus.sprout` —
`ring_start`'s first notification is dropped, because the tree is built before `app.run` exists —
and it does not matter here, since that notification is a focus GAIN.

### 4.4 What an unprompted save must not do

A person who presses ctrl-s on a buffer with no file is told `Unnamed`, and on a buffer with a write
already in flight is told `Busy`. Both are right: they answer a question that was asked.

A trigger asks nothing, so it must not answer. A status line repeating "nothing to save: this buffer
has no file" every second is worse than silence. So every unprompted save clears one predicate
first:

```sprout
fn due(p: Pane m) -> Bool
```

dirty, not already writing, with somewhere to write, and not barred. Failing it is not an error and
says nothing. The last two conjuncts each exist because of a way silence goes wrong.

### 4.5 A refusal must not become a loop, or a loss

Staying silent is only half the job: the trigger still has to come back, and exactly once.

**Refused for `Busy` — do not lose it.** The save goes out when the outstanding write ANSWERS, in
`resumed`, not on the next trigger. `WhenIdle` could have waited for a tick; `WhenUnfocused` could
not — its trigger fired while the write was out, and cannot fire again, because ticks are not its
trigger and the pane is already unfocused. Under the first design everything typed after a ctrl-s
was dropped until the user happened to Tab in and out again. The write's answer is the last moment
anything is listening, so that is where the retry lives, for both strategies.

**Failed — do not repeat it.** A failed write leaves the pane dirty, quiet and free, which is
exactly the state the trigger fires on: the same bytes would go out on the next tick, and every tick
after it. Opening a read-only file with `idle:1000` and typing one character would attempt a write
and push `cannot write …` into the status line about twice a second, forever. So a failure BARS the
bytes it failed on (`refused`), and typing lifts the bar — new bytes are a new question, so a
transient failure needs no restart to recover from. ctrl-s ignores the bar entirely: a person is
never turned away for a reason they cannot see, and deliberately retrying is what that key is for.

## 5. Ticks, not milliseconds

`WhenIdle` counts TICKS, not milliseconds, because a widget cannot see milliseconds. `App.tick_ms`
is the application's field and `TickEvent` carries no payload, so the pane has no way to learn how
long one tick is. Handing it a millisecond count it could not convert would be a lie in the type.

The conversion therefore happens in `ide/saving.sprout`, from the one constant it also feeds to
`App.tick_ms`. That module exists for exactly this: it is where the loop's frame deadline and the
pane's tick budget are both in scope, and it is importable by a test, which `ide/app.sprout` is not
— a module exporting `main` collides with a test's own.

```sprout
export fn tick_ms() -> Int = 500

# Never zero: a budget of zero ticks fires on the tick carrying the keystroke.
fn idle_ticks(ms: Int) -> Int = if ms < tick_ms() then 1 else ms / tick_ms()
```

A tick measures quiet at the TERMINAL, not quiet in the pane. `app.input_loop` restarts its read
deadline on any input at all, so a keystroke the pane declines — ctrl-s, a key pressed while the
tree has focus, a mouse report — postpones an autosave without being an edit. This is the behaviour
wanted (the person is present and working) but it is not what "idle" means literally, and it is why
the counter also resets inside the pane on an edit: ticks banked before a keystroke must not count
toward the quiet after it.

## 6. The API

```sprout
# ide.editor
export type SaveWhen (..) = | Manually | WhenIdle Int | WhenUnfocused

export type EditorOpts m = (..., save_when: SaveWhen,
                            on_demand: Maybe (m -> Bool),
                            on_saving: Maybe (Saving -> m), ...)
```

`editor_opts()` starts at `Manually`, so a widget nobody configured behaves as it did before this
change.

## 7. Configuring the binary

```
ide --save-when=manual
ide --save-when=idle:MS     # default, MS defaults to 1000
ide --save-when=unfocused
```

Parsed with `stdlib.args`. An unrecognised value falls back to the default rather than exiting: the
IDE takes over the terminal on the next line, and a usage error printed into the alternate screen is
a usage error nobody sees.

**The default is `idle:1000`**, which is a deliberate departure from all three references in §3.
They can afford an off-by-default timer because each has another route to the same effect —
frame deactivation, or a separate `#foo#`. A terminal has neither, so leaving the timer off would
mean shipping the explicit-save behaviour this change exists to replace.

1000 ms is VS Code's `files.autoSaveDelay`, not JetBrains' 15 s, because the delay is being asked to
carry the whole job rather than to back up a deactivation trigger.

## 8. What this does not fix

**There is still no undo.** This is the cost §3 flagged, and the default in §7 makes it the common
case rather than an opt-in one. With `Manually`, a mistyped key is undone by not saving: the file on
disk is the floor. With `idle:1000` that floor is one second thick, and git is the only recovery
left.

Every editor in §3 has something under it. JetBrains has Local History, which catches what undo does
not. Emacs writes to `#foo#` and leaves the visited file alone. VS Code has Hot Exit, and local
history in the Timeline view (`workbench.localHistory.enabled`, on by default).
Sprout has the buffer and nothing else — which is why undo is the next piece of work, filed in
`BACKLOG.md` §4.5. Anyone who wants the old floor back before it lands has `--save-when=manual`.

**A save is still whole-file.** Nothing here makes an autosave cheaper than an explicit one: the
pane hands over `buffer.buffer_text`, which rebuilds the whole document. At one write per second per
edited file that is fine; it is not fine per keystroke, which is why no strategy offers one.

## 9. Verification

`tests/ide/test_ide_saving.spr` pins the flag: every spelling, and every way of getting it wrong —
an unrecognised word, `idle` with no delay, an unparseable delay, zero, negative. None may exit and
none may produce a zero tick budget.

`tests/ide/test_ide_editor.spr` drives every trigger as the app loop does — a tick through
`widget.feed`, a blur through a real Tab across a two-widget ring — and pins:

- a tick short of the threshold saves nothing, and the threshold's tick saves;
- an edit resets the count, so quiet is measured from the last keystroke;
- a clean pane, a pathless pane and a pane with a write outstanding all stay silent;
- a pane that was silent because it was `Busy` saves the moment the write answers — under
  `WhenUnfocused` as well as `WhenIdle`, and not twice;
- a failed write bars the bytes it failed on, typing lifts the bar, and ctrl-s ignores it;
- `Manually` ignores ticks and blurs, and ctrl-s still reaches the application under all three.

`just ide-smoke` runs the binary THREE times, each run pinned to one strategy. Pinning is the point:
with autosave on, a run that presses ctrl-s proves nothing about ctrl-s, because the file would have
reached the disk either way. So `--save-when=manual` presses ctrl-s; `idle:500` types and pauses;
`unfocused` types and Tabs away. Each was checked to leave the file untouched when its own strategy
is swapped for `manual`.

The `unfocused` run is the one that cannot be replaced by a unit test. A blur's messages reach the
loop only because `focus.move_focus` blurs BEFORE it lights and appends what the blurred widget
said; had it lit first, or dropped that reply the way `ring_start` drops its first notification, the
save would vanish with the pane none the wiser.
