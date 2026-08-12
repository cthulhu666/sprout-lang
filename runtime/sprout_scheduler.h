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

/* Build a `stdlib.chan.Recv a` (L0.9 channel close). Defined in sprout_runtime.c, where the
 * static ctor-name lookup and the GC temp-root macros live; __chan_recv calls these to return
 * `Got v` / `Closed`. sprout_chan_make_got roots `v` across the boxing allocation. */
long long sprout_chan_make_got(long long v);
long long sprout_chan_make_closed(void);

/* L0.11 select. sprout_chan_make_selected builds `stdlib.chan.Selected index recv` (recv is an
 * already-boxed Recv a; rooted across the allocation). sprout_list_next steps a Sprout `List Int`
 * in the scheduler: returns 1 and fills the head/tail out-params on a Cons, 0 on Nil (the Nil/Cons tag
 * lookup is static in the runtime TU). Both defined in sprout_runtime.c. */
long long sprout_chan_make_selected(long long index, long long recv_boxed);
int sprout_list_next(long long cur, long long* out_head, long long* out_tail);

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
/* Deregister `fd` (registered for `interest`) so it will not report readiness. Used
 * by scope_cancel to force-drop a task suspended in the poller (L0.5). Idempotent:
 * a not-registered fd (already fired one-shot, or never added) is silently ignored. */
void sprout_poll_remove(int fd, int interest);
/* Arm a one-shot timer for `ms` (> 0) milliseconds; the fired event carries `token` back
 * from sprout_poll_wait like a ready fd (L0.6 task_sleep). Writes into `*out_id` an opaque
 * handle used to tear the timer down (kqueue: the EVFILT_TIMER ident; epoll: the timerfd).
 *
 * Returns 1 on success and 0 if the timer could NOT be armed, leaving *out_id untouched. The
 * failure is not hypothetical: the epoll backend spends one timerfd per registration, so under
 * `ulimit -n` pressure arming is the first thing to fail — and a per-connection bounded read arms
 * one every time it blocks. Aborting there would drop every in-flight connection to punish the one
 * that could not be armed, so the caller decides: a bounded read reports its timeout and sheds that
 * connection, while task_sleep / with_timeout have no honest degradation and still fail loudly.
 * (Returns int, not long long, so it is not mistaken for a Sprout builtin — it is internal C
 * called only from the scheduler.) */
int sprout_poll_add_timer(long long ms, void* token, long long* out_id);
/* Tear down a timer created by sprout_poll_add_timer. MUST discard even an already-fired,
 * not-yet-retrieved event (timers fire asynchronously to the pump) so a dropped sleeping
 * task cannot be resumed by a stale token. Idempotent (ENOENT ignored). */
void sprout_poll_remove_timer(long long timer_id);
/* Block until ≥1 registration is ready; fill `out_tokens` (up to `max`) with the ready
 * tokens and return the count. `out_is_timer[i]` is 1 when event i came from a TIMER
 * registration rather than an fd — the combined fd-or-timer park needs it, since it has one
 * task registered on both and must tear down whichever did NOT fire, and the timer teardown
 * close()s a descriptor on Linux so it must run exactly once. Both arrays must have room
 * for `max` entries. */
int  sprout_poll_wait(void** out_tokens, int* out_is_timer, int max);

/* Suspend the current green task until `fd` is ready for `interest` (READ|WRITE),
 * then resume it. Called from the retrofitted tcp_* builtins on EAGAIN. */
void scheduler_park_on_fd(int fd, int interest);
/* Suspend the current task until `fd` is ready for `interest` OR `ms` milliseconds elapse,
 * whichever comes first. Returns 1 if the fd became ready, 0 if the deadline won. The task is
 * NOT dropped on expiry — it resumes normally and its caller decides what a timeout means — which
 * is what lets a timed read report `Err` while leaving the connection valid and its linear
 * release path intact (cf. Go's SetReadDeadline / Java's SO_TIMEOUT, as opposed to cancelling
 * the task). `ms` must be > 0; callers wanting "don't wait at all" should skip the park.
 * May also return 0 WITHOUT having waited, when the timer could not be armed at all (see
 * sprout_poll_add_timer) — a caller must therefore treat 0 as "the deadline is not honourable
 * right now", which for every current caller is the same action as a real expiry. */
int  scheduler_park_on_fd_timeout(int fd, int interest, long long ms);
/* Same, for an fd that no handle table owns yet — an in-flight connect(). If this task is
 * force-dropped while parked (with_timeout / scope_cancel), the drop CLOSES `fd`, since the
 * parked frame it frees held the only reference. Use scheduler_park_on_fd for any fd a
 * handle already owns; closing that one on drop would be a double-close. */
void scheduler_park_on_unowned_fd(int fd, int interest);
/* The bounded twin of the above: unowned-fd park with a deadline. Returns 1 = fd ready, 0 = the
 * deadline won (or could not be armed), exactly as scheduler_park_on_fd_timeout. Used by the HTTP
 * client, whose socket is unowned for the whole request AND must obey the caller's deadline at
 * every park — connect, send and read alike. */
int  scheduler_park_on_unowned_fd_timeout(int fd, int interest, long long ms);

/* Register a cleanup for memory a builtin holds ACROSS a park, or clear it with fn == NULL.
 * force_drop_task (with_timeout expiry / scope_cancel) frees a parked task's green stack without
 * unwinding the C frame on it, so malloc'd memory reachable only from that frame leaks. The
 * standing rule is to keep such state on the task stack instead (see tcp_connect); this exists for
 * state that structurally cannot be, such as a response buffer that grows across many parks.
 *
 * The hook runs BEFORE the roots and stack are freed, so `arg` may point into the green stack. It
 * must only free plain heap memory: no allocation, no GC interaction, no parking. Callers MUST
 * clear it before returning, so it can never outlive the frame `arg` refers to. */
void scheduler_set_park_cleanup(void (*fn)(void*), void* arg);

#endif /* SPROUT_SCHEDULER_H */
