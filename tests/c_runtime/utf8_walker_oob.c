/* Regression test for the truncated-UTF-8 OOB reads in the string walkers
 * (fundamentals review 2026-07-03, W2/R1).
 *
 * sprout_utf8_char_width() returns a width (2/3/4) from the lead byte alone.
 * The walkers (sprout_utf8_codepoint_count, sprout_utf8_byte_offset,
 * sprout_utf8_decode_at via str_char_at) then advanced/decoded that many bytes
 * with no check that the continuation bytes are actually present. A String that
 * ends in a truncated multibyte lead therefore walked past the NUL terminator
 * (heap-buffer-overflow).
 *
 * A Sprout String is a NUL-terminated heap buffer here: bytes {0xF0, '\0'} is a
 * 4-byte lead followed immediately by the terminator — three continuation bytes
 * short. On the UNFIXED runtime, ASan reports a heap-buffer-overflow. On the
 * fixed runtime the walker detects the truncation and aborts cleanly via
 * tcp_fail *before* any out-of-bounds read, so ASan reports nothing and the
 * process exits non-zero with the panic message.
 *
 * argv[1] selects which walker to exercise (one per process, since the panic
 * exits): len | char_at | slice.
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

long long sprout_register_ctor(long long tag, const char* name, long long arity);
long long str_len(long long s_val);
long long str_char_at(long long s_val, long long index);
long long str_slice(long long s_i, long long start, long long length);
long long sprout_tag(long long h);

/* Heap-allocate {bytes..., '\0'} so a past-the-end read is a heap-buffer
 * overflow ASan is guaranteed to flag (vs. a static array's global redzone). */
static long long make_bad(const unsigned char* bytes, size_t n) {
  char* p = (char*)malloc(n + 1);
  memcpy(p, bytes, n);
  p[n] = '\0';
  return (long long)(uintptr_t)p;
}

int main(int argc, char** argv) {
  if (argc != 2) return 99;
  sprout_register_ctor(1, "Nothing", 0);
  sprout_register_ctor(2, "Just", 1);

  /* Truncated 4-byte lead, immediately terminated. */
  const unsigned char truncated[] = { 0xF0 };
  long long s = make_bad(truncated, sizeof(truncated));

  if (strcmp(argv[1], "len") == 0) {
    volatile long long n = str_len(s);
    printf("str_len returned %lld (should have aborted)\n", n);
  } else if (strcmp(argv[1], "char_at") == 0) {
    volatile long long r = str_char_at(s, 0);
    printf("str_char_at returned tag %lld (should have aborted)\n", sprout_tag(r));
  } else if (strcmp(argv[1], "slice") == 0) {
    volatile long long r = str_slice(s, 0, 1);
    printf("str_slice returned %lld (should have aborted)\n", r);
  } else {
    return 98;
  }
  return 0; /* reached only if no abort occurred -> test fails */
}
