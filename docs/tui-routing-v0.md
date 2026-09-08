# TUI routing — an answer that finds the widget that asked

**Status:** implemented. Builds on `docs/tui-widgets-v0.md` (M3), which owns the widget
contract, the layout engine and the pump; this document owns everything about *addressed*
delivery and supersedes §3.8's account of `Cmd`.

## 1. Problem

A widget asks for IO by returning a `Cmd m` — a description of work. The app loop runs it in
a task of its own and posts the result back as a message. That result arrives at
`App.update`, which receives an `m` and an opaque `Widget m`.

`Widget m` is `exists s. Widget (View s m)`. The state type is hidden, so `update` can
replace the tree but cannot address *into* it. When a file-tree widget asks to read a
directory, the answer arrives somewhere that cannot hand it back to the asker.

The consequence, before this change: only application-global commands (quit, reload
everything) were useful, and `examples/tui_dashboard.sprout` issued none — every honest
version of a demo command needed a route back to one specific widget.

## 2. Goals and non-goals

**Goals.** An answer reaches the widget that asked for it. An undeliverable answer is
observable rather than silently dropped. A widget written against its own vocabulary can
still be embedded in another application. `App.update` keeps working unchanged for
applications that never address anything.

**Non-goals.** Focus — who holds it, tab order, what happens on `Tab` — is the widget
library's (M4). This change ships the *substrate* focus needs (`ToEvent`), not the
mechanism. Container widgets are also M4; the containers here are hand-written, in the demo
and in the test suite, exactly as an application must write them today.

## 3. Decisions

### 3.1 The address rides on the command, and ids are declared

```sprout
export type Cmd m (..) =
  | Cmd (Maybe WidgetId) (Unit -> m !{IO})

export fn cmd(f: Unit -> m !{IO}) -> Cmd m                 # answer goes to update
export fn cmd_to(id: WidgetId, f: Unit -> m !{IO}) -> Cmd m  # answer comes back to me
```

**Prior art**, verified from source rather than documentation pages:

| | mechanism | addressing primitive |
|---|---|---|
| **Elm** | `Cmd.map : (a -> msg) -> Cmd a -> Cmd msg` (`elm/core/src/Platform/Cmd.elm`) | none — the parent's message type *embeds* the child's, so routing is a pattern match |
| **Bubbletea** | `Update(Msg) (Model, Cmd)`, `Cmd func() Msg` (`bubbletea/tea.go`) | none — "any component-level message handling must be implemented manually within your model's `Update`" |

Neither framework needs an addressing primitive, and the reason is the same in both: the
parent holds its children **concretely and by name**, so `update` can call
`child.Update(msg)` directly. Sprout's tree hides child state *and* is homogeneous in `m` —
there is no per-level message wrapper to match on. Both of the moves prior art routes by are
unavailable here, by construction. This is the price of the existential, and it is the first
place that price has actually been charged.

**Ids are declared by the widget author, not derived from tree position.** The design forces
it: a widget tags its *own* command, so it must know its id when it constructs the command,
and a leaf does not know where it sits in its parent's list. Derived positional paths
(`"0/2/1"`) would need containers to rewrite commands on the way up, and would misdeliver
when a list reorders while a command is in flight — silently, to a *different* widget, which
is strictly worse than failing to deliver.

Nothing enforces id uniqueness. See §3.4 for what that costs and how it is bounded.

### 3.2 `View` gains a `route` field, and declining is `Nothing`

```sprout
export type Delivery m (..) =
  | ToMsg m
  | ToEvent event.Event
  | ToFocus Bool   # added by C2; docs/tui-focus-v0.md §4.2

route: s -> WidgetId -> Delivery m -> Maybe (s, List m, List (Cmd m))
```

The reply is `on_event`'s shape under a `Maybe`. A leaf fills the field with `no_route` or
`route_if(mine, handler, _, _, _)`; a container forwards to its children, which is the only
thing that can — the framework cannot see through `Widget m` to know whether a given widget
holds children at all.

