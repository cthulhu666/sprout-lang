# Native Memory Management V1 Draft

This document outlines a pragmatic hosted-runtime memory-management roadmap for
Sprout, centered on a garbage-collected native runtime profile. It is a design
and planning document, not part of the normative language spec.

This draft does not make garbage collection part of the Sprout core-language
contract. See `docs/memory-management-abstraction.md` for the cross-cutting
design constraint that core-language memory management should remain abstract
unless Sprout explicitly adopts visible resource or lifetime semantics.

## Problem Statement

Interpreter execution inherits Python's memory management. Native execution
does not. The current native runtime allocates Sprout values manually and keeps
them for process lifetime in most language-level paths.

Today this is acceptable for a small prototype backend, but it leaves several
important gaps:

1. Long-running native programs will accumulate unreachable values.
2. Allocation cost and retention behavior are not visible enough to measure.
3. Runtime representation work and memory-management work are too tightly
   coupled around scattered `malloc` calls.
4. Native performance and latency claims will remain incomplete until the
   backend has an explicit reclamation strategy.

## Goals

1. Introduce a real reclamation strategy for native Sprout values.
2. Preserve Sprout's current language surface and beginner-friendly ergonomics.
3. Keep the first memory-management milestone incremental and reviewable.
4. Make allocation behavior observable before adding a collector.
5. Prefer predictable, bounded pause behavior over maximum peak throughput.

## Non-Goals

1. No language-surface change in the first memory-management milestone.
2. No ownership/borrowing or linear-type design in this roadmap.
3. No moving collector in the first slice.
4. No concurrent or generational GC in the first slice.
5. No attempt to solve all runtime performance issues through GC alone.

## Current State

Interpreter mode:
- Uses Python objects for ADTs, closures, vectors, maps, bytes, builders, and
  tuples.
- Reclamation is delegated to Python's runtime.

Native mode:
- ADTs are boxed into heap-allocated `SproutObj` values.
- Closures and partial application environments allocate through the LLVM
  backend.
- Vectors, maps, bytes, builders, and many helper buffers allocate manually in
  the generated native runtime.
- Some helper-local buffers are freed explicitly.
- A first non-moving mark-sweep collector now exists in the native runtime,
  with both exit-time collection and a default threshold-triggered
  mid-execution policy.
- Fuller root coverage, validation, and threshold tuning remain follow-up work.

## Why GC Is The Default Direction For This Profile

Sprout already leans toward:

- immutable values,
- higher-order functions,
- closures,
- persistent ADTs and collection values,
- simple beginner-facing semantics.

That combination fits GC much better than manual management, explicit
ownership, or whole-language linearity.

The main rationale is not fashion but runtime shape: unrestricted sharing and
closure capture make deterministic non-GC lifetime tracking much more invasive
at the language and compiler levels.

## V1 Collector Shape

The recommended v1 collector is:

- precise enough to understand Sprout-managed object layouts,
- non-moving,
- stop-the-world,
- mark-sweep,
- single-threaded.

### Why Non-Moving In V1

The current native backend treats boxed values as raw handles and threads them
through runtime calls freely. A moving collector would require a much stricter
handle discipline and root-updating strategy than the runtime currently has.

A non-moving collector is less ambitious and fits the current representation
better.

### Why Mark-Sweep In V1

Mark-sweep gives Sprout the missing capability first: reclaim unreachable
values. It also avoids introducing copying barriers, semispace constraints, or
promotion policies before the runtime has enough instrumentation to justify
them.

### V1 Tradeoff

The tradeoff is pause behavior. A first stop-the-world collector is not meant
to be Sprout's final latency story. The point of v1 is correctness,
observability, and representation discipline, not maximum pause robustness.

## Managed Heap Boundary

Before a collector is added, native allocation should move behind a dedicated
runtime-managed allocation layer.

Example direction:

- `sprout_alloc_obj(...)`
- `sprout_alloc_vector(...)`
- `sprout_alloc_map(...)`
- `sprout_alloc_bytes(...)`
- `sprout_alloc_builder(...)`

The exact API can change, but the design goal should not: separate
Sprout-managed heap values from helper-local temporary buffers.

This boundary enables:

1. allocation accounting,
2. central object registration,
3. future mark bits/headers,
4. later sweep logic,
5. future collector experiments without rewriting every runtime helper again.

## Root Model

A first GC design needs an explicit notion of roots.

Likely root classes:

1. Native global Sprout values, including runtime-initialized top-level lets.
2. Live call-frame locals and temporaries that hold boxed Sprout values.
3. Closure environments reachable from live function values.
4. Runtime-managed container payloads that recursively reference other
   Sprout-managed values.

The first milestone should make this root model explicit in the runtime and
backend design before implementing sweep logic.

## V1 Implementation Plan

V1 should proceed in stages.

### Stage 0: Allocation Visibility

Add lightweight instrumentation:

- allocation counters by runtime value kind,
- optional debug logging for object creation,
- a small stress test that allocates many short-lived ADTs/closures.

Status: completed.

This stage exists to measure the runtime before designing policy.

### Stage 1: Centralized Allocation

Route Sprout-managed allocations through a single runtime layer.

Scope:
- ADTs first,
- closure environments second,
- vectors/maps/bytes/builders after that.

Expected outcome:
- fewer ad hoc allocation sites,
- explicit distinction between managed values and helper-local buffers,
- easier runtime auditing.

