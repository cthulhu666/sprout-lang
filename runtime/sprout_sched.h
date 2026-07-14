/* Shared interface between the C runtime (sprout_runtime.c) and the cooperative
 * green-thread scheduler (sprout_sched.c).
 *
 * Each green task runs with its OWN GC temp-root context (SproutRoots): a
 * non-moving pool plus a LIFO head. The collector's mark_roots scans EVERY
 * registered context, so a suspended task keeps its roots alive while another
 * task allocates. The scheduler MUST call sprout_roots_switch(task->roots)
 * immediately before switching execution into a task, so push/pop from that
 * task's generated code lands in its own context (the switch-point-alignment
 * invariant). See docs/concurrency-design-exploration-2026-07-13.md (§8.5).
 */
#ifndef SPROUT_SCHED_H
#define SPROUT_SCHED_H

#include <stddef.h>

/* Opaque per-task GC temp-root context. The struct is completed in
 * sprout_runtime.c; the scheduler only ever holds pointers to it. */
typedef struct SproutRoots SproutRoots;

/* Allocate a fresh root context with `pool_slots` LIFO slots and register it so
 * the collector scans it. The pool is non-moving (never realloc'd). */
SproutRoots* sprout_roots_new(size_t pool_slots);

/* The context whose generated code is currently executing. */
SproutRoots* sprout_roots_current(void);

/* Make `r` the active context: subsequent push/pop from generated code use it.
 * Call immediately before switching execution into the owning task. */
void sprout_roots_switch(SproutRoots* r);

/* Unregister and free `r`. Call only after its owning task has finished, so no
 * live root still lives in the pool. */
void sprout_roots_free(SproutRoots* r);

/* Push a PTR root into `r` (NOT the current context). Used to keep a spawned
 * task's work-closure reachable between spawn and first run. `slot` must point
 * to stable storage holding the GC pointer for the lifetime of `r`. */
void sprout_roots_push_ptr(SproutRoots* r, void* slot);

/* Runtime panic path (backtrace + abort), reusable from the scheduler TU. */
__attribute__((noreturn)) void sprout_fail(const char* msg);

#endif /* SPROUT_SCHED_H */