**Whether the delivery was claimed has to be in the reply, because nothing else can observe
it.** A widget that ignores a delivery returns its state unchanged, which is exactly what a
widget that handled it and changed nothing returns. Without the distinction: a stale address
is dropped with nothing able to log it, two widgets sharing an id both act on the same
answer, and first-wins is not expressible.

**Why `Maybe` and not a `Bool` beside the state.** The first draft was
`(s, Bool, List m, List (Cmd m))`, and it can express a state no caller should honour — *I
declined, and here are some messages*. `step_to` discarded those silently, so the reply type
promised four components and the framework quietly used one or four depending on the flag.
`Maybe` makes the bad reply unrepresentable rather than merely undocumented
(`docs/guidelines.md` #3), and a decline has no state to discard, so §3.4's "the original tree
stands" stops being a rule the caller must remember.

It also collapses code. `deliver` reuses the same repack as `on_event`, and `map_msgs`
retargets both replies with one `remap_one` — the two shapes only diverged because of the
flag. Found by review of the first implementation; the flag version was written, tested and
working before it was replaced.

**Why one `Delivery` sum rather than two entry points.** Focus needs *targeted events* —
keystrokes to one widget — and `route` carries `m`, not `Event`. Wrapping events in `m` is
unavailable for the same reason the address cannot live in the message (§3.5). So focus would
have wanted a sixth field. Every field addition is a breaking change to every `View`
construction in existence, and folding the sum in now costs one small type and breaks the
contract once instead of twice.

### 3.3 `map_msgs` becomes a prism

```sprout
export fn map_msgs(f: m -> n, unf: n -> Maybe m, w: Widget m) -> Widget n
```

`route` puts `m` in **input** position. Every other occurrence of `m` in `View` is
output-only, which is precisely why the two-argument `map_msgs` was writable. With `route`
added, the wrapper must turn a delivered `n` back into the `m` the inner widget speaks, and
`n -> m` does not exist. The compiler says so directly:

```
ERROR: check: Signature too general for its body in map_msgs_naive:
type variable n merged with declared variable m
```

Elm never hits this because `msg` is covariant everywhere in its widget surface. `route` is
the first contravariant occurrence, and a plain functor dies with it.

`unf` is the backward half: it recognises the messages that belong to the embedded widget and
rejects the rest. An embedder that tags with `ChildMsg` already owns that pattern match, so
`unf` is a three-line function. A `ToEvent` crosses untouched — it carries no message.

**This is a breaking change to an exported stdlib function**, and the only one in this change
that is not additive. `App.update` is unchanged; `map_msgs` is not.

Namespacing is a *separate* wrapper rather than part of `unf`: two copies of one widget
collide in a single vocabulary too, where there is no message type to retarget. Built in
§3.9.

### 3.4 An unclaimed answer falls back to `update`

`step_to` delivers, and on `Nothing` it runs `apply(update, [msg], w)` on the **original**
tree — which is the only tree it holds, since a decline carries no other.

A stale address is not something the framework can resolve: the asker closed while its
command was in flight. But the answer is a value of the application's own message type, which
`update` is already total over. The application that wants a drop writes the
`_ -> done(w, Continue)` arm it must have anyway; the application that wants to know gets to
know. Silent drop gives the choice to no one.

Two hazards this does not fix, both the widget author's to defend against:

- **Reincarnation.** Declared ids trade positional staleness for identity confusion — close a
  tab, reopen it, and an in-flight answer lands on a fresh instance that never asked. Harmless
  for a directory listing, a bug for anything stateful. A generation counter is not worth the
  machinery yet.
- **Id collision.** Two widgets with the same id: the container stops at the first claimant,
  so delivery is deterministic in tree order rather than duplicated. That is a convention the
  container implements, not an invariant the framework enforces.

### 3.5 Rejected alternatives

- **Put the address in the message.** Requires `m` to carry a `WidgetId`, and `m` is the
  application's own type — the framework cannot constrain it.
- **Derived positional paths with containers rewriting on the way up.** No author burden, but
  misdelivers under reorder (§3.1).
- **Routing as a container-only concern, no `View` change.** No formulation exists, twice
  over: `Event` is closed and not parameterized over `m`, so an addressed message cannot ride
  `on_event`; and delivering a continuation `s -> s` instead of a message cannot cross
  `Chan (Signal m)`, because `s` is existential.
- **Do nothing.** That is the state this change exists to end.

### 3.6 Ordering

`step_to` reuses `step`'s glue, so the widget-commands-lead-application-commands invariant
holds on the delivery path as it does on the event path.

That invariant was never about *cross-signal* order. Two in-flight commands complete in
whatever order their tasks finish, addressed or not, so **a route handler must tolerate
answers arriving reordered.**

`route` deliberately does not return `Flow`. A widget quits by emitting the application's
quit message, and `apply` over the route-emitted messages decides `Flow` — giving `route` its
own would let a widget bypass the vocabulary. The `SigTo` arm feeds its result through
`advance` like every other path, so a route-emitted `Quit` paints its farewell frame and drops
what follows.

### 3.7 `SigTo` is public, and worker delivery is supported

`Signal m` is exported and `boot` workers post into the same channel, so a worker can address
a widget directly without going through `Cmd` at all. That is supported rather than
accidental: it is how a background producer feeds one pane without the application relaying.

The consequence worth stating: **`update` never sees a routed answer, even a live one.** An
application-level observer — a status bar counting loaded files — learns of it only if the
target widget re-announces through its `List m`. This inverts M3's everything-flows-through-
`update` property, deliberately.

### 3.8 `route_when`: the claim belongs to the handler, not the address

`route_if` answers `Just` whenever the id matches, whatever the handler did. That is right
for a `ToMsg` — an answer arriving at the widget that asked for it has got home whether or
not the state moved — and wrong for a `ToEvent` under focus:

```
a key reaches the focused widget -> the widget ignores it
  -> route_if claims it anyway -> app.delivered sees Just -> no fallback
  -> the application's global binding never fires
```

Both behaviours are wanted, so this is a second combinator rather than a change to
`route_if`. The handler returns the `Maybe` itself:

```sprout
export fn route_when(mine: WidgetId,
                     handler: s -> Delivery m -> Maybe(s, List m, List (Cmd m)),
                     st: s, target: WidgetId, d: Delivery m) -> Maybe(s, List m, List (Cmd m))
```

The type is the argument. Under `route_if` the framework must *invent* a reply for a widget
that had none — the test for it reads `expected none, got []`, an empty message list standing
in for the absence of one, which §3.2 exists to make impossible.

### 3.9 `namespaced`: qualifying an embedded widget's ids

`map_msgs` retargets the message type and leaves `WidgetId` alone, so two copies of one
widget answer to the same address and `deliver_first` silently picks the first. This is not a
`map_msgs` problem — two copies in the *same* vocabulary collide too, where there is no
message type to retarget — so it is its own wrapper:

```sprout
export fn namespaced(prefix: String, w: Widget m) -> Widget m
export fn cmd_readdress(f: WidgetId -> WidgetId, c: Cmd m) -> Cmd m
```

A `WidgetId` travels in exactly two directions, so the wrapper has two seams and no more:
inward as `route`'s target, where the prefix is stripped (`Nothing` if absent, so a foreign
address is declined rather than forwarded); outward as a `Cmd`'s return address, where it is
put back. `cmd_readdress` is `cmd_map`'s complement — one retargets what an answer *says*,
the other who it is *for*. A global command has no address and stays global.

