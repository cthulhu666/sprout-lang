/* Promotion age, the remembered rule and the nursery rule (docs/gc-stage2-v0.md §5.1, §5.2, §6),
 * driven directly with minor collections on and SPROUT_GC_GENCHECK=1, which adds the young-bitmap,
 * count, premise-3 and live-prediction checks after every sweep.
 *
 * Collection is explicit: the threshold is zeroed after each one (the trigger's result is kept in
 * g_thr and g_heap first), so only `collect` runs the collector.  With SPROUT_GC_PROMOTE_AGE=K
 * the first collection is a major, which keeps colour; a survivor stays young until its age
 * before a sweep is at least K-1.
 *
 *   age_seq        — a rooted Ref is young after the first K-1 collections and old after the Kth:
 *                    colour, youngmap bit, region counts.  Run at K = 2 and 3.
 *   age_large      — the same for a large object.
 *   major_age      — a major's colour-keeping sweep promotes only at age >= K-1.
 *   remember       — a store into a young Ref (no barrier), whose parent is then promoted while
 *                    the child stays young: the parent is remembered and the child survives the
 *                    minor that promotes it.  The crash case without the rule.
 *   set_before_major — when the next collection is a major the set is empty and no header
 *                    carries bit 13, and the child is still alive after that major.
 *   major_input_*  — the 2x rule reads the heap count, or the old count.
 *   nursery_*      — the next collection runs heap + max(base, baseline x 2) allocations on; a
 *                    pinned threshold turns the rule off; the footprint floor stays before a major.
 *   bad_*          — each aborts with a message the caller checks.
 *
 * `k_cases` lists every case with its environment; main sets it before sprout_set_argv. */
#include <stdio.h>
#include <stdlib.h>

#include "../../runtime/sprout_runtime.c"
#include "runtime_case.h"

static void* g_chain;     /* permanent root: head of a chain of Refs */
static long long g_tail;  /* last Ref of the chain */
static void* g_keep;      /* permanent root: one rooted object */
static void* g_keep2;
static long long g_thr;   /* the threshold the last collection chose */
static long long g_heap;  /* the heap count after it */
static int g_k = 1;       /* the case's promotion age */

static void collect(void) {
  sprout_gc_collect_with_reason("threshold");
  g_thr = g_gc_threshold;
  g_heap = g_managed_heap_count;
  g_gc_threshold = 0;  /* the adaptive rule re-bases it; only `collect` collects here */
}

static void register_roots(void) {
  register_root_slot(&g_chain, SPROUT_ROOT_PTR, 0);
  register_root_slot(&g_keep, SPROUT_ROOT_PTR, 0);
  register_root_slot(&g_keep2, SPROUT_ROOT_PTR, 0);
}

/* Extend the rooted chain by n Refs. */
static void extend_chain(long long n) {
  for (long long i = 0; i < n; i++) {
    long long x = ref_new(0);
    if (g_chain == NULL) g_chain = (void*)(uintptr_t)x;
    else ref_write(g_tail, x);
    g_tail = x;
  }
}

static void garbage_refs(long long m) {
  for (long long i = 0; i < m; i++) ref_new(i);
}

static long long chain_length(void) {
  long long n = 0, cur = (long long)(uintptr_t)g_chain;
  while (cur != 0 && sprout_heap_lookup((void*)(uintptr_t)cur) != NULL) { n++; cur = ref_read(cur); }
  return n;
}

static long long make_str(size_t len) {
  SproutStr s = sprout_str_alloc(len, "promote_age: out of memory");
  memset(s.hdr_p, 'a', len);
  s.hdr_p[len] = '\0';
  return sprout_ptr_word(s.hdr_p);
}

static int young_bit_set(long long w) {
  SproutRegion* r = region_find((void*)(uintptr_t)w);
  if (r->is_large) return r->young_count == 1;
  return youngmap_test(r, (size_t)((char*)(uintptr_t)w - 8 - r->base) / SPROUT_SLOT_GRAN);
}

static long long young_count_of(long long w) { return region_find((void*)(uintptr_t)w)->young_count; }
static long long old_count_of(long long w)   { return region_find((void*)(uintptr_t)w)->old_count; }

