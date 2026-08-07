# tests/diagnostic_stream

Fixtures for `just diagnostic-stream-smoke`.

The invariant under test is a property of the **driver process**, not of a Sprout
value, so it cannot be written as a `.spr` unit test: it is about which *stream*
a diagnostic lands on and what *exit status* the process returns. Those are only
observable from outside the program, so the assertions live in the justfile
recipe and the inputs live here.

| fixture | expected |
|---|---|
| `unknown_variable.spr` | rejected — `ERROR: check: Unknown variable` on **stderr**, nonzero exit, no IR on stdout |
| `parse_error.spr` | rejected — `ERROR: bundle:` on **stderr**, nonzero exit (a different code path: bundler, not checker) |
| `valid.spr` | accepted — exit 0, IR on **stdout**, stderr free of diagnostics |

`valid.spr` is the positive control. Without it the other two would also pass
against a compiler that rejected every input, which would make the gate
worthless in precisely the situation it exists to catch.

Keep these fixtures free of imports. They must exercise the diagnostic path
only, so a prelude or stdlib change cannot turn this gate red for an unrelated
reason.
