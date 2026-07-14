/* Readiness poller for Sprout L0.3 I/O parking (EXPERIMENTAL).
 *
 * A thin, internal (not Sprout-visible) layer over kqueue (macOS/BSD) and epoll
 * (Linux). The scheduler (sprout_sched.c) owns the task<->fd association; this
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
#include "sprout_sched.h"

#ifdef __APPLE__
#include <sys/event.h>
#include <sys/time.h>

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

int sprout_poll_wait(void** out_tokens, int max) {
  struct kevent evs[64];
  int want = (max < 64) ? max : 64;
  int n = kevent(g_kq, NULL, 0, evs, want, NULL);   /* NULL timeout = block */
  if (n < 0) sprout_fail("sprout_poll_wait: kevent wait failed");
  for (int i = 0; i < n; i++) out_tokens[i] = evs[i].udata;
  return n;
}

#else  /* Linux */
#include <sys/epoll.h>
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

int sprout_poll_wait(void** out_tokens, int max) {
  struct epoll_event evs[64];
  int want = (max < 64) ? max : 64;
  int n = epoll_wait(g_ep, evs, want, -1);   /* -1 = block */
  if (n < 0) sprout_fail("sprout_poll_wait: epoll_wait failed");
  for (int i = 0; i < n; i++) out_tokens[i] = evs[i].data.ptr;
  return n;
}

#endif
