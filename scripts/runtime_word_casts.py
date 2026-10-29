#!/usr/bin/env python3
"""Fail when the C runtime converts a pointer to an integer outside an allowed place.

docs/string-header-invariant-v0.md §4.3, rule 1: every AST cast node with
castKind PointerToIntegral is counted per enclosing top-level decl (a function, or the
variable of a global initializer) in runtime/*.c and runtime/*.h.

  runtime_word_casts.py CLANG RUNTIME_DIR BASELINE ADDRESS_CASTS [--emit-baseline]
                        [--scan-cache FILE]

Allowed with no entry: sprout_str_word and sprout_ptr_word (the exits), and the functions in
ADDRESS_CASTS (`file:name`, so a same-named static in another file is not exempt), provided
none returns an integer type of 64 bits or more and none takes a pointer to one (an out-param).
Everything else must match BASELINE (`file:decl:count`) exactly or below: a new decl or a higher
count fails, and so does an entry whose decl now has fewer casts, so the list only shrinks.

Every runtime/*.c is scanned. --emit-baseline prints the current non-allowed counts in baseline
format and exits. The AST holds only the host's #ifdef branches, so an ADDRESS_CASTS entry may
carry a `darwin` or `linux` tag: it is then read only on that host and ignored on the other.

--scan-cache FILE is for the test, which re-checks one tree against many list files. It stores
the decl table beside a hash of runtime/*.c and *.h and is ignored when the hash differs.
"""
import glob
import hashlib
import json
import os
import subprocess
import sys
import tempfile

EXITS = frozenset(("sprout_str_word", "sprout_ptr_word"))
CAST_KIND = "PointerToIntegral"
PLATFORMS = ("darwin", "linux")
QUALIFIERS = frozenset(("const", "volatile", "restrict", "__restrict", "__restrict__", "_Atomic"))


def host_platform():
    return "darwin" if sys.platform == "darwin" else "linux" if sys.platform.startswith("linux") else sys.platform


def load_tu(clang, path):
    """Parse one TU's AST dump. Every location dict gets its `file` filled in.

    Clang prints a location's `file` only when it differs from the previous location printed,
    so the hook walks locations in document order and carries the file forward.
    """
    cur = [None]

    def hook(pairs):
        d = dict(pairs)
        if "offset" in d:
            f = d.get("file")
            if f is None:
                d["file"] = cur[0]
            else:
                cur[0] = f
        return d

    with tempfile.TemporaryFile() as err:
        proc = subprocess.Popen(
            [clang, "-fsyntax-only", "-Xclang", "-ast-dump=json", path],
            stdout=subprocess.PIPE,
            stderr=err,
        )
        try:
            tu = json.load(proc.stdout, object_pairs_hook=hook)
        except ValueError:
            tu = None
        proc.stdout.close()
        rc = proc.wait()
        if rc != 0 or tu is None:
            err.seek(0)
            sys.stderr.write(err.read().decode(errors="replace"))
            sys.exit("runtime-word-casts: clang failed on %s (exit %d)" % (path, rc))
    return tu


def decl_file(node):
    loc = node.get("loc") or {}
    return (loc.get("expansionLoc") or loc).get("file")


def count_casts(node):
    n = 0
    stack = [node]
    while stack:
        x = stack.pop()
        if isinstance(x, dict):
            if x.get("castKind") == CAST_KIND:
                n += 1
            children = x.values()
        else:
            children = x
        stack.extend(v for v in children if isinstance(v, (dict, list)))
    return n


def has_body(node):
    return any(c.get("kind") == "CompoundStmt" for c in node.get("inner", ()))


def type_parts(t, typedefs, depth=0):
    """(base, pointer depth) of a C type string, qualifiers dropped, typedef names resolved.

    Clang desugars neither a function's return type nor a pointer parameter's pointee in the
    JSON dump, so the typedef chain (uintptr_t -> unsigned long) is followed here.
    """
    if "(" in t or "[" in t:
        return t, 0
    stars = t.count("*")
    base = " ".join(w for w in t.replace("*", " ").split() if w not in QUALIFIERS)
    if base in typedefs and depth < 16:
        b, s = type_parts(typedefs[base], typedefs, depth + 1)
        return b, s + stars
    return base, stars