/* Survivors that are young after collection n are not old; after collection K they are. */
static int age_seq(void) {
  register_roots();
  long long x = ref_new(7);
  g_keep = (void*)(uintptr_t)x;
  for (int n = 1; n <= g_k; n++) {
    collect();
    char what[96];
    if (n < g_k) {
      snprintf(what, sizeof what, "after collection %d of K=%d the survivor is young", n, g_k);
      check(!is_old(x), what);
      snprintf(what, sizeof what, "after collection %d of K=%d its youngmap bit is set", n, g_k);
      check(young_bit_set(x), what);
      snprintf(what, sizeof what, "after collection %d of K=%d its region counts it young", n, g_k);
      check(young_count_of(x) >= 1, what);
    } else {
      snprintf(what, sizeof what, "after collection %d of K=%d the survivor is old", n, g_k);
      check(is_old(x), what);
      snprintf(what, sizeof what, "after collection %d of K=%d its youngmap bit is clear", n, g_k);
      check(!young_bit_set(x), what);
      snprintf(what, sizeof what, "after collection %d of K=%d its region counts it old", n, g_k);
      check(old_count_of(x) >= 1, what);
    }
  }
  check(ref_read(x) == 7, "the survivor is intact");
  check(g_gen.n_major == 1 && g_gen.n_minor == g_k - 1, "one major, then minors");
  return g_failed;
}

static int age_large(void) {
  register_roots();
  long long s = make_str(10000);
  g_keep = (void*)(uintptr_t)s;
  collect();
  check(!is_old(s), "a large survivor is young after the first collection (K=2)");
  check(young_count_of(s) == 1 && old_count_of(s) == 0, "its region counts it young");
  collect();
  check(is_old(s), "and old after the second");
  check(young_count_of(s) == 0 && old_count_of(s) == 1, "its region counts it old");
  g_keep = NULL;
  return g_failed;
}

/* MAJOR_EVERY=1: major (keeps colour), minor before a major (clears it), major (keeps it). */
static int major_age(void) {
  register_roots();
  long long x = ref_new(5);
  g_keep = (void*)(uintptr_t)x;
  collect();
  check(g_gen.n_major == 1 && !is_old(x), "the first major leaves the survivor young");
  collect();
  check(g_gen.n_minor == 1 && !is_old(x), "the minor before a major leaves nothing old");
  collect();
  check(g_gen.n_major == 2, "the third collection is a major");
  check(is_old(x), "the second major promotes it: its age before that sweep is K-1");
  check(!young_bit_set(x) && ref_read(x) == 5, "and it is intact");
  return g_failed;
}

/* The crash case: P young (no barrier on a store into it), then promoted while C stays young. */
static int remember(void) {
  register_roots();
  long long p = ref_new(0);
  g_keep = (void*)(uintptr_t)p;
  collect();                               /* the major: P survives, young (age 1) */
  check(!is_old(p), "P is young after the first collection");
  long long c = ref_new(42);
  ref_write(p, c);                         /* P is young: no barrier, no record */
  check(!is_remembered(p), "a store into a young Ref records nothing");
  collect();                               /* minor: P promoted, C (age 0) stays young */
  check(is_old(p) && !is_old(c), "P is promoted while its child stays young");
  check(is_remembered(p) && g_gen.remembered.len == 1, "the promoted parent of a young child is remembered");
  collect();                               /* minor: P traced through the set, C marked and promoted */
  check(g_gen.n_minor == 2, "two minors ran");
  check(sprout_heap_lookup((void*)(uintptr_t)c) != NULL, "the child survived the minor after its parent's promotion");
  check(ref_read(p) == c && ref_read(c) == 42, "the edge and the child are intact");
  check(is_old(c) && !is_remembered(p) && g_gen.remembered.len == 0,
        "the child is promoted, and the parent is dropped from the set");
  return g_failed;
}

static int set_before_major(void) {
  register_roots();
  long long p = ref_new(0);
  g_keep = (void*)(uintptr_t)p;
  collect();
  check(!is_old(p), "P is young after the first collection");
  long long c = ref_new(42);
  ref_write(p, c);
  collect();                               /* minors_since_major reaches MAJOR_EVERY: a major is next */
  check(g_gen.colorless == 1, "a major is next");
  check(g_gen.remembered.len == 0, "the set is empty before the major's sweep");
  check(!is_remembered(p), "no header carries bit 13 before the major");
  check(!is_old(p), "the colour-clearing sweep left nothing old");
  collect();                               /* the major */
  check(g_gen.n_major == 2, "the third collection is a major");
  check(sprout_heap_lookup((void*)(uintptr_t)c) != NULL && ref_read(p) == c && ref_read(c) == 42,
        "the child is alive after the major");
  collect();
  collect();
  check(sprout_heap_lookup((void*)(uintptr_t)c) != NULL && ref_read(c) == 42, "and after the next ones");
  return g_failed;
}