Half of it would be worse than none: strip inward without qualifying outward and the answer
comes back addressed to a name nothing outside the wrapper knows.

Opt-in, not automatic. A container cannot namespace its children on its own — `Slot` carries
no name, and inventing one from list position would make an id depend on sibling order. Named
slots can be built over this primitive if C2 shows containers need them.

## 4. Surface

| Module | Added | Changed |
|---|---|---|
| `stdlib/tui/widget.sprout` | `Delivery`, `cmd`, `cmd_to`, `cmd_addr`, `no_route`, `route_if`, `route_when`, `deliver`, `id_eq`, `namespaced`, `cmd_readdress` | `Cmd` carries `Maybe WidgetId`; `View` gains `route`; `map_msgs` takes `unf` |
| `stdlib/tui/app.sprout` | `step_to`, `SigTo` | `run_cmd` posts `SigTo` for an addressed command |

## 5. Syntax, type-system and error-message impact

None. No new syntax, no typing rule, no diagnostic, no builtin — `runtime/APPROVED_BUILTINS`
is unchanged.

One language gap was confirmed while writing the tests — **an alias inside another alias's
body is not expanded** — and has since been FIXED (2026-09-07, spec §5.6.2). It shared a
root cause with the two `type alias` items filed alongside it: inference stored an alias
only when its body eta-reduced. `type alias Reply = Maybe (Widget Msg, Says, Asks)` now
works, so the spelled-out bodies in `tests/stdlib/test_tui_route.spr` are no longer
forced.

