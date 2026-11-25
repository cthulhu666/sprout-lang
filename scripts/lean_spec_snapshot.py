#!/usr/bin/env python3
"""Snapshot of the spec sections the Lean model follows (docs/lean-model-v0.md §7).

  lean_spec_snapshot.py --write   copy the sections into formal/spec-snapshot/
  lean_spec_snapshot.py --check   fail if a section differs from its snapshot

The sections are cut by heading, so moving a section within the spec changes nothing. A
change to the text fails `--check` until the model is re-checked and the snapshot refreshed.
"""
import difflib
import os
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SPEC = ROOT / "docs" / "spec-v0.md"
# LEAN_SNAPSHOT_DIR is for `just test-lean-oracle`, which checks the check against a doctored copy.
OUT = pathlib.Path(os.environ.get("LEAN_SNAPSHOT_DIR", ROOT / "formal" / "spec-snapshot"))

# (file, first heading, heading that ends the section)
SECTIONS = [
    ("spec-5.1-5.4-decls-exprs.md", r"^### 5\.1 Function declaration", r"^### 5\.5 "),
    ("spec-5.6-adt.md", r"^### 5\.6 ADT declaration", r"^### 5\.6\.1 "),
    ("spec-5.5-match.md", r"^### 5\.5 Match expression", r"^### 5\.6 "),
    ("spec-5.8-linear.md", r"^### 5\.8 Linear types", r"^### 5\.9 "),
    ("spec-7-typing-rules.md", r"^## 7\. Typing Rules", r"^## 8\. "),
    ("spec-10.10-main.md", r"^### 10\.10 Main entrypoint", r"^### 10\.11 "),
    # §8.5 up to its kind rules; the `Applicative` and `Monad` parts are outside Core
    ("spec-8.5-classes-kinds.md", r"^## 8\.5 Standard Prelude Typeclass Instances",
     r"^### `Applicative` class"),
]


def cut(lines, start, end):
    first = next((i for i, l in enumerate(lines) if re.match(start, l)), None)
    if first is None:
        sys.exit(f"lean_spec_snapshot: heading not found in the spec: {start}")
    last = next((i for i in range(first + 1, len(lines)) if re.match(end, lines[i])), None)
    if last is None:
        sys.exit(f"lean_spec_snapshot: no heading after the section: {end}")
    return lines[first:last]


# LEAN_MODEL_DIR is for `just test-lean-oracle`, which plants a tag citing an unsnapshotted section.
MODEL = pathlib.Path(os.environ.get("LEAN_MODEL_DIR", ROOT / "formal" / "SproutCore"))
TAG = re.compile(r"--\s*spec\s+(.*)")
SECTION = re.compile(r"§(\d+(?:\.\d+[a-z]?)*)")
HEADING = re.compile(r"^#+\s+(\d+(?:\.\d+[a-z]?)*)\.?\s")


def uncovered_tags(lines):
    """Sections a `-- spec §…` rule tag cites that no snapshotted section holds."""
    covered = set()
    for _, start, end in SECTIONS:
        for l in cut(lines, start, end):
            m = HEADING.match(l)
            if m:
                covered.add(m.group(1))
    missing = []
    for path in sorted(MODEL.rglob("*.lean")):
        for n, l in enumerate(path.read_text().splitlines(), 1):
            m = TAG.search(l)
            if not m:
                continue
            for sec in SECTION.findall(m.group(1)):
                # exact: a cut that stops at `### 5.6.1` holds §5.6, not §5.6.1 (a subsection
                # of a covered heading is covered only if the cut reaches its own heading)
                if sec not in covered:
                    missing.append(f"{os.path.relpath(path, ROOT)}:{n}: §{sec}")
    return missing


def main(argv):
    if len(argv) != 2 or argv[1] not in ("--write", "--check"):
        sys.exit(__doc__)
    lines = SPEC.read_text().splitlines(keepends=True)
    failed = False
    missing = uncovered_tags(lines)
    if missing:
        sys.stderr.write("lean-spec-snapshot: rule tags cite spec sections the snapshot does not hold.\n"
                         "Add them to SECTIONS in scripts/lean_spec_snapshot.py:\n  "
                         + "\n  ".join(missing) + "\n")
        return 1
    for name, start, end in SECTIONS:
        text = "".join(cut(lines, start, end))
        path = OUT / name
        if argv[1] == "--write":
            OUT.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
            continue
        old = path.read_text() if path.exists() else ""
        if old != text:
            failed = True
            diff = difflib.unified_diff(old.splitlines(keepends=True), text.splitlines(keepends=True),
                                        f"snapshot/{name}", f"docs/spec-v0.md ({name})", n=1)
            sys.stderr.write("".join(list(diff)[:60]))
    if failed:
        sys.stderr.write(
            "\nlean-spec-snapshot-check: a spec section the Lean model follows has changed.\n"
            "Re-check formal/SproutCore against the changed text (each rule carries a\n"
            "`-- spec §...` tag), then run `just lean-spec-snapshot` and commit the snapshot\n"
            "with the model change, or with a note that the model needs none.\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
