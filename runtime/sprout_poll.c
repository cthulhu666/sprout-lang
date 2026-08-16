/* Readiness poller for Sprout L0.3 I/O parking (EXPERIMENTAL).
 *
 * A thin, internal (not Sprout-visible) layer over kqueue (macOS/BSD), epoll (Linux)
 * and WSAPoll (Windows). The scheduler (sprout_scheduler.c) owns the task<->fd
 * association; this layer only stores an opaque per-registration token and hands it
 * back when the fd becomes ready. All three backends are readiness APIs (register
 * interest -> notified when readable/writable -> the caller does the non-blocking
 * read/write itself); see epoll(7) / kqueue(2) / WSAPoll on Microsoft Learn.
 *
 * Registration is ONE-SHOT: an fd is watched for exactly one readiness event, then
 * dropped. The scheduler re-registers on the next EAGAIN. This matches the
 * cooperative model (one task waits on a given fd at a time) and keeps the poller
 * stateless between waits — the simplest thing that is correct. Edge-triggered is
 * a later optimization, not needed for correctness.
 *
 * The platform split is THREE-WAY and Windows comes first. It cannot be an arm after
 * the POSIX `#else`, which reads "not macOS" as "Linux".
 */
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#ifndef _WIN32
#include <unistd.h>   /* close() — the epoll arm's per-timer timerfd */
#endif
#include "sprout_scheduler.h"

#if defined(_WIN32)
/* ── Windows: WSAPoll for sockets, a deadline min-heap for timers ────────────
 *
 * The asymmetry that shapes this backend: kqueue and epoll each expose a timer AS A
 * POLLABLE OBJECT (EVFILT_TIMER, timerfd), so on those platforms one kernel wait covers
 * both readiness and deadlines. Windows has no timerfd equivalent, so deadlines live in
 * a min-heap here and the nearest one becomes the wait's timeout. Two consequences the
 * POSIX arms never face:
 *
 *   1. A wait whose parked set is ENTIRELY timers has no socket to pass. WSAPoll cannot
 *      express that — "The array must contain at least one structure with a valid socket",
 *      and it returns WSAEINVAL "if none of the sockets ... were valid". That set is not
 *      exotic: a lone task_sleep produces it. Such a wait uses Sleep() instead; see
 *      sprout_poll_wait for why that cannot miss a wakeup.
 *   2. Arming a timer allocates no descriptor, so sprout_poll_add_timer's documented
 *      "could not arm, returns 0" path — a real failure under `ulimit -n` on epoll — is
 *      unreachable here short of the heap's realloc failing.
 *
 * OS floor is Windows 10 version 2004, set by WSAPoll's history rather than its presence:
 * only from that version does a TCP socket that FAILS to connect report
 * (POLLHUP|POLLERR|POLLWRNORM). Before it a failed connect() was silent, which would hang
 * tcp_connect's park forever.
 */
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef _WIN32_WINNT
#define _WIN32_WINNT 0x0A00   /* Windows 10 */
#endif
#include <winsock2.h>
#include <windows.h>
#include <limits.h>

/* Socket registrations: a WSAPOLLFD array plus a parallel token array, kept COMPACT
 * (no holes) so g_fd_len is both the array length WSAPoll is given and the live count
 * the empty-set test needs. WSAPoll would also let us park a hole as a negative fd and
 * ignore it, but then those two numbers diverge and every caller has to remember which
 * one it wanted. Keyed by fd alone, like epoll — kqueue can hold a read and a write
 * knote on one fd, but the scheduler never registers both, so the narrower key is
 * enough and a re-add replaces in place exactly as EPOLL_CTL_MOD does. */
static WSAPOLLFD* g_fds       = NULL;
static void**     g_fd_tokens = NULL;
static int        g_fd_len    = 0;
static int        g_fd_cap    = 0;

/* Deadline min-heap. `id` is monotonically increasing and never reused, which is what
 * makes sprout_poll_remove_timer safe against a stale token: an id belonging to a fired
 * (already harvested) timer matches nothing rather than matching a new timer that landed
 * in the same slot. */
typedef struct {
  long long deadline;   /* GetTickCount64() ms */
  long long id;
  void*     token;
} WinTimer;

static WinTimer*  g_timers    = NULL;
static int        g_timer_len = 0;
static int        g_timer_cap = 0;
static long long  g_timer_seq = 1;

