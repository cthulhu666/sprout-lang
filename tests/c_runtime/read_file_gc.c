#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

long long sprout_register_ctor(long long tag, const char* name, long long arity);
long long read_file(long long path_i);
long long sprout_field(long long h, long long idx);

int main(int argc, char** argv) {
  if (argc != 2) return 99;
  setenv("SPROUT_GC_STRESS", "1", 1);
  sprout_register_ctor(1, "Err", 1);
  sprout_register_ctor(2, "Ok", 1);
  long long result = read_file((long long)(uintptr_t)argv[1]);
  const char* payload = (const char*)(uintptr_t)sprout_field(result, 0);
  printf("%zu\n", strlen(payload));
  return 0;
}
