/* Regression test for the truncated-UTF-8 OOB read (review 2026-06-27, F2).
 *
 * A Sprout String is required to be valid UTF-8. read_file returned the raw
 * file bytes as a String without validating, so a file ending mid-codepoint
 * (or containing an embedded NUL / invalid bytes) produced a malformed String;
 * a later str_len / str_slice / str_char_at then walked past the NUL
 * terminator (OOB read).
 *
 * read_file now validates the content and returns Err on malformed UTF-8.
 *
 * argv[1] = a valid-UTF-8 file (must read back as Ok)
 * argv[2] = an invalid-UTF-8 file (must read back as Err)
 *
 * On the UNFIXED runtime, argv[2] reads back as Ok -> this test fails.
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

long long sprout_register_ctor(long long tag, const char* name, long long arity);
long long read_file(long long path_i);
long long sprout_tag(long long h);

int main(int argc, char** argv) {
  if (argc != 3) return 99;
  sprout_register_ctor(1, "Err", 1);
  sprout_register_ctor(2, "Ok", 1);

  long long ok = read_file((long long)(uintptr_t)argv[1]);
  if (sprout_tag(ok) != 2) {
    fprintf(stderr, "FAIL: valid-UTF-8 file did not read back as Ok\n");
    return 1;
  }

  long long bad = read_file((long long)(uintptr_t)argv[2]);
  if (sprout_tag(bad) != 1) {
    fprintf(stderr, "FAIL: invalid-UTF-8 file did not read back as Err\n");
    return 1;
  }

  printf("read_file-utf8-validated\n");
  return 0;
}
