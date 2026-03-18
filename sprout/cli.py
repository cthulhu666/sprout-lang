from __future__ import annotations

import argparse
import atexit
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from .ast import to_dict
from .codegen_llvm import CodegenError, compile_to_llvm
from .formatter import format_source, lint_source
from .interpreter import RuntimeError, run_program
from .module_loader import ModuleLoadError, load_module_bundle, resolve_program_names
from .parser import ParseError, parse
from .surface_checks import SurfaceCheckError, validate_public_surface
from .stdlib import with_http_prelude, with_prelude
from .tokenizer import TokenizeError
from .typeclass_lowering import TypeclassLoweringError, lower_typeclasses
from .typechecker import TypeCheckError, typecheck_program

_REPL_HISTORY_LIMIT = 1000


def _bundle_has_implicit_prelude(bundle: object | None) -> bool:
    if bundle is None:
        return False
    return any(path.name == "prelude.sprout" and "stdlib" in path.parts for path in bundle.modules)


def cmd_parse(path: Path) -> int:
    bundle = load_module_bundle(path)
    source = bundle.source
    tree = parse(source)
    resolve_program_names(tree, bundle)
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
        resolve_program_names(tree, bundle)
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
        resolve_program_names(tree, bundle)
    validate_public_surface(tree, bundle)
    typecheck_program(tree)
    validate_public_surface(tree, bundle)
    lowered = lower_typeclasses(tree)
    typecheck_program(lowered)
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
        resolve_program_names(tree, bundle)
    validate_public_surface(tree, bundle)
    typecheck_program(tree)
    validate_public_surface(tree, bundle)
    lowered = lower_typeclasses(tree)
    typecheck_program(lowered)
    llvm_ir = compile_to_llvm(lowered)

    if not native:
        out.write_text(llvm_ir, encoding="utf-8")
        return 0

    clang = shutil.which("clang")
    if clang is None:
        raise CodegenError("clang not found; install clang or compile with --emit-llvm only")

    with tempfile.NamedTemporaryFile("w", suffix=".ll", delete=False, encoding="utf-8") as tmp:
        tmp.write(llvm_ir)
        ll_path = Path(tmp.name)
    runtime_c = """#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/types.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <netdb.h>
#include <errno.h>
#include <sys/time.h>
#include <unistd.h>

typedef struct {
  long long tag;
  long long f0;
  long long f1;
  long long f2;
} SproutObj;

typedef struct ObjNode {
  SproutObj* ptr;
  struct ObjNode* next;
} ObjNode;

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
  char* host;
  char* port;
  char* path;
} HttpUrl;

static ObjNode* g_objs = NULL;
static SproutObj* g_nothing_singleton = NULL;
static CtorMeta g_ctor_meta[2048];
static long long g_ctor_meta_len = 0;
static int g_listener_fd[2048];
static int g_listener_used[2048];
static int g_conn_fd[2048];
static int g_conn_used[2048];
static long long g_next_listener_handle = 1;
static long long g_next_conn_handle = 1;
static int g_sprout_argc = 0;
static char** g_sprout_argv = NULL;

static void tcp_fail(const char* msg);
long long sprout_make0(long long tag);
long long sprout_make1(long long tag, long long a0);
static char* dup_cstr(const char* s);
static void json_append_value(ByteBuf* out, long long value);

static long long box_ptr(SproutObj* p) {
  return (long long)(uintptr_t)p;
}

static SproutObj* unbox_ptr(long long h) {
  return (SproutObj*)(uintptr_t)h;
}

static void register_obj(SproutObj* p) {
  ObjNode* n = (ObjNode*)malloc(sizeof(ObjNode));
  n->ptr = p;
  n->next = g_objs;
  g_objs = n;
}

static int is_obj_handle(long long h) {
  uintptr_t u = (uintptr_t)h;
  for (ObjNode* n = g_objs; n != NULL; n = n->next) {
    if ((uintptr_t)n->ptr == u) return 1;
  }
  return 0;
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
  if (is_obj_handle(v)) {
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
long long env_get(const char* name) {
  if (name == NULL) tcp_fail("env_get: null name");
  const char* value = getenv(name);
  if (value == NULL) return sprout_make0(find_ctor_tag_by_name("Nothing"));
  return sprout_make1(find_ctor_tag_by_name("Just"), (long long)(uintptr_t)value);
}
long long sprout_set_argv(int argc, char** argv) {
  g_sprout_argc = argc;
  g_sprout_argv = argv;
  return 0;
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
long long read_int_lines(const char* path) {
  if (path == NULL) tcp_fail("read_int_lines: null path");
  FILE* f = fopen(path, "r");
  if (f == NULL) tcp_fail("read_int_lines: cannot open file");
  VectorVal* v = (VectorVal*)malloc(sizeof(VectorVal));
  if (v == NULL) tcp_fail("read_int_lines: out of memory");
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
      long long* new_data = (long long*)realloc(v->data, (size_t)new_cap * sizeof(long long));
      if (new_data == NULL) tcp_fail("read_int_lines: out of memory");
      v->data = new_data;
      v->cap = new_cap;
    }
    v->data[v->len] = value;
    v->len++;
  }
  fclose(f);
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
      g_nothing_singleton = (SproutObj*)malloc(sizeof(SproutObj));
      g_nothing_singleton->tag = tag;
      g_nothing_singleton->f0 = 0;
      g_nothing_singleton->f1 = 0;
      g_nothing_singleton->f2 = 0;
      register_obj(g_nothing_singleton);
    }
    return box_ptr(g_nothing_singleton);
  }
  SproutObj* o = (SproutObj*)malloc(sizeof(SproutObj));
  o->tag = tag;
  o->f0 = 0;
  o->f1 = 0;
  o->f2 = 0;
  register_obj(o);
  return box_ptr(o);
}
long long sprout_make1(long long tag, long long a0) {
  SproutObj* o = (SproutObj*)malloc(sizeof(SproutObj));
  o->tag = tag;
  o->f0 = a0;
  o->f1 = 0;
  o->f2 = 0;
  register_obj(o);
  return box_ptr(o);
}
long long sprout_make2(long long tag, long long a0, long long a1) {
  SproutObj* o = (SproutObj*)malloc(sizeof(SproutObj));
  o->tag = tag;
  o->f0 = a0;
  o->f1 = a1;
  o->f2 = 0;
  register_obj(o);
  return box_ptr(o);
}
long long sprout_make3(long long tag, long long a0, long long a1, long long a2) {
  SproutObj* o = (SproutObj*)malloc(sizeof(SproutObj));
  o->tag = tag;
  o->f0 = a0;
  o->f1 = a1;
  o->f2 = a2;
  register_obj(o);
  return box_ptr(o);
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

long long str_len(const char* s) {
  if (s == NULL) tcp_fail("str_len: null input");
  return (long long)strlen(s);
}

_Bool str_eq(const char* left, const char* right) {
  if (left == NULL || right == NULL) tcp_fail("str_eq: null input");
  return strcmp(left, right) == 0;
}

const char* str_slice(const char* s, long long start, long long length) {
  if (s == NULL) tcp_fail("str_slice: null input");
  if (start < 0 || length < 0) tcp_fail("str_slice: start/length must be >= 0");
  size_t slen = strlen(s);
  if ((size_t)start >= slen) {
    char* out = (char*)malloc(1);
    if (out == NULL) tcp_fail("str_slice: out of memory");
    out[0] = '\\0';
    return out;
  }
  size_t avail = slen - (size_t)start;
  size_t want = (size_t)length;
  size_t take = want < avail ? want : avail;
  char* out = (char*)malloc(take + 1);
  if (out == NULL) tcp_fail("str_slice: out of memory");
  memcpy(out, s + start, take);
  out[take] = '\\0';
  return out;
}

long long str_find(const char* haystack, const char* needle) {
  if (haystack == NULL || needle == NULL) tcp_fail("str_find: null input");
  const char* pos = strstr(haystack, needle);
  if (pos == NULL) return -1;
  return (long long)(pos - haystack);
}

_Bool str_starts_with(const char* s, const char* prefix) {
  if (s == NULL || prefix == NULL) tcp_fail("str_starts_with: null input");
  size_t prefix_len = strlen(prefix);
  return strncmp(s, prefix, prefix_len) == 0;
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

static char* dup_slice(const char* start, size_t len) {
  char* out = (char*)malloc(len + 1);
  if (out == NULL) tcp_fail("http_request: out of memory");
  memcpy(out, start, len);
  out[len] = '\\0';
  return out;
}

static char* dup_cstr(const char* text) {
  return dup_slice(text, strlen(text));
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
  return sprout_make1(find_ctor_tag_by_name("Err"), err);
}

static long long http_err1(const char* ctor_name, long long payload) {
  long long err = sprout_make1(find_ctor_tag_by_name(ctor_name), payload);
  return sprout_make1(find_ctor_tag_by_name("Err"), err);
}

static long long http_ok_response(long long status, const char* headers, const char* body) {
  long long resp = sprout_make3(
    find_ctor_tag_by_name("stdlib.http.HttpResponse"),
    status,
    (long long)(uintptr_t)headers,
    (long long)(uintptr_t)body
  );
  return sprout_make1(find_ctor_tag_by_name("Ok"), resp);
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

const char* json_stringify(long long value) {
  ByteBuf out;
  buf_init(&out);
  json_append_value(&out, value);
  if (out.data == NULL) return dup_cstr("");
  return out.data;
}

static int parse_http_url(const char* url, HttpUrl* out, char** err) {
  const char* prefix = "http://";
  size_t prefix_len = strlen(prefix);
  if (strncmp(url, prefix, prefix_len) != 0) {
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
    out->port = dup_cstr("80");
  }
  out->path = slash != NULL ? dup_cstr(slash) : dup_cstr("/");
  return 1;
}

static void free_http_url(HttpUrl* url) {
  free(url->host);
  free(url->port);
  free(url->path);
}

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

  struct addrinfo hints;
  memset(&hints, 0, sizeof(hints));
  hints.ai_socktype = SOCK_STREAM;
  hints.ai_family = AF_UNSPEC;
  struct addrinfo* infos = NULL;
  int gai = getaddrinfo(parsed.host, parsed.port, &hints, &infos);
  if (gai != 0) {
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
    free_http_url(&parsed);
    if (last_errno == EAGAIN || last_errno == EWOULDBLOCK) return http_err0("stdlib.http.HttpTimeout");
    return http_err1("stdlib.http.HttpNetwork", (long long)(uintptr_t)dup_cstr(strerror(last_errno)));
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

  if (response.data == NULL || response.len == 0) {
    free(response.data);
    return http_err1(
      "stdlib.http.HttpNetwork",
      (long long)(uintptr_t)dup_cstr("remote closed connection without response")
    );
  }

  const char* sep = strstr(response.data, "\\r\\n\\r\\n");
  size_t sep_len = 4;
  if (sep == NULL) {
    sep = strstr(response.data, "\\n\\n");
    sep_len = 2;
  }
  if (sep == NULL) {
    free(response.data);
    return http_err1("stdlib.http.HttpDecode", (long long)(uintptr_t)dup_cstr("invalid http response"));
  }

  const char* line_end = strstr(response.data, "\\r\\n");
  size_t line_sep_len = 2;
  if (line_end == NULL || line_end > sep) {
    line_end = strstr(response.data, "\\n");
    line_sep_len = 1;
  }
  if (line_end == NULL || line_end > sep) {
    free(response.data);
    return http_err1("stdlib.http.HttpDecode", (long long)(uintptr_t)dup_cstr("invalid status line"));
  }

  const char* code_start = strchr(response.data, ' ');
  if (code_start == NULL || code_start >= line_end) {
    free(response.data);
    return http_err1("stdlib.http.HttpDecode", (long long)(uintptr_t)dup_cstr("invalid status line"));
  }
  code_start++;
  char* code_end = NULL;
  long long status = strtoll(code_start, &code_end, 10);
  if (code_end == code_start || code_end > line_end) {
    free(response.data);
    return http_err1("stdlib.http.HttpDecode", (long long)(uintptr_t)dup_cstr("invalid status code"));
  }
  if (status >= 400) {
    free(response.data);
    return http_err1("stdlib.http.HttpBadStatus", status);
  }

  const char* headers_start = line_end + line_sep_len;
  char* headers = dup_slice(headers_start, (size_t)(sep - headers_start));
  char* body_out = dup_cstr(sep + sep_len);
  free(response.data);
  return http_ok_response(status, headers, body_out);
}

long long vector_empty() {
  VectorVal* v = (VectorVal*)malloc(sizeof(VectorVal));
  if (v == NULL) tcp_fail("vector_empty: out of memory");
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
  VectorVal* v = (VectorVal*)(uintptr_t)vec;
  if (v == NULL) tcp_fail("vector_get: null vector");
  if (index < 0 || index >= v->len) {
    return sprout_make0(find_ctor_tag_by_name("Nothing"));
  }
  return sprout_make1(find_ctor_tag_by_name("Just"), v->data[index]);
}

long long vector_set(long long vec, long long index, long long value) {
  VectorVal* src = (VectorVal*)(uintptr_t)vec;
  if (src == NULL) tcp_fail("vector_set: null vector");
  VectorVal* out = (VectorVal*)malloc(sizeof(VectorVal));
  if (out == NULL) tcp_fail("vector_set: out of memory");
  out->len = src->len;
  out->cap = src->len;
  if (out->cap == 0) {
    out->data = NULL;
    return (long long)(uintptr_t)out;
  }
  out->data = (long long*)malloc((size_t)out->cap * sizeof(long long));
  if (out->data == NULL) tcp_fail("vector_set: out of memory");
  memcpy(out->data, src->data, (size_t)out->len * sizeof(long long));
  if (index >= 0 && index < out->len) {
    out->data[index] = value;
  }
  return (long long)(uintptr_t)out;
}

long long vector_append(long long vec, long long value) {
  VectorVal* src = (VectorVal*)(uintptr_t)vec;
  if (src == NULL) tcp_fail("vector_append: null vector");
  VectorVal* out = (VectorVal*)malloc(sizeof(VectorVal));
  if (out == NULL) tcp_fail("vector_append: out of memory");
  out->len = src->len + 1;
  out->cap = out->len;
  out->data = (long long*)malloc((size_t)out->cap * sizeof(long long));
  if (out->data == NULL) tcp_fail("vector_append: out of memory");
  if (src->len > 0) {
    memcpy(out->data, src->data, (size_t)src->len * sizeof(long long));
  }
  out->data[src->len] = value;
  return (long long)(uintptr_t)out;
}

long long map_empty() {
  MapVal* m = (MapVal*)malloc(sizeof(MapVal));
  if (m == NULL) tcp_fail("map_empty: out of memory");
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
  MapVal* m = (MapVal*)(uintptr_t)map_h;
  if (m == NULL) tcp_fail("map_get: null map");
  if (key == NULL) tcp_fail("map_get: null key");
  long long idx = map_find_index(m, key);
  if (idx < 0) {
    return sprout_make0(find_ctor_tag_by_name("Nothing"));
  }
  return sprout_make1(find_ctor_tag_by_name("Just"), m->entries[idx].value);
}

long long map_set(long long map_h, const char* key, long long value) {
  MapVal* src = (MapVal*)(uintptr_t)map_h;
  if (src == NULL) tcp_fail("map_set: null map");
  if (key == NULL) tcp_fail("map_set: null key");
  long long existing = map_find_index(src, key);
  long long out_len = existing >= 0 ? src->len : (src->len + 1);

  MapVal* out = (MapVal*)malloc(sizeof(MapVal));
  if (out == NULL) tcp_fail("map_set: out of memory");
  out->len = out_len;
  out->cap = out_len;
  out->entries = out_len == 0 ? NULL : (MapEntry*)malloc((size_t)out_len * sizeof(MapEntry));
  if (out_len > 0 && out->entries == NULL) tcp_fail("map_set: out of memory");

  for (long long i = 0; i < src->len; i++) {
    out->entries[i].key = strdup(src->entries[i].key);
    if (out->entries[i].key == NULL) tcp_fail("map_set: out of memory");
    out->entries[i].value = src->entries[i].value;
  }
  if (existing >= 0) {
    out->entries[existing].value = value;
  } else {
    out->entries[src->len].key = strdup(key);
    if (out->entries[src->len].key == NULL) tcp_fail("map_set: out of memory");
    out->entries[src->len].value = value;
  }
  return (long long)(uintptr_t)out;
}

long long map_remove(long long map_h, const char* key) {
  MapVal* src = (MapVal*)(uintptr_t)map_h;
  if (src == NULL) tcp_fail("map_remove: null map");
  if (key == NULL) tcp_fail("map_remove: null key");
  long long remove_idx = map_find_index(src, key);
  if (remove_idx < 0) return map_h;

  MapVal* out = (MapVal*)malloc(sizeof(MapVal));
  if (out == NULL) tcp_fail("map_remove: out of memory");
  out->len = src->len - 1;
  out->cap = out->len;
  out->entries = out->len == 0 ? NULL : (MapEntry*)malloc((size_t)out->len * sizeof(MapEntry));
  if (out->len > 0 && out->entries == NULL) tcp_fail("map_remove: out of memory");

  long long j = 0;
  for (long long i = 0; i < src->len; i++) {
    if (i == remove_idx) continue;
    out->entries[j].key = strdup(src->entries[i].key);
    if (out->entries[j].key == NULL) tcp_fail("map_remove: out of memory");
    out->entries[j].value = src->entries[i].value;
    j++;
  }
  return (long long)(uintptr_t)out;
}

long long map_size(long long map_h) {
  MapVal* m = (MapVal*)(uintptr_t)map_h;
  if (m == NULL) tcp_fail("map_size: null map");
  return m->len;
}

long long map_nth_key(long long map_h, long long index) {
  MapVal* m = (MapVal*)(uintptr_t)map_h;
  if (m == NULL) tcp_fail("map_nth_key: null map");
  if (index < 0 || index >= m->len) {
    return sprout_make0(find_ctor_tag_by_name("Nothing"));
  }
  char* key = strdup(m->entries[index].key);
  if (key == NULL) tcp_fail("map_nth_key: out of memory");
  return sprout_make1(find_ctor_tag_by_name("Just"), (long long)(uintptr_t)key);
}

long long map_nth_value(long long map_h, long long index) {
  MapVal* m = (MapVal*)(uintptr_t)map_h;
  if (m == NULL) tcp_fail("map_nth_value: null map");
  if (index < 0 || index >= m->len) {
    return sprout_make0(find_ctor_tag_by_name("Nothing"));
  }
  return sprout_make1(find_ctor_tag_by_name("Just"), m->entries[index].value);
}

long long bytes_empty() {
  BytesVal* out = (BytesVal*)malloc(sizeof(BytesVal));
  if (out == NULL) tcp_fail("bytes_empty: out of memory");
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
  BytesVal* value = (BytesVal*)(uintptr_t)bytes_h;
  if (value == NULL) tcp_fail("bytes_slice: null bytes");
  if (start < 0 || count < 0) tcp_fail("bytes_slice: start/count must be >= 0");
  size_t s = (size_t)start;
  size_t c = (size_t)count;
  if (s > value->len) s = value->len;
  if (s + c > value->len) c = value->len - s;
  BytesVal* out = (BytesVal*)malloc(sizeof(BytesVal));
  if (out == NULL) tcp_fail("bytes_slice: out of memory");
  out->len = c;
  out->data = c == 0 ? NULL : (unsigned char*)malloc(c);
  if (c > 0 && out->data == NULL) tcp_fail("bytes_slice: out of memory");
  if (c > 0) memcpy(out->data, value->data + s, c);
  return (long long)(uintptr_t)out;
}

long long bytes_append(long long left_h, long long right_h) {
  BytesVal* left = (BytesVal*)(uintptr_t)left_h;
  BytesVal* right = (BytesVal*)(uintptr_t)right_h;
  if (left == NULL || right == NULL) tcp_fail("bytes_append: null bytes");
  BytesVal* out = (BytesVal*)malloc(sizeof(BytesVal));
  if (out == NULL) tcp_fail("bytes_append: out of memory");
  out->len = left->len + right->len;
  out->data = out->len == 0 ? NULL : (unsigned char*)malloc(out->len);
  if (out->len > 0 && out->data == NULL) tcp_fail("bytes_append: out of memory");
  if (left->len > 0) memcpy(out->data, left->data, left->len);
  if (right->len > 0) memcpy(out->data + left->len, right->data, right->len);
  return (long long)(uintptr_t)out;
}

long long bytes_singleton(long long value) {
  if (value < 0 || value > 255) tcp_fail("bytes_singleton: byte out of range");
  BytesVal* out = (BytesVal*)malloc(sizeof(BytesVal));
  if (out == NULL) tcp_fail("bytes_singleton: out of memory");
  out->len = 1;
  out->data = (unsigned char*)malloc(1);
  if (out->data == NULL) tcp_fail("bytes_singleton: out of memory");
  out->data[0] = (unsigned char)value;
  return (long long)(uintptr_t)out;
}

long long bytes_from_utf8(const char* raw) {
  if (raw == NULL) tcp_fail("bytes_from_utf8: null input");
  size_t len = strlen(raw);
  BytesVal* out = (BytesVal*)malloc(sizeof(BytesVal));
  if (out == NULL) tcp_fail("bytes_from_utf8: out of memory");
  out->len = len;
  out->data = len == 0 ? NULL : (unsigned char*)malloc(len);
  if (len > 0 && out->data == NULL) tcp_fail("bytes_from_utf8: out of memory");
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
  BytesVal* value = (BytesVal*)(uintptr_t)bytes_h;
  if (value == NULL) tcp_fail("bytes_to_utf8: null bytes");
  const char* reason = NULL;
  if (!utf8_validate(value->data, value->len, &reason)) {
    long long err = sprout_make1(
      find_ctor_tag_by_name("stdlib.bytes.Utf8DecodeError"),
      (long long)(uintptr_t)dup_cstr(reason)
    );
    return sprout_make1(find_ctor_tag_by_name("Err"), err);
  }
  char* out = (char*)malloc(value->len + 1);
  if (out == NULL) tcp_fail("bytes_to_utf8: out of memory");
  if (value->len > 0) memcpy(out, value->data, value->len);
  out[value->len] = '\\0';
  return sprout_make1(find_ctor_tag_by_name("Ok"), (long long)(uintptr_t)out);
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
  long long h = g_next_listener_handle++;
  if (h >= 2048) {
    close(fd);
    tcp_fail("tcp_listen: handle table full");
  }
  g_listener_fd[h] = fd;
  g_listener_used[h] = 1;
  return h;
}

static long long tcp_net_ok(long long payload) {
  return sprout_make1(find_ctor_tag_by_name("Ok"), payload);
}

static long long tcp_net_err0(const char* ctor_name) {
  long long err = sprout_make0(find_ctor_tag_by_name(ctor_name));
  return sprout_make1(find_ctor_tag_by_name("Err"), err);
}

static long long tcp_net_err1(const char* ctor_name, long long payload) {
  long long err = sprout_make1(find_ctor_tag_by_name(ctor_name), payload);
  return sprout_make1(find_ctor_tag_by_name("Err"), err);
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

  long long h = g_next_conn_handle++;
  if (h >= 2048) {
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
  long long h = g_next_conn_handle++;
  if (h >= 2048) {
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
  BytesVal* out = (BytesVal*)malloc(sizeof(BytesVal));
  if (out == NULL) tcp_fail("tcp_read_exact: out of memory");
  out->len = (size_t)count;
  out->data = count == 0 ? NULL : (unsigned char*)malloc((size_t)count);
  if (count > 0 && out->data == NULL) tcp_fail("tcp_read_exact: out of memory");
  size_t received = 0;
  while (received < (size_t)count) {
    ssize_t n = recv(g_conn_fd[conn], out->data + received, (size_t)count - received, 0);
    if (n == 0) {
      free(out->data);
      free(out);
      return tcp_net_err0("stdlib.net.TcpEndOfStream");
    }
    if (n < 0) {
      free(out->data);
      free(out);
      return tcp_net_err1("stdlib.net.TcpReadFailed", (long long)(uintptr_t)strerror(errno));
    }
    received += (size_t)n;
  }
  return tcp_net_ok((long long)(uintptr_t)out);
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
"""
    with tempfile.NamedTemporaryFile("w", suffix=".c", delete=False, encoding="utf-8") as tmp_c:
        tmp_c.write(runtime_c)
        c_path = Path(tmp_c.name)
    try:
        subprocess.run([clang, str(ll_path), str(c_path), "-O2", "-o", str(out)], check=True)
    finally:
        ll_path.unlink(missing_ok=True)
        c_path.unlink(missing_ok=True)
    return 0


