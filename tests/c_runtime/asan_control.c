/* Positive control for the sanitizer gate (docs/string-header-invariant-v0.md §4.5).
 *
 * str_eq reads the header word at payload-8. A malloc'd buffer that is not a Sprout String has no
 * such word, so under ASan that read lands in the buffer's left redzone. Built without
 * -fsanitize=address this program exits 0; run.sh builds it sanitized only in sanitize mode and
 * requires the report, so a CI job that quietly lost the sanitizer fails here. */
#include <stdlib.h>
#include <string.h>

#include "str_check.h"

long long int_to_string(long long value);

int main(void) {
  char* bare = strdup("42");
  const char* real = (const char*)(uintptr_t)int_to_string(42);
  (void)str_eq(bare, real);
  free(bare);
  return 0;
}
