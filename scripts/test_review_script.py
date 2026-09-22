#!/usr/bin/env python3
"""Tests for the workflow script embedded in .claude/skills/sprout-review/SKILL.md.

That script is JavaScript inside a markdown fence. Nothing executes it until a
review is already running, so a typo in it costs a round of reviewer agents
before it surfaces, and a logic slip in the dedup or the verify cap costs a lost
finding and never surfaces at all. This extracts the fence and runs it against
stub agents, so both are caught by `just test-review-script`.

Four behaviours are worth pinning beyond "it parses":

  1. Dedup must merge one bug reported at two nearby lines, and must NOT merge two
     different bugs that happen to sit nearby. Line-exact keying failed the first;
     proximity alone fails the second.
  2. The verify cap must keep a single-vote MEDIUM finding. On the run it was
     calibrated against, the most valuable finding was exactly that, and a
     votes-only cap would have dropped it.
  3. One batched verifier judges every finding, so a verdict it omits must leave
     that finding unconfirmed. A short reply must not be able to pass a finding.
  4. The effort ladder is a table in SKILL.md prose and a table in the script,
     written in different files with nothing between them. These pin the two
     together, and pin that a malformed level falls back rather than running
     zero passes and then recording a review that never happened.
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
const INDEX_MODE = %s
// `args` is a declared global in the Workflow runtime, holding the tool's `args`
// input or `undefined`. Declared here for the same reason: left out, every
// `args`-reading line is a ReferenceError rather than the fallback it models.
const args = %s
let reviewN = 0
let verifyCalls = 0
const EFFORTS = []
const agent = async (prompt, opts) => {
  const label = (opts && opts.label) || ''
  EFFORTS.push({ label, effort: (opts && opts.effort) || null, prompt })
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
      // INDEX_MODE lets the stub number its reply DIFFERENTLY from the prompt.
      // Echoing the parsed index can never disagree with the code under test,
      // so the whole mis-numbering class was untestable while it was the only
      // mode — which is how the join shipped unvalidated.
      let idx = Number(m[1])
      if (INDEX_MODE === 'one_based') idx += 1
      else if (INDEX_MODE === 'out_of_range') idx += 100
      else if (INDEX_MODE === 'duplicate') idx = 0
      verdicts.push({ index: idx, refuted: !!(hit && hit.refuted), reason: 'stub' })
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
__main().then(r => console.log(JSON.stringify({ ...r, LOGS, verifyCalls, reviewN, EFFORTS })))
"""


def run(reviews, verdicts=(), index_mode="prompt", args=None):
    """Run the extracted script with `reviews[i]` as pass i's findings.

    `index_mode` controls how the stub verifier numbers its reply: "prompt"
    echoes the indices it was given, "one_based"/"out_of_range"/"duplicate"
    number it wrongly, which is what exercises the join's validation.

    `args` is the Workflow `args` input — the effort level and review target."""
    src = HARNESS % (json.dumps(reviews), json.dumps(list(verdicts)),
                     json.dumps(index_mode), json.dumps(args), extract_script())
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

# --- a finding the skeptic never answered is UNVERIFIED, not refuted ---------
# One batched verifier can return a short list — truncation, a dropped index.
# Treating a missing verdict as a pass would inflate `confirmed`; filing it as
# refuted is just as wrong the other way, because step 5 tells the reader to
# skim the refuted list, and an unjudged high-severity bug would be in it.
out, err = run([[LONE_HIGH]] + [[]] * 2, verdicts=[{"at": "h.ts:2", "omit": True}])
check("an unanswered finding is not confirmed", 0, out["confirmed"])
check("an unanswered finding is NOT called refuted", 0, len(field(out, "refuted")))
check("an unanswered finding is reported unverified", 1, len(field(out, "unverified")))
check("an unanswered finding says why", "no verdict returned",
      field(out, "unverified")[0].get("unverifiedBecause") if field(out, "unverified") else None)

# --- the verdict-to-finding join --------------------------------------------
# The riskiest thing batching introduced: verdicts arrive keyed by a number the
# model chose. A stub that echoes the prompt's own indices can never disagree
# with the code, so these use `index_mode` to number the reply wrongly.
#
# Numbered 1-based, the old join gave every finding its PREDECESSOR's verdict:
# the refuted one came back confirmed. Nothing may be confirmed on a reply
# whose numbering cannot be trusted.
out, err = run([[LONE_HIGH], [LONE_MEDIUM]] + [[]] * 1,
               verdicts=[{"at": "m.ts:5", "refuted": True}], index_mode="one_based")
check("a 1-based reply confirms nothing", 0, out["confirmed"])
check("a 1-based reply is reported unverified", 2, len(field(out, "unverified")))
check("a discarded batch is logged", 1,
      sum(1 for m in field(out, "LOGS") if "VERDICTS DISCARDED" in m))

out, err = run([[LONE_HIGH], [LONE_MEDIUM]] + [[]] * 1, index_mode="out_of_range")
check("an out-of-range index confirms nothing", 0, out["confirmed"])

out, err = run([[LONE_HIGH], [LONE_MEDIUM]] + [[]] * 1, index_mode="duplicate")
check("a duplicated index confirms nothing", 0, out["confirmed"])

# A correctly-numbered reply must route each verdict to ITS OWN finding. This is
# what kills the mutant that applies verdict[0] to everything: refute only the
# medium, and the high must survive while the medium does not.
out, err = run([[LONE_HIGH], [LONE_MEDIUM]] + [[]] * 1,
               verdicts=[{"at": "m.ts:5", "refuted": True}])
