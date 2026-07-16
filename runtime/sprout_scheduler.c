/* Cooperative green-thread scheduler for Sprout L0.1 structured concurrency
 * (EXPERIMENTAL). Single OS thread, no preemption: tasks run until they call
 * task_yield (or finish). Housed in its own translation unit so the deprecated-
 * on-macOS ucontext API and its feature-test macros stay out of the main
 * runtime. See docs/concurrency-design-exploration-2026-07-13.md (§4.A, §8.5)
 * and runtime/sprout_scheduler.h for the GC-root-context contract.
 *
 * Concurrency model (L0.1/L0.2): structured, join-only. `with_scope` opens a
 * scope, runs its body, then __scope_join drives the scheduler until every task
 * spawned into the scope has finished. Scopes NEST: a task may open its own
 * inner scope; its join loop runs on that task's green stack, with its own
 * per-scope ready queue and its own return context, so an outer task's yields
 * after a nested scope return to the correct (outer) loop. Cancellation and
 * error propagation come later.
 *
 * GC integration: each task carries its own SproutRoots context. The scheduler
 * calls sprout_roots_switch(task->roots) immediately before switching execution
 * into a task, so the collector — which scans every registered context — always
 * roots the running task's in-flight values, and push/pop from generated code
 * lands in the correct per-task LIFO.
 */

/* ucontext on macOS lives behind these feature-test macros; define before any
 * include pulls in <sys/ucontext.h>. */
#define _XOPEN_SOURCE 700
#define _DARWIN_C_SOURCE 1

#include <stdlib.h>
#include <stdint.h>
#include <ucontext.h>
#include "sprout_scheduler.h"

/* macOS marks makecontext/swapcontext deprecated; we knowingly use them. */
#ifdef __APPLE__
#pragma clang diagnostic push
#pragma clang diagnostic ignored "-Wdeprecated-declarations"
#endif

/* Fixed per-task sizing. Stacks and root pools are non-moving mallocs — required
 * because a root slot is an address into the task's stack read while the task is
 * suspended (see sprout_scheduler.h). */
#define SPROUT_TASK_STACK_BYTES (1u << 20)   /* 1 MiB per green stack */
#define SPROUT_TASK_ROOT_SLOTS  16384        /* per-task GC temp-root LIFO depth */
#define SPROUT_PUMP_STACK_BYTES (1u << 18)   /* 256 KiB for the scheduler pump */

/* A scope tracks only its outstanding-task count and (at most) the one task
 * blocked in `__scope_join` waiting for it to drain. Under the top-level pump a
 * join is a wait-on-live-count, not a queue drain, so scopes need no ready queue
 * of their own — all runnable tasks share one global queue. */
typedef struct Scope {
  long long     live;      /* tasks spawned into this scope, not yet finished */
  struct Task*  waiter;    /* the task parked in __scope_join(this), or NULL */
  struct Task*  forks;     /* awaitable (task_fork) tasks, linked via scope_next;
                              reclaimed at scope close (fire-and-forget tasks are
                              reclaimed on done and never appear here) */
  long long     reason;    /* stop-reason: 0 none / 1 cancelled (scope_cancel) / 2 timed-out
                              (with_timeout). Read cooperatively by task_cancelled/task_status. */
  struct Task*  owner;     /* the task that opened this scope (only it may cancel it) */
} Scope;

/* Scope stop-reasons (Scope.reason). task_cancelled = (reason != 0); task_status maps them. */
enum { REASON_NONE = 0, REASON_CANCELLED = 1, REASON_TIMEDOUT = 2 };

