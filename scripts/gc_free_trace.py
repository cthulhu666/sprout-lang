# scripts/gc_free_trace.py
#
# Reusable lldb diagnostic for GC use-after-free bugs in the typed/direct
# codegen paths.  Records every register_managed_ptr (alloc) and
# sprout_gc_free_payload (free) with a backtrace, and STOPS the instant a
# function entered with a pointer argument that was already freed (a live use
# of freed memory).  Prints the victim's full alloc/free lineage.
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
# <watch_fn> is the function whose FIRST pointer arg (x0 on arm64) is the value
# to check against the freed-set on every entry (e.g. a match-dispatch fn that
# aborts on a corrupted scrutinee).

import lldb

_hist = {}        # payload_ptr -> [(event, order, kind, [frames])]
_order = [0]
_KINDS = {1: "OBJ", 2: "CSTR", 3: "CLOSURE", 4: "VECTOR",
          5: "MAP", 6: "RANGE", 7: "REF", 8: "TUPLE"}


def _frames(thread, n=9):
    out = []
    for i in range(min(n, thread.GetNumFrames())):
        f = thread.GetFrameAtIndex(i)
        out.append(f.GetFunctionName() or hex(f.GetPC()))
    return out


def _on_alloc(frame, bp_loc, d):
    ptr = frame.FindVariable("ptr").GetValueAsUnsigned()
    kind = frame.FindVariable("kind").GetValueAsUnsigned()
    if ptr:
        _order[0] += 1
        _hist.setdefault(ptr, []).append(("alloc", _order[0], _KINDS.get(kind, str(kind)), _frames(frame.thread)))
    return False


def _on_free(frame, bp_loc, d):
    node = frame.FindVariable("node").GetValueAsUnsigned()
    if not node:
        return False
    err = lldb.SBError()
    proc = frame.thread.process
    ptr = proc.ReadUnsignedFromMemory(node, 8, err)        # node->ptr at offset 0
    kind = proc.ReadUnsignedFromMemory(node + 8, 4, err)   # node->kind
    if err.Success() and ptr:
        _order[0] += 1
        _hist.setdefault(ptr, []).append(("free", _order[0], _KINDS.get(kind, str(kind)), _frames(frame.thread)))
    return False


def _on_watch(frame, bp_loc, d):
    arg = frame.FindRegister("x0").GetValueAsUnsigned()
    evs = _hist.get(arg)
    if evs and any(e[0] == "free" for e in evs):
        print("\n=== USE-AFTER-FREE: watched fn entered with freed ptr 0x%x ===" % arg)
        for ev, o, k, frs in evs:
            print("--- %-5s order=%d kind=%s" % (ev, o, k))
            for fn in frs:
                print("      " + fn)
        return True   # STOP
    return False


def _cmd_gctrace(debugger, command, result, internal_dict):
    watch_fn = command.strip()
    if not watch_fn:
        print("usage: gctrace <watch_fn_name>")
        return
    target = debugger.GetSelectedTarget()
    for name, cb in (("register_managed_ptr", "_on_alloc"),
                     ("sprout_gc_free_payload", "_on_free"),
                     (watch_fn, "_on_watch")):
        bp = target.BreakpointCreateByName(name)
        bp.SetScriptCallbackFunction("gc_free_trace." + cb)
    print("gctrace armed: alloc/free recorded; will stop when %s sees a freed ptr" % watch_fn)


def __lldb_init_module(debugger, internal_dict):
    debugger.HandleCommand("command script add -f gc_free_trace._cmd_gctrace gctrace")
