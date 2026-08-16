/* Execution-context seam for the green-thread scheduler.
 *
 * The scheduler switches between a pump and N green tasks. That is the one part
 * of the runtime with no portable spelling: POSIX offers ucontext, Windows offers
 * Fibers, and the two disagree about who owns the stack — makecontext runs on a
 * stack you hand it, CreateFiberEx allocates its own and returns an opaque handle.
 * So SproutCtx OWNS its stack, and no operation here takes a caller-supplied one.
 *
 * Four operations cover every use in sprout_scheduler.c:
 *
 *   adopt_current  the thread already running (task-0 / main), whose stack the
 *                  runtime did not allocate and must never free
 *   create         a fresh context that begins executing `entry` on a new stack
 *   switch         suspend one context, resume another
 *   destroy        release the stack of a context that is not running
 *
 * This header must be the FIRST include in any translation unit that uses it: the
 * POSIX arm's feature-test macros select the ucontext declarations, and a
 * feature-test macro is inert once any libc header has been read.
 *
 * Design and rationale: docs/windows-port-v0.md §4.4, §4.6.
 */
#ifndef SPROUT_CONTEXT_H
#define SPROUT_CONTEXT_H

#include <stddef.h>

/* Returned by sprout_ctx_create / sprout_ctx_adopt_current. Codes rather than an
 * internal abort, so each caller keeps its own diagnostic wording. */
#define SPROUT_CTX_ENOMEM (-1)   /* no memory for the stack */
#define SPROUT_CTX_EPRIME (-2)   /* the context could not be initialized */

#if defined(_WIN32)

/* ─────────────────────────── Windows: Win32 Fibers ───────────────────────────
 *
 * Fibers are the direct analogue: cooperatively scheduled, one runs per thread,
 * and switching is explicit. The mapping is exact — ConvertThreadToFiber for the
 * thread we are already on, CreateFiberEx for the pump and every green task.
 *
 * "Only fibers can execute other fibers", so the adopt must happen before any
 * switch. sprout_scheduler_init is a constructor and adopts task-0 there, which
 * is early enough for every path into the scheduler.
 */

/* Windows 10 is the port's floor — WSAPoll does not report failed connects before
 * version 2004 (docs/windows-port-v0.md §4.3). The fiber API itself only needs
 * 0x0400. */
#ifndef _WIN32_WINNT
#define _WIN32_WINNT 0x0A00
#endif

#include <windows.h>

typedef struct SproutCtx {
  LPVOID fiber;    /* CreateFiberEx handle, or this thread's own from ConvertThreadToFiber */
  int    adopted;  /* 1 = the thread's own fiber; DeleteFiber would kill the thread */
} SproutCtx;

/* CreateFiberEx wants VOID WINAPI f(LPVOID); the seam's entry points take no
 * argument. Trampoline rather than cast between incompatible function-pointer
 * types — the cast happens to work on x64 (WINAPI is a no-op there and the extra
 * argument is ignored) but is wrong under 32-bit stdcall. The entry travels in
 * lpParameter, which exists for exactly this. */
static VOID WINAPI sprout_ctx_fiber_entry(LPVOID param) {
  ((void (*)(void))param)();
  /* Unreachable: every entry leaves by switching out. If one ever returned, the
   * thread running the fiber would exit — which is why that is a precondition and
   * not a detail. */
}

static inline int sprout_ctx_adopt_current(SproutCtx* c) {
  c->adopted = 1;
  c->fiber   = ConvertThreadToFiber(NULL);
  return (c->fiber == NULL) ? SPROUT_CTX_EPRIME : 0;
}

static inline int sprout_ctx_create(SproutCtx* c, void (*entry)(void), size_t stack_bytes) {
  c->adopted = 0;
  /* commit 0 = the executable's default, so pages are backed as the stack grows —
   * matching the POSIX arm, where malloc'd pages are equally only backed once
   * touched. Committing stack_bytes up front would make every task cost 1 MiB of
   * real memory. Reserve is the same size the POSIX arm allocates; Windows'
   * own default reserve is 1 MiB, which SPROUT_TASK_STACK_BYTES already matches.
   *
   * FIBER_FLAG_FLOAT_SWITCH adds CONTEXT_FLOATING_POINT to what the fiber saves.
   * Sprout compiles Float arithmetic to real `double` instructions, so unsaved FP
   * state is silent data corruption across a yield. It is redundant on x86-64 and
   * ARM64, where winnt.h defines CONTEXT_FULL to already include the FP bit, and
   * load-bearing on 32-bit x86, where CONTEXT_FULL is CONTROL|INTEGER|SEGMENTS and
   * omits it. Passed unconditionally: it costs nothing where it is redundant, and
   * the bug it prevents is invisible. */
  c->fiber = CreateFiberEx((SIZE_T)0, (SIZE_T)stack_bytes, FIBER_FLAG_FLOAT_SWITCH,
                           sprout_ctx_fiber_entry, (LPVOID)(void*)entry);
  return (c->fiber == NULL) ? SPROUT_CTX_ENOMEM : 0;
}

