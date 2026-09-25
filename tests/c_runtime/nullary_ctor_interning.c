/* Nullary constructors are interned per tag, not per name.
 *
 * A zero-arity constructor has no fields, so two values carrying the same tag
 * are indistinguishable and nothing can mutate one. Sharing a single object per
 * tag is therefore observationally equivalent for EVERY nullary ctor — it was
 * never a property of `Nothing`, which is all sprout_make0's strcmp allowlist
 * ever covered. `Nil` terminates every list and was not on it. (`Bool` is NOT
 * among them: it lowers to a native i1 and constructs no object at all.)
 *
 *   intern   — two constructions of one tag return the SAME object, for a name
 *              that no allowlist could have known.
 *   distinct — interning is keyed on the tag, so two nullary ctors do not
 *              collide, and each reads back its own tag.
 *   arity    — a ctor WITH fields is never interned, by either route: built
 *              normally two constructions are separate objects, and reaching
 *              sprout_make0 with nfields==0 on an arity>0 meta must decline to
 *              intern rather than cache a short object under that tag.
 *   weak     — the cache is weak: the singleton is an ordinary managed object,
 *              and when a sweep reclaims it the cache slot must be cleared. If
 *              it is not, the next construction hands back freed memory.
 *
 * TAG 7, NOT TAG 0, IN case_weak, AND THAT IS THE WHOLE POINT. A freed slot's
 * header is rewritten to SPROUT_HEAP_FREE | (ssize << 14) with ssize == 16, so
 * sprout_tag on a stale pointer reads 16 >> 8 == 0. An earlier version of this
 * case registered tag 0 and asserted the readback was 0 — which freed-slot
 * garbage satisfies, so the whole file passed with the invalidation deleted.
 * A short CSTR reusing the slot reads 0 too (aux = strlen, strlen >> 8 == 0).
 * No reachable garbage reads as 7.
 *
 * Under SPROUT_GC_LINEAGE=1 the reclaimed slot is POISONED rather than reused,
 * and sprout_tag aborts on it — a discriminator that does not depend on what the
 * allocator hands back next. run.sh runs this case that way too.
 *
 * Every case also runs under SPROUT_GC_STRESS=1, which collects on every
 * allocation. That is why the identity cases hold their handles in ROOTED
 * slots: an unrooted handle is freed by that collection and the next allocation
 * reuses the address, making `a == b` true for entirely the wrong reason.
 */
#include <stdint.h>
#include <stdio.h>
#include <string.h>

long long sprout_register_ctor(long long tag, const char* name, long long arity,
                               const char* field_kinds);
long long sprout_alloc_obj(long long tag, long long nfields);
long long sprout_tag(long long h);
long long int_to_string(long long value);
long long sprout_gc_push_i64_root(void* slot);

/* Rooted across every allocation below; see the header note. */
static long long g_a, g_b, g_c, g_d;

static void root_slots(void) {
  sprout_gc_push_i64_root(&g_a);
  sprout_gc_push_i64_root(&g_b);
  sprout_gc_push_i64_root(&g_c);
  sprout_gc_push_i64_root(&g_d);
}

/* Must actually cross the collector's threshold, which is g_gc_threshold_base
 * (4096 objects) and stays there while the live set is this small. 200 — what
 * this used to allocate — never collected at all without SPROUT_GC_STRESS, so
 * half the matrix exercised nothing. */
#define CHURN 8000
static void churn(void) {
  for (int i = 0; i < CHURN; i++) (void)int_to_string(i);
}

static int case_intern(void) {
  root_slots();
  sprout_register_ctor(0, "main.Spark", 0, "");
  g_a = sprout_alloc_obj(0, 0);
  g_b = sprout_alloc_obj(0, 0);
  if (g_a != g_b) {
    fprintf(stderr, "intern: nullary ctor allocated twice (%lld vs %lld)\n", g_a, g_b);
    return 1;
  }
  if (sprout_tag(g_a) != 0) {
    fprintf(stderr, "intern: tag readback %lld, expected 0\n", sprout_tag(g_a));
    return 1;
  }
  printf("nullary-ctor-interned\n");
  return 0;
}

