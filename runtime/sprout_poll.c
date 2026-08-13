/* Readiness poller for Sprout L0.3 I/O parking (EXPERIMENTAL).
 *
 * A thin, internal (not Sprout-visible) layer over kqueue (macOS/BSD) and epoll
 * (Linux). The scheduler (sprout_scheduler.c) owns the task<->fd association; this
 * layer only stores an opaque per-registration token and hands it back when the
 * fd becomes ready. Both backends are readiness APIs (register interest -> notified
 * when readable/writable -> the caller does the non-blocking read/write itself);
 * see epoll(7) / kqueue(2).
 *
 * Registration is ONE-SHOT: an fd is watched for exactly one readiness event, then
 * dropped. The scheduler re-registers on the next EAGAIN. This matches the
 * cooperative model (one task waits on a given fd at a time) and keeps the poller
 * stateless between waits — the simplest thing that is correct. Edge-triggered is
 * a later optimization, not needed for correctness.
 */
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <stdint.h>
#include "sprout_scheduler.h"

#ifdef __APPLE__
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
