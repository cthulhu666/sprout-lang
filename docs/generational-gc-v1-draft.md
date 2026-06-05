# Generational GC V1 — Implementation Plan

> **Status (2026-05-28): deprioritized.** Profiling after the type-aware GC
> rooting fix (N-queens P0, see
> [nqueens-optim-iteration-2026-05-28.md](nqueens-optim-iteration-2026-05-28.md))
> showed `register_managed_ptr` is only ~1% of CPU on N-queens — not the
> bottleneck this draft assumes. The current bottleneck (~44% of CPU) is the
> function-call boundary into `sprout_gc_push_i64_root` / `sprout_gc_pop_roots`,
> which a per-`ManagedNode` generational split does not address.
>
> Successor direction: a **bump-allocated nursery** (no per-object metadata;
> objects identified by address-range membership) is the higher-leverage
> variant for allocation-heavy workloads. See N-queens P4 in BACKLOG.md. This
> document remains useful for the empirical baseline and write-barrier
> analysis; do **not** treat the implementation plan below as the intended
> path.

This document captures the design, empirical background, and advisor-reviewed
implementation plan for a generational garbage collector for the Sprout native
runtime. It supersedes the in-conversation design discussion from 2026-05-27.

---

## Motivation

N-queens at N=12 (14,200 solutions) runs at ~1,620 ms in Sprout — Python speed
despite compiling to native code via clang -O2. The binary retires **29 billion
instructions** in that time. The performance profile traces to two layers:

1. **Per-allocation overhead**: every allocation calls `register_managed_ptr`,
   which inserts a `ManagedNode` into the `g_heap_index[131071]` hash table and
   prepends it to `g_heap_nodes`. This involves a hash computation, pointer
   write, and counter increment on every `vector_get` (which wraps the result
   in a `Just` ADT heap object even though it is immediately pattern-matched
   and discarded). At N=12 this happens millions of times.

2. **GC cycle cost**: mark-and-sweep cycles over the entire heap.

These two factors have different leverage — see the section below.

---

## Empirical Baseline: The Threshold Experiment

Raising `SPROUT_GC_THRESHOLD` to 1,000,000 (preventing virtually all GC
cycles) reduced N-queens N=12 from ~1,740 ms to ~1,670 ms — a **4% reduction**.

**Key conclusion**: GC *cycle* cost is only ~4% of total N-queens time. The
bottleneck is the per-allocation overhead of `register_managed_ptr`, not the
mark-and-sweep phase itself.

**Implication for generational GC**:

- Generational GC primarily reduces cycle cost by making minor GC O(young)
  instead of O(heap). Its theoretical ceiling for N-queens is therefore
  roughly 4% × (minor-GC-efficiency-factor).
- Expected speedup for N-queens: **10–20%** at best.
- Expected speedup for long-lived programs (compiler bootstrap, servers): much
  larger — these programs accumulate a large old-gen and pay for full sweeps
  repeatedly; minor-only cycles amortize that cost well.

The other improvement — **unboxed struct returns for Maybe-returning externs**
— directly eliminates the per-allocation overhead and is expected to give 3–5×
speedup on allocation-heavy workloads like N-queens. That improvement should
be implemented first; generational GC is complementary and longer-term.

---

## Current GC Architecture

### Data structures

```c
static ManagedNode* g_heap_nodes = NULL;       // single linked list, all objects
static ManagedNode* g_heap_index[131071];      // hash table (ptr → ManagedNode*)

typedef struct ManagedNode {
  void*          ptr;
  SproutHeapKind kind;
  size_t         aux_slots;
  int            marked;
  ManagedNode*   next;
  ManagedNode*   hash_next;
} ManagedNode;
```

### Key globals

```c
static long long g_managed_heap_count = 0;       // total live objects
static long long g_managed_alloc_since_gc = 0;   // allocations since last cycle
static long long g_gc_threshold = 4096;           // trigger threshold (total objects)
```

### GC cycle

