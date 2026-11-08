#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

long long sprout_register_ctor(long long tag, const char* name, long long arity,
                               const char* field_kinds);
long long fs_read_bytes(long long path_i);
long long sprout_field(long long h, long long idx);
long long bytes_length(long long bytes_h);

/* The Ok payload of fs_read_bytes stays alive and intact under stress GC. */
int main(int argc, char** argv) {
  if (argc != 2) return 99;
  setenv("SPROUT_GC_STRESS", "1", 1);
  sprout_register_ctor(1, "Err", 1, "p");
  sprout_register_ctor(2, "Ok", 1, "p");
  long long result = fs_read_bytes((long long)(uintptr_t)argv[1]);
  printf("%lld\n", bytes_length(sprout_field(result, 0)));
  return 0;
}