## 6. Compatibility

Additive except for three breaking changes, all to a contract two days old:

1. `View` gains `route` — every construction needs one more field. `no_route` is the
   one-word answer for a widget that is never addressed.
2. `map_msgs` takes `unf` (§3.3).
3. `Cmd` is built with `cmd`/`cmd_to` rather than the bare constructor.

Migrated in this change: four test suites, one conformance fixture, one example.

§3.8 and §3.9 landed later (2026-09-08) and are purely additive: `route_when` sits beside
`route_if`, `namespaced` and `cmd_readdress` are new names. No existing construction changes.

## 7. Tests

`tests/stdlib/test_tui_route.spr` — 47 assertions, all pure:

- a leaf claims its own id and ignores another's; `no_route` claims nothing
- a declined delivery carries **no reply at all**, distinguished from one carrying an empty
  message list
- an addressed *event* reaches the same leaf, and respects the address
- a container reaches the addressed child, reaches its sibling by the sibling's id, descends
  more than one level, and reports an unclaimed address as unhandled
- a duplicated id resolves to the first in tree order, and is delivered exactly once
- `cmd_to` carries the asker's id, `cmd` does not, and **`cmd_map` preserves both** — the case
  that would rot silently
- the prism: an embedded-vocabulary delivery routes through and comes back retargeted, a
  foreign message is not delivered and produces no reply, an event crosses untouched
- `step_to`: a routed answer reaches the widget then `update`; an unroutable one falls back to
  `update` carrying the original message; the routed widget's command leads the application's;
  a routed answer can still quit
- `route_when`: a handler's decline is reported unhandled and carries no reply, while the same
  widget claims a delivery it did use — and a container propagates both verdicts, which is
  what lets an unused key reach `update`
- `namespaced`: a widget answers to its qualified id and no longer to its bare one nor to
  another namespace's; two copies of one widget are told apart by return address; an outgoing
  command is readdressed; namespaces nest in both directions; `measure` is untouched

**What is not covered by an automated test.** The `SigTo` arm of `run`, for the same reason
`run` has never been covered: it takes over the terminal and blocks on stdin.

Verified by hand with an A/B whose discriminator is which path ends the program. A widget
asks for work addressed to itself on a keypress; its `route` replies `Done`, which quits,
while `update` on the same answer returns `Continue`. Stdin was fed one key and then held
open for 8 seconds, and the program timestamped **its own** exit — timing the shell pipeline
measures the `sleep` and proves nothing, a mistake made once already on the `SigMsg` path.

| build | `run()` returned after |
|---|---|
| `route_if(wid(), on_route, …)` | **1 ms** |
| identical program, `no_route` | **7679 ms** (i.e. EOF) |

So the answer travelled task → channel → `SigTo` → `deliver` → `route`, and EOF is
demonstrably not what ended it. The control is what makes the 1 ms mean something: with
routing removed and nothing else changed, the same keypress leaves the app running to EOF.

`examples/tui_dashboard.sprout` was then run under a **pty** (`script -q`), which is what
makes `TermIdle` fire at all — a piped stdin never idles, so the tick that drives the clock's
command does not exist without one. Over a 3-second run its uptime line advanced `up 0s` →
`up 2s` and it quit cleanly on `q`. That number can only advance if the clock's addressed
answer reached the clock widget, so it is the same round trip observed in the real example
rather than a scratch program.
