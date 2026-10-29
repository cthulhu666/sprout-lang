/* The terminal exits hand Sprout headered Strings (docs/string-header-invariant-v0.md §7 step 3).
 *
 * Runs under SPROUT_GC_HDRCHECK=1, so every String that leaves through sprout_str_word is
 * header-checked, and str_eq / str_byte_len check each payload again on the way in. argv[1]:
 *   key   stdin carries every key the non-tty path of term_read_key names, a fresh one-byte
 *         key, a 2-byte key, two malformed sequences (U+FFFD), then EOF.
 *   line  term_read_line: CRLF, an empty line, a last line without a newline, then EOF.
 *   avail term_read_avail: Bytes from a pipe, then TermFailed(text) once fd 0 is closed.
 *   tty   term_read_key on a pty: the four arrow keys, a 2-byte key, a malformed one with the
 *         next key kept, a plain key, an unknown CSI and a lone ESC.
 * Not reached: term_read_line's read error (sprout_fail) and term_input_failed's poll branches
 * ("stdin is not open", poll error), which need a descriptor that fstat accepts and poll refuses.
 */
#define _GNU_SOURCE
#define _DARWIN_C_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <termios.h>
#include <unistd.h>

#include "str_check.h"

long long term_read_key(void);
long long term_read_line(void);
long long term_read_avail(long long max_val, long long ms_val);
long long bytes_length(long long bytes_h);

enum { JUST = 3, NOTHING = 4, BYTES = 10, FAILED = 11 };

static int expect_keys(const char* const* want, int n) {
  for (int i = 0; i < n; i++) {
    long long k = term_read_key();
    if (!is_str(k, want[i])) {
      fprintf(stderr, "FAIL: key %d is not a headered String \"%s\"\n", i, want[i]);
      return 1;
    }
  }
  return 0;
}

static int run_key(void) {
  static const char* const want[] = {
    "ctrl-a", "ctrl-b", "ctrl-d", "ctrl-e", "ctrl-f", "ctrl-l", "backspace", "backspace",
    "escape", "enter", "enter", "tab", "x", "\xc3\xa9", "\xef\xbf\xbd", "A", "\xef\xbf\xbd", ""
  };
  return expect_keys(want, (int)(sizeof want / sizeof want[0]));
}

static int run_line(void) {
  static const char* const want[] = { "first", "", "second" };
  for (int i = 0; i < 3; i++) {
    long long r = term_read_line();
    if (sprout_tag(r) != JUST || !is_str(sprout_field(r, 0), want[i]))
      return fail("term_read_line is not Just(<headered String>)");
  }
  if (sprout_tag(term_read_line()) != NOTHING) return fail("term_read_line at EOF");
  return 0;
}

static int run_avail(void) {
  /* The writer on the pipe may not have run yet; a zero timeout would see TermIdle. */
  struct pollfd pfd = { STDIN_FILENO, POLLIN, 0 };
  int pr;
  while ((pr = poll(&pfd, 1, -1)) < 0 && errno == EINTR) {}
  if (pr < 0) return fail("poll on stdin");
  long long r = term_read_avail(16, 0);
  if (sprout_tag(r) != BYTES || bytes_length(sprout_field(r, 0)) != 3)
    return fail("term_read_avail is not TermBytes of 3 bytes");
  close(STDIN_FILENO);
  r = term_read_avail(16, 0);
  if (sprout_tag(r) != FAILED || !is_str(sprout_field(r, 0), NULL))
    return fail("term_read_avail on a closed fd is not TermFailed(<headered String>)");
  return 0;
}

static int run_tty_child(int slave) {
  struct termios t;
  if (tcgetattr(slave, &t) != 0) return fail("tcgetattr on the pty slave");
  cfmakeraw(&t);
  t.c_cc[VMIN] = 1;
  t.c_cc[VTIME] = 0;
  if (tcsetattr(slave, TCSANOW, &t) != 0) return fail("tcsetattr on the pty slave");
  if (dup2(slave, STDIN_FILENO) < 0) return fail("dup2");
  static const char* const want[] = {
    "up", "down", "right", "left", "\xc3\xa9", "\xef\xbf\xbd", "A", "z", "escape", "escape"
  };
  return expect_keys(want, (int)(sizeof want / sizeof want[0]));
}

static int run_tty(void) {
  int master = posix_openpt(O_RDWR | O_NOCTTY);
  const char* name = NULL;
  if (master >= 0 && grantpt(master) == 0 && unlockpt(master) == 0) name = ptsname(master);
  if (name == NULL) {
    printf("term-strings-tty-skipped\n");
    return 0;
  }
  pid_t pid = fork();
  if (pid < 0) return fail("fork");
  if (pid == 0) {
    int slave = open(name, O_RDWR | O_NOCTTY);
    if (slave < 0) _exit(fail("open the pty slave"));
    _exit(run_tty_child(slave));
  }
  static const char keys[] =
    "\x1b[A\x1b[B\x1b[C\x1b[D" "\xc3\xa9" "\xc3" "A" "z" "\x1b[Z" "\x1b";
  alarm(30);
  if (write(master, keys, sizeof keys - 1) != (ssize_t)(sizeof keys - 1)) return fail("write");
  int status = 0;
  if (waitpid(pid, &status, 0) != pid) return fail("waitpid");
  if (!WIFEXITED(status) || WEXITSTATUS(status) != 0) return fail("the tty child failed");
  printf("term-strings-tty\n");
  return 0;
}

int main(int argc, char** argv) {
  if (argc != 2) return 99;
  setenv("SPROUT_GC_HDRCHECK", "1", 1);
  sprout_register_ctor(JUST, "Just", 1, "p");
  sprout_register_ctor(NOTHING, "Nothing", 0, "");
  sprout_register_ctor(BYTES, "stdlib.terminal.TermBytes", 1, "p");
  sprout_register_ctor(FAILED, "stdlib.terminal.TermFailed", 1, "p");
  int rc;
  if (strcmp(argv[1], "key") == 0) rc = run_key();
  else if (strcmp(argv[1], "line") == 0) rc = run_line();
  else if (strcmp(argv[1], "avail") == 0) rc = run_avail();
  else if (strcmp(argv[1], "tty") == 0) return run_tty();
  else return 98;
  if (rc == 0) printf("term-strings-%s\n", argv[1]);
  return rc;
}
