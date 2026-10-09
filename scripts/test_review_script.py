#!/usr/bin/env python3
"""Tests for the workflow script embedded in .claude/skills/sprout-review/SKILL.md.

That script is JavaScript inside a markdown fence. Nothing executes it until a
review is already running, so a typo in it costs a round of reviewer agents
before it surfaces. This extracts the fence and runs it against stub agents, so
`just test-review-script` catches both that and a logic slip in the verify path.

The script reports every finding its passes returned and judges every one of
them. It does not group, rank by agreement, or gate — the caller reads the list
and decides what it means. Six behaviours are worth pinning beyond "it parses":

  1. Every finding reaches the skeptic. A lone low is judged like anything else:
     run 1790751683-30359 had eight findings, all low, and the gate sent the two
     that were already conceded dead while withholding the six that were not.
  2. Nothing is merged. Two reports of one bug stay two findings and both are
     judged, so `found` counts REPORTS and means one fixed thing across runs.
  3. One batched verifier judges every finding, so a verdict it omits must leave
     that finding unconfirmed. A short reply must not be able to pass a finding.
  4. The effort ladder is a table in SKILL.md prose and a table in the script,
     written in different files with nothing between them. These pin the two
     together, and pin that a malformed level falls back rather than running
     zero passes and then recording a review that never happened.
  5. The cap evicts by severity, and announces what it withheld. It is the only
     reason a finding can come back unjudged.
  6. One file reported at two path spellings is reported at one canonical path,
     so the durable findings file does not mix the two spellings.
  7. Cleanup passes are a separate track: they never reach the skeptic, the cap,
     `found` or `confirmed`, and their count per level is pinned like N's.
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
// One entry per cleanup pass, in call order; `null` models a pass that failed.
const CLEANUPS = %s
// `args` is a declared global in the Workflow runtime, holding the tool's `args`
// input or `undefined`. Declared here for the same reason: left out, every
// `args`-reading line is a ReferenceError rather than the fallback it models.
const args = %s
let reviewN = 0
let cleanupN = 0
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
  if (label.startsWith('cleanup:')) {
    const c = CLEANUPS[cleanupN++]
    return c === null ? null : { findings: c || [] }
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
__main().then(r => console.log(JSON.stringify({ ...r, LOGS, verifyCalls, reviewN, cleanupN, EFFORTS })))
"""


def run(reviews, verdicts=(), index_mode="prompt", args=None, cleanups=()):
    """Run the extracted script with `reviews[i]` as pass i's findings.

    `index_mode` controls how the stub verifier numbers its reply: "prompt"
    echoes the indices it was given, "one_based"/"out_of_range"/"duplicate"
    number it wrongly, which is what exercises the join's validation.

    `args` is the Workflow `args` input — the effort level and review target.
    `cleanups[i]` is cleanup pass i's findings, or None for a pass that failed."""
    src = HARNESS % (json.dumps(reviews), json.dumps(list(verdicts)),
                     json.dumps(index_mode), json.dumps(list(cleanups)),
                     json.dumps(args), extract_script())
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
# The one case that still costs N rather than N+1: nothing to judge, no skeptic.
check("an empty review spawns no skeptic", 0, out["verifyCalls"])

# --- nothing is merged, and nothing is lost to a merge ----------------------
# Clustering by proximity-plus-wording was removed rather than retuned. It
# reported 8 distinct for 4 real issues on run 1790751683-30359, split two
# reports at an IDENTICAL line (overlap 0.40 against a 0.5 floor), and cost a
# verify slot on the duplicate. Measured duplicates score 0.36/0.40/0.45/0.46/
# 0.53 across three runs — they straddle the floor, so no cutoff separates them.
# A reader resolves "same bug?" natively; the script no longer guesses.
SAME_A = {"file": "a.ts", "line": 100, "severity": "low",
          "summary": "the retry loop drops the last error silently", "scenario": "s1"}
SAME_B = {"file": "a.ts", "line": 103, "severity": "medium",
          "summary": "the retry loop drops the last error and returns null", "scenario": "s2"}
NEARBY_OTHER = {"file": "a.ts", "line": 101, "severity": "low",
                "summary": "unrelated: the header comment names the wrong flag",
                "scenario": "s3"}

out, err = run([[SAME_A], [SAME_B]] + [[]] * 1)
check("one bug reported twice stays two findings", 2, out["found"])
check("both reports of one bug are judged", 2, out["confirmed"])
check("both reports cost one skeptic, not two", 1, out["verifyCalls"])
# `found` counts REPORTS, so the column means one thing across every run. The
# judged grouping is the caller's, and belongs in the findings file.
check("no finding is dropped to a merge", 2, len(field(out, "raw")))

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

# THE REGRESSION. A lone low used to fall below the verify gate, and on run
# 1790751683-30359 every one of eight findings was a lone low: the skeptic was
# handed the only two that shared a line — both already conceded dead — and
# never saw the two that contradicted a claim in the PR body. Severity and
# corroboration were both degenerate, so the ranking they fed was arbitrary.
out, err = run([[LONE_LOW]] + [[]] * 2)
check("a lone low IS verified", 1, out["confirmed"])
check("a lone low is not left unverified", 0, len(field(out, "unverified")))
check("a lone low spawns the skeptic", 1, out["verifyCalls"])
# The denominator must not shrink when the cap tightens, or the ledger's `found`
# column silently starts meaning something else.
check("a judged finding still counts as found", 1, out["found"])