`sprout_gc_collect_with_reason`:
1. `sprout_gc_mark_roots()` — walk `g_root_nodes`, `g_temp_root_nodes`, handle table
2. `sprout_gc_drain_marks()` — iterative worklist expansion (avoids C stack overflow)
3. `sprout_gc_sweep()` — linear scan of `g_heap_nodes`; free unmarked, reset marked on survivors

### Freelists

- `g_managed_node_freelist`: recycles `ManagedNode` structs
- `g_sprout_obj_freelist`: recycles `SproutObj` allocations (linked via `f0` field)

### Mutation

`ref_write` is the **only** mutation primitive. All other data structures are
immutable after construction. `ref_write` calls `find_managed_ptr` (hash
lookup) to validate the ref handle before writing.

---

## Generational GC Design

### Core invariant

> Sprout data structures are **immutable after construction** except for
> `RefVal` (written via `ref_write`). Therefore, an old-generation object
> **cannot** point to a young-generation object unless `ref_write` placed a
> young pointer into an old `Ref` after the young object was allocated.

This invariant means:

- During a **minor GC** (young-only sweep), old objects never need to be
  traversed to find additional live young objects — unless they contain a
  `Ref` whose value was updated after the `Ref` was promoted.
- The write barrier is **only needed in `ref_write`**.

### Pre-implementation audit (completed)

Before implementing, two mutation sources were audited:

**Builder (`bytes_builder_append`)**: creates a new `BuilderVal` on every
append — does NOT mutate the existing `BuilderVal`. No write barrier needed.

**Closures**: no evidence of closure patching after construction in
`sprout_runtime.c`. Closure env slots are written once at creation time by
`sprout_alloc_closure_env`; no post-construction mutation found. No write
barrier needed.

**Verdict**: `ref_write` is the only write barrier site.

---

## Implementation Plan

### Step 0 — Instrument before touching GC

Add timing instrumentation to `sprout_gc_collect_with_reason` to confirm the
4% baseline on the target benchmark:

```c
// at top of collect_with_reason:
struct timeval t0, t1;
gettimeofday(&t0, NULL);
// ... existing GC ...
gettimeofday(&t1, NULL);
// accumulate in g_gc_total_us
```

Also add `g_total_wall_us` (measured via `atexit` vs startup) so that
`SPROUT_DEBUG_GC=1` can print `gc_time / total_time` as a fraction. This
makes the ceiling claim falsifiable before and after implementation.

### Step 1 — Add generation to ManagedNode

```c
typedef struct ManagedNode {
  void*          ptr;
  SproutHeapKind kind;
  size_t         aux_slots;
  int            marked;
  int            generation;    // 0 = young, 1 = old
  ManagedNode*   next;
  ManagedNode*   hash_next;
} ManagedNode;
```

Initialize `generation = 0` in `register_managed_ptr`.

### Step 2 — Split the heap list

Replace `g_heap_nodes` with two separate lists:

```c
static ManagedNode* g_young_nodes = NULL;   // generation == 0
static ManagedNode* g_old_nodes   = NULL;   // generation == 1

static long long g_young_count   = 0;
static long long g_old_count     = 0;
```

Update `register_managed_ptr` to prepend to `g_young_nodes` and increment
`g_young_count`. Update `g_managed_heap_count` to be `g_young_count +
g_old_count` for compatibility with existing threshold and livelock logic.

`g_heap_index` is **shared** across both generations — `find_managed_ptr`
works the same way.

### Step 3 — Add remembered set

```c
#define SPROUT_REMEMBERED_CAP 4096

static ManagedNode* g_remembered_set[SPROUT_REMEMBERED_CAP];
static size_t       g_remembered_len = 0;
```

The remembered set holds old-gen `Ref` nodes whose values were updated to
point to young objects.

```c
long long ref_write(long long ref, long long value) {
  ManagedNode* node = find_managed_ptr((void*)(uintptr_t)ref);
  if (node == NULL || node->kind != SPROUT_HEAP_REF) tcp_fail("ref_write: not a Ref");
  ((RefVal*)node->ptr)->value = value;

  /* Write barrier: if old Ref now points into the young gen, remember it. */
  if (node->generation == 1) {
    ManagedNode* target = find_managed_ptr((void*)(uintptr_t)value);
    if (target != NULL && target->generation == 0) {
      remembered_set_add(node);
    }
  }
  return 0;
}
```

