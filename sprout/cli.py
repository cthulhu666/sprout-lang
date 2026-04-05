from __future__ import annotations

import argparse
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile

from .analysis_bridge import default_analysis_service_cmd
from .analysis_cli import cmd_analysis_cli
from .analysis_bridge_runtime import (
    render_analysis_bridge_helpers_c,
    render_analysis_bridge_runtime_c,
)
from .analysis_contract import (
    OP_CHECK_SOURCE,
    OP_COMPLETE_IN_STATE,
    OP_DECLARED_NAMES_IN_SOURCE,
    OP_DIAGNOSTICS_IN_SOURCE,
    OP_EVAL_EXPR_IN_SOURCE,
    OP_EXPORTED_NAMES_IN_SOURCE,
    OP_INSTANCES_IN_SOURCE,
    OP_SYMBOL_INVENTORY_IN_SOURCE,
    OP_SYMBOL_LOCATIONS_IN_SOURCE,
    OP_TYPE_OF_IN_SOURCE,
)
from . import ast
from .ast import to_dict
from .codegen_llvm import CodegenError, compile_to_llvm
from .elaborate import ElaborateError
from .formatter import format_source, lint_source
from .interpreter import RuntimeError, run_program
from .module_loader import CompilerWarning, ModuleLoadError, load_module_bundle, resolve_program_names
from .parser import ParseError, parse
from .repl import cmd_repl
from .surface_checks import SurfaceCheckError, validate_public_surface
from .stdlib import with_http_prelude, with_prelude
from .tokenizer import TokenizeError
from .typeclass_lowering import TypeclassLoweringError, lower_typeclasses
from .typechecker import TypeCheckError, typecheck_program


def _bundle_has_implicit_prelude(bundle: object | None) -> bool:
    if bundle is None:
        return False
    return any(path.name == "prelude.sprout" and "stdlib" in path.parts for path in bundle.modules)


def _print_warnings(warnings: list[CompilerWarning]) -> None:
    for warning in warnings:
        print(
            f"warning: {warning.path}:{warning.line}:{warning.column}: {warning.message}",
            file=sys.stderr,
        )


def _missing_entrypoint_message(entry_main_name: str) -> str:
    return f"Executable entrypoint `{entry_main_name}` is missing"


def _entrypoint_arity_message(entry_main_name: str) -> str:
    return f"Executable entrypoint `{entry_main_name}` must take zero arguments"


def _entrypoint_type_message(entry_main_name: str, actual_type: str) -> str:
    return f"Executable entrypoint `{entry_main_name}` must have type Unit !{{IO}}, got {actual_type}"


def _validate_executable_entrypoint(program: ast.Program, typed: dict[str, str], entry_main_name: str) -> None:
    main_type = typed.get(entry_main_name)
    if main_type is None:
        raise TypeCheckError(_missing_entrypoint_message(entry_main_name))
    main_decl = next(
        (
            decl
            for decl in program.declarations
            if isinstance(decl, ast.FnDecl) and decl.name == entry_main_name
        ),
        None,
    )
    if main_decl is None:
        raise TypeCheckError(_missing_entrypoint_message(entry_main_name))
    if len(main_decl.params) != 0:
        raise TypeCheckError(_entrypoint_arity_message(entry_main_name))
    if main_type != "Unit !{IO}":
        raise TypeCheckError(_entrypoint_type_message(entry_main_name, main_type))


def cmd_parse(path: Path) -> int:
    bundle = load_module_bundle(path)
    source = bundle.source
    tree = parse(source)
    _print_warnings(resolve_program_names(tree, bundle))
    print(json.dumps(to_dict(tree), indent=2))
    return 0


def cmd_fmt(path: Path, check: bool = False) -> int:
    source = path.read_text(encoding="utf-8")
    formatted = format_source(source)
    current = source if source.endswith("\n") else source + "\n"
    if check:
        if formatted != current:
            print(f"needs formatting: {path}")
            return 1
        print("ok")
        return 0
    if formatted != current:
        path.write_text(formatted, encoding="utf-8")
        print(f"formatted {path}")
        return 0
    print(f"already formatted {path}")
    return 0


def cmd_lint(path: Path) -> int:
    issues = lint_source(path.read_text(encoding="utf-8"))
    if not issues:
        print("ok")
        return 0
    for issue in issues:
        print(f"{path}:{issue.line}:{issue.column}: {issue.message}")
    return 1


def cmd_check(path: Path, with_stdlib: bool = False, with_http_stdlib: bool = False) -> int:
    bundle = load_module_bundle(path)
    source = bundle.source
    if with_http_stdlib:
        source = with_http_prelude(source)
        bundle = None
    elif with_stdlib and not _bundle_has_implicit_prelude(bundle):
        source = with_prelude(source)
        bundle = None
    tree = parse(source)
    if bundle is not None:
        _print_warnings(resolve_program_names(tree, bundle))
    validate_public_surface(tree, bundle)
    typed = typecheck_program(tree)
    validate_public_surface(tree, bundle)
    print("ok")
    for name in sorted(typed.keys()):
        print(f"{name}: {typed[name]}")
    return 0


def cmd_run(
    path: Path,
    with_stdlib: bool = False,
    with_http_stdlib: bool = False,
    program_args: list[str] | None = None,
) -> int:
    bundle = load_module_bundle(path)
    source = bundle.source
    if with_http_stdlib:
        source = with_http_prelude(source)
        bundle = None
    elif with_stdlib and not _bundle_has_implicit_prelude(bundle):
        source = with_prelude(source)
        bundle = None
    tree = parse(source)
    if bundle is not None:
        _print_warnings(resolve_program_names(tree, bundle))
    validate_public_surface(tree, bundle)
    typecheck_program(tree)
    validate_public_surface(tree, bundle)
    lowered = lower_typeclasses(tree)
    entry_main_name = "main"
    if bundle is not None:
        entry_info = bundle.modules[path.resolve()]
        if entry_info.header.module is not None:
            entry_main_name = f"{entry_info.header.module}.main"
    typed = typecheck_program(lowered)
    _validate_executable_entrypoint(lowered, typed, entry_main_name)
    run_program(lowered, argv=program_args)
    return 0


