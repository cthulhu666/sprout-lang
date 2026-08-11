/* Regression test for term_read_key string safety (review 2026-07-03, W2/R4).
 *
 * term_read_key returned a pointer into a mutable `static char buf[2]` for the
 * single-character path, so a retained String silently changed under the caller
 * on the next call (aliasing). It also returned the raw input byte unvalidated,
 * minting an invalid-UTF-8 String from a non-ASCII byte (a single read() cannot
 * capture a complete multibyte sequence).
 *
 * Fixed: the character path heap-allocates + registers a fresh String, and a
 * non-ASCII byte is no longer returned raw.
 *
 * SECOND ROUND (2026-08-11): aborting on >=0x80 was the deferred half of that
 * fix, and the deferral had a cost — every accented or non-Latin keypress killed
 * the REPL and lost the session. term_read_key now ASSEMBLES the continuation
 * bytes into a complete character, and substitutes U+FFFD when the sequence is
 * truncated or invalid rather than aborting. U+FFFD follows the WHATWG Encoding
 * Standard ("The constraints in the UTF-8 decoder above match 'Best Practices
 * for Using U+FFFD' from the Unicode standard") and Rust's from_utf8_lossy; it
 * is chosen over an error channel because this builtin returns a bare String and
 * a REPL has no meaningful recovery to perform for a malformed keypress.
 *
 * The invariant the abort protected is still held: every returned String is
 * valid UTF-8. It is now held by construction (utf8_validate on the assembled
 * bytes) rather than by refusing to proceed.
 *
 * stdin is piped (not a tty), so term_read_key takes its getchar() branch.
 * argv[1] selects the case:
 *   alias     -> stdin "AB": two calls must return distinct, correctly-valued
 *                Strings (on the UNFIXED runtime both alias one static buffer).
 *   badbyte   -> stdin 0xC3 alone: a truncated sequence must return U+FFFD and
 *                must NOT abort. (Before this round: aborted. Before the first
 *                round: returned an invalid 1-byte String.)
 *   multibyte -> stdin "é" (C3 A9): must return both bytes as one String, so a
 *                2-byte key arrives as one complete character.
 *   wide      -> stdin "日🌱" (E6 97 A5, F0 9F 8C B1): 3- and 4-byte sequences
 *                back to back, so the continuation count is read from the lead
 *                byte rather than assumed to be one.
 *   badcont   -> stdin C3 41 ('A' where a continuation byte belongs): must
 *                return U+FFFD, proving continuation bytes are VALIDATED and not
 *                merely counted.
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
  /* U+FFFD REPLACEMENT CHARACTER, UTF-8 encoded. */
  static const char* replacement = "\357\277\275";

  if (strcmp(argv[1], "badbyte") == 0) {
    const char* r = (const char*)(uintptr_t)term_read_key();
    if (strcmp(r, replacement) != 0) {
      fprintf(stderr, "FAIL: truncated sequence gave '%s', want U+FFFD\n", r);
      return 1;
    }
    printf("term-read-key-replacement\n");
    return 0;
  }
  if (strcmp(argv[1], "badcont") == 0) {
    const char* r = (const char*)(uintptr_t)term_read_key();
    if (strcmp(r, replacement) != 0) {
      fprintf(stderr, "FAIL: invalid continuation gave '%s', want U+FFFD\n", r);
      return 1;
    }
    /* The 'A' must not be swallowed: an unconsumed byte belongs to the NEXT key.
     * Getting this wrong loses a keystroke, which is invisible in a pass/fail on
     * the malformed key alone. */
    const char* next = (const char*)(uintptr_t)term_read_key();
    if (strcmp(next, "A") != 0) {
      fprintf(stderr, "FAIL: byte after a bad continuation was lost, got '%s'\n", next);
      return 1;
    }
    printf("term-read-key-badcont-keeps-next\n");
    return 0;
  }
  if (strcmp(argv[1], "multibyte") == 0) {
    const char* r = (const char*)(uintptr_t)term_read_key();
    if (strcmp(r, "\303\251") != 0) {
      fprintf(stderr, "FAIL: 2-byte key gave '%s' (%zu bytes), want 'é'\n", r, strlen(r));
      return 1;
    }
    printf("term-read-key-multibyte\n");
    return 0;
  }
  if (strcmp(argv[1], "wide") == 0) {
    const char* three = (const char*)(uintptr_t)term_read_key();
    if (strcmp(three, "\346\227\245") != 0) {
      fprintf(stderr, "FAIL: 3-byte key gave '%s' (%zu bytes)\n", three, strlen(three));
      return 1;
    }
    const char* four = (const char*)(uintptr_t)term_read_key();
    if (strcmp(four, "\360\237\214\261") != 0) {
      fprintf(stderr, "FAIL: 4-byte key gave '%s' (%zu bytes)\n", four, strlen(four));
      return 1;
    }
    printf("term-read-key-wide\n");
    return 0;
  }
  return 98;
}
