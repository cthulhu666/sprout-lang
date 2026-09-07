#!/usr/bin/env python3
# Review gate: on Stop, refuse the turn once per distinct working-tree state until
# the agent has reviewed its own change against the checklist below.
#
# Stop is the only unconditional exit from a turn, so it is the only event that can
# gate "the change is finished". PostToolUse fires mid-edit and cannot block;
# TaskCompleted is opt-in by the model (no TaskCreate call, no hook).
#
# Loop safety, three ways: the first Stop of a session records the tree as the
# BASELINE (pre-existing dirt never fires), a state already shown is never shown
# twice, and MAX_BLOCKS caps a session no matter what.
#
# The checklist is path-aware: only the items whose paths actually moved are shown.
#
# Wired as a Stop hook from .claude/settings.json.
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

MAX_BLOCKS = 5

# Generated artifacts, not reviewed prose or code. Excluding them also keeps a
# post-`refresh-seed` Stop cheap: the seed is 13 MB and its diff runs to hundreds
# of thousands of lines, and `tests/golden/ir/` is 61 more files on top.
IGNORED = (
    ".claude/",
    "build/",
    "tests/golden/ir/",
    "bootstrap/compile_driver.ll",
)

# (predicate on a changed path, item, how to answer it)
CHECKLIST = [
    (
        lambda p: p.endswith((".sprout", ".spr")),
        "Is it idiomatic Sprout?",
        "Read docs/idiomatic-sprout.md and docs/style-guide-v0.md now — do not answer "
        "from memory; the language moves and your recollection of it is out of date.",
    ),
    (
        lambda p: p.startswith("stdlib/"),
        "Does it follow the authoring guidelines for this layer?",
        "Read docs/guidelines.md, heeding the [Library] / [Compiler] audience tags.",
    ),
    (
        lambda p: p.startswith("stdlib/compiler/") or p.startswith("runtime/"),
        "Are the GC ABI invariants and rooting rules upheld?",
        "Read docs/compiler-internals.md — type-aware rooting, the GC safety linter, "
        "and (for runtime/) the APPROVED_BUILTINS justification rule.",
    ),
    (
        lambda p: True,
        "Are docs and spec in sync with the change?",
        "AGENTS.md §Docs & Spec: a change to syntax, semantics, typing, evaluation "
        "order, visibility or diagnostics updates docs/spec-v0.md and the relevant "
        "docs/*.md in the SAME change. Landed roadmap/BACKLOG items get closed too.",
    ),
]


def log(msg):
    print(f"[review-gate] {msg}", file=sys.stderr, flush=True)


def git(root, *args):
    p = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, errors="replace"
    )
    return p.stdout if p.returncode == 0 else ""


def interesting(path):
    return not path.startswith(IGNORED)


def worktree_roots(root):
    """Every checkout of this repo. Work here happens in linked worktrees, so a gate
    reading only the main one reviews nothing."""
    roots = [
        Path(line[len("worktree ") :]).resolve()
        for line in git(root, "worktree", "list", "--porcelain").splitlines()
        if line.startswith("worktree ")
    ]
    return roots or [root]


def changed_paths(root):
    # --porcelain=v1 -uall: one line per changed or untracked file, "XY path".
    # `key` carries the worktree so two checkouts of one path stay distinct; `path`
    # stays repo-relative because that is what the CHECKLIST predicates match on.
    out = []
    for wt in worktree_roots(root):
        for line in git(wt, "status", "--porcelain=v1", "-uall").splitlines():
            if len(line) < 4:
                continue
            status, path = line[:2], line[3:]
            # A rename prints "old -> new"; the new name is what a reviewer reads.
            if " -> " in path:
                path = path.split(" -> ", 1)[1]
            path = path.strip('"')
            if interesting(path):
                key = path if wt == root else f"{wt.name}/{path}"
                out.append((status, key, path, wt))
    return out


def tree_state(paths):
    """One digest per changed path, so the report can name what moved since baseline.

    An untracked file is stat'd rather than read — an untracked build artifact
    would otherwise be hashed in full on every single Stop.
    """
    state = {}
    for status, key, path, wt in paths:
        if status == "??":
            try:
                st = (wt / path).stat()
                body = f"{st.st_size}:{st.st_mtime_ns}"
            except OSError:
                body = "gone"
        else:
            body = git(wt, "diff", "HEAD", "--", path)
        state[key] = status + ":" + hashlib.sha256(body.encode()).hexdigest()
    return state


def digest(state):
    return hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()


def main():
    d = json.load(sys.stdin)
    session = d.get("session_id") or "no-session"

    root = os.environ.get("CLAUDE_PROJECT_DIR") or d.get("cwd") or "."
    root = git(root, "rev-parse", "--show-toplevel").strip()
    if not root:
        log("not a git repo — pass through")
        return 0
    root = Path(root).resolve()

    paths = changed_paths(root)
    if not paths:
        return 0
    now = tree_state(paths)
    fp = digest(now)

    # State lives in the git dir, so it is per-worktree and never committed.
    git_dir = git(root, "rev-parse", "--absolute-git-dir").strip()
    state_path = Path(git_dir) / "claude-review-gate" / f"{session}.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        state = json.loads(state_path.read_text())
    except (OSError, ValueError):
        state = None

    if state is None:
        # First Stop of the session: whatever is dirty now predates the agent.
        state_path.write_text(json.dumps({"baseline": now, "seen": [fp], "blocks": 0}))
        log("baseline recorded — pass through")
        return 0

    if fp in state["seen"]:
        return 0

    state["seen"].append(fp)
    state["blocks"] += 1
    over_cap = state["blocks"] > MAX_BLOCKS
    state_path.write_text(json.dumps(state))

    if over_cap:
        print(json.dumps({"systemMessage": "[review-gate] block cap reached — passing through"}))
        return 0

    base = state["baseline"]
    moved = [(s, k, p) for s, k, p, _ in paths if now[k] != base.get(k)]
    moved += [(" X", k, k) for k in sorted(set(base) - set(now))]

    lines = [
        "REVIEW GATE — this change has not been reviewed. Review it against the",
        "checklist, state the verdict per item, and fix what fails before stopping.",
        "",
        "Changed this session:",
    ]
    lines += [f"  {status} {key}" for status, key, _ in moved]
    lines.append("")
    n = 0
    for applies, item, how in CHECKLIST:
        if not any(applies(p) for _, _, p in moved):
            continue
        n += 1
        lines.append(f"{n}) {item}")
        lines.append(f"   {how}")
    print("\n".join(lines), file=sys.stderr)
    return 2


sys.exit(main())
