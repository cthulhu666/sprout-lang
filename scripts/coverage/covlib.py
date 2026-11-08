"""Shared helpers: parse .ll, decode sancov flags/pcs, classify names, parse Sprout sources."""
import os, re, struct, glob, collections, functools

# scripts/coverage/covlib.py -> repo root; all output lives under COV (COV_DIR, default build/coverage).
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
COV = os.path.abspath(os.environ.get("COV_DIR") or os.path.join(REPO, "build", "coverage"))

DEF_RE = re.compile(r'^define [^@]*@("(?:[^"]+)"|[A-Za-z0-9_.$\-]+)\(')
REF_RE = re.compile(r'@("(?:[^"]+)"|[A-Za-z0-9_.$\-]+)')
GLOB_RE = re.compile(r'^@("(?:[^"]+)"|[A-Za-z0-9_.$\-]+)\s*=(.*)$')


def unq(n):
    return n[1:-1] if n.startswith('"') else n


def parse_ll(path):
    """-> funcs: name -> {'instrs': int, 'refs': set}, globals: name -> set(refs)"""
    funcs, globs = {}, {}
    cur = None
    with open(path, errors="replace") as fh:
        for line in fh:
            if cur is None:
                m = DEF_RE.match(line)
                if m:
                    cur = unq(m.group(1)); funcs[cur] = {"instrs": 0, "refs": set()}
                    continue
                g = GLOB_RE.match(line)
                if g:
                    globs[unq(g.group(1))] = {unq(x) for x in REF_RE.findall(g.group(2))}
                continue
            if line.startswith("}"):
                cur = None; continue
            s = line.strip()
            if not s or s.endswith(":") and " " not in s or s.startswith(";"):
                continue
            funcs[cur]["instrs"] += 1
            for r in REF_RE.findall(s):
                funcs[cur]["refs"].add(unq(r))
    return funcs, globs


def lambda_owner(funcs, globs):
    """lambda/eta name -> first non-generated referencing function (through globals and other lambdas)."""
    referrers = collections.defaultdict(set)
    for f, d in funcs.items():
        for r in d["refs"]:
            referrers[r].add(f)
    glob_refs = collections.defaultdict(set)
    for g, refs in globs.items():
        for r in refs:
            glob_refs[r].add(g)

    def direct(n):
        out = set(x for x in referrers.get(n, ()) if x != n)
        for g in glob_refs.get(n, ()):
            out |= set(x for x in referrers.get(g, ()) if x != n)
        return out

    owner = {}
    for f in funcs:
        if not is_generated(f):
            continue
        seen, frontier = {f}, [f]
        found = None
        while frontier and found is None:
            nxt = []
            for n in frontier:
                for r in sorted(direct(n)):
                    if not is_generated(r):
                        found = r; break
                    if r not in seen:
                        seen.add(r); nxt.append(r)
                if found: break
            frontier = nxt
        owner[f] = found
    return owner


def is_generated(n):
    return n.startswith("__sprout_ir_lambda_") or n.startswith("__sprout_ir_eta_")


def or_into(acc, b):
    ia = int.from_bytes(acc, "little") | int.from_bytes(b, "little")
    return bytearray(ia.to_bytes(len(acc), "little"))


def decode_bits(pcs_path, nm_path, flags):
    """-> name -> list of 0/1 block flags, in PC-table order (a function starts at an entry-marked record)."""
    with open(pcs_path, "rb") as fh:
        raw = fh.read()
    n = len(raw) // 16
    recs = struct.unpack(f"<{2*n}Q", raw)
    addr2name = {}
    with open(nm_path) as fh:
        for line in fh:
            parts = line.split(None, 2)
            if len(parts) == 3:
                nm = parts[2].strip()
                if nm.startswith("_"):
                    nm = nm[1:]
                addr2name.setdefault(int(parts[0], 16), nm)
    out = {}
    cur = None
    for i in range(n):
        pc, fl = recs[2 * i], recs[2 * i + 1]
        if fl & 1:
            cur = addr2name.get(pc, f"?0x{pc:x}")
            out[cur] = []
        out[cur].append(1 if flags[i] else 0)
    return out


def decode(pcs_path, nm_path, flags):
    """-> name -> (nblocks, ncovered, entry_covered)"""
    return {name: [len(bl), sum(bl), bool(bl[0])] for name, bl in decode_bits(pcs_path, nm_path, flags).items()}