typedef struct Task {
  ucontext_t   ctx;
  void*        stack;      /* malloc'd green stack; NULL for task-0 (native stack) */
  SproutRoots* roots;      /* this task's GC temp-root context */
  long long    work;       /* Unit->a closure handle (env ptr); rooted via &work */
  long long    result;     /* awaitable task's result; rooted via &result post-done */
  struct Task* awaiter;    /* the ONE task parked in __task_await(this), or NULL;
                              a Task is awaited by at most one task (single awaiter) */
  int          awaitable;  /* 1 = task_fork (keep result until awaited); 0 = fire-and-forget */
  int          done;       /* set by the trampoline when the body returns */
  Scope*       scope;      /* the scope this task was spawned into (NULL for task-0) */
  struct Task* next;       /* global ready-queue link */
  struct Task* scope_next; /* scope->forks list link (awaitable tasks only) */
  int          park_kind;  /* PARK_* — what this task is suspended on (routes cancel-drop) */
  int          park_fd;    /* fd this task is suspended-in-the-poller on, or -1 (L0.5) */
  int          park_interest; /* SPROUT_POLL_READ|WRITE it parked with (for poll_remove) */
  long long    park_timer_id; /* opaque timer handle when park_kind == PARK_TIMER (L0.6) */
  struct Task* io_next;    /* g_io_head list link (only while parked on I/O or a timer) */
  struct Task* io_prev;
  int          on_io_list; /* 1 iff currently linked on g_io_head (robust membership signal,
                              immune to park_kind being reset by the pump on wake — L0.7 §5.3) */
  int          in_rq;      /* 1 iff currently linked in the ready queue (runnable) — L0.7 §5.2 */
  struct Task* deadline_child; /* non-NULL iff this task is a with_timeout owner parked in
                              __await_deadline, pointing at the body task it is timing (L0.7) */
} Task;

/* How a task sitting on g_io_head is suspended, so scope_cancel tears down the right
 * poller registration when it force-drops the task. */
enum { PARK_NONE = 0, PARK_FD = 1, PARK_TIMER = 2 };

/* The single scheduler pump. Every task (and task-0/main) PARKS by swapping to
 * g_pump; the pump resumes a task by swapping into it. One pump owns the whole
 * schedule, so nesting and I/O parking fall out of the same mechanism. */
static ucontext_t g_pump;
static char       g_pump_stack[SPROUT_PUMP_STACK_BYTES];

/* task-0 (main): native stack + the 131072-slot compiler root pool. Materialized
 * as a Task so the pump parks/resumes it uniformly (roots set at startup). */
static Task  g_task0;
static Task* g_current_task = &g_task0;

/* One global FIFO ready queue (round-robin). */
static Task* g_rq_head = NULL;
static Task* g_rq_tail = NULL;

/* Every task currently suspended in the poller (registered, not in the ready queue),
 * on ONE global doubly-linked list — the single source of truth for "who is parked on
 * I/O." The pump blocks in the poller iff this is non-empty and the ready queue is
 * empty; scope_cancel walks it to force-drop a cancelled scope's parked tasks (L0.5).
 * Doubly-linked so the pump can O(1)-unlink a woken task and cancel can O(1)-unlink a
 * dropped one from the middle. Design doc §7 calls this list g_io_head. */
static Task* g_io_head = NULL;

static void io_list_push(Task* t) {
  t->io_prev = NULL;
  t->io_next = g_io_head;
  if (g_io_head != NULL) g_io_head->io_prev = t;
  g_io_head = t;
  t->on_io_list = 1;
}

static void io_list_remove(Task* t) {
  if (t->io_prev != NULL) t->io_prev->io_next = t->io_next;
  else                    g_io_head = t->io_next;
  if (t->io_next != NULL) t->io_next->io_prev = t->io_prev;
  t->io_prev = t->io_next = NULL;
  t->on_io_list = 0;
}

static void rq_push(Task* t) {
  t->next = NULL;
  if (g_rq_tail == NULL) { g_rq_head = g_rq_tail = t; }
  else { g_rq_tail->next = t; g_rq_tail = t; }
  t->in_rq = 1;
}

static Task* rq_pop(void) {
  Task* t = g_rq_head;
  if (t == NULL) return NULL;
  g_rq_head = t->next;
  if (g_rq_head == NULL) g_rq_tail = NULL;
  t->next = NULL;
  t->in_rq = 0;
  return t;
}

/* Scope handle ABI: __scope_open returns the Scope* encoded as the i64 the Sprout
 * `Scope` value wraps; spawn/join decode it back. The Scope is a non-moving malloc
 * live from open until join frees it, so the handle stays valid throughout. */
static Scope* scope_of(long long handle) { return (Scope*)(intptr_t)handle; }

/* Task handle ABI: __task_fork returns the Task* encoded as the i64 the Sprout
 * `Task a` value wraps; task_await decodes it. Valid until the owning scope
 * closes (which frees the record). */
static Task* task_of(long long handle) { return (Task*)(intptr_t)handle; }

/* Task body ABI: a `Unit -> a !{IO}` closure handle points to its env; slot 0
 * is the code pointer; the call is code(env_handle, unit=0) with unit the i64 0
 * sentinel. Returns the closure's i64 result (discarded for fire-and-forget). */