void sprout_poll_init(void) {
  /* WSAStartup belongs to the socket layer (W3), not here: the runtime must have
   * initialized Winsock before it can hand us an fd to poll in the first place. */
}

static void fds_reserve(int need) {
  if (need <= g_fd_cap) return;
  int cap = (g_fd_cap < 16) ? 16 : g_fd_cap;
  while (cap < need) cap *= 2;
  /* Each result is stored before the next allocation, so a failure never leaves a global
   * pointing at a block realloc already released. sprout_fail does not return, but a
   * dangling global is the kind of thing that outlives the reason it was safe. */
  WSAPOLLFD* nf = (WSAPOLLFD*)realloc(g_fds, (size_t)cap * sizeof(WSAPOLLFD));
  if (nf == NULL) sprout_fail("sprout_poll: out of memory growing the fd set");
  g_fds = nf;
  void** nt = (void**)realloc(g_fd_tokens, (size_t)cap * sizeof(void*));
  if (nt == NULL) sprout_fail("sprout_poll: out of memory growing the token set");
  g_fd_tokens = nt;
  g_fd_cap = cap;
}

static int fds_find(SOCKET s) {
  for (int i = 0; i < g_fd_len; i++)
    if (g_fds[i].fd == s) return i;
  return -1;
}

/* Swap-remove: order in the array carries no meaning, so the last entry fills the hole. */
static void fds_drop(int i) {
  g_fd_len--;
  if (i != g_fd_len) {
    g_fds[i]       = g_fds[g_fd_len];
    g_fd_tokens[i] = g_fd_tokens[g_fd_len];
  }
}

void sprout_poll_add(int fd, int interest, void* token) {
  /* fd arrives as int because the interface predates Windows and W3 owns turning the
   * handle table into SOCKETs. A Winsock SOCKET is a UINT_PTR, so this cast is lossy on
   * Win64 in principle — in practice Windows documents socket handles as values that fit
   * in 32 bits (they are kernel handles), and W3 widens the interface when it converts
   * the table. Until then this is the same int the rest of the runtime passes around. */
  SOCKET s = (SOCKET)(intptr_t)fd;
  SHORT ev = (interest == SPROUT_POLL_WRITE) ? POLLWRNORM : POLLRDNORM;
  int i = fds_find(s);
  if (i < 0) {
    fds_reserve(g_fd_len + 1);
    i = g_fd_len++;
    g_fds[i].fd = s;
  }
  g_fds[i].events  = ev;
  g_fds[i].revents = 0;
  g_fd_tokens[i]   = token;
}

void sprout_poll_remove(int fd, int interest) {
  (void)interest;   /* keyed by fd, as on epoll */
  int i = fds_find((SOCKET)(intptr_t)fd);
  if (i >= 0) fds_drop(i);   /* not found = already fired or never added; ignore */
}

static void timer_swap(int a, int b) {
  WinTimer t = g_timers[a];
  g_timers[a] = g_timers[b];
  g_timers[b] = t;
}

static void timer_sift_up(int i) {
  while (i > 0) {
    int p = (i - 1) / 2;
    if (g_timers[p].deadline <= g_timers[i].deadline) break;
    timer_swap(p, i);
    i = p;
  }
}

static void timer_sift_down(int i) {
  for (;;) {
    int l = 2 * i + 1, r = l + 1, m = i;
    if (l < g_timer_len && g_timers[l].deadline < g_timers[m].deadline) m = l;
    if (r < g_timer_len && g_timers[r].deadline < g_timers[m].deadline) m = r;
    if (m == i) break;
    timer_swap(m, i);
    i = m;
  }
}

static void timer_drop(int i) {
  g_timer_len--;
  if (i != g_timer_len) {
    g_timers[i] = g_timers[g_timer_len];
    timer_sift_down(i);
    timer_sift_up(i);
  }
}

int sprout_poll_add_timer(long long ms, void* token, long long* out_id) {
  if (g_timer_len == g_timer_cap) {
    int cap = (g_timer_cap < 16) ? 16 : g_timer_cap * 2;
    WinTimer* nt = (WinTimer*)realloc(g_timers, (size_t)cap * sizeof(WinTimer));
    if (nt == NULL) return 0;   /* the only way to fail here; see the header's contract */
    g_timers = nt;
    g_timer_cap = cap;
  }
  long long id = g_timer_seq++;
  int i = g_timer_len++;
  g_timers[i].deadline = (long long)GetTickCount64() + ms;
  g_timers[i].id       = id;
  g_timers[i].token    = token;
  timer_sift_up(i);   /* moves the entry, so the id is read from the local, not from [i] */
  *out_id = id;
  return 1;
}

