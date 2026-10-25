/* Runtime code that names a constructor takes its tag from the registered metadata when the
 * program supplies no constant. Register the constructors at non-default tags and check each
 * builder returns the registered tag: the `_unboxed` Maybe reads (hit and miss), `read_file`'s
 * `Err` and `Ok`, and the channel builders' `stdlib.chan.Got` / `stdlib.chan.Closed`. Each
 * builder runs twice so a cached tag is exercised too.
 * Unboxed layout: low word = tag, high word = payload.
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

typedef unsigned __int128 Unboxed2;

long long sprout_register_ctor(long long tag, const char* name, long long arity,
                               const char* field_kinds);
long long vector_empty(void);
long long vector_push(long long vec, long long value);
long long bytes_from_utf8(long long raw);
Unboxed2 vector_get_unboxed(long long vec, long long index);
Unboxed2 bytes_get_unboxed(long long bytes, long long index);
long long read_file(long long path);
long long sprout_tag(long long h);
long long sprout_chan_make_got(long long v);
long long sprout_chan_make_closed(void);

static int check(const char* what, Unboxed2 got, long long want_tag, long long want_payload) {
  long long tag = (long long)(unsigned long long)got;
  long long payload = (long long)(unsigned long long)(got >> 64);
  if (tag != want_tag || payload != want_payload) {
    fprintf(stderr, "%s: got tag %lld payload %lld, want tag %lld payload %lld\n", what, tag,
            payload, want_tag, want_payload);
    return 1;
  }
  return 0;
}

static int check_tag(const char* what, long long obj, long long want_tag) {
  long long tag = sprout_tag(obj);
  if (tag != want_tag) {
    fprintf(stderr, "%s: got tag %lld, want %lld\n", what, tag, want_tag);
    return 1;
  }
  return 0;
}

int main(void) {
  sprout_register_ctor(7, "Nothing", 0, "");
  sprout_register_ctor(8, "Just", 1, "i");
  sprout_register_ctor(21, "Err", 1, "p");
  sprout_register_ctor(22, "Ok", 1, "p");
  sprout_register_ctor(23, "stdlib.chan.Got", 1, "i");
  sprout_register_ctor(24, "stdlib.chan.Closed", 0, "");

  long long vec = vector_empty();
  vector_push(vec, 41);
  vector_push(vec, 42);
  long long bytes = bytes_from_utf8((long long)(const char*)"AB");

  int bad = 0;
  bad |= check("vector hit", vector_get_unboxed(vec, 1), 8, 42);
  bad |= check("vector miss", vector_get_unboxed(vec, 2), 7, 0);
  bad |= check("vector negative", vector_get_unboxed(vec, -1), 7, 0);
  bad |= check("bytes hit", bytes_get_unboxed(bytes, 1), 8, 'B');
  bad |= check("bytes miss", bytes_get_unboxed(bytes, 2), 7, 0);

  char path[] = "/tmp/sprout_tag_fallback_XXXXXX";
  int fd = mkstemp(path);
  if (fd < 0) {
    perror("mkstemp");
    return 1;
  }
  close(fd);
  for (int round = 0; round < 2; round++) {
    bad |= check_tag("read_file missing -> Err",
                     read_file((long long)(uintptr_t)"/definitely/missing/sprout-tag.txt"), 21);
    bad |= check_tag("read_file ok -> Ok", read_file((long long)(uintptr_t)path), 22);
    bad |= check_tag("chan Got", sprout_chan_make_got(5), 23);
    bad |= check_tag("chan Closed", sprout_chan_make_closed(), 24);
    bad |= check("vector hit again", vector_get_unboxed(vec, 0), 8, 41);
  }
  unlink(path);
  if (bad) return 1;
  puts("ctor-tag-fallback");
  return 0;
}