static long long sprout_task_invoke(long long work) {
  void* env = (void*)(uintptr_t)work;
  long long (*code)(long long, long long) = *(long long (**)(long long, long long))env;
  return code(work, 0);
}

/* Park the current task: hand control to the pump. The pump restores
 * g_current_task + g_current_roots before it resumes us. */
static void park_to_pump(void) {
  swapcontext(&g_current_task->ctx, &g_pump);
}

/* The scheduler pump loop (runs on its own stack). Picks the next runnable task;
 * when nothing is runnable but tasks are parked on I/O, blocks in the poller;
 * when nothing can make progress, fails loudly. Never returns — it yields control
 * only by swapping into a task, and regains it when that task parks/finishes. */
static void pump_loop(void) {
  for (;;) {
    Task* t = rq_pop();
    if (t == NULL) {
      if (g_io_head != NULL) {
        void* toks[64];
        int n = sprout_poll_wait(toks, 64);   /* blocks in kqueue/epoll */
        for (int i = 0; i < n; i++) {         /* each ready fd / fired timer wakes its task */
          Task* w = (Task*)toks[i];
          io_list_remove(w);                  /* no longer poller-parked */
          w->park_kind = PARK_NONE;
          w->park_fd = -1;
          rq_push(w);
        }
        continue;
      }
      sprout_fail("scheduler: deadlock — tasks parked with no way to make progress");
    }
    g_current_task = t;
    sprout_roots_switch(t->roots);        /* switch-point: match roots to the task */
    swapcontext(&g_pump, &t->ctx);        /* run t until it parks or finishes */
    if (t != &g_task0 && t->done) {       /* finished (task-0 never reclaimed here) */
      if (t->awaitable) {
        /* Keep the record + roots (they root the result) until task_await consumes
         * it or the owning scope closes; only the green stack is done with here. */
        free(t->stack);
        t->stack = NULL;
      } else {                            /* fire-and-forget: reclaim everything now */
        sprout_roots_free(t->roots);
        free(t->stack);
        free(t);
      }
    }
  }
}

/* makecontext entry point for a green task. On entry the pump has set
 * g_current_task to us and switched g_current_roots to our context. Runs the body,
 * wakes any awaiter and the scope's joiner if we were the last, then parks forever. */
static void task_trampoline(void) {
  Task* t = g_current_task;
  long long r = sprout_task_invoke(t->work);
  if (t->awaitable) {
    /* Keep the result reachable from completion until task_await consumes it (or
     * the scope closes). Root &result in our own context — scanned via the
     * registry while we stay registered. No allocation between the store and the
     * push, so the result is never momentarily unrooted. */
    t->result = r;
    sprout_roots_push_ptr(t->roots, &t->result);
  }
  t->done = 1;
  if (t->awaiter != NULL) {                   /* a task_await or a with_timeout owner is blocked on us */
    Task* aw = t->awaiter;
    t->awaiter = NULL;
    /* Enqueue the awaiter EXACTLY once (L0.7 §5.3). A with_timeout owner may be parked on BOTH
     * us (awaiter) and its own deadline timer; a double rq_push would self-cycle the ready
     * queue. park_kind cannot dedup this — the pump resets it to PARK_NONE on timer harvest —
     * so we use on_io_list (live g_io_head membership) and deadline_child:
     *  - aw->on_io_list: its deadline timer is still live (not harvested) -> tear it down and
     *    unlink, then push (mirrors the pump's own wake path).
     *  - aw->deadline_child == t but off the io list: the pump ALREADY harvested its timer and
     *    enqueued it -> do NOT push again.
     *  - ordinary task_await awaiter (deadline_child == NULL, never on the io list) -> push. */
    if (aw->on_io_list) {
      sprout_poll_remove_timer(aw->park_timer_id);
      io_list_remove(aw);
      aw->park_kind = PARK_NONE;
      rq_push(aw);
    } else if (aw->deadline_child == t) {
      /* already runnable via the timer harvest; second push would corrupt the queue */
    } else {
      rq_push(aw);
    }
  }
  Scope* s = t->scope;
  s->live--;
  if (s->live == 0 && s->waiter != NULL) {    /* last child wakes the joiner */
    rq_push(s->waiter);
    s->waiter = NULL;
  }
  swapcontext(&t->ctx, &g_pump);   /* back to the pump; never resumed (done) */
}

