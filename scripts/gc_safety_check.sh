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
#   2  — runtime file missing, or brace tracking drifted (always, with or without --strict)
#
# Usage:
#   scripts/gc_safety_check.sh            # informational (always exits 0)
#   scripts/gc_safety_check.sh --strict   # exit 1 if any issues found
#   scripts/gc_safety_check.sh --verbose  # also print clean-function names
#
# GC_SAFETY_RUNTIME overrides the file checked (the test points it at fixtures).
set -euo pipefail

RUNTIME="${GC_SAFETY_RUNTIME:-runtime/sprout_runtime.c}"
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
    in_block = 0; fatal = 0; cur_fn = ""
}

# ── Code-only view of one line ──────────────────────────────────────────────
# Drops char/string literals (with backslash escapes), // comments and /* */
# comments. in_block carries an open block comment across lines, so call this
# exactly once per input line, in order.
function strip_literals(line,   i, n, ch, nx, q, out, p) {
    out = ""; n = length(line); i = 1
    while (i <= n) {
        if (in_block) {
            p = index(substr(line, i), "*/")
            if (!p) return out
            i += p + 1; in_block = 0; out = out " "
            continue
        }
        ch = substr(line, i, 1); nx = substr(line, i + 1, 1)
        if (ch == "/" && nx == "/") return out
        if (ch == "/" && nx == "*") { in_block = 1; i += 2; continue }
        if (ch == "\"" || ch == "\x27") {
            q = ch; i++
            while (i <= n) {
                ch = substr(line, i, 1)
                if (ch == "\\") { i += 2; continue }
                if (ch == q) break
                i++
            }
            i++; out = out " "
            continue
        }
        out = out ch; i++
    }
    return out
}

# ── Net brace count for one (already stripped) line ─────────────────────────
function net_braces(code,   i, ch, net) {
    net = 0
    for (i = 1; i <= length(code); i++) {
        ch = substr(code, i, 1)
        if (ch == "{") net++
        else if (ch == "}") net--
    }
    return net
}

# ── Does this stripped line start a top-level function definition? ──────────
# Simple return types: void int long_long char* size_t _Bool long.
# Excludes struct/typedef returns (SproutObj*, etc.) — mirrors Python regex.
function is_fn_start(code) {
    return (code ~ /^(static[[:space:]]+)?(const[[:space:]]+)?(unsigned[[:space:]]+)?(void|int|_Bool|size_t|(long[[:space:]]+long)|char[[:space:]]*\*?|long)[[:space:]]/ &&
            code ~ /[a-zA-Z_][a-zA-Z0-9_]*[[:space:]]*\([^)]*\)[[:space:]]*\{/)
}

# Brace tracking went wrong: stop, loudly. A silent miscount folds later
# functions into one body and leaves them unchecked.
function drift(msg) {
    printf "ERROR: %s: brace tracking drifted: %s\n", runtime, msg > "/dev/stderr"
    fatal = 2
    exit 2
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
{ code = strip_literals($0) }

!in_fn {
    if (is_fn_start(code)) {
        in_fn = 1; fn_lineno = NR; cur_fn = fn_name_from_sig($0)
        body = $0 "\n"
        depth = net_braces(code)
        if (depth < 0) drift(sprintf("line %d: depth %d on the opening line of %s()", NR, depth, cur_fn))
        if (depth == 0) { analyze(body, fn_lineno); in_fn = 0; body = "" }
    }
    next
}

in_fn {
    # A column-0 definition inside a body: the previous body never closed.
    if (is_fn_start(code))
        drift(sprintf("line %d starts %s() while %s() (line %d) is still open", \
                      NR, fn_name_from_sig($0), cur_fn, fn_lineno))
    body = body $0 "\n"
    depth += net_braces(code)
    if (depth < 0) drift(sprintf("line %d: depth %d inside %s() (line %d)", NR, depth, cur_fn, fn_lineno))
    if (depth == 0) { analyze(body, fn_lineno); in_fn = 0; body = "" }
}

END {
    if (fatal) exit fatal
    if (in_fn)
        drift(sprintf("end of file reached inside %s() (line %d)", cur_fn, fn_lineno))
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