static inline void sprout_ctx_switch(SproutCtx* from, SproutCtx* to) {
  (void)from;   /* precondition: `from` is the running fiber, which SwitchToFiber implies */
  SwitchToFiber(to->fiber);
}

static inline void sprout_ctx_destroy(SproutCtx* c) {
  /* Deleting the thread's OWN fiber makes the thread call ExitThread, so an
   * adopted context releases nothing — same as the POSIX arm, where task-0 owns no
   * malloc'd stack. Idempotent for the same reason free(NULL) is. */
  if (c->fiber != NULL && !c->adopted) DeleteFiber(c->fiber);
  c->fiber = NULL;
}

#else

/* ──────────────────────────────── POSIX: ucontext ─────────────────────────── */

/* ucontext on macOS lives behind these; define before any include pulls in
 * <sys/ucontext.h>. */
#define _XOPEN_SOURCE 700
#define _DARWIN_C_SOURCE 1

#include <stdlib.h>
#include <ucontext.h>

/* macOS marks getcontext/makecontext/swapcontext deprecated; we knowingly use
 * them. Scoped to this header, so the suppression covers the four calls that
 * need it rather than every line of the scheduler. */
#ifdef __APPLE__
#pragma clang diagnostic push
#pragma clang diagnostic ignored "-Wdeprecated-declarations"
#endif

/* A suspended (or running) execution context. `stack` is owned: non-NULL exactly
 * when this context was created by sprout_ctx_create and not yet destroyed. It is
 * a non-moving malloc because GC root slots are addresses INTO it, read while the
 * task is suspended (see sprout_scheduler.h). */
typedef struct SproutCtx {
  ucontext_t uc;
  void*      stack;
} SproutCtx;

/* Adopt the caller's own thread of execution as a context. Its stack belongs to
 * the OS, so nothing is allocated and destroy is a no-op; `uc` is filled by the
 * first switch OUT of this context. Cannot fail here — the return type exists for
 * the Windows arm, where ConvertThreadToFiber can. */
static inline int sprout_ctx_adopt_current(SproutCtx* c) {
  c->stack = NULL;
  return 0;
}

/* Prime `c` to begin executing `entry` on a fresh stack of `stack_bytes`.
 * `entry` must never return — every context here leaves by switching out.
 * Today both callers word EPRIME as "getcontext failed"; the Windows arm cannot
 * produce EPRIME at all, so that wording stays accurate where it is used. */
static inline int sprout_ctx_create(SproutCtx* c, void (*entry)(void), size_t stack_bytes) {
  c->stack = malloc(stack_bytes);
  if (c->stack == NULL) return SPROUT_CTX_ENOMEM;
  if (getcontext(&c->uc) != 0) {      /* initializes uc before makecontext reads its uc_* fields */
    free(c->stack);
    c->stack = NULL;
    return SPROUT_CTX_EPRIME;
  }
  c->uc.uc_stack.ss_sp   = c->stack;
  c->uc.uc_stack.ss_size = stack_bytes;
  c->uc.uc_link          = NULL;      /* `entry` never returns; nothing to resume */
  makecontext(&c->uc, entry, 0);
  return 0;
}

/* Suspend `from` and resume `to`. `from` MUST be the context currently executing:
 * Windows' SwitchToFiber names only the destination, so a switch whose source is
 * some third context has no implementation there. */
static inline void sprout_ctx_switch(SproutCtx* from, SproutCtx* to) {
  swapcontext(&from->uc, &to->uc);
}

/* Release `c`'s stack. Idempotent, and a no-op on an adopted context. The caller
 * must not be executing on `c` — the scheduler destroys a context only from the
 * pump, which runs on its own. */
static inline void sprout_ctx_destroy(SproutCtx* c) {
  free(c->stack);
  c->stack = NULL;
}

#ifdef __APPLE__
#pragma clang diagnostic pop
#endif

#endif /* _WIN32 */

#endif /* SPROUT_CONTEXT_H */