def cmd_compile(
    path: Path,
    out: Path,
    with_stdlib: bool = False,
    with_http_stdlib: bool = False,
    native: bool = False,
) -> int:
    bundle = load_module_bundle(path)
    source = bundle.source
    if with_http_stdlib:
        source = with_http_prelude(source)
        bundle = None
    elif with_stdlib and not _bundle_has_implicit_prelude(bundle):
        source = with_prelude(source)
        bundle = None
    tree = parse(source)
    if bundle is not None:
        _print_warnings(resolve_program_names(tree, bundle))
    validate_public_surface(tree, bundle)
    typecheck_program(tree)
    validate_public_surface(tree, bundle)
    lowered = lower_typeclasses(tree)
    entry_main_name = "main"
    if bundle is not None:
        entry_info = bundle.modules[path.resolve()]
        if entry_info.header.module is not None:
            entry_main_name = f"{entry_info.header.module}.main"
    typed = typecheck_program(lowered)
    _validate_executable_entrypoint(lowered, typed, entry_main_name)
    llvm_ir = compile_to_llvm(lowered, entry_main_name=entry_main_name)

    if not native:
        out.write_text(llvm_ir, encoding="utf-8")
        return 0

    clang = shutil.which("clang")
    if clang is None:
        raise CodegenError("clang not found; install clang or compile with --emit-llvm only")

    with tempfile.NamedTemporaryFile("w", suffix=".ll", delete=False, encoding="utf-8") as tmp:
        tmp.write(llvm_ir)
        ll_path = Path(tmp.name)
    embedded_analysis_service_cmd = default_analysis_service_cmd()
    runtime_c = """#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <regex.h>
#include <sys/socket.h>
#include <sys/types.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <netdb.h>
#include <errno.h>
#include <sys/time.h>
#include <sys/wait.h>
#include <signal.h>
#include <termios.h>
#include <unistd.h>
#ifdef __APPLE__
#pragma clang diagnostic push
#pragma clang diagnostic ignored "-Wdeprecated-declarations"
#include <Security/SecureTransport.h>
#include <Security/SecTrust.h>
#include <Security/SecCertificate.h>
#endif

typedef struct {
  long long tag;
  long long f0;
  long long f1;
  long long f2;
} SproutObj;

typedef enum {
  SPROUT_HEAP_OBJ = 1,
  SPROUT_HEAP_CLOSURE = 2,
  SPROUT_HEAP_VECTOR = 3,
  SPROUT_HEAP_MAP = 4,
  SPROUT_HEAP_BYTES = 5,
  SPROUT_HEAP_BUILDER = 6,
  SPROUT_HEAP_TUPLE = 7,
  SPROUT_HEAP_RANGE = 8
} SproutHeapKind;

typedef struct ManagedNode {
  void* ptr;
  SproutHeapKind kind;
  size_t aux_slots;
  int marked;
  struct ManagedNode* next;
  struct ManagedNode* hash_next;
} ManagedNode;

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

typedef struct {
  long long tag;
  const char* name;
  long long arity;
} CtorMeta;

typedef struct {
  long long len;
  long long cap;
  long long* data;
} VectorVal;

typedef struct {
  char* key;
  long long value;
} MapEntry;

typedef struct {
  long long len;
  long long cap;
  MapEntry* entries;
} MapVal;

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
  char* host;
  char* port;
  char* path;
  int use_tls;
} HttpUrl;

static ManagedNode* g_heap_nodes = NULL;
static ManagedNode* g_heap_index[131071];
static RootNode* g_root_nodes = NULL;
static RootNode* g_temp_root_nodes = NULL;
static SproutObj* g_nothing_singleton = NULL;
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
static long long g_gc_marked_count = 0;

static void tcp_fail(const char* msg);
long long sprout_make0(long long tag);
long long sprout_make1(long long tag, long long a0);
long long sprout_tag(long long h);
long long sprout_field(long long h, long long idx);
static CtorMeta* find_ctor(long long tag);
static char* alloc_cstr(size_t len, const char* ctx);
static char* dup_slice(const char* start, size_t len);
static char* dup_cstr(const char* s);
static void buf_init(ByteBuf* buf);
static void buf_append_bytes(ByteBuf* buf, const char* data, size_t len);
static void buf_append_cstr(ByteBuf* buf, const char* text);
static void buf_append_char(ByteBuf* buf, char ch);
static void regex_builtin_fail(const char* builtin, const char* detail);
static int regex_compile_ere(const char* pattern, regex_t* out_regex, char** out_error);
static size_t sprout_utf8_char_width(unsigned char lead);
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

static int sprout_debug_alloc_truthy(const char* value) {
  if (value == NULL || value[0] == '\\0') return 0;
  if (strcmp(value, "0") == 0) return 0;
  if (strcmp(value, "false") == 0) return 0;
  if (strcmp(value, "off") == 0) return 0;
  return 1;
}

static void sprout_debug_alloc_report(void) {
  if (!g_debug_alloc_enabled) return;
  fprintf(
    stderr,
    "[sprout alloc] sprout_obj=%lld closure=%lld vector=%lld map=%lld bytes=%lld builder=%lld gc_swept=%lld\\n",
    g_debug_alloc_sprout_obj,
    g_debug_alloc_closure,
    g_debug_alloc_vector,
    g_debug_alloc_map,
    g_debug_alloc_bytes,
    g_debug_alloc_builder,
    g_debug_gc_swept
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
  if (raw == NULL || raw[0] == '\\0') return;
  if (!sprout_debug_alloc_truthy(raw)) {
    g_gc_threshold = 0;
    return;
  }
  char* end = NULL;
  long long parsed = strtoll(raw, &end, 10);
  if (end == raw || *end != '\\0' || parsed <= 0) {
    tcp_fail("SPROUT_GC_THRESHOLD: expected positive integer");
  }
  g_gc_threshold = parsed;
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
    "[sprout gc] cycle=%lld reason=%s threshold=%lld heap_before=%lld heap_after=%lld live=%lld roots=%lld marked=%lld alloc_since_gc=%lld swept=%lld elapsed_us=%lld\\n",
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
}

static void sprout_gc_maybe_collect_threshold(void) {
  if (g_gc_threshold <= 0) return;
  if (g_gc_active) return;
  if (g_managed_heap_count < g_gc_threshold) return;
  sprout_gc_collect_with_reason("threshold");
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

static char* sprout_strdup_counted(long long* counter, const char* text, const char* ctx) {
  size_t len = strlen(text);
  char* out = (char*)sprout_alloc_counted(counter, len + 1, ctx);
  memcpy(out, text, len + 1);
  return out;
}

static long long box_ptr(SproutObj* p) {
  return (long long)(uintptr_t)p;
}

static SproutObj* unbox_ptr(long long h) {
  return (SproutObj*)(uintptr_t)h;
}

static size_t sprout_managed_ptr_hash(void* ptr) {
  uintptr_t value = (uintptr_t)ptr;
  value ^= value >> 33;
  value *= (uintptr_t)0xff51afd7ed558ccdULL;
  value ^= value >> 33;
  return (size_t)(value % (uintptr_t)(sizeof(g_heap_index) / sizeof(g_heap_index[0])));
}

static void sprout_managed_index_insert(ManagedNode* node) {
  size_t bucket = sprout_managed_ptr_hash(node->ptr);
  node->hash_next = g_heap_index[bucket];
  g_heap_index[bucket] = node;
}

static void sprout_managed_index_remove(ManagedNode* node) {
  size_t bucket = sprout_managed_ptr_hash(node->ptr);
  ManagedNode* prev = NULL;
  ManagedNode* current = g_heap_index[bucket];
  while (current != NULL) {
    if (current == node) {
      if (prev == NULL) {
        g_heap_index[bucket] = current->hash_next;
      } else {
        prev->hash_next = current->hash_next;
      }
      node->hash_next = NULL;
      return;
    }
    prev = current;
    current = current->hash_next;
  }
}

static void register_managed_ptr(void* ptr, SproutHeapKind kind, size_t aux_slots) {
  ManagedNode* n = (ManagedNode*)malloc(sizeof(ManagedNode));
  if (n == NULL) tcp_fail("register_managed_ptr: out of memory");
  n->ptr = ptr;
  n->kind = kind;
  n->aux_slots = aux_slots;
  n->marked = 0;
  n->next = g_heap_nodes;
  n->hash_next = NULL;
  g_heap_nodes = n;
  sprout_managed_index_insert(n);
  g_managed_heap_count++;
  g_managed_alloc_since_gc++;
}

static ManagedNode* find_managed_ptr(void* ptr) {
  size_t bucket = sprout_managed_ptr_hash(ptr);
  for (ManagedNode* n = g_heap_index[bucket]; n != NULL; n = n->hash_next) {
    if (n->ptr == ptr) return n;
  }
  return NULL;
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

static void register_obj(SproutObj* p) {
  register_managed_ptr(p, SPROUT_HEAP_OBJ, 0);
}

static SproutObj* sprout_alloc_obj_raw(const char* ctx) {
  sprout_gc_maybe_collect_threshold();
  return (SproutObj*)sprout_alloc_counted(&g_debug_alloc_sprout_obj, sizeof(SproutObj), ctx);
}

static SproutObj* sprout_init_obj(SproutObj* obj, long long tag, long long f0, long long f1, long long f2) {
  obj->tag = tag;
  obj->f0 = f0;
  obj->f1 = f1;
  obj->f2 = f2;
  return obj;
}

static long long sprout_box_registered_obj(SproutObj* obj) {
  register_obj(obj);
  return box_ptr(obj);
}

static long long sprout_make_registered_obj(long long tag, long long f0, long long f1, long long f2, const char* ctx) {
  return sprout_box_registered_obj(sprout_init_obj(sprout_alloc_obj_raw(ctx), tag, f0, f1, f2));
}

void* sprout_alloc_closure_env(long long size) {
  if (size < 0) tcp_fail("sprout_alloc_closure_env: size must be >= 0");
  sprout_gc_maybe_collect_threshold();
  void* out = sprout_alloc_counted(&g_debug_alloc_closure, (size_t)size, "sprout_alloc_closure_env: out of memory");
  size_t slots = size == 0 ? 0 : (((size_t)size / sizeof(long long)) - 1);
  register_managed_ptr(out, SPROUT_HEAP_CLOSURE, slots);
  return out;
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

static long long sprout_gc_push_root(void* slot, SproutRootKind kind, size_t aux_words) {
  RootNode* node = (RootNode*)malloc(sizeof(RootNode));
  if (node == NULL) tcp_fail("sprout_gc_push_root: out of memory");
  node->slot = slot;
  node->kind = kind;
  node->aux_words = aux_words;
  node->next = g_temp_root_nodes;
  g_temp_root_nodes = node;
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
  for (long long i = 0; i < count; i++) {
    if (g_temp_root_nodes == NULL) tcp_fail("sprout_gc_pop_roots: root stack underflow");
    RootNode* next = g_temp_root_nodes->next;
    free(g_temp_root_nodes);
    g_temp_root_nodes = next;
  }
  return 0;
}

#define SPROUT_GC_PUSH_I64_LOCAL(slot_name) do { \
  long long sprout_gc_tmp_ignored = sprout_gc_push_i64_root(&(slot_name)); \
  (void)sprout_gc_tmp_ignored; \
} while (0)

#define SPROUT_GC_PUSH_PTR_LOCAL(slot_name) do { \
  long long sprout_gc_tmp_ignored = sprout_gc_push_ptr_root(&(slot_name)); \
  (void)sprout_gc_tmp_ignored; \
} while (0)

#define SPROUT_GC_POP_LOCALS(count_value) do { \
  long long sprout_gc_tmp_ignored = sprout_gc_pop_roots((count_value)); \
  (void)sprout_gc_tmp_ignored; \
} while (0)

void* sprout_alloc_tuple_blob(long long size_bytes) {
  if (size_bytes < 0) tcp_fail("sprout_alloc_tuple_blob: size must be >= 0");
  sprout_gc_maybe_collect_threshold();
  void* out = sprout_alloc_counted(&g_debug_alloc_sprout_obj, (size_t)size_bytes, "sprout_alloc_tuple_blob: out of memory");
  register_managed_ptr(out, SPROUT_HEAP_TUPLE, ((size_t)size_bytes) / sizeof(uintptr_t));
  return out;
}

static VectorVal* sprout_alloc_vector_val(const char* ctx) {
  sprout_gc_maybe_collect_threshold();
  VectorVal* out = (VectorVal*)sprout_alloc_counted(&g_debug_alloc_vector, sizeof(VectorVal), ctx);
  register_managed_ptr(out, SPROUT_HEAP_VECTOR, 0);
  return out;
}

static long long* sprout_alloc_vector_data(size_t count, const char* ctx) {
  return count == 0 ? NULL : (long long*)sprout_alloc_counted(&g_debug_alloc_vector, count * sizeof(long long), ctx);
}

static long long* sprout_realloc_vector_data(long long* data, size_t count, const char* ctx) {
  return (long long*)sprout_realloc_counted(&g_debug_alloc_vector, data, count * sizeof(long long), ctx);
}

static MapVal* sprout_alloc_map_val(const char* ctx) {
  sprout_gc_maybe_collect_threshold();
  MapVal* out = (MapVal*)sprout_alloc_counted(&g_debug_alloc_map, sizeof(MapVal), ctx);
  register_managed_ptr(out, SPROUT_HEAP_MAP, 0);
  return out;
}

static MapEntry* sprout_alloc_map_entries(size_t count, const char* ctx) {
  return count == 0 ? NULL : (MapEntry*)sprout_alloc_counted(&g_debug_alloc_map, count * sizeof(MapEntry), ctx);
}

static BytesVal* sprout_alloc_bytes_val(const char* ctx) {
  sprout_gc_maybe_collect_threshold();
  BytesVal* out = (BytesVal*)sprout_alloc_counted(&g_debug_alloc_bytes, sizeof(BytesVal), ctx);
  register_managed_ptr(out, SPROUT_HEAP_BYTES, 0);
  return out;
}

static unsigned char* sprout_alloc_bytes_data(size_t count, const char* ctx) {
  return count == 0 ? NULL : (unsigned char*)sprout_alloc_counted(&g_debug_alloc_bytes, count, ctx);
}

static BuilderVal* sprout_alloc_builder_val(const char* ctx) {
  sprout_gc_maybe_collect_threshold();
  BuilderVal* out = (BuilderVal*)sprout_alloc_counted(&g_debug_alloc_builder, sizeof(BuilderVal), ctx);
  register_managed_ptr(out, SPROUT_HEAP_BUILDER, 0);
  return out;
}

static BytesVal** sprout_alloc_builder_chunks(size_t count, const char* ctx) {
  return count == 0 ? NULL : (BytesVal**)sprout_alloc_counted(&g_debug_alloc_builder, count * sizeof(BytesVal*), ctx);
}

static IntRangeVal* sprout_alloc_range_val(const char* ctx) {
  sprout_gc_maybe_collect_threshold();
  IntRangeVal* out = (IntRangeVal*)sprout_alloc_counted(&g_debug_alloc_sprout_obj, sizeof(IntRangeVal), ctx);
  register_managed_ptr(out, SPROUT_HEAP_RANGE, 0);
  return out;
}

static int is_obj_handle(long long h) {
  ManagedNode* node = find_managed_ptr((void*)(uintptr_t)h);
  return node != NULL && node->kind == SPROUT_HEAP_OBJ;
}

static size_t sprout_heap_child_count(ManagedNode* node) {
  if (node == NULL) return 0;
  switch (node->kind) {
    case SPROUT_HEAP_OBJ: {
      CtorMeta* meta = find_ctor(((SproutObj*)node->ptr)->tag);
      return meta == NULL || meta->arity < 0 ? 0 : (size_t)meta->arity;
    }
    case SPROUT_HEAP_CLOSURE:
      return node->aux_slots;
    case SPROUT_HEAP_VECTOR:
      return (size_t)((VectorVal*)node->ptr)->len;
    case SPROUT_HEAP_MAP:
      return (size_t)((MapVal*)node->ptr)->len;
    case SPROUT_HEAP_BYTES:
      return 0;
    case SPROUT_HEAP_BUILDER:
      return ((BuilderVal*)node->ptr)->count;
    case SPROUT_HEAP_TUPLE:
      return node->aux_slots;
    case SPROUT_HEAP_RANGE:
      return 0;
  }
  return 0;
}

static long long sprout_heap_child_value(ManagedNode* node, size_t index) {
  if (node == NULL) tcp_fail("sprout_heap_child_value: null node");
  switch (node->kind) {
    case SPROUT_HEAP_OBJ: {
      SproutObj* obj = (SproutObj*)node->ptr;
      if (index == 0) return obj->f0;
      if (index == 1) return obj->f1;
      if (index == 2) return obj->f2;
      break;
    }
    case SPROUT_HEAP_CLOSURE: {
      long long* slots = (long long*)node->ptr;
      return slots[index + 1];
    }
    case SPROUT_HEAP_VECTOR:
      return ((VectorVal*)node->ptr)->data[index];
    case SPROUT_HEAP_MAP:
      return ((MapVal*)node->ptr)->entries[index].value;
    case SPROUT_HEAP_BYTES:
      break;
    case SPROUT_HEAP_BUILDER:
      return (long long)(uintptr_t)((BuilderVal*)node->ptr)->chunks[index];
    case SPROUT_HEAP_TUPLE: {
      uintptr_t word = 0;
      memcpy(&word, (char*)node->ptr + (index * sizeof(uintptr_t)), sizeof(uintptr_t));
      return (long long)word;
    }
    case SPROUT_HEAP_RANGE:
      break;
  }
  tcp_fail("sprout_heap_child_value: index out of range");
  return 0;
}

static void sprout_gc_mark_ptr(void* ptr);

static void sprout_gc_mark_value(long long value) {
  sprout_gc_mark_ptr((void*)(uintptr_t)value);
}

static void sprout_gc_mark_node(ManagedNode* node) {
  if (node == NULL || node->marked) return;
  node->marked = 1;
  g_gc_marked_count++;
  size_t child_count = sprout_heap_child_count(node);
  for (size_t i = 0; i < child_count; i++) {
    long long child = sprout_heap_child_value(node, i);
    sprout_gc_mark_value(child);
  }
}

static void sprout_gc_mark_ptr(void* ptr) {
  ManagedNode* node = find_managed_ptr(ptr);
  if (node != NULL) sprout_gc_mark_node(node);
}

static long long sprout_gc_root_count(void) {
  long long count = 0;
  for (RootNode* root = g_root_nodes; root != NULL; root = root->next) count++;
  for (RootNode* root = g_temp_root_nodes; root != NULL; root = root->next) count++;
  return count;
}

static void sprout_gc_mark_roots(void) {
  for (RootNode* root = g_root_nodes; root != NULL; root = root->next) {
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
  for (RootNode* root = g_temp_root_nodes; root != NULL; root = root->next) {
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

static void sprout_gc_free_payload(ManagedNode* node) {
  switch (node->kind) {
    case SPROUT_HEAP_OBJ:
      free(node->ptr);
      return;
    case SPROUT_HEAP_CLOSURE:
      free(node->ptr);
      return;
    case SPROUT_HEAP_VECTOR: {
      VectorVal* value = (VectorVal*)node->ptr;
      free(value->data);
      free(value);
      return;
    }
    case SPROUT_HEAP_MAP: {
      MapVal* value = (MapVal*)node->ptr;
      for (long long i = 0; i < value->len; i++) free(value->entries[i].key);
      free(value->entries);
      free(value);
      return;
    }
    case SPROUT_HEAP_BYTES: {
      BytesVal* value = (BytesVal*)node->ptr;
      free(value->data);
      free(value);
      return;
    }
    case SPROUT_HEAP_BUILDER: {
      BuilderVal* value = (BuilderVal*)node->ptr;
      free(value->chunks);
      free(value);
      return;
    }
    case SPROUT_HEAP_TUPLE:
      free(node->ptr);
      return;
    case SPROUT_HEAP_RANGE:
      free(node->ptr);
      return;
  }
}

static void sprout_gc_sweep(void) {
  ManagedNode* prev = NULL;
  ManagedNode* node = g_heap_nodes;
  while (node != NULL) {
    ManagedNode* next = node->next;
    if (!node->marked) {
      if (node->ptr == g_nothing_singleton) g_nothing_singleton = NULL;
      sprout_managed_index_remove(node);
      sprout_gc_free_payload(node);
      if (prev == NULL) {
        g_heap_nodes = next;
      } else {
        prev->next = next;
      }
      free(node);
      g_debug_gc_swept++;
      g_managed_heap_count--;
    } else {
      node->marked = 0;
      prev = node;
    }
    node = next;
  }
}

static void sprout_gc_collect(void) {
  sprout_gc_collect_with_reason("atexit");
}

static void sprout_gc_collect_with_reason(const char* reason) {
  if (g_gc_active) return;
  g_gc_active = 1;
  long long started_us = sprout_now_micros();
  long long heap_before = g_managed_heap_count;
  long long root_count = sprout_gc_root_count();
  long long alloc_since_gc = g_managed_alloc_since_gc;
  long long swept_before = g_debug_gc_swept;
  g_gc_cycle_count++;
  g_gc_marked_count = 0;
  sprout_gc_mark_roots();
  sprout_gc_sweep();
  long long finished_us = sprout_now_micros();
  long long elapsed_us = 0;
  if (finished_us >= started_us) elapsed_us = finished_us - started_us;
  sprout_gc_log_cycle(reason, heap_before, g_managed_heap_count, root_count, g_gc_marked_count, alloc_since_gc, g_debug_gc_swept - swept_before, elapsed_us);
  g_managed_alloc_since_gc = 0;
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
  tcp_fail("constructor metadata not registered");
  return -1;
}

static void print_inline_value(long long v);

static void print_inline_obj(SproutObj* o) {
  CtorMeta* m = find_ctor(o->tag);
  if (m == NULL) {
    printf("Ctor%lld", o->tag);
    return;
  }
  printf("%s", m->name);
  if (m->arity <= 0) return;
  printf("(");
  print_inline_value(o->f0);
  if (m->arity > 1) {
    printf(", ");
    print_inline_value(o->f1);
  }
  if (m->arity > 2) {
    printf(", ");
    print_inline_value(o->f2);
  }
  printf(")");
}

static void print_inline_value(long long v) {
  ManagedNode* node = find_managed_ptr((void*)(uintptr_t)v);
  if (node != NULL && node->kind == SPROUT_HEAP_RANGE) {
    IntRangeVal* value = (IntRangeVal*)node->ptr;
    printf("%lld..%lld", value->start, value->end);
  } else if (is_obj_handle(v)) {
    print_inline_obj(unbox_ptr(v));
  } else {
    printf("%lld", v);
  }
}

long long print_int(long long x) {
  printf("%lld\\n", x);
  return x;
}
long long print_str(const char* s) {
  printf("%s\\n", s);
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
  printf("\\n");
  return 0;
}
long long print_value(long long x) {
  print_inline_value(x);
  printf("\\n");
  return x;
}
long long parse_int(const char* s) {
  if (s == NULL) tcp_fail("parse_int: null input");
  char* end = NULL;
  long long out = strtoll(s, &end, 10);
  if (end == s || *end != '\\0') tcp_fail("parse_int: invalid integer");
  return out;
}
char* int_to_string(long long value) {
  char buf[32];
  int written = snprintf(buf, sizeof(buf), "%lld", value);
  if (written < 0) tcp_fail("int_to_string: formatting failed");
  size_t size = (size_t)written + 1;
  char* out = (char*)malloc(size);
  if (out == NULL) tcp_fail("int_to_string: out of memory");
  memcpy(out, buf, size);
  return out;
}
long long int_range(long long start, long long end) {
  IntRangeVal* out = sprout_alloc_range_val("int_range: out of memory");
  out->start = start;
  out->end = end;
  return (long long)(uintptr_t)out;
}
long long int_range_start(long long range_h) {
  IntRangeVal* value = (IntRangeVal*)(uintptr_t)range_h;
  ManagedNode* node = find_managed_ptr(value);
  if (value == NULL || node == NULL || node->kind != SPROUT_HEAP_RANGE) tcp_fail("int_range_start: expected IntRange");
  return value->start;
}
long long int_range_end(long long range_h) {
  IntRangeVal* value = (IntRangeVal*)(uintptr_t)range_h;
  ManagedNode* node = find_managed_ptr(value);
  if (value == NULL || node == NULL || node->kind != SPROUT_HEAP_RANGE) tcp_fail("int_range_end: expected IntRange");
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
  sprout_debug_alloc_maybe_enable();
  sprout_debug_gc_maybe_enable();
  sprout_gc_threshold_maybe_enable();
  sprout_gc_maybe_register();
  return 0;
}
long long sprout_nothing(long long tag) {
  if (g_nothing_singleton == NULL) {
    g_nothing_singleton = sprout_init_obj(sprout_alloc_obj_raw("sprout_nothing: out of memory"), tag, 0, 0, 0);
    register_obj(g_nothing_singleton);
  }
  return box_ptr(g_nothing_singleton);
}
long long argv_get(long long index) {
  if (index < 0) return sprout_make0(find_ctor_tag_by_name("Nothing"));
  if (g_sprout_argv == NULL) return sprout_make0(find_ctor_tag_by_name("Nothing"));
  if (index >= (long long)(g_sprout_argc - 1)) return sprout_make0(find_ctor_tag_by_name("Nothing"));
  return sprout_make1(find_ctor_tag_by_name("Just"), (long long)(uintptr_t)g_sprout_argv[index + 1]);
}
const char* read_file(const char* path) {
  if (path == NULL) tcp_fail("read_file: null path");
  FILE* f = NULL;
  int close_after = 0;
  if (strcmp(path, "-") == 0) {
    f = stdin;
  } else {
    f = fopen(path, "rb");
    if (f == NULL) tcp_fail("read_file: cannot open file");
    close_after = 1;
  }

  size_t cap = 4096;
  size_t len = 0;
  char* out = (char*)malloc(cap);
  if (out == NULL) {
    if (close_after) fclose(f);
    tcp_fail("read_file: out of memory");
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
          tcp_fail("read_file: out of memory");
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
        if (close_after) fclose(f);
        tcp_fail("read_file: read error");
      }
    }
  }

  if (close_after) fclose(f);
  out[len] = '\\0';
  return out;
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
  while (len > 0 && (line[len - 1] == '\\n' || line[len - 1] == '\\r')) {
    len -= 1;
    line[len] = '\\0';
  }
  return sprout_make1(find_ctor_tag_by_name("Just"), (long long)(uintptr_t)line);
}
_Bool term_is_interactive(void) {
  return isatty(fileno(stdin)) && isatty(fileno(stdout));
}
long long term_clear(void) {
  fputs("\x1b[2J\x1b[H", stdout);
  fflush(stdout);
  return 0;
}
long long term_move(long long row, long long col) {
  fprintf(stdout, "\x1b[%lld;%lldH", row, col);
  fflush(stdout);
  return 0;
}
long long term_hide_cursor(void) {
  fputs("\x1b[?25l", stdout);
  fflush(stdout);
  return 0;
}
long long term_show_cursor(void) {
  fputs("\x1b[?25h", stdout);
  fflush(stdout);
  return 0;
}
const char* term_read_key(void) {
  static char buf[2] = {0, 0};
  static const char* token_ctrl_a = "ctrl-a";
  static const char* token_ctrl_b = "ctrl-b";
  static const char* token_ctrl_d = "ctrl-d";
  static const char* token_ctrl_e = "ctrl-e";
  static const char* token_ctrl_f = "ctrl-f";
  static const char* token_backspace = "backspace";
  static const char* token_down = "down";
  static const char* token_escape = "escape";
  static const char* token_enter = "enter";
  static const char* token_left = "left";
  static const char* token_right = "right";
  static const char* token_tab = "tab";
  static const char* token_up = "up";
  buf[0] = '\\0';
  buf[1] = '\\0';
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
        char byte = '\\0';
        ssize_t count = read(STDIN_FILENO, &byte, 1);
        if (count > 0) {
          ch = (unsigned char)byte;
          if (ch == 27) {
            struct termios raw_more = raw;
            raw_more.c_cc[VMIN] = 0;
            raw_more.c_cc[VTIME] = 1;
            if (tcsetattr(STDIN_FILENO, TCSANOW, &raw_more) == 0) {
              char next = '\\0';
              char third = '\\0';
              ssize_t next_count = read(STDIN_FILENO, &next, 1);
              if (next_count > 0 && next == '[') {
                ssize_t third_count = read(STDIN_FILENO, &third, 1);
                if (third_count > 0) {
                  tcsetattr(STDIN_FILENO, TCSANOW, &oldt);
                  if (third == 'A') return token_up;
                  if (third == 'B') return token_down;
                  if (third == 'C') return token_right;
                  if (third == 'D') return token_left;
                }
              }
            }
          }
        }
        tcsetattr(STDIN_FILENO, TCSANOW, &oldt);
      }
    }
  }
  if (ch == EOF) return buf;
  if (ch == 1) return token_ctrl_a;
  if (ch == 2) return token_ctrl_b;
  if (ch == 4) return token_ctrl_d;
  if (ch == 5) return token_ctrl_e;
  if (ch == 6) return token_ctrl_f;
  if (ch == 8 || ch == 127) return token_backspace;
  if (ch == 27) return token_escape;
  if (ch == '\\n' || ch == '\\r') return token_enter;
  if (ch == '\\t') return token_tab;
  buf[0] = (char)ch;
  return buf;
}
long long term_write(const char* text) {
  if (text == NULL) tcp_fail("term_write: null text");
  fputs(text, stdout);
  fflush(stdout);
  return 0;
}
static char* sprout_json_escape(const char* text) {
  if (text == NULL) tcp_fail("analysis service: null json text");
  size_t extra = 0;
  for (const unsigned char* p = (const unsigned char*)text; *p != '\\0'; ++p) {
    switch (*p) {
      case '\\\\':
      case '"':
      case '\\n':
      case '\\r':
      case '\\t':
        extra += 1;
        break;
      default:
        break;
    }
  }
  size_t len = strlen(text);
  char* out = alloc_cstr(len + extra, "analysis service: out of memory");
  size_t idx = 0;
  for (const unsigned char* p = (const unsigned char*)text; *p != '\\0'; ++p) {
    switch (*p) {
      case '\\\\':
        out[idx++] = '\\\\';
        out[idx++] = '\\\\';
        break;
      case '"':
        out[idx++] = '\\\\';
        out[idx++] = '"';
        break;
      case '\\n':
        out[idx++] = '\\\\';
        out[idx++] = 'n';
        break;
      case '\\r':
        out[idx++] = '\\\\';
        out[idx++] = 'r';
        break;
      case '\\t':
        out[idx++] = '\\\\';
        out[idx++] = 't';
        break;
      default:
        out[idx++] = (char)*p;
        break;
    }
  }
  out[idx] = '\\0';
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
  pattern[key_len + 2] = '\\0';
  const char* pos = strstr(text, pattern);
  free(pattern);
  if (pos == NULL) return NULL;
  pos += pattern_len;
  while (*pos == ' ' || *pos == '\\n' || *pos == '\\r' || *pos == '\\t') pos++;
  if (*pos != ':') return NULL;
  pos++;
  while (*pos == ' ' || *pos == '\\n' || *pos == '\\r' || *pos == '\\t') pos++;
  return pos;
}
static int sprout_json_field_is_true(const char* text, const char* key) {
  const char* pos = sprout_json_after_key(text, key);
  return pos != NULL && strncmp(pos, "true", 4) == 0;
}
static const char* sprout_json_skip_ws(const char* pos) {
  if (pos == NULL) return NULL;
  while (*pos == ' ' || *pos == '\\n' || *pos == '\\r' || *pos == '\\t') pos++;
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
  while (*pos != '\\0') {
    if (*pos == '"') {
      out[idx] = '\\0';
      *pos_ptr = pos + 1;
      return out;
    }
    if ((unsigned char)*pos < 0x20) {
      if (err_msg != NULL) *err_msg = "invalid control character in string";
      free(out);
      return NULL;
    }
    if (*pos == '\\\\') {
      pos++;
      if (*pos == '\\0') {
        if (err_msg != NULL) *err_msg = "unterminated escape sequence";
        free(out);
        return NULL;
      }
      switch (*pos) {
        case '\\\\':
        case '"':
        case '/':
          out[idx++] = *pos;
          break;
        case 'b':
          out[idx++] = '\\b';
          break;
        case 'f':
          out[idx++] = '\\f';
          break;
        case 'n':
          out[idx++] = '\\n';
          break;
        case 'r':
          out[idx++] = '\\r';
          break;
        case 't':
          out[idx++] = '\\t';
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
            if (pos[1] != '\\\\' || pos[2] != 'u') {
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
  out->len = 0;
  out->cap = 0;
  out->data = NULL;
  pos = sprout_json_skip_ws(pos);
  if (*pos == ']') return out;
  while (*pos != '\\0') {
    char* item = sprout_json_parse_string(&pos);
    if (item == NULL) return out;
    if (out->len == out->cap) {
      long long new_cap = out->cap == 0 ? 4 : (out->cap * 2);
      out->data = sprout_realloc_vector_data(out->data, (size_t)new_cap, "analysis service: out of memory");
      out->cap = new_cap;
    }
    out->data[out->len++] = (long long)(uintptr_t)item;
    pos = sprout_json_skip_ws(pos);
    if (*pos == ']') return out;
    if (*pos != ',') return out;
    pos++;
  }
  return out;
}
static char* sprout_json_extract_string(const char* text, const char* key) {
  const char* pos = sprout_json_after_key(text, key);
  return pos == NULL ? NULL : sprout_json_parse_string(&pos);
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
  while (*pos != '\\0') {
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
static void sprout_builtin_fail_detail(const char* builtin_name, const char* detail) {
  size_t len = strlen(builtin_name) + strlen(detail) + 2;
  char* msg = alloc_cstr(len, "analysis service: out of memory");
  snprintf(msg, len + 1, "%s: %s", builtin_name, detail);
  tcp_fail(msg);
}
static char* sprout_json_encode_string_array_from_vec_handle(const void* vec_handle_ptr, const char* builtin_name, const char* label) {
  long long vec_handle = (long long)(uintptr_t)vec_handle_ptr;
  ManagedNode* vec_node = find_managed_ptr((void*)(uintptr_t)vec_handle);
  if (vec_node == NULL || vec_node->kind != SPROUT_HEAP_OBJ) {
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
  ManagedNode* raw_node = find_managed_ptr((void*)(uintptr_t)raw_handle);
  if (raw_node == NULL || raw_node->kind != SPROUT_HEAP_VECTOR) {
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
  out[cursor] = '\\0';
  return out;
}
__SPROUT_ANALYSIS_BRIDGE_RUNTIME__
static long long sprout_err_string_result(const char* message) {
  return sprout_make1(find_ctor_tag_by_name("Err"), (long long)(uintptr_t)dup_cstr(message));
}
__SPROUT_ANALYSIS_BRIDGE_REQUEST_HELPERS__
__SPROUT_ANALYSIS_BRIDGE_RESPONSE_HELPERS__
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
  char* imports_json = sprout_json_encode_string_array_from_vec_handle(imports_handle, "repl_complete_in_state", "imports");
  char* declarations_json = sprout_json_encode_string_array_from_vec_handle(declarations_handle, "repl_complete_in_state", "declarations");
  size_t request_len = strlen(escaped_line_buffer) + strlen(imports_json) + strlen(declarations_json) + 88;
  char* request = alloc_cstr(request_len, "analysis service: out of memory");
  snprintf(
    request,
    request_len + 1,
    "{\\\"op\\\":\\\"__SPROUT_ANALYSIS_OP_COMPLETE_IN_STATE__\\\",\\\"line_buffer\\\":\\\"%s\\\",\\\"imports\\\":%s,\\\"declarations\\\":%s}\\n",
    escaped_line_buffer,
    imports_json,
    declarations_json
  );
  free(escaped_line_buffer);
  free(imports_json);
  free(declarations_json);
  char* response = NULL;
  char* error = NULL;
  if (!sprout_run_analysis_service(request, sprout_analysis_service_retry_allowed("__SPROUT_ANALYSIS_OP_COMPLETE_IN_STATE__"), &response, &error)) {
    free(request);
    sprout_builtin_fail_detail("repl_complete_in_state", error != NULL ? error : "__SPROUT_ANALYSIS_SERVICE_REQUEST_FAILED__");
  }
  free(request);
  if (sprout_json_field_is_true(response, "ok")) {
    return sprout_analysis_completion_tuple_or_fail(
      "repl_complete_in_state",
      response,
      "prefix",
      "matches"
    );
  }
  error = sprout_json_extract_string(response, "error");
  free(response);
  sprout_builtin_fail_detail("repl_complete_in_state", error != NULL ? error : "__SPROUT_ANALYSIS_SERVICE_INVALID_RESPONSE__");
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
long long repl_eval_expr_in_source(const char* module_source, const char* expr) {
  return sprout_analysis_vec_string_result("__SPROUT_ANALYSIS_OP_EVAL_EXPR_IN_SOURCE__", module_source, expr);
}
long long repl_check_source(const char* module_source) {
  return sprout_analysis_check_source_result("__SPROUT_ANALYSIS_OP_CHECK_SOURCE__", module_source);
}
long long analysis_check_source(const char* module_source) {
  return sprout_analysis_check_source_result("__SPROUT_ANALYSIS_OP_CHECK_SOURCE__", module_source);
}
long long repl_declared_names_in_source(const char* module_source) {
  return sprout_analysis_string_array_result("__SPROUT_ANALYSIS_OP_DECLARED_NAMES_IN_SOURCE__", module_source);
}
long long analysis_declared_names_in_source(const char* module_source) {
  return sprout_analysis_string_array_result("__SPROUT_ANALYSIS_OP_DECLARED_NAMES_IN_SOURCE__", module_source);
}
long long repl_exported_names_in_source(const char* module_source) {
  return sprout_analysis_string_array_result("__SPROUT_ANALYSIS_OP_EXPORTED_NAMES_IN_SOURCE__", module_source);
}
long long analysis_exported_names_in_source(const char* module_source) {
  return sprout_analysis_string_array_result("__SPROUT_ANALYSIS_OP_EXPORTED_NAMES_IN_SOURCE__", module_source);
}
long long repl_symbol_inventory_in_source(const char* module_source) {
  return sprout_analysis_inventory_result("__SPROUT_ANALYSIS_OP_SYMBOL_INVENTORY_IN_SOURCE__", module_source);
}
long long analysis_symbol_inventory_in_source(const char* module_source) {
  return sprout_analysis_inventory_result("__SPROUT_ANALYSIS_OP_SYMBOL_INVENTORY_IN_SOURCE__", module_source);
}
long long analysis_symbol_locations_in_source(const char* module_source) {
  return sprout_analysis_symbol_locations_result("__SPROUT_ANALYSIS_OP_SYMBOL_LOCATIONS_IN_SOURCE__", module_source);
}
long long repl_diagnostics_in_source(const char* module_source) {
  return sprout_analysis_diagnostics_result("__SPROUT_ANALYSIS_OP_DIAGNOSTICS_IN_SOURCE__", module_source);
}
long long analysis_diagnostics_in_source(const char* module_source) {
  return sprout_analysis_diagnostics_result("__SPROUT_ANALYSIS_OP_DIAGNOSTICS_IN_SOURCE__", module_source);
}
long long repl_type_of(const char* source) {
  (void)source;
  tcp_fail("repl_type_of: not supported in native backend");
  return 0;
}
long long repl_type_of_in_source(const char* module_source, const char* expr) {
  return sprout_analysis_type_result("__SPROUT_ANALYSIS_OP_TYPE_OF_IN_SOURCE__", module_source, expr);
}
long long analysis_type_of_in_source(const char* module_source, const char* expr) {
  return sprout_analysis_type_result("__SPROUT_ANALYSIS_OP_TYPE_OF_IN_SOURCE__", module_source, expr);
}
long long repl_instances(const char* source) {
  (void)source;
  tcp_fail("repl_instances: not supported in native backend");
  return 0;
}
long long repl_instances_in_source(const char* module_source, const char* type_expr_source) {
  return sprout_analysis_instances_result("__SPROUT_ANALYSIS_OP_INSTANCES_IN_SOURCE__", module_source, type_expr_source);
}
long long analysis_instances_in_source(const char* module_source, const char* type_expr_source) {
  return sprout_analysis_instances_result("__SPROUT_ANALYSIS_OP_INSTANCES_IN_SOURCE__", module_source, type_expr_source);
}
long long repl_complete(const char* source) {
  (void)source;
  tcp_fail("repl_complete: not supported in native backend");
  return 0;
}
long long repl_complete_in_state(const char* line_buffer, const void* imports_handle, const void* declarations_handle) {
  return sprout_analysis_completion_result(line_buffer, imports_handle, declarations_handle);
}
long long repl_reset_session(void) {
  return 0;
}
long long read_int_lines(const char* path) {
  if (path == NULL) tcp_fail("read_int_lines: null path");
  FILE* f = fopen(path, "r");
  if (f == NULL) tcp_fail("read_int_lines: cannot open file");
  VectorVal* v = sprout_alloc_vector_val("read_int_lines: out of memory");
  SPROUT_GC_PUSH_PTR_LOCAL(v);
  v->len = 0;
  v->cap = 0;
  v->data = NULL;

  char buf[4096];
  while (fgets(buf, sizeof(buf), f) != NULL) {
    size_t n = strlen(buf);
    while (n > 0 && (buf[n - 1] == '\\n' || buf[n - 1] == '\\r')) {
      buf[n - 1] = '\\0';
      n--;
    }
    if (n == 0) continue;
    char* end = NULL;
    long long value = strtoll(buf, &end, 10);
    if (end == buf || *end != '\\0') tcp_fail("read_int_lines: invalid integer line");
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
  SPROUT_GC_POP_LOCALS(1);
  return (long long)(uintptr_t)v;
}
long long sprout_register_ctor(long long tag, const char* name, long long arity) {
  g_ctor_meta[g_ctor_meta_len].tag = tag;
  g_ctor_meta[g_ctor_meta_len].name = name;
  g_ctor_meta[g_ctor_meta_len].arity = arity;
  g_ctor_meta_len++;
  return 0;
}
long long sprout_make0(long long tag) {
  CtorMeta* meta = find_ctor(tag);
  if (meta != NULL && strcmp(meta->name, "Nothing") == 0) {
    if (g_nothing_singleton == NULL) {
      g_nothing_singleton = sprout_init_obj(sprout_alloc_obj_raw("sprout_make0: out of memory"), tag, 0, 0, 0);
      register_obj(g_nothing_singleton);
    }
    return box_ptr(g_nothing_singleton);
  }
  return sprout_make_registered_obj(tag, 0, 0, 0, "sprout_make0: out of memory");
}
long long sprout_make1(long long tag, long long a0) {
  return sprout_make_registered_obj(tag, a0, 0, 0, "sprout_make1: out of memory");
}
long long sprout_make2(long long tag, long long a0, long long a1) {
  return sprout_make_registered_obj(tag, a0, a1, 0, "sprout_make2: out of memory");
}
long long sprout_make3(long long tag, long long a0, long long a1, long long a2) {
  return sprout_make_registered_obj(tag, a0, a1, a2, "sprout_make3: out of memory");
}
long long sprout_tag(long long h) {
  return unbox_ptr(h)->tag;
}
long long sprout_field(long long h, long long idx) {
  SproutObj* o = unbox_ptr(h);
  if (idx == 0) return o->f0;
  if (idx == 1) return o->f1;
  return o->f2;
}

static void tcp_fail(const char* msg) {
  const char* colon = strchr(msg, ':');
  if (colon != NULL) {
    size_t name_len = (size_t)(colon - msg);
    const char* detail = colon + 1;
    while (*detail == ' ') detail++;
    fprintf(stderr, "runtime error: builtin `%.*s`: %s\\n", (int)name_len, msg, detail);
  } else {
    fprintf(stderr, "runtime error: %s\\n", msg);
  }
  exit(1);
}

const char* str_concat(const char* left, const char* right) {
  if (left == NULL || right == NULL) tcp_fail("str_concat: null input");
  size_t left_len = strlen(left);
  size_t right_len = strlen(right);
  char* out = (char*)malloc(left_len + right_len + 1);
  if (out == NULL) tcp_fail("str_concat: out of memory");
  memcpy(out, left, left_len);
  memcpy(out + left_len, right, right_len);
  out[left_len + right_len] = '\\0';
  return out;
}

static size_t sprout_utf8_char_width(unsigned char lead) {
  if ((lead & 0x80) == 0) return 1;
  if ((lead & 0xE0) == 0xC0) return 2;
  if ((lead & 0xF0) == 0xE0) return 3;
  if ((lead & 0xF8) == 0xF0) return 4;
  tcp_fail("str_len: invalid UTF-8 lead byte");
  return 1;
}

static size_t sprout_utf8_codepoint_count(const char* s) {
  size_t count = 0;
  size_t i = 0;
  while (s[i] != '\\0') {
    i += sprout_utf8_char_width((unsigned char)s[i]);
    count++;
  }
  return count;
}

static size_t sprout_utf8_byte_offset(const char* s, size_t codepoint_offset) {
  size_t i = 0;
  size_t count = 0;
  while (s[i] != '\\0' && count < codepoint_offset) {
    i += sprout_utf8_char_width((unsigned char)s[i]);
    count++;
  }
  return i;
}

long long str_len(const char* s) {
  if (s == NULL) tcp_fail("str_len: null input");
  return (long long)sprout_utf8_codepoint_count(s);
}

_Bool str_eq(const char* left, const char* right) {
  if (left == NULL || right == NULL) tcp_fail("str_eq: null input");
  return strcmp(left, right) == 0;
}

const char* str_slice(const char* s, long long start, long long length) {
  if (s == NULL) tcp_fail("str_slice: null input");
  if (start < 0 || length < 0) tcp_fail("str_slice: start/length must be >= 0");
  size_t total = sprout_utf8_codepoint_count(s);
  if ((size_t)start >= total) {
    char* out = (char*)malloc(1);
    if (out == NULL) tcp_fail("str_slice: out of memory");
    out[0] = '\\0';
    return out;
  }
  size_t start_byte = sprout_utf8_byte_offset(s, (size_t)start);
  size_t end_codepoint = (size_t)start + (size_t)length;
  if (end_codepoint > total) end_codepoint = total;
  size_t end_byte = sprout_utf8_byte_offset(s, end_codepoint);
  size_t take = end_byte - start_byte;
  char* out = (char*)malloc(take + 1);
  if (out == NULL) tcp_fail("str_slice: out of memory");
  memcpy(out, s + start_byte, take);
  out[take] = '\\0';
  return out;
}

long long str_char_at(const char* s, long long index) {
  if (s == NULL) tcp_fail("str_char_at: null input");
  if (index < 0) return sprout_make0(find_ctor_tag_by_name("Nothing"));
  size_t total = sprout_utf8_codepoint_count(s);
  if ((size_t)index >= total) return sprout_make0(find_ctor_tag_by_name("Nothing"));
  const char* out = str_slice(s, index, 1);
  return sprout_make1(find_ctor_tag_by_name("Just"), (long long)(uintptr_t)out);
}

long long str_find(const char* haystack, const char* needle) {
  if (haystack == NULL || needle == NULL) tcp_fail("str_find: null input");
  const char* pos = strstr(haystack, needle);
  if (pos == NULL) return -1;
  size_t prefix_len = (size_t)(pos - haystack);
  size_t count = 0;
  size_t i = 0;
  while (i < prefix_len) {
    i += sprout_utf8_char_width((unsigned char)haystack[i]);
    count++;
  }
  return (long long)count;
}

_Bool str_starts_with(const char* s, const char* prefix) {
  if (s == NULL || prefix == NULL) tcp_fail("str_starts_with: null input");
  size_t prefix_len = strlen(prefix);
  return strncmp(s, prefix, prefix_len) == 0;
}

long long str_compare(const char* left, const char* right) {
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
    return sprout_make1(find_ctor_tag_by_name("Err"), (long long)(uintptr_t)error);
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
  return sprout_make1(find_ctor_tag_by_name("Just"), (long long)(uintptr_t)range);
}

const char* regex_replace_all_literal(const char* pattern, const char* replacement, const char* text) {
  if (pattern == NULL || replacement == NULL || text == NULL) {
    tcp_fail("regex_replace_all_literal: null input");
  }
  regex_t compiled;
  char* error = NULL;
  if (!regex_compile_ere(pattern, &compiled, &error)) {
    regex_builtin_fail("regex_replace_all_literal", error);
  }
  ByteBuf out;
  buf_init(&out);
  const char* cursor = text;
  regmatch_t match;
  while (regexec(&compiled, cursor, 1, &match, 0) == 0) {
    if (match.rm_so < 0 || match.rm_eo < 0) {
      regfree(&compiled);
      tcp_fail("regex_replace_all_literal: regexec failed");
    }
    size_t start = (size_t)match.rm_so;
    size_t end = (size_t)match.rm_eo;
    buf_append_bytes(&out, cursor, start);
    buf_append_cstr(&out, replacement);
    if (end == 0) {
      if (cursor[0] == '\\0') break;
      size_t width = sprout_utf8_char_width((unsigned char)cursor[0]);
      buf_append_bytes(&out, cursor, width);
      cursor += width;
    } else {
      cursor += end;
    }
  }
  buf_append_cstr(&out, cursor);
  regfree(&compiled);
  return out.data;
}

const char* regex_escape(const char* raw) {
  if (raw == NULL) tcp_fail("regex_escape: null input");
  ByteBuf out;
  buf_init(&out);
  for (size_t i = 0; raw[i] != '\\0'; i++) {
    if (strchr(".^$*+?()[]{}|\\\\", raw[i]) != NULL) {
      buf_append_char(&out, '\\\\');
    }
    buf_append_char(&out, raw[i]);
  }
  return out.data == NULL ? dup_cstr("") : out.data;
}

const char* char_to_string(const char* ch) {
  if (ch == NULL) tcp_fail("char_to_string: null input");
  return ch;
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
  buf->data[buf->len] = '\\0';
}

static void buf_append_cstr(ByteBuf* buf, const char* text) {
  buf_append_bytes(buf, text, strlen(text));
}

static void buf_append_char(ByteBuf* buf, char ch) {
  buf_reserve(buf, buf->len + 2);
  buf->data[buf->len] = ch;
  buf->len += 1;
  buf->data[buf->len] = '\\0';
}

static char* alloc_cstr(size_t len, const char* ctx) {
  char* out = (char*)malloc(len + 1);
  if (out == NULL) tcp_fail(ctx);
  out[0] = '\\0';
  return out;
}

static char* dup_slice(const char* start, size_t len) {
  char* out = alloc_cstr(len, "http_request: out of memory");
  memcpy(out, start, len);
  out[len] = '\\0';
  return out;
}

static char* dup_cstr(const char* text) {
  return dup_slice(text, strlen(text));
}

static char* regex_prefixed_error(const char* prefix, const char* detail) {
  size_t prefix_len = strlen(prefix);
  size_t detail_len = strlen(detail);
  char* out = alloc_cstr(prefix_len + detail_len, "regex_validate: out of memory");
  memcpy(out, prefix, prefix_len);
  memcpy(out + prefix_len, detail, detail_len);
  out[prefix_len + detail_len] = '\\0';
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
  out[builtin_len + 2 + detail_len] = '\\0';
  tcp_fail(out);
}

static int regex_translate_pattern(const char* pattern, char** out_pattern, char** out_error) {
  ByteBuf out;
  buf_init(&out);
  int in_class = 0;
  for (size_t i = 0; pattern[i] != '\\0';) {
    char ch = pattern[i];
    if (ch == '\\\\') {
      char esc = pattern[i + 1];
      if (esc == '\\0') {
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
          buf_append_cstr(&out, " \\t\\r\\n");
        } else {
          buf_append_cstr(&out, "[ \\t\\r\\n]");
        }
      } else if (strchr("\\\\.^$*+?()[]|{}-", esc) != NULL) {
        buf_append_char(&out, '\\\\');
        buf_append_char(&out, esc);
      } else {
        if (out.data != NULL) free(out.data);
        *out_pattern = NULL;
        *out_error = regex_prefixed_error("unsupported regex feature: escape \\\\", (char[]){esc, '\\0'});
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
  while (s[i] != '\\0' && i < byte_limit) {
    i += sprout_utf8_char_width((unsigned char)s[i]);
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
  long long err = sprout_make0(find_ctor_tag_by_name(ctor_name));
  SPROUT_GC_PUSH_I64_LOCAL(err);
  long long out = sprout_make1(find_ctor_tag_by_name("Err"), err);
  SPROUT_GC_POP_LOCALS(1);
  return out;
}

static long long http_err1(const char* ctor_name, long long payload) {
  long long rooted_payload = payload;
  SPROUT_GC_PUSH_I64_LOCAL(rooted_payload);
  long long err = sprout_make1(find_ctor_tag_by_name(ctor_name), payload);
  SPROUT_GC_PUSH_I64_LOCAL(err);
  long long out = sprout_make1(find_ctor_tag_by_name("Err"), err);
  SPROUT_GC_POP_LOCALS(2);
  return out;
}

static long long http_ok_response(long long status, const char* headers, const char* body) {
  long long resp = sprout_make3(
    find_ctor_tag_by_name("stdlib.http.HttpResponse"),
    status,
    (long long)(uintptr_t)headers,
    (long long)(uintptr_t)body
  );
  SPROUT_GC_PUSH_I64_LOCAL(resp);
  long long out = sprout_make1(find_ctor_tag_by_name("Ok"), resp);
  SPROUT_GC_POP_LOCALS(1);
  return out;
}

static void json_append_hex4(ByteBuf* out, unsigned char value) {
  static const char* hex = "0123456789abcdef";
  char escaped[6];
  escaped[0] = '\\\\';
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
  for (const unsigned char* p = (const unsigned char*)raw; *p != '\\0'; p++) {
    unsigned char ch = *p;
    if (ch == '"') {
      const char escaped_quote[2] = {'\\\\', '"'};
      buf_append_bytes(out, escaped_quote, 2);
    } else if (ch == '\\\\') {
      const char escaped_slash[2] = {'\\\\', '\\\\'};
      buf_append_bytes(out, escaped_slash, 2);
    } else if (ch == '\\b') {
      const char escaped_backspace[2] = {'\\\\', 'b'};
      buf_append_bytes(out, escaped_backspace, 2);
    } else if (ch == '\\f') {
      const char escaped_formfeed[2] = {'\\\\', 'f'};
      buf_append_bytes(out, escaped_formfeed, 2);
    } else if (ch == '\\n') {
      const char escaped_newline[2] = {'\\\\', 'n'};
      buf_append_bytes(out, escaped_newline, 2);
    } else if (ch == '\\r') {
      const char escaped_return[2] = {'\\\\', 'r'};
      buf_append_bytes(out, escaped_return, 2);
    } else if (ch == '\\t') {
      const char escaped_tab[2] = {'\\\\', 't'};
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
  CtorMeta* meta = find_ctor(unbox_ptr(value)->tag);
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
  long long rooted_value = value;
  SPROUT_GC_PUSH_I64_LOCAL(rooted_value);
  long long out = sprout_make1(find_ctor_tag_by_name("Ok"), value);
  SPROUT_GC_POP_LOCALS(1);
  return out;
}

static long long json_parse_err_result(const char* message) {
  long long err = sprout_make1(
    find_ctor_tag_by_name("stdlib.json.JsonDecode"),
    (long long)(uintptr_t)dup_cstr(message)
  );
  SPROUT_GC_PUSH_I64_LOCAL(err);
  long long out = sprout_make1(find_ctor_tag_by_name("Err"), err);
  SPROUT_GC_POP_LOCALS(1);
  return out;
}

static long long json_parse_reverse_array_items(long long reversed) {
  long long rooted_reversed = reversed;
  SPROUT_GC_PUSH_I64_LOCAL(rooted_reversed);
  long long out = sprout_make0(find_ctor_tag_by_name("stdlib.json.JsonArrayNil"));
  SPROUT_GC_PUSH_I64_LOCAL(out);
  long long cursor = reversed;
  while (json_ctor_is(json_ctor_name(cursor), "JsonArrayCons")) {
    long long value = sprout_field(cursor, 0);
    SPROUT_GC_PUSH_I64_LOCAL(value);
    out = sprout_make2(find_ctor_tag_by_name("stdlib.json.JsonArrayCons"), value, out);
    SPROUT_GC_POP_LOCALS(1);
    cursor = sprout_field(cursor, 1);
  }
  SPROUT_GC_POP_LOCALS(1);
  SPROUT_GC_POP_LOCALS(1);
  return out;
}

static long long json_parse_reverse_object_items(long long reversed) {
  long long rooted_reversed = reversed;
  SPROUT_GC_PUSH_I64_LOCAL(rooted_reversed);
  long long out = sprout_make0(find_ctor_tag_by_name("stdlib.json.JsonObjectNil"));
  SPROUT_GC_PUSH_I64_LOCAL(out);
  long long cursor = reversed;
  while (json_ctor_is(json_ctor_name(cursor), "JsonObjectCons")) {
    long long key = sprout_field(cursor, 0);
    long long value = sprout_field(cursor, 1);
    SPROUT_GC_PUSH_I64_LOCAL(value);
    out = sprout_make3(find_ctor_tag_by_name("stdlib.json.JsonObjectCons"), key, value, out);
    SPROUT_GC_POP_LOCALS(1);
    cursor = sprout_field(cursor, 2);
  }
  SPROUT_GC_POP_LOCALS(1);
  SPROUT_GC_POP_LOCALS(1);
  return out;
}

static long long json_parse_array(const char** pos_ptr, char** err_msg) {
  const char* pos = sprout_json_skip_ws(*pos_ptr);
  pos++;
  long long reversed = sprout_make0(find_ctor_tag_by_name("stdlib.json.JsonArrayNil"));
  SPROUT_GC_PUSH_I64_LOCAL(reversed);
  pos = sprout_json_skip_ws(pos);
  if (*pos == ']') {
    long long list = json_parse_reverse_array_items(reversed);
    SPROUT_GC_PUSH_I64_LOCAL(list);
    long long out = sprout_make1(find_ctor_tag_by_name("stdlib.json.JsonArray"), list);
    SPROUT_GC_POP_LOCALS(2);
    *pos_ptr = pos + 1;
    return out;
  }
  while (*pos != '\\0') {
    long long value = json_parse_value(&pos, err_msg);
    if (err_msg != NULL && *err_msg != NULL) {
      SPROUT_GC_POP_LOCALS(1);
      return 0;
    }
    SPROUT_GC_PUSH_I64_LOCAL(value);
    reversed = sprout_make2(find_ctor_tag_by_name("stdlib.json.JsonArrayCons"), value, reversed);
    SPROUT_GC_POP_LOCALS(1);
    pos = sprout_json_skip_ws(pos);
    if (*pos == ']') {
      long long list = json_parse_reverse_array_items(reversed);
      SPROUT_GC_PUSH_I64_LOCAL(list);
      long long out = sprout_make1(find_ctor_tag_by_name("stdlib.json.JsonArray"), list);
      SPROUT_GC_POP_LOCALS(2);
      *pos_ptr = pos + 1;
      return out;
    }
    if (*pos != ',') {
      if (err_msg != NULL && *err_msg == NULL) *err_msg = dup_cstr("expected ',' or ']'");
      SPROUT_GC_POP_LOCALS(1);
      return 0;
    }
    pos = sprout_json_skip_ws(pos + 1);
  }
  if (err_msg != NULL && *err_msg == NULL) *err_msg = dup_cstr("unterminated array");
  SPROUT_GC_POP_LOCALS(1);
  return 0;
}

static long long json_parse_object(const char** pos_ptr, char** err_msg) {
  const char* pos = sprout_json_skip_ws(*pos_ptr);
  pos++;
  long long reversed = sprout_make0(find_ctor_tag_by_name("stdlib.json.JsonObjectNil"));
  SPROUT_GC_PUSH_I64_LOCAL(reversed);
  pos = sprout_json_skip_ws(pos);
  if (*pos == '}') {
    long long list = json_parse_reverse_object_items(reversed);
    SPROUT_GC_PUSH_I64_LOCAL(list);
    long long out = sprout_make1(find_ctor_tag_by_name("stdlib.json.JsonObject"), list);
    SPROUT_GC_POP_LOCALS(2);
    *pos_ptr = pos + 1;
    return out;
  }
  while (*pos != '\\0') {
    const char* parse_string_err = NULL;
    char* key = sprout_json_parse_string_impl(&pos, &parse_string_err);
    if (key == NULL) {
      if (err_msg != NULL && *err_msg == NULL) {
        *err_msg = dup_cstr(parse_string_err != NULL ? parse_string_err : "expected string key");
      }
      SPROUT_GC_POP_LOCALS(1);
      return 0;
    }
    pos = sprout_json_skip_ws(pos);
    if (*pos != ':') {
      free(key);
      if (err_msg != NULL && *err_msg == NULL) *err_msg = dup_cstr("expected ':' after object key");
      SPROUT_GC_POP_LOCALS(1);
      return 0;
    }
    pos = sprout_json_skip_ws(pos + 1);
    long long value = json_parse_value(&pos, err_msg);
    if (err_msg != NULL && *err_msg != NULL) {
      free(key);
      SPROUT_GC_POP_LOCALS(1);
      return 0;
    }
    SPROUT_GC_PUSH_I64_LOCAL(value);
    reversed = sprout_make3(
      find_ctor_tag_by_name("stdlib.json.JsonObjectCons"),
      (long long)(uintptr_t)key,
      value,
      reversed
    );
    SPROUT_GC_POP_LOCALS(1);
    pos = sprout_json_skip_ws(pos);
    if (*pos == '}') {
      long long list = json_parse_reverse_object_items(reversed);
      SPROUT_GC_PUSH_I64_LOCAL(list);
      long long out = sprout_make1(find_ctor_tag_by_name("stdlib.json.JsonObject"), list);
      SPROUT_GC_POP_LOCALS(2);
      *pos_ptr = pos + 1;
      return out;
    }
    if (*pos != ',') {
      if (err_msg != NULL && *err_msg == NULL) *err_msg = dup_cstr("expected ',' or '}'");
      SPROUT_GC_POP_LOCALS(1);
      return 0;
    }
    pos = sprout_json_skip_ws(pos + 1);
  }
  if (err_msg != NULL && *err_msg == NULL) *err_msg = dup_cstr("unterminated object");
  SPROUT_GC_POP_LOCALS(1);
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
  int valid = end != NULL && *end == '\\0';
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
  if (pos == NULL || *pos == '\\0') {
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
    return sprout_make1(find_ctor_tag_by_name("stdlib.json.JsonString"), (long long)(uintptr_t)value);
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

const char* json_stringify(long long value) {
  ByteBuf out;
  buf_init(&out);
  json_append_value(&out, value);
  if (out.data == NULL) return dup_cstr("");
  return out.data;
}

long long json_parse(const char* raw) {
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
  if (pos == NULL || *pos != '\\0') {
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

static long long http_response_result(char* response_data) {
  if (response_data == NULL || response_data[0] == '\\0') {
    free(response_data);
    return http_err1(
      "stdlib.http.HttpNetwork",
      (long long)(uintptr_t)dup_cstr("remote closed connection without response")
    );
  }

  const char* sep = strstr(response_data, "\\r\\n\\r\\n");
  size_t sep_len = 4;
  if (sep == NULL) {
    sep = strstr(response_data, "\\n\\n");
    sep_len = 2;
  }
  if (sep == NULL) {
    free(response_data);
    return http_err1("stdlib.http.HttpDecode", (long long)(uintptr_t)dup_cstr("invalid http response"));
  }

  const char* line_end = strstr(response_data, "\\r\\n");
  size_t line_sep_len = 2;
  if (line_end == NULL || line_end > sep) {
    line_end = strstr(response_data, "\\n");
    line_sep_len = 1;
  }
  if (line_end == NULL || line_end > sep) {
    free(response_data);
    return http_err1("stdlib.http.HttpDecode", (long long)(uintptr_t)dup_cstr("invalid status line"));
  }

  const char* code_start = strchr(response_data, ' ');
  if (code_start == NULL || code_start >= line_end) {
    free(response_data);
    return http_err1("stdlib.http.HttpDecode", (long long)(uintptr_t)dup_cstr("invalid status line"));
  }
  code_start++;
  char* code_end = NULL;
  long long status = strtoll(code_start, &code_end, 10);
  if (code_end == code_start || code_end > line_end) {
    free(response_data);
    return http_err1("stdlib.http.HttpDecode", (long long)(uintptr_t)dup_cstr("invalid status code"));
  }
  if (status >= 400) {
    free(response_data);
    return http_err1("stdlib.http.HttpBadStatus", status);
  }

  const char* headers_start = line_end + line_sep_len;
  char* headers = dup_slice(headers_start, (size_t)(sep - headers_start));
  char* body_out = dup_cstr(sep + sep_len);
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
  if (anchor_path == NULL || anchor_path[0] == '\\0') {
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

typedef struct {
  int fd;
  int last_errno;
} TlsConn;

static OSStatus tls_read_func(SSLConnectionRef connection, void* data, size_t* dataLength) {
  TlsConn* conn = (TlsConn*)(uintptr_t)connection;
  if (conn == NULL || data == NULL || dataLength == NULL) return errSSLClosedAbort;
  size_t requested = *dataLength;
  ssize_t n;
  while (1) {
    n = recv(conn->fd, data, requested, 0);
    if (n >= 0) break;
    if (errno == EINTR) continue;
    conn->last_errno = errno;
    if (errno == EAGAIN || errno == EWOULDBLOCK) return errSSLWouldBlock;
    return errSSLClosedAbort;
  }
  conn->last_errno = 0;
  if (n == 0) {
    *dataLength = 0;
    return errSSLClosedGraceful;
  }
  *dataLength = (size_t)n;
  return noErr;
}

static OSStatus tls_write_func(SSLConnectionRef connection, const void* data, size_t* dataLength) {
  TlsConn* conn = (TlsConn*)(uintptr_t)connection;
  if (conn == NULL || data == NULL || dataLength == NULL) return errSSLClosedAbort;
  size_t requested = *dataLength;
  ssize_t n;
  while (1) {
    n = send(conn->fd, data, requested, 0);
    if (n >= 0) break;
    if (errno == EINTR) continue;
    conn->last_errno = errno;
    if (errno == EAGAIN || errno == EWOULDBLOCK) return errSSLWouldBlock;
    return errSSLClosedAbort;
  }
  conn->last_errno = 0;
  if (n == 0) {
    *dataLength = 0;
    return errSSLClosedAbort;
  }
  *dataLength = (size_t)n;
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

static OSStatus tls_handshake(SSLContextRef ctx, TlsConn* conn) {
  while (1) {
    conn->last_errno = 0;
    OSStatus status = SSLHandshake(ctx);
    if (status == noErr || tls_status_is_auth_event(status)) {
      return noErr;
    }
    if (status == errSSLWouldBlock) {
      if (tls_status_timed_out(conn)) return status;
      continue;
    }
    return status;
  }
}

static OSStatus tls_write_all(SSLContextRef ctx, TlsConn* conn, const char* data, size_t len) {
  size_t offset = 0;
  while (offset < len) {
    size_t written = len - offset;
    conn->last_errno = 0;
    OSStatus status = SSLWrite(ctx, data + offset, len - offset, &written);
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
    return http_err1("stdlib.http.HttpNetwork", (long long)(uintptr_t)dup_cstr(gai_strerror(gai)));
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
    return http_err1("stdlib.http.HttpNetwork", (long long)(uintptr_t)dup_cstr(strerror(last_errno)));
  }

  TlsConn tls = {.fd = fd, .last_errno = 0};
  SSLContextRef ctx = SSLCreateContext(NULL, kSSLClientSide, kSSLStreamType);
  if (ctx == NULL) {
    close(fd);
    return http_err1("stdlib.http.HttpNetwork", (long long)(uintptr_t)dup_cstr("tls context creation failed"));
  }
  OSStatus status = SSLSetIOFuncs(ctx, tls_read_func, tls_write_func);
  if (status == noErr) status = SSLSetSessionOption(ctx, kSSLSessionOptionBreakOnServerAuth, true);
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
      if (tls_status_is_auth_event(status)) {
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
          return http_err1(
            "stdlib.http.HttpNetwork",
            (long long)(uintptr_t)dup_cstr("tls certificate verification failed")
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
    return http_err1(
      "stdlib.http.HttpNetwork",
      (long long)(uintptr_t)tls_error_message("tls handshake failed", status, &tls)
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
    return http_err1(
      "stdlib.http.HttpNetwork",
      (long long)(uintptr_t)tls_error_message("tls write failed", status, &tls)
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
    return http_err1(
      "stdlib.http.HttpNetwork",
      (long long)(uintptr_t)tls_error_message("tls read failed", status, &tls)
    );
  }
  return http_response_result(response.data);
}
#pragma clang diagnostic pop
#endif

static void append_header_block(ByteBuf* out, const char* raw) {
  const char* line = raw;
  while (*line != '\\0') {
    const char* end = line;
    while (*end != '\\0' && *end != '\\n' && *end != '\\r') end++;
    const char* content_end = end;
    while (content_end > line && (content_end[-1] == ' ' || content_end[-1] == '\\t')) content_end--;
    const char* content_start = line;
    while (content_start < content_end && (*content_start == ' ' || *content_start == '\\t')) content_start++;
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
      buf_append_cstr(out, "\\r\\n");
    }
    while (*end == '\\r' || *end == '\\n') end++;
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
    long long out = http_err1("stdlib.http.HttpDecode", (long long)(uintptr_t)url_err);
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
  buf_append_cstr(&request, " HTTP/1.1\\r\\nHost: ");
  buf_append_cstr(&request, parsed.host);
  buf_append_cstr(&request, "\\r\\nConnection: close\\r\\n");
  buf_append_bytes(&request, header_block.data == NULL ? "" : header_block.data, header_block.len);
  char content_len[64];
  snprintf(content_len, sizeof(content_len), "Content-Length: %zu\\r\\n", strlen(body));
  buf_append_cstr(&request, content_len);
  buf_append_cstr(&request, "\\r\\n");
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
    return http_err1(
      "stdlib.http.HttpNetwork",
      (long long)(uintptr_t)dup_cstr("https unsupported on this platform")
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
    return http_err1("stdlib.http.HttpNetwork", (long long)(uintptr_t)dup_cstr(gai_strerror(gai)));
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
    return http_err1("stdlib.http.HttpNetwork", (long long)(uintptr_t)dup_cstr(strerror(last_errno)));
  }

  if (!send_all(fd, request.data, request.len)) {
    int send_errno = errno;
    free(request.data);
    close(fd);
    free_http_url(&parsed);
    if (send_errno == EAGAIN || send_errno == EWOULDBLOCK) return http_err0("stdlib.http.HttpTimeout");
    return http_err1("stdlib.http.HttpNetwork", (long long)(uintptr_t)dup_cstr(strerror(send_errno)));
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
        return http_err1(
          "stdlib.http.HttpNetwork",
          (long long)(uintptr_t)dup_cstr("remote closed connection without response")
        );
      }
      return http_err1("stdlib.http.HttpNetwork", (long long)(uintptr_t)dup_cstr(strerror(recv_errno)));
    }
    buf_append_bytes(&response, chunk, (size_t)n);
  }
  close(fd);
  free_http_url(&parsed);
  return http_response_result(response.data);
}

long long vector_empty() {
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

typedef struct {
  long long key;
  long long index;
  long long value;
} SortItem;

static int sort_item_cmp(const void* left, const void* right) {
  const SortItem* a = (const SortItem*)left;
  const SortItem* b = (const SortItem*)right;
  if (a->key < b->key) return -1;
  if (a->key > b->key) return 1;
  if (a->index < b->index) return -1;
  if (a->index > b->index) return 1;
  return 0;
}

long long vector_sort_by_int(long long decorated_list) {
  long long rooted_list = decorated_list;
  SPROUT_GC_PUSH_I64_LOCAL(rooted_list);

  size_t len = 0;
  size_t cap = 0;
  SortItem* items = NULL;
  long long cursor = decorated_list;
  while (1) {
    ManagedNode* node = find_managed_ptr((void*)(uintptr_t)cursor);
    if (node == NULL || node->kind != SPROUT_HEAP_OBJ) {
      free(items);
      SPROUT_GC_POP_LOCALS(1);
      tcp_fail("vector_sort_by_int: expected List");
    }
    SproutObj* obj = (SproutObj*)node->ptr;
    CtorMeta* meta = find_ctor(obj->tag);
    if (meta == NULL) {
      free(items);
      SPROUT_GC_POP_LOCALS(1);
      tcp_fail("vector_sort_by_int: missing list constructor metadata");
    }
    if (strcmp(meta->name, "Nil") == 0) {
      break;
    }
    if (strcmp(meta->name, "Cons") != 0) {
      free(items);
      SPROUT_GC_POP_LOCALS(1);
      tcp_fail("vector_sort_by_int: expected List");
    }
    ManagedNode* tuple_node = find_managed_ptr((void*)(uintptr_t)obj->f0);
    if (tuple_node == NULL || tuple_node->kind != SPROUT_HEAP_TUPLE || tuple_node->aux_slots != 3) {
      free(items);
      SPROUT_GC_POP_LOCALS(1);
      tcp_fail("vector_sort_by_int: expected decorated (Int, Int, a) tuples");
    }
    if (len == cap) {
      size_t new_cap = cap == 0 ? 16 : cap * 2;
      SortItem* new_items = (SortItem*)realloc(items, new_cap * sizeof(SortItem));
      if (new_items == NULL) {
        free(items);
        SPROUT_GC_POP_LOCALS(1);
        tcp_fail("vector_sort_by_int: out of memory");
      }
      items = new_items;
      cap = new_cap;
    }
    items[len].key = sprout_heap_child_value(tuple_node, 0);
    items[len].index = sprout_heap_child_value(tuple_node, 1);
    items[len].value = sprout_heap_child_value(tuple_node, 2);
    len++;
    cursor = obj->f1;
  }

  qsort(items, len, sizeof(SortItem), sort_item_cmp);
  VectorVal* out = sprout_alloc_vector_val("vector_sort_by_int: out of memory");
  out->len = (long long)len;
  out->cap = (long long)len;
  out->data = len == 0 ? NULL : sprout_alloc_vector_data(len, "vector_sort_by_int: out of memory");
  for (size_t i = 0; i < len; i++) {
    out->data[i] = items[i].value;
  }
  free(items);
  SPROUT_GC_POP_LOCALS(1);
  return (long long)(uintptr_t)out;
}

long long map_empty() {
  MapVal* m = sprout_alloc_map_val("map_empty: out of memory");
  m->len = 0;
  m->cap = 0;
  m->entries = NULL;
  return (long long)(uintptr_t)m;
}

static long long map_find_index(MapVal* m, const char* key) {
  for (long long i = 0; i < m->len; i++) {
    if (strcmp(m->entries[i].key, key) == 0) return i;
  }
  return -1;
}

long long map_get(long long map_h, const char* key) {
  long long rooted_map = map_h;
  SPROUT_GC_PUSH_I64_LOCAL(rooted_map);
  MapVal* m = (MapVal*)(uintptr_t)map_h;
  if (m == NULL) tcp_fail("map_get: null map");
  if (key == NULL) tcp_fail("map_get: null key");
  long long idx = map_find_index(m, key);
  if (idx < 0) {
    SPROUT_GC_POP_LOCALS(1);
    return sprout_make0(find_ctor_tag_by_name("Nothing"));
  }
  long long out = sprout_make1(find_ctor_tag_by_name("Just"), m->entries[idx].value);
  SPROUT_GC_POP_LOCALS(1);
  return out;
}

long long map_set(long long map_h, const char* key, long long value) {
  long long rooted_map = map_h;
  long long rooted_value = value;
  SPROUT_GC_PUSH_I64_LOCAL(rooted_map);
  SPROUT_GC_PUSH_I64_LOCAL(rooted_value);
  MapVal* src = (MapVal*)(uintptr_t)map_h;
  if (src == NULL) tcp_fail("map_set: null map");
  if (key == NULL) tcp_fail("map_set: null key");
  long long existing = map_find_index(src, key);
  long long out_len = existing >= 0 ? src->len : (src->len + 1);

  MapVal* out = sprout_alloc_map_val("map_set: out of memory");
  out->len = out_len;
  out->cap = out_len;
  out->entries = sprout_alloc_map_entries((size_t)out_len, "map_set: out of memory");

  for (long long i = 0; i < src->len; i++) {
    out->entries[i].key = sprout_strdup_counted(&g_debug_alloc_map, src->entries[i].key, "map_set: out of memory");
    out->entries[i].value = src->entries[i].value;
  }
  if (existing >= 0) {
    out->entries[existing].value = rooted_value;
  } else {
    out->entries[src->len].key = sprout_strdup_counted(&g_debug_alloc_map, key, "map_set: out of memory");
    out->entries[src->len].value = rooted_value;
  }
  SPROUT_GC_POP_LOCALS(2);
  return (long long)(uintptr_t)out;
}

long long map_remove(long long map_h, const char* key) {
  long long rooted_map = map_h;
  SPROUT_GC_PUSH_I64_LOCAL(rooted_map);
  MapVal* src = (MapVal*)(uintptr_t)map_h;
  if (src == NULL) tcp_fail("map_remove: null map");
  if (key == NULL) tcp_fail("map_remove: null key");
  long long remove_idx = map_find_index(src, key);
  if (remove_idx < 0) {
    SPROUT_GC_POP_LOCALS(1);
    return map_h;
  }

  MapVal* out = sprout_alloc_map_val("map_remove: out of memory");
  out->len = src->len - 1;
  out->cap = out->len;
  out->entries = sprout_alloc_map_entries((size_t)out->len, "map_remove: out of memory");

  long long j = 0;
  for (long long i = 0; i < src->len; i++) {
    if (i == remove_idx) continue;
    out->entries[j].key = sprout_strdup_counted(&g_debug_alloc_map, src->entries[i].key, "map_remove: out of memory");
    out->entries[j].value = src->entries[i].value;
    j++;
  }
  SPROUT_GC_POP_LOCALS(1);
  return (long long)(uintptr_t)out;
}

long long map_size(long long map_h) {
  MapVal* m = (MapVal*)(uintptr_t)map_h;
  if (m == NULL) tcp_fail("map_size: null map");
  return m->len;
}

long long map_nth_key(long long map_h, long long index) {
  long long rooted_map = map_h;
  SPROUT_GC_PUSH_I64_LOCAL(rooted_map);
  MapVal* m = (MapVal*)(uintptr_t)map_h;
  if (m == NULL) tcp_fail("map_nth_key: null map");
  if (index < 0 || index >= m->len) {
    SPROUT_GC_POP_LOCALS(1);
    return sprout_make0(find_ctor_tag_by_name("Nothing"));
  }
  char* key = sprout_strdup_counted(&g_debug_alloc_map, m->entries[index].key, "map_nth_key: out of memory");
  long long out = sprout_make1(find_ctor_tag_by_name("Just"), (long long)(uintptr_t)key);
  SPROUT_GC_POP_LOCALS(1);
  return out;
}

long long map_nth_value(long long map_h, long long index) {
  long long rooted_map = map_h;
  SPROUT_GC_PUSH_I64_LOCAL(rooted_map);
  MapVal* m = (MapVal*)(uintptr_t)map_h;
  if (m == NULL) tcp_fail("map_nth_value: null map");
  if (index < 0 || index >= m->len) {
    SPROUT_GC_POP_LOCALS(1);
    return sprout_make0(find_ctor_tag_by_name("Nothing"));
  }
  long long out = sprout_make1(find_ctor_tag_by_name("Just"), m->entries[index].value);
  SPROUT_GC_POP_LOCALS(1);
  return out;
}

long long bytes_empty() {
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

long long bytes_from_utf8(const char* raw) {
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
    long long err = sprout_make1(
      find_ctor_tag_by_name("stdlib.bytes.Utf8DecodeError"),
      (long long)(uintptr_t)dup_cstr(reason)
    );
    SPROUT_GC_PUSH_I64_LOCAL(err);
    long long out = sprout_make1(find_ctor_tag_by_name("Err"), err);
    SPROUT_GC_POP_LOCALS(2);
    return out;
  }
  char* out = (char*)malloc(value->len + 1);
  if (out == NULL) tcp_fail("bytes_to_utf8: out of memory");
  if (value->len > 0) memcpy(out, value->data, value->len);
  out[value->len] = '\\0';
  SPROUT_GC_POP_LOCALS(1);
  return sprout_make1(find_ctor_tag_by_name("Ok"), (long long)(uintptr_t)out);
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
  out[j] = '\\0';
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
  long long err = sprout_make1(find_ctor_tag_by_name(ctor_name), (long long)(uintptr_t)dup_cstr(payload));
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
  BytesVal* value = (BytesVal*)(uintptr_t)bytes_h;
  if (value == NULL) tcp_fail("crypto_base64_encode: null bytes");
  char* out = base64_encode_bytes(value->data, value->len);
  if (out == NULL) tcp_fail("crypto_base64_encode: out of memory");
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
    if (connect(fd, it->ai_addr, it->ai_addrlen) == 0) {
      error_msg = NULL;
      break;
    }
    error_msg = strerror(errno);
    close(fd);
    fd = -1;
  }
  freeaddrinfo(resolved);

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
  int fd = accept(g_listener_fd[listener], NULL, NULL);
  if (fd < 0) tcp_fail("tcp_accept: accept failed");
  long long h = alloc_conn_handle();
  if (h < 0) {
    close(fd);
    tcp_fail("tcp_accept: connection table full");
  }
  g_conn_fd[h] = fd;
  g_conn_used[h] = 1;
  return h;
}

const char* tcp_read(long long conn) {
  if (conn <= 0 || conn >= 2048 || !g_conn_used[conn]) tcp_fail("tcp_read: unknown connection handle");
  char* buf = (char*)malloc(65537);
  if (buf == NULL) tcp_fail("tcp_read: out of memory");
  ssize_t n = recv(g_conn_fd[conn], buf, 65536, 0);
  if (n < 0) {
    free(buf);
    tcp_fail("tcp_read: recv failed");
  }
  buf[n] = '\\0';
  return buf;
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
    if (n <= 0) tcp_fail("tcp_write: send failed");
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
    if (n <= 0) {
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
    const char* payload = tcp_read(conn);
    tcp_write(conn, payload);
    tcp_close(conn);
    served++;
  }
  tcp_close_listener(listener);
  return 0;
}
""".replace(
    "__SPROUT_ANALYSIS_BRIDGE_RUNTIME__",
    render_analysis_bridge_runtime_c(embedded_analysis_service_cmd),
).replace(
    "__SPROUT_ANALYSIS_BRIDGE_REQUEST_HELPERS__",
    render_analysis_bridge_helpers_c(),
).replace(
    "__SPROUT_ANALYSIS_BRIDGE_RESPONSE_HELPERS__",
    "",
).replace(
    "__SPROUT_ANALYSIS_OP_CHECK_SOURCE__", OP_CHECK_SOURCE
).replace(
    "__SPROUT_ANALYSIS_OP_TYPE_OF_IN_SOURCE__", OP_TYPE_OF_IN_SOURCE
).replace(
    "__SPROUT_ANALYSIS_OP_DECLARED_NAMES_IN_SOURCE__", OP_DECLARED_NAMES_IN_SOURCE
).replace(
    "__SPROUT_ANALYSIS_OP_EXPORTED_NAMES_IN_SOURCE__", OP_EXPORTED_NAMES_IN_SOURCE
).replace(
    "__SPROUT_ANALYSIS_OP_SYMBOL_INVENTORY_IN_SOURCE__", OP_SYMBOL_INVENTORY_IN_SOURCE
).replace(
    "__SPROUT_ANALYSIS_OP_DIAGNOSTICS_IN_SOURCE__", OP_DIAGNOSTICS_IN_SOURCE
).replace(
    "__SPROUT_ANALYSIS_OP_SYMBOL_LOCATIONS_IN_SOURCE__", OP_SYMBOL_LOCATIONS_IN_SOURCE
).replace(
    "__SPROUT_ANALYSIS_OP_INSTANCES_IN_SOURCE__", OP_INSTANCES_IN_SOURCE
).replace(
    "__SPROUT_ANALYSIS_OP_EVAL_EXPR_IN_SOURCE__", OP_EVAL_EXPR_IN_SOURCE
).replace(
    "__SPROUT_ANALYSIS_OP_COMPLETE_IN_STATE__", OP_COMPLETE_IN_STATE
)
    with tempfile.NamedTemporaryFile("w", suffix=".c", delete=False, encoding="utf-8") as tmp_c:
        tmp_c.write(runtime_c)
        c_path = Path(tmp_c.name)
    try:
        clang_cmd = [clang, str(ll_path), str(c_path), "-O2"]
        if sys.platform == "darwin":
            clang_cmd.extend(["-framework", "Security", "-framework", "CoreFoundation"])
        clang_cmd.extend(["-o", str(out)])
        subprocess.run(clang_cmd, check=True)
    finally:
        ll_path.unlink(missing_ok=True)
        c_path.unlink(missing_ok=True)
    return 0




