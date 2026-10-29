/* Shared prelude for the C runtime tests (docs/string-header-invariant-v0.md §7 step 3):
 * extern decls for the runtime, fail, and is_str. is_str asserts a word is a headered String:
 * str_eq and str_byte_len check the header on the way in, so it only holds under
 * SPROUT_GC_HDRCHECK=1.
 */
#ifndef SPROUT_TESTS_STR_CHECK_H
#define SPROUT_TESTS_STR_CHECK_H

#include <stdint.h>
#include <stdio.h>
#include <string.h>

long long sprout_register_ctor(long long tag, const char* name, long long arity,
                               const char* field_kinds);
_Bool str_eq(const char* left, const char* right);
long long str_byte_len(long long s_val);
long long sprout_tag(long long h);
long long sprout_field(long long h, long long idx);

static int fail(const char* what) {
  fprintf(stderr, "FAIL: %s\n", what);
  return 1;
}

/* The word is a headered String of exactly `want` (NULL: any non-empty text). */
static int is_str(long long word, const char* want) {
  const char* s = (const char*)(uintptr_t)word;
  if (s == NULL) return 0;
  if (!str_eq(s, s) || str_byte_len(word) != (long long)strlen(s)) return 0;
  return want == NULL ? strlen(s) > 0 : strcmp(s, want) == 0;
}

#endif
