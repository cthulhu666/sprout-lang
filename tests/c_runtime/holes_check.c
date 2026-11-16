/* GENCHECK under SPROUT_GC_HOLES=1 (docs/gc-stage2-v0.md §5.7, §6 premises 2, 3, 5-7, 10, 11, 13-16),
 * driven directly with minor collections on.  Collection is explicit: the threshold is zeroed
 * after each one, so only `collect` runs the collector.  An object is made old the real way,
 * rooted across g_gen.promote_age collections, the first a major.
 *
 *   positive       — an old heap, minors and majors, young survivors that stay young, vectors with
 *                    out-of-line buffers, bytes and builders (young and promoted), a large object
 *                    and inline-tail release; GENCHECK passes throughout.
 *   scribble       — every hole is filled with junk that looks like headers and part of it is
 *                    allocated into; no walk steps over a hole by header (premise 14).
 *   tail_retained  — an old vector in a region with no young object releases its inline tail;
 *                    the next minor must drop the tail from `occ`.
 *   *_verify, *_hdrcheck — the positive and scribble cases again with SPROUT_FL_VERIFY=1 or
 *                    SPROUT_GC_HDRCHECK=1: the verify and HDRCHECK walks hold on a real heap.
 *   bad_*          — each corrupts one map, count or list after a clean collection and runs the
 *                    check, which must abort with its own message (the caller checks it).
 *   bad_fl_*       — the same under SPROUT_FL_VERIFY=1 (premise 12): the cursors, the tail lists
 *                    and each allocation's extent.  bad_hdr_* — under SPROUT_GC_HDRCHECK=1: a
 *                    header whose step does not land on a `slotmap` start or the run's end.
 *
 * `k_cases` lists every case and the switch it sets; main sets the environment before
 * sprout_set_argv. */
#include <stdio.h>
#include <stdlib.h>

#include "../../runtime/sprout_runtime.c"
#include "runtime_case.h"

static void* g_chain;     /* permanent root: head of the old chain of Refs */
static long long g_tail;  /* last Ref of the chain */
static void* g_o[8];      /* permanent roots: the objects a case keeps alive */

static void collect(void) {
  sprout_gc_collect_with_reason("threshold");
  g_gc_threshold = 0;  /* the adaptive rule re-bases it; only `collect` collects here */
}

static void collect_major(void) {
  sprout_gc_collect_with_reason("atexit");
  g_gc_threshold = 0;
}

static void register_roots(void) {
  register_root_slot(&g_chain, SPROUT_ROOT_PTR, 0);
  for (size_t i = 0; i < sizeof g_o / sizeof g_o[0]; i++) register_root_slot(&g_o[i], SPROUT_ROOT_PTR, 0);
}

static long long W(void* p) { return (long long)(uintptr_t)p; }
static void* P(long long w) { return (void*)(uintptr_t)w; }

/* A chain of n Refs rooted at g_chain. */
static void extend_chain(long long n) {
  for (long long i = 0; i < n; i++) {
    long long x = ref_new(0);
    if (g_chain == NULL) g_chain = P(x);
    else ref_write(g_tail, x);
    g_tail = x;
  }
}

/* Strings of mixed classes, vectors with out-of-line buffers, bytes, builders and a large
 * object, none rooted. */
static void churn(int round) {
  for (int i = 0; i < 400; i++) make_str(1 + ((size_t)i * 7 + (size_t)round) % 200);
  for (int i = 0; i < 6; i++) {
    vec_make_filled(600 + i, 0);
    bytes_singleton(i);
    bytes_builder_append(bytes_builder_u16_be(i), bytes_builder_u16_be(i + 1));
  }
  long long v = vec_make_filled(100, 0);
  vector_push(v, 1);
  make_str(10000 + (size_t)round);
}

