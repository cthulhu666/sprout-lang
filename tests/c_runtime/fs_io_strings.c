/* The fs / io exits hand Sprout headered Strings (docs/string-header-invariant-v0.md §7 step 3).
 *
 * Runs under SPROUT_GC_HDRCHECK=1, so every String that leaves through sprout_str_word is
 * header-checked, and str_eq / str_byte_len check each payload again on the way in.
 *
 * Covered: fs_list_dir (entries; Err(FsNotFound) and Err(FsInvalidPath), which leave through
 * sprout_err1_text, the helper http, crypto and tcp share), stdin_read_bytes (as Bytes), and the
 * non-String words of fs_read_bytes and read_int_lines.
 * Not covered: fs_list_dir's readdir error, the other fs_* entry points (they share fs_err), and
 * the http, crypto and tcp callers of sprout_err1_text.
 *
 * argv[1] = a scratch directory that exists and is empty; stdin carries the bytes "hel\0o".
 */
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "str_check.h"

/* Not under ASan: its allocator aborts on an oversized malloc instead of returning NULL. */
#if defined(__SANITIZE_ADDRESS__)
#define SPROUT_TEST_ASAN 1
#elif defined(__has_feature)
#if __has_feature(address_sanitizer)
#define SPROUT_TEST_ASAN 1
#endif
#endif

long long fs_list_dir(long long path_i);
long long fs_read_bytes(long long path_i);
long long stdin_read_bytes(long long n);
long long read_int_lines(const char* path);
long long bytes_length(long long bytes_h);
long long bytes_get(long long bytes_h, long long index);
long long vector_length(long long vec);
long long vector_get(long long vec, long long index);

enum { ERR = 1, OK = 2, JUST = 3, NOTHING = 4, CONS = 5, NIL = 6, FS_NOT_FOUND = 7,
       FS_INVALID_PATH = 8, FS_IO_ERROR = 9 };

static long long W(const char* p) { return (long long)(uintptr_t)p; }

/* Fixture setup: write `content` to `path`. Returns 0 on success. */
static int put_file(const char* path, const char* content) {
  FILE* f = fopen(path, "wb");
  if (f == NULL) return 1;
  size_t len = strlen(content);
  int bad = fwrite(content, 1, len, f) != len;
  return fclose(f) != 0 || bad;
}

/* `r` is Err(<ctor>(<String>)): the String is `want`, or any non-empty text when NULL. */
static int is_fs_err(long long r, long long ctor, const char* want) {
  if (sprout_tag(r) != ERR) return 0;
  long long e = sprout_field(r, 0);
  return sprout_tag(e) == ctor && is_str(sprout_field(e, 0), want);
}

