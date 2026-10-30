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

#endif
