# Quiet-machine compiler A/B (`just bench-vm`)

`just bench-vm <base-ref> <head-ref>` compares two commits of the compiler on a throwaway GCE VM,
where nothing else runs. It is for wall-clock questions a laptop cannot answer. It is not a gate:
the numbers are timings, and the run costs money.

## What it measures

On the VM, `scripts/bench_vm_remote.sh` builds two compilers, each from its own tree's committed
seed (`bootstrap/compile_driver.ll`) and runtime at `-O2`: **A** from base, **B** from head. The
seed is that tree's compiler compiled by itself (CI enforces the fixed point), so each side is
what its tree ships: its codegen and its runtime ABI. Each ref's committed seed must be current:
CI enforces this on master and on PR heads, and a local unreseeded commit measures its stale seed.
Only the seed, `runtime/`, `stdlib/` and `scripts/check_clang_cpr.sh` are shipped to the VM. After
installing clang it runs `check_clang_cpr.sh`, so a host whose clang lowers `__int128` wrongly fails
the run instead of skewing it. The script then:

1. Compiles both trees' compiler with A and with B and compares the IR (`identical IR` or
   `IR DIFFERS`). A codegen change differs legitimately; a refactor should not.
2. Times A and B compiling **base's** compiler, `BENCH_ROUNDS` interleaved rounds (odd rounds A then
   B, even rounds B then A), under `/usr/bin/time -v`.
3. Runs each once more with `SPROUT_DEBUG_ALLOC=1` and tabulates the exit counters
   ([debugging.md](debugging.md) §What did a program allocate?).

## Config

Three variables, required. Set them in the environment or in `~/.config/sprout/bench-vm.env`
(shell syntax, one `NAME=value` per line; override the path with `BENCH_VM_CONFIG`). The file can
also set every optional knob below. It is sourced once, in a subshell; the environment wins over
the file. The script never reads the active `gcloud` config, and exits 2
before any `gcloud` call if one is missing.

- `BENCH_PROJECT`
- `BENCH_ACCOUNT`
- `BENCH_ZONE`

Keep the values out of the repo. Every `gcloud` call passes them explicitly.

Optional knobs, with defaults: `BENCH_MACHINE` (`c4a-standard-2`), `BENCH_IMAGE_FAMILY`
(`ubuntu-2604-lts-arm64`), `BENCH_IMAGE_PROJECT` (`ubuntu-os-cloud`),
`BENCH_ROUNDS` (`9`, an integer of at least 1, else exit 2 before any `gcloud` call),
`BENCH_TTL` (`20 + 2 * BENCH_ROUNDS` minutes, so `38m` for 9 rounds; an explicit value wins),
`BENCH_SSH_RETRY_SLEEP` (`5`, seconds between ssh probes), `BENCH_OUT_DIR` (`build/bench-vm`).

## The VM is always deleted

Three layers, innermost last:

1. **The script's trap.** `EXIT`, `INT`, `TERM` and `HUP` run one cleanup that deletes the VM by its
   exact name, then lists by that name to prove it is gone. If it is not, the script prints the
   delete command to run by hand and exits 1, even when the benchmark succeeded. It never lists or
   deletes by label or pattern. A signal during `instances create` makes the cleanup wait for the
   create to finish first, since a delete before the server-side insert finds nothing. Running
   `ssh`/`scp` jobs are killed at once.
2. **`--max-run-duration=$BENCH_TTL` with `--instance-termination-action=DELETE`.** The platform
   deletes the VM when the TTL ends, even if the script is killed with `SIGKILL` or the laptop
   sleeps.
3. **Auto-delete of the boot disk** (the default), so no disk outlives the VM.

The VM has no service account and no scopes, and `block-project-ssh-keys=true` puts the ssh key in
the instance's metadata, so the project's metadata is left untouched.

## Reading the output

The report goes to `build/bench-vm/<vm-name>/results.txt` and is printed. Plain text, easy to diff.

- Header: both SHAs, `nproc`, CPU model, kernel, clang version, load average before and after. A
  `WARNING: load <x> at start, machine not quiet` line appears when the 1-minute load average was
  still above 0.5 after up to two minutes of waiting for the builds' own load to settle.
- `A vs B compiling ...: identical IR` for both trees. `IR DIFFERS` is expected only when the change
  alters codegen.
- Per-round wall times, then median, min and max, and the median peak RSS in MiB. Compare medians;
  use min and max to judge the spread, and rerun with more `BENCH_ROUNDS` when they overlap.
- The allocation table, A against B. Counts are exact and repeat; a counter that moves is real
  whatever the timings say.