int main(int argc, char** argv) {
  if (argc != 2) return 99;
  setenv("SPROUT_GC_HDRCHECK", "1", 1);
  sprout_register_ctor(ERR, "Err", 1, "p");
  sprout_register_ctor(OK, "Ok", 1, "p");
  sprout_register_ctor(JUST, "Just", 1, "p");
  sprout_register_ctor(NOTHING, "Nothing", 0, "");
  sprout_register_ctor(CONS, "Cons", 2, "pp");
  sprout_register_ctor(NIL, "Nil", 0, "");
  sprout_register_ctor(FS_NOT_FOUND, "stdlib.fs.FsNotFound", 1, "s");
  sprout_register_ctor(FS_INVALID_PATH, "stdlib.fs.FsInvalidPath", 1, "s");
  sprout_register_ctor(FS_IO_ERROR, "stdlib.fs.FsIoError", 1, "s");

  char path[1024];
  const char* dir = argv[1];

  snprintf(path, sizeof path, "%s/file.txt", dir);
  if (put_file(path, "h\xc3\xa9llo\n") != 0) return fail("file.txt setup");

  /* Errors that leave through sprout_err1_text: Err(<FsError ctor>(<String>)). */
  snprintf(path, sizeof path, "%s/no_such_dir", dir);
  if (!is_fs_err(fs_list_dir(W(path)), FS_NOT_FOUND, NULL))
    return fail("fs_list_dir(missing) is not Err(FsNotFound(text))");
  if (!is_fs_err(fs_list_dir(W("")), FS_INVALID_PATH, "empty path"))
    return fail("fs_list_dir(\"\") is not Err(FsInvalidPath(\"empty path\"))");

  /* fs_list_dir: each entry is a headered String. */
  snprintf(path, sizeof path, "%s/other.txt", dir);
  if (put_file(path, "o") != 0) return fail("other.txt setup");
  long long listed = fs_list_dir(W(dir));
  if (sprout_tag(listed) != OK) return fail("fs_list_dir did not return Ok");
  int seen_file = 0, seen_other = 0, count = 0;
  for (long long l = sprout_field(listed, 0); sprout_tag(l) == CONS; l = sprout_field(l, 1)) {
    long long name = sprout_field(l, 0);
    if (!is_str(name, NULL)) return fail("fs_list_dir entry is not a headered String");
    seen_file += is_str(name, "file.txt");
    seen_other += is_str(name, "other.txt");
    count++;
  }
  if (count != 2 || !seen_file || !seen_other) return fail("fs_list_dir entries are wrong");

  /* fs_read_bytes: a Bytes object of the file's length. */
  snprintf(path, sizeof path, "%s/file.txt", dir);
  long long bytes = fs_read_bytes(W(path));
  if (sprout_tag(bytes) != OK || bytes_length(sprout_field(bytes, 0)) != 7)
    return fail("fs_read_bytes did not return the 7 bytes");

  /* fs_read_bytes sizes its buffer from st_size, so a sparse file of 2^50 bytes (more than any
   * 47-bit address space holds) makes malloc fail: the out-of-memory Err must name the path like
   * every other fs_* Err. Skipped where the filesystem refuses a file that large (ext4 caps files
   * at 16 TiB, so this runs on APFS and tmpfs). */
#ifndef SPROUT_TEST_ASAN
  snprintf(path, sizeof path, "%s/huge.sparse", dir);
  FILE* huge = fopen(path, "wb");
  if (huge != NULL) {
    int sized = ftruncate(fileno(huge), (off_t)1 << 50) == 0;
    fclose(huge);
    if (sized) {
      char want[1100];
      snprintf(want, sizeof want, "%s: out of memory", path);
      long long r = fs_read_bytes(W(path));
      if (!is_fs_err(r, FS_IO_ERROR, want))
        return fail("fs_read_bytes out-of-memory Err does not name the path");
    }
    unlink(path);
  }
#endif

  /* A path of about 2000 bytes: the Err names all of it, then the reason. The reason is whatever
   * errno the OS gives (ENAMETOOLONG for a 2000-byte component, else ENOENT). */
  {
    static char longpath[4200];
    static char want[4300];
    int n = snprintf(longpath, sizeof longpath, "%s/", dir);
    memset(longpath + n, 'a', 2000);
    longpath[n + 2000] = '\0';
    int fd = open(longpath, O_RDONLY);
    if (fd >= 0) return fail("the long path unexpectedly opened");
    int e = errno;
    snprintf(want, sizeof want, "%s: %s", longpath, strerror(e));
    if (!is_fs_err(fs_read_bytes(W(longpath)), e == ENAMETOOLONG ? FS_INVALID_PATH : FS_NOT_FOUND, want))
      return fail("fs_read_bytes on a 2000-byte path does not give \"<full path>: <reason>\"");
  }

  /* read_int_lines: a vector of the parsed lines. */
  snprintf(path, sizeof path, "%s/ints.txt", dir);
  if (put_file(path, "1\n2\n3\n") != 0) return fail("ints.txt setup");
  long long ints = read_int_lines(path);
  long long last = vector_get(ints, 2);
  if (vector_length(ints) != 3 || sprout_tag(last) != JUST || sprout_field(last, 0) != 3)
    return fail("read_int_lines");

  /* stdin_read_bytes: Just(Bytes "hel\0o", NUL kept), then Nothing at EOF. */
  long long in = stdin_read_bytes(5);
  static const char want[5] = { 'h', 'e', 'l', '\0', 'o' };
  if (sprout_tag(in) != JUST || bytes_length(sprout_field(in, 0)) != 5)
    return fail("stdin_read_bytes(5) is not Just(<5 bytes>)");
  for (int i = 0; i < 5; i++) {
    long long b = bytes_get(sprout_field(in, 0), i);
    if (sprout_tag(b) != JUST || sprout_field(b, 0) != (unsigned char)want[i])
      return fail("stdin_read_bytes(5) changed a byte");
  }
  if (sprout_tag(stdin_read_bytes(1)) != NOTHING) return fail("stdin_read_bytes at EOF");

  printf("fs-io-strings\n");
  return 0;
}