void sprout_poll_remove_timer(long long timer_id) {
  /* O(parked timers) scan. The POSIX arms get O(1) removal from the kernel, but they pay
   * a syscall for it; scanning a heap that is bounded by the number of CONCURRENTLY parked
   * sleepers is cheaper than one WSAPoll iteration and needs no id->slot index that every
   * sift would have to maintain. Revisit only if a profile says so. */
  for (int i = 0; i < g_timer_len; i++) {
    if (g_timers[i].id == timer_id) { timer_drop(i); return; }
  }
  /* Not found: already harvested by sprout_poll_wait, or never armed. Either way the
   * contract's goal — this token will not resume a task — already holds. */
}

int sprout_poll_wait(void** out_tokens, int* out_is_timer, int max) {
  int want = (max < 64) ? max : 64;
  for (;;) {
    /* Timeout: the nearest deadline, clamped into WSAPoll's signed INT. The clamp is not
     * cosmetic — a deadline already past computes negative, and WSAPoll reads a negative
     * timeout as "wait indefinitely", i.e. the exact opposite of "this one is due now". */
    int timeout = -1;
    if (g_timer_len > 0) {
      long long d = g_timers[0].deadline - (long long)GetTickCount64();
      if (d < 0) d = 0;
      if (d > INT_MAX) d = INT_MAX;
      timeout = (int)d;
    }

    if (g_fd_len > 0) {
      if (WSAPoll(g_fds, (ULONG)g_fd_len, timeout) == SOCKET_ERROR)
        sprout_fail("sprout_poll_wait: WSAPoll failed");
    } else if (timeout >= 0) {
      /* Timers only. Sleep cannot miss a wakeup here, and the reason is a property of the
       * runtime rather than of Sleep: the sole cross-thread wakeup is the detached
       * getaddrinfo thread, and a pending DNS park IS a socket park (the loopback pair in
       * sprout_runtime.c), so any set containing one is not this set. */
      Sleep((DWORD)timeout);
    } else {
      /* Nothing registered at all — the pump only enters the poller with something parked. */
      sprout_fail("sprout_poll_wait: called with no registrations");
    }

    int n = 0;
    /* Ready sockets. Collect first, drop after: fds_drop moves the last entry into the
     * hole, so mutating mid-scan would skip whatever was moved down. */
    int hit[64];
    int nhit = 0;
    for (int i = 0; i < g_fd_len && n < want; i++) {
      if (g_fds[i].revents == 0) continue;
      out_tokens[n]   = g_fd_tokens[i];
      out_is_timer[n] = 0;
      n++;
      hit[nhit++] = i;
    }
    while (nhit > 0) fds_drop(hit[--nhit]);   /* descending: a drop never shifts a lower index */

    /* Due timers — harvested on BOTH paths, not just the timeout one. A deadline and a
     * ready socket can come due in the same wait, and the caller handles a batch carrying
     * both (a PARK_FD_TIMER task is registered on each). */
    long long now = (long long)GetTickCount64();
    while (g_timer_len > 0 && n < want && g_timers[0].deadline <= now) {
      out_tokens[n]   = g_timers[0].token;
      out_is_timer[n] = 1;
      n++;
      timer_drop(0);
    }

    if (n > 0) return n;
    /* WSAPoll timed out with nothing due yet (timer granularity). Nothing was consumed,
     * so re-entering the wait is exactly right — the same reasoning as the POSIX arms'
     * EINTR handling, which returns 0 and lets the pump re-poll. */
  }
}

#elif defined(__APPLE__)
#include <sys/event.h>
#include <sys/time.h>
#include <errno.h>

static int g_kq = -1;

void sprout_poll_init(void) {
  g_kq = kqueue();
  if (g_kq < 0) sprout_fail("sprout_poll_init: kqueue failed");
}

void sprout_poll_add(int fd, int interest, void* token) {
  struct kevent ev;
  int16_t filter = (interest == SPROUT_POLL_WRITE) ? EVFILT_WRITE : EVFILT_READ;
  /* EV_ONESHOT: fire once, then the knote is removed automatically. */
  EV_SET(&ev, fd, filter, EV_ADD | EV_ONESHOT, 0, 0, token);
  if (kevent(g_kq, &ev, 1, NULL, 0, NULL) < 0)
    sprout_fail("sprout_poll_add: kevent register failed");
}

