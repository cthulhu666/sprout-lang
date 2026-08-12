/* Regression test for the EMFILE accept hot-spin (code review finding 9).
 *
 * BEFORE the fix, tcp_accept's EMFILE/ENFILE path parked on the listener and retried without
 * draining anything. accept() under EMFILE does NOT dequeue the pending connection, so the listener
 * stayed readable, the level-triggered park returned immediately, and the loop spun at 100% CPU for
 * as long as descriptors were exhausted and any connection was pending. The runtime comment claimed
 * convergence by copying the reasoning from the retryable-errno paths, where each retry CONSUMES its
 * queued connection — which EMFILE does not.
 *
 * The FIX adds a reserve descriptor: on EMFILE, close it (freeing one slot), accept+close the whole
 * pending backlog (shedding it — each accept reuses the one freed slot, each close returns it), then
 * reopen the reserve. This genuinely drains the backlog so the subsequent park is a real wait, not a
 * spin — the convergence the old comment only asserted.
 *
 * This exercises accept_shed_backlog directly under a lowered RLIMIT_NOFILE, with no scheduler and no
 * timing: fill a listener's backlog, exhaust descriptors so a bare accept() returns EMFILE, then
 * assert the shed (a) drains the backlog — a following nonblocking accept returns EAGAIN, not another
 * pending fd and not EMFILE — (b) sheds a positive count, and (c) re-arms the reserve for next time.
 *
 * tcp_accept itself cannot be called here: it parks in the scheduler, which is not running in a bare
 * C test. The shed logic is therefore a named, non-static function used as a deterministic test seam;
 * the end-to-end survival half is tests/task_io_smoke/http_accept_exhaustion.spr.
 *
 * On the UNFIXED runtime accept_shed_backlog / accept_reserve_arm / g_accept_reserve_fd do not exist,
 * so this fails to link — the expected RED for a newly-added contract.
 */
#include <errno.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <stdio.h>
#include <string.h>
#include <sys/resource.h>
#include <sys/socket.h>
#include <unistd.h>

/* Test seam — non-static in the runtime so this bare test can reach them without a scheduler. */
extern int  g_accept_reserve_fd;
extern void accept_reserve_arm(void);
extern int  accept_shed_backlog(int listener_fd);

