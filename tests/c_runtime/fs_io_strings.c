/* The fs / io exits hand Sprout headered Strings (docs/string-header-invariant-v0.md §7 step 3).
 *
 * Runs under SPROUT_GC_HDRCHECK=1, so every String that leaves through sprout_str_word is
 * header-checked, and str_eq / str_byte_len check each payload again on the way in.
 *
 * Covered: read_file (null path, directory, missing file, invalid UTF-8, Ok), write_file (null
 * path, null content, unopenable path, short write on /dev/full where it exists, Ok), fs_list_dir
 * (entries; Err(FsNotFound) and Err(FsInvalidPath), which leave through sprout_err1_text, the
 * helper http, crypto and tcp share), stdin_read_bytes, and the non-String words of
 * fs_read_bytes and read_int_lines.
 * Not covered: read_file's out-of-memory branches and its "-" (stdin) path, fs_list_dir's readdir
 * error, the other fs_* entry points (they share fs_err), and the http, crypto and tcp callers
 * of sprout_err1_text.
 *
 * argv[1] = a scratch directory that exists and is empty; stdin carries the bytes "hello".
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "str_check.h"

long long read_file(long long path_i);
long long write_file(long long path_i, long long content_i);
long long fs_list_dir(long long path_i);
long long fs_read_bytes(long long path_i);
long long stdin_read_bytes(long long n);
long long read_int_lines(const char* path);
long long bytes_length(long long bytes_h);
long long vector_length(long long vec);
long long vector_get(long long vec, long long index);

enum { ERR = 1, OK = 2, JUST = 3, NOTHING = 4, CONS = 5, NIL = 6, FS_NOT_FOUND = 7,
       FS_INVALID_PATH = 8 };

static long long W(const char* p) { return (long long)(uintptr_t)p; }

/* `r` is Err(<non-empty String>), or Err("want") when want is given. */
static int is_err(long long r, const char* want) {
  return sprout_tag(r) == ERR && is_str(sprout_field(r, 0), want);
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

  char path[1024];
  const char* dir = argv[1];

  /* read_file: null path, a directory (fread fails, or fopen does), a missing file. */
  if (!is_err(read_file(0), "null path")) return fail("read_file(null) is not Err(null path)");
  if (!is_err(read_file(W(dir)), NULL)) return fail("read_file(dir) is not Err(text)");
  snprintf(path, sizeof path, "%s/missing", dir);
  if (!is_err(read_file(W(path)), NULL)) return fail("read_file(missing) is not Err(text)");

  /* write_file: null path, null content, an unopenable path; then a real write. */
  if (!is_err(write_file(0, W("x")), "null path")) return fail("write_file(null path)");
  snprintf(path, sizeof path, "%s/file.txt", dir);
  if (!is_err(write_file(W(path), 0), "null content")) return fail("write_file(null content)");
  char nope[1100];
  snprintf(nope, sizeof nope, "%s/no/such/dir/f", dir);
  if (!is_err(write_file(W(nope), W("x")), NULL)) return fail("write_file(bad path) is not Err");
  /* A short write fails only when the stream flushes inside fwrite, so use content larger than
   * the stdio buffer on /dev/full. Absent on macOS; skipped there. */
  if (access("/dev/full", W_OK) == 0) {
    size_t big_len = 1 << 20;
    char* big = (char*)malloc(big_len + 1);
    memset(big, 'a', big_len);
    big[big_len] = '\0';
    long long r = write_file(W("/dev/full"), W(big));
    free(big);
    if (!is_err(r, NULL)) return fail("write_file(/dev/full) is not Err");
  }
  long long wrote = write_file(W(path), W("h\xc3\xa9llo\n"));
  if (sprout_tag(wrote) != OK) return fail("write_file did not return Ok");

  /* read_file on invalid UTF-8: Err(<validator's reason>). */
  snprintf(path, sizeof path, "%s/bad.txt", dir);
  if (sprout_tag(write_file(W(path), W("\xff"))) != OK) return fail("bad.txt write_file");
  if (!is_err(read_file(W(path)), NULL)) return fail("read_file(invalid UTF-8) is not Err(text)");
  unlink(path); /* the fs_list_dir check below counts the directory's entries */

  /* Errors that leave through sprout_err1_text: Err(<FsError ctor>(<String>)). */
  snprintf(path, sizeof path, "%s/no_such_dir", dir);
  if (!is_fs_err(fs_list_dir(W(path)), FS_NOT_FOUND, NULL))
    return fail("fs_list_dir(missing) is not Err(FsNotFound(text))");
  if (!is_fs_err(fs_list_dir(W("")), FS_INVALID_PATH, "empty path"))
    return fail("fs_list_dir(\"\") is not Err(FsInvalidPath(\"empty path\"))");

  /* read_file Ok: the file's text, headered. */
  snprintf(path, sizeof path, "%s/file.txt", dir);
  long long got = read_file(W(path));
  if (sprout_tag(got) != OK || !is_str(sprout_field(got, 0), "h\xc3\xa9llo\n"))
    return fail("read_file Ok payload is not the file's text");

  /* fs_list_dir: each entry is a headered String. */
  snprintf(path, sizeof path, "%s/other.txt", dir);
  if (sprout_tag(write_file(W(path), W("o"))) != OK) return fail("second write_file");
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

  /* read_int_lines: a vector of the parsed lines. */
  snprintf(path, sizeof path, "%s/ints.txt", dir);
  if (sprout_tag(write_file(W(path), W("1\n2\n3\n"))) != OK) return fail("ints write_file");
  long long ints = read_int_lines(path);
  long long last = vector_get(ints, 2);
  if (vector_length(ints) != 3 || sprout_tag(last) != JUST || sprout_field(last, 0) != 3)
    return fail("read_int_lines");

  /* stdin_read_bytes: Just("hello"), then Nothing at EOF. */
  long long in = stdin_read_bytes(5);
  if (sprout_tag(in) != JUST || !is_str(sprout_field(in, 0), "hello"))
    return fail("stdin_read_bytes(5) is not Just(\"hello\")");
  if (sprout_tag(stdin_read_bytes(1)) != NOTHING) return fail("stdin_read_bytes at EOF");

  printf("fs-io-strings\n");
  return 0;
}
