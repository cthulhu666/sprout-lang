#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <limits.h>
#include <stdarg.h>
#include <string.h>
#include <regex.h>
#include <sys/socket.h>
#include <sys/types.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <netdb.h>
#include <errno.h>
#include <sys/time.h>
#include <time.h>
#include <sys/wait.h>
#include <poll.h>
#include <fcntl.h>
#include <signal.h>
#include <termios.h>
#include <unistd.h>
#include <execinfo.h>
#include <pthread.h>
#include <sys/resource.h>
#include "sprout_scheduler.h"
#ifdef __APPLE__
#pragma clang diagnostic push
#pragma clang diagnostic ignored "-Wdeprecated-declarations"
#include <Security/SecureTransport.h>
#include <Security/SecTrust.h>
#include <Security/SecCertificate.h>
#include <mach-o/dyld.h>
#endif

/* SproutObj removed — tag lives in the inline heap header at (payload_ptr - 8);
 * fields are accessed as ((long long*)payload)[idx].  The typedef keeps
 * box_ptr / unbox_ptr compiling without cascading pointer-cast changes. */
typedef void SproutObj;

typedef enum {
  SPROUT_HEAP_FREE = 0,   /* slot on a class freelist; header aux = slot_bytes */
  SPROUT_HEAP_OBJ = 1,
  SPROUT_HEAP_CLOSURE = 2,
  SPROUT_HEAP_VECTOR = 3,
  SPROUT_HEAP_MAP = 4,
  SPROUT_HEAP_BYTES = 5,
  SPROUT_HEAP_BUILDER = 6,
  SPROUT_HEAP_TUPLE = 7,
  SPROUT_HEAP_RANGE = 8,
  SPROUT_HEAP_REF = 9,
  SPROUT_HEAP_CSTR = 10
} SproutHeapKind;

typedef enum {
  SPROUT_ROOT_I64 = 1,
  SPROUT_ROOT_PTR = 2,
  SPROUT_ROOT_SCAN = 3
} SproutRootKind;

typedef struct RootNode {
  void* slot;
  SproutRootKind kind;
  size_t aux_words;
  struct RootNode* next;
} RootNode;

/* Per-task GC temp-root context (opaque `SproutRoots` in sprout_scheduler.h).
 * Bundles what used to be three file-static globals (temp-root head, pool,
 * pool top) so each green task carries its own LIFO. `g_current_roots` selects
 * the active one; the collector walks the whole registry via `reg_next`. */
struct SproutRoots {
  RootNode*         pool;      /* non-moving array of `pool_size` RootNodes */
  size_t            pool_size;
  size_t            pool_top;  /* LIFO stack pointer into `pool` */
  RootNode*         head;      /* newest pushed temp root (linked via ->next) */
  struct SproutRoots* reg_next;/* registry link over all live contexts */
};

typedef struct {
  long long tag;
  const char* name;
  long long arity;
  const char* field_kinds; /* one char per field: i=Int b=Bool s=String/Char p=ADT/closure _=type-var */
} CtorMeta;

typedef struct {
  long long len;
  long long cap;
  long long* data;
} VectorVal;

typedef struct InternBucket { char* str; struct InternBucket* next; } InternBucket;

typedef struct {
  const char* key;    /* interned string — permanent, never freed */
  long long   value;  /* Sprout handle (GC child at index 0) */
  long long   left;   /* left child handle, or 0 (GC child at index 1) */
  long long   right;  /* right child handle, or 0 (GC child at index 2) */
  int         height; /* AVL height: 1 for leaf */
  int         size;   /* subtree size for O(log n) nth-key/nth-value */
} BSTNode;

typedef struct {
  char* data;
  size_t len;
  size_t cap;
} ByteBuf;

typedef struct {
  size_t len;
  unsigned char* data;
} BytesVal;

typedef struct {
  size_t len;
  size_t count;
  BytesVal** chunks;
} BuilderVal;

typedef struct {
  long long start;
  long long end;
} IntRangeVal;

typedef struct {
  long long value;
} RefVal;

typedef struct {
  char* host;
  char* port;
  char* path;
  int use_tls;
} HttpUrl;

static InternBucket* g_intern_table[65537];
static RootNode* g_root_nodes = NULL;   /* persistent (never-popped) roots, global */
static void* g_nothing_singleton = NULL;
/* IRType (stdlib.compiler.sprout_ir) nullary-ctor singletons.  Without this,
 * every IRType construction in the IR-codegen path allocates 16 bytes — and
 * IRType values flow through every IRFunction param, every IRLoadEnvSlot,
 * every IRGetField.  Caching keeps the ADT as cheap as the string-based kind
 * convention it replaced. */
static void* g_irtheap_singleton = NULL;
static void* g_irtscalar_singleton = NULL;
static void* g_irtunknown_singleton = NULL;
static CtorMeta g_ctor_meta[2048];
static long long g_ctor_meta_len = 0;
static int g_listener_fd[2048];
static int g_listener_used[2048];
static int g_conn_fd[2048];
static int g_conn_used[2048];

static long long alloc_listener_handle(void) {
  for (long long h = 1; h < 2048; h++) {
    if (!g_listener_used[h]) return h;
  }
  return -1;
}

static long long alloc_conn_handle(void) {
  for (long long h = 1; h < 2048; h++) {
    if (!g_conn_used[h]) return h;
  }
  return -1;
}
static int g_sprout_argc = 0;
static char** g_sprout_argv = NULL;
static int g_debug_alloc_enabled = 0;
static int g_debug_alloc_report_registered = 0;
static int g_debug_gc_enabled = 0;
static int g_gc_collect_registered = 0;
static int g_gc_active = 0;
static long long g_debug_alloc_sprout_obj = 0;
static long long g_debug_alloc_closure = 0;
static long long g_debug_alloc_vector = 0;
static long long g_debug_alloc_map = 0;
static long long g_debug_alloc_bytes = 0;
static long long g_debug_alloc_builder = 0;
static long long g_debug_gc_swept = 0;
static long long g_gc_cycle_count = 0;
static long long g_managed_heap_count = 0;
static long long g_managed_alloc_since_gc = 0;
static long long g_gc_threshold = 4096;
/* Floor for the adaptive threshold (smallest object count at which a collection
   may be triggered).  Equals the initial g_gc_threshold, or SPROUT_GC_THRESHOLD
   when set.  Keeps tiny programs from GC-thrashing once the threshold tracks the
   live set. */
static long long g_gc_threshold_base = 4096;
static long long g_gc_marked_count = 0;
/* Per-type live counts and CSTR bytes after each sweep (logged with SPROUT_DEBUG_GC). */
static long long g_gc_live_obj = 0, g_gc_live_closure = 0, g_gc_live_vec = 0;
static long long g_gc_live_map = 0, g_gc_live_bytes = 0, g_gc_live_builder = 0;
static long long g_gc_live_tuple = 0, g_gc_live_range = 0, g_gc_live_ref = 0;
static long long g_gc_live_cstr = 0, g_gc_live_cstr_bytes = 0;
static double g_gc_adapt_ratio = 0.2;   /* fraction of heap swept below which threshold grows; 0 disables */
static double g_gc_adapt_factor = 2.0;  /* multiplier applied to threshold when adapting */
static long long g_gc_adapt_cap = 0;    /* uncapped; BST map is O(1) unmanaged payload per node */
/* Livelock detection: track consecutive cycles that sweep almost nothing. */
static long long g_gc_consecutive_bad_cycles = 0;
static int       g_gc_livelock_warned = 0;
static double    g_gc_livelock_ratio = 0.05;  /* sweep efficiency below which a cycle is "bad" */
static long long g_gc_livelock_cycles = 1000; /* consecutive bad cycles before triggering */
static int       g_gc_livelock_action = 1;    /* 0=off  1=warn  2=abort */
/* Crash attribution: name of the function currently being compiled to IR. */
static const char* g_sprout_current_fn = NULL;

/* Main-thread stack bounds, captured once at startup (sprout_capture_stack_bounds)
 * from a healthy stack. The SIGSEGV handler uses them to tell a stack overflow
 * (fault address at/just-below the stack) from a wild-pointer fault. */
static char* g_stack_lo = NULL;
static char* g_stack_hi = NULL;

/* async-signal-safe write of a NUL-terminated string to stderr */
static void sov_write(const char* s) {
  ssize_t r = write(STDERR_FILENO, s, strlen(s));
  (void)r;
}

/* SIGSEGV/SIGBUS/SIGABRT handler. Runs on an ALTERNATE signal stack (see
 * sprout_install_crash_handlers), which is what lets it execute at all after a
 * stack overflow has exhausted the thread stack. Uses only async-signal-safe
 * primitives (write/strlen/backtrace/_exit) — no stdio/malloc. */
static void sprout_crash_handler(int sig, siginfo_t* info, void* ucontext) {
  (void)ucontext;
  int is_overflow = 0;
  if ((sig == SIGSEGV || sig == SIGBUS) && info != NULL && g_stack_lo != NULL) {
    char* a = (char*)info->si_addr;
    /* The live stack never faults; only the guard region just below it does,
     * so any fault in this band is a stack overflow. 64 KiB of slack covers
     * the guard pages and the small bounds approximation on Linux. */
    if (a >= g_stack_lo - 65536 && a <= g_stack_hi) is_overflow = 1;
  }
  if (is_overflow) {
    sov_write("\n[sprout] fatal: stack overflow - unbounded or too-deep recursion\n");
  } else {
    sov_write("\n[sprout] ");
    sov_write((sig == SIGSEGV) ? "SIGSEGV" : (sig == SIGBUS) ? "SIGBUS" : "SIGABRT");
    if (g_sprout_current_fn != NULL) {
      sov_write(" while emitting IR for: ");
      sov_write(g_sprout_current_fn);
    }
    sov_write("\n");
  }
  /* Native backtrace. On Linux, named (vs bare-address) frames require the
   * binary to be linked with -rdynamic. */
  void* frames[64];
  int n = backtrace(frames, 64);
  backtrace_symbols_fd(frames, n, STDERR_FILENO);
  _exit(128 + sig);
}

/* Capture the main-thread stack bounds. Called once, early, from a healthy
 * stack. Allocation-free and portable (no _GNU_SOURCE / per-arch code). */
static void sprout_capture_stack_bounds(void) {
#if defined(__APPLE__)
  pthread_t self = pthread_self();
  char* hi = (char*)pthread_get_stackaddr_np(self);   /* highest address */
  size_t sz = pthread_get_stacksize_np(self);
  g_stack_hi = hi;
  g_stack_lo = hi - sz;
#else
  volatile char probe;
  char* approx_top = (char*)&probe;   /* within a few hundred bytes of the top */
  struct rlimit rl;
  size_t sz = (getrlimit(RLIMIT_STACK, &rl) == 0 && rl.rlim_cur != RLIM_INFINITY)
                ? (size_t)rl.rlim_cur : (size_t)(8 * 1024 * 1024);
  g_stack_hi = approx_top;
  g_stack_lo = approx_top - sz;
#endif
}

/* Install crash handlers on an ALTERNATE signal stack. The alternate stack is
 * essential: a stack-overflow SIGSEGV cannot run a handler on the exhausted
 * thread stack, so without SA_ONSTACK the handler re-faults and the process
 * dies silently with a bare SIGSEGV (exactly the failure this replaces). */
static void sprout_install_crash_handlers(void) {
  /* Warm up backtrace() now, on a healthy stack: glibc's first call can
   * dlopen libgcc_s (not async-signal-safe), which would be unsafe to trigger
   * from inside the handler. */
  void* warmup[4];
  (void)backtrace(warmup, 4);

  static char alt_buf[65536];   /* > MINSIGSTKSZ; ample for backtrace(64) */
  stack_t ss;
  ss.ss_sp = alt_buf;
  ss.ss_size = sizeof(alt_buf);
  ss.ss_flags = 0;
  sigaltstack(&ss, NULL);

  struct sigaction sa;
  memset(&sa, 0, sizeof(sa));
  sa.sa_sigaction = sprout_crash_handler;
  sa.sa_flags = SA_ONSTACK | SA_SIGINFO;
  sigemptyset(&sa.sa_mask);
  sigaction(SIGSEGV, &sa, NULL);
  sigaction(SIGBUS, &sa, NULL);
  sigaction(SIGABRT, &sa, NULL);
}

long long sprout_set_current_fn(const char* fn_name) {
  g_sprout_current_fn = fn_name;
  return 0;
}

__attribute__((noreturn)) static void tcp_fail(const char* msg);
/* A Sprout String is required to be valid UTF-8; raw bytes from external
 * sources (files, stdin, processes, network) are validated at ingestion. */
static int utf8_validate(const unsigned char* data, size_t len, const char** reason);
__attribute__((noreturn)) void sprout_abort_match(void);
long long sprout_make0(long long tag);
long long sprout_make1(long long tag, long long a0);
long long sprout_make2(long long tag, long long a0, long long a1);
long long sprout_make3(long long tag, long long a0, long long a1, long long a2);
long long sprout_make4(long long tag, long long a0, long long a1, long long a2, long long a3);
long long sprout_make5(long long tag, long long a0, long long a1, long long a2, long long a3, long long a4);
long long sprout_make6(long long tag, long long a0, long long a1, long long a2, long long a3, long long a4, long long a5);
long long sprout_make7(long long tag, long long a0, long long a1, long long a2, long long a3, long long a4, long long a5, long long a6);
long long sprout_make8(long long tag, long long a0, long long a1, long long a2, long long a3, long long a4, long long a5, long long a6, long long a7);
long long sprout_make9(long long tag, long long a0, long long a1, long long a2, long long a3, long long a4, long long a5, long long a6, long long a7, long long a8);
long long sprout_rebox2(long long tag, long long f0);
long long sprout_rebox3(long long tag, long long f0, long long f1);
long long sprout_tag(long long h);
long long sprout_field(long long h, long long idx);
static CtorMeta* find_ctor(long long tag);
static char* alloc_cstr(size_t len, const char* ctx);
static char* dup_slice(const char* start, size_t len);
static char* dup_cstr(const char* s);
static char* register_cstr(char* value);
static char* dup_managed_slice(const char* start, size_t len, const char* ctx);
static char* dup_managed_cstr(const char* s, const char* ctx);
static char* sprout_gc_alloc_cstr(size_t len, const char* ctx);
static char* sprout_gc_adopt_cstr(char* buf, size_t len, const char* ctx);
static void buf_init(ByteBuf* buf);
static void buf_append_bytes(ByteBuf* buf, const char* data, size_t len);
static void buf_append_cstr(ByteBuf* buf, const char* text);
static void buf_append_char(ByteBuf* buf, char ch);
static void regex_builtin_fail(const char* builtin, const char* detail);
static int regex_compile_ere(const char* pattern, regex_t* out_regex, char** out_error);
static size_t sprout_utf8_char_width(unsigned char lead);
static long long sprout_utf8_decode_at(const char* s, size_t pos, size_t width);
static long long sprout_utf8_codepoint_prefix_count(const char* s, size_t byte_limit);
static void json_append_value(ByteBuf* out, long long value);
static BytesVal* bytes_from_chunk_bytes(const unsigned char* data, size_t len, const char* ctx);
static IntRangeVal* sprout_alloc_range_val(const char* ctx);
static void sha256_digest(const unsigned char* data, size_t len, unsigned char out[32]);
static void hmac_sha256_digest(const unsigned char* key, size_t key_len, const unsigned char* msg, size_t msg_len, unsigned char out[32]);
static char* base64_encode_bytes(const unsigned char* data, size_t len);
static int base64_decode_bytes(const char* text, unsigned char** out_data, size_t* out_len, const char** err);
static void sprout_gc_collect(void);
static void sprout_gc_collect_with_reason(const char* reason);
static long long sprout_now_micros(void);
static void* sprout_heap_lookup(void* p);
static int sprout_obj_alloc_arity(int arity);
void sprout_gc_trace_alloc(void* payload);
void sprout_gc_trace_free(void* payload);
static int g_gc_stress;  /* -1 until first read; defined below */

static int sprout_debug_alloc_truthy(const char* value) {
  if (value == NULL || value[0] == '\0') return 0;
  if (strcmp(value, "0") == 0) return 0;
  if (strcmp(value, "false") == 0) return 0;
  if (strcmp(value, "off") == 0) return 0;
  return 1;
}

static void sprout_debug_alloc_report(void) {
  if (!g_debug_alloc_enabled) return;
  fprintf(
    stderr,
    "[sprout alloc] sprout_obj=%lld closure=%lld vector=%lld map=%lld bytes=%lld builder=%lld gc_swept=%lld gc_cycles=%lld\n",
    g_debug_alloc_sprout_obj,
    g_debug_alloc_closure,
    g_debug_alloc_vector,
    g_debug_alloc_map,
    g_debug_alloc_bytes,
    g_debug_alloc_builder,
    g_debug_gc_swept,
    g_gc_cycle_count
  );
}

static void sprout_debug_alloc_maybe_enable(void) {
  if (g_debug_alloc_enabled) return;
  if (!sprout_debug_alloc_truthy(getenv("SPROUT_DEBUG_ALLOC"))) return;
  g_debug_alloc_enabled = 1;
  if (!g_debug_alloc_report_registered) {
    atexit(sprout_debug_alloc_report);
    g_debug_alloc_report_registered = 1;
  }
}

static void sprout_debug_gc_maybe_enable(void) {
  if (g_debug_gc_enabled) return;
  if (!sprout_debug_alloc_truthy(getenv("SPROUT_DEBUG_GC"))) return;
  g_debug_gc_enabled = 1;
}

static void sprout_gc_threshold_maybe_enable(void) {
  const char* raw = getenv("SPROUT_GC_THRESHOLD");
  if (raw == NULL || raw[0] == '\0') return;
  if (!sprout_debug_alloc_truthy(raw)) {
    g_gc_threshold = 0;
    return;
  }
  char* end = NULL;
  long long parsed = strtoll(raw, &end, 10);
  if (end == raw || *end != '\0' || parsed <= 0) {
    tcp_fail("SPROUT_GC_THRESHOLD: expected positive integer");
  }
  g_gc_threshold = parsed;
  g_gc_threshold_base = parsed;
}

static void sprout_gc_adapt_maybe_enable(void) {
  /* SPROUT_GC_ADAPT_RATIO: swept/heap fraction below which threshold is grown.
     Default is 0.2 (grow when less than 20%% of the heap was freed).
     Set to 0 to disable adaptive GC entirely. */
  const char* ratio_raw = getenv("SPROUT_GC_ADAPT_RATIO");
  if (ratio_raw != NULL && ratio_raw[0] != '\0') {
    char* end = NULL;
    double parsed = strtod(ratio_raw, &end);
    if (end == ratio_raw || *end != '\0' || parsed < 0.0 || parsed > 1.0)
      tcp_fail("SPROUT_GC_ADAPT_RATIO: expected float in [0, 1]");
    g_gc_adapt_ratio = parsed;
  }
  /* SPROUT_GC_ADAPT_FACTOR: factor by which threshold is multiplied when the
     swept fraction is below SPROUT_GC_ADAPT_RATIO. Must be > 1. Default 2.0. */
  const char* factor_raw = getenv("SPROUT_GC_ADAPT_FACTOR");
  if (factor_raw != NULL && factor_raw[0] != '\0') {
    char* end = NULL;
    double parsed = strtod(factor_raw, &end);
    if (end == factor_raw || *end != '\0' || parsed <= 1.0)
      tcp_fail("SPROUT_GC_ADAPT_FACTOR: expected float > 1");
    g_gc_adapt_factor = parsed;
  }
  /* SPROUT_GC_ADAPT_CAP: maximum value the threshold may grow to.
     0 (the default) means no cap. */
  const char* cap_raw = getenv("SPROUT_GC_ADAPT_CAP");
  if (cap_raw != NULL && cap_raw[0] != '\0') {
    char* end = NULL;
    long long parsed = strtoll(cap_raw, &end, 10);
    if (end == cap_raw || *end != '\0' || parsed < 0)
      tcp_fail("SPROUT_GC_ADAPT_CAP: expected non-negative integer");
    g_gc_adapt_cap = parsed;
  }
}

static void sprout_gc_livelock_maybe_enable(void) {
  const char* ratio_raw = getenv("SPROUT_GC_LIVELOCK_RATIO");
  if (ratio_raw != NULL && ratio_raw[0] != '\0') {
    char* end = NULL;
    double parsed = strtod(ratio_raw, &end);
    if (end == ratio_raw || *end != '\0' || parsed < 0.0 || parsed > 1.0)
      tcp_fail("SPROUT_GC_LIVELOCK_RATIO: expected float in [0, 1]");
    g_gc_livelock_ratio = parsed;
  }
  const char* cycles_raw = getenv("SPROUT_GC_LIVELOCK_CYCLES");
  if (cycles_raw != NULL && cycles_raw[0] != '\0') {
    char* end = NULL;
    long long parsed = strtoll(cycles_raw, &end, 10);
    if (end == cycles_raw || *end != '\0' || parsed < 0)
      tcp_fail("SPROUT_GC_LIVELOCK_CYCLES: expected non-negative integer");
    g_gc_livelock_cycles = parsed;
  }
  const char* action_raw = getenv("SPROUT_GC_LIVELOCK_ACTION");
  if (action_raw != NULL && action_raw[0] != '\0') {
    if (strcmp(action_raw, "off") == 0 || strcmp(action_raw, "0") == 0)
      g_gc_livelock_action = 0;
    else if (strcmp(action_raw, "warn") == 0)
      g_gc_livelock_action = 1;
    else if (strcmp(action_raw, "abort") == 0)
      g_gc_livelock_action = 2;
    else
      tcp_fail("SPROUT_GC_LIVELOCK_ACTION: expected off, warn, or abort");
  }
}

static void sprout_gc_maybe_register(void) {
  if (g_gc_collect_registered) return;
  atexit(sprout_gc_collect);
  g_gc_collect_registered = 1;
}

static long long sprout_now_micros(void) {
  struct timeval tv;
  if (gettimeofday(&tv, NULL) != 0) return 0;
  return ((long long)tv.tv_sec * 1000000LL) + (long long)tv.tv_usec;
}

/* Monotonic microsecond counter (CLOCK_MONOTONIC).
 * Intended use: elapsed-time measurement (benchmarks, timeouts).
 * The epoch is unspecified — only differences between two calls are meaningful.
 * DO NOT use for wall-clock timestamps; use gettimeofday / CLOCK_REALTIME for those. */
long long time_now_micros(void) {
  struct timespec ts;
  if (clock_gettime(CLOCK_MONOTONIC, &ts) != 0) return 0;
  return (long long)ts.tv_sec * 1000000LL + (long long)ts.tv_nsec / 1000LL;
}

/* Wall-clock microseconds since the Unix epoch (gettimeofday / CLOCK_REALTIME).
 * Intended use: log timestamps and other civil-time rendering.
 * NOT monotonic — subject to NTP steps and manual clock changes, so it must not
 * be used for elapsed-time measurement (use time_now_micros for that).
 * Exposes the same value as the internal sprout_now_micros. */
long long wall_time_micros(void) {
  return sprout_now_micros();
}

static void sprout_gc_log_cycle(
  const char* reason,
  long long heap_before,
  long long heap_after,
  long long root_count,
  long long marked_count,
  long long alloc_since_gc,
  long long swept_delta,
  long long elapsed_us
) {
  if (!g_debug_gc_enabled) return;
  fprintf(
    stderr,
    "[sprout gc] cycle=%lld reason=%s threshold=%lld heap_before=%lld heap_after=%lld live=%lld roots=%lld marked=%lld alloc_since_gc=%lld swept=%lld elapsed_us=%lld\n",
    g_gc_cycle_count,
    reason,
    g_gc_threshold,
    heap_before,
    heap_after,
    heap_after,
    root_count,
    marked_count,
    alloc_since_gc,
    swept_delta,
    elapsed_us
  );
  fprintf(
    stderr,
    "[sprout gc]   types: obj=%lld closure=%lld vec=%lld map=%lld ref=%lld cstr=%lld(%.1fKB) bytes=%lld builder=%lld tuple=%lld range=%lld\n",
    g_gc_live_obj, g_gc_live_closure, g_gc_live_vec, g_gc_live_map,
    g_gc_live_ref, g_gc_live_cstr, (double)g_gc_live_cstr_bytes / 1024.0,
    g_gc_live_bytes, g_gc_live_builder, g_gc_live_tuple, g_gc_live_range
  );
}

/* SPROUT_GC_LINEAGE=1 — diagnostic mode for the rooting use-after-free class.
   When on, swept OBJ payloads are NOT recycled onto the freelist; instead the
   header kind is poisoned to SPROUT_GC_POISON (0xFF) and the block is leaked.
   A later sprout_tag read on a dangling reference sees the poison kind and
   aborts at the exact use site instead of silently walking a reused object.
   Leaking is acceptable: this mode is for short diagnostic runs only. */
static int g_gc_lineage = -1;
static int sprout_gc_lineage_on(void) {
  if (g_gc_lineage < 0) { const char* e = getenv("SPROUT_GC_LINEAGE"); g_gc_lineage = (e && e[0] == '1') ? 1 : 0; }
  return g_gc_lineage;
}

/* SPROUT_GC_HDRCHECK=1 — consistency assert: a CSTR header's aux byte length
 * must match strlen at sweep time.  Also enabled implicitly when
 * SPROUT_GC_LINEAGE=1 (both catch the same class of corruption). */
static int g_gc_hdrcheck = -1;
static int sprout_gc_hdrcheck_on(void) {
  if (g_gc_hdrcheck < 0) { const char* e = getenv("SPROUT_GC_HDRCHECK"); g_gc_hdrcheck = (e && e[0] == '1') ? 1 : 0; }
  return g_gc_hdrcheck || g_gc_lineage;
}

/* ── Inline heap header (64-bit word at payload_ptr - 8) ────────────────────
 * Layout: bits 0-7  = SproutHeapKind (kind)
 *         bits 8-9  = GC color (unused in this phase, written as 0)
 *         bits 10-13 = reserved GC bits (written as 0)
 *         bits 14-63 = aux (50 bits); interpretation per kind:
 *           OBJ:     (tag << 4) | arity  — low 4 bits = ctor arity (0..9)
 *           CLOSURE: n_caps
 *           TUPLE:   width in words
 *           others:  0
 * SPROUT_GC_POISON (0xFF) written to kind bits marks a lineage-mode corpse. */
#define SPROUT_GC_POISON ((uint64_t)0xFF)

static inline uint64_t sprout_hdr_make(SproutHeapKind kind, unsigned long long aux) {
  return (uint64_t)(kind & 0xFF) | ((uint64_t)aux << 14);
}
static inline uint64_t sprout_hdr_of(void* payload) {
  uint64_t h; memcpy(&h, (char*)payload - 8, 8); return h;
}
static inline SproutHeapKind sprout_hdr_kind(uint64_t h) {
  return (SproutHeapKind)(h & 0xFF);
}
static inline unsigned long long sprout_hdr_aux(uint64_t h) {
  return h >> 14;
}

/* Write both kind and aux into the header word at (payload - 8). */
static inline void sprout_hdr_write(void* payload, SproutHeapKind kind, unsigned long long aux) {
  uint64_t h = sprout_hdr_make(kind, aux);
  memcpy((char*)payload - 8, &h, 8);
}

/* Write tag into OBJ header keeping the arity nibble intact.
 * Must be called before first use of sprout_tag on this payload.
 * Arity must fit in 4 bits (0..9); checked in debug mode. */
static inline void sprout_obj_write_tag(void* payload, long long tag, int arity) {
  /* Debug arity-range check: 4-bit nibble holds 0..9 (max ctor arity). */
  if (sprout_gc_hdrcheck_on() && (arity < 0 || arity > 9)) {
    fprintf(stderr, "[sprout] sprout_obj_write_tag: arity %d out of 0..9 range\n", arity);
    abort();
  }
  sprout_hdr_write(payload, SPROUT_HEAP_OBJ,
                   ((unsigned long long)tag << 4) | (unsigned long long)(unsigned int)arity);
}

/* ── Region-based arena allocator ─────────────────────────────────────────
 * One 1-MiB chunk = one Region. Objects land at slot-aligned offsets;
 * a 1-bit slotmap (1 bit per 16-byte slot) tracks live starts so that
 * sprout_heap_lookup can distinguish a real payload from an interior word
 * (membership exactness). Large objects (slot > 4096 bytes) get their own
 * single-slot region entry with is_large=1 and slotmap=NULL. */
#define SPROUT_REGION_SIZE  ((size_t)(1u << 20))   /* 1 MiB */
#define SPROUT_SLOT_GRAN    16                       /* allocation granularity */
#define SPROUT_SLOTS_PER_REGION (SPROUT_REGION_SIZE / SPROUT_SLOT_GRAN) /* 65536 */
#define SPROUT_SLOTMAP_BYTES    (SPROUT_SLOTS_PER_REGION / 8)           /* 8192 */
#define SPROUT_LARGE_THRESHOLD  4096                /* slot_bytes > this → large */
#define SPROUT_GC_COLOR_BIT     ((uint64_t)0x100)  /* bit 8 = mark color */
#define SPROUT_FREELIST_CLASSES 257                 /* classes 1..256 + sentinel */

typedef struct {
  char*     base;         /* malloc'd 1-MiB block (or single large object block) */
  size_t    cap;          /* bytes in block (SPROUT_REGION_SIZE or slot_bytes) */
  size_t    bump;         /* next free byte offset from base */
  long long live_count;   /* objects marked live in last sweep */
  long long poison_count; /* lineage corpses in this region (never released while > 0) */
  uint8_t*  slotmap;    /* 8192-byte bitmap (NULL for is_large) */
  int       is_large;   /* 1 = single large-object region */
} SproutRegion;

static SproutRegion* g_regions = NULL;  /* sorted by base address */
static size_t        g_region_count = 0;
static size_t        g_region_cap   = 0;
/* Hint index of a normal region with bump room; validated before use and
 * refreshed on miss — table inserts/removals may shift it. */
static size_t        g_open_region_hint = 0;

/* Class freelist: index = slot_bytes/16 (class 1..256 for <= 4096-byte slots).
 * Free slot's header word = {kind=SPROUT_HEAP_FREE, aux=slot_bytes}.
 * Next pointer stored in payload word 0 (slot >= 16 bytes guarantees room). */
static void* g_freelist[SPROUT_FREELIST_CLASSES];  /* index 0 unused */

/* Round up to next multiple of 16. */
static inline size_t round16(size_t n) {
  return (n + 15u) & ~(size_t)15u;
}

/* Slot size for a heap object: header (8) + payload, rounded to 16. Must match
 * round16(8 + payload_bytes) at every alloc call site exactly, or the sweep
 * slot-walk desyncs. */
static size_t slot_bytes(SproutHeapKind kind, unsigned long long aux) {
  size_t payload = 0;
  switch (kind) {
    case SPROUT_HEAP_OBJ:
      /* aux low nibble = ctor arity; alloc pads to sprout_obj_alloc_arity in
       * lineage mode, so mirror that here to stay slot-consistent. */
      payload = (size_t)sprout_obj_alloc_arity((int)(aux & 0xF)) * 8;
      break;
    case SPROUT_HEAP_CLOSURE: payload = ((size_t)aux + 1) * 8;   break; /* n_caps+1 slots (slot0=code) */
    case SPROUT_HEAP_VECTOR:  payload = sizeof(VectorVal);       break;
    case SPROUT_HEAP_MAP:     payload = sizeof(BSTNode);         break;
    case SPROUT_HEAP_BYTES:   payload = sizeof(BytesVal);        break;
    case SPROUT_HEAP_BUILDER: payload = sizeof(BuilderVal);      break;
    case SPROUT_HEAP_TUPLE:   payload = (size_t)aux * 8;        break;
    case SPROUT_HEAP_RANGE:   payload = sizeof(IntRangeVal);     break;
    case SPROUT_HEAP_REF:     payload = sizeof(RefVal);          break;
    case SPROUT_HEAP_CSTR:    payload = (size_t)aux + 1;        break; /* aux=len, +1 for NUL */
    default:                  payload = 8;                      break;
  }
  if (payload < 8) payload = 8; /* minimum 1 payload word for freelist next-ptr */
  return round16(8 + payload);
}

/* Insert a new region into the table, keeping it sorted by base address. */
static void region_table_insert(SproutRegion r) {
  if (g_region_count >= g_region_cap) {
    size_t new_cap = g_region_cap < 16 ? 16 : g_region_cap * 2;
    SproutRegion* t = (SproutRegion*)realloc(g_regions, new_cap * sizeof(SproutRegion));
    if (t == NULL) tcp_fail("region_table_insert: out of memory");
    g_regions = t;
    g_region_cap = new_cap;
  }
  size_t lo = 0, hi = g_region_count;
  while (lo < hi) {
    size_t mid = lo + (hi - lo) / 2;
    if ((uintptr_t)g_regions[mid].base < (uintptr_t)r.base) lo = mid + 1;
    else hi = mid;
  }
  memmove(&g_regions[lo + 1], &g_regions[lo], (g_region_count - lo) * sizeof(SproutRegion));
  g_regions[lo] = r;
  g_region_count++;
}

/* Remove region at index i; region's base/slotmap already freed by caller. */
static void region_table_remove(size_t i) {
  memmove(&g_regions[i], &g_regions[i + 1], (g_region_count - i - 1) * sizeof(SproutRegion));
  g_region_count--;
}

/* Open a fresh 1-MiB region and append it to the table. */
static SproutRegion* open_new_region(void) {
  char* base = (char*)malloc(SPROUT_REGION_SIZE);
  if (base == NULL) tcp_fail("open_new_region: out of memory");
  uint8_t* slotmap = (uint8_t*)calloc(SPROUT_SLOTMAP_BYTES, 1);
  if (slotmap == NULL) { free(base); tcp_fail("open_new_region: out of memory for slotmap"); }
  SproutRegion r;
  r.base = base;
  r.cap = SPROUT_REGION_SIZE;
  r.bump = 0;
  r.live_count = 0;
  r.poison_count = 0;
  r.slotmap = slotmap;
  r.is_large = 0;
  region_table_insert(r);
  /* Binary search for the entry we just inserted (table may have realloc'd). */
  size_t lo = 0, hi = g_region_count;
  while (lo < hi) {
    size_t mid = lo + (hi - lo) / 2;
    if ((uintptr_t)g_regions[mid].base < (uintptr_t)base) lo = mid + 1;
    else hi = mid;
  }
  g_open_region_hint = lo;
  return &g_regions[lo];
}

/* Find the region whose base <= p < base+cap, or NULL. Binary search. */
static SproutRegion* region_find(void* p) {
  uintptr_t addr = (uintptr_t)p;
  size_t lo = 0, hi = g_region_count;
  while (lo < hi) {
    size_t mid = lo + (hi - lo) / 2;
    uintptr_t rbase = (uintptr_t)g_regions[mid].base;
    if (rbase > addr) hi = mid;
    else if (rbase + g_regions[mid].cap <= addr) lo = mid + 1;
    else return &g_regions[mid];
  }
  return NULL;
}

/* ── GC profiling instrumentation (opt-in, off by default) ────────────────
 * HOT counters (per-lookup / per-edge / per-slot) are gated at COMPILE time
 * behind -DSPROUT_GC_PROFILE, so they are byte-for-byte absent from a default
 * build.  COLD per-cycle counters and the exit dump are gated at RUNTIME on
 * SPROUT_GC_PROFILE=1, so a profiling build stays silent until asked.
 *
 * Build a profiling binary, then run it:
 *   clang <ir>.ll runtime/sprout_runtime.c -O2 -DSPROUT_GC_PROFILE ... -o bin
 *   SPROUT_GC_PROFILE=1 ./bin   # dumps "[gc profile] ..." at exit */
#if defined(SPROUT_GC_PROFILE)
static unsigned long long g_prof_fmp_calls;                        /* hot */
static unsigned long long g_prof_drain_edges, g_prof_sweep_visits; /* hot */
static unsigned long long g_prof_trace_hits, g_prof_trace_misses;  /* hot: drain-phase lookup outcomes */
static unsigned long long g_prof_freelist_hits;                    /* hot: alloc reuse from freelist */
static unsigned long long g_prof_cycles, g_prof_mark_slots, g_prof_gc_us; /* cold */
static int g_prof_enabled = -1;
static int g_gc_tracing = 0;   /* set to 1 during drain to classify hit/miss */
static int sprout_prof_on(void) {
  if (g_prof_enabled < 0) {
    const char* e = getenv("SPROUT_GC_PROFILE");
    g_prof_enabled = (e && e[0] == '1') ? 1 : 0;
  }
  return g_prof_enabled;
}
/* Note a sprout_heap_lookup outcome; called once per invocation. */
static void sprout_prof_note_probe(int hit) {
  if (g_gc_tracing) {
    if (hit) g_prof_trace_hits++;
    else     g_prof_trace_misses++;
  }
}
#  define SPROUT_PROF_HOT(stmt)  do { stmt; } while (0)
#  define SPROUT_PROF_COLD(stmt) do { if (sprout_prof_on()) { stmt; } } while (0)
__attribute__((destructor)) static void sprout_gc_profile_dump(void) {
  if (!sprout_prof_on()) return;
  fprintf(stderr,
    "[gc profile] cycles=%llu heap_lookup_calls=%llu drain_edges=%llu "
    "sweep_visits=%llu mark_root_slots=%llu gc_us=%llu "
    "trace_hits=%llu trace_misses=%llu "
    "region_count=%zu freelist_hits=%llu\n",
    g_prof_cycles, g_prof_fmp_calls, g_prof_drain_edges,
    g_prof_sweep_visits, g_prof_mark_slots, g_prof_gc_us,
    g_prof_trace_hits, g_prof_trace_misses,
    g_region_count, g_prof_freelist_hits);
}
#else
#  define SPROUT_PROF_HOT(stmt)  ((void)0)
#  define SPROUT_PROF_COLD(stmt) ((void)0)
#endif

/* Allocate one heap slot from the region arena. Selects from per-class freelist,
 * bump-allocates in the current open region, or opens a new region. Large objects
 * (slot_bytes > SPROUT_LARGE_THRESHOLD) get a dedicated malloc block.
 * Writes the header, sets slotmap bit, increments counters, returns payload ptr. */
static void* sprout_gc_alloc_block(SproutHeapKind kind, unsigned long long aux,
                                   size_t payload_bytes, const char* ctx) {
  size_t needed_slot = round16(8 + payload_bytes);
  if (needed_slot > SPROUT_LARGE_THRESHOLD) {
    /* Large object path: dedicated malloc block. */
    char* block = (char*)malloc(needed_slot);
    if (block == NULL) tcp_fail(ctx);
    sprout_hdr_write(block + 8, kind, aux);
    SproutRegion r;
    r.base = block;
    r.cap = needed_slot;
    r.bump = needed_slot;
    r.live_count = 0;
    r.poison_count = 0;
    r.slotmap = NULL;
    r.is_large = 1;
    region_table_insert(r);
    g_managed_heap_count++;
    g_managed_alloc_since_gc++;
    if (g_gc_stress == 1 || sprout_gc_lineage_on()) sprout_gc_trace_alloc(block + 8);
    return block + 8;
  }
  size_t cls = needed_slot / SPROUT_SLOT_GRAN;  /* class 1..256 */
  if (cls < 1) cls = 1;
  /* Try freelist. */
  if (g_freelist[cls] != NULL) {
    void* payload = g_freelist[cls];
    void* next;
    memcpy(&next, payload, sizeof(void*));  /* next-ptr in payload word 0 */
    g_freelist[cls] = next;
    /* Slotmap bit stayed set while the slot was FREE (sprout_heap_lookup
     * rejects FREE by header kind), so reuse is header-write only. */
    sprout_hdr_write(payload, kind, aux);
    SPROUT_PROF_HOT(g_prof_freelist_hits++);
    g_managed_heap_count++;
    g_managed_alloc_since_gc++;
    if (g_gc_stress == 1 || sprout_gc_lineage_on()) sprout_gc_trace_alloc(payload);
    return payload;
  }
  /* Bump allocate: try the hinted open region, else scan, else open one. */
  SproutRegion* r = NULL;
  if (g_open_region_hint < g_region_count) {
    SproutRegion* cand = &g_regions[g_open_region_hint];
    if (!cand->is_large && cand->bump + needed_slot <= cand->cap) r = cand;
  }
  if (r == NULL) {
    for (size_t i = g_region_count; i > 0; i--) {
      SproutRegion* cand = &g_regions[i - 1];
      if (!cand->is_large && cand->bump + needed_slot <= cand->cap) {
        r = cand;
        g_open_region_hint = i - 1;
        break;
      }
    }
  }
  if (r == NULL) {
    r = open_new_region();
  }
  size_t off = r->bump;
  r->bump += needed_slot;
  char* slot_base = r->base + off;
  sprout_hdr_write(slot_base + 8, kind, aux);
  size_t slot_idx = off / SPROUT_SLOT_GRAN;
  r->slotmap[slot_idx / 8] |= (uint8_t)(1u << (slot_idx % 8));
  g_managed_heap_count++;
  g_managed_alloc_since_gc++;
  if (g_gc_stress == 1 || sprout_gc_lineage_on()) sprout_gc_trace_alloc(slot_base + 8);
  return slot_base + 8;   /* payload pointer */
}

/* Allocate a GC-managed CSTR block with an inline 8-byte header at (payload-8).
 * Header aux = byte length (excluding NUL terminator).
 * The slot is already arena-registered by sprout_gc_alloc_block; the caller
 * only fills payload[0..len-1] and sets payload[len] = '\0'.
 * Never returns NULL (tcp_fail on OOM). */
static char* sprout_gc_alloc_cstr(size_t len, const char* ctx) {
  return (char*)sprout_gc_alloc_block(SPROUT_HEAP_CSTR, (unsigned long long)len,
                                      len + 1, ctx);
}

/* Adopt a plain malloc'd buffer into a GC-headered CSTR block.
 * Allocates a new headered (arena-registered) block, copies len bytes + NUL,
 * frees buf.
 *
 * INVARIANT: buf must be a plain malloc'd buffer — NEVER a headered arena CSTR
 * (which would cause free() to corrupt the block by ignoring the 8-byte prefix). */
static char* sprout_gc_adopt_cstr(char* buf, size_t len, const char* ctx) {
  char* out = sprout_gc_alloc_cstr(len, ctx);
  if (len > 0) memcpy(out, buf, len);
  out[len] = '\0';
  free(buf);
  return out;
}

/* lldb anchors for scripts/gc_free_trace.py (`just gc-trace`): empty noinline
 * functions fired per alloc/free ONLY under stress or lineage mode, so the
 * tracer has stable symbols to break on with the payload as the argument. */
__attribute__((noinline, used)) void sprout_gc_trace_alloc(void* payload) { (void)payload; }
__attribute__((noinline, used)) void sprout_gc_trace_free(void* payload) { (void)payload; }

static int g_gc_stress = -1;
static void sprout_gc_maybe_collect_threshold(void) {
  if (g_gc_stress < 0) { const char* e = getenv("SPROUT_GC_STRESS"); g_gc_stress = (e && e[0]=='1') ? 1 : 0; }
  if (g_gc_stress || (g_gc_threshold > 0 && !g_gc_active && g_managed_heap_count >= g_gc_threshold)) {
    sprout_gc_collect_with_reason("threshold");
  }
}

static void* sprout_alloc_counted(long long* counter, size_t size, const char* ctx) {
  if (g_debug_alloc_enabled) (*counter)++;
  void* out = malloc(size);
  if (out == NULL) tcp_fail(ctx);
  return out;
}

static void* sprout_realloc_counted(long long* counter, void* ptr, size_t size, const char* ctx) {
  if (g_debug_alloc_enabled) (*counter)++;
  void* out = realloc(ptr, size);
  if (out == NULL) tcp_fail(ctx);
  return out;
}

static long long box_ptr(void* p) {
  return (long long)(uintptr_t)p;
}

static void* unbox_ptr(long long h) {
  return (void*)(uintptr_t)h;
}

/* Exact membership: p is a live payload iff (a) it falls in a region, (b) the
 * offset p-base is 8 mod 16 (payloads sit at slot+8), AND (c) the slotmap bit
 * for that slot is set (cleared while free or not-yet-allocated). Returns the
 * header address (p-8) if member, NULL otherwise.
 * For is_large entries: member iff p == base+8. O(log region_count). */
static void* sprout_heap_lookup(void* p) {
  SPROUT_PROF_HOT(g_prof_fmp_calls++);
  SproutRegion* r = region_find(p);
  if (r == NULL) {
    SPROUT_PROF_HOT(sprout_prof_note_probe(0));
    return NULL;
  }
  if (r->is_large) {
    if (p == r->base + 8) {
      SPROUT_PROF_HOT(sprout_prof_note_probe(1));
      return r->base;   /* header at base, payload at base+8 */
    }
    SPROUT_PROF_HOT(sprout_prof_note_probe(0));
    return NULL;
  }
  size_t off = (size_t)((char*)p - r->base);
  /* Payload must be at slot+8; slot must be 16-aligned. */
  if (off < 8 || (off - 8) % SPROUT_SLOT_GRAN != 0) {
    SPROUT_PROF_HOT(sprout_prof_note_probe(0));
    return NULL;
  }
  size_t slot = (off - 8) / SPROUT_SLOT_GRAN;  /* slot index of containing slot */
  if (!(r->slotmap[slot / 8] & (1u << (slot % 8)))) {
    SPROUT_PROF_HOT(sprout_prof_note_probe(0));
    return NULL;
  }
  /* FREE slots keep their slotmap bit (reuse is then header-write only);
   * reject them here so dangling pointers to freed slots still miss. */
  uint64_t h; memcpy(&h, (char*)p - 8, 8);
  if ((h & 0xFF) == SPROUT_HEAP_FREE) {
    SPROUT_PROF_HOT(sprout_prof_note_probe(0));
    return NULL;
  }
  SPROUT_PROF_HOT(sprout_prof_note_probe(1));
  return (char*)p - 8;  /* header address */
}

static void register_root_slot(void* slot, SproutRootKind kind, size_t aux_words) {
  RootNode* node = (RootNode*)malloc(sizeof(RootNode));
  if (node == NULL) tcp_fail("register_root_slot: out of memory");
  node->slot = slot;
  node->kind = kind;
  node->aux_words = aux_words;
  node->next = g_root_nodes;
  g_root_nodes = node;
}

/* Allocation arity for an OBJ with the given ctor arity.
 * In lineage mode the corpse must hold payload[0]=frames and payload[1]=count
 * (poison backtrace slots), so objects smaller than 2 payload words are padded.
 * In normal builds this always returns arity unchanged — no overhead. */
static int sprout_obj_alloc_arity(int arity) {
  return (sprout_gc_lineage_on() && arity < 2) ? 2 : arity;
}


static void* sprout_alloc_obj_raw(int arity, const char* ctx) {
  sprout_gc_maybe_collect_threshold();
  int aa = sprout_obj_alloc_arity(arity);
  /* payload_bytes = aa (padded) fields; tag moves into the header (not payload).
   * aux uses ctor arity (not padded aa) so sprout_hdr_aux gives the real arity.
   * sprout_obj_write_tag overwrites this header with the final (tag<<4)|arity. */
  void* payload = sprout_gc_alloc_block(SPROUT_HEAP_OBJ, (unsigned long long)arity,
                                        (size_t)aa * 8, ctx);
  if (g_debug_alloc_enabled) g_debug_alloc_sprout_obj++;
  /* Zero padding slots (lineage only: aa > arity); no-op in normal builds. */
  for (int i = arity; i < aa; i++) ((long long*)payload)[i] = 0;
  return payload;
}

static long long sprout_make_registered_obj(int arity, long long tag, long long a0, long long a1, long long a2, const char* ctx) {
  void* payload = sprout_alloc_obj_raw(arity, ctx);
  sprout_obj_write_tag(payload, tag, arity);
  if (arity >= 1) ((long long*)payload)[0] = a0;
  if (arity >= 2) ((long long*)payload)[1] = a1;
  if (arity >= 3) ((long long*)payload)[2] = a2;
  return box_ptr(payload);
}

long long sprout_alloc_closure_env(long long size) {
  if (size < 0) tcp_fail("sprout_alloc_closure_env: size must be >= 0");
  sprout_gc_maybe_collect_threshold();
  /* SYNC WITH stdlib/compiler/ir_lowering.sprout IRMkClosure lowering (FIX R4#8):
   *   IR computes size = (n_caps + 1) * 8.
   *   Runtime computes aux_slots = (size / 8) - 1 = n_caps.
   * Any layout change must update both formulas. */
  size_t slots = size == 0 ? 0 : (((size_t)size / sizeof(long long)) - 1);
  void* out = sprout_gc_alloc_block(SPROUT_HEAP_CLOSURE, (unsigned long long)slots,
                                    (size_t)size, "sprout_alloc_closure_env: out of memory");
  if (g_debug_alloc_enabled) g_debug_alloc_closure++;
  /* FIX R4#9: zero-init the env payload to eliminate any window between
   * allocation and the IR-emitted capture stores where GC could observe
   * uninitialized slot values. */
  if (size > 0) memset(out, 0, (size_t)size);
  return (long long)(uintptr_t)out;
}

long long sprout_gc_register_i64_root(void* slot) {
  register_root_slot(slot, SPROUT_ROOT_I64, 0);
  return 0;
}

long long sprout_gc_register_ptr_root(void* slot) {
  register_root_slot(slot, SPROUT_ROOT_PTR, 0);
  return 0;
}

long long sprout_gc_register_scan_root(void* slot, long long size_bytes) {
  if (size_bytes < 0) tcp_fail("sprout_gc_register_scan_root: size must be >= 0");
  register_root_slot(slot, SPROUT_ROOT_SCAN, ((size_t)size_bytes) / sizeof(uintptr_t));
  return 0;
}

/* GC temp-root pool: push/pop is always LIFO (stack discipline enforced by
 * codegen), so a bump-index pool is sufficient and avoids malloc on every
 * sprout_gc_push_i64_root call in the lexer hot path. Task-0 (`main`) uses the
 * static 131072-slot pool below (4 MiB BSS; sized for deeply recursive compiler
 * passes). Green tasks get their own smaller pools via sprout_roots_new().
 * Cooperative switches only happen at yield points (never mid push/pop pair),
 * so head == &pool[pool_top-1] holds per context.
 * NOTE: the real fix for depth is TCO in recursive Sprout functions
 * (scan_lines et al.); this is a safety margin for call chains that grow with
 * stdlib size. */
#define SPROUT_ROOT_POOL_SIZE 131072
static RootNode g_root_pool[SPROUT_ROOT_POOL_SIZE];

/* Task-0 root context, backed by the static pool. Registered as the sole live
 * context at startup; `main` runs on it. */
static struct SproutRoots g_task0_roots = {
  g_root_pool, SPROUT_ROOT_POOL_SIZE, 0, NULL, NULL
};
static struct SproutRoots* g_current_roots  = &g_task0_roots;
static struct SproutRoots* g_roots_registry = &g_task0_roots;

static long long sprout_gc_push_root(void* slot, SproutRootKind kind, size_t aux_words) {
  struct SproutRoots* rc = g_current_roots;
  if (rc->pool_top >= rc->pool_size)
    tcp_fail("sprout_gc_push_root: GC root pool exhausted");
  RootNode* node = &rc->pool[rc->pool_top++];
  node->slot = slot;
  node->kind = kind;
  node->aux_words = aux_words;
  node->next = rc->head;
  rc->head = node;
  return 0;
}

long long sprout_gc_push_i64_root(void* slot) {
  return sprout_gc_push_root(slot, SPROUT_ROOT_I64, 0);
}

long long sprout_gc_push_ptr_root(void* slot) {
  return sprout_gc_push_root(slot, SPROUT_ROOT_PTR, 0);
}

long long sprout_gc_push_scan_root(void* slot, long long size_bytes) {
  if (size_bytes < 0) tcp_fail("sprout_gc_push_scan_root: size must be >= 0");
  return sprout_gc_push_root(slot, SPROUT_ROOT_SCAN, ((size_t)size_bytes) / sizeof(uintptr_t));
}

long long sprout_gc_pop_roots(long long count) {
  if (count < 0) tcp_fail("sprout_gc_pop_roots: count must be >= 0");
  struct SproutRoots* rc = g_current_roots;
  for (long long i = 0; i < count; i++) {
    if (rc->head == NULL) tcp_fail("sprout_gc_pop_roots: root stack underflow");
    if (rc->pool_top == 0) tcp_fail("sprout_gc_pop_roots: root pool underflow");
    RootNode* next = rc->head->next;
    rc->pool_top--;
    rc->head = next;
  }
  return 0;
}

/* ── Per-task root-context API (declared in sprout_scheduler.h) ────────────────
 * The cooperative scheduler (sprout_scheduler.c) owns task creation and switching;
 * these entry points let it allocate, select, and free a task's root context
 * without reaching into the struct. */
SproutRoots* sprout_roots_current(void) { return g_current_roots; }

SproutRoots* sprout_roots_main(void) { return &g_task0_roots; }

void sprout_roots_switch(SproutRoots* r) { g_current_roots = r; }

SproutRoots* sprout_roots_new(size_t pool_slots) {
  struct SproutRoots* rc = (struct SproutRoots*)malloc(sizeof(*rc));
  if (rc == NULL) tcp_fail("sprout_roots_new: out of memory");
  rc->pool = (RootNode*)malloc(pool_slots * sizeof(RootNode));
  if (rc->pool == NULL) tcp_fail("sprout_roots_new: out of memory (pool)");
  rc->pool_size = pool_slots;
  rc->pool_top  = 0;
  rc->head      = NULL;
  /* Register so the collector scans this context from now on. */
  rc->reg_next    = g_roots_registry;
  g_roots_registry = rc;
  return rc;
}

void sprout_roots_push_ptr(SproutRoots* r, void* slot) {
  if (r->pool_top >= r->pool_size)
    tcp_fail("sprout_roots_push_ptr: GC root pool exhausted");
  RootNode* node = &r->pool[r->pool_top++];
  node->slot = slot;
  node->kind = SPROUT_ROOT_PTR;
  node->aux_words = 0;
  node->next = r->head;
  r->head = node;
}

void sprout_roots_free(SproutRoots* r) {
  /* Unregister from the linked registry, then free pool + context. */
  struct SproutRoots** link = &g_roots_registry;
  while (*link != NULL && *link != r) link = &(*link)->reg_next;
  if (*link == r) *link = r->reg_next;
  free(r->pool);
  free(r);
}

#define SPROUT_GC_PUSH_I64_LOCAL(slot_name) do {   long long sprout_gc_tmp_ignored = sprout_gc_push_i64_root(&(slot_name));   (void)sprout_gc_tmp_ignored; } while (0)

#define SPROUT_GC_PUSH_PTR_LOCAL(slot_name) do {   long long sprout_gc_tmp_ignored = sprout_gc_push_ptr_root(&(slot_name));   (void)sprout_gc_tmp_ignored; } while (0)

#define SPROUT_GC_POP_LOCALS(count_value) do {   long long sprout_gc_tmp_ignored = sprout_gc_pop_roots((count_value));   (void)sprout_gc_tmp_ignored; } while (0)

/* ── GC Handle Table ─────────────────────────────────────────────────────
 * Handles root GC-managed values in C builtins without manual push/pop
 * accounting.  A SproutHandle is an index into a fixed table; the GC scans
 * all in-use slots during mark_roots.  Handles are released automatically
 * via __attribute__((cleanup)) when the C variable goes out of scope —
 * on ALL exit paths including early returns and error branches.
 *
 * Moving-GC path: update g_handle_table[h].value on object relocation;
 * all reads through sprout_handle_get() see the new address automatically.
 * The shadow root stack (sprout_gc_push_i64_root / sprout_gc_pop_roots)
 * remains in use for generated Sprout IR code; handles are for C builtins.
 */
/* CPR unboxed return types: returned in registers by _unboxed extern variants.
 * The caller (codegen) extracts tag + fields via LLVM extractvalue and never
 * heap-allocates the ADT wrapper when the result is immediately pattern-matched. */
typedef struct { int64_t tag; int64_t f0; }             SproutUnboxed2;
typedef struct { int64_t tag; int64_t f0; int64_t f1; } SproutUnboxed3;

#define SPROUT_HANDLE_TABLE_SIZE 1024
typedef int32_t SproutHandle;
#define SPROUT_INVALID_HANDLE ((SproutHandle)-1)

typedef struct { long long value; uint8_t in_use; } SproutHandleSlot;

static SproutHandleSlot g_handle_table[SPROUT_HANDLE_TABLE_SIZE];
static SproutHandle     g_handle_freelist[SPROUT_HANDLE_TABLE_SIZE];
static int              g_handle_freelist_top = 0;

static void sprout_handle_table_init(void) {
  for (int i = 0; i < SPROUT_HANDLE_TABLE_SIZE; i++) {
    g_handle_table[i].in_use = 0;
    g_handle_freelist[i] = (SproutHandle)(SPROUT_HANDLE_TABLE_SIZE - 1 - i);
  }
  g_handle_freelist_top = SPROUT_HANDLE_TABLE_SIZE;
}

__attribute__((constructor))
static void sprout_handle_table_ctor(void) { sprout_handle_table_init(); }

/* Ignore SIGPIPE process-globally: writing to a socket/pipe whose peer has
 * closed must return EPIPE from send()/write() rather than killing the process.
 * The TCP builtins (tcp_write, tcp_write_all) and their error-return paths
 * depend on this disposition. Installed via constructor — not sprout_set_argv —
 * so it holds even when a codegen entry point omits the argv initializer. */
__attribute__((constructor))
static void sprout_ignore_sigpipe_ctor(void) { signal(SIGPIPE, SIG_IGN); }

static SproutHandle sprout_handle_new(long long value) {
  if (g_handle_freelist_top == 0)
    tcp_fail("sprout_handle_new: handle table exhausted");
  SproutHandle h = g_handle_freelist[--g_handle_freelist_top];
  g_handle_table[h].value  = value;
  g_handle_table[h].in_use = 1;
  return h;
}

static long long sprout_handle_get(SproutHandle h) {
  return g_handle_table[h].value;
}

static void sprout_handle_set(SproutHandle h, long long value) {
  g_handle_table[h].value = value;
}

static void sprout_handle_release(SproutHandle h) {
  g_handle_table[h].in_use = 0;
  g_handle_freelist[g_handle_freelist_top++] = h;
}

static void sprout_handle_cleanup(SproutHandle* hp) {
  if (*hp != SPROUT_INVALID_HANDLE) sprout_handle_release(*hp);
}

/* SPROUT_HANDLE(name, val): declare a scoped GC root; released automatically
 *   on scope exit (including early returns and error paths).
 * SPROUT_HANDLE_SET(h, val): update a handle's value in place (required when
 *   the handle holds a mutable accumulator built up in a loop).
 * For raw pointer values (char*, VectorVal*): store as (long long)(uintptr_t)ptr;
 *   mark_roots calls sprout_gc_mark_value which calls sprout_heap_lookup,
 *   correctly handling both boxed object pointers and unboxed managed CSTRs. */
#define SPROUT_HANDLE(name, val)   SproutHandle name     __attribute__((cleanup(sprout_handle_cleanup)))     = sprout_handle_new(val)
#define SPROUT_HANDLE_SET(h, val) sprout_handle_set((h), (val))

long long sprout_alloc_tuple_blob(long long size_bytes) {
  if (size_bytes < 0) tcp_fail("sprout_alloc_tuple_blob: size must be >= 0");
  sprout_gc_maybe_collect_threshold();
  size_t words = ((size_t)size_bytes) / sizeof(uintptr_t);
  void* out = sprout_gc_alloc_block(SPROUT_HEAP_TUPLE, (unsigned long long)words,
                                    (size_t)size_bytes, "sprout_alloc_tuple_blob: out of memory");
  if (g_debug_alloc_enabled) g_debug_alloc_sprout_obj++;
  return (long long)(uintptr_t)out;
}

static VectorVal* sprout_alloc_vector_val(const char* ctx) {
  sprout_gc_maybe_collect_threshold();
  VectorVal* out = (VectorVal*)sprout_gc_alloc_block(SPROUT_HEAP_VECTOR, 0, sizeof(VectorVal), ctx);
  if (g_debug_alloc_enabled) g_debug_alloc_vector++;
  return out;
}

static long long* sprout_alloc_vector_data(size_t count, const char* ctx) {
  return count == 0 ? NULL : (long long*)sprout_alloc_counted(&g_debug_alloc_vector, count * sizeof(long long), ctx);
}

static long long* sprout_realloc_vector_data(long long* data, size_t count, const char* ctx) {
  return (long long*)sprout_realloc_counted(&g_debug_alloc_vector, data, count * sizeof(long long), ctx);
}

static BSTNode* sprout_alloc_bst_node(const char* ctx) {
  sprout_gc_maybe_collect_threshold();
  BSTNode* out = (BSTNode*)sprout_gc_alloc_block(SPROUT_HEAP_MAP, 0, sizeof(BSTNode), ctx);
  if (g_debug_alloc_enabled) g_debug_alloc_map++;
  return out;
}

static size_t sprout_intern_hash(const char* s) {
  size_t h = 5381;
  for (unsigned char c; (c = (unsigned char)*s) != 0; s++)
    h = (h << 5) + h + c;
  return h % 65537;
}

static const char* intern_string(const char* s) {
  if (s == NULL) return NULL;
  size_t bucket = sprout_intern_hash(s);
  for (InternBucket* b = g_intern_table[bucket]; b != NULL; b = b->next)
    if (strcmp(b->str, s) == 0) return b->str;
  InternBucket* entry = (InternBucket*)malloc(sizeof(InternBucket));
  if (!entry) tcp_fail("intern_string: out of memory");
  size_t len = strlen(s);
  entry->str = (char*)malloc(len + 1);
  if (!entry->str) tcp_fail("intern_string: out of memory for string");
  memcpy(entry->str, s, len + 1);
  entry->next = g_intern_table[bucket];
  g_intern_table[bucket] = entry;
  return entry->str;
}

static BytesVal* sprout_alloc_bytes_val(const char* ctx) {
  sprout_gc_maybe_collect_threshold();
  BytesVal* out = (BytesVal*)sprout_gc_alloc_block(SPROUT_HEAP_BYTES, 0, sizeof(BytesVal), ctx);
  if (g_debug_alloc_enabled) g_debug_alloc_bytes++;
  return out;
}

static unsigned char* sprout_alloc_bytes_data(size_t count, const char* ctx) {
  return count == 0 ? NULL : (unsigned char*)sprout_alloc_counted(&g_debug_alloc_bytes, count, ctx);
}

static BuilderVal* sprout_alloc_builder_val(const char* ctx) {
  sprout_gc_maybe_collect_threshold();
  BuilderVal* out = (BuilderVal*)sprout_gc_alloc_block(SPROUT_HEAP_BUILDER, 0, sizeof(BuilderVal), ctx);
  if (g_debug_alloc_enabled) g_debug_alloc_builder++;
  return out;
}

static BytesVal** sprout_alloc_builder_chunks(size_t count, const char* ctx) {
  return count == 0 ? NULL : (BytesVal**)sprout_alloc_counted(&g_debug_alloc_builder, count * sizeof(BytesVal*), ctx);
}

static IntRangeVal* sprout_alloc_range_val(const char* ctx) {
  sprout_gc_maybe_collect_threshold();
  IntRangeVal* out = (IntRangeVal*)sprout_gc_alloc_block(SPROUT_HEAP_RANGE, 0, sizeof(IntRangeVal), ctx);
  if (g_debug_alloc_enabled) g_debug_alloc_sprout_obj++;
  return out;
}

long long ref_new(long long value) {
  sprout_gc_maybe_collect_threshold();
  RefVal* r = (RefVal*)sprout_gc_alloc_block(SPROUT_HEAP_REF, 0, sizeof(RefVal), "ref_new: out of memory");
  if (g_debug_alloc_enabled) g_debug_alloc_sprout_obj++;
  r->value = value;
  return box_ptr(r);
}

/* Kind of the live heap object at p, or -1 when p is not a managed payload.
 * Reads the header once via the address sprout_heap_lookup already resolved. */
static int sprout_heap_kind_at(void* p) {
  void* hdr = sprout_heap_lookup(p);
  if (hdr == NULL) return -1;
  uint64_t h; memcpy(&h, hdr, 8);
  return (int)(h & 0xFF);
}

long long ref_read(long long ref) {
  if (sprout_heap_kind_at((void*)(uintptr_t)ref) != SPROUT_HEAP_REF)
    tcp_fail("ref_read: not a Ref");
  return ((RefVal*)(uintptr_t)ref)->value;
}

long long ref_write(long long ref, long long value) {
  if (sprout_heap_kind_at((void*)(uintptr_t)ref) != SPROUT_HEAP_REF)
    tcp_fail("ref_write: not a Ref");
  ((RefVal*)(uintptr_t)ref)->value = value;
  return 0;
}

static int is_obj_handle(long long h) {
  return sprout_heap_kind_at((void*)(uintptr_t)h) == SPROUT_HEAP_OBJ;
}

/* Number of GC-traceable children of a payload, derived from its inline header. */
static size_t sprout_heap_child_count_payload(void* payload) {
  if (payload == NULL) return 0;
  uint64_t h = sprout_hdr_of(payload);
  SproutHeapKind kind = sprout_hdr_kind(h);
  unsigned long long aux = sprout_hdr_aux(h);
  switch (kind) {
    case SPROUT_HEAP_OBJ:     return (size_t)(aux & 0xF);        /* arity */
    case SPROUT_HEAP_CLOSURE: return (size_t)aux;                /* n_caps (slot 0 skipped) */
    case SPROUT_HEAP_VECTOR:  return (size_t)((VectorVal*)payload)->len;
    case SPROUT_HEAP_MAP:     return 3;
    case SPROUT_HEAP_BYTES:   return 0;
    case SPROUT_HEAP_BUILDER: return ((BuilderVal*)payload)->count;
    case SPROUT_HEAP_TUPLE:   return (size_t)aux;
    case SPROUT_HEAP_RANGE:   return 0;
    case SPROUT_HEAP_REF:     return 1;
    case SPROUT_HEAP_CSTR:    return 0;
    default:                  return 0;
  }
}

static long long sprout_heap_child_value_payload(void* payload, size_t index) {
  if (payload == NULL) tcp_fail("sprout_heap_child_value: null payload");
  uint64_t h = sprout_hdr_of(payload);
  SproutHeapKind kind = sprout_hdr_kind(h);
  switch (kind) {
    case SPROUT_HEAP_OBJ:
      return ((long long*)payload)[index];
    case SPROUT_HEAP_CLOSURE: {
      /* Slot 0 = code pointer (not a heap value); skip it. */
      long long* slots = (long long*)payload;
      return slots[index + 1];
    }
    case SPROUT_HEAP_VECTOR:
      return ((VectorVal*)payload)->data[index];
    case SPROUT_HEAP_MAP: {
      BSTNode* bst = (BSTNode*)payload;
      if (index == 0) return bst->value;
      if (index == 1) return bst->left;
      if (index == 2) return bst->right;
      break;
    }
    case SPROUT_HEAP_BYTES: break;
    case SPROUT_HEAP_BUILDER:
      return (long long)(uintptr_t)((BuilderVal*)payload)->chunks[index];
    case SPROUT_HEAP_TUPLE: {
      uintptr_t word = 0;
      memcpy(&word, (char*)payload + (index * sizeof(uintptr_t)), sizeof(uintptr_t));
      return (long long)word;
    }
    case SPROUT_HEAP_RANGE: break;
    case SPROUT_HEAP_REF:
      if (index == 0) return ((RefVal*)payload)->value;
      break;
    case SPROUT_HEAP_CSTR: break;
    default: break;
  }
  tcp_fail("sprout_heap_child_value: index out of range");
  return 0;
}

/* ── Iterative GC mark (replaces recursive sprout_gc_mark_node) ──────────
 * The old design called sprout_gc_mark_node recursively for each heap child,
 * which overflows the C stack for large heaps (long linked-list chains from
 * import-pair lists, type substitution dicts, etc.).  This version maintains
 * an explicit grey-set worklist on the C heap so mark depth is O(1) stack
 * regardless of heap graph depth.
 */
static void**  g_gc_mark_worklist = NULL;  /* payload pointers (void*) */
static size_t  g_gc_mark_wl_len = 0;
static size_t  g_gc_mark_wl_cap = 0;

static void gc_mark_enqueue(void* payload) {
  if (payload == NULL) return;
  /* Color bit in header (bit 8). */
  void* hdr_ptr = (char*)payload - 8;
  uint64_t h; memcpy(&h, hdr_ptr, 8);
  if (h & SPROUT_GC_COLOR_BIT) return;  /* already marked */
  h |= SPROUT_GC_COLOR_BIT;
  memcpy(hdr_ptr, &h, 8);
  g_gc_marked_count++;
  if (g_gc_mark_wl_len >= g_gc_mark_wl_cap) {
    size_t new_cap = g_gc_mark_wl_cap < 1024 ? 1024 : g_gc_mark_wl_cap * 2;
    void** new_wl = (void**)realloc(g_gc_mark_worklist, new_cap * sizeof(void*));
    if (!new_wl) tcp_fail("GC mark: out of memory for worklist");
    g_gc_mark_worklist = new_wl;
    g_gc_mark_wl_cap = new_cap;
  }
  g_gc_mark_worklist[g_gc_mark_wl_len++] = payload;
}

static void sprout_gc_mark_value(long long value) {
  void* hdr = sprout_heap_lookup((void*)(uintptr_t)value);
  if (hdr != NULL) gc_mark_enqueue((char*)hdr + 8);
}

static void sprout_gc_mark_ptr(void* ptr) {
  void* hdr = sprout_heap_lookup(ptr);
  if (hdr != NULL) gc_mark_enqueue((char*)hdr + 8);
}

/* Drain the grey-set worklist: expand each grey node (marked but children
 * not yet processed) into its children until all reachable nodes are black.
 * Must be called after sprout_gc_mark_roots() and before sprout_gc_sweep(). */
static void sprout_gc_drain_marks(void) {
  SPROUT_PROF_HOT(g_gc_tracing = 1);
  while (g_gc_mark_wl_len > 0) {
    void* payload = g_gc_mark_worklist[--g_gc_mark_wl_len];
    size_t child_count = sprout_heap_child_count_payload(payload);
    for (size_t i = 0; i < child_count; i++) {
      long long child_val = sprout_heap_child_value_payload(payload, i);
      SPROUT_PROF_HOT(g_prof_drain_edges++);
      void* child_hdr = sprout_heap_lookup((void*)(uintptr_t)child_val);
      if (child_hdr != NULL) gc_mark_enqueue((char*)child_hdr + 8);
    }
  }
  SPROUT_PROF_HOT(g_gc_tracing = 0);
  free(g_gc_mark_worklist);
  g_gc_mark_worklist = NULL;
  g_gc_mark_wl_len = 0;
  g_gc_mark_wl_cap = 0;
}

static long long sprout_gc_root_count(void) {
  long long count = 0;
  for (RootNode* root = g_root_nodes; root != NULL; root = root->next) count++;
  /* Temp roots live per task; sum every registered context. */
  for (struct SproutRoots* rc = g_roots_registry; rc != NULL; rc = rc->reg_next)
    for (RootNode* root = rc->head; root != NULL; root = root->next) count++;
  return count;
}

static void sprout_gc_mark_root_list(RootNode* head) {
  for (RootNode* root = head; root != NULL; root = root->next) {
    if (root->kind == SPROUT_ROOT_I64) {
      sprout_gc_mark_value(*(long long*)root->slot);
    } else if (root->kind == SPROUT_ROOT_PTR) {
      sprout_gc_mark_ptr(*(void**)root->slot);
    } else {
      for (size_t i = 0; i < root->aux_words; i++) {
        uintptr_t word = 0;
        memcpy(&word, (char*)root->slot + (i * sizeof(uintptr_t)), sizeof(uintptr_t));
        sprout_gc_mark_ptr((void*)word);
      }
    }
  }
}

static void sprout_gc_mark_roots(void) {
  sprout_gc_mark_root_list(g_root_nodes);
  /* Temp roots are per task: scan EVERY registered context, so a task suspended
   * at a yield point keeps its roots while another task allocates. Over-rooting
   * a suspended task is safe; under-rooting one frees live values. */
  for (struct SproutRoots* rc = g_roots_registry; rc != NULL; rc = rc->reg_next)
    sprout_gc_mark_root_list(rc->head);
  for (int i = 0; i < SPROUT_HANDLE_TABLE_SIZE; i++) {
    if (g_handle_table[i].in_use)
      sprout_gc_mark_value(g_handle_table[i].value);
  }
}

/* Release extra-allocated storage for a payload (the slot itself is NOT freed;
 * that is done by the sweep's freelist push). */
static void sprout_release_payload_extras(void* payload, SproutHeapKind kind) {
  switch (kind) {
    case SPROUT_HEAP_VECTOR: {
      VectorVal* v = (VectorVal*)payload;
      free(v->data);
      break;
    }
    case SPROUT_HEAP_BYTES: {
      BytesVal* v = (BytesVal*)payload;
      free(v->data);
      break;
    }
    case SPROUT_HEAP_BUILDER: {
      BuilderVal* v = (BuilderVal*)payload;
      free(v->chunks);
      break;
    }
    default: break;  /* OBJ/CLOSURE/TUPLE/MAP/REF/RANGE/CSTR: storage IS the slot */
  }
}

/* Null out any singleton handles that point to a payload being reclaimed. */
static void sprout_gc_invalidate_singletons(void* payload) {
  if (payload == g_nothing_singleton)    g_nothing_singleton    = NULL;
  if (payload == g_irtheap_singleton)    g_irtheap_singleton    = NULL;
  if (payload == g_irtscalar_singleton)  g_irtscalar_singleton  = NULL;
  if (payload == g_irtunknown_singleton) g_irtunknown_singleton = NULL;
}

/* Return the slot walk stride for a header word.
 * FREE or POISON: aux field (h>>14) floored at 16 bytes.
 * Any live kind: slot_bytes(kind, aux). */
static inline size_t sprout_slot_step(uint64_t h) {
  uint64_t kind_bits = h & 0xFF;
  if (kind_bits == SPROUT_HEAP_FREE || kind_bits == SPROUT_GC_POISON) {
    size_t ssize = (size_t)(h >> 14);
    return (ssize < 16) ? 16 : ssize;
  }
  return slot_bytes((SproutHeapKind)kind_bits, h >> 14);
}

/* Region-walking mark-sweep collector.  Pass 1: process every slot in every
 * region (live → clear color; dead → release extras + write FREE header + clear
 * slotmap, OR poison in lineage mode).  Pass 2: release regions with no live and
 * no poison.  Pass 3: rebuild the per-class freelists from surviving FREE slots. */
static void sprout_gc_sweep(void) {
  g_gc_live_obj = g_gc_live_closure = g_gc_live_vec = 0;
  g_gc_live_map = g_gc_live_bytes = g_gc_live_builder = 0;
  g_gc_live_tuple = g_gc_live_range = g_gc_live_ref = 0;
  g_gc_live_cstr = g_gc_live_cstr_bytes = 0;

  int lineage_on = sprout_gc_lineage_on();

  /* Pass 1: process all slots in all regions. */
  for (size_t ri = 0; ri < g_region_count; ri++) {
    SproutRegion* r = &g_regions[ri];
    if (r->is_large) {
      /* OBJ (the only poisoned kind) is never large — max OBJ slot is 96 bytes
       * — so large entries need no poison handling. */
      void* payload = r->base + 8;
      uint64_t h; memcpy(&h, r->base, 8);
      if (h & SPROUT_GC_COLOR_BIT) {
        h &= ~SPROUT_GC_COLOR_BIT;
        memcpy(r->base, &h, 8);
        r->live_count = 1;
      } else {
        SproutHeapKind kind = sprout_hdr_kind(h);
        sprout_gc_invalidate_singletons(payload);
        if (g_gc_stress == 1 || lineage_on) sprout_gc_trace_free(payload);
        sprout_release_payload_extras(payload, kind);
        free(r->base);
        r->base = NULL;  /* mark for removal in Pass 2 */
        g_managed_heap_count--;
        g_debug_gc_swept++;
        r->live_count = 0;
      }
      continue;
    }

    /* Normal region: walk slots. */
    long long region_live = 0;
    long long region_poison = 0;
    size_t off = 0;
    while (off < r->bump) {
      uint64_t h; memcpy(&h, r->base + off, 8);
      size_t ssize = sprout_slot_step(h);
      uint64_t kind_bits = h & 0xFF;
      if (kind_bits == SPROUT_HEAP_FREE) {
        off += ssize;
        continue;
      }
      if (kind_bits == SPROUT_GC_POISON) {
        region_poison++;
        off += ssize;
        continue;
      }
      SproutHeapKind kind = (SproutHeapKind)kind_bits;
      unsigned long long aux = h >> 14;
      void* payload = r->base + off + 8;
      if (h & SPROUT_GC_COLOR_BIT) {
        h &= ~SPROUT_GC_COLOR_BIT;
        memcpy(r->base + off, &h, 8);
        region_live++;
        if (g_debug_gc_enabled) {
          switch (kind) {
            case SPROUT_HEAP_OBJ:     g_gc_live_obj++;     break;
            case SPROUT_HEAP_CLOSURE: g_gc_live_closure++;  break;
            case SPROUT_HEAP_VECTOR:  g_gc_live_vec++;      break;
            case SPROUT_HEAP_MAP:     g_gc_live_map++;      break;
            case SPROUT_HEAP_BYTES:   g_gc_live_bytes++;    break;
            case SPROUT_HEAP_BUILDER: g_gc_live_builder++;  break;
            case SPROUT_HEAP_TUPLE:   g_gc_live_tuple++;    break;
            case SPROUT_HEAP_RANGE:   g_gc_live_range++;    break;
            case SPROUT_HEAP_REF:     g_gc_live_ref++;      break;
            case SPROUT_HEAP_CSTR:
              g_gc_live_cstr++;
              g_gc_live_cstr_bytes += (long long)aux;
              break;
            default: break;
          }
        }
      } else {
        /* Dead slot. */
        sprout_gc_invalidate_singletons(payload);

        if (sprout_gc_hdrcheck_on() && kind == SPROUT_HEAP_CSTR) {
          size_t actual_len = strlen((const char*)payload);
          if (aux != (unsigned long long)actual_len) {
            fprintf(stderr, "[sprout] HDRCHECK: CSTR aux mismatch at sweep: hdr_aux=%llu strlen=%zu\n",
                    aux, actual_len);
            abort();
          }
        }

        if (lineage_on && kind == SPROUT_HEAP_OBJ) {
          /* Poison: write poison header with slot_bytes in aux (for walk). */
          uint64_t phdr = SPROUT_GC_POISON | ((uint64_t)ssize << 14);
          memcpy(r->base + off, &phdr, 8);
          void** frames = (void**)malloc(sizeof(void*) * 32);
          int n = frames ? backtrace(frames, 32) : 0;
          ((long long*)payload)[0] = (long long)(uintptr_t)frames;
          ((long long*)payload)[1] = (long long)n;
          region_poison++;
          /* Slotmap bit STAYS set (dangling reads must resolve to corpse).
           * The corpse leaves the live count like any other dead object;
           * only its memory is retained. */
          g_managed_heap_count--;
          g_debug_gc_swept++;
          if (g_gc_stress == 1 || lineage_on) sprout_gc_trace_free(payload);
        } else {
          /* Slotmap bit stays set for FREE slots; sprout_heap_lookup rejects
           * them by header kind, and reuse rewrites the header in place. */
          if (g_gc_stress == 1 || lineage_on) sprout_gc_trace_free(payload);
          sprout_release_payload_extras(payload, kind);
          uint64_t free_hdr = (uint64_t)SPROUT_HEAP_FREE | ((uint64_t)ssize << 14);
          memcpy(r->base + off, &free_hdr, 8);
          g_managed_heap_count--;
          g_debug_gc_swept++;
        }
      }
      SPROUT_PROF_HOT(g_prof_sweep_visits++);
      off += ssize;
    }
    r->live_count = region_live;
    r->poison_count = region_poison;
  }

  /* Pass 2: release empty regions (no live, no poison). Keep >= 1 normal region. */
  int kept_normal = 0;
  for (size_t ri = 0; ri < g_region_count; ri++) {
    if (!g_regions[ri].is_large && g_regions[ri].base != NULL) kept_normal++;
  }

  size_t ri = g_region_count;
  while (ri > 0) {
    ri--;
    SproutRegion* r = &g_regions[ri];
    if (r->is_large) {
      if (r->base == NULL) {
        region_table_remove(ri);
      }
      continue;
    }
    if (r->live_count == 0 && r->poison_count == 0 && kept_normal > 1) {
      free(r->base);
      free(r->slotmap);
      kept_normal--;
      region_table_remove(ri);
    }
  }

  /* Ensure at least one normal region exists for the bump path. */
  if (kept_normal == 0) {
    open_new_region();
  }

  /* Pass 3: rebuild class freelists from surviving regions. */
  memset(g_freelist, 0, sizeof(g_freelist));
  for (size_t ri2 = 0; ri2 < g_region_count; ri2++) {
    SproutRegion* r = &g_regions[ri2];
    if (r->is_large) continue;
    size_t off = 0;
    while (off < r->bump) {
      uint64_t h; memcpy(&h, r->base + off, 8);
      size_t ssize = sprout_slot_step(h);
      if ((h & 0xFF) == SPROUT_HEAP_FREE) {
        size_t cls = ssize / SPROUT_SLOT_GRAN;
        if (cls < SPROUT_FREELIST_CLASSES) {
          void* payload = r->base + off + 8;
          memcpy(payload, &g_freelist[cls], sizeof(void*));
          g_freelist[cls] = payload;
        }
      }
      off += ssize;
    }
  }
}

static void sprout_gc_collect(void) {
  sprout_gc_collect_with_reason("atexit");
}

static int g_gc_disabled = -1;
static void sprout_gc_collect_with_reason(const char* reason) {
  /* SPROUT_GC_DISABLE=1 is a TRUE no-collect mode for diagnosis: it lets you
     cleanly bisect "is this a GC/rooting bug or a codegen value bug?".  Unlike
     SPROUT_GC_THRESHOLD=<huge>, which does NOT stop collection, this skips the
     collector entirely.  Do NOT use it as a workaround — it leaks. */
  if (g_gc_disabled < 0) { const char* e = getenv("SPROUT_GC_DISABLE"); g_gc_disabled = (e && e[0] == '1') ? 1 : 0; }
  if (g_gc_disabled) return;
  if (g_gc_active) return;
  g_gc_active = 1;
  long long started_us = sprout_now_micros();
  long long heap_before = g_managed_heap_count;
  long long root_count = sprout_gc_root_count();
  long long alloc_since_gc = g_managed_alloc_since_gc;
  long long swept_before = g_debug_gc_swept;
  g_gc_cycle_count++;
  SPROUT_PROF_COLD(g_prof_cycles++);
  SPROUT_PROF_COLD(g_prof_mark_slots += (unsigned long long)root_count);
  g_gc_marked_count = 0;
  sprout_gc_mark_roots();
  sprout_gc_drain_marks();
  sprout_gc_sweep();
  long long finished_us = sprout_now_micros();
  long long elapsed_us = 0;
  if (finished_us >= started_us) elapsed_us = finished_us - started_us;
  SPROUT_PROF_COLD(g_prof_gc_us += (unsigned long long)elapsed_us);
  sprout_gc_log_cycle(reason, heap_before, g_managed_heap_count, root_count, g_gc_marked_count, alloc_since_gc, g_debug_gc_swept - swept_before, elapsed_us);
  /* Adaptive threshold: re-base on the LIVE set after each collection so the heap
     (hence RSS) stays proportional to live data instead of ratcheting upward
     without bound.  The previous policy only ever GREW the threshold (x
     adapt_factor whenever < adapt_ratio of the heap was swept), so an
     allocation-heavy workload that kept tripping the ratio — e.g. the
     self-hosted compiler emitting its own ~17MB of IR — drove the threshold, and
     thus the retained garbage, up without limit (multi-GB RSS to produce a few
     MB of output).  Targeting live*factor self-tunes: it rises for genuinely
     live-heavy heaps (avoiding GC thrash) and falls again when the working set
     shrinks, bounding peak RSS to ~factor x the live set.  g_gc_threshold_base
     is the floor for small programs; the optional cap still applies. */
  if (g_gc_adapt_ratio > 0.0) {
    long long target = (long long)((double)g_managed_heap_count * g_gc_adapt_factor);
    if (target < g_gc_threshold_base) target = g_gc_threshold_base;
    if (g_gc_adapt_cap > 0 && target > g_gc_adapt_cap) target = g_gc_adapt_cap;
    g_gc_threshold = target;
  }
  g_managed_alloc_since_gc = 0;
  /* Livelock detection: warn (or abort) when consecutive cycles sweep almost nothing. */
  if (g_gc_livelock_action > 0 && g_gc_livelock_cycles > 0) {
    double sweep_efficiency = heap_before > 0
      ? (double)(heap_before - g_managed_heap_count) / (double)heap_before
      : 1.0;
    if (sweep_efficiency < g_gc_livelock_ratio) {
      g_gc_consecutive_bad_cycles++;
      if (g_gc_consecutive_bad_cycles >= g_gc_livelock_cycles && !g_gc_livelock_warned) {
        fprintf(stderr,
          "[sprout gc] livelock: %lld consecutive cycles sweeping %.1f%% < %.1f%%"
          "; heap=%lld roots=%lld threshold=%lld\n",
          g_gc_consecutive_bad_cycles,
          sweep_efficiency * 100.0, g_gc_livelock_ratio * 100.0,
          g_managed_heap_count, root_count, g_gc_threshold);
        g_gc_livelock_warned = 1;
        if (g_gc_livelock_action == 2) {
          fprintf(stderr, "[sprout gc] livelock: aborting (SPROUT_GC_LIVELOCK_ACTION=abort)\n");
          abort();
        }
      }
    } else {
      g_gc_consecutive_bad_cycles = 0;
      g_gc_livelock_warned = 0;
    }
  }
  g_gc_active = 0;
}

static CtorMeta* find_ctor(long long tag) {
  for (long long i = 0; i < g_ctor_meta_len; i++) {
    if (g_ctor_meta[i].tag == tag) return &g_ctor_meta[i];
  }
  return NULL;
}

static long long find_ctor_tag_by_name(const char* name) {
  for (long long i = 0; i < g_ctor_meta_len; i++) {
    if (strcmp(g_ctor_meta[i].name, name) == 0) return g_ctor_meta[i].tag;
  }
  // Fallback: if only the qualified name was registered (e.g. "stdlib.process.ProcResult")
  // but we're looking up the short name ("ProcResult"), match by suffix.
  size_t name_len = strlen(name);
  for (long long i = 0; i < g_ctor_meta_len; i++) {
    const char* reg = g_ctor_meta[i].name;
    size_t reg_len = strlen(reg);
    if (reg_len > name_len && reg[reg_len - name_len - 1] == '.' &&
        strcmp(reg + reg_len - name_len, name) == 0)
      return g_ctor_meta[i].tag;
  }
  tcp_fail("constructor metadata not registered");
  return -1;
}

static void print_inline_value(long long v);

static void print_inline_obj(void* o) {
  long long tag = sprout_tag((long long)(uintptr_t)o);
  CtorMeta* m = find_ctor(tag);
  if (m == NULL) {
    printf("Ctor%lld", tag);
    return;
  }
  printf("%s", m->name);
  if (m->arity <= 0) return;
  printf("(");
  long long* fields = (long long*)o;
  print_inline_value(fields[0]);
  if (m->arity > 1) { printf(", "); print_inline_value(fields[1]); }
  if (m->arity > 2) { printf(", "); print_inline_value(fields[2]); }
  if (m->arity > 3) { printf(", "); print_inline_value(fields[3]); }
  if (m->arity > 4) { printf(", "); print_inline_value(fields[4]); }
  if (m->arity > 5) { printf(", "); print_inline_value(fields[5]); }
  if (m->arity > 6) { printf(", "); print_inline_value(fields[6]); }
  printf(")");
}

static void print_inline_value(long long v) {
  void* hdr = sprout_heap_lookup((void*)(uintptr_t)v);
  if (hdr != NULL) {
    uint64_t h; memcpy(&h, hdr, 8);
    SproutHeapKind kind = sprout_hdr_kind(h);
    if (kind == SPROUT_HEAP_CSTR) {
      printf("%s", (const char*)(uintptr_t)v);
    } else if (kind == SPROUT_HEAP_RANGE) {
      IntRangeVal* value = (IntRangeVal*)(uintptr_t)v;
      printf("%lld..%lld", value->start, value->end);
    } else if (kind == SPROUT_HEAP_TUPLE) {
      /* Tuple blob: aux words, each a value (recurse).  Renders structurally
         like an ADT — e.g. (1, 7) — consistent with print's Bool-as-i64
         semantics (print(true) prints 1, not "true"). */
      size_t n = (size_t)(h >> 14);
      printf("(");
      for (size_t i = 0; i < n; i++) {
        if (i > 0) printf(", ");
        long long word;
        memcpy(&word, (const char*)(uintptr_t)v + i * sizeof(uintptr_t), sizeof(word));
        print_inline_value(word);
      }
      printf(")");
    } else if (kind == SPROUT_HEAP_OBJ) {
      print_inline_obj(unbox_ptr(v));
    } else {
      printf("%lld", v);
    }
  } else {
    printf("%lld", v);
  }
}

long long print_int(long long x) {
  printf("%lld\n", x);
  return x;
}
long long print_str(const char* s) {
  printf("%s\n", s);
  return 0;
}
long long eprint_str(const char* s) {
  fprintf(stderr, "%s\n", s);
  return 0;
}
/* Bridge for old stage-1 codegen that emits @eprint(i64) — Sprout strings are
   heap pointers passed as i64 before emit_eprint_call was added. */
long long eprint(long long s) {
  fprintf(stderr, "%s\n", (const char*)s);
  return 0;
}
long long print_text(const char* s) {
  printf("%s", s);
  return 0;
}
long long print_value_part(long long x) {
  print_inline_value(x);
  return x;
}
long long print_newline(void) {
  printf("\n");
  return 0;
}
long long print_value(long long x) {
  print_inline_value(x);
  printf("\n");
  return x;
}
// W8/P1 (D5): parse_int is now a total pure-Sprout function (prelude.sprout,
// String -> Maybe Int). The C strtoll builtin was removed; the committed seed no
// longer references @parse_int as a host symbol (it defines the Sprout one).
long long int_to_string(long long value) {
  char buf[32];
  int written = snprintf(buf, sizeof(buf), "%lld", value);
  if (written < 0) tcp_fail("int_to_string: formatting failed");
  size_t content_len = (size_t)written;
  sprout_gc_maybe_collect_threshold();
  char* out = sprout_gc_alloc_cstr(content_len, "int_to_string: out of memory");
  memcpy(out, buf, content_len);
  out[content_len] = '\0';
  return (long long)(uintptr_t)out;
}
/* Format a Double (passed as its i64 bit-pattern, uniform ABI) to decimal text.
 * Uses "%g" (6 significant digits, trailing zeros stripped) as the default;
 * fine-grained formatting is a separate future tool.  When "%g" yields a bare
 * integer form (no '.', exponent, or inf/nan letters), a ".0" is appended so a
 * Double never reads as an Int.  Correct float->decimal formatting (incl. inf/
 * nan and scientific notation) is impractical to reproduce faithfully in Sprout,
 * so this stays in C — a correctness justification, not a performance one. */
long long double_to_string(long long bits) {
  double d;
  memcpy(&d, &bits, sizeof(d));
  char buf[64];
  int written = snprintf(buf, sizeof(buf), "%g", d);
  if (written < 0) tcp_fail("double_to_string: formatting failed");
  int is_bare_int = 1;
  for (int i = 0; i < written; i++) {
    char c = buf[i];
    if (c == '.' || c == 'e' || c == 'E' ||
        c == 'n' || c == 'N' || c == 'i' || c == 'I') { is_bare_int = 0; break; }
  }
  if (is_bare_int && written + 2 < (int)sizeof(buf)) {
    buf[written]     = '.';
    buf[written + 1] = '0';
    written += 2;
    buf[written] = '\0';
  }
  size_t content_len = (size_t)written;
  sprout_gc_maybe_collect_threshold();
  char* out = sprout_gc_alloc_cstr(content_len, "double_to_string: out of memory");
  memcpy(out, buf, content_len);
  out[content_len] = '\0';
  return (long long)(uintptr_t)out;
}
long long int_range(long long start, long long end) {
  IntRangeVal* out = sprout_alloc_range_val("int_range: out of memory");
  out->start = start;
  out->end = end;
  return (long long)(uintptr_t)out;
}
long long int_range_start(long long range_h) {
  IntRangeVal* value = (IntRangeVal*)(uintptr_t)range_h;
  if (value == NULL || sprout_heap_kind_at(value) != SPROUT_HEAP_RANGE)
    tcp_fail("int_range_start: expected IntRange");
  return value->start;
}
long long int_range_end(long long range_h) {
  IntRangeVal* value = (IntRangeVal*)(uintptr_t)range_h;
  if (value == NULL || sprout_heap_kind_at(value) != SPROUT_HEAP_RANGE)
    tcp_fail("int_range_end: expected IntRange");
  return value->end;
}
long long env_get(const char* name) {
  if (name == NULL) tcp_fail("env_get: null name");
  const char* value = getenv(name);
  if (value == NULL) return sprout_make0(find_ctor_tag_by_name("Nothing"));
  return sprout_make1(find_ctor_tag_by_name("Just"), (long long)(uintptr_t)value);
}
long long sprout_set_argv(int argc, char** argv) {
  g_sprout_argc = argc;
  g_sprout_argv = argv;
  sprout_capture_stack_bounds();
  sprout_install_crash_handlers();
  sprout_debug_alloc_maybe_enable();
  sprout_debug_gc_maybe_enable();
  sprout_gc_threshold_maybe_enable();
  sprout_gc_adapt_maybe_enable();
  sprout_gc_livelock_maybe_enable();
  sprout_gc_maybe_register();
  return 0;
}
long long sprout_nothing(long long tag) {
  if (g_nothing_singleton == NULL) {
    void* obj = sprout_alloc_obj_raw(0, "sprout_nothing: out of memory");
    sprout_obj_write_tag(obj, tag, 0);
    g_nothing_singleton = obj;
  }
  return box_ptr(g_nothing_singleton);
}
long long argv_get(long long index) {
  if (index < 0) return sprout_make0(find_ctor_tag_by_name("Nothing"));
  if (g_sprout_argv == NULL) return sprout_make0(find_ctor_tag_by_name("Nothing"));
  if (index >= (long long)(g_sprout_argc - 1)) return sprout_make0(find_ctor_tag_by_name("Nothing"));
  return sprout_make1(find_ctor_tag_by_name("Just"), (long long)(uintptr_t)g_sprout_argv[index + 1]);
}
long long read_file(long long path_i) {
  const char* path = (const char*)(uintptr_t)path_i;
  if (path == NULL) {
    char* msg = dup_managed_cstr("null path", "read_file: out of memory");
    SPROUT_HANDLE(h_msg, (long long)(uintptr_t)msg);
    return sprout_make1(find_ctor_tag_by_name("Err"), sprout_handle_get(h_msg));
  }
  FILE* f = NULL;
  int close_after = 0;
  if (strcmp(path, "-") == 0) {
    f = stdin;
  } else {
    f = fopen(path, "rb");
    if (f == NULL) {
      char* msg = dup_managed_cstr(strerror(errno), "read_file: out of memory");
      SPROUT_HANDLE(h_msg, (long long)(uintptr_t)msg);
      return sprout_make1(find_ctor_tag_by_name("Err"), sprout_handle_get(h_msg));
    }
    close_after = 1;
  }
  size_t cap = 4096;
  size_t len = 0;
  sprout_gc_maybe_collect_threshold();
  char* out = (char*)malloc(cap);
  if (out == NULL) {
    if (close_after) fclose(f);
    char* msg = dup_managed_cstr("out of memory", "read_file: out of memory");
    SPROUT_HANDLE(h_msg, (long long)(uintptr_t)msg);
    return sprout_make1(find_ctor_tag_by_name("Err"), sprout_handle_get(h_msg));
  }
  char buf[4096];
  while (1) {
    size_t n = fread(buf, 1, sizeof(buf), f);
    if (n > 0) {
      while (len + n + 1 > cap) {
        size_t new_cap = cap * 2;
        char* grown = (char*)realloc(out, new_cap);
        if (grown == NULL) {
          if (close_after) fclose(f);
          free(out);
          char* msg = dup_managed_cstr("out of memory", "read_file: out of memory");
          SPROUT_HANDLE(h_msg, (long long)(uintptr_t)msg);
          return sprout_make1(find_ctor_tag_by_name("Err"), sprout_handle_get(h_msg));
        }
        out = grown;
        cap = new_cap;
      }
      memcpy(out + len, buf, n);
      len += n;
    }
    if (n < sizeof(buf)) {
      if (feof(f)) break;
      if (ferror(f)) {
        char* msg = dup_managed_cstr(strerror(errno), "read_file: out of memory");
        if (close_after) fclose(f);
        free(out);
        SPROUT_HANDLE(h_msg, (long long)(uintptr_t)msg);
        return sprout_make1(find_ctor_tag_by_name("Err"), sprout_handle_get(h_msg));
      }
    }
  }
  if (close_after) fclose(f);
  out[len] = '\0';
  const char* utf8_reason = NULL;
  if (!utf8_validate((const unsigned char*)out, len, &utf8_reason)) {
    free(out);
    char* msg = dup_managed_cstr(utf8_reason, "read_file: out of memory");
    SPROUT_HANDLE(h_msg, (long long)(uintptr_t)msg);
    return sprout_make1(find_ctor_tag_by_name("Err"), sprout_handle_get(h_msg));
  }
  out = sprout_gc_adopt_cstr(out, len, "read_file: out of memory");  SPROUT_HANDLE(h_out, (long long)(uintptr_t)out);
  return sprout_make1(find_ctor_tag_by_name("Ok"), sprout_handle_get(h_out));
}
long long write_file(long long path_i, long long content_i) {
  const char *path    = (const char *)(uintptr_t)path_i;
  const char *content = (const char *)(uintptr_t)content_i;
  if (path == NULL) {
    char* msg = dup_managed_cstr("null path", "write_file: out of memory");
    SPROUT_HANDLE(h_msg, (long long)(uintptr_t)msg);
    return sprout_make1(find_ctor_tag_by_name("Err"), sprout_handle_get(h_msg));
  }
  if (content == NULL) {
    char* msg = dup_managed_cstr("null content", "write_file: out of memory");
    SPROUT_HANDLE(h_msg, (long long)(uintptr_t)msg);
    return sprout_make1(find_ctor_tag_by_name("Err"), sprout_handle_get(h_msg));
  }
  FILE *f = fopen(path, "wb");
  if (!f) {
    char* msg = dup_managed_cstr(strerror(errno), "write_file: out of memory");
    SPROUT_HANDLE(h_msg, (long long)(uintptr_t)msg);
    return sprout_make1(find_ctor_tag_by_name("Err"), sprout_handle_get(h_msg));
  }
  size_t len = strlen(content);
  if (fwrite(content, 1, len, f) != len) {
    char* msg = dup_managed_cstr(strerror(errno), "write_file: out of memory");
    fclose(f);
    SPROUT_HANDLE(h_msg, (long long)(uintptr_t)msg);
    return sprout_make1(find_ctor_tag_by_name("Err"), sprout_handle_get(h_msg));
  }
  fclose(f);
  return sprout_make1(find_ctor_tag_by_name("Ok"), 0LL);
}
long long panic(long long msg_i) {
  const char* msg = (const char*)(uintptr_t)msg_i;
  tcp_fail(msg ? msg : "panic");
  return 0LL; /* unreachable */
}
// ---- stdlib.process: proc_run / proc_run_stdin --------------------------------

typedef struct { char* data; size_t len; size_t cap; } GrowBuf;

static GrowBuf sprout_growbuf_new(void) {
  GrowBuf b; b.cap = 4096; b.len = 0;
  b.data = (char*)malloc(b.cap);
  if (b.data == NULL) tcp_fail("proc_run: out of memory");
  b.data[0] = '\0';
  return b;
}

static void sprout_growbuf_append(GrowBuf* b, const char* p, size_t n) {
  if (!n) return;
  if (b->len + n + 1 > b->cap) {
    if (n > SIZE_MAX - b->len - 1) tcp_fail("proc_run: output too large");
    size_t needed = b->len + n + 1;
    size_t new_cap = b->cap;
    while (needed > new_cap) {
      if (new_cap > SIZE_MAX / 2) tcp_fail("proc_run: output too large");
      new_cap *= 2;
    }
    char* grown = (char*)realloc(b->data, new_cap);
    if (grown == NULL) tcp_fail("proc_run: out of memory");
    b->data = grown;
    b->cap = new_cap;
  }
  memcpy(b->data + b->len, p, n);
  b->len += n;
  b->data[b->len] = '\0';
}

// Build a Sprout ProcResult(Int, String, String) ADT value: (exit_code, stdout, stderr).
// Takes GC ownership of out->data and err->data (both must be plain malloc'd buffers).
static long long sprout_make_proc_result(int exit_code, GrowBuf* out, GrowBuf* err) {
  out->data = sprout_gc_adopt_cstr(out->data, out->len, "proc_run: out of memory");  long long rooted_out = (long long)(uintptr_t)out->data;
  SPROUT_GC_PUSH_I64_LOCAL(rooted_out);
  err->data = sprout_gc_adopt_cstr(err->data, err->len, "proc_run: out of memory");  long long rooted_err = (long long)(uintptr_t)err->data;
  SPROUT_GC_PUSH_I64_LOCAL(rooted_err);
  long long tag = find_ctor_tag_by_name("stdlib.process.ProcResult");
  long long obj = sprout_make_registered_obj(3, tag, (long long)exit_code,
                                             rooted_out, rooted_err,
                                             "proc_run: out of memory");
  SPROUT_GC_POP_LOCALS(2);
  return obj;
}

// Convert a Sprout Vec String (i64) to a NULL-terminated char** for execvp.
// Caller must free() the returned array; the strings themselves are GC-managed.
static char** sprout_vec_string_to_argv(long long vec_val) {
  /* vec_val is an OBJ handle; field 0 is the VectorVal* payload handle. */
  VectorVal* vec = (VectorVal*)(uintptr_t)((long long*)(uintptr_t)vec_val)[0];
  long long  n   = vec->len;
  char** argv = (char**)malloc((size_t)(n + 1) * sizeof(char*));
  for (long long i = 0; i < n; i++)
    argv[i] = (char*)(uintptr_t)vec->data[i];
  argv[n] = NULL;
  return argv;
}

// fork+exec with separate stdout/stderr capture via poll().
// stdin_data=NULL means redirect stdin from /dev/null.
static long long sprout_proc_run_impl(
  char**      argv,
  const char* stdin_data,
  size_t      stdin_len
) {
  if (argv[0] == NULL) {
    GrowBuf o = sprout_growbuf_new(), e = sprout_growbuf_new();
    sprout_growbuf_append(&e, "proc_run: empty argv", 20);
    return sprout_make_proc_result(1, &o, &e);
  }
  int out_fds[2] = {-1,-1}, err_fds[2] = {-1,-1}, in_fds[2] = {-1,-1};

  if (pipe(out_fds) < 0 || pipe(err_fds) < 0 ||
      (stdin_data && pipe(in_fds) < 0)) {
    if (out_fds[0] >= 0) { close(out_fds[0]); close(out_fds[1]); }
    if (err_fds[0] >= 0) { close(err_fds[0]); close(err_fds[1]); }
    GrowBuf o = sprout_growbuf_new(), e = sprout_growbuf_new();
    sprout_growbuf_append(&e, "proc_run: pipe() failed", 23);
    return sprout_make_proc_result(1, &o, &e);
  }

  pid_t pid = fork();
  if (pid < 0) {
    close(out_fds[0]); close(out_fds[1]);
    close(err_fds[0]); close(err_fds[1]);
    if (stdin_data) { close(in_fds[0]); close(in_fds[1]); }
    GrowBuf o = sprout_growbuf_new(), e = sprout_growbuf_new();
    sprout_growbuf_append(&e, "proc_run: fork() failed", 23);
    return sprout_make_proc_result(1, &o, &e);
  }

  if (pid == 0) {
    dup2(out_fds[1], STDOUT_FILENO);
    dup2(err_fds[1], STDERR_FILENO);
    close(out_fds[0]); close(out_fds[1]);
    close(err_fds[0]); close(err_fds[1]);
    if (stdin_data) {
      dup2(in_fds[0], STDIN_FILENO);
      close(in_fds[0]); close(in_fds[1]);
    } else {
      int dev = open("/dev/null", O_RDONLY);
      if (dev >= 0) { dup2(dev, STDIN_FILENO); close(dev); }
    }
    execvp(argv[0], argv);
    _exit(127);
  }

  // parent: close write ends of output pipes, read end of stdin pipe
  close(out_fds[1]); out_fds[1] = -1;
  close(err_fds[1]); err_fds[1] = -1;
  if (stdin_data) {
    close(in_fds[0]); in_fds[0] = -1;
    // Empty stdin: close the write end immediately so the child sees EOF right
    // away rather than blocking. Without this, stdin_done is pre-set to 1 but
    // in_fds[1] stays open, causing a deadlock with any child that reads until
    // EOF (e.g. cat, wc -c).
    if (stdin_len == 0) { close(in_fds[1]); in_fds[1] = -1; }
  }

  GrowBuf out_buf = sprout_growbuf_new(), err_buf = sprout_growbuf_new();
  char    tmp[4096];
  size_t  stdin_written = 0;
  int     stdin_done    = (!stdin_data || stdin_len == 0);

  // Suppress SIGPIPE for the duration of the poll loop so write() returns EPIPE
  // instead of killing the process if the child closes its stdin end early.
  // execvp() resets SIG_IGN to SIG_DFL in the child, so subprocess disposition
  // is unaffected. The suppress is scoped here (not at fork) so early pipe/fork
  // error returns don't need to restore it.
  struct sigaction sa_old_pipe;
  struct sigaction sa_ign_pipe = {0};
  sa_ign_pipe.sa_handler = SIG_IGN;
  if (stdin_data) sigaction(SIGPIPE, &sa_ign_pipe, &sa_old_pipe);
  // poll loop: drain stdout/stderr while optionally writing stdin
  while (out_fds[0] >= 0 || err_fds[0] >= 0 || !stdin_done) {
    struct pollfd pfds[3];
    int n = 0, out_i = -1, err_i = -1, in_i = -1;
    if (out_fds[0] >= 0) { pfds[n].fd=out_fds[0]; pfds[n].events=POLLIN; out_i=n++; }
    if (err_fds[0] >= 0) { pfds[n].fd=err_fds[0]; pfds[n].events=POLLIN; err_i=n++; }
    if (!stdin_done)      { pfds[n].fd=in_fds[1];  pfds[n].events=POLLOUT; in_i=n++; }
    if (!n) break;
    if (poll(pfds, (nfds_t)n, -1) < 0) break;
    if (out_i >= 0 && (pfds[out_i].revents & (POLLIN|POLLHUP|POLLERR))) {
      ssize_t nr = read(out_fds[0], tmp, sizeof(tmp));
      if (nr > 0) sprout_growbuf_append(&out_buf, tmp, (size_t)nr);
      else { close(out_fds[0]); out_fds[0] = -1; }
    }
    if (err_i >= 0 && (pfds[err_i].revents & (POLLIN|POLLHUP|POLLERR))) {
      ssize_t nr = read(err_fds[0], tmp, sizeof(tmp));
      if (nr > 0) sprout_growbuf_append(&err_buf, tmp, (size_t)nr);
      else { close(err_fds[0]); err_fds[0] = -1; }
    }
    if (in_i >= 0 && (pfds[in_i].revents & (POLLOUT|POLLHUP|POLLERR))) {
      ssize_t nw = write(in_fds[1], stdin_data + stdin_written,
                         stdin_len - stdin_written);
      if (nw > 0) stdin_written += (size_t)nw;
      else { close(in_fds[1]); in_fds[1] = -1; stdin_done = 1; continue; }
      if (stdin_written >= stdin_len) {
        close(in_fds[1]); in_fds[1] = -1; stdin_done = 1;
      }
    }
  }
  if (out_fds[0] >= 0) close(out_fds[0]);
  if (err_fds[0] >= 0) close(err_fds[0]);
  if (in_fds[1]  >= 0) close(in_fds[1]);

  int wstatus = 0;
  waitpid(pid, &wstatus, 0);
  if (stdin_data) sigaction(SIGPIPE, &sa_old_pipe, NULL);
  int exit_code = WIFEXITED(wstatus) ? WEXITSTATUS(wstatus) : 1;
  return sprout_make_proc_result(exit_code, &out_buf, &err_buf);
}

// Sprout extern fn proc_run_vec(argv: Vec String) -> ProcResult !{IO}
long long proc_run_vec(long long argv_val) {
  char** argv = sprout_vec_string_to_argv(argv_val);
  long long result = sprout_proc_run_impl(argv, NULL, 0);
  free(argv);
  return result;
}

// Sprout extern fn proc_run_stdin_vec(argv: Vec String, stdin_data: String) -> ProcResult !{IO}
long long proc_run_stdin_vec(long long argv_val, long long stdin_val) {
  char**      argv       = sprout_vec_string_to_argv(argv_val);
  const char* stdin_data = (const char*)(uintptr_t)stdin_val;
  size_t      stdin_len  = strlen(stdin_data);
  long long   result     = sprout_proc_run_impl(argv, stdin_data, stdin_len);
  free(argv);
  return result;
}

long long term_read_line(void) {
  char* line = NULL;
  size_t cap = 0;
  ssize_t len = getline(&line, &cap, stdin);
  if (len < 0) {
    free(line);
    if (feof(stdin)) return sprout_make0(find_ctor_tag_by_name("Nothing"));
    tcp_fail("term_read_line: read error");
  }
  while (len > 0 && (line[len - 1] == '\n' || line[len - 1] == '\r')) {
    len -= 1;
    line[len] = '\0';
  }
  line = register_cstr(line);
  SPROUT_HANDLE(h_line, (long long)(uintptr_t)line);
  return sprout_make1(find_ctor_tag_by_name("Just"), sprout_handle_get(h_line));
}
/* stdin_read_bytes: read exactly n bytes from stdin; returns Nothing on EOF.
 * Required because fread has no Sprout equivalent — getline reads to newline,
 * not to a fixed byte count as LSP/DAP Content-Length framing requires. */
long long stdin_read_bytes(long long n_val) {
  if (n_val < 0) tcp_fail("stdin_read_bytes: negative byte count");
  char* buf = (char*)malloc((size_t)n_val + 1);
  if (!buf) tcp_fail("stdin_read_bytes: out of memory");
  size_t total = 0;
  while (total < (size_t)n_val) {
    size_t got = fread(buf + total, 1, (size_t)n_val - total, stdin);
    if (got == 0) {
      free(buf);
      if (feof(stdin)) return sprout_make0(find_ctor_tag_by_name("Nothing"));
      tcp_fail("stdin_read_bytes: read error");
    }
    total += got;
  }
  buf[n_val] = '\0';
  buf = register_cstr(buf);
  SPROUT_HANDLE(h_buf, (long long)(uintptr_t)buf);
  return sprout_make1(find_ctor_tag_by_name("Just"), sprout_handle_get(h_buf));
}
_Bool term_is_interactive(void) {
  return isatty(fileno(stdin)) && isatty(fileno(stdout));
}
long long term_clear(void) {
  fputs("[2J[H", stdout);
  fflush(stdout);
  return 0;
}
long long term_move(long long row, long long col) {
  fprintf(stdout, "[%lld;%lldH", row, col);
  fflush(stdout);
  return 0;
}
long long term_hide_cursor(void) {
  fputs("[?25l", stdout);
  fflush(stdout);
  return 0;
}
long long term_show_cursor(void) {
  fputs("[?25h", stdout);
  fflush(stdout);
  return 0;
}
long long term_read_key(void) {
  static const char* token_ctrl_a = "ctrl-a";
  static const char* token_ctrl_b = "ctrl-b";
  static const char* token_ctrl_d = "ctrl-d";
  static const char* token_ctrl_e = "ctrl-e";
  static const char* token_ctrl_f = "ctrl-f";
  static const char* token_ctrl_l = "ctrl-l";
  static const char* token_backspace = "backspace";
  static const char* token_down = "down";
  static const char* token_escape = "escape";
  static const char* token_enter = "enter";
  static const char* token_left = "left";
  static const char* token_right = "right";
  static const char* token_tab = "tab";
  static const char* token_up = "up";
  int ch = EOF;
  if (!isatty(STDIN_FILENO)) {
    ch = getchar();
  } else {
    struct termios oldt;
    if (tcgetattr(STDIN_FILENO, &oldt) != 0) {
      ch = getchar();
    } else {
      struct termios raw = oldt;
      raw.c_lflag &= (tcflag_t)~(ICANON | ECHO);
      raw.c_cc[VMIN] = 1;
      raw.c_cc[VTIME] = 0;
      if (tcsetattr(STDIN_FILENO, TCSANOW, &raw) != 0) {
        ch = getchar();
      } else {
        char byte = '\0';
        ssize_t count = read(STDIN_FILENO, &byte, 1);
        if (count > 0) {
          ch = (unsigned char)byte;
          if (ch == 27) {
            struct termios raw_more = raw;
            raw_more.c_cc[VMIN] = 0;
            raw_more.c_cc[VTIME] = 1;
            if (tcsetattr(STDIN_FILENO, TCSANOW, &raw_more) == 0) {
              char next = '\0';
              char third = '\0';
              ssize_t next_count = read(STDIN_FILENO, &next, 1);
              if (next_count > 0 && next == '[') {
                ssize_t third_count = read(STDIN_FILENO, &third, 1);
                if (third_count > 0) {
                  tcsetattr(STDIN_FILENO, TCSANOW, &oldt);
                  if (third == 'A') return (long long)(uintptr_t)token_up;
                  if (third == 'B') return (long long)(uintptr_t)token_down;
                  if (third == 'C') return (long long)(uintptr_t)token_right;
                  if (third == 'D') return (long long)(uintptr_t)token_left;
                }
              }
            }
          }
        }
        tcsetattr(STDIN_FILENO, TCSANOW, &oldt);
      }
    }
  }
  if (ch == EOF) return (long long)(uintptr_t)"";
  if (ch == 1) return (long long)(uintptr_t)token_ctrl_a;
  if (ch == 2) return (long long)(uintptr_t)token_ctrl_b;
  if (ch == 4) return (long long)(uintptr_t)token_ctrl_d;
  if (ch == 5) return (long long)(uintptr_t)token_ctrl_e;
  if (ch == 6) return (long long)(uintptr_t)token_ctrl_f;
  if (ch == 12) return (long long)(uintptr_t)token_ctrl_l;
  if (ch == 8 || ch == 127) return (long long)(uintptr_t)token_backspace;
  if (ch == 27) return (long long)(uintptr_t)token_escape;
  if (ch == '\n' || ch == '\r') return (long long)(uintptr_t)token_enter;
  if (ch == '\t') return (long long)(uintptr_t)token_tab;
  /* A single read() byte >= 0x80 is at most the lead of a multibyte sequence,
   * never a complete UTF-8 char, so returning it would mint an invalid String.
   * Reject with a clean panic, uniform with the other UTF-8 builtins (review
   * W2/R4); assembling a full multibyte key is a separate deferred feature. */
  if (ch >= 0x80)
    tcp_fail("term_read_key: non-ASCII byte cannot form a complete UTF-8 char");
  /* Heap-allocate a fresh String per keypress so a retained result never
   * mutates under the caller on the next call (the old static-buffer aliasing). */
  sprout_gc_maybe_collect_threshold();
  char* out = sprout_gc_alloc_cstr(1, "term_read_key: out of memory");
  out[0] = (char)ch;
  out[1] = '\0';
  return (long long)(uintptr_t)out;
}
long long term_write(long long text_val) {
  const char* text = (const char*)text_val;
  if (text == NULL) tcp_fail("term_write: null text");
  fputs(text, stdout);
  fflush(stdout);
  return 0;
}
static char* sprout_json_escape(const char* text) {
  if (text == NULL) tcp_fail("analysis service: null json text");
  size_t extra = 0;
  for (const unsigned char* p = (const unsigned char*)text; *p != '\0'; ++p) {
    switch (*p) {
      case '\\':
      case '"':
      case '\n':
      case '\r':
      case '\t':
        extra += 1;
        break;
      default:
        break;
    }
  }
  size_t len = strlen(text);
  char* out = alloc_cstr(len + extra, "analysis service: out of memory");
  size_t idx = 0;
  for (const unsigned char* p = (const unsigned char*)text; *p != '\0'; ++p) {
    switch (*p) {
      case '\\':
        out[idx++] = '\\';
        out[idx++] = '\\';
        break;
      case '"':
        out[idx++] = '\\';
        out[idx++] = '"';
        break;
      case '\n':
        out[idx++] = '\\';
        out[idx++] = 'n';
        break;
      case '\r':
        out[idx++] = '\\';
        out[idx++] = 'r';
        break;
      case '\t':
        out[idx++] = '\\';
        out[idx++] = 't';
        break;
      default:
        out[idx++] = (char)*p;
        break;
    }
  }
  out[idx] = '\0';
  return out;
}
static const char* sprout_json_after_key(const char* text, const char* key) {
  if (text == NULL || key == NULL) return NULL;
  size_t key_len = strlen(key);
  size_t pattern_len = key_len + 2;
  char* pattern = alloc_cstr(pattern_len, "analysis service: out of memory");
  pattern[0] = '"';
  memcpy(pattern + 1, key, key_len);
  pattern[key_len + 1] = '"';
  pattern[key_len + 2] = '\0';
  const char* pos = strstr(text, pattern);
  free(pattern);
  if (pos == NULL) return NULL;
  pos += pattern_len;
  while (*pos == ' ' || *pos == '\n' || *pos == '\r' || *pos == '\t') pos++;
  if (*pos != ':') return NULL;
  pos++;
  while (*pos == ' ' || *pos == '\n' || *pos == '\r' || *pos == '\t') pos++;
  return pos;
}
static int sprout_json_field_is_true(const char* text, const char* key) {
  const char* pos = sprout_json_after_key(text, key);
  return pos != NULL && strncmp(pos, "true", 4) == 0;
}
static const char* sprout_json_skip_ws(const char* pos) {
  if (pos == NULL) return NULL;
  while (*pos == ' ' || *pos == '\n' || *pos == '\r' || *pos == '\t') pos++;
  return pos;
}
static int sprout_json_parse_hex4(const char* pos, unsigned int* out) {
  if (strlen(pos) < 4) return 0;
  unsigned int value = 0;
  for (int i = 0; i < 4; i++) {
    unsigned char ch = (unsigned char)pos[i];
    value <<= 4;
    if (ch >= '0' && ch <= '9') {
      value |= (unsigned int)(ch - '0');
    } else if (ch >= 'a' && ch <= 'f') {
      value |= (unsigned int)(10 + ch - 'a');
    } else if (ch >= 'A' && ch <= 'F') {
      value |= (unsigned int)(10 + ch - 'A');
    } else {
      return 0;
    }
  }
  *out = value;
  return 1;
}
static size_t sprout_json_append_utf8(char* out, size_t idx, unsigned int codepoint) {
  if (codepoint <= 0x7f) {
    out[idx++] = (char)codepoint;
  } else if (codepoint <= 0x7ff) {
    out[idx++] = (char)(0xc0 | ((codepoint >> 6) & 0x1f));
    out[idx++] = (char)(0x80 | (codepoint & 0x3f));
  } else if (codepoint <= 0xffff) {
    out[idx++] = (char)(0xe0 | ((codepoint >> 12) & 0x0f));
    out[idx++] = (char)(0x80 | ((codepoint >> 6) & 0x3f));
    out[idx++] = (char)(0x80 | (codepoint & 0x3f));
  } else {
    out[idx++] = (char)(0xf0 | ((codepoint >> 18) & 0x07));
    out[idx++] = (char)(0x80 | ((codepoint >> 12) & 0x3f));
    out[idx++] = (char)(0x80 | ((codepoint >> 6) & 0x3f));
    out[idx++] = (char)(0x80 | (codepoint & 0x3f));
  }
  return idx;
}
static char* sprout_json_parse_string_impl(const char** pos_ptr, const char** err_msg) {
  const char* pos = sprout_json_skip_ws(*pos_ptr);
  if (pos == NULL || *pos != '"') return NULL;
  pos++;
  size_t cap = strlen(pos) + 1;
  char* out = alloc_cstr(cap, "analysis service: out of memory");
  size_t idx = 0;
  while (*pos != '\0') {
    if (*pos == '"') {
      out[idx] = '\0';
      *pos_ptr = pos + 1;
      return out;
    }
    if ((unsigned char)*pos < 0x20) {
      if (err_msg != NULL) *err_msg = "invalid control character in string";
      free(out);
      return NULL;
    }
    if (*pos == '\\') {
      pos++;
      if (*pos == '\0') {
        if (err_msg != NULL) *err_msg = "unterminated escape sequence";
        free(out);
        return NULL;
      }
      switch (*pos) {
        case '\\':
        case '"':
        case '/':
          out[idx++] = *pos;
          break;
        case 'b':
          out[idx++] = '\b';
          break;
        case 'f':
          out[idx++] = '\f';
          break;
        case 'n':
          out[idx++] = '\n';
          break;
        case 'r':
          out[idx++] = '\r';
          break;
        case 't':
          out[idx++] = '\t';
          break;
        case 'u': {
          unsigned int codepoint = 0;
          if (!sprout_json_parse_hex4(pos + 1, &codepoint)) {
            if (err_msg != NULL) *err_msg = "invalid unicode escape";
            free(out);
            return NULL;
          }
          pos += 4;
          if (codepoint >= 0xd800 && codepoint <= 0xdbff) {
            if (pos[1] != '\\' || pos[2] != 'u') {
              if (err_msg != NULL) *err_msg = "missing low surrogate";
              free(out);
              return NULL;
            }
            unsigned int low = 0;
            if (!sprout_json_parse_hex4(pos + 3, &low) || low < 0xdc00 || low > 0xdfff) {
              if (err_msg != NULL) *err_msg = "invalid low surrogate";
              free(out);
              return NULL;
            }
            codepoint = 0x10000 + (((codepoint - 0xd800) << 10) | (low - 0xdc00));
            pos += 6;
          } else if (codepoint >= 0xdc00 && codepoint <= 0xdfff) {
            if (err_msg != NULL) *err_msg = "unexpected low surrogate";
            free(out);
            return NULL;
          }
          idx = sprout_json_append_utf8(out, idx, codepoint);
          break;
        }
        default:
          if (err_msg != NULL) *err_msg = "invalid escape sequence";
          free(out);
          return NULL;
      }
      pos++;
      continue;
    }
    out[idx++] = *pos;
    pos++;
  }
  free(out);
  return NULL;
}
static char* sprout_json_parse_string(const char** pos_ptr) {
  return sprout_json_parse_string_impl(pos_ptr, NULL);
}
static VectorVal* sprout_json_extract_string_array(const char* text, const char* key) {
  const char* pos = sprout_json_after_key(text, key);
  pos = sprout_json_skip_ws(pos);
  if (pos == NULL || *pos != '[') return NULL;
  pos++;
  VectorVal* out = sprout_alloc_vector_val("analysis service: out of memory");
  SPROUT_HANDLE(h_out, (long long)(uintptr_t)out);
  out->len = 0;
  out->cap = 0;
  out->data = NULL;
  pos = sprout_json_skip_ws(pos);
  if (*pos == ']') return (VectorVal*)(uintptr_t)sprout_handle_get(h_out);
  while (*pos != '\0') {
    char* item = sprout_json_parse_string(&pos);
    if (item == NULL) return (VectorVal*)(uintptr_t)sprout_handle_get(h_out);
    item = register_cstr(item);
    if (out->len == out->cap) {
      long long new_cap = out->cap == 0 ? 4 : (out->cap * 2);
      out->data = sprout_realloc_vector_data(out->data, (size_t)new_cap, "analysis service: out of memory");
      out->cap = new_cap;
    }
    out->data[out->len++] = (long long)(uintptr_t)item;
    pos = sprout_json_skip_ws(pos);
    if (*pos == ']') return (VectorVal*)(uintptr_t)sprout_handle_get(h_out);
    if (*pos != ',') return (VectorVal*)(uintptr_t)sprout_handle_get(h_out);
    pos++;
  }
  return (VectorVal*)(uintptr_t)sprout_handle_get(h_out);
}
static char* sprout_json_extract_string(const char* text, const char* key) {
  const char* pos = sprout_json_after_key(text, key);
  return pos == NULL ? NULL : sprout_json_parse_string(&pos);
}
static long long sprout_json_extract_int(const char* text, const char* key, int* found_out) {
  const char* pos = sprout_json_after_key(text, key);
  if (pos == NULL) { if (found_out) *found_out = 0; return 0; }
  char* end = NULL;
  long long value = strtoll(pos, &end, 10);
  if (end == pos) { if (found_out) *found_out = 0; return 0; }
  if (found_out) *found_out = 1;
  return value;
}
static long long* sprout_json_extract_int_array(const char* text, const char* key, long long* out_len) {
  const char* pos = sprout_json_after_key(text, key);
  pos = sprout_json_skip_ws(pos);
  if (out_len != NULL) *out_len = 0;
  if (pos == NULL || *pos != '[') return NULL;
  pos++;
  long long cap = 0;
  long long len = 0;
  long long* out = NULL;
  pos = sprout_json_skip_ws(pos);
  if (*pos == ']') return out;
  while (*pos != '\0') {
    char* end = NULL;
    long long value = strtoll(pos, &end, 10);
    if (end == pos) {
      if (out != NULL) free(out);
      return NULL;
    }
    if (len == cap) {
      long long new_cap = cap == 0 ? 4 : (cap * 2);
      long long* grown = realloc(out, sizeof(long long) * (size_t)new_cap);
      if (grown == NULL) {
        if (out != NULL) free(out);
        tcp_fail("analysis service: out of memory");
      }
      out = grown;
      cap = new_cap;
    }
    out[len++] = value;
    pos = sprout_json_skip_ws(end);
    if (*pos == ']') {
      if (out_len != NULL) *out_len = len;
      return out;
    }
    if (*pos != ',') break;
    pos = sprout_json_skip_ws(pos + 1);
  }
  if (out != NULL) free(out);
  return NULL;
}
__attribute__((noreturn)) static void sprout_builtin_fail_detail(const char* builtin_name, const char* detail) {
  size_t len = strlen(builtin_name) + strlen(detail) + 2;
  char* msg = alloc_cstr(len, "analysis service: out of memory");
  snprintf(msg, len + 1, "%s: %s", builtin_name, detail);
  tcp_fail(msg);
}
static char* sprout_json_encode_string_array_from_vec_handle(const void* vec_handle_ptr, const char* builtin_name, const char* label) {
  long long vec_handle = (long long)(uintptr_t)vec_handle_ptr;
  if (sprout_heap_kind_at((void*)(uintptr_t)vec_handle) != SPROUT_HEAP_OBJ) {
    char detail[128];
    snprintf(detail, sizeof(detail), "expects %s to be Vec String", label);
    sprout_builtin_fail_detail(builtin_name, detail);
  }
  if (sprout_tag(vec_handle) != find_ctor_tag_by_name("Vec")) {
    char detail[128];
    snprintf(detail, sizeof(detail), "expects %s to be Vec String", label);
    sprout_builtin_fail_detail(builtin_name, detail);
  }
  long long raw_handle = sprout_field(vec_handle, 0);
  if (sprout_heap_kind_at((void*)(uintptr_t)raw_handle) != SPROUT_HEAP_VECTOR) {
    char detail[128];
    snprintf(detail, sizeof(detail), "expects %s to be Vec String", label);
    sprout_builtin_fail_detail(builtin_name, detail);
  }
  VectorVal* raw = (VectorVal*)(uintptr_t)raw_handle;
  size_t count = raw->len < 0 ? 0 : (size_t)raw->len;
  char** escaped_items = count == 0 ? NULL : (char**)malloc(sizeof(char*) * count);
  if (count != 0 && escaped_items == NULL) tcp_fail("analysis service: out of memory");
  size_t total_len = 2;
  for (size_t i = 0; i < count; i++) {
    const char* item = (const char*)(uintptr_t)raw->data[i];
    if (item == NULL) {
      if (escaped_items != NULL) free(escaped_items);
      char detail[128];
      snprintf(detail, sizeof(detail), "expects %s to contain only String values", label);
      sprout_builtin_fail_detail(builtin_name, detail);
    }
    escaped_items[i] = sprout_json_escape(item);
    total_len += strlen(escaped_items[i]) + 2;
    if (i + 1 < count) total_len += 1;
  }
  char* out = alloc_cstr(total_len, "analysis service: out of memory");
  size_t cursor = 0;
  out[cursor++] = '[';
  for (size_t i = 0; i < count; i++) {
    size_t item_len = strlen(escaped_items[i]);
    out[cursor++] = '"';
    memcpy(out + cursor, escaped_items[i], item_len);
    cursor += item_len;
    out[cursor++] = '"';
    if (i + 1 < count) out[cursor++] = ',';
    free(escaped_items[i]);
  }
  if (escaped_items != NULL) free(escaped_items);
  out[cursor++] = ']';
  out[cursor] = '\0';
  return out;
}

static FILE* sprout_analysis_service_in = NULL;
static FILE* sprout_analysis_service_out = NULL;
static pid_t sprout_analysis_service_pid = -1;
static int sprout_analysis_service_atexit_registered = 0;
static int sprout_analysis_service_sigpipe_ignored = 0;
static int sprout_analysis_service_last_status = 0;
static int sprout_analysis_service_last_status_valid = 0;
static void sprout_record_analysis_service_status(int status) {
  sprout_analysis_service_last_status = status;
  sprout_analysis_service_last_status_valid = 1;
}
static int sprout_analysis_service_command_not_found(void) {
  return sprout_analysis_service_last_status_valid
    && WIFEXITED(sprout_analysis_service_last_status)
    && WEXITSTATUS(sprout_analysis_service_last_status) == 127;
}
static void sprout_close_analysis_service(void) {
  if (sprout_analysis_service_in != NULL) {
    fclose(sprout_analysis_service_in);
    sprout_analysis_service_in = NULL;
  }
  if (sprout_analysis_service_out != NULL) {
    fclose(sprout_analysis_service_out);
    sprout_analysis_service_out = NULL;
  }
  if (sprout_analysis_service_pid > 0) {
    int status = 0;
    if (waitpid(sprout_analysis_service_pid, &status, 0) == sprout_analysis_service_pid) {
      sprout_record_analysis_service_status(status);
    }
    sprout_analysis_service_pid = -1;
  }
}
static int sprout_analysis_service_is_stale(void) {
  if (sprout_analysis_service_pid <= 0) return 0;
  int status = 0;
  pid_t waited = waitpid(sprout_analysis_service_pid, &status, WNOHANG);
  if (waited == 0) return 0;
  if (waited == sprout_analysis_service_pid) {
    sprout_record_analysis_service_status(status);
    sprout_analysis_service_pid = -1;
    return 1;
  }
  return 0;
}
static int sprout_ensure_analysis_service(char** error_out) {
  if (!sprout_analysis_service_sigpipe_ignored) {
    signal(SIGPIPE, SIG_IGN);
    sprout_analysis_service_sigpipe_ignored = 1;
  }
  if (sprout_analysis_service_is_stale()) {
    sprout_close_analysis_service();
  }
  if (sprout_analysis_service_in != NULL && sprout_analysis_service_out != NULL && sprout_analysis_service_pid > 0) {
    return 1;
  }
  sprout_analysis_service_last_status_valid = 0;
  const char* cmd = getenv("SPROUT_ANALYSIS_SERVICE_CMD");
  if (cmd == NULL || *cmd == '\0') cmd = "python3 -m sprout.analysis_service_entrypoint";
  int request_pipe[2] = {-1, -1};
  int response_pipe[2] = {-1, -1};
  if (pipe(request_pipe) != 0 || pipe(response_pipe) != 0) {
    if (request_pipe[0] >= 0) close(request_pipe[0]);
    if (request_pipe[1] >= 0) close(request_pipe[1]);
    if (response_pipe[0] >= 0) close(response_pipe[0]);
    if (response_pipe[1] >= 0) close(response_pipe[1]);
    *error_out = dup_cstr("analysis service: unable to create pipes");
    return 0;
  }
  pid_t pid = fork();
  if (pid < 0) {
    close(request_pipe[0]);
    close(request_pipe[1]);
    close(response_pipe[0]);
    close(response_pipe[1]);
    *error_out = dup_cstr("analysis service: unable to fork");
    return 0;
  }
  if (pid == 0) {
    dup2(request_pipe[0], STDIN_FILENO);
    dup2(response_pipe[1], STDOUT_FILENO);
    /* The daemon's stdout carries the JSON protocol, so stderr is a free
       diagnostic channel.  Historically it was sent to /dev/null, which
       silently discarded every panic, `sprout_tag: null pointer` abort, and
       stack-overflow backtrace — leaving a crashed daemon indistinguishable
       from a hang (the client just sees "empty response").  Capture stderr to
       a logfile instead.  Override the path with SPROUT_ANALYSIS_SERVICE_LOG,
       or set it to "off" to restore the previous /dev/null behavior. */
    const char* log_target = getenv("SPROUT_ANALYSIS_SERVICE_LOG");
    if (log_target == NULL || log_target[0] == '\0')
      log_target = "/tmp/sprout_analysis_service.log";
    if (strcmp(log_target, "off") == 0 || freopen(log_target, "a", stderr) == NULL) {
      freopen("/dev/null", "w", stderr);
    } else {
      /* freopen reassigns fd 2 to the logfile; the fd (not the FILE* stream's
         buffering state) is what survives the execl below, so the daemon
         inherits the redirect.  C guarantees stderr is never fully buffered at
         startup and both glibc and macOS libc leave it unbuffered, so the
         daemon's post-exec fprintf(stderr,...) crash messages — including the
         no-newline `sprout_tag: null pointer` abort — reach the log on their
         own.  The banner is written with raw write(2) for the same reason the
         stack-overflow handler does: no dependence on stdio state. */
      char banner[160];
      int bn = snprintf(banner, sizeof(banner),
        "\n=== sprout analysis-service daemon started (pid %d) ===\n",
        (int)getpid());
      if (bn > 0) { ssize_t bw = write(STDERR_FILENO, banner, (size_t)bn); (void)bw; }
    }
    close(request_pipe[0]);
    close(request_pipe[1]);
    close(response_pipe[0]);
    close(response_pipe[1]);
    execl("/bin/sh", "sh", "-lc", cmd, (char*)NULL);
    _exit(127);
  }
  close(request_pipe[0]);
  close(response_pipe[1]);
  FILE* in_file = fdopen(request_pipe[1], "w");
  FILE* out_file = fdopen(response_pipe[0], "r");
  if (in_file == NULL || out_file == NULL) {
    if (in_file != NULL) fclose(in_file);
    else close(request_pipe[1]);
    if (out_file != NULL) fclose(out_file);
    else close(response_pipe[0]);
    close(request_pipe[0]);
    close(response_pipe[1]);
    waitpid(pid, NULL, 0);
    *error_out = dup_cstr("analysis service: unable to open pipes");
    return 0;
  }
  setvbuf(in_file, NULL, _IOLBF, 0);
  sprout_analysis_service_in = in_file;
  sprout_analysis_service_out = out_file;
  sprout_analysis_service_pid = pid;
  if (!sprout_analysis_service_atexit_registered) {
    atexit(sprout_close_analysis_service);
    sprout_analysis_service_atexit_registered = 1;
  }
  return 1;
}
static int sprout_run_analysis_service(const char* request_json, int retry_once, char** response_out, char** error_out) {
  int max_attempts = retry_once ? 2 : 1;
  for (int attempt = 0; attempt < max_attempts; attempt++) {
    if (!sprout_ensure_analysis_service(error_out)) return 0;
    if (fputs(request_json, sprout_analysis_service_in) == EOF || fflush(sprout_analysis_service_in) != 0) {
      sprout_close_analysis_service();
      if (attempt + 1 < max_attempts) continue;
      *error_out = dup_cstr("analysis service: request failed");
      return 0;
    }
    char* response = NULL;
    size_t response_cap = 0;
    ssize_t response_len = getline(&response, &response_cap, sprout_analysis_service_out);
    if (response_len < 0) {
      if (response != NULL) free(response);
      sprout_close_analysis_service();
      if (attempt + 1 < max_attempts) continue;
      if (sprout_analysis_service_command_not_found()) *error_out = dup_cstr("analysis service: command failed to start; check SPROUT_ANALYSIS_SERVICE_CMD");
      else *error_out = dup_cstr("analysis service: empty response");
      return 0;
    }
    if (response_len > 0 && response[response_len - 1] == '\n') {
      response[response_len - 1] = '\0';
    }
    *response_out = response;
    return 1;
  }
  *error_out = dup_cstr("analysis service: request failed");
  return 0;
}
static int sprout_analysis_service_retry_allowed(const char* op) {
  return strcmp(op, "check_source") == 0
    || strcmp(op, "type_of_in_source") == 0
    || strcmp(op, "declared_names_in_source") == 0
    || strcmp(op, "exported_names_in_source") == 0
    || strcmp(op, "symbol_inventory_in_source") == 0
    || strcmp(op, "diagnostics_in_source") == 0
    || strcmp(op, "symbol_locations_in_source") == 0
    || strcmp(op, "instances_in_source") == 0
    || strcmp(op, "complete_in_state") == 0
    || strcmp(op, "session_create") == 0
    || strcmp(op, "session_type_of") == 0
    || strcmp(op, "session_diagnostics") == 0;
}

static long long sprout_err_string_result(const char* message) {
  char* owned = dup_managed_cstr(message, "analysis service: out of memory");
  SPROUT_HANDLE(h_message, (long long)(uintptr_t)owned);
  return sprout_make1(find_ctor_tag_by_name("Err"), sprout_handle_get(h_message));
}

static char* sprout_analysis_request_source_only(const char* op, const char* module_source) {
  char* escaped_source = sprout_json_escape(module_source);
  size_t request_len = strlen(op) + strlen(escaped_source) + 48;
  char* request = alloc_cstr(request_len, "analysis service: out of memory");
  snprintf(
    request,
    request_len + 1,
    "{\"op\":\"%s\",\"module_source\":\"%s\"}\n",
    op,
    escaped_source
  );
  free(escaped_source);
  return request;
}

static char* sprout_analysis_request_source_field(
  const char* op,
  const char* module_source,
  const char* field_name,
  const char* field_value
) {
  char* escaped_source = sprout_json_escape(module_source);
  char* escaped_value = sprout_json_escape(field_value);
  size_t request_len = strlen(op) + strlen(escaped_source) + strlen(field_name) + strlen(escaped_value) + 64;
  char* request = alloc_cstr(request_len, "analysis service: out of memory");
  snprintf(
    request,
    request_len + 1,
    "{\"op\":\"%s\",\"module_source\":\"%s\",\"%s\":\"%s\"}\n",
    op,
    escaped_source,
    field_name,
    escaped_value
  );
  free(escaped_source);
  free(escaped_value);
  return request;
}

static long long sprout_analysis_error_from_response(char* response) {
  char* error = sprout_json_extract_string(response, "error");
  free(response);
  long long out = sprout_err_string_result(error != NULL ? error : "analysis service: invalid response");
  if (error != NULL) free(error);
  return out;
}

static long long sprout_analysis_ok_string_result_from_response(char* response, const char* value_key) {
  char* value = sprout_json_extract_string(response, value_key);
  free(response);
  if (value == NULL) return sprout_err_string_result("analysis service: invalid response");
  value = register_cstr(value);
  SPROUT_HANDLE(h_value, (long long)(uintptr_t)value);
  return sprout_make1(find_ctor_tag_by_name("Ok"), sprout_handle_get(h_value));
}

static long long sprout_analysis_ok_vec_string_result(VectorVal* items) {
  if (items == NULL) return sprout_err_string_result("analysis service: invalid response");
  long long rooted_items = (long long)(uintptr_t)items;
  SPROUT_GC_PUSH_I64_LOCAL(rooted_items);
  long long items_vec = sprout_make1(find_ctor_tag_by_name("Vec"), rooted_items);
  SPROUT_GC_PUSH_I64_LOCAL(items_vec);
  long long out = sprout_make1(find_ctor_tag_by_name("Ok"), items_vec);
  SPROUT_GC_POP_LOCALS(2);
  return out;
}

static long long sprout_analysis_ok_vec_string_result_from_response(char* response, const char* value_key) {
  VectorVal* items = sprout_json_extract_string_array(response, value_key);
  free(response);
  return sprout_analysis_ok_vec_string_result(items);
}

static long long sprout_analysis_ok_string_vec_pair_result(char* label, VectorVal* items) {
  if (label == NULL || items == NULL) return sprout_err_string_result("analysis service: invalid response");
  label = register_cstr(label);
  SPROUT_HANDLE(h_label, (long long)(uintptr_t)label);
  long long rooted_items = (long long)(uintptr_t)items;
  SPROUT_GC_PUSH_I64_LOCAL(rooted_items);
  long long items_vec = sprout_make1(find_ctor_tag_by_name("Vec"), rooted_items);
  SPROUT_GC_PUSH_I64_LOCAL(items_vec);
  void* tuple = (void*)(uintptr_t)sprout_alloc_tuple_blob((long long)(sizeof(uintptr_t) * 2));
  long long pair = (long long)(uintptr_t)tuple;
  SPROUT_GC_PUSH_I64_LOCAL(pair);
  uintptr_t* words = (uintptr_t*)tuple;
  words[0] = (uintptr_t)sprout_handle_get(h_label);
  words[1] = (uintptr_t)items_vec;
  long long out = sprout_make1(find_ctor_tag_by_name("Ok"), pair);
  SPROUT_GC_POP_LOCALS(3);
  return out;
}

static long long sprout_analysis_ok_string_vec_pair_from_response(
  char* response,
  const char* string_key,
  const char* array_key
) {
  char* label = sprout_json_extract_string(response, string_key);
  VectorVal* items = sprout_json_extract_string_array(response, array_key);
  free(response);
  return sprout_analysis_ok_string_vec_pair_result(label, items);
}

static long long sprout_analysis_completion_tuple_or_fail(
  const char* builtin_name,
  char* response,
  const char* string_key,
  const char* array_key
) {
  char* prefix = sprout_json_extract_string(response, string_key);
  VectorVal* matches = sprout_json_extract_string_array(response, array_key);
  free(response);
  if (prefix == NULL || matches == NULL) {
    sprout_builtin_fail_detail(builtin_name, "analysis service: invalid response");
  }
  prefix = register_cstr(prefix);
  SPROUT_HANDLE(h_prefix, (long long)(uintptr_t)prefix);
  long long rooted_matches = (long long)(uintptr_t)matches;
  SPROUT_GC_PUSH_I64_LOCAL(rooted_matches);
  long long matches_vec = sprout_make1(find_ctor_tag_by_name("Vec"), rooted_matches);
  SPROUT_GC_PUSH_I64_LOCAL(matches_vec);
  void* tuple = (void*)(uintptr_t)sprout_alloc_tuple_blob((long long)(sizeof(uintptr_t) * 2));
  long long tuple_h = (long long)(uintptr_t)tuple;
  SPROUT_GC_PUSH_I64_LOCAL(tuple_h);
  uintptr_t* words = (uintptr_t*)tuple;
  words[0] = (uintptr_t)sprout_handle_get(h_prefix);
  words[1] = (uintptr_t)matches_vec;
  SPROUT_GC_POP_LOCALS(3);
  return tuple_h;
}

static long long sprout_analysis_ok_inventory_result(
  VectorVal* declared,
  VectorVal* imported,
  VectorVal* exported
) {
  if (declared == NULL || imported == NULL || exported == NULL) {
    return sprout_err_string_result("analysis service: invalid response");
  }
  long long rooted_declared = (long long)(uintptr_t)declared;
  long long rooted_imported = (long long)(uintptr_t)imported;
  long long rooted_exported = (long long)(uintptr_t)exported;
  SPROUT_GC_PUSH_I64_LOCAL(rooted_declared);
  SPROUT_GC_PUSH_I64_LOCAL(rooted_imported);
  SPROUT_GC_PUSH_I64_LOCAL(rooted_exported);
  long long declared_vec = sprout_make1(find_ctor_tag_by_name("Vec"), rooted_declared);
  SPROUT_GC_PUSH_I64_LOCAL(declared_vec);
  long long imported_vec = sprout_make1(find_ctor_tag_by_name("Vec"), rooted_imported);
  SPROUT_GC_PUSH_I64_LOCAL(imported_vec);
  long long exported_vec = sprout_make1(find_ctor_tag_by_name("Vec"), rooted_exported);
  SPROUT_GC_PUSH_I64_LOCAL(exported_vec);
  void* tuple = (void*)(uintptr_t)sprout_alloc_tuple_blob((long long)(sizeof(uintptr_t) * 3));
  long long tuple_h = (long long)(uintptr_t)tuple;
  SPROUT_GC_PUSH_I64_LOCAL(tuple_h);
  uintptr_t* words = (uintptr_t*)tuple;
  words[0] = (uintptr_t)declared_vec;
  words[1] = (uintptr_t)imported_vec;
  words[2] = (uintptr_t)exported_vec;
  long long out = sprout_make1(find_ctor_tag_by_name("Ok"), tuple_h);
  SPROUT_GC_POP_LOCALS(7);
  return out;
}

static long long sprout_analysis_ok_inventory_from_response(
  char* response,
  const char* declared_key,
  const char* imported_key,
  const char* exported_key
) {
  VectorVal* declared = sprout_json_extract_string_array(response, declared_key);
  long long rooted_declared = (long long)(uintptr_t)declared;
  SPROUT_GC_PUSH_I64_LOCAL(rooted_declared);
  VectorVal* imported = sprout_json_extract_string_array(response, imported_key);
  long long rooted_imported = (long long)(uintptr_t)imported;
  SPROUT_GC_PUSH_I64_LOCAL(rooted_imported);
  VectorVal* exported = sprout_json_extract_string_array(response, exported_key);
  long long rooted_exported = (long long)(uintptr_t)exported;
  SPROUT_GC_PUSH_I64_LOCAL(rooted_exported);
  free(response);
  long long out = sprout_analysis_ok_inventory_result(declared, imported, exported);
  SPROUT_GC_POP_LOCALS(3);
  return out;
}

static long long sprout_analysis_diagnostics_vec_or_fail(
  const char* builtin_name,
  char* response,
  const char* messages_key,
  const char* lines_key,
  const char* columns_key
) {
  VectorVal* messages = sprout_json_extract_string_array(response, messages_key);
  long long rooted_messages = (long long)(uintptr_t)messages;
  SPROUT_GC_PUSH_I64_LOCAL(rooted_messages);
  long long line_count = 0;
  long long column_count = 0;
  long long* lines = sprout_json_extract_int_array(response, lines_key, &line_count);
  long long* columns = sprout_json_extract_int_array(response, columns_key, &column_count);
  free(response);
  if (
    messages == NULL ||
    line_count != messages->len ||
    column_count != messages->len ||
    (messages->len > 0 && (lines == NULL || columns == NULL))
  ) {
    if (lines != NULL) free(lines);
    if (columns != NULL) free(columns);
    SPROUT_GC_POP_LOCALS(1);
    sprout_builtin_fail_detail(builtin_name, "analysis service: invalid response");
  }
  VectorVal* out = sprout_alloc_vector_val("analysis service: out of memory");
  long long rooted_out = (long long)(uintptr_t)out;
  SPROUT_GC_PUSH_I64_LOCAL(rooted_out);
  out->len = messages->len;
  out->cap = messages->len;
  out->data = messages->len == 0 ? NULL : sprout_realloc_vector_data(NULL, (size_t)messages->len, "analysis service: out of memory");
  for (long long i = 0; i < messages->len; i++) {
    void* tuple = (void*)(uintptr_t)sprout_alloc_tuple_blob((long long)(sizeof(uintptr_t) * 3));
    uintptr_t* words = (uintptr_t*)tuple;
    words[0] = (uintptr_t)messages->data[i];
    words[1] = (uintptr_t)lines[i];
    words[2] = (uintptr_t)columns[i];
    out->data[i] = (long long)(uintptr_t)tuple;
  }
  if (lines != NULL) free(lines);
  if (columns != NULL) free(columns);
  long long out_vec = sprout_make1(find_ctor_tag_by_name("Vec"), rooted_out);
  SPROUT_GC_POP_LOCALS(2);
  return out_vec;
}

static long long sprout_analysis_ok_symbol_locations_from_response(
  char* response,
  const char* categories_key,
  const char* names_key,
  const char* lines_key,
  const char* columns_key
) {
  VectorVal* categories = sprout_json_extract_string_array(response, categories_key);
  long long rooted_categories = (long long)(uintptr_t)categories;
  SPROUT_GC_PUSH_I64_LOCAL(rooted_categories);
  VectorVal* names = sprout_json_extract_string_array(response, names_key);
  long long rooted_names = (long long)(uintptr_t)names;
  SPROUT_GC_PUSH_I64_LOCAL(rooted_names);
  long long line_count = 0;
  long long column_count = 0;
  long long* lines = sprout_json_extract_int_array(response, lines_key, &line_count);
  long long* columns = sprout_json_extract_int_array(response, columns_key, &column_count);
  free(response);
  if (
    categories == NULL ||
    names == NULL ||
    categories->len != names->len ||
    line_count != categories->len ||
    column_count != categories->len ||
    (categories->len > 0 && (lines == NULL || columns == NULL))
  ) {
    if (lines != NULL) free(lines);
    if (columns != NULL) free(columns);
    SPROUT_GC_POP_LOCALS(2);
    return sprout_err_string_result("analysis service: invalid response");
  }
  VectorVal* out = sprout_alloc_vector_val("analysis service: out of memory");
  long long rooted_out = (long long)(uintptr_t)out;
  SPROUT_GC_PUSH_I64_LOCAL(rooted_out);
  out->len = categories->len;
  out->cap = categories->len;
  out->data = categories->len == 0 ? NULL : sprout_realloc_vector_data(NULL, (size_t)categories->len, "analysis service: out of memory");
  for (long long i = 0; i < categories->len; i++) {
    void* tuple = (void*)(uintptr_t)sprout_alloc_tuple_blob((long long)(sizeof(uintptr_t) * 4));
    uintptr_t* words = (uintptr_t*)tuple;
    words[0] = (uintptr_t)categories->data[i];
    words[1] = (uintptr_t)names->data[i];
    words[2] = (uintptr_t)lines[i];
    words[3] = (uintptr_t)columns[i];
    out->data[i] = (long long)(uintptr_t)tuple;
  }
  if (lines != NULL) free(lines);
  if (columns != NULL) free(columns);
  long long rooted_vec_raw = (long long)(uintptr_t)out;
  long long out_vec = sprout_make1(find_ctor_tag_by_name("Vec"), rooted_vec_raw);
  SPROUT_GC_PUSH_I64_LOCAL(out_vec);
  long long result = sprout_make1(find_ctor_tag_by_name("Ok"), out_vec);
  SPROUT_GC_POP_LOCALS(4);
  return result;
}


static long long sprout_analysis_check_source_result(const char* op, const char* module_source) {
  char* request = sprout_analysis_request_source_only(op, module_source);
  char* response = NULL;
  char* error = NULL;
  if (!sprout_run_analysis_service(request, sprout_analysis_service_retry_allowed(op), &response, &error)) {
    free(request);
    long long out = sprout_err_string_result(error != NULL ? error : "__SPROUT_ANALYSIS_SERVICE_REQUEST_FAILED__");
    if (error != NULL) free(error);
    return out;
  }
  free(request);
  if (sprout_json_field_is_true(response, "ok")) {
    free(response);
    return sprout_make1(find_ctor_tag_by_name("Ok"), 0);
  }
  return sprout_analysis_error_from_response(response);
}
static long long sprout_analysis_type_result(const char* op, const char* module_source, const char* expr) {
  char* request = sprout_analysis_request_source_field(op, module_source, "expr", expr);
  char* response = NULL;
  char* error = NULL;
  if (!sprout_run_analysis_service(request, sprout_analysis_service_retry_allowed(op), &response, &error)) {
    free(request);
    long long out = sprout_err_string_result(error != NULL ? error : "__SPROUT_ANALYSIS_SERVICE_REQUEST_FAILED__");
    if (error != NULL) free(error);
    return out;
  }
  free(request);
  if (sprout_json_field_is_true(response, "ok")) {
    return sprout_analysis_ok_string_result_from_response(response, "value");
  }
  return sprout_analysis_error_from_response(response);
}
static long long sprout_analysis_instances_result(const char* op, const char* module_source, const char* query) {
  char* request = sprout_analysis_request_source_field(op, module_source, "query", query);
  char* response = NULL;
  char* error = NULL;
  if (!sprout_run_analysis_service(request, sprout_analysis_service_retry_allowed(op), &response, &error)) {
    free(request);
    long long out = sprout_err_string_result(error != NULL ? error : "__SPROUT_ANALYSIS_SERVICE_REQUEST_FAILED__");
    if (error != NULL) free(error);
    return out;
  }
  free(request);
  if (sprout_json_field_is_true(response, "ok")) {
    return sprout_analysis_ok_string_vec_pair_from_response(response, "query_type", "matches");
  }
  return sprout_analysis_error_from_response(response);
}
static long long sprout_analysis_vec_string_result(const char* op, const char* module_source, const char* expr) {
  char* request = sprout_analysis_request_source_field(op, module_source, "expr", expr);
  char* response = NULL;
  char* error = NULL;
  if (!sprout_run_analysis_service(request, sprout_analysis_service_retry_allowed(op), &response, &error)) {
    free(request);
    long long out = sprout_err_string_result(error != NULL ? error : "__SPROUT_ANALYSIS_SERVICE_REQUEST_FAILED__");
    if (error != NULL) free(error);
    return out;
  }
  free(request);
  if (sprout_json_field_is_true(response, "ok")) {
    return sprout_analysis_ok_vec_string_result_from_response(response, "value");
  }
  return sprout_analysis_error_from_response(response);
}
static long long sprout_analysis_string_array_result(const char* op, const char* module_source) {
  char* request = sprout_analysis_request_source_only(op, module_source);
  char* response = NULL;
  char* error = NULL;
  if (!sprout_run_analysis_service(request, sprout_analysis_service_retry_allowed(op), &response, &error)) {
    free(request);
    long long out = sprout_err_string_result(error != NULL ? error : "__SPROUT_ANALYSIS_SERVICE_REQUEST_FAILED__");
    if (error != NULL) free(error);
    return out;
  }
  free(request);
  if (sprout_json_field_is_true(response, "ok")) {
    return sprout_analysis_ok_vec_string_result_from_response(response, "value");
  }
  return sprout_analysis_error_from_response(response);
}
static long long sprout_analysis_inventory_result(const char* op, const char* module_source) {
  char* request = sprout_analysis_request_source_only(op, module_source);
  char* response = NULL;
  char* error = NULL;
  if (!sprout_run_analysis_service(request, sprout_analysis_service_retry_allowed(op), &response, &error)) {
    free(request);
    long long out = sprout_err_string_result(error != NULL ? error : "__SPROUT_ANALYSIS_SERVICE_REQUEST_FAILED__");
    if (error != NULL) free(error);
    return out;
  }
  free(request);
  if (sprout_json_field_is_true(response, "ok")) {
    return sprout_analysis_ok_inventory_from_response(
      response,
      "declared",
      "imported",
      "exported"
    );
  }
  return sprout_analysis_error_from_response(response);
}
static long long sprout_analysis_diagnostics_result(const char* op, const char* module_source) {
  char* request = sprout_analysis_request_source_only(op, module_source);
  char* response = NULL;
  char* error = NULL;
  if (!sprout_run_analysis_service(request, sprout_analysis_service_retry_allowed(op), &response, &error)) {
    free(request);
    sprout_builtin_fail_detail(op, error != NULL ? error : "__SPROUT_ANALYSIS_SERVICE_REQUEST_FAILED__");
  }
  free(request);
  if (sprout_json_field_is_true(response, "ok")) {
    return sprout_analysis_diagnostics_vec_or_fail(
      op,
      response,
      "messages",
      "lines",
      "columns"
    );
  }
  error = sprout_json_extract_string(response, "error");
  free(response);
  sprout_builtin_fail_detail(op, error != NULL ? error : "__SPROUT_ANALYSIS_SERVICE_INVALID_RESPONSE__");
  return 0;
}
static long long sprout_analysis_symbol_locations_result(const char* op, const char* module_source) {
  char* request = sprout_analysis_request_source_only(op, module_source);
  char* response = NULL;
  char* error = NULL;
  if (!sprout_run_analysis_service(request, sprout_analysis_service_retry_allowed(op), &response, &error)) {
    free(request);
    long long out = sprout_err_string_result(error != NULL ? error : "__SPROUT_ANALYSIS_SERVICE_REQUEST_FAILED__");
    if (error != NULL) free(error);
    return out;
  }
  free(request);
  if (sprout_json_field_is_true(response, "ok")) {
    return sprout_analysis_ok_symbol_locations_from_response(
      response,
      "categories",
      "names",
      "lines",
      "columns"
    );
  }
  return sprout_analysis_error_from_response(response);
}
static long long sprout_analysis_completion_result(const char* line_buffer, const void* imports_handle, const void* declarations_handle) {
  char* escaped_line_buffer = sprout_json_escape(line_buffer);
  char* imports_json = sprout_json_encode_string_array_from_vec_handle(imports_handle, "analysis_complete_in_state", "imports");
  char* declarations_json = sprout_json_encode_string_array_from_vec_handle(declarations_handle, "analysis_complete_in_state", "declarations");
  size_t request_len = strlen(escaped_line_buffer) + strlen(imports_json) + strlen(declarations_json) + 88;
  char* request = alloc_cstr(request_len, "analysis service: out of memory");
  snprintf(
    request,
    request_len + 1,
    "{\"op\":\"complete_in_state\",\"line_buffer\":\"%s\",\"imports\":%s,\"declarations\":%s}\n",
    escaped_line_buffer,
    imports_json,
    declarations_json
  );
  free(escaped_line_buffer);
  free(imports_json);
  free(declarations_json);
  char* response = NULL;
  char* error = NULL;
  if (!sprout_run_analysis_service(request, sprout_analysis_service_retry_allowed("complete_in_state"), &response, &error)) {
    free(request);
    sprout_builtin_fail_detail("analysis_complete_in_state", error != NULL ? error : "__SPROUT_ANALYSIS_SERVICE_REQUEST_FAILED__");
  }
  free(request);
  if (sprout_json_field_is_true(response, "ok")) {
    return sprout_analysis_completion_tuple_or_fail(
      "analysis_complete_in_state",
      response,
      "prefix",
      "matches"
    );
  }
  error = sprout_json_extract_string(response, "error");
  free(response);
  sprout_builtin_fail_detail("analysis_complete_in_state", error != NULL ? error : "__SPROUT_ANALYSIS_SERVICE_INVALID_RESPONSE__");
  return 0;
}
long long repl_add_import(const char* source) {
  (void)source;
  tcp_fail("repl_add_import: not supported in native backend");
  return 0;
}
long long repl_add_declaration(const char* source) {
  (void)source;
  tcp_fail("repl_add_declaration: not supported in native backend");
  return 0;
}
long long repl_eval_expr(const char* source) {
  (void)source;
  tcp_fail("repl_eval_expr: not supported in native backend");
  return 0;
}
/* --- Session op request builders --- */
static char* sprout_analysis_request_no_args(const char* op) {
  size_t request_len = strlen(op) + 16;
  char* request = alloc_cstr(request_len, "analysis service: out of memory");
  snprintf(request, request_len + 1, "{\"op\":\"%s\"}\n", op);
  return request;
}
static char* sprout_analysis_request_session_id_only(const char* op, long long session_id) {
  size_t request_len = strlen(op) + 48;
  char* request = alloc_cstr(request_len, "analysis service: out of memory");
  snprintf(request, request_len + 1, "{\"op\":\"%s\",\"session_id\":%lld}\n", op, session_id);
  return request;
}
static char* sprout_analysis_request_session_update(long long session_id, const char* kind, const char* line) {
  char* ek = sprout_json_escape(kind);
  char* el = sprout_json_escape(line);
  size_t len = strlen(ek) + strlen(el) + 80;
  char* request = alloc_cstr(len, "analysis service: out of memory");
  snprintf(request, len + 1,
    "{\"op\":\"session_update\",\"session_id\":%lld,\"kind\":\"%s\",\"line\":\"%s\"}\n",
    session_id, ek, el);
  free(ek); free(el);
  return request;
}
static char* sprout_analysis_request_session_id_expr(const char* op, long long session_id, const char* expr) {
  char* ee = sprout_json_escape(expr);
  size_t len = strlen(op) + strlen(ee) + 64;
  char* request = alloc_cstr(len, "analysis service: out of memory");
  snprintf(request, len + 1,
    "{\"op\":\"%s\",\"session_id\":%lld,\"expr\":\"%s\"}\n",
    op, session_id, ee);
  free(ee);
  return request;
}
/* --- Session builtins --- */
long long analysis_session_create(long long dummy) {
  (void)dummy;
  char* request = sprout_analysis_request_no_args("session_create");
  char* response = NULL;
  char* error = NULL;
  if (!sprout_run_analysis_service(request, 1, &response, &error)) {
    free(request);
    if (error != NULL) free(error);
    sprout_builtin_fail_detail("analysis_session_create", "analysis service: not available");
  }
  free(request);
  int found = 0;
  long long session_id = sprout_json_extract_int(response, "value", &found);
  free(response);
  if (!found) sprout_builtin_fail_detail("analysis_session_create", "analysis service: session_create: missing session_id");
  return session_id;
}
long long analysis_session_update(long long session_id, long long kind_handle, long long line_handle) {
  const char* kind = (const char*)(uintptr_t)kind_handle;
  const char* line = (const char*)(uintptr_t)line_handle;
  char* request = sprout_analysis_request_session_update(session_id, kind, line);
  char* response = NULL;
  char* error = NULL;
  if (!sprout_run_analysis_service(request, 0, &response, &error)) {
    free(request);
    long long out = sprout_err_string_result(error != NULL ? error : "analysis service: session_update failed");
    if (error != NULL) free(error);
    return out;
  }
  free(request);
  if (sprout_json_field_is_true(response, "ok")) {
    free(response);
    return sprout_make1(find_ctor_tag_by_name("Ok"), 0LL);
  }
  return sprout_analysis_error_from_response(response);
}
long long analysis_session_eval(long long session_id, long long expr_handle) {
  const char* expr = (const char*)(uintptr_t)expr_handle;
  char* request = sprout_analysis_request_session_id_expr("session_eval", session_id, expr);
  char* response = NULL;
  char* error = NULL;
  if (!sprout_run_analysis_service(request, 0, &response, &error)) {
    free(request);
    long long out = sprout_err_string_result(error != NULL ? error : "analysis service: session_eval failed");
    if (error != NULL) free(error);
    return out;
  }
  free(request);
  if (sprout_json_field_is_true(response, "ok")) {
    return sprout_analysis_ok_vec_string_result_from_response(response, "value");
  }
  return sprout_analysis_error_from_response(response);
}
long long analysis_session_type_of(long long session_id, long long expr_handle) {
  const char* expr = (const char*)(uintptr_t)expr_handle;
  char* request = sprout_analysis_request_session_id_expr("session_type_of", session_id, expr);
  char* response = NULL;
  char* error = NULL;
  if (!sprout_run_analysis_service(request, 1, &response, &error)) {
    free(request);
    long long out = sprout_err_string_result(error != NULL ? error : "analysis service: session_type_of failed");
    if (error != NULL) free(error);
    return out;
  }
  free(request);
  if (sprout_json_field_is_true(response, "ok")) {
    return sprout_analysis_ok_string_result_from_response(response, "value");
  }
  return sprout_analysis_error_from_response(response);
}
long long analysis_session_diagnostics(long long session_id) {
  char* request = sprout_analysis_request_session_id_only("session_diagnostics", session_id);
  char* response = NULL;
  char* error = NULL;
  if (!sprout_run_analysis_service(request, 1, &response, &error)) {
    free(request);
    sprout_builtin_fail_detail("analysis_session_diagnostics", error != NULL ? error : "analysis service: request failed");
  }
  free(request);
  if (sprout_json_field_is_true(response, "ok")) {
    return sprout_analysis_diagnostics_vec_or_fail("analysis_session_diagnostics", response, "messages", "lines", "columns");
  }
  char* err_msg = sprout_json_extract_string(response, "error");
  free(response);
  sprout_builtin_fail_detail("analysis_session_diagnostics", err_msg != NULL ? err_msg : "analysis service: session_diagnostics failed");
  return 0;
}
long long analysis_session_destroy(long long session_id) {
  char* request = sprout_analysis_request_session_id_only("session_destroy", session_id);
  char* response = NULL;
  char* error = NULL;
  if (!sprout_run_analysis_service(request, 0, &response, &error)) {
    free(request);
    if (error != NULL) free(error);
    return 0;
  }
  free(request);
  free(response);
  return 0;
}
/* --- sproutd self-init: auto-set SPROUT_ANALYSIS_SERVICE_CMD if unset --- */
long long sproutd_self_init(void) {
  const char* existing = getenv("SPROUT_ANALYSIS_SERVICE_CMD");
  if (existing != NULL && existing[0] != '\0') return 0;
  char own_exe[PATH_MAX];
#ifdef __APPLE__
  uint32_t sz = (uint32_t)PATH_MAX;
  if (_NSGetExecutablePath(own_exe, &sz) != 0) return 0;
  { char* resolved = realpath(own_exe, NULL);
    if (resolved == NULL) return 0;
    strncpy(own_exe, resolved, PATH_MAX - 1);
    own_exe[PATH_MAX - 1] = '\0';
    free(resolved); }
#else
  ssize_t len = readlink("/proc/self/exe", own_exe, PATH_MAX - 1);
  if (len < 0) return 0;
  own_exe[len] = '\0';
#endif
  const char* stdlib_root = getenv("SPROUT_STDLIB_ROOT");
  char inferred[PATH_MAX];
  if (stdlib_root == NULL || stdlib_root[0] == '\0') {
    char dir[PATH_MAX];
    strncpy(dir, own_exe, PATH_MAX - 1);
    dir[PATH_MAX - 1] = '\0';
    char* slash = strrchr(dir, '/');
    if (slash) *slash = '\0';
    snprintf(inferred, sizeof(inferred), "%s/../stdlib", dir);
    stdlib_root = inferred;
  }
  char cmd[PATH_MAX * 2 + 36];
  snprintf(cmd, sizeof(cmd), "'%s' --analysis-service '%s'", own_exe, stdlib_root);
  setenv("SPROUT_ANALYSIS_SERVICE_CMD", cmd, 0);
#ifdef __APPLE__
  setenv("SPROUT_DARWIN_FRAMEWORKS", "1", 0);
#endif
  return 0;
}

/* Like sproutd_self_init but uses an explicit stdlib_root instead of
 * inferring from the binary location or SPROUT_STDLIB_ROOT.  Required so
 * that `sproutd --lsp <stdlib_root>` threads the CLI argument into the
 * analysis-service command rather than silently using a different root.  */
long long sproutd_init_with_root(long long root_handle) {
  const char* stdlib_root = (const char*)(uintptr_t)root_handle;
  const char* existing = getenv("SPROUT_ANALYSIS_SERVICE_CMD");
  if (existing != NULL && existing[0] != '\0') return 0;
  char own_exe[PATH_MAX];
#ifdef __APPLE__
  uint32_t sz = (uint32_t)PATH_MAX;
  if (_NSGetExecutablePath(own_exe, &sz) != 0) return 0;
  { char* resolved = realpath(own_exe, NULL);
    if (resolved == NULL) return 0;
    strncpy(own_exe, resolved, PATH_MAX - 1);
    own_exe[PATH_MAX - 1] = '\0';
    free(resolved); }
#else
  ssize_t len = readlink("/proc/self/exe", own_exe, PATH_MAX - 1);
  if (len < 0) return 0;
  own_exe[len] = '\0';
#endif
  char cmd[PATH_MAX * 2 + 36];
  snprintf(cmd, sizeof(cmd), "'%s' --analysis-service '%s'", own_exe, stdlib_root);
  setenv("SPROUT_ANALYSIS_SERVICE_CMD", cmd, 0);
#ifdef __APPLE__
  setenv("SPROUT_DARWIN_FRAMEWORKS", "1", 0);
#endif
  return 0;
}

long long analysis_eval_expr_in_source(const char* module_source, const char* expr) {
  return sprout_analysis_vec_string_result("eval_expr_in_source", module_source, expr);
}
long long repl_eval_expr_in_source(const char* module_source, const char* expr) {
  return analysis_eval_expr_in_source(module_source, expr);
}
long long repl_check_source(const char* module_source) {
  return sprout_analysis_check_source_result("check_source", module_source);
}
long long analysis_check_source(const char* module_source) {
  return sprout_analysis_check_source_result("check_source", module_source);
}
long long repl_declared_names_in_source(const char* module_source) {
  return sprout_analysis_string_array_result("declared_names_in_source", module_source);
}
long long analysis_declared_names_in_source(const char* module_source) {
  return sprout_analysis_string_array_result("declared_names_in_source", module_source);
}
long long repl_exported_names_in_source(const char* module_source) {
  return sprout_analysis_string_array_result("exported_names_in_source", module_source);
}
long long analysis_exported_names_in_source(const char* module_source) {
  return sprout_analysis_string_array_result("exported_names_in_source", module_source);
}
long long repl_symbol_inventory_in_source(const char* module_source) {
  return sprout_analysis_inventory_result("symbol_inventory_in_source", module_source);
}
long long analysis_symbol_inventory_in_source(const char* module_source) {
  return sprout_analysis_inventory_result("symbol_inventory_in_source", module_source);
}
long long analysis_symbol_locations_in_source(const char* module_source) {
  return sprout_analysis_symbol_locations_result("symbol_locations_in_source", module_source);
}
long long repl_diagnostics_in_source(const char* module_source) {
  return sprout_analysis_diagnostics_result("diagnostics_in_source", module_source);
}
long long analysis_diagnostics_in_source(const char* module_source) {
  return sprout_analysis_diagnostics_result("diagnostics_in_source", module_source);
}
long long repl_type_of(const char* source) {
  (void)source;
  tcp_fail("repl_type_of: not supported in native backend");
  return 0;
}
long long repl_type_of_in_source(const char* module_source, const char* expr) {
  return sprout_analysis_type_result("type_of_in_source", module_source, expr);
}
long long analysis_type_of_in_source(const char* module_source, const char* expr) {
  return sprout_analysis_type_result("type_of_in_source", module_source, expr);
}
long long repl_instances(const char* source) {
  (void)source;
  tcp_fail("repl_instances: not supported in native backend");
  return 0;
}
long long repl_instances_in_source(const char* module_source, const char* type_expr_source) {
  return sprout_analysis_instances_result("instances_in_source", module_source, type_expr_source);
}
long long analysis_instances_in_source(const char* module_source, const char* type_expr_source) {
  return sprout_analysis_instances_result("instances_in_source", module_source, type_expr_source);
}
long long repl_complete(const char* source) {
  (void)source;
  tcp_fail("repl_complete: not supported in native backend");
  return 0;
}
long long analysis_complete_in_state(const char* line_buffer, const void* imports_handle, const void* declarations_handle) {
  return sprout_analysis_completion_result(line_buffer, imports_handle, declarations_handle);
}
long long repl_complete_in_state(const char* line_buffer, const void* imports_handle, const void* declarations_handle) {
  return analysis_complete_in_state(line_buffer, imports_handle, declarations_handle);
}
long long repl_reset_session(void) {
  return 0;
}
long long read_int_lines(const char* path) {
  if (path == NULL) tcp_fail("read_int_lines: null path");
  FILE* f = fopen(path, "r");
  if (f == NULL) tcp_fail("read_int_lines: cannot open file");
  VectorVal* v = sprout_alloc_vector_val("read_int_lines: out of memory");
  SPROUT_HANDLE(h_v, (long long)(uintptr_t)v);
  v->len = 0;
  v->cap = 0;
  v->data = NULL;

  char buf[4096];
  while (fgets(buf, sizeof(buf), f) != NULL) {
    size_t n = strlen(buf);
    while (n > 0 && (buf[n - 1] == '\n' || buf[n - 1] == '\r')) {
      buf[n - 1] = '\0';
      n--;
    }
    if (n == 0) continue;
    char* end = NULL;
    long long value = strtoll(buf, &end, 10);
    if (end == buf || *end != '\0') tcp_fail("read_int_lines: invalid integer line");
    if (v->len == v->cap) {
      long long new_cap = v->cap == 0 ? 8 : (v->cap * 2);
      long long* new_data = sprout_realloc_vector_data(v->data, (size_t)new_cap, "read_int_lines: out of memory");
      v->data = new_data;
      v->cap = new_cap;
    }
    v->data[v->len] = value;
    v->len++;
  }
  fclose(f);
  return sprout_handle_get(h_v);
}
long long sprout_register_ctor(long long tag, const char* name, long long arity, const char* field_kinds) {
  if (g_ctor_meta_len >= (long long)(sizeof(g_ctor_meta) / sizeof(g_ctor_meta[0]))) {
    tcp_fail("sprout_register_ctor: constructor metadata table full");
  }
  g_ctor_meta[g_ctor_meta_len].tag = tag;
  g_ctor_meta[g_ctor_meta_len].name = name;
  g_ctor_meta[g_ctor_meta_len].arity = arity;
  g_ctor_meta[g_ctor_meta_len].field_kinds = field_kinds;
  g_ctor_meta_len++;
  return 0;
}

/* -------------------------------------------------------------------------
 * Milestone C: debugger value-inspection helpers
 *
 * Call these from lldb at a breakpoint to inspect Sprout values:
 *
 *   (lldb) call (void)sprout_debug_int($x0)   -- print an Int/Bool value
 *   (lldb) call (void)sprout_debug_adt($x0)   -- print an ADT value
 *
 * sprout_debug_adt recurses up to depth 4 using field_kinds to decode fields.
 * -------------------------------------------------------------------------*/

static void sprout_debug_adt_rec(long long val, int depth);

void sprout_debug_int(long long val) {
  fprintf(stderr, "<Int: %lld>\n", val);
}

static void sprout_debug_field(long long fval, char kind, int depth) {
  switch (kind) {
    case 'i': fprintf(stderr, "%lld", fval); break;
    case 'b': fprintf(stderr, "%s", fval ? "True" : "False"); break;
    case 's': {
      /* Strings are stored as i64 raw pointers to a C string (GC Option C). */
      const char* s = (const char*)(uintptr_t)fval;
      if (s == NULL) { fprintf(stderr, "<null-string>"); break; }
      fprintf(stderr, "\"%.80s%s\"", s, (strlen(s) > 80 ? "..." : ""));
      break;
    }
    case 'p': case '_':
      sprout_debug_adt_rec(fval, depth + 1);
      break;
    default:
      fprintf(stderr, "0x%llx", (unsigned long long)fval);
  }
}

static void sprout_debug_adt_rec(long long val, int depth) {
  if (val == 0) { fprintf(stderr, "null"); return; }
  if (depth > 4) { fprintf(stderr, "..."); return; }
  long long tag = sprout_tag(val);
  CtorMeta* meta = find_ctor(tag);
  if (meta == NULL) {
    fprintf(stderr, "<tag:%lld>", tag);
    return;
  }
  fprintf(stderr, "%s", meta->name);
  if (meta->arity == 0) return;
  fprintf(stderr, "(");
  const char* fk = (meta->field_kinds != NULL) ? meta->field_kinds : "";
  /* Read only the fields this object actually has; reading beyond the ctor
     arity is OOB on exact-size objects. */
  for (long long i = 0; i < meta->arity && i < 9; i++) {
    if (i > 0) fprintf(stderr, ", ");
    char kind = (fk[i] != '\0') ? fk[i] : '_';
    sprout_debug_field(sprout_field(val, i), kind, depth);
  }
  fprintf(stderr, ")");
}

void sprout_debug_adt(long long val) {
  sprout_debug_adt_rec(val, 0);
  fprintf(stderr, "\n");
}

/* Helper: lazily allocate or return the cached nullary-ctor singleton. */
static long long get_or_make_singleton(void** slot, long long tag) {
  if (*slot == NULL) {
    void* obj = sprout_alloc_obj_raw(0, "sprout_make0: out of memory");
    sprout_obj_write_tag(obj, tag, 0);
    *slot = obj;
  }
  return box_ptr(*slot);
}

long long sprout_make0(long long tag) {
  CtorMeta* meta = find_ctor(tag);
  if (meta != NULL) {
    /* Singleton-eligible nullary ctors.  Each name-match avoids one
     * allocation per construction site; the IRType cluster (IRTHeap,
     * IRTScalar, IRTUnknown) is constructed *frequently* during IR-codegen
     * (every IRFunction param, every IRLoadEnvSlot, every IRGetField). */
    const char* name = meta->name;
    /* meta->name carries the fully-qualified ctor name; the find here uses
     * the bare suffix after the last '.' (sprout_register_ctor records the
     * source-form name).  For "Nothing" the name is bare; IRType ctors are
     * qualified under stdlib.compiler.sprout_ir. */
    if (strcmp(name, "Nothing") == 0)
      return get_or_make_singleton(&g_nothing_singleton, tag);
    if (strcmp(name, "stdlib.compiler.sprout_ir.IRTHeap") == 0)
      return get_or_make_singleton(&g_irtheap_singleton, tag);
    if (strcmp(name, "stdlib.compiler.sprout_ir.IRTScalar") == 0)
      return get_or_make_singleton(&g_irtscalar_singleton, tag);
    if (strcmp(name, "stdlib.compiler.sprout_ir.IRTUnknown") == 0)
      return get_or_make_singleton(&g_irtunknown_singleton, tag);
  }
  return sprout_make_registered_obj(0, tag, 0, 0, 0, "sprout_make0: out of memory");
}
long long sprout_make1(long long tag, long long a0) {
  return sprout_make_registered_obj(1, tag, a0, 0, 0, "sprout_make1: out of memory");
}
/* L0.9 channels: build a `stdlib.chan.Recv a` value on the scheduler's behalf. The scheduler TU
 * cannot see the static ctor-name lookup or the GC temp-root macros, so `__chan_recv` calls these.
 * `Got v` roots `v` across the boxing allocation (which may trigger a collection). Qualified ctor
 * names are collision-safe — a bare `Got`/`Closed` could be shadowed by another module's ctor. */
long long sprout_chan_make_got(long long v) {
  long long rooted = v;
  SPROUT_GC_PUSH_I64_LOCAL(rooted);
  long long obj = sprout_make1(find_ctor_tag_by_name("stdlib.chan.Got"), rooted);
  SPROUT_GC_POP_LOCALS(1);
  return obj;
}
long long sprout_chan_make_closed(void) {
  return sprout_make0(find_ctor_tag_by_name("stdlib.chan.Closed"));
}
/* L0.11 select: build `stdlib.chan.Selected index recv` for __chan_select. `recv` is an already-
 * boxed `Recv a` (from make_got/make_closed); root it across the make2 allocation (index is a
 * scalar). Field order matches `Selected Int (Recv a)`: field 0 = index, field 1 = recv. */
long long sprout_chan_make_selected(long long index, long long recv_boxed) {
  long long rooted = recv_boxed;
  SPROUT_GC_PUSH_I64_LOCAL(rooted);
  long long obj = sprout_make2(find_ctor_tag_by_name("stdlib.chan.Selected"), index, rooted);
  SPROUT_GC_POP_LOCALS(1);
  return obj;
}
long long sprout_make2(long long tag, long long a0, long long a1) {
  return sprout_make_registered_obj(2, tag, a0, a1, 0, "sprout_make2: out of memory");
}
long long sprout_rebox2(long long tag, long long f0) {
  CtorMeta* m = find_ctor(tag);
  if (m != NULL && m->arity == 0) return sprout_make0(tag);
  return sprout_make1(tag, f0);
}
long long sprout_rebox3(long long tag, long long f0, long long f1) {
  CtorMeta* m = find_ctor(tag);
  if (m != NULL && m->arity == 0) return sprout_make0(tag);
  if (m != NULL && m->arity == 1) return sprout_make1(tag, f0);
  return sprout_make2(tag, f0, f1);
}
long long sprout_make3(long long tag, long long a0, long long a1, long long a2) {
  return sprout_make_registered_obj(3, tag, a0, a1, a2, "sprout_make3: out of memory");
}
long long sprout_tag(long long h) {
  if (h == 0) {
    fprintf(stderr, "[sprout] sprout_tag: null pointer");
    if (g_sprout_current_fn != NULL)
      fprintf(stderr, " (in: %s)", g_sprout_current_fn);
    fprintf(stderr, "\n");
    fflush(stderr);
    abort();
  }
  void* payload = (void*)(uintptr_t)h;
  uint64_t hdr = sprout_hdr_of(payload);
  /* Cheap kind check first: the poison kind (0xFF) is virtually never present,
     so this short-circuits before the lineage-flag check on this hot path. */
  if (sprout_hdr_kind(hdr) == (SproutHeapKind)SPROUT_GC_POISON && sprout_gc_lineage_on()) {
    fprintf(stderr,
            "\n=== USE-AFTER-FREE: sprout_tag read poisoned ptr 0x%llx ===\n",
            (unsigned long long)h);
    if (g_sprout_current_fn != NULL)
      fprintf(stderr, "  (reading fn: %s)\n", g_sprout_current_fn);
    /* Backtrace was stored in payload[0]=frames, payload[1]=count at free time. */
    void** frames = (void**)(uintptr_t)((long long*)payload)[0];
    int n = (int)((long long*)payload)[1];
    if (frames != NULL && n > 0) {
      fprintf(stderr,
              "  free backtrace (collection that swept the victim; the alloc\n"
              "  frame above the GC chain is the unrooted-live-across site):\n");
      fflush(stderr);
      backtrace_symbols_fd(frames, n, fileno(stderr));
    } else {
      fprintf(stderr, "  (no free backtrace recorded)\n");
    }
    abort();
  }
  /* Tag is in the upper bits of aux: aux = (tag << 4) | arity. */
  return (long long)(sprout_hdr_aux(hdr) >> 4);
}
long long sprout_field(long long h, long long idx) {
  return ((long long*)(uintptr_t)h)[idx];
}
long long sprout_make4(long long tag, long long a0, long long a1, long long a2, long long a3) {
  void* obj = sprout_alloc_obj_raw(4, "sprout_make4: out of memory");
  sprout_obj_write_tag(obj, tag, 4);
  ((long long*)obj)[0] = a0; ((long long*)obj)[1] = a1;
  ((long long*)obj)[2] = a2; ((long long*)obj)[3] = a3;
  return box_ptr(obj);
}
long long sprout_make5(long long tag, long long a0, long long a1, long long a2, long long a3, long long a4) {
  void* obj = sprout_alloc_obj_raw(5, "sprout_make5: out of memory");
  sprout_obj_write_tag(obj, tag, 5);
  ((long long*)obj)[0] = a0; ((long long*)obj)[1] = a1; ((long long*)obj)[2] = a2;
  ((long long*)obj)[3] = a3; ((long long*)obj)[4] = a4;
  return box_ptr(obj);
}
long long sprout_make6(long long tag, long long a0, long long a1, long long a2, long long a3, long long a4, long long a5) {
  void* obj = sprout_alloc_obj_raw(6, "sprout_make6: out of memory");
  sprout_obj_write_tag(obj, tag, 6);
  ((long long*)obj)[0] = a0; ((long long*)obj)[1] = a1; ((long long*)obj)[2] = a2;
  ((long long*)obj)[3] = a3; ((long long*)obj)[4] = a4; ((long long*)obj)[5] = a5;
  return box_ptr(obj);
}
long long sprout_make7(long long tag, long long a0, long long a1, long long a2, long long a3, long long a4, long long a5, long long a6) {
  void* obj = sprout_alloc_obj_raw(7, "sprout_make7: out of memory");
  sprout_obj_write_tag(obj, tag, 7);
  ((long long*)obj)[0] = a0; ((long long*)obj)[1] = a1; ((long long*)obj)[2] = a2;
  ((long long*)obj)[3] = a3; ((long long*)obj)[4] = a4; ((long long*)obj)[5] = a5;
  ((long long*)obj)[6] = a6;
  return box_ptr(obj);
}
long long sprout_make8(long long tag, long long a0, long long a1, long long a2, long long a3, long long a4, long long a5, long long a6, long long a7) {
  void* obj = sprout_alloc_obj_raw(8, "sprout_make8: out of memory");
  sprout_obj_write_tag(obj, tag, 8);
  ((long long*)obj)[0] = a0; ((long long*)obj)[1] = a1; ((long long*)obj)[2] = a2;
  ((long long*)obj)[3] = a3; ((long long*)obj)[4] = a4; ((long long*)obj)[5] = a5;
  ((long long*)obj)[6] = a6; ((long long*)obj)[7] = a7;
  return box_ptr(obj);
}
long long sprout_make9(long long tag, long long a0, long long a1, long long a2, long long a3, long long a4, long long a5, long long a6, long long a7, long long a8) {
  void* obj = sprout_alloc_obj_raw(9, "sprout_make9: out of memory");
  sprout_obj_write_tag(obj, tag, 9);
  ((long long*)obj)[0] = a0; ((long long*)obj)[1] = a1; ((long long*)obj)[2] = a2;
  ((long long*)obj)[3] = a3; ((long long*)obj)[4] = a4; ((long long*)obj)[5] = a5;
  ((long long*)obj)[6] = a6; ((long long*)obj)[7] = a7; ((long long*)obj)[8] = a8;
  return box_ptr(obj);
}

__attribute__((noreturn)) void sprout_abort_match(void) {
  fprintf(stderr, "runtime error: non-exhaustive match\n");
  exit(1);
}
__attribute__((noreturn)) static void tcp_fail(const char* msg) {
  const char* colon = strchr(msg, ':');
  if (colon != NULL) {
    size_t name_len = (size_t)(colon - msg);
    const char* detail = colon + 1;
    while (*detail == ' ') detail++;
    fprintf(stderr, "runtime error: builtin `%.*s`: %s\n", (int)name_len, msg, detail);
  } else {
    fprintf(stderr, "runtime error: %s\n", msg);
  }
  exit(1);
}

/* Non-static panic path for the scheduler TU (sprout_scheduler.h). */
__attribute__((noreturn)) void sprout_fail(const char* msg) { tcp_fail(msg); }

long long str_concat(long long left_i, long long right_i) {
  const char* left = (const char*)(uintptr_t)left_i;
  const char* right = (const char*)(uintptr_t)right_i;
  if (left == NULL || right == NULL) tcp_fail("str_concat: null input");
  size_t left_len = strlen(left);
  size_t right_len = strlen(right);
  SPROUT_HANDLE(h_left, left_i);
  SPROUT_HANDLE(h_right, right_i);
  sprout_gc_maybe_collect_threshold();
  const char* left_now = (const char*)(uintptr_t)sprout_handle_get(h_left);
  const char* right_now = (const char*)(uintptr_t)sprout_handle_get(h_right);
  size_t total_len = left_len + right_len;
  char* out = sprout_gc_alloc_cstr(total_len, "str_concat: out of memory");
  memcpy(out, left_now, left_len);
  memcpy(out + left_len, right_now, right_len);
  out[total_len] = '\0';  return (long long)(uintptr_t)out;
}

/* L0.11 select: step a Sprout `List Int` for the scheduler's __chan_select. Returns 1 and fills the
 * head/tail out-params on a Cons cell, 0 on Nil. The scheduler TU cannot see the static Nil/Cons tag
 * lookup, so it calls this (mirrors string_concat_many's walk). Returns int, not long long, so the
 * approved-builtins check does not mistake it for a Sprout builtin. */
int sprout_list_next(long long cur, long long* out_head, long long* out_tail) {
  long long nil_tag  = find_ctor_tag_by_name("Nil");
  long long cons_tag = find_ctor_tag_by_name("Cons");
  long long tag = sprout_tag(cur);
  if (tag == nil_tag) return 0;
  if (tag != cons_tag) tcp_fail("sprout_list_next: malformed list (not Cons or Nil)");
  *out_head = sprout_field(cur, 0);
  *out_tail = sprout_field(cur, 1);
  return 1;
}

/* string_concat_many: concatenate a List String into a single String. */
long long string_concat_many(long long list_handle) {
  long long nil_tag  = find_ctor_tag_by_name("Nil");
  long long cons_tag = find_ctor_tag_by_name("Cons");
  /* First pass: compute total byte length. */
  size_t total = 0;
  long long cur = list_handle;
  while (sprout_tag(cur) != nil_tag) {
    if (sprout_tag(cur) != cons_tag) tcp_fail("string_concat_many: malformed list");
    const char* s = (const char*)(uintptr_t)sprout_field(cur, 0);
    if (s == NULL) tcp_fail("string_concat_many: null string element");
    total += strlen(s);
    cur = sprout_field(cur, 1);
  }
  SPROUT_HANDLE(h_list, list_handle);
  sprout_gc_maybe_collect_threshold();
  char* out = sprout_gc_alloc_cstr(total, "string_concat_many: out of memory");
  /* Second pass: copy bytes. */
  size_t pos = 0;
  cur = sprout_handle_get(h_list);
  while (sprout_tag(cur) != nil_tag) {
    const char* elem = (const char*)(uintptr_t)sprout_field(cur, 0);
    size_t slen = strlen(elem);
    memcpy(out + pos, elem, slen);
    pos += slen;
    cur = sprout_field(cur, 1);
  }
  out[total] = '\0';  return (long long)(uintptr_t)out;
}

/* string_join_newlines: join a List String appending '\n' after each element.
 * Two-pass: compute total length, malloc once, copy. No GC inside traversal. */
long long string_join_newlines(long long list_handle) {
  long long nil_tag  = find_ctor_tag_by_name("Nil");
  long long cons_tag = find_ctor_tag_by_name("Cons");
  size_t total = 0;
  long long cur = list_handle;
  while (sprout_tag(cur) != nil_tag) {
    if (sprout_tag(cur) != cons_tag) tcp_fail("string_join_newlines: malformed list");
    const char* s = (const char*)(uintptr_t)sprout_field(cur, 0);
    if (s == NULL) tcp_fail("string_join_newlines: null string element");
    total += strlen(s) + 1;
    cur = sprout_field(cur, 1);
  }
  SPROUT_HANDLE(h_list, list_handle);
  sprout_gc_maybe_collect_threshold();
  char* out = sprout_gc_alloc_cstr(total, "string_join_newlines: out of memory");
  size_t pos = 0;
  cur = sprout_handle_get(h_list);
  while (sprout_tag(cur) != nil_tag) {
    const char* elem = (const char*)(uintptr_t)sprout_field(cur, 0);
    size_t slen = strlen(elem);
    memcpy(out + pos, elem, slen);
    pos += slen;
    out[pos++] = '\n';
    cur = sprout_field(cur, 1);
  }
  out[pos] = '\0';  return (long long)(uintptr_t)out;
}

static size_t sprout_utf8_char_width(unsigned char lead) {
  if ((lead & 0x80) == 0) return 1;
  if ((lead & 0xE0) == 0xC0) return 2;
  if ((lead & 0xF0) == 0xE0) return 3;
  if ((lead & 0xF8) == 0xF0) return 4;
  tcp_fail("str_utf8: invalid UTF-8 lead byte");
  return 1;
}

/* Validated forward step: the byte width of the UTF-8 char at s[i], having
 * verified every continuation byte s[i+1 .. i+width-1] is present (before the
 * NUL) and matches the 0b10xxxxxx pattern. Panics via tcp_fail on a truncated
 * or malformed sequence.
 *
 * Safety: the scan stops at the first NUL — always inside the allocation, since
 * Sprout Strings are NUL-terminated — so it never reads past the terminator,
 * even on a malformed String. Every walker below routes through this, making
 * them the last line of defense until ingestion validation lands (review W2/R2).
 * Callers pass i at a codepoint boundary with s[i] != '\0'. */
static size_t sprout_utf8_step(const char* s, size_t i) {
  size_t width = sprout_utf8_char_width((unsigned char)s[i]);
  for (size_t k = 1; k < width; k++) {
    /* A NUL (0x00) fails this test too, so a truncated tail is rejected here
     * before the width-byte advance could overshoot the terminator. */
    if (((unsigned char)s[i + k] & 0xC0) != 0x80)
      tcp_fail("str_utf8: truncated or malformed UTF-8 sequence");
  }
  return width;
}

/* Decode the Unicode codepoint of the `width`-byte UTF-8 sequence at s[pos].
 * Mirrors stdlib.compiler.source.decode_codepoint_at. `width` MUST come from
 * sprout_utf8_step (not the bare sprout_utf8_char_width), which guarantees all
 * continuation bytes are present — so the u[1..width-1] reads below are in
 * bounds. Returns the codepoint as an i64 — the runtime representation of a
 * Sprout Char. */
static long long sprout_utf8_decode_at(const char* s, size_t pos, size_t width) {
  const unsigned char* u = (const unsigned char*)(s + pos);
  switch (width) {
    case 1:  return (long long)u[0];
    case 2:  return (long long)(((u[0] & 0x1FU) << 6) | (u[1] & 0x3FU));
    case 3:  return (long long)(((u[0] & 0x0FU) << 12) | ((u[1] & 0x3FU) << 6) | (u[2] & 0x3FU));
    default: return (long long)(((u[0] & 0x07U) << 18) | ((u[1] & 0x3FU) << 12) | ((u[2] & 0x3FU) << 6) | (u[3] & 0x3FU));
  }
}

static size_t sprout_utf8_codepoint_count(const char* s) {
  size_t count = 0;
  size_t i = 0;
  while (s[i] != '\0') {
    i += sprout_utf8_step(s, i);
    count++;
  }
  return count;
}

static size_t sprout_utf8_byte_offset(const char* s, size_t codepoint_offset) {
  size_t i = 0;
  size_t count = 0;
  while (s[i] != '\0' && count < codepoint_offset) {
    i += sprout_utf8_step(s, i);
    count++;
  }
  return i;
}

long long str_len(long long s_val) {
  const char* s = (const char*)s_val;
  if (s == NULL) tcp_fail("str_len: null input");
  return (long long)sprout_utf8_codepoint_count(s);
}

_Bool str_eq(const char* left, const char* right) {
  if (left == NULL || right == NULL) tcp_fail("str_eq: null input");
  /* Fast-reject: if both are managed CSTRs with differing byte lengths they
   * cannot be equal (strcmp is byte-wise, so length equality is necessary). */
  void* lhdr = sprout_heap_lookup((void*)left);
  if (lhdr != NULL) {
    void* rhdr = sprout_heap_lookup((void*)right);
    if (rhdr != NULL) {
      uint64_t lh, rh;
      memcpy(&lh, lhdr, 8);
      memcpy(&rh, rhdr, 8);
      if ((lh & 0xFF) == SPROUT_HEAP_CSTR && (rh & 0xFF) == SPROUT_HEAP_CSTR &&
          (lh >> 14) != (rh >> 14)) return 0;
    }
  }
  return strcmp(left, right) == 0;
}

long long str_slice(long long s_i, long long start, long long length) {
  const char* s = (const char*)(uintptr_t)s_i;
  if (s == NULL) tcp_fail("str_slice: null input");
  if (start < 0 || length < 0) tcp_fail("str_slice: start/length must be >= 0");
  SPROUT_HANDLE(h_s, s_i);
  size_t total = sprout_utf8_codepoint_count(s);
  size_t start_byte = 0;
  size_t take = 0;
  if ((size_t)start < total) {
    start_byte = sprout_utf8_byte_offset(s, (size_t)start);
    size_t end_codepoint = (size_t)start + (size_t)length;
    if (end_codepoint > total) end_codepoint = total;
    size_t end_byte = sprout_utf8_byte_offset(s, end_codepoint);
    take = end_byte - start_byte;
  }
  sprout_gc_maybe_collect_threshold();
  const char* slice_now = (const char*)(uintptr_t)sprout_handle_get(h_s);
  char* out = sprout_gc_alloc_cstr(take, "str_slice: out of memory");
  if (take > 0) memcpy(out, slice_now + start_byte, take);
  out[take] = '\0';  return (long long)(uintptr_t)out;
}

/* str_slice_bytes: O(strlen + L) byte-indexed substring.
 *
 * Why this exists alongside str_slice: str_slice converts codepoint indices
 * to byte offsets via two O(N) walks (sprout_utf8_byte_offset, called twice),
 * plus an O(N) codepoint count for bounds. Hot loops that already track byte
 * positions (e.g. codegen.dbg_count_header_lines, which uses str_find for
 * ASCII delimiters) waste those walks; this variant skips them.
 *
 * Safety: the caller MUST pass byte_start and byte_start+byte_len at UTF-8
 * codepoint boundaries. We enforce this with two cheap O(1) checks: a
 * continuation byte (0b10xxxxxx) at either endpoint indicates a mid-codepoint
 * split and aborts. This matches Rust's &str[byte_range] panic semantics,
 * trading a clear runtime error for silent UTF-8 corruption.
 *
 * Out-of-range bounds (beyond strlen) are clamped, mirroring str_slice's
 * clamping behaviour for ergonomics.
 */
long long str_slice_bytes(long long s_i, long long byte_start, long long byte_len) {
  const char* s = (const char*)(uintptr_t)s_i;
  if (s == NULL) tcp_fail("str_slice_bytes: null input");
  if (byte_start < 0 || byte_len < 0) tcp_fail("str_slice_bytes: byte_start/byte_len must be >= 0");
  SPROUT_HANDLE(h_s, s_i);
  size_t total = strlen(s);
  size_t bs = (size_t)byte_start;
  size_t bl = (size_t)byte_len;
  if (bs > total) bs = total;
  if (bs + bl > total) bl = total - bs;
  /* Codepoint-boundary checks: a UTF-8 continuation byte has the bit pattern
   * 10xxxxxx, i.e. (byte & 0xC0) == 0x80. The start and end of any valid
   * codepoint sequence is never a continuation byte. */
  if (bs > 0 && bs < total && ((unsigned char)s[bs] & 0xC0) == 0x80)
    tcp_fail("str_slice_bytes: byte_start splits a UTF-8 codepoint");
  if (bs + bl < total && ((unsigned char)s[bs + bl] & 0xC0) == 0x80)
    tcp_fail("str_slice_bytes: byte_start+byte_len splits a UTF-8 codepoint");
  sprout_gc_maybe_collect_threshold();
  const char* slice_now = (const char*)(uintptr_t)sprout_handle_get(h_s);
  char* out = sprout_gc_alloc_cstr(bl, "str_slice_bytes: out of memory");
  if (bl > 0) memcpy(out, slice_now + bs, bl);
  out[bl] = '\0';  return (long long)(uintptr_t)out;
}

long long str_char_at(long long s_val, long long index) {
  const char* s = (const char*)s_val;
  if (s == NULL) tcp_fail("str_char_at: null input");
  if (index < 0) return sprout_make0(find_ctor_tag_by_name("Nothing"));
  /* Scan forward to the index-th UTF-8 codepoint.  This avoids both the
   * separate sprout_utf8_codepoint_count() pass (O(N) just for bounds) and
   * the str_slice() call (O(N) + malloc).  For ASCII the returned char string
   * comes from a static table so no allocation occurs at all. */
  size_t byte_pos = 0;
  long long cp_idx = 0;
  while (s[byte_pos] != '\0') {
    size_t width = sprout_utf8_step(s, byte_pos);
    if (cp_idx == index) {
      /* A Sprout Char is its Unicode codepoint (immediate i64), so return the
       * decoded codepoint directly — no allocation. */
      return sprout_make1(find_ctor_tag_by_name("Just"),
                          sprout_utf8_decode_at(s, byte_pos, width));
    }
    byte_pos += width;
    cp_idx++;
  }
  return sprout_make0(find_ctor_tag_by_name("Nothing"));
}

/* str_split_lines: O(N) line splitting, replacing the O(N^2) split_lines_at
 * loop that called str_char_at + str_slice for each codepoint in the source.
 * Returns a Sprout List String; each line excludes the trailing newline. */
long long str_split_lines(long long s_val) {
  const char* s = (const char*)s_val;
  if (s == NULL) tcp_fail("str_split_lines: null input");
  size_t total = strlen(s);

  /* One forward pass to collect (start_byte, end_byte) spans. */
  typedef struct { size_t start; size_t end; } Span;
  Span* spans = NULL;
  size_t nspans = 0, cap = 0;
  size_t line_start = 0;
  for (size_t i = 0; i <= total; i++) {
    if (s[i] == '\n' || s[i] == '\0') {
      if (nspans >= cap) {
        cap = (cap < 64) ? 64 : cap * 2;
        Span* tmp = (Span*)realloc(spans, cap * sizeof(Span));
        if (!tmp) { free(spans); tcp_fail("str_split_lines: out of memory"); }
        spans = tmp;
      }
      spans[nspans++] = (Span){ line_start, i };
      line_start = i + 1;
    }
  }

  /* Build Cons list from back to front (last span prepended first, head at front). */
  long long cons_tag = find_ctor_tag_by_name("Cons");
  long long nil_tag  = find_ctor_tag_by_name("Nil");
  SPROUT_HANDLE(h_s, (long long)(uintptr_t)s);
  SPROUT_HANDLE(h_list, sprout_make0(nil_tag));
  for (size_t k = nspans; k-- > 0;) {
    size_t slen = spans[k].end - spans[k].start;
    char* line = sprout_gc_alloc_cstr(slen, "str_split_lines: out of memory");
    memcpy(line, s + spans[k].start, slen);
    line[slen] = '\0';    {
      SPROUT_HANDLE(h_line, (long long)(uintptr_t)line);
      SPROUT_HANDLE_SET(h_list, sprout_make2(cons_tag, sprout_handle_get(h_line), sprout_handle_get(h_list)));
    }
  }

  free(spans);
  return sprout_handle_get(h_list);
}

/* split_words: split a string on ASCII whitespace (space, tab, \n, \r),
 * returning a Sprout List String.  Runs in O(N) with one forward pass. */
long long split_words(const char* s) {
  if (s == NULL) tcp_fail("split_words: null input");
  size_t total = strlen(s);

  typedef struct { size_t start; size_t end; } WSpan;
  WSpan* spans = NULL;
  size_t nspans = 0, cap = 0;
  size_t i = 0;
  while (i < total) {
    while (i < total && (s[i]==' '||s[i]=='\t'||s[i]=='\n'||s[i]=='\r')) i++;
    if (i >= total) break;
    size_t wstart = i;
    while (i < total && !(s[i]==' '||s[i]=='\t'||s[i]=='\n'||s[i]=='\r')) i++;
    if (nspans >= cap) {
      cap = (cap < 64) ? 64 : cap * 2;
      WSpan* tmp = (WSpan*)realloc(spans, cap * sizeof(WSpan));
      if (!tmp) { free(spans); tcp_fail("split_words: out of memory"); }
      spans = tmp;
    }
    spans[nspans++] = (WSpan){ wstart, i };
  }

  long long cons_tag = find_ctor_tag_by_name("Cons");
  long long nil_tag  = find_ctor_tag_by_name("Nil");
  SPROUT_HANDLE(h_list, sprout_make0(nil_tag));
  for (size_t k = nspans; k-- > 0;) {
    size_t slen = spans[k].end - spans[k].start;
    char* word = sprout_gc_alloc_cstr(slen, "split_words: out of memory");
    memcpy(word, s + spans[k].start, slen);
    word[slen] = '\0';    {
      SPROUT_HANDLE(h_word, (long long)(uintptr_t)word);
      SPROUT_HANDLE_SET(h_list, sprout_make2(cons_tag, sprout_handle_get(h_word), sprout_handle_get(h_list)));
    }
  }
  free(spans);
  return sprout_handle_get(h_list);
}

/* Cached tags for Maybe constructors — looked up once, reused on every
 * str_char_at_byte call (which is on the hot lexer path).  Eliminates
 * the O(n_ctors) linear scan in find_ctor_tag_by_name per character. */
static long long g_tag_just    = -1;
static long long g_tag_nothing = -1;

static long long cached_tag_just(void) {
  if (g_tag_just < 0) g_tag_just = find_ctor_tag_by_name("Just");
  return g_tag_just;
}
static long long cached_tag_nothing(void) {
  if (g_tag_nothing < 0) g_tag_nothing = find_ctor_tag_by_name("Nothing");
  return g_tag_nothing;
}

static long long bst_get(long long h, const char* key);
static BSTNode*  bst_nth_node(long long h, long long n);

/* ── CPR unboxed extern variants ─────────────────────────────────────────────
 * Each _unboxed variant mirrors its boxed counterpart but returns SproutUnboxed2
 * instead of a heap-allocated Just/Nothing.  The codegen emits calls to these
 * when a match immediately scrutinises the return value (direct-match CPR path).
 * GC safety: none of these call sprout_makeN, so no GC can trigger after the
 * last allocation returns.  The caller pushes extracted field0 as a temp root
 * in the Just arm before any further GC-triggering call (see emit_match_unboxed_adt).
 * NOTE: if a future moving GC is added, the returned i64 pointers must be
 * re-rooted via handles before any allocation; the non-moving invariant makes
 * this safe today. */

SproutUnboxed2 env_get_unboxed(const char* name) {
  if (name == NULL) tcp_fail("env_get_unboxed: null name");
  const char* value = getenv(name);
  if (value == NULL) return (SproutUnboxed2){ cached_tag_nothing(), 0 };
  return (SproutUnboxed2){ cached_tag_just(), (int64_t)(uintptr_t)value };
}

SproutUnboxed2 argv_get_unboxed(long long index) {
  if (index < 0 || g_sprout_argv == NULL || index >= (long long)(g_sprout_argc - 1))
    return (SproutUnboxed2){ cached_tag_nothing(), 0 };
  return (SproutUnboxed2){ cached_tag_just(), (int64_t)(uintptr_t)g_sprout_argv[index + 1] };
}

SproutUnboxed2 term_read_line_unboxed(void) {
  char* line = NULL;
  size_t cap = 0;
  ssize_t len = getline(&line, &cap, stdin);
  if (len < 0) {
    free(line);
    if (feof(stdin)) return (SproutUnboxed2){ cached_tag_nothing(), 0 };
    tcp_fail("term_read_line_unboxed: read error");
  }
  while (len > 0 && (line[len - 1] == '\n' || line[len - 1] == '\r')) {
    len -= 1;
    line[len] = '\0';
  }
  line = register_cstr(line);
  return (SproutUnboxed2){ cached_tag_just(), (int64_t)(uintptr_t)line };
}

SproutUnboxed2 str_char_at_unboxed(long long s_val, long long index) {
  const char* s = (const char*)s_val;
  if (s == NULL) tcp_fail("str_char_at_unboxed: null input");
  if (index < 0) return (SproutUnboxed2){ cached_tag_nothing(), 0 };
  size_t byte_pos = 0;
  long long cp_idx = 0;
  while (s[byte_pos] != '\0') {
    size_t width = sprout_utf8_step(s, byte_pos);
    if (cp_idx == index) {
      /* A Sprout Char is its Unicode codepoint (immediate i64) — return it. */
      return (SproutUnboxed2){ cached_tag_just(),
                               (int64_t)sprout_utf8_decode_at(s, byte_pos, width) };
    }
    byte_pos += width;
    cp_idx++;
  }
  return (SproutUnboxed2){ cached_tag_nothing(), 0 };
}

SproutUnboxed2 regex_find_range_unboxed(const char* pattern, const char* text) {
  if (pattern == NULL || text == NULL) tcp_fail("regex_find_range_unboxed: null input");
  regex_t compiled;
  char* error = NULL;
  if (!regex_compile_ere(pattern, &compiled, &error)) {
    regex_builtin_fail("regex_find_range_unboxed", error);
  }
  regmatch_t match;
  int status = regexec(&compiled, text, 1, &match, 0);
  regfree(&compiled);
  if (status == REG_NOMATCH) return (SproutUnboxed2){ cached_tag_nothing(), 0 };
  if (status != 0 || match.rm_so < 0 || match.rm_eo < 0)
    tcp_fail("regex_find_range_unboxed: regexec failed");
  IntRangeVal* range = sprout_alloc_range_val("regex_find_range_unboxed: out of memory");
  range->start = sprout_utf8_codepoint_prefix_count(text, (size_t)match.rm_so);
  range->end   = sprout_utf8_codepoint_prefix_count(text, (size_t)match.rm_eo);
  return (SproutUnboxed2){ cached_tag_just(), (int64_t)(uintptr_t)range };
}

SproutUnboxed2 vector_get_unboxed(long long vec, long long index) {
  VectorVal* v = (VectorVal*)(uintptr_t)vec;
  if (v == NULL) tcp_fail("vector_get_unboxed: null vector");
  if (index < 0 || index >= v->len) return (SproutUnboxed2){ cached_tag_nothing(), 0 };
  return (SproutUnboxed2){ cached_tag_just(), v->data[index] };
}

SproutUnboxed2 map_get_unboxed(long long map_h, long long key_val) {
  const char* key = (const char*)key_val;
  if (key == NULL) tcp_fail("map_get_unboxed: null key");
  long long found = bst_get(map_h, key);
  if (found == LLONG_MIN) return (SproutUnboxed2){ cached_tag_nothing(), 0 };
  return (SproutUnboxed2){ cached_tag_just(), found };
}

SproutUnboxed2 map_nth_key_unboxed(long long map_h, long long index) {
  BSTNode* node = bst_nth_node(map_h, index);
  if (node == NULL) return (SproutUnboxed2){ cached_tag_nothing(), 0 };
  return (SproutUnboxed2){ cached_tag_just(), (int64_t)(uintptr_t)node->key };
}

SproutUnboxed2 map_nth_value_unboxed(long long map_h, long long index) {
  BSTNode* node = bst_nth_node(map_h, index);
  if (node == NULL) return (SproutUnboxed2){ cached_tag_nothing(), 0 };
  return (SproutUnboxed2){ cached_tag_just(), node->value };
}

SproutUnboxed2 bytes_get_unboxed(long long bytes_h, long long index) {
  BytesVal* value = (BytesVal*)(uintptr_t)bytes_h;
  if (value == NULL) tcp_fail("bytes_get_unboxed: null bytes");
  if (index < 0 || (size_t)index >= value->len) return (SproutUnboxed2){ cached_tag_nothing(), 0 };
  return (SproutUnboxed2){ cached_tag_just(), (int64_t)value->data[index] };
}

/* ── end CPR unboxed extern variants ────────────────────────────────────────*/

/* str_char_at_byte / str_char_width_at_byte removed: replaced by the safe,
 * total decode_char_at over Bytes in stdlib.compiler.source (review F3). */

/* str_byte_len: byte length of the string (strlen).
 * O(1) fast path for managed CSTRs: header aux stores the byte count at
 * allocation time and is always in sync (HDRCHECK verifies this).
 * Falls back to strlen for static string literals not in the GC heap. */
long long str_byte_len(long long s_val) {
  const char* s = (const char*)s_val;
  if (s == NULL) tcp_fail("str_byte_len: null input");
  void* hdr = sprout_heap_lookup((void*)s);
  uint64_t h = 0;
  if (hdr != NULL) memcpy(&h, hdr, 8);
  if ((h & 0xFF) == SPROUT_HEAP_CSTR) {
    unsigned long long len = (unsigned long long)(h >> 14);
    if (sprout_gc_hdrcheck_on()) {
      size_t actual = strlen(s);
      if (len != (unsigned long long)actual) {
        fprintf(stderr, "[sprout] HDRCHECK: str_byte_len aux=%llu strlen=%zu\n", len, actual);
        abort();
      }
    }
    return (long long)len;
  }
  return (long long)strlen(s);
}

/* str_starts_with_at_byte: O(|prefix|) starts-with check from a byte offset.
 * Avoids the O(N) remaining-text allocation of match_string's old approach. */
_Bool str_starts_with_at_byte(long long s_val, long long byte_pos, long long prefix_val) {
  const char* s = (const char*)s_val;
  const char* prefix = (const char*)prefix_val;
  if (s == NULL || prefix == NULL) tcp_fail("str_starts_with_at_byte: null input");
  if (byte_pos < 0) return 0;
  size_t len = strlen(s);
  size_t pos = (size_t)byte_pos;
  if (pos > len) return 0;
  return strncmp(s + pos, prefix, strlen(prefix)) == 0;
}

long long str_find(long long haystack_val, long long needle_val) {
  const char* haystack = (const char*)haystack_val;
  const char* needle = (const char*)needle_val;
  if (haystack == NULL || needle == NULL) tcp_fail("str_find: null input");
  const char* pos = strstr(haystack, needle);
  if (pos == NULL) return -1;
  size_t prefix_len = (size_t)(pos - haystack);
  size_t count = 0;
  size_t i = 0;
  while (i < prefix_len) {
    i += sprout_utf8_step(haystack, i);
    count++;
  }
  return (long long)count;
}

_Bool str_starts_with(long long s_val, long long prefix_val) {
  const char* s = (const char*)s_val;
  const char* prefix = (const char*)prefix_val;
  if (s == NULL || prefix == NULL) tcp_fail("str_starts_with: null input");
  size_t prefix_len = strlen(prefix);
  return strncmp(s, prefix, prefix_len) == 0;
}

long long str_compare(long long left_val, long long right_val) {
  const char* left = (const char*)left_val;
  const char* right = (const char*)right_val;
  if (left == NULL || right == NULL) tcp_fail("str_compare: null input");
  int cmp = strcmp(left, right);
  if (cmp < 0) return -1;
  if (cmp > 0) return 1;
  return 0;
}

long long regex_validate(const char* pattern) {
  if (pattern == NULL) tcp_fail("regex_validate: null input");
  regex_t compiled;
  char* error = NULL;
  if (!regex_compile_ere(pattern, &compiled, &error)) {
    error = register_cstr(error);
    SPROUT_HANDLE(h_error, (long long)(uintptr_t)error);
    return sprout_make1(find_ctor_tag_by_name("Err"), sprout_handle_get(h_error));
  }
  regfree(&compiled);
  return sprout_make1(find_ctor_tag_by_name("Ok"), 0);
}

_Bool regex_is_match(const char* pattern, const char* text) {
  if (pattern == NULL || text == NULL) tcp_fail("regex_is_match: null input");
  regex_t compiled;
  char* error = NULL;
  if (!regex_compile_ere(pattern, &compiled, &error)) {
    regex_builtin_fail("regex_is_match", error);
  }
  regmatch_t match;
  int status = regexec(&compiled, text, 1, &match, 0);
  regfree(&compiled);
  return status == 0;
}

long long regex_find_range(const char* pattern, const char* text) {
  if (pattern == NULL || text == NULL) tcp_fail("regex_find_range: null input");
  regex_t compiled;
  char* error = NULL;
  if (!regex_compile_ere(pattern, &compiled, &error)) {
    regex_builtin_fail("regex_find_range", error);
  }
  regmatch_t match;
  int status = regexec(&compiled, text, 1, &match, 0);
  regfree(&compiled);
  if (status == REG_NOMATCH) {
    return sprout_make0(find_ctor_tag_by_name("Nothing"));
  }
  if (status != 0 || match.rm_so < 0 || match.rm_eo < 0) {
    tcp_fail("regex_find_range: regexec failed");
  }
  IntRangeVal* range = sprout_alloc_range_val("regex_find_range: out of memory");
  range->start = sprout_utf8_codepoint_prefix_count(text, (size_t)match.rm_so);
  range->end = sprout_utf8_codepoint_prefix_count(text, (size_t)match.rm_eo);
  SPROUT_HANDLE(h_range, (long long)(uintptr_t)range);
  return sprout_make1(find_ctor_tag_by_name("Just"), sprout_handle_get(h_range));
}

long long regex_replace_all_literal(long long pattern_i, long long replacement_i, long long text_i) {
  const char* pattern = (const char*)(uintptr_t)pattern_i;
  const char* replacement = (const char*)(uintptr_t)replacement_i;
  const char* text = (const char*)(uintptr_t)text_i;
  if (pattern == NULL || replacement == NULL || text == NULL) {
    tcp_fail("regex_replace_all_literal: null input");
  }
  SPROUT_HANDLE(h_pattern, pattern_i);
  SPROUT_HANDLE(h_replacement, replacement_i);
  SPROUT_HANDLE(h_text, text_i);
  sprout_gc_maybe_collect_threshold();
  const char* pattern_now = (const char*)(uintptr_t)sprout_handle_get(h_pattern);
  const char* replacement_now = (const char*)(uintptr_t)sprout_handle_get(h_replacement);
  const char* text_now = (const char*)(uintptr_t)sprout_handle_get(h_text);
  regex_t compiled;
  char* error = NULL;
  if (!regex_compile_ere(pattern_now, &compiled, &error)) {
    regex_builtin_fail("regex_replace_all_literal", error);
  }
  ByteBuf out;
  buf_init(&out);
  const char* cursor = text_now;
  regmatch_t match;
  while (regexec(&compiled, cursor, 1, &match, 0) == 0) {
    if (match.rm_so < 0 || match.rm_eo < 0) {
      regfree(&compiled);
      tcp_fail("regex_replace_all_literal: regexec failed");
    }
    size_t start = (size_t)match.rm_so;
    size_t end = (size_t)match.rm_eo;
    buf_append_bytes(&out, cursor, start);
    buf_append_cstr(&out, replacement_now);
    if (end == 0) {
      if (cursor[0] == '\0') break;
      size_t width = sprout_utf8_step(cursor, 0);
      buf_append_bytes(&out, cursor, width);
      cursor += width;
    } else {
      cursor += end;
    }
  }
  buf_append_cstr(&out, cursor);
  regfree(&compiled);
  char* result = sprout_gc_adopt_cstr(out.data, out.len, "regex_replace_all_literal: out of memory");  return (long long)(uintptr_t)result;
}

long long regex_escape(long long raw_i) {
  const char* raw = (const char*)(uintptr_t)raw_i;
  if (raw == NULL) tcp_fail("regex_escape: null input");
  SPROUT_HANDLE(h_raw, raw_i);
  sprout_gc_maybe_collect_threshold();
  const char* raw_now = (const char*)(uintptr_t)sprout_handle_get(h_raw);
  ByteBuf out;
  buf_init(&out);
  for (size_t i = 0; raw_now[i] != '\0'; i++) {
    if (strchr(".^$*+?()[]{}|\\-", raw_now[i]) != NULL) {
      buf_append_char(&out, '\\');
    }
    buf_append_char(&out, raw_now[i]);
  }
  char* escaped = out.data != NULL ? out.data : dup_cstr("");
  size_t escaped_len = out.data != NULL ? out.len : 0;
  char* result = sprout_gc_adopt_cstr(escaped, escaped_len, "regex_escape: out of memory");  return (long long)(uintptr_t)result;
}

/* A Sprout Char is a Unicode scalar value: 0 .. 0x10FFFF, excluding the UTF-16
 * surrogate range D800 .. DFFF. These are exactly the codepoints utf8_validate
 * accepts. Encoding an out-of-range value would mint an invalid-UTF-8 String
 * (or an invalid Char), so both the Int->String and Int->Char constructors
 * reject it with a clean panic (a Maybe-returning surface API is future work,
 * pending the ingestion-policy decision — review W2/D4). */
static void sprout_validate_codepoint(long long cp, const char* who) {
  if (cp < 0 || cp > 0x10FFFF || (cp >= 0xD800 && cp <= 0xDFFF)) tcp_fail(who);
}

long long char_to_str(long long codepoint) {
  sprout_validate_codepoint(codepoint, "char_to_str: codepoint out of Unicode range");
  unsigned int cp = (unsigned int)codepoint;
  char buf[5];
  size_t len;
  if (cp <= 0x7f) {
    buf[0] = (char)cp; len = 1;
  } else if (cp <= 0x7ff) {
    buf[0] = (char)(0xc0 | (cp >> 6));
    buf[1] = (char)(0x80 | (cp & 0x3f)); len = 2;
  } else if (cp <= 0xffff) {
    buf[0] = (char)(0xe0 | (cp >> 12));
    buf[1] = (char)(0x80 | ((cp >> 6) & 0x3f));
    buf[2] = (char)(0x80 | (cp & 0x3f)); len = 3;
  } else {
    buf[0] = (char)(0xf0 | (cp >> 18));
    buf[1] = (char)(0x80 | ((cp >> 12) & 0x3f));
    buf[2] = (char)(0x80 | ((cp >> 6) & 0x3f));
    buf[3] = (char)(0x80 | (cp & 0x3f)); len = 4;
  }
  buf[len] = '\0';
  sprout_gc_maybe_collect_threshold();
  char* out = sprout_gc_alloc_cstr(len, "char_to_str: out of memory");
  memcpy(out, buf, len);
  out[len] = '\0';  return (long long)(uintptr_t)out;
}

/* char_to_string: a Char is an immediate i64 Unicode codepoint; encode it to a
 * fresh UTF-8 heap String.  (Formerly the identity, when Char and String shared
 * the heap-string representation.) */
long long char_to_string(long long ch) {
  return char_to_str(ch);
}

/* char_from_codepoint: a Char IS its Unicode codepoint (an immediate i64), so
 * this is the identity.  Retained as the typed Int->Char constructor at the
 * Sprout level (char_to_str / char_to_string only produce String). */
long long char_from_codepoint(long long codepoint) {
  sprout_validate_codepoint(codepoint, "char_from_codepoint: codepoint out of Unicode range");
  return codepoint;
}

static void buf_init(ByteBuf* buf) {
  buf->data = NULL;
  buf->len = 0;
  buf->cap = 0;
}

static void buf_reserve(ByteBuf* buf, size_t want) {
  if (want <= buf->cap) return;
  size_t next = buf->cap == 0 ? 256 : buf->cap;
  while (next < want) next *= 2;
  char* grown = (char*)realloc(buf->data, next);
  if (grown == NULL) tcp_fail("http_request: out of memory");
  buf->data = grown;
  buf->cap = next;
}

static void buf_append_bytes(ByteBuf* buf, const char* data, size_t len) {
  buf_reserve(buf, buf->len + len + 1);
  memcpy(buf->data + buf->len, data, len);
  buf->len += len;
  buf->data[buf->len] = '\0';
}

static void buf_append_cstr(ByteBuf* buf, const char* text) {
  buf_append_bytes(buf, text, strlen(text));
}

static void buf_append_char(ByteBuf* buf, char ch) {
  buf_reserve(buf, buf->len + 2);
  buf->data[buf->len] = ch;
  buf->len += 1;
  buf->data[buf->len] = '\0';
}

static char* alloc_cstr(size_t len, const char* ctx) {
  char* out = (char*)malloc(len + 1);
  if (out == NULL) tcp_fail(ctx);
  out[0] = '\0';
  return out;
}

static char* dup_slice(const char* start, size_t len) {
  char* out = alloc_cstr(len, "http_request: out of memory");
  memcpy(out, start, len);
  out[len] = '\0';
  return out;
}

static char* dup_cstr(const char* text) {
  return dup_slice(text, strlen(text));
}

/* register_cstr: ensure a string is GC-managed with an inline header.
 *
 * If value is already in the GC arena (lookup succeeds) it is returned as-is.
 * Otherwise the plain malloc'd buffer is adopted: a new headered arena block is
 * allocated, the content is copied, the old buffer is freed, and the new pointer
 * is returned.
 *
 * INVARIANT: callers must capture the return value — the returned pointer may
 * differ from value.  NEVER pass a headered arena CSTR here (both
 * sprout_gc_alloc_cstr and sprout_gc_adopt_cstr already produce arena blocks). */
static char* register_cstr(char* value) {
  if (value == NULL) return NULL;
  /* Already in the GC arena (lookup succeeds) → already registered. */
  if (sprout_heap_lookup(value) != NULL) return value;
  size_t len = strlen(value);
  char* headered = sprout_gc_alloc_cstr(len, "register_cstr: out of memory");
  if (len > 0) memcpy(headered, value, len);
  headered[len] = '\0';
  free(value);
  /* No register call — sprout_gc_alloc_block already registered the block. */
  return headered;
}

static char* dup_managed_slice(const char* start, size_t len, const char* ctx) {
  char* out = sprout_gc_alloc_cstr(len, ctx);
  if (len > 0) memcpy(out, start, len);
  out[len] = '\0';
  return out;
}

static char* dup_managed_cstr(const char* text, const char* ctx) {
  return dup_managed_slice(text, strlen(text), ctx);
}

static char* regex_prefixed_error(const char* prefix, const char* detail) {
  size_t prefix_len = strlen(prefix);
  size_t detail_len = strlen(detail);
  char* out = alloc_cstr(prefix_len + detail_len, "regex_validate: out of memory");
  memcpy(out, prefix, prefix_len);
  memcpy(out + prefix_len, detail, detail_len);
  out[prefix_len + detail_len] = '\0';
  return out;
}

static void regex_builtin_fail(const char* builtin, const char* detail) {
  size_t builtin_len = strlen(builtin);
  size_t detail_len = strlen(detail);
  char* out = alloc_cstr(builtin_len + 2 + detail_len, "regex_validate: out of memory");
  memcpy(out, builtin, builtin_len);
  out[builtin_len] = ':';
  out[builtin_len + 1] = ' ';
  memcpy(out + builtin_len + 2, detail, detail_len);
  out[builtin_len + 2 + detail_len] = '\0';
  tcp_fail(out);
}

static int regex_translate_pattern(const char* pattern, char** out_pattern, char** out_error) {
  ByteBuf out;
  buf_init(&out);
  int in_class = 0;
  for (size_t i = 0; pattern[i] != '\0';) {
    char ch = pattern[i];
    if (ch == '\\') {
      char esc = pattern[i + 1];
      if (esc == '\0') {
        if (out.data != NULL) free(out.data);
        *out_pattern = NULL;
        *out_error = dup_cstr("invalid regex pattern: trailing escape");
        return 0;
      }
      if (esc >= '0' && esc <= '9') {
        if (out.data != NULL) free(out.data);
        *out_pattern = NULL;
        *out_error = dup_cstr("unsupported regex feature: backreferences");
        return 0;
      }
      if (esc == 'd') {
        if (in_class) buf_append_cstr(&out, "0-9");
        else buf_append_cstr(&out, "[0-9]");
      } else if (esc == 'w') {
        if (in_class) buf_append_cstr(&out, "A-Za-z0-9_");
        else buf_append_cstr(&out, "[A-Za-z0-9_]");
      } else if (esc == 's') {
        if (in_class) {
          buf_append_cstr(&out, " \t\r\n");
        } else {
          buf_append_cstr(&out, "[ \t\r\n]");
        }
      } else if (strchr("\\.^$*+?()[]|{}-", esc) != NULL) {
        buf_append_char(&out, '\\');
        buf_append_char(&out, esc);
      } else {
        if (out.data != NULL) free(out.data);
        *out_pattern = NULL;
        *out_error = regex_prefixed_error("unsupported regex feature: escape \\", (char[]){esc, '\0'});
        return 0;
      }
      i += 2;
      continue;
    }
    if (!in_class) {
      if (ch == '[') {
        in_class = 1;
        buf_append_char(&out, ch);
      } else if (ch == '{' || ch == '}') {
        if (out.data != NULL) free(out.data);
        *out_pattern = NULL;
        *out_error = dup_cstr("unsupported regex feature: counted repetition");
        return 0;
      } else if ((ch == '*' || ch == '+' || ch == '?') && pattern[i + 1] == '?') {
        if (out.data != NULL) free(out.data);
        *out_pattern = NULL;
        *out_error = dup_cstr("unsupported regex feature: non-greedy quantifiers");
        return 0;
      } else if (ch == '(' && pattern[i + 1] == '?') {
        if (out.data != NULL) free(out.data);
        *out_pattern = NULL;
        *out_error = dup_cstr("unsupported regex feature: extended group syntax");
        return 0;
      } else {
        buf_append_char(&out, ch);
      }
    } else {
      if (ch == ']') in_class = 0;
      buf_append_char(&out, ch);
    }
    i += 1;
  }
  if (out.data == NULL) out.data = alloc_cstr(0, "regex_validate: out of memory");
  *out_pattern = out.data;
  *out_error = NULL;
  return 1;
}

static int regex_compile_ere(const char* pattern, regex_t* out_regex, char** out_error) {
  char* translated = NULL;
  char* translation_error = NULL;
  if (!regex_translate_pattern(pattern, &translated, &translation_error)) {
    *out_error = translation_error;
    return 0;
  }
  int status = regcomp(out_regex, translated, REG_EXTENDED);
  free(translated);
  if (status != 0) {
    char errbuf[256];
    regerror(status, out_regex, errbuf, sizeof(errbuf));
    *out_error = regex_prefixed_error("invalid regex pattern: ", errbuf);
    return 0;
  }
  *out_error = NULL;
  return 1;
}

static long long sprout_utf8_codepoint_prefix_count(const char* s, size_t byte_limit) {
  size_t count = 0;
  size_t i = 0;
  while (s[i] != '\0' && i < byte_limit) {
    i += sprout_utf8_step(s, i);
    count += 1;
  }
  return (long long)count;
}

static char* upper_copy(const char* text) {
  size_t len = strlen(text);
  char* out = dup_slice(text, len);
  for (size_t i = 0; i < len; i++) {
    if (out[i] >= 'a' && out[i] <= 'z') out[i] = (char)(out[i] - 'a' + 'A');
  }
  return out;
}

static long long http_err0(const char* ctor_name) {
  SPROUT_HANDLE(h_err, sprout_make0(find_ctor_tag_by_name(ctor_name)));
  long long out = sprout_make1(find_ctor_tag_by_name("Err"), sprout_handle_get(h_err));
  return out;
}

static long long http_err1(const char* ctor_name, long long payload) {
  SPROUT_HANDLE(h_payload, payload);
  SPROUT_HANDLE(h_err, sprout_make1(find_ctor_tag_by_name(ctor_name), sprout_handle_get(h_payload)));
  long long out = sprout_make1(find_ctor_tag_by_name("Err"), sprout_handle_get(h_err));
  return out;
}

static long long http_err_cstr(const char* ctor_name, char* payload) {
  payload = register_cstr(payload);
  SPROUT_HANDLE(h_payload, (long long)(uintptr_t)payload);
  SPROUT_HANDLE(h_err, sprout_make1(find_ctor_tag_by_name(ctor_name), sprout_handle_get(h_payload)));
  long long out = sprout_make1(find_ctor_tag_by_name("Err"), sprout_handle_get(h_err));
  return out;
}

static long long http_err_text(const char* ctor_name, const char* payload) {
  return http_err_cstr(ctor_name, dup_managed_cstr(payload, "http_request: out of memory"));
}

static long long http_ok_response(long long status, char* headers, char* body) {
  headers = register_cstr(headers);
  body = register_cstr(body);
  SPROUT_HANDLE(h_headers, (long long)(uintptr_t)headers);
  SPROUT_HANDLE(h_body, (long long)(uintptr_t)body);
  SPROUT_HANDLE(h_resp, sprout_make3(
    find_ctor_tag_by_name("stdlib.http.HttpResponse"),
    status,
    sprout_handle_get(h_headers),
    sprout_handle_get(h_body)
  ));
  long long out = sprout_make1(find_ctor_tag_by_name("Ok"), sprout_handle_get(h_resp));
  return out;
}

static void json_append_hex4(ByteBuf* out, unsigned char value) {
  static const char* hex = "0123456789abcdef";
  char escaped[6];
  escaped[0] = '\\';
  escaped[1] = 'u';
  escaped[2] = '0';
  escaped[3] = '0';
  escaped[4] = hex[(value >> 4) & 0x0f];
  escaped[5] = hex[value & 0x0f];
  buf_append_bytes(out, escaped, sizeof(escaped));
}

static void json_append_escaped_string(ByteBuf* out, const char* raw) {
  if (raw == NULL) tcp_fail("json_stringify: null string");
  char quote = '"';
  buf_append_bytes(out, &quote, 1);
  for (const unsigned char* p = (const unsigned char*)raw; *p != '\0'; p++) {
    unsigned char ch = *p;
    if (ch == '"') {
      const char escaped_quote[2] = {'\\', '"'};
      buf_append_bytes(out, escaped_quote, 2);
    } else if (ch == '\\') {
      const char escaped_slash[2] = {'\\', '\\'};
      buf_append_bytes(out, escaped_slash, 2);
    } else if (ch == '\b') {
      const char escaped_backspace[2] = {'\\', 'b'};
      buf_append_bytes(out, escaped_backspace, 2);
    } else if (ch == '\f') {
      const char escaped_formfeed[2] = {'\\', 'f'};
      buf_append_bytes(out, escaped_formfeed, 2);
    } else if (ch == '\n') {
      const char escaped_newline[2] = {'\\', 'n'};
      buf_append_bytes(out, escaped_newline, 2);
    } else if (ch == '\r') {
      const char escaped_return[2] = {'\\', 'r'};
      buf_append_bytes(out, escaped_return, 2);
    } else if (ch == '\t') {
      const char escaped_tab[2] = {'\\', 't'};
      buf_append_bytes(out, escaped_tab, 2);
    } else if (ch < 0x20) {
      json_append_hex4(out, ch);
    } else {
      buf_append_bytes(out, (const char*)p, 1);
    }
  }
  buf_append_bytes(out, &quote, 1);
}

static const char* json_ctor_name(long long value) {
  if (!is_obj_handle(value)) return NULL;
  CtorMeta* meta = find_ctor(sprout_tag(value));
  return meta == NULL ? NULL : meta->name;
}

static int json_ctor_is(const char* ctor_name, const char* leaf_name) {
  if (ctor_name == NULL) return 0;
  if (strcmp(ctor_name, leaf_name) == 0) return 1;
  size_t ctor_len = strlen(ctor_name);
  size_t leaf_len = strlen(leaf_name);
  if (ctor_len <= leaf_len) return 0;
  if (strcmp(ctor_name + ctor_len - leaf_len, leaf_name) != 0) return 0;
  return ctor_name[ctor_len - leaf_len - 1] == '.';
}

static void json_append_array(ByteBuf* out, long long value) {
  const char* ctor_name = json_ctor_name(value);
  if (!json_ctor_is(ctor_name, "JsonArray")) {
    tcp_fail("json_stringify: expects JsonArray");
  }
  buf_append_cstr(out, "[");
  long long cursor = sprout_field(value, 0);
  int first = 1;
  while (1) {
    const char* cursor_name = json_ctor_name(cursor);
    if (cursor_name == NULL) tcp_fail("json_stringify: expects JsonArray");
    if (json_ctor_is(cursor_name, "JsonArrayNil")) break;
    if (!json_ctor_is(cursor_name, "JsonArrayCons")) {
      tcp_fail("json_stringify: expects JsonArray");
    }
    if (!first) buf_append_cstr(out, ",");
    json_append_value(out, sprout_field(cursor, 0));
    cursor = sprout_field(cursor, 1);
    first = 0;
  }
  buf_append_cstr(out, "]");
}

static void json_append_object(ByteBuf* out, long long value) {
  const char* ctor_name = json_ctor_name(value);
  if (!json_ctor_is(ctor_name, "JsonObject")) {
    tcp_fail("json_stringify: expects JsonObject");
  }
  buf_append_cstr(out, "{");
  long long cursor = sprout_field(value, 0);
  int first = 1;
  while (1) {
    const char* cursor_name = json_ctor_name(cursor);
    if (cursor_name == NULL) tcp_fail("json_stringify: expects JsonObject");
    if (json_ctor_is(cursor_name, "JsonObjectNil")) break;
    if (!json_ctor_is(cursor_name, "JsonObjectCons")) {
      tcp_fail("json_stringify: expects JsonObject");
    }
    if (!first) buf_append_cstr(out, ",");
    json_append_escaped_string(out, (const char*)(uintptr_t)sprout_field(cursor, 0));
    buf_append_cstr(out, ":");
    json_append_value(out, sprout_field(cursor, 1));
    cursor = sprout_field(cursor, 2);
    first = 0;
  }
  buf_append_cstr(out, "}");
}

static void json_append_value(ByteBuf* out, long long value) {
  const char* ctor_name = json_ctor_name(value);
  if (ctor_name == NULL) tcp_fail("json_stringify: expects Json");
  if (json_ctor_is(ctor_name, "JsonNull")) {
    buf_append_cstr(out, "null");
  } else if (json_ctor_is(ctor_name, "JsonBool")) {
    buf_append_cstr(out, sprout_field(value, 0) != 0 ? "true" : "false");
  } else if (json_ctor_is(ctor_name, "JsonInt")) {
    char int_buf[64];
    snprintf(int_buf, sizeof(int_buf), "%lld", sprout_field(value, 0));
    buf_append_cstr(out, int_buf);
  } else if (json_ctor_is(ctor_name, "JsonString")) {
    json_append_escaped_string(out, (const char*)(uintptr_t)sprout_field(value, 0));
  } else if (json_ctor_is(ctor_name, "JsonArray")) {
    json_append_array(out, value);
  } else if (json_ctor_is(ctor_name, "JsonObject")) {
    json_append_object(out, value);
  } else {
    tcp_fail("json_stringify: expects Json");
  }
}

static long long json_parse_value(const char** pos_ptr, char** err_msg);

static long long json_parse_ok_result(long long value) {
  SPROUT_HANDLE(h_value, value);
  long long out = sprout_make1(find_ctor_tag_by_name("Ok"), sprout_handle_get(h_value));
  return out;
}

static long long json_parse_err_result(const char* message) {
  char* owned = dup_managed_cstr(message, "json_parse: out of memory");
  SPROUT_HANDLE(h_message, (long long)(uintptr_t)owned);
  SPROUT_HANDLE(h_err, sprout_make1(
    find_ctor_tag_by_name("stdlib.json.JsonDecode"),
    sprout_handle_get(h_message)
  ));
  long long out = sprout_make1(find_ctor_tag_by_name("Err"), sprout_handle_get(h_err));
  return out;
}

static long long json_parse_reverse_array_items(long long reversed) {
  SPROUT_HANDLE(h_reversed, reversed);
  SPROUT_HANDLE(h_out, sprout_make0(find_ctor_tag_by_name("stdlib.json.JsonArrayNil")));
  long long cursor = reversed;
  while (json_ctor_is(json_ctor_name(cursor), "JsonArrayCons")) {
    long long value = sprout_field(cursor, 0);
    SPROUT_HANDLE_SET(h_out, sprout_make2(find_ctor_tag_by_name("stdlib.json.JsonArrayCons"), value, sprout_handle_get(h_out)));
    cursor = sprout_field(cursor, 1);
  }
  return sprout_handle_get(h_out);
}

static long long json_parse_reverse_object_items(long long reversed) {
  SPROUT_HANDLE(h_reversed, reversed);
  SPROUT_HANDLE(h_out, sprout_make0(find_ctor_tag_by_name("stdlib.json.JsonObjectNil")));
  long long cursor = reversed;
  while (json_ctor_is(json_ctor_name(cursor), "JsonObjectCons")) {
    long long key = sprout_field(cursor, 0);
    long long value = sprout_field(cursor, 1);
    SPROUT_HANDLE_SET(h_out, sprout_make3(find_ctor_tag_by_name("stdlib.json.JsonObjectCons"), key, value, sprout_handle_get(h_out)));
    cursor = sprout_field(cursor, 2);
  }
  return sprout_handle_get(h_out);
}

static long long json_parse_array(const char** pos_ptr, char** err_msg) {
  const char* pos = sprout_json_skip_ws(*pos_ptr);
  pos++;
  SPROUT_HANDLE(h_reversed, sprout_make0(find_ctor_tag_by_name("stdlib.json.JsonArrayNil")));
  pos = sprout_json_skip_ws(pos);
  if (*pos == ']') {
    SPROUT_HANDLE(h_list, json_parse_reverse_array_items(sprout_handle_get(h_reversed)));
    long long out = sprout_make1(find_ctor_tag_by_name("stdlib.json.JsonArray"), sprout_handle_get(h_list));
    *pos_ptr = pos + 1;
    return out;
  }
  while (*pos != '\0') {
    long long value = json_parse_value(&pos, err_msg);
    if (err_msg != NULL && *err_msg != NULL) {
      return 0;
    }
    {
      SPROUT_HANDLE(h_value, value);
      SPROUT_HANDLE_SET(h_reversed, sprout_make2(find_ctor_tag_by_name("stdlib.json.JsonArrayCons"), sprout_handle_get(h_value), sprout_handle_get(h_reversed)));
    }
    pos = sprout_json_skip_ws(pos);
    if (*pos == ']') {
      SPROUT_HANDLE(h_list, json_parse_reverse_array_items(sprout_handle_get(h_reversed)));
      long long out = sprout_make1(find_ctor_tag_by_name("stdlib.json.JsonArray"), sprout_handle_get(h_list));
      *pos_ptr = pos + 1;
      return out;
    }
    if (*pos != ',') {
      if (err_msg != NULL && *err_msg == NULL) *err_msg = dup_cstr("expected ',' or ']'");
      return 0;
    }
    pos = sprout_json_skip_ws(pos + 1);
  }
  if (err_msg != NULL && *err_msg == NULL) *err_msg = dup_cstr("unterminated array");
  return 0;
}

static long long json_parse_object(const char** pos_ptr, char** err_msg) {
  const char* pos = sprout_json_skip_ws(*pos_ptr);
  pos++;
  SPROUT_HANDLE(h_reversed, sprout_make0(find_ctor_tag_by_name("stdlib.json.JsonObjectNil")));
  pos = sprout_json_skip_ws(pos);
  if (*pos == '}') {
    SPROUT_HANDLE(h_list, json_parse_reverse_object_items(sprout_handle_get(h_reversed)));
    long long out = sprout_make1(find_ctor_tag_by_name("stdlib.json.JsonObject"), sprout_handle_get(h_list));
    *pos_ptr = pos + 1;
    return out;
  }
  while (*pos != '\0') {
    const char* parse_string_err = NULL;
    char* key = sprout_json_parse_string_impl(&pos, &parse_string_err);
    if (key == NULL) {
      if (err_msg != NULL && *err_msg == NULL) {
        *err_msg = dup_cstr(parse_string_err != NULL ? parse_string_err : "expected string key");
      }
      return 0;
    }
    pos = sprout_json_skip_ws(pos);
    if (*pos != ':') {
      free(key);
      if (err_msg != NULL && *err_msg == NULL) *err_msg = dup_cstr("expected ':' after object key");
      return 0;
    }
    {
      key = register_cstr(key);
      SPROUT_HANDLE(h_key, (long long)(uintptr_t)key);
      pos = sprout_json_skip_ws(pos + 1);
      long long value = json_parse_value(&pos, err_msg);
      if (err_msg != NULL && *err_msg != NULL) {
        return 0;
      }
      SPROUT_HANDLE(h_value, value);
      SPROUT_HANDLE_SET(h_reversed, sprout_make3(
        find_ctor_tag_by_name("stdlib.json.JsonObjectCons"),
        sprout_handle_get(h_key),
        sprout_handle_get(h_value),
        sprout_handle_get(h_reversed)
      ));
    }
    pos = sprout_json_skip_ws(pos);
    if (*pos == '}') {
      SPROUT_HANDLE(h_list, json_parse_reverse_object_items(sprout_handle_get(h_reversed)));
      long long out = sprout_make1(find_ctor_tag_by_name("stdlib.json.JsonObject"), sprout_handle_get(h_list));
      *pos_ptr = pos + 1;
      return out;
    }
    if (*pos != ',') {
      if (err_msg != NULL && *err_msg == NULL) *err_msg = dup_cstr("expected ',' or '}'");
      return 0;
    }
    pos = sprout_json_skip_ws(pos + 1);
  }
  if (err_msg != NULL && *err_msg == NULL) *err_msg = dup_cstr("unterminated object");
  return 0;
}

static long long json_parse_number(const char** pos_ptr, char** err_msg) {
  const char* pos = sprout_json_skip_ws(*pos_ptr);
  const char* start = pos;
  if (*pos == '-') pos++;
  if (*pos == '0') {
    pos++;
  } else if (*pos >= '1' && *pos <= '9') {
    while (*pos >= '0' && *pos <= '9') pos++;
  } else {
    if (err_msg != NULL && *err_msg == NULL) *err_msg = dup_cstr("invalid number");
    return 0;
  }
  if (*pos == '.' || *pos == 'e' || *pos == 'E') {
    if (err_msg != NULL && *err_msg == NULL) *err_msg = dup_cstr("only integer JSON numbers are supported");
    return 0;
  }
  size_t len = (size_t)(pos - start);
  char* raw = dup_slice(start, len);
  char* end = NULL;
  long long parsed = strtoll(raw, &end, 10);
  int valid = end != NULL && *end == '\0';
  free(raw);
  if (!valid) {
    if (err_msg != NULL && *err_msg == NULL) *err_msg = dup_cstr("invalid number");
    return 0;
  }
  *pos_ptr = pos;
  return sprout_make1(find_ctor_tag_by_name("stdlib.json.JsonInt"), parsed);
}

static long long json_parse_value(const char** pos_ptr, char** err_msg) {
  const char* pos = sprout_json_skip_ws(*pos_ptr);
  if (pos == NULL || *pos == '\0') {
    if (err_msg != NULL && *err_msg == NULL) *err_msg = dup_cstr("unexpected end of input");
    return 0;
  }
  if (*pos == '"') {
    const char* parse_string_err = NULL;
    char* value = sprout_json_parse_string_impl(&pos, &parse_string_err);
    if (value == NULL) {
      if (err_msg != NULL && *err_msg == NULL) {
        *err_msg = dup_cstr(parse_string_err != NULL ? parse_string_err : "invalid string");
      }
      return 0;
    }
    *pos_ptr = pos;
    value = register_cstr(value);
    SPROUT_HANDLE(h_value, (long long)(uintptr_t)value);
    return sprout_make1(find_ctor_tag_by_name("stdlib.json.JsonString"), sprout_handle_get(h_value));
  }
  if (*pos == '{') {
    long long out = json_parse_object(&pos, err_msg);
    if (err_msg == NULL || *err_msg == NULL) *pos_ptr = pos;
    return out;
  }
  if (*pos == '[') {
    long long out = json_parse_array(&pos, err_msg);
    if (err_msg == NULL || *err_msg == NULL) *pos_ptr = pos;
    return out;
  }
  if (strncmp(pos, "true", 4) == 0) {
    *pos_ptr = pos + 4;
    return sprout_make1(find_ctor_tag_by_name("stdlib.json.JsonBool"), 1);
  }
  if (strncmp(pos, "false", 5) == 0) {
    *pos_ptr = pos + 5;
    return sprout_make1(find_ctor_tag_by_name("stdlib.json.JsonBool"), 0);
  }
  if (strncmp(pos, "null", 4) == 0) {
    *pos_ptr = pos + 4;
    return sprout_make0(find_ctor_tag_by_name("stdlib.json.JsonNull"));
  }
  if (*pos == '-' || (*pos >= '0' && *pos <= '9')) {
    long long out = json_parse_number(&pos, err_msg);
    if (err_msg == NULL || *err_msg == NULL) *pos_ptr = pos;
    return out;
  }
  if (err_msg != NULL && *err_msg == NULL) *err_msg = dup_cstr("unexpected token");
  return 0;
}

long long json_stringify(long long value) {
  SPROUT_HANDLE(h_value, value);
  sprout_gc_maybe_collect_threshold();
  ByteBuf out;
  buf_init(&out);
  json_append_value(&out, sprout_handle_get(h_value));
  char* raw = out.data != NULL ? out.data : dup_cstr("");
  size_t raw_len = out.data != NULL ? out.len : 0;
  char* result = sprout_gc_adopt_cstr(raw, raw_len, "json_stringify: out of memory");  return (long long)(uintptr_t)result;
}

long long json_parse(long long raw_val) {
  const char* raw = (const char*)raw_val;
  if (raw == NULL) tcp_fail("json_parse expects String");
  const char* pos = raw;
  char* err_msg = NULL;
  long long value = json_parse_value(&pos, &err_msg);
  if (err_msg != NULL) {
    long long out = json_parse_err_result(err_msg);
    free(err_msg);
    return out;
  }
  pos = sprout_json_skip_ws(pos);
  if (pos == NULL || *pos != '\0') {
    return json_parse_err_result("unexpected trailing characters");
  }
  return json_parse_ok_result(value);
}

static int parse_http_url(const char* url, HttpUrl* out, char** err) {
  const char* http_prefix = "http://";
  const char* https_prefix = "https://";
  size_t prefix_len = 0;
  if (strncmp(url, http_prefix, strlen(http_prefix)) == 0) {
    out->use_tls = 0;
    prefix_len = strlen(http_prefix);
  } else if (strncmp(url, https_prefix, strlen(https_prefix)) == 0) {
    out->use_tls = 1;
    prefix_len = strlen(https_prefix);
  } else {
    *err = dup_cstr("unsupported url scheme");
    return 0;
  }
  const char* rest = url + prefix_len;
  const char* slash = strchr(rest, '/');
  const char* host_end = slash != NULL ? slash : rest + strlen(rest);
  if (host_end == rest) {
    *err = dup_cstr("missing host");
    return 0;
  }
  const char* colon = NULL;
  for (const char* p = rest; p < host_end; p++) {
    if (*p == ':') colon = p;
  }
  if (colon != NULL) {
    if (colon == rest || colon + 1 >= host_end) {
      *err = dup_cstr("invalid host or port");
      return 0;
    }
    out->host = dup_slice(rest, (size_t)(colon - rest));
    out->port = dup_slice(colon + 1, (size_t)(host_end - colon - 1));
  } else {
    out->host = dup_slice(rest, (size_t)(host_end - rest));
    out->port = dup_cstr(out->use_tls ? "443" : "80");
  }
  out->path = slash != NULL ? dup_cstr(slash) : dup_cstr("/");
  return 1;
}

static void free_http_url(HttpUrl* url) {
  free(url->host);
  free(url->port);
  free(url->path);
}

static char ascii_lower_char(char ch) {
  if (ch >= 'A' && ch <= 'Z') return (char)(ch - 'A' + 'a');
  return ch;
}

static int ascii_ieq_char(char left, char right) {
  return ascii_lower_char(left) == ascii_lower_char(right);
}

static int ascii_contains_token_ci(const char* text, const char* token) {
  if (text == NULL || token == NULL || token[0] == '\0') return 0;
  size_t token_len = strlen(token);
  const char* cursor = text;
  while (*cursor != '\0') {
    while (*cursor == ' ' || *cursor == '\t' || *cursor == ',') cursor++;
    const char* start = cursor;
    while (*cursor != '\0' && *cursor != ',') cursor++;
    const char* end = cursor;
    while (end > start && (end[-1] == ' ' || end[-1] == '\t')) end--;
    if ((size_t)(end - start) == token_len) {
      size_t i = 0;
      while (i < token_len && ascii_ieq_char(start[i], token[i])) i++;
      if (i == token_len) return 1;
    }
    if (*cursor == ',') cursor++;
  }
  return 0;
}

static char* http_header_value_ci(const char* headers, const char* key) {
  if (headers == NULL || key == NULL) return NULL;
  size_t key_len = strlen(key);
  const char* line = headers;
  while (*line != '\0') {
    const char* end = line;
    while (*end != '\0' && *end != '\n' && *end != '\r') end++;
    const char* colon = NULL;
    for (const char* p = line; p < end; p++) {
      if (*p == ':') {
        colon = p;
        break;
      }
    }
    if (colon != NULL) {
      const char* key_start = line;
      while (key_start < colon && (*key_start == ' ' || *key_start == '\t')) key_start++;
      const char* key_end = colon;
      while (key_end > key_start && (key_end[-1] == ' ' || key_end[-1] == '\t')) key_end--;
      if ((size_t)(key_end - key_start) == key_len) {
        size_t i = 0;
        while (i < key_len && ascii_ieq_char(key_start[i], key[i])) i++;
        if (i == key_len) {
          const char* value_start = colon + 1;
          while (value_start < end && (*value_start == ' ' || *value_start == '\t')) value_start++;
          const char* value_end = end;
          while (value_end > value_start && (value_end[-1] == ' ' || value_end[-1] == '\t')) value_end--;
          return dup_slice(value_start, (size_t)(value_end - value_start));
        }
      }
    }
    while (*end == '\r' || *end == '\n') end++;
    line = end;
  }
  return NULL;
}

static int hex_digit_value(char ch) {
  if (ch >= '0' && ch <= '9') return ch - '0';
  if (ch >= 'a' && ch <= 'f') return 10 + (ch - 'a');
  if (ch >= 'A' && ch <= 'F') return 10 + (ch - 'A');
  return -1;
}

static char* http_decode_chunked_body(const char* body, char** err) {
  ByteBuf out;
  buf_init(&out);
  const char* cursor = body;
  while (1) {
    while (*cursor == '\r' || *cursor == '\n') cursor++;
    const char* size_start = cursor;
    while (*cursor != '\0' && *cursor != '\r' && *cursor != '\n' && *cursor != ';') cursor++;
    const char* size_end = cursor;
    if (size_start == size_end) {
      free(out.data);
      *err = dup_cstr("invalid chunk size");
      return NULL;
    }
    size_t chunk_size = 0;
    for (const char* p = size_start; p < size_end; p++) {
      int digit = hex_digit_value(*p);
      if (digit < 0) {
        free(out.data);
        *err = dup_cstr("invalid chunk size");
        return NULL;
      }
      chunk_size = (chunk_size * 16u) + (size_t)digit;
    }
    while (*cursor != '\0' && *cursor != '\n') cursor++;
    if (*cursor == '\n') cursor++;
    if (chunk_size == 0) {
      while (*cursor == '\r' || *cursor == '\n') cursor++;
      break;
    }
    if (strlen(cursor) < chunk_size) {
      free(out.data);
      *err = dup_cstr("truncated chunk data");
      return NULL;
    }
    buf_append_bytes(&out, cursor, chunk_size);
    cursor += chunk_size;
    if (cursor[0] == '\r' && cursor[1] == '\n') {
      cursor += 2;
    } else if (cursor[0] == '\n') {
      cursor += 1;
    } else {
      free(out.data);
      *err = dup_cstr("invalid chunk terminator");
      return NULL;
    }
  }
  return out.data != NULL ? out.data : dup_cstr("");
}

static long long http_response_result(char* response_data) {
  if (response_data == NULL || response_data[0] == '\0') {
    free(response_data);
    return http_err_text(
      "stdlib.http.HttpNetwork",
      "remote closed connection without response"
    );
  }

  const char* sep = strstr(response_data, "\r\n\r\n");
  size_t sep_len = 4;
  if (sep == NULL) {
    sep = strstr(response_data, "\n\n");
    sep_len = 2;
  }
  if (sep == NULL) {
    free(response_data);
    return http_err_text("stdlib.http.HttpDecode", "invalid http response");
  }

  const char* line_end = strstr(response_data, "\r\n");
  size_t line_sep_len = 2;
  if (line_end == NULL || line_end > sep) {
    line_end = strstr(response_data, "\n");
    line_sep_len = 1;
  }
  if (line_end == NULL || line_end > sep) {
    free(response_data);
    return http_err_text("stdlib.http.HttpDecode", "invalid status line");
  }

  const char* code_start = strchr(response_data, ' ');
  if (code_start == NULL || code_start >= line_end) {
    free(response_data);
    return http_err_text("stdlib.http.HttpDecode", "invalid status line");
  }
  code_start++;
  char* code_end = NULL;
  long long status = strtoll(code_start, &code_end, 10);
  if (code_end == code_start || code_end > line_end) {
    free(response_data);
    return http_err_text("stdlib.http.HttpDecode", "invalid status code");
  }
  if (status >= 400) {
    free(response_data);
    return http_err1("stdlib.http.HttpBadStatus", status);
  }

  const char* headers_start = line_end + line_sep_len;
  char* headers = dup_slice(headers_start, (size_t)(sep - headers_start));
  char* body_out = dup_cstr(sep + sep_len);
  char* transfer_encoding = http_header_value_ci(headers, "Transfer-Encoding");
  char* content_encoding = http_header_value_ci(headers, "Content-Encoding");
  if (content_encoding != NULL) {
    if (content_encoding[0] != '\0' && !ascii_contains_token_ci(content_encoding, "identity")) {
      free(content_encoding);
      free(transfer_encoding);
      free(headers);
      free(body_out);
      free(response_data);
      return http_err_text("stdlib.http.HttpDecode", "unsupported content encoding");
    }
    free(content_encoding);
  }
  if (transfer_encoding != NULL) {
    if (ascii_contains_token_ci(transfer_encoding, "chunked")) {
      char* chunk_err = NULL;
      char* decoded = http_decode_chunked_body(body_out, &chunk_err);
      free(body_out);
      body_out = decoded;
      if (chunk_err != NULL) {
        free(transfer_encoding);
        free(headers);
        free(response_data);
        return http_err_cstr("stdlib.http.HttpDecode", chunk_err);
      }
    } else if (transfer_encoding[0] != '\0') {
      free(transfer_encoding);
      free(headers);
      free(body_out);
      free(response_data);
      return http_err_text("stdlib.http.HttpDecode", "unsupported transfer encoding");
    }
    free(transfer_encoding);
  }
  free(response_data);
  return http_ok_response(status, headers, body_out);
}

#ifdef __APPLE__
static unsigned char* read_binary_file(const char* path, size_t* out_len) {
  FILE* f = fopen(path, "rb");
  if (f == NULL) return NULL;
  if (fseek(f, 0, SEEK_END) != 0) {
    fclose(f);
    return NULL;
  }
  long size = ftell(f);
  if (size < 0) {
    fclose(f);
    return NULL;
  }
  if (fseek(f, 0, SEEK_SET) != 0) {
    fclose(f);
    return NULL;
  }
  unsigned char* data = (unsigned char*)malloc((size_t)size);
  if (size > 0 && data == NULL) {
    fclose(f);
    return NULL;
  }
  size_t got = fread(data, 1, (size_t)size, f);
  fclose(f);
  if (got != (size_t)size) {
    free(data);
    return NULL;
  }
  if (out_len != NULL) *out_len = got;
  return data;
}

static OSStatus tls_configure_peer_trust(SecTrustRef trust) {
  const char* anchor_path = getenv("SPROUT_HTTP_CA_CERT");
  if (anchor_path == NULL || anchor_path[0] == '\0') {
    return noErr;
  }
  size_t cert_len = 0;
  unsigned char* cert_data = read_binary_file(anchor_path, &cert_len);
  if (cert_data == NULL) return errSecIO;
  CFDataRef cf_data = CFDataCreate(kCFAllocatorDefault, cert_data, cert_len);
  free(cert_data);
  if (cf_data == NULL) return errSecAllocate;
  SecCertificateRef cert = SecCertificateCreateWithData(kCFAllocatorDefault, cf_data);
  CFRelease(cf_data);
  if (cert == NULL) return errSecDecode;
  const void* values[1] = {cert};
  CFArrayRef anchors = CFArrayCreate(kCFAllocatorDefault, values, 1, &kCFTypeArrayCallBacks);
  if (anchors == NULL) {
    CFRelease(cert);
    return errSecAllocate;
  }
  OSStatus status = SecTrustSetAnchorCertificates(trust, anchors);
  if (status == noErr) {
    status = SecTrustSetAnchorCertificatesOnly(trust, true);
  }
  CFRelease(anchors);
  CFRelease(cert);
  return status;
}

static int tls_uses_custom_ca_anchor(void) {
  const char* anchor_path = getenv("SPROUT_HTTP_CA_CERT");
  return anchor_path != NULL && anchor_path[0] != '\0';
}

typedef struct {
  int fd;
  int last_errno;
} TlsConn;

static OSStatus tls_read_func(SSLConnectionRef connection, void* data, size_t* dataLength) {
  TlsConn* conn = (TlsConn*)(uintptr_t)connection;
  if (conn == NULL || data == NULL || dataLength == NULL) return errSSLClosedAbort;
  size_t requested = *dataLength;
  if (requested == 0) {
    *dataLength = 0;
    return noErr;
  }
  ssize_t n;
  while (1) {
    n = recv(conn->fd, data, requested, 0);
    if (n >= 0) break;
    if (errno == EINTR) continue;
    conn->last_errno = errno;
    *dataLength = 0;
    if (errno == EAGAIN || errno == EWOULDBLOCK) return errSSLWouldBlock;
    return errSSLClosedAbort;
  }
  conn->last_errno = 0;
  if (n == 0) {
    *dataLength = 0;
    return errSSLClosedGraceful;
  }
  *dataLength = (size_t)n;
  if ((size_t)n < requested) return errSSLWouldBlock;
  return noErr;
}

static OSStatus tls_write_func(SSLConnectionRef connection, const void* data, size_t* dataLength) {
  TlsConn* conn = (TlsConn*)(uintptr_t)connection;
  if (conn == NULL || data == NULL || dataLength == NULL) return errSSLClosedAbort;
  size_t requested = *dataLength;
  if (requested == 0) {
    *dataLength = 0;
    return noErr;
  }
  ssize_t n;
  while (1) {
    n = send(conn->fd, data, requested, 0);
    if (n >= 0) break;
    if (errno == EINTR) continue;
    conn->last_errno = errno;
    *dataLength = 0;
    if (errno == EAGAIN || errno == EWOULDBLOCK) return errSSLWouldBlock;
    return errSSLClosedAbort;
  }
  conn->last_errno = 0;
  if (n == 0) {
    *dataLength = 0;
    return errSSLClosedAbort;
  }
  *dataLength = (size_t)n;
  if ((size_t)n < requested) return errSSLWouldBlock;
  return noErr;
}

static int tls_status_timed_out(const TlsConn* conn) {
  return conn != NULL && (conn->last_errno == EAGAIN || conn->last_errno == EWOULDBLOCK);
}

static int tls_status_is_auth_event(OSStatus status) {
  return status == errSSLPeerAuthCompleted || status == errSSLServerAuthCompleted;
}

static char* tls_error_message(const char* prefix, OSStatus status, const TlsConn* conn) {
  char detail[256];
  if (conn != NULL && conn->last_errno != 0) {
    snprintf(detail, sizeof(detail), "%s (status=%d, errno=%d: %s)", prefix, (int)status, conn->last_errno, strerror(conn->last_errno));
  } else {
    snprintf(detail, sizeof(detail), "%s (status=%d)", prefix, (int)status);
  }
  return dup_cstr(detail);
}

static int tls_debug_enabled(void) {
  const char* raw = getenv("SPROUT_HTTP_TLS_DEBUG");
  return raw != NULL && raw[0] != '\0' && strcmp(raw, "0") != 0;
}

static void tls_debug_log(const char* fmt, ...) {
  if (!tls_debug_enabled()) return;
  va_list args;
  va_start(args, fmt);
  fprintf(stderr, "[sprout tls] ");
  vfprintf(stderr, fmt, args);
  fprintf(stderr, "\n");
  va_end(args);
}

static OSStatus tls_write_all(SSLContextRef ctx, TlsConn* conn, const char* data, size_t len) {
  size_t offset = 0;
  while (offset < len) {
    size_t written = len - offset;
    conn->last_errno = 0;
    OSStatus status = SSLWrite(ctx, data + offset, len - offset, &written);
    tls_debug_log("write status=%d wrote=%zu remaining=%zu errno=%d", (int)status, written, len - offset, conn->last_errno);
    offset += written;
    if (status == noErr) continue;
    if (tls_status_is_auth_event(status)) continue;
    if (status == errSSLWouldBlock) {
      if (written == 0 && tls_status_timed_out(conn)) return status;
      continue;
    }
    return status;
  }
  return noErr;
}

static OSStatus tls_read_append(SSLContextRef ctx, TlsConn* conn, ByteBuf* response) {
  char chunk[4096];
  while (1) {
    size_t chunk_len = sizeof(chunk);
    conn->last_errno = 0;
    OSStatus status = SSLRead(ctx, chunk, sizeof(chunk), &chunk_len);
    tls_debug_log("read status=%d chunk_len=%zu errno=%d", (int)status, chunk_len, conn->last_errno);
    if (chunk_len > 0) {
      buf_append_bytes(response, chunk, chunk_len);
    }
    if (status == noErr) continue;
    if (tls_status_is_auth_event(status)) continue;
    if (status == errSSLClosedGraceful || status == errSSLClosedNoNotify || status == errSSLClosedAbort) {
      return response->len > 0 ? noErr : status;
    }
    if (status == errSSLWouldBlock) {
      if (chunk_len == 0 && tls_status_timed_out(conn)) return status;
      continue;
    }
    return status;
  }
}

static long long http_request_tls(HttpUrl* parsed, const char* request_data, size_t request_len, long long timeout_ms) {
  struct addrinfo hints;
  memset(&hints, 0, sizeof(hints));
  hints.ai_socktype = SOCK_STREAM;
  hints.ai_family = AF_UNSPEC;
  struct addrinfo* infos = NULL;
  int gai = getaddrinfo(parsed->host, parsed->port, &hints, &infos);
  if (gai != 0) {
    return http_err_text("stdlib.http.HttpNetwork", gai_strerror(gai));
  }

  int fd = -1;
  int last_errno = 0;
  for (struct addrinfo* it = infos; it != NULL; it = it->ai_next) {
    fd = socket(it->ai_family, it->ai_socktype, it->ai_protocol);
    if (fd < 0) {
      last_errno = errno;
      continue;
    }
    struct timeval tv;
    tv.tv_sec = (time_t)(timeout_ms / 1000);
    tv.tv_usec = (suseconds_t)((timeout_ms % 1000) * 1000);
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
    setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof(tv));
    if (connect(fd, it->ai_addr, it->ai_addrlen) == 0) break;
    last_errno = errno;
    close(fd);
    fd = -1;
  }
  freeaddrinfo(infos);
  if (fd < 0) {
    if (last_errno == EAGAIN || last_errno == EWOULDBLOCK) return http_err0("stdlib.http.HttpTimeout");
    return http_err_text("stdlib.http.HttpNetwork", strerror(last_errno));
  }

  TlsConn tls = {.fd = fd, .last_errno = 0};
  int use_custom_ca = tls_uses_custom_ca_anchor();
  tls_debug_log("connect host=%s port=%s timeout_ms=%lld custom_ca=%s", parsed->host, parsed->port, timeout_ms, use_custom_ca ? "yes" : "no");
  SSLContextRef ctx = SSLCreateContext(NULL, kSSLClientSide, kSSLStreamType);
  if (ctx == NULL) {
    close(fd);
    return http_err_text("stdlib.http.HttpNetwork", "tls context creation failed");
  }
  OSStatus status = SSLSetIOFuncs(ctx, tls_read_func, tls_write_func);
  tls_debug_log("manual_trust=%d", use_custom_ca ? 1 : 0);
  if (status == noErr && use_custom_ca) status = SSLSetSessionOption(ctx, kSSLSessionOptionBreakOnServerAuth, true);
  if (status == noErr) status = SSLSetConnection(ctx, (SSLConnectionRef)&tls);
  if (status == noErr) status = SSLSetPeerDomainName(ctx, parsed->host, strlen(parsed->host));
  if (status == noErr) {
    while (1) {
      tls.last_errno = 0;
      status = SSLHandshake(ctx);
      if (status == noErr) break;
      if (status == errSSLWouldBlock) {
        if (tls_status_timed_out(&tls)) {
          SSLDisposeContext(ctx);
          close(fd);
          return http_err0("stdlib.http.HttpTimeout");
        }
        continue;
      }
      if (use_custom_ca && tls_status_is_auth_event(status)) {
        SecTrustRef trust = NULL;
        OSStatus trust_status = SSLCopyPeerTrust(ctx, &trust);
        if (trust_status == noErr && trust != NULL) {
          trust_status = tls_configure_peer_trust(trust);
          if (trust_status == noErr && !SecTrustEvaluateWithError(trust, NULL)) {
            trust_status = errSecNotTrusted;
          }
        } else if (trust_status == noErr) {
          trust_status = errSecIO;
        }
        if (trust != NULL) CFRelease(trust);
        if (trust_status != noErr) {
          SSLDisposeContext(ctx);
          close(fd);
          return http_err_text(
            "stdlib.http.HttpNetwork",
            "tls certificate verification failed"
          );
        }
        continue;
      }
      break;
    }
  }
  if (status != noErr) {
    SSLDisposeContext(ctx);
    close(fd);
    return http_err_cstr(
      "stdlib.http.HttpNetwork",
      tls_error_message("tls handshake failed", status, &tls)
    );
  }

  status = tls_write_all(ctx, &tls, request_data, request_len);
  if (status == errSSLWouldBlock && tls_status_timed_out(&tls)) {
    SSLDisposeContext(ctx);
    close(fd);
    return http_err0("stdlib.http.HttpTimeout");
  }
  if (status != noErr) {
    SSLDisposeContext(ctx);
    close(fd);
    return http_err_cstr(
      "stdlib.http.HttpNetwork",
      tls_error_message("tls write failed", status, &tls)
    );
  }

  ByteBuf response;
  buf_init(&response);
  status = tls_read_append(ctx, &tls, &response);
  SSLDisposeContext(ctx);
  close(fd);
  if (status == errSSLWouldBlock && tls_status_timed_out(&tls)) {
    free(response.data);
    return http_err0("stdlib.http.HttpTimeout");
  }
  if (status != noErr) {
    free(response.data);
    return http_err_cstr(
      "stdlib.http.HttpNetwork",
      tls_error_message("tls read failed", status, &tls)
    );
  }
  return http_response_result(response.data);
}
#pragma clang diagnostic pop
#endif

static void append_header_block(ByteBuf* out, const char* raw) {
  const char* line = raw;
  while (*line != '\0') {
    const char* end = line;
    while (*end != '\0' && *end != '\n' && *end != '\r') end++;
    const char* content_end = end;
    while (content_end > line && (content_end[-1] == ' ' || content_end[-1] == '\t')) content_end--;
    const char* content_start = line;
    while (content_start < content_end && (*content_start == ' ' || *content_start == '\t')) content_start++;
    if (content_start < content_end) {
      const char* colon = NULL;
      for (const char* p = content_start; p < content_end; p++) {
        if (*p == ':') {
          colon = p;
          break;
        }
      }
      if (colon == NULL) tcp_fail("http_request: headers must be 'Name: Value' lines");
      if (colon == content_start) tcp_fail("http_request: header name cannot be empty");
      buf_append_bytes(out, content_start, (size_t)(content_end - content_start));
      buf_append_cstr(out, "\r\n");
    }
    while (*end == '\r' || *end == '\n') end++;
    line = end;
  }
}

static int send_all(int fd, const char* data, size_t len) {
  while (len > 0) {
    ssize_t wrote = send(fd, data, len, 0);
    if (wrote <= 0) return 0;
    data += wrote;
    len -= (size_t)wrote;
  }
  return 1;
}

long long http_request(const char* method, const char* url, const char* headers_raw, const char* body, long long timeout_ms) {
  if (method == NULL) tcp_fail("http_request: null method");
  if (url == NULL) tcp_fail("http_request: null url");
  if (headers_raw == NULL) tcp_fail("http_request: null headers");
  if (body == NULL) tcp_fail("http_request: null body");
  if (timeout_ms < 1) tcp_fail("http_request: timeout_ms must be >= 1");

  HttpUrl parsed = {0};
  char* url_err = NULL;
  if (!parse_http_url(url, &parsed, &url_err)) {
    long long out = http_err_cstr("stdlib.http.HttpDecode", url_err);
    return out;
  }

  ByteBuf header_block;
  buf_init(&header_block);
  append_header_block(&header_block, headers_raw);

  ByteBuf request;
  buf_init(&request);
  char* method_upper = upper_copy(method);
  buf_append_cstr(&request, method_upper);
  buf_append_cstr(&request, " ");
  buf_append_cstr(&request, parsed.path);
  buf_append_cstr(&request, " HTTP/1.1\r\nHost: ");
  buf_append_cstr(&request, parsed.host);
  buf_append_cstr(&request, "\r\nConnection: close\r\n");
  buf_append_bytes(&request, header_block.data == NULL ? "" : header_block.data, header_block.len);
  char content_len[64];
  snprintf(content_len, sizeof(content_len), "Content-Length: %zu\r\n", strlen(body));
  buf_append_cstr(&request, content_len);
  buf_append_cstr(&request, "\r\n");
  buf_append_cstr(&request, body);

  free(method_upper);
  free(header_block.data);

#ifdef __APPLE__
  if (parsed.use_tls) {
    long long out = http_request_tls(&parsed, request.data, request.len, timeout_ms);
    free(request.data);
    free_http_url(&parsed);
    return out;
  }
#else
  if (parsed.use_tls) {
    free(request.data);
    free_http_url(&parsed);
    return http_err_text(
      "stdlib.http.HttpNetwork",
      "https unsupported on this platform"
    );
  }
#endif

  struct addrinfo hints;
  memset(&hints, 0, sizeof(hints));
  hints.ai_socktype = SOCK_STREAM;
  hints.ai_family = AF_UNSPEC;
  struct addrinfo* infos = NULL;
  int gai = getaddrinfo(parsed.host, parsed.port, &hints, &infos);
  if (gai != 0) {
    free(request.data);
    free_http_url(&parsed);
    return http_err_text("stdlib.http.HttpNetwork", gai_strerror(gai));
  }

  int fd = -1;
  int last_errno = 0;
  for (struct addrinfo* it = infos; it != NULL; it = it->ai_next) {
    fd = socket(it->ai_family, it->ai_socktype, it->ai_protocol);
    if (fd < 0) {
      last_errno = errno;
      continue;
    }
    struct timeval tv;
    tv.tv_sec = (time_t)(timeout_ms / 1000);
    tv.tv_usec = (suseconds_t)((timeout_ms % 1000) * 1000);
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
    setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof(tv));
    if (connect(fd, it->ai_addr, it->ai_addrlen) == 0) break;
    last_errno = errno;
    close(fd);
    fd = -1;
  }
  freeaddrinfo(infos);
  if (fd < 0) {
    free(request.data);
    free_http_url(&parsed);
    if (last_errno == EAGAIN || last_errno == EWOULDBLOCK) return http_err0("stdlib.http.HttpTimeout");
    return http_err_text("stdlib.http.HttpNetwork", strerror(last_errno));
  }

  if (!send_all(fd, request.data, request.len)) {
    int send_errno = errno;
    free(request.data);
    close(fd);
    free_http_url(&parsed);
    if (send_errno == EAGAIN || send_errno == EWOULDBLOCK) return http_err0("stdlib.http.HttpTimeout");
    return http_err_text("stdlib.http.HttpNetwork", strerror(send_errno));
  }
  free(request.data);

  ByteBuf response;
  buf_init(&response);
  while (1) {
    char chunk[4096];
    ssize_t n = recv(fd, chunk, sizeof(chunk), 0);
    if (n == 0) break;
    if (n < 0) {
      int recv_errno = errno;
      int saw_no_response = response.len == 0;
      free(response.data);
      close(fd);
      free_http_url(&parsed);
      if (recv_errno == EAGAIN || recv_errno == EWOULDBLOCK) return http_err0("stdlib.http.HttpTimeout");
      if (recv_errno == ECONNRESET && saw_no_response) {
        return http_err_text(
          "stdlib.http.HttpNetwork",
          "remote closed connection without response"
        );
      }
      return http_err_text("stdlib.http.HttpNetwork", strerror(recv_errno));
    }
    buf_append_bytes(&response, chunk, (size_t)n);
  }
  close(fd);
  free_http_url(&parsed);
  return http_response_result(response.data);
}

long long vector_empty(void) {
  VectorVal* v = sprout_alloc_vector_val("vector_empty: out of memory");
  v->len = 0;
  v->cap = 0;
  v->data = NULL;
  return (long long)(uintptr_t)v;
}

long long vector_length(long long vec) {
  VectorVal* v = (VectorVal*)(uintptr_t)vec;
  if (v == NULL) tcp_fail("vector_length: null vector");
  return v->len;
}

long long vector_get(long long vec, long long index) {
  long long rooted_vec = vec;
  SPROUT_GC_PUSH_I64_LOCAL(rooted_vec);
  VectorVal* v = (VectorVal*)(uintptr_t)vec;
  if (v == NULL) tcp_fail("vector_get: null vector");
  if (index < 0 || index >= v->len) {
    SPROUT_GC_POP_LOCALS(1);
    return sprout_make0(find_ctor_tag_by_name("Nothing"));
  }
  long long out = sprout_make1(find_ctor_tag_by_name("Just"), v->data[index]);
  SPROUT_GC_POP_LOCALS(1);
  return out;
}

long long vector_set(long long vec, long long index, long long value) {
  long long rooted_vec = vec;
  long long rooted_value = value;
  SPROUT_GC_PUSH_I64_LOCAL(rooted_vec);
  SPROUT_GC_PUSH_I64_LOCAL(rooted_value);
  VectorVal* src = (VectorVal*)(uintptr_t)vec;
  if (src == NULL) tcp_fail("vector_set: null vector");
  VectorVal* out = sprout_alloc_vector_val("vector_set: out of memory");
  out->len = src->len;
  out->cap = src->len;
  if (out->cap == 0) {
    out->data = NULL;
    SPROUT_GC_POP_LOCALS(2);
    return (long long)(uintptr_t)out;
  }
  out->data = sprout_alloc_vector_data((size_t)out->cap, "vector_set: out of memory");
  memcpy(out->data, src->data, (size_t)out->len * sizeof(long long));
  if (index >= 0 && index < out->len) {
    out->data[index] = rooted_value;
  }
  SPROUT_GC_POP_LOCALS(2);
  return (long long)(uintptr_t)out;
}

long long vector_append(long long vec, long long value) {
  long long rooted_vec = vec;
  long long rooted_value = value;
  SPROUT_GC_PUSH_I64_LOCAL(rooted_vec);
  SPROUT_GC_PUSH_I64_LOCAL(rooted_value);
  VectorVal* src = (VectorVal*)(uintptr_t)vec;
  if (src == NULL) tcp_fail("vector_append: null vector");
  VectorVal* out = sprout_alloc_vector_val("vector_append: out of memory");
  out->len = src->len + 1;
  out->cap = out->len;
  out->data = sprout_alloc_vector_data((size_t)out->cap, "vector_append: out of memory");
  if (src->len > 0) {
    memcpy(out->data, src->data, (size_t)src->len * sizeof(long long));
  }
  out->data[src->len] = rooted_value;
  SPROUT_GC_POP_LOCALS(2);
  return (long long)(uintptr_t)out;
}

/* Concatenate two vectors into one fresh n+m backing array (two memcpy, no
 * intermediate cons cells). Backs Semigroup (Vec a).append. */
long long vector_concat(long long a, long long b) {
  long long rooted_a = a;
  long long rooted_b = b;
  SPROUT_GC_PUSH_I64_LOCAL(rooted_a);
  SPROUT_GC_PUSH_I64_LOCAL(rooted_b);
  VectorVal* va = (VectorVal*)(uintptr_t)rooted_a;
  VectorVal* vb = (VectorVal*)(uintptr_t)rooted_b;
  if (va == NULL || vb == NULL) tcp_fail("vector_concat: null vector");
  long long na = va->len;
  long long nb = vb->len;
  VectorVal* out = sprout_alloc_vector_val("vector_concat: out of memory");
  out->len = na + nb;
  out->cap = out->len;
  out->data = sprout_alloc_vector_data((size_t)out->cap, "vector_concat: out of memory");
  /* Re-fetch: the two allocations above may have run the collector. */
  va = (VectorVal*)(uintptr_t)rooted_a;
  vb = (VectorVal*)(uintptr_t)rooted_b;
  if (na > 0) memcpy(out->data, va->data, (size_t)na * sizeof(long long));
  if (nb > 0) memcpy(out->data + na, vb->data, (size_t)nb * sizeof(long long));
  SPROUT_GC_POP_LOCALS(2);
  return (long long)(uintptr_t)out;
}

long long vec_make_filled(long long n, long long val) {
  long long rooted_val = val;
  SPROUT_GC_PUSH_I64_LOCAL(rooted_val);
  VectorVal* out = sprout_alloc_vector_val("vec_make_filled: out of memory");
  out->len = n;
  out->cap = n;
  out->data = sprout_alloc_vector_data((size_t)n, "vec_make_filled: out of memory");
  for (long long i = 0; i < n; i++) out->data[i] = rooted_val;
  SPROUT_GC_POP_LOCALS(1);
  return (long long)(uintptr_t)out;
}

long long vector_mutset(long long vec, long long index, long long value) {
  VectorVal* v = (VectorVal*)(uintptr_t)vec;
  if (v == NULL) tcp_fail("vector_mutset: null vector");
  if (index < 0 || index >= v->len) tcp_fail("vector_mutset: index out of bounds");
  v->data[index] = value;
  return 0;
}

long long vector_get_direct(long long vec, long long index) {
  VectorVal* v = (VectorVal*)(uintptr_t)vec;
  if (v == NULL) tcp_fail("vector_get_direct: null vector");
  if (index < 0 || index >= v->len) tcp_fail("vector_get_direct: index out of bounds");
  return v->data[index];
}

long long vector_from_list(long long list_handle) {
  /* Build a Vec<a> from a forward-ordered List<a> in O(n) time and space. */
  long long nil_tag = find_ctor_tag_by_name("Nil");
  /* First pass: count elements (no allocation, no GC risk). */
  long long count = 0;
  long long cur = list_handle;
  while (sprout_tag(cur) != nil_tag) {
    count++;
    cur = sprout_field(cur, 1);
  }
  /* Root the list while we allocate. */
  long long rooted_list = list_handle;
  SPROUT_GC_PUSH_I64_LOCAL(rooted_list);
  VectorVal* v = sprout_alloc_vector_val("vector_from_list: out of memory");
  v->len = count;
  v->cap = count;
  if (count == 0) {
    v->data = NULL;
    SPROUT_GC_POP_LOCALS(1);
    return (long long)(uintptr_t)v;
  }
  v->data = sprout_alloc_vector_data((size_t)count, "vector_from_list: out of memory");
  /* Second pass: fill Vec front-to-back (list head → index 0). */
  cur = rooted_list;
  for (long long i = 0; i < count; i++) {
    v->data[i] = sprout_field(cur, 0);
    cur = sprout_field(cur, 1);
  }
  SPROUT_GC_POP_LOCALS(1);
  return (long long)(uintptr_t)v;
}

/* ─── Persistent AVL BST map ────────────────────────────────────────────────
 * Each BST node is an independent SPROUT_HEAP_MAP managed object.
 * Empty map = handle 0 (no allocation needed).
 * Keys are interned strings (permanent, never freed by map sweep).
 * Subtree size enables O(log n) nth-key/nth-value.
 * Structural sharing: O(log n) new nodes per insert/remove.
 */

static int bst_height(long long h) {
  return h == 0 ? 0 : ((BSTNode*)(uintptr_t)h)->height;
}
static int bst_size(long long h) {
  return h == 0 ? 0 : ((BSTNode*)(uintptr_t)h)->size;
}
static void bst_update(BSTNode* n) {
  int lh = bst_height(n->left), rh = bst_height(n->right);
  n->height = 1 + (lh > rh ? lh : rh);
  n->size   = 1 + bst_size(n->left) + bst_size(n->right);
}
static int bst_bf(long long h) {
  if (h == 0) return 0;
  BSTNode* n = (BSTNode*)(uintptr_t)h;
  return bst_height(n->left) - bst_height(n->right);
}

/* forward declaration — bst_balance calls both rotations */
static long long bst_rotate_left(long long h);

static long long bst_rotate_right(long long h) {
  /* Creates 2 new nodes; caller must ensure h is GC-reachable. */
  long long a = h, b = ((BSTNode*)(uintptr_t)a)->left;
  long long b_right = ((BSTNode*)(uintptr_t)b)->right;
  long long a_right = ((BSTNode*)(uintptr_t)a)->right;
  long long b_left  = ((BSTNode*)(uintptr_t)b)->left;
  long long new_a = 0, new_b = 0;
  SPROUT_GC_PUSH_I64_LOCAL(a);       SPROUT_GC_PUSH_I64_LOCAL(b);
  SPROUT_GC_PUSH_I64_LOCAL(b_right); SPROUT_GC_PUSH_I64_LOCAL(a_right);
  SPROUT_GC_PUSH_I64_LOCAL(b_left);  SPROUT_GC_PUSH_I64_LOCAL(new_a);
  SPROUT_GC_PUSH_I64_LOCAL(new_b);
  { BSTNode* na = sprout_alloc_bst_node("bst_rotate_right: oom");
    BSTNode* oa = (BSTNode*)(uintptr_t)a;
    na->key = oa->key; na->value = oa->value;
    na->left = b_right; na->right = a_right;
    bst_update(na); new_a = (long long)(uintptr_t)na; }
  { BSTNode* nb = sprout_alloc_bst_node("bst_rotate_right: oom");
    BSTNode* ob = (BSTNode*)(uintptr_t)b;
    nb->key = ob->key; nb->value = ob->value;
    nb->left = b_left; nb->right = new_a;
    bst_update(nb); new_b = (long long)(uintptr_t)nb; }
  SPROUT_GC_POP_LOCALS(7);
  return new_b;
}

static long long bst_rotate_left(long long h) {
  long long a = h, b = ((BSTNode*)(uintptr_t)a)->right;
  long long b_left  = ((BSTNode*)(uintptr_t)b)->left;
  long long a_left  = ((BSTNode*)(uintptr_t)a)->left;
  long long b_right = ((BSTNode*)(uintptr_t)b)->right;
  long long new_a = 0, new_b = 0;
  SPROUT_GC_PUSH_I64_LOCAL(a);      SPROUT_GC_PUSH_I64_LOCAL(b);
  SPROUT_GC_PUSH_I64_LOCAL(b_left); SPROUT_GC_PUSH_I64_LOCAL(a_left);
  SPROUT_GC_PUSH_I64_LOCAL(b_right); SPROUT_GC_PUSH_I64_LOCAL(new_a);
  SPROUT_GC_PUSH_I64_LOCAL(new_b);
  { BSTNode* na = sprout_alloc_bst_node("bst_rotate_left: oom");
    BSTNode* oa = (BSTNode*)(uintptr_t)a;
    na->key = oa->key; na->value = oa->value;
    na->left = a_left; na->right = b_left;
    bst_update(na); new_a = (long long)(uintptr_t)na; }
  { BSTNode* nb = sprout_alloc_bst_node("bst_rotate_left: oom");
    BSTNode* ob = (BSTNode*)(uintptr_t)b;
    nb->key = ob->key; nb->value = ob->value;
    nb->left = new_a; nb->right = b_right;
    bst_update(nb); new_b = (long long)(uintptr_t)nb; }
  SPROUT_GC_POP_LOCALS(7);
  return new_b;
}

static long long bst_balance(long long h) {
  if (h == 0) return 0;
  long long rooted_h = h;
  SPROUT_GC_PUSH_I64_LOCAL(rooted_h);
  int bf = bst_bf(rooted_h);
  long long result;
  if (bf > 1) {
    long long left_h = ((BSTNode*)(uintptr_t)rooted_h)->left;
    if (bst_bf(left_h) < 0) { /* LR case */
      long long new_left = 0, new_h = 0;
      SPROUT_GC_PUSH_I64_LOCAL(new_left); SPROUT_GC_PUSH_I64_LOCAL(new_h);
      new_left = bst_rotate_left(left_h);
      { BSTNode* na = sprout_alloc_bst_node("bst_balance: oom");
        BSTNode* old = (BSTNode*)(uintptr_t)rooted_h;
        na->key = old->key; na->value = old->value;
        na->left = new_left; na->right = old->right;
        bst_update(na); new_h = (long long)(uintptr_t)na; }
      result = bst_rotate_right(new_h);
      SPROUT_GC_POP_LOCALS(2);
    } else { /* LL case */
      result = bst_rotate_right(rooted_h);
    }
  } else if (bf < -1) {
    long long right_h = ((BSTNode*)(uintptr_t)rooted_h)->right;
    if (bst_bf(right_h) > 0) { /* RL case */
      long long new_right = 0, new_h = 0;
      SPROUT_GC_PUSH_I64_LOCAL(new_right); SPROUT_GC_PUSH_I64_LOCAL(new_h);
      new_right = bst_rotate_right(right_h);
      { BSTNode* na = sprout_alloc_bst_node("bst_balance: oom");
        BSTNode* old = (BSTNode*)(uintptr_t)rooted_h;
        na->key = old->key; na->value = old->value;
        na->left = old->left; na->right = new_right;
        bst_update(na); new_h = (long long)(uintptr_t)na; }
      result = bst_rotate_left(new_h);
      SPROUT_GC_POP_LOCALS(2);
    } else { /* RR case */
      result = bst_rotate_left(rooted_h);
    }
  } else {
    result = rooted_h;
  }
  SPROUT_GC_POP_LOCALS(1);
  return result;
}

static long long bst_insert_node(long long h, const char* ikey, long long value) {
  if (h == 0) { /* allocate leaf */
    long long rv = value, new_h = 0;
    SPROUT_GC_PUSH_I64_LOCAL(rv); SPROUT_GC_PUSH_I64_LOCAL(new_h);
    BSTNode* n = sprout_alloc_bst_node("bst_insert: oom");
    n->key = ikey; n->value = rv; n->left = 0; n->right = 0;
    n->height = 1; n->size = 1;
    new_h = (long long)(uintptr_t)n;
    SPROUT_GC_POP_LOCALS(2);
    return new_h;
  }
  BSTNode* node = (BSTNode*)(uintptr_t)h;
  int cmp = strcmp(ikey, node->key);
  if (cmp == 0) { /* update value, same structure */
    long long rh = h, rv = value, new_h = 0;
    SPROUT_GC_PUSH_I64_LOCAL(rh); SPROUT_GC_PUSH_I64_LOCAL(rv); SPROUT_GC_PUSH_I64_LOCAL(new_h);
    BSTNode* n = sprout_alloc_bst_node("bst_insert: oom");
    BSTNode* old = (BSTNode*)(uintptr_t)rh;
    n->key = old->key; n->value = rv; n->left = old->left; n->right = old->right;
    n->height = old->height; n->size = old->size;
    new_h = (long long)(uintptr_t)n;
    SPROUT_GC_POP_LOCALS(3);
    return new_h;
  }
  long long rh = h, rv = value, new_child = 0, new_h = 0;
  SPROUT_GC_PUSH_I64_LOCAL(rh); SPROUT_GC_PUSH_I64_LOCAL(rv);
  SPROUT_GC_PUSH_I64_LOCAL(new_child); SPROUT_GC_PUSH_I64_LOCAL(new_h);
  if (cmp < 0) {
    new_child = bst_insert_node(((BSTNode*)(uintptr_t)rh)->left, ikey, rv);
    BSTNode* n = sprout_alloc_bst_node("bst_insert: oom");
    BSTNode* old = (BSTNode*)(uintptr_t)rh;
    n->key = old->key; n->value = old->value; n->left = new_child; n->right = old->right;
    bst_update(n); new_h = (long long)(uintptr_t)n;
  } else {
    new_child = bst_insert_node(((BSTNode*)(uintptr_t)rh)->right, ikey, rv);
    BSTNode* n = sprout_alloc_bst_node("bst_insert: oom");
    BSTNode* old = (BSTNode*)(uintptr_t)rh;
    n->key = old->key; n->value = old->value; n->left = old->left; n->right = new_child;
    bst_update(n); new_h = (long long)(uintptr_t)n;
  }
  long long balanced = bst_balance(new_h);
  SPROUT_GC_POP_LOCALS(4);
  return balanced;
}

static BSTNode* bst_find_min(long long h) {
  /* Pure read — no allocation; h must be GC-reachable at call site. */
  BSTNode* n = (BSTNode*)(uintptr_t)h;
  while (n && n->left) n = (BSTNode*)(uintptr_t)n->left;
  return n;
}

static long long bst_remove_min(long long h) {
  if (h == 0) return 0;
  BSTNode* node = (BSTNode*)(uintptr_t)h;
  if (node->left == 0) return node->right;
  long long rh = h, new_left = 0, new_h = 0;
  SPROUT_GC_PUSH_I64_LOCAL(rh); SPROUT_GC_PUSH_I64_LOCAL(new_left); SPROUT_GC_PUSH_I64_LOCAL(new_h);
  new_left = bst_remove_min(((BSTNode*)(uintptr_t)rh)->left);
  BSTNode* n = sprout_alloc_bst_node("bst_remove_min: oom");
  BSTNode* old = (BSTNode*)(uintptr_t)rh;
  n->key = old->key; n->value = old->value; n->left = new_left; n->right = old->right;
  bst_update(n); new_h = (long long)(uintptr_t)n;
  long long balanced = bst_balance(new_h);
  SPROUT_GC_POP_LOCALS(3);
  return balanced;
}

static long long bst_remove_node(long long h, const char* ikey) {
  if (h == 0) return 0;
  BSTNode* node = (BSTNode*)(uintptr_t)h;
  int cmp = strcmp(ikey, node->key);
  if (cmp == 0) {
    if (node->left == 0) return node->right;
    if (node->right == 0) return node->left;
    /* two children: replace with in-order successor */
    long long rh = h, new_right = 0, new_h = 0;
    SPROUT_GC_PUSH_I64_LOCAL(rh); SPROUT_GC_PUSH_I64_LOCAL(new_right); SPROUT_GC_PUSH_I64_LOCAL(new_h);
    BSTNode* succ = bst_find_min(((BSTNode*)(uintptr_t)rh)->right);
    const char* succ_key = succ->key;
    long long succ_value = succ->value; /* alive: reachable from rh */
    new_right = bst_remove_min(((BSTNode*)(uintptr_t)rh)->right);
    BSTNode* n = sprout_alloc_bst_node("bst_remove: oom");
    BSTNode* old = (BSTNode*)(uintptr_t)rh;
    n->key = succ_key; n->value = succ_value; n->left = old->left; n->right = new_right;
    bst_update(n); new_h = (long long)(uintptr_t)n;
    long long balanced = bst_balance(new_h);
    SPROUT_GC_POP_LOCALS(3);
    return balanced;
  }
  long long rh = h, new_child = 0, new_h = 0;
  SPROUT_GC_PUSH_I64_LOCAL(rh); SPROUT_GC_PUSH_I64_LOCAL(new_child); SPROUT_GC_PUSH_I64_LOCAL(new_h);
  if (cmp < 0) {
    new_child = bst_remove_node(((BSTNode*)(uintptr_t)rh)->left, ikey);
    BSTNode* n = sprout_alloc_bst_node("bst_remove: oom");
    BSTNode* old = (BSTNode*)(uintptr_t)rh;
    n->key = old->key; n->value = old->value; n->left = new_child; n->right = old->right;
    bst_update(n); new_h = (long long)(uintptr_t)n;
  } else {
    new_child = bst_remove_node(((BSTNode*)(uintptr_t)rh)->right, ikey);
    BSTNode* n = sprout_alloc_bst_node("bst_remove: oom");
    BSTNode* old = (BSTNode*)(uintptr_t)rh;
    n->key = old->key; n->value = old->value; n->left = old->left; n->right = new_child;
    bst_update(n); new_h = (long long)(uintptr_t)n;
  }
  long long balanced = bst_balance(new_h);
  SPROUT_GC_POP_LOCALS(3);
  return balanced;
}

static long long bst_get(long long h, const char* key) {
  while (h != 0) {
    BSTNode* node = (BSTNode*)(uintptr_t)h;
    int cmp = strcmp(key, node->key);
    if (cmp == 0) return node->value;
    h = (cmp < 0) ? node->left : node->right;
  }
  return LLONG_MIN; /* sentinel: not found */
}

static BSTNode* bst_nth_node(long long h, long long n) {
  while (h != 0) {
    BSTNode* node = (BSTNode*)(uintptr_t)h;
    long long ls = (long long)bst_size(node->left);
    if (n < ls) { h = node->left; }
    else if (n == ls) { return node; }
    else { n -= ls + 1; h = node->right; }
  }
  return NULL;
}

/* In-order accumulation: appends BST keys (sorted) to tail, returning full list. */
static long long bst_to_list_acc(long long h, long long tail) {
  if (h == 0) return tail;
  long long rh = h, rtail = tail, acc = 0;
  SPROUT_GC_PUSH_I64_LOCAL(rh); SPROUT_GC_PUSH_I64_LOCAL(rtail); SPROUT_GC_PUSH_I64_LOCAL(acc);
  acc = bst_to_list_acc(((BSTNode*)(uintptr_t)rh)->right, rtail);
  { BSTNode* node = (BSTNode*)(uintptr_t)rh;
    long long kv = (long long)(uintptr_t)node->key;
    long long cons = sprout_make2(find_ctor_tag_by_name("Cons"), kv, acc);
    acc = cons; }
  long long result = bst_to_list_acc(((BSTNode*)(uintptr_t)rh)->left, acc);
  SPROUT_GC_POP_LOCALS(3);
  return result;
}

long long map_empty(void) {
  return 0; /* empty BST = null handle */
}

long long map_get(long long map_h, long long key_val) {
  const char* key = (const char*)key_val;
  if (key == NULL) tcp_fail("map_get: null key");
  long long rm = map_h;
  SPROUT_GC_PUSH_I64_LOCAL(rm);
  long long found = bst_get(rm, key);
  long long out = (found == LLONG_MIN)
    ? sprout_make0(find_ctor_tag_by_name("Nothing"))
    : sprout_make1(find_ctor_tag_by_name("Just"), found);
  SPROUT_GC_POP_LOCALS(1);
  return out;
}

long long map_set(long long map_h, long long key_val, long long value) {
  const char* key = (const char*)key_val;
  if (key == NULL) tcp_fail("map_set: null key");
  const char* ikey = intern_string(key); /* intern before any GC-triggering alloc */
  long long rm = map_h, rv = value;
  SPROUT_GC_PUSH_I64_LOCAL(rm); SPROUT_GC_PUSH_I64_LOCAL(rv);
  long long result = bst_insert_node(rm, ikey, rv);
  SPROUT_GC_POP_LOCALS(2);
  return result;
}

long long map_remove(long long map_h, long long key_val) {
  const char* key = (const char*)key_val;
  if (key == NULL) tcp_fail("map_remove: null key");
  const char* ikey = intern_string(key);
  long long rm = map_h;
  SPROUT_GC_PUSH_I64_LOCAL(rm);
  long long result = bst_remove_node(rm, ikey);
  SPROUT_GC_POP_LOCALS(1);
  return result;
}

long long map_size(long long map_h) {
  return (long long)bst_size(map_h);
}

long long map_nth_key(long long map_h, long long index) {
  long long rm = map_h;
  SPROUT_GC_PUSH_I64_LOCAL(rm);
  BSTNode* node = bst_nth_node(rm, index);
  long long out = (node == NULL)
    ? sprout_make0(find_ctor_tag_by_name("Nothing"))
    : sprout_make1(find_ctor_tag_by_name("Just"), (long long)(uintptr_t)node->key);
  SPROUT_GC_POP_LOCALS(1);
  return out;
}

long long map_nth_value(long long map_h, long long index) {
  long long rm = map_h;
  SPROUT_GC_PUSH_I64_LOCAL(rm);
  BSTNode* node = bst_nth_node(rm, index);
  long long out = (node == NULL)
    ? sprout_make0(find_ctor_tag_by_name("Nothing"))
    : sprout_make1(find_ctor_tag_by_name("Just"), node->value);
  SPROUT_GC_POP_LOCALS(1);
  return out;
}

/* ── NativeSet ─────────────────────────────────────────────────────────────
   Backed by the persistent BST with value=0 (dummy). Keys are interned.
   SPROUT_HEAP_MAP nodes; GC sees value/left/right as children.
*/

long long native_set_empty(void) {
  return 0; /* empty BST */
}

long long native_set_insert(long long item_val, long long set_h) {
  const char* item = (const char*)item_val;
  if (item == NULL) tcp_fail("native_set_insert: null item");
  const char* ikey = intern_string(item);
  long long rs = set_h;
  SPROUT_GC_PUSH_I64_LOCAL(rs);
  if (bst_get(rs, ikey) != LLONG_MIN) { /* already present */
    SPROUT_GC_POP_LOCALS(1);
    return rs;
  }
  long long result = bst_insert_node(rs, ikey, 0LL);
  SPROUT_GC_POP_LOCALS(1);
  return result;
}

long long native_set_member(long long item_val, long long set_h) {
  const char* item = (const char*)item_val;
  if (item == NULL) return 0;
  return bst_get(set_h, item) != LLONG_MIN ? 1 : 0;
}

long long native_set_to_list(long long set_h) {
  long long rs = set_h;
  SPROUT_GC_PUSH_I64_LOCAL(rs);
  long long list_nil = sprout_make0(find_ctor_tag_by_name("Nil"));
  long long result = bst_to_list_acc(rs, list_nil);
  SPROUT_GC_POP_LOCALS(1);
  return result;
}

SproutUnboxed3 native_set_to_list_unboxed(long long set_h) {
  long long list = native_set_to_list(set_h);
  long long tag = sprout_tag(list);
  long long nil_tag = find_ctor_tag_by_name("Nil");
  if (tag == nil_tag)
    return (SproutUnboxed3){ tag, 0, 0 };
  return (SproutUnboxed3){ tag, sprout_field(list, 0), sprout_field(list, 1) };
}

long long native_set_size(long long set_h) {
  return (long long)bst_size(set_h);
}

long long bytes_empty(void) {
  BytesVal* out = sprout_alloc_bytes_val("bytes_empty: out of memory");
  out->len = 0;
  out->data = NULL;
  return (long long)(uintptr_t)out;
}

long long bytes_length(long long bytes_h) {
  BytesVal* value = (BytesVal*)(uintptr_t)bytes_h;
  if (value == NULL) tcp_fail("bytes_length: null bytes");
  return (long long)value->len;
}

long long bytes_get(long long bytes_h, long long index) {
  BytesVal* value = (BytesVal*)(uintptr_t)bytes_h;
  if (value == NULL) tcp_fail("bytes_get: null bytes");
  if (index < 0 || (size_t)index >= value->len) {
    return sprout_make0(find_ctor_tag_by_name("Nothing"));
  }
  return sprout_make1(find_ctor_tag_by_name("Just"), (long long)value->data[index]);
}

long long bytes_slice(long long bytes_h, long long start, long long count) {
  long long rooted_bytes = bytes_h;
  SPROUT_GC_PUSH_I64_LOCAL(rooted_bytes);
  BytesVal* value = (BytesVal*)(uintptr_t)bytes_h;
  if (value == NULL) tcp_fail("bytes_slice: null bytes");
  if (start < 0 || count < 0) tcp_fail("bytes_slice: start/count must be >= 0");
  size_t s = (size_t)start;
  size_t c = (size_t)count;
  if (s > value->len) s = value->len;
  if (s + c > value->len) c = value->len - s;
  BytesVal* out = sprout_alloc_bytes_val("bytes_slice: out of memory");
  out->len = c;
  out->data = sprout_alloc_bytes_data(c, "bytes_slice: out of memory");
  if (c > 0) memcpy(out->data, value->data + s, c);
  SPROUT_GC_POP_LOCALS(1);
  return (long long)(uintptr_t)out;
}

long long bytes_append(long long left_h, long long right_h) {
  long long rooted_left = left_h;
  long long rooted_right = right_h;
  SPROUT_GC_PUSH_I64_LOCAL(rooted_left);
  SPROUT_GC_PUSH_I64_LOCAL(rooted_right);
  BytesVal* left = (BytesVal*)(uintptr_t)left_h;
  BytesVal* right = (BytesVal*)(uintptr_t)right_h;
  if (left == NULL || right == NULL) tcp_fail("bytes_append: null bytes");
  BytesVal* out = sprout_alloc_bytes_val("bytes_append: out of memory");
  out->len = left->len + right->len;
  out->data = sprout_alloc_bytes_data(out->len, "bytes_append: out of memory");
  if (left->len > 0) memcpy(out->data, left->data, left->len);
  if (right->len > 0) memcpy(out->data + left->len, right->data, right->len);
  SPROUT_GC_POP_LOCALS(2);
  return (long long)(uintptr_t)out;
}

long long bytes_singleton(long long value) {
  if (value < 0 || value > 255) tcp_fail("bytes_singleton: byte out of range");
  BytesVal* out = sprout_alloc_bytes_val("bytes_singleton: out of memory");
  out->len = 1;
  out->data = sprout_alloc_bytes_data(1, "bytes_singleton: out of memory");
  out->data[0] = (unsigned char)value;
  return (long long)(uintptr_t)out;
}

long long bytes_from_utf8(long long raw_val) {
  const char* raw = (const char*)raw_val;
  if (raw == NULL) tcp_fail("bytes_from_utf8: null input");
  size_t len = strlen(raw);
  BytesVal* out = sprout_alloc_bytes_val("bytes_from_utf8: out of memory");
  out->len = len;
  out->data = sprout_alloc_bytes_data(len, "bytes_from_utf8: out of memory");
  if (len > 0) memcpy(out->data, raw, len);
  return (long long)(uintptr_t)out;
}

static int utf8_validate(const unsigned char* data, size_t len, const char** reason) {
  size_t i = 0;
  while (i < len) {
    unsigned char b0 = data[i];
    if (b0 == 0) {
      *reason = "decoded string contains NUL byte";
      return 0;
    }
    if (b0 <= 0x7F) {
      i += 1;
      continue;
    }
    if ((b0 & 0xE0) == 0xC0) {
      if (i + 1 >= len) { *reason = "truncated UTF-8 sequence"; return 0; }
      unsigned char b1 = data[i + 1];
      if ((b1 & 0xC0) != 0x80 || b0 < 0xC2) { *reason = "invalid UTF-8 sequence"; return 0; }
      i += 2;
      continue;
    }
    if ((b0 & 0xF0) == 0xE0) {
      if (i + 2 >= len) { *reason = "truncated UTF-8 sequence"; return 0; }
      unsigned char b1 = data[i + 1];
      unsigned char b2 = data[i + 2];
      if ((b1 & 0xC0) != 0x80 || (b2 & 0xC0) != 0x80) { *reason = "invalid UTF-8 sequence"; return 0; }
      if ((b0 == 0xE0 && b1 < 0xA0) || (b0 == 0xED && b1 >= 0xA0)) { *reason = "invalid UTF-8 sequence"; return 0; }
      i += 3;
      continue;
    }
    if ((b0 & 0xF8) == 0xF0) {
      if (i + 3 >= len) { *reason = "truncated UTF-8 sequence"; return 0; }
      unsigned char b1 = data[i + 1];
      unsigned char b2 = data[i + 2];
      unsigned char b3 = data[i + 3];
      if ((b1 & 0xC0) != 0x80 || (b2 & 0xC0) != 0x80 || (b3 & 0xC0) != 0x80) {
        *reason = "invalid UTF-8 sequence";
        return 0;
      }
      if ((b0 == 0xF0 && b1 < 0x90) || (b0 == 0xF4 && b1 >= 0x90) || b0 > 0xF4) {
        *reason = "invalid UTF-8 sequence";
        return 0;
      }
      i += 4;
      continue;
    }
    *reason = "invalid UTF-8 sequence";
    return 0;
  }
  return 1;
}

long long bytes_to_utf8(long long bytes_h) {
  long long rooted_bytes = bytes_h;
  SPROUT_GC_PUSH_I64_LOCAL(rooted_bytes);
  BytesVal* value = (BytesVal*)(uintptr_t)bytes_h;
  if (value == NULL) tcp_fail("bytes_to_utf8: null bytes");
  const char* reason = NULL;
  if (!utf8_validate(value->data, value->len, &reason)) {
    char* message = dup_managed_cstr(reason, "bytes_to_utf8: out of memory");
    SPROUT_HANDLE(h_message, (long long)(uintptr_t)message);
    long long err = sprout_make1(
      find_ctor_tag_by_name("stdlib.bytes.Utf8DecodeError"),
      sprout_handle_get(h_message)
    );
    SPROUT_GC_PUSH_I64_LOCAL(err);
    long long out = sprout_make1(find_ctor_tag_by_name("Err"), err);
    SPROUT_GC_POP_LOCALS(2);
    return out;
  }
  size_t vlen = value->len;
  char* out = sprout_gc_alloc_cstr(vlen, "bytes_to_utf8: out of memory");
  if (vlen > 0) memcpy(out, value->data, vlen);
  out[vlen] = '\0';  SPROUT_HANDLE(h_out, (long long)(uintptr_t)out);
  long long result = sprout_make1(find_ctor_tag_by_name("Ok"), sprout_handle_get(h_out));
  SPROUT_GC_POP_LOCALS(1);
  return result;
}

static BytesVal* bytes_from_chunk_bytes(const unsigned char* data, size_t len, const char* ctx) {
  BytesVal* out = sprout_alloc_bytes_val(ctx);
  out->len = len;
  out->data = sprout_alloc_bytes_data(len, ctx);
  if (len > 0) memcpy(out->data, data, len);
  return out;
}

static BuilderVal* builder_alloc(size_t len, size_t count) {
  BuilderVal* out = sprout_alloc_builder_val("bytes_builder: out of memory");
  out->len = len;
  out->count = count;
  out->chunks = sprout_alloc_builder_chunks(count, "bytes_builder: out of memory");
  return out;
}

static long long sprout_div_floor(long long left, long long right) {
  long long q = left / right;
  long long r = left % right;
  if (r != 0 && ((r > 0) != (right > 0))) q -= 1;
  return q;
}

long long bytes_builder_empty(void) {
  return (long long)(uintptr_t)builder_alloc(0, 0);
}

long long bytes_builder_bytes(long long bytes_h) {
  long long rooted_bytes = bytes_h;
  SPROUT_GC_PUSH_I64_LOCAL(rooted_bytes);
  BytesVal* value = (BytesVal*)(uintptr_t)bytes_h;
  if (value == NULL) tcp_fail("bytes_builder_bytes: null bytes");
  BuilderVal* out = builder_alloc(value->len, value->len == 0 ? 0 : 1);
  if (out->count == 1) out->chunks[0] = value;
  SPROUT_GC_POP_LOCALS(1);
  return (long long)(uintptr_t)out;
}

long long bytes_builder_byte(long long value) {
  if (value < 0 || value > 255) tcp_fail("bytes_builder_byte: byte out of range");
  unsigned char data[1] = {(unsigned char)value};
  BytesVal* chunk = bytes_from_chunk_bytes(data, 1, "bytes_builder_byte: out of memory");
  SPROUT_GC_PUSH_PTR_LOCAL(chunk);
  BuilderVal* out = builder_alloc(1, 1);
  out->chunks[0] = chunk;
  SPROUT_GC_POP_LOCALS(1);
  return (long long)(uintptr_t)out;
}

static unsigned char builder_mod_256(long long value) {
  long long q = sprout_div_floor(value, 256);
  return (unsigned char)(value - q * 256);
}

long long bytes_builder_u16_be(long long value) {
  unsigned char data[2];
  data[0] = builder_mod_256(sprout_div_floor(value, 256));
  data[1] = builder_mod_256(value);
  BytesVal* chunk = bytes_from_chunk_bytes(data, 2, "bytes_builder_u16_be: out of memory");
  SPROUT_GC_PUSH_PTR_LOCAL(chunk);
  BuilderVal* out = builder_alloc(2, 1);
  out->chunks[0] = chunk;
  SPROUT_GC_POP_LOCALS(1);
  return (long long)(uintptr_t)out;
}

long long bytes_builder_u32_be(long long value) {
  unsigned char data[4];
  data[0] = builder_mod_256(sprout_div_floor(value, 16777216));
  data[1] = builder_mod_256(sprout_div_floor(value, 65536));
  data[2] = builder_mod_256(sprout_div_floor(value, 256));
  data[3] = builder_mod_256(value);
  BytesVal* chunk = bytes_from_chunk_bytes(data, 4, "bytes_builder_u32_be: out of memory");
  SPROUT_GC_PUSH_PTR_LOCAL(chunk);
  BuilderVal* out = builder_alloc(4, 1);
  out->chunks[0] = chunk;
  SPROUT_GC_POP_LOCALS(1);
  return (long long)(uintptr_t)out;
}

long long bytes_builder_append(long long left_h, long long right_h) {
  long long rooted_left = left_h;
  long long rooted_right = right_h;
  SPROUT_GC_PUSH_I64_LOCAL(rooted_left);
  SPROUT_GC_PUSH_I64_LOCAL(rooted_right);
  BuilderVal* left = (BuilderVal*)(uintptr_t)left_h;
  BuilderVal* right = (BuilderVal*)(uintptr_t)right_h;
  if (left == NULL || right == NULL) tcp_fail("bytes_builder_append: null builder");
  if (left->count == 0) {
    SPROUT_GC_POP_LOCALS(2);
    return right_h;
  }
  if (right->count == 0) {
    SPROUT_GC_POP_LOCALS(2);
    return left_h;
  }
  BuilderVal* out = builder_alloc(left->len + right->len, left->count + right->count);
  for (size_t i = 0; i < left->count; i++) out->chunks[i] = left->chunks[i];
  for (size_t i = 0; i < right->count; i++) out->chunks[left->count + i] = right->chunks[i];
  SPROUT_GC_POP_LOCALS(2);
  return (long long)(uintptr_t)out;
}

long long bytes_builder_build(long long builder_h) {
  long long rooted_builder = builder_h;
  SPROUT_GC_PUSH_I64_LOCAL(rooted_builder);
  BuilderVal* value = (BuilderVal*)(uintptr_t)builder_h;
  if (value == NULL) tcp_fail("bytes_builder_build: null builder");
  BytesVal* out = sprout_alloc_bytes_val("bytes_builder_build: out of memory");
  out->len = value->len;
  out->data = sprout_alloc_bytes_data(out->len, "bytes_builder_build: out of memory");
  size_t offset = 0;
  for (size_t i = 0; i < value->count; i++) {
    BytesVal* chunk = value->chunks[i];
    if (chunk == NULL) tcp_fail("bytes_builder_build: null chunk");
    if (chunk->len > 0) memcpy(out->data + offset, chunk->data, chunk->len);
    offset += chunk->len;
  }
  SPROUT_GC_POP_LOCALS(1);
  return (long long)(uintptr_t)out;
}

static uint32_t crypto_rotr32(uint32_t x, uint32_t n) {
  return (x >> n) | (x << (32 - n));
}

static uint32_t crypto_ch(uint32_t x, uint32_t y, uint32_t z) {
  return (x & y) ^ (~x & z);
}

static uint32_t crypto_maj(uint32_t x, uint32_t y, uint32_t z) {
  return (x & y) ^ (x & z) ^ (y & z);
}

static uint32_t crypto_sig0(uint32_t x) {
  return crypto_rotr32(x, 7) ^ crypto_rotr32(x, 18) ^ (x >> 3);
}

static uint32_t crypto_sig1(uint32_t x) {
  return crypto_rotr32(x, 17) ^ crypto_rotr32(x, 19) ^ (x >> 10);
}

static uint32_t crypto_ep0(uint32_t x) {
  return crypto_rotr32(x, 2) ^ crypto_rotr32(x, 13) ^ crypto_rotr32(x, 22);
}

static uint32_t crypto_ep1(uint32_t x) {
  return crypto_rotr32(x, 6) ^ crypto_rotr32(x, 11) ^ crypto_rotr32(x, 25);
}

typedef struct {
  uint32_t state[8];
  uint64_t bitlen;
  unsigned char data[64];
  size_t datalen;
} Sha256Ctx;

static const uint32_t SHA256_K[64] = {
  0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5,
  0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
  0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3,
  0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
  0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc,
  0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
  0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7,
  0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
  0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13,
  0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
  0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3,
  0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
  0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5,
  0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
  0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208,
  0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
};

static void sha256_transform(Sha256Ctx* ctx, const unsigned char data[64]) {
  uint32_t m[64];
  for (size_t i = 0; i < 16; i++) {
    m[i] =
      ((uint32_t)data[i * 4] << 24) |
      ((uint32_t)data[i * 4 + 1] << 16) |
      ((uint32_t)data[i * 4 + 2] << 8) |
      ((uint32_t)data[i * 4 + 3]);
  }
  for (size_t i = 16; i < 64; i++) {
    m[i] = crypto_sig1(m[i - 2]) + m[i - 7] + crypto_sig0(m[i - 15]) + m[i - 16];
  }

  uint32_t a = ctx->state[0];
  uint32_t b = ctx->state[1];
  uint32_t c = ctx->state[2];
  uint32_t d = ctx->state[3];
  uint32_t e = ctx->state[4];
  uint32_t f = ctx->state[5];
  uint32_t g = ctx->state[6];
  uint32_t h = ctx->state[7];

  for (size_t i = 0; i < 64; i++) {
    uint32_t t1 = h + crypto_ep1(e) + crypto_ch(e, f, g) + SHA256_K[i] + m[i];
    uint32_t t2 = crypto_ep0(a) + crypto_maj(a, b, c);
    h = g;
    g = f;
    f = e;
    e = d + t1;
    d = c;
    c = b;
    b = a;
    a = t1 + t2;
  }

  ctx->state[0] += a;
  ctx->state[1] += b;
  ctx->state[2] += c;
  ctx->state[3] += d;
  ctx->state[4] += e;
  ctx->state[5] += f;
  ctx->state[6] += g;
  ctx->state[7] += h;
}

static void sha256_init(Sha256Ctx* ctx) {
  ctx->bitlen = 0;
  ctx->datalen = 0;
  ctx->state[0] = 0x6a09e667;
  ctx->state[1] = 0xbb67ae85;
  ctx->state[2] = 0x3c6ef372;
  ctx->state[3] = 0xa54ff53a;
  ctx->state[4] = 0x510e527f;
  ctx->state[5] = 0x9b05688c;
  ctx->state[6] = 0x1f83d9ab;
  ctx->state[7] = 0x5be0cd19;
}

static void sha256_update(Sha256Ctx* ctx, const unsigned char* data, size_t len) {
  for (size_t i = 0; i < len; i++) {
    ctx->data[ctx->datalen++] = data[i];
    if (ctx->datalen == 64) {
      sha256_transform(ctx, ctx->data);
      ctx->bitlen += 512;
      ctx->datalen = 0;
    }
  }
}

static void sha256_final(Sha256Ctx* ctx, unsigned char out[32]) {
  size_t i = ctx->datalen;
  ctx->bitlen += (uint64_t)ctx->datalen * 8;
  ctx->data[i++] = 0x80;

  if (i > 56) {
    while (i < 64) ctx->data[i++] = 0x00;
    sha256_transform(ctx, ctx->data);
    i = 0;
  }

  while (i < 56) ctx->data[i++] = 0x00;
  for (int j = 7; j >= 0; j--) {
    ctx->data[i++] = (unsigned char)((ctx->bitlen >> (j * 8)) & 0xff);
  }
  sha256_transform(ctx, ctx->data);

  for (size_t j = 0; j < 8; j++) {
    out[j * 4] = (unsigned char)((ctx->state[j] >> 24) & 0xff);
    out[j * 4 + 1] = (unsigned char)((ctx->state[j] >> 16) & 0xff);
    out[j * 4 + 2] = (unsigned char)((ctx->state[j] >> 8) & 0xff);
    out[j * 4 + 3] = (unsigned char)(ctx->state[j] & 0xff);
  }
}

static void sha256_digest(const unsigned char* data, size_t len, unsigned char out[32]) {
  Sha256Ctx ctx;
  sha256_init(&ctx);
  sha256_update(&ctx, data, len);
  sha256_final(&ctx, out);
}

static void hmac_sha256_digest(const unsigned char* key, size_t key_len, const unsigned char* msg, size_t msg_len, unsigned char out[32]) {
  unsigned char key_block[64];
  unsigned char inner[32];
  unsigned char key_hash[32];
  if (key_len > 64) {
    sha256_digest(key, key_len, key_hash);
    key = key_hash;
    key_len = 32;
  }
  memset(key_block, 0, sizeof(key_block));
  if (key_len > 0) memcpy(key_block, key, key_len);
  for (size_t i = 0; i < 64; i++) key_block[i] ^= 0x36;

  Sha256Ctx ctx;
  sha256_init(&ctx);
  sha256_update(&ctx, key_block, 64);
  sha256_update(&ctx, msg, msg_len);
  sha256_final(&ctx, inner);

  memset(key_block, 0, sizeof(key_block));
  memcpy(key_block, key, key_len);
  for (size_t i = 0; i < 64; i++) key_block[i] ^= 0x5c;
  sha256_init(&ctx);
  sha256_update(&ctx, key_block, 64);
  sha256_update(&ctx, inner, 32);
  sha256_final(&ctx, out);
}

static char* base64_encode_bytes(const unsigned char* data, size_t len) {
  static const char table[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
  size_t out_len = 4 * ((len + 2) / 3);
  char* out = (char*)malloc(out_len + 1);
  if (out == NULL) return NULL;
  size_t j = 0;
  for (size_t i = 0; i < len;) {
    size_t remaining = len - i;
    unsigned char a = data[i++];
    unsigned char b = remaining > 1 ? data[i++] : 0;
    unsigned char c = remaining > 2 ? data[i++] : 0;
    out[j++] = table[(a >> 2) & 0x3f];
    out[j++] = table[((a & 0x03) << 4) | (b >> 4)];
    out[j++] = remaining > 1 ? table[((b & 0x0f) << 2) | (c >> 6)] : '=';
    out[j++] = remaining > 2 ? table[c & 0x3f] : '=';
  }
  out[j] = '\0';
  return out;
}

static int base64_value(char c) {
  if (c >= 'A' && c <= 'Z') return c - 'A';
  if (c >= 'a' && c <= 'z') return c - 'a' + 26;
  if (c >= '0' && c <= '9') return c - '0' + 52;
  if (c == '+') return 62;
  if (c == '/') return 63;
  return -1;
}

static int base64_decode_bytes(const char* text, unsigned char** out_data, size_t* out_len, const char** err) {
  size_t len = strlen(text);
  if (len % 4 != 0) {
    *err = "invalid base64 length";
    return 0;
  }

  size_t size = len / 4 * 3;
  if (len >= 1 && text[len - 1] == '=') size--;
  if (len >= 2 && text[len - 2] == '=') size--;

  unsigned char* out = size == 0 ? NULL : (unsigned char*)sprout_alloc_counted(
    &g_debug_alloc_bytes,
    size,
    "crypto_base64_decode: out of memory"
  );
  if (size > 0 && out == NULL) {
    *err = "out of memory";
    return 0;
  }

  size_t j = 0;
  for (size_t i = 0; i < len; i += 4) {
    char c0 = text[i];
    char c1 = text[i + 1];
    char c2 = text[i + 2];
    char c3 = text[i + 3];
    int v0 = base64_value(c0);
    int v1 = base64_value(c1);
    if (v0 < 0 || v1 < 0) {
      free(out);
      *err = "invalid base64 character";
      return 0;
    }
    if (c2 == '=') {
      if (c3 != '=') {
        free(out);
        *err = "invalid base64 padding";
        return 0;
      }
      if (i + 4 != len) {
        free(out);
        *err = "invalid base64 padding";
        return 0;
      }
      out[j++] = (unsigned char)((v0 << 2) | (v1 >> 4));
      break;
    }
    int v2 = base64_value(c2);
    if (v2 < 0) {
      free(out);
      *err = "invalid base64 character";
      return 0;
    }
    if (c3 == '=') {
      if (i + 4 != len) {
        free(out);
        *err = "invalid base64 padding";
        return 0;
      }
      out[j++] = (unsigned char)((v0 << 2) | (v1 >> 4));
      out[j++] = (unsigned char)(((v1 & 0x0f) << 4) | (v2 >> 2));
      break;
    }
    int v3 = base64_value(c3);
    if (v3 < 0) {
      free(out);
      *err = "invalid base64 character";
      return 0;
    }
    out[j++] = (unsigned char)((v0 << 2) | (v1 >> 4));
    out[j++] = (unsigned char)(((v1 & 0x0f) << 4) | (v2 >> 2));
    out[j++] = (unsigned char)(((v2 & 0x03) << 6) | v3);
  }

  *out_data = out;
  *out_len = size;
  return 1;
}

static long long crypto_err1(const char* ctor_name, const char* payload) {
  char* owned = dup_managed_cstr(payload, "crypto: out of memory");
  SPROUT_HANDLE(h_payload, (long long)(uintptr_t)owned);
  long long err = sprout_make1(find_ctor_tag_by_name(ctor_name), sprout_handle_get(h_payload));
  SPROUT_GC_PUSH_I64_LOCAL(err);
  long long out = sprout_make1(find_ctor_tag_by_name("Err"), err);
  SPROUT_GC_POP_LOCALS(1);
  return out;
}

static long long crypto_err2(const char* ctor_name, long long a0, long long a1) {
  long long rooted_a0 = a0;
  long long rooted_a1 = a1;
  SPROUT_GC_PUSH_I64_LOCAL(rooted_a0);
  SPROUT_GC_PUSH_I64_LOCAL(rooted_a1);
  long long err = sprout_make2(find_ctor_tag_by_name(ctor_name), a0, a1);
  SPROUT_GC_PUSH_I64_LOCAL(err);
  long long out = sprout_make1(find_ctor_tag_by_name("Err"), err);
  SPROUT_GC_POP_LOCALS(3);
  return out;
}

long long crypto_sha256(long long bytes_h) {
  long long rooted_bytes = bytes_h;
  SPROUT_GC_PUSH_I64_LOCAL(rooted_bytes);
  BytesVal* value = (BytesVal*)(uintptr_t)bytes_h;
  if (value == NULL) tcp_fail("crypto_sha256: null bytes");
  unsigned char digest[32];
  sha256_digest(value->data, value->len, digest);
  BytesVal* out = bytes_from_chunk_bytes(digest, 32, "crypto_sha256: out of memory");
  SPROUT_GC_POP_LOCALS(1);
  return (long long)(uintptr_t)out;
}

long long crypto_hmac_sha256(long long key_h, long long msg_h) {
  long long rooted_key = key_h;
  long long rooted_msg = msg_h;
  SPROUT_GC_PUSH_I64_LOCAL(rooted_key);
  SPROUT_GC_PUSH_I64_LOCAL(rooted_msg);
  BytesVal* key = (BytesVal*)(uintptr_t)key_h;
  BytesVal* msg = (BytesVal*)(uintptr_t)msg_h;
  if (key == NULL || msg == NULL) tcp_fail("crypto_hmac_sha256: null bytes");
  unsigned char digest[32];
  hmac_sha256_digest(key->data, key->len, msg->data, msg->len, digest);
  BytesVal* out = bytes_from_chunk_bytes(digest, 32, "crypto_hmac_sha256: out of memory");
  SPROUT_GC_POP_LOCALS(2);
  return (long long)(uintptr_t)out;
}

long long crypto_base64_encode(long long bytes_h) {
  long long rooted_bytes_h = bytes_h;
  SPROUT_GC_PUSH_I64_LOCAL(rooted_bytes_h);
  BytesVal* value = (BytesVal*)(uintptr_t)rooted_bytes_h;
  if (value == NULL) tcp_fail("crypto_base64_encode: null bytes");
  sprout_gc_maybe_collect_threshold();
  char* plain = base64_encode_bytes(value->data, value->len);
  if (plain == NULL) tcp_fail("crypto_base64_encode: out of memory");
  size_t plain_len = strlen(plain);
  char* out = sprout_gc_adopt_cstr(plain, plain_len, "crypto_base64_encode: out of memory");  SPROUT_GC_POP_LOCALS(1);
  return (long long)(uintptr_t)out;
}

long long crypto_base64_decode(const char* raw) {
  if (raw == NULL) tcp_fail("crypto_base64_decode: null input");
  unsigned char* data = NULL;
  size_t len = 0;
  const char* err = NULL;
  if (!base64_decode_bytes(raw, &data, &len, &err)) {
    return crypto_err1("stdlib.crypto.Base64DecodeError", err);
  }
  BytesVal* out = sprout_alloc_bytes_val("crypto_base64_decode: out of memory");
  SPROUT_GC_PUSH_PTR_LOCAL(out);
  out->len = len;
  out->data = len == 0 ? NULL : data;
  long long result = sprout_make1(find_ctor_tag_by_name("Ok"), (long long)(uintptr_t)out);
  SPROUT_GC_POP_LOCALS(1);
  return result;
}

long long crypto_bytes_xor(long long left_h, long long right_h) {
  long long rooted_left = left_h;
  long long rooted_right = right_h;
  SPROUT_GC_PUSH_I64_LOCAL(rooted_left);
  SPROUT_GC_PUSH_I64_LOCAL(rooted_right);
  BytesVal* left = (BytesVal*)(uintptr_t)left_h;
  BytesVal* right = (BytesVal*)(uintptr_t)right_h;
  if (left == NULL || right == NULL) tcp_fail("crypto_bytes_xor: null bytes");
  if (left->len != right->len) {
    SPROUT_GC_POP_LOCALS(2);
    return crypto_err2("stdlib.crypto.BytesXorLengthMismatch", (long long)left->len, (long long)right->len);
  }
  BytesVal* out = sprout_alloc_bytes_val("crypto_bytes_xor: out of memory");
  SPROUT_GC_PUSH_PTR_LOCAL(out);
  out->len = left->len;
  out->data = sprout_alloc_bytes_data(out->len, "crypto_bytes_xor: out of memory");
  for (size_t i = 0; i < out->len; i++) out->data[i] = left->data[i] ^ right->data[i];
  long long result = sprout_make1(find_ctor_tag_by_name("Ok"), (long long)(uintptr_t)out);
  SPROUT_GC_POP_LOCALS(3);
  return result;
}

long long crypto_random_bytes(long long count) {
  if (count < 0) {
    return crypto_err1("stdlib.crypto.CryptoInvalidArgument", "count must be >= 0");
  }
  size_t len = (size_t)count;
  BytesVal* out = sprout_alloc_bytes_val("crypto_random_bytes: out of memory");
  SPROUT_GC_PUSH_PTR_LOCAL(out);
  out->len = len;
  out->data = sprout_alloc_bytes_data(len, "crypto_random_bytes: out of memory");
  if (len > 0) {
    FILE* fp = fopen("/dev/urandom", "rb");
    if (fp == NULL) {
      SPROUT_GC_POP_LOCALS(1);
      return crypto_err1("stdlib.crypto.CryptoUnavailable", strerror(errno));
    }
    size_t got = fread(out->data, 1, len, fp);
    if (got != len || ferror(fp)) {
      int saved_errno = errno;
      fclose(fp);
      SPROUT_GC_POP_LOCALS(1);
      return crypto_err1(
        "stdlib.crypto.CryptoUnavailable",
        saved_errno != 0 ? strerror(saved_errno) : "failed to read random bytes"
      );
    }
    fclose(fp);
  }
  long long result = sprout_make1(find_ctor_tag_by_name("Ok"), (long long)(uintptr_t)out);
  SPROUT_GC_POP_LOCALS(1);
  return result;
}

/* L0.3 I/O parking: sockets are made non-blocking so a would-block read/write/
 * accept returns EAGAIN, at which point the tcp_* builtins park the current green
 * task (scheduler_park_on_fd) and let siblings run instead of freezing the OS thread.
 * A single-task program (no with_scope) parks into the always-ready pump, which
 * blocks in the poller — behaviorally identical to a blocking call. */
static void tcp_set_nonblocking(int fd) {
  int flags = fcntl(fd, F_GETFL, 0);
  if (flags < 0) tcp_fail("tcp: fcntl F_GETFL failed");
  if (fcntl(fd, F_SETFL, flags | O_NONBLOCK) < 0) tcp_fail("tcp: fcntl F_SETFL failed");
}

long long tcp_listen(long long port) {
  if (port < 1 || port > 65535) tcp_fail("tcp_listen: port out of range");
  int fd = socket(AF_INET, SOCK_STREAM, 0);
  if (fd < 0) tcp_fail("tcp_listen: socket failed");
  int one = 1;
  if (setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one)) < 0) {
    close(fd);
    tcp_fail("tcp_listen: setsockopt failed");
  }
  struct sockaddr_in addr;
  memset(&addr, 0, sizeof(addr));
  addr.sin_family = AF_INET;
  addr.sin_port = htons((unsigned short)port);
  addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
  if (bind(fd, (struct sockaddr*)&addr, sizeof(addr)) < 0) {
    close(fd);
    tcp_fail("tcp_listen: bind failed");
  }
  if (listen(fd, 16) < 0) {
    close(fd);
    tcp_fail("tcp_listen: listen failed");
  }
  tcp_set_nonblocking(fd);   /* so tcp_accept parks on EAGAIN rather than blocking */
  long long h = alloc_listener_handle();
  if (h < 0) {
    close(fd);
    tcp_fail("tcp_listen: handle table full");
  }
  g_listener_fd[h] = fd;
  g_listener_used[h] = 1;
  return h;
}

static long long tcp_net_ok(long long payload) {
  long long rooted_payload = payload;
  SPROUT_GC_PUSH_I64_LOCAL(rooted_payload);
  long long out = sprout_make1(find_ctor_tag_by_name("Ok"), payload);
  SPROUT_GC_POP_LOCALS(1);
  return out;
}

static long long tcp_net_err0(const char* ctor_name) {
  long long err = sprout_make0(find_ctor_tag_by_name(ctor_name));
  SPROUT_GC_PUSH_I64_LOCAL(err);
  long long out = sprout_make1(find_ctor_tag_by_name("Err"), err);
  SPROUT_GC_POP_LOCALS(1);
  return out;
}

static long long tcp_net_err1(const char* ctor_name, long long payload) {
  long long rooted_payload = payload;
  SPROUT_GC_PUSH_I64_LOCAL(rooted_payload);
  long long err = sprout_make1(find_ctor_tag_by_name(ctor_name), payload);
  SPROUT_GC_PUSH_I64_LOCAL(err);
  long long out = sprout_make1(find_ctor_tag_by_name("Err"), err);
  SPROUT_GC_POP_LOCALS(2);
  return out;
}

long long tcp_connect(const char* host, long long port) {
  if (host == NULL) tcp_fail("tcp_connect: null host");
  if (port < 1 || port > 65535) {
    return tcp_net_err1(
      "stdlib.net.TcpInvalidArgument",
      (long long)(uintptr_t)"port must be in 1..65535"
    );
  }
  char port_buf[16];
  snprintf(port_buf, sizeof(port_buf), "%lld", port);
  struct addrinfo hints;
  memset(&hints, 0, sizeof(hints));
  hints.ai_family = AF_UNSPEC;
  hints.ai_socktype = SOCK_STREAM;
  struct addrinfo* resolved = NULL;
  int gai = getaddrinfo(host, port_buf, &hints, &resolved);
  if (gai != 0) {
    return tcp_net_err1("stdlib.net.TcpConnectFailed", (long long)(uintptr_t)gai_strerror(gai));
  }

  int fd = -1;
  const char* error_msg = "connect failed";
  for (struct addrinfo* it = resolved; it != NULL; it = it->ai_next) {
    fd = socket(it->ai_family, it->ai_socktype, it->ai_protocol);
    if (fd < 0) continue;
    /* connect() stays BLOCKING (set O_NONBLOCK only after it succeeds): a
     * non-blocking connect returns EINPROGRESS and would need a write-readiness
     * park; loopback/typical connects complete promptly. */
    if (connect(fd, it->ai_addr, it->ai_addrlen) == 0) {
      error_msg = NULL;
      break;
    }
    error_msg = strerror(errno);
    close(fd);
    fd = -1;
  }
  freeaddrinfo(resolved);
  if (fd >= 0) tcp_set_nonblocking(fd);   /* steady-state read/write parks on EAGAIN */

  if (fd < 0) {
    return tcp_net_err1("stdlib.net.TcpConnectFailed", (long long)(uintptr_t)error_msg);
  }

  long long h = alloc_conn_handle();
  if (h < 0) {
    close(fd);
    return tcp_net_err1("stdlib.net.TcpConnectFailed", (long long)(uintptr_t)"connection table full");
  }
  g_conn_fd[h] = fd;
  g_conn_used[h] = 1;
  return tcp_net_ok(h);
}

long long tcp_accept(long long listener) {
  if (listener <= 0 || listener >= 2048 || !g_listener_used[listener]) {
    tcp_fail("tcp_accept: unknown listener handle");
  }
  int fd;
  for (;;) {
    fd = accept(g_listener_fd[listener], NULL, NULL);
    if (fd >= 0) break;
    if (errno == EAGAIN || errno == EWOULDBLOCK) {
      scheduler_park_on_fd(g_listener_fd[listener], SPROUT_POLL_READ);
      continue;
    }
    tcp_fail("tcp_accept: accept failed");
  }
  tcp_set_nonblocking(fd);   /* accepted conn parks on EAGAIN */
  long long h = alloc_conn_handle();
  if (h < 0) {
    close(fd);
    tcp_fail("tcp_accept: connection table full");
  }
  g_conn_fd[h] = fd;
  g_conn_used[h] = 1;
  return h;
}

long long tcp_read(long long conn) {
  if (conn <= 0 || conn >= 2048 || !g_conn_used[conn]) tcp_fail("tcp_read: unknown connection handle");
  sprout_gc_maybe_collect_threshold();
  char* buf = (char*)malloc(65537);
  if (buf == NULL) tcp_fail("tcp_read: out of memory");
  ssize_t n;
  for (;;) {
    n = recv(g_conn_fd[conn], buf, 65536, 0);
    if (n >= 0) break;
    if (errno == EAGAIN || errno == EWOULDBLOCK) {   /* no unrooted GC temp held */
      scheduler_park_on_fd(g_conn_fd[conn], SPROUT_POLL_READ);
      continue;
    }
    free(buf);  /* plain malloc buffer, free is correct */
    tcp_fail("tcp_read: recv failed");
  }
  buf[n] = '\0';
  char* head = sprout_gc_adopt_cstr(buf, (size_t)n, "tcp_read: out of memory");  return (long long)(uintptr_t)head;
}

/* Recoverable sibling of tcp_read: read whatever is available (up to 64 KiB) and return it as
 * Ok(String) — Ok("") on a peer EOF — or Err(TcpError) on a socket failure, NEVER exit(1). The
 * recoverable socket family (tcp_connect / tcp_read_exact / tcp_write_all) had no
 * read-WHATEVER'S-AVAILABLE member: tcp_read_exact blocks for an exact byte count, unusable for
 * reading an HTTP header block whose length is unknown until "\r\n\r\n" is seen. A server needs
 * this so one client's connection reset drops that connection, not the whole process. */
long long tcp_read_avail(long long conn) {
  if (conn <= 0 || conn >= 2048 || !g_conn_used[conn])
    return tcp_net_err0("stdlib.net.TcpInvalidHandle");
  sprout_gc_maybe_collect_threshold();
  char* buf = (char*)malloc(65537);
  if (buf == NULL) tcp_fail("tcp_read_avail: out of memory");
  ssize_t n;
  for (;;) {
    n = recv(g_conn_fd[conn], buf, 65536, 0);
    if (n >= 0) break;
    if (errno == EAGAIN || errno == EWOULDBLOCK) {   /* no unrooted GC temp held */
      scheduler_park_on_fd(g_conn_fd[conn], SPROUT_POLL_READ);
      continue;
    }
    int saved_errno = errno;   /* capture before free(), which may clobber errno */
    free(buf);                 /* plain malloc buffer, free is correct */
    return tcp_net_err1("stdlib.net.TcpReadFailed", (long long)(uintptr_t)strerror(saved_errno));
  }
  buf[n] = '\0';
  /* adopt then wrap: no allocation between the adopt and tcp_net_ok, which roots the payload
   * across its own Ok-box allocation. */
  char* head = sprout_gc_adopt_cstr(buf, (size_t)n, "tcp_read_avail: out of memory");
  return tcp_net_ok((long long)(uintptr_t)head);
}

long long tcp_read_exact(long long conn, long long count) {
  if (conn <= 0 || conn >= 2048 || !g_conn_used[conn]) return tcp_net_err0("stdlib.net.TcpInvalidHandle");
  if (count < 0) {
    return tcp_net_err1(
      "stdlib.net.TcpInvalidArgument",
      (long long)(uintptr_t)"count must be >= 0"
    );
  }
  BytesVal* out = sprout_alloc_bytes_val("tcp_read_exact: out of memory");
  SPROUT_GC_PUSH_PTR_LOCAL(out);
  out->len = (size_t)count;
  out->data = sprout_alloc_bytes_data((size_t)count, "tcp_read_exact: out of memory");
  size_t received = 0;
  while (received < (size_t)count) {
    ssize_t n = recv(g_conn_fd[conn], out->data + received, (size_t)count - received, 0);
    if (n == 0) {
      SPROUT_GC_POP_LOCALS(1);
      return tcp_net_err0("stdlib.net.TcpEndOfStream");
    }
    if (n < 0) {
      if (errno == EAGAIN || errno == EWOULDBLOCK) {   /* out is rooted across the park */
        scheduler_park_on_fd(g_conn_fd[conn], SPROUT_POLL_READ);
        continue;
      }
      SPROUT_GC_POP_LOCALS(1);
      return tcp_net_err1("stdlib.net.TcpReadFailed", (long long)(uintptr_t)strerror(errno));
    }
    received += (size_t)n;
  }
  long long result = tcp_net_ok((long long)(uintptr_t)out);
  SPROUT_GC_POP_LOCALS(1);
  return result;
}

long long tcp_write(long long conn, const char* payload) {
  if (conn <= 0 || conn >= 2048 || !g_conn_used[conn]) tcp_fail("tcp_write: unknown connection handle");
  if (payload == NULL) tcp_fail("tcp_write: null payload");
  size_t len = strlen(payload);
  const char* p = payload;
  while (len > 0) {
    ssize_t n = send(g_conn_fd[conn], p, len, 0);
    if (n < 0) {
      if (errno == EAGAIN || errno == EWOULDBLOCK) {
        scheduler_park_on_fd(g_conn_fd[conn], SPROUT_POLL_WRITE);
        continue;
      }
      tcp_fail("tcp_write: send failed");
    }
    if (n == 0) tcp_fail("tcp_write: send failed");
    p += n;
    len -= (size_t)n;
  }
  return 0;
}

long long tcp_write_all(long long conn, long long payload_h) {
  if (conn <= 0 || conn >= 2048 || !g_conn_used[conn]) return tcp_net_err0("stdlib.net.TcpInvalidHandle");
  BytesVal* payload = (BytesVal*)(uintptr_t)payload_h;
  if (payload == NULL) tcp_fail("tcp_write_all: null payload");
  size_t len = payload->len;
  const unsigned char* p = payload->data;
  while (len > 0) {
    ssize_t n = send(g_conn_fd[conn], p, len, 0);
    if (n < 0) {
      if (errno == EAGAIN || errno == EWOULDBLOCK) {   /* payload reachable via caller roots */
        scheduler_park_on_fd(g_conn_fd[conn], SPROUT_POLL_WRITE);
        continue;
      }
      return tcp_net_err1("stdlib.net.TcpWriteFailed", (long long)(uintptr_t)strerror(errno));
    }
    if (n == 0) {
      return tcp_net_err1("stdlib.net.TcpWriteFailed", (long long)(uintptr_t)strerror(errno));
    }
    p += n;
    len -= (size_t)n;
  }
  return tcp_net_ok((long long)payload->len);
}

long long tcp_close(long long conn) {
  if (conn <= 0 || conn >= 2048 || !g_conn_used[conn]) tcp_fail("tcp_close: unknown connection handle");
  close(g_conn_fd[conn]);
  g_conn_used[conn] = 0;
  g_conn_fd[conn] = -1;
  return 0;
}

long long tcp_close_listener(long long listener) {
  if (listener <= 0 || listener >= 2048 || !g_listener_used[listener]) {
    tcp_fail("tcp_close_listener: unknown listener handle");
  }
  close(g_listener_fd[listener]);
  g_listener_used[listener] = 0;
  g_listener_fd[listener] = -1;
  return 0;
}

long long tcp_echo_serve(long long port, long long max_connections) {
  if (max_connections < 1) tcp_fail("tcp_echo_serve: max_connections must be >= 1");
  long long listener = tcp_listen(port);
  long long served = 0;
  while (served < max_connections) {
    long long conn = tcp_accept(listener);
    long long payload_i = tcp_read(conn);
    const char* payload = (const char*)(uintptr_t)payload_i;
    tcp_write(conn, payload);
    tcp_close(conn);
    served++;
  }
  tcp_close_listener(listener);
  return 0;
}

/* L0.1 structured-concurrency builtins (__scope_open/__scope_join/__scope_spawn
 * /task_yield) live in the cooperative-scheduler TU, runtime/sprout_scheduler.c. */
