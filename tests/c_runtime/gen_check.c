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
 *   bad_every_on    — the same value with SPROUT_GC_MINOR=1 aborts at startup, in
 *                     sprout_set_argv, before any collection.
 *   bad_every_late  — the same, where the program's only collection is the atexit one: the abort
 *                     must come before the program prints anything.
 *   major_every     — SPROUT_GC_MAJOR_EVERY=3: a major follows every three minors.
 *
 * `k_cases` lists every case with its environment; main sets it before sprout_set_argv. */
#include <stdio.h>
#include <stdlib.h>

#include "../../runtime/sprout_runtime.c"
#include "runtime_case.h"

static void* g_old;  /* permanent root: the old Ref */

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
  /* Not reached: sprout_set_argv aborts on the bad SPROUT_GC_MAJOR_EVERY. */
  fprintf(stderr, "bad_every_on: no abort\n");
  return 1;
}

static int bad_every_late(void) {
  ref_new(0);
  printf("after-alloc\n");
  fflush(stdout);
  return 0;  /* the atexit collection is the first one */
}

/* The unbarriered store without GENCHECK is not detected: missing_barrier returns 1 because
 * nothing aborted, which is the point. */
static int no_check(void) {
  (void)missing_barrier();
  return 0;
}

typedef struct {
  const char* name;
  int (*fn)(void);
  const char* minor;        /* SPROUT_GC_MINOR */
  int gencheck;             /* set SPROUT_GC_GENCHECK=1 */
  const char* major_every;  /* SPROUT_GC_MAJOR_EVERY, or NULL to leave it unset */
} GenCase;

static const GenCase k_cases[] = {
  { "missing_barrier", missing_barrier, "1", 1, NULL },
  { "barrier",         barrier,         "1", 1, NULL },
  { "minor_keeps_old", minor_keeps_old, "1", 1, NULL },
  { "major_every",     major_every,     "1", 1, "3" },
  { "no_check",        no_check,        "1", 0, NULL },
  { "bad_every_off",   bad_every_off,   "0", 1, "abc" },
  { "bad_every_on",    bad_every_on,    "1", 1, "abc" },
  { "bad_every_late",  bad_every_late,  "1", 1, "abc" },
};

int main(int argc, char** argv) {
  if (argc < 2) return 2;
  const GenCase* c = NULL;
  for (size_t i = 0; i < sizeof k_cases / sizeof k_cases[0]; i++)
    if (strcmp(argv[1], k_cases[i].name) == 0) c = &k_cases[i];
  if (!c) return 2;
  setenv("SPROUT_GC_LINEAGE", "0", 1);
  setenv("SPROUT_GC_MINOR", c->minor, 1);
  if (c->gencheck) setenv("SPROUT_GC_GENCHECK", "1", 1);
  if (c->major_every) setenv("SPROUT_GC_MAJOR_EVERY", c->major_every, 1);
  sprout_set_argv(argc, argv);  /* reads the switches, as a compiled program's main does */
  if (c->fn() || g_failed) return 1;
  printf("gen-check-%s\n", c->name);
  return 0;
}
