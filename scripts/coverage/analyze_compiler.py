"""Compiler coverage from the instrumented compiler run (sancov blocks + PGO entry counts),
optionally unioned (function-level) with compiler functions executed inside test binaries."""
import sys, os, glob, json, collections, subprocess, re
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from covlib import *

C = COV
B = f"{C}/build"
R = f"{C}/compiler_run"
OUT = f"{C}/results"; os.makedirs(OUT, exist_ok=True)
MAIN_MOD = "stdlib.compiler.compile_driver"

funcs, globs = parse_ll(f"{B}/cd.ll")
owner = lambda_owner(funcs, globs)

# nm of the instrumented compiler
nm_path = f"{B}/compile_driver_inst.nm"
with open(nm_path, "w") as fh:
    subprocess.run(["nm", "-n", f"{B}/compile_driver_inst"], stdout=fh, check=True)

npcs = os.path.getsize(f"{B}/compiler.pcs") // 16
acc = bytearray(npcs)
files = glob.glob(f"{R}/flags/cc.*.flags")
for p in files:
    with open(p, "rb") as fh:
        b = fh.read()
    assert len(b) == npcs, p
    acc = or_into(acc, b)
cov = decode(f"{B}/compiler.pcs", nm_path, acc)
missing = [f for f in funcs if f not in cov]
print(f"processes with flags: {len(files)}; IR functions {len(funcs)}; sancov functions {len(cov)}; IR fns missing from pc table: {len(missing)} {missing[:5]}")

# PGO entry counts
prof = f"{R}/cc.profdata"
subprocess.run(["llvm-profdata", "merge", "-o", prof] + glob.glob(f"{R}/prof/*.profraw"), check=True)
show = subprocess.run(["llvm-profdata", "show", "--all-functions", "--counts", prof], capture_output=True, text=True, check=True).stdout
pgo = {}
cur = None
for line in show.splitlines():
    m = re.match(r"^  (\S.*):$", line)
    if m:
        cur = m.group(1); continue
    # -pgo-instrument-entry => "entry_first = 1": counter 0 is the entry count
    m = re.match(r"^    Block counts: \[(.*)\]", line)
    if m and cur:
        cs = [int(x) for x in m.group(1).split(",") if x.strip()]
        pgo[cur] = sum(cs)  # any counter > 0 <=> function ran (MST flow conservation)

src = all_source_fns(compiler=True)
ir_names = set()
def canon(n):
    return MAIN_MOD + n[4:] if n.startswith("main.") else n
for f in funcs:
    ir_names.add(canon(f))
known = {"src": set(src), "ir": ir_names}

# per IR function record
rows = []
agree = disagree = 0
dis = []
for f, d in funcs.items():
    c = cov.get(f, [0, 0, False])
    mod, kind = module_of(f, MAIN_MOD)
    if kind in ("lambda", "eta"):
        o = owner.get(f)
        if o:
            mod = module_of(o, MAIN_MOD)[0]
    cf = canon(f)
    base = base_name(cf, known) if kind == "fn" else cf
    pc = pgo.get(f)
    if pc is not None:
        if (pc > 0) == c[2]: agree += 1
        else:
            disagree += 1; dis.append((f, pc, c))
    rows.append(dict(ir=f, base=base, module=mod, kind=kind, instrs=d["instrs"], blocks=c[0], bcov=c[1], entered=c[2], pgo=pc))
print(f"PGO vs sancov entry agreement: {agree} agree, {disagree} disagree; PGO records {len(pgo)}")
for x in dis[:10]: print("  disagree", x)

json.dump(rows, open(f"{OUT}/compiler_ir_rows.json", "w"))

is_comp = lambda m: m is not None and m.startswith("stdlib.compiler.")

# function level over source-level functions (variants collapsed), compiler modules only
fn = {}
for r in rows:
    if r["kind"] not in ("fn", "instance") or not is_comp(r["module"]):
        continue
    e = fn.setdefault(r["base"], dict(module=r["module"], kind=r["kind"], instrs=0, entered=False, variants=0))
    e["instrs"] += r["instrs"]; e["entered"] |= r["entered"]; e["variants"] += 1

# in-process unit tests (tests/stdlib/compiler etc): function names executed in any test binary
union_path = f"{C}/results/stdlib_executed_names.json"
test_exec = set(json.load(open(union_path))) if os.path.exists(union_path) else None
def in_tests(b):
    return test_exec is not None and (b in test_exec or any(b + s in test_exec for s in SUFFIXES) or any(b + s1 + s2 in test_exec for s1 in SUFFIXES for s2 in SUFFIXES))

