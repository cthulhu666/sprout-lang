# scripts/gc_free_trace.py
#
# Reusable lldb diagnostic for GC use-after-free bugs.  Records every
# sprout_gc_trace_alloc (alloc) and sprout_gc_trace_free (free) event with a
# backtrace, and STOPS when a watched function is entered with a pointer that
# was already freed.  Prints the victim's full alloc/free lineage.
#
# The anchors (sprout_gc_trace_alloc / sprout_gc_trace_free) fire only when the
# binary is run under SPROUT_GC_STRESS=1 or SPROUT_GC_LINEAGE=1; both anchors
# receive the PAYLOAD pointer as arg0 (x0 on arm64, rdi on x86_64).
#
# WHY this exists: GC UAFs of the rooting class are timing-sensitive and heap
# addresses are NOT stable across runs (even with ASLR off), so hardcoding an
# address from a prior run fails.  This traces within a SINGLE process.
#
# Usage (run under SPROUT_GC_STRESS=1 to make the bug deterministic):
#   just gc-trace <file.spr> <watch_fn>
# or directly:
#   lldb -b -o "settings set target.env-vars SPROUT_GC_STRESS=1" \
#           -o "command script import scripts/gc_free_trace.py" \
#           -o "gctrace <watch_fn>" -o "run" -o "quit" <binary>
#
# <watch_fn> is the function whose FIRST pointer arg is the value to check
# against the freed-set on every entry (e.g. a match-dispatch fn that aborts
# on a corrupted scrutinee).

import lldb

_hist = {}        # payload_ptr -> [(event, order, kind, [frames])]
_order = [0]
# Maps SproutHeapKind enum values to human-readable names.
_KINDS = {0: "FREE", 1: "OBJ", 2: "CLOSURE", 3: "VECTOR", 4: "MAP",
          5: "BYTES", 6: "BUILDER", 7: "TUPLE", 8: "RANGE", 9: "REF",
          10: "CSTR", 0xFF: "POISON"}


def _frames(thread, n=9):
    out = []
    for i in range(min(n, thread.GetNumFrames())):
        f = thread.GetFrameAtIndex(i)
        out.append(f.GetFunctionName() or hex(f.GetPC()))
    return out


def _arg0(frame):
    """Return the first integer argument for both arm64 (x0) and x86_64 (rdi)."""
    reg = frame.FindRegister("x0")
    if reg.IsValid():
        return reg.GetValueAsUnsigned()
    reg = frame.FindRegister("rdi")
    if reg.IsValid():
        return reg.GetValueAsUnsigned()
    return 0


def _read_header(frame, ptr):
    """Read the 8-byte inline header at (ptr - 8); returns (header, ok)."""
    if not ptr:
        return 0, False
    err = lldb.SBError()
    h = frame.thread.process.ReadUnsignedFromMemory(ptr - 8, 8, err)
    return h, err.Success()


def _on_alloc(frame, bp_loc, d):
    # sprout_gc_trace_alloc(void* payload): arg0 = payload pointer.
    ptr = _arg0(frame)
    if ptr:
        h, ok = _read_header(frame, ptr)
        kind_bits = (h & 0xFF) if ok else 0
        _order[0] += 1
        _hist.setdefault(ptr, []).append(
            ("alloc", _order[0], _KINDS.get(kind_bits, str(kind_bits)), _frames(frame.thread)))
    return False


def _on_free(frame, bp_loc, d):
    # sprout_gc_trace_free(void* payload): arg0 = payload pointer.
    ptr = _arg0(frame)
    if not ptr:
        return False
    h, ok = _read_header(frame, ptr)
    kind_bits = (h & 0xFF) if ok else 0
    _order[0] += 1
    _hist.setdefault(ptr, []).append(
        ("free", _order[0], _KINDS.get(kind_bits, str(kind_bits)), _frames(frame.thread)))
    return False


