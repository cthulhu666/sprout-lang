/* Regression test for term_read_key string safety (review 2026-07-03, W2/R4).
 *
 * term_read_key returned a pointer into a mutable `static char buf[2]` for the
 * single-character path, so a retained String silently changed under the caller
 * on the next call (aliasing). It also returned the raw input byte unvalidated,
 * minting an invalid-UTF-8 String from a non-ASCII byte (a single read() cannot
 * capture a complete multibyte sequence).
 *
 * Fixed: the character path heap-allocates + registers a fresh String, and a
 * non-ASCII (>=0x80) byte aborts via tcp_fail (uniform with R1/R3; multibyte
 * key input is a separate deferred feature).
 *
 * stdin is piped (not a tty), so term_read_key takes its getchar() branch.
 * argv[1] selects the case:
 *   alias   -> stdin "AB": two calls must return distinct, correctly-valued
 *              Strings (on the UNFIXED runtime both alias one static buffer).
 *   badbyte -> stdin 0xC3: the call must abort (UNFIXED: returns an invalid
 *              1-byte String, so the process exits 0 and this test fails).
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

long long term_read_key(void);

int main(int argc, char** argv) {
  if (argc != 2) return 99;

  if (strcmp(argv[1], "alias") == 0) {
    const char* a = (const char*)(uintptr_t)term_read_key();
    const char* b = (const char*)(uintptr_t)term_read_key();
    if (a == b) {
      fprintf(stderr, "FAIL: two term_read_key results alias the same buffer\n");
      return 1;
    }
    if (strcmp(a, "A") != 0 || strcmp(b, "B") != 0) {
      fprintf(stderr, "FAIL: term_read_key returned wrong content: '%s' '%s'\n", a, b);
      return 1;
    }
    printf("term-read-key-distinct\n");
    return 0;
  }
  if (strcmp(argv[1], "badbyte") == 0) {
    volatile long long r = term_read_key();
    printf("term_read_key returned %lld on a non-ASCII byte (should have aborted)\n", r);
    return 0;
  }
  return 98;
}