check("verdicts land on their own finding: one confirmed", 1, out["confirmed"])
check("verdicts land on their own finding: the right one",
      "h.ts", field(out, "findings")[0]["file"] if field(out, "findings") else None)
check("verdicts land on their own finding: the other refuted",
      "m.ts", field(out, "refuted")[0]["file"] if field(out, "refuted") else None)

# --- the cap evicts by severity, not by votes -------------------------------
# Votes-first sorting put corroborated lows ahead of a lone high and evicted the
# high, reinstating through the cap the votes-only gate the severity test
# rejects. VERIFY_CAP is 10, so eleven gate-clearing findings make it bind.
CORROBORATED_LOWS = [{"file": "c%d.ts" % i, "line": 1, "severity": "low",
                      "summary": "duplicated low finding number %d here" % i,
                      "scenario": "s"} for i in range(10)]
out, err = run([CORROBORATED_LOWS, CORROBORATED_LOWS, [LONE_HIGH]])
check("eleven findings clear the gate", 11, out["found"])
check("the lone high is verified, not evicted", "h.ts",
      next((f["file"] for f in field(out, "findings") if f["file"] == "h.ts"), None))
check("the cap evicts a low instead", "low",
      field(out, "unverified")[0]["severity"] if field(out, "unverified") else None)

# --- the cost model: N reviewers and at most one verifier -------------------
# The agent count is the reason this shape was chosen, so it is pinned. Three
# distinct findings used to mean three verify agents.
out, err = run([[LONE_HIGH], [LONE_MEDIUM], [SAME_A]])
check("one verify agent regardless of finding count", 1, out["verifyCalls"])
check("N reviewer agents", 3, out["reviewN"])

# No finding clears the gate, so the skeptic is skipped entirely and the run
# costs N, not N+1. SKILL.md and README.md both claim this.
out, err = run([[LONE_LOW]] + [[]] * 2)
check("no verify agent when nothing clears the gate", 0, out["verifyCalls"])

# --- the effort ladder -------------------------------------------------------
# The level is the user's only dial, and it reaches the script as data. These
# pin the table in SKILL.md §Arguments against the code that implements it —
# the two are written in different files and nothing else compares them.
LADDER = {"low": 1, "medium": 2, "high": 3, "xhigh": 5, "max": 8}
for level, passes in LADDER.items():
    out, err = run([[]] * passes, args={"effort": level, "target": ""})
    check("%s runs %d reviewer(s)" % (level, passes), passes, out["reviewN"])
    check("%s reports the level it ran at" % level, level, out["effort"])
    check("%s reports its pass count" % level, passes, out["passes"])
    check("%s sets the reviewers' effort" % level, [level] * passes,
          [e["effort"] for e in field(out, "EFFORTS")])

# A malformed `args` must not run zero passes and then close a ledger row saying
# a review happened. The caller should have rejected the level; this is what
# happens when it did not.
for bad in (None, {}, {"effort": "higher"}, {"effort": None}, {"effort": 3}):
    out, err = run([[]] * 3, args=bad)
    check("%s falls back to high" % json.dumps(bad), "high", out["effort"])
    check("%s still runs 3 passes" % json.dumps(bad), 3, out["reviewN"])
out, err = run([[]] * 3, args={"effort": "higher"})
check("a bad level is logged, not absorbed", 1,
      sum(1 for m in field(out, "LOGS") if "defaulting to high" in m))

# The skeptic never drops below medium. It is told to default to refuted=true
# when unsure, so a cheaper skeptic is cheaper at KILLING real findings — the
# one dial where saving tokens costs correctness rather than coverage.
out, err = run([[LONE_HIGH]], args={"effort": "low", "target": ""})
check("low reviewers still get a medium skeptic", "medium",
      next((e["effort"] for e in field(out, "EFFORTS")
            if e["label"].startswith("verify:")), None))
out, err = run([[LONE_HIGH]] * 8, args={"effort": "max", "target": ""})
check("above the floor the skeptic matches the level", "max",
      next((e["effort"] for e in field(out, "EFFORTS")
            if e["label"].startswith("verify:")), None))

# The target reaches the reviewers. The prompt has described this branch since
# it was ported, while nothing could pass one — so what is checked is that the
# target is in the prompt the reviewer is actually handed, not just in the
# result. A target that only reaches the return value reviews the wrong thing.
out, err = run([[]], args={"effort": "low", "target": "  1234  "})
check("the target is trimmed", "1234", out["target"])
check("the target is in the reviewer's prompt", 1,
      sum(1 for e in field(out, "EFFORTS")
          if e["label"].startswith("review:") and "`1234`" in e["prompt"]))
# ...and with no target, the reviewer is told to diff the branch instead. The
# two prompts are exclusive: neither may leak the other's instruction.
out, err = run([[]] * 3, args={"effort": "high"})
check("no target reports null rather than empty", None, out["target"])
check("without a target the reviewer diffs the branch", 3,
      sum(1 for e in field(out, "EFFORTS")
          if e["label"].startswith("review:") and "@{upstream}" in e["prompt"]))
out, err = run([[]], args={"effort": "low", "target": "1234"})
check("with a target the branch diff is not mentioned", 0,
      sum(1 for e in field(out, "EFFORTS")
          if e["label"].startswith("review:") and "@{upstream}" in e["prompt"]))

# --- the cap is announced, per the Workflow guidance on silent caps ----------
out, err = run([[LONE_LOW]] + [[]] * 2)
check("the skipped count is logged", 1,
      sum(1 for m in field(out, "LOGS") if "UNVERIFIED" in m))

if failures:
    print("==> sprout-review script tests FAILED (%d)" % len(failures), file=sys.stderr)
    sys.exit(1)
print("==> sprout-review script tests passed")