def is_wide(base):
    """An integer type of 64 bits or more (long, long long, __int128 and their unsigned forms)."""
    w = base.split()
    return "__int128" in w or ("long" in w and "double" not in w)


def wide_return(node, typedefs):
    """The spelled return type when it is a 64-bit-or-wider integer, else ""."""
    ret = ((node.get("type") or {}).get("qualType") or "").split("(", 1)[0].strip()
    base, stars = type_parts(ret, typedefs)
    return ret if stars == 0 and is_wide(base) else ""


def wide_pointer_param(node, typedefs):
    """The spelled type of the first pointer-to-wide-integer parameter, else ""."""
    for c in node.get("inner", ()):
        if c.get("kind") == "ParmVarDecl":
            t = (c.get("type") or {}).get("qualType") or ""
            base, stars = type_parts(t, typedefs)
            if stars >= 1 and is_wide(base):
                return t
    return ""


def fingerprint(rt_dir):
    h = hashlib.sha1()
    for path in sorted(glob.glob(os.path.join(rt_dir, "*.c")) + glob.glob(os.path.join(rt_dir, "*.h"))):
        h.update(os.path.basename(path).encode() + b"\0")
        with open(path, "rb") as fh:
            h.update(fh.read() + b"\0")
    return h.hexdigest()


def scan(clang, rt_dir):
    """Return {(relfile, decl): (count, wide_return, wide_ptr_param, defined)} merged over all TUs."""
    root = os.path.realpath(rt_dir)
    rel_cache = {}

    def rel(f):
        if f not in rel_cache:
            r = os.path.realpath(f)
            rel_cache[f] = ("runtime/" + os.path.relpath(r, root)) if r.startswith(root + os.sep) else None
        return rel_cache[f]

    decls = {}
    for tu_path in sorted(glob.glob(os.path.join(root, "*.c"))):
        tu = load_tu(clang, tu_path)
        typedefs = {
            n["name"]: n["type"]["qualType"]
            for n in tu.get("inner", ())
            if n.get("kind") == "TypedefDecl" and "qualType" in (n.get("type") or {})
        }
        local = {}
        for node in tu.get("inner", ()):
            f = decl_file(node)
            r = rel(f) if f else None
            if r is None:
                continue
            kind = node.get("kind")
            name = node.get("name") or kind
            n = count_casts(node)
            key = (r, name)
            c, wr, wp, d = local.get(key, (0, "", "", False))
            if kind == "FunctionDecl":
                wr = wr or wide_return(node, typedefs)
                wp = wp or wide_pointer_param(node, typedefs)
                d = d or has_body(node)
            local[key] = (c + n, wr, wp, d)
        del tu
        for key, (c, wr, wp, d) in local.items():
            oc, owr, owp, od = decls.get(key, (0, "", "", False))
            decls[key] = (max(c, oc), wr or owr, wp or owp, d or od)
    return decls


def scan_cached(clang, rt_dir, cache_path):
    """scan(), reusing cache_path when it was written for the same runtime sources."""
    fp = fingerprint(rt_dir)
    try:
        with open(cache_path) as fh:
            data = json.load(fh)
        if data.get("fingerprint") == fp:
            return {(f, n): (c, wr, wp, d) for f, n, c, wr, wp, d in data["decls"]}
    except (OSError, ValueError, KeyError):
        pass
    decls = scan(clang, rt_dir)
    with open(cache_path, "w") as fh:
        json.dump({"fingerprint": fp, "decls": [[f, n, *v] for (f, n), v in sorted(decls.items())]}, fh)
    return decls


def read_entries(path):
    """Yield (lineno, text-without-comment) for non-blank lines."""
    with open(path) as fh:
        for i, line in enumerate(fh, 1):
            text = line.split("#", 1)[0].strip()
            if text:
                yield i, text


