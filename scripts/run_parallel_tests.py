from __future__ import annotations

import os
import queue
import shlex
import subprocess
import sys
import threading
from pathlib import Path


def discover_tests() -> list[str]:
    return sorted(
        str(path).replace("/", ".")[:-3]
        for path in Path("tests").glob("test_*.py")
    )


def select_tests(argv: list[str]) -> list[str]:
    if argv:
        return argv
    configured = os.environ.get("SPROUT_TESTS", "").strip()
    if configured:
        return shlex.split(configured)
    return discover_tests()


def main() -> int:
    tests = select_tests(sys.argv[1:])
    if not tests:
        print("No test files found", file=sys.stderr)
        return 1

    jobs = max(1, int(os.environ.get("SPROUT_TEST_JOBS", "4")))
    work: queue.Queue[str | None] = queue.Queue()
    failures: list[tuple[str, int]] = []
    failure_lock = threading.Lock()

    for test in tests:
        work.put(test)
    for _ in range(jobs):
        work.put(None)

    def run_worker(slot: int) -> None:
        prefix = f"[job-{slot}]"
        while True:
            test = work.get()
            if test is None:
                return
            print(f"{prefix} START {test}", flush=True)
            proc = subprocess.Popen(
                [sys.executable, "-m", "unittest", test, "-v"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            assert proc.stdout is not None
            for line in proc.stdout:
                print(f"{prefix} {line.rstrip()}", flush=True)
            code = proc.wait()
            if code != 0:
                with failure_lock:
                    failures.append((test, code))
                print(f"{prefix} FAIL {test} (exit {code})", flush=True)
            else:
                print(f"{prefix} OK {test}", flush=True)

    threads = [
        threading.Thread(target=run_worker, args=(slot,), daemon=False)
        for slot in range(1, jobs + 1)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    if failures:
        print("Parallel test failures:", file=sys.stderr)
        for test, code in failures:
            print(f"  {test} (exit {code})", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