void sprout_poll_remove(int fd, int interest) {
  struct kevent ev;
  int16_t filter = (interest == SPROUT_POLL_WRITE) ? EVFILT_WRITE : EVFILT_READ;
  EV_SET(&ev, fd, filter, EV_DELETE, 0, 0, NULL);
  /* ENOENT means the knote is already gone (fired one-shot, or never added) — the
   * force-drop's goal (this fd will not report readiness) already holds, so ignore. */
  if (kevent(g_kq, &ev, 1, NULL, 0, NULL) < 0 && errno != ENOENT)
    sprout_fail("sprout_poll_remove: kevent EV_DELETE failed");
}

int sprout_poll_add_timer(long long ms, void* token, long long* out_id) {
  /* ident = the parked Task* — a task sleeps on at most one timer, so it is unique, and
   * EVFILT_TIMER's ident namespace is disjoint from the EVFILT_READ/WRITE fd filters. */
  uintptr_t ident = (uintptr_t)token;
  struct kevent ev;
  /* fflags = 0: the default EVFILT_TIMER unit is milliseconds (per sys/event.h; this SDK
   * has no NOTE_MSECONDS). EV_ONESHOT fires once then removes the knote. */
  EV_SET(&ev, ident, EVFILT_TIMER, EV_ADD | EV_ONESHOT, 0, ms, token);
  if (kevent(g_kq, &ev, 1, NULL, 0, NULL) < 0) return 0;   /* caller decides; see the header */
  *out_id = (long long)ident;
  return 1;
}

void sprout_poll_remove_timer(long long timer_id) {
  struct kevent ev;
  /* EV_DELETE removes the knote AND discards a triggered-but-unretrieved timer event, so
   * a dropped sleeper cannot be resumed by a stale token (design §5.1). ENOENT = the
   * one-shot already fired and was drained; ignore. */
  EV_SET(&ev, (uintptr_t)timer_id, EVFILT_TIMER, EV_DELETE, 0, 0, NULL);
  if (kevent(g_kq, &ev, 1, NULL, 0, NULL) < 0 && errno != ENOENT)
    sprout_fail("sprout_poll_remove_timer: kevent EV_DELETE failed");
}

int sprout_poll_wait(void** out_tokens, int* out_is_timer, int max) {
  struct kevent evs[64];
  int want = (max < 64) ? max : 64;
  int n = kevent(g_kq, NULL, 0, evs, want, NULL);   /* NULL timeout = block */
  /* EINTR is a signal arriving while we blocked, not a failure. Report zero ready registrations and
   * let the pump re-poll: nothing was consumed, so re-entering the wait is exactly right. Aborting
   * here would take the whole process — every in-flight connection — for a delivered signal. */
  if (n < 0 && errno == EINTR) return 0;
  if (n < 0) sprout_fail("sprout_poll_wait: kevent wait failed");
  for (int i = 0; i < n; i++) {
    out_tokens[i]   = evs[i].udata;
    /* kqueue reports the filter, so the fd-vs-timer question answers itself here. */
    out_is_timer[i] = (evs[i].filter == EVFILT_TIMER) ? 1 : 0;
  }
  return n;
}

#else  /* Linux */
#include <sys/epoll.h>
#include <sys/timerfd.h>
#include <time.h>
#include <errno.h>

static int g_ep = -1;

void sprout_poll_init(void) {
  g_ep = epoll_create1(0);
  if (g_ep < 0) sprout_fail("sprout_poll_init: epoll_create1 failed");
}

void sprout_poll_add(int fd, int interest, void* token) {
  /* epoll_event.data is a union, so we cannot stash both the token (data.ptr)
   * and recover the fd later — hence we do NOT delete on wait. Instead EPOLLONESHOT
   * disarms (but keeps) the fd after it fires, and each park re-arms via MOD;
   * the first park on an fd (ENOENT) falls back to ADD. Closing the fd removes it
   * from the set automatically (epoll(7)), so there is no leak. */
  struct epoll_event ev;
  memset(&ev, 0, sizeof(ev));
  ev.events = ((interest == SPROUT_POLL_WRITE) ? EPOLLOUT : EPOLLIN) | EPOLLONESHOT;
  ev.data.ptr = token;
  if (epoll_ctl(g_ep, EPOLL_CTL_MOD, fd, &ev) < 0) {
    if (errno == ENOENT) {
      if (epoll_ctl(g_ep, EPOLL_CTL_ADD, fd, &ev) < 0)
        sprout_fail("sprout_poll_add: epoll_ctl ADD failed");
    } else {
      sprout_fail("sprout_poll_add: epoll_ctl MOD failed");
    }
  }
}

