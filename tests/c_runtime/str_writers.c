/* The SproutStr exits and writers (docs/string-header-invariant-v0.md §4.1, §4.2, §8).
 *
 * Each selector is its own process; run.sh checks the abort cases by message. A headerless String
 * is built here from a buffer whose header word has a non-CSTR kind: the header word is set, so
 * nothing reads uninitialised memory. The runtime is included, not linked, so main can setenv
 * before the first HDRCHECK query. */
#include <stdio.h>

#include "../../runtime/sprout_runtime.c"

static uint64_t g_fake[4];

static SproutStr fake_headerless(void) {
  g_fake[0] = sprout_hdr_make(SPROUT_HEAP_BYTES, 0);
  memcpy(&g_fake[1], "abc", 4);
  SproutStr s = { (char*)&g_fake[1] };
  return s;
}

static long long leaves_headerless(SproutStr s) { return sprout_str_word(s); }
static SproutStr enters_headerless(long long w) { return sprout_str_of_word(w); }

static int writer_ok(void) {
  SproutStr d = sprout_str_dup("abc", "writer_ok");
  SproutStr i = sprout_str_intern("abc");
  SproutStr dw = enters_headerless(leaves_headerless(d));
  SproutStr iw = enters_headerless(leaves_headerless(i));
  return dw.hdr_p == d.hdr_p && iw.hdr_p == i.hdr_p && strcmp(dw.hdr_p, "abc") == 0 &&
         strcmp(iw.hdr_p, "abc") == 0;
}

int main(int argc, char** argv) {
  if (argc != 2) return 2;
  const char* sel = argv[1];
  setenv("SPROUT_GC_LINEAGE", "0", 1);
  setenv("SPROUT_GC_HDRCHECK", strcmp(sel, "headerless_off") == 0 ? "0" : "1", 1);

  if (strcmp(sel, "headerless") == 0) {
    leaves_headerless(fake_headerless());
  } else if (strcmp(sel, "headerless_off") == 0) {
    SproutStr s = fake_headerless();
    if (leaves_headerless(s) != (long long)(uintptr_t)s.hdr_p) return 1;
    printf("headerless-off-ok\n");
    return 0;
  } else if (strcmp(sel, "of_word_headerless") == 0) {
    enters_headerless((long long)(uintptr_t)fake_headerless().hdr_p);
  } else if (strcmp(sel, "writer_ok") == 0) {
    if (!writer_ok()) return 1;
    printf("writer-ok\n");
    return 0;
  } else if (strcmp(sel, "intern_null") == 0) {
    sprout_str_intern(NULL);
  } else if (strcmp(sel, "register_null") == 0) {
    sprout_str_register(NULL, "register_null");
  } else if (strcmp(sel, "dup_null") == 0) {
    sprout_str_dup(NULL, "dup_null");
  } else if (strcmp(sel, "dup_slice_null") == 0) {
    sprout_str_dup_slice(NULL, 0, "dup_slice_null");
  } else if (strcmp(sel, "adopt_null") == 0) {
    sprout_str_adopt(NULL, 0, "adopt_null");
  } else if (strcmp(sel, "register_arena") == 0) {
    SproutStr a = sprout_str_alloc(3, "register_arena");
    memcpy(a.hdr_p, "abc", 4);
    sprout_str_register(a.hdr_p, "register_arena");
  } else {
    return 2;
  }
  printf("no-abort\n");
  return 0;
}
