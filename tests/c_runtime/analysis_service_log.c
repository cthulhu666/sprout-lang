/* sprout_open_service_log opens the analysis service's log only when the path is a regular file the
 * current user owns and that group and others cannot write.
 *
 * Includes the runtime translation unit (like cloexec_fds.c) to reach the file-static helper.
 * usage: analysis_service_log <scratch-dir> */
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>

#include "../../runtime/sprout_runtime.c"

static int g_failed = 0;

static void check(int ok, const char* what) {
  if (!ok) {
    fprintf(stderr, "FAIL: %s\n", what);
    g_failed = 1;
  }
}

static long file_size(const char* path) {
  struct stat st;
  return stat(path, &st) == 0 ? (long)st.st_size : -1;
}

int main(int argc, char** argv) {
  if (argc < 2) { fprintf(stderr, "usage: %s <scratch-dir>\n", argv[0]); return 2; }
  char path[1024], target[1024];

  /* A missing file is created, owner-only. */
  snprintf(path, sizeof(path), "%s/fresh.log", argv[1]);
  int fd = sprout_open_service_log(path);
  check(fd >= 0, "a missing file is created");
  if (fd >= 0) {
    struct stat st;
    check(fstat(fd, &st) == 0 && (st.st_mode & 0777) == 0600, "a created file is mode 0600");
    check(!(fcntl(fd, F_GETFL) & O_NONBLOCK), "O_NONBLOCK is cleared after the checks");
    check(write(fd, "one\n", 4) == 4, "write to the new log");
    close(fd);
  }

  /* An own 0600 file is opened for append. */
  fd = sprout_open_service_log(path);
  check(fd >= 0, "an own 0600 file opens");
  if (fd >= 0) {
    check(write(fd, "two\n", 4) == 4, "write to the existing log");
    close(fd);
    check(file_size(path) == 8, "the second open appends");
  }

  /* A file that group or others can write is refused. */
  snprintf(path, sizeof(path), "%s/open.log", argv[1]);
  fd = open(path, O_WRONLY | O_CREAT, 0600);
  if (fd >= 0) close(fd);
  chmod(path, 0666);
  check(sprout_open_service_log(path) == -1, "a mode 0666 file is refused");
  chmod(path, 0620);
  check(sprout_open_service_log(path) == -1, "a group-writable file is refused");
  chmod(path, 0602);
  check(sprout_open_service_log(path) == -1, "an other-writable file is refused");

  /* A link is not followed, and its target is left alone. */
  snprintf(target, sizeof(target), "%s/target.log", argv[1]);
  fd = open(target, O_WRONLY | O_CREAT, 0600);
  if (fd >= 0) close(fd);
  snprintf(path, sizeof(path), "%s/link.log", argv[1]);
  check(symlink(target, path) == 0, "create the link");
  check(sprout_open_service_log(path) == -1, "a symlink is refused");
  check(file_size(target) == 0, "the link target is untouched");

  /* A second name for the file is refused, and the original is left alone. */
  snprintf(target, sizeof(target), "%s/own.log", argv[1]);
  fd = open(target, O_WRONLY | O_CREAT, 0600);
  if (fd >= 0) close(fd);
  snprintf(path, sizeof(path), "%s/hard.log", argv[1]);
  check(link(target, path) == 0, "create the hard link");
  check(sprout_open_service_log(path) == -1, "a hard-linked file is refused");
  check(sprout_open_service_log(target) == -1, "the original of a hard link is refused");
  check(file_size(target) == 0, "the hard-link original is untouched");

  /* A FIFO with no reader returns at once instead of blocking. */
  snprintf(path, sizeof(path), "%s/fifo.log", argv[1]);
  check(mkfifo(path, 0600) == 0, "create the fifo");
  check(sprout_open_service_log(path) == -1, "a fifo is refused without blocking");

  /* A directory is refused. */
  check(sprout_open_service_log(argv[1]) == -1, "a directory is refused");

  /* A refused log is reported on the parent's stderr, as one line naming the path.  fd 2 is
   * pointed at a file for the call; the child writes the line before it redirects its own. */
  snprintf(path, sizeof(path), "%s/stderr.txt", argv[1]);
  snprintf(target, sizeof(target), "%s/fifo.log", argv[1]);
  setenv("SPROUT_ANALYSIS_SERVICE_LOG", target, 1);
  setenv("SPROUT_ANALYSIS_SERVICE_CMD", "true", 1);
  int saved_err = dup(STDERR_FILENO);
  int cap = open(path, O_WRONLY | O_CREAT | O_TRUNC, 0600);
  check(saved_err >= 0 && cap >= 0, "capture file for stderr");
  if (saved_err >= 0 && cap >= 0) {
    dup2(cap, STDERR_FILENO);
    char* err = NULL;
    int started = sprout_ensure_analysis_service(&err);
    sprout_close_analysis_service();
    dup2(saved_err, STDERR_FILENO);
    check(started, "the service starts although its log is refused");
    FILE* f = fopen(path, "r");
    char line[1024] = "";
    if (f != NULL) {
      if (fgets(line, sizeof(line), f) == NULL) line[0] = '\0';
      fclose(f);
    }
    check(strstr(line, "analysis service log not used") != NULL, "a refused log is reported");
    check(strstr(line, target) != NULL, "the report names the log path");
  }
  if (cap >= 0) close(cap);
  if (saved_err >= 0) close(saved_err);

  /* "/dev/null" is a request for no log, like "off": no warning is written. */
  snprintf(path, sizeof(path), "%s/devnull_stderr.txt", argv[1]);
  setenv("SPROUT_ANALYSIS_SERVICE_LOG", "/dev/null", 1);
  saved_err = dup(STDERR_FILENO);
  cap = open(path, O_WRONLY | O_CREAT | O_TRUNC, 0600);
  check(saved_err >= 0 && cap >= 0, "capture file for the /dev/null case");
  if (saved_err >= 0 && cap >= 0) {
    dup2(cap, STDERR_FILENO);
    char* err = NULL;
    int started = sprout_ensure_analysis_service(&err);
    sprout_close_analysis_service();
    dup2(saved_err, STDERR_FILENO);
    check(started, "the service starts with /dev/null as its log");
    check(file_size(path) == 0, "a /dev/null log writes no warning");
  }
  if (cap >= 0) close(cap);
  if (saved_err >= 0) close(saved_err);

  /* The default path is per user; an empty variable means the default. */
  char buf[64];
  unsetenv("SPROUT_ANALYSIS_SERVICE_LOG");
  char want[64];
  snprintf(want, sizeof(want), "/tmp/sprout_analysis_service-%lu.log", (unsigned long)getuid());
  check(strcmp(sprout_analysis_service_log_path(buf, sizeof(buf)), want) == 0,
        "the default log path names the user");
  setenv("SPROUT_ANALYSIS_SERVICE_LOG", "", 1);
  check(strcmp(sprout_analysis_service_log_path(buf, sizeof(buf)), want) == 0,
        "an empty variable means the default log path");
  setenv("SPROUT_ANALYSIS_SERVICE_LOG", "/x/y.log", 1);
  check(strcmp(sprout_analysis_service_log_path(buf, sizeof(buf)), "/x/y.log") == 0,
        "the variable names the log path");

  /* The daemon leads its own process group. */
  setenv("SPROUT_ANALYSIS_SERVICE_LOG", "off", 1);
  setenv("SPROUT_ANALYSIS_SERVICE_CMD", "cat", 1);
  {
    char* err = NULL;
    int started = sprout_ensure_analysis_service(&err);
    check(started, "the service starts for the process group check");
    if (started) {
      pid_t pid = sprout_analysis_service_pid;
      int own_group = 0;
      for (int i = 0; i < 100 && !own_group; i++) {
        own_group = getpgid(pid) == pid;
        if (!own_group) usleep(20000);
      }
      check(own_group, "the daemon is its own process group leader");
      check(getpgid(0) != pid, "the daemon is not in this process's group");
    }
    sprout_close_analysis_service();
  }

  /* With any of fds 0, 1 and 2 closed in the parent, a pipe end or the log lands on it. The
   * daemon must still get its stdin, stdout and a stderr that reaches the log. */
  for (int closed = 0; closed <= 2; closed++) {
    snprintf(path, sizeof(path), "%s/closed%d.log", argv[1], closed);
    pid_t p = fork();
    if (p == 0) {
      close(closed);
      setenv("SPROUT_ANALYSIS_SERVICE_LOG", path, 1);
      setenv("SPROUT_ANALYSIS_SERVICE_CMD", "echo daemon-stderr >&2", 1);
      char* err = NULL;
      int started = sprout_ensure_analysis_service(&err);
      sprout_close_analysis_service();
      if (!started) _exit(10);
      FILE* f = fopen(path, "r");
      if (f == NULL) _exit(11);
      char text[512] = "";
      size_t n = fread(text, 1, sizeof(text) - 1, f);
      text[n] = '\0';
      fclose(f);
      if (strstr(text, "analysis-service daemon started") == NULL) _exit(12);
      if (strstr(text, "daemon-stderr") == NULL) _exit(13);
      _exit(0);
    }
    int status = 0;
    check(p > 0 && waitpid(p, &status, 0) == p, "fork for the closed-descriptor case");
    char what[64];
    snprintf(what, sizeof(what), "the service logs with fd %d closed", closed);
    if (!(WIFEXITED(status) && WEXITSTATUS(status) == 0)) {
      fprintf(stderr, "closed fd %d: child status %d\n", closed, WIFEXITED(status) ? WEXITSTATUS(status) : -1);
      check(0, what);
    }
  }

  if (g_failed) return 1;
  printf("analysis-service-log\n");
  return 0;
}