def main():
    argv = sys.argv[1:]
    emit = "--emit-baseline" in argv
    cache = None
    if "--scan-cache" in argv:
        i = argv.index("--scan-cache")
        if i + 1 >= len(argv):
            sys.exit(__doc__)
        cache = argv[i + 1]
        del argv[i : i + 2]
    args = [a for a in argv if a != "--emit-baseline"]
    if len(args) != 4:
        sys.exit(__doc__)
    clang, rt_dir, baseline_path, addr_path = args

    decls = scan_cached(clang, rt_dir, cache) if cache else scan(clang, rt_dir)
    problems = []

    host = host_platform()
    addr = {}
    for i, text in read_entries(addr_path):
        parts = text.split()
        key = tuple(parts[0].rsplit(":", 1))
        if len(parts) > 2 or (len(parts) == 2 and parts[1] not in PLATFORMS) or len(key) != 2 or not all(key):
            problems.append(
                "%s:%d: malformed entry %r (want FILE:NAME or FILE:NAME darwin|linux)" % (addr_path, i, text)
            )
            continue
        if len(parts) == 2 and parts[1] != host:
            continue
        if key in addr:
            problems.append("%s:%d: duplicate entry %s:%s" % (addr_path, i, key[0], key[1]))
        addr[key] = i

    def allowed(key):
        return key[1] in EXITS or key in addr

    if emit:
        lines = ["%s:%s:%d" % (f, name, v[0]) for (f, name), v in decls.items() if v[0] and not allowed((f, name))]
        print("\n".join(sorted(lines)))
        return 0

    for key in sorted(addr):
        f, name = key
        d = decls.get(key)
        if d is None or not d[3]:
            problems.append("ADDRESS_CASTS: %s:%s is not defined (stale entry; delete it)" % (f, name))
            continue
        count, wide_ret, wide_ptr, _ = d
        if not count:
            problems.append(
                "ADDRESS_CASTS: %s:%s has no pointer-to-integer cast on this platform (stale entry; delete it)"
                % (f, name)
            )
        if wide_ret:
            problems.append(
                "ADDRESS_CASTS: %s:%s returns a 64-bit integer type (%s), so its casts can reach a "
                "Sprout word; it cannot be listed" % (f, name, wide_ret)
            )
        if wide_ptr:
            problems.append(
                "ADDRESS_CASTS: %s:%s takes a pointer to a 64-bit integer type (%s), an out-param its "
                "casts can reach a Sprout word through; it cannot be listed" % (f, name, wide_ptr)
            )

    baseline = {}
    for i, text in read_entries(baseline_path):
        parts = text.rsplit(":", 2)
        if len(parts) != 3 or not parts[2].isdigit() or int(parts[2]) < 1:
            problems.append("%s:%d: malformed entry %r (want file:decl:count)" % (baseline_path, i, text))
            continue
        key = (parts[0], parts[1])
        if key in baseline:
            problems.append("%s:%d: duplicate entry %s:%s" % (baseline_path, i, key[0], key[1]))
        baseline[key] = int(parts[2])

    total = 0
    for key in sorted(set(baseline) | set(decls)):
        f, name = key
        have = decls.get(key, (0,))[0]
        want = baseline.get(key)
        if allowed(key):
            if want is not None:
                problems.append(
                    "%s:%s is allowed (exit or ADDRESS_CASTS) and needs no baseline entry; delete it" % (f, name)
                )
            continue
        total += have
        if want is None:
            if have:
                problems.append(
                    "%s:%s has %d pointer-to-integer cast(s) and is not in the baseline; "
                    "use sprout_str_word/sprout_ptr_word, or list it in ADDRESS_CASTS "
                    "if it is address arithmetic" % (f, name, have)
                )
        elif have > want:
            problems.append("%s:%s has %d pointer-to-integer cast(s), baseline allows %d" % (f, name, have, want))
        elif have < want:
            problems.append(
                "%s:%s now has %d pointer-to-integer cast(s), baseline lists %d; "
                "%s the baseline entry" % (f, name, have, want, "lower" if have else "delete")
            )

    if problems:
        for p in problems:
            sys.stderr.write("runtime-word-casts: %s\n" % p)
        return 1
    print(
        "runtime-word-casts: ok (%d baseline entries, %d casts, %d ADDRESS_CASTS functions)"
        % (len(baseline), total, len(addr))
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
