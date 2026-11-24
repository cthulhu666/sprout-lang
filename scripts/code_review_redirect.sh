#!/usr/bin/env bash
# Claude Code UserPromptExpansion hook: block a typed `/code-review` and point to
# `/pr-tracker:sprout-review`, which runs it and records the run in the review ledger.
# The wrapper calls code-review through the Skill tool, which does not fire this event.
# Never blocks on empty or unparsable input.
set -euo pipefail

INPUT=$(cat)

printf '%s' "$INPUT" | python3 -c '
import sys, json
try:
    d = json.load(sys.stdin)
    name = d.get("command_name", "")
    args = (d.get("command_args") or "").strip()
except Exception:
    sys.exit(0)
if name != "code-review":
    sys.exit(0)
wrapper = "/pr-tracker:sprout-review" + (" " + args if args else "")
reason = ("In this repo, run " + wrapper + " instead: it runs /code-review and "
          "records the run in the review ledger (/prs).")
print(json.dumps({"decision": "block", "reason": reason}))
' 2>/dev/null || true
