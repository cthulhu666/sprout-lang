/* The heap header kinds and the only places a pointer becomes a Sprout word
 * (docs/string-header-invariant-v0.md §4). Shared by sprout_runtime.c, sprout_scheduler.c and
 * sprout_poll.c. `just runtime-word-casts` exempts the exits below by `file:name`. */
#ifndef SPROUT_WORD_H
#define SPROUT_WORD_H

#include <stdint.h>
#include <string.h>

#include "sprout_scheduler.h"

/* Adding a kind means editing THREE places: this enum, slot_bytes (its slot
 * size) and sprout_slot_kind_known (the HDRCHECK walk's "is this a header at
 * all" test).  Miss the third and the sweep aborts on the first slot of the new
 * kind — under a flag CI sets job-wide, so the first sign is every job red. */
typedef enum {
  SPROUT_HEAP_FREE = 0,   /* slot on a class freelist; header aux = slot_bytes */
  SPROUT_HEAP_OBJ = 1,
  SPROUT_HEAP_CLOSURE = 2,
  SPROUT_HEAP_VECTOR = 3,
  SPROUT_HEAP_MAP = 4,
  SPROUT_HEAP_BYTES = 5,
  SPROUT_HEAP_BUILDER = 6,
  SPROUT_HEAP_TUPLE = 7,
  SPROUT_HEAP_REF = 9,
  SPROUT_HEAP_CSTR = 10
} SproutHeapKind;

static inline uint64_t sprout_hdr_of(void* payload) {
  uint64_t h; memcpy(&h, (char*)payload - 8, 8); return h;
}
static inline SproutHeapKind sprout_hdr_kind(uint64_t h) {
  return (SproutHeapKind)(h & 0xFF);
}

/* A Sprout String whose bytes carry an inline CSTR header at hdr_p - 8. A struct, so clang
 * rejects a char*, a literal or a void* where one is wanted. Only sprout_str_alloc,
 * sprout_str_intern and sprout_str_of_word_in construct one; the gate rejects any other
 * initializer, compound literal, write to hdr_p or pointer cast between SproutStr* and a
 * non-void pointer. */
typedef struct { char* hdr_p; } SproutStr;

/* SPROUT_GC_HDRCHECK=1 or SPROUT_GC_LINEAGE=1: -1 until sprout_gc_hdrcheck_on() first reads
 * the environment, then 0 or 1. -1 is truthy, so test through sprout_hdrcheck(), never the int. */
extern int g_sprout_hdrcheck;
int sprout_gc_hdrcheck_on(void);
static inline int sprout_hdrcheck(void) {
  int on = g_sprout_hdrcheck;
  return on < 0 ? sprout_gc_hdrcheck_on() : on;
}

/* Out of line so the exits stay a load and a branch. Defined in sprout_runtime.c. */
__attribute__((noreturn, cold, noinline)) void sprout_str_check_fail(int entering, const char* caller);

/* Whether p is NULL or has no CSTR header at p - 8. Reads the header only (O(1): no strlen). */
static inline int sprout_str_headerless(const char* p) {
  return p == NULL || sprout_hdr_kind(sprout_hdr_of((void*)p)) != SPROUT_HEAP_CSTR;
}

/* A String leaving the runtime as a Sprout value. Under HDRCHECK it must carry a CSTR header.
 * Use through sprout_str_word, which names the caller in the failure. */
static inline long long sprout_str_word_in(SproutStr s, const char* caller) {
  if (sprout_hdrcheck() && sprout_str_headerless(s.hdr_p)) sprout_str_check_fail(0, caller);
  return (long long)(uintptr_t)s.hdr_p;
}
#define sprout_str_word(s) sprout_str_word_in((s), __func__)

/* The reverse: a word that should hold a String, from a parameter typed long long. */
static inline SproutStr sprout_str_of_word_in(long long w, const char* caller) {
  SproutStr s = { (char*)(uintptr_t)w };
  if (sprout_hdrcheck() && sprout_str_headerless(s.hdr_p)) sprout_str_check_fail(1, caller);
  return s;
}
#define sprout_str_of_word(w) sprout_str_of_word_in((w), __func__)

/* A non-String pointer (object, vector, closure, map, task, channel) becoming a Sprout word. The
 * gate rejects a char pointer here: a String leaves through sprout_str_word. */
static inline long long sprout_ptr_word(const void* p) {
  return (long long)(uintptr_t)p;
}

#endif
