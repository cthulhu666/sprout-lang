#!/usr/bin/env bash
# GC safety linter for runtime/sprout_runtime.c (Python-free port).
#
# Detects C functions where sprout_gc_maybe_collect_threshold() fires while
# const char*/char* locals or parameters are live but not yet registered
# as GC roots.
#
# Rooting mechanisms recognized:
#   SPROUT_GC_PUSH_PTR_LOCAL(var) / SPROUT_GC_PUSH_I64_LOCAL(var)
#   SPROUT_HANDLE(name, ...)
#
# Exit codes:
#   0  — no findings (or findings exist but --strict was not given)
#   1  — findings present and --strict was given
#
# Usage:
#   scripts/gc_safety_check.sh            # informational (always exits 0)
#   scripts/gc_safety_check.sh --strict   # exit 1 if any issues found
#   scripts/gc_safety_check.sh --verbose  # also print clean-function names
set -euo pipefail

RUNTIME="runtime/sprout_runtime.c"
STRICT=0; VERBOSE=0
for arg in "$@"; do
    case "$arg" in --strict) STRICT=1 ;; --verbose) VERBOSE=1 ;; esac
done

if [ ! -f "$RUNTIME" ]; then
    echo "ERROR: $RUNTIME not found" >&2; exit 2
fi

# awk program — POSIX-compatible (no gawk extensions required).
#
# Algorithm (matches Python gc_safety_check.py):
# 1. Find function definitions whose return type is one of the "simple" C
#    types (void, int, long long, char*, size_t, _Bool, long).  Struct/typedef
#    return types (SproutObj*, VectorVal* …) are excluded — same as Python.
# 2. Accumulate the function body via brace counting.
# 3. For functions that contain sprout_gc_maybe_collect_threshold():
#    a. Split body at the FIRST GC call → before/after.
#    b. Extract char* param names from the signature line.
#    c. Extract char* local variable names from the before-portion.
#    d. Remove variables already rooted in the before-portion.
#    e. Report any remaining variables that appear (as whole words) in after.
awk -v strict="$STRICT" -v verbose="$VERBOSE" -v runtime="$RUNTIME" '
BEGIN {
    GC = "sprout_gc_maybe_collect_threshold()"
    in_fn = 0; depth = 0; body = ""; fn_lineno = 0
    issues = 0; fns_total = 0
}

# ── Net brace count for one line (ignores strings/comments, good enough) ─────
function net_braces(line,   i, ch, net) {
    net = 0
    for (i = 1; i <= length(line); i++) {
        ch = substr(line, i, 1)
        if (ch == "{") net++
        else if (ch == "}") net--
    }
    return net
}

# ── Extract function name: last identifier before "(" ─────────────────────
function fn_name_from_sig(sig,   pos, pre, name) {
    pos = index(sig, "(")
    if (!pos) return "?"
    pre = substr(sig, 1, pos - 1)
    if (match(pre, /[a-zA-Z_][a-zA-Z0-9_]*[[:space:]]*$/)) {
        name = substr(pre, RSTART, RLENGTH)
        gsub(/[[:space:]]/, "", name)
        return name
    }
    return "?"
}

# ── Extract "char *ident" names into global array cvars[] ─────────────────
function extract_char_ptrs(text,   i, n, line, pos, rest, v) {
    delete cvars
    n = split(text, _L, "\n")
    for (i = 1; i <= n; i++) {
        line = _L[i]
        while (match(line, /char[[:space:]]*\*[[:space:]]*/)) {
            pos = RSTART + RLENGTH
            rest = substr(line, pos)
            if (match(rest, /^[a-zA-Z_][a-zA-Z0-9_]*/)) {
                v = substr(rest, 1, RLENGTH)
                cvars[v] = 1
            }
            line = substr(line, pos)
            if (!length(line)) break
        }
    }
}

# ── Whole-word occurrence test ─────────────────────────────────────────────
function word_in(w, text,   re) {
    re = "(^|[^a-zA-Z0-9_])" w "([^a-zA-Z0-9_]|$)"
    return (text ~ re)
}

# ── Analyze one complete function body ─────────────────────────────────────
function analyze(body, lineno,   gc_pos, sig, fn, before, after, body_before, v, fn_issues) {
    fns_total++
    gc_pos = index(body, GC)
    if (!gc_pos) return

    # Signature is the first line of the body
    sig = substr(body, 1, index(body, "\n") - 1)
    fn  = fn_name_from_sig(sig)

    # Split body at the GC call
    before = substr(body, 1, gc_pos - 1)
    after  = substr(body, gc_pos + length(GC))

    # Collect char* params from signature (text between the first parens)
    delete all_vars
    if (match(sig, /\([^)]*\)/)) {
        extract_char_ptrs(substr(sig, RSTART + 1, RLENGTH - 2))
        for (v in cvars) all_vars[v] = 1
    }

    # Collect char* locals declared in body_before (skip the sig line)
    body_before = (index(before, "\n") ? substr(before, index(before, "\n") + 1) : "")
    extract_char_ptrs(body_before)
    for (v in cvars) all_vars[v] = 1

    # Remove variables already rooted before the GC call
    for (v in all_vars) {
        if (index(before, "SPROUT_GC_PUSH_PTR_LOCAL(" v ")") > 0 ||
            index(before, "SPROUT_GC_PUSH_I64_LOCAL(" v ")") > 0 ||
            index(before, "SPROUT_HANDLE(") > 0)
            delete all_vars[v]
    }

    # Report unrooted vars that appear after the GC call
    fn_issues = 0
    for (v in all_vars) {
        if (word_in(v, after)) {
            fn_issues++
            issues++
            printf "  %s ~line %d: %s(): \"%s\" (char* heap ptr) used after %s\n", \
                runtime, lineno, fn, v, "sprout_gc_maybe_collect_threshold()"
        }
    }
    if (!fn_issues && verbose)
        print "  OK  " fn "()"
}

# ── Main: detect function starts ───────────────────────────────────────────
!in_fn {
    # Match simple return types: void int long_long char* size_t _Bool long
    # Excludes struct/typedef returns (SproutObj*, etc.) — mirrors Python regex.
    if ($0 ~ /^(static[[:space:]]+)?(const[[:space:]]+)?(unsigned[[:space:]]+)?(void|int|_Bool|size_t|(long[[:space:]]+long)|char[[:space:]]*\*?|long)[[:space:]]/ &&
        $0 ~ /[a-zA-Z_][a-zA-Z0-9_]*[[:space:]]*\([^)]*\)[[:space:]]*\{/) {
        in_fn = 1; fn_lineno = NR
        body = $0 "\n"
        depth = net_braces($0)
        if (depth == 0) { analyze(body, fn_lineno); in_fn = 0; body = "" }
    }
    next
}

in_fn {
    body = body $0 "\n"
    depth += net_braces($0)
    if (depth == 0) { analyze(body, fn_lineno); in_fn = 0; body = "" }
}

END {
    if (issues > 0) {
        printf "%s: GC safety issues in %s (%d found):\n",
            (strict ? "FAIL" : "WARN"), runtime, issues
        if (!strict)
            print "\n  NOTE: re-run with --strict to treat these as errors."
        exit (strict ? 1 : 0)
    }
    print "GC safety OK — " fns_total " runtime functions checked, 0 issues."
}
' "$RUNTIME"
