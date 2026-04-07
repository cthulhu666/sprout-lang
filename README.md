![Sprout logo](assets/logo.png)

# Sprout

Sprout is an experimental, statically typed, functional-first programming language focused on being "Haskell for ordinary people": strong type safety, predictable semantics, and approachable syntax.

The v0 core aims to infer types wherever they can be determined unambiguously
without compromising implementation simplicity, predictable behavior, or
diagnostic quality.

## Status

Prototype implementation stage. The repository includes a working tokenizer/parser/typechecker,
interpreter runtime, early native backend, module loader, and stdlib examples.

## Docs

- [Specification v0 (Normative)](./docs/spec-v0.md)
- [Style Guide v0 (Non-Normative)](./docs/style-guide-v0.md)
- [Language Design v0](./docs/language-design-v0.md)
- [Effect System v0 Plan](./docs/effect-system-v0-plan.md)
- [Effect System v1 Draft](./docs/effect-system-v1-draft.md)
- [Int Ranges v1 Draft](./docs/int-ranges-v1-draft.md)
- [Char and Text v1 Draft](./docs/char-text-v1-draft.md)
- [Native REPL Roadmap](./docs/native-repl-roadmap.md)
- [REPL Self-Hosting v1 Draft](./docs/repl-self-hosting-v1-draft.md)
- [Compiler Self-Hosting Roadmap](./docs/compiler-self-hosting-roadmap.md)
- [Sequencing Sugar v1 Draft](./docs/sequencing-sugar-v1-draft.md)
- [Language Design Best Practices (Research Notes)](./docs/language-design-best-practices.md)
- [HM Typechecker Guide (Human-Friendly)](./docs/hm-typechecker.md)

Normative status:

- `docs/spec-v0.md` defines the stable Sprout core for v0.
- `docs/style-guide-v0.md` defines the default repository source-style
  conventions for humans and AI agents, but it does not change the language
  contract.
- Features described elsewhere in this README but not specified in `docs/spec-v0.md`
  are implementation features or experimental extensions.
- Function-local `where` blocks on `fn` declarations are part of the normative v0 core.
- In particular, the current module system and typeclass support are implemented
  in the prototype, but are not yet part of normative v0.
- The current implementation also includes an experimental first records slice:
  nominal record declarations such as `type User = { name: String }`, typed
  record literals such as `User { name = "Ada" }`, and field projection via the
  contextual special form `get user name`. Records are not part of normative v0
  yet, and record updates remain deferred.
- The current implementation also includes an experimental `IntRange` slice:
  inclusive `a..b` syntax, distinct `IntRange` values, ascending and
  descending unit-step semantics, and prelude helpers such as `range`,
  `range_contains`, `range_count`, `range_to_list`, `range_to_vec`, and
  `range_fold`. Integer ranges are not part of normative v0 yet.
- The current implementation also includes an experimental `Char` and text
  semantics slice: distinct `Char` values and char literals such as `'a'`,
  `String` helper semantics defined in terms of Unicode code points, and
  stdlib helpers such as `char_at`, `char_at_or`, `string_from_char`, and
  `string_chars`. Source literals currently reject `\0` until native execution
  can preserve embedded NUL code points consistently. This surface is not part
  of normative v0 yet.
- The current implementation also includes an experimental `stdlib.regex`
  module for compiled regex values, first-match search, literal replacement,
  and plain-text escaping. It intentionally stays out of core syntax and
  `match` patterns for now, and is not part of normative v0 yet.
- The current implementation also includes an experimental declaration-status
  annotation slice via top-level comment directives such as `#@unstable`,
  `#@temporary`, `#@wip`, and `#@deprecated ...`. Imported uses of annotated
  exported values now emit compiler warnings, but this annotation surface is
  not part of normative v0 yet.
- The current implementation also includes experimental `do` notation for
  sequencing `Maybe` and `Result` values. The surface is meant to leave room
  for broader Haskell-style sequencing later, but the current semantics are
  intentionally narrower and are not part of normative v0 yet. The current
  experimental implementation now also supports `IO`-sequencing-style `do`
  blocks with non-final plain `!{IO}` expression steps, pure local
  `let` steps, and irrefutable bind patterns such as tuple destructuring in
  `<-` steps.
- The current implementation also includes an experimental compiler-driver
  helper module in `stdlib/compiler.sprout`. It provides a Sprout-owned
  `CompilerSession` wrapper over the snapshot-analysis bridge, with helpers
  such as `check`, `type_of`, `eval_lines`, `declared_names`,
  `exported_names`, `symbol_inventory`, `diagnostics`, and `instances`.
  This module is not part of normative v0 yet.
- The current implementation uses explicit function effects in the v0 core:
  pure functions omit an annotation, effectful functions use `!{IO}`, and
  higher-order helpers may use restricted singleton effect variables such as
  `!{e}`.
- Mixed/open effect rows are not supported yet; keep `!{IO}` and `!{e}` cases
  concrete for now.
- `docs/effect-system-v1-draft.md` is now a forward-looking draft for the next
  effect milestone beyond the implemented v0 baseline. The active recommended
  direction is `IO` sequencing ergonomics and diagnostics, including the
  narrow mixed `IO` plus inner `Maybe`/`Result` `do` model, rather than richer
  effect rows.
- Native REPL work is the current tooling priority.
- `docs/repl-self-hosting-v1-draft.md` and the language-server/compiler
  milestones are currently deferred as product work, but the native REPL bridge
  is being shaped as reusable language-service infrastructure for those later
  directions rather than as REPL-only glue.
- `docs/compiler-self-hosting-roadmap.md` now captures the broader staged path
  from the current host implementation toward a compiler implemented
  substantially in Sprout, but that roadmap is explicitly downstream of the
  current native-REPL-first pause point.

## Repository Layout

- `docs/` design and process documents
- `examples/` sample source files
- `sprout/` implementation (`tokenizer`, `parser`, `typechecker`, `cli`)
- `tests/` parser and typechecker tests
- `tests/conformance/` executable language behavior fixtures (`run`, `parse_error`, `type_error`, `runtime_error`)
- `stdlib/` language-level standard library source (`prelude.sprout`)
  plus protocol helpers such as `http.sprout`
- `mise.toml` pinned local toolchain (`python`, `just`)
- `justfile` common project commands

v0 execution note:

- Top-level `let` bindings must be pure.
- Effectful work is expected to live in functions, with `main` as the entrypoint.

## Tooling (mise + just)

