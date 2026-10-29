/* Map keys do not grow the intern table.
 *
 * A map stores its key string as given, and the collector frees it once the map
 * drops it. The intern table (intern_string) is a permanent malloc'd table outside
 * the arena, kept for env, argv and terminal tokens; `intern=` in the exit report
 * is the only account of it, since neither the allocation counters nor the live
 * census can see that memory.
 *
 * Map keys once went through it, so a program computing a fresh key per insert
 * grew it for the life of the process. Each selector below must leave it where one
 * key repeated leaves it:
 *
 *   distinct — KEYS computed keys must add no entries.
 *   repeated — the same key KEYS times: the baseline.
 *   remove   — removing KEYS computed keys a map does not hold must add none.
 *
 * run.sh reads the number from the exit REPORT rather than the counters, which
 * are static. That is the better assertion anyway: the report is the product, so
 * this covers its format alongside the arithmetic. No run asserts an absolute
 * value — the runtime interns during init, and only the difference between two
 * runs of this binary cancels that baseline.
 */
#include <stdio.h>
#include <string.h>

long long sprout_set_argv(int argc, char** argv);
long long map_empty(void);
long long map_set(long long map_h, long long key_val, long long value);
long long map_remove(long long map_h, long long key_val);
long long int_to_string(long long value);
long long sprout_gc_push_i64_root(void* slot);
long long sprout_gc_pop_roots(long long count);

#define KEYS 2000

int main(int argc, char** argv) {
  sprout_set_argv(argc, argv);
  if (argc < 2) {
    fprintf(stderr, "usage: %s distinct|repeated|remove\n", argv[0]);
    return 2;
  }
  int distinct = strcmp(argv[1], "distinct") == 0;
  int remove = strcmp(argv[1], "remove") == 0;
  if (!distinct && !remove && strcmp(argv[1], "repeated") != 0) {
    fprintf(stderr, "unknown selector '%s'\n", argv[1]);
    return 2;
  }

  /* Both handles are rooted for the whole loop: int_to_string and map_set both
     allocate, so either can collect while the other's value is only in a local. */
  long long m = map_empty();
  long long key = 0;
  sprout_gc_push_i64_root(&m);
  sprout_gc_push_i64_root(&key);

  if (!distinct && !remove) key = int_to_string(7);
  for (long long i = 0; i < KEYS; i++) {
    if (distinct || remove) key = int_to_string(i);
    m = remove ? map_remove(m, key) : map_set(m, key, i);
  }

  sprout_gc_pop_roots(2);
  printf("intern-probe-%s\n", argv[1]);
  return 0;
}
