from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from pathlib import Path
import re
import subprocess
import sys
import tempfile


GC_LINE = re.compile(
    r"\[sprout gc\] cycle=(?P<cycle>\d+) reason=(?P<reason>[a-z]+) "
    r"threshold=(?P<threshold>\d+) heap_before=(?P<heap_before>\d+) "
    r"heap_after=(?P<heap_after>\d+) live=(?P<live>\d+) "
    r"swept=(?P<swept>\d+) elapsed_us=(?P<elapsed_us>\d+)"
)


@dataclass(frozen=True)
class Workload:
    name: str
    expected_stdout: str
    source: str


WORKLOADS = (
    Workload(
        name="crypto_churn",
        expected_stdout="600",
        source="""
module main
import stdlib.bytes (from_string, length)
import stdlib.crypto as crypto

fn churn(n: Int, acc: Int) -> Int =
  if n == 0 then acc else
    match crypto.bytes_xor(from_string("abc"), from_string("ABC")) with
    | Ok out -> churn(n - 1, acc + length(out))
    | Err _ -> acc

fn main() -> Unit !{IO} =
  print(churn(200, 0))
""".strip(),
    ),
    Workload(
        name="vector_build",
        expected_stdout="400",
        source="""
module main
import stdlib.collections (Vec, vec_append, vec_empty, vec_get_or, vec_length)

fn build(n: Int, acc: Vec Int) -> Vec Int =
  if n == 0 then acc else build(n - 1, vec_append(n, acc))

fn score(vec: Vec Int) -> Int =
  vec_length(vec) + vec_get_or(0, 0, vec)

fn main() -> Unit !{IO} =
  print(score(build(200, vec_empty())))
""".strip(),
    ),
    Workload(
        name="builder_build",
        expected_stdout="64",
        source="""
module main
import stdlib.bytes (Builder, builder_append, builder_build, builder_byte, builder_empty, length)

fn build(n: Int, acc: Builder) -> Builder =
  if n == 0 then acc else build(n - 1, builder_append(acc, builder_byte(65)))

fn main() -> Unit !{IO} =
  print(length(builder_build(build(64, builder_empty()))))
""".strip(),
    ),
)


@dataclass(frozen=True)
class Cycle:
    cycle: int
    reason: str
    threshold: int
    heap_before: int
    heap_after: int
    live: int
    swept: int
    elapsed_us: int


@dataclass(frozen=True)
class Summary:
    workload: str
    threshold_label: str
    cycle_count: int
    threshold_cycles: int
    atexit_cycles: int
    swept_total: int
    max_live: int
    total_elapsed_us: int
    max_elapsed_us: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Measure native GC behavior across threshold settings.")
    parser.add_argument(
        "--threshold",
        action="append",
        dest="thresholds",
        help="Threshold label to measure. Repeatable. Defaults to off,1,128,1024,4096.",
    )
    parser.add_argument(
        "--workload",
        action="append",
        dest="workloads",
        help=f"Workload name to run. Repeatable. Defaults to all: {', '.join(w.name for w in WORKLOADS)}.",
    )
    return parser.parse_args()


def compile_workload(workload: Workload, tmp_path: Path) -> Path:
    spr_path = tmp_path / f"{workload.name}.sprout"
    bin_path = tmp_path / workload.name
    spr_path.write_text(workload.source + "\n", encoding="utf-8")
    subprocess.run(
        [
            sys.executable,
            "-m",
            "sprout.cli",
            "compile",
            str(spr_path),
            "--native",
            "--with-stdlib",
            "-o",
            str(bin_path),
        ],
        check=True,
    )
    return bin_path


def parse_cycles(stderr: str) -> list[Cycle]:
    cycles: list[Cycle] = []
    for match in GC_LINE.finditer(stderr):
        cycles.append(
            Cycle(
                cycle=int(match.group("cycle")),
                reason=match.group("reason"),
                threshold=int(match.group("threshold")),
                heap_before=int(match.group("heap_before")),
                heap_after=int(match.group("heap_after")),
                live=int(match.group("live")),
                swept=int(match.group("swept")),
                elapsed_us=int(match.group("elapsed_us")),
            )
        )
    return cycles


