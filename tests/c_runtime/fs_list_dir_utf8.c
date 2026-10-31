/* fs_list_dir and entry names that are not valid UTF-8.
 *
 * A directory entry name is raw bytes. fs_list_dir returns Strings, so it must not create one
 * from a name that fails utf8_validate: the whole call returns Err(FsIoError(<reason>)) and no
 * partial list escapes. A valid non-ASCII name still lists.
 *
 * Runs under SPROUT_GC_HDRCHECK=1. argv[1] = a scratch directory that exists and is empty.
 *
 * Some filesystems refuse a name that is not valid UTF-8 (APFS answers EILSEQ). Creating the
 * file is then impossible, so that half prints "fs-list-dir-utf8-invalid-skipped" and the case
 * passes; Linux filesystems accept the name, which is where it runs. The valid-name half always
 * runs.
 */
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "str_check.h"

long long fs_list_dir(long long path_i);

enum { ERR = 1, OK = 2, CONS = 5, NIL = 6, FS_IO_ERROR = 7 };

static long long W(const char* p) { return (long long)(uintptr_t)p; }

static int touch(const char* path) {
  int fd = open(path, O_CREAT | O_WRONLY, 0644);
  if (fd < 0) return -1;
  close(fd);
  return 0;
}

/* Number of entries in an Ok list whose names are all headered Strings; -1 if `r` is not Ok. */
static int count_names(long long r, const char* must_contain) {
  if (sprout_tag(r) != OK) return -1;
  int n = 0, seen = 0;
  for (long long l = sprout_field(r, 0); sprout_tag(l) == CONS; l = sprout_field(l, 1)) {
    long long name = sprout_field(l, 0);
    if (!is_str(name, NULL)) return -1;
    if (must_contain != NULL && is_str(name, must_contain)) seen = 1;
    n++;
  }
  return (must_contain != NULL && !seen) ? -1 : n;
}

int main(int argc, char** argv) {
  if (argc != 2) return 99;
  setenv("SPROUT_GC_HDRCHECK", "1", 1);
  sprout_register_ctor(ERR, "Err", 1, "p");
  sprout_register_ctor(OK, "Ok", 1, "p");
  sprout_register_ctor(CONS, "Cons", 2, "pp");
  sprout_register_ctor(NIL, "Nil", 0, "");
  sprout_register_ctor(FS_IO_ERROR, "stdlib.fs.FsIoError", 1, "s");

  const char* dir = argv[1];
  char path[1024];

  /* A valid non-ASCII name lists. */
  snprintf(path, sizeof path, "%s/caf\xc3\xa9.txt", dir);
  if (touch(path) != 0) return fail("could not create a valid non-ASCII file name");
  if (count_names(fs_list_dir(W(dir)), "caf\xc3\xa9.txt") != 1)
    return fail("fs_list_dir did not list the valid non-ASCII name");

  /* An invalid name makes the whole call Err, even with valid entries beside it. */
  snprintf(path, sizeof path, "%s/bad\xff.txt", dir);
  if (touch(path) != 0) {
    printf("fs-list-dir-utf8-invalid-skipped\n");
    fprintf(stderr, "note: creating a non-UTF-8 name failed (%s); invalid-name case skipped\n",
            strerror(errno));
    return 0;
  }
  long long r = fs_list_dir(W(dir));
  if (sprout_tag(r) != ERR) return fail("fs_list_dir returned Ok for a name that is not valid UTF-8");
  long long e = sprout_field(r, 0);
  if (sprout_tag(e) != FS_IO_ERROR) return fail("the error is not FsIoError");
  const char* reason = (const char*)(uintptr_t)sprout_field(e, 0);
  if (!is_str((long long)(uintptr_t)reason, NULL) || strstr(reason, "not valid UTF-8") == NULL ||
      strstr(reason, dir) == NULL)
    return fail("the error text does not name the problem and the directory");

  /* Without the bad entry the directory lists again, so nothing was left open or half built. */
  unlink(path);
  if (count_names(fs_list_dir(W(dir)), "caf\xc3\xa9.txt") != 1)
    return fail("fs_list_dir did not recover after the bad name was removed");

  printf("fs-list-dir-utf8\n");
  return 0;
}
