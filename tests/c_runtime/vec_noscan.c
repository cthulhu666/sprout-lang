/* NOSCAN vectors (docs/pointer-free-vectors-v0.md §4.1, §6.1), driven directly.
 *
 * A NOSCAN vector's elements are never heap pointers, so marking skips them, the sweep still sizes
 * its slot, and the store barrier has nothing to record. The cases build "Vector Int" values by
 * hand and set the flag the way the compiler will (sprout_vec_set_noscan). Each runs in its own
 * process with SPROUT_GC_HDRCHECK=1, so a sweep that misreads a flagged header's aux aborts.
 *
 *   retention   — an element equal to a garbage object's address keeps it alive in an unflagged
 *                 vector and not in a flagged one (the false-pointer case).
 *   inherit     — vector_set, vector_append and vector_concat copy the bit; concat needs both.
 *   empty_set   — vector_set on an empty flagged vector carries the bit.
 *   push_out    — a flagged vector pushed past the inline ceiling keeps the bit and its elements.
 *   barrier_noscan, barrier_scan — under SPROUT_GC_MINOR=1, a store into an old flagged vector is
 *                 not remembered; into an unflagged one it is.
 *
 * The runtime is included so the cases reach the static remembered set. */
#include <stdio.h>
#include <stdlib.h>

#include "../../runtime/sprout_runtime.c"
#include "runtime_case.h"

#define NO_FLAG 0
#define FLAG 1

static void* g_a;  /* permanent roots */
static void* g_b;
static void* g_keep[8];

static void collect_major(void) { sprout_gc_collect_with_reason("atexit"); }

/* Reads the raw header bit, not sprout_vec_is_noscan, so the layout is checked independently. */
static int flagged(long long w) {
  return (sprout_hdr_of((void*)(uintptr_t)w) & SPROUT_VEC_NOSCAN_BIT) != 0;
}
static long long len_of(long long w) { return ((VectorVal*)(uintptr_t)w)->len; }
static long long at(long long w, long long i) { return ((VectorVal*)(uintptr_t)w)->data[i]; }

static long long make_vec(long long n, long long fill, int flag) {
  long long v = vec_make_filled(n, fill);
  if (flag) sprout_vec_set_noscan((void*)(uintptr_t)v);
  return v;
}

static void root(void** slot) { register_root_slot(slot, SPROUT_ROOT_PTR, 0); }

static int retention(void) {
  /* Two garbage Refs; each vector's one element is a Ref's payload address. */
  long long ga = ref_new(0), gb = ref_new(0);
  long long va = make_vec(1, ga, FLAG);
  long long vb = make_vec(1, gb, NO_FLAG);
  g_a = (void*)(uintptr_t)va;
  g_b = (void*)(uintptr_t)vb;
  root(&g_a);
  root(&g_b);
  check(flagged(va) && !flagged(vb), "only the first vector carries the bit");
  collect_major();
  check(sprout_heap_lookup((void*)(uintptr_t)ga) == NULL, "a NOSCAN element is not followed");
  check(sprout_heap_lookup((void*)(uintptr_t)gb) != NULL, "an unflagged element is followed");
  check(at(va, 0) == ga && len_of(va) == 1, "the flagged vector keeps its element");
  check(flagged(va), "the bit survives a collection");
  collect_major();
  check(sprout_heap_lookup((void*)(uintptr_t)va) != NULL, "the flagged vector itself is live");
  return g_failed;
}

static int inherit(void) {
  long long f = make_vec(3, 7, FLAG), u = make_vec(2, 5, NO_FLAG);
  g_keep[0] = (void*)(uintptr_t)f;
  g_keep[1] = (void*)(uintptr_t)u;
  root(&g_keep[0]);
  root(&g_keep[1]);

  long long s = vector_set(f, 1, 99);
  g_keep[2] = (void*)(uintptr_t)s;
  root(&g_keep[2]);
  check(flagged(s), "vector_set of a flagged vector is flagged");
  check(len_of(s) == 3 && at(s, 0) == 7 && at(s, 1) == 99 && at(s, 2) == 7, "vector_set contents");
  long long su = vector_set(u, 0, 1);
  check(!flagged(su), "vector_set of an unflagged vector is unflagged");

  long long a = vector_append(f, 42);
  g_keep[3] = (void*)(uintptr_t)a;
  root(&g_keep[3]);
  check(flagged(a), "vector_append of a flagged vector is flagged");
  check(len_of(a) == 4 && at(a, 3) == 42 && at(a, 0) == 7, "vector_append contents");
  long long au = vector_append(u, 42);
  check(!flagged(au), "vector_append of an unflagged vector is unflagged");

  long long ff = vector_concat(f, f);
  g_keep[4] = (void*)(uintptr_t)ff;
  root(&g_keep[4]);
  check(flagged(ff), "vector_concat of two flagged vectors is flagged");
  check(len_of(ff) == 6 && at(ff, 5) == 7, "vector_concat contents");
  check(!flagged(vector_concat(f, u)), "flagged ++ unflagged is unflagged");
  check(!flagged(vector_concat(u, f)), "unflagged ++ flagged is unflagged");
  check(!flagged(vector_concat(u, u)), "unflagged ++ unflagged is unflagged");

  collect_major();  /* HDRCHECK sweeps every derived vector's header */
  check(flagged(s) && flagged(a) && flagged(ff), "the bit survives a collection");
  check(at(s, 1) == 99 && at(a, 3) == 42 && at(ff, 3) == 7, "derived vectors read back");
  return g_failed;
}

