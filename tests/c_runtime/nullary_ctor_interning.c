/* Nullary constructors are interned per tag, not per name.
 *
 * A zero-arity constructor has no fields, so two values carrying the same tag
 * are indistinguishable and nothing can mutate one. Sharing a single object per
 * tag is therefore observationally equivalent for EVERY nullary ctor — it was
 * never a property of `Nothing`, which is all sprout_make0's strcmp allowlist
 * ever covered. `Nil` and `True`/`False` are the ones that cost the most, and
 * neither was on it.
 *
 *   intern   — two constructions of one tag return the SAME object, for a name
 *              that no allowlist could have known.
 *   distinct — interning is keyed on the tag, so two nullary ctors do not
 *              collide, and each reads back its own tag.
 *   arity    — a ctor WITH fields is never interned; two constructions must be
 *              separate objects or field writes would alias.
 *   weak     — the cache is weak: the singleton is an ordinary managed object,
 *              and when a sweep reclaims it the cache slot must be cleared. If
 *              it is not, the next construction hands back freed memory, so
 *              this case reads the tag afterwards.
 *
 * Every case runs under SPROUT_GC_STRESS=1 too, so a collection lands between
 * each pair of allocations. That is why the identity cases hold their handles in
 * ROOTED slots: an unrooted handle is freed by that collection and the next
 * allocation reuses the address, which would make `a == b` true for entirely
 * the wrong reason — a false green that survives the feature being deleted.
 */
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

/* Allocate enough to force a collection; results are deliberately dropped. */
static void churn(void) {
  for (int i = 0; i < 200; i++) (void)int_to_string(i);
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
  g_a = sprout_alloc_obj(0, 2);
  g_b = sprout_alloc_obj(0, 2);
  if (g_a == g_b) {
    fprintf(stderr, "arity: a ctor with fields was interned; writes would alias\n");
    return 1;
  }
  printf("nullary-ctor-arity-respected\n");
  return 0;
}

static int case_weak(void) {
  root_slots();
  sprout_register_ctor(0, "main.Ember", 0, "");
  (void)sprout_alloc_obj(0, 0);   /* caches the singleton, then drops every reference */
  churn();                        /* the sweep reclaims it and must clear the cache */
  g_a = sprout_alloc_obj(0, 0);
  if (sprout_tag(g_a) != 0) {     /* a stale cache makes this read freed memory */
    fprintf(stderr, "weak: tag readback %lld after collection, expected 0\n",
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