static int positive(void) {
  register_roots();
  g_o[0] = P(vec_make_filled(600, 0));                                      /* out-of-line, old */
  g_o[1] = P(vec_make_filled(100, 0));                                      /* inline, old, grows */
  g_o[2] = P(make_str(10000));                                              /* large, old */
  g_o[3] = P(bytes_singleton(7));                                           /* bytes, old */
  g_o[4] = P(bytes_builder_append(bytes_builder_u16_be(1), bytes_builder_u16_be(2)));  /* builder */
  extend_chain(2000);
  age_to_old(W(g_chain), collect);
  check(g_gen.n_major == 1, "the first collection is a major");
  check(is_old(W(g_o[0])) && is_old(W(g_o[1])) && is_old(W(g_o[3])) && is_old(W(g_o[4])),
        "the owners are old");
  for (int round = 0; round < 30; round++) {
    churn(round);
    for (int i = 0; i < 10; i++) {  /* young survivors, promoted by later minors */
      long long x = ref_new(0);
      ref_write(g_tail, x);
      g_tail = x;
    }
    g_o[5] = P(vec_make_filled(600 + round, 0));                              /* young, out of line */
    g_o[6] = P(bytes_builder_append(bytes_builder_u16_be(round), bytes_builder_u16_be(1)));
    g_o[7] = P(vec_make_filled(200, 0));                                      /* young, inline */
    if (round % 3 == 1) vector_push(W(g_o[7]), 1);                            /* releases a young tail */
    if (round % 4 == 1) vector_push(W(g_o[1]), round);                        /* old: tail, then growth */
    if (round % 7 == 6) collect_major(); else collect();
  }
  check(g_gen.n_minor > 15 && g_gen.n_major >= 4, "minors and majors ran");
  check(chain_length(g_chain) == 2000 + 300, "the chain, with its survivors, is intact");
  check(vector_length(W(g_o[0])) == 600 && vector_length(W(g_o[1])) > 100, "the vectors are intact");
  check(bytes_length(W(g_o[3])) == 1 && sprout_heap_lookup(g_o[2]) != NULL, "bytes and the large string are intact");
  return g_failed;
}

/* Fill every hole below the bump of every ordinary region with junk: 0xFF bytes, and at the
 * first granule of each run a header that is a plausible OBJ of 3 fields. */
static void scribble_holes(void) {
  const uint64_t obj = sprout_hdr_make(SPROUT_HEAP_OBJ, 3);
  size_t runs = 0;
  for (size_t ri = 0; ri < g_region_count; ri++) {
    SproutRegion* r = &g_regions[ri];
    if (r->is_large) continue;
    const size_t top = r->bump / SPROUT_SLOT_GRAN;
    for (size_t g = 0; g < top;) {
      if (bitmap_test(r->occ, g)) { g++; continue; }
      size_t e = g;
      while (e < top && !bitmap_test(r->occ, e)) e++;
      memset(r->base + g * SPROUT_SLOT_GRAN, 0xFF, (e - g) * SPROUT_SLOT_GRAN);
      memcpy(r->base + g * SPROUT_SLOT_GRAN, &obj, sizeof obj);
      runs++;
      g = e;
    }
  }
  check(runs > 0, "the collection left holes to scribble on");
}

static int scribble(void) {
  register_roots();
  for (int i = 0; i < 3000; i++) {  /* survivors interleaved with garbage of other sizes */
    extend_chain(1);
    make_str(1 + (size_t)i % 150);
    ref_new(i);
  }
  age_to_old(W(g_chain), collect);
  for (int round = 0; round < 4; round++) {
    for (int i = 0; i < 1500; i++) {
      if (i % 3 == 0) extend_chain(1);
      make_str(1 + (size_t)(i + round) % 120);
    }
    collect();
    scribble_holes();
    garbage_refs(400);                  /* allocates into part of the holes */
    for (int i = 0; i < 100; i++) make_str(1 + (size_t)i % 90);
    collect();
    scribble_holes();
    collect_major();
    scribble_holes();
    garbage_refs(50);
    collect();
  }
  check(chain_length(g_chain) == 3000 + 4 * 500, "the chain is intact");
  return g_failed;
}