int main(void) {
  /* Small descriptor budget so we can exhaust it deliberately. Best-effort: if the environment does
   * not honor it we detect non-exhaustion below and SKIP rather than assert a false pass. */
  struct rlimit rl;
  rl.rlim_cur = 40;
  rl.rlim_max = 40;
  if (setrlimit(RLIMIT_NOFILE, &rl) != 0) {
    fprintf(stderr, "SKIP: setrlimit(RLIMIT_NOFILE) unavailable (%s)\n", strerror(errno));
    printf("emfile-shed-skipped\n");
    return 0;
  }

  /* Listener on an ephemeral loopback port. */
  int lfd = socket(AF_INET, SOCK_STREAM, 0);
  if (lfd < 0) { perror("socket(listener)"); return 2; }
  int one = 1;
  setsockopt(lfd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));
  struct sockaddr_in addr;
  memset(&addr, 0, sizeof(addr));
  addr.sin_family = AF_INET;
  addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
  addr.sin_port = 0;   /* kernel picks the port */
  if (bind(lfd, (struct sockaddr*)&addr, sizeof(addr)) < 0) { perror("bind"); return 2; }
  if (listen(lfd, 16) < 0) { perror("listen"); return 2; }
  /* Nonblocking so accept returns EAGAIN on an empty backlog rather than blocking. */
  fcntl(lfd, F_SETFL, fcntl(lfd, F_GETFL, 0) | O_NONBLOCK);
  socklen_t alen = sizeof(addr);
  if (getsockname(lfd, (struct sockaddr*)&addr, &alen) < 0) { perror("getsockname"); return 2; }

  /* Arm the reserve while descriptors are still plentiful — what tcp_listen does. */
  accept_reserve_arm();
  if (g_accept_reserve_fd < 0) {
    fprintf(stderr, "FAIL: reserve descriptor did not arm\n");
    return 1;
  }

  /* Two independent steps, because on macOS a beyond-backlog loopback connect is REFUSED before
   * descriptors run out — so a single "connect until it fails" loop stops with fds still free and
   * never reaches EMFILE.
   *
   * Step 1: seed the accept queue with a handful of connections (well under the backlog of 16, so
   * each loopback connect completes synchronously without blocking). These are what the shed drains. */
  int clients[16];
  int nclients = 0;
  for (int i = 0; i < 8; i++) {
    int c = socket(AF_INET, SOCK_STREAM, 0);
    if (c < 0) break;
    if (connect(c, (struct sockaddr*)&addr, sizeof(addr)) < 0) { close(c); break; }
    clients[nclients++] = c;
  }
  if (nclients == 0) {
    fprintf(stderr, "FAIL: could not seed the accept queue\n");
    return 1;
  }

  /* Step 2: exhaust the REMAINING descriptors with plain fds, so a bare accept() returns EMFILE. The
   * cap doubles as the non-enforcement guard: if the rlimit is not honored we hit the cap, not EMFILE,
   * and SKIP below. */
  int filler[4096];
  int nfiller = 0;
  for (;;) {
    int f = open("/dev/null", O_RDONLY);
    if (f < 0) break;                                 /* EMFILE: descriptors exhausted */
    if (nfiller < 4096) {
      filler[nfiller++] = f;
    } else {
      close(f);
      break;
    }
  }

  /* A bare accept must now fail with EMFILE (all descriptors in use). If the environment did not
   * actually exhaust — e.g. rlimit not enforced under the sandbox — SKIP rather than assert falsely. */
  errno = 0;
  int probe = accept(lfd, NULL, NULL);
  if (probe >= 0) {
    close(probe);
    fprintf(stderr, "SKIP: environment did not reach EMFILE (rlimit not enforced?)\n");
    printf("emfile-shed-skipped\n");
    return 0;
  }
  if (errno != EMFILE && errno != ENFILE) {
    fprintf(stderr, "FAIL: expected EMFILE/ENFILE from accept, got errno=%d (%s)\n",
            errno, strerror(errno));
    return 1;
  }

  /* THE ASSERTION. Shed the backlog. The reserve is open (armed above), so this can free a slot. */
  int shed = accept_shed_backlog(lfd);
  if (shed <= 0) {
    fprintf(stderr, "FAIL: shed drained nothing (returned %d)\n", shed);
    return 1;
  }

  /* Backlog must now be empty. To check that, accept() has to be able to distinguish an EMPTY queue
   * from mere fd-exhaustion — and the two platforms disagree on the order of those checks: BSD/macOS
   * inspects the accept queue first (empty -> EAGAIN), while Linux allocates the fd first, so on a
   * drained queue with no free descriptor it returns EMFILE before ever looking at the queue. The
   * reserve we just reopened holds the last slot, so free one filler descriptor here; now a free fd is
   * available and accept() returns EAGAIN iff the queue is genuinely empty, on both platforms. */
  if (nfiller > 0) close(filler[--nfiller]);
  errno = 0;
  int after = accept(lfd, NULL, NULL);
  if (after >= 0) {
    close(after);
    fprintf(stderr, "FAIL: backlog not drained (post-shed accept returned fd %d)\n", after);
    return 1;
  }
  if (errno != EAGAIN && errno != EWOULDBLOCK) {
    fprintf(stderr, "FAIL: post-shed accept errno=%d (%s), expected EAGAIN\n",
            errno, strerror(errno));
    return 1;
  }

  /* Reserve must be re-armed for the next EMFILE event. */
  if (g_accept_reserve_fd < 0) {
    fprintf(stderr, "FAIL: reserve descriptor not re-armed after shed\n");
    return 1;
  }

  for (int i = 0; i < nclients; i++) close(clients[i]);
  for (int i = 0; i < nfiller; i++) close(filler[i]);
  printf("emfile-shed-drained\n");
  return 0;
}