static int empty_set(void) {
  long long e = vector_empty();
  sprout_vec_set_noscan((void*)(uintptr_t)e);
  g_keep[0] = (void*)(uintptr_t)e;
  root(&g_keep[0]);
  long long s = vector_set(e, 0, 1);
  g_keep[1] = (void*)(uintptr_t)s;
  root(&g_keep[1]);
  check(len_of(s) == 0, "vector_set on an empty vector stays empty");
  check(flagged(s), "vector_set on an empty flagged vector is flagged");
  long long u = vector_set(vector_empty(), 0, 1);
  check(!flagged(u), "vector_set on an empty unflagged vector is unflagged");
  long long ea = vector_append(e, 3);
  check(flagged(ea) && len_of(ea) == 1 && at(ea, 0) == 3, "vector_append on an empty flagged vector");
  collect_major();
  check(flagged(s), "the bit survives a collection");
  return g_failed;
}

/* Pushes past SPROUT_VEC_INLINE_MAX move the elements out of line; the header is edited, and the
 * bit must stay while the count field drops to 0. */
static int push_out(void) {
  static const long long sizes[] = { 8, SPROUT_VEC_INLINE_MAX };
  for (size_t k = 0; k < sizeof sizes / sizeof sizes[0]; k++) {
    long long n = sizes[k];
    long long v = make_vec(n, 3, FLAG);
    g_a = (void*)(uintptr_t)v;
    if (k == 0) root(&g_a);
    check(flagged(v) && vec_data_is_inline((VectorVal*)(uintptr_t)v), "starts inline and flagged");
    long long total = SPROUT_VEC_INLINE_MAX + 100;
    for (long long i = n; i < total; i++) vector_push(v, i);
    check(len_of(v) == total, "every push lands");
    check(flagged(v), "the bit survives the move out of line");
    check(!vec_data_is_inline((VectorVal*)(uintptr_t)v), "the elements are out of line");
    check((sprout_hdr_aux(sprout_hdr_of((void*)(uintptr_t)v)) & SPROUT_VEC_AUX_COUNT_MASK) == 0,
          "the inline count is dropped");
    collect_major();  /* HDRCHECK: aux and ->data must agree */
    check(flagged(v) && len_of(v) == total, "the vector survives a sweep");
    int ok = 1;
    for (long long i = 0; i < total; i++) ok &= at(v, i) == (i < n ? 3 : i);
    check(ok, "every element reads back");
  }
  return g_failed;
}

/* Make a rooted vector old: the first collection is a major, which keeps the colour bit. */
static long long make_old_vec(int flag) {
  long long v = make_vec(4, 0, flag);
  g_a = (void*)(uintptr_t)v;
  root(&g_a);
  collect_major();
  check(is_old(v), "a survivor of a major is old");
  check(g_gen.remembered.len == 0, "nothing is remembered yet");
  return v;
}

static int barrier_noscan(void) {
  long long v = make_old_vec(FLAG);
  vector_mutset(v, 1, 9);
  check(g_gen.remembered.len == 0 && !is_remembered(v), "vector_mutset on a flagged vector records nothing");
  vector_push(v, 5);  /* grows out of line */
  check(g_gen.remembered.len == 0 && !is_remembered(v), "vector_push on a flagged vector records nothing");
  check(len_of(v) == 5 && at(v, 1) == 9 && at(v, 4) == 5, "the stores still land");
  sprout_gc_collect_with_reason("threshold");  /* a minor, with GENCHECK */
  check(g_gen.n_minor == 1, "the second collection is a minor");
  check(flagged(v) && at(v, 1) == 9 && at(v, 4) == 5, "the vector survives a minor");
  return g_failed;
}

static int barrier_scan(void) {
  long long v = make_old_vec(NO_FLAG);
  vector_mutset(v, 1, 9);
  check(g_gen.remembered.len == 1 && is_remembered(v), "vector_mutset on an unflagged vector records it");
  sprout_gc_collect_with_reason("threshold");
  check(g_gen.n_minor == 1 && g_gen.remembered.len == 0, "a minor empties the set");
  vector_push(v, 5);
  check(g_gen.remembered.len == 1 && is_remembered(v), "vector_push on an unflagged vector records it");
  check(len_of(v) == 5 && at(v, 1) == 9 && at(v, 4) == 5, "the stores still land");
  return g_failed;
}

typedef struct {
  const char* name;
  int (*fn)(void);
  int minor;  /* SPROUT_GC_MINOR=1 with GENCHECK */
} NoscanCase;

static const NoscanCase k_cases[] = {
  { "retention",       retention,       0 },
  { "inherit",         inherit,         0 },
  { "empty_set",       empty_set,       0 },
  { "push_out",        push_out,        0 },
  { "barrier_noscan",  barrier_noscan,  1 },
  { "barrier_scan",    barrier_scan,    1 },
};

int main(int argc, char** argv) {
  if (argc < 2) return 2;
  const NoscanCase* c = NULL;
  for (size_t i = 0; i < sizeof k_cases / sizeof k_cases[0]; i++)
    if (strcmp(argv[1], k_cases[i].name) == 0) c = &k_cases[i];
  if (!c) return 2;
  setenv("SPROUT_GC_LINEAGE", "0", 1);
  setenv("SPROUT_GC_HDRCHECK", "1", 1);
  if (c->minor) {
    setenv("SPROUT_GC_MINOR", "1", 1);
    setenv("SPROUT_GC_GENCHECK", "1", 1);
  }
  sprout_set_argv(argc, argv);  /* reads the switches, as a compiled program's main does */
  if (c->fn() || g_failed) return 1;
  printf("vec-noscan-%s\n", c->name);
  return 0;
}
