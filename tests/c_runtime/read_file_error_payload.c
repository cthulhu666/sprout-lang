/* read_file on a missing path returns an Err whose payload is a headered String.
 * Under SPROUT_GC_HDRCHECK, str_eq aborts on a payload without a CSTR header (e.g. an
 * unmanaged strerror string), and str_byte_len on a header whose length is wrong. */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

long long sprout_register_ctor(long long tag, const char* name, long long arity,
                               const char* field_kinds);
_Bool str_eq(const char* left, const char* right);
long long str_byte_len(long long s_val);
long long read_file(long long path_i);
long long sprout_tag(long long h);
long long sprout_field(long long h, long long idx);

int main(void) {
  setenv("SPROUT_GC_HDRCHECK", "1", 1);
  sprout_register_ctor(1, "Err", 1, "p");
  sprout_register_ctor(2, "Ok", 1, "p");
  long long result = read_file((long long)(uintptr_t)"/definitely/missing/sprout-runtime-test.txt");
  if (sprout_tag(result) != 1) {
    fprintf(stderr, "FAIL: missing file did not read back as Err\n");
    return 1;
  }
  const char* msg = (const char*)(uintptr_t)sprout_field(result, 0);
  if (msg == NULL || strlen(msg) == 0) {
    fprintf(stderr, "FAIL: Err payload is not a non-empty string\n");
    return 1;
  }
  if (!str_eq(msg, msg) || str_byte_len((long long)(uintptr_t)msg) != (long long)strlen(msg)) {
    fprintf(stderr, "FAIL: Err payload header disagrees with the string\n");
    return 1;
  }
  printf("read_file-err-payload-string\n");
  return 0;
}
