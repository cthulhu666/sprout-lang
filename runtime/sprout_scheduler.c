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
#include <unistd.h>   /* close() — force-dropping an unowned-fd park closes the socket */
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

/* L0.8 channels: a bounded buffered queue between tasks. Defined below; forward-declared
 * here because Task points back at the channel it is parked on. */
typedef struct Chan Chan;

/* L0.11 select: one registration of a select-parked task on one channel. Forward-declared here
 * because both Task (its list of registrations) and Chan (its queue of them) point at it. */
typedef struct SelectWaiter SelectWaiter;

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
  int          park_close_fd; /* fd force_drop_task must CLOSE, or -1. Set only by
                               * scheduler_park_on_unowned_fd: an in-flight connect parks on a
                               * bare socket that no handle table owns yet, so a cancel-drop is
                               * the socket's last reference — see that function. */
  long long    park_timer_id; /* opaque timer handle when park_kind == PARK_TIMER (L0.6) */
  int          park_timer_dead; /* 1 once THIS park's timer registration has been torn down.
                                 * The Linux backend close()s the timerfd, so a second teardown
                                 * would close a reused descriptor — every site that tears a
                                 * task's own timer down checks and sets this, making
                                 * exactly-once an explicit invariant rather than one derived
                                 * from which paths happen to be mutually exclusive. */
  int          park_woke_by_timer; /* set by the pump on wake: 1 = the timer fired, 0 = the fd
                                    * became ready. Only meaningful for PARK_FD_TIMER. */
  struct Task* io_next;    /* g_io_head list link (only while parked on I/O or a timer) */
  struct Task* io_prev;
  int          on_io_list; /* 1 iff currently linked on g_io_head (robust membership signal,
                              immune to park_kind being reset by the pump on wake — L0.7 §5.3) */
  int          in_rq;      /* 1 iff currently linked in the ready queue (runnable) — L0.7 §5.2 */
  struct Task* deadline_child; /* non-NULL iff this task is a with_timeout owner parked in
                              __await_deadline, pointing at the body task it is timing (L0.7) */
  /* L0.8 channels. A task parked in chan_send/chan_recv sits on ONE channel wait-queue
   * (never on g_io_head), linked via chan_prev/chan_next. `chan_pending` is a rooted
   * delivery slot (pushed at task_create): the value a send-parked task is holding, or the
   * value handed to a recv-parked task on wake. `chan_is_sender` selects which of the
   * channel's two queues it is on (for force-drop unlink). */
  long long    chan_pending;
  Chan*        park_chan;      /* the channel this task is parked on when park_kind==PARK_CHAN */
  struct Task* chan_prev;      /* channel wait-queue links (send_waiters or recv_waiters) */
  struct Task* chan_next;
  int          chan_is_sender; /* 1 = on the channel's send queue, 0 = on its recv queue */
  int          chan_closed_wake; /* L0.9: 1 iff woken by __chan_close (not a value delivery) — a
                                    recv-parked task then returns Closed, a send-parked one aborts */
  /* L0.11 select. A task parked in chan_select (park_kind==PARK_SELECT) registers on N channels at
   * once, so it cannot use the single chan_prev/chan_next links. `sel_regs` is the head of its own
   * list of SelectWaiter registrations (linked via sib_next), one per channel; it is force-drop's
   * handle to unlink the task from ALL its channels. On wake, the sender/closer sets chan_pending /
   * chan_closed_wake (reused) plus `sel_fired_index` = which channel fired. */
  SelectWaiter* sel_regs;
  long long     sel_fired_index;
} Task;

/* How a task is suspended, so force-drop tears down the right registration (poller fd/timer, a
 * channel wait-queue, or — for select — every channel it registered on) when it drops the task. */
enum { PARK_NONE = 0, PARK_FD = 1, PARK_TIMER = 2, PARK_CHAN = 3, PARK_SELECT = 4,
       /* Registered on an fd AND a timer at once (a bounded read): whichever fires first wakes
        * the task, and the loser's registration is torn down on resume or on force-drop. */
       PARK_FD_TIMER = 5 };

/* L0.8 channel: a bounded buffered FIFO ring shared between tasks. Non-moving malloc; the
 * pointer IS the Int the Sprout `Chan a` value wraps. Owns a SproutRoots context that roots
 * every buffer slot (registered → the collector scans buffered heap values). Freed at the
 * owning scope's __scope_join, when no task can still be parked on it. */
