/* C half of the merge-sort pair: the algorithm of `mutvec_sort` in
 * stdlib/mutable.sprout (insertion sort over blocks of 16, then bottom-up merge
 * passes between two buffers), on the input sort_bench.sprout generates.
 *
 * Prints, per n, ns per element for fill+sort minus fill alone, and a checksum
 * the Sprout half must match. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#define BLOCK 16

static long long now_micros(void) {
  struct timespec ts;
  clock_gettime(CLOCK_MONOTONIC, &ts);
  return (long long)ts.tv_sec * 1000000LL + ts.tv_nsec / 1000;
}

/* The LCG sort_bench.sprout uses; it stays inside Sprout's checked Int range. */
static void fill(long long* a, long long n) {
  long long x = 42;
  for (long long i = 0; i < n; i++) {
    x = (x * 1103515245 + 12345) % 2147483648LL;
    a[i] = (x / 65536) % 65536;
  }
}

static void insertion(long long* a, long long lo, long long hi) {
  for (long long i = lo + 1; i < hi; i++) {
    long long x = a[i], j = i;
    while (j > lo && x < a[j - 1]) { a[j] = a[j - 1]; j--; }
    a[j] = x;
  }
}

static void merge(const long long* src, long long* dst, long long i, long long mid,
                  long long j, long long hi, long long k) {
  while (i < mid && j < hi) dst[k++] = (src[j] < src[i]) ? src[j++] : src[i++];
  while (i < mid) dst[k++] = src[i++];
  while (j < hi) dst[k++] = src[j++];
}

static void sort(long long* a, long long* scratch, long long n) {
  for (long long lo = 0; lo < n; lo += BLOCK) insertion(a, lo, lo + BLOCK < n ? lo + BLOCK : n);
  if (n <= BLOCK) return;
  long long *src = a, *dst = scratch;
  for (long long w = BLOCK; w < n; w *= 2) {
    for (long long lo = 0; lo < n; lo += 2 * w) {
      long long mid = lo + w < n ? lo + w : n, hi = lo + 2 * w < n ? lo + 2 * w : n;
      merge(src, dst, lo, mid, mid, hi, lo);
    }
    long long* t = src; src = dst; dst = t;
  }
  if (src != a) memcpy(a, src, (size_t)n * sizeof(long long));
}

static void run(long long n, long long reps) {
  long long* a = malloc((size_t)n * sizeof(long long));
  long long* scratch = malloc((size_t)n * sizeof(long long));
  long long t0 = now_micros();
  for (long long r = 0; r < reps; r++) fill(a, n);
  long long t1 = now_micros();
  for (long long r = 0; r < reps; r++) { fill(a, n); sort(a, scratch, n); }
  long long t2 = now_micros();
  long long check = 0;
  for (long long i = 0; i < n; i++) {
    if (i > 0 && a[i - 1] > a[i]) { fprintf(stderr, "not sorted at %lld\n", i); exit(1); }
    check = (check * 31 + a[i]) % 1000000007;
  }
  double ns = (double)((t2 - t1) - (t1 - t0)) * 1000.0 / (double)(n * reps);
  printf("sort c n=%lld ns_per_elem=%.1f checksum=%lld\n", n, ns, check);
  free(a);
  free(scratch);
}

int main(void) {
  run(440, 2000);
  run(100000, 5);
  return 0;
}
