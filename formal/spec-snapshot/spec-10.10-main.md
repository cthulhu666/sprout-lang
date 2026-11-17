### 10.10 Main entrypoint

```sprout
fn main() -> Unit !{IO} =
  print("hello")
```

`main` is the conventional program entrypoint in v0. An executable entrypoint must
be a zero-argument `fn main() -> Unit !{IO}` or `fn main() -> Int !{IO}`, after
module qualification is resolved. Pure `main` definitions and effect-polymorphic
`main` definitions are rejected at the executable boundary. Helper functions
may still use shapes such as `Maybe a !{IO}` or `Result e a !{IO}` and be
handled explicitly from `main`.

The entrypoint signature must be written out: the gate reads the **declared**
signature, not the inferred type, so `fn main() = print("hi")` is rejected even
though its body is well-typed.

These rules are enforced at type-check for any **defined** `main` (a function
named `main` in the entry file; an imported module's `main` is an ordinary function
and is not gated), as a final well-formedness gate that runs only once the body
itself typechecks — so a broken `main` body reports its own error first. The diagnostics are:
`Executable entrypoint \`main\` must take zero arguments`,
`… must declare its return type and effect` (an unannotated `main`),
`… must return Unit or Int`, `… must declare the {IO} effect` (a pure `main`),
and `… must not be effect-polymorphic`. Each carries the accepted signature as
a trailing hint — `; write \`fn main() -> Unit !{IO}\`, or \`fn main() -> Int
!{IO}\` to set the process exit code` — so one edit resolves the diagnostic
rather than each rejected field revealing the next. A **missing** `main` is not yet
diagnosed: at type-check the compiler cannot distinguish a library check from an
executable build (a library module legitimately defines no `main`), so requiring
one awaits an explicit executable-vs-library compile mode.

A `Unit`-returning `main` always exits the process with code `0`. An
`Int`-returning `main`'s return value becomes the process exit code
(truncated to the platform's native exit-code width, mirroring C's `int
main(void)` convention):

```sprout
fn main() -> Int !{IO} =
  do
    ok <- run_checks()
    if ok then 0 else 1
```