struct Chan {
  long long*   buffer;     /* ring of `cap` slots; each slot address rooted via `roots` */
  long long    cap;
  long long    count;      /* occupied slots */
  long long    head;       /* dequeue index */
  long long    tail;       /* enqueue index */
  SproutRoots* roots;      /* roots buffer[0..cap) so buffered heap values survive collection */
  Task*        send_head;  /* FIFO of send-parked tasks (buffer full), linked via chan_* */
  Task*        send_tail;
  Task*        recv_head;  /* FIFO of recv-parked tasks (buffer empty) */
  Task*        recv_tail;
  SelectWaiter* select_head; /* L0.11: FIFO of select-parked registrations on this channel's recv
                              * side, consulted by chan_send/chan_close IN ADDITION to recv_head */
  SelectWaiter* select_tail;
  int          closed;     /* L0.9: set once by __chan_close; recv returns Closed when drained */
  Scope*       scope;      /* the scope that created it (frees it at join) */
  struct Chan* all_prev;   /* global g_all_chans list (for free-at-join + cancel-walk) */
  struct Chan* all_next;
};

/* L0.11 select: a select-parked task's registration on ONE channel. The task holds a list of these
 * (one per channel it is selecting on) via sib_next; each channel holds a FIFO of them via
 * q_prev/q_next. Pure scheduler memory (task/chan are runtime pointers, sel_index a scalar) — no
 * GC roots; the delivered value rides the task's already-rooted chan_pending. */
struct SelectWaiter {
  Task*         task;      /* the parked selector */
  Chan*         chan;      /* the channel this registration is on */
  long long     sel_index; /* index in the select list, returned to the selector on fire */
  SelectWaiter* q_prev;    /* this channel's select-wait queue links */
  SelectWaiter* q_next;
  SelectWaiter* sib_next;  /* next of THIS task's registrations (for unlink-all-on-fire/drop) */
};

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

/* Every live channel, on one global doubly-linked list. Channels are NOT on g_io_head (a
 * channel-parked task has no poller registration). This list lets __scope_join free a
 * closing scope's channels and lets scope_cancel find channel-parked tasks of a cancelled
 * scope (a task may be parked on an ancestor scope's channel, so cancel filters by the
 * TASK's scope while walking every channel's wait-queues). */
static Chan* g_all_chans = NULL;

static void rq_push(Task* t);   /* defined below; chan_wake enqueues a woken task */

static void all_chans_push(Chan* ch) {
  ch->all_prev = NULL;
  ch->all_next = g_all_chans;
  if (g_all_chans != NULL) g_all_chans->all_prev = ch;
  g_all_chans = ch;
}

static void all_chans_remove(Chan* ch) {
  if (ch->all_prev != NULL) ch->all_prev->all_next = ch->all_next;
  else                      g_all_chans = ch->all_next;
  if (ch->all_next != NULL) ch->all_next->all_prev = ch->all_prev;
  ch->all_prev = ch->all_next = NULL;
}

/* Channel wait-queue (FIFO, doubly-linked so force-drop can O(1)-unlink from the middle).
 * `head`/`tail` are &ch->send_head/&ch->send_tail or the recv pair. */
static void chan_q_push(Task** head, Task** tail, Task* t) {
  t->chan_next = NULL;
  t->chan_prev = *tail;
  if (*tail != NULL) (*tail)->chan_next = t;
  else               *head = t;
  *tail = t;
}

static Task* chan_q_pop(Task** head, Task** tail) {
  Task* t = *head;
  if (t == NULL) return NULL;
  *head = t->chan_next;
  if (*head != NULL) (*head)->chan_prev = NULL;
  else               *tail = NULL;
  t->chan_next = t->chan_prev = NULL;
  return t;
}

static void chan_q_remove(Task** head, Task** tail, Task* t) {
  if (t->chan_prev != NULL) t->chan_prev->chan_next = t->chan_next;
  else                      *head = t->chan_next;
  if (t->chan_next != NULL) t->chan_next->chan_prev = t->chan_prev;
  else                      *tail = t->chan_prev;
  t->chan_next = t->chan_prev = NULL;
}

/* Wake a channel-parked task: clear its park state and make it runnable. The counterparty
 * has already popped it off the wait-queue and set up any delivered value. */
static void chan_wake(Task* t) {
  t->park_kind = PARK_NONE;
  t->park_chan = NULL;
  rq_push(t);
}

/* L0.11 select-wait queue (per channel, on the recv side). Doubly-linked so unlink is O(1). */
static void sw_q_push(Chan* ch, SelectWaiter* w) {
  w->q_next = NULL;
  w->q_prev = ch->select_tail;
  if (ch->select_tail != NULL) ch->select_tail->q_next = w;
  else                         ch->select_head = w;
  ch->select_tail = w;
}

static void sw_q_remove(SelectWaiter* w) {
  Chan* ch = w->chan;
  if (w->q_prev != NULL) w->q_prev->q_next = w->q_next;
  else                   ch->select_head = w->q_next;
  if (w->q_next != NULL) w->q_next->q_prev = w->q_prev;
  else                   ch->select_tail = w->q_prev;
  w->q_prev = w->q_next = NULL;
}

