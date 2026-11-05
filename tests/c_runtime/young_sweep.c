/* The young-only sweep (docs/gc-stage2-v0.md §5.3, §6), driven directly with minor collections
 * on and SPROUT_GC_GENCHECK=1, which adds the young-bitmap, free-count and region checks after
 * every sweep.
 *
 * An object is made old the real way: rooted across the first collection (a major). Collection
 * is explicit: the threshold is zeroed after each one, so only `collect` runs the collector.
 *
 *   walked_young   — a minor over a large old heap visits the young slots, not the old ones, and
 *                    a second one does too when the young slots are reused from the free lists.
 *   free_count     — g_gc_free_after_sweep equals a full walk's count after every young-only
 *                    minor and after allocations that reuse listed slots and split inline tails.
 *   no_double_list — a slot freed by one minor and not reused is on its list once after the next.
 *   region_release — young garbage that fills fresh regions leaves none of them behind.
 *   large_young    — a young large object is freed (or promoted, when rooted) by a minor.
 *   lineage        — the same under SPROUT_GC_LINEAGE=1, where a dead OBJ is poisoned, not listed.
 *   bad_young_bit, bad_young_bit_free, bad_free_count — each corrupts the bookkeeping the
 *                    checks read and must abort with the GENCHECK message (the caller checks).
 *
 * `k_cases` lists every case with its environment; main sets it before sprout_set_argv. */
#include <stdio.h>
#include <stdlib.h>

#include "../../runtime/sprout_runtime.c"
#include "runtime_case.h"

static void* g_chain;     /* permanent root: head of the old chain of Refs */
static long long g_tail;  /* last Ref of the chain (reachable from g_chain) */
static void* g_keep;      /* permanent root: one rooted object */

static void collect(void) {
  sprout_gc_collect_with_reason("threshold");
  g_gc_threshold = 0;  /* the adaptive rule re-bases it; only `collect` collects here */
}

/* An old chain of n Refs: rooted across the first collection, a major. */
static void setup_old(long long n) {
  register_root_slot(&g_chain, SPROUT_ROOT_PTR, 0);
  register_root_slot(&g_keep, SPROUT_ROOT_PTR, 0);
  long long head = ref_new(0);
  g_chain = (void*)(uintptr_t)head;
  g_tail = head;
  for (long long i = 1; i < n; i++) {
    long long x = ref_new(0);
    ref_write(g_tail, x);
    g_tail = x;
  }
  collect();
  check(g_gen.n_major == 1 && g_gen.n_minor == 0, "the first collection is a major");
  check(is_old(head) && is_old(g_tail), "the chain is old");
}

static long long chain_length(void) {
  long long n = 0, cur = (long long)(uintptr_t)g_chain;
  while (cur != 0 && sprout_heap_lookup((void*)(uintptr_t)cur) != NULL) { n++; cur = ref_read(cur); }
  return n;
}

static void garbage_refs(long long m) {
  for (long long i = 0; i < m; i++) ref_new(i);
}

/* A CSTR of `len` bytes, filled so HDRCHECK's strlen agrees with the header. */
static long long make_str(size_t len) {
  SproutStr s = sprout_str_alloc(len, "young_sweep: out of memory");
  memset(s.hdr_p, 'a', len);
  s.hdr_p[len] = '\0';
  return sprout_ptr_word(s.hdr_p);
}

/* Strings of mixed classes, and a vector pushed past its inline area (a FREE tail split off). */
static void churn(int round) {
  for (int i = 0; i < 600; i++) make_str(1 + ((size_t)i * 7 + (size_t)round) % 200);
  long long v = vec_make_filled(100, 0);
  vector_push(v, 1);
}

/* The size classes the free lists should hold: every FREE slot of every ordinary region. */
static long long count_free_slots(void) {
  long long n = 0;
  for (size_t ri = 0; ri < g_region_count; ri++) {
    SproutRegion* r = &g_regions[ri];
    if (r->is_large) continue;
    size_t off = 0;
    while (off < r->bump) {
      uint64_t h; memcpy(&h, r->base + off, 8);
      size_t ss = sprout_slot_step(h);
      size_t cls = ss / SPROUT_SLOT_GRAN;
      if ((h & 0xFF) == SPROUT_HEAP_FREE && cls >= 1 && cls < SPROUT_FREELIST_CLASSES) n++;
      off += ss;
    }
  }
  return n;
}

static int cmp_ptr(const void* a, const void* b) {
  uintptr_t x = (uintptr_t)*(void* const*)a, y = (uintptr_t)*(void* const*)b;
  return x < y ? -1 : x > y;
}

