# `stdlib/tui` core — v0

Status: **implemented.** Milestone M2 of the TUI/IDE arc (M0 = terminal I/O
runtime, M1 = `stdlib.fs`, then `stdlib.unicode` split out of this milestone and landed ahead of
it — `docs/stdlib-unicode-v0.md`). Non-normative: `docs/spec-v0.md` is unaffected — this milestone
adds no syntax, no typing rule and no builtin.

## 1. Problem

Sprout can now read raw terminal bytes and ask the terminal its size (M0), and it has 78 lines of
`stdlib/terminal.sprout` that write individual escape sequences. Between those two there is
nothing. The only TUI in the tree, `examples/sentry_issue_browser_tui.sprout`, redraws by clearing
the screen and re-writing every line, and decodes keys by string-matching the names that
`term_read_key` invents in C.

Two consequences worth naming:

- **Escape-sequence decoding still lives in C and is wrong.** `term_read_key`
  (`runtime/sprout_runtime.c:3695`) recognises exactly `ESC [ A/B/C/D`. Given `ESC [ 1;5C`
  (ctrl-right) it reads `ESC`, `[`, `1`, matches nothing, restores termios and falls through to
  the byte dispatch, which returns `"escape"` — leaving `;5C` in the tty buffer to surface as
  three fake keypresses. Nothing tests this, because a C function reading fd 0 is not reachable
  from a golden-stdout fixture; the conformance file says so itself
  (`tests/conformance/run/terminal_escapes.spr:10-13`).
- **Full-screen redraw is the only rendering strategy available**, and `term_write` `fflush`es on
  every call, so anything finer-grained is a syscall per cell.

`BACKLOG.md:1986` (`P1`, *line wrapping / viewport helpers in stdlib*) is this milestone.

## 2. Goals and non-goals

**Goals.** A pure, testable core: rectangle arithmetic, a style/colour model, a double-buffered
screen with minimal-diff output, and an incremental input decoder that is a total function from
bytes to events.

**Non-goals, deliberately deferred.** Widgets, layout and the app loop are M3/M4 — this milestone
is the substrate they sit on and ships with no widget. Stylesheets are M9. Mouse *reporting mode*
(the escape that asks the terminal to send mouse events) belongs with the app loop that would enable
it; M2 only **decodes** mouse sequences.

Grapheme clustering and double-width cells were originally deferred to §7 and are now **in scope**
(§3.3), on the strength of `stdlib/unicode`.

## 3. Decisions

### 3.1 A key is a code plus a separate modifier set

Surveyed, each verified against its primary source:

| Library | Printable key | Named key | Modifiers |
|---|---|---|---|
| crossterm (Rust) | `KeyCode::Char(char)` | `KeyCode::Up`, `F(u8)`, … (27 variants) | separate `KeyModifiers` bitflags on `KeyEvent` |
| tcell (Go) | `KeyRune` placeholder + `EventKey.Rune()` | `KeyUp`, `KeyF1`–`KeyF64`, … | separate `ModMask` (`ModShift`/`ModCtrl`/`ModAlt`/`ModMeta`/`ModHyper`) |
| kitty protocol | codepoint in the CSI parameter | functional-key codepoints | separate numeric parameter, `1 + bitfield` |

Unanimous: the character and the modifiers are **separate fields**, never fused into one
constructor per combination. So:

```sprout
type Key (..) =
  | KChar Char
  | KEnter | KTab | KBackTab | KBackspace | KDelete | KInsert | KEsc
  | KUp | KDown | KLeft | KRight | KHome | KEnd | KPageUp | KPageDown
  | KF Int

type Mods (..) = | Mods Bool Bool Bool     # ctrl, alt, shift
```

Sprout has no bitflags, and three `Bool` fields is exactly the ctrl/alt/shift set a terminal can
actually deliver over the legacy encoding — meta/hyper/super need the kitty protocol, which is
§7. `KBackTab` is kept as its own code rather than `KTab` + shift, following crossterm: the wire
sequence (`ESC [ Z`) carries no modifier parameter, so synthesising one would be a fiction.

### 3.2 The decoder never resolves a lone ESC — the caller's timeout does

The kitty specification states the problem exactly: *"No reliable way to distinguish single `Esc`
key presses from the start of a escape sequence. Currently, client programs use fragile timing
related hacks for this, leading to bugs."*

The hack is unavoidable on the legacy encoding, but it does not have to be in the decoder. The
signature the plan fixed makes this natural:

```sprout
decode(input: Bytes) -> (List Event, Bytes)
```

A trailing byte sequence that is a **prefix of** something longer — a lone `ESC`, an `ESC [`, an
incomplete CSI, a truncated UTF-8 sequence — is not decoded. It is returned as the remainder, and
the caller prepends the next read to it. The function is total, deterministic and has no notion of
time, which is what makes it unit-testable against byte fixtures.

Resolution happens one level up, in M3's pump: when `term_read_avail(max, ms)` returns `TermIdle`
with a pending remainder of exactly `ESC`, that ESC was the Escape key. This is why M0's timeout
parameter is load-bearing rather than decorative, and it confines the timing hack to a single
place with a name.