/* Unlink a select-parked task from EVERY channel it registered on and free its registration array
 * (sel_regs points at the array base; siblings are consecutive, chained via sib_next). Must run
 * before the task is woken or reclaimed, so no channel keeps a dangling waiter. */
static void select_unlink_all(Task* t) {
  for (SelectWaiter* w = t->sel_regs; w != NULL; w = w->sib_next) sw_q_remove(w);
  free(t->sel_regs);
  t->sel_regs = NULL;
}

/* Fire a select-parked task: the caller has already set chan_pending / chan_closed_wake. Record
 * which channel won, unlink from all channels, make it runnable. Peek-then-fire (never pop the
 * waiter first): select_unlink_all does the queue removal, so a pop would double-remove. */
static void select_fire(Task* t, long long fired_index) {
  t->sel_fired_index = fired_index;
  select_unlink_all(t);
  t->park_kind = PARK_NONE;
  rq_push(t);
}

/* Try to take a ready value from `ch` WITHOUT parking. Returns 1 (and sets *out_v / *out_closed)
 * when the channel is ready — a buffered element (draining a parked sender into the freed slot), a
 * rendezvous sender parked with a value, or closed-and-empty — else 0 (empty + open: caller parks).
 * Shared by __chan_recv and the __chan_select scan so "is this channel ready and what does it
 * yield" lives in one place. */
static int chan_poll_take(Chan* ch, long long* out_v, int* out_closed) {
  if (ch->count > 0) {
    long long v = ch->buffer[ch->head];
    ch->buffer[ch->head] = 0;             /* clear so a drained slot pins nothing */
    ch->head = (ch->head + 1) % ch->cap;
    ch->count--;
    /* A parked sender (buffer was full): move its value into the slot we just freed, FIFO. */
    Task* sdr = chan_q_pop(&ch->send_head, &ch->send_tail);
    if (sdr != NULL) {
      ch->buffer[ch->tail] = sdr->chan_pending;
      sdr->chan_pending = 0;
      ch->tail = (ch->tail + 1) % ch->cap;
      ch->count++;
      chan_wake(sdr);
    }
    *out_v = v; *out_closed = 0;
    return 1;
  }
  /* Rendezvous (cap 0): a sender is parked waiting to hand its value over — take it directly. */
  Task* rv_sdr = chan_q_pop(&ch->send_head, &ch->send_tail);
  if (rv_sdr != NULL) {
    long long v = rv_sdr->chan_pending;
    rv_sdr->chan_pending = 0;
    chan_wake(rv_sdr);
    *out_v = v; *out_closed = 0;
    return 1;
  }
  /* Empty + closed: end of stream. */
  if (ch->closed) { *out_closed = 1; return 1; }
  return 0;                               /* empty + open: not ready */
}

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

/* Chan handle ABI: __chan_new returns the Chan* encoded as the i64 the Sprout `Chan a`
 * value wraps; chan_send/chan_recv decode it. Valid until the owning scope closes. */
