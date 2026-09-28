/* The intern table is REPORTED, and the number counts distinct keys.
 *
 * Every map_set interns its key into a permanent malloc'd table outside the
 * arena (intern_string), so neither the allocation counters nor the live census
 * can see that memory: the galaxy client's census read cstr=111(9.2KB) while
 * holding 75,640 keys. `intern=` in the exit report is the only account of it.
 *
 * That number is worth nothing unless it counts DISTINCT keys rather than
 * insertions. A program inserting one key in a loop leaks nothing; one computing
 * a fresh key per iteration grows the table forever, and only the first reading
 * distinguishes them.
 *
 *   distinct — KEYS computed keys must add KEYS entries.
 *   repeated — the same key KEYS times must add one.
 *
 * run.sh reads the number from the exit REPORT rather than the counters, which
 * are static. That is the better assertion anyway: the report is the product, so
 * this covers its format alongside the arithmetic. Neither run asserts an
 * absolute value — the runtime interns during init, and only the difference
 * between two runs of this binary cancels that baseline.
 */
#include <stdio.h>
#include <string.h>

long long sprout_set_argv(int argc, char** argv);
long long map_empty(void);
long long map_set(long long map_h, long long key_val, long long value);
long long int_to_string(long long value);
long long sprout_gc_push_i64_root(void* slot);
long long sprout_gc_pop_roots(long long count);

#define KEYS 2000

int main(int argc, char** argv) {
  sprout_set_argv(argc, argv);
  if (argc < 2) {
    fprintf(stderr, "usage: %s distinct|repeated\n", argv[0]);
    return 2;
  }
  int distinct = strcmp(argv[1], "distinct") == 0;
  if (!distinct && strcmp(argv[1], "repeated") != 0) {
    fprintf(stderr, "unknown selector '%s'\n", argv[1]);
    return 2;
  }

  /* Both handles are rooted for the whole loop: int_to_string and map_set both
     allocate, so either can collect while the other's value is only in a local. */
  long long m = map_empty();
  long long key = 0;
  sprout_gc_push_i64_root(&m);
  sprout_gc_push_i64_root(&key);

  if (!distinct) key = int_to_string(7);
  for (long long i = 0; i < KEYS; i++) {
    if (distinct) key = int_to_string(i);
    m = map_set(m, key, i);
  }

  sprout_gc_pop_roots(2);
  printf("intern-probe-%s\n", distinct ? "distinct" : "repeated");
  return 0;
}
