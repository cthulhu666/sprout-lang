#!/usr/bin/env python3
"""Fail when the C runtime lets a pointer's address become a signed 64-bit integer.

docs/string-header-invariant-v0.md §4.3. Over runtime/*.c and runtime/*.h, per top-level decl (a
function, or the variable of a global initializer), the checker enforces three rules.

Rule 1: a decl with a site fails unless it is allowed. A site is:

  - an AST cast with castKind PointerToIntegral, and
  - an IntegralCast whose operand is spelled `uintptr_t` or `intptr_t` (the address types) and
    whose destination is a signed integer type of 64 bits or more (`long`, `long long`,
    `int64_t`, `__int128`), implicit or explicit. One whose operand is itself a counted
    PointerToIntegral cast is not counted twice.

Allowed: the exits (EXITS, keyed `file:name`), and the functions in ADDRESS_CASTS (`file:name`)
provided that, inside the function:
  - A1: every PointerToIntegral cast has destination type spelled `uintptr_t`;
  - A2: no address-type value is converted to a signed 64-bit-or-wider integer type;
  - it does not return an integer type of 64 bits or more, and takes no pointer to one.

Rule 2, everywhere: a call to sprout_ptr_word whose argument, with pointer-to-pointer casts and
parentheses stripped, has at any layer the type pointer-to-char (any signedness or qualifiers).

Rule 3, outside WRITERS: a SproutStr is not built by an initializer list or a compound literal,
and `hdr_p` is not written, address-taken or modified. Only a read (an lvalue-to-rvalue
conversion) passes. Nor is a pointer to SproutStr cast to or from a pointer to any other type but
`void` (`*(SproutStr*)&raw`, `*(char**)&s = raw`), explicit or implicit.

Every runtime/*.c is scanned. The AST holds only the host's #ifdef branches, so an ADDRESS_CASTS
entry may carry a `darwin` or `linux` tag: it is then read only on that host and ignored on the
other.

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
# another file is not exempt. sprout_str_of_word_in is not one: it casts an integer to a pointer.
EXITS = frozenset(("runtime/sprout_word.h:sprout_str_word_in", "runtime/sprout_word.h:sprout_ptr_word"))

# The only decls that may build a SproutStr or write its hdr_p (rule 3), keyed `file:name`.
WRITERS = frozenset(
    (
        "runtime/sprout_runtime.c:sprout_str_alloc",
        "runtime/sprout_runtime.c:sprout_str_intern",
        "runtime/sprout_word.h:sprout_str_of_word_in",
    )
)
_names = sorted(w.rsplit(":", 1)[1] for w in WRITERS)
WRITER_LIST = ", ".join(_names[:-1]) + " and " + _names[-1]
PTR_EXIT = "sprout_ptr_word"
STR_TYPE = "SproutStr"
STR_FIELD = "hdr_p"
CHAR_TYPES = frozenset(("char", "signed char", "unsigned char"))
PTR_CAST_KINDS = frozenset(("BitCast", "NoOp"))
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
#   char_words      rule 2: sprout_ptr_word calls whose argument is a char pointer
#   forges          rule 3: initializer lists and compound literals of type SproutStr
#   hdr_writes      rule 3: uses of hdr_p other than a read
#   puns            rule 3: pointer casts between SproutStr* and a non-void pointer type
Decl = namedtuple(
    "Decl",
    "casts bad_casts conv_free conv_after_ok bad_dest conv_note wide_return wide_ptr defined "
    "char_words forges hdr_writes puns",
)
EMPTY = Decl(0, 0, 0, 0, "", "", "", "", False, 0, 0, 0, 0)

PUN_CAST_KIND = "BitCast"


class Typedefs(dict):
    """One TU's typedef table. str_base is STR_TYPE resolved through it, set once by scan()."""

    str_base = STR_TYPE


def sites(d):
    """Sites counted when the decl is not listed in ADDRESS_CASTS."""
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


def is_char_pointer(t, typedefs):
    """Whether spelled type t is a pointer to char, signed char or unsigned char."""
    base, stars = type_parts(t, typedefs)
    return stars == 1 and base in CHAR_TYPES


def first_inner(node):
    inner = node.get("inner") or ()
    return inner[0] if inner else None


def callee_name(call):
    """The name of the function a CallExpr calls directly, else ""."""
    x = first_inner(call)
    while x is not None and x.get("kind") in ("ParenExpr", "ImplicitCastExpr"):
        x = first_inner(x)
    if x is not None and x.get("kind") == "DeclRefExpr":
        return (x.get("referencedDecl") or {}).get("name") or ""
    return ""


def passes_char_pointer(arg, typedefs):
    """Whether, at any layer under pointer-to-pointer casts and parentheses, arg is a char pointer."""
    x = arg
    while x is not None:
        if is_char_pointer(spelled(x), typedefs):
            return True
        kind = x.get("kind")
        is_ptr_cast = kind in ("ImplicitCastExpr", "CStyleCastExpr") and x.get("castKind") in PTR_CAST_KINDS
        if kind == "ParenExpr" or is_ptr_cast:
            x = first_inner(x)
        else:
            return False
    return False


def is_str_type(t, typedefs, stars):
    """Whether spelled type t is SproutStr at pointer depth `stars`, aliases resolved."""
    return type_parts(t, typedefs) == (typedefs.str_base, stars)


