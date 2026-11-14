/* replay: decoder and validator for a Sprout GC allocation trace (docs/gc-stage2-v0.md §5.7).
 *
 *   clang -O2 bench/gc_place/replay.c -o replay
 *   replay --check <trace>
 *
 * Written by a runtime built with -DSPROUT_GC_ALLOCTRACE when SPROUT_GC_ALLOCTRACE=<path> is set
 * (sprout_at_* in runtime/sprout_runtime.c).  This file holds the authoritative format.
 *
 * FORMAT.  The file starts with the 6 bytes "SPAT1\n".  Then records, each a tag byte followed by
 * unsigned LEB128 varints (7 bits per byte, low group first, high bit set = more follows):
 *
 *   A kind slot_bytes      an allocation.  Its id is implicit: the number of A records before it
 *                          (0, 1, 2, ...).  kind is the SproutHeapKind value; slot_bytes is the
 *                          slot size the allocator needed (the whole malloc size for a large
 *                          object).  Every managed allocation is one A.
 *   F id                   a collection freed object id.  Only between a G and its E.
 *   T id rest_bytes        vector id's inline elements moved out of line: its slot shrank and
 *                          rest_bytes at the end of the slot were freed as a slot of their own.
 *                          At most once per id; only a VECTOR (kind 3).
 *   B id                   vector id now owns a malloc'd element buffer (it did not at its A, or
 *                          never had inline elements).  Bytes and builders own theirs from A.
 *   G cycle                a collection starts (cycle number, counting from 1).
 *   E cycle kind heap_after free_slots ordinary_regions large_regions
 *                          that collection ends.  kind: 0 minors off, 1 major, 2 minor with the
 *                          young-only sweep, 3 minor with the full sweep.  heap_after is the
 *                          live object count, free_slots the free-list length.
 *
 * The runtime flushes the trace after the atexit collection, so a complete trace ends with an E.
 *
 * Structure: decode() reads records and calls a Handlers entry per record; --check is one set of
 * handlers.  A placement policy is another set, driven by the same loop. */
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define KIND_VECTOR 3   /* SPROUT_HEAP_VECTOR in runtime/sprout_word.h */
#define LARGE_THRESHOLD 4096   /* SPROUT_LARGE_THRESHOLD: a bigger slot is a malloc block of its own */
#define READ_BUF (1 << 20)

typedef struct {
  FILE* f;
  unsigned char buf[READ_BUF];
  size_t pos, len;
  uint64_t offset;   /* bytes consumed so far */
} Reader;

typedef struct {
  int (*alloc)(void* ctx, uint64_t id, uint64_t kind, uint64_t slot_bytes);
  int (*free_)(void* ctx, uint64_t id);
  int (*tail)(void* ctx, uint64_t id, uint64_t rest_bytes);
  int (*vec_out)(void* ctx, uint64_t id);
  int (*gc_start)(void* ctx, uint64_t cycle);
  int (*gc_end)(void* ctx, uint64_t cycle, uint64_t kind, uint64_t heap_after, uint64_t free_slots,
                uint64_t ordinary, uint64_t large);
  int (*finish)(void* ctx);   /* at end of file; nonzero fails the decode */
} Handlers;

static char g_err[256];
static int g_last_tag;   /* tag of the last record decoded */

static int fail(const Reader* rd, const char* fmt, ...) __attribute__((format(printf, 2, 3)));
static int fail(const Reader* rd, const char* fmt, ...) {
  int n = snprintf(g_err, sizeof g_err, "byte %llu: ", (unsigned long long)rd->offset);
  va_list ap;
  va_start(ap, fmt);
  vsnprintf(g_err + n, sizeof g_err - (size_t)n, fmt, ap);
  va_end(ap);
  return 1;
}

static int next_byte(Reader* rd) {
  if (rd->pos == rd->len) {
    rd->len = fread(rd->buf, 1, sizeof rd->buf, rd->f);
    rd->pos = 0;
    if (rd->len == 0) return -1;
  }
  rd->offset++;
  return rd->buf[rd->pos++];
}

/* 0 on success, 1 on a truncated or overlong varint. */
static int read_varint(Reader* rd, uint64_t* out) {
  uint64_t v = 0;
  for (int shift = 0; shift < 70; shift += 7) {
    int b = next_byte(rd);
    if (b < 0) return fail(rd, "truncated varint");
    v |= (uint64_t)(b & 0x7f) << shift;
    if (!(b & 0x80)) { *out = v; return 0; }
  }
  return fail(rd, "varint longer than 10 bytes");
}

