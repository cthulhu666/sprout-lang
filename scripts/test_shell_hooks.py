#!/usr/bin/env python3
"""Exercise scripts/seed_gate.sh and scripts/guidelines_reminder.sh in a throwaway repo.

Usage: python3 scripts/test_shell_hooks.py [scratch_dir]
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SEED_GATE = HERE / "seed_gate.sh"
GUIDELINES = HERE / "guidelines_reminder.sh"
REDIRECT = HERE / "code_review_redirect.sh"
S = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/shell-hooks-scratch").resolve()
WT = S.parent / (S.name + "-wt")

shutil.rmtree(S, ignore_errors=True)
shutil.rmtree(WT, ignore_errors=True)
S.mkdir(parents=True)

results = []


def g(where, *a):
    subprocess.run(["git", "-C", str(where), *a], check=True, capture_output=True)


def hook(script, payload, cwd):
    return subprocess.run(
        ["bash", str(script)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=str(cwd),
    )


def check(label, cond, detail=""):
    results.append(cond)
    print(f"{'OK  ' if cond else 'FAIL'} {label}{'  ' + detail if detail and not cond else ''}")


def commit_payload(cmd):
    return {"tool_input": {"command": cmd}, "hook_event_name": "PreToolUse"}


g(S.parent, "init", "-q", str(S))
g(S, "config", "user.email", "t@t")
g(S, "config", "user.name", "t")
(S / "stdlib" / "compiler").mkdir(parents=True)
(S / "bootstrap").mkdir()
(S / "stdlib/compiler/infer.sprout").write_text("a\n")
(S / "bootstrap/compile_driver.ll").write_text("seed\n")
g(S, "add", "-A")
g(S, "commit", "-qm", "init")
g(S, "worktree", "add", "-q", "-b", "wt", str(WT))

# --- seed gate ---------------------------------------------------------------
r = hook(SEED_GATE, commit_payload("ls -la"), cwd=S)
check("seed gate: a non-commit command passes", r.returncode == 0)

# Compiler source staged in the WORKTREE, seed untouched. The hook's cwd is the main
# checkout, which has nothing staged — this is the case that silently passed before.
(WT / "stdlib/compiler/infer.sprout").write_text("changed in worktree\n")
g(WT, "add", "stdlib/compiler/infer.sprout")

r = hook(SEED_GATE, commit_payload(f'cd {WT} && git commit -m "x"'), cwd=S)
check("seed gate: `cd <worktree> && git commit` without a seed is blocked", r.returncode == 2)

r = hook(SEED_GATE, commit_payload(f'git -C {WT} commit -m "x"'), cwd=S)
check("seed gate: `git -C <worktree> commit` without a seed is blocked", r.returncode == 2)

(WT / "bootstrap/compile_driver.ll").write_text("reseeded\n")
g(WT, "add", "bootstrap/compile_driver.ll")
r = hook(SEED_GATE, commit_payload(f'cd {WT} && git commit -m "x"'), cwd=S)
check("seed gate: a staged seed lets the worktree commit through", r.returncode == 0)

# The main checkout must keep behaving as it always did.
(S / "stdlib/compiler/infer.sprout").write_text("changed in main\n")
g(S, "add", "stdlib/compiler/infer.sprout")
r = hook(SEED_GATE, commit_payload('git commit -m "x"'), cwd=S)
check("seed gate: main checkout without a seed is still blocked", r.returncode == 2)

# --- guidelines reminder -----------------------------------------------------
r = hook(GUIDELINES, {"tool_input": {"file_path": "/x/stdlib/prelude.sprout"}}, cwd=HERE.parent)
try:
    out = json.loads(r.stdout)
    ctx = out["hookSpecificOutput"]["additionalContext"]
except (ValueError, KeyError):
    out, ctx = None, ""
check("guidelines: a .sprout edit emits additionalContext on STDOUT", bool(ctx), r.stdout[:200])
check("guidelines: it names guidelines.md", "guidelines.md" in ctx)
check("guidelines: stderr stays empty (Claude never reads exit-0 stderr)", r.stderr == "")

r = hook(GUIDELINES, {"tool_input": {"file_path": "/x/README.md"}}, cwd=HERE.parent)
check("guidelines: a non-Sprout path stays silent", r.returncode == 0 and r.stdout.strip() == "")

# --- code-review redirect ----------------------------------------------------
def redirect(stdin, cwd=HERE.parent):
    return subprocess.run(
        ["bash", str(REDIRECT)], input=stdin, capture_output=True, text=True, cwd=str(cwd)
    )


def expansion(name, args):
    return json.dumps(
        {"hook_event_name": "UserPromptExpansion", "command_name": name, "command_args": args}
    )


def reason_of(r):
    try:
        d = json.loads(r.stdout)
        return d["reason"] if d.get("decision") == "block" else None
    except (ValueError, KeyError, TypeError):
        return None


r = redirect(expansion("code-review", "medium 909"))
check(
    "redirect: /code-review <args> is blocked and names the wrapper with the args",
    r.returncode == 0 and "/pr-tracker:sprout-review medium 909" in (reason_of(r) or ""),
    r.stdout[:200] + r.stderr[:200],
)

r = redirect(expansion("code-review", ""))
why = reason_of(r) or ""
check(
    "redirect: no args gives no trailing or double space",
    "/pr-tracker:sprout-review instead" in why and "  " not in why,
    why,
)

r = redirect(expansion("code-review", 'fix "quoted" arg'))
check(
    "redirect: a double quote in args still yields valid JSON",
    'fix "quoted" arg' in (reason_of(r) or ""),
    r.stdout[:200],
)

r = redirect(expansion("pr-tracker:sprout-review", "high"))
check("redirect: the wrapper itself passes silently", r.returncode == 0 and r.stdout == "")

r = redirect(expansion("code-reviewer", ""))
check("redirect: a prefix lookalike is not blocked", r.returncode == 0 and r.stdout == "")

r = redirect("not json{")
check("redirect: garbage stdin passes silently", r.returncode == 0 and r.stdout == "")

r = redirect("")
check("redirect: empty stdin passes silently", r.returncode == 0 and r.stdout == "")

settings = json.loads((HERE.parent / ".claude/settings.json").read_text())
cmds = [
    h["command"]
    for evs in settings["hooks"].values()
    for e in evs
    for h in e["hooks"]
]
check(
    "settings: every hook command points at an existing script",
    all(
        (HERE.parent / "scripts" / c.rsplit("/scripts/", 1)[1]).exists()
        for c in cmds
    ),
)
check(
    "settings: code-review redirect is wired on UserPromptExpansion",
    any(
        e.get("matcher") == "code-review"
        and any("code_review_redirect.sh" in h["command"] for h in e["hooks"])
        for e in settings["hooks"].get("UserPromptExpansion", [])
    ),
)

shutil.rmtree(WT, ignore_errors=True)
print("\nSUITE", "PASSED" if all(results) else "FAILED")
sys.exit(0 if all(results) else 1)