def is_str_pointer_pun(dst, src, typedefs):
    """Whether a pointer cast dst <- src joins a SproutStr pointer to a non-void, non-SproutStr one.

    `void*` is exempt on both sides (malloc, memcpy). Pointers only: depth >= 1 on both.
    """
    d_base, d_stars = type_parts(dst, typedefs)
    s_base, s_stars = type_parts(src, typedefs)
    if d_stars < 1 or s_stars < 1 or (d_base == typedefs.str_base) == (s_base == typedefs.str_base):
        return False
    other = s_base if d_base == typedefs.str_base else d_base
    return other != "void"


def is_str_write(member, parent, typedefs):
    """Whether a MemberExpr names SproutStr's hdr_p other than to read it.

    A read is wrapped in an LValueToRValue cast; an assignment, `&`, `++` and a compound
    assignment have another parent. The parent skips parentheses. A prvalue member (older clang
    on `f().hdr_p`, which has no cast) cannot be written, so it passes.
    """
    base = first_inner(member)
    if member.get("name") != STR_FIELD or base is None:
        return False
    if not is_str_type(spelled(base), typedefs, 1 if member.get("isArrow") else 0):
        return False
    if member.get("valueCategory") == "prvalue":
        return False
    is_read = (
        parent is not None and parent.get("kind") == "ImplicitCastExpr" and parent.get("castKind") == "LValueToRValue"
    )
    return not is_read


def analyze(node, typedefs):
    """Count a decl's casts, address-type conversions and rule 2/3 uses (see Decl)."""
    casts = bad = free = after_ok = 0
    char_words = forges = hdr_writes = puns = 0
    bad_dest = conv_note = ""
    stack = [(node, None)]
    while stack:
        x, parent = stack.pop()
        if isinstance(x, dict):
            nkind = x.get("kind")
            if nkind == "CallExpr" and callee_name(x) == PTR_EXIT:
                inner = x.get("inner") or ()
                if len(inner) > 1 and passes_char_pointer(inner[1], typedefs):
                    char_words += 1
            elif nkind in ("InitListExpr", "CompoundLiteralExpr") and is_str_type(spelled(x), typedefs, 0):
                forges += 1
            elif nkind == "MemberExpr" and is_str_write(x, parent, typedefs):
                hdr_writes += 1
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
            elif kind == PUN_CAST_KIND:
                op = operand(x)
                if op is not None and is_str_pointer_pun(spelled(x), spelled(op), typedefs):
                    puns += 1
            children = x.values()
            child_parent = parent if nkind == "ParenExpr" else x
        else:
            children = x
            child_parent = parent
        stack.extend((v, child_parent) for v in children if isinstance(v, (dict, list)))
    return EMPTY._replace(
        casts=casts,
        bad_casts=bad,
        conv_free=free,
        conv_after_ok=after_ok,
        bad_dest=bad_dest,
        conv_note=conv_note,
        char_words=char_words,
        forges=forges,
        hdr_writes=hdr_writes,
        puns=puns,
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
        typedefs = Typedefs(
            (n["name"], n["type"]["qualType"])
            for n in tu.get("inner", ())
            if n.get("kind") == "TypedefDecl" and "qualType" in (n.get("type") or {})
        )
        typedefs.str_base = type_parts(STR_TYPE, typedefs)[0]
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
    ap.add_argument("address_casts")
    ap.add_argument("--scan-cache", metavar="FILE", help="reuse the decl table in FILE (for the test)")
    args = ap.parse_args()
    clang, rt_dir, addr_path = args.clang, args.runtime_dir, args.address_casts

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

    for key in sorted(decls):
        d = decls[key]
        if d.char_words:
            problems.append(
                "%s:%s passes a char pointer to %s (rule 2: a String leaves the runtime through "
                "sprout_str_word, which checks its header)" % (key[0], key[1], PTR_EXIT)
            )
        if "%s:%s" % key in WRITERS:
            continue
        if d.forges:
            problems.append(
                "%s:%s constructs a %s outside the writers (rule 3: only %s do)"
                % (key[0], key[1], STR_TYPE, WRITER_LIST)
            )
        if d.puns:
            problems.append(
                "%s:%s casts between a %s pointer and another pointer type outside the writers (rule 3: "
                "only %s may; void* is exempt)" % (key[0], key[1], STR_TYPE, WRITER_LIST)
            )
        if d.hdr_writes:
            problems.append(
                "%s:%s writes %s.%s outside the writers (rule 3: it may be read, not assigned or address-taken)"
                % (key[0], key[1], STR_TYPE, STR_FIELD)
            )

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

    for key in sorted(decls):
        d = decls[key]
        have = sites(d)
        if have and not allowed(key):
            problems.append(
                "%s:%s has %d pointer-to-integer cast(s) outside the exits and ADDRESS_CASTS; "
                "use sprout_str_word/sprout_ptr_word, or list it in ADDRESS_CASTS "
                "if it is address arithmetic%s" % (key[0], key[1], have, sites_note(d))
            )

    if problems:
        for p in problems:
            sys.stderr.write("runtime-word-casts: %s\n" % p)
        return 1
    print("runtime-word-casts: ok (%d ADDRESS_CASTS functions)" % len(addr))
    return 0


if __name__ == "__main__":
    sys.exit(main())