This repo uses [`mise`](https://mise.jdx.dev/) to pin tools and [`just`](https://github.com/casey/just) as task runner.

1. Install tools from `mise.toml`:
   `mise install`
2. Run commands through mise:
   `mise exec -- just test`

Common tasks:

- Parse file: `mise exec -- just parse examples/fizzbuzz.sprout`
- Format repo: `mise exec -- just fmt`
- Check repo formatting: `mise exec -- just fmt-check`
- Lint repo: `mise exec -- just lint`
- Format file: `mise exec -- just fmt-file examples/fizzbuzz.sprout`
- Check file formatting: `mise exec -- just fmt-check-file examples/fizzbuzz.sprout`
- Lint file: `mise exec -- just lint-file examples/fizzbuzz.sprout`
- Typecheck file: `mise exec -- just check examples/fizzbuzz.sprout`
- Run file: `mise exec -- just run examples/fizzbuzz.sprout`
- Run the default full test gate in parallel: `mise exec -- just test` (`SPROUT_TEST_JOBS` controls concurrency; default is 4)
- Run selected test modules in parallel: `mise exec -- just test tests.test_parser tests.test_typechecker`
- Run selected test modules through an env var: `SPROUT_TESTS="tests.test_parser tests.test_typechecker" mise exec -- just test`
- Run the parallel runner explicitly: `mise exec -- just test-parallel` (also accepts optional test modules)
- Run the serial fallback suite: `mise exec -- just test-serial`
- Measure fast native GC threshold workloads: `mise exec -- just measure-gc-thresholds`
- Measure opt-in real workloads too: `mise exec -- just measure-gc-real`
- Start REPL: `mise exec -- python -m sprout.cli repl` (default interpreter-launched path) or `mise exec -- python -m sprout.cli repl --native` (experimental native launcher backed by `analysis-service`; both run the Sprout-hosted frontend in [stdlib/repl.sprout](./stdlib/repl.sprout); [examples/repl_hosted.sprout](./examples/repl_hosted.sprout) remains a thin wrapper; the native launcher now reuses a cached compiled REPL binary between launches and the compiled native frontend carries its own default `analysis-service` command based on the Python used at compile time; loads the foundational prelude by default; interactive mode detection, line editing, `Tab` completion, and `Up`/`Down` history now live in Sprout code; `Tab` completion is ASCII case-insensitive and can complete imported namespace members such as `json.string` after `import stdlib.json`; `:{` and `:}` execute explicit multiline REPL blocks sequentially behind a distinct `block| ` continuation prompt, and `:cancel` aborts the current block; ordinary `import ...` lines work inside the session)
  If native REPL cache build fails, the launcher now reports the native compile error directly and suggests the interpreter-backed `repl` path.
  Native REPL startup itself no longer requires a live `analysis-service`; the bridge is contacted lazily on the first analysis-backed action such as `import`, declaration acceptance, `:type`, `:instances`, or expression evaluation.
- Run tests: `mise exec -- just test`
- Run selected tests: `mise exec -- just test tests.test_parser tests.test_typechecker`
- Run serial full test suite explicitly: `mise exec -- just test-serial`
- Run legacy serial alias: `mise exec -- just test-all`
- Run integration-style IO tests: `mise exec -- just test-integration`
- Emit LLVM IR: `mise exec -- just compile examples/factorial.sprout /tmp/factorial.ll`
- Build native binary (clang): `mise exec -- just compile-native /tmp/prog.sprout /tmp/prog`

Integration-style IO test convention:

- Service-backed tests live in [tests/test_integration_io.py](./tests/test_integration_io.py).
- Shared local-fixture helpers live in [tests/integration_support.py](./tests/integration_support.py).
- Prefer local mock services on `127.0.0.1` over external hosted dependencies.
- Keep `just test` as the default authoritative gate when run without filters, use targeted forms such as `mise exec -- just test tests.test_parser tests.test_typechecker` or `SPROUT_TESTS="..." mise exec -- just test` for faster local loops, use `mise exec -- just test-parallel` when you want to invoke the same runner explicitly, use `mise exec -- just test-serial` or `mise exec -- just test-all` only for fallback/debugging, and use `mise exec -- just test-integration` when iterating on service-backed interpreter/native behavior.
- Use `mise exec -- just measure-gc-thresholds` for the fast GC regression/stress loop and `mise exec -- just measure-gc-real` or targeted runs such as `python3 scripts/measure_gc_thresholds.py --workload aoc_day5 --threshold off --threshold 4096` for the heavier real workloads that now drive default-threshold decisions. The opt-in real set currently includes `vector_build_medium`, `aoc_day3`, `aoc_day4_small`, and `aoc_day5`, and the script summarizes GC cycles, swept nodes, max live heap, max root-slot count, max marked-node count, wall time, and elapsed microseconds across the selected thresholds.
- Current GC tuning note: after indexing managed nodes for mark-time lookup, the native runtime now defaults to `SPROUT_GC_THRESHOLD=4096`. On the measured `aoc_day3` and `aoc_day5` workloads, `4096` is dramatically better than the old `1024` default and better than `off`. Use `python3 -m sprout.cli compile examples/aoc_2025_day_5.sprout --native -o /tmp/aoc_day5 && SPROUT_GC_THRESHOLD=8192 /tmp/aoc_day5 < day5input` or a similar override only when comparing thresholds on a specific workload.

## Builtin Helpers (v0)

Runtime builtins (host-implemented):

`!{IO}` builtins:

- `print(x) -> Unit !{IO}`
- `print_int(x: Int) -> Int !{IO}` (prints and returns `x`, useful for native backend subset)
- `read_lines(path: String) -> List String !{IO}`
- `read_file(path: String) -> String !{IO}`
- `read_int_lines(path: String) -> Vector Int !{IO}`
- `env_get(name: String) -> Maybe String !{IO}`
- `argv_get(index: Int) -> Maybe String !{IO}` (`0` is the first user-supplied program argument)
- `int_range(lo: Int, hi: Int) -> IntRange`
- `int_range_start(r: IntRange) -> Int`
- `int_range_end(r: IntRange) -> Int`
- `tcp_listen(port: Int) -> Int !{IO}`
- `tcp_accept(listener: Int) -> Int !{IO}`
- `tcp_read(conn: Int) -> String !{IO}`
- `tcp_write(conn: Int, payload: String) -> Unit !{IO}`
- `tcp_connect(host: String, port: Int) -> Result stdlib.net.TcpError Int !{IO}`
- `tcp_read_exact(conn: Int, count: Int) -> Result stdlib.net.TcpError Bytes !{IO}`
- `tcp_write_all(conn: Int, payload: Bytes) -> Result stdlib.net.TcpError Int !{IO}`
- `tcp_close(conn: Int) -> Unit !{IO}`
- `tcp_close_listener(listener: Int) -> Unit !{IO}`
- `tcp_echo_serve(port: Int, max_connections: Int) -> Unit !{IO}`
- `http_request(method: String, url: String, headers: String, body: String, timeout_ms: Int) -> Result HttpError HttpResponse !{IO}`
- `crypto_random_bytes(count: Int) -> Result stdlib.crypto.CryptoError Bytes !{IO}`
- `term_clear() -> Unit !{IO}`
- `term_move(row: Int, col: Int) -> Unit !{IO}`
- `term_hide_cursor() -> Unit !{IO}`
- `term_show_cursor() -> Unit !{IO}`
- `term_read_key() -> String !{IO}` (reads one key from stdin; in TTY mode it reads immediately without waiting for newline)
- `term_read_line() -> Maybe String !{IO}` (reads one stdin line, trims trailing `\n`/`\r\n`, returns `Nothing` at EOF)
- `term_write(text: String) -> Unit !{IO}`

Experimental snapshot analysis hooks:

- Active snapshot/state hooks used by the current Sprout REPL frontend.
  The host implementation now routes snapshot analysis through `sprout.analysis`:
- `repl_eval_expr_in_source(module_source: String, expr: String) -> Result String (Vec String) !{IO}`
- `repl_check_source(module_source: String) -> Result String Unit !{IO}`
- `repl_declared_names_in_source(module_source: String) -> Result String (Vec String) !{IO}`
- `repl_exported_names_in_source(module_source: String) -> Result String (Vec String) !{IO}`
- `repl_symbol_inventory_in_source(module_source: String) -> Result String (Vec String, Vec String, Vec String) !{IO}` (`declared`, `imported`, `exported`)
- `repl_diagnostics_in_source(module_source: String) -> Vec (String, Int, Int) !{IO}`
- `repl_type_of_in_source(module_source: String, expr: String) -> Result String String !{IO}`
- `repl_instances_in_source(module_source: String, query: String) -> Result String (String, Vec String) !{IO}`
- `repl_complete_in_state(line_buffer: String, imports: Vec String, declarations: Vec String) -> (String, Vec String) !{IO}`
- `repl_reset_session() -> Unit !{IO}`

- Neutral compatibility aliases now exist for the shared analysis subset:
  `analysis_check_source`, `analysis_declared_names_in_source`,
  `analysis_exported_names_in_source`, `analysis_symbol_inventory_in_source`,
  `analysis_symbol_locations_in_source`, `analysis_diagnostics_in_source`,
  `analysis_type_of_in_source`, `analysis_instances_in_source`.
- Python-side analysis helpers in `sprout.analysis` now also expose
  `symbol_metadata_in_source(...)` for structured top-level/import symbol
  metadata, including optional definition locations for imported symbols when
  the provider declaration is available, without widening the builtin/runtime
  ABI. They also expose
  `structured_diagnostics_in_source(...)` for severity/stage/location-aware
  diagnostics records while the builtin bridge keeps the older tuple shape.

- Legacy compatibility hooks still present in the host runtime, but no longer used
  by `stdlib/repl.sprout`:
- `repl_add_import(source: String) -> Result String Unit !{IO}`
- `repl_add_declaration(source: String) -> Result String Unit !{IO}`
- `repl_eval_expr(source: String) -> Result String (Vec String) !{IO}`
- `repl_type_of(source: String) -> Result String String !{IO}`
- `repl_instances(source: String) -> Result String (String, Vec String) !{IO}`
- `repl_complete(line_buffer: String) -> (String, Vec String) !{IO}`

These are implementation hooks for the Sprout-hosted REPL frontend. They are
still mostly interpreter-backed. The current near-term priority is making that
bridge native-capable rather than making it self-hosted. The canonical
analysis-service subprocess boundary is now
`python -m sprout.analysis_adapter` for snapshot `check_source` and
`declared_names_in_source` / `exported_names_in_source` /
`symbol_inventory_in_source` / `diagnostics_in_source` /
`type_of_in_source` / `instances_in_source` / `eval_expr_in_source` queries,
plus compatibility-only explicit-state `complete_in_state`, as the first explicit host-service
bridge below the REPL frontend, and native compiled programs now use that bridge for
`repl_check_source(...)`, `analysis_check_source(...)`,
`repl_declared_names_in_source(...)`, `analysis_declared_names_in_source(...)`,
`repl_exported_names_in_source(...)`, `analysis_exported_names_in_source(...)`,
`repl_symbol_inventory_in_source(...)`,
`analysis_symbol_inventory_in_source(...)`,
`repl_diagnostics_in_source(...)`, `analysis_diagnostics_in_source(...)`,
`repl_type_of_in_source(...)`, `analysis_type_of_in_source(...)`,
`repl_instances_in_source(...)`, `analysis_instances_in_source(...)`, and
`repl_eval_expr_in_source(...)`, plus `repl_complete_in_state(...)` and
`analysis_symbol_locations_in_source(...)`. The active Sprout REPL frontend no
longer depends on that bridge for `Tab` completion; completion now runs locally
in `stdlib/repl.sprout` from the current session text state, and startup no
longer calls `repl_reset_session()` either, so that hook is now
compatibility-only rather than part of the active frontend path. The rest
of the REPL/analysis snapshot hooks still report unsupported-backend runtime
errors in native binaries. End-to-end native execution of the current Sprout
REPL frontend is now verified by compiling and running
`examples/repl_hosted.sprout` with that bridge in place, and the user-facing
`repl --native` launcher now exposes that path experimentally while still using
the Python `analysis-service` bridge underneath. The canonical stdio adapter is
[sprout/analysis_adapter.py](./sprout/analysis_adapter.py), while
[sprout/analysis_stdio.py](./sprout/analysis_stdio.py) is now only a
compatibility wrapper over the reusable dispatcher in
[sprout/analysis_dispatch.py](./sprout/analysis_dispatch.py) and protocol loop
in [sprout/analysis_protocol.py](./sprout/analysis_protocol.py), which is the
intended replacement seam for a future non-Python native service. That bridge
is being treated as reusable language-service infrastructure for later
self-hosted compiler and language-server work, not as REPL-only plumbing. The launcher
reuses both a cached
compiled REPL binary between launches and one long-lived analysis-service
subprocess per native program run, with one automatic restart for replay-safe
snapshot queries if that child dies mid-session. The hidden
`sprout.analysis_stdio`, `sprout.analysis_service`, and
`sprout.cli analysis-service` remain only as compatibility wrappers.
Native programs can override the service command via
`SPROUT_ANALYSIS_SERVICE_CMD`; if that command is invalid, native REPL and
native snapshot-query failures now point back to that env var explicitly.
Tests can override the launcher cache
directory via `SPROUT_NATIVE_REPL_CACHE_DIR`.

Native TCP listener and connection handle tables now reuse closed slots, so long-running native servers no longer fail after a fixed total number of accepted connections.

Pure value transforms and runtime-backed persistent data helpers:

- `parse_int(s: String) -> Int`
- `int_to_string(value: Int) -> String` (runtime primitive; public formatting should prefer `Show.to_string`)
- `char_to_string(value: Char) -> String`
- `split_words(s: String) -> List String`
- `str_concat(a: String, b: String) -> String`
- `str_len(s: String) -> Int`
- `str_slice(s: String, start: Int, len: Int) -> String`
- `str_char_at(s: String, index: Int) -> Maybe Char`
- `str_find(s: String, needle: String) -> Int` (`-1` when not found)
- `str_starts_with(s: String, prefix: String) -> Bool`
- `str_compare(left: String, right: String) -> Int` (`-1`, `0`, `1`)
- `bytes_empty() -> Bytes`
- `bytes_length(value: Bytes) -> Int`
- `bytes_get(value: Bytes, index: Int) -> Maybe Int`
- `bytes_slice(value: Bytes, start: Int, count: Int) -> Bytes`
- `bytes_append(left: Bytes, right: Bytes) -> Bytes`
- `bytes_singleton(value: Int) -> Bytes`
- `bytes_from_utf8(raw: String) -> Bytes`
- `bytes_to_utf8(value: Bytes) -> Result stdlib.bytes.Utf8Error String`
- `bytes_builder_empty() -> Builder`
- `bytes_builder_bytes(value: Bytes) -> Builder`
- `bytes_builder_byte(value: Int) -> Builder`
- `bytes_builder_u16_be(value: Int) -> Builder`
- `bytes_builder_u32_be(value: Int) -> Builder`
- `bytes_builder_append(left: Builder, right: Builder) -> Builder`
- `bytes_builder_build(value: Builder) -> Bytes`
- `crypto_sha256(value: Bytes) -> Bytes`
- `crypto_hmac_sha256(key: Bytes, msg: Bytes) -> Bytes`
- `crypto_base64_encode(value: Bytes) -> String`
- `crypto_base64_decode(raw: String) -> Result stdlib.crypto.Base64Error Bytes`
- `crypto_bytes_xor(left: Bytes, right: Bytes) -> Result stdlib.crypto.BytesOpError Bytes`
- `map_empty() -> Map a`
- `map_get(m: Map a, key: String) -> Maybe a`
- `map_set(m: Map a, key: String, value: a) -> Map a`
- `map_remove(m: Map a, key: String) -> Map a`
- `map_size(m: Map a) -> Int`
- public JSON entrypoints live in `stdlib.json` as `parse(raw)` and `stringify(value)`
- `vector_empty() -> Vector a`
- `vector_length(v: Vector a) -> Int`
- `vector_get(v: Vector a, index: Int) -> Maybe a`
- `vector_set(v: Vector a, index: Int, value: a) -> Vector a`
- `vector_append(v: Vector a, value: a) -> Vector a`
- `map_nth_key(m: Map a, index: Int) -> Maybe String`
- `map_nth_value(m: Map a, index: Int) -> Maybe a`

Effect notes:

- Sprout v0 now tracks the built-in `IO` effect on function types.
- Pure functions omit an effect annotation.
- Effectful functions use `!{IO}`, for example `fn main() -> Unit !{IO} = ...`.
- Restricted effect polymorphism is supported for higher-order helpers via
  singleton effect variables such as:
  `fn apply_twice(f: Int -> Int !{e}, x: Int) -> Int !{e} = f(f(x))`.
- Executable `main` must stay concrete and have type `Unit !{IO}`.
- Effects do not change Sprout's strict execution order; they constrain which
  functions may call which other functions.
- Mixed/open effect rows and additional effect labels are still deferred follow-up work.

String/runtime helpers are host-implemented primitives. In the current experimental text slice, `str_len`, `str_slice`, `str_char_at`, and `str_find` use Unicode code-point semantics rather than UTF-8 byte offsets. Application code should use `stdlib.string`; direct `str_*`/`split_words` usage is reserved for `stdlib.*` modules. The same applies to raw `regex_*` helpers, which are internal to `stdlib.regex`.

Standard library (Sprout source in `stdlib/prelude.sprout`):

- `Maybe a` (`Just`, `Nothing`)
- `map(fn, list) -> List`
- `fold(fn, init, list) -> value`
- `filter(predicate, list) -> List`
- `split_ints(s: String) -> List Int`
- `Vec a` plus foundational helpers:
  - `vec_empty()`
  - `vec_singleton(value)`
  - `vec_prepend(value, vec)`
  - `vec_append(value, vec)`
  - `vec_length(vec)`
  - `vec_get(index, vec) -> Maybe a`
  - `vec_get_or(index, fallback, vec)`
  - `vec_set(index, value, vec)`
  - `vec_map(f, vec)`
  - `vec_fold(f, init, vec)`
  - `vec_filter(pred, vec)`
  - `vec_filter_map(f, vec)`
  - `vec_any(pred, vec)`
  - `vec_all(pred, vec)`
  - `vec_count(pred, vec)`
  - `vec_slice(start, count, vec)`
  - `vec_reverse(vec)`
  - `vec_sum(vec)`
  - `vec_sum_by(f, vec)`
  - `vec_sort(vec)` where `Ord a` (initial built-in coverage: `Int`, `Bool`, `String`)
  - `vec_sort_by(key, vec)` where `Ord key`
- `Dict v` plus foundational helpers:
  - `dict_empty()`
  - `dict_get(key, dict) -> Maybe v`
  - `dict_set(key, value, dict)`
  - `dict_remove(key, dict)`
  - `dict_keys(dict) -> Vec String`
  - `dict_values(dict) -> Vec v`
  - `dict_entries(dict) -> Vec (String, v)`
  - dict literals: `{foo: 1, "bar": 2}`, `{}`
- `Show t`, `Ord t`, `Semigroup t`, `Functor f`, and `Foldable f`
- `to_string(x)` is the default `Show` operation
- `map(f, xs)` is the default `Functor` operation
- `fold(step, init, xs)` is the default `Foldable` operation
- `fmap(f, xs)` remains available as an alias for the underlying `Functor` method
- `foldable_to_vec(xs: f a) -> Vec a` where `Foldable f`
- prelude instances are currently provided for `String`, `List a`, `Vec a`, and `Dict v`
- `left ++ right` works in the default REPL for strings and lists
- `Result e a` with helpers:
  - `after(effect, value)` sequences `effect` and returns `value`
  - experimental `do` blocks for `Maybe`/`Result`, `IO`, and mixed `IO` plus inner `Maybe`/`Result` sequencing, for example:
    `do ... x <- mx ... y <- my ... Just((x, y))`
    The intended current model is intentionally narrow: mixed `IO` blocks use
    `<-` to unwrap an inner `Maybe`/`Result` and short-circuit on failure; code
    that needs the whole container should use explicit `match`.
    See `examples/do_notation_demo.sprout` for `Maybe`/`Result` and
    `examples/io_do_demo.sprout` / `examples/io_result_do_demo.sprout` for
    helper-level mixed `IO` plus `Maybe` / `Result` flows handled by a
    `Unit !{IO}` `main`.
  - forward pipe operator: `value |> f` rewrites to `f(value)`, and
    `value |> g(a, b)` rewrites to `g(a, b, value)`
  - function composition operators:
    `f >> g` rewrites to `\x -> g(f(x))`
    `f << g` rewrites to `\x -> f(g(x))`
  - `pipe(f, value)`
  - `result_map(f, r)`
  - `result_map_error(f, r)`
  - `result_and_then(f, r)`
  - `result_with_default(fallback, r)`
  - `result_pipe(f, r)` aliases `result_and_then` in pipeline style
  - `result_pipe_ok(f, r)` aliases `result_map` in pipeline style
  - `result_pipe_error(f, r)` aliases `result_map_error` in pipeline style
  - `when_ok(f, r)` runs `f` for `Ok` and preserves `r`
  - `when_error(f, r)` runs `f` for `Err` and preserves `r`

The current `after(effect, value)` helper in `stdlib/prelude.sprout` is a
lightweight compatibility convenience for single-step `IO` sequencing. It is
still supported, but `do` is the preferred surface for multi-step sequencing
and mixed `IO` plus `Maybe`/`Result` flows.

Experimental compiler helper module (in `stdlib/compiler.sprout`):

- `CompilerSession` with `empty_session()`, `with_import(line, session)`, and
  `with_declaration(line, session)`
- `CompilerReport` as a Sprout-owned snapshot analysis result carrying
  validity, optional primary error text, diagnostics, and symbol inventory
- `session_source(session) -> String`
- snapshot helpers over the existing host analysis bridge:
  `analyze(session)`,
  `check(session)`, `declared_names(session)`, `exported_names(session)`,
  `type_of(session, expr)`, `eval_lines(session, expr)`,
  `symbol_inventory(session)`, `diagnostics(session)`, and
  `instances(session, query)`
- wrapper result types:
  `SymbolInventory`, `Diagnostic`, `InstanceMatches`, and `CompilerReport`

Current call semantics note: ordinary function values support under-application.
Sprout uses nested arrow types for multi-parameter functions, so `f(x)` returns
another function value when `f` still has remaining parameters.

Function-local bindings are available through `where` on `fn` declarations:

```sprout
fn score(n: Int) -> Int =
  x + y
where
  x = n + 1
  y = x * 2
```

This `where` form is intentionally small in v0:

- it applies only to `fn` declarations
- bindings are value bindings only, with no local type annotations
- bindings may use either a single name or tuple destructuring with names / `_`
- bindings are non-recursive and may use only function parameters and earlier local bindings

Math module (in `stdlib/math.sprout`):

- Int-only helper surface; this does not add `Float`, `Decimal`, or fixed-width integer types to v0
- `abs(x) -> Int`
- `min(x, y) -> Int`
- `max(x, y) -> Int`
- `clamp(x, lo, hi) -> Int`
- `sign(x) -> Int`
- `pow(base, exp) -> Maybe Int`
- `mod(x, n) -> Maybe Int`
- `gcd(x, y) -> Int`
- `lcm(x, y) -> Int`
- `is_even(x) -> Bool`
- `is_odd(x) -> Bool`

Math semantics:

- `mod(x, n)` is Euclidean modulo
- when `n > 0`, `mod(x, n)` returns `Just r` with `0 <= r < n`
- when `n <= 0`, `mod(x, n)` returns `Nothing`
- `pow(base, exp)` returns `Nothing` when `exp < 0`
- interpreter `Int` arithmetic currently follows host arbitrary-precision integer behavior
- the current native backend still lowers `Int` to `i64`, so overflow-sensitive results for `abs`, `pow`, `gcd`, and `lcm` are not yet backend-independent outside the backend's current representable range
- this is a v0 implementation limitation, not the intended long-term meaning of `Int`

For module code, prefer:
`import stdlib.math as math`
then call helpers like `math.mod(...)` and `math.gcd(...)`.

Example usage:

```sprout
fn inc(x: Int) -> Int = x + 1
fn double_if_large(x: Int) -> Result String Int =
  if x > 10 then Ok(x * 2) else Err("too-small")
fn show_ok(x: Int) -> Unit !{IO} = print(x)
fn show_error(e: String) -> Unit !{IO} = print(e)

# Nested style
when_error(
  show_error,
  when_ok(
    show_ok,
    result_pipe_ok(
      inc,
      result_pipe(
        double_if_large,
        Ok(pipe(inc, 20))
      ),
    )
  )
)

# Piped style
Ok(20)
|> result_pipe_ok(inc)
|> result_pipe(double_if_large)
|> result_pipe_ok(inc)
|> when_ok(show_ok)
|> when_error(show_error)
```

Runnable demo:
- `python3 -m sprout.cli run --with-stdlib examples/result_demo.sprout`

HTTP stdlib helpers (in `stdlib/http.sprout`):

- uses foundational prelude `Maybe` and `Result`
- `HttpResponse(status, headers, body)`
- `HttpError` variants (`HttpTimeout`, `HttpNetwork`, `HttpBadStatus`, `HttpDecode`)
- `HttpStatusError` variants (`HttpUnsupportedStatus`)
- `parse_request_line(raw) -> Maybe RequestLine`
- `http_response(status, body) -> Result HttpStatusError String`
- `http_response_body(resp: HttpResponse) -> String`
- `http_ok(body) -> String`
- `http_bad_request() -> String`
- `http_echo_response(raw_request) -> String`

Experimental HTTP server helpers (in `stdlib/http_server.sprout`):

- `HttpRequest` request values with accessor helpers
- `HttpServerResponse` response values built via helper functions
- `HttpServerError` variants (`HttpInvalidRequest`, `HttpServerUnsupportedStatus`)
- `parse(raw) -> Result HttpServerError HttpRequest`
- `render(resp) -> Result HttpServerError String`
- `response(status, body) -> HttpServerResponse`
- `ok(body) -> HttpServerResponse`
- `bad_request(body) -> HttpServerResponse`
- `not_found(body) -> HttpServerResponse`
- `with_header(name, value, resp) -> HttpServerResponse`
- `request_method(req) -> String`
- `request_path(req) -> String`
- `request_version(req) -> String`
- `request_body(req) -> String`
- `request_header(name, req) -> Maybe String`
- `serve_n(port, max_connections, handler) -> Unit !{IO}`

Current experimental scope:

- HTTP/1.1 request line parsing plus header parsing into a `Dict String`
- `Content-Length` request bodies
- `Connection: close` responses only
- sequential request handling per accepted connection
- no keep-alive, chunked transfer encoding for server responses, TLS server support, or multi-reactor native server runtime yet

JSON stdlib helpers (in `stdlib/json.sprout`):

- `JsonError` / `Json` / `JsonArray` / `JsonObject` ADTs
- `JsonEncode a` plus `encode(value)` for directly encodable values (`Json`, `Int`, `Bool`, `String`)
- `JsonArrayStep` / `JsonObjectStep` traversal ADTs
- builder helpers: `null()`, `bool(value)`, `int(value)`, `string(value)`, `array_from_list(items)`, `object_from_pairs(items)`, `object_from_dict(items)`
- `parse(raw) -> Result JsonError Json`
- `stringify(value: Json) -> String` (compact JSON for the currently representable subset)
- `json_get_field(value, key) -> Maybe Json`
- `json_get_string(value) -> Maybe String`
- `json_get_int(value) -> Maybe Int`
- `json_get_array(value) -> Maybe JsonArray`
- `json_get_object(value) -> Maybe JsonObject`
- `json_array_next(array) -> Maybe JsonArrayStep`
- `json_object_next(object) -> Maybe JsonObjectStep`

Example:

```sprout
import stdlib.json as json

fn payload() -> json.Json =
  json.object_from_pairs(
    [
      ("title", json.string("hello")),
      ("count", json.int(2)),
      ("items", json.array_from_list([json.string("a"), json.bool(true)]))
    ]
  )
```

`http_response` currently supports a practical fixed subset of common statuses:
`200`, `201`, `202`, `204`, `400`, `401`, `403`, `404`, `405`, `409`, `410`,
`422`, `429`, `500`, `501`, `502`, `503`, and `504`. Unsupported codes return
`Err(HttpUnsupportedStatus(code))` instead of being silently rewritten.

HTTP client convenience module (in `stdlib/http_client.sprout`):

- `http_get(url, headers, timeout_ms) -> Result HttpError HttpResponse`
- `http_post(url, headers, body, timeout_ms) -> Result HttpError HttpResponse`
- `http_put(url, headers, body, timeout_ms) -> Result HttpError HttpResponse`

TCP client helper types (in `stdlib/net.sprout`):

- uses foundational prelude `Result`
- `TcpError` variants (`TcpInvalidArgument`, `TcpInvalidHandle`, `TcpConnectFailed`, `TcpReadFailed`, `TcpWriteFailed`, `TcpEndOfStream`)
- `TcpConnection`
- `TcpListener`
- `connect(host, port) -> Result TcpError TcpConnection`
- `read_exact(conn, count) -> Result TcpError Bytes`
- `write_all(conn, payload) -> Result TcpError Int`
- `read_exact_utf8(conn, count) -> Result TcpError String`
- `write_all_utf8(conn, payload) -> Result TcpError Int`
- `close(conn) -> Unit !{IO}`
- `listen_local(port) -> TcpListener`
- `accept(listener) -> TcpConnection`
- `close_listener(listener) -> Unit !{IO}`
- `tcp_error_message(err) -> String`

`TcpConnection` and `TcpListener` are now exported as opaque handle types; application code can use the types but cannot forge the underlying constructors outside `stdlib.net`.

Bytes helpers (in `stdlib/bytes.sprout`):

- uses foundational prelude `Maybe` and `Result`
- `Builder` opaque type for efficient packet construction
- `empty() -> Bytes`
- `singleton(value) -> Bytes`
- `length(value) -> Int`
- `get(value, index) -> Maybe Int`
- `slice(value, start, count) -> Bytes`
- `append(left, right) -> Bytes`
- `u16_be(value) -> Bytes`
- `u32_be(value) -> Bytes`
- `read_u16_be(value) -> Maybe Int`
- `read_u32_be(value) -> Maybe Int`
- `from_string(raw) -> Bytes`
- `to_string(value) -> Result Utf8Error String`
- `c_string(raw) -> Bytes`
- `read_c_string(value) -> Result Utf8Error String`
- builder helpers:
  - `builder_empty() -> Builder`
  - `builder_bytes(value: Bytes) -> Builder`
  - `builder_byte(value: Int) -> Builder`
  - `builder_u16_be(value: Int) -> Builder`
  - `builder_u32_be(value: Int) -> Builder`
  - `builder_append(left: Builder, right: Builder) -> Builder`
  - `builder_build(value: Builder) -> Bytes`

Crypto helpers (in `stdlib/crypto.sprout`):

- `sha256(value: Bytes) -> Bytes`
- `hmac_sha256(key: Bytes, message: Bytes) -> Bytes`
- `base64_encode(value: Bytes) -> String`
- `base64_decode(raw: String) -> Result Base64Error Bytes`
- `bytes_xor(left: Bytes, right: Bytes) -> Result BytesOpError Bytes`
- `random_bytes(count: Int) -> Result CryptoError Bytes` (effectful; reads runtime entropy)

SCRAM helpers (in `stdlib/scram.sprout`):

- `no_channel_binding() -> String`
- `random_nonce(count: Int) -> Result CryptoError String`
- `client_first_bare(username, nonce) -> String`
- `client_first_message(username, nonce) -> String`
- `parse_server_first(raw) -> Result ScramError ScramServerFirst`
- `client_final_without_proof(channel_binding, server) -> String`
- `client_proof(password, client_first_bare_raw, server, channel_binding) -> Result ScramError String`
- `client_final_message(password, client_first_bare_raw, server, channel_binding) -> Result ScramError String`
- `server_signature(password, client_first_bare_raw, server, channel_binding) -> Result ScramError String`
- `verify_server_final(password, client_first_bare_raw, server, channel_binding, raw) -> Result ScramError Bool`
- `error_message(err) -> String`

The first slice is intentionally generic and SCRAM-SHA-256-focused; protocol-specific auth and wire-message flow should live in external libraries layered on top.

Terminal convenience module (in `stdlib/terminal.sprout`):

- `term_home() -> Unit !{IO}`
- `term_reset_screen() -> Unit !{IO}`
- `term_render_line(row, text) -> Unit !{IO}`
- `term_read_key_once() -> String !{IO}`
- `term_read_line_once() -> Maybe String !{IO}`

These helpers follow the current sequencing style rule: use `do` for
multi-step `IO`, and keep `after(...)` for trivial single-step convenience.

Collections module (in `stdlib/collections.sprout`):

- compatibility namespace for the foundational collection/typeclass surface now defined in the prelude
- existing imports such as `import stdlib.collections (Vec, Dict, Functor, Foldable, map, fold, vec_append, dict_get)` continue to resolve
- prefer the unqualified prelude surface in standalone code and the default REPL

Low-level runtime notes:

- `Vector` and `vector_*` builtins exist as backend/runtime primitives.
- For module code, `stdlib.collections` remains the stable compatibility import path for collection helpers.
- CLI/module checks reject raw `Vector`/`Map` and `vector_*`/`map_*` usage outside `stdlib.*` modules.
- Builtin failures now follow one convention:
  `runtime error: builtin \`name\`: detail`
- `sprout run` surfaces that as `error: runtime error: builtin ...`.
- Native binaries print the same runtime-error message to stderr and exit with status `1`.

String module (in `stdlib/string.sprout`):

- `words(raw: String) -> List String`
- `concat(left: String, right: String) -> String`
- `length(raw: String) -> Int`
- `slice(raw: String, start: Int, count: Int) -> String`
- `take(raw: String, count: Int) -> String`
- `drop(raw: String, count: Int) -> String`
- `find(raw: String, needle: String) -> Int`
- `starts_with(raw: String, prefix: String) -> Bool`
- `contains(raw: String, needle: String) -> Bool`
- `ends_with(raw: String, suffix: String) -> Bool`
- `char_at(raw: String, index: Int) -> Maybe Char`
- `char_at_or(raw: String, index: Int, fallback: Char) -> Char`
- `string_from_char(ch: Char) -> String`
- `trim_left(raw: String) -> String`
- `trim_right(raw: String) -> String`
- `trim(raw: String) -> String`
- `is_empty(raw: String) -> Bool`
- `strip_prefix(raw: String, prefix: String) -> Maybe String`
- `strip_suffix(raw: String, suffix: String) -> Maybe String`
- `split_once(raw: String, sep: String) -> Maybe (String, String)`
- `string_chars(raw: String) -> Vec Char`
- `string_lines(raw: String) -> Vec String`
- `string_digits(raw: String) -> Vec Int`

For module code, prefer:
`import stdlib.string as string`
then call helpers like `string.concat(...)` and `string.length(...)`.

Regex module (experimental, in `stdlib/regex.sprout`):

- `compile(pattern: String) -> Result RegexError Regex`
- `is_match(re: Regex, text: String) -> Bool`
- `find_first(re: Regex, text: String) -> Maybe Match`
- `split_first(re: Regex, text: String) -> Maybe (String, String)`
- `replace_all_literal(re: Regex, replacement: String, text: String) -> String`
- `escape(raw: String) -> String`
- `RegexError` distinguishes `RegexInvalidPattern String` from `RegexUnsupportedFeature String`
- `Match(..)` exposes `Match start end` code-point offsets
- Supported regex surface is intentionally small: literals, `.`, `*`, `+`, `?`, grouping, alternation, character classes, anchors, escaped metacharacters, and ASCII shorthands `\d`, `\w`, `\s`
- Deliberately unsupported in this milestone: counted repetition `{m,n}`, non-greedy quantifiers, extended `(?...)` group syntax, and backreferences
- Patterns are ordinary `String` literals, so backslashes must survive Sprout string parsing first; for example, write `"\\\\d+"` in source to pass `\d+` to the regex compiler

For module code, prefer:
`import stdlib.regex as regex`
then call helpers like `regex.compile(...)` and `regex.replace_all_literal(...)`.

Example classification:

- Runnable examples define `main() -> Unit !{IO}` and can be used with `sprout run`; many also work with `sprout compile`, but backend coverage still varies by feature.
- Library-style examples expose helpers without `main`; use `sprout check` for them directly, or import them from another runnable module.
- `examples/sentry_api.sprout` is a library-style module layering Sentry-specific API helpers plus typed issue-summary and issue-detail decoding on top of generic `stdlib.http` + `stdlib.http_client`.
- `examples/sentry_issue_browser_tui.sprout` is a library-style interactive issue browser module with environment-based config loading, list navigation, refresh, and detail rendering.
- `examples/sentry_issue_browser.sprout` is the runnable wrapper around that helper module for `sprout run` and `sprout compile --native`, including HTTPS-backed Sentry API calls in native mode.
- `examples/http_get_cli.sprout` is a runnable CLI example that reads its URL from `argv_get(0)` and prints the response body.
- `examples/text_demo.sprout` is a runnable Unicode-aware text summary CLI showing `Char`, `char_at_or`, `string_from_char`, and code-point `length`.
- `examples/regex_demo.sprout` is a runnable experimental regex demo showing `compile`, `find_first`, `is_match`, `replace_all_literal`, and `escape`, including doubled-backslash regex patterns inside ordinary string literals.

Load stdlib prelude explicitly for standalone files:

- `python3 -m sprout.cli check --with-stdlib your_file.sprout`
- `python3 -m sprout.cli run --with-stdlib your_file.sprout`
- module-loaded programs get the foundational prelude implicitly

Load HTTP and JSON helpers:

- `python3 -m sprout.cli check --with-http-stdlib your_file.sprout`
- `python3 -m sprout.cli run --with-http-stdlib your_file.sprout`
- Pass program arguments to `sprout run` after the source path; inside Sprout, read them with `argv_get(index)`.
- Example HTTP echo server:
  `SPROUT_NET_MODEL=reactor python3 -m sprout.cli run examples/http_echo_server.sprout`
- Example HTTP GET CLI:
  `python3 -m sprout.cli run examples/http_get_cli.sprout http://127.0.0.1:8080/`
- Example text demo:
  `python3 -m sprout.cli run examples/text_demo.sprout "zażółć gęślą jaźń"`
- Example regex demo:
  `python3 -m sprout.cli run examples/regex_demo.sprout "ticket=AB-42 owner=ada"`
- Sentry issue browser:
  `SENTRY_ORG=your-org SENTRY_PROJECT=your-project SENTRY_TOKEN=token python3 -m sprout.cli run examples/sentry_issue_browser.sprout`
  Interactive terminals use arrow keys or `j`/`k` to move, `Enter` to open details, `r` to refresh, and `q` to quit. Non-interactive runs fall back to the plain issue list.
- Native Sentry issue browser build:
  `mise exec -- just compile-native examples/sentry_issue_browser.sprout /tmp/sentry_issue_browser`
- Sentry issue browser helper flow:
  `SENTRY_ORG=your-org SENTRY_PROJECT=your-project SENTRY_TOKEN=token python3 -m sprout.cli repl`
  then import `examples.sentry_issue_browser_tui` and evaluate `run_entrypoint()`
- Collections helper demo:
  `python3 -m sprout.cli run examples/collections_demo.sprout`
- Typeclass collections demo (experimental surface area, not normative v0):
  `python3 -m sprout.cli run examples/typeclass_functor_foldable_demo.sprout`

## Native Backend (Early)

`sprout compile` currently supports a small subset:

- top-level `fn`, `type`, and top-level `let` (constant and runtime-initialized),
- `Int`/`Bool`/`String` typed params and returns, plus effectful returns such as `Unit !{IO}` and `Int !{IO}`,
- expressions: literals, vars, arithmetic, comparisons, `&&`, `||`, function composition (`f >> g` means `g(f(x))`, `f << g` means `f(g(x))`), lambdas (`\(x, y) -> ...`), `if`, direct calls, indirect closure calls, recursion,
- tuple expressions/types/patterns with general `n`-tuple arity (`(x, y)`, `(Int, String)`, `match pair with | (x, y) -> ...`),
  one-arg lambdas may also use the shorthand `\x -> ...`,
  and empty lambda parameter lists are currently rejected,
- ADT constructor calls and `match` lowering (constructors with up to 3 fields),
- generic type variables are currently erased to runtime `i64` handles,
- closure-backed function values are supported, including captured lambdas and higher-order params/returns,
- `print(...)` lowering for `Int`/`Bool`/`String`/ADT values,
- `print_int(...)` external call.

TCP server builtins are available in interpreter and native (`sprout compile --native`) modes.
Typed TCP client builtins are also available and now use `Bytes` payloads for raw protocol data.
Application code should prefer the typed `stdlib.net` wrapper API over bare `Int` socket handles.
Raw `bytes_*` primitives are internal to `stdlib.*`; application code should use `stdlib.bytes`.
`to_string` rejects invalid UTF-8 and decoded NUL bytes; `read_c_string` is the intended helper for null-terminated protocol strings.
`http_request` is available in interpreter and native modes for plain `http://` requests. Native mode also supports `https://` on macOS via the system TLS stack.
Set `SPROUT_HTTP_TLS_DEBUG=1` when running a native binary to emit TLS handshake/read/write debug lines to stderr.

Interpreter runtime has a swappable server model selected by `SPROUT_NET_MODEL`:

- `reactor` (default): event loop / readiness-based echo server
- `blocking`: simple blocking accept/read/write loop

## Modules (Experimental)

The prototype currently supports file-based modules with top-of-file headers.
This module system is not yet part of normative v0; treat this section as
implementation status rather than language spec.

Sprout supports:

- `module a.b.c`
- `import x.y.z`
- `import x.y.z as alias`
- `import x.y.z (name1, name2)`
- `import x.y.z as alias (name1, name2)`
- `export fn ...`, `export type Name`, `export type Name(..)`, `export let ...` (top-level only)

Resolution:

- `import stdlib.http` resolves to `stdlib/http.sprout`
- loader checks importing file directory first, then current working directory
- import cycles are rejected
- bare `import x.y.z` introduces a namespace qualifier using the last path segment (`z.symbol`)
- `import x.y.z as alias` introduces `alias.symbol`
- `import x.y.z (name1, name2)` imports only those names unqualified
- importing `export type Name` exposes the type name only
- importing `export type Name(..)` also exposes the type's constructors for pattern matches and calls
- top-level declarations are internally namespaced by module, so imported modules no longer flatten into one global scope

Export behavior:

- only explicitly exported top-level declarations are importable
- declarations without `export` are module-private

Commands:

- LLVM IR output: `python3 -m sprout.cli compile input.sprout -o out.ll`
- Native binary (requires `clang`): `python3 -m sprout.cli compile input.sprout --native -o out_bin`
- REPL: `python3 -m sprout.cli repl`
  - commands: `:type EXPR`, `:t EXPR`, `:instances TYPE`, `:i TYPE`, `:help`, `:quit`
  - the foundational prelude is loaded by default, so list literals, dict literals, `split_ints(...)`, `foldable_to_vec(...)`, and `++` work immediately
  - `:instances TYPE` lists matching unary typeclass instances for a type, including constructor-head matches such as `List Int` reporting `Functor List`
  - use ordinary imports inside the session to access stdlib modules, for example `import stdlib.http` or `import stdlib.string`
  - imported modules use their final path segment (`http.http_ok(...)`, `math.gcd(...)`)
- Formatter/linter baseline:
  - format in place: `python3 -m sprout.cli fmt your_file.sprout`
  - check formatting only: `python3 -m sprout.cli fmt --check your_file.sprout`
  - lint baseline style issues: `python3 -m sprout.cli lint your_file.sprout`
  - current scope: whitespace-aware formatting, comment preservation, trailing-whitespace/tab/final-newline checks

## Backlog

See [docs/backlog.md](./docs/backlog.md) for the current roadmap and follow-up work.

## Contributing

See [AGENTS.md](./AGENTS.md) for project-specific collaboration and change rules.