`remembered_set_add` is a linear deduplicated insert (the remembered set is
tiny in practice — programs rarely write young pointers into old Refs at high
frequency):

```c
static void remembered_set_add(ManagedNode* node) {
  for (size_t i = 0; i < g_remembered_len; i++) {
    if (g_remembered_set[i] == node) return; /* already present */
  }
  if (g_remembered_len < SPROUT_REMEMBERED_CAP) {
    g_remembered_set[g_remembered_len++] = node;
  }
  /* If cap exceeded, next minor GC will trigger a full major GC instead.
   * See the "safety cap" rule in Step 5. */
}
```

### Step 4 — Minor GC mark phase

During a minor GC, `gc_mark_enqueue` must skip old-gen objects (they are
never freed by a minor cycle):

```c
static int g_gc_minor = 0;   /* 1 when inside a minor GC cycle */

static void gc_mark_enqueue(ManagedNode* node) {
  if (node == NULL || node->marked) return;
  if (g_gc_minor && node->generation == 1) return;  /* skip old objects */
  node->marked = 1;
  g_gc_marked_count++;
  /* ... existing worklist push ... */
}
```

Additional roots for minor GC:

```c
static void sprout_gc_mark_roots_minor(void) {
  sprout_gc_mark_roots();  /* existing root walk (roots always treated as live) */

  /* Remembered set: old Refs pointing to young objects are additional roots. */
  for (size_t i = 0; i < g_remembered_len; i++) {
    ManagedNode* old_ref = g_remembered_set[i];
    if (old_ref != NULL && old_ref->generation == 1) {
      long long child_val = ((RefVal*)old_ref->ptr)->value;
      gc_mark_enqueue(find_managed_ptr((void*)(uintptr_t)child_val));
    }
  }
}
```

### Step 5 — Minor GC sweep + promotion

```c
static void sprout_gc_sweep_minor(void) {
  ManagedNode* prev = NULL;
  ManagedNode* node = g_young_nodes;
  while (node != NULL) {
    ManagedNode* next = node->next;
    if (!node->marked) {
      /* Collect */
      if (node->ptr == g_nothing_singleton) g_nothing_singleton = NULL;
      sprout_managed_index_remove(node);
      sprout_gc_free_payload(node);
      if (prev == NULL) g_young_nodes = next;
      else              prev->next = next;
      node->next = g_managed_node_freelist;
      g_managed_node_freelist = node;
      g_debug_gc_swept++;
      g_young_count--;
    } else {
      /* Promote to old generation */
      node->marked = 0;
      node->generation = 1;
      /* Splice out of young list */
      if (prev == NULL) g_young_nodes = next;
      else              prev->next = next;
      /* Prepend to old list */
      node->next = g_old_nodes;
      g_old_nodes = node;
      g_young_count--;
      g_old_count++;
    }
    node = next;
  }
}
```

### Step 6 — Threshold logic

Two orthogonal thresholds:

```c
static long long g_nursery_threshold = 512;   /* minor GC when young count >= this */
static long long g_gc_threshold = 4096;        /* major GC when old count >= this */
```

`sprout_gc_maybe_collect_threshold` becomes:

```c
static void sprout_gc_maybe_collect_threshold(void) {
  if (g_gc_stress < 0) { const char* e = getenv("SPROUT_GC_STRESS"); g_gc_stress = (e && e[0]=='1') ? 1 : 0; }
  if (g_gc_stress) {
    sprout_gc_collect_with_reason("stress");
    return;
  }
  if (g_gc_active) return;

  /* Safety cap: if young objects exceed 10× nursery_threshold, force a major GC
   * to avoid unbounded young-gen growth (e.g. remembered-set overflow scenario). */
  if (g_nursery_threshold > 0 && g_young_count >= 10 * g_nursery_threshold) {
    sprout_gc_collect_with_reason("young_cap");
    return;
  }

  /* Normal minor GC */
  if (g_nursery_threshold > 0 && g_young_count >= g_nursery_threshold) {
    sprout_gc_collect_minor("threshold_minor");
    return;
  }

  /* Major GC (old-gen only threshold) */
  if (g_gc_threshold > 0 && g_old_count >= g_gc_threshold) {
    sprout_gc_collect_with_reason("threshold_major");
  }
}
```