/* Decode the whole file; returns 0 or 1 with g_err set.  Ids are assigned here. */
static int decode(Reader* rd, const Handlers* h, void* ctx) {
  static const char magic[6] = { 'S', 'P', 'A', 'T', '1', '\n' };
  for (int i = 0; i < 6; i++)
    if (next_byte(rd) != (unsigned char)magic[i]) return fail(rd, "bad magic (not a SPAT1 trace)");
  uint64_t next_id = 0, v[6];
  int tag;
  while ((tag = next_byte(rd)) >= 0) {
    g_last_tag = tag;
    int n;
    switch (tag) {
      case 'A': n = 2; break;
      case 'F': case 'B': case 'G': n = 1; break;
      case 'T': n = 2; break;
      case 'E': n = 6; break;
      default: return fail(rd, "unknown record tag 0x%02x", tag);
    }
    for (int i = 0; i < n; i++)
      if (read_varint(rd, &v[i])) return 1;
    int bad = 0;
    switch (tag) {
      case 'A': bad = h->alloc(ctx, next_id++, v[0], v[1]); break;
      case 'F': bad = h->free_(ctx, v[0]); break;
      case 'T': bad = h->tail(ctx, v[0], v[1]); break;
      case 'B': bad = h->vec_out(ctx, v[0]); break;
      case 'G': bad = h->gc_start(ctx, v[0]); break;
      case 'E': bad = h->gc_end(ctx, v[0], v[1], v[2], v[3], v[4], v[5]); break;
    }
    if (bad) {   /* the handler's message, prefixed with the position */
      char msg[sizeof g_err];
      memcpy(msg, g_err, sizeof msg);
      return fail(rd, "%s", msg);
    }
  }
  return h->finish(ctx);
}

/* ── --check ─────────────────────────────────────────────────────────────────────────────── */

enum { F_FREED = 1, F_TAILED = 2 };

typedef struct {
  unsigned char* st;     /* per id: F_* flags */
  unsigned char* kind;   /* per id */
  uint64_t cap;
  uint64_t allocs, frees, tails, vec_outs, bytes, large;   /* large: slot_bytes > LARGE_THRESHOLD */
  uint64_t gcs[4];
  int in_gc;
  uint64_t gc_cycle, last_cycle;
} Check;

static int err(const char* fmt, ...) __attribute__((format(printf, 1, 2)));
static int err(const char* fmt, ...) {
  va_list ap;
  va_start(ap, fmt);
  vsnprintf(g_err, sizeof g_err, fmt, ap);
  va_end(ap);
  return 1;
}

static int chk_alloc(void* ctx, uint64_t id, uint64_t kind, uint64_t slot_bytes) {
  Check* c = ctx;
  if (id >= c->cap) {
    uint64_t ncap = c->cap ? c->cap * 2 : 1 << 16;
    c->st = realloc(c->st, ncap);
    c->kind = realloc(c->kind, ncap);
    if (c->st == NULL || c->kind == NULL) { fprintf(stderr, "replay: out of memory\n"); exit(2); }
    memset(c->st + c->cap, 0, ncap - c->cap);
    c->cap = ncap;
  }
  c->kind[id] = (unsigned char)kind;
  c->allocs++;
  c->bytes += slot_bytes;
  c->large += slot_bytes > LARGE_THRESHOLD;
  return 0;
}

/* The id must be allocated (below the A count) before any later record names it. */
static int known(const Check* c, const char* what, uint64_t id) {
  return id < c->allocs ? 0 : err("%s %llu: no such allocation (%llu so far)", what,
                                  (unsigned long long)id, (unsigned long long)c->allocs);
}

static int chk_free(void* ctx, uint64_t id) {
  Check* c = ctx;
  if (known(c, "F", id)) return 1;
  if (!c->in_gc) return err("F %llu outside a collection", (unsigned long long)id);
  if (c->st[id] & F_FREED) return err("F %llu: freed twice", (unsigned long long)id);
  c->st[id] |= F_FREED;
  c->frees++;
  return 0;
}

