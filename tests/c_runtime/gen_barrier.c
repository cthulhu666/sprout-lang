/* The generational write barrier (docs/gc-generational-v0.md §14.4), driven directly.
 *
 * Between collections today's sweep leaves no object with the colour bit set, so the barrier is
 * inert in a real run. These cases set the bit by hand to stand in for "old" and check the
 * barrier's contract: a clear colour bit records nothing; a set colour bit sets REMEMBERED and
 * records the object once; each C store site calls it; age saturates without touching bit 13.
 *
 * The runtime is included, not linked, so the case reaches the static SproutGenState (g_gen) and
 * the header helpers. No allocation here is rooted: the cases allocate a handful of objects, far
 * below the collection threshold, and every colour bit set by hand is cleared before exit. */
#include <stdio.h>

#include "../../runtime/sprout_runtime.c"
#include "runtime_case.h"

static uint64_t hdr_of(long long word) {
  uint64_t h;
  memcpy(&h, (char*)(uintptr_t)word - 8, 8);
  return h;
}
static void hdr_put(long long word, uint64_t h) { memcpy((char*)(uintptr_t)word - 8, &h, 8); }
static void make_old(long long word) { hdr_put(word, hdr_of(word) | SPROUT_GC_COLOR_BIT); }
static void make_young(long long word) {
  hdr_put(word, hdr_of(word) & ~(SPROUT_GC_COLOR_BIT | SPROUT_GC_REMEMBERED_BIT));
}
static int is_remembered(long long word) { return (hdr_of(word) & SPROUT_GC_REMEMBERED_BIT) != 0; }
static void reset_set(void) { g_gen.len = 0; }

/* Colour clear: nothing is recorded, by the extern entry or by any store site. */
static void young_records_nothing(void) {
  long long r = ref_new(0);
  long long v = vec_make_filled(2, 0);
  reset_set();
  sprout_gc_write_barrier((void*)(uintptr_t)r);
  ref_write(r, 5);
  vector_mutset(v, 0, 5);
  vector_push(v, 5);
  check(g_gen.len == 0, "a young object is never recorded");
  check(!is_remembered(r) && !is_remembered(v), "a young object never gains REMEMBERED");
}

/* Colour set: REMEMBERED is set and the object is recorded exactly once. */
static void old_is_recorded_once(void) {
  long long r = ref_new(0);
  make_old(r);
  reset_set();
  sprout_gc_write_barrier((void*)(uintptr_t)r);
  check(is_remembered(r), "an old object gains REMEMBERED");
  check(g_gen.len == 1 && g_gen.remembered[0] == (void*)(uintptr_t)r, "an old object is recorded");
  check((hdr_of(r) & SPROUT_GC_COLOR_BIT) != 0, "the barrier leaves the colour bit alone");
  sprout_gc_write_barrier((void*)(uintptr_t)r);
  sprout_gc_write_barrier((void*)(uintptr_t)r);
  check(g_gen.len == 1, "a remembered object is not recorded again");
  make_young(r);
}

/* Each C store site records its target, and only an old one. */
static void sites_record_their_target(void) {
  long long r = ref_new(0);
  make_old(r);
  reset_set();
  ref_write(r, 9);
  check(g_gen.len == 1 && g_gen.remembered[0] == (void*)(uintptr_t)r, "ref_write records the Ref");
  make_young(r);

  long long v = vec_make_filled(2, 0);
  make_old(v);
  reset_set();
  vector_mutset(v, 1, 9);
  check(g_gen.len == 1 && g_gen.remembered[0] == (void*)(uintptr_t)v, "vector_mutset records the vector");
  make_young(v);

  /* The push grows out of the inline buffer; the recorded object is the VectorVal, which does not
   * move, and the barrier runs before the growth. */
  long long p = vec_make_filled(2, 0);
  make_old(p);
  reset_set();
  vector_push(p, 9);
  check(g_gen.len == 1 && g_gen.remembered[0] == (void*)(uintptr_t)p, "vector_push records the vector");
  check(((VectorVal*)(uintptr_t)p)->len == 3, "vector_push still pushes");
  check(is_remembered(p), "REMEMBERED survives the vector_push growth edit");
  make_young(p);
}

/* The set grows past its first allocation without losing entries. */
static void set_grows(void) {
  enum { N = 1000 };  /* past the set's first capacity, under the 4096-object collection threshold */
  reset_set();
  long long objs[N];
  for (int i = 0; i < N; i++) {
    objs[i] = ref_new(0);
    make_old(objs[i]);
    sprout_gc_write_barrier((void*)(uintptr_t)objs[i]);
  }
  check(g_gen.len == N, "every distinct old object is recorded");
  int ok = 1;
  for (int i = 0; i < N; i++) ok &= g_gen.remembered[i] == (void*)(uintptr_t)objs[i];
  check(ok, "the set keeps its entries across growth");
  for (int i = 0; i < N; i++) make_young(objs[i]);
}

/* Age saturates at 15, and neither the bump nor the saturation touches bit 13 or the other fields. */
static void age_saturates(void) {
  _Static_assert(SPROUT_GC_AGE_MAX == 15, "age is 4 bits");
  _Static_assert(SPROUT_GC_REMEMBERED_BIT == ((uint64_t)1 << 13), "REMEMBERED is bit 13");
  _Static_assert((SPROUT_GC_AGE_MASK & SPROUT_GC_REMEMBERED_BIT) == 0, "age and REMEMBERED are disjoint");
  _Static_assert((SPROUT_GC_AGE_MASK & SPROUT_GC_COLOR_BIT) == 0, "age and colour are disjoint");

  uint64_t h = sprout_hdr_make(SPROUT_HEAP_OBJ, 0x1234);
  uint64_t base = h;
  for (int i = 0; i < 40; i++) h = sprout_hdr_age_bump(h);
  check(sprout_hdr_age(h) == 15, "age saturates at 15");
  check((h & ~SPROUT_GC_AGE_MASK) == (base & ~SPROUT_GC_AGE_MASK), "saturation changes only the age bits");

  h = sprout_hdr_make(SPROUT_HEAP_OBJ, 0x1234) | SPROUT_GC_REMEMBERED_BIT | SPROUT_GC_COLOR_BIT;
  uint64_t before = h;
  for (int i = 0; i < 40; i++) h = sprout_hdr_age_bump(h);
  check(sprout_hdr_age(h) == 15, "age saturates with REMEMBERED and colour set");
  check((h & ~SPROUT_GC_AGE_MASK) == (before & ~SPROUT_GC_AGE_MASK),
        "age bumps leave REMEMBERED, colour, kind and aux alone");
}

int main(void) {
  setenv("SPROUT_GC_LINEAGE", "0", 1);
  young_records_nothing();
  old_is_recorded_once();
  sites_record_their_target();
  set_grows();
  age_saturates();
  if (g_failed) return 1;
  printf("gen-barrier\n");
  return 0;
}
