long long sprout_register_ctor(long long tag, const char* name, long long arity);

int main(void) {
  for (long long i = 0; i < 2050; i++) {
    sprout_register_ctor(i, "Ctor", 0);
  }
  return 0;
}
