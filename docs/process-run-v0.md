# Running child processes (v0)

`stdlib.process` runs a program, captures its stdout and stderr as `Bytes`, and reports how it
ended. This document is the design behind the API and the runtime function `proc_run_raw` under it.

## API

```sprout
proc_run(argv)                        -> Result ProcError ProcResult !{IO}
proc_run_stdin(argv, text)            -> Result ProcError ProcResult !{IO}
proc_run_with(config, argv, stdin)    -> Result ProcError ProcResult !{IO}

type ProcExit   = Exited Int | Signaled Int
type ProcResult = ProcResult ProcExit Bytes Bytes
type ProcGroup  = InheritGroup | NewGroup
ProcConfig(timeout_ms: Maybe Int, max_output_bytes: Int, group: ProcGroup)
type ProcError  = ProcSpawnFailed String | ProcTimedOut Bytes Bytes
                | ProcOutputTooLarge Int Bytes Bytes | ProcInvalidArgument String
```

- `default_proc_config()` is no timeout, 16 MiB per stream and the caller's process group.
- `proc_ok` is true only for `Exited 0`. A signal death is `Signaled n`, not exit status 1.
- `ProcSpawnFailed` covers a missing or non-executable program (`ENOENT`, `EACCES`, ...), a failed
  `fork` or `pipe`, and a helper thread that could not start. Before, a failed exec read as exit 127.
- `ProcTimedOut` and `ProcOutputTooLarge` carry what the child wrote before it was killed. The
  stream that passed the cap is cut at exactly the cap, which `ProcOutputTooLarge` carries first.
  The cap applies to stdout and stderr separately.
- `ProcInvalidArgument`: empty argv, `Just n` with `n < 1`, or `max_output_bytes < 1`. These are
  checked in Sprout before the extern runs.
- A caller binds the result with `match`, or `try` in a function that returns the same error type.
  In a `do` block `<-` unwraps a `Result` (spec §5.9), so it is not the way to hold one.
- `NewGroup` puts the child in its own process group, so a kill on timeout or overflow also reaches
  what the child started. With `InheritGroup` only the child is killed, and a grandchild that still
  holds the output pipes does not hold the call up: the call stops reading when it kills.

## Runtime

One extern, `proc_run_raw`, replaces `proc_run_vec` and `proc_run_stdin_vec`. It returns a flat
record `ProcRaw(kind, code, out, err, message)` that `stdlib/process.sprout` turns into the ADTs
above, so the runtime builds no ADT but that one.

1. The task resolves `argv[0]` against `PATH` in the parent (as `execvp` does, including the default
   path when `PATH` is unset), forks on the scheduler thread, and calls `execv` in the child with the
   resolved path. `execvp` is not async-signal-safe on glibc (it allocates while it searches), and a
   helper thread may hold the allocator lock at the time of the fork.
2. Between `fork` and `execv` the child makes only async-signal-safe calls: `dup2` onto 0/1/2,
   `signal(SIGPIPE, SIG_DFL)`, `setpgid` for `NewGroup`. The runtime ignores `SIGPIPE` for itself,
   and an ignored signal stays ignored across exec, so without the reset every child inherited it.
3. An exec failure is reported through a close-on-exec pipe: the child writes `errno` and exits, and
   the parent reads that pipe before it goes on. The read ends at exec or at the child's exit.
4. A detached helper thread (128 KiB stack) polls the child's stdout and stderr, feeds stdin, enforces
   the cap, waits for the child with `waitid(WNOWAIT)` and reaps it. The task parks on the helper's
   completion pipe, so every other green task keeps running.
5. A timeout, an overflow or a cancel kills the child. The kill runs under a mutex that the helper
   also takes to set `exited` and reap, so it is never aimed at a pid that was reaped and reused.
6. `with_timeout` or `scope_cancel` on a parked task runs a park hook: kill, stop the helper, drop the
   task's reference. The completion pipe's read end is the park's unowned fd, which the scheduler
   closes. The helper never allocates on the GC heap, never calls `sprout_fail`, and creates no
   descriptor; an out-of-memory failure there kills the child and is reported as `ProcSpawnFailed`.

Descriptor and reference-count ownership is a table beside `ProcRequest` in
`runtime/sprout_runtime.c`.

## Not done

- No cap on the number of helper threads: one per running child.
- `execv` does not fall back to `/bin/sh` for a file with no `#!` line, as `execvp` does.
- Windows: see `docs/windows-port-v0.md` (`CreateProcess`, and the completion pipe).
