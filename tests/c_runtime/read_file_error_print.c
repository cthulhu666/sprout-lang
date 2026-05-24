#include <stdint.h>

long long sprout_register_ctor(long long tag, const char* name, long long arity);
long long read_file(long long path_i);
long long print_value(long long x);

int main(void) {
  sprout_register_ctor(1, "Err", 1);
  sprout_register_ctor(2, "Ok", 1);
  long long result = read_file((long long)(uintptr_t)"/definitely/missing/sprout-runtime-test.txt");
  print_value(result);
  return 0;
}
