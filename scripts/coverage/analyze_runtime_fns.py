"""Uncovered C runtime functions (all binaries merged), sized by source lines of the function's own file."""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from covlib import COV as C
d = json.load(open(f"{C}/results/runtime_export_all.json"))
fns = {}
for data in d["data"]:
    for f in data["functions"]:
        files = f["filenames"]
        if not files or "/runtime/" not in files[0]:
            continue
        own = [r for r in f["regions"] if r[5] == 0 and r[7] == 0]
        if not own:
            continue
        lo, hi = min(r[0] for r in own), max(r[2] for r in own)
        name = f["name"].split(":")[-1]
        k = (os.path.basename(files[0]), name)
        c = fns.get(k, (0, 0, 0))
        fns[k] = (c[0] + f["count"], max(c[1], hi - lo + 1), lo)
unc = sorted(((v[1], k[0], k[1], v[2]) for k, v in fns.items() if v[0] == 0), reverse=True)
print(f"{len(fns)} runtime functions, {len(unc)} never entered")
GROUPS = [("GC", ("gc", "sweep", "mark", "region", "slot", "heap", "root", "alloc", "fl_", "barrier", "hdr")),
          ("scheduler/task", ("sched", "task", "park", "chan", "timer", "deadline")),
          ("poller/net", ("poll", "tcp", "accept", "http", "tls", "dns", "net", "socket")),
          ("analysis service", ("analysis", "sproutd")),
          ("other", ())]
by = {g: [] for g, _ in GROUPS}
for l, f, n, s in unc:
    if f == "sprout_poll.c":
        by["poller/net"].append((l, f, n, s)); continue
    if f == "sprout_scheduler.c":
        by["scheduler/task"].append((l, f, n, s)); continue
    for g, keys in GROUPS:
        if not keys or any(k in n.lower() for k in keys):
            by[g].append((l, f, n, s)); break
for g, xs in by.items():
    print(f"\n{g}: {len(xs)} uncovered")
    for l, f, n, s in xs[:25]:
        print(f"  {l:4d} lines  {f}:{n} (L{s})")
json.dump([dict(file=f, fn=n, lines=l, start=s) for l, f, n, s in unc], open(f"{C}/results/runtime_uncovered.json", "w"), indent=0)
