/* Prints sizeof(RootNode), the per-slot cost of a GC temp-root pool.
 *
 * Exists because the per-task pool size derived from it is quoted in prose in
 * several places, and when RootNode shrank 32 -> 24 bytes every copy went
 * stale at once, with nothing able to notice. run.sh multiplies this by
 * SPROUT_TASK_ROOT_SLOTS (read from the scheduler, its own source of truth)
 * and checks the documented figure against the product, so the next size
 * change fails loudly instead of leaving confidently-wrong numbers behind.
 *
 * `RootNode` is file-static, so this includes the translation unit rather than
 * adding a runtime accessor that would exist only for a test. run.sh links it
 * in PLACE OF sprout_runtime.c, alongside the other runtime TUs. */
#include <stdio.h>

#include "../../runtime/sprout_runtime.c"

int main(void) {
  printf("%zu\n", sizeof(RootNode));
  return 0;
}