static int case_distinct(void) {
  root_slots();
  sprout_register_ctor(0, "main.Red", 0, "");
  sprout_register_ctor(1, "main.Green", 0, "");
  g_a = sprout_alloc_obj(0, 0);
  g_b = sprout_alloc_obj(1, 0);
  if (g_a == g_b) {
    fprintf(stderr, "distinct: two nullary tags share one object\n");
    return 1;
  }
  if (sprout_tag(g_a) != 0 || sprout_tag(g_b) != 1) {
    fprintf(stderr, "distinct: tags read back as %lld / %lld, expected 0 / 1\n",
            sprout_tag(g_a), sprout_tag(g_b));
    return 1;
  }
  /* Re-entering each must still hit its own slot, not the most recent one. */
  g_c = sprout_alloc_obj(0, 0);
  g_d = sprout_alloc_obj(1, 0);
  if (g_c != g_a || g_d != g_b) {
    fprintf(stderr, "distinct: re-construction did not return the cached object\n");
    return 1;
  }
  printf("nullary-ctor-distinct\n");
  return 0;
}

static int case_arity(void) {
  root_slots();
  sprout_register_ctor(0, "main.Pair", 2, "ii");

  /* The normal route. Fields are written immediately, as codegen does:
   * sprout_alloc_obj returns UNINITIALIZED payload words under a header that
   * already advertises them, so rooting the handle across another allocation
   * with them unwritten hands the marker two garbage words to trace. */
  g_a = sprout_alloc_obj(0, 2);
  ((long long*)(uintptr_t)g_a)[0] = 0;
  ((long long*)(uintptr_t)g_a)[1] = 0;
  g_b = sprout_alloc_obj(0, 2);
  ((long long*)(uintptr_t)g_b)[0] = 0;
  ((long long*)(uintptr_t)g_b)[1] = 0;
  if (g_a == g_b) {
    fprintf(stderr, "arity: a ctor with fields was interned; writes would alias\n");
    return 1;
  }

  /* The route that actually reaches sprout_make0's `meta->arity == 0` guard:
   * nfields==0 on a meta whose arity is 2. Interning here would cache a
   * zero-field object under a tag whose readers expect two. */
  g_c = sprout_alloc_obj(0, 0);
  g_d = sprout_alloc_obj(0, 0);
  if (g_c == g_d) {
    fprintf(stderr, "arity: sprout_make0 interned a tag whose meta arity is 2\n");
    return 1;
  }
  printf("nullary-ctor-arity-respected\n");
  return 0;
}

static int case_weak(void) {
  root_slots();
  sprout_register_ctor(7, "main.Ember", 0, "");
  (void)sprout_alloc_obj(7, 0);   /* caches the singleton, then drops every reference */
  churn();                        /* the sweep reclaims it and must clear the cache */
  g_a = sprout_alloc_obj(7, 0);
  /* A stale cache returns the freed slot, whose header reads back as tag 0
   * (or aborts outright under SPROUT_GC_LINEAGE=1). */
  if (sprout_tag(g_a) != 7) {
    fprintf(stderr, "weak: tag readback %lld after collection, expected 7 — "
                    "the cache was not cleared when the singleton was swept\n",
            sprout_tag(g_a));
    return 1;
  }
  printf("nullary-ctor-weak-cache\n");
  return 0;
}

int main(int argc, char** argv) {
  const char* sel = (argc > 1) ? argv[1] : "intern";
  if (strcmp(sel, "intern") == 0) return case_intern();
  if (strcmp(sel, "distinct") == 0) return case_distinct();
  if (strcmp(sel, "arity") == 0) return case_arity();
  if (strcmp(sel, "weak") == 0) return case_weak();
  fprintf(stderr, "unknown selector: %s\n", sel);
  return 2;
}
