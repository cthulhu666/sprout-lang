#!/usr/bin/env python3
"""Tests for the workflow script embedded in .claude/skills/sprout-review/SKILL.md.

That script is JavaScript inside a markdown fence. Nothing executes it until a
review is already running, so a typo in it costs a round of reviewer agents
before it surfaces, and a logic slip in the dedup or the verify cap costs a lost
finding and never surfaces at all. This extracts the fence and runs it against
stub agents, so both are caught by `just test-review-script`.

Three behaviours are worth pinning beyond "it parses":

  1. Dedup must merge one bug reported at two nearby lines, and must NOT merge two
     different bugs that happen to sit nearby. Line-exact keying failed the first;
     proximity alone fails the second.
  2. The verify cap must keep a single-vote MEDIUM finding. On the run it was
     calibrated against, the most valuable finding was exactly that, and a
     votes-only cap would have dropped it.
  3. One batched verifier judges every finding, so a verdict it omits must leave
     that finding unconfirmed. A short reply must not be able to pass a finding.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL = os.path.join(ROOT, ".claude", "skills", "sprout-review", "SKILL.md")

failures = []


def field(out, name):
    """A result key, or a recorded failure. Reading it directly would traceback on
    a script that omits the key, killing the remaining checks — and the checks
    after it are the ones that say what else broke."""
    if name not in out:
        print("  FAIL the result has no `%s` key" % name, file=sys.stderr)
        failures.append(name)
        return []
    return out[name]


def check(what, want, got):
    if want == got:
        print("  ok   %s" % what)
    else:
        print("  FAIL %s: wanted [%s], got [%s]" % (what, want, got), file=sys.stderr)
        failures.append(what)


def extract_script():
    md = open(SKILL).read()
    blocks = [b.split("```", 1)[0] for b in md.split("```js")[1:]]
    if len(blocks) != 1:
        print("expected exactly one ```js block in SKILL.md, found %d" % len(blocks),
              file=sys.stderr)
        sys.exit(1)
    return blocks[0].replace("export const meta", "const meta")


# The Workflow runtime wraps a script body in an async function, which is what
# makes its top-level `await` and `return` legal. Wrap it the same way rather
# than treating it as an ES module, or the check fails on the runtime's own
# calling convention instead of on the script.
HARNESS = """
const REVIEWS = %s
const VERDICTS = %s
let reviewN = 0
let verifyCalls = 0
const agent = async (prompt, opts) => {
  const label = (opts && opts.label) || ''
  if (label.startsWith('verify:')) {
    // One skeptic gets the whole list, so the stub answers by reading each
    // entry's `[i] file:line` header back out of the prompt it was handed.
    // That also pins the prompt format the real verifier's indices rely on.
    verifyCalls += 1
    const heads = [...prompt.matchAll(/^\\[(\\d+)\\] (\\S+):(\\d+) \\(/gm)]
    const verdicts = []
    for (const m of heads) {
      const hit = VERDICTS.find(v => v.at === m[2] + ':' + m[3])
      if (hit && hit.omit) continue   // answer nothing for this one
      verdicts.push({ index: Number(m[1]), refuted: !!(hit && hit.refuted), reason: 'stub' })
    }
    return { verdicts }
  }
  return { findings: REVIEWS[reviewN++] || [] }
}
const parallel = async ts => Promise.all(ts.map(t => t()))
const phase = () => {}
const LOGS = []
const log = m => LOGS.push(m)
async function __main() {
%s
}
__main().then(r => console.log(JSON.stringify({ ...r, LOGS, verifyCalls, reviewN })))
"""


def run(reviews, verdicts=()):
    """Run the extracted script with `reviews[i]` as pass i's findings."""
    src = HARNESS % (json.dumps(reviews), json.dumps(list(verdicts)), extract_script())
    d = tempfile.mkdtemp(prefix="sprout_review_script_")
    try:
        path = os.path.join(d, "script.mjs")
        open(path, "w").write(src)
        syn = subprocess.run([NODE, "--check", path], capture_output=True, text=True)
        if syn.returncode != 0:
            return None, syn.stderr
        r = subprocess.run([NODE, path], capture_output=True, text=True)
        if r.returncode != 0:
            return None, r.stderr
        return json.loads(r.stdout.strip().split("\n")[-1]), ""
    finally:
        shutil.rmtree(d, ignore_errors=True)


NODE = shutil.which("node")
if not NODE:
    # Not skipped: a gate that goes quiet when a tool is missing reports the same
    # green as one that ran, which is how a gate stops being a gate. node is
    # pinned in mise.toml for exactly this.
    print("node not found — run via `mise exec -- just test-review-script`", file=sys.stderr)
    sys.exit(1)

print("==> sprout-review script")

# --- it parses and runs at all ----------------------------------------------
out, err = run([[]] * 3)
if out is None:
    print("  FAIL the script does not run:\n%s" % err, file=sys.stderr)
    sys.exit(1)
print("  ok   the script parses and runs")
check("an empty review returns zero found", 0, out["found"])
check("an empty review returns zero confirmed", 0, out["confirmed"])

# --- dedup: merge the same bug, keep different ones apart --------------------
SAME_A = {"file": "a.ts", "line": 100, "severity": "low",
          "summary": "the retry loop drops the last error silently", "scenario": "s1"}
SAME_B = {"file": "a.ts", "line": 103, "severity": "medium",
          "summary": "the retry loop drops the last error and returns null", "scenario": "s2"}
NEARBY_OTHER = {"file": "a.ts", "line": 101, "severity": "low",
                "summary": "unrelated: the header comment names the wrong flag",
                "scenario": "s3"}

out, err = run([[SAME_A], [SAME_B]] + [[]] * 1)
check("one bug at two nearby lines is one finding", 1, out["found"])
check("merging sums the votes", 2, out["findings"][0]["votes"])
# The reader acts on the summary, so the merged entry must carry the harsher
# reading, not whichever line sorted first.
check("the merged entry keeps the severe wording", "medium", out["findings"][0]["severity"])
check("the merged entry keeps the severe line", 103, out["findings"][0]["line"])

out, err = run([[SAME_A], [NEARBY_OTHER]] + [[]] * 1)
check("two different bugs one line apart stay two", 2, out["found"])

# --- the verify cap ----------------------------------------------------------
LONE_MEDIUM = {"file": "m.ts", "line": 5, "severity": "medium",
               "summary": "the new marker makes a local record look foreign", "scenario": "s"}
LONE_LOW = {"file": "l.ts", "line": 9, "severity": "low",
            "summary": "a stale doc comment names a flag that was renamed", "scenario": "s"}
LONE_HIGH = {"file": "h.ts", "line": 2, "severity": "high",
             "summary": "the guard is inverted so every request is admitted", "scenario": "s"}

out, err = run([[LONE_MEDIUM]] + [[]] * 2)
check("a single-vote medium IS verified", 1, out["confirmed"])
check("a single-vote medium is not left unverified", 0, len(field(out, "unverified")))

out, err = run([[LONE_HIGH]] + [[]] * 2)
check("a single-vote high IS verified", 1, out["confirmed"])

out, err = run([[LONE_LOW]] + [[]] * 2)
check("a single-vote low is NOT verified", 0, out["confirmed"])
check("a single-vote low is reported unverified", 1, len(field(out, "unverified")))
# The denominator must not shrink when the cap tightens, or the ledger's `found`
# column silently starts meaning something else.
check("an unverified finding still counts as found", 1, out["found"])

out, err = run([[LONE_LOW], [LONE_LOW]] + [[]] * 1)
check("a corroborated low IS verified", 1, out["confirmed"])

# --- refuted findings are separated, not dropped ----------------------------
out, err = run([[LONE_HIGH]] + [[]] * 2, verdicts=[{"at": "h.ts:2", "refuted": True}])
check("a refuted finding is not confirmed", 0, out["confirmed"])
check("a refuted finding is still reported", 1, len(field(out, "refuted")))
check("a refuted finding still counts as found", 1, out["found"])

# --- a finding the skeptic never answered is NOT confirmed -------------------
# One batched verifier can return a short list — truncation, a dropped index.
# Treating a missing verdict as a pass would let that inflate `confirmed`.
out, err = run([[LONE_HIGH]] + [[]] * 2, verdicts=[{"at": "h.ts:2", "omit": True}])
check("an unanswered finding is not confirmed", 0, out["confirmed"])
check("an unanswered finding is reported as refuted", 1, len(field(out, "refuted")))

# --- the cost model: N reviewers and exactly one verifier --------------------
# The agent count is the reason this shape was chosen, so it is pinned. Three
# distinct findings used to mean three verify agents.
out, err = run([[LONE_HIGH], [LONE_MEDIUM], [SAME_A]])
check("one verify agent regardless of finding count", 1, out["verifyCalls"])
check("N reviewer agents", 3, out["reviewN"])

# --- the cap is announced, per the Workflow guidance on silent caps ----------
out, err = run([[LONE_LOW]] + [[]] * 2)
check("the skipped count is logged", 1,
      sum(1 for m in field(out, "LOGS") if "UNVERIFIED" in m))

if failures:
    print("==> sprout-review script tests FAILED (%d)" % len(failures), file=sys.stderr)
    sys.exit(1)
print("==> sprout-review script tests passed")
