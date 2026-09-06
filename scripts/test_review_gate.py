#!/usr/bin/env python3
"""Exercise scripts/review_gate.py in a throwaway repo.

Usage: python3 scripts/test_review_gate.py [scratch_dir]
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HOOK = Path(__file__).resolve().parent / "review_gate.py"
S = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/review-gate-scratch").resolve()

shutil.rmtree(S, ignore_errors=True)
S.mkdir(parents=True)


def g(*a):
    subprocess.run(["git", "-C", str(S), *a], check=True, capture_output=True)


def write(rel, body):
    p = S / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)


g("init", "-q")
g("config", "user.email", "t@t")
g("config", "user.name", "t")
write("stdlib/prelude.sprout", "a\n")
write("bootstrap/compile_driver.ll", "seed\n")
write("tests/golden/ir/examples__x.sprout.ll", "golden\n")
g("add", "-A")
g("commit", "-qm", "init")
write("preexisting.md", "dirt\n")


def run(label, expect, absent=None, present=None, session="sess1"):
    p = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps({"session_id": session, "cwd": str(S), "hook_event_name": "Stop"}),
        capture_output=True,
        text=True,
        cwd=str(S),
        env={**os.environ, "CLAUDE_PROJECT_DIR": str(S)},
    )
    good = p.returncode == expect
    for needle in absent or []:
        if needle in p.stderr:
            good = False
            label += f"  [{needle!r} should not be present]"
    for needle in present or []:
        if needle not in p.stderr:
            good = False
            label += f"  [{needle!r} missing]"
    print(f"{'OK  ' if good else 'FAIL'} {label}: exit {p.returncode} (expected {expect})")
    if p.stdout.strip():
        print("     stdout:", p.stdout.strip())
    for line in p.stderr.splitlines():
        print("     |", line)
    return good


results = [run("1. first Stop, pre-existing dirt -> baseline", 0)]
results.append(run("2. nothing changed since baseline", 0))

write("stdlib/prelude.sprout", "a\nb\n")
results.append(
    run(
        "3. .sprout edit -> idiomatic + guidelines items",
        2,
        absent=["preexisting.md"],
        present=["stdlib/prelude.sprout", "idiomatic-sprout.md", "guidelines.md", "spec"],
    )
)
results.append(run("4. same state re-presented", 0))

write("g.spr", "new\n")
results.append(run("5. new untracked file", 2, present=["g.spr"]))

(S / ".claude").mkdir(exist_ok=True)
write(".claude/settings.local.json", "{}\n")
results.append(run("6. .claude/ change only", 0))

write("bootstrap/compile_driver.ll", "reseeded\n" * 100)
results.append(run("7. generated seed change only", 0))

write("tests/golden/ir/examples__x.sprout.ll", "regenerated\n")
write("build/compile_driver_bin_stage1", "binary\n")
results.append(run("8. golden IR + build/ change only", 0))

# A fresh session baselines the existing dirt, so only the docs file has moved.
results.append(run("9a. fresh session baselines current dirt", 0, session="sess2"))
write("docs/spec-v0.md", "prose\n")
results.append(
    run(
        "9b. docs-only change -> spec item only",
        2,
        absent=["idiomatic-sprout.md", "compiler-internals.md", "guidelines.md"],
        present=["docs/spec-v0.md", "AGENTS.md"],
        session="sess2",
    )
)

write("stdlib/compiler/infer.sprout", "x\n")
results.append(
    run(
        "10. stdlib/compiler edit -> GC/internals item",
        2,
        present=["compiler-internals.md", "idiomatic-sprout.md"],
    )
)

# sess1 has spent 3 blocks by now (cases 3, 5, 10 — case 9 ran under sess2), so
# two more are shown and every state after the cap passes through with the notice.
for i in range(4):
    write("stdlib/prelude.sprout", "a\n" + "b\n" * (i + 2))
    results.append(run(f"11.{i}. block {i + 4} of cap {5}", 2 if i < 2 else 0))

state = json.loads((S / ".git/claude-review-gate/sess1.json").read_text())
print("state:", {k: (v if k != "seen" else len(v)) for k, v in state.items()})
print("\nSUITE", "PASSED" if all(results) else "FAILED")
sys.exit(0 if all(results) else 1)