def _repl_compose_source(
    declarations: list[str],
    tail: list[str] | None = None,
    *,
    with_stdlib: bool,
) -> str:
    chunks = declarations + (tail or [])
    user_source = "\n\n".join(chunk for chunk in chunks if chunk.strip())
    if with_stdlib:
        return user_source
    return with_prelude(user_source)


def _repl_parse_and_check(
    declarations: list[str],
    tail: list[str] | None = None,
    *,
    with_stdlib: bool,
) -> tuple[object, dict[str, str]]:
    source = _repl_compose_source(declarations, tail, with_stdlib=with_stdlib)
    if not with_stdlib:
        tree = parse(source)
        validate_public_surface(tree, None)
        types = typecheck_program(tree)
        return tree, types

    imports = "\n".join(
        [
            "module stdlib.repl",
            "import stdlib.collections (Maybe, List, Vec, Dict, Semigroup, Functor, Foldable, list_map, list_fold, list_append, vec_empty, vec_prepend, vec_append, vec_length, vec_get, vec_get_or, vec_set, vec_map, vec_fold, vec_slice, vec_reverse, vec_sum, vec_sum_by, foldable_to_vec, dict_empty, dict_get, dict_set, dict_remove, dict_keys, dict_values)",
            "import stdlib.collections",
            "import stdlib.http",
            "import stdlib.http_client",
            "import stdlib.net",
            "import stdlib.bytes",
            "import stdlib.math",
            "import stdlib.string",
            "import stdlib.terminal",
        ]
    )
    source = f"{imports}\n\n{source}"
    with tempfile.TemporaryDirectory() as tmpdir:
        temp_path = Path(tmpdir) / "repl_session.sprout"
        temp_path.write_text(source, encoding="utf-8")
        bundle = load_module_bundle(temp_path)
        tree = parse(bundle.source)
        resolve_program_names(tree, bundle)
        validate_public_surface(tree, bundle)
        types = typecheck_program(tree)
        return tree, types