/* Entries on the free lists, and how many appear twice. Every entry must be in a live region. */
static long long list_entries(long long* dups) {
  long long bound = count_free_slots() + 16;
  void** v = (void**)malloc((size_t)bound * sizeof(void*));
  long long n = 0;
  for (size_t cls = 1; cls < SPROUT_FREELIST_CLASSES; cls++) {
    for (void* p = g_freelist[cls]; p != NULL;) {
      if (region_find(p) == NULL) { check(0, "a free-list entry is in no live region"); free(v); *dups = 0; return n; }
      if (n >= bound) { check(0, "a free list is longer than the FREE slots (a cycle?)"); free(v); *dups = 0; return n; }
      v[n++] = p;
      void* next; memcpy(&next, p, sizeof next);
      p = next;
    }
  }
  qsort(v, (size_t)n, sizeof(void*), cmp_ptr);
  *dups = 0;
  for (long long i = 1; i < n; i++) if (v[i] == v[i - 1]) (*dups)++;
  free(v);
  return n;
}

static void check_free_lists(const char* when) {
  long long dups = 0;
  long long entries = list_entries(&dups);
  long long walked = count_free_slots();
  if (g_gc_free_after_sweep != walked || entries != walked || dups != 0) {
    g_failed = 1;
    fprintf(stderr, "FAIL: free lists %s: g_gc_free_after_sweep=%lld entries=%lld full_walk=%lld dups=%lld\n",
            when, g_gc_free_after_sweep, entries, walked, dups);
  }
}

static void young_bit(long long w, int on) {
  SproutRegion* r = region_find((void*)(uintptr_t)w);
  size_t g = (size_t)((char*)(uintptr_t)w - 8 - r->base) / SPROUT_SLOT_GRAN;
  uint64_t* m = (uint64_t*)r->youngmap;
  if (on) m[g / 64] |= (uint64_t)1 << (g % 64);
  else    m[g / 64] &= ~((uint64_t)1 << (g % 64));
}

static int walked_young(void) {
  const long long N = 20000, M = 2000;
  setup_old(N);
  garbage_refs(M);
  long long w0 = g_debug_gc_walked;
  collect();
  long long d = g_debug_gc_walked - w0;
  check(g_gen.n_minor == 1, "the second collection is a minor");
  check(d <= M + 64, "a minor over a large old heap walks only the young slots");
  check_free_lists("after the first minor");
  garbage_refs(M);  /* reuses the slots the minor just listed */
  w0 = g_debug_gc_walked;
  collect();
  d = g_debug_gc_walked - w0;
  check(g_gen.n_minor == 2, "the third collection is a minor");
  check(d <= M + 64, "a minor that reuses listed slots walks only the young slots");
  check_free_lists("after the second minor");
  check(chain_length() == N, "the old chain is intact");
  return g_failed;
}

static int free_count(void) {
  setup_old(5000);
  for (int round = 0; round < 15; round++) {
    churn(round);
    check_free_lists("after allocations that reuse listed slots");
    for (int i = 0; i < 10; i++) {  /* a few young survivors, promoted by the minor */
      long long x = ref_new(0);
      ref_write(g_tail, x);
      g_tail = x;
    }
    collect();
    check_free_lists("after a minor");
  }
  check(g_gen.n_minor == 15 && g_gen.n_major == 1, "fifteen minors follow the major");
  check(chain_length() == 5000 + 150, "the chain, with its survivors, is intact");
  return g_failed;
}

static int no_double_list(void) {
  setup_old(3000);
  garbage_refs(1000);
  for (int i = 0; i < 300; i++) make_str(1 + (size_t)i % 90);
  collect();
  check_free_lists("after the minor that freed them");
  collect();  /* nothing young, nothing reused */
  check(g_gen.n_minor == 2, "two minors ran");
  check_free_lists("after a minor that visits nothing");
  garbage_refs(50);
  collect();
  check_free_lists("after a minor that reused some");
  return g_failed;
}

static int region_release(void) {
  setup_old(100);
  garbage_refs(200000);  /* ~3 MiB of 16-byte slots: fresh regions holding nothing old */
  check(g_region_count >= 4, "the garbage filled fresh regions");
  collect();
  check(g_gen.n_minor == 1, "the second collection is a minor");
  check(g_region_count == 1, "every region left with no live slot was released");
  for (size_t ri = 0; ri < g_region_count; ri++)
    if (!g_regions[ri].is_large)
      check(g_regions[ri].live_count > 0 || g_regions[ri].poison_count > 0, "no ordinary region is left empty");
  check_free_lists("after the release");
  check(chain_length() == 100, "the old chain is intact");
  garbage_refs(200000);
  collect();
  check(g_region_count == 1, "a second round releases them again");
  check_free_lists("after the second release");
  return g_failed;
}