def pct(a, b):
    return f"{100*a/b:5.1f}%" if b else "  n/a"


# ---------------- module classification ----------------

@functools.lru_cache(maxsize=None)
def stdlib_modules():
    mods = []
    for p in glob.glob(f"{REPO}/stdlib/**/*.sprout", recursive=True):
        rel = os.path.relpath(p, REPO)[:-7]
        if rel.startswith("stdlib/compiler/"):
            continue
        mods.append("prelude" if rel == "stdlib/prelude" else rel.replace("/", "."))
    return tuple(sorted(mods))


@functools.lru_cache(maxsize=None)
def _source_modules():
    """Dotted names of every stdlib/**/*.sprout file, compiler included."""
    return frozenset(os.path.relpath(p, REPO)[:-7].replace("/", ".")
                     for p in glob.glob(f"{REPO}/stdlib/**/*.sprout", recursive=True))


def module_of(name, main_module):
    """Returns (module, kind). kind in fn|instance|lambda|eta|gen. main_module names the `main.` module."""
    if name.startswith("__sprout_ir_lambda_"):
        return (None, "lambda")
    if name.startswith("__sprout_ir_eta_"):
        return (None, "eta")
    if name.startswith("__tc_"):
        rest = name[5:]
        m = re.match(r"((?:[a-z_][a-z0-9_]*\.)+)", rest)
        if m:
            return (m.group(1).rstrip("."), "instance")
        m = re.search(r"stdlib_compiler_([a-z_]+?)_[A-Z]", rest)
        if m:
            return ("stdlib.compiler." + m.group(1), "instance")
        for mod in sorted(stdlib_modules(), key=len, reverse=True):
            if mod != "prelude" and "_" + mod.replace(".", "_") + "_" in "_" + rest:
                return (mod, "instance")
        return ("prelude", "instance")
    if name.startswith("__"):
        return (None, "gen")
    if name in ("main",):
        return (None, "gen")
    if name.startswith("main."):
        return (main_module, "fn")
    if name.startswith("stdlib."):
        parts = name.split(".")
        # longest module prefix that exists
        for k in range(len(parts) - 1, 1, -1):
            mod = ".".join(parts[:k])
            if mod in _source_modules():
                return (mod, "fn")
        return (".".join(parts[:-1]), "fn")
    if "." in name:
        return (name.rsplit(".", 1)[0], "fn")
    return ("prelude", "fn")


SUFFIXES = ("_argw", "$u", "_worker")


def base_name(name, known):
    """Strip compiler-generated variant suffixes while the result is a known source fn/IR name."""
    changed = True
    while changed:
        changed = False
        if name in known.get("src", ()):
            return name
        for s in SUFFIXES:
            if name.endswith(s) and (name[: -len(s)] in known.get("src", ()) or name[: -len(s)] in known.get("ir", ())):
                name = name[: -len(s)]; changed = True; break
    return name


# ---------------- Sprout source ----------------
TOP_FN = re.compile(r"^(?:export\s+)?fn\s+([a-z_][A-Za-z0-9_']*)")
TOP_DECL = re.compile(r"^(?:export\s+|pub\s+)?(fn|type|instance|class|extern|import|module|let|effect)\b")


def source_fns(path, module):
    """-> dict qualified_ir_name -> (start_line, src_lines)"""
    with open(path, errors="replace") as fh:
        lines = fh.read().split("\n")
    starts = [i for i, l in enumerate(lines) if TOP_DECL.match(l)]
    out = {}
    for j, i in enumerate(starts):
        m = TOP_FN.match(lines[i])
        if not m:
            continue
        end = starts[j + 1] if j + 1 < len(starts) else len(lines)
        # trim trailing comment/blank lines (they belong to the next decl's doc comment)
        body = [l for l in lines[i:end] if l.strip() and not l.strip().startswith("#")]
        q = m.group(1) if module == "prelude" else f"{module}.{m.group(1)}"
        out[q] = (i + 1, len(body))
    return out


def all_source_fns(compiler):
    res = {}
    for p in glob.glob(f"{REPO}/stdlib/**/*.sprout", recursive=True):
        rel = os.path.relpath(p, REPO)[:-7]
        is_c = rel.startswith("stdlib/compiler/")
        if is_c != compiler:
            continue
        mod = "prelude" if rel == "stdlib/prelude" else rel.replace("/", ".")
        for q, v in source_fns(p, mod).items():
            res[q] = (mod,) + v
    return res