static int tail_retained(void) {
  register_roots();
  g_o[0] = P(vec_make_filled(100, 0));
  age_to_old(W(g_o[0]), collect);
  check(is_old(W(g_o[0])), "the vector is old");
  SproutRegion* r = region_find(g_o[0]);
  check(r != NULL && !r->is_large && r->young_count == 0, "its region holds no young object");
  const size_t hdr = (size_t)((char*)g_o[0] - 8 - r->base);
  const size_t old_slot = slot_bytes(SPROUT_HEAP_VECTOR, 100);
  const size_t new_slot = slot_bytes(SPROUT_HEAP_VECTOR, 0);
  vector_push(W(g_o[0]), 1);            /* past the inline capacity: the tail is released */
  collect();                            /* a minor that sweeps only young regions */
  check(g_gen.n_minor >= 1, "the last collection is a minor");
  const size_t first = (hdr + new_slot) / SPROUT_SLOT_GRAN, n = (old_slot - new_slot) / SPROUT_SLOT_GRAN;
  int held = 0;
  for (size_t g = first; g < first + n; g++) held += bitmap_test(r->occ, g);
  check(held == 0, "the freed tail left `occ` at the next minor");
  check(bitmap_test(r->occ, hdr / SPROUT_SLOT_GRAN), "the vector itself is still in `occ`");
  check(vector_length(W(g_o[0])) == 101, "the vector is intact");
  return g_failed;
}

/* Old and young objects of every owner kind, in ordinary regions: g_o[0..2] old (a 7-granule
 * string, a vector with an out-of-line buffer, bytes), g_o[3..5] the same kinds young after one
 * minor.  The collection's own GENCHECK passes; each bad case then breaks one thing. */
static void scene(void) {
  register_roots();
  g_o[0] = P(make_str(100));
  g_o[1] = P(vec_make_filled(600, 0));
  g_o[2] = P(bytes_singleton(1));
  extend_chain(50);
  age_to_old(W(g_chain), collect);
  check(is_old(W(g_o[0])) && is_old(W(g_o[1])) && is_old(W(g_o[2])), "the first three are old");
  g_o[3] = P(make_str(100));
  g_o[4] = P(vec_make_filled(600, 0));
  g_o[5] = P(bytes_singleton(2));
  collect();
  check(!is_old(W(g_o[3])) && !is_old(W(g_o[4])) && !is_old(W(g_o[5])), "the next three are young");
}

static size_t granule_of(void* payload) {
  SproutRegion* r = region_find(payload);
  return (size_t)((char*)payload - 8 - r->base) / SPROUT_SLOT_GRAN;
}

static int bad_end(const char* name) {
  sprout_gc_check_sweep(1, 1);          /* what the collector runs after a sweep */
  fprintf(stderr, "%s: no abort\n", name);
  return 1;
}

static int bad_slotmap_outside_occ(void) {
  scene();
  SproutRegion* r = region_find(g_o[3]);
  size_t g = r->bump / SPROUT_SLOT_GRAN + 3;
  r->slotmap[g / 8] |= (uint8_t)(1u << (g % 8));
  return bad_end("bad_slotmap_outside_occ");
}

static int bad_youngmap_outside_occ(void) {
  scene();
  SproutRegion* r = region_find(g_o[3]);
  youngmap_set(r, r->bump / SPROUT_SLOT_GRAN + 3);
  return bad_end("bad_youngmap_outside_occ");
}

static int bad_extent_outside_occ(void) {
  scene();
  SproutRegion* r = region_find(g_o[3]);
  r->occ[(granule_of(g_o[3]) + 3) / 64] &= ~((uint64_t)1 << ((granule_of(g_o[3]) + 3) % 64));
  return bad_end("bad_extent_outside_occ");
}

static int bad_oldocc_extra(void) {
  scene();
  SproutRegion* r = region_find(g_o[3]);
  size_t g = r->bump / SPROUT_SLOT_GRAN + 3;
  r->oldocc[g / 64] |= (uint64_t)1 << (g % 64);
  return bad_end("bad_oldocc_extra");
}