def _on_watch(frame, bp_loc, d):
    arg = _arg0(frame)
    evs = _hist.get(arg)
    if evs and any(e[0] == "free" for e in evs):
        print("\n=== USE-AFTER-FREE: watched fn entered with freed ptr 0x%x ===" % arg)
        for ev, o, k, frs in evs:
            print("--- %-5s order=%d kind=%s" % (ev, o, k))
            for fn in frs:
                print("      " + fn)
        return True   # STOP
    return False


_VALID_TAGS = [None]   # set by gctracetag: only these tags are legal for the watched scrutinee


def _on_watch_tag(frame, bp_loc, d):
    # Content check: fire when the watched fn is entered with a scrutinee whose
    # tag is not a legal one.  Tag lives in the OBJ header at (payload - 8):
    #   header aux = (tag << 4) | arity  →  tag = (header >> 14) >> 4.
    # Also detects lineage poison (kind bits == 0xFF).
    arg = _arg0(frame)
    if not arg:
        return False
    h, ok = _read_header(frame, arg)
    if not ok:
        return False
    kind_bits = h & 0xFF
    if kind_bits == 0xFF:  # SPROUT_GC_POISON — corpse from lineage mode
        print("\n=== CORRUPT SCRUTINEE: poison corpse at 0x%x ===" % arg)
        evs = _hist.get(arg)
        if not evs:
            print("  (no recorded alloc/free history for this address)")
        else:
            for ev, o, k, frs in evs:
                print("--- %-5s order=%d kind=%s" % (ev, o, k))
                for fn in frs:
                    print("      " + fn)
        return True   # STOP
    tag = (h >> 14) >> 4   # extract tag from OBJ header aux
    if tag in _VALID_TAGS[0]:
        return False
    print("\n=== CORRUPT SCRUTINEE: watched fn entered with ptr 0x%x, tag=%d (legal: %s) ===" % (arg, tag, sorted(_VALID_TAGS[0])))
    evs = _hist.get(arg)
    if not evs:
        print("  (no recorded alloc/free history for this address — not a managed ptr or pre-trace)")
    else:
        for ev, o, k, frs in evs:
            print("--- %-5s order=%d kind=%s" % (ev, o, k))
            for fn in frs:
                print("      " + fn)
    return True   # STOP


def _cmd_gctracetag(debugger, command, result, internal_dict):
    parts = command.split()
    if len(parts) != 2:
        print("usage: gctracetag <watch_fn_name> <comma-separated-legal-tags>")
        return
    watch_fn = parts[0]
    _VALID_TAGS[0] = set(int(t) for t in parts[1].split(","))
    target = debugger.GetSelectedTarget()
    for name, cb in (("sprout_gc_trace_alloc", "_on_alloc"),
                     ("sprout_gc_trace_free",  "_on_free"),
                     (watch_fn, "_on_watch_tag")):
        bp = target.BreakpointCreateByName(name)
        bp.SetScriptCallbackFunction("gc_free_trace." + cb)
    print("gctracetag armed: alloc/free recorded; stop when %s sees tag not in %s" % (watch_fn, sorted(_VALID_TAGS[0])))


def _cmd_gctrace(debugger, command, result, internal_dict):
    watch_fn = command.strip()
    if not watch_fn:
        print("usage: gctrace <watch_fn_name>")
        return
    target = debugger.GetSelectedTarget()
    for name, cb in (("sprout_gc_trace_alloc", "_on_alloc"),
                     ("sprout_gc_trace_free",  "_on_free"),
                     (watch_fn, "_on_watch")):
        bp = target.BreakpointCreateByName(name)
        bp.SetScriptCallbackFunction("gc_free_trace." + cb)
    print("gctrace armed: alloc/free recorded; will stop when %s sees a freed ptr" % watch_fn)


def __lldb_init_module(debugger, internal_dict):
    debugger.HandleCommand("command script add -f gc_free_trace._cmd_gctrace gctrace")
    debugger.HandleCommand("command script add -f gc_free_trace._cmd_gctracetag gctracetag")
