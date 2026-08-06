/* C libm reference for bench/math_transcendental.
 *
 * Mirrors math_transcendental_bench.sprout exactly: same iteration count, same
 * argument sweep, same accumulate-and-checksum shape, same baseline row. The point is
 * a like-for-like ratio, so any change to the sweep must be made in both files.
 *
 * On keeping the accumulator NON-volatile: an earlier version used `volatile double
 * acc`, which was a measurement bug rather than a safety net. The volatile store/load
 * round trip costs ~2.2ns per iteration and becomes a latency chain that the libm call
 * executes inside out-of-order, so `exp` measured only 7us more than the empty
 * baseline over 200k reps — an impossible 0.03ns per exp call. A plain accumulator
 * cannot be optimised away either, because `acc` is printed as a checksum, and the
 * serial `acc = acc + f(x)` FP dependency blocks the reassociation that vectorising
 * the libm calls would require (absent -ffast-math, which is not used).
 *
 * Build: see bench.sh (clang -O2 -lm).
 */
#include <math.h>
#include <stdio.h>
#include <sys/time.h>

#define REPS 2000000
#define STEP 0.000005

static long long now_us(void) {
  struct timeval tv;
  gettimeofday(&tv, NULL);
  return (long long)tv.tv_sec * 1000000 + (long long)tv.tv_usec;
}

static double arg(long i) { return (double)i * STEP; }

/* One row: run `which` over the sweep, print elapsed microseconds and checksum. */
static void row(const char *label, int which) {
  double acc = 0.0;
  long long t0 = now_us();
  for (long i = 0; i < REPS; i++) {
    double x = arg(i);
    switch (which) {
      case 0: acc = acc + x;                    break; /* baseline  */
      case 1: acc = acc + exp(x);               break;
      case 2: acc = acc + log(x + 0.001);       break; /* ln        */
      case 3: acc = acc + log10(x + 0.001);     break;
      case 4: acc = acc + cbrt(x + 0.001);      break;
      case 5: acc = acc + pow(x + 1.0, 2.5);    break; /* pow_frac  */
      case 6: acc = acc + pow(x + 1.0, 4.0);    break; /* pow_int   */
      default: break;
    }
  }
  long long t1 = now_us();
  printf("%s us=%lld checksum=%.17g\n", label, t1 - t0, (double)acc);
}

int main(void) {
  printf("reps=%d\n", REPS);
  row("baseline", 0);
  row("exp", 1);
  row("ln", 2);
  row("log10", 3);
  row("cbrt", 4);
  row("pow_frac", 5);
  row("pow_int", 6);
  return 0;
}
