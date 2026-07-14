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

/* Fixed per-task sizing (L0.1). Stacks and root pools are non-moving mallocs —
 * required because a root slot is an address into the task's stack read while
 * the task is suspended (see sprout_sched.h). */
#define SPROUT_TASK_STACK_BYTES (1u << 20)   /* 1 MiB per green stack */
#define SPROUT_TASK_ROOT_SLOTS  16384        /* per-task GC temp-root LIFO depth */

typedef struct Scope {
  long long     id;         /* == (intptr_t)this; informational (logging) */
  long long     live;       /* tasks spawned into this scope, not yet finished */
  struct Task*  rq_head;    /* per-scope FIFO ready queue (round-robin) */
  struct Task*  rq_tail;
} Scope;

typedef struct Task {
  ucontext_t   ctx;
  void*        stack;        /* malloc'd, non-moving; freed on completion */
  SproutRoots* roots;        /* this task's GC temp-root context */
  long long    work;         /* Unit->Unit closure handle (env ptr); rooted via &work */
  int          done;         /* set by the trampoline when the body returns */
  Scope*       scope;        /* the scope this task was spawned into */
  ucontext_t*  sched_return; /* return context of the join loop currently driving us */
  struct Task* next;         /* ready-queue link */
} Task;

/* The task whose generated code is currently executing, or NULL when control is
 * in a join loop / main. Nested joins save and restore this around each switch
 * (a natural stack via join-loop locals), so it always names the running task —
 * including while an outer task P drives an inner scope on P's own green stack. */
static Task* g_current_task = NULL;

static void rq_push(Scope* s, Task* t) {
  t->next = NULL;
  if (s->rq_tail == NULL) { s->rq_head = s->rq_tail = t; }
  else { s->rq_tail->next = t; s->rq_tail = t; }
}

static Task* rq_pop(Scope* s) {
  Task* t = s->rq_head;
  if (t == NULL) return NULL;
  s->rq_head = t->next;
  if (s->rq_head == NULL) s->rq_tail = NULL;
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

/* makecontext entry point. On entry the driving join loop has set g_current_task
 * to us and switched g_current_roots to our context. Runs the body to completion,
 * then swaps back to whichever loop is driving us (never returns). */
static void task_trampoline(void) {
  Task* t = g_current_task;
  sprout_task_invoke(t->work);
  t->done = 1;
  t->scope->live--;
  swapcontext(&t->ctx, t->sched_return);   /* back to the driving loop; unreached after */
}

long long __scope_open(void) {
  Scope* s = (Scope*)malloc(sizeof(Scope));
  if (s == NULL) sprout_fail("__scope_open: out of memory");
  s->id = (long long)(intptr_t)s;
  s->live = 0;
  s->rq_head = NULL;
  s->rq_tail = NULL;
  return s->id;
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
  t->sched_return = NULL;   /* set by the join loop before each swap-in */
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

  rq_push(s, t);
  s->live++;
  return 0;
}

long long task_yield(void) {
  Task* t = g_current_task;
  if (t == NULL) sprout_fail("task_yield: called outside a task");
  rq_push(t->scope, t);                    /* become runnable again in our own scope */
  swapcontext(&t->ctx, t->sched_return);   /* back to the loop currently driving us */
  /* resumed later: the driving loop restored g_current_task and our roots first */
  return 0;
}

long long __scope_join(long long scope_handle) {
  Scope* s = scope_of(scope_handle);
  if (s == NULL) sprout_fail("__scope_join: null scope");

  /* This loop runs on the caller's stack — main's, or an outer task's when a
   * scope is nested. Save the caller's identity + root context so we restore
   * them after every switch back from a child task; `my_sched` is THIS loop's
   * own return context, so a child's yield returns here and not to an outer or
   * exited join loop. */
  Task*        caller       = g_current_task;
  SproutRoots* caller_roots = sprout_roots_current();
  ucontext_t   my_sched;

  while (s->live > 0) {
    Task* t = rq_pop(s);
    if (t == NULL)
      sprout_fail("__scope_join: scope still live but no runnable task (deadlock)");

    g_current_task  = t;
    t->sched_return = &my_sched;           /* our children return to us */
    sprout_roots_switch(t->roots);         /* switch-point: match roots to the task */
    swapcontext(&my_sched, &t->ctx);       /* run t until it yields or finishes */

    g_current_task = caller;               /* running code is the caller's again */
    sprout_roots_switch(caller_roots);

    if (t->done) {                         /* finished: reclaim its resources */
      sprout_roots_free(t->roots);
      free(t->stack);
      free(t);
    }
  }

  free(s);
  return 0;
}

#ifdef __APPLE__
#pragma clang diagnostic pop
#endif
