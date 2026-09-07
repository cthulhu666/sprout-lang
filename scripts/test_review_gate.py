#!/usr/bin/env python3
"""Exercise scripts/review_gate.py in a throwaway repo.

Usage: python3 scripts/test_review_gate.py [scratch_dir]
"""
import itertools
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


TURN = itertools.count()


def run(label, expect, absent=None, present=None, session="sess1", prompt=None, cwd=None, chain=False):
    # CLAUDE_PROJECT_DIR is the MAIN checkout even for a session working in a linked
    # worktree, so `cwd` is the only field that tells the two apart. Each call gets its
    # own prompt_id (= its own user turn) unless a case pins one to spend the budget.
    wt = cwd or S
    payload = {
        "session_id": session,
        "cwd": str(wt),
        "hook_event_name": "Stop",
        "stop_hook_active": chain,
    }
    if prompt is not False:
        payload["prompt_id"] = prompt or f"auto-{next(TURN)}"
    p = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=str(wt),
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

# A worktree session must be reviewed for ITS OWN edits and must not be blocked on
# another checkout's: this repo has 66 worktrees, and a union over them reports other
# sessions' concurrent work as if it were this session's.
WT = S.parent / (S.name + "-wt")
shutil.rmtree(WT, ignore_errors=True)
g("worktree", "add", "-q", "-b", "wt-branch", str(WT))
results.append(run("12a. worktree session baselines", 0, session="sess3", cwd=WT))

(WT / "stdlib/compiler").mkdir(parents=True, exist_ok=True)
(WT / "stdlib/compiler/types.sprout").write_text("edited in a worktree\n")
write("only-in-main.md", "a different session, editing the main checkout\n")
results.append(
    run(
        "12b. worktree session sees its own edit, not the main checkout's",
        2,
        present=["types.sprout", "idiomatic-sprout.md", "compiler-internals.md"],
        absent=["only-in-main.md"],
        session="sess3",
        cwd=WT,
    )
)

# The budget is per USER TURN, spent within one turn and refilled by the next. The
# session-lifetime counter this replaced went dead here and never re-armed.
for i in range(4):
    write("stdlib/prelude.sprout", "a\n" + "b\n" * (i + 2))
    results.append(run(f"11.{i}. turn t1, block {i + 1} of 3", 2 if i < 3 else 0, prompt="t1"))
for i in range(2):
    write("stdlib/prelude.sprout", "a\n" + "c\n" * (i + 2))
    results.append(run(f"11.{i + 4}. turn t2 refills the budget", 2, prompt="t2"))

# prompt_id is optional in the payload; stop_hook_active carries the same signal,
# being false exactly when this Stop opens a fresh turn.
write("stdlib/prelude.sprout", "a\nd\n")
results.append(run("15a. no prompt_id, fresh chain -> refilled", 2, prompt=False))
for i in range(3):
    write("stdlib/prelude.sprout", "a\nd\n" + "e\n" * (i + 1))
    results.append(
        run(f"15b.{i}. no prompt_id, same chain", 2 if i < 2 else 0, prompt=False, chain=True)
    )

# An accepted review re-anchors the baseline, so the next report is scoped to what is
# new. Without it, both the report and the checklist filter grow to the whole session.
results.append(run("13a. fresh session baselines", 0, session="sess4"))
write("stdlib/noisy.sprout", "s\n")
results.append(run("13b. sprout edit blocks", 2, session="sess4", present=["idiomatic-sprout.md"]))
results.append(run("13c. unchanged -> review accepted", 0, session="sess4"))
write("docs/quiet.md", "d\n")
results.append(
    run(
        "13d. docs-only edit now shows only the spec item",
        2,
        session="sess4",
        present=["docs/quiet.md", "AGENTS.md"],
        absent=["idiomatic-sprout.md", "guidelines.md", "noisy.sprout"],
    )
)

# Committing clears the tree. Nothing is pending review then, so the landed files must
# not come back as phantom deletions the next time something moves. Runs last: it
# commits every other case's leftovers too.
results.append(run("14a. fresh session baselines", 0, session="sess5"))
write("stdlib/landed.sprout", "s\n")
results.append(run("14b. edit blocks", 2, session="sess5", present=["landed.sprout"]))
results.append(run("14c. unchanged -> review accepted", 0, session="sess5"))
g("add", "-A")
g("commit", "-qm", "land the change")
results.append(run("14d. clean tree passes", 0, session="sess5"))
write("docs/after-landing.md", "d\n")
results.append(
    run(
        "14e. next edit does not resurrect the landed file",
        2,
        session="sess5",
        present=["docs/after-landing.md"],
        absent=["landed.sprout"],
    )
)

shutil.rmtree(WT, ignore_errors=True)
state = json.loads((S / ".git/claude-review-gate/sess1.json").read_text())
print("state:", {k: (v if k != "seen" else len(v)) for k, v in state.items()})
print("\nSUITE", "PASSED" if all(results) else "FAILED")
sys.exit(0 if all(results) else 1)
