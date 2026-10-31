# A regex engine in Sprout (v0, 2026-10-31)

Status: proposal. Moves `stdlib.regex` off libc's POSIX engine. Part of stage 2 of
[runtime-memory-safety-v0.md](runtime-memory-safety-v0.md), which lists `regex_validate` and
`regex_find_match` as staying C "for libc's regex engine". This doc is the case for not staying.
It also deletes the `<regex.h>` include that blocks the Windows port (`BACKLOG.md`: **POSIX
`<regex.h>` has no MSVC equivalent**), choosing "own an ERE engine" over narrowing the feature.

`stdlib.regex` is not in the spec (README §"Implemented, not yet in the spec");
[regex-v1-draft.md](regex-v1-draft.md) is its document of record, and this change updates it,
not `spec-v0.md`.

## 1. Problem

`stdlib.regex` is five builtins (`regex_validate`, `regex_is_match`, `regex_find_match`,
`regex_replace_all_literal`, `regex_escape`) over `regcomp`/`regexec` with `REG_EXTENDED`, plus
`regex_translate_pattern`, a C translator from Sprout's syntax to POSIX ERE.

**Every call recompiles the pattern.** `Regex` holds only the pattern string; `compile` validates
it and throws the result away, and each `is_match`, `find_first` and `replace_all_literal` runs
`regcomp` and `regfree` again. Measured on macOS libc, Apple M3 Pro, three runs (`regcomp` +
`regfree` in a loop, then `regexec` alone on one compiled pattern):

| pattern | text | `regcomp` | `regexec` | `regexec` per byte |
|---|---|---:|---:|---:|
| `error` | 44 B, hit | 1.5–1.6 µs | 0.2 µs | — |
| `error` | 1 KB, hit at end | 1.5–1.6 µs | 9.0–10.3 µs | 8.7–9.9 ns |
| `(GET\|POST) /[a-z0-9/_]+\.(html\|json)` | 55 B, hit | 5.8–6.2 µs | 1.1–1.5 µs | — |
| same | 1 KB, hit at end | 5.8–7.2 µs | 13.7–15.0 µs | 13–14 ns |

On a short text, compiling is 80–88% of the call.

**`.` and classes match bytes.** The runtime never calls `setlocale`, so `regcomp` runs in the C
locale, where POSIX.1-2024 §6.2 gives 256 single-byte characters. Probed on macOS libc and on
glibc 2.39 (the `linux-smoke` image), same results on both:

| pattern | text | result |
|---|---|---|
| `^.$` | `é` (2 bytes) | no match |
| `^..$` | `é` | match `[0, 2)` bytes |
| `^[é]$` | `é` | no match |

`find_first` converts byte offsets with `sprout_utf8_codepoint_prefix_count`, which rounds an
offset inside a character up to the next boundary. A match of half a character comes back as a
span shifted to whole characters, not the bytes that matched. `regex-v1-draft.md` promises "the
same code-point indexing model as `stdlib.string`".

**The two libcs accept different patterns.** Probed the same way:

| pattern | macOS (TRE) | glibc 2.39 |
|---|---|---|
| `a**`, `a+*`, `a?+` | error | `a*` |
| `a\|` | error | empty alternative |
| empty pattern | error | matches empty |
| `(\|a)` on `a` | error | `[0, 1)` |

A Sprout program that compiles on Linux can fail `compile` on macOS.

### 1.1 Fixes at each layer

- **Caller (a Sprout wrapper over `regex_find_match`).** Measured and failed: `regex_is_match` as
  a Sprout call to it is 9.9% slower on a late match in 1 KB
  (`bench/results-2026-10-31-regex-is-match.md`, branch `move-regex-is-match`). It counts code
  points twice and allocates, and it keeps the recompile.
- **Function (keep libc; hold the compiled `regex_t` in `Regex`; compile under a UTF-8 locale).**
  Fixes the recompile and `.`, with one changed builtin. It loses on four counts. The runtime has
  no finalizers, so a `regex_t` held by a GC value is never `regfree`d; the alternative is an
  explicit `free` in the API. The platform split above stays. The MSVC blocker stays. And the
  builtin count stays, against stage 2's direction.
- **Representation (own the engine).** This doc. It removes all three problems and the five
  builtins. The cost is the engine and the risk that it is slower (§6).

## 2. Goals and non-goals