/* Startup: initialize the poller and the pump context, and materialize task-0.
 * Done in a constructor so bare tcp_* calls outside any with_scope can still park
 * (the pump/poller are not a with_scope-only facility). */
__attribute__((constructor))
static void sprout_scheduler_init(void) {
  sprout_poll_init();

  g_task0.stack = NULL;                 /* native stack; never freed */
  g_task0.roots = sprout_roots_main();  /* the 131072-slot compiler pool */
  g_task0.done  = 0;
  g_task0.scope = NULL;
  g_task0.next  = NULL;
  g_task0.park_kind = PARK_NONE;         /* task-0 parks on I/O / timers like any task */
  g_task0.park_fd = -1;
  g_task0.park_timer_id = 0;
  g_task0.io_next = NULL;
  g_task0.io_prev = NULL;
  g_task0.on_io_list = 0;
  g_task0.in_rq = 0;
  g_task0.deadline_child = NULL;
  g_current_task = &g_task0;

  if (getcontext(&g_pump) != 0) sprout_fail("sprout_scheduler_init: getcontext failed");
  g_pump.uc_stack.ss_sp   = g_pump_stack;
  g_pump.uc_stack.ss_size = SPROUT_PUMP_STACK_BYTES;
  g_pump.uc_link          = NULL;       /* pump_loop never returns */
  makecontext(&g_pump, pump_loop, 0);
}

long long __scope_open(void) {
  Scope* s = (Scope*)malloc(sizeof(Scope));
  if (s == NULL) sprout_fail("__scope_open: out of memory");
  s->live      = 0;
  s->waiter    = NULL;
  s->forks     = NULL;
  s->reason    = REASON_NONE;
  s->owner     = g_current_task;   /* only this task may __scope_cancel it */
  return (long long)(intptr_t)s;
}

/* Shared task construction: allocate the record + green stack + GC root context,
 * root the work-closure, prime the ucontext, and enqueue it runnable. `awaitable`
 * selects the reclamation lifecycle (see the Task struct and pump_loop). Returns
 * the new Task*. */
static Task* task_create(Scope* s, long long work, int awaitable) {
  Task* t = (Task*)malloc(sizeof(Task));
  if (t == NULL) sprout_fail("task scheduler: out of memory (task record)");
  t->stack = malloc(SPROUT_TASK_STACK_BYTES);
  if (t->stack == NULL) sprout_fail("task scheduler: out of memory (green stack)");
  t->roots      = sprout_roots_new(SPROUT_TASK_ROOT_SLOTS);
  t->work       = work;
  t->result     = 0;
  t->awaiter    = NULL;
  t->awaitable  = awaitable;
  t->done       = 0;
  t->scope      = s;
  t->next       = NULL;
  t->scope_next = NULL;
  t->park_kind  = PARK_NONE;
  t->park_fd    = -1;      /* not I/O-parked until scheduler_park_on_fd runs */
  t->park_interest = 0;
  t->park_timer_id = 0;
  t->io_next    = NULL;
  t->io_prev    = NULL;
  t->on_io_list = 0;
  t->in_rq      = 0;       /* rq_push below sets it once the task is enqueued */
  t->deadline_child = NULL;

  /* Keep the work-closure reachable from spawn until the task first runs: root
   * &work in the task's own context (scanned by the collector via the registry).
   * Stable because Task is a non-moving malloc. */
  sprout_roots_push_ptr(t->roots, &t->work);

  /* getcontext initializes the struct before makecontext reads its uc_* fields. */
  if (getcontext(&t->ctx) != 0) sprout_fail("task scheduler: getcontext failed");
  t->ctx.uc_stack.ss_sp   = t->stack;
  t->ctx.uc_stack.ss_size = SPROUT_TASK_STACK_BYTES;
  t->ctx.uc_link          = NULL;   /* trampoline swaps out explicitly; never returns */
  makecontext(&t->ctx, task_trampoline, 0);

  rq_push(t);       /* runnable, in the global queue */
  s->live++;
  return t;
}

/* Fire-and-forget: spawn a Unit->Unit task. Reclaimed fully on done. */
long long __scope_spawn(long long scope_handle, long long work) {
  Scope* s = scope_of(scope_handle);
  if (s == NULL) sprout_fail("__scope_spawn: null scope");
  task_create(s, work, 0);
  return 0;
}

/* Awaitable: spawn a Unit->a task, link it into the scope's fork list (so a
 * never-awaited fork is reclaimed at scope close), and return its handle. */
