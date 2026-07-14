/* Cooperative green-thread scheduler for Sprout L0.1 structured concurrency
 * (EXPERIMENTAL). Single OS thread, no preemption: tasks run until they call
 * task_yield (or finish). Housed in its own translation unit so the deprecated-
 * on-macOS ucontext API and its feature-test macros stay out of the main
 * runtime. See docs/concurrency-design-exploration-2026-07-13.md (§4.A, §8.5)
 * and runtime/sprout_sched.h for the GC-root-context contract.
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
#include "sprout_sched.h"

/* macOS marks makecontext/swapcontext deprecated; we knowingly use them. */
#ifdef __APPLE__
#pragma clang diagnostic push
#pragma clang diagnostic ignored "-Wdeprecated-declarations"
#endif

/* Fixed per-task sizing. Stacks and root pools are non-moving mallocs — required
 * because a root slot is an address into the task's stack read while the task is
 * suspended (see sprout_sched.h). */
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
} Scope;

typedef struct Task {
  ucontext_t   ctx;
  void*        stack;      /* malloc'd green stack; NULL for task-0 (native stack) */
  SproutRoots* roots;      /* this task's GC temp-root context */
  long long    work;       /* Unit->Unit closure handle (env ptr); rooted via &work */
  int          done;       /* set by the trampoline when the body returns */
  Scope*       scope;      /* the scope this task was spawned into (NULL for task-0) */
  struct Task* next;       /* global ready-queue link */
} Task;

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

/* Count of tasks currently parked on I/O (registered with the poller, not in the
 * ready queue). When the ready queue is empty and this is >0, the pump blocks in
 * the poller; when both are zero with tasks still parked, it is a deadlock. */
static long long g_io_parked = 0;

static void rq_push(Task* t) {
  t->next = NULL;
  if (g_rq_tail == NULL) { g_rq_head = g_rq_tail = t; }
  else { g_rq_tail->next = t; g_rq_tail = t; }
}

static Task* rq_pop(void) {
  Task* t = g_rq_head;
  if (t == NULL) return NULL;
  g_rq_head = t->next;
  if (g_rq_head == NULL) g_rq_tail = NULL;
  t->next = NULL;
  return t;
}

/* Scope handle ABI: __scope_open returns the Scope* encoded as the i64 the Sprout
 * `Scope` value wraps; spawn/join decode it back. The Scope is a non-moving malloc
 * live from open until join frees it, so the handle stays valid throughout. */
static Scope* scope_of(long long handle) { return (Scope*)(intptr_t)handle; }

/* Task body ABI: a `Unit -> Unit !{IO}` closure handle points to its env; slot 0
 * is the code pointer; the call is code(env_handle, unit=0) with unit the i64 0
 * sentinel. */
static void sprout_task_invoke(long long work) {
  void* env = (void*)(uintptr_t)work;
  long long (*code)(long long, long long) = *(long long (**)(long long, long long))env;
  (void)code(work, 0);
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
      if (g_io_parked > 0) {
        void* toks[64];
        int n = sprout_poll_wait(toks, 64);   /* blocks in kqueue/epoll */
        for (int i = 0; i < n; i++) { g_io_parked--; rq_push((Task*)toks[i]); }
        continue;
      }
      sprout_fail("scheduler: deadlock — tasks parked with no way to make progress");
    }
    g_current_task = t;
    sprout_roots_switch(t->roots);        /* switch-point: match roots to the task */
    swapcontext(&g_pump, &t->ctx);        /* run t until it parks or finishes */
    if (t != &g_task0 && t->done) {       /* finished: reclaim (task-0 never here) */
      sprout_roots_free(t->roots);
      free(t->stack);
      free(t);
    }
  }
}

/* makecontext entry point for a green task. On entry the pump has set
 * g_current_task to us and switched g_current_roots to our context. Runs the body,
 * wakes the scope's joiner if we were the last, then parks forever. */
