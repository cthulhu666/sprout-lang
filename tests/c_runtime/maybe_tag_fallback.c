/* The `_unboxed` reads take the `Just`/`Nothing` tags from the registered constructor
 * metadata when the program does not supply them as constants. Register both with
 * non-default tags and check each read returns the registered one, for a hit and a miss.
 * Returned layout: low word = tag, high word = payload.
 */
#include <stdio.h>
#include <string.h>

typedef unsigned __int128 Unboxed2;

long long sprout_register_ctor(long long tag, const char* name, long long arity,
                               const char* field_kinds);
long long vector_empty(void);
long long vector_push(long long vec, long long value);
long long bytes_from_utf8(long long raw);
Unboxed2 vector_get_unboxed(long long vec, long long index);
Unboxed2 bytes_get_unboxed(long long bytes, long long index);

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

int main(void) {
  sprout_register_ctor(7, "Nothing", 0, "");
  sprout_register_ctor(8, "Just", 1, "i");

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
  if (bad) return 1;
  puts("maybe-tag-fallback");
  return 0;
}