not_emitted = {q: v for q, v in src.items() if q not in fn and q not in ir_names}
json.dump(dict(fn=fn, not_emitted=not_emitted), open(f"{OUT}/compiler_fn.json", "w"))

per = collections.defaultdict(lambda: collections.Counter())
for b, e in fn.items():
    p = per[e["module"]]
    p["fns"] += 1; p["cov"] += e["entered"]; p["instrs"] += e["instrs"]; p["cov_instrs"] += e["instrs"] if e["entered"] else 0
    if test_exec is not None:
        p["cov_u"] += e["entered"] or in_tests(b)
for q, v in not_emitted.items():
    per[v[0]]["not_emitted"] += 1
    if test_exec is not None and in_tests(q):
        per[v[0]]["ne_test_exec"] += 1
for r in rows:
    if not is_comp(r["module"]):
        continue
    p = per[r["module"]]
    p["blocks"] += r["blocks"]; p["bcov"] += r["bcov"]
    if r["kind"] == "lambda":
        p["lam"] += 1; p["lam_cov"] += r["entered"]

T = collections.Counter()
for p in per.values(): T.update(p)

def pct(a, b): return f"{100*a/b:5.1f}%" if b else "  n/a"
lines = []
lines.append(f"COMPILER (instrumented compiler process, all `just test` invocations)")
lines.append(f"  function-level (emitted named fns+instance methods, variants collapsed): {T['cov']}/{T['fns']} = {pct(T['cov'],T['fns'])}")
lines.append(f"  IR-instruction-weighted: {T['cov_instrs']}/{T['instrs']} = {pct(T['cov_instrs'],T['instrs'])}")
lines.append(f"  block-level (all IR blocks incl. lambdas, eta thunks): {T['bcov']}/{T['blocks']} = {pct(T['bcov'],T['blocks'])}")
lines.append(f"  lambdas entered: {T['lam_cov']}/{T['lam']}")
lines.append(f"  source fns never emitted into the compiler binary (dead from main): {T['not_emitted']} (of which executed in test binaries: {T['ne_test_exec']})")
if test_exec is not None:
    lines.append(f"  function-level UNION with in-process test binaries (emitted fns): {T['cov_u']}/{T['fns']} = {pct(T['cov_u'],T['fns'])}")
    sl = T['fns'] + T['not_emitted']
    lines.append(f"  source-level incl. never-emitted as uncovered, union: {T['cov_u']+T['ne_test_exec']}/{sl} = {pct(T['cov_u']+T['ne_test_exec'], sl)}")
lines.append("")
lines.append(f"  {'module':40s} {'fns':>5s} {'fn%':>6s} {'union%':>6s} {'blk%':>6s} {'blocks':>7s} {'instr%':>6s} {'noemit':>6s} {'src%':>6s}")
for m, p in sorted(per.items(), key=lambda kv: kv[1]['cov']/max(1,kv[1]['fns'])):
    lines.append(f"  {m.replace('stdlib.compiler.',''):40s} {p['fns']:5d} {pct(p['cov'],p['fns'])} {pct(p['cov_u'],p['fns']) if test_exec is not None else '   -  '} {pct(p['bcov'],p['blocks'])} {p['blocks']:7d} {pct(p['cov_instrs'],p['instrs'])} {p['not_emitted']:6d} {pct(p['cov_u']+p['ne_test_exec'], p['fns']+p['not_emitted'])}")

# top uncovered
unc = [(e["instrs"], b, e["module"]) for b, e in fn.items() if not e["entered"] and not (test_exec and in_tests(b))]
unc.sort(reverse=True)
lines.append("")
lines.append("  Top 30 uncovered emitted compiler functions (not run by compiler process nor any test binary), by IR instrs:")
bym = collections.defaultdict(list)
for ins, b, m in unc[:30]:
    bym[m].append((ins, b.split(".")[-1], src.get(b, (None, None, None))))
for m, xs in sorted(bym.items(), key=lambda kv: -sum(x[0] for x in kv[1])):
    lines.append(f"   {m.replace('stdlib.compiler.','')}: " + ", ".join(f"{n} ({ins} instr, L{s[1]}, {s[2]} src lines)" for ins, n, s in xs))
ne = sorted(((v[2], q, v) for q, v in not_emitted.items() if not (test_exec and in_tests(q))), reverse=True)
lines.append("")
lines.append(f"  Never-emitted compiler source fns not run by any test binary: {len(ne)}; largest 15 by source lines:")
for sl_, q, v in ne[:15]:
    lines.append(f"   {q.replace('stdlib.compiler.','')} (L{v[1]}, {sl_} lines)")
txt = "\n".join(lines)
print(txt)
open(f"{OUT}/compiler_report.txt", "w").write(txt + "\n")