static int bad_oldocc_missing(void) {
  scene();
  SproutRegion* r = region_find(g_o[0]);
  size_t g = granule_of(g_o[0]) + 3;    /* inside the old string's extent, not its start */
  r->oldocc[g / 64] &= ~((uint64_t)1 << (g % 64));
  return bad_end("bad_oldocc_missing");
}

static int bad_markocc(void) {
  scene();
  SproutRegion* r = region_find(g_o[3]);
  r->markocc[0] |= 1;
  return bad_end("bad_markocc");
}

static void set_color(void* payload, int on) {
  uint64_t h = sprout_hdr_of(payload);
  if (on) h |= SPROUT_GC_COLOR_BIT; else h &= ~SPROUT_GC_COLOR_BIT;
  memcpy((char*)payload - 8, &h, 8);
}

static int bad_color_young(void) {
  scene();
  set_color(g_o[3], 1);
  return bad_end("bad_color_young");
}

static int bad_color_old(void) {
  scene();
  set_color(g_o[0], 0);
  return bad_end("bad_color_old");
}

/* Take `payload` off the list it is on, and put it on `to` (NULL: nowhere). */
static void move_buffer(void* payload, SproutWorklist* from, SproutWorklist* to) {
  size_t i = 0;
  while (i < from->len && from->items[i] != payload) i++;
  check(i < from->len, "the owner is on the list the case takes it from");
  from->items[i] = from->items[--from->len];
  if (to != NULL) sprout_wl_push(to, payload, "holes_check: out of memory");
}

static int bad_buf_missing(void) {
  scene();
  move_buffer(g_o[4], &region_find(g_o[4])->buf_young, NULL);
  return bad_end("bad_buf_missing");
}

static int bad_buf_twice(void) {
  scene();
  sprout_wl_push(&region_find(g_o[4])->buf_young, g_o[4], "holes_check: out of memory");
  return bad_end("bad_buf_twice");
}

static int bad_buf_young_on_old(void) {
  scene();
  SproutRegion* r = region_find(g_o[4]);
  move_buffer(g_o[4], &r->buf_young, &r->buf_old);
  return bad_end("bad_buf_young_on_old");
}

/* ---- SPROUT_FL_VERIFY under HOLES (premise 12) ---- */

/* Release a real inline tail: an inline vector of 100 elements grows past its slot.  Returns the
 * tail's address, and its region and class. */
static char* release_tail(SproutRegion** rp, size_t* cls) {
  register_roots();
  g_o[0] = P(vec_make_filled(100, 0));
  SproutRegion* r = region_find(g_o[0]);
  const size_t hdr = (size_t)((char*)g_o[0] - 8 - r->base);
  const size_t old_slot = slot_bytes(SPROUT_HEAP_VECTOR, 100);
  const size_t new_slot = slot_bytes(SPROUT_HEAP_VECTOR, 0);
  vector_push(W(g_o[0]), 1);
  *rp = r;
  *cls = (old_slot - new_slot) / SPROUT_SLOT_GRAN;
  return r->base + hdr + new_slot;
}

static int bad_fl_cursor_backward(void) {
  scene();
  void* a = P(make_str(1));             /* one granule, from the main cursor */
  SproutRegion* r = region_find(a);
  const size_t ga = granule_of(a), g0 = occ_next_set(r->occ, 0);
  check(g0 < ga, "a live granule lies below the allocation");
  r->occ[g0 / 64] &= ~((uint64_t)1 << (g0 % 64));   /* a hole the cursor already passed */
  g_hole_cur.ri = (size_t)(r - g_regions);
  g_hole_cur.gran = g0;
  make_str(1);
  fprintf(stderr, "bad_fl_cursor_backward: no abort\n");
  return 1;
}

static int bad_fl_tail_twice(void) {
  SproutRegion* r; size_t cls;
  char* tail = release_tail(&r, &cls);
  sprout_holes_push_tail(r, tail, cls);
  fprintf(stderr, "bad_fl_tail_twice: no abort\n");
  return 1;
}

