"""Stdlib coverage from the sancov-instrumented test binaries (stdlib/compiler/ide unit tests + conformance/run)."""
import sys, os, glob, json, collections, struct
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from covlib import *

C = COV
R = f"{C}/stdlib_run"
OUT = f"{C}/results"; os.makedirs(OUT, exist_ok=True)

def decode_bits(pcs_path, nm_path, flags):
    raw = open(pcs_path, "rb").read()
    n = len(raw) // 16
    recs = struct.unpack(f"<{2*n}Q", raw)
    addr2name = {}
    for line in open(nm_path):
        parts = line.split(None, 2)
        if len(parts) == 3:
            nm = parts[2].strip(); nm = nm[1:] if nm.startswith("_") else nm
            addr2name.setdefault(int(parts[0], 16), nm)
    out = {}; cur = None
    for i in range(n):
        pc, fl = recs[2*i], recs[2*i+1]
        if fl & 1:
            cur = addr2name.get(pc, f"?0x{pc:x}"); out[cur] = []
        out[cur].append(1 if flags[i] else 0)
    return out

status = {}
for st in glob.glob(f"{R}/work/*/status"):
    idx, kind, s, f = open(st).read().rstrip("\n").split("\t")
    status[idx] = (kind, s, f)

blocks = {}   # name -> {nblocks: bitlist(or)}
entered = collections.defaultdict(set)  # name -> set(test idx)
instrs = {}
nobin = []
nodata = []
for idx, (kind, s, f) in sorted(status.items(), key=lambda kv: int(kv[0])):
    W = f"{R}/work/{idx}"
    if not os.path.exists(f"{W}/t.pcs"):
        nobin.append((f, s)); continue
    fl = glob.glob(f"{R}/flags/{idx}.*.flags")
    npcs = os.path.getsize(f"{W}/t.pcs") // 16
    acc = bytearray(npcs)
    for p in fl:
        b = open(p, "rb").read()
        if len(b) == npcs: acc = or_into(acc, b)
    if not fl:
        nodata.append((f, s))
    bits = decode_bits(f"{W}/t.pcs", f"{W}/t.nm", acc)
    funcs, _ = parse_ll(f"{W}/t.ll")
    for name, bl in bits.items():
        if name.startswith("main.") or name.startswith("?"):
            continue
        d = blocks.setdefault(name, {})
        cur = d.get(len(bl))
        d[len(bl)] = bl if cur is None else [a | b for a, b in zip(cur, bl)]
        if bl and bl[0]:
            entered[name].add(idx)
    for name, d in funcs.items():
        instrs[name] = max(instrs.get(name, 0), d["instrs"])

print(f"test inputs {len(status)}; no binary/pcs {len(nobin)}; ran but no flags (crash/_exit) {len(nodata)}")
for f, s in nobin: print("  no-binary", s, f)
for f, s in nodata: print("  no-data", s, f)
json.dump(sorted(entered), open(f"{OUT}/stdlib_executed_names.json", "w"))

src = all_source_fns(compiler=False)
ir_names = set(blocks)
known = {"src": set(src), "ir": ir_names}

# collapse to source-level fns
fn = {}
for name in blocks:
    mod, kind = module_of(name, None)
    if kind not in ("fn", "instance") or mod is None or mod.startswith("stdlib.compiler.") or mod.startswith("testsupport") or mod.startswith("ide"):
        continue
    if mod != "prelude" and not mod.startswith("stdlib."):
        continue
    b = base_name(name, known) if kind == "fn" else name
    e = fn.setdefault(b, dict(module=mod, kind=kind, entered=False, instrs=0, nb=0, nbc=0, tests=set()))
    e["entered"] |= bool(entered.get(name))
    e["tests"] |= entered.get(name, set())
    e["instrs"] += instrs.get(name, 0)
    nb, bl = max(blocks[name].items(), key=lambda kv: kv[0])  # largest variant shape
    # use the shape seen in the most binaries is not tracked; take the OR within the largest shape
    e["nb"] += nb; e["nbc"] += sum(bl)
for q, v in src.items():
    if q not in fn:
        fn[q] = dict(module=v[0], kind="fn", entered=False, instrs=0, nb=0, nbc=0, tests=set(), not_emitted=True)

# optional union with the compiler process
cpath = f"{OUT}/compiler_ir_rows.json"
comp_entered = set()
if os.path.exists(cpath):
    for r in json.load(open(cpath)):
        if r["entered"]:
            comp_entered.add(r["ir"])
            comp_entered.add(base_name(r["ir"], known))

per = collections.defaultdict(collections.Counter)
for b, e in fn.items():
    p = per[e["module"]]
    p["fns"] += 1; p["cov"] += e["entered"]; p["ne"] += e.get("not_emitted", False)
    p["cov_u"] += e["entered"] or b in comp_entered
    p["blocks"] += e["nb"]; p["bcov"] += e["nbc"]
    p["srcl"] += src.get(b, (0, 0, 0))[2]
    p["srcl_cov"] += src.get(b, (0, 0, 0))[2] if e["entered"] else 0
T = collections.Counter()
for p in per.values(): T.update(p)
def pct(a, b): return f"{100*a/b:5.1f}%" if b else "  n/a"
L = []
L.append("STDLIB (non-compiler stdlib/**/*.sprout incl. prelude; executed by test binaries)")
L.append(f"  function-level: {T['cov']}/{T['fns']} = {pct(T['cov'],T['fns'])}   (denominator = source top-level fns + emitted instance methods; {T['ne']} never emitted in any test binary)")
L.append(f"  source-line-weighted (top-level fns): {T['srcl_cov']}/{T['srcl']} = {pct(T['srcl_cov'],T['srcl'])}")
L.append(f"  block-level over emitted fns: {T['bcov']}/{T['blocks']} = {pct(T['bcov'],T['blocks'])}")
L.append(f"  function-level incl. compiler-process execution: {T['cov_u']}/{T['fns']} = {pct(T['cov_u'],T['fns'])}")
L.append("")
L.append(f"  {'module':34s} {'fns':>5s} {'fn%':>6s} {'+cc%':>6s} {'blk%':>6s} {'noemit':>6s}")
for m, p in sorted(per.items(), key=lambda kv: (kv[1]['cov']/max(1,kv[1]['fns']), -kv[1]['fns'])):
    L.append(f"  {m.replace('stdlib.',''):34s} {p['fns']:5d} {pct(p['cov'],p['fns'])} {pct(p['cov_u'],p['fns'])} {pct(p['bcov'],p['blocks'])} {p['ne']:6d}")
unc = []
for b, e in fn.items():
    if e["entered"]: continue
    s = src.get(b)
    unc.append((s[2] if s else 0, e["instrs"], b, e["module"], s[1] if s else None, e.get("not_emitted", False)))
unc.sort(reverse=True)
L.append("")
L.append("  Top 30 uncovered stdlib functions by source lines (ne = never emitted into any test binary):")
bym = collections.defaultdict(list)
for sl, ins, b, m, line, ne in unc[:30]:
    bym[m].append(f"{b.split('.')[-1]} ({sl} lines, L{line}{', ne' if ne else f', {ins} instr'})")
for m, xs in sorted(bym.items(), key=lambda kv: -len(kv[1])):
    L.append(f"   {m.replace('stdlib.','')}: " + ", ".join(xs))
txt = "\n".join(L); print(txt)
open(f"{OUT}/stdlib_report.txt", "w").write(txt + "\n")
json.dump({b: dict(e, tests=sorted(e["tests"])) for b, e in fn.items()}, open(f"{OUT}/stdlib_fn.json", "w"))
