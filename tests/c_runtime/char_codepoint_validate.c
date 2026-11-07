/* Regression test for invalid-codepoint String minting (review 2026-07-03,
 * W2/R3).
 *
 * char_from_codepoint accepted negative, >0x10FFFF, and UTF-16 surrogate
 * (D800..DFFF) codepoints, minting invalid Chars, and so invalid-UTF-8 Strings,
 * from pure Sprout code (e.g. char_from_codepoint(-1)). A Sprout String must be
 * valid UTF-8, so these now abort via sprout_fail. char_to_string takes a Char,
 * which Sprout cannot build out of range; it re-checks as a backstop.
 *
 * argv[1] selects the case (one per process, since the panic exits):
 *   neg       -> char_to_string(-1)        must abort
 *   toobig    -> char_to_string(0x110000)  must abort
 *   surrogate -> char_to_string(0xD800)    must abort
 *   from_neg  -> char_from_codepoint(-1)   must abort
 *   nul       -> char_to_string(0)         must abort (a String holds no NUL)
 *   nul_char  -> char_from_codepoint(0)    must succeed (0 is a valid Char)
 *   ok        -> char_to_string(0x1F600)   must succeed (valid scalar value)
 *
 * On the UNFIXED runtime the abort cases return a String instead of aborting,
 * so the process exits 0 and this test fails.
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

long long char_to_string(long long codepoint);
long long char_from_codepoint(long long codepoint);
long long str_len(long long s_val);

int main(int argc, char** argv) {
  if (argc != 2) return 99;

  if (strcmp(argv[1], "neg") == 0) {
    volatile long long r = char_to_string(-1);
    printf("char_to_string(-1) returned %lld (should have aborted)\n", r);
    return 0;
  }
  if (strcmp(argv[1], "toobig") == 0) {
    volatile long long r = char_to_string(0x110000);
    printf("char_to_string(0x110000) returned %lld (should have aborted)\n", r);
    return 0;
  }
  if (strcmp(argv[1], "surrogate") == 0) {
    volatile long long r = char_to_string(0xD800);
    printf("char_to_string(0xD800) returned %lld (should have aborted)\n", r);
    return 0;
  }
  if (strcmp(argv[1], "from_neg") == 0) {
    volatile long long r = char_from_codepoint(-1);
    printf("char_from_codepoint(-1) returned %lld (should have aborted)\n", r);
    return 0;
  }
  if (strcmp(argv[1], "nul") == 0) {
    volatile long long r = char_to_string(0);
    printf("char_to_string(0) returned %lld (should have aborted)\n", r);
    return 0;
  }
  if (strcmp(argv[1], "nul_char") == 0) {
    if (char_from_codepoint(0) != 0) {
      fprintf(stderr, "FAIL: char_from_codepoint(0) did not return 0\n");
      return 1;
    }
    printf("nul-char-ok\n");
    return 0;
  }
  if (strcmp(argv[1], "ok") == 0) {
    long long s = char_to_string(0x1F600); /* U+1F600, a valid scalar value */
    if (str_len(s) != 1) {
      fprintf(stderr, "FAIL: valid codepoint did not round-trip to one char\n");
      return 1;
    }
    printf("char-codepoint-validated\n");
    return 0;
  }
  return 98;
}
