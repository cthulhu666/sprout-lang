#!/usr/bin/env python3
"""Fail when the C runtime lets a pointer's address become a signed 64-bit integer.

docs/string-header-invariant-v0.md §4.3, rule 1. Over runtime/*.c and runtime/*.h, per top-level
decl (a function, or the variable of a global initializer), the checker counts sites:

  - an AST cast with castKind PointerToIntegral, and
  - an IntegralCast whose operand is spelled `uintptr_t` or `intptr_t` (the address types) and
    whose destination is a signed integer type of 64 bits or more (`long`, `long long`,
    `int64_t`, `__int128`), implicit or explicit. One whose operand is itself a counted
    PointerToIntegral cast is not counted twice.

Allowed with no entry: the exits (EXITS, keyed `file:name`), and the functions in ADDRESS_CASTS
(`file:name`) provided that, inside the function:
  - A1: every PointerToIntegral cast has destination type spelled `uintptr_t`;
  - A2: no address-type value is converted to a signed 64-bit-or-wider integer type;
  - it does not return an integer type of 64 bits or more, and takes no pointer to one.
Everything else must match BASELINE (`file:decl:count`) exactly or below: a new decl or a higher
count fails, and so does an entry whose decl now has fewer sites, so the list only shrinks.

Every runtime/*.c is scanned. --emit-baseline prints the current non-allowed counts in baseline
format and exits. The AST holds only the host's #ifdef branches, so an ADDRESS_CASTS entry may
carry a `darwin` or `linux` tag: it is then read only on that host and ignored on the other.

--scan-cache FILE is for the test, which re-checks one tree against many list files. It stores
the decl table beside a hash of runtime/*.c and *.h and is ignored when the hash differs.
"""
import argparse
import glob
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from collections import namedtuple

# The only places a pointer may become a Sprout word, keyed `file:name`. A same-named function in
# another file is not exempt. The header is created by docs/string-header-invariant-v0.md §4.
EXITS = frozenset(("runtime/sprout_word.h:sprout_str_word", "runtime/sprout_word.h:sprout_ptr_word"))
CAST_KIND = "PointerToIntegral"
CONV_KIND = "IntegralCast"
ADDRESS_TYPE = "uintptr_t"
ADDRESS_TYPES = frozenset(("uintptr_t", "intptr_t"))
PLATFORMS = ("darwin", "linux")
QUALIFIERS = frozenset(("const", "volatile", "restrict", "__restrict", "__restrict__", "_Atomic"))

# One top-level decl, merged over every TU that sees it.
#   casts           PointerToIntegral casts
#   bad_casts       of those, the ones whose destination is not `uintptr_t` (rule A1)
#   conv_free       address-type -> signed 64-bit conversions whose operand is not a cast
#   conv_after_ok   the same, whose operand is a PointerToIntegral cast to `uintptr_t`
#   bad_dest        spelling of the first A1 destination
#   conv_note       "uintptr_t to long long": the first A2 conversion
#   wide_return     spelled return type when 64-bit or wider, else ""
#   wide_ptr        spelled type of the first pointer-to-wide-integer parameter, else ""
#   defined         has a body
Decl = namedtuple(
    "Decl",
    "casts bad_casts conv_free conv_after_ok bad_dest conv_note wide_return wide_ptr defined",
)
EMPTY = Decl(0, 0, 0, 0, "", "", "", "", False)


def sites(d):
    """Sites counted against the baseline when the decl is not listed in ADDRESS_CASTS."""
    return d.casts + d.conv_free


def merge(a, b, num):
    """Combine two views of a decl: counts with num (sum or max), strings and flags with `or`."""
    return Decl(*(x or y if isinstance(x, (str, bool)) else num(x, y) for x, y in zip(a, b)))


def sites_note(d):
    """Suffix naming the conversions behind a count, when there are any."""
    return " (includes %d %s conversion(s))" % (d.conv_free, d.conv_note) if d.conv_free else ""


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


def spelled(node):
    """The type clang prints for an expression, qualifiers dropped; typedef names are kept."""
    t = (node.get("type") or {}).get("qualType") or ""
    return " ".join(w for w in t.split() if w not in QUALIFIERS)


def operand(cast):
    """A cast's child expression, parentheses skipped (None when it has none)."""
    inner = cast.get("inner") or ()
    x = inner[0] if inner else None
    while x is not None and x.get("kind") == "ParenExpr":
        inner = x.get("inner") or ()
        x = inner[0] if inner else None
    return x


def signed_wide(t, typedefs):
    """Whether spelled type t is a signed integer of 64 bits or more once typedefs are resolved."""
    base, stars = type_parts(t, typedefs)
    return stars == 0 and is_wide(base) and "unsigned" not in base.split()


