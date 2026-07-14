/* Cooperative green-thread scheduler for Sprout L0.1 structured concurrency
 * (EXPERIMENTAL). Single OS thread, no preemption: tasks run until they call
 * task_yield (or finish). Housed in its own translation unit so the deprecated-
 * on-macOS ucontext API and its feature-test macros stay out of the main
 * runtime. See docs/concurrency-design-exploration-2026-07-13.md (§4.A, §8.5)
 * and runtime/sprout_sched.h for the GC-root-context contract.
 *
 * Concurrency model (L0.1): structured, join-only. `with_scope` opens a scope,
 * runs its body, then __scope_join drives the scheduler until every task spawned
 * into the scope has finished. Cancellation and error propagation come later.
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

/* Fixed per-task sizing (L0.1). Stacks and root pools are non-moving mallocs —
 * required because a root slot is an address into the task's stack read while
 * the task is suspended (see sprout_sched.h). */
#define SPROUT_TASK_STACK_BYTES (1u << 20)   /* 1 MiB per green stack */
#define SPROUT_TASK_ROOT_SLOTS  16384        /* per-task GC temp-root LIFO depth */

typedef struct Task {
  ucontext_t   ctx;
  void*        stack;       /* malloc'd, non-moving; freed on completion */
  SproutRoots* roots;       /* this task's GC temp-root context */
  long long    work;        /* Unit->Unit closure handle (env ptr); rooted via &work */
  int          done;        /* set by the trampoline when the body returns */
  struct Task* next;        /* ready-queue link (FIFO) */
} Task;

/* Ready queue: FIFO so resumes are round-robin. */
static Task* g_rq_head = NULL;
static Task* g_rq_tail = NULL;

/* Scheduler context: the point inside __scope_join to which tasks return.
 * L0.1 permits at most one open scope at a time (nested scopes are rejected in
 * __scope_open), so a single scheduler context and live-counter suffice. */
static ucontext_t g_sched_ctx;
static Task*      g_current_task = NULL;   /* running task, or NULL in scheduler/main */
static int        g_scope_open   = 0;
static long long  g_scope_live   = 0;      /* tasks spawned into the open scope, not yet finished */
static long long  g_next_scope_id = 0;

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

/* Task body ABI: a `Unit -> Unit !{IO}` closure handle points to its env; slot 0
 * is the code pointer; the call is code(env_handle, unit=0) with unit the i64 0
 * sentinel. */
static void sprout_task_invoke(long long work) {
  void* env = (void*)(uintptr_t)work;
  long long (*code)(long long, long long) = *(long long (**)(long long, long long))env;
  (void)code(work, 0);
}

/* makecontext entry point. On entry the scheduler has set g_current_task to us
 * and switched g_current_roots to our context. Runs the body to completion, then
 * returns — resuming uc_link (g_sched_ctx) back in the __scope_join loop. */
static void task_trampoline(void) {
  Task* t = g_current_task;
  sprout_task_invoke(t->work);
  t->done = 1;
  g_scope_live--;
}

long long __scope_open(void) {
  if (g_current_task != NULL)
    sprout_fail("__scope_open: nested scope from within a task is not supported yet (L0.2)");
  if (g_scope_open)
    sprout_fail("__scope_open: a scope is already open (join it first)");
  g_scope_open = 1;
  g_scope_live = 0;
  return g_next_scope_id++;
}

long long __scope_spawn(long long scope_id, long long work) {
  (void)scope_id;
  if (!g_scope_open)
    sprout_fail("__scope_spawn: no open scope");

  Task* t = (Task*)malloc(sizeof(Task));
  if (t == NULL) sprout_fail("__scope_spawn: out of memory (task)");
  t->stack = malloc(SPROUT_TASK_STACK_BYTES);
  if (t->stack == NULL) sprout_fail("__scope_spawn: out of memory (stack)");
  t->roots = sprout_roots_new(SPROUT_TASK_ROOT_SLOTS);
  t->work  = work;
  t->done  = 0;
  t->next  = NULL;

  /* Keep the work-closure reachable from spawn until the task first runs: root
   * &work in the task's own context (scanned by the collector via the registry).
   * Stable because Task is a non-moving malloc. */
  sprout_roots_push_ptr(t->roots, &t->work);

  /* getcontext initializes the struct before makecontext reads its uc_* fields. */
  if (getcontext(&t->ctx) != 0) sprout_fail("__scope_spawn: getcontext failed");
  t->ctx.uc_stack.ss_sp   = t->stack;
  t->ctx.uc_stack.ss_size = SPROUT_TASK_STACK_BYTES;
  t->ctx.uc_link          = &g_sched_ctx;   /* return here when the body finishes */
  makecontext(&t->ctx, task_trampoline, 0);

  rq_push(t);
  g_scope_live++;
  return 0;
}

long long task_yield(void) {
  Task* t = g_current_task;
  if (t == NULL) sprout_fail("task_yield: called outside a task");
  rq_push(t);                              /* become runnable again */
  swapcontext(&t->ctx, &g_sched_ctx);      /* back to the __scope_join loop */
  /* resumed later: the scheduler restored g_current_task and our roots first */
  return 0;
}

long long __scope_join(long long scope_id) {
  (void)scope_id;
  /* The join loop runs on the caller's (task-0 / main's) stack. Remember its root
   * context so we can restore it after every switch back from a task. */
  SproutRoots* saved = sprout_roots_current();

  while (g_scope_live > 0) {
    Task* t = rq_pop();
    if (t == NULL)
      sprout_fail("__scope_join: scope still live but no runnable task (deadlock)");

    g_current_task = t;
    sprout_roots_switch(t->roots);        /* switch-point: match roots to the task */
    swapcontext(&g_sched_ctx, &t->ctx);   /* run t until it yields or finishes */

    g_current_task = NULL;
    sprout_roots_switch(saved);           /* back on the join loop's own context */

    if (t->done) {                        /* finished: reclaim its resources */
      sprout_roots_free(t->roots);
      free(t->stack);
      free(t);
    }
  }

  g_scope_open = 0;
  return 0;
}

#ifdef __APPLE__
#pragma clang diagnostic pop
#endif
