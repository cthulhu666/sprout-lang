#!/usr/bin/env python3
# Review gate: on Stop, refuse the turn once per distinct working-tree state until
# the agent has reviewed its own change against the checklist below.
#
# Stop is the only unconditional exit from a turn, so it is the only event that can
# gate "the change is finished". PostToolUse fires mid-edit and cannot block;
# TaskCompleted is opt-in by the model (no TaskCreate call, no hook).
#
# The cost of that is that Stop cannot tell "finished" from "blocked on a question",
# and an agent waiting on the user cannot review-and-fix. A turn whose last tool call
# is AskUserQuestion therefore passes through — recording NOTHING, so the change is
# still unreviewed and still gated once the answer arrives (see ended_on_question).
#
# Loop safety, three ways: the first Stop of a session records the tree as the
# BASELINE (pre-existing dirt never fires), a state already shown is never shown
# twice, and MAX_BLOCKS_PER_TURN caps any single user turn.
#
# The checklist is path-aware: only the items whose paths actually moved are shown.
# "Moved" means since the last ACCEPTED review, not since the session began — an
# unmoving baseline grows the report, and the checklist filter with it, until every
# item fires on every block and the gate is noise.
#
# Wired as a Stop hook from .claude/settings.json.
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

# Per USER TURN, not per session. Termination is the harness's job already: Claude Code
# force-ends a turn after CLAUDE_CODE_STOP_HOOK_BLOCK_CAP (default 8) consecutive
# blocking Stops. This only has to sit under that, so a runaway is released quietly here
# instead of by the platform's warning — and the budget refills every turn.
MAX_BLOCKS_PER_TURN = 3

# Enough transcript to hold the current turn. One entry can be large, so this is a
# byte budget rather than a line count, and the leading partial line is dropped.
TAIL_BYTES = 512 * 1024

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


def changed_paths(root):
    # --porcelain=v1 -uall: one line per changed or untracked file, "XY path".
    out = []
    for line in git(root, "status", "--porcelain=v1", "-uall").splitlines():
        if len(line) < 4:
            continue
        status, path = line[:2], line[3:]
        # A rename prints "old -> new"; the new name is what a reviewer reads.
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        path = path.strip('"')
        if interesting(path):
            out.append((status, path))
    return out


def tree_state(root, paths):
    """One digest per changed path, so the report can name what moved since baseline.

    An untracked file is stat'd rather than read — an untracked build artifact
    would otherwise be hashed in full on every single Stop.
    """
    state = {}
    for status, path in paths:
        if status == "??":
            try:
                st = (root / path).stat()
                body = f"{st.st_size}:{st.st_mtime_ns}"
            except OSError:
                body = "gone"
        else:
            body = git(root, "diff", "HEAD", "--", path)
        state[path] = status + ":" + hashlib.sha256(body.encode()).hexdigest()
    return state


def tail_entries(path):
    """The transcript's trailing JSONL entries, newest last. [] on any problem."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - TAIL_BYTES))
            raw = f.read()
        lines = raw.split(b"\n")
        if size > TAIL_BYTES:
            lines = lines[1:]  # the seek landed mid-entry
        out = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if isinstance(entry, dict):  # a bare list or string parses fine
                out.append(entry)
        return out
    except OSError:
        return []


def content_of(entry):
    """An entry's message content — a str, or a list of block dicts, or None."""
    msg = entry.get("message")
    return msg.get("content") if isinstance(msg, dict) else None


def blocks_of(entry):
    content = content_of(entry)
    if not isinstance(content, list):
        return []
    return [b for b in content if isinstance(b, dict)]


def ended_on_question(path):
    """Did this turn stop because the agent asked the USER something?

    True when the most recent tool call of the current turn is AskUserQuestion. A
    question is not a finished change: the agent cannot review-and-fix while it is
    blocked on an answer, and Stop cannot tell the two apart on its own (Stop is the
    only unconditional turn exit, so it fires either way — see the module header).

    Scoped to the current turn, whose start is the last real user prompt; a
    tool_result is the agent's own work coming back, not a prompt. Question ->
    answer -> more edits -> Stop therefore still blocks, because the edit's tool
    call is the most recent one.
    """
    if not path:
        return False
    for entry in reversed(tail_entries(path)):
        # `type` first, and only then the message's role: a real transcript carries
        # entries (`last-prompt`, `attachment`, …) whose message says "user" but which
        # are not prompts, and reading those as a turn boundary would end the scan early.
        msg = entry.get("message")
        role = entry.get("type") or (msg.get("role") if isinstance(msg, dict) else None)
        if role == "user":
            content = content_of(entry)
            if isinstance(content, str) or any(
                b.get("type") == "text" for b in blocks_of(entry)
            ):
                return False  # reached the prompt, no tool call since
        elif role == "assistant":
            for b in reversed(blocks_of(entry)):
                if b.get("type") == "tool_use":
                    return b.get("name") == "AskUserQuestion"
    return False


