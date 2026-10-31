/* Fixture runtime for scripts/test_approved_builtins.sh. */
typedef struct {
  long long a, b;
} FxPair;

/* Not `long long`, so the old `^long long` grep cannot see these two. */
_Bool fx_flag(long long x) { return x != 0; }
FxPair *fx_pair(long long x) {
  static FxPair p;
  p.a = x;
  p.b = x;
  return &p;
}

long long fx_plain(long long x) { return x; }

__attribute__((noreturn)) void fx_abort(void) {
  for (;;) {
  }
}

/* Non-static, but no Sprout code names it. */
long long fx_internal(long long x) { return x + 1; }
static long long fx_static(long long x) { return x; }
long long fx_use_static(long long x) { return fx_static(x); }
