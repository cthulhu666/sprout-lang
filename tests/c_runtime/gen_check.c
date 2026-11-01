/* Minor collections and the GENCHECK verifier (docs/gc-generational-v0.md §14.4, §14.5), driven
 * directly under SPROUT_GC_MINOR=1 SPROUT_GC_GENCHECK=1.
 *
 * An object is made old the real way: rooted across the first collection (a major, which keeps
 * the colour bit on every survivor). The runtime is included so the cases reach the static
 * remembered set and the collector entry. Nothing here is rooted except `g_old`, a permanent root.
 *
 *   missing_barrier — a young Ref is stored into the old one with NO barrier and has no other
 *                     root. The next minor would free it while reachable: GENCHECK must abort,
 *                     naming the owner. The caller checks for the abort and its message.
 *   barrier         — the same store through ref_write: no abort, the young Ref survives, is
 *                     promoted, and the remembered set is empty again.
 *   minor_keeps_old — a minor does not free an unrooted old object; a major does.
 *   no_check        — the unbarriered store without GENCHECK is not detected: the verifier is
 *                     what turns the missing barrier into an abort, not the minor itself.
 *   bad_every_off   — SPROUT_GC_MAJOR_EVERY=abc with SPROUT_GC_MINOR=0: the mode is off, the bad
 *                     value is never read, and the collection runs.
 *   bad_every_on    — the same value with SPROUT_GC_MINOR=1 aborts at the first collection. */
#include <stdio.h>
#include <stdlib.h>

#include "../../runtime/sprout_runtime.c"
#include "runtime_case.h"

static void* g_old;  /* permanent root: the old Ref */

static int is_old(long long word) {
  return (sprout_hdr_of((void*)(uintptr_t)word) & SPROUT_GC_COLOR_BIT) != 0;
}
static void collect(void) { sprout_gc_collect_with_reason("threshold"); }

/* Root a fresh Ref, run the first collection (a major), and return it, old. */
static long long make_old_ref(void) {
  long long r = ref_new(0);
  g_old = (void*)(uintptr_t)r;
  register_root_slot(&g_old, SPROUT_ROOT_PTR, 0);
  collect();
  check(g_gen.n_major == 1 && g_gen.n_minor == 0, "the first collection is a major");
  check(is_old((long long)(uintptr_t)g_old), "a survivor of a major is old");
  return (long long)(uintptr_t)g_old;
}

static int missing_barrier(void) {
  long long r = make_old_ref();
  long long y = ref_new(7);
  ((RefVal*)(uintptr_t)r)->value = y;  /* the bug: no sprout_gc_write_barrier */
  collect();                           /* a minor: GENCHECK aborts here */
  fprintf(stderr, "missing_barrier: no abort\n");
  return 1;
}

static int barrier(void) {
  long long r = make_old_ref();
  long long y = ref_new(7);
  ref_write(r, y);
  check(is_remembered(r) && g_gen.remembered.len == 1, "the barrier remembers the old Ref");
  collect();
  check(g_gen.n_minor == 1, "the second collection is a minor");
  check(g_gen.remembered.len == 0 && !is_remembered(r), "a minor empties the remembered set");
  long long got = ref_read(r);
  check(got == y, "the old Ref still holds the young one");
  check(sprout_heap_lookup((void*)(uintptr_t)y) != NULL, "the young Ref survived");
  check(is_old(y), "a survivor of a minor is old");
  check(ref_read(y) == 7, "the young Ref still reads back");
  /* Churn: many minors keep the structure intact. */
  for (int i = 0; i < 50; i++) {
    long long z = ref_new(i);
    ref_write(y, z);
    collect();
    check(ref_read(ref_read(y)) == i, "a store into an old Ref survives each minor");
  }
  return g_failed;
}

static int minor_keeps_old(void) {
  long long r = make_old_ref();
  g_old = NULL;
  collect();
  check(g_gen.n_minor == 1, "the second collection is a minor");
  check(sprout_heap_lookup((void*)(uintptr_t)r) != NULL, "a minor keeps an unrooted old object");
  sprout_gc_collect_with_reason("atexit");
  check(g_gen.n_major == 2, "a non-threshold reason forces a major");
  check(sprout_heap_lookup((void*)(uintptr_t)r) == NULL, "a major frees it");
  return g_failed;
}

static int major_every(void) {
  setenv("SPROUT_GC_MAJOR_EVERY", "3", 1);
  make_old_ref();
  for (int i = 0; i < 3; i++) collect();
  check(g_gen.n_major == 1 && g_gen.n_minor == 3, "three minors follow a major");
  collect();
  check(g_gen.n_major == 2 && g_gen.n_minor == 3, "the fourth collection is a major");
  return g_failed;
}

static int bad_every_off(void) {
  ref_new(0);
  collect();
  check(g_gen.on == 0, "SPROUT_GC_MINOR=0 leaves the mode off");
  return g_failed;
}

static int bad_every_on(void) {
  ref_new(0);
  collect();  /* aborts: SPROUT_GC_MAJOR_EVERY is read with the mode on */
  fprintf(stderr, "bad_every_on: no abort\n");
  return 1;
}

int main(int argc, char** argv) {
  if (argc < 2) return 2;
  int off = strcmp(argv[1], "bad_every_off") == 0;
  setenv("SPROUT_GC_LINEAGE", "0", 1);
  setenv("SPROUT_GC_MINOR", off ? "0" : "1", 1);
  if (strncmp(argv[1], "bad_every_", 10) == 0) setenv("SPROUT_GC_MAJOR_EVERY", "abc", 1);
  if (strcmp(argv[1], "no_check") != 0) setenv("SPROUT_GC_GENCHECK", "1", 1);
  int rc;
  if (strcmp(argv[1], "missing_barrier") == 0) return missing_barrier();
  if (strcmp(argv[1], "bad_every_on") == 0) return bad_every_on();
  if (strcmp(argv[1], "no_check") == 0) {
    (void)missing_barrier();  /* returns 1: nothing aborted, which is the point */
    printf("gen-check-no_check\n");
    return 0;
  }
  if (strcmp(argv[1], "barrier") == 0) rc = barrier();
  else if (strcmp(argv[1], "minor_keeps_old") == 0) rc = minor_keeps_old();
  else if (strcmp(argv[1], "major_every") == 0) rc = major_every();
  else if (strcmp(argv[1], "bad_every_off") == 0) rc = bad_every_off();
  else return 2;
  if (rc || g_failed) return 1;
  printf("gen-check-%s\n", argv[1]);
  return 0;
}
