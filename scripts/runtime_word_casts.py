#!/usr/bin/env python3
"""Fail when the C runtime converts a pointer to an integer outside an allowed place.

docs/string-header-invariant-v0.md §4.3, rule 1: every AST cast node with
castKind PointerToIntegral is counted per enclosing top-level decl (a function, or the
variable of a global initializer) in runtime/*.c and runtime/*.h.

  runtime_word_casts.py CLANG RUNTIME_DIR BASELINE ADDRESS_CASTS [--emit-baseline]

Allowed with no entry: sprout_str_word and sprout_ptr_word (the exits), and the functions in
ADDRESS_CASTS, provided none returns `long long`. Everything else must match BASELINE
(`file:decl:count`) exactly or below: a new decl or a higher count fails, and so does an entry
whose decl now has fewer casts, so the list only shrinks.

--emit-baseline prints the current non-allowed counts in baseline format and exits.
The AST holds only the host's #ifdef branches, so an ADDRESS_CASTS entry may carry a `darwin` or
`linux` tag: it is then read only on that host and ignored on the other.
"""
import json
import os
import subprocess
import sys
import tempfile

TUS = ("sprout_runtime.c", "sprout_scheduler.c", "sprout_poll.c")
EXITS = frozenset(("sprout_str_word", "sprout_ptr_word"))
CAST_KIND = "PointerToIntegral"
PLATFORMS = ("darwin", "linux")


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
            for v in x.values():
                if isinstance(v, (dict, list)):
                    stack.append(v)
        else:
            for v in x:
                if isinstance(v, (dict, list)):
                    stack.append(v)
    return n


def has_body(node):
    return any(c.get("kind") == "CompoundStmt" for c in node.get("inner", ()))


def return_type(node):
    t = node.get("type") or {}
    q = t.get("desugaredQualType") or t.get("qualType") or ""
    return q.split("(", 1)[0].strip()


def scan(clang, rt_dir):
    """Return {(relfile, decl): (count, returns_word, defined)} merged over all TUs."""
    root = os.path.realpath(rt_dir)
    rel_cache = {}

    def rel(f):
        if f not in rel_cache:
            r = os.path.realpath(f)
            rel_cache[f] = ("runtime/" + os.path.relpath(r, root)) if r.startswith(root + os.sep) else None
        return rel_cache[f]

    decls = {}
    for tu_name in TUS:
        tu = load_tu(clang, os.path.join(root, tu_name))
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
            c, w, d = local.get(key, (0, False, False))
            if kind == "FunctionDecl":
                w = w or return_type(node) == "long long"
                d = d or has_body(node)
            local[key] = (c + n, w, d)
        del tu
        for key, (c, w, d) in local.items():
            oc, ow, od = decls.get(key, (0, False, False))
            decls[key] = (max(c, oc), w or ow, d or od)
    return decls


def read_entries(path):
    """Yield (lineno, text-without-comment) for non-blank lines."""
    with open(path) as fh:
        for i, line in enumerate(fh, 1):
            text = line.split("#", 1)[0].strip()
            if text:
                yield i, text


def main():
    args = [a for a in sys.argv[1:] if a != "--emit-baseline"]
    emit = len(args) != len(sys.argv) - 1
    if len(args) != 4:
        sys.exit(__doc__)
    clang, rt_dir, baseline_path, addr_path = args

    decls = scan(clang, rt_dir)
    problems = []

    host = host_platform()
    addr = {}
    for i, text in read_entries(addr_path):
        parts = text.split()
        if len(parts) > 2 or (len(parts) == 2 and parts[1] not in PLATFORMS):
            problems.append(
                "%s:%d: malformed entry %r (want NAME or NAME darwin|linux)" % (addr_path, i, text)
            )
            continue
        name = parts[0]
        if len(parts) == 2 and parts[1] != host:
            continue
        if name in addr:
            problems.append("%s:%d: duplicate entry %s" % (addr_path, i, name))
        addr[name] = i
    allowed = EXITS | set(addr)

    if emit:
        lines = ["%s:%s:%d" % (f, name, c) for (f, name), (c, _, _) in decls.items() if c and name not in allowed]
        print("\n".join(sorted(lines)))
        return 0

    for name in sorted(addr):
        found = [(k, v) for k, v in decls.items() if k[1] == name and v[2]]
        if not found:
            problems.append("ADDRESS_CASTS: %s is not defined in runtime/ (stale entry; delete it)" % name)
            continue
        if not any(v[0] for _, v in found):
            problems.append(
                "ADDRESS_CASTS: %s has no pointer-to-integer cast on this platform (stale entry; delete it)" % name
            )
        for (f, _), (_, w, _) in found:
            if w:
                problems.append(
                    "ADDRESS_CASTS: %s:%s returns long long, so its casts can reach a Sprout word; "
                    "it cannot be listed" % (f, name)
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
        have = decls.get(key, (0, False, False))[0]
        want = baseline.get(key)
        if name in allowed:
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