long long __task_fork(long long scope_handle, long long work) {
  Scope* s = scope_of(scope_handle);
  if (s == NULL) sprout_fail("__task_fork: null scope");
  Task* t = task_create(s, work, 1);
  t->scope_next = s->forks;
  s->forks = t;
  return (long long)(intptr_t)t;
}

/* Block until the forked task finishes, then return its result. If it is already
 * done, read the result immediately; otherwise park as its awaiter and let the
 * pump run it (and everything else) to completion. The result stays rooted by the
 * task's own context until the scope closes, so it survives any collection here
 * and remains valid when handed back to the caller (who then roots it). */
long long __task_await(long long task_handle) {
  Task* t = task_of(task_handle);
  if (t == NULL) sprout_fail("__task_await: null task");
  /* A task force-dropped by scope_cancel has its roots freed (roots == NULL) and never
   * ran to completion, so it has no result and nothing will ever wake an awaiter of it.
   * The design's invariant is that a cancelled task is never awaited (owner cancels
   * after it already has the result it needs — §4.2); a body that violates it would
   * otherwise park here forever. Loud-fail instead of a silent hang. roots == NULL is
   * unambiguous: a live or *done* awaitable always still holds its roots. */
  if (t->roots == NULL)
    sprout_fail("__task_await: awaiting a task dropped by scope_cancel "
                "(a cancelled task produces no result)");
  if (!t->done) {
    t->awaiter = g_current_task;
    park_to_pump();
    /* resumed by the trampoline once t finished */
  }
  return t->result;
}

/* Force-drop a task that is suspended in the poller (still on g_io_head). Deregister its
 * poller registration, unlink it, and reclaim roots+stack TOGETHER — a parked task holds live
 * values rooted INTO its green stack (L0.3 park contract), so freeing the stack while keeping
 * the roots is a use-after-free (mark_roots would scan freed memory). Decrements the owning
 * scope's live count. Shared by scope_cancel (L0.5) and __await_deadline's timeout (L0.7).
 * The caller must have already established the task is force-droppable (still on g_io_head). */
static void force_drop_task(Task* t) {
  /* A sibling awaiting this task would be stranded (nothing wakes it, live never hits 0, the
   * owner's join deadlocks). Dropping a task the CURRENT task itself awaits is safe: the awaiter
   * is running this drop and proceeds itself (the with_timeout owner-timeout path, §5.2). */
  if (t->awaiter != NULL && t->awaiter != g_current_task)
    sprout_fail("force_drop_task: cannot drop a task awaited by a sibling — "
                "await only from the scope owner");
  /* A deadline-owner (a task blocked inside with_timeout) cannot be dropped without orphaning
   * its inner scope — the tree-cancel cascade is deferred (design §5.5). Loud-fail, don't
   * corrupt. This fires both for scope_cancel reaching a nested with_timeout and for a
   * with_timeout whose body is itself blocked in a nested with_timeout. */
  if (t->deadline_child != NULL)
    sprout_fail("with_timeout: cannot time out / cancel a body blocked inside with_timeout — "
                "deadline/cancel nesting cascade is deferred");
  Scope* ts = t->scope;
  /* Deregister whatever it is suspended on. For a timer this must discard even an already-
   * fired-but-undrained event (timers fire async to the pump, unlike fds which are drained
   * before the owner runs) — else a stale token would later resume a freed task (task_sleep
   * design §5.1). */
  if (t->park_kind == PARK_TIMER) sprout_poll_remove_timer(t->park_timer_id);
  else                            sprout_poll_remove(t->park_fd, t->park_interest);
  io_list_remove(t);
  /* Free roots first (unregisters the context so no later collection scans it), then the
   * stack; no allocation between (design §7). */
  sprout_roots_free(t->roots);
  free(t->stack);
  if (t->awaitable) {
    /* Record stays reachable via scope->forks; scope-close frees it. Null the freed pointers
     * so close's guard skips them (no double-free). Never awaited. */
    t->roots = NULL;
    t->stack = NULL;
  } else {
    /* Fire-and-forget: not in scope->forks, so free the record here and now. */
    free(t);
  }
  ts->live--;
}

/* Request cancellation of `scope` (L0.5). Owner-only: only the task that opened the scope may
 * cancel it — this guarantees no task is parked awaiting a sibling that a concurrent canceller
 * would drop (the "no cancelled task is ever awaited" invariant; cancellation doc §10.2). Sets
 * the cooperative reason; ready/yield-parked tasks observe it via task_cancelled and return on
 * their own; join-parked tasks are left for their inner scope to drain (local propagation). */
