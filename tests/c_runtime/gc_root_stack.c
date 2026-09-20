/* The GC temp-root stack: the per-context root array must be marked in full,
 * including contexts that are not current, and the pop bound must hold.
 *
 * The roots of a context are pool[0..pool_top) — push bumps, pop decrements,
 * marking scans. Two things can break silently and are asserted here:
 *
 *   survive       — every slot in the live range is marked, not just the last
 *                   pushed one. A scan that stops early frees a live value,
 *                   which only shows up as corruption much later.
 *   other_context — a registered but NOT current context (a suspended green
 *                   task, a channel's buffer) is scanned too. Under-rooting one
 *                   frees values another task still holds.
 *   underflow     — popping past the live range aborts instead of wrapping
 *                   pool_top around and rooting the whole pool as garbage.
 *
 * Runs under SPROUT_GC_STRESS=1, so every allocation below collects. */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

long long int_to_string(long long value);
long long sprout_gc_push_ptr_root(void* slot);
long long sprout_gc_pop_roots(long long count);

typedef struct SproutRoots SproutRoots;
SproutRoots* sprout_roots_new(size_t pool_slots);
SproutRoots* sprout_roots_current(void);
void sprout_roots_switch(SproutRoots* r);
void sprout_roots_push_ptr(SproutRoots* r, void* slot);

#define SLOTS 64
#define CHURN 200

static const char* g_slots[SLOTS];
static const char* g_far_slot;

/* Allocate enough to force many collections; the result is deliberately dropped. */
static void churn(void) {
  for (int i = 0; i < CHURN; i++) (void)int_to_string(i);
}

static int expect(const char* got, long long want, const char* what) {
  char buf[32];
  snprintf(buf, sizeof(buf), "%lld", want);
  if (got == NULL || strcmp(got, buf) != 0) {
    fprintf(stderr, "%s: expected \"%s\", got \"%s\"\n", what, buf, got == NULL ? "(null)" : got);
    return 1;
  }
  return 0;
}

/* Fill the live range, churn, and read every slot back. */
static int case_survive(void) {
  for (int i = 0; i < SLOTS; i++) {
    g_slots[i] = (const char*)(uintptr_t)int_to_string(1000000 + i);
    sprout_gc_push_ptr_root(&g_slots[i]);
  }
  churn();
  int bad = 0;
  for (int i = 0; i < SLOTS; i++) bad += expect(g_slots[i], 1000000 + i, "survive");
  /* Pop half, churn again: the survivors are the ones still inside the range. */
  sprout_gc_pop_roots(SLOTS / 2);
  churn();
  for (int i = 0; i < SLOTS / 2; i++) bad += expect(g_slots[i], 1000000 + i, "survive-after-pop");
  sprout_gc_pop_roots(SLOTS / 2);
  if (bad == 0) printf("gc-root-stack-survive\n");
  return bad != 0;
}

/* A registered context that is not the current one still has its roots marked. */
static int case_other_context(void) {
  SproutRoots* other = sprout_roots_new(4);
  SproutRoots* main_roots = sprout_roots_current();

  sprout_roots_switch(other);
  g_far_slot = (const char*)(uintptr_t)int_to_string(4242);
  sprout_roots_push_ptr(other, &g_far_slot);
  sprout_roots_switch(main_roots);

  churn();
  int bad = expect(g_far_slot, 4242, "other-context");
  if (bad == 0) printf("gc-root-stack-other-context\n");
  return bad != 0;
}

/* Popping one past the live range must abort, not wrap pool_top around.
 * Runs on a fresh context so the range is exactly what this case pushed:
 * task-0's pool already holds the scheduler's permanent roots. */
static int case_underflow(void) {
  static const char* slot[3];
  sprout_roots_switch(sprout_roots_new(8));
  for (int i = 0; i < 3; i++) {
    slot[i] = (const char*)(uintptr_t)int_to_string(i);
    sprout_gc_push_ptr_root(&slot[i]);
  }
  sprout_gc_pop_roots(4);
  printf("gc-root-stack-underflow-not-caught\n");
  return 1;
}

int main(int argc, char** argv) {
  if (argc != 2) return 99;
  setenv("SPROUT_GC_STRESS", "1", 1);
  if (strcmp(argv[1], "survive") == 0) return case_survive();
  if (strcmp(argv[1], "other_context") == 0) return case_other_context();
  if (strcmp(argv[1], "underflow") == 0) return case_underflow();
  return 99;
}
