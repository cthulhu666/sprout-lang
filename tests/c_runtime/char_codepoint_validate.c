/* Regression test for invalid-codepoint String minting (review 2026-07-03,
 * W2/R3).
 *
 * char_to_str / char_from_codepoint accepted negative, >0x10FFFF, and UTF-16
 * surrogate (D800..DFFF) codepoints, minting invalid-UTF-8 Strings / invalid
 * Chars from pure Sprout code (e.g. char_to_str(-1)). A Sprout String must be
 * valid UTF-8, so these now abort via tcp_fail.
 *
 * argv[1] selects the case (one per process, since the panic exits):
 *   neg       -> char_to_str(-1)          must abort
 *   toobig    -> char_to_str(0x110000)    must abort
 *   surrogate -> char_to_str(0xD800)      must abort
 *   from_neg  -> char_from_codepoint(-1)  must abort
 *   ok        -> char_to_str(0x1F600)     must succeed (valid scalar value)
 *
 * On the UNFIXED runtime the abort cases return a String instead of aborting,
 * so the process exits 0 and this test fails.
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

long long char_to_str(long long codepoint);
long long char_from_codepoint(long long codepoint);
long long str_len(long long s_val);

int main(int argc, char** argv) {
  if (argc != 2) return 99;

  if (strcmp(argv[1], "neg") == 0) {
    volatile long long r = char_to_str(-1);
    printf("char_to_str(-1) returned %lld (should have aborted)\n", r);
    return 0;
  }
  if (strcmp(argv[1], "toobig") == 0) {
    volatile long long r = char_to_str(0x110000);
    printf("char_to_str(0x110000) returned %lld (should have aborted)\n", r);
    return 0;
  }
  if (strcmp(argv[1], "surrogate") == 0) {
    volatile long long r = char_to_str(0xD800);
    printf("char_to_str(0xD800) returned %lld (should have aborted)\n", r);
    return 0;
  }
  if (strcmp(argv[1], "from_neg") == 0) {
    volatile long long r = char_from_codepoint(-1);
    printf("char_from_codepoint(-1) returned %lld (should have aborted)\n", r);
    return 0;
  }
  if (strcmp(argv[1], "ok") == 0) {
    long long s = char_to_str(0x1F600); /* U+1F600, a valid scalar value */
    if (str_len(s) != 1) {
      fprintf(stderr, "FAIL: valid codepoint did not round-trip to one char\n");
      return 1;
    }
    printf("char-codepoint-validated\n");
    return 0;
  }
  return 98;
}