static int large_young(void) {
  setup_old(5000);
  g_keep = (void*)(uintptr_t)make_str(10000);
  long long dead1 = make_str(10000), dead2 = make_str(20000);
  garbage_refs(500);
  size_t regions = g_region_count;
  long long w0 = g_debug_gc_walked;
  collect();
  long long d = g_debug_gc_walked - w0;
  check(g_gen.n_minor == 1, "the second collection is a minor");
  check(g_region_count == regions - 2, "the two dead young large objects were freed");
  check(sprout_heap_lookup((void*)(uintptr_t)dead1) == NULL, "a dead young large object is gone");
  check(sprout_heap_lookup((void*)(uintptr_t)dead2) == NULL, "another one too");
  check(sprout_heap_lookup(g_keep) != NULL && is_old((long long)(uintptr_t)g_keep),
        "a rooted young large object is promoted");
  check(d <= 500 + 3 + 64, "a minor walks the young slots and the young large objects only");
  collect();
  check(g_region_count == regions - 2, "the promoted one stays through a minor");
  g_keep = NULL;
  sprout_gc_collect_with_reason("atexit");
  check(g_region_count == regions - 3, "a major frees the old large object");
  check(chain_length() == 5000, "the old chain is intact");
  return g_failed;
}

static int lineage(void) {
  setup_old(3000);
  check(sprout_gc_lineage_on() == 1, "SPROUT_GC_LINEAGE=1 turns lineage on");
  for (int round = 0; round < 4; round++) {
    for (int i = 0; i < 300; i++) {
      long long o = sprout_alloc_obj(7, 1);  /* dead: poisoned, not listed */
      ((long long*)(uintptr_t)o)[0] = 0;
    }
    garbage_refs(500);                        /* dead: freed and listed */
    for (int i = 0; i < 100; i++) make_str(1 + (size_t)i % 60);
    long long w0 = g_debug_gc_walked;
    collect();
    long long d = g_debug_gc_walked - w0;
    check(d <= 300 + 500 + 100 + 64, "a minor under lineage walks only the young slots");
    check_free_lists("after a minor under lineage");
  }
  check(g_gen.n_minor == 4, "four minors ran");
  check(chain_length() == 3000, "the old chain is intact");
  return g_failed;
}

static int bad_young_bit(void) {
  setup_old(1000);
  long long y = ref_new(1);  /* garbage: the minor must free it */
  young_bit(y, 0);
  collect();                 /* the check must abort here */
  fprintf(stderr, "bad_young_bit: no abort\n");
  return 1;
}

static int bad_young_bit_free(void) {
  setup_old(1000);
  long long y = ref_new(1);
  garbage_refs(100);
  collect();                 /* y is freed and listed */
  young_bit(y, 1);           /* a stale bit on a FREE slot: the next minor would list it twice */
  collect();
  fprintf(stderr, "bad_young_bit_free: no abort\n");
  return 1;
}

static int bad_free_count(void) {
  setup_old(1000);
  garbage_refs(100);
  g_gc_free_after_sweep += 1;
  collect();                 /* the young-only minor keeps the count incrementally */
  fprintf(stderr, "bad_free_count: no abort\n");
  return 1;
}

typedef struct {
  const char* name;
  int (*fn)(void);
  int lineage;  /* SPROUT_GC_LINEAGE=1 */
} YoungCase;

static const YoungCase k_cases[] = {
  { "walked_young",       walked_young,       0 },
  { "free_count",         free_count,         0 },
  { "no_double_list",     no_double_list,     0 },
  { "region_release",     region_release,     0 },
  { "large_young",        large_young,        0 },
  { "lineage",            lineage,            1 },
  { "bad_young_bit",      bad_young_bit,      0 },
  { "bad_young_bit_free", bad_young_bit_free, 0 },
  { "bad_free_count",     bad_free_count,     0 },
};

int main(int argc, char** argv) {
  if (argc < 2) return 2;
  const YoungCase* c = NULL;
  for (size_t i = 0; i < sizeof k_cases / sizeof k_cases[0]; i++)
    if (strcmp(argv[1], k_cases[i].name) == 0) c = &k_cases[i];
  if (!c) return 2;
  setenv("SPROUT_GC_MINOR", "1", 1);
  setenv("SPROUT_GC_GENCHECK", "1", 1);
  setenv("SPROUT_GC_MAJOR_EVERY", "1000", 1);
  setenv("SPROUT_GC_HDRCHECK", "1", 1);
  setenv("SPROUT_GC_LINEAGE", c->lineage ? "1" : "0", 1);
  unsetenv("SPROUT_GC_STRESS");
  unsetenv("SPROUT_GC_AGEPROF");
  sprout_set_argv(argc, argv);  /* reads the switches, as a compiled program's main does */
  g_gc_threshold = 0;
  if (c->fn() || g_failed) return 1;
  printf("young-sweep-%s\n", c->name);
  return 0;
}