Goals:
- All of `stdlib.regex` in Sprout. The five builtins, the translator, `regex_compile_ere`,
  `regex_builtin_fail` and `regex_prefixed_error` are deleted.
- `compile` builds the matcher once and `Regex` holds it.
- Time linear in the text for every pattern.
- `.`, classes and offsets count code points. `.` matches every code point, newline included, as
  both libcs do today without `REG_NEWLINE`.
- One grammar (§4.4) on every platform.
- The same public API except `regex_find_match`, which `stdlib.regex` exports today
  (`export extern fn`) and which goes with the builtins; `find_first` is its public form. Same
  leftmost-longest spans as today on ASCII text, for `find_first` and `split_first`.
- No benchmark row slower than master (the roadmap's no-slower rule).

Non-goals: captures, non-greedy and counted repetition, backreferences, lookaround,
case-insensitive matching, Unicode `\d`/`\w`/`\s` (they stay ASCII), and leftmost-first
semantics. Each is rejected or absent today.

## 3. Prior art

| | engine in | algorithm | linear | semantics | offsets | `.` |
|---|---|---|---|---|---|---|
| Go `regexp` | Go | Pike VM, one-pass, bounded backtracker | yes, documented | leftmost-first; `CompilePOSIX`/`Longest` give leftmost-longest | bytes | code point |
| Rust `regex` | Rust | Pike VM, bounded backtracker, lazy and full DFA | O(m·n) | leftmost-first only | bytes | code point |
| Haskell `regex-tdfa` | Haskell | tagged DFA (Laurikari) | O(n) claimed, hedged | POSIX ERE | code point on `String`/`Text`, byte on `ByteString` | code point on `String`/`Text` |
| Java `java.util.regex` | Java | backtracking | no | leftmost-first | UTF-16 units | code point |
| Python `re` | C matcher, Python parser | backtracking | no | leftmost-first | code point on `str` | code point |
| OCaml `Str` | C matcher, OCaml compiler | backtracking | no | leftmost-first | bytes | byte |
| macOS libc (today) | C, TRE | tagged NFA; backtracking only with backrefs | — | POSIX leftmost-longest | bytes | byte in the C locale |

Sources: [pkg.go.dev/regexp](https://pkg.go.dev/regexp), Go `src/regexp/exec.go`;
[docs.rs/regex](https://docs.rs/regex/latest/regex/) and `regex_automata::MatchKind`;
[regex-tdfa](https://hackage.haskell.org/package/regex-tdfa) and its README; the Java 21
`Pattern` docs and `Pattern.java`; [Python `re`](https://docs.python.org/3/library/re.html) and
`Modules/_sre/sre_lib.h`; [OCaml `Str`](https://ocaml.org/manual/5.3/api/Str.html) and
`otherlibs/str/strstubs.c`; Apple Libc `regex/TRE/lib/regexec.c`; POSIX.1-2024
[§9](https://pubs.opengroup.org/onlinepubs/9799919799/basedefs/V1_chap09.html) (`.` follows
the locale; earliest start, then longest) and
[§6.2](https://pubs.opengroup.org/onlinepubs/9799919799/basedefs/V1_chap06.html).

What this design takes:
- **Engine in the language itself, compiled once into a value**: Go, Rust, Haskell, Java. Rust's
  docs call compiling in a loop an anti-pattern; Python caches compiled patterns.
- **Leftmost-longest by state set**: Go's `machine.step` in longest mode drops a thread that
  started after an already-found match, and keeps a later end over an earlier one. §4.2 does the
  same with one start per state instead of one thread per instruction, because no captures are
  reported.
- **Code point `.`**: every engine here that reads text as characters. Only the byte-oriented
  ones (`Str`, libc in the C locale) match a byte.

Where they diverge: leftmost-first (Go's default, Rust, Java, Python) would change today's spans
(`a|ab` on `ab` is `[0, 2)` now and `[0, 1)` under leftmost-first), so this stays leftmost-longest.
Byte offsets (Go, Rust) would change what `Match` means; the scan counts code points for free
(§4.2), so this keeps code points.

## 4. Design

### 4.1 Compile

`compile` parses the pattern (§4.4) into a syntax tree, builds a Thompson NFA, and removes its
epsilon edges. What remains is the states that consume one code point, plus one **accept bit**.
A set of states is one 64-bit `Int` when there are at most 63 states. The two example patterns
above have 5 and 19.

**Contexts.** Anchors are the only zero-width items (no `\b`, no lookaround). An offset in the
text is in one of four contexts: start (offset 0), end (offset = length), both (empty text), or
middle. `^` holds only in start and both, `$` only in end and both. Each state's epsilon closure
is computed per context, and the accept bit is part of that closure. So whether a match may end
at an offset depends on that offset's context: after `a` in `a$`, the accept bit is set only in
the end context. `a^b` and `e$f` are legal ERE and never match, as on both libcs.

`Regex` becomes an opaque record:
- `first[ctx]`: the closure of the start state, per context;
- `follow`: the closure after each state, for the middle context, as byte-sliced tables (below);
- `follow_end`: the same per state, for the end context. A step always lands at an offset of 1
  or more, so the start and both contexts never apply to `follow`;
- `ascii`: 128 masks, the states whose class accepts each ASCII code point;
- the classes themselves, tested in order for a code point ≥ 128.

**Byte-sliced `follow`.** Sprout has no count-trailing-zeros or popcount, and a shift by a
non-literal count lowers to a five-block guard (`ast_to_ir`, the shift guard). So `follow(mask)`
is not computed by walking set bits. The mask is cut into bytes, and each byte indexes its own
256-entry table holding the OR of the follow sets of those 8 states. A pattern with `n` states
needs `c = ⌈(n + 1) / 8⌉` lookups per step: 1 for `error`, 3 for the class pattern. The step is
unrolled per `c` with literal shift counts, so it needs no guard. The tables hold `256·c`
`Int`s, built in `compile`: a cost the `compile` row must absorb (§6).

### 4.2 Match

The text is read as UTF-8, one code point per step. An ASCII byte is one `bytes_get` and one
`ascii` lookup. A longer sequence is decoded with `stdlib.unicode.utf8`'s `seq_length` and
`codepoint_at` and tested against the classes, the slow path.

`active` holds the states ready to consume the next code point. The scan visits offsets 0 to
length **inclusive**. At each offset it ORs in `first[ctx]` (while it still injects starts) and
checks the accept bit. Then, unless the offset is the length, it consumes a code point:
`next = follow(active & accepts(cp))`. The states that accept `cp` fire, and their closures, in
the context of the offset reached, form the new set. The `accepts` masks never hold the accept
bit, so it is cleared before `follow` and set again only by a closure that reaches it. The visit
at offset = length is what lets `$`, `x*$` and an empty match at the end succeed.

- **`is_match`**: unanchored, so starts are injected at every offset. It stops at the first
  accept bit.
- **`find_first`** (leftmost-longest span, POSIX §9). Two threads in one state at one offset
  have the same future, so the earlier start dominates: it reaches every end the other can, and
  the leftmost start wins. So each state needs only its earliest start. The byte-sliced tables
  cannot carry that per state, since an entry merges 8 states. Instead the live threads are
  **start groups**: pairs `(start, mask)`, ordered by start, with disjoint masks. A step runs the
  groups earliest first. Each computes `follow(mask & accepts(cp))` with the same tables,
  removes the states an earlier group already claimed, and drops out if nothing is left. A
  new start is a group appended last, with `first[ctx]` minus the claimed states. The number of
  groups is at most the number of live states, and usually 1 to 3. So a step costs that many
  table steps, with no walk over set bits. Injection stops once a group reaches the accept bit.
  The scan stops once no group started at or before the best match's start. The groups live in
  two `LinVec Int`s (stdlib.linvec, in place under linearity), compacted each step; each write
  is a `vector_mutset` call.
- **Two phases for `find_first`.** First an `is_match`-style mask scan finds `E`, the earliest
  offset with the accept bit set. A miss ends there, as cheap as `is_match`. Every match ends at
  `E` or later. A pattern whose matches are at most `L` code points long (no `*` or `+`) then
  has its leftmost start at or after `E − L`: a match starting earlier would end before `E`. So
  the start-tracking scan runs only over `[E − L, …)`. Unbounded patterns run it from 0.
- **Offsets come free.** The scan counts code points as it decodes them, so `Match` gets code
  point offsets with no prefix count. It also keeps byte offsets for `split_first` and
  `replace_all_literal`, which slice with `str_slice_bytes`.
- **More than 63 states**: masks become a `LinVec Int` of words. This is a separate, slower path
  with its own benchmark row. Both libcs accept such patterns, so rejecting them would be a
  regression.

### 4.3 Text access

Today Sprout reads a `String`'s bytes by copying it into `Bytes` (`bytes_from_utf8`) and calling
`bytes_get`. A `match` on it with plain `Just x`/`Nothing` arms lowers to `bytes_get_unboxed`
(`ast_to_ir.unboxed_maybe_extern_name`). That does not allocate, but it is a C call per byte.
`vector_get`, behind the `follow` and `ascii` lookups, is the same. So an ASCII step in the
middle context is `2 + c` C calls. The roadmap's missing primitive 1 (an O(1) byte read on
`String`) removes the copy. Inline loads (byte-buffer-v0.md §3: 6.5× → 2.6× C) remove the calls.
Stage 0 (§6) measures whether the engine needs either before it can pass.

### 4.4 Grammar

```
regex    = alt
alt      = concat { "|" concat }
concat   = { repeat }
repeat   = atom { "*" | "+" | "?" }
atom     = char | "." | "^" | "$" | class | escape | "(" alt ")"
class    = "[" [ "^" ] [ "]" ] { item } "]"
item     = cchar [ "-" cchar ] | "[:" name ":]" | escape
escape   = "\" ( "d" | "w" | "s" | meta )
meta     = one of  \ . ^ $ * + ? ( ) [ ] | { } -
name     = alpha | digit | alnum | upper | lower | space | punct | xdigit | blank | cntrl
           | graph | print
```

`char` is any code point but `\ . ^ $ * + ? ( ) [ | { }`; `cchar` is any code point but `\` and
`]`. A `-` first or last in a class is literal. A quantifier on `^` or `$` is an error.

An empty `concat` matches the empty string, so the empty pattern, `()`, `a|` and `(|a)` are
legal, as in glibc, Go and Python. These are errors:
- a quantifier with nothing before it (`*a`, `a|*`, `(*a)`) or after an anchor (`^*`);
- an unmatched `)`, as in Go and Python. A `]` outside a class is an ordinary character, as in
  POSIX.

`a**` and `a+*` are `a*`; `a?+` is `a*`.

Rejected with `RegexUnsupportedFeature`, with today's texts:
- backreferences;
- `{` and `}` ("counted repetition");
- a quantifier followed by `?` ("non-greedy quantifiers");
- `(?` ("extended group syntax");
- an unknown escape;
- `[=` and `[.` inside a class (new: "equivalence and collating classes").

Invalid patterns get `RegexInvalidPattern "invalid regex pattern: <what>"`. The parser returns
`RegexError` directly, so `classify_compile_error`'s prefix match goes away. The
`RegexUnsupportedFeature` payload stays the text after the prefix, as today.

### 4.5 The rest

- **`replace_all_literal`** loops `find` over byte offsets of the **whole** text. `^` therefore
  matches only at offset 0. Today the C loop calls `regexec` on each remaining suffix without
  `REG_NOTBOL`, so `^` matches again after every replacement (§5). An empty match copies one
  code point and advances, as the C loop does. The pieces are joined once with
  `string_concat_many`.
- **`escape`** is a byte scan, already on the roadmap's movable list.

## 5. Behaviour changes

On ASCII text, `find_first` and `split_first` spans stay the same for every pattern the grammar
accepts. The differential test (§7) holds this. Everything that changes:

| | today | after |
|---|---|---|
| `.` on non-ASCII | one byte | one code point; `^.$` matches `é` |
| class on non-ASCII | bytes: `[é]` never matches `é` | code points; `[à-ž]` is a code point range |
| span inside a character | rounded to whole characters | cannot occur |
| `\` before a special character inside `[...]` | literal `\`: `[\.]` is `\` or `.` | escape: `[\.]` is `.`; `[\\]` is `\` (`[\d]`, `[\w]`, `[\s]` and unknown escapes do not change) |
| `^` in `replace_all_literal` | matches after each replacement: `^a`, `X`, `aaa` gives `XXX` | text start only: `Xaa` |
| `a**`, `a+*`, `a?+`, `a\|`, empty pattern, `(\|a)` | per platform (§1) | the first three are `a*`; the rest match empty, as on glibc |
| unmatched `)` | both libcs: a literal `)` | error |
| `[[=a=]]`, `[[.a.]]` | both libcs accept | `RegexUnsupportedFeature` |
| invalid-pattern text after the prefix | each libc's `regerror` | the parser's own |
| `regex.regex_find_match` | exported | removed; `find_first` is the public form |

Downstream: GitHub code search over the default branches of all `cthulhu666` repos finds
`stdlib.regex`, and any `.sprout` file mentioning `regex`, only in sprout-lang.

## 6. Stages and the gate

0. **Bench, then prototype.** First land `bench/regex_is_match` and its results from branch
   `move-regex-is-match`, extended to the rows below. That defines the baseline. Then hand-write
   the tables for the two bench patterns and time the §4.2 loops over `bytes_get`: `is_match` and
   `find_first` on the 1 KB late-hit rows. The class pattern has `+`, so its `find_first` runs
   the start-group scan from offset 0. Go if both beat master's per-call time (`regcomp` +
   `regexec`, plus the prefix counts for `find_first`). If not, missing primitive 1 and inline
   loads come first, each with its own design note, as the roadmap says.
1. **Engine.** The parser, the NFA, the tables, `is_match`, `find_first` and the rest behind the
   existing API, with the builtins still in place, plus §7's tests.
2. **Gate, then delete.** Benchmark under the roadmap's rule. If every row passes, delete the five
   builtins and the translator. Then port the regex cases in `tests/c_runtime/` and the two tests
   that use regex externs as ABI probes (`test_bool_extern_abi.spr`,
   `test_ir_codegen_cpr_maybe_externs.spr`) to another extern of the same shape.

Rows, each against master:
- the 8 `is_match` rows of `bench/regex_is_match`;
- the same 8 for `find_first`;
- `split_first` and `replace_all_literal` on 1 KB with many matches;
- `compile` for both patterns (today `regex_validate`: translate, `regcomp`, `regfree`);
- a non-ASCII text;
- a pattern over 63 states.

**Risks, most likely first:**
- **`compile`.** A Sprout parser, NFA and `256·c` table entries have to beat 1.5–6 µs of
  `regcomp`. Every matching call gets that time back, but the rule is per row.
- **`find_first` on an unbounded pattern, late hit.** The text is read twice: the mask scan to
  `E`, then the start-group scan from 0, with `LinVec` writes per group per step. Together they
  must beat `regcomp` + `regexec` + two prefix counts, about 10 ns per byte on 1 KB.
- **The over-63 path**, which has neither single-`Int` masks nor tables.

## 7. Tests

- `tests/stdlib/test_regex.spr` stays and must pass unchanged, except for invalid-pattern
  message texts.
- **Grammar**: every §4.4 error case and every §5 row, as a direct assertion.
- **Differential.** Random patterns are drawn only from forms on which the grammar and both libcs
  agree: no undefined forms, no `\` inside a class. Texts are random ASCII including `\n`, and
  anchors appear anywhere in the pattern. A small C program under `tests/` gives libc's results,
  which must equal the engine's for `find_first` spans and for `replace_all_literal` output on
  patterns without `^`.
- **Contexts**: `$`, `a$` on `ab`, `x*$`, `a^b`, `^$` on the empty text, `(a|$)` and `(^a|b)*`.
- **Code points**: each §5 non-ASCII row, plus a span next to a 4-byte character.
- **Linear time**: `(a|aa)*b` and `(a*)*b` on growing inputs of `a`, with the cost per byte
  bounded across sizes, as the `rooting-cost-gate` growth arm does.
- **More than 63 states**: 100 alternated literals, matched and missed.

## 8. Docs

- `regex-v1-draft.md`: the grammar, code point rules, compile-once and the `^` rule for
  `replace_all_literal`.
- `runtime-memory-safety-v0.md` stage 2: the regex lines point here.
- `native-sprout-runtime-v0.md` §5 ("POSIX `regex.h` stays until there is a reason…"): the
  reasons are §1.
- `builtins-reference.md` and `APPROVED_BUILTINS`: the five names go.
- The `stdlib/prelude.sprout` comment on regex primitives, the `json-regex` case in
  `tests/c_runtime/run.sh`, and the golden IR for `examples/regex_demo.sprout` (regenerated, its
  diff read).
- `BACKLOG.md`: one entry for this doc, deleted when stage 2 lands. The MSVC regex entry is
  retired by it.

## 9. Open questions

- More than 63 states: a `LinVec` of words, or two `Int`s up to 126 states first?
- A literal prefix (`error`) could skip ahead with a byte search. That needs a byte-offset
  `str_find`, since today's counts code points. Only worth it if stage 0 says so.
- Tables cost `256·c` entries per pattern. If the `compile` row fails, build them lazily per byte
  value seen.
