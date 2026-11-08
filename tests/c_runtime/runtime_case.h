/* Helpers shared by the c_runtime cases that include sprout_runtime.c (they use runtime symbols,
 * so include this AFTER the runtime). Cases that link the runtime use str_check.h alone. */
#ifndef SPROUT_C_RUNTIME_CASE_H
#define SPROUT_C_RUNTIME_CASE_H

#include <stdio.h>

/* A headered String the GC never frees, for use as input. */
static long long (*const I)(const char*) = sprout_interned_word;

/* A collectable String nothing else roots, so under stress the callee must root its input. */
static long long F(const char* text) { return str_concat(I(text), I("")); }

static int g_failed = 0;
static void check(int ok, const char* what) {
  if (!ok) { g_failed = 1; fprintf(stderr, "FAIL: %s\n", what); }
}

/* True when the object's REMEMBERED header bit is set. */
static inline int is_remembered(long long word) {
  return (sprout_hdr_of((void*)(uintptr_t)word) & SPROUT_GC_REMEMBERED_BIT) != 0;
}

/* True when the object's colour bit is set, i.e. it is old. */
static inline int is_old(long long word) {
  return (sprout_hdr_of((void*)(uintptr_t)word) & SPROUT_GC_COLOR_BIT) != 0;
}

/* Run `collect_fn` until `w` is old: at most promote_age times. */
static inline void age_to_old(long long w, void (*collect_fn)(void)) {
  for (unsigned i = 0; i < g_gen.promote_age && !is_old(w); i++) collect_fn();
}

/* Links of the Ref chain that starts at `head` and is still a live heap object. */
static inline long long chain_length(void* head) {
  long long n = 0, cur = (long long)(uintptr_t)head;
  while (cur != 0 && sprout_heap_lookup((void*)(uintptr_t)cur) != NULL) { n++; cur = ref_read(cur); }
  return n;
}

/* m Refs nothing roots. */
static inline void garbage_refs(long long m) {
  for (long long i = 0; i < m; i++) ref_new(i);
}

/* A CSTR of `len` bytes, filled so HDRCHECK's strlen agrees with the header. */
static inline long long make_str(size_t len) {
  SproutStr s = sprout_str_alloc(len, "runtime_case: out of memory");
  memset(s.hdr_p, 'a', len);
  s.hdr_p[len] = '\0';
  return sprout_ptr_word(s.hdr_p);
}

#endif