static int chk_tail(void* ctx, uint64_t id, uint64_t rest) {
  Check* c = ctx;
  if (known(c, "T", id)) return 1;
  if (c->st[id] & F_FREED) return err("T %llu: object already freed", (unsigned long long)id);
  if (c->kind[id] != KIND_VECTOR) return err("T %llu: kind %u is not a vector", (unsigned long long)id, c->kind[id]);
  if (c->st[id] & F_TAILED) return err("T %llu: second tail release", (unsigned long long)id);
  if (rest == 0) return err("T %llu: rest_bytes is 0", (unsigned long long)id);
  c->st[id] |= F_TAILED;
  c->tails++;
  return 0;
}

static int chk_vec_out(void* ctx, uint64_t id) {
  Check* c = ctx;
  if (known(c, "B", id)) return 1;
  if (c->st[id] & F_FREED) return err("B %llu: object already freed", (unsigned long long)id);
  if (c->kind[id] != KIND_VECTOR) return err("B %llu: kind %u is not a vector", (unsigned long long)id, c->kind[id]);
  c->vec_outs++;
  return 0;
}

static int chk_gc_start(void* ctx, uint64_t cycle) {
  Check* c = ctx;
  if (c->in_gc) return err("G %llu inside collection %llu", (unsigned long long)cycle, (unsigned long long)c->gc_cycle);
  if (cycle <= c->last_cycle) return err("G %llu: cycle does not increase (last %llu)", (unsigned long long)cycle, (unsigned long long)c->last_cycle);
  c->in_gc = 1;
  c->gc_cycle = cycle;
  return 0;
}

static int chk_gc_end(void* ctx, uint64_t cycle, uint64_t kind, uint64_t heap_after, uint64_t free_slots,
                      uint64_t ordinary, uint64_t large) {
  Check* c = ctx;
  (void)free_slots; (void)ordinary; (void)large;
  if (!c->in_gc) return err("E %llu without a G", (unsigned long long)cycle);
  if (cycle != c->gc_cycle) return err("E %llu closes collection %llu", (unsigned long long)cycle, (unsigned long long)c->gc_cycle);
  if (kind > 3) return err("E %llu: kind %llu out of range", (unsigned long long)cycle, (unsigned long long)kind);
  if (c->allocs - c->frees != heap_after)
    return err("E %llu: allocs-frees = %llu but heap_after = %llu", (unsigned long long)cycle,
               (unsigned long long)(c->allocs - c->frees), (unsigned long long)heap_after);
  c->in_gc = 0;
  c->last_cycle = cycle;
  c->gcs[kind]++;
  return 0;
}

static int chk_finish(void* ctx) {
  Check* c = ctx;
  if (c->in_gc) return err("end of file inside collection %llu (no E: the run did not finish)", (unsigned long long)c->gc_cycle);
  return 0;
}

static const Handlers check_handlers = { chk_alloc, chk_free, chk_tail, chk_vec_out, chk_gc_start, chk_gc_end, chk_finish };

int main(int argc, char** argv) {
  if (argc != 3 || strcmp(argv[1], "--check") != 0) {
    fprintf(stderr, "usage: %s --check <trace>\n", argv[0]);
    return 2;
  }
  Reader* rd = calloc(1, sizeof *rd);
  rd->f = fopen(argv[2], "rb");
  if (rd->f == NULL) { perror(argv[2]); return 2; }
  Check c;
  memset(&c, 0, sizeof c);
  if (decode(rd, &check_handlers, &c)) {
    fprintf(stderr, "replay: %s: %s\n", argv[2], g_err);
    return 1;
  }
  printf("ok: allocs=%llu frees=%llu collections=%llu (off=%llu major=%llu minor_young=%llu minor_full=%llu) "
         "tails=%llu vec_out=%llu bytes=%llu large=%llu last_cycle=%llu ends_with=%c\n",
         (unsigned long long)c.allocs, (unsigned long long)c.frees,
         (unsigned long long)(c.gcs[0] + c.gcs[1] + c.gcs[2] + c.gcs[3]),
         (unsigned long long)c.gcs[0], (unsigned long long)c.gcs[1], (unsigned long long)c.gcs[2],
         (unsigned long long)c.gcs[3], (unsigned long long)c.tails, (unsigned long long)c.vec_outs,
         (unsigned long long)c.bytes, (unsigned long long)c.large, (unsigned long long)c.last_cycle,
         g_last_tag ? g_last_tag : '-');
  return 0;
}
