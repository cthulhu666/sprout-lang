/* Regression test: accept_shed_backlog must be BOUNDED per call (green-threads review, finding 2).
 *
 * BEFORE the fix the drain was `for (;;)`, stopping only when the accept queue reported EAGAIN. It
 * runs on the single scheduler thread with no park and no yield in it, so every iteration is one the
 * whole process is frozen for — including the handlers whose completion returns the very descriptors
 * the exhaustion is waiting on. Against a peer refilling the queue as fast as we empty it, that does
 * not merely stall: it sustains the condition it is trying to relieve. It is also a strictly worse
 * failure than the 100% CPU spin the shed was written to replace, since that spin at least re-entered
 * the pump on every iteration and let siblings run.
 *
 * The FIX caps one visit at ACCEPT_SHED_MAX_PER_CALL iterations. Reaching the cap is not a failure
 * mode: the listener is still readable, so the caller's park returns at once and the next call sheds
 * the next batch — same throughput, pump serviced between batches.
 *
 * THE ASSERTION, and why it needs no EMFILE. accept_shed_backlog's precondition is an armed reserve,
 * not descriptor exhaustion, so the bound can be tested directly: queue markedly more connections than
 * the cap, shed once, and require that it stopped early with the queue still non-empty. On the unfixed
 * runtime the single call drains all of them and `shed == nseeded`, which is the RED. A second and
 * third call must then keep making progress, so the bound cannot be satisfied by a shed that refuses
 * to drain at all.
 *
 * Deliberately NOT hardcoding 64 here. The property under test is "bounded, and still converging",
 * which `shed < nseeded` states directly; pinning the constant would make this fail on a retune that
 * preserves the property. tests/c_runtime/run.sh separately pins that the constant exists.
 *
 * SKIPs (still exit 0, marker "accept-shed-bounded-skipped") when the environment cannot give us
 * enough descriptors to queue past the cap, rather than asserting a false pass.
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

/* Comfortably above ACCEPT_SHED_MAX_PER_CALL (64) without approaching a default 256-fd budget. */
#define SEED_TARGET 100
#define LISTEN_BACKLOG 200
/* The queue depth below which this test cannot distinguish "bounded" from "drained everything", so
 * a short seed SKIPs. It must stay ABOVE the cap: the whole assertion is that a correct shed stops
 * before MIN_SEED. Raising ACCEPT_SHED_MAX_PER_CALL to 80 or beyond makes this test fail rather than
 * silently pass, which is the intended coupling — retune both together, deliberately. */
#define MIN_SEED 80

int main(void) {
  /* Best-effort raise: SEED_TARGET client sockets plus the listener, the reserve and stdio need more
   * headroom than some default soft limits give. Failure is fine — the seed loop below detects a
   * short queue and SKIPs. */
  struct rlimit rl;
  if (getrlimit(RLIMIT_NOFILE, &rl) == 0 && rl.rlim_cur < 512) {
    rl.rlim_cur = (rl.rlim_max < 512) ? rl.rlim_max : 512;
    setrlimit(RLIMIT_NOFILE, &rl);
  }

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
  /* Backlog well past SEED_TARGET so every seeded connect lands in the queue rather than being
   * refused — the queue depth IS what this test manipulates. */
  if (listen(lfd, LISTEN_BACKLOG) < 0) { perror("listen"); return 2; }
  fcntl(lfd, F_SETFL, fcntl(lfd, F_GETFL, 0) | O_NONBLOCK);
  socklen_t alen = sizeof(addr);
  if (getsockname(lfd, (struct sockaddr*)&addr, &alen) < 0) { perror("getsockname"); return 2; }

  accept_reserve_arm();
  if (g_accept_reserve_fd < 0) {
    fprintf(stderr, "FAIL: reserve descriptor did not arm\n");
    return 1;
  }

  /* Queue SEED_TARGET pending connections. Loopback connects complete synchronously while the
   * backlog has room, so these sit in the accept queue with nothing accepting them. */
  int clients[SEED_TARGET];
  int nclients = 0;
  for (int i = 0; i < SEED_TARGET; i++) {
    int c = socket(AF_INET, SOCK_STREAM, 0);
    if (c < 0) break;
    if (connect(c, (struct sockaddr*)&addr, sizeof(addr)) < 0) { close(c); break; }
    clients[nclients++] = c;
  }

  /* Decide SKIP-vs-assert on the SEED COUNT, before shedding anything. Deriving it from the shed
   * result instead would make "the environment gave us too few connections" and "the shed drained
   * every one of them" the same predicate — and the second of those is precisely the RED this test
   * exists to produce, so it would be reported as a skip and pass. */
  if (nclients < MIN_SEED) {
    fprintf(stderr, "SKIP: queued only %d connections, need >= %d to prove boundedness\n",
            nclients, MIN_SEED);
    for (int i = 0; i < nclients; i++) close(clients[i]);
    close(lfd);
    printf("accept-shed-bounded-skipped\n");
    return 0;
  }

  int shed_first = accept_shed_backlog(lfd);

  /* (a) BOUNDED: one call did not drain everything we queued. This is the assertion that goes RED on
   * the unfixed `for (;;)` drain, where shed_first == nclients. */
  if (shed_first <= 0) {
    fprintf(stderr, "FAIL: shed drained nothing (returned %d)\n", shed_first);
    return 1;
  }
  if (shed_first >= nclients) {
    fprintf(stderr, "FAIL: shed drained all %d queued connections in ONE call (returned %d) — the "
                    "per-call bound is gone and the drain can freeze the scheduler pump\n",
            nclients, shed_first);
    return 1;
  }

  /* (b) The queue really is still non-empty — the cap stopped us mid-drain rather than the kernel
   * having handed us fewer connections than we think we queued. A descriptor is free here (the shed
   * closes every fd it accepts), so accept() can distinguish "empty" from "exhausted" on both
   * platforms; see the note in emfile_accept_shed.c. */
  errno = 0;
  int pending = accept(lfd, NULL, NULL);
  if (pending < 0) {
    fprintf(stderr, "FAIL: queue empty after a capped shed (accept errno=%d (%s)); the cap did not "
                    "stop the drain early\n", errno, strerror(errno));
    return 1;
  }
  close(pending);

  /* (c) CONVERGING: repeated calls keep shedding and eventually drain. A bound that never finishes
   * would be no better than the freeze it replaced. */
  int rounds = 0;
  int shed_total = shed_first;
  for (;;) {
    int s = accept_shed_backlog(lfd);
    shed_total += s;
    if (s == 0) break;
    if (++rounds > 64) {
      fprintf(stderr, "FAIL: shed never drained the queue in %d rounds (%d shed total)\n",
              rounds, shed_total);
      return 1;
    }
  }
  errno = 0;
  int after = accept(lfd, NULL, NULL);
  if (after >= 0) {
    close(after);
    fprintf(stderr, "FAIL: connections still queued after the drain loop\n");
    return 1;
  }

  /* Reserve must survive the whole sequence armed, ready for the next EMFILE event. */
  if (g_accept_reserve_fd < 0) {
    fprintf(stderr, "FAIL: reserve descriptor not re-armed after the drain loop\n");
    return 1;
  }

  for (int i = 0; i < nclients; i++) close(clients[i]);
  close(lfd);
  printf("accept-shed-bounded\n");
  return 0;
}