void sprout_poll_remove(int fd, int interest) {
  (void)interest;   /* epoll keys purely by fd (no per-filter knote as in kqueue) */
  /* We do NOT close the fd here (§10.3: cleanup-on-drop is accept-and-document), so
   * we must explicitly EPOLL_CTL_DEL — an EPOLLONESHOT-disarmed fd is still in the
   * set. ENOENT means it was never added (or already removed); ignore. */
  if (epoll_ctl(g_ep, EPOLL_CTL_DEL, fd, NULL) < 0 && errno != ENOENT)
    sprout_fail("sprout_poll_remove: epoll_ctl DEL failed");
}

int sprout_poll_add_timer(long long ms, void* token, long long* out_id) {
  /* One timerfd per sleeper (the epoll cost the design notes as the scaling boundary). Under
   * descriptor pressure this is the FIRST allocation to fail, and it must not be fatal: with
   * ~500 concurrently parked bounded reads and `ulimit -n 1024`, aborting here would drop every
   * in-flight connection instead of shedding the one that could not be armed. */
  int tfd = timerfd_create(CLOCK_MONOTONIC, TFD_CLOEXEC);
  if (tfd < 0) return 0;
  struct itimerspec its;
  memset(&its, 0, sizeof(its));                 /* it_interval = 0 -> one-shot */
  its.it_value.tv_sec  = (time_t)(ms / 1000);
  its.it_value.tv_nsec = (long)((ms % 1000) * 1000000L);
  if (timerfd_settime(tfd, 0, &its, NULL) < 0) {
    close(tfd);
    return 0;
  }
  struct epoll_event ev;
  memset(&ev, 0, sizeof(ev));
  ev.events = EPOLLIN | EPOLLONESHOT;
  /* TAG the timer's token in bit 0 so sprout_poll_wait can report fd-vs-timer, which the
   * combined fd-or-timer park needs (it has one task registered on BOTH, and must tear down
   * the loser — while the timer backend's teardown close()s an fd, so it has to happen exactly
   * once). kqueue gets this for free from evs[i].filter; epoll_event.data is a union carrying
   * only the token, so the distinction has to be encoded IN the token. Task pointers come from
   * malloc and are at least 8-byte aligned, so bit 0 is always free. Tag/untag stays private to
   * this file — the scheduler only ever sees the untagged token plus the is_timer flag. */
  ev.data.ptr = (void*)((uintptr_t)token | 1u);
  if (epoll_ctl(g_ep, EPOLL_CTL_ADD, tfd, &ev) < 0) {
    close(tfd);
    return 0;
  }
  *out_id = (long long)tfd;
  return 1;
}

void sprout_poll_remove_timer(long long timer_id) {
  int tfd = (int)timer_id;
  /* DEL then close. close() alone removes the fd from the epoll set, and — crucially —
   * discards any fired-but-unretrieved event, so a dropped sleeper cannot be resumed by a
   * stale token (design §5.1). DEL first is belt-and-suspenders; ignore its errors. */
  epoll_ctl(g_ep, EPOLL_CTL_DEL, tfd, NULL);
  close(tfd);
}

int sprout_poll_wait(void** out_tokens, int* out_is_timer, int max) {
  struct epoll_event evs[64];
  int want = (max < 64) ? max : 64;
  int n = epoll_wait(g_ep, evs, want, -1);   /* -1 = block */
  /* EINTR: see the kqueue branch. Zero ready registrations, pump re-polls, nothing consumed. */
  if (n < 0 && errno == EINTR) return 0;
  if (n < 0) sprout_fail("sprout_poll_wait: epoll_wait failed");
  for (int i = 0; i < n; i++) {
    uintptr_t raw = (uintptr_t)evs[i].data.ptr;   /* bit 0 set == a timer (see add_timer) */
    out_is_timer[i] = (int)(raw & 1u);
    out_tokens[i]   = (void*)(raw & ~(uintptr_t)1u);
  }
  return n;
}

#endif
