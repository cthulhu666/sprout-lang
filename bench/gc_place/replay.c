/* replay: decoder and validator for a Sprout GC allocation trace (docs/gc-stage2-v0.md §5.7).
 *
 *   clang -O2 bench/gc_place/replay.c -o replay
 *   replay --check <trace>                       validate a trace
 *   replay --policies all|<name>,... <trace>     replay it under placement policies (bench/gc_place/run.sh)
 *   replay --selftest                           the policies on tiny hand-written traces
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
 *   E cycle kind heap_after free_slots ordinary_regions large_regions walked
 *                          that collection ends.  kind: 0 minors off, 1 major, 2 minor with the
 *                          young-only sweep, 3 minor with the full sweep (GC_KIND_* below).
 *                          heap_after is the live object count, free_slots the free-list length,
 *                          walked the slots the sweep stepped over (the cycle line's walked=).
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
/* The E record's collection kinds: SPROUT_GC_KIND_* in runtime/sprout_runtime.c. */
enum { GC_KIND_OFF, GC_KIND_MAJOR, GC_KIND_MINOR_YOUNG, GC_KIND_MINOR_FULL, GC_KINDS };
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
                uint64_t ordinary, uint64_t large, uint64_t walked);
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
  uint64_t next_id = 0, v[7];
  int tag;
  while ((tag = next_byte(rd)) >= 0) {
    g_last_tag = tag;
    int n;
    switch (tag) {
      case 'A': n = 2; break;
      case 'F': case 'B': case 'G': n = 1; break;
      case 'T': n = 2; break;
      case 'E': n = 7; break;
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
      case 'E': bad = h->gc_end(ctx, v[0], v[1], v[2], v[3], v[4], v[5], v[6]); break;
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
  uint64_t gcs[GC_KINDS];
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
                      uint64_t ordinary, uint64_t large, uint64_t walked) {
  Check* c = ctx;
  (void)free_slots; (void)ordinary; (void)large; (void)walked;
  if (!c->in_gc) return err("E %llu without a G", (unsigned long long)cycle);
  if (cycle != c->gc_cycle) return err("E %llu closes collection %llu", (unsigned long long)cycle, (unsigned long long)c->gc_cycle);
  if (kind >= GC_KINDS) return err("E %llu: kind %llu out of range", (unsigned long long)cycle, (unsigned long long)kind);
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

/* ── placement simulator (docs/gc-stage2-v0.md §5.7, options C and E against today's layout) ────
 *
 * Which ids a collection frees does not depend on placement, so every policy replays the same
 * allocations and frees at the same collections; only where objects land, and so what memory is
 * held, differs.  All policies share one object table (id -> Obj) and each keeps its own placement
 * of an object in Obj.pl[its slot].  A slot_bytes above LARGE_THRESHOLD is never placed: its bytes
 * count as held while it lives, identically for every policy.
 *
 * Each policy owns an address space of fixed-size regions, indexed 0..n-1.  "Lowest address" is
 * the lowest index, and a released region's index is reused (lowest first) before the space grows.
 * (The runtime's arena hands chunks back LIFO; the order of regions, not their number, differs.)
 *
 * Memory is measured at each G, before its frees (the high-water point), and at each E:
 *   held = regions x region_size + live large bytes.
 *
 * POLICIES
 *   base      today's layout with SPROUT_GC_REGION_LISTS=1, 1 MiB regions.  Per region and class an
 *             exact-fit free slot count; class c takes from the lowest region holding one (a cursor
 *             per class, reset at every E, lowered by a tail), else bumps exactly as
 *             sprout_gc_alloc_block does: the hint region if the slot fits, else the highest region
 *             with room, else a new region (which becomes the hint).  A freed slot rejoins its
 *             region's class at the F; a T tail is a free slot of class rest/16 at once.  At an E a
 *             region with no live object is released (a young-only minor, kind 2, only one that
 *             was bumped into), and when none is left a fresh one is opened, as Pass 2 and the
 *             young sweep do.  Which slot of a class is popped within a region is not modelled.
 *   holes     1 MiB mixed-size regions (option C).  At each E the holes are the maximal runs of
 *             16 B granules covered by no live object's extent, unbumped space included.  Between
 *             collections one cursor walks the holes in address order and only moves forward: a
 *             request that fits the current hole bumps; one that does not skips to the next hole
 *             that fits, and every skipped remainder (the current hole's and each passed hole's) is
 *             wasted until the next E.  Past the last hole a new region opens as one big hole.
 *             T shrinks the extent; the tail is a hole only at the next E.  An empty region is
 *             released at every E, none is reopened.
 *   holes-ovf as holes, but a request above OVF_THRESHOLD bytes that does not fit the current hole
 *             goes to an overflow cursor instead of moving the main one.  It walks the holes ahead
 *             of the main cursor (never behind its own last position), takes the first that fits,
 *             and otherwise opens a fresh region appended to the holes.  Holes it passes are not
 *             wasted: only the main cursor's skips count.  Both cursors bump the same hole.
 *   class-R   single-class regions of R bytes (option E): floor(R / slot_bytes) slots of one class,
 *             the remainder is internal waste.  Class c takes a slot in the lowest region of class
 *             c with one (cursor per class, reset at every E), else opens a region of class c.  T
 *             does not shrink the object: the tail is held dead until the object is freed.  An
 *             empty region is released at every E.  With R < 1 MiB the R-regions are also carved
 *             from 1 MiB chunks (the runtime's arena unit) and "chunked" memory is counted: a new
 *             region takes the lowest free R-slot of the lowest chunk with one, else the lowest
 *             released chunk index, else a new chunk; a chunk is released at the E that empties
 *             its last R-region.  held(chunked) = chunks x 1 MiB + large.
 *
 * SWEEP WORK (not time).  C and E both sweep by bitmap, one word at a time; the number of words is
 * reported per E kind, for ONE bitmap, beside the number of bitmaps a region has.  C: 1 MiB / 16 /
 * 64 = 1024 words per bitmap, 4 bitmaps (occupancy old and mark, slotmap, youngmap).  E: ceil
 * (slots / 64) words per bitmap, 4 bitmaps (alloc, mark, old, young).  Kinds 0, 1 and 3 (full
 * sweeps) sweep every region of the policy, kind 2 only those allocated into since the last E.
 * Holes per E are counted too and are NOT a sweep cost: the bitmap-driven walk never writes them.
 *
 * ALLOCATION-SIDE WORK, counted between the same events the runtime pays for them.
 *   base      regions the per-class cursor tests (as fl_region_pop and fl_region_pop_slow do: the
 *             cursor's region, then each later one), and the bump path's hint test and downward
 *             region scan.  Large regions sit in the runtime's table too and are not modelled, so
 *             the counts are a lower bound.
 *   holes     holes a cursor examines (a cursor pays for a hole when it first reaches it; the main
 *             and overflow cursors pay separately), the occupancy-bitmap words read to find each
 *             (words from the previous hole's last word through this one's last word, plus the
 *             rest of any region passed; a word shared by two holes is read twice; a freshly
 *             opened region's one big hole costs nothing), holes entered (received an allocation),
 *             holes the main cursor skipped with room left, holes the overflow cursor passed.
 *   class-R   regions the class cursor tests, placements that land in a region other than the
 *             class's previous one (the first of each interval counts), and alloc-bitmap words
 *             read to find the lowest free slot: a hint word per region (lowest word that may hold
 *             a free bit, lowered by a free), each word read from the hint to the one with a free
 *             bit counting once.
 *
 * FLOOR INDICATOR.  sprout_gc_collect_with_reason raises the next collection's target to
 * heap + (heap + free) / 3 when floor_on: kinds 0 and 1, and kind 3 (with region lists, a minor
 * takes the full sweep exactly when the next collection is a major, so floor_on holds), not
 * kind 2.  The target is a lower bound on the collection point, and a collection fires when the
 * heap count reaches max(target, floor).  A replay cannot move collections earlier, so for each
 * such E the floor is recomputed from the policy's own free measure and compared with the
 * heap count at the next G in the trace (heap_after + the allocations between): a floor above
 * it means that collection would have come later, by the difference.  Intervals whose trace
 * floor already exceeds the next G's heap (a collection that was not the threshold's) are left out. */

#define REGION_BYTES (1u << 20)
#define REGION_GRANULES (REGION_BYTES / 16)
#define BM_WORDS (REGION_GRANULES / 64)
#define MAX_POL 8
#define NCLS 257   /* class = slot_bytes / 16, 1..256 */
#define OVF_THRESHOLD 256
#define HOLES_BITMAPS 4   /* occupancy old and mark, slotmap, youngmap */
#define CLASS_BITMAPS 4   /* alloc, mark, old, young */
#define KIND_BYTES 5
#define KIND_BUILDER 6
#define NO_OBJ UINT32_MAX

enum { O_OWNER = 1, O_LARGE = 2, O_TAILED = 4, O_DEAD = 8 };

/* g_gen.promote_age = 3: a survivor of a keep-colour sweep stays young while its age before the
 * sweep is below PROMOTE_AGE - 1 (sprout_gc_stays_young); every survivor's age is bumped. */
#define PROMOTE_AGE 3
#define YOUNG_BELOW (PROMOTE_AGE - 1)
#define AGE_MAX 15
enum { PK_BASE, PK_HOLES, PK_CLASS };

static void* xcalloc(size_t n, size_t sz) {
  void* p = calloc(n, sz);
  if (p == NULL) { fprintf(stderr, "replay: out of memory\n"); exit(2); }
  return p;
}

static void* xrealloc(void* p, size_t n) {
  p = realloc(p, n);
  if (p == NULL) { fprintf(stderr, "replay: out of memory\n"); exit(2); }
  return p;
}

typedef struct { uint64_t n, max; double sum; } Stat;
static void stat_add(Stat* s, uint64_t v) { s->n++; s->sum += (double)v; if (v > s->max) s->max = v; }
static double stat_mean(const Stat* s) { return s->n ? s->sum / (double)s->n : 0; }

typedef struct { uint32_t region, off; } Place;   /* off: granule offset in the region (holes) */

typedef struct {
  uint64_t id;
  uint32_t size;   /* slot bytes now: a T takes the tail off */
  uint32_t tail;   /* bytes a T freed from this object */
  uint8_t kind, flags;
  uint8_t age, young;   /* collections survived (saturating at 15); young: not yet promoted */
  uint32_t ypos;        /* index in Sim.yl while young */
  Place pl[MAX_POL];
} Obj;

typedef struct { uint64_t n, moved, delay_max; double delay_sum; } FloorStat;

typedef struct Policy Policy;
struct Policy {
  const char* name;
  int type, slot;
  uint64_t region_bytes;
  void (*alloc)(Policy*, Obj*);
  void (*free_)(Policy*, Obj*);
  void (*tail)(Policy*, Obj*, uint32_t rest);   /* before o->size shrinks */
  void (*gc_end)(Policy*, uint64_t kind);
  uint64_t (*regions)(const Policy*);
  uint64_t (*chunks)(const Policy*);   /* NULL, or the 1 MiB chunks the regions are carved from */
  uint64_t (*free_units)(const Policy*);   /* free slots (base, class-R) or granules (holes), after an E */
  void (*snap)(Policy*);   /* at a new G peak */
  /* an ordinary object enters (+1) or leaves (-1) the young set; in_free: a free during a collection */
  void (*young_adj)(Policy*, const Obj*, int delta, int in_free);
  uint64_t g_n, g_peak, peak_regions, pk_regs;   /* pk_regs: regions at the peak G */
  uint64_t g_regs_max;   /* most ordinary regions at any G */
  double g_sum, e_sum;
  uint64_t e_n;
  uint64_t gk_peak, peak_chunks;   /* chunked memory, when chunks != NULL */
  double gk_sum, ek_sum;
  uint64_t allocs;   /* ordinary allocations placed */
  uint64_t examined, words, switches;   /* allocation side (see the SWEEP/ALLOCATION notes above) */
  uint64_t iv_examined0, iv_words0;
  Stat iv_ex, iv_w;   /* per collection interval */
  uint32_t nbitmaps;   /* bitmaps a region has to sweep (C and E) */
  Stat sw_words[GC_KINDS], sw_regions[GC_KINDS];   /* per E kind: words of ONE bitmap, regions swept */
  Stat sw_words_old[GC_KINDS], sw_regions_old[GC_KINDS];   /* the same with kind 2 on touched regions only (the earlier model) */
  Stat eager_words[GC_KINDS];   /* holes: words an eager hole scan over every used region reads, per E kind */
  FloorStat fl[2];
};

/* ── id -> pool index: open addressing, linear probing, backward-shift delete ── */
typedef struct { uint64_t* k; uint32_t* v; size_t cap, cnt; } Map;
#define MAP_EMPTY UINT64_MAX

static size_t map_hash(uint64_t id, size_t mask) { return (size_t)((id * 0x9E3779B97F4A7C15ull) >> 32) & mask; }

static void map_put(Map* m, uint64_t id, uint32_t v) {
  if ((m->cnt + 1) * 2 > m->cap) {
    Map n;
    n.cap = m->cap ? m->cap * 2 : 1 << 16;
    n.cnt = 0;
    n.k = xcalloc(n.cap, sizeof(uint64_t));
    n.v = xcalloc(n.cap, sizeof(uint32_t));
    for (size_t i = 0; i < n.cap; i++) n.k[i] = MAP_EMPTY;
    for (size_t i = 0; i < m->cap; i++)
      if (m->k[i] != MAP_EMPTY) {
        size_t j = map_hash(m->k[i], n.cap - 1);
        while (n.k[j] != MAP_EMPTY) j = (j + 1) & (n.cap - 1);
        n.k[j] = m->k[i];
        n.v[j] = m->v[i];
      }
    free(m->k);
    free(m->v);
    n.cnt = m->cnt;
    *m = n;
  }
  size_t j = map_hash(id, m->cap - 1);
  while (m->k[j] != MAP_EMPTY) j = (j + 1) & (m->cap - 1);
  m->k[j] = id;
  m->v[j] = v;
  m->cnt++;
}

static uint32_t map_get(const Map* m, uint64_t id) {
  if (m->cap == 0) return NO_OBJ;
  size_t mask = m->cap - 1, j = map_hash(id, mask);
  while (m->k[j] != MAP_EMPTY) {
    if (m->k[j] == id) return m->v[j];
    j = (j + 1) & mask;
  }
  return NO_OBJ;
}

static void map_del(Map* m, uint64_t id) {
  size_t mask = m->cap - 1, i = map_hash(id, mask);
  while (m->k[i] != id) i = (i + 1) & mask;
  size_t j = i;
  for (;;) {
    j = (j + 1) & mask;
    if (m->k[j] == MAP_EMPTY) break;
    size_t h = map_hash(m->k[j], mask);
    if (i <= j ? (i < h && h <= j) : (i < h || h <= j)) continue;
    m->k[i] = m->k[j];
    m->v[i] = m->v[j];
    i = j;
  }
  m->k[i] = MAP_EMPTY;
  m->cnt--;
}

typedef struct {
  Map map;
  Obj* pool;
  uint32_t pool_cap, pool_used, pool_free;
  Policy* pols[MAX_POL];
  int npol;
  uint64_t allocs, frees, large_allocs, large_live, large_cnt, large_peak, live_objs;
  uint64_t live_bytes;   /* ordinary slot bytes of live objects */
  uint64_t owners, collections, ecount[GC_KINDS];
  Stat owners_e, live_e;   /* at each E */
  /* base calibration against the trace's own E records */
  int have_base;
  Stat cal_base, cal_trace;
  uint64_t cal_maxdiff, cal_large_miss, cal_n;
  uint64_t cal_free_maxdiff, cal_free_miss, cal_free_n;   /* base's free slots against the E's free_slots */
  /* floor indicator: set at an E where the footprint floor applies, settled at the next G */
  int fl_pending;
  uint64_t fl_heap_after, fl_trace_floor;   /* the trace's own floor at that E, UINT64_MAX if unknown */
  uint64_t fl_val[MAX_POL][2];
  uint64_t fl_applying, fl_skipped;   /* intervals counted, and left out because the trace's floor was already above */
  Stat fl_interval;   /* allocations between the E and the next G, intervals counted */
  Stat walked_k[GC_KINDS];   /* today's walked= slots per E kind, from the E records */
  uint32_t* yl;       /* pool indices of the live young ordinary objects */
  size_t ny, ycap;
} Sim;

static int g_in_gc;   /* between a G and its E: frees are the collection's sweep */

static Obj* pool_new(Sim* s, uint32_t* idx) {
  uint32_t i;
  if (s->pool_free != NO_OBJ) {
    i = s->pool_free;
    s->pool_free = s->pool[i].pl[0].region;
  } else {
    if (s->pool_used == s->pool_cap) {
      s->pool_cap = s->pool_cap ? s->pool_cap * 2 : 1 << 16;
      s->pool = xrealloc(s->pool, (size_t)s->pool_cap * sizeof(Obj));
    }
    i = s->pool_used++;
  }
  *idx = i;
  memset(&s->pool[i], 0, sizeof(Obj));
  return &s->pool[i];
}

static FILE* g_perg;   /* REPLAY_PERG=<path>: one "policy cycle regions" line per G */

static void measure(Sim* s, Policy* p, int at_g) {
  uint64_t regs = p->regions(p), held = regs * p->region_bytes + s->large_live;
  if (at_g && regs > p->g_regs_max) p->g_regs_max = regs;
  if (g_perg != NULL)   /* a G line is "policy cycle regions", an E line "E policy cycle regions" */
    fprintf(g_perg, at_g ? "%s %llu %llu\n" : "E %s %llu %llu\n", p->name, (unsigned long long)(at_g ? p->g_n : p->e_n) + 1, (unsigned long long)regs);
  if (regs > p->peak_regions) p->peak_regions = regs;
  uint64_t cks = p->chunks ? p->chunks(p) : 0, heldk = cks * REGION_BYTES + s->large_live;
  if (cks > p->peak_chunks) p->peak_chunks = cks;
  if (at_g) {
    p->g_n++;
    p->g_sum += (double)held;
    p->gk_sum += (double)heldk;
    if (p->chunks && heldk > p->gk_peak) p->gk_peak = heldk;
    if (held > p->g_peak) { p->g_peak = held; p->pk_regs = regs; if (p->snap) p->snap(p); }
  } else {
    p->e_n++;
    p->e_sum += (double)held;
    p->ek_sum += (double)heldk;
  }
}

/* Moves an ordinary object into or out of the young set, and tells every policy. */
static void young_set(Sim* s, uint32_t idx, int young, int in_free) {
  Obj* o = &s->pool[idx];
  if (o->young == young) return;
  if (young) {
    if (s->ny == s->ycap) {
      s->ycap = s->ycap ? s->ycap * 2 : 1 << 16;
      s->yl = xrealloc(s->yl, s->ycap * sizeof(uint32_t));
    }
    o->ypos = (uint32_t)s->ny;
    s->yl[s->ny++] = idx;
  } else {
    uint32_t last = s->yl[--s->ny];
    s->yl[o->ypos] = last;
    s->pool[last].ypos = o->ypos;
  }
  o->young = (uint8_t)young;
  for (int i = 0; i < s->npol; i++)
    if (s->pols[i]->young_adj) s->pols[i]->young_adj(s->pols[i], o, young ? 1 : -1, in_free);
}

/* After the policies' gc_end (which read the young set as the sweep found it): ages and promotion.
 * Kind 2 sweeps the young set and keeps colour.  Kind 1 (a major followed by a minor) marks
 * everything and sweeps with keep-colour, so each survivor's young state is recomputed from its age.
 * Kinds 0 and 3 clear colour (no generations / the next collection is a major): nothing stays young. */
static void sim_age(Sim* s, uint64_t kind) {
  if (kind == GC_KIND_MINOR_YOUNG) {
    for (size_t i = s->ny; i-- > 0;) {
      uint32_t idx = s->yl[i];
      Obj* o = &s->pool[idx];
      int stays = o->age < YOUNG_BELOW;
      if (o->age < AGE_MAX) o->age++;
      if (!stays) young_set(s, idx, 0, 0);
    }
    return;
  }
  for (uint32_t idx = 0; idx < s->pool_used; idx++) {
    Obj* o = &s->pool[idx];
    if (o->flags & (O_DEAD | O_LARGE)) continue;
    int stays = kind == GC_KIND_MAJOR && o->age < YOUNG_BELOW;
    if (o->age < AGE_MAX) o->age++;
    young_set(s, idx, stays, 0);
  }
}

static int sim_alloc(Sim* s, uint64_t id, uint64_t kind, uint64_t slot_bytes) {
  if (slot_bytes == 0 || slot_bytes % 16 != 0) return err("A %llu: slot_bytes %llu is not a positive multiple of 16", (unsigned long long)id, (unsigned long long)slot_bytes);
  uint32_t idx;
  Obj* o = pool_new(s, &idx);
  o->id = id;
  o->size = (uint32_t)slot_bytes;
  o->kind = (uint8_t)kind;
  s->allocs++;
  s->live_objs++;
  if (kind == KIND_BYTES || kind == KIND_BUILDER) { o->flags |= O_OWNER; s->owners++; }
  if (slot_bytes > LARGE_THRESHOLD) {
    o->flags |= O_LARGE;
    s->large_allocs++;
    s->large_cnt++;
    s->large_live += slot_bytes;
    if (s->large_live > s->large_peak) s->large_peak = s->large_live;
  } else {
    s->live_bytes += slot_bytes;
    for (int i = 0; i < s->npol; i++) { s->pols[i]->allocs++; s->pols[i]->alloc(s->pols[i], o); }
    young_set(s, idx, 1, 0);
  }
  map_put(&s->map, id, idx);
  return 0;
}

static int sim_free(Sim* s, uint64_t id) {
  uint32_t idx = map_get(&s->map, id);
  if (idx == NO_OBJ) return err("F %llu: not a live object", (unsigned long long)id);
  Obj* o = &s->pool[idx];
  if (o->flags & O_OWNER) s->owners--;
  if (o->flags & O_LARGE) {
    s->large_live -= o->size;
    s->large_cnt--;
  } else {
    s->live_bytes -= o->size;
    young_set(s, idx, 0, g_in_gc);
    for (int i = 0; i < s->npol; i++) s->pols[i]->free_(s->pols[i], o);
  }
  o->flags |= O_DEAD;
  s->frees++;
  s->live_objs--;
  map_del(&s->map, id);
  o->pl[0].region = s->pool_free;
  s->pool_free = idx;
  return 0;
}

static int sim_tail(Sim* s, uint64_t id, uint64_t rest) {
  uint32_t idx = map_get(&s->map, id);
  if (idx == NO_OBJ) return err("T %llu: not a live object", (unsigned long long)id);
  Obj* o = &s->pool[idx];
  if ((o->flags & (O_LARGE | O_TAILED)) || rest == 0 || rest >= o->size || rest % 16 != 0)
    return err("T %llu: rest %llu does not fit an ordinary untailed object of %u bytes", (unsigned long long)id, (unsigned long long)rest, o->size);
  for (int i = 0; i < s->npol; i++) s->pols[i]->tail(s->pols[i], o, (uint32_t)rest);
  o->flags |= O_TAILED;
  o->tail = (uint32_t)rest;
  o->size -= (uint32_t)rest;
  s->live_bytes -= rest;
  return 0;
}

static int sim_vec_out(Sim* s, uint64_t id) {
  uint32_t idx = map_get(&s->map, id);
  if (idx == NO_OBJ) return err("B %llu: not a live object", (unsigned long long)id);
  Obj* o = &s->pool[idx];
  if (o->kind == KIND_VECTOR && !(o->flags & O_OWNER)) { o->flags |= O_OWNER; s->owners++; }
  return 0;
}

/* The footprint floor of sprout_gc_collect_with_reason for a heap and a free measure. */
static uint64_t floor_of(uint64_t heap, uint64_t free_units) { return heap + (heap + free_units) / 3; }

/* A policy's floor variants at an E: [0] in its own unit (free slots; granules for holes), [1] for
 * holes only, the granules divided by the mean live slot size in granules.  Returns the variant count. */
static int floor_vals(const Sim* s, const Policy* p, uint64_t heap, uint64_t* out) {
  uint64_t fu = p->free_units(p);
  out[0] = floor_of(heap, fu);
  if (p->type != PK_HOLES) return 1;
  uint64_t objs = s->live_objs - s->large_cnt, gran = s->live_bytes / 16;
  out[1] = floor_of(heap, gran ? (uint64_t)((double)fu * (double)objs / (double)gran) : fu);
  return 2;
}

static int sim_gc_start(Sim* s) {
  if (s->fl_pending) {
    uint64_t heap_g = s->live_objs;
    s->fl_pending = 0;
    if (s->fl_trace_floor != UINT64_MAX && s->fl_trace_floor > heap_g) {
      s->fl_skipped++;
    } else {
      s->fl_applying++;
      stat_add(&s->fl_interval, heap_g - s->fl_heap_after);
      for (int i = 0; i < s->npol; i++) {
        int nv = s->pols[i]->type == PK_HOLES ? 2 : 1;
        for (int v = 0; v < nv; v++) {
          FloorStat* f = &s->pols[i]->fl[v];
          f->n++;
          if (s->fl_val[i][v] > heap_g) {
            uint64_t d = s->fl_val[i][v] - heap_g;
            f->moved++;
            f->delay_sum += (double)d;
            if (d > f->delay_max) f->delay_max = d;
          }
        }
      }
    }
  }
  for (int i = 0; i < s->npol; i++) measure(s, s->pols[i], 1);
  g_in_gc = 1;
  return 0;
}

/* ordinary and large are the trace's own region counts at this E, free_slots its free-list length;
 * UINT64_MAX skips the comparison, and for walked it leaves the kind's walked row out. */
static int sim_gc_end(Sim* s, uint64_t kind, uint64_t heap_after, uint64_t ordinary, uint64_t large, uint64_t free_slots,
                      uint64_t walked) {
  if (heap_after != UINT64_MAX && heap_after != s->live_objs)
    return err("E: %llu live objects replayed, trace says %llu", (unsigned long long)s->live_objs, (unsigned long long)heap_after);
  s->collections++;
  if (kind < GC_KINDS) s->ecount[kind]++;
  if (kind < GC_KINDS && walked != UINT64_MAX) stat_add(&s->walked_k[kind], walked);
  for (int i = 0; i < s->npol; i++) {
    Policy* p = s->pols[i];
    p->gc_end(p, kind);
    measure(s, p, 0);
    stat_add(&p->iv_ex, p->examined - p->iv_examined0);
    stat_add(&p->iv_w, p->words - p->iv_words0);
    p->iv_examined0 = p->examined;
    p->iv_words0 = p->words;
    if (p->type == PK_BASE && ordinary != UINT64_MAX) {
      uint64_t b = p->regions(p), d = b > ordinary ? b - ordinary : ordinary - b;
      stat_add(&s->cal_base, b);
      stat_add(&s->cal_trace, ordinary);
      if (d > s->cal_maxdiff) s->cal_maxdiff = d;
      if (d) s->cal_n++;
    }
    if (p->type == PK_BASE && free_slots != UINT64_MAX) {
      uint64_t b = p->free_units(p), d = b > free_slots ? b - free_slots : free_slots - b;
      if (d > s->cal_free_maxdiff) s->cal_free_maxdiff = d;
      if (d) s->cal_free_miss++;
      s->cal_free_n++;
    }
  }
  sim_age(s, kind);
  g_in_gc = 0;
  if (ordinary != UINT64_MAX && large != s->large_cnt) s->cal_large_miss++;
  stat_add(&s->owners_e, s->owners);
  stat_add(&s->live_e, s->live_bytes + s->large_live);
  if (kind == GC_KIND_OFF || kind == GC_KIND_MAJOR || kind == GC_KIND_MINOR_FULL) {
    s->fl_pending = 1;
    s->fl_heap_after = s->live_objs;
    s->fl_trace_floor = free_slots == UINT64_MAX ? UINT64_MAX : floor_of(s->live_objs, free_slots);
    for (int i = 0; i < s->npol; i++) floor_vals(s, s->pols[i], s->live_objs, s->fl_val[i]);
  }
  return 0;
}

/* ── base ───────────────────────────────────────────────────────────────────────────────── */
typedef struct { uint32_t bump, live; uint32_t* fc; uint8_t used; } BReg;   /* fc[class]: free slots */
typedef struct {
  Policy p;
  BReg* r;
  uint32_t n, cap, nused;
  int64_t hint;
  uint32_t cursor[NCLS];
  uint64_t free;   /* free slots over the used regions: the runtime's g_gc_freelist_len */
  uint64_t bump_allocs, hint_tests, scans, scan_regions;   /* the bump path: allocations, hint tests, downward scans and regions they test */
} Base;

static uint32_t base_open(Base* b) {
  uint32_t i = 0;
  while (i < b->n && b->r[i].used) i++;
  if (i == b->n) {
    if (b->n == b->cap) {
      b->cap = b->cap ? b->cap * 2 : 16;
      b->r = xrealloc(b->r, (size_t)b->cap * sizeof(BReg));
    }
    memset(&b->r[b->n], 0, sizeof(BReg));
    b->n++;
  }
  BReg* r = &b->r[i];
  if (r->fc == NULL) r->fc = xcalloc(NCLS, sizeof(uint32_t)); else memset(r->fc, 0, NCLS * sizeof(uint32_t));
  r->bump = r->live = 0;
  r->used = 1;
  b->nused++;
  b->hint = i;
  return i;
}

static void base_alloc(Policy* p, Obj* o) {
  Base* b = (Base*)p;
  uint32_t cls = o->size / 16, sz = o->size;
  for (uint32_t* c = &b->cursor[cls]; *c < b->n; (*c)++) {
    BReg* r = &b->r[*c];
    if (!r->used) continue;   /* a released region is not in the runtime's table */
    p->examined++;
    if (r->fc[cls] > 0) {
      r->fc[cls]--;
      b->free--;
      r->live++;
      o->pl[p->slot].region = *c;
      return;
    }
  }
  b->bump_allocs++;
  int64_t ri = -1;
  if (b->hint >= 0 && (uint32_t)b->hint < b->n) {
    const BReg* r = &b->r[b->hint];
    b->hint_tests++;
    if (r->used && r->bump + sz <= REGION_BYTES) ri = b->hint;
  }
  if (ri < 0) {
    if (b->n > 0) b->scans++;
    for (uint32_t i = b->n; i > 0; i--) {
      const BReg* r = &b->r[i - 1];
      if (!r->used) continue;
      b->scan_regions++;
      if (r->bump + sz <= REGION_BYTES) { ri = i - 1; b->hint = ri; break; }
    }
  }
  if (ri < 0) ri = base_open(b);
  b->r[ri].bump += sz;
  b->r[ri].live++;
  o->pl[p->slot].region = (uint32_t)ri;
}

static void base_free(Policy* p, Obj* o) {
  Base* b = (Base*)p;
  BReg* r = &b->r[o->pl[p->slot].region];
  r->fc[o->size / 16]++;
  b->free++;
  r->live--;
}

static void base_tail(Policy* p, Obj* o, uint32_t rest) {
  Base* b = (Base*)p;
  uint32_t ri = o->pl[p->slot].region, cls = rest / 16;
  b->r[ri].fc[cls]++;
  b->free++;
  if (b->cursor[cls] > ri) b->cursor[cls] = ri;
}

static void base_gc_end(Policy* p, uint64_t kind) {
  Base* b = (Base*)p;
  int full = kind != GC_KIND_MINOR_YOUNG;
  uint32_t released = 0;
  for (uint32_t i = 0; i < b->n; i++) {
    BReg* r = &b->r[i];
    if (r->used && r->live == 0 && (full || r->bump > 0)) {
      for (uint32_t c = 1; c < NCLS; c++) b->free -= r->fc[c];
      r->used = 0;
      b->nused--;
      released++;
      if (b->hint == (int64_t)i) b->hint = -1;
    }
  }
  if (b->nused == 0 && (full || released)) base_open(b);
  memset(b->cursor, 0, sizeof b->cursor);
}

static uint64_t base_regions(const Policy* p) { return ((const Base*)p)->nused; }
static uint64_t base_free_units(const Policy* p) { return ((const Base*)p)->free; }

/* ── holes (option C) ─────────────────────────────────────────────────────────────────────── */
/* What a sweep reads of a region, the same for holes and class-R (first member of HReg and CReg). */
typedef struct {
  uint32_t young;   /* live young objects */
  uint32_t nw;      /* bitmap words one sweep of the region reads */
  uint8_t used, touched, yfree;   /* yfree: this collection freed a young object here */
} RegSweep;

static void reg_young_adj(RegSweep* r, int delta, int in_free) {
  r->young += (uint32_t)delta;
  if (delta < 0 && in_free) r->yfree = 1;
}

/* The sweep work of one E: a young-only minor sweeps the regions allocated into since the last E
 * or holding or freeing a young object, any other kind every region.  The `_old` rows count
 * the allocated-into regions only (the earlier model).  Regions are `stride` bytes apart. */
static void reg_sweep_stats(Policy* p, const RegSweep* regs, size_t stride, uint32_t n, uint64_t kind) {
  uint64_t swept = 0, words = 0, swept_old = 0, words_old = 0;
  for (uint32_t i = 0; i < n; i++) {
    const RegSweep* r = (const RegSweep*)((const char*)regs + (size_t)i * stride);
    if (!r->used) continue;
    if (kind != GC_KIND_MINOR_YOUNG || r->touched || r->young > 0 || r->yfree) { swept++; words += r->nw; }
    if (kind != GC_KIND_MINOR_YOUNG || r->touched) { swept_old++; words_old += r->nw; }
  }
  stat_add(&p->sw_regions[kind], swept);
  stat_add(&p->sw_words[kind], words);
  stat_add(&p->sw_regions_old[kind], swept_old);
  stat_add(&p->sw_words_old[kind], words_old);
}

typedef struct { RegSweep sw; uint64_t* bm; uint32_t live; } HReg;   /* bm: live granules */
typedef struct {
  uint32_t reg, cur, end;   /* granule offsets: [cur, end) is free */
  uint32_t words;           /* occupancy-bitmap words a cursor reads to find this hole */
  uint8_t entered;
} Hole;
typedef struct {
  Policy p;
  int ovf, tails;
  uint64_t tail_hits;
  uint64_t* tl[NCLS];   /* tails: per class a min-heap of (region << 32 | granule offset) of freed inline tails */
  uint32_t ntl[NCLS], capl[NCLS];
  HReg* r;
  uint32_t n, cap, nused;
  Hole* h;
  size_t nh, hcap, mainc, ovfc;
  size_t last_ex[2];   /* hole each cursor last examined (main, overflow); SIZE_MAX = none yet */
  uint64_t waste_interval, waste_total, waste_peak, skipped, passed, entered;
  uint64_t free_g;     /* free granules in the holes at the last E */  Stat holes_all, holes_young, live_young;   /* per E: holes found, informational only */
} Holes;

static void bm_range(uint64_t* bm, uint32_t s, uint32_t len, int set) {
  uint32_t e = s + len;
  while (s < e) {
    uint32_t w = s >> 6, b = s & 63, n = 64 - b;
    if (n > e - s) n = e - s;
    uint64_t mask = (n == 64 ? ~0ull : ((1ull << n) - 1)) << b;
    if (set) bm[w] |= mask; else bm[w] &= ~mask;
    s += n;
  }
}

static void holes_push(Holes* h, uint32_t reg, uint32_t cur, uint32_t end, uint32_t words) {
  if (h->nh == h->hcap) {
    h->hcap = h->hcap ? h->hcap * 2 : 1024;
    h->h = xrealloc(h->h, h->hcap * sizeof(Hole));
  }
  h->h[h->nh] = (Hole){ .reg = reg, .cur = cur, .end = end, .words = words, .entered = 0 };
  h->nh++;
}

/* A new region, appended to the holes as one big hole whose extent is known without a scan. */
static void holes_open(Holes* h) {
  uint32_t i = 0;
  while (i < h->n && h->r[i].sw.used) i++;
  if (i == h->n) {
    if (h->n == h->cap) {
      h->cap = h->cap ? h->cap * 2 : 16;
      h->r = xrealloc(h->r, (size_t)h->cap * sizeof(HReg));
    }
    memset(&h->r[h->n], 0, sizeof(HReg));
    h->n++;
  }
  HReg* r = &h->r[i];
  r->bm = xcalloc(BM_WORDS, sizeof(uint64_t));
  r->live = 0;
  r->sw = (RegSweep){ .nw = BM_WORDS, .used = 1 };
  h->nused++;
  holes_push(h, i, 0, REGION_GRANULES, 0);
}

/* A cursor reaches hole j: it pays for the words that find it, once per arrival. */
static void holes_examine(Holes* h, int cursor, size_t j) {
  if (h->last_ex[cursor] == j) return;
  h->last_ex[cursor] = j;
  h->p.examined++;
  h->p.words += h->h[j].words;
}

static void holes_place(Holes* h, Hole* ho, Obj* o, uint32_t g) {
  HReg* r = &h->r[ho->reg];
  o->pl[h->p.slot].region = ho->reg;
  o->pl[h->p.slot].off = ho->cur;
  bm_range(r->bm, ho->cur, g, 1);
  ho->cur += g;
  r->live++;
  r->sw.touched = 1;
  if (!ho->entered) { ho->entered = 1; h->entered++; }
}

static void holes_skip(Holes* h, size_t j) {
  uint32_t rem = h->h[j].end - h->h[j].cur;
  if (rem > 0) {
    h->waste_interval += (uint64_t)rem * 16;
    h->waste_total += (uint64_t)rem * 16;
    h->skipped++;
    h->h[j].cur = h->h[j].end;
  }
}

static void tl_push(Holes* h, uint32_t cls, uint64_t key) {
  if (h->ntl[cls] == h->capl[cls]) {
    h->capl[cls] = h->capl[cls] ? h->capl[cls] * 2 : 8;
    h->tl[cls] = xrealloc(h->tl[cls], (size_t)h->capl[cls] * sizeof(uint64_t));
  }
  uint64_t* a = h->tl[cls];
  uint32_t i = h->ntl[cls]++;
  while (i > 0 && a[(i - 1) / 2] > key) { a[i] = a[(i - 1) / 2]; i = (i - 1) / 2; }
  a[i] = key;
}

static uint64_t tl_pop(Holes* h, uint32_t cls) {
  uint64_t* a = h->tl[cls];
  uint64_t top = a[0], key = a[--h->ntl[cls]];
  uint32_t n = h->ntl[cls], i = 0;
  while (2 * i + 1 < n) {
    uint32_t c = 2 * i + 1;
    if (c + 1 < n && a[c + 1] < a[c]) c++;
    if (a[c] >= key) break;
    a[i] = a[c];
    i = c;
  }
  if (n > 0) a[i] = key;
  return top;
}

static void holes_alloc(Policy* p, Obj* o) {
  Holes* h = (Holes*)p;
  uint32_t g = o->size / 16;
  if (h->tails && h->ntl[g] > 0) {   /* an exact-class freed tail, lowest region first */
    uint64_t key = tl_pop(h, g);
    HReg* r = &h->r[key >> 32];
    o->pl[h->p.slot].region = (uint32_t)(key >> 32);
    o->pl[h->p.slot].off = (uint32_t)key;
    bm_range(r->bm, (uint32_t)key, g, 1);
    r->live++;
    r->sw.touched = 1;
    h->tail_hits++;
    return;
  }
  if (h->mainc < h->nh) holes_examine(h, 0, h->mainc);
  if (h->mainc < h->nh && h->h[h->mainc].end - h->h[h->mainc].cur >= g) {
    holes_place(h, &h->h[h->mainc], o, g);
    return;
  }
  if (h->ovf && o->size > OVF_THRESHOLD && h->mainc < h->nh) {
    size_t j = h->ovfc > h->mainc ? h->ovfc : h->mainc + 1;
    while (j < h->nh) {
      holes_examine(h, 1, j);
      if (h->h[j].end - h->h[j].cur >= g) break;
      h->passed++;
      j++;
    }
    if (j >= h->nh) { holes_open(h); j = h->nh - 1; holes_examine(h, 1, j); }
    h->ovfc = j;
    holes_place(h, &h->h[j], o, g);
    return;
  }
  size_t j = h->mainc;
  if (j < h->nh) { holes_skip(h, j); j++; }
  while (j < h->nh) {
    holes_examine(h, 0, j);
    if (h->h[j].end - h->h[j].cur >= g) break;
    holes_skip(h, j);
    j++;
  }
  if (j >= h->nh) { holes_open(h); j = h->nh - 1; holes_examine(h, 0, j); }
  h->mainc = j;
  holes_place(h, &h->h[j], o, g);
}

static void holes_free(Policy* p, Obj* o) {
  Holes* h = (Holes*)p;
  HReg* r = &h->r[o->pl[p->slot].region];
  bm_range(r->bm, o->pl[p->slot].off, o->size / 16, 0);
  r->live--;
}

static void holes_young_adj(Policy* p, const Obj* o, int delta, int in_free) {
  reg_young_adj(&((Holes*)p)->r[o->pl[p->slot].region].sw, delta, in_free);
}

static void holes_tail(Policy* p, Obj* o, uint32_t rest) {
  Holes* h = (Holes*)p;
  HReg* r = &h->r[o->pl[p->slot].region];
  uint32_t off = o->pl[p->slot].off + (o->size - rest) / 16;
  bm_range(r->bm, off, rest / 16, 0);
  if (h->tails) tl_push(h, rest / 16, (uint64_t)o->pl[p->slot].region << 32 | off);
}

/* The maximal zero runs of a region's live bitmap, appended to the holes.  Each carries the words
 * a walker reads to reach it from the previous hole's last word (or the region's first), plus
 * `*carry`: the words of the regions and region tails passed since the previous hole. */
static uint64_t holes_scan(Holes* h, uint32_t ri, uint64_t* carry) {
  const uint64_t* bm = h->r[ri].bm;
  uint64_t count = 0;
  uint32_t pos = 0, sp = 0;   /* sp: word the walk has read up to */
  while (pos < REGION_GRANULES) {
    uint32_t w = pos >> 6;
    uint64_t bits = ~bm[w] & (~0ull << (pos & 63));
    while (bits == 0) {
      if (++w == BM_WORDS) goto done;
      bits = ~bm[w];
    }
    uint32_t start = w * 64 + (uint32_t)__builtin_ctzll(bits), end;
    w = start >> 6;
    uint64_t ones = bm[w] & (~0ull << (start & 63));
    while (ones == 0) {
      if (++w == BM_WORDS) { ones = 1; w = BM_WORDS - 1; end = REGION_GRANULES; goto emit; }
      ones = bm[w];
    }
    end = w * 64 + (uint32_t)__builtin_ctzll(ones);
  emit:;
    uint32_t end_w = (end - 1) >> 6;
    holes_push(h, ri, start, end, (uint32_t)(*carry + (end_w - sp + 1)));
    *carry = 0;
    sp = end_w;
    count++;
    pos = end;
  }
done:
  *carry += count ? BM_WORDS - 1 - sp : BM_WORDS;
  return count;
}

static void holes_gc_end(Policy* p, uint64_t kind) {
  Holes* h = (Holes*)p;
  reg_sweep_stats(p, (const RegSweep*)h->r, sizeof(HReg), h->n, kind);
  for (uint32_t i = 0; i < h->n; i++) {
    HReg* r = &h->r[i];
    if (r->sw.used && r->live == 0) {
      free(r->bm);
      r->bm = NULL;
      r->sw.used = 0;
      h->nused--;
    }
  }
  stat_add(&p->eager_words[kind], (uint64_t)h->nused * BM_WORDS);
  h->nh = 0;
  uint64_t all = 0, young_holes = 0, live_young = 0, carry = 0;
  for (uint32_t i = 0; i < h->n; i++) {
    h->r[i].sw.yfree = 0;
    if (!h->r[i].sw.used) continue;
    uint64_t c = holes_scan(h, i, &carry);
    all += c;
    if (h->r[i].sw.touched) { young_holes += c; live_young += h->r[i].live; }
    h->r[i].sw.touched = 0;
  }
  stat_add(&h->holes_all, all);
  stat_add(&h->holes_young, young_holes);
  stat_add(&h->live_young, live_young);
  h->free_g = 0;
  for (size_t j = 0; j < h->nh; j++) h->free_g += h->h[j].end - h->h[j].cur;
  h->mainc = h->ovfc = 0;
  memset(h->ntl, 0, sizeof h->ntl);   /* unused tails are free granules, now holes */
  h->last_ex[0] = h->last_ex[1] = SIZE_MAX;
  if (h->waste_interval > h->waste_peak) h->waste_peak = h->waste_interval;
  h->waste_interval = 0;
}

static uint64_t holes_regions(const Policy* p) { return ((const Holes*)p)->nused; }
static uint64_t holes_free_units(const Policy* p) { return ((const Holes*)p)->free_g; }

/* ── class-R (option E) ───────────────────────────────────────────────────────────────────── */
typedef struct {
  RegSweep sw;                               /* sw.nw: alloc-bitmap words */
  uint32_t cls, nslots, inuse, hint_w;       /* hint_w: lowest word that may hold a free bit */
  uint32_t chunk, cslot;                     /* where the region sits among 1 MiB chunks (R < 1 MiB) */
  uint64_t* ab;                              /* alloc bitmap, padding bits set */
} CReg;
typedef struct {
  Policy p;
  uint32_t R;
  CReg* r;
  uint32_t n, cap, nused;
  uint32_t* lst[NCLS];   /* region indices of a class */
  uint32_t nl[NCLS], capl[NCLS], cur[NCLS];
  uint32_t last_reg[NCLS];   /* region of the class's previous placement in this interval, NO_OBJ if none */
  uint8_t unsorted[NCLS];
  uint64_t dead_tail;
  uint64_t free;   /* free slots over the used regions */
  /* 1 MiB chunks: a bit per R-slot in use */
  uint64_t* ck;
  uint32_t ck_n, ck_cap, ck_used, per_chunk;
  /* at the peak G */
  uint64_t pk_waste, pk_tail, pk_free, pk_classes, pk_regions, pk_bm_bytes;
} ClassR;

static void class_chunk_take(ClassR* c, CReg* r) {
  uint64_t full = c->per_chunk == 64 ? ~0ull : (1ull << c->per_chunk) - 1;
  uint32_t ci = c->ck_n;
  for (uint32_t i = 0; i < c->ck_n; i++)
    if (c->ck[i] != 0 && c->ck[i] != full) { ci = i; break; }
  if (ci == c->ck_n)
    for (uint32_t i = 0; i < c->ck_n; i++)
      if (c->ck[i] == 0) { ci = i; break; }
  if (ci == c->ck_n) {
    if (c->ck_n == c->ck_cap) {
      c->ck_cap = c->ck_cap ? c->ck_cap * 2 : 16;
      c->ck = xrealloc(c->ck, (size_t)c->ck_cap * sizeof(uint64_t));
    }
    c->ck[c->ck_n++] = 0;
  }
  if (c->ck[ci] == 0) c->ck_used++;
  uint32_t slot = (uint32_t)__builtin_ctzll(~c->ck[ci]);
  c->ck[ci] |= 1ull << slot;
  r->chunk = ci;
  r->cslot = slot;
}

static void class_chunk_give(ClassR* c, const CReg* r) {
  c->ck[r->chunk] &= ~(1ull << r->cslot);
  if (c->ck[r->chunk] == 0) c->ck_used--;
}

static void class_place(ClassR* c, uint32_t ri, Obj* o) {
  CReg* r = &c->r[ri];
  Policy* p = &c->p;
  uint32_t cls = o->size / 16, w = r->hint_w;
  for (;; w++) {
    p->words++;
    uint64_t bits = ~r->ab[w];
    if (bits) {
      uint32_t bit = (uint32_t)__builtin_ctzll(bits);
      r->ab[w] |= 1ull << bit;
      o->pl[p->slot].off = w * 64 + bit;
      break;
    }
  }
  r->hint_w = w;
  r->inuse++;
  r->sw.touched = 1;
  c->free--;
  if (c->last_reg[cls] != ri) { p->switches++; c->last_reg[cls] = ri; }
  o->pl[p->slot].region = ri;
}

static void class_alloc(Policy* p, Obj* o) {
  ClassR* c = (ClassR*)p;
  uint32_t cls = o->size / 16;
  for (; c->cur[cls] < c->nl[cls]; c->cur[cls]++) {
    uint32_t ri = c->lst[cls][c->cur[cls]];
    p->examined++;
    if (c->r[ri].inuse < c->r[ri].nslots) { class_place(c, ri, o); return; }
  }
  uint32_t i = 0;
  while (i < c->n && c->r[i].sw.used) i++;
  if (i == c->n) {
    if (c->n == c->cap) {
      c->cap = c->cap ? c->cap * 2 : 16;
      c->r = xrealloc(c->r, (size_t)c->cap * sizeof(CReg));
    }
    memset(&c->r[c->n], 0, sizeof(CReg));
    c->n++;
  }
  CReg* r = &c->r[i];
  r->cls = cls;
  r->nslots = c->R / o->size;
  r->inuse = 0;
  r->sw = (RegSweep){ .nw = (r->nslots + 63) / 64, .used = 1 };
  r->hint_w = 0;
  r->ab = xcalloc(r->sw.nw, sizeof(uint64_t));
  if (r->nslots % 64) r->ab[r->sw.nw - 1] = ~0ull << (r->nslots % 64);   /* padding is never free */
  c->free += r->nslots;
  c->nused++;
  if (c->p.chunks) class_chunk_take(c, r);
  if (c->nl[cls] == c->capl[cls]) {
    c->capl[cls] = c->capl[cls] ? c->capl[cls] * 2 : 8;
    c->lst[cls] = xrealloc(c->lst[cls], (size_t)c->capl[cls] * sizeof(uint32_t));
  }
  if (c->nl[cls] > 0 && c->lst[cls][c->nl[cls] - 1] > i) c->unsorted[cls] = 1;
  c->lst[cls][c->nl[cls]++] = i;
  c->cur[cls] = c->nl[cls] - 1;
  class_place(c, i, o);
}

static void class_free(Policy* p, Obj* o) {
  ClassR* c = (ClassR*)p;
  CReg* r = &c->r[o->pl[p->slot].region];
  uint32_t slot = o->pl[p->slot].off, w = slot / 64;
  r->ab[w] &= ~(1ull << (slot % 64));
  if (w < r->hint_w) r->hint_w = w;
  r->inuse--;
  c->free++;
  if (o->flags & O_TAILED) c->dead_tail -= o->tail;
}

static void class_young_adj(Policy* p, const Obj* o, int delta, int in_free) {
  reg_young_adj(&((ClassR*)p)->r[o->pl[p->slot].region].sw, delta, in_free);
}

static void class_tail(Policy* p, Obj* o, uint32_t rest) {
  (void)o;
  ((ClassR*)p)->dead_tail += rest;
}

static int cmp_u32(const void* a, const void* b) {
  uint32_t x = *(const uint32_t*)a, y = *(const uint32_t*)b;
  return x < y ? -1 : x > y;
}

static void class_gc_end(Policy* p, uint64_t kind) {
  ClassR* c = (ClassR*)p;
  reg_sweep_stats(p, (const RegSweep*)c->r, sizeof(CReg), c->n, kind);
  for (uint32_t i = 0; i < c->n; i++) {
    CReg* r = &c->r[i];
    if (r->sw.used && r->inuse == 0) {
      r->sw.used = 0;
      c->nused--;
      c->free -= r->nslots;
      if (c->p.chunks) class_chunk_give(c, r);
      free(r->ab);
      r->ab = NULL;
    }
    r->sw.touched = 0;
    r->sw.yfree = 0;
  }
  for (uint32_t cls = 1; cls < NCLS; cls++) {
    uint32_t k = 0;
    for (uint32_t j = 0; j < c->nl[cls]; j++)
      if (c->r[c->lst[cls][j]].sw.used) c->lst[cls][k++] = c->lst[cls][j];
    c->nl[cls] = k;
    if (c->unsorted[cls]) { qsort(c->lst[cls], k, sizeof(uint32_t), cmp_u32); c->unsorted[cls] = 0; }
    c->cur[cls] = 0;
    c->last_reg[cls] = NO_OBJ;
  }
}

static uint64_t class_regions(const Policy* p) { return ((const ClassR*)p)->nused; }
static uint64_t class_chunks(const Policy* p) { return ((const ClassR*)p)->ck_used; }
static uint64_t class_free_units(const Policy* p) { return ((const ClassR*)p)->free; }

static void class_snap(Policy* p) {
  ClassR* c = (ClassR*)p;
  c->pk_waste = c->pk_free = c->pk_classes = c->pk_bm_bytes = 0;
  for (uint32_t i = 0; i < c->n; i++) {
    const CReg* r = &c->r[i];
    if (!r->sw.used) continue;
    c->pk_waste += c->R - (uint64_t)r->nslots * r->cls * 16;
    c->pk_free += (uint64_t)(r->nslots - r->inuse) * r->cls * 16;
    c->pk_bm_bytes += (r->nslots + 7) / 8;   /* one bitmap of slots_per_region bits */
  }
  for (uint32_t cls = 1; cls < NCLS; cls++) c->pk_classes += c->nl[cls] > 0;
  c->pk_tail = c->dead_tail;
  c->pk_regions = c->nused;
}

/* ── policy construction and the report ───────────────────────────────────────────────────── */
static Policy* policy_new(const char* name) {
  Policy* p = NULL;
  if (strcmp(name, "base") == 0) {
    Base* b = xcalloc(1, sizeof *b);
    b->hint = -1;
    b->p = (Policy){ .name = "base", .type = PK_BASE, .region_bytes = REGION_BYTES, .alloc = base_alloc, .free_ = base_free,
                     .tail = base_tail, .gc_end = base_gc_end, .regions = base_regions, .free_units = base_free_units };
    p = &b->p;
  } else if (strcmp(name, "holes") == 0 || strcmp(name, "holes-ovf") == 0 ||
             strcmp(name, "holes-ovf-tails") == 0) {
    Holes* h = xcalloc(1, sizeof *h);
    h->tails = strcmp(name, "holes-ovf-tails") == 0;
    h->ovf = h->tails || strcmp(name, "holes-ovf") == 0;    h->last_ex[0] = h->last_ex[1] = SIZE_MAX;
    h->p = (Policy){ .name = h->tails ? "holes-ovf-tails" : h->ovf ? "holes-ovf" : "holes", .type = PK_HOLES, .region_bytes = REGION_BYTES, .alloc = holes_alloc,
                     .free_ = holes_free, .tail = holes_tail, .gc_end = holes_gc_end, .regions = holes_regions,
                     .free_units = holes_free_units, .nbitmaps = HOLES_BITMAPS, .young_adj = holes_young_adj };
    p = &h->p;
  } else if (strncmp(name, "class-", 6) == 0) {
    static const struct { const char* n; uint32_t r; } sizes[] = {
      { "class-16K", 16u << 10 }, { "class-64K", 64u << 10 }, { "class-256K", 256u << 10 }, { "class-1M", 1u << 20 } };
    for (size_t i = 0; i < sizeof sizes / sizeof *sizes; i++)
      if (strcmp(name, sizes[i].n) == 0) {
        ClassR* c = xcalloc(1, sizeof *c);
        c->R = sizes[i].r;
        c->per_chunk = REGION_BYTES / c->R;
        for (uint32_t k = 0; k < NCLS; k++) c->last_reg[k] = NO_OBJ;
        c->p = (Policy){ .name = sizes[i].n, .type = PK_CLASS, .region_bytes = c->R, .alloc = class_alloc, .free_ = class_free,
                         .tail = class_tail, .gc_end = class_gc_end, .regions = class_regions,
                         .chunks = c->R < REGION_BYTES ? class_chunks : NULL, .free_units = class_free_units,
                         .snap = class_snap, .nbitmaps = CLASS_BITMAPS, .young_adj = class_young_adj };
        p = &c->p;
      }
  }
  return p;
}

static const char* const ALL_POLICIES[] = { "base", "holes", "holes-ovf", "holes-ovf-tails", "class-16K", "class-64K", "class-256K", "class-1M" };

/* "all", or a comma-separated list of policy names; 0 on success. */
static int sim_init(Sim* s, const char* list) {
  memset(s, 0, sizeof *s);
  s->pool_free = NO_OBJ;
  char buf[256];
  if (strcmp(list, "all") == 0) {
    buf[0] = 0;
    for (size_t i = 0; i < sizeof ALL_POLICIES / sizeof *ALL_POLICIES; i++) {
      if (i) strcat(buf, ",");
      strcat(buf, ALL_POLICIES[i]);
    }
  } else {
    snprintf(buf, sizeof buf, "%s", list);
  }
  for (char* tok = strtok(buf, ","); tok != NULL; tok = strtok(NULL, ",")) {
    Policy* p = policy_new(tok);
    if (p == NULL || s->npol == MAX_POL) { fprintf(stderr, "replay: unknown policy '%s'\n", tok); return 1; }
    p->slot = s->npol;
    s->pols[s->npol++] = p;
    s->have_base |= p->type == PK_BASE;
  }
  return s->npol == 0;
}

static double mib(double b) { return b / 1048576.0; }
static double kib(double b) { return b / 1024.0; }
#define U(x) ((unsigned long long)(x))

static const char* const KIND_NAME[GC_KINDS] = { "off(0)", "major(1)", "minor-young(2)", "minor-full(3)" };
static const int KIND_ORDER[GC_KINDS] = { GC_KIND_MAJOR, GC_KIND_MINOR_YOUNG, GC_KIND_MINOR_FULL, GC_KIND_OFF };

/* SproutRegion without the trace field, and SproutRegionFl (257 class heads), in bytes. */
#define META_REGION 96u
#define META_FL 2056u
#define META_MAP 8192u

static void report_sweep(const Sim* s) {
  printf("\nsweep work per E, mean by E kind: words of ONE bitmap (a region updates `bm` bitmaps), then regions swept.\n"
         "Kinds 0, 1, 3 sweep every region, kind 2 those allocated into since the last E or holding a live young object.\nHole counts are not a cost.\n");
  printf("%-16s %3s |", "", "bm");
  for (int k = 0; k < GC_KINDS; k++) printf(" %14s |", KIND_NAME[KIND_ORDER[k]]);
  printf(" %12s\n", "all E");
  printf("%-16s %3s |", "E count", "");
  for (int k = 0; k < GC_KINDS; k++) printf(" %14llu |", U(s->ecount[KIND_ORDER[k]]));
  printf(" %12llu\n", U(s->collections));
  printf("%-16s %3s |", "today walked", "");
  double wsum = 0;
  for (int k = 0; k < GC_KINDS; k++) {
    const Stat* w = &s->walked_k[KIND_ORDER[k]];
    if (w->n) printf(" %14.0f |", stat_mean(w)); else printf(" %14s |", "-");
    wsum += w->sum;
  }
  if (s->collections && wsum > 0) printf(" %12.0f\n", wsum / (double)s->collections); else printf(" %12s\n", "-");
  for (int i = 0; i < s->npol; i++) {
    const Policy* p = s->pols[i];
    if (p->type == PK_BASE) continue;
    char lab[40];
    for (int row = 0; row < 2; row++) {
      const Stat* st = row ? p->sw_regions : p->sw_words;
      snprintf(lab, sizeof lab, "%s %s", p->name, row ? "regions" : "words");
      printf("%-16s ", lab);
      if (row == 0) printf("%3u |", p->nbitmaps); else printf("%3s |", "");
      double tot = 0;
      for (int k = 0; k < GC_KINDS; k++) {
        const Stat* x = &st[KIND_ORDER[k]];
        if (x->n) printf(" %14.1f |", stat_mean(x)); else printf(" %14s |", "-");
        tot += x->sum;
      }
      printf(" %12.1f\n", s->collections ? tot / (double)s->collections : 0.0);
    }
    for (int row = 0; row < 3; row++) {
      const Stat* st = row == 0 ? p->sw_words_old : row == 1 ? p->sw_regions_old : p->eager_words;
      if (row == 2 && p->type != PK_HOLES) continue;
      if (row == 2) snprintf(lab, sizeof lab, "%s eager", p->name);
      else snprintf(lab, sizeof lab, "%s %s*", p->name, row ? "regions" : "words");
      printf("%-16s %3s |", lab, "");
      double tot = 0;
      for (int k = 0; k < GC_KINDS; k++) {
        const Stat* x = &st[KIND_ORDER[k]];
        if (x->n) printf(" %14.1f |", stat_mean(x)); else printf(" %14s |", "-");
        tot += x->sum;
      }
      printf(" %12.1f\n", s->collections ? tot / (double)s->collections : 0.0);
    }
  }
  printf("* kind 2 on regions allocated into since the last E only (the earlier model); the rows above also sweep every\n"
         "region holding a live young object (a survivor stays young until it has survived %d collections), as\n"
         "sprout_gc_sweep_young does.  Ages: kind 2 and 1 recompute young from age, 0 and 3 promote everything.\n"
         "eager: words an eager hole scan over every used region reads at each E -- eager hole scan, not in the design.\n",
         PROMOTE_AGE);
}

static void report_alloc_side(const Sim* s) {
  printf("\nallocation-side work, totals over the run (per alloc = total / ordinary allocations; per interval = between two Es)\n");
  for (int i = 0; i < s->npol; i++) {
    const Policy* p = s->pols[i];
    double per = p->allocs ? 1.0 / (double)p->allocs : 0.0;
    if (p->type == PK_BASE) {
      const Base* b = (const Base*)p;
      printf("%-10s cursor regions tested %llu (%.4f/alloc; per interval mean %.1f max %llu) | bump path: %llu allocs, %llu hint tests, "
             "%llu downward scans testing %llu regions (%.4f/alloc)\n",
             p->name, U(p->examined), (double)p->examined * per, stat_mean(&p->iv_ex), U(p->iv_ex.max), U(b->bump_allocs),
             U(b->hint_tests), U(b->scans), U(b->scan_regions), (double)b->scan_regions * per);
    } else if (p->type == PK_HOLES) {
      const Holes* h = (const Holes*)p;
      printf("%-10s holes examined %llu (%.4f/alloc) entered %llu skipped(main, room left) %llu passed(overflow) %llu | occupancy words read %llu "
             "(%.3f/alloc; per interval mean %.1f max %llu)\n",
             p->name, U(p->examined), (double)p->examined * per, U(h->entered), U(h->skipped), U(h->passed), U(p->words),
             (double)p->words * per, stat_mean(&p->iv_w), U(p->iv_w.max));
      if (h->tails) printf("%-10s tail-list hits %llu (%.4f/alloc)\n", p->name, U(h->tail_hits), (double)h->tail_hits * per);
    } else {
      printf("%-10s regions tested %llu (%.4f/alloc; per interval mean %.1f max %llu) | region switches %llu (%.4f/alloc) | alloc-bitmap words read "
             "%llu (%.3f/alloc; per interval mean %.1f max %llu)\n",
             p->name, U(p->examined), (double)p->examined * per, stat_mean(&p->iv_ex), U(p->iv_ex.max), U(p->switches),
             (double)p->switches * per, U(p->words), (double)p->words * per, stat_mean(&p->iv_w), U(p->iv_w.max));
    }
  }
}

static void report_floor(const Sim* s) {
  printf("\nfootprint-floor indicator: Es of kind 0/1/3 whose floor applies and are followed by a G: %llu counted, %llu left out "
         "(the trace's own floor already above the next G's heap); mean allocations E->G %.0f\n",
         U(s->fl_applying), U(s->fl_skipped), stat_mean(&s->fl_interval));
  printf("%-10s %-24s %9s %9s %10s %12s %12s\n", "policy", "free measure", "applying", "later", "max delay", "mean(later)", "mean(all)");
  for (int i = 0; i < s->npol; i++) {
    const Policy* p = s->pols[i];
    int nv = p->type == PK_HOLES ? 2 : 1;
    for (int v = 0; v < nv; v++) {
      const FloorStat* f = &p->fl[v];
      const char* what = p->type == PK_HOLES ? (v ? "granules / mean live slot" : "granules") : "slots";
      printf("%-10s %-24s %9llu %9llu %10llu %12.1f %12.1f\n", p->name, what, U(f->n), U(f->moved), U(f->delay_max),
             f->moved ? f->delay_sum / (double)f->moved : 0.0, f->n ? f->delay_sum / (double)f->n : 0.0);
    }
  }
}

/* True when base's replay matches the trace exactly. */
static int calibration_ok(const Sim* s) {
  return s->cal_maxdiff == 0 && s->cal_large_miss == 0 && s->cal_free_maxdiff == 0;
}

static void report(const Sim* s, const char* path) {
  printf("== %s\n", path);
  printf("allocs=%llu frees=%llu collections=%llu (off=%llu major=%llu minor_young=%llu minor_full=%llu) "
         "large_allocs=%llu large_live_peak=%.1f MiB\n",
         U(s->allocs), U(s->frees), U(s->collections), U(s->ecount[GC_KIND_OFF]), U(s->ecount[GC_KIND_MAJOR]),
         U(s->ecount[GC_KIND_MINOR_YOUNG]), U(s->ecount[GC_KIND_MINOR_FULL]),
         U(s->large_allocs), mib((double)s->large_peak));
  printf("live after E (ordinary slot bytes + large): peak %.2f MiB, mean %.2f MiB\n", mib((double)s->live_e.max), mib(stat_mean(&s->live_e)));
  printf("live buffer owners at E (bytes, builders, vectors with a B): peak %llu, mean %.1f\n", U(s->owners_e.max), stat_mean(&s->owners_e));
  const Policy* base = NULL;
  for (int i = 0; i < s->npol; i++) if (s->pols[i]->type == PK_BASE) base = s->pols[i];
  if (base != NULL && s->cal_trace.n > 0) {
    printf("base calibration vs the trace's E records, exact at every E: ordinary regions base max %llu mean %.2f | trace max %llu mean %.2f | "
           "Es with a mismatch %llu of %llu, max per-E diff %llu | large_regions mismatches %llu | free slots: Es with a mismatch %llu of %llu, "
           "max per-E diff %llu | %s\n",
           U(s->cal_base.max), stat_mean(&s->cal_base), U(s->cal_trace.max), stat_mean(&s->cal_trace), U(s->cal_n), U(s->cal_trace.n),
           U(s->cal_maxdiff), U(s->cal_large_miss), U(s->cal_free_miss), U(s->cal_free_n), U(s->cal_free_maxdiff),
           calibration_ok(s) ? "CALIBRATION OK" : "CALIBRATION MISS");
  }
  printf("\n%-11s %10s %10s %8s %9s %11s %11s\n", "policy", "peak_held", "mean_held", "vs_base", "peak_reg", "mean_held@E", "peak_reg@G");
  for (int i = 0; i < s->npol; i++) {
    const Policy* p = s->pols[i];
    printf("%-11s %9.1fM %9.1fM ", p->name, mib((double)p->g_peak), mib(p->g_n ? p->g_sum / (double)p->g_n : 0));
    if (base != NULL && base->g_peak) printf("%7.2fx ", (double)p->g_peak / (double)base->g_peak); else printf("%8s ", "-");
    printf("%9llu %10.1fM %11llu\n", U(p->peak_regions), mib(p->e_n ? p->e_sum / (double)p->e_n : 0), U(p->g_regs_max));
  }
  int any = 0;
  for (int i = 0; i < s->npol; i++) any |= s->pols[i]->chunks != NULL;
  if (any) {
    printf("\nchunked memory (R-regions carved from 1 MiB chunks; held = chunks x 1 MiB + large):\n");
    printf("%-11s %10s %10s %8s %9s %11s %12s\n", "policy", "peak_held", "mean_held", "vs_base", "peak_chk", "mean_held@E", "vs R-granular");
    for (int i = 0; i < s->npol; i++) {
      const Policy* p = s->pols[i];
      if (p->chunks == NULL) continue;
      printf("%-11s %9.1fM %9.1fM ", p->name, mib((double)p->gk_peak), mib(p->g_n ? p->gk_sum / (double)p->g_n : 0));
      if (base != NULL && base->g_peak) printf("%7.2fx ", (double)p->gk_peak / (double)base->g_peak); else printf("%8s ", "-");
      printf("%9llu %10.1fM %11.2fx\n", U(p->peak_chunks), mib(p->e_n ? p->ek_sum / (double)p->e_n : 0),
             p->g_peak ? (double)p->gk_peak / (double)p->g_peak : 0.0);
    }
  }
  printf("\nmetadata at the peak G (regions at that point; SproutRegion %u B, SproutRegionFl %u B, bitmap = 8 KiB per 1 MiB region):\n", META_REGION, META_FL);
  printf("%-11s %9s | %12s %12s %12s | %9s %s\n", "policy", "regions", "bitmaps KiB", "lists KiB", "total KiB", "% of held", "(class-R: 3 bitmaps; with a 4th: total KiB)");
  for (int i = 0; i < s->npol; i++) {
    const Policy* p = s->pols[i];
    double maps, lists = 0, total, total4 = 0;
    if (p->type == PK_CLASS) {
      const ClassR* c = (const ClassR*)p;
      maps = 3.0 * (double)c->pk_bm_bytes;
      total = maps + (double)c->pk_regions * META_REGION;
      total4 = total + (double)c->pk_bm_bytes;
    } else {
      maps = (double)p->pk_regs * (p->type == PK_HOLES ? 4 : 2) * META_MAP;
      lists = (double)p->pk_regs * META_FL;
      total = maps + lists + (double)p->pk_regs * META_REGION;
    }
    printf("%-11s %9llu | %12.1f %12.1f %12.1f | %8.2f%%", p->name, U(p->type == PK_CLASS ? ((const ClassR*)p)->pk_regions : p->pk_regs),
           kib(maps), kib(lists), kib(total), p->g_peak ? 100.0 * total / (double)p->g_peak : 0.0);
    if (p->type == PK_CLASS) printf("  %.1f", kib(total4));
    printf("\n");
  }
  any = 0;
  for (int i = 0; i < s->npol; i++) any |= s->pols[i]->type == PK_HOLES;
  if (any) {
    printf("\n%-11s %9s %9s | %-17s | %-17s | %-17s  (holes per E: informational, NOT a sweep cost)\n", "holes", "waste_tot", "waste_pk", "holes/E all", "holes/E young", "live in young");
    printf("%-11s %9s %9s | %8s %8s | %8s %8s | %8s %8s\n", "", "MiB", "KiB/intvl", "mean", "max", "mean", "max", "mean", "max");
    for (int i = 0; i < s->npol; i++) {
      if (s->pols[i]->type != PK_HOLES) continue;
      const Holes* h = (const Holes*)s->pols[i];
      printf("%-11s %9.1f %9.1f | %8.1f %8llu | %8.1f %8llu | %8.1f %8llu\n", h->p.name, mib((double)h->waste_total), kib((double)h->waste_peak),
             stat_mean(&h->holes_all), U(h->holes_all.max), stat_mean(&h->holes_young), U(h->holes_young.max), stat_mean(&h->live_young),
             U(h->live_young.max));
    }
  }
  any = 0;
  for (int i = 0; i < s->npol; i++) any |= s->pols[i]->type == PK_CLASS;
  if (any) {
    printf("\n%-11s %12s %12s %12s %8s %8s\n", "class-R", "remainder", "tail dead", "free slots", "classes", "regions");
    printf("%-11s %12s %12s %12s %8s %8s\n", "(at peak)", "MiB", "KiB", "MiB", "in use", "");
    for (int i = 0; i < s->npol; i++) {
      if (s->pols[i]->type != PK_CLASS) continue;
      const ClassR* c = (const ClassR*)s->pols[i];
      printf("%-11s %12.2f %12.1f %12.2f %8llu %8llu\n", c->p.name, mib((double)c->pk_waste), kib((double)c->pk_tail),
             mib((double)c->pk_free), U(c->pk_classes), U(c->pk_regions));
    }
  }
  report_sweep(s);
  report_alloc_side(s);
  report_floor(s);
  printf("\n");
}

static int s_alloc(void* c, uint64_t id, uint64_t kind, uint64_t sz) { return sim_alloc(c, id, kind, sz); }
static int s_free(void* c, uint64_t id) { return sim_free(c, id); }
static int s_tail(void* c, uint64_t id, uint64_t rest) { return sim_tail(c, id, rest); }
static int s_vec_out(void* c, uint64_t id) { return sim_vec_out(c, id); }
static int s_gc_start(void* c, uint64_t cycle) { (void)cycle; return sim_gc_start(c); }
static int s_gc_end(void* c, uint64_t cycle, uint64_t kind, uint64_t heap_after, uint64_t free_slots, uint64_t ordinary, uint64_t large, uint64_t walked) {
  (void)cycle;
  return sim_gc_end(c, kind, heap_after, ordinary, large, free_slots, walked);
}
static int s_finish(void* c) { (void)c; return 0; }
static const Handlers sim_handlers = { s_alloc, s_free, s_tail, s_vec_out, s_gc_start, s_gc_end, s_finish };

/* ── --selftest: the policies on tiny hand-written traces ─────────────────────────────────── */
static int g_fail, g_checks;
#define CHECK(cond, ...) do { g_checks++; if (!(cond)) { g_fail++; printf("  FAIL %s:%d: ", __func__, __LINE__); printf(__VA_ARGS__); printf("\n"); } } while (0)

typedef struct { Sim s; uint64_t next; int cycle; } T;

static T* t_new(const char* policies) {
  T* t = xcalloc(1, sizeof *t);
  if (sim_init(&t->s, policies)) { fprintf(stderr, "selftest: bad policy list %s\n", policies); exit(2); }
  return t;
}
static uint64_t t_a(T* t, uint32_t bytes) {
  if (sim_alloc(&t->s, t->next, 3, bytes)) { printf("selftest: %s\n", g_err); exit(2); }
  return t->next++;
}
static void t_f(T* t, uint64_t id) { if (sim_free(&t->s, id)) { printf("selftest: %s\n", g_err); exit(2); } }
static void t_tail(T* t, uint64_t id, uint32_t rest) { if (sim_tail(&t->s, id, rest)) { printf("selftest: %s\n", g_err); exit(2); } }
static void t_g(T* t) { sim_gc_start(&t->s); }
static void t_e2(T* t, uint64_t kind, uint64_t free_slots) {
  if (sim_gc_end(&t->s, kind, UINT64_MAX, UINT64_MAX, UINT64_MAX, free_slots, UINT64_MAX)) { printf("selftest: %s\n", g_err); exit(2); }
}
static void t_e(T* t, uint64_t kind) { t_e2(t, kind, UINT64_MAX); }
static const Obj* t_obj(T* t, uint64_t id) { return &t->s.pool[map_get(&t->s.map, id)]; }
static Holes* t_holes(T* t, int i) { return (Holes*)t->s.pols[i]; }
static Base* t_base(T* t, int i) { return (Base*)t->s.pols[i]; }
static ClassR* t_class(T* t, int i) { return (ClassR*)t->s.pols[i]; }

/* Objects of 16, 32 and 16 bytes at granules 0, 1, 3; the 32-byte one is freed. */
static T* holes_setup(const char* policy) {
  T* t = t_new(policy);
  t_a(t, 16); t_a(t, 32); t_a(t, 16);
  t_g(t); t_f(t, 1); t_e(t, GC_KIND_MAJOR);
  return t;
}

static void test_hole_exactly_filled(void) {
  T* t = holes_setup("holes");
  Holes* h = t_holes(t, 0);
  CHECK(h->nh == 2, "holes after E: %zu, want 2", h->nh);
  CHECK(h->h[0].cur == 1 && h->h[0].end == 3, "hole 0 is [%u,%u), want [1,3)", h->h[0].cur, h->h[0].end);
  CHECK(h->h[1].cur == 3 + 1 && h->h[1].end == REGION_GRANULES, "hole 1 is [%u,%u), want [4,%u)", h->h[1].cur, h->h[1].end, REGION_GRANULES);
  uint64_t a = t_a(t, 32);
  CHECK(t_obj(t, a)->pl[0].off == 1, "32 B request landed at granule %u, want 1", t_obj(t, a)->pl[0].off);
  uint64_t b = t_a(t, 16);
  CHECK(t_obj(t, b)->pl[0].off == 4, "next request landed at granule %u, want 4", t_obj(t, b)->pl[0].off);
  CHECK(h->waste_total == 0 && h->skipped == 0, "an exactly filled hole wasted %llu B, skipped %llu", (unsigned long long)h->waste_total, (unsigned long long)h->skipped);
  CHECK(h->nused == 1, "regions %u, want 1", h->nused);
}

static void test_hole_skipped(void) {
  T* t = holes_setup("holes");
  Holes* h = t_holes(t, 0);
  uint64_t a = t_a(t, 48);
  CHECK(t_obj(t, a)->pl[0].off == 4, "48 B request landed at granule %u, want 4 (past the 32 B hole)", t_obj(t, a)->pl[0].off);
  CHECK(h->waste_total == 32 && h->waste_interval == 32 && h->skipped == 1, "waste %llu B skipped %llu, want 32 and 1", (unsigned long long)h->waste_total, (unsigned long long)h->skipped);
  uint64_t b = t_a(t, 16);
  CHECK(t_obj(t, b)->pl[0].off == 4 + 3, "the cursor moved back: next request at granule %u, want 7", t_obj(t, b)->pl[0].off);
  t_g(t); t_e(t, GC_KIND_MINOR_YOUNG);
  CHECK(h->waste_peak == 32 && h->waste_interval == 0, "waste peak %llu interval %llu after E, want 32 and 0", (unsigned long long)h->waste_peak, (unsigned long long)h->waste_interval);
  /* an intermediate hole that does not fit is wasted whole: holes of 32 B and 16 B, then a 48 B request */
  t = t_new("holes");
  uint64_t id[5];
  for (int i = 0; i < 5; i++) id[i] = t_a(t, i == 1 ? 32 : 16);   /* granules 0, 1-2, 3, 4, 5 */
  t_g(t); t_f(t, id[1]); t_f(t, id[3]); t_e(t, GC_KIND_MAJOR);
  h = t_holes(t, 0);
  CHECK(h->nh == 3, "holes %zu, want 3: [1,3) [4,5) [6,..)", h->nh);
  uint64_t c = t_a(t, 48);
  CHECK(t_obj(t, c)->pl[0].off == 6, "48 B request landed at granule %u, want 6", t_obj(t, c)->pl[0].off);
  CHECK(h->waste_total == 48 && h->skipped == 2, "waste %llu B skipped %llu, want 32 + 16 = 48 and 2", (unsigned long long)h->waste_total, (unsigned long long)h->skipped);
}

static void test_hole_overflow(void) {
  /* ids at granules: 0 (16 B), 1..18 (288 B, freed), 19 (16 B).  The hole [1,19) is 288 B. */
  for (int ovf = 0; ovf < 2; ovf++) {
    T* t = t_new(ovf ? "holes-ovf" : "holes");
    t_a(t, 16);
    uint64_t big = t_a(t, 288);
    t_a(t, 16);
    t_g(t); t_f(t, big); t_e(t, GC_KIND_MAJOR);
    Holes* h = t_holes(t, 0);
    CHECK(h->nh == 2 && h->h[0].cur == 1 && h->h[0].end == 19, "ovf=%d: hole 0 is [%u,%u)", ovf, h->h[0].cur, h->h[0].end);
    uint64_t a = t_a(t, 304);   /* 19 granules: does not fit the 18 granule hole, and is above 256 B */
    uint64_t b = t_a(t, 16);
    if (ovf) {
      CHECK(h->waste_total == 0, "holes-ovf wasted %llu B", (unsigned long long)h->waste_total);
      CHECK(t_obj(t, a)->pl[0].off == 20 && t_obj(t, b)->pl[0].off == 1, "holes-ovf: 304 B at %u (want 20), 16 B at %u (want 1: main cursor stayed)", t_obj(t, a)->pl[0].off, t_obj(t, b)->pl[0].off);
    } else {
      CHECK(h->waste_total == 288, "holes wasted %llu B, want 288", (unsigned long long)h->waste_total);
      CHECK(t_obj(t, a)->pl[0].off == 20 && t_obj(t, b)->pl[0].off == 39, "holes: 304 B at %u (want 20), 16 B at %u (want 39)", t_obj(t, a)->pl[0].off, t_obj(t, b)->pl[0].off);
    }
  }
}

/* 257 slots of 4096 B: 256 fill region 0, one opens region 1.  Free region 0's, E: it is released.
 * 255 more fill region 1 and the next request opens a region, which must reuse index 0. */
static void test_region_released_and_reused(void) {
  static const char* const names[] = { "base", "holes", "holes-ovf", "class-1M" };
  for (int k = 0; k < 4; k++) {
    T* t = t_new(names[k]);
    uint64_t id[257];
    for (int i = 0; i < 257; i++) id[i] = t_a(t, 4096);
    CHECK(t->s.pols[0]->regions(t->s.pols[0]) == 2, "%s: %llu regions after 257 slots, want 2", names[k], (unsigned long long)t->s.pols[0]->regions(t->s.pols[0]));
    t_g(t);
    for (int i = 0; i < 256; i++) t_f(t, id[i]);
    t_e(t, GC_KIND_MAJOR);
    CHECK(t->s.pols[0]->regions(t->s.pols[0]) == 1, "%s: %llu regions after region 0 emptied, want 1", names[k], (unsigned long long)t->s.pols[0]->regions(t->s.pols[0]));
    uint64_t last = 0;
    for (int i = 0; i < 255; i++) last = t_a(t, 4096);
    CHECK(t_obj(t, last)->pl[0].region == 1, "%s: 255 more should still fit region 1, last landed in %u", names[k], t_obj(t, last)->pl[0].region);
    uint64_t x = t_a(t, 4096);
    CHECK(t->s.pols[0]->regions(t->s.pols[0]) == 2 && t_obj(t, x)->pl[0].region == 0, "%s: the new region is index %u (regions %llu), want 0 reused", names[k], t_obj(t, x)->pl[0].region, (unsigned long long)t->s.pols[0]->regions(t->s.pols[0]));
  }
}

static void test_base_reopens_and_reuses(void) {
  T* t = t_new("base,holes");
  uint64_t a = t_a(t, 64);
  t_g(t); t_f(t, a); t_e(t, GC_KIND_MAJOR);
  CHECK(t->s.pols[0]->regions(t->s.pols[0]) == 1, "base keeps one fresh region, has %llu", (unsigned long long)t->s.pols[0]->regions(t->s.pols[0]));
  CHECK(t_base(t, 0)->r[t_base(t, 0)->hint].bump == 0, "the fresh region is not bumped");
  CHECK(t->s.pols[1]->regions(t->s.pols[1]) == 0, "holes releases every empty region");
  /* an exact-fit slot is reused by its class, not bumped: free a 64 B slot among live ones */
  t = t_new("base");
  uint64_t x = t_a(t, 64), y = t_a(t, 64);
  (void)y;
  t_g(t); t_f(t, x); t_e(t, GC_KIND_MINOR_FULL);
  uint32_t bump = t_base(t, 0)->r[0].bump;
  uint64_t z = t_a(t, 64), w = t_a(t, 32);
  CHECK(t_base(t, 0)->r[0].bump == bump + 32, "64 B reused a free slot (no bump), 32 B bumped: bump %u, want %u", t_base(t, 0)->r[0].bump, bump + 32);
  CHECK(t_obj(t, z)->pl[0].region == 0 && t_obj(t, w)->pl[0].region == 0, "both in region 0");
}

static void test_tails(void) {
  /* a 512 B vector gives back 480 B */
  T* t = t_new("base,holes,class-16K");
  uint64_t v = t_a(t, 512);
  t_tail(t, v, 480);
  uint64_t u = t_a(t, 480);
  /* base: the tail is a free slot of class 30 at once, so the 480 B request pops it, no bump */
  CHECK(t_base(t, 0)->r[0].bump == 512, "base: bump %u, want 512 (the tail was reused)", t_base(t, 0)->r[0].bump);
  CHECK(t_obj(t, u)->pl[0].region == 0, "base: placed in region 0");
  /* holes: the tail is no hole before the next E, so the request bumps */
  CHECK(t_obj(t, u)->pl[1].off == 512 / 16, "holes: 480 B at granule %u, want %u (past the old extent)", t_obj(t, u)->pl[1].off, 512 / 16);
  /* class-R: nothing shrinks; the tail is dead until the vector goes */
  CHECK(t_class(t, 2)->dead_tail == 480, "class-16K: dead tail %llu, want 480", (unsigned long long)t_class(t, 2)->dead_tail);
  t_g(t); t_e(t, GC_KIND_MAJOR);
  Holes* h = t_holes(t, 1);
  int found = 0;
  for (size_t i = 0; i < h->nh; i++) found |= h->h[i].cur == 2 && h->h[i].end == 512 / 16;
  CHECK(found, "holes: after E the tail [2,32) is a hole");
  t_g(t); t_f(t, v); t_e(t, GC_KIND_MAJOR);
  CHECK(t_class(t, 2)->dead_tail == 0, "class-16K: dead tail %llu after the vector is freed, want 0", (unsigned long long)t_class(t, 2)->dead_tail);
  CHECK(t_obj(t, u)->size == 480, "u size");
}

/* A 512 B vector gives back 480 B (granules 2..31), a 160 B vector gives back 128 B (class 8).
 * A 480 B request takes the first tail before any collection, at granule 2.  The 128 B tail is
 * unused, so at the next E it is a hole, and the 160 B vector's own 32 B head stays live. */
static void test_tail_list(void) {
  T* t = t_new("holes-ovf,holes-ovf-tails");
  uint64_t v = t_a(t, 512), w = t_a(t, 160);   /* granules 0..31, 32..41 */
  t_tail(t, v, 480);
  t_tail(t, w, 128);
  Holes* h = t_holes(t, 1);
  uint64_t u = t_a(t, 480);
  CHECK(t_obj(t, u)->pl[0].off == 42 && t_obj(t, u)->pl[1].off == 2, "480 B: plain at %u (want 42), tails at %u (want 2)", t_obj(t, u)->pl[0].off, t_obj(t, u)->pl[1].off);
  CHECK(h->tail_hits == 1 && t_holes(t, 0)->p.allocs == h->p.allocs, "tail hits %llu, want 1", U(h->tail_hits));
  uint64_t x = t_a(t, 16);   /* no class-1 tail: hole cursors */
  CHECK(t_obj(t, x)->pl[1].off == 42 && t_obj(t, x)->pl[0].off == 72, "16 B: tails at %u (want 42), plain at %u (want 72)", t_obj(t, x)->pl[1].off, t_obj(t, x)->pl[0].off);
  CHECK(h->tail_hits == 1, "tail hits %llu after a miss, want 1", U(h->tail_hits));
  t_g(t); t_e(t, GC_KIND_MAJOR);
  CHECK(h->ntl[8] == 0, "tail list dropped at E");
  int found = 0;
  for (size_t i = 0; i < h->nh; i++) found |= h->h[i].cur == 34 && h->h[i].end == 42;
  CHECK(found, "after E the unused 128 B tail [34,42) joins a hole; holes: %zu", h->nh);
}

static void test_class_region_fills(void) {
  T* t = t_new("class-16K");
  ClassR* c = t_class(t, 0);
  uint64_t id[5];
  for (int i = 0; i < 4; i++) id[i] = t_a(t, 4096);
  CHECK(c->nused == 1 && c->r[0].nslots == 4, "one region of 4 slots, have %u regions of %u slots", c->nused, c->r[0].nslots);
  id[4] = t_a(t, 4096);
  CHECK(c->nused == 2 && t_obj(t, id[4])->pl[0].region == 1, "the fifth slot opens region 1");
  t_a(t, 5 * 16);   /* class 5: 204 slots in 16 KiB, 64 B remainder */
  CHECK(c->nused == 3 && c->r[2].nslots == 16384 / 80, "class 5: %u slots", c->r[2].nslots);
  t_g(t);
  for (int i = 0; i < 4; i++) t_f(t, id[i]);
  t_e(t, GC_KIND_MAJOR);
  CHECK(c->nused == 2, "region 0 released, regions %u", c->nused);
  uint64_t a = t_a(t, 4096), b = t_a(t, 4096), d = t_a(t, 4096);
  CHECK(t_obj(t, a)->pl[0].region == 1 && t_obj(t, b)->pl[0].region == 1 && t_obj(t, d)->pl[0].region == 1, "the lowest region with a free slot is filled first");
  uint64_t e = t_a(t, 4096);
  CHECK(t_obj(t, e)->pl[0].region == 0, "a full class opens a region at the reused index 0, got %u", t_obj(t, e)->pl[0].region);
  c->p.snap(&c->p);
  CHECK(c->pk_classes == 2 && c->pk_regions == 3, "peak snapshot: %llu classes, %llu regions", (unsigned long long)c->pk_classes, (unsigned long long)c->pk_regions);
  /* remainder: class 5 wastes 16384 - 204*80 = 64 B; class 256 wastes 0 */
  CHECK(c->pk_waste == 64, "remainder %llu B, want 64", (unsigned long long)c->pk_waste);

}

static void test_large_counts_as_held(void) {
  T* t = t_new("base,holes,class-1M");
  uint64_t big = t_a(t, 8192), small = t_a(t, 32);
  (void)small;
  t_g(t);
  for (int i = 0; i < 3; i++) {
    const Policy* p = t->s.pols[i];
    CHECK(p->g_peak == p->regions(p) * p->region_bytes + 8192, "%s: held %llu does not include the large object", p->name, (unsigned long long)p->g_peak);
  }
  t_f(t, big); t_e(t, GC_KIND_MAJOR);
  t_g(t);
  CHECK(t->s.large_live == 0 && t->s.large_peak == 8192, "large live %llu peak %llu", (unsigned long long)t->s.large_live, (unsigned long long)t->s.large_peak);
}

/* class-256K: 64 slots of 4096 B per region, 4 regions per chunk.  320 objects fill regions 0-4, so
 * chunk 0 holds regions 0-3 and chunk 1 region 4.  Emptying regions 1-3 releases no chunk; emptying
 * region 0 releases chunk 0; a new region (another class) then takes chunk 1's next R-slot. */
static void test_chunked_accounting(void) {
  T* t = t_new("class-256K,class-1M");
  ClassR* c = t_class(t, 0);
  uint64_t id[320];
  for (int i = 0; i < 320; i++) id[i] = t_a(t, 4096);
  CHECK(c->nused == 5 && c->ck_used == 2, "5 regions in 2 chunks, have %u regions %u chunks", c->nused, c->ck_used);
  CHECK(c->r[3].chunk == 0 && c->r[3].cslot == 3 && c->r[4].chunk == 1 && c->r[4].cslot == 0, "region 3 at chunk %u slot %u, region 4 at chunk %u slot %u",
        c->r[3].chunk, c->r[3].cslot, c->r[4].chunk, c->r[4].cslot);
  t_g(t);
  CHECK(c->p.gk_peak == 2 * REGION_BYTES && c->p.g_peak == 5 * (256u << 10), "peak chunked %llu R-granular %llu", U(c->p.gk_peak), U(c->p.g_peak));
  for (int i = 64; i < 256; i++) t_f(t, id[i]);
  t_e(t, GC_KIND_MAJOR);
  CHECK(c->nused == 2 && c->ck_used == 2, "regions 1-3 gone, but both chunks stay: %u regions %u chunks", c->nused, c->ck_used);
  t_g(t);
  CHECK(c->p.chunks(&c->p) * REGION_BYTES == 2 * REGION_BYTES && c->p.regions(&c->p) * c->R == 2 * (256u << 10), "chunked 2 MiB against R-granular 512 KiB");
  for (int i = 0; i < 64; i++) t_f(t, id[i]);
  t_e(t, GC_KIND_MAJOR);
  CHECK(c->nused == 1 && c->ck_used == 1, "region 0 gone, chunk 0 released: %u regions %u chunks", c->nused, c->ck_used);
  uint64_t x = t_a(t, 16);
  const CReg* nr = &c->r[t_obj(t, x)->pl[0].region];
  CHECK(c->nused == 2 && c->ck_used == 1 && nr->chunk == 1 && nr->cslot == 1, "a new region takes chunk %u slot %u (want 1, 1), chunks %u", nr->chunk, nr->cslot, c->ck_used);
  CHECK(t->s.pols[1]->chunks == NULL, "class-1M has no chunked accounting");
}

/* Holes: [1,3) then [4, end) after E.  A 48 B request skips the first (2 granules) and finds the
 * second; the cursor reads word 0 for the first hole and words 0..1023 for the second. */
static void test_alloc_counters_holes(void) {
  T* t = holes_setup("holes");
  Holes* h = t_holes(t, 0);
  CHECK(h->h[0].words == 1 && h->h[1].words == 1024, "words to reach the holes: %u and %u, want 1 and 1024", h->h[0].words, h->h[1].words);
  uint64_t ex0 = h->p.examined, w0 = h->p.words, en0 = h->entered;
  t_a(t, 48);
  CHECK(h->p.examined - ex0 == 2 && h->p.words - w0 == 1025, "48 B request: %llu holes examined, %llu words, want 2 and 1025", U(h->p.examined - ex0), U(h->p.words - w0));
  CHECK(h->skipped == 1 && h->entered - en0 == 1 && h->passed == 0, "skipped %llu entered %llu passed %llu, want 1 1 0", U(h->skipped), U(h->entered - en0), U(h->passed));
  t_a(t, 16);
  CHECK(h->p.examined - ex0 == 2 && h->p.words - w0 == 1025, "the next request stays in the hole at no cost: %llu holes, %llu words", U(h->p.examined - ex0), U(h->p.words - w0));
}

/* class-16K, four 4096 B slots a region.  Five requests: four fill region 0 (one bitmap word each),
 * the fifth tests the full region 0 and opens region 1.  After E frees slots 0 and 1, the next
 * request tests region 0 and takes slot 0 from the hint word. */
static void test_alloc_counters_class(void) {
  T* t = t_new("class-16K");
  ClassR* c = t_class(t, 0);
  uint64_t id[5];
  for (int i = 0; i < 5; i++) id[i] = t_a(t, 4096);
  CHECK(c->p.examined == 4 && c->p.words == 5 && c->p.switches == 2, "after 5 requests: tested %llu, words %llu, switches %llu, want 4 5 2", U(c->p.examined), U(c->p.words), U(c->p.switches));
  t_g(t); t_f(t, id[0]); t_f(t, id[1]); t_e(t, GC_KIND_MAJOR);
  uint64_t x = t_a(t, 4096);
  CHECK(c->p.examined == 5 && c->p.words == 6 && c->p.switches == 3, "after the reuse: tested %llu, words %llu, switches %llu, want 5 6 3", U(c->p.examined), U(c->p.words), U(c->p.switches));
  CHECK(t_obj(t, x)->pl[0].region == 0 && t_obj(t, x)->pl[0].off == 0, "reused region %u slot %u, want 0 0", t_obj(t, x)->pl[0].region, t_obj(t, x)->pl[0].off);
  /* class-1M, 16 B slots: 64 fill word 0 (one read each), the 65th reads word 0 again and then word 1 */
  t = t_new("class-1M");
  c = t_class(t, 0);
  for (int i = 0; i < 65; i++) t_a(t, 16);
  CHECK(c->p.words == 66 && c->r[0].hint_w == 1, "65 slots of one class: words %llu hint word %u, want 66 and 1", U(c->p.words), c->r[0].hint_w);
}

/* Base: two 64 B slots, free the first, E, then a 64 B and a 32 B request. */
static void test_alloc_counters_base(void) {
  T* t = t_new("base");
  Base* b = t_base(t, 0);
  uint64_t x = t_a(t, 64);
  t_a(t, 64);
  t_g(t); t_f(t, x); t_e(t, GC_KIND_MINOR_FULL);
  CHECK(b->free == 1, "free slots after E: %llu, want 1", U(b->free));
  t_a(t, 64);
  t_a(t, 32);
  CHECK(b->free == 0, "free slots after the reuse: %llu, want 0", U(b->free));
  CHECK(b->p.examined == 3 && b->bump_allocs == 3 && b->hint_tests == 2 && b->scans == 0, "examined %llu bump %llu hint tests %llu scans %llu, want 3 3 2 0",
        U(b->p.examined), U(b->bump_allocs), U(b->hint_tests), U(b->scans));
}

/* Ten 64 B objects, six freed: heap 4, free 6.  Floor 4 + (4 + 6) / 3 = 7.  Two allocations to the
 * next G leave the heap at 6, so base's collection would come one allocation later. */
static void test_floor_indicator(void) {
  T* t = t_new("base,holes");
  uint64_t id[10];
  for (int i = 0; i < 10; i++) id[i] = t_a(t, 64);
  t_g(t);
  for (int i = 0; i < 6; i++) t_f(t, id[i]);
  t_e(t, GC_KIND_MAJOR);   /* the trace's free_slots unknown: its own floor is not compared */
  Policy* b = t->s.pols[0];
  Policy* h = t->s.pols[1];
  CHECK(t->s.fl_val[0][0] == 7, "base floor %llu, want 7", U(t->s.fl_val[0][0]));
  CHECK(t->s.fl_val[1][0] == 4 + (4 + 65520) / 3, "holes floor in granules %llu, want %llu", U(t->s.fl_val[1][0]), U(4 + (4 + 65520) / 3));
  CHECK(t->s.fl_val[1][1] == 4 + (4 + 16380) / 3, "holes floor per mean slot %llu, want %llu", U(t->s.fl_val[1][1]), U(4 + (4 + 16380) / 3));
  t_a(t, 64);
  t_a(t, 64);
  t_g(t);
  CHECK(b->fl[0].n == 1 && b->fl[0].moved == 1 && b->fl[0].delay_max == 1, "base: %llu applying, %llu later, max delay %llu, want 1 1 1", U(b->fl[0].n), U(b->fl[0].moved), U(b->fl[0].delay_max));
  CHECK(h->fl[0].moved == 1 && h->fl[0].delay_max == 4 + (4 + 65520) / 3 - 6, "holes: max delay %llu", U(h->fl[0].delay_max));
  CHECK(h->fl[1].moved == 1 && h->fl[1].delay_max == 4 + (4 + 16380) / 3 - 6, "holes per mean slot: max delay %llu", U(h->fl[1].delay_max));
  /* heap 6, two slots reused: free 4, floor 6 + 10 / 3 = 9; three allocations reach heap 9, not past it */
  t_e(t, GC_KIND_MINOR_FULL);
  t_a(t, 64); t_a(t, 64); t_a(t, 64);
  t_g(t);
  CHECK(b->fl[0].n == 2 && b->fl[0].moved == 1, "a floor equal to the heap does not move: %llu applying, %llu later", U(b->fl[0].n), U(b->fl[0].moved));
  /* a young-only minor has no floor */
  t_e(t, GC_KIND_MINOR_YOUNG);
  t_g(t);
  CHECK(b->fl[0].n == 2 && t->s.fl_applying == 2, "kind 2 applies no floor: %llu counted", U(t->s.fl_applying));
  /* a collection whose trace floor already exceeds the next G's heap is left out */
  t_e2(t, GC_KIND_MAJOR, 1000);
  t_g(t);
  CHECK(t->s.fl_skipped == 1 && b->fl[0].n == 2, "left out: %llu skipped, %llu applying", U(t->s.fl_skipped), U(b->fl[0].n));
}

/* A young-only minor sweeps a region holding a stay-young survivor with no new allocation; it drops
 * out when the object is promoted (age 3rd E) or dies. */
static void test_young_regions_swept(void) {
  T* t = t_new("holes,class-16K");
  const char* nm[2] = { "holes", "class-16K" };
  uint64_t a = t_a(t, 16);
  uint64_t sw[2], old[2];
  int want[4] = { 1, 1, 1, 0 };   /* E1 touched; E2, E3 young-only; promoted at E3, so E4 skips it */
  for (int e = 0; e < 4; e++) {
    for (int i = 0; i < 2; i++) { sw[i] = (uint64_t)t->s.pols[i]->sw_regions[2].sum; old[i] = (uint64_t)t->s.pols[i]->sw_regions_old[2].sum; }
    t_g(t); t_e(t, GC_KIND_MINOR_YOUNG);
    for (int i = 0; i < 2; i++) {
      uint64_t d = (uint64_t)t->s.pols[i]->sw_regions[2].sum - sw[i], dold = (uint64_t)t->s.pols[i]->sw_regions_old[2].sum - old[i];
      CHECK(d == (uint64_t)want[e], "%s E%d swept %llu regions, want %d", nm[i], e + 1, U(d), want[e]);
      CHECK(dold == (e == 0 ? 1u : 0u), "%s E%d touched-only %llu", nm[i], e + 1, U(dold));
    }
  }
  CHECK(t_obj(t, a)->young == 0, "object still young after 4 Es");
  CHECK(t_holes(t, 0)->r[0].sw.young == 0 && t_class(t, 1)->r[0].sw.young == 0, "region young counts not back to 0");
  /* a young object freed by the collection keeps its region in that E's set, not the next */
  uint64_t b = t_a(t, 16);
  for (int e = 0; e < 2; e++) {
    for (int i = 0; i < 2; i++) sw[i] = (uint64_t)t->s.pols[i]->sw_regions[2].sum;
    t_g(t); if (e == 0) t_f(t, b); t_e(t, GC_KIND_MINOR_YOUNG);
    for (int i = 0; i < 2; i++) {
      uint64_t d = (uint64_t)t->s.pols[i]->sw_regions[2].sum - sw[i];
      CHECK(d == (e == 0 ? 1u : 0u), "%s dead-young E%d swept %llu", nm[i], e + 1, U(d));
    }
  }
}

static void put_varint(FILE* f, uint64_t x) {
  while (x >= 0x80) { fputc((int)(x | 0x80), f); x >>= 7; }
  fputc((int)x, f);
}

/* One E record: cycle kind heap_after free_slots ordinary_regions large_regions walked. */
static void put_e(FILE* f, uint64_t cycle, uint64_t kind, uint64_t walked) {
  fputc('G', f); put_varint(f, cycle);
  fputc('E', f);
  uint64_t v[7] = { cycle, kind, 0, 1, 1, 0, walked };
  for (int i = 0; i < 7; i++) put_varint(f, v[i]);
}

/* A written trace with E records carrying walked: --check accepts it, and the replay's per-kind
 * walked rows are the sums, maxima and counts of those fields (70000 needs a 3-byte varint). */
static void test_walked_in_e_record(void) {
  FILE* f = tmpfile();
  if (f == NULL) { printf("selftest: no tmpfile\n"); exit(2); }
  fputs("SPAT1\n", f);
  fputc('A', f); put_varint(f, KIND_VECTOR); put_varint(f, 16);
  fputc('G', f); put_varint(f, 1);
  fputc('F', f); put_varint(f, 0);
  fputc('E', f);
  uint64_t first[7] = { 1, GC_KIND_MAJOR, 0, 1, 1, 0, 300 };
  for (int i = 0; i < 7; i++) put_varint(f, first[i]);
  put_e(f, 2, GC_KIND_MINOR_YOUNG, 70000);
  put_e(f, 3, GC_KIND_MINOR_YOUNG, 30);
  Reader* rd = xcalloc(1, sizeof *rd);
  rd->f = f;
  rewind(f);
  Check c;
  memset(&c, 0, sizeof c);
  CHECK(decode(rd, &check_handlers, &c) == 0, "--check rejects the trace: %s", g_err);
  CHECK(c.gcs[GC_KIND_MAJOR] == 1 && c.gcs[GC_KIND_MINOR_YOUNG] == 2, "check counted %llu majors, %llu young minors", U(c.gcs[GC_KIND_MAJOR]), U(c.gcs[GC_KIND_MINOR_YOUNG]));
  Sim* s = xcalloc(1, sizeof *s);
  CHECK(sim_init(s, "holes") == 0, "bad policy list");
  memset(rd, 0, sizeof *rd);
  rd->f = f;
  rewind(f);
  CHECK(decode(rd, &sim_handlers, s) == 0, "replay rejects the trace: %s", g_err);
  const Stat* major = &s->walked_k[GC_KIND_MAJOR];
  const Stat* young = &s->walked_k[GC_KIND_MINOR_YOUNG];
  CHECK(major->n == 1 && major->sum == 300 && major->max == 300, "major walked n=%llu sum=%.0f max=%llu", U(major->n), major->sum, U(major->max));
  CHECK(young->n == 2 && young->sum == 70030 && young->max == 70000, "young walked n=%llu sum=%.0f max=%llu", U(young->n), young->sum, U(young->max));
  CHECK(s->walked_k[GC_KIND_OFF].n == 0 && s->walked_k[GC_KIND_MINOR_FULL].n == 0, "kinds that did not occur have walked rows");
  fclose(f);
}

static int run_selftest(void) {
  test_walked_in_e_record();
  test_young_regions_swept();
  test_hole_exactly_filled();
  test_hole_skipped();
  test_hole_overflow();
  test_region_released_and_reused();
  test_base_reopens_and_reuses();
  test_tails();
  test_tail_list();
  test_class_region_fills();
  test_large_counts_as_held();
  test_chunked_accounting();
  test_alloc_counters_holes();
  test_alloc_counters_class();
  test_alloc_counters_base();
  test_floor_indicator();
  printf("selftest: %d checks, %d failed\n", g_checks, g_fail);
  return g_fail != 0;
}

int main(int argc, char** argv) {
  if (argc == 2 && strcmp(argv[1], "--selftest") == 0) return run_selftest();
  int check = argc == 3 && strcmp(argv[1], "--check") == 0;
  int sim = argc == 4 && strcmp(argv[1], "--policies") == 0;
  if (!check && !sim) {
    fprintf(stderr, "usage: %s --check <trace>\n       %s --policies all|<name>[,<name>...] <trace>\n       %s --selftest\n"
                    "policies: base holes holes-ovf holes-ovf-tails class-16K class-64K class-256K class-1M\n", argv[0], argv[0], argv[0]);
    return 2;
  }
  const char* path = argv[argc - 1];
  Reader* rd = calloc(1, sizeof *rd);
  rd->f = fopen(path, "rb");
  if (rd->f == NULL) { perror(path); return 2; }
  if (sim) {
    Sim* s = xcalloc(1, sizeof *s);
    if (sim_init(s, argv[2])) return 2;
    const char* perg = getenv("REPLAY_PERG");
    if (perg != NULL && perg[0] != '\0' && (g_perg = fopen(perg, "w")) == NULL) { perror(perg); return 2; }
    if (decode(rd, &sim_handlers, s)) {
      fprintf(stderr, "replay: %s: %s\n", path, g_err);
      return 1;
    }
    report(s, path);
    if (g_perg != NULL) fclose(g_perg);
    return 0;
  }
  Check c;
  memset(&c, 0, sizeof c);
  if (decode(rd, &check_handlers, &c)) {
    fprintf(stderr, "replay: %s: %s\n", path, g_err);
    return 1;
  }
  printf("ok: allocs=%llu frees=%llu collections=%llu (off=%llu major=%llu minor_young=%llu minor_full=%llu) "
         "tails=%llu vec_out=%llu bytes=%llu large=%llu last_cycle=%llu ends_with=%c\n",
         (unsigned long long)c.allocs, (unsigned long long)c.frees,
         (unsigned long long)(c.gcs[GC_KIND_OFF] + c.gcs[GC_KIND_MAJOR] + c.gcs[GC_KIND_MINOR_YOUNG] + c.gcs[GC_KIND_MINOR_FULL]),
         (unsigned long long)c.gcs[GC_KIND_OFF], (unsigned long long)c.gcs[GC_KIND_MAJOR],
         (unsigned long long)c.gcs[GC_KIND_MINOR_YOUNG], (unsigned long long)c.gcs[GC_KIND_MINOR_FULL],
         (unsigned long long)c.tails, (unsigned long long)c.vec_outs,
         (unsigned long long)c.bytes, (unsigned long long)c.large, (unsigned long long)c.last_cycle,
         g_last_tag ? g_last_tag : '-');
  return 0;
}
