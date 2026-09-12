# Stale replies — `stdlib.version` and `stdlib.stamped` (v0)

Status: normative for the two modules it names; the adoption in `ide/` is the worked example.

## 1. The problem

A command runs in its own task (`stdlib/tui/app.sprout:239` spawns one), so the answer to a
request lands an unbounded number of loop steps after the request that produced it. The asker
keeps running in between. An answer therefore describes the moment it was *asked for*, and
reading it as a description of the present is wrong.

It shipped as a data-loss bug in the IDE's editor pane. Save `a.txt`; before the write answers,
open `b.txt`; the answer arrives and the pane rebuilds its document from it. The pane now shows
B's text with A as its save target, and the next ctrl-s writes B over A. The same shape, milder,
on the read side: open A, open B, A's body arrives last and the pane shows the file the user did
not pick.

## 2. Two kinds of irrelevance

These are different, and conflating them is why most off-the-shelf answers do not fit:

| | what happened | example |
|---|---|---|
| **Superseded** | a newer request of the same kind exists | open A, open B — A's body is garbage |
| **Invalidated** | no newer request, but the state the reply was *about* moved | save, then type |

A save is not superseded by anything; the buffer simply changed under it. Any mechanism that only
cancels a previous request — `AbortController`, a switch-to-latest combinator — fixes the first
row and leaves the data-loss bug standing.

## 3. Design

**The check is pure.** Widget state advances through `on_event: s -> event.Event -> (s, List m,
List (Cmd m))` (`stdlib/tui/widget.sprout:120`), which has no `!{IO}`. A widget cannot await, and
cannot read a clock. Whatever carries "when" must be an ordinary value.

**Not a timestamp.** `stdlib/time.sprout` says `wall_micros` is not monotonic — NTP slew and manual
changes move it backwards. `now_micros` is `CLOCK_MONOTONIC` but both are `!{IO}`, and both are
monotone in *time* rather than in *state*: two edits inside one microsecond share a stamp, and a
stamp that does not change when the state changes is the failure being removed.

**Equality, not ordering.** Two comparisons are available and they ask different questions:

- *equal to current* — "is this still exactly relevant?"
- *at least the last applied* — "is this newer than what I have already used?"

For a fact about a moment — *these exact bytes reached the disk* — ordering is not merely weaker,
it is wrong: an answer newer than the last one applied but older than the current buffer is still
a lie. `fresh` therefore compares for equality, which is the stricter of the two.

**So the stamp must never repeat.** Equality is only sound if a stale stamp cannot find a matching
present; the ABA case (3 → 4 → 3) is exactly what ordering is usually reached for. The repetition
is the defect, so it is fixed in the stamp rather than papered over in the comparison:
`version.Version` has no constructor from `Int` and no accessor, so a value is reachable only by
stepping from `origin`.

A false *negative* under `fresh` — a reply dropped that was still fine — means the stamp is too
broad (an edit count inside a directory listing's stamp), not that ordering is needed.

## 4. Prior art

React's Effect documentation carries this exact race (`fetchBio('Bob')`, `fetchBio('Taylor')`,
Taylor completing first) and fixes it with a flag on the receiver, not with a better promise:

```js
let ignore = false;
fetchBio(person).then(result => { if (!ignore) setBio(result); });
return () => { ignore = true; }
```

The lesson taken here is that a promise solves *delivery*, not *relevance*, and a language with
first-class promises still needs a receiver-side check. Sprout already has the delivery half —
`task_fork` returns a linear `Task a`, awaited with `task_await` — and the TUI discards it at the
`Cmd` seam, where a typed future becomes an untyped message. `Stamped` puts back the one bit that
conversion loses.

## 5. The API

```sprout
# stdlib.version
export type Version deriving (Eq, Ord, ToString)   -- constructor private
export fn origin() -> Version
export fn next(v: Version) -> Version

# stdlib.stamped
export type Stamped s a deriving (Eq, ToString)    -- constructor private
export fn stamp(now: s, x: a) -> Stamped s a
export fn fresh(now: s, st: Stamped s a) -> Maybe a where Eq s
```

