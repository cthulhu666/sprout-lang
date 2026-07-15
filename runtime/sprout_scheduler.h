/* Shared interface between the C runtime (sprout_runtime.c) and the cooperative
 * green-thread scheduler (sprout_scheduler.c).
 *
 * Each green task runs with its OWN GC temp-root context (SproutRoots): a
 * non-moving pool plus a LIFO head. The collector's mark_roots scans EVERY
 * registered context, so a suspended task keeps its roots alive while another
 * task allocates. The scheduler MUST call sprout_roots_switch(task->roots)
 * immediately before switching execution into a task, so push/pop from that
 * task's generated code lands in its own context (the switch-point-alignment
 * invariant). See docs/concurrency-design-exploration-2026-07-13.md (§8.5).
 */
#ifndef SPROUT_SCHEDULER_H
#define SPROUT_SCHEDULER_H

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

/* Task-0 (main) GC root context — the static 131072-slot pool. The scheduler
 * materializes main as a task record pointing at this; main keeps the native
 * stack + this pool (a green main would exhaust it and break the bootstrap). */
SproutRoots* sprout_roots_main(void);

/* ── I/O parking (L0.3) ───────────────────────────────────────────────────
 * Readiness poller (kqueue/epoll) in sprout_poll.c, driven by the scheduler.
 * The `tcp_*` builtins call scheduler_park_on_fd on EAGAIN to suspend the current
 * green task until the fd is ready; siblings run meanwhile. */
#define SPROUT_POLL_READ  1
#define SPROUT_POLL_WRITE 2

/* Poller — the poller stores an opaque per-fd token (the parked task) and hands
 * it back on readiness; it has no knowledge of Task. One-shot registration. */
void sprout_poll_init(void);
void sprout_poll_add(int fd, int interest, void* token);
/* Block until ≥1 registered fd is ready; fill `out_tokens` (up to `max`) with
 * the ready fds' tokens and return the count. */
int  sprout_poll_wait(void** out_tokens, int max);

/* Suspend the current green task until `fd` is ready for `interest` (READ|WRITE),
 * then resume it. Called from the retrofitted tcp_* builtins on EAGAIN. */
void scheduler_park_on_fd(int fd, int interest);

#endif /* SPROUT_SCHEDULER_H */
