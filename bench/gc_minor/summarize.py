"""Summarise ab.sh's results.tsv as Markdown. Run: python3 -I summarize.py results.tsv [...]

Per workload x setting x arm: median and min-max of each metric. Per pair of arms, the ratio of
medians (minor / off) and a flag where the two arms' min-max ranges overlap, i.e. the direction
of the ratio is inside the run-to-run spread. Then the checks: stdout hashes across arms, exit codes.
"""
import csv
import statistics
import sys
from collections import defaultdict

# (column header, value getter). cpu is user+sys.
METRICS = [
    ("real s", lambda r: float(r["real"])),
    ("cpu s", lambda r: float(r["user"]) + float(r["sys"])),
    ("gc ms", lambda r: int(r["gc_us_total"]) / 1000.0),
    ("p50 us", lambda r: int(r["gc_us_steady_p50"])),
    ("p90 us", lambda r: int(r["gc_us_steady_p90"])),
    ("rss MB", lambda r: int(r["max_rss_kb"]) / 1024.0),
]


OFF = ("0", "-", "")


def arm_key(r):
    """('0', '-', '') for the off arm; ('1', '-' | 'E', label) for minor, with its
    SPROUT_GC_MAJOR_EVERY and its `-a` arm label. Older TSVs have no arm column: label ''."""
    return (r["minor"], r["major_every"], r.get("arm") or "")


def arm_label(k):
    if k[0] == "0":
        return "off"
    if k[2]:
        return k[2]
    return "minor" if k[1] == "-" else f"minor E={k[1]}"


def arm_order(k):
    return (k[0], 0 if k[1] == "-" else int(k[1]), k[2])


def fmt(x):
    if x >= 100:
        return f"{x:,.0f}"
    if x >= 10:
        return f"{x:.1f}"
    return f"{x:.2f}"


def cell(vals):
    return f"{fmt(statistics.median(vals))} ({fmt(min(vals))}-{fmt(max(vals))})"


def main(paths):
    rows = []
    for p in paths:
        with open(p, newline="") as fh:
            rows += list(csv.DictReader(fh, delimiter="\t"))
    if not rows:
        print("no rows")
        return 1

    groups = defaultdict(list)  # (setting, workload, arm) -> rows
    for r in rows:
        groups[(r["setting"], r["workload"], arm_key(r))].append(r)

    wl_order = []
    for r in rows:
        if r["workload"] not in wl_order:
            wl_order.append(r["workload"])

    for setting in ("fixed", "default", "every", "arms"):
        wls = [w for w in wl_order if any(g[0] == setting and g[1] == w for g in groups)]
        if not wls:
            continue
        title = {
            "fixed": "fixed: ADAPT_RATIO=0, fixed THRESHOLD (collection counts differ; compare per-collection p50/p90)",
            "default": "default: no GC env but the arm",
            "every": "every: SPROUT_GC_MAJOR_EVERY sweep, default settings, MINOR=0 baseline",
            "arms": "arms: named MINOR=1 arms (-a) with extra env, default settings, MINOR=0 baseline",
        }[setting]
        print(f"\n## {title}\n")
        print("Cells: median (min-max). `ratio` = arm / off by medians; `~` = the ranges overlap,")
        print("so the direction is inside the run-to-run spread. cycles = total (minor+major).\n")
        head = ["workload", "arm", "n"] + [m[0] for m in METRICS] + ["cycles (min+maj)"]
        print("| " + " | ".join(head) + " |")
        print("|" + "|".join(["---"] * len(head)) + "|")
        for w in wls:
            arms = sorted({g[2] for g in groups if g[0] == setting and g[1] == w}, key=arm_order)
            base = groups.get((setting, w, OFF))
            for a in arms:
                rs = groups[(setting, w, a)]
                cells = [w, arm_label(a), str(len(rs))]
                cells += [cell([m[1](r) for r in rs]) for m in METRICS]
                cyc = statistics.median(int(r["cycles"]) for r in rs)
                mi = statistics.median(int(r["minors"]) for r in rs)
                ma = statistics.median(int(r["majors"]) for r in rs)
                cells.append(f"{cyc:,.0f} ({mi:,.0f}+{ma:,.0f})")
                print("| " + " | ".join(cells) + " |")
                if a != OFF and base:
                    ratios = []
                    for _, get in METRICS:
                        b, v = [get(r) for r in base], [get(r) for r in rs]
                        mb = statistics.median(b)
                        if mb == 0:
                            ratios.append("n/a")
                            continue
                        flag = " ~" if not (max(v) < min(b) or min(v) > max(b)) else ""
                        ratios.append(f"{statistics.median(v) / mb:.2f}{flag}")
                    cb = statistics.median(int(r["cycles"]) for r in base)
                    cv = statistics.median(int(r["cycles"]) for r in rs)
                    cr = f"{cv / cb:.2f}x" if cb else "n/a"
                    print("| | *ratio* | | " + " | ".join(ratios) + f" | {cr} |")

    print("\n## Checks\n")
    bad = False

    # Stdout is checked across every arm and setting of a workload.
    hashes = defaultdict(set)
    for r in rows:
        hashes[r["workload"]].add(r["stdout_hash"])
    for w, hs in hashes.items():
        if len(hs) > 1:
            bad = True
            print(f"- **STDOUT MISMATCH** {w}: {len(hs)} distinct hashes {sorted(hs)}")

    for r in rows:
        if r["exit"] != "0":
            bad = True
            arm = f" arm={r['arm']}" if r.get("arm") else ""
            print(f"- NONZERO EXIT {r['exit']}: {r['workload']} {r['setting']} minor={r['minor']}{arm} run {r['run']}")

    if not bad:
        print("- none: stdout hashes agree, all exits 0")

    loads = [float(r["load"]) for r in rows]
    print(f"\nLoad average at run start: median {statistics.median(loads):.2f}, max {max(loads):.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