long long __scope_cancel(long long scope_handle) {
  Scope* s = scope_of(scope_handle);
  if (s == NULL) sprout_fail("__scope_cancel: null scope");
  if (g_current_task != s->owner)
    sprout_fail("__scope_cancel: only the scope's owning task may cancel it");
  s->reason = REASON_CANCELLED;

  /* Force-drop this scope's poller-parked tasks (they cannot observe the flag while asleep in
   * kqueue/epoll). No-race (single-thread): the pump drains every poll_wait token — unlinking
   * it from g_io_head — before it can resume the owner. So any task still on g_io_head here has
   * NOT fired; and the owner is running so s->waiter == NULL (no join-wake races the drop). */
  for (Task* t = g_io_head; t != NULL; ) {
    Task* next = t->io_next;              /* save before we unlink/free t */
    if (t->scope == s && t != g_current_task) force_drop_task(t);
    t = next;
  }
  return 0;
}

/* The current task's scope stop-reason: 0 none / 1 cancelled / 2 timed-out. task-0 and any task
 * with no scope are never stopping. The Sprout stdlib builds task_cancelled (reason != 0) and
 * task_status on top of this — task_cancelled is no longer a builtin (L0.7). */
long long __task_stop_reason(void) {
  Task* t = g_current_task;
  if (t == NULL || t->scope == NULL) return REASON_NONE;
  return t->scope->reason;
}

/* L0.7 with_timeout core. The caller (stdlib with_timeout) has already forked `body` as an
 * AWAITABLE child in `scope` (runnable, live). Arm a one-shot deadline timer on the OWNER and
 * register the owner as the child's awaiter, then park until EITHER the child finishes OR the
 * timer fires. Returns 1 if the child completed within the deadline (the caller then task_awaits
 * it for the result), 0 if it timed out (child force-dropped; caller returns Expired). Reached
 * only with ms > 0 — the stdlib wrapper routes ms <= 0 to an immediate Expired without forking.
 * See docs/concurrency-deadlines-design-2026-07-15.md §5.2/§5.3. */
long long __await_deadline(long long scope_handle, long long task_handle, long long ms) {
  Scope* s = scope_of(scope_handle);
  if (s == NULL) sprout_fail("__await_deadline: null scope");
  Task* child = task_of(task_handle);
  if (child == NULL) sprout_fail("__await_deadline: null task");
  Task* owner = g_current_task;

  /* Arm the deadline on the owner + await the child. The owner is now parked on BOTH the timer
   * (g_io_head) and as the child's awaiter; whichever fires first wakes it exactly once (the
   * trampoline/harvest dedup, §5.3). */
  long long tid;
  sprout_poll_add_timer(ms, owner, &tid);
  owner->park_kind      = PARK_TIMER;
  owner->park_timer_id  = tid;
  owner->deadline_child = child;
  io_list_push(owner);
  child->awaiter = owner;
  park_to_pump();

  owner->deadline_child = NULL;         /* no longer a deadline-owner */

  if (child->done) {
    /* Completed within the deadline. The child-first trampoline path already tore our timer
     * down and unlinked us; if instead the timer harvested us and the child then finished, the
     * timer is already consumed. If we are somehow still linked, tear it down. */
    if (owner->on_io_list) { sprout_poll_remove_timer(owner->park_timer_id); io_list_remove(owner); }
    child->awaiter = NULL;
    return 1;
  }

  /* The timer fired (child not done): the pump harvested + unlinked our timer, so we are off
   * g_io_head. Classify the child (§5.2): */
  if (child->on_io_list) {
    /* Parked directly on I/O / a task_sleep timer — the supported MVP case. Time it out. */
    s->reason = REASON_TIMEDOUT;
    child->awaiter = NULL;              /* clear so force_drop's sibling-awaiter guard is not
                                          tripped by our own (owner) awaiter link */
    force_drop_task(child);
    return 0;
  }
  if (child->in_rq) {
    /* Its I/O went ready right at the boundary and it is now runnable. Dropping a queued task
     * is a UAF, and it is one tick from done — let it finish (Completed). The timer is spent,
     * so this is now a plain await with a single wake source (the child's trampoline). */
    child->awaiter = owner;
    park_to_pump();
    child->awaiter = NULL;
    return 1;
  }
  /* Neither parked on I/O nor runnable -> blocked in a nested with_scope join or a task_await.
   * Force-dropping it would orphan its inner scope; the tree-cancel cascade is deferred. This is
   * the direct-I/O-only MVP boundary (design §5.2/§5.5). */
  sprout_fail("with_timeout: cannot time out a body blocked in a nested scope/await — "
              "deadline cascade deferred; time out a body that parks directly on I/O");
  return 0;  /* unreachable */
}