/* K=2: N=2000 survivors of the first major, then 5000 new ones.  The heap count says "major
 * next" (7000 > 2 x 2000); the old count does not (2000 promoted, the new ones stay young). */
static int major_input(int want_colorless) {
  register_roots();
  extend_chain(2000);
  collect();
  extend_chain(5000);
  collect();
  check(g_gen.n_minor == 1, "the second collection is a minor");
  check(g_gen.colorless == want_colorless, want_colorless ? "the heap count triggers a major"
                                                          : "the old count does not trigger a major");
  collect();
  check(chain_length() == 7000, "the chain is intact");
  return g_failed;
}
static int major_input_heap(void) { return major_input(1); }
static int major_input_old(void)  { return major_input(0); }

static long long imax(long long a, long long b) { return a > b ? a : b; }

/* N=1, K=1.  3000 survive the major; a minor that keeps 1500 more picks heap + 2 x baseline. */
static int nursery_sizing(void) {
  register_roots();
  extend_chain(3000);
  collect();
  check(g_gen.baseline == 3000, "the baseline is the live count after the major");
  extend_chain(1500);
  garbage_refs(500);
  collect();
  check(g_gen.n_minor == 1, "the second collection is a minor");
  check(g_heap == 4500, "the heap holds the old and the young survivors");
  check(g_thr == g_heap + imax(g_gc_threshold_base, 3000 * 2),
        "the next collection runs heap + baseline x (factor - 1) allocations on");
  /* A small baseline: the floor is g_gc_threshold_base. */
  g_chain = NULL;
  sprout_gc_collect_with_reason("atexit");  /* a major: the chain dies */
  extend_chain(100);
  sprout_gc_collect_with_reason("atexit");  /* a major: baseline 100 */
  g_gc_threshold = 0;
  garbage_refs(10);
  collect();
  check(g_gen.baseline == 100, "the baseline follows the last major");
  check(g_thr == g_heap + g_gc_threshold_base, "a small baseline sizes the nursery at the threshold base");
  return g_failed;
}

/* A pinned threshold (SPROUT_GC_THRESHOLD) turns the rule off: heap x factor, as before. */
static int nursery_pinned(void) {
  register_roots();
  extend_chain(3000);
  collect();
  extend_chain(1500);
  collect();
  check(g_gen.n_minor == 1, "the second collection is a minor");
  check(g_thr == imax(g_gc_threshold_base, (long long)((double)g_heap * g_gc_adapt_factor)),
        "a pinned threshold keeps heap x factor");
  return g_failed;
}

/* The collection before a major keeps the footprint floor: master's rule, not the nursery's. */
static int nursery_floor_before_major(void) {
  register_roots();
  extend_chain(3000);
  collect();
  extend_chain(1500);
  collect();                               /* minors_since_major reaches MAJOR_EVERY: a major is next */
  check(g_gen.colorless == 1, "a major is next");
  long long floor_ = g_heap + (g_heap + g_gc_freelist_len) / g_gc_footprint_k;
  long long want = imax(imax(g_gc_threshold_base, (long long)((double)g_heap * g_gc_adapt_factor)), floor_);
  check(g_thr == want, "the collection before a major sizes the next one with the footprint floor");
  collect();                               /* the major */
  check(g_thr == imax(imax(g_gc_threshold_base, (long long)((double)g_heap * g_gc_adapt_factor)),
                      g_heap + (g_heap + g_gc_freelist_len) / g_gc_footprint_k),
        "and so does the major");
  return g_failed;
}

/* An old object whose store skipped the barrier, with a young child that stays young: premise 3. */
static int bad_unremembered_edge(void) {
  register_roots();
  long long a = ref_new(0);
  g_keep = (void*)(uintptr_t)a;
  collect();
  collect();                               /* K=2: A is old after the second collection */
  check(is_old(a), "A is old");
  long long y = ref_new(1);
  g_keep2 = (void*)(uintptr_t)y;           /* Y is also a root, so the minor marks it */
  ((RefVal*)(uintptr_t)a)->value = y;      /* the store skips sprout_gc_write_barrier */
  collect();                               /* Y stays young; A is old, unremembered: the check aborts */
  fprintf(stderr, "bad_unremembered_edge: no abort\n");
  return 1;
}

/* Bit 13 left on a header while the next collection is a major. */
static int bad_major_bit13(void) {
  register_roots();
  long long p = ref_new(0);
  g_keep = (void*)(uintptr_t)p;
  collect();
  uint64_t h = sprout_hdr_of((void*)(uintptr_t)p) | SPROUT_GC_REMEMBERED_BIT;
  memcpy((char*)(uintptr_t)p - 8, &h, 8);
  collect();                               /* a major is next: the check aborts */
  fprintf(stderr, "bad_major_bit13: no abort\n");
  return 1;
}

