// Minimal SanitizerCoverage sink: inline-bool-flag + pc-table.
// At exit writes the flag array to $SANCOV_OUT/<binary-tag>.<pid>.flags.
// If $SANCOV_PCS is set and the file does not exist, writes the unslid PC table there
// (pairs of uint64: pc, flags; flags&1 = function entry).
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <mach-o/dyld.h>

static bool *g_flags_beg, *g_flags_end;
static const uintptr_t *g_pcs_beg, *g_pcs_end;
static int g_registered;

static void sancov_write_all(int fd, const void *p, size_t n) {
  const char *c = p;
  while (n) { ssize_t w = write(fd, c, n); if (w <= 0) return; c += w; n -= (size_t)w; }
}

static void sancov_dump(void) {
  const char *dir = getenv("SANCOV_OUT");
  if (dir && g_flags_beg) {
    const char *tag = getenv("SANCOV_TAG");
    char path[4096];
    snprintf(path, sizeof path, "%s/%s.%d.flags", dir, tag ? tag : "bin", (int)getpid());
    int fd = open(path, O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (fd >= 0) { sancov_write_all(fd, g_flags_beg, (size_t)(g_flags_end - g_flags_beg)); close(fd); }
  }
  const char *pcs = getenv("SANCOV_PCS");
  if (pcs && g_pcs_beg && access(pcs, F_OK) != 0) {
    intptr_t slide = _dyld_get_image_vmaddr_slide(0);
    char tmp[4096];
    snprintf(tmp, sizeof tmp, "%s.%d.tmp", pcs, (int)getpid());
    int fd = open(tmp, O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (fd >= 0) {
      size_t n = (size_t)(g_pcs_end - g_pcs_beg) / 2;
      for (size_t i = 0; i < n; i++) {
        uint64_t rec[2] = { (uint64_t)(g_pcs_beg[2 * i] - (uintptr_t)slide), (uint64_t)g_pcs_beg[2 * i + 1] };
        sancov_write_all(fd, rec, sizeof rec);
      }
      close(fd);
      rename(tmp, pcs);
    }
  }
}

static void sancov_register(void) {
  if (!g_registered) { g_registered = 1; atexit(sancov_dump); }
}

void __sanitizer_cov_bool_flag_init(bool *start, bool *stop) {
  if (start == stop) return;
  g_flags_beg = start; g_flags_end = stop;
  sancov_register();
}

void __sanitizer_cov_pcs_init(const uintptr_t *beg, const uintptr_t *end) {
  g_pcs_beg = beg; g_pcs_end = end;
  sancov_register();
}