Status: completed for ADTs, closure environments, and the current
vector/map/bytes/builder runtime value types.

### Stage 2: Heap Metadata

Add the minimal metadata required for a collector.

Examples:
- object kind tag,
- mark bit,
- managed heap linkage,
- layout information sufficient for traversal.

The implementation does not need a perfect generalized object model on day one,
but it does need enough structure to walk all managed references correctly.

Status: completed for the current managed heap kinds, with per-kind traversal
hooks in the native runtime.

### Stage 3: First Stop-The-World Mark-Sweep Collector

Add:

1. root discovery,
2. marking from roots,
3. sweeping unreachable managed objects,
4. validation tests for reclamation safety.

The collector should start simple and prioritize correctness over heuristics.

Status: partially completed.

Current implementation status:
- the native runtime can register managed roots for scalar runtime globals,
- the native runtime can also register aggregate global roots via conservative
  storage scanning,
- the native runtime now has a temporary shadow-root stack for backend-managed
  live values,
- the backend currently uses that stack for function/lambda parameters,
  closure construction, tuple packing, constructor packing, and pattern-bound
  locals in match/destructuring paths,
- native runtime helpers now also root managed arguments and freshly allocated
  managed pointers across the main allocation-heavy helper families, including
  vectors/maps, bytes/builders, crypto helpers, and network result wrappers,
- the main native runtime helper surface has now been audited for this
  root-coverage class of bug, with only narrower path-specific gaps left,
- the collector marks from those roots and sweeps managed heap nodes,
- collection currently runs at process exit via `atexit(...)`,
- the native profile now also enables threshold-triggered mid-execution
  collection by default using a fixed managed-node threshold,
- the current default threshold is `1024` managed heap nodes,
- `SPROUT_GC_THRESHOLD=<positive-int>` overrides that threshold and
  `SPROUT_GC_THRESHOLD=off` disables in-process collection,
- opt-in debug logging exists for validation and now reports the active
  threshold, live managed-node count, and per-cycle elapsed time with each
  cycle,
- debug allocation reporting now includes a `gc_swept` count.

Remaining work before this stage can be considered fully complete:
- extend the current shadow-root strategy to all live mid-execution values that
  can survive allocations,
- keep validating and tuning the current threshold-triggered mid-execution
  policy,
- add stronger reclamation-focused tests once collection can run mid-program.

## V2 Direction

V2 should be explicitly about making GC more robust only after the baseline v1
collector is implemented and measured.

Likely v2 directions:

- incremental marking to reduce pauses,
- generational policy if young-object churn dominates,
- better collector diagnostics and pause measurements,
- specialized treatment of tiny values where profiling justifies it,
- allocation fast paths and related throughput work.

V2 should remain measurement-driven. Sprout should not commit up front to
concurrent or moving GC until the v1 runtime has enough instrumentation to
justify that complexity.

## Temporary Buffer Policy

Not every native allocation should become GC-managed.

The runtime should keep explicit manual cleanup for helper-local temporaries
such as:

- transient HTTP request/response buffers,
- temporary JSON/string assembly buffers,
- socket helper scratch buffers,
- parser/codec intermediate buffers that do not escape as Sprout values.

GC should manage language-visible values. Runtime-local work buffers should
remain explicitly owned where practical.

## Diagnostics and Observability

The first milestone should also improve runtime visibility.

Recommended debug-mode outputs:

- total allocations by kind,
- total bytes allocated by kind when measurable,
- live managed object count after collection,
- collection count and sweep count,
- optional maximum pause timing in debug builds.

Current measurement workflow:

- `mise exec -- just measure-gc-thresholds` compiles a small set of fixed
  allocation-heavy native workloads and compares `SPROUT_GC_THRESHOLD`
  settings including `off`, `1`, `128`, `1024`, and `4096`.
- The summary reports cycle counts, sweep totals, max live heap, and total/max
  `elapsed_us` so threshold tuning can stay measurement-driven instead of
  guess-based.

These should remain implementation diagnostics, not user-facing language
features.

## Risks

1. Root discovery will be the hardest correctness problem.
2. It is easy to accidentally mix managed and unmanaged allocations if the
   runtime boundary is not enforced early.
3. A first stop-the-world collector may be good enough for correctness but not
   for low-latency server workloads.
4. Representation work in closures and collections may need to move earlier
   than expected if traversal hooks are too ad hoc.

## Recommended First Implementation Slice

The next concrete slice should be:

1. add allocation counters and debug accounting,
2. centralize ADT allocation behind a managed runtime helper,
3. centralize closure-environment allocation behind the same managed boundary
   or a compatible companion boundary,
4. add focused tests proving the managed-allocation path is exercised,
5. measure before committing to the exact collector API.

This keeps the first step vertical and useful without prematurely landing a
half-designed collector.

## Summary Roadmap

### V1

1. add allocation visibility and debug accounting,
2. centralize managed allocation for Sprout values,
3. formalize root enumeration points,
4. ship a simple non-moving stop-the-world mark-sweep collector,
5. validate correctness with focused collector and regression coverage.

### V2

1. benchmark pause behavior and allocation-heavy programs,
2. improve pause behavior and throughput only where measurements justify it,
3. evaluate incremental or generational follow-up work,
4. revisit representation-specific optimizations once the basic GC is stable.