/* Cooperative yield: become runnable again and let the pump run another task.
 * Legal from any task including task-0 (a no-op round-trip when nothing else is
 * ready), so there is no "outside a task" case under the always-materialized
 * task-0. */
long long task_yield(void) {
  rq_push(g_current_task);
  park_to_pump();
  return 0;
}

long long __scope_join(long long scope_handle) {
  Scope* s = scope_of(scope_handle);
  if (s == NULL) sprout_fail("__scope_join: null scope");

  /* Wait until every task spawned into `s` has finished. If any are still live,
   * park the joiner (task-0 or an outer task) as the scope's waiter; the last
   * child to finish re-enqueues us. Nesting works because the pump keeps running
   * every other runnable task while we are parked. */
  if (s->live > 0) {
    s->waiter = g_current_task;
    park_to_pump();
    /* resumed with s->live == 0 (only the last child wakes the waiter) */
  }
  /* Every task has finished. Reclaim each awaitable fork's record + root context
   * (their green stacks were already freed on done). An awaited result stays
   * rooted by the caller's frame, so dropping the task's own root here is safe; a
   * never-awaited result is simply released. Fire-and-forget tasks were reclaimed
   * on done and are not in this list. */
  for (Task* f = s->forks; f != NULL; ) {
    Task* next = f->scope_next;
    /* A fork force-dropped by scope_cancel already had its roots freed (roots=NULL);
     * only its record survives in this list. Guard against a double-free (design §7). */
    if (f->roots != NULL) sprout_roots_free(f->roots);
    free(f);
    f = next;
  }
  free(s);
  return 0;
}

/* Suspend the current task until `fd` is ready for `interest`; the pump runs (or
 * poller-blocks for) other tasks meanwhile. Called from the tcp_* builtins on
 * EAGAIN. No GC temp roots may be held unrooted across this call — the pump can
 * drive tasks that trigger a collection while we are parked. */
void scheduler_park_on_fd(int fd, int interest) {
  /* Record what we parked on so scope_cancel can deregister this fd and drop us while
   * we are asleep in the poller (we cannot check task_cancelled from here). The pump
   * clears park_fd and unlinks us on wake; scope_cancel does so on force-drop. */
  g_current_task->park_kind     = PARK_FD;
  g_current_task->park_fd       = fd;
  g_current_task->park_interest = interest;
  io_list_push(g_current_task);
  sprout_poll_add(fd, interest, g_current_task);
  park_to_pump();
  /* resumed by the pump after the poller reported readiness */
}

/* Suspend the current task on a one-shot timer for `ms` (> 0) milliseconds; the pump runs
 * other tasks and poller-blocks meanwhile, and resumes us when the timer fires. Parks on
 * g_io_head exactly like an fd wait, so scope_cancel force-drops a sleeping task too.
 * Reached only with ms > 0 — the stdlib task_sleep wrapper routes ms <= 0 to task_yield
 * (a zero-value timerfd disarms on Linux → would hang; design §5.2). */
static void scheduler_park_on_timer(long long ms) {
  long long tid;
  sprout_poll_add_timer(ms, g_current_task, &tid);
  g_current_task->park_kind     = PARK_TIMER;
  g_current_task->park_timer_id = tid;
  io_list_push(g_current_task);
  park_to_pump();
  /* Resumed after the timer fired and the pump drained it. Tear the timer down (kqueue:
   * ENOENT no-op, one-shot already gone; epoll: EPOLL_CTL_DEL + close the timerfd). The
   * cancel-drop path is mutually exclusive with this one (a dropped task never resumes),
   * so the timer is torn down exactly once. */
  sprout_poll_remove_timer(tid);
}

/* task_sleep(ms) with ms > 0 (the wrapper handles ms <= 0). Returns Unit (0). */
long long __task_sleep(long long ms) {
  scheduler_park_on_timer(ms);
  return 0;
}

#ifdef __APPLE__
#pragma clang diagnostic pop
#endif
