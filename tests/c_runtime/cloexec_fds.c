/* Every descriptor the runtime creates is close-on-exec, so a program started by proc_run does not
 * inherit its sockets, pipes or files.
 *
 * Includes the runtime translation unit (like str_writers.c) to reach its file-static helpers, and
 * run.sh links only the other TUs.
 *
 * Part 1 drives runtime entry points, then requires every open descriptor above stderr to carry
 * FD_CLOEXEC. Part 2 checks each file-static wrapper directly. */
#include <errno.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <stdio.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>

#include "../../runtime/sprout_runtime.c"

static int g_failed = 0;

static void expect_cloexec(int fd, const char* what) {
  int fl = fcntl(fd, F_GETFD);
  if (fl < 0 || !(fl & FD_CLOEXEC)) {
    fprintf(stderr, "FAIL: %s (fd %d) is not close-on-exec\n", what, fd);
    g_failed = 1;
  }
}

/* Every open descriptor from 3 up. Nothing here may be inheritable: stdio is 0..2 and the
 * harness's own are close-on-exec or above the range scanned. */
static void expect_all_cloexec(const char* when) {
  for (int fd = 3; fd < 256; fd++) {
    int fl = fcntl(fd, F_GETFD);
    if (fl < 0) continue;
    if (!(fl & FD_CLOEXEC)) {
      fprintf(stderr, "FAIL: %s: fd %d is open and not close-on-exec\n", when, fd);
      g_failed = 1;
    }
  }
}

int main(void) {
  /* Part 1: runtime entry points. */
  sprout_register_ctor(21, "Err", 1, "p");
  sprout_register_ctor(22, "Ok", 1, "p");
  long long r = tcp_listen("127.0.0.1", 0);
  (void)r;
#if defined(__linux__)
  sprout_poll_init();
#endif
  expect_all_cloexec("after tcp_listen and sprout_poll_init");

  /* Part 2: each wrapper. */
  int s = cloexec_socket(AF_INET, SOCK_STREAM, 0);
  expect_cloexec(s, "cloexec_socket");

  int p[2] = {-1, -1};
  if (cloexec_pipe(p) != 0) { perror("cloexec_pipe"); return 2; }
  expect_cloexec(p[0], "cloexec_pipe read end");
  expect_cloexec(p[1], "cloexec_pipe write end");

  int o = cloexec_open("/dev/null", O_RDONLY, 0);
  expect_cloexec(o, "cloexec_open");

  FILE* fr = cloexec_fopen("/dev/null", "rb");
  FILE* fw = cloexec_fopen("/dev/null", "wb");
  if (fr == NULL || fw == NULL) { perror("cloexec_fopen"); return 2; }
  expect_cloexec(fileno(fr), "cloexec_fopen rb");
  expect_cloexec(fileno(fw), "cloexec_fopen wb");

  /* accept: a loopback pair. */
  struct sockaddr_in addr;
  memset(&addr, 0, sizeof(addr));
  addr.sin_family = AF_INET;
  addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
  socklen_t alen = sizeof(addr);
  int l = cloexec_socket(AF_INET, SOCK_STREAM, 0);
  if (bind(l, (struct sockaddr*)&addr, sizeof(addr)) != 0 || listen(l, 4) != 0 ||
      getsockname(l, (struct sockaddr*)&addr, &alen) != 0) {
    perror("listener");
    return 2;
  }
  int c = cloexec_socket(AF_INET, SOCK_STREAM, 0);
  if (c < 0 || connect(c, (struct sockaddr*)&addr, sizeof(addr)) != 0) { perror("connect"); return 2; }
  int a = cloexec_accept(l);
  if (a < 0) { perror("cloexec_accept"); return 2; }
  expect_cloexec(a, "cloexec_accept");

  /* An accept that takes nothing keeps accept's errno. */
  fcntl(l, F_SETFL, fcntl(l, F_GETFL, 0) | O_NONBLOCK);
  errno = 0;
  if (cloexec_accept(l) != -1 || (errno != EAGAIN && errno != EWOULDBLOCK)) {
    fprintf(stderr, "FAIL: cloexec_accept on an empty queue: errno %d, want EAGAIN\n", errno);
    g_failed = 1;
  }

  /* The accept reserve is armed by tcp_listen above. */
  expect_cloexec(g_accept_reserve_fd, "accept reserve");

  expect_all_cloexec("after the wrappers");

  if (g_failed) return 1;
  printf("cloexec-fds\n");
  return 0;
}