def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sprout")
    sub = parser.add_subparsers(dest="command", required=True)

    p_parse = sub.add_parser("parse", help="parse a Sprout file and print AST as JSON")
    p_parse.add_argument("file", type=Path)
    p_fmt = sub.add_parser("fmt", help="format a Sprout file")
    p_fmt.add_argument("file", type=Path)
    p_fmt.add_argument("--check", action="store_true", help="report whether formatting changes are needed")
    p_lint = sub.add_parser("lint", help="lint a Sprout file for baseline style issues")
    p_lint.add_argument("file", type=Path)
    p_check = sub.add_parser("check", help="typecheck a Sprout file")
    p_check.add_argument("file", type=Path)
    p_check.add_argument("--with-stdlib", action="store_true", help="load stdlib prelude")
    p_check.add_argument(
        "--with-http-stdlib",
        action="store_true",
        help="load stdlib http helpers",
    )
    p_run = sub.add_parser("run", help="typecheck and run a Sprout file")
    p_run.add_argument("file", type=Path)
    p_run.add_argument("--with-stdlib", action="store_true", help="load stdlib prelude")
    p_run.add_argument(
        "--with-http-stdlib",
        action="store_true",
        help="load stdlib http helpers",
    )
    p_run.add_argument("program_args", nargs="*", help="arguments exposed to the program via argv_get")
    p_compile = sub.add_parser("compile", help="typecheck and compile a Sprout file")
    p_compile.add_argument("file", type=Path)
    p_compile.add_argument("-o", "--output", type=Path, required=True, help="output file")
    p_compile.add_argument("--with-stdlib", action="store_true", help="load stdlib prelude")
    p_compile.add_argument(
        "--with-http-stdlib",
        action="store_true",
        help="load stdlib http helpers",
    )
    p_compile.add_argument(
        "--native",
        action="store_true",
        help="emit native binary with clang (default writes LLVM .ll text)",
    )
    sub.add_parser("analysis-service", help=argparse.SUPPRESS)
    sub.add_parser("analysis-stdio", help=argparse.SUPPRESS)
    p_repl = sub.add_parser("repl", help="start a simple interactive Sprout REPL")
    p_repl.add_argument(
        "--native",
        action="store_true",
        help="experimentally launch the Sprout REPL frontend as a native binary via the analysis-service bridge",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "parse":
            return cmd_parse(args.file)
        if args.command == "fmt":
            return cmd_fmt(args.file, check=args.check)
        if args.command == "lint":
            return cmd_lint(args.file)
        if args.command == "check":
            return cmd_check(
                args.file,
                with_stdlib=args.with_stdlib,
                with_http_stdlib=args.with_http_stdlib,
            )
        if args.command == "run":
            return cmd_run(
                args.file,
                with_stdlib=args.with_stdlib,
                with_http_stdlib=args.with_http_stdlib,
                program_args=args.program_args,
            )
        if args.command == "compile":
            return cmd_compile(
                args.file,
                out=args.output,
                with_stdlib=args.with_stdlib,
                with_http_stdlib=args.with_http_stdlib,
                native=args.native,
            )
        if args.command == "analysis-service":
            return cmd_analysis_cli(args.command)
        if args.command == "analysis-stdio":
            return cmd_analysis_cli(args.command)
        if args.command == "repl":
            return cmd_repl(native=args.native)
    except (
        ParseError,
        TokenizeError,
        TypeCheckError,
        RuntimeError,
        CodegenError,
        ElaborateError,
        ModuleLoadError,
        SurfaceCheckError,
        TypeclassLoweringError,
        subprocess.CalledProcessError,
    ) as exc:
        print(f"error: {exc}")
        return 1

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