### 3.3 A cell holds a grapheme cluster and owns the cell to its right when wide

Pulled into M2 rather than deferred: a renderer that miscounts columns desynchronises the whole
line after the first CJK character, and the diff engine's cursor moves are computed from those same
counts. `stdlib/unicode` (landed separately) supplies both properties.

Surveyed, each verified against its primary source:

| Library | Cell content | Wide character |
|---|---|---|
| tcell (Go) | `SetContent(x, y, mainc rune, combc []rune, style)` — primary rune + combining slice | *"Wide (East Asian full width and emoji) runes occupy two cells, and attempts to place character at next cell to the right will have undefined effects."* |
| ratatui (Rust) | `Cell { symbol: CompactString }` — a whole cluster, on the stack when short | `diff_iter` *"Skips zero-width graphemes and control characters"* and skips the index after a double-width symbol |

They agree on the model — a cell holds a **cluster**, and a wide cluster owns the cell to its right —
and differ only in storage. ratatui's small-string does not port: a Sprout `String` is a heap
C-string with no small-string optimisation, so a `String` cell allocates once per cell per frame,
and a 200×50 screen is 10,000 cells. tcell's split does port, because the no-combining-mark case is
`Nil`, a nullary constructor that allocates nothing.

```sprout
type Cell (..) =
  | Cell Int (List Int) Style     # primary codepoint, combining marks, style
  | Continuation                  # the right half of a wide cell
```

**The primary is an `Int` codepoint, not a `Char`, because there is no `Char -> Int`.** A `Char`
cannot be measured — see `BACKLOG.md` §Compiler / Stdlib Misc — so a `Char` cell would have to
store its width alongside it as denormalised state that can disagree with its content.
`unicode.codepoint_width` takes the codepoint directly, and every `stdlib/unicode` entry point is
keyed the same way.

`Continuation` is an explicit arm rather than a sentinel value so the renderer's `match` is total.
Two consequences this design **defines** where tcell leaves them undefined, because a pure language
has no "undefined effects" available:

- **Writing over a `Continuation`** repairs the wide cell to its left, replacing it with a space.
  Leaving a half-cell behind would emit a cursor move into the middle of a character.
- **A wide cluster in the last column** is replaced by a space, matching tcell exactly: *"Wide runes
  that are printed in the last column will be replaced with a single width space on output."*

Zero-width codepoints are not cells at all — a combining mark joins the `List Int` of the cell to
its left, which is what makes `e` + U+0301 one cell rather than two.

### 3.4 Colour keeps the terminal palette reachable

```sprout
type Color (..) = | DefaultColor | Ansi Int | Rgb Int Int Int
```

`stdlib/terminal.sprout` today emits 24-bit truecolor only (`set_fg`/`set_bg`). That cannot express
*"the user's terminal theme blue"* — an app that wants to blend with the surrounding terminal needs
the indexed palette, and a theme that hardcodes RGB fights the user's colour scheme. `Ansi Int`
covers both the 16-colour and 256-colour palettes (SGR `38;5;n`), matching crossterm's
`Color::AnsiValue(u8)` beside its `Color::Rgb`.

### 3.5 Rendering is a diff, and a frame is one write

`Screen` holds a front and a back `MutMatrix Cell` (`stdlib/mutable.sprout:171` — it exists, with
`mutmatrix_get`/`set`/`fill` already). Widgets write the back buffer; `diff_to_ansi` walks both,
emits a cursor move plus a styled run for each changed span, swaps, and returns one `String`. The
app writes it with a single `term_write`.

`docs/string-building-v0.md` gives the assembly rule; the frame is built as a `List String`
accumulated in reverse and joined with one `string_concat_many` pass, the same shape
`stdlib/string.sprout:323` uses for `join`.

## 4. Modules

| Module | Contents |
|---|---|
| `stdlib/tui/geometry.sprout` | `Size`, `Offset`, `Region`; `intersect`, `clamp`, `split_h`/`split_v`, `contains` |
| `stdlib/tui/style.sprout` | `Color`, `Style`, `style_to_ansi`, `style_default` |
| `stdlib/tui/event.sprout` | `Key`, `Mods`, `MouseButton`, `Event` |
| `stdlib/tui/keys.sprout` | `decode : Bytes -> (List Event, Bytes)` |
| `stdlib/tui/screen.sprout` | `Cell`, `Screen`, `screen_new`, `screen_put`, `screen_write`, `diff_to_ansi`, `screen_resize` |

`screen.sprout` imports `stdlib.unicode.width` and `stdlib.unicode.grapheme`; nothing else in the
milestone depends on Unicode. `screen_write` is the string-level entry point — it segments into
clusters, measures each, and places them — and `screen_put` is the single-cluster primitive it is
built from.

`event.sprout` is separate from `keys.sprout` so a widget can match on `Event` without importing
the decoder — M3's widgets depend on the vocabulary, not on the parser.

```sprout
type Event (..) =
  | KeyPress Key Mods
  | MousePress MouseButton Offset
  | MouseRelease Offset
  | Paste String
  | ResizeEvent Size
  | TickEvent
```