static void task_trampoline(void) {
  Task* t = g_current_task;
  sprout_task_invoke(t->work);
  t->done = 1;
  Scope* s = t->scope;
  s->live--;
  if (s->live == 0 && s->waiter != NULL) {   /* last child wakes the joiner */
    rq_push(s->waiter);
    s->waiter = NULL;
  }
  swapcontext(&t->ctx, &g_pump);   /* back to the pump; never resumed (done) */
}

/* Startup: initialize the poller and the pump context, and materialize task-0.
 * Done in a constructor so bare tcp_* calls outside any with_scope can still park
 * (the pump/poller are not a with_scope-only facility). */
__attribute__((constructor))
static void sprout_sched_init(void) {
  sprout_poll_init();

  g_task0.stack = NULL;                 /* native stack; never freed */
  g_task0.roots = sprout_roots_main();  /* the 131072-slot compiler pool */
  g_task0.done  = 0;
  g_task0.scope = NULL;
  g_task0.next  = NULL;
  g_current_task = &g_task0;

  if (getcontext(&g_pump) != 0) sprout_fail("sprout_sched_init: getcontext failed");
  g_pump.uc_stack.ss_sp   = g_pump_stack;
  g_pump.uc_stack.ss_size = SPROUT_PUMP_STACK_BYTES;
  g_pump.uc_link          = NULL;       /* pump_loop never returns */
  makecontext(&g_pump, pump_loop, 0);
}

long long __scope_open(void) {
  Scope* s = (Scope*)malloc(sizeof(Scope));
  if (s == NULL) sprout_fail("__scope_open: out of memory");
  s->live   = 0;
  s->waiter = NULL;
  return (long long)(intptr_t)s;
}

long long __scope_spawn(long long scope_handle, long long work) {
  Scope* s = scope_of(scope_handle);
  if (s == NULL) sprout_fail("__scope_spawn: null scope");

  Task* t = (Task*)malloc(sizeof(Task));
  if (t == NULL) sprout_fail("__scope_spawn: out of memory (task)");
  t->stack = malloc(SPROUT_TASK_STACK_BYTES);
  if (t->stack == NULL) sprout_fail("__scope_spawn: out of memory (stack)");
  t->roots = sprout_roots_new(SPROUT_TASK_ROOT_SLOTS);
  t->work  = work;
  t->done  = 0;
  t->scope = s;
  t->next  = NULL;

  /* Keep the work-closure reachable from spawn until the task first runs: root
   * &work in the task's own context (scanned by the collector via the registry).
   * Stable because Task is a non-moving malloc. */
  sprout_roots_push_ptr(t->roots, &t->work);

  /* getcontext initializes the struct before makecontext reads its uc_* fields. */
  if (getcontext(&t->ctx) != 0) sprout_fail("__scope_spawn: getcontext failed");
  t->ctx.uc_stack.ss_sp   = t->stack;
  t->ctx.uc_stack.ss_size = SPROUT_TASK_STACK_BYTES;
  t->ctx.uc_link          = NULL;   /* trampoline swaps out explicitly; never returns */
  makecontext(&t->ctx, task_trampoline, 0);

  rq_push(t);       /* runnable, in the global queue */
  s->live++;
  return 0;
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
  free(s);
  return 0;
}

/* Suspend the current task until `fd` is ready for `interest`; the pump runs (or
 * poller-blocks for) other tasks meanwhile. Called from the tcp_* builtins on
 * EAGAIN. No GC temp roots may be held unrooted across this call — the pump can
 * drive tasks that trigger a collection while we are parked. */
void sched_park_on_fd(int fd, int interest) {
  sprout_poll_add(fd, interest, g_current_task);
  g_io_parked++;
  park_to_pump();
  /* resumed by the pump after the poller reported readiness */
}

#ifdef __APPLE__
#pragma clang diagnostic pop
#endif