### Step 7 — Minor→major chaining

After a minor GC, if promotion causes `g_old_count >= g_gc_threshold`, trigger
a major GC immediately:

```c
static void sprout_gc_collect_minor(const char* reason) {
  if (g_gc_active) return;
  g_gc_active = 1;
  g_gc_minor = 1;
  long long before_young = g_young_count;
  sprout_gc_mark_roots_minor();
  sprout_gc_drain_marks();
  sprout_gc_sweep_minor();
  /* Clear remembered set — swept/promoted; rebuild incrementally from here. */
  g_remembered_len = 0;
  g_gc_minor = 0;
  g_gc_active = 0;

  /* Chain to major if old gen now exceeds its threshold. */
  if (g_gc_threshold > 0 && g_old_count >= g_gc_threshold) {
    sprout_gc_collect_with_reason("post_minor_major");
  }
  /* ... logging ... */
}
```

The existing `sprout_gc_collect_with_reason` becomes the **major** GC: it
sweeps `g_old_nodes` (or both lists if doing a full collection). For a full
major GC, `g_gc_minor = 0`, and `gc_mark_enqueue` does not skip any objects.
After the major sweep, surviving young objects are also promoted (or a
separate young sweep pass runs first to collect young garbage before
promoting).

### Step 8 — SPROUT_DEBUG_GC extensions

Extend the existing debug log line to include generation counts:

```
[sprout gc] minor: young_before=512 collected=391 promoted=121  (1.2 ms)
[sprout gc] major: old_before=4200 old_after=3100  (4.1 ms)
[sprout gc] gc_fraction: 4.1% of 29.5 s wall time
```

New env vars (following existing naming convention):

- `SPROUT_GC_NURSERY_THRESHOLD=512` — minor GC trigger (default 512)

---

## Caveats and Open Questions

### 1. `g_heap_index` is shared

`find_managed_ptr` is used during marking (in `gc_mark_enqueue` and
`sprout_gc_drain_marks`) and during `ref_write`. Because the hash table is
shared, old-gen objects remain findable by pointer during minor GC. This is
correct — `find_managed_ptr` for an old-gen ptr during minor GC still returns
the node; it will simply be skipped by `gc_mark_enqueue` due to the
`generation == 1` guard.

### 2. `g_nothing_singleton` interaction

`sprout_gc_sweep` checks `if (node->ptr == g_nothing_singleton) g_nothing_singleton = NULL`
before freeing. This check must be replicated in `sprout_gc_sweep_minor`. The
`g_nothing_singleton` is seeded in `sprout_set_argv` and will quickly be
promoted to old-gen; after promotion it will never be collected by minor GC.

### 3. Adaptive threshold

The existing adaptive threshold doubles `g_gc_threshold` when the swept
fraction is low. For generational GC, this logic should apply to the *major*
threshold (`g_gc_threshold`) based on old-gen sweeps, not to the nursery
threshold. The nursery threshold is a tuning parameter for young-gen
collection frequency and should stay fixed (or be separately adaptive).

### 4. Impact ceiling for N-queens

Per the threshold experiment: GC cycles are ~4% of N-queens wall time.
Generational GC reduces cycle cost but not per-allocation overhead.
**Expected N-queens speedup: 10–20%.**

The higher-leverage fix for N-queens and similar allocation-heavy workloads
is unboxed struct returns for Maybe-returning externs (see
`docs/unboxed-maybe-returns-v1-draft.md`). That fix should be implemented
first.

---

## Files to Change

| File | Change |
|------|--------|
| `runtime/sprout_runtime.c` | All GC implementation |
| `BACKLOG.md` | Add backlog item |
| `docs/generational-gc-v1-draft.md` | This document |

No Sprout source changes required — the optimization is entirely in the C
runtime, transparent to user programs and the compiler.

---

## Branch

Implementation: `perf/generational-gc` (already created, no code written yet).