def new_turn(d, state):
    """(is this Stop the start of a new user turn, turn id to store).

    prompt_id is a UUID correlating a user prompt with every event downstream of it.
    It is optional in the payload; when it is absent, stop_hook_active carries the
    same signal, being false exactly on a turn's first Stop.
    """
    pid = d.get("prompt_id")
    if pid:
        return pid != state.get("turn"), pid
    return not d.get("stop_hook_active"), state.get("turn")


def digest(state):
    return hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()


def main():
    d = json.load(sys.stdin)
    session = d.get("session_id") or "no-session"

    # The checkout THIS session works in, taken from `cwd`. CLAUDE_PROJECT_DIR is the
    # main checkout even for a session in a linked worktree, and this repo has 66 of
    # them: reviewing all of them reports other sessions' concurrent edits as if they
    # were this session's, which is a review this session cannot perform.
    root = d.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or "."
    root = git(root, "rev-parse", "--show-toplevel").strip()
    if not root:
        log("not a git repo — pass through")
        return 0
    root = Path(root).resolve()

    paths = changed_paths(root)
    now = tree_state(root, paths)
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
        # First Stop of the session: whatever is dirty now predates the agent. This runs
        # even for a clean tree, which then baselines as empty — skipping it there would
        # spend the baseline on the session's first real change and never review it.
        state_path.write_text(
            json.dumps(
                {"baseline": now, "seen": [fp], "blocks": 0, "turn": d.get("prompt_id")}
            )
        )
        log("baseline recorded — pass through")
        return 0

    if not paths:
        # The tree went clean — committed or reverted. Nothing is pending review, so
        # re-anchor; otherwise the landed files reappear as phantom deletions in the
        # next report, which is the same staleness as a frozen baseline.
        if state["baseline"]:
            state["baseline"] = now
            state_path.write_text(json.dumps(state))
        return 0

    if fp in state["seen"]:
        # Reported once, and the agent then ended a turn without touching another
        # file: the review was answered. Re-anchor, so the next report names what is
        # new rather than everything touched since the session began.
        state["baseline"] = now
        state_path.write_text(json.dumps(state))
        return 0

    if ended_on_question(d.get("transcript_path")):
        # Deliberately records NOTHING — not `seen`, not the baseline, not a block.
        # Marking this state reviewed would let the change escape review entirely the
        # moment the question is answered, which is worse than the annoyance it fixes.
        log("turn ended on a question — pass through, still unreviewed")
        return 0

    fresh, turn = new_turn(d, state)
    if fresh:
        state["turn"], state["blocks"] = turn, 0
    state["seen"].append(fp)
    state["blocks"] += 1
    over_cap = state["blocks"] > MAX_BLOCKS_PER_TURN
    state_path.write_text(json.dumps(state))

    if over_cap:
        print(json.dumps({"systemMessage": "[review-gate] block cap reached — passing through"}))
        return 0

    base = state["baseline"]
    moved = [(s, p) for s, p in paths if now[p] != base.get(p)]
    moved += [(" X", p) for p in sorted(set(base) - set(now))]

    lines = [
        "REVIEW GATE — this change has not been reviewed. Review it against the",
        "checklist, state the verdict per item, and fix what fails before stopping.",
        "",
        "Changed since the last review:",
    ]
    lines += [f"  {status} {path}" for status, path in moved]
    lines.append("")
    n = 0
    for applies, item, how in CHECKLIST:
        if not any(applies(p) for _, p in moved):
            continue
        n += 1
        lines.append(f"{n}) {item}")
        lines.append(f"   {how}")
    print("\n".join(lines), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