def summarize(workload: Workload, threshold_label: str, cycles: list[Cycle]) -> Summary:
    return Summary(
        workload=workload.name,
        threshold_label=threshold_label,
        cycle_count=len(cycles),
        threshold_cycles=sum(1 for cycle in cycles if cycle.reason == "threshold"),
        atexit_cycles=sum(1 for cycle in cycles if cycle.reason == "atexit"),
        swept_total=sum(cycle.swept for cycle in cycles),
        max_live=max((cycle.live for cycle in cycles), default=0),
        total_elapsed_us=sum(cycle.elapsed_us for cycle in cycles),
        max_elapsed_us=max((cycle.elapsed_us for cycle in cycles), default=0),
    )


def run_workload(bin_path: Path, workload: Workload, threshold_label: str) -> Summary:
    env = os.environ.copy()
    env["SPROUT_DEBUG_GC"] = "1"
    env["SPROUT_GC_THRESHOLD"] = threshold_label
    run = subprocess.run([str(bin_path)], check=False, capture_output=True, text=True, env=env)
    if run.returncode != 0:
        raise RuntimeError(
            f"{workload.name} failed under threshold {threshold_label}:"
            f"\nstdout:\n{run.stdout}\nstderr:\n{run.stderr}"
        )
    actual_stdout = run.stdout.strip()
    if actual_stdout != workload.expected_stdout:
        raise RuntimeError(
            f"{workload.name} expected stdout {workload.expected_stdout!r}"
            f" but got {actual_stdout!r} under threshold {threshold_label}"
        )
    cycles = parse_cycles(run.stderr)
    if not cycles:
        raise RuntimeError(
            f"{workload.name} produced no GC diagnostics under threshold {threshold_label}."
        )
    return summarize(workload, threshold_label, cycles)


def render_table(rows: list[Summary]) -> str:
    headers = (
        "workload",
        "threshold",
        "cycles",
        "threshold_cycles",
        "atexit_cycles",
        "swept_total",
        "max_live",
        "total_elapsed_us",
        "max_elapsed_us",
    )
    values = [headers] + [
        (
            row.workload,
            row.threshold_label,
            str(row.cycle_count),
            str(row.threshold_cycles),
            str(row.atexit_cycles),
            str(row.swept_total),
            str(row.max_live),
            str(row.total_elapsed_us),
            str(row.max_elapsed_us),
        )
        for row in rows
    ]
    widths = [max(len(line[idx]) for line in values) for idx in range(len(headers))]

    def format_line(cols: tuple[str, ...]) -> str:
        return "  ".join(col.ljust(widths[idx]) for idx, col in enumerate(cols))

    lines = [format_line(headers), format_line(tuple("-" * width for width in widths))]
    lines.extend(format_line(row) for row in values[1:])
    return "\n".join(lines)


def selected_workloads(names: list[str] | None) -> list[Workload]:
    if not names:
        return list(WORKLOADS)
    available = {workload.name: workload for workload in WORKLOADS}
    missing = [name for name in names if name not in available]
    if missing:
        raise SystemExit(f"Unknown workload(s): {', '.join(missing)}")
    return [available[name] for name in names]


def main() -> int:
    args = parse_args()
    thresholds = args.thresholds or ["off", "1", "128", "1024", "4096"]
    workloads = selected_workloads(args.workloads)
    rows: list[Summary] = []

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        compiled = {workload.name: compile_workload(workload, tmp_path) for workload in workloads}
        for workload in workloads:
            print(f"# {workload.name}")
            workload_rows: list[Summary] = []
            for threshold_label in thresholds:
                workload_rows.append(run_workload(compiled[workload.name], workload, threshold_label))
            print(render_table(workload_rows))
            print()
            rows.extend(workload_rows)

    if rows:
        print("# Overall")
        print(render_table(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