def _repl_is_declaration(source: str) -> bool:
    stripped = source.strip()
    return stripped.startswith(("fn ", "let ", "type ", "class ", "instance ", "export "))


def _repl_lookup_type(types: dict[str, str], name: str) -> str:
    direct = types.get(name)
    if direct is not None:
        return direct
    qualified = [typ for key, typ in types.items() if key.endswith(f".{name}")]
    if len(qualified) == 1:
        return qualified[0]
    raise KeyError(name)


def _repl_history_path() -> Path:
    override = os.environ.get("SPROUT_REPL_HISTORY")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".sprout_repl_history"


def _configure_repl_readline(history_path: Path | None = None) -> None:
    try:
        import readline
    except ImportError:
        return

    target = history_path if history_path is not None else _repl_history_path()
    readline.parse_and_bind("tab: complete")
    readline.parse_and_bind("set editing-mode emacs")
    readline.set_history_length(_REPL_HISTORY_LIMIT)
    if target.exists():
        try:
            readline.read_history_file(target)
        except OSError:
            pass

    def _write_history() -> None:
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            readline.write_history_file(target)
        except OSError:
            pass

    atexit.register(_write_history)


def cmd_repl(with_stdlib: bool = False) -> int:
    declarations: list[str] = []
    repl_counter = 0
    interactive = sys.stdin.isatty() and sys.stdout.isatty()

    def emit(text: str) -> None:
        print(text)

    def process_submission(source: str) -> None:
        nonlocal repl_counter
        stripped = source.strip()
        if stripped == "":
            return
        if stripped in {":quit", ":q", ":exit"}:
            raise EOFError
        if stripped == ":help":
            emit("Commands: :type EXPR, :t EXPR, :quit, :help")
            return
        if stripped.startswith("module ") or stripped.startswith("import "):
            emit("error: repl does not support module/import headers")
            return
        type_expr: str | None = None
        if stripped.startswith(":type "):
            type_expr = stripped[len(":type ") :].strip()
        elif stripped.startswith(":t "):
            type_expr = stripped[len(":t ") :].strip()
        if type_expr is not None:
            expr = type_expr
            if expr == "":
                emit("error: :type expects an expression")
                return
            repl_counter += 1
            name = f"__repl_value_{repl_counter}"
            _, types = _repl_parse_and_check(
                declarations,
                [f"let {name} = {expr}"],
                with_stdlib=with_stdlib,
            )
            emit(_repl_lookup_type(types, name))
            return
        if _repl_is_declaration(stripped):
            _repl_parse_and_check(
                declarations + [source],
                with_stdlib=with_stdlib,
            )
            declarations.append(source)
            emit("ok")
            return

        repl_counter += 1
        name = f"__repl_value_{repl_counter}"
        _, types = _repl_parse_and_check(
            declarations,
            [f"let {name} = {source}"],
            with_stdlib=with_stdlib,
        )
        inferred_type = _repl_lookup_type(types, name)
        main_body = name if inferred_type == "IO Unit" else f"print({name})"
        tree, _ = _repl_parse_and_check(
            declarations,
            [f"let {name} = {source}", f"fn main() -> IO Unit = {main_body}"],
            with_stdlib=with_stdlib,
        )
        lowered = lower_typeclasses(tree)
        typecheck_program(lowered)
        run_program(lowered)

    if interactive:
        _configure_repl_readline()
        emit("Sprout REPL. Use :help for commands.")
        while True:
            try:
                line = input("sprout> ")
            except EOFError:
                emit("")
                break
            try:
                process_submission(line)
            except EOFError:
                break
            except (
                ParseError,
                TokenizeError,
                TypeCheckError,
                RuntimeError,
                ModuleLoadError,
                SurfaceCheckError,
                TypeclassLoweringError,
            ) as exc:
                emit(f"error: {exc}")
        return 0

    for raw in sys.stdin:
        try:
            process_submission(raw.rstrip("\n"))
        except EOFError:
            break
        except (
            ParseError,
            TokenizeError,
            TypeCheckError,
            RuntimeError,
            ModuleLoadError,
            SurfaceCheckError,
            TypeclassLoweringError,
        ) as exc:
            emit(f"error: {exc}")
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
    p_repl = sub.add_parser("repl", help="start a simple interactive Sprout REPL")
    p_repl.add_argument("--with-stdlib", action="store_true", help="load all stdlib modules into the REPL")

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
        if args.command == "repl":
            return cmd_repl(with_stdlib=args.with_stdlib)
    except (
        ParseError,
        TokenizeError,
        TypeCheckError,
        RuntimeError,
        CodegenError,
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
