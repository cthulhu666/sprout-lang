#!/usr/bin/env python3
"""Aggregate `SPROUT_DEBUG_GC=1` per-cycle `elapsed_us` into a pause distribution.

The runtime already logs one line per collection; this only summarises it, so there
is no instrument to keep in sync. Prints the percentiles plus the mean live set and
the mean slots swept, which are the two quantities the pause is a function of
(docs/gc-generational-v0.md §13).

**Reports the minimum of each statistic across `--reps` runs.** A single run of a
pause distribution on a laptop measures the machine's load as much as the collector:
the same binary has produced max = 182 us and max = 8,516 us on consecutive runs with
identical `live`, `swept` and `marked` (§13.4). Load can only ever *add* time, so the
minimum across repetitions is the one summary that converges instead of drifting.
Note this is a min of percentiles, not a percentile of the pooled runs — it answers
"how good does this configuration get", which is what a cost model needs. It is NOT
a claim about the worst pause a frame will see; §13.4 says why that is not available
from this instrument.

Usage: pause_stats.py [--reps N] <label> <binary> [KEY=VAL ...]
"""
import os
import re
import subprocess
import sys

LINE = re.compile(r"^\[sprout gc\] cycle=(\d+) .*?live=(\d+) .*?swept=(-?\d+) elapsed_us=(\d+)")


def pct(xs, p):
    if not xs:
        return 0
    k = (len(xs) - 1) * p / 100.0
    lo = int(k)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def one_run(binary, env):
    """One run -> (sorted pauses, mean live, mean swept), or None if it never collected."""
    try:
        proc = subprocess.run([binary], env=env, capture_output=True, text=True)
    except OSError as exc:
        print(f"ERROR: cannot run {binary}: {exc}", file=sys.stderr)
        sys.exit(2)
    us, live, swept = [], [], []
    for line in proc.stderr.splitlines():
        m = LINE.match(line)
        if m:
            us.append(int(m.group(4)))
            live.append(int(m.group(2)))
            swept.append(int(m.group(3)))
    if not us:
        return None
    return sorted(us), sum(live) // len(live), sum(swept) // len(swept)


def main():
    argv = sys.argv[1:]
    reps = 3
    if argv and argv[0] == "--reps":
        reps = int(argv[1])
        argv = argv[2:]
    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    label, binary = argv[0], argv[1]

    env = dict(os.environ, SPROUT_DEBUG_GC="1")
    for kv in argv[2:]:
        key, value = kv.split("=", 1)
        env[key] = value

    runs = [r for r in (one_run(binary, env) for _ in range(reps)) if r is not None]
    if not runs:
        print(f"{label:28s} NO GC CYCLES")
        return 1

    best = {p: min(pct(r[0], p) for r in runs) for p in (50, 95, 99)}
    cycles = min(len(r[0]) for r in runs)
    live = min(r[1] for r in runs)
    swept = min(r[2] for r in runs)
    print(
        f"{label:28s} cycles={cycles:>7d}  "
        f"p50={best[50]:>8.0f}  p95={best[95]:>8.0f}  p99={best[99]:>8.0f}  "
        f"|  live~{live:>8d}  swept~{swept:>8d}"
    )
    return 0


sys.exit(main())