def analyze(node, typedefs):
    """Count a decl's casts and address-type conversions (see Decl); the type fields stay empty."""
    casts = bad = free = after_ok = 0
    bad_dest = conv_note = ""
    stack = [node]
    while stack:
        x = stack.pop()
        if isinstance(x, dict):
            kind = x.get("castKind")
            if kind == CAST_KIND:
                casts += 1
                if spelled(x) != ADDRESS_TYPE:
                    bad += 1
                    bad_dest = bad_dest or spelled(x)
            elif kind == CONV_KIND:
                op = operand(x)
                src, dst = (spelled(op), spelled(x)) if op is not None else ("", "")
                if src in ADDRESS_TYPES and signed_wide(dst, typedefs):
                    conv_note = conv_note or "%s to %s" % (src, dst)
                    if op.get("castKind") != CAST_KIND:
                        free += 1
                    elif spelled(op) == ADDRESS_TYPE:
                        after_ok += 1
            children = x.values()
        else:
            children = x
        stack.extend(v for v in children if isinstance(v, (dict, list)))
    return EMPTY._replace(
        casts=casts, bad_casts=bad, conv_free=free, conv_after_ok=after_ok, bad_dest=bad_dest, conv_note=conv_note
    )


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
    """Return {(relfile, decl): Decl} merged over all TUs."""
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
            d = analyze(node, typedefs)
            if kind == "FunctionDecl":
                d = d._replace(
                    wide_return=wide_return(node, typedefs),
                    wide_ptr=wide_pointer_param(node, typedefs),
                    defined=has_body(node),
                )
            key = (r, name)
            local[key] = merge(local.get(key, EMPTY), d, lambda x, y: x + y)
        del tu
        for key, d in local.items():
            decls[key] = merge(decls.get(key, EMPTY), d, max)
    return decls


def scan_cached(clang, rt_dir, cache_path):
    """scan(), reusing cache_path when it was written for the same runtime sources."""
    fp = fingerprint(rt_dir)
    try:
        with open(cache_path) as fh:
            data = json.load(fh)
        if data.get("fingerprint") == fp:
            return {(row["file"], row["name"]): Decl(*(row[f] for f in Decl._fields)) for row in data["decls"]}
    except (OSError, ValueError, KeyError):
        pass
    decls = scan(clang, rt_dir)
    with open(cache_path, "w") as fh:
        rows = [dict(d._asdict(), file=f, name=n) for (f, n), d in sorted(decls.items())]
        json.dump({"fingerprint": fp, "decls": rows}, fh)
    return decls


def read_entries(path):
    """Yield (lineno, text-without-comment) for non-blank lines."""
    with open(path) as fh:
        for i, line in enumerate(fh, 1):
            text = line.split("#", 1)[0].strip()
            if text:
                yield i, text


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("clang")
    ap.add_argument("runtime_dir")
    ap.add_argument("baseline")
    ap.add_argument("address_casts")
    ap.add_argument("--emit-baseline", action="store_true", help="print the current counts in baseline format")
    ap.add_argument("--scan-cache", metavar="FILE", help="reuse the decl table in FILE (for the test)")
    args = ap.parse_args()
    clang, rt_dir, baseline_path, addr_path = args.clang, args.runtime_dir, args.baseline, args.address_casts
    emit = args.emit_baseline

    decls = scan_cached(clang, rt_dir, args.scan_cache) if args.scan_cache else scan(clang, rt_dir)
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
        return "%s:%s" % key in EXITS or key in addr

    if emit:
        lines = ["%s:%s:%d" % (f, name, sites(d)) for (f, name), d in decls.items() if sites(d) and not allowed((f, name))]
        print("\n".join(sorted(lines)))
        return 0

    for key in sorted(addr):
        f, name = key
        d = decls.get(key)
        if d is None or not d.defined:
            problems.append("ADDRESS_CASTS: %s:%s is not defined (stale entry; delete it)" % (f, name))
            continue
        if not d.casts:
            problems.append(
                "ADDRESS_CASTS: %s:%s has no pointer-to-integer cast on this platform (stale entry; delete it)"
                % (f, name)
            )
        if d.bad_casts:
            problems.append(
                "ADDRESS_CASTS: %s:%s casts a pointer to %s (rule A1: a listed function casts to %s); "
                "it cannot be listed as is" % (f, name, d.bad_dest, ADDRESS_TYPE)
            )
        if d.conv_free + d.conv_after_ok:
            problems.append(
                "ADDRESS_CASTS: %s:%s converts %s (rule A2: an address must not become a signed 64-bit "
                "integer); it cannot be listed" % (f, name, d.conv_note)
            )
        if d.wide_return:
            problems.append(
                "ADDRESS_CASTS: %s:%s returns a 64-bit integer type (%s), so its casts can reach a "
                "Sprout word; it cannot be listed" % (f, name, d.wide_return)
            )
        if d.wide_ptr:
            problems.append(
                "ADDRESS_CASTS: %s:%s takes a pointer to a 64-bit integer type (%s), an out-param its "
                "casts can reach a Sprout word through; it cannot be listed" % (f, name, d.wide_ptr)
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
        d = decls.get(key, EMPTY)
        have = sites(d)
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
                    "if it is address arithmetic%s" % (f, name, have, sites_note(d))
                )
        elif have > want:
            problems.append(
                "%s:%s has %d pointer-to-integer cast(s), baseline allows %d%s"
                % (f, name, have, want, sites_note(d))
            )
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