out, err = run([[LONE_LOW], [LONE_LOW]] + [[]] * 1)
check("two reports of one low are both verified", 2, out["confirmed"])

# --- two reports at one line stay two, and both are judged ------------------
# Run 1790417756-48747 lost a real regression at ast_to_ir.sprout:7512 this way:
# two lows at one line, phrased differently, scored 1 vote each, cleared no gate
# and were never checked. Location-based corroboration was the first fix; judging
# everything makes the question moot.
SAME_LINE_A = {"file": "stdlib/compiler/ast_to_ir.sprout", "line": 7512,
               "severity": "low",
               "summary": "poison literal embeds a duplicate runtime error prefix",
               "scenario": "s"}
SAME_LINE_B = {"file": "stdlib/compiler/ast_to_ir.sprout", "line": 7512,
               "severity": "low",
               "summary": "unresolved dictionary thunk message printed twice",
               "scenario": "s"}
out, err = run([[SAME_LINE_A], [SAME_LINE_B], []])
check("unlike wording still reports separately", 2, out["found"])
check("both same-line lows are verified", 2, out["confirmed"])
check("a same-line low pair spawns the skeptic", 1, out["verifyCalls"])
check("neither is withheld from the skeptic", 0, len(field(out, "unverified")))

# --- one file, two path spellings, one canonical path -----------------------
# An agent returns an absolute or a repo-relative path depending on how it
# navigated (run 1790183127-58366). Nothing is merged on it any more, but the
# findings file is durable and must not mix the two spellings, so the suffix rule
# stays: each path collapses to the shortest path in the run it ends with on a
# segment boundary, which needs no repo root.
ABS_SPELLING = {"file": "/Users/x/repo/stdlib/prelude.sprout", "line": 1812,
                "severity": "medium",
                "summary": "builder append copies the chunk pointer array per call",
                "scenario": "s"}
REL_SPELLING = dict(ABS_SPELLING, file="stdlib/prelude.sprout")
out, err = run([[ABS_SPELLING], [REL_SPELLING], []])
check("two spellings stay two findings", 2, out["found"])
check("every spelling is reported at the canonical path",
      ["stdlib/prelude.sprout", "stdlib/prelude.sprout"],
      sorted(f["file"] for f in field(out, "findings")))

# The suffix rule must not merge two genuinely different files. A shared
# basename is not a shared path, so these stay apart.
TWIN_A = {"file": "src/a/mod.rs", "line": 10, "severity": "medium",
          "summary": "identical wording in two different files here", "scenario": "s"}
TWIN_B = dict(TWIN_A, file="src/b/mod.rs")
out, err = run([[TWIN_A], [TWIN_B], []])
check("a shared basename does not rewrite a different file", 2,
      len({f["file"] for f in field(out, "findings")}))

# --- every finding as its pass reported it comes back -----------------------
# `raw` is what the ledger stores beside the report. It was added because a
# post-dedup report cannot say whether a clustering constant was set right; it
# stays because the report is judged and the ledger should hold the unjudged
# claims too, in the reviewer's own words.
out, err = run([[SAME_LINE_A], [SAME_LINE_B], []])
check("every finding as reported is returned as raw", 2, len(field(out, "raw")))
check("raw keeps the reviewer's own summary",
      sorted(["poison literal embeds a duplicate runtime error prefix",
              "unresolved dictionary thunk message printed twice"]),
      sorted(f["summary"] for f in field(out, "raw")))

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

# --- the cap evicts by severity, and is the only way to go unjudged ---------
# With no gate, VERIFY_CAP is the sole reason a finding comes back unverified.
# Eviction is severity-major: sorting it by agreement once put ten corroborated
# lows ahead of a lone high and evicted the high. VERIFY_CAP is 10, so eleven
# findings make it bind.
TEN_LOWS = [{"file": "c%d.ts" % i, "line": 1, "severity": "low",
             "summary": "low finding number %d here" % i,
             "scenario": "s"} for i in range(10)]
out, err = run([TEN_LOWS, [LONE_HIGH], []])
check("eleven findings are all found", 11, out["found"])
check("the lone high is verified, not evicted", "h.ts",
      next((f["file"] for f in field(out, "findings") if f["file"] == "h.ts"), None))
check("the cap evicts a low instead", "low",
      field(out, "unverified")[0]["severity"] if field(out, "unverified") else None)
check("exactly one finding is past the cap", 1, len(field(out, "unverified")))
check("the evicted finding says why", True,
      "VERIFY_CAP" in field(out, "unverified")[0].get("unverifiedBecause", "")
      if field(out, "unverified") else None)
# Per the Workflow guidance on silent caps: a bounded pass must not read as full
# coverage. This is the one log line a reader needs.
check("the withheld count is logged", 1,
      sum(1 for m in field(out, "LOGS") if "UNVERIFIED" in m))