static Chan* chan_of(long long handle) { return (Chan*)(intptr_t)handle; }

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
        int   is_timer[64];
        int n = sprout_poll_wait(toks, is_timer, 64);   /* blocks in kqueue/epoll */
        for (int i = 0; i < n; i++) {         /* each ready fd / fired timer wakes its task */
          Task* w = (Task*)toks[i];
          /* A harvested TIMER is spent, and the pump is the only party that knows it fired — so
           * the pump owns its teardown. On kqueue a fired one-shot is already gone and EV_DELETE
           * is a no-op, which is why skipping this was invisible there; on Linux the timerfd stays
           * OPEN until removed, so every fired deadline leaked a descriptor. `with_timeout` hit
           * this on all four of its expiry paths (__await_deadline returns without tearing the
           * timer down), and the pre-existing timeout fixtures each expire only one or two timers
           * per process, so it never showed. */
          if (is_timer[i] && !w->park_timer_dead) {
            sprout_poll_remove_timer(w->park_timer_id);
            w->park_timer_dead = 1;
          }
          /* A PARK_FD_TIMER task has TWO registrations, so both can be reported in one batch. The
           * first event wakes and unlinks it; the second must still be accounted for above (a
           * fired timerfd has to be closed) but must NOT wake it a second time — a double
           * rq_push would self-cycle the ready queue. Membership on g_io_head is the signal.
           * Every other park kind has a single registration and so cannot appear twice. */
          if (!w->on_io_list) continue;
          w->park_woke_by_timer = is_timer[i];
          io_list_remove(w);                  /* no longer poller-parked */
          w->park_kind = PARK_NONE;
          w->park_fd = -1;
          w->park_close_fd = -1;   /* woken, not dropped: the parker owns the fd again */
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
      if (!aw->park_timer_dead) {           /* still linked => the timer never fired => live */
        sprout_poll_remove_timer(aw->park_timer_id);
        aw->park_timer_dead = 1;
      }
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
  g_task0.park_close_fd = -1;
  g_task0.park_timer_id = 0;
  g_task0.park_timer_dead = 0;
  g_task0.park_woke_by_timer = 0;
  g_task0.io_next = NULL;
  g_task0.io_prev = NULL;
  g_task0.on_io_list = 0;
  g_task0.in_rq = 0;
  g_task0.deadline_child = NULL;
  g_task0.chan_pending = 0;
  g_task0.park_chan = NULL;
  g_task0.chan_prev = NULL;
  g_task0.chan_next = NULL;
  g_task0.chan_is_sender = 0;
  g_task0.chan_closed_wake = 0;
  g_task0.sel_regs = NULL;
  g_task0.sel_fired_index = 0;
  /* task-0 runs with_scope bodies that send/recv on channels; root its delivery slot. */
  sprout_roots_push_ptr(g_task0.roots, &g_task0.chan_pending);
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
  t->park_close_fd = -1;   /* set only while parked on an fd no handle table owns */
  t->park_timer_id = 0;
  t->park_timer_dead = 0;
  t->park_woke_by_timer = 0;
  t->io_next    = NULL;
  t->io_prev    = NULL;
  t->on_io_list = 0;
  t->in_rq      = 0;       /* rq_push below sets it once the task is enqueued */
  t->deadline_child = NULL;
  t->chan_pending = 0;
  t->park_chan    = NULL;
  t->chan_prev    = NULL;
  t->chan_next    = NULL;
  t->chan_is_sender = 0;
  t->chan_closed_wake = 0;
  t->sel_regs     = NULL;
  t->sel_fired_index = 0;

  /* Keep the work-closure reachable from spawn until the task first runs: root
   * &work in the task's own context (scanned by the collector via the registry).
   * Stable because Task is a non-moving malloc. Root &chan_pending too: it is the
   * delivery slot for channel ops (a send-parked task's held value, or a value
   * handed to a recv-parked task) — always rooted, holds 0 when idle. */
  sprout_roots_push_ptr(t->roots, &t->work);
  sprout_roots_push_ptr(t->roots, &t->chan_pending);

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
  /* Deregister whatever it is suspended on. A channel-parked task is NOT on g_io_head — it
   * sits on one of its channel's wait-queues; unlink it there (its chan_pending value dies
   * with the task: never delivered, nobody else references it — correct). Otherwise it is
   * poller-parked (on g_io_head): for a timer this must discard even an already-fired-but-
   * undrained event (timers fire async to the pump, unlike fds which are drained before the
   * owner runs) — else a stale token would later resume a freed task (task_sleep design §5.1). */
  if (t->park_kind == PARK_SELECT) {
    /* L0.11: a select-parked task sits on the select queue of EVERY channel it listed. Unlink it
     * from all of them (and free its registration array). It appears at most once per channel, so
     * a later cancel-walk of another channel no longer sees it — no double-drop. */
    select_unlink_all(t);
  } else if (t->park_kind == PARK_CHAN) {
    Chan* ch = t->park_chan;
    if (t->chan_is_sender) chan_q_remove(&ch->send_head, &ch->send_tail, t);
    else                   chan_q_remove(&ch->recv_head, &ch->recv_tail, t);
    t->park_chan = NULL;
  } else {
    /* PARK_FD_TIMER holds BOTH registrations, so a force-drop has to tear down both. */
    if (t->park_kind == PARK_TIMER || t->park_kind == PARK_FD_TIMER) {
      if (!t->park_timer_dead) {
        sprout_poll_remove_timer(t->park_timer_id);
        t->park_timer_dead = 1;
      }
    }
    if (t->park_kind != PARK_TIMER) sprout_poll_remove(t->park_fd, t->park_interest);
    io_list_remove(t);
    /* An unowned-fd park (in-flight connect) leaves the socket referenced ONLY by the parked
     * frame we are about to free — no handle table entry, so nothing else can ever close it.
     * Without this, each timed-out connect would leak a descriptor. */
    if (t->park_close_fd >= 0) {
      close(t->park_close_fd);
      t->park_close_fd = -1;
    }
  }
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

  /* Force-drop this scope's channel-parked tasks. They are not on g_io_head, so walk every
   * live channel's send/recv wait-queues and drop tasks whose scope is s — a task of s may be
   * parked on an ancestor scope's channel, so we filter by the TASK's scope, not the channel's.
   * (Same single-thread no-race reasoning as the g_io_head walk: the owner is running, so no
   * counterparty is concurrently waking these tasks.) */
  for (Chan* ch = g_all_chans; ch != NULL; ch = ch->all_next) {
    for (Task* t = ch->send_head; t != NULL; ) {
      Task* next = t->chan_next;          /* save before force_drop unlinks t */
      if (t->scope == s && t != g_current_task) force_drop_task(t);
      t = next;
    }
    for (Task* t = ch->recv_head; t != NULL; ) {
      Task* next = t->chan_next;
      if (t->scope == s && t != g_current_task) force_drop_task(t);
      t = next;
    }
    /* L0.11: select-waiters. Dropping one unlinks its task from EVERY channel it listed, which can
     * free a `next` we cached on this channel (a duplicate-channel select), so re-read the head
     * after each drop rather than following a saved link. A dropped task's waiters are all gone, so
     * the head advances past them — progress is guaranteed and no waiter is dropped twice. */
    for (SelectWaiter* w = ch->select_head; w != NULL; ) {
      if (w->task->scope == s && w->task != g_current_task) {
        force_drop_task(w->task);
        w = ch->select_head;
      } else {
        w = w->q_next;
      }
    }
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
  owner->park_kind        = PARK_TIMER;
  owner->park_timer_id    = tid;
  owner->park_timer_dead  = 0;          /* fresh registration */
  owner->deadline_child   = child;
  io_list_push(owner);
  child->awaiter = owner;
  park_to_pump();

  owner->deadline_child = NULL;         /* no longer a deadline-owner */

  if (child->done) {
    /* Completed within the deadline. The child-first trampoline path already tore our timer
     * down and unlinked us; if instead the timer harvested us and the child then finished, the
     * timer is already consumed. If we are somehow still linked, tear it down. */
    if (owner->on_io_list) {
      if (!owner->park_timer_dead) {
        sprout_poll_remove_timer(owner->park_timer_id);
        owner->park_timer_dead = 1;
      }
      io_list_remove(owner);
    }
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
  if (child->park_kind == PARK_CHAN || child->park_kind == PARK_SELECT) {
    /* Parked in chan_send/chan_recv (PARK_CHAN) or chan_select (PARK_SELECT) — droppable, same
     * MVP-supported class as direct I/O. Not on g_io_head, so this is distinct from the on_io_list
     * branch above; force_drop tears down the channel/select registration(s). */
    s->reason = REASON_TIMEDOUT;
    child->awaiter = NULL;
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
  /* Free this scope's channels. Every task spawned into s has finished, and inner scopes have
   * already joined (structured nesting), so no task is parked on any channel of s — safe to
   * free its root context, buffer, and record, and unlink it from the global channel list. */
  for (Chan* ch = g_all_chans; ch != NULL; ) {
    Chan* next = ch->all_next;            /* save before all_chans_remove unlinks ch */
    if (ch->scope == s) {
      sprout_roots_free(ch->roots);
      free(ch->buffer);
      all_chans_remove(ch);
      free(ch);
    }
    ch = next;
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

/* Park until `fd` is ready for `interest` OR `ms` elapse. Returns 1 = fd ready, 0 = timed out.
 *
 * The task is NOT dropped when the deadline wins — it resumes and its caller reports a timeout,
 * leaving the socket valid and every linear release path intact. That is the Go/Java/Erlang model
 * (SetReadDeadline / SO_TIMEOUT / gen_tcp:recv Timeout) rather than the cancel-the-task model of
 * `with_timeout`, and it is the only one that composes with linearity here: a force-dropped task
 * never runs its `close`, and a per-connection deadline in the cancel model would also cost an
 * extra green stack per connection.
 *
 * Both registrations carry this task as their token, so BOTH can be reported in a single poll
 * batch; the pump handles that (it wakes the task once and still accounts for a fired timer), and
 * `park_timer_dead` keeps the timer's teardown exactly-once across the pump and this function. */
int scheduler_park_on_fd_timeout(int fd, int interest, long long ms) {
  Task* t = g_current_task;
  long long tid;
  sprout_poll_add(fd, interest, t);
  sprout_poll_add_timer(ms, t, &tid);
  t->park_kind          = PARK_FD_TIMER;
  t->park_fd            = fd;
  t->park_interest      = interest;
  t->park_timer_id      = tid;
  t->park_timer_dead    = 0;
  t->park_woke_by_timer = 0;
  io_list_push(t);
  park_to_pump();
  /* Resumed. Retire the loser: poll_remove is documented idempotent, so it is safe whether or not
   * the fd fired; the timer needs the flag because its teardown closes a descriptor. */
  sprout_poll_remove(fd, interest);
  if (!t->park_timer_dead) {
    sprout_poll_remove_timer(tid);
    t->park_timer_dead = 1;
  }
  return t->park_woke_by_timer ? 0 : 1;
}

/* scheduler_park_on_fd for an fd that NO handle table owns yet — currently only tcp_connect's
 * in-flight connect, which parks on writability of a bare socket() result.
 *
 * Every other park is on an fd reachable from a handle (a listener, or a connection), so a
 * cancel-drop can leave it open and the handle's owner still closes it. A connect-in-progress fd
 * is reachable only from the parked frame, which force_drop_task frees — so the drop is the last
 * chance to close it, and it does so via park_close_fd. On a normal wake the pump clears the
 * field: the resumed caller owns the fd again and closes it (or installs it in the table). */
void scheduler_park_on_unowned_fd(int fd, int interest) {
  g_current_task->park_close_fd = fd;
  scheduler_park_on_fd(fd, interest);
  g_current_task->park_close_fd = -1;   /* belt-and-braces; the pump already cleared it on wake */
}

/* Suspend the current task on a one-shot timer for `ms` (> 0) milliseconds; the pump runs
 * other tasks and poller-blocks meanwhile, and resumes us when the timer fires. Parks on
 * g_io_head exactly like an fd wait, so scope_cancel force-drops a sleeping task too.
 * Reached only with ms > 0 — the stdlib task_sleep wrapper routes ms <= 0 to task_yield
 * (a zero-value timerfd disarms on Linux → would hang; design §5.2). */
static void scheduler_park_on_timer(long long ms) {
  long long tid;
  sprout_poll_add_timer(ms, g_current_task, &tid);
  g_current_task->park_kind       = PARK_TIMER;
  g_current_task->park_timer_id   = tid;
  g_current_task->park_timer_dead = 0;   /* fresh registration */
  io_list_push(g_current_task);
  park_to_pump();
  /* Resumed after the timer fired, which means the PUMP already tore it down on harvest (it is
   * the single owner of a fired timer's teardown — see pump_loop). Tearing it down again here
   * would close a reused descriptor on Linux. The cancel-drop path is mutually exclusive with
   * this one either way: a dropped task never resumes. */
  (void)tid;
}

/* task_sleep(ms) with ms > 0 (the wrapper handles ms <= 0). Returns Unit (0). */
long long __task_sleep(long long ms) {
  scheduler_park_on_timer(ms);
  return 0;
}

/* ── L0.8 channels ─────────────────────────────────────────────────────────
 * Bounded buffered FIFO between tasks. chan_send parks when the buffer is full;
 * chan_recv parks when empty. Delivery is a direct handoff by the counterparty (no
 * condvar re-check): the waker moves the value and enqueues the parked task, which then
 * just completes. Every task's chan_pending is rooted (task_create), so a value in flight
 * across a park stays live. See docs/concurrency-channels-design-2026-07-16.md §5, §11.
 *
 * Invariants (both from cap >= 1, so send-parked and recv-parked never coexist on one chan):
 *   send_waiters non-empty  =>  count == cap  (a sender parks only on a full buffer)
 *   recv_waiters non-empty  =>  count == 0    (a receiver parks only on an empty buffer)
 */

/* Create a cap-slot buffered channel in `scope`. cap must be >= 1 (rendezvous deferred). */
long long __chan_new(long long scope_handle, long long capacity) {
  Scope* s = scope_of(scope_handle);
  if (s == NULL) sprout_fail("__chan_new: null scope");
  if (capacity < 0)
    sprout_fail("__chan_new: capacity must be >= 0 (0 = rendezvous / unbuffered)");
  Chan* ch = (Chan*)malloc(sizeof(Chan));
  if (ch == NULL) sprout_fail("__chan_new: out of memory (channel record)");
  ch->cap   = capacity;
  ch->count = 0;
  ch->head  = 0;
  ch->tail  = 0;
  ch->send_head = ch->send_tail = NULL;
  ch->recv_head = ch->recv_tail = NULL;
  ch->select_head = ch->select_tail = NULL;
  ch->closed = 0;
  ch->scope = s;
  if (capacity == 0) {
    /* Rendezvous: no buffer — every value is handed directly from a sender to a receiver, so
     * there is nothing to root. Keep an empty (registered) roots context so the free path at
     * __scope_join stays uniform, and avoid malloc(0) (implementation-defined, may return NULL
     * which the OOM checks would misread). The pending value lives in the parked task's own
     * `chan_pending`, already rooted by that task's root context. */
    ch->buffer = NULL;
    ch->roots  = sprout_roots_new(1);
  } else {
    ch->buffer = (long long*)malloc((size_t)capacity * sizeof(long long));
    if (ch->buffer == NULL) sprout_fail("__chan_new: out of memory (channel buffer)");
    /* Root every buffer slot once (addresses are stable — the buffer is a fixed malloc). Empty
     * slots hold 0; the mark path is membership-guarded, so a scalar/0 slot is a safe no-op. */
    ch->roots = sprout_roots_new((size_t)capacity);
    for (long long i = 0; i < capacity; i++) {
      ch->buffer[i] = 0;
      sprout_roots_push_ptr(ch->roots, &ch->buffer[i]);
    }
  }
  all_chans_push(ch);
  return (long long)(intptr_t)ch;
}

/* Send `value` into `ch`. Hands directly to a waiting receiver, else buffers, else (full)
 * parks the sender holding the value until a receiver frees a slot. */
long long __chan_send(long long chan_handle, long long value) {
  Chan* ch = chan_of(chan_handle);
  if (ch == NULL) sprout_fail("__chan_send: null channel");
  /* Sending into a closed channel is a program bug (Go/Kotlin: send-after-close is an error).
   * Sprout has no recovery, so abort rather than silently dropping the value. */
  if (ch->closed) sprout_fail("__chan_send: send on closed channel");

  /* A waiting receiver (=> buffer empty): hand off directly, buffer stays empty. A plain
   * chan_recv parker takes priority over a select-waiter (documented; Go guarantees no
   * cross-select fairness anyway). */
  Task* r = chan_q_pop(&ch->recv_head, &ch->recv_tail);
  if (r != NULL) {
    r->chan_pending = value;            /* rooted via &r->chan_pending; no alloc before wake */
    chan_wake(r);
    return 0;
  }
  /* A waiting select-waiter (=> this channel was empty when it registered): hand off directly and
   * unregister it from its other channels. Peek (don't pop) — select_fire does the queue removal. */
  SelectWaiter* sw = ch->select_head;
  if (sw != NULL) {
    Task* st = sw->task;
    st->chan_pending = value;           /* rooted via &st->chan_pending; no alloc before wake */
    st->chan_closed_wake = 0;
    select_fire(st, sw->sel_index);
    return 0;
  }
  /* Space in the buffer: enqueue at the tail. */
  if (ch->count < ch->cap) {
    ch->buffer[ch->tail] = value;
    ch->tail = (ch->tail + 1) % ch->cap;
    ch->count++;
    return 0;
  }
  /* Full: park holding the value until a receiver moves it into the freed slot. */
  Task* self = g_current_task;
  self->chan_pending    = value;
  self->park_kind       = PARK_CHAN;
  self->park_chan       = ch;
  self->chan_is_sender  = 1;
  chan_q_push(&ch->send_head, &ch->send_tail, self);
  park_to_pump();
  /* Resumed either by a receiver that moved chan_pending into the buffer (normal), or by
   * chan_close, which wakes send-parked tasks to abort (the value is never delivered). */
  if (self->chan_closed_wake) sprout_fail("__chan_send: send on closed channel");
  return 0;
}

/* Receive the next value from `ch` (FIFO), as a `Recv a` (L0.9). Returns `Got v` while values
 * remain — buffered values drain before any close is observed, waking a parked sender to refill
 * the freed slot. On an empty channel: `Closed` if it was closed (never parks), else park until a
 * sender hands over a value or chan_close wakes us with `Closed`. */
long long __chan_recv(long long chan_handle) {
  Chan* ch = chan_of(chan_handle);
  if (ch == NULL) sprout_fail("__chan_recv: null channel");

  /* Ready (buffered value / parked rendezvous sender / closed-and-drained): take without parking.
   * Buffered values drain as Got before Closed is observed — chan_poll_take checks count first. */
  long long v; int closed;
  if (chan_poll_take(ch, &v, &closed))
    return closed ? sprout_chan_make_closed() : sprout_chan_make_got(v);
  /* Empty + open: park until a sender places a value in our chan_pending, or chan_close wakes us. */
  Task* self = g_current_task;
  self->park_kind      = PARK_CHAN;
  self->park_chan      = ch;
  self->chan_is_sender = 0;
  chan_q_push(&ch->recv_head, &ch->recv_tail, self);
  park_to_pump();
  /* Resumed by a sender (value in chan_pending) or by chan_close (no value → Closed). */
  if (self->chan_closed_wake) {
    self->chan_closed_wake = 0;
    return sprout_chan_make_closed();
  }
  v = self->chan_pending;
  self->chan_pending = 0;
  return sprout_chan_make_got(v);
}

/* Close `ch`: signal end-of-stream. Sets the closed flag, then wakes every parked task — recv-
 * parked tasks return `Closed` (buffer is empty by the recv_head-non-empty ⟹ count==0 invariant),
 * send-parked tasks abort on resume (send on closed). Their held values are dropped (never
 * delivered; nobody references them). Any task holding the channel may close it. A double close is
 * a synchronization bug → loud-fail (Go panics on double-close). */
long long __chan_close(long long chan_handle) {
  Chan* ch = chan_of(chan_handle);
  if (ch == NULL) sprout_fail("__chan_close: null channel");
  if (ch->closed) sprout_fail("__chan_close: channel already closed");
  ch->closed = 1;
  for (Task* r = chan_q_pop(&ch->recv_head, &ch->recv_tail); r != NULL;
       r = chan_q_pop(&ch->recv_head, &ch->recv_tail)) {
    r->chan_closed_wake = 1;
    chan_wake(r);
  }
  for (Task* sdr = chan_q_pop(&ch->send_head, &ch->send_tail); sdr != NULL;
       sdr = chan_q_pop(&ch->send_head, &ch->send_tail)) {
    sdr->chan_closed_wake = 1;
    chan_wake(sdr);
  }
  /* L0.11: wake select-waiters on this channel with Closed. select_fire unlinks each from ALL its
   * channels (advancing select_head), so re-peek the head each iteration until the queue drains. */
  for (SelectWaiter* sw = ch->select_head; sw != NULL; sw = ch->select_head) {
    Task* st = sw->task;
    st->chan_closed_wake = 1;
    select_fire(st, sw->sel_index);
  }
  return 0;
}

/* L0.11 select. Wait on N channels of one element type; return the index of the channel that
 * became ready first and its Recv outcome, as `Selected Int (Recv a)`. First a synchronous scan
 * (lowest-index ready channel wins — the tie-break); if none is ready, register a SelectWaiter on
 * every channel and park until a sender/closer delivers into chan_pending and fires us. */
long long __chan_select(long long list_handle) {
  /* Walk the List Int of channel handles into a C array (sprout_list_next hides the Nil/Cons tag
   * lookup, which is static in the runtime TU). Two passes: count, then fill. */
  long long n = 0, head, tail;
  for (long long cur = list_handle; sprout_list_next(cur, &head, &tail); cur = tail) n++;
  if (n == 0) sprout_fail("__chan_select: empty channel list (a select with no cases can never proceed)");
  Chan** chans = (Chan**)malloc((size_t)n * sizeof(Chan*));
  if (chans == NULL) sprout_fail("__chan_select: out of memory (channel array)");
  {
    long long i = 0;
    for (long long cur = list_handle; sprout_list_next(cur, &head, &tail); cur = tail) {
      Chan* ch = chan_of(head);
      if (ch == NULL) { free(chans); sprout_fail("__chan_select: null channel in list"); }
      chans[i++] = ch;
    }
  }
  /* Synchronous scan: take from the lowest-index ready channel. */
  for (long long i = 0; i < n; i++) {
    long long v; int closed;
    if (chan_poll_take(chans[i], &v, &closed)) {
      long long sel = sprout_chan_make_selected(i, closed ? sprout_chan_make_closed()
                                                          : sprout_chan_make_got(v));
      free(chans);
      return sel;
    }
  }
  /* None ready: register one SelectWaiter per channel and park. The registration array is pure
   * scheduler memory (no Sprout heap pointers → no GC roots); the delivered value rides the
   * already-rooted chan_pending. */
  Task* self = g_current_task;
  SelectWaiter* regs = (SelectWaiter*)malloc((size_t)n * sizeof(SelectWaiter));
  if (regs == NULL) { free(chans); sprout_fail("__chan_select: out of memory (waiters)"); }
  for (long long i = 0; i < n; i++) {
    regs[i].task      = self;
    regs[i].chan      = chans[i];
    regs[i].sel_index = i;
    regs[i].q_prev = regs[i].q_next = NULL;
    regs[i].sib_next  = (i + 1 < n) ? &regs[i + 1] : NULL;
    sw_q_push(chans[i], &regs[i]);
  }
  free(chans);
  self->sel_regs   = regs;
  self->park_kind  = PARK_SELECT;
  park_to_pump();
  /* Resumed: a sender/closer set chan_pending / chan_closed_wake + sel_fired_index and unlinked us
   * from every channel (sel_regs freed and NULLed by select_unlink_all). */
  long long i = self->sel_fired_index;
  if (self->chan_closed_wake) {
    self->chan_closed_wake = 0;
    return sprout_chan_make_selected(i, sprout_chan_make_closed());
  }
  long long v = self->chan_pending;
  self->chan_pending = 0;
  return sprout_chan_make_selected(i, sprout_chan_make_got(v));
}

#ifdef __APPLE__
#pragma clang diagnostic pop
#endif
