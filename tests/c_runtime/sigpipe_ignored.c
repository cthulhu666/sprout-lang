/* Regression test for the SIGPIPE process-kill DoS (review 2026-06-27, F-NET-1).
 *
 * Before the fix, the runtime left SIGPIPE at its default disposition, so a
 * write to a socket/pipe whose peer had closed killed the whole process with
 * signal 13 — taking down any Sprout TCP server whose client disconnected.
 *
 * The runtime now installs signal(SIGPIPE, SIG_IGN) in a constructor. Linking
 * runtime/sprout_runtime.c runs that constructor before main(). This test does
 * NOT set the disposition itself; it verifies the runtime did.
 *
 * On the UNFIXED runtime the write() below raises SIGPIPE and this process dies
 * (exit via signal, no "sigpipe-ignored" output) — the test fails.
 */
#include <errno.h>
#include <signal.h>
#include <stdio.h>
#include <unistd.h>

int main(void) {
  /* 1. The runtime constructor must have set SIGPIPE to SIG_IGN. */
  struct sigaction sa;
  if (sigaction(SIGPIPE, NULL, &sa) != 0) {
    perror("sigaction");
    return 2;
  }
  if (sa.sa_handler != SIG_IGN) {
    fprintf(stderr, "FAIL: SIGPIPE disposition is not SIG_IGN\n");
    return 1;
  }

  /* 2. Writing to a pipe whose read end is closed must return EPIPE, not kill
   *    us. This is the actual behavior the TCP builtins rely on. */
  int fds[2];
  if (pipe(fds) != 0) {
    perror("pipe");
    return 2;
  }
  close(fds[0]);
  errno = 0;
  ssize_t r = write(fds[1], "x", 1);
  if (!(r == -1 && errno == EPIPE)) {
    fprintf(stderr, "FAIL: expected write to return -1/EPIPE, got r=%zd errno=%d\n", r, errno);
    return 1;
  }

  printf("sigpipe-ignored\n");
  return 0;
}