static int bad_fl_tail_not_in_occ(void) {
  scene();
  SproutRegion* r = region_find(g_o[3]);
  sprout_holes_push_tail(r, r->base + (r->bump / SPROUT_SLOT_GRAN + 3) * SPROUT_SLOT_GRAN, 1);
  fprintf(stderr, "bad_fl_tail_not_in_occ: no abort\n");
  return 1;
}

static int bad_fl_tail_slotmap(void) {
  scene();
  SproutRegion* r = region_find(g_o[3]);   /* young: in `occ`, not in `oldocc`, but a slot start */
  sprout_holes_push_tail(r, r->base + granule_of(g_o[3]) * SPROUT_SLOT_GRAN, 1);
  fprintf(stderr, "bad_fl_tail_slotmap: no abort\n");
  return 1;
}

static int bad_fl_tail_oldocc(void) {
  scene();
  SproutRegion* r = region_find(g_o[0]);   /* inside the old string's extent: `oldocc`, no slot start */
  sprout_holes_push_tail(r, r->base + (granule_of(g_o[0]) + 3) * SPROUT_SLOT_GRAN, 1);
  fprintf(stderr, "bad_fl_tail_oldocc: no abort\n");
  return 1;
}

static int bad_fl_tail_take_unlisted(void) {
  SproutRegion* r; size_t cls;
  char* tail = release_tail(&r, &cls);
  const size_t idx = (size_t)(tail - r->base) / SPROUT_SLOT_GRAN;
  check(r->tailvf != NULL && bitmap_test(r->tailvf, idx), "the released tail is recorded as listed");
  if (r->tailvf != NULL) r->tailvf[idx / 64] &= ~((uint64_t)1 << (idx % 64));
  make_str(cls * SPROUT_SLOT_GRAN - 9);  /* the tail's exact class: allocation takes it */
  fprintf(stderr, "bad_fl_tail_take_unlisted: no abort\n");
  return 1;
}

static int bad_fl_alloc_in_occ(void) {
  scene();
  sprout_holes_verify_alloc(&g_hole_cur, region_find(g_o[3]), granule_of(g_o[3]), 1);
  fprintf(stderr, "bad_fl_alloc_in_occ: no abort\n");
  return 1;
}

static int bad_fl_tails_at_sweep(void) {
  scene();
  g_hole_tails[2] = (void*)&g_o[0];
  sprout_holes_verify_before_sweep();
  fprintf(stderr, "bad_fl_tails_at_sweep: no abort\n");
  return 1;
}

static int bad_fl_cursor_not_reset(void) {
  scene();
  g_hole_ovf.gran = 5;
  sprout_holes_verify_after_sweep();
  fprintf(stderr, "bad_fl_cursor_not_reset: no abort\n");
  return 1;
}

/* ---- HDRCHECK under HOLES: a header whose step ends off a `slotmap` start or the run's end ---- */

static void set_aux(void* payload, uint64_t aux) {
  uint64_t h = sprout_hdr_of(payload);
  h = (h & (((uint64_t)1 << 14) - 1)) | (aux << 14);
  memcpy((char*)payload - 8, &h, 8);
}

static int bad_hdr_too_long(void) {
  scene();
  set_aux(g_o[0], 164);                 /* a 100-byte string; the header now says 164 */
  sprout_holes_hdrcheck_walk();
  fprintf(stderr, "bad_hdr_too_long: no abort\n");
  return 1;
}

static int bad_hdr_too_short(void) {
  scene();
  set_aux(g_o[0], 20);
  sprout_holes_hdrcheck_walk();
  fprintf(stderr, "bad_hdr_too_short: no abort\n");
  return 1;
}

typedef struct {
  const char* name;
  int (*fn)(void);
  const char* env;   /* the verify switch the case sets, "both" for the two, or NULL */
} HolesCase;