`ResizeEvent` and `TickEvent` are produced by the pump from `TermResized` and from timers, not by
`decode` — they are in the vocabulary because a widget matches one `Event`.

## 5. Syntax, type-system and error-message impact

None. No new syntax, no typing rule, no diagnostic. Every module is ordinary Sprout over existing
prelude and `stdlib.mutable`/`stdlib.bytes` surface.

## 6. Compatibility

Purely additive. `stdlib/terminal.sprout` is untouched: `term_read_key` and the existing
`set_fg`/`set_bold` helpers keep working, and `stdlib/repl.sprout` and
`examples/sentry_issue_browser_tui.sprout` continue to use them. Nothing is deprecated in this
milestone — the C decoder is superseded in *practice* by `keys.sprout`, but removing it is a
separate change with its own consumers to migrate.

No builtin is added, so `runtime/APPROVED_BUILTINS` is unchanged.

## 7. Deferred, filed in `BACKLOG.md` §4 with this change

1. **The kitty keyboard protocol.** Would remove the ESC ambiguity (§3.2) outright and deliver
   meta/super/hyper and key-release events, at the cost of a capability negotiation. The legacy
   decoder is required regardless as the fallback.
2. **Mouse reporting mode.** M2 decodes SGR mouse sequences; nothing enables them.
3. **Terminal-reported width disagreement.** §3.3 computes width from UAX #11, which is the best a
   program can do unilaterally. A terminal that disagrees — most often on emoji ZWJ sequences, and
   on Ambiguous-width characters under a CJK locale — renders a line at a width the diff engine did
   not predict, and the cursor arithmetic drifts from there on. Any real fix is a negotiation with
   the terminal rather than a change to the tables; there is active work on such a mode, not
   surveyed here. Recovery in the meantime is a full repaint, which `screen.sprout` supports.

## 8. Tests

No terminal and no fixture process. `geometry`, `style`, `event` and `keys` are pure, so those are
`run_suite`/`check_eq`; `screen` mutates a `MutMatrix` and so uses the `TestState` form. **162
assertions, all passing.**

- `test_tui_geometry.spr` — 37: intersect/clamp/split, empty and degenerate regions.
- `test_tui_style.spr` — 19: `style_to_ansi` byte-exactness for each `Color` arm and attribute combination.
- `test_tui_keys.spr` — 80, the bulk. Byte fixtures per sequence family: plain ASCII, ctrl chords,
  UTF-8 assembly, `ESC [ A`–`D`, modified arrows (`ESC [ 1;5C`), `ESC O P` (F1, SS3 form), `ESC [ 15~`
  (F5, CSI-tilde form), SGR mouse (`ESC [ < 0;12;5M`), bracketed paste, and — the point of §3.2 —
  every proper prefix of each of those returning `([], <the whole input>)`.
- `test_tui_screen.spr` — 26: a diff over a known mutation emits exactly the changed run; an
  unchanged frame emits the empty string. Plus the §3.3 cases, which are where this gets
  interesting: a wide cluster claims the cell to its right; writing over that `Continuation` repairs
  the wide cell to a space; writing over the wide cell clears its stale continuation; a wide cluster
  in the last column becomes a space; a combining mark joins the cell to its left rather than taking
  its own; and a diff that crosses a wide cell emits one cluster for two columns.

Byte-exact ANSI output also gets `tests/conformance/run/tui_frame.{spr,out}`, following
`terminal_escapes.{spr,out}`. It pins the stdout of a real binary, which the unit suites cannot:
those assert bytes against expressions built from the same helpers the implementation uses, so both
sides of an assertion can move together. Note the sibling fixture's header says the input half of
`stdlib.terminal` is not golden-stdout testable because it depends on stdin and `isatty` — that
stopped being true of *decoding* with this milestone, and `tui_frame` exercises it.

The regression that motivates the milestone is a test in its own right: `ESC [ 1;5C` must decode to
one `KeyPress KRight (Mods true false false)` and leave an empty remainder — where the C decoder
returns `escape` and leaks three bytes.

**The screen suite was mutation-tested**, because it passed on its first run and a suite that has
never been red is not yet evidence. Disabling wide-cell repair turns three cases red; dropping the
last-column rule turns one red; making the diff stop skipping `Continuation` cells turned **none**
red — a default-styled wide cell makes the two behaviours byte-identical. The suite gained a
*bold* wide cell for that, which catches the stray reset the broken skip emits.

## 9. Verification

`mise exec -- just test` (DoD #5), `just fmt` (#4), `just compile-examples-stage1` (#6),
`just ir-golden-diff` (#12 — `stdlib/` is codegen-affecting).

**Seed:** `stdlib/tui/` is outside `compile_driver`'s import closure, so the fixed point should
hold and no reseed should be needed. Stated as a prediction to be *checked by
`just verify-bootstrap-fixed-point`*, not assumed — the same prediction was made for `stdlib.fs`
in M1 and was wrong, because `compile_driver` reaches `stdlib.fs` through `read_file`. Nothing in
the compiler reads a terminal, so the reasoning is different here, but the gate is what settles it.