Both are pure and O(1). The enforcement is what is *absent*: there is no `unstamp`, no field
accessor, and no `Stamped(..)` export, so the only route to the payload runs through `fresh`.
Neither is a `wrap`, which §7 would otherwise ask for: exporting a `wrap` exports its constructor
(spec §5.6.1), and a `Version` anyone can mint is not a version at all. A sealed single-constructor
ADT is the only shape that hides one, at one small allocation per step.
Forgetting the check is not a mistake that can be made — it is not expressible. Same move as
`wrap FilePath = String` (`docs/guidelines.md` §7): zero cost, and the error becomes a compile
error.

`s` is the caller's, because only the caller knows what a reply's relevance depends on. A
`Version` alone says *something* moved; pair it with an identity when *what* moved matters.

## 6. Adoption in `ide/`

Both of the editor pane's answers are stamped, and both handshakes have the same shape: the pane
is asked, mints a receipt, and `ide/app.sprout` ferries that receipt through the IO and back
without ever reading it.

| | request | pane answers | IO | reply |
|---|---|---|---|---|
| open | `Choose` → `on_open` | `ReadFrom path ver` | `read_at` | `Loaded (Stamped ver (path, body))` |
| save | `HandOver` → `on_demand` | `SaveTo path text ver` | `write_body` | `Stored (Stamped ver path)` |

`Pane.ver` advances on every change to what the pane shows — a file claimed, a body filled in, a
key typed, a write confirmed. Being *asked* for the text does not advance it: the receipt has to
name the pane the text came from.

Three consequences worth naming:

- **A claim mints a receipt and changes nothing else.** It exists only so a body arriving later has
  something to be checked against. The first cut also adopted the path at claim time, to show the
  name before the text; that gave the pane a save target it had not read, and a ctrl-s during a slow
  or failing read wrote the empty buffer over the file. Path and text are adopted together, in
  `begun`, or not at all — a stamped *pair*, not a stamped body.
- The pane no longer remembers its handover. The receipt carries it, so the `edits`/`handed` pair
  the first fix used is gone, and with it the chance of checking one and not the other.
- `confirmed` does not read the answer's payload. `fresh` has already proved the path is this
  pane's own, so `document.written` — which clears dirt and nothing else — is all that runs. A
  `Stored` cannot teach a pane a path; naming a pathless buffer is what a save-as is for.

`Listed`, the file tree's reply, is deliberately **not** stamped: it is keyed by the path it
describes and applied to that node, so a late one restates a directory rather than misdescribing
another. `docs/tui-routing-v0.md` §3.4 calls this out as the harmless case.

## 7. Verification

`tests/stdlib/test_stamped.spr` covers the primitive, including the ABA case equality would wave
through if a version could repeat. `tests/ide/test_ide_editor.spr` drives both handshakes end to
end and pins the version in every expectation on purpose: an unplanned extra bump would invalidate
receipts still in flight, and the numbers are where that shows.

The guards were checked by mutation — `fresh` blunted to `Just(x)` turns all three stale cases red,
and the read-race case then shows A's text where B's belongs.

## 8. What this does not fix

**Ordering of the effects themselves.** `fresh` filters the answers a receiver accepts; it says
nothing about the order two writes reach the disk. Save, edit, save again puts two `write_body`
tasks in flight; if the second lands first, its `Stored` is accepted and the first then overwrites
the file with the older text. No stamp can see that, because the losing write is not a stale
*reply* — it is a live *effect*. The fix is to keep one write outstanding per pane. Filed in
`BACKLOG.md` §4.5.

The general form is worth stating: staleness checking makes a receiver safe against answers that
outlived their question. It does not make the world safe against requests that outlive each other.

## 9. Deferred

- **`at_least` for cache-like replies.** LSP diagnostics for document version 5 land, then version
  4's arrive late; both are stale against the buffer, but showing 4 after 5 is worse than showing
  5. That wants a comparison against the last *applied* stamp, which is a second function rather
  than a different rule for `fresh`. Not added: `docs/ide-v0.md` §9 has the LSP pane deferred, and
  there is no call site to shape it against.
- **Correlation in the framework.** `Cmd` carries an address (`widget.sprout:53`) and no
  correspondence, so every widget author has to remember to stamp. Three replies in the IDE meant
  three chances to forget, and the first pass forgot all three. Putting the receipt in
  `stdlib/tui`'s reply path would make it structural, and is worth doing once a second stateful
  widget exists to confirm the shape.
