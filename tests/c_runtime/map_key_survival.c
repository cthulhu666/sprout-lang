/* A map key is a GC child of its node: a key reachable only from the map must
 * survive collection, under SPROUT_GC_STRESS=1 where every allocation collects.
 *
 * Keys are built at runtime (int_to_string), so each is a heap string, and the
 * only reference left to it after map_set is the one inside the map. Churn then
 * collects repeatedly, and every key is read back by position and by lookup.
 *
 *   insert — keys survive the inserts themselves, which allocate path copies
 *            (and rebalance) while a new key is held only by the caller.
 *   churn  — keys survive unrelated allocation.
 *   remove — keys of the nodes that stay survive a remove, including the
 *            two-child case that copies the successor's key into a new node.
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

long long sprout_register_ctor(long long tag, const char* name, long long arity,
                               const char* field_kinds);
long long map_empty(void);
long long map_set(long long map_h, long long key_val, long long value);
long long map_remove(long long map_h, long long key_val);
long long map_get(long long map_h, long long key_val);
long long map_size(long long map_h);
long long map_nth_key(long long map_h, long long index);
long long int_to_string(long long value);
long long sprout_field(long long h, long long idx);
long long sprout_gc_push_i64_root(void* slot);
long long sprout_gc_pop_roots(long long count);

#define KEYS 600
#define CHURN 400

static int fail(const char* what, long long i) {
  fprintf(stderr, "map-key-survival: %s (i=%lld)\n", what, i);
  return 1;
}

/* Every key 0..KEYS-1 for which keep(i) holds is present, in a map of `want` keys. */
static int check_map(long long m, int (*keep)(long long), long long want) {
  static char seen[KEYS];
  memset(seen, 0, sizeof(seen));
  if (map_size(m) != want) return fail("size", map_size(m));
  for (long long i = 0; i < want; i++) {
    long long just = map_nth_key(m, i);
    const char* key = (const char*)(uintptr_t)sprout_field(just, 0);
    char* end = NULL;
    long long n = strtoll(key, &end, 10);
    char buf[32];
    snprintf(buf, sizeof(buf), "%lld", n);
    if (*end != '\0' || strcmp(buf, key) != 0 || n < 0 || n >= KEYS) return fail("key bytes", i);
    if (seen[n]) return fail("duplicate key", n);
    seen[n] = 1;
  }
  for (long long i = 0; i < KEYS; i++)
    if ((seen[i] != 0) != (keep(i) != 0)) return fail("wrong key set", i);
  return 0;
}

static int keep_all(long long i) { (void)i; return 1; }
static int keep_odd(long long i) { return (i & 1) == 1; }

static void churn(void) {
  for (int i = 0; i < CHURN; i++) (void)int_to_string(i);
}

int main(void) {
  setenv("SPROUT_GC_STRESS", "1", 1);
  sprout_register_ctor(7, "Nothing", 0, "");
  sprout_register_ctor(8, "Just", 1, "p");
  long long m = map_empty();
  long long key = 0;
  sprout_gc_push_i64_root(&m);
  sprout_gc_push_i64_root(&key);

  for (long long i = 0; i < KEYS; i++) {
    key = int_to_string(i);
    m = map_set(m, key, i);
  }
  key = 0;  /* the map holds the only reference to every key now */
  churn();
  if (check_map(m, keep_all, KEYS)) return 1;

  for (long long i = 0; i < KEYS; i += 2) {
    key = int_to_string(i);
    m = map_remove(m, key);
  }
  key = 0;
  churn();
  if (check_map(m, keep_odd, KEYS / 2)) return 1;

  sprout_gc_pop_roots(2);
  printf("map-key-survival\n");
  return 0;
}
