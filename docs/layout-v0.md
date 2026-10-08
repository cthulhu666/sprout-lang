# Layout pass v0

Status: implemented (issue #456). Normative rule: `docs/spec-v0.md` §2.1 "Layout".

## Problem

Each block construct found its own end with its own token scan: `scan_do_step_end` and
`looks_like_do_step_start` for `do` steps, `parse_let_block` for `let … in`,
`do_let_first_binding_end` for a `do`-`let` group, `branch_col` for match arms,
`collect_*_layout` for class and instance bodies, and a greedy parse for `where`. Each was a
second source of truth next to the expression parser, and they disagreed at the edges. PR #453
spent five review rounds on one of them. A `do` block inside call parentheses could not end at
the `)`, because the scanner clamped bracket depth at 0.

## Goals / non-goals

- One pass turns indentation into virtual tokens. The parser reads those and never a column.
- Every program the old parser accepted parses to the same AST, apart from the fixes listed
  under "Behaviour changes".
- Non-goal: new syntax. A single-line `do` (`do print(1)`) stays an error, as before.
- Non-goal: explicit `;` and `{ }` for blocks. The tokens are virtual only.

## Prior art

| Language | Where layout lives | What closes a block |
|---|---|---|
| Haskell 2010 §10.3 | function L over tokens | dedent, explicit `}`, and the parse-error(t) rule |
| GHC | lexer, plus parser feedback | dedent; `close : vccurly \| error {% popContext }` in `Parser.y` |
| PureScript (`CST/Layout.hs`) | pure token pass | dedent, brackets, `,`, `in`, `then`/`else`, `where` |
| F# (spec "Lexical Filtering") | `LexFilter.fs` pre-parse stack | offside, closing brackets, keywords |
| Python (ref §2.1.6, §2.1.8) | tokenizer INDENT/DEDENT | dedent; no layout inside brackets |
| Lean 4 (`Parser/Basic.lean`) | parser combinators | `checkColGe` / `checkColEq` fail |

Haskell's parse-error rule needs the parser: GHC's `Note [Layout and error]` says L "is
distributed between Lexer.x and Parser.y". PureScript drops the rule. Its header says Haskell
"has a few problematic productions which make it impossible to implement a purely lexical layout
algorithm", and that PureScript does not have them. Sprout does not have them either, so it
follows PureScript: a pure pass, with explicit closer tokens in place of the parse-error rule.

## The pass

`stdlib/compiler/layout.sprout`, run by `parser.parse_program` and `parser.parse_expr`. It emits
three token kinds, `TokenLayoutOpen`, `TokenLayoutSep`, `TokenLayoutClose`, each at the position
of the next real token, so AST positions do not move.

It keeps a stack of:

- **blocks** — `Do c`, `Match c`, `Let c f`, `Where c f`, `Body c f` (class/instance members),
  `Root`. `c` is the item column; `f` is the floor: for `Let` and `Where` the threshold the
  block opened over (0 inside a bracket), for `Body` the declaration's column. `Root`'s column is 1, and a line in it that
  starts with a declaration keyword starts an item at any column, so declarations may be
  indented.
- **brackets** — `(`, `[`, `{`, a template, a template interpolation.
- **markers** — `if`, `then`, `match`, waiting for their `then`, `else`, `with`.

Opening. `do`, `with` (after a `match`), `let` (not a top-level declaration) and `where` (after a
function body's `=`) open a block at the next token. A class or instance body opens at the first
line that starts with `fn` right of the declaration's column; a `fn` on the head line is an
error. A block whose first token sits at or left of the enclosing block's threshold is empty:
the item column of a `Do`, `Match` or `Root`, or the floor of a `Let`, `Where` or `Body`. Match
arms alone may open on the threshold, since `|` cannot be read as an item. A `do` whose first
token is on the `do` line is empty too, as before.

A new line, outside brackets, is compared with the innermost block:

| Block | Closes when the line starts | Separator when the line starts |
|---|---|---|
| `Do c` | left of `c` | at `c`, with a token that can begin a step |
| `Match c` | left of `c`, or at `c` with anything but `\|`, `then`, `else` | never: `\|` separates arms |
| `Let c f` | left of `f`, or at `f` with an item start | right of `f`, shaped `<pattern> =` |
| `Where c f` | left of `f`, or with a declaration keyword at any column | at or right of `f`, shaped `<pattern> =` |
| `Body c f` | at or left of `f`, unless a member's `where`; or with a declaration keyword other than `fn` | a `fn` at `c`; a `fn` elsewhere in `(f, …)` is an error |

Three exceptions skip the comparison. A line after a token that cannot end an item (`->`, `=`,
`<-`, an operator, `if`, `then`, `else`, `in`, `match`) continues the item: `| Nothing ->`
followed by the arm's body in the arm column is house style in `infer.sprout`. A line that
starts with the `then`, `else` or `with` its `if` or `match` marker waits for continues the
item at any column, since nothing else can take that token. And a line
starting with `in` never closes a `Let` by dedent; the `in` rule closes the innermost one, so
`let total =` / `let a = 1` / `in a + 1` binds the `in` to the inner group.

Two more rules sit in the table's margins. In the floor column a `Let` closes only on a line
that starts an item there (a step, `|`, a declaration), so `else` at the `do` column still
continues `let n = if c then`. A `Where` closes at a line starting with a declaration keyword,
at any column, so indented declarations end it, and its bindings may sit in the declaration's
own column, as the greedy parse allowed. A `where` whose next token is a declaration keyword
is empty. And a declaration keyword (`fn`, `type`, `class`, `instance`, `extern`, `export`)
closes every expression block, at a line start or mid-line, so a `where` group or a `match`
ends at the next member even inside a one-line brace-form body. Wherever it sits, it starts a
declaration or member, so a `fn`'s `where` opens bindings. A `let` right after an operand on
the same line does the same: no expression continues with a `let`.

Closers, which never reach past a bracket:

- `)` `]` `}` and template ends close every block above their bracket. So does `,`.
- `in` closes every block down to and including the innermost `Let`.
- `then` and `else` close `Match` blocks down to their marker, so at the arm column they end
  an `if` inside the arm, or one around the `match`. `else` with no `then` marker
  (a `let`-`else` or `<-`-`else`) closes the `Match` blocks above the innermost other block.
  A comprehension's `for` closes `Match` blocks too, and so does its `if` guard: an `if`
  right after an operand in a bracket's own expression can only be a guard, since `if` is a
  prefix form. In a `do` opened inside the bracket, it starts the next step.
- `with` closes down to its `match` marker and opens the arms.
- A function-body `where` closes every block above the declaration.

A `{` right after a class or instance head is a brace-form body: a bracket that still tracks
its members' `=`, so a member's `where` opens bindings. It ends the head, so a `fn` after the
`}` is a new declaration.

## Sprout deviations from Haskell

1. **Match arms may share the enclosing column.** `match … with` and its `| …` lines at the
   `do` column is the house style. A `Match` block ends at a line in its column that is not `|`,
   `then` or `else`.
2. **A step starts only on a token that can begin one.** `else`, `then`, `|`, operators and
   closing brackets at the block column continue the step. GHC's DoAndIfThenElse is the same
   idea for `then`/`else`.
3. **`let` and `where` groups are relaxed.** A line right of the floor is a continuation, or a
   new binding if it reads `<pattern> =`, at any column. The corpus has ~166 lines of
   `let x =` with the right-hand side on the next line, left of `x`; strict layout rejects
   all of them. A new block may open anywhere right of the floor, so a nested `do` may sit
   left of the outer binding (issue #456's repro).

## Behaviour changes

These accept programs the old parser rejected:

- A `do` block closes at `)`, `]`, `}` or `,` on its last line (BACKLOG P2, now removed).
- A one-line `let … in` works anywhere: `fn f(n) = let x = n + 1 in x + 10` used to fail with
  an error pointing at the next declaration (BACKLOG P3, now removed).
- In `let … in`, a right-hand side may start left of its binding, as it already could in a
  `do`-`let`.
- A line after `->`, `=` or an operator continues the item even at the block column, where a
  `do` used to start a new step there and fail.
- An `in` line closes the innermost `let` group at any column, including the binding column.
- An `else` left of the `do` that holds its `if` belongs to that `if`. The old parser failed
  with `Expected keyword else at 0:0`.

These reject programs the old parser accepted:

- Two `where` bindings on one line, `where a = 1 b = 2`. The old parse split them where the
  first right-hand side ended; a `where` block now takes one binding per line, like a `let`.
- A line in a match's arm column that is not an arm, such as `|> g` after the last arm. The
  old parser put it in the last arm, so `g` applied to that arm only. It is now an error
  naming the operator: a match is no operand, so it goes in parentheses.
- A top-level `fn … = do` whose steps sit in column 1. A block opens right of the block
  around it, and the top level's column is 1.
- A class or instance member on the head line, `instance Named Box fn name(b) = …`. It used
  to become a top-level function, leaving the instance without its method; it is now an
  error saying the body starts on the next line.
- A binding right of a match's arm column after a `match` right-hand side whose arms sit
  left of the binding (`let r = match x with` / arms / a further-indented `s = 3`). A line
  right of the arm column continues the last arm.

A `do`-`let` group's bindings may sit left or right of the first binding, as before; the old
BACKLOG entry asking whether to enforce the first binding's column is closed by stating the
rule in spec §2.1 rather than tightening it.

## Tests

- `tests/stdlib/test_layout_blocks.spr` — layouts the old parser accepted, including the
  #456 repro, the PR #453 review shapes, and `where` bindings in the declaration's column.
- `tests/conformance/parse_error/` — the five rejections above
  (`where_two_bindings_one_line`, `match_arm_column_operator`, `root_do_steps_in_column_one`,
  `member_on_head_line`, `binding_right_of_match_arms`), and `nested_do_in_outer_do_column`,
  a nested `do` in the outer `do`'s column that must stay empty rather than take the outer
  steps.
- `tests/stdlib/test_layout_block_closers.spr` — the closers above, and an `else` left of
  the `do` that holds its `if`.
- `tests/stdlib/compiler/test_layout.spr` — the pass's token output.
- IR byte-identity, old parser against new, across sprout_lang (646 files), uncharted-suns
  (295), repbit (23) and sprout-postgres (13). The one verdict change is
  `test_layout_block_closers.spr`, which the old parser rejected.
- A generated differential (162 variants: binding columns, nested `do` and match, arm bodies,
  `if`/`else`, `do` lambdas as arguments, `where`, `let … in`, `else` handlers, instance members,
  pipelines) found no program the old parser accepted and the new one rejects, and identical
  IR wherever both accept; 37 variants only the new parser accepts. It ran against the
  pre-pass seed, so it is a migration check and not kept as a gate.
- Six reviews then found 26 shapes neither check had. Twenty are fixed and pinned:
  `where` bindings in the declaration's column (at the root, in a class or instance body, and
  a member's `where` in the instance's column), a `let` binding right of the first, a binding
  whose `=` is on the next line, a comprehension `if` after a one-line match, `then`/`else`
  in the arm column, a whole indented file, declarations indented at other columns than the
  rest, an indented declaration ending a `where` group, an empty `where` before a `fn` (at
  the top level and in a body), a `fn` after a brace-form body, a mid-line `fn` in a
  one-line brace body, a nested `let` group in the outer binding's column or inside a call,
  a declaration keyword in a class or instance body's member column, a mid-line `fn` after a
  `type` or a `where` group, a `then`, `else` or `with` left of the arms that hold its
  `if` or `match`, a mid-line `let` declaration, and an `if` step after an operand in a `do`
  lambda passed as an argument. Five are the rejections above. The last was this pass's own:
  a nested `do` in the outer `do`'s column took the outer steps as its own, and now it is
  empty, as before. The sixth review also corrected the spec's claim that no line inside
  brackets is compared.