# --- the cost model: N reviewers and at most one verifier -------------------
# The agent count is the reason this shape was chosen, so it is pinned. Three
# distinct findings used to mean three verify agents.
out, err = run([[LONE_HIGH], [LONE_MEDIUM], [SAME_A]])
check("one verify agent regardless of finding count", 1, out["verifyCalls"])
check("N reviewer agents", 3, out["reviewN"])
check("three findings are all judged", 3, out["confirmed"])

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
          [e["effort"] for e in field(out, "EFFORTS") if e["label"].startswith("review:")])

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

# --- the cleanup track ------------------------------------------------------
# /simplify's four angles, report-only. They share the diff and the fan-out with
# the bug passes and nothing else: no skeptic, no cap, no `found`/`confirmed`.
# Those columns mean "bugs" in every ledger row ever written, and the skeptic is
# told to refute anything with no failure, which every cleanup lacks.
CLEANUP_LADDER = {"low": 1, "medium": 1, "high": 1, "xhigh": 4, "max": 4}
ANGLES = ["### Reuse", "### Simplification", "### Efficiency", "### Altitude"]
for level, c in CLEANUP_LADDER.items():
    out, err = run([[]] * LADDER[level], args={"effort": level, "target": ""})
    check("%s runs %d cleanup pass(es)" % (level, c), c, out["cleanupN"])
    check("%s reports its cleanup pass count" % level, c, out.get("cleanupPasses"))
    cl = [e for e in field(out, "EFFORTS") if e["label"].startswith("cleanup:")]
    check("%s sets the cleanup effort" % level, [level] * c, [e["effort"] for e in cl])
    # Every angle is covered exactly once, however the passes split them.
    check("%s covers each angle once" % level, [1] * 4,
          [sum(1 for e in cl if a in e["prompt"]) for a in ANGLES])

DUP_HELPER = {"file": "stdlib/net.sprout", "line": 40, "severity": "low",
              "category": "reuse", "summary": "re-implements list_take",
              "scenario": "two copies to keep in step; call list_take"}
out, err = run([[]] * 3, cleanups=[[DUP_HELPER]], args={"effort": "high", "target": ""})
check("a cleanup is returned", 1, len(field(out, "cleanups")))
check("a cleanup never reaches the skeptic", 0, out["verifyCalls"])
check("a cleanup is not counted as found", 0, out["found"])
check("a cleanup is not in raw", 0, len(field(out, "raw")))
cl = [e for e in field(out, "EFFORTS") if e["label"].startswith("cleanup:")]
check("the cleanup pass is told not to edit", True,
      bool(cl) and "do not edit" in cl[0]["prompt"].lower())
check("the cleanup pass is not told to hunt bugs", False,
      bool(cl) and "for real bugs" in cl[0]["prompt"])
check("the cleanup pass gets the branch diff", True,
      bool(cl) and "@{upstream}" in cl[0]["prompt"])

# A bug and a cleanup together: the bug is judged, the cleanup is not, and the
# cap and counts see only the bug.
out, err = run([[LONE_HIGH], [], []], cleanups=[[DUP_HELPER]])
check("with a cleanup beside it, the bug is still confirmed", 1, out["confirmed"])
check("the skeptic is handed the bug alone", 1,
      sum(1 for e in field(out, "EFFORTS") if e["label"] == "verify:1"))

# With one angle per pass, the angle IS the category: a pass that mislabels its
# finding must not file an efficiency cleanup under reuse.
MISLABELLED = dict(DUP_HELPER, category="reuse", summary="list rebuilt per call")
out, err = run([[]] * 5, cleanups=[[], [], [MISLABELLED], []],
               args={"effort": "xhigh", "target": ""})
check("one-angle passes stamp their own category", ["efficiency"],
      [f.get("category") for f in field(out, "cleanups")])

# A failed cleanup pass must not cost the bug review, and must not read as "the
# code is clean" either.
out, err = run([[LONE_HIGH], [], []], cleanups=[None])
check("a failed cleanup pass keeps the bug result", 1, out["confirmed"])
check("a failed cleanup pass is logged", 1,
      sum(1 for m in field(out, "LOGS") if "cleanup pass" in m and "failed" in m))

# Cleanups come back most costly first, and on the run's canonical paths.
SMALL = dict(DUP_HELPER, severity="low", summary="small")
BIG = dict(DUP_HELPER, severity="high", summary="big",
           file="/Users/x/repo/stdlib/prelude.sprout")
out, err = run([[REL_SPELLING], [], []], cleanups=[[SMALL, BIG]])
check("cleanups are ordered by severity", ["big", "small"],
      [f["summary"] for f in field(out, "cleanups")])
check("cleanup paths are canonicalised with the bugs'", "stdlib/prelude.sprout",
      field(out, "cleanups")[0]["file"] if field(out, "cleanups") else None)

if failures:
    print("==> sprout-review script tests FAILED (%d)" % len(failures), file=sys.stderr)
    sys.exit(1)
print("==> sprout-review script tests passed")