/* A non-empty set while the next collection is a major. */
static int bad_major_set(void) {
  register_roots();
  long long p = ref_new(0);
  g_keep = (void*)(uintptr_t)p;
  collect();
  collect();                               /* a major is next */
  check(g_gen.colorless == 1, "a major is next");
  sprout_wl_push(&g_gen.remembered, (void*)(uintptr_t)p, "promote_age: out of memory");
  sprout_gc_check_sweep(0, 0);             /* what the collector runs after the sweep */
  fprintf(stderr, "bad_major_set: no abort\n");
  return 1;
}

typedef struct {
  const char* name;
  int (*fn)(void);
  int k;               /* SPROUT_GC_PROMOTE_AGE */
  int nursery;         /* SPROUT_GC_NURSERY */
  const char* input;   /* SPROUT_GC_MAJOR_INPUT, or NULL */
  const char* every;   /* SPROUT_GC_MAJOR_EVERY */
  const char* thr;     /* SPROUT_GC_THRESHOLD, or NULL */
} AgeCase;

static const AgeCase k_cases[] = {
  { "age2",                      age_seq,                     2, 0, NULL,   "1000", NULL },
  { "age3",                      age_seq,                     3, 0, NULL,   "1000", NULL },
  { "age_large",                 age_large,                   2, 0, NULL,   "1000", NULL },
  { "major_age",                 major_age,                   3, 0, NULL,   "1",    NULL },
  { "remember",                  remember,                    2, 0, NULL,   "1000", NULL },
  { "set_before_major",          set_before_major,            2, 0, NULL,   "1",    NULL },
  { "major_input_heap",          major_input_heap,            2, 0, "heap", "1000", NULL },
  { "major_input_old",           major_input_old,             2, 0, "old",  "1000", NULL },
  { "nursery_sizing",            nursery_sizing,              1, 1, NULL,   "1000", NULL },
  { "nursery_pinned",            nursery_pinned,              1, 1, NULL,   "1000", "5000" },
  { "nursery_floor_before_major", nursery_floor_before_major, 1, 1, NULL,   "1",    NULL },
  { "bad_unremembered_edge",     bad_unremembered_edge,       2, 0, NULL,   "1000", NULL },
  { "bad_major_bit13",           bad_major_bit13,             2, 0, NULL,   "1",    NULL },
  { "bad_major_set",             bad_major_set,               2, 0, NULL,   "1",    NULL },
};

int main(int argc, char** argv) {
  if (argc < 2) return 2;
  const AgeCase* c = NULL;
  for (size_t i = 0; i < sizeof k_cases / sizeof k_cases[0]; i++)
    if (strcmp(argv[1], k_cases[i].name) == 0) c = &k_cases[i];
  /* bad_env <var> <value>: a bad switch value aborts at startup, before anything runs. */
  int bad_env = strcmp(argv[1], "bad_env") == 0 && argc == 4;
  if (!c && !bad_env) return 2;
  setenv("SPROUT_GC_MINOR", "1", 1);
  setenv("SPROUT_GC_GENCHECK", "1", 1);
  setenv("SPROUT_GC_HDRCHECK", "1", 1);
  setenv("SPROUT_GC_LINEAGE", "0", 1);
  unsetenv("SPROUT_GC_STRESS");
  unsetenv("SPROUT_GC_AGEPROF");
  unsetenv("SPROUT_GC_THRESHOLD");
  if (bad_env) {
    setenv(argv[2], argv[3], 1);
    sprout_set_argv(argc, argv);
    fprintf(stderr, "bad_env: no abort\n");
    return 1;
  }
  char buf[16];
  snprintf(buf, sizeof buf, "%d", c->k);
  setenv("SPROUT_GC_PROMOTE_AGE", buf, 1);
  setenv("SPROUT_GC_NURSERY", c->nursery ? "1" : "0", 1);
  if (c->input) setenv("SPROUT_GC_MAJOR_INPUT", c->input, 1);
  else unsetenv("SPROUT_GC_MAJOR_INPUT");
  setenv("SPROUT_GC_MAJOR_EVERY", c->every, 1);
  if (c->thr) setenv("SPROUT_GC_THRESHOLD", c->thr, 1);
  g_k = c->k;
  sprout_set_argv(argc, argv);  /* reads the switches, as a compiled program's main does */
  g_gc_threshold = 0;
  if (c->fn() || g_failed) return 1;
  printf("promote-age-%s\n", c->name);
  return 0;
}