static const HolesCase k_cases[] = {
  { "positive",               positive, NULL },
  { "positive_verify",        positive, "both" },
  { "scribble",               scribble, NULL },
  { "scribble_verify",        scribble, "SPROUT_FL_VERIFY" },
  { "scribble_hdrcheck",      scribble, "SPROUT_GC_HDRCHECK" },
  { "tail_retained",          tail_retained, NULL },
  { "bad_slotmap_outside_occ", bad_slotmap_outside_occ, NULL },
  { "bad_youngmap_outside_occ", bad_youngmap_outside_occ, NULL },
  { "bad_extent_outside_occ", bad_extent_outside_occ, NULL },
  { "bad_oldocc_extra",       bad_oldocc_extra, NULL },
  { "bad_oldocc_missing",     bad_oldocc_missing, NULL },
  { "bad_markocc",            bad_markocc, NULL },
  { "bad_color_young",        bad_color_young, NULL },
  { "bad_color_old",          bad_color_old, NULL },
  { "bad_buf_missing",        bad_buf_missing, NULL },
  { "bad_buf_twice",          bad_buf_twice, NULL },
  { "bad_buf_young_on_old",   bad_buf_young_on_old, NULL },
  { "bad_fl_cursor_backward", bad_fl_cursor_backward, "SPROUT_FL_VERIFY" },
  { "bad_fl_tail_twice",      bad_fl_tail_twice, "SPROUT_FL_VERIFY" },
  { "bad_fl_tail_not_in_occ", bad_fl_tail_not_in_occ, "SPROUT_FL_VERIFY" },
  { "bad_fl_tail_slotmap",    bad_fl_tail_slotmap, "SPROUT_FL_VERIFY" },
  { "bad_fl_tail_oldocc",     bad_fl_tail_oldocc, "SPROUT_FL_VERIFY" },
  { "bad_fl_tail_take_unlisted", bad_fl_tail_take_unlisted, "SPROUT_FL_VERIFY" },
  { "bad_fl_alloc_in_occ",    bad_fl_alloc_in_occ, "SPROUT_FL_VERIFY" },
  { "bad_fl_tails_at_sweep",  bad_fl_tails_at_sweep, "SPROUT_FL_VERIFY" },
  { "bad_fl_cursor_not_reset", bad_fl_cursor_not_reset, "SPROUT_FL_VERIFY" },
  { "bad_hdr_too_long",       bad_hdr_too_long, "SPROUT_GC_HDRCHECK" },
  { "bad_hdr_too_short",      bad_hdr_too_short, "SPROUT_GC_HDRCHECK" },
};

int main(int argc, char** argv) {
  if (argc < 2) return 2;
  const HolesCase* c = NULL;
  for (size_t i = 0; i < sizeof k_cases / sizeof k_cases[0]; i++)
    if (strcmp(argv[1], k_cases[i].name) == 0) c = &k_cases[i];
  if (!c) return 2;
  setenv("SPROUT_GC_HOLES", "1", 1);
  setenv("SPROUT_GC_MINOR", "1", 1);
  setenv("SPROUT_GC_GENCHECK", "1", 1);
  setenv("SPROUT_GC_MAJOR_EVERY", "1000", 1);
  unsetenv("SPROUT_GC_HDRCHECK");
  unsetenv("SPROUT_FL_VERIFY");
  if (c->env != NULL && strcmp(c->env, "both") == 0) {
    setenv("SPROUT_FL_VERIFY", "1", 1);
    setenv("SPROUT_GC_HDRCHECK", "1", 1);
  } else if (c->env != NULL) {
    setenv(c->env, "1", 1);
  }
  unsetenv("SPROUT_GC_LINEAGE");
  unsetenv("SPROUT_GC_STRESS");
  unsetenv("SPROUT_GC_AGEPROF");
  unsetenv("SPROUT_GC_REGION_LISTS");
  sprout_set_argv(argc, argv);  /* reads the switches, as a compiled program's main does */
  g_gc_threshold = 0;
  if (c->fn() || g_failed) return 1;
  printf("holes-check-%s\n", c->name);
  return 0;
}
