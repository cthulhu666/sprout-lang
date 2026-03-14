from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

from .ast import to_dict
from .codegen_llvm import CodegenError, compile_to_llvm
from .interpreter import RuntimeError, run_program
from .module_loader import ModuleLoadError, load_module_bundle, resolve_program_names
from .parser import ParseError, parse
from .stdlib import with_http_prelude, with_prelude
from .tokenizer import TokenizeError
from .typechecker import TypeCheckError, typecheck_program


def cmd_parse(path: Path) -> int:
    bundle = load_module_bundle(path)
    source = bundle.source
    tree = parse(source)
    resolve_program_names(tree, bundle)
    print(json.dumps(to_dict(tree), indent=2))
    return 0


def cmd_check(path: Path, with_stdlib: bool = False, with_http_stdlib: bool = False) -> int:
    bundle = load_module_bundle(path)
    source = bundle.source
    if with_http_stdlib:
        source = with_http_prelude(source)
        bundle = None
    elif with_stdlib:
        source = with_prelude(source)
        bundle = None
    tree = parse(source)
    if bundle is not None:
        resolve_program_names(tree, bundle)
    typed = typecheck_program(tree)
    print("ok")
    for name in sorted(typed.keys()):
        print(f"{name}: {typed[name]}")
    return 0


def cmd_run(path: Path, with_stdlib: bool = False, with_http_stdlib: bool = False) -> int:
    bundle = load_module_bundle(path)
    source = bundle.source
    if with_http_stdlib:
        source = with_http_prelude(source)
        bundle = None
    elif with_stdlib:
        source = with_prelude(source)
        bundle = None
    tree = parse(source)
    if bundle is not None:
        resolve_program_names(tree, bundle)
    typecheck_program(tree)
    run_program(tree)
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
    elif with_stdlib:
        source = with_prelude(source)
        bundle = None
    tree = parse(source)
    if bundle is not None:
        resolve_program_names(tree, bundle)
    typecheck_program(tree)
    llvm_ir = compile_to_llvm(tree)

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
#include <netinet/in.h>
#include <arpa/inet.h>
#include <unistd.h>

typedef struct {
  long long tag;
  long long f0;
  long long f1;
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

static ObjNode* g_objs = NULL;
static CtorMeta g_ctor_meta[2048];
static long long g_ctor_meta_len = 0;
static int g_listener_fd[2048];
static int g_listener_used[2048];
static int g_conn_fd[2048];
static int g_conn_used[2048];
static long long g_next_listener_handle = 1;
static long long g_next_conn_handle = 1;

static void tcp_fail(const char* msg);

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
  SproutObj* o = (SproutObj*)malloc(sizeof(SproutObj));
  o->tag = tag;
  o->f0 = 0;
  o->f1 = 0;
  register_obj(o);
  return box_ptr(o);
}
long long sprout_make1(long long tag, long long a0) {
  SproutObj* o = (SproutObj*)malloc(sizeof(SproutObj));
  o->tag = tag;
  o->f0 = a0;
  o->f1 = 0;
  register_obj(o);
  return box_ptr(o);
}
long long sprout_make2(long long tag, long long a0, long long a1) {
  SproutObj* o = (SproutObj*)malloc(sizeof(SproutObj));
  o->tag = tag;
  o->f0 = a0;
  o->f1 = a1;
  register_obj(o);
  return box_ptr(o);
}
long long sprout_tag(long long h) {
  return unbox_ptr(h)->tag;
}
long long sprout_field(long long h, long long idx) {
  SproutObj* o = unbox_ptr(h);
  return idx == 0 ? o->f0 : o->f1;
}

static void tcp_fail(const char* msg) {
  fprintf(stderr, "%s\\n", msg);
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sprout")
    sub = parser.add_subparsers(dest="command", required=True)

    p_parse = sub.add_parser("parse", help="parse a Sprout file and print AST as JSON")
    p_parse.add_argument("file", type=Path)
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

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "parse":
            return cmd_parse(args.file)
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
            )
        if args.command == "compile":
            return cmd_compile(
                args.file,
                out=args.output,
                with_stdlib=args.with_stdlib,
                with_http_stdlib=args.with_http_stdlib,
                native=args.native,
            )
    except (
        ParseError,
        TokenizeError,
        TypeCheckError,
        RuntimeError,
        CodegenError,
        ModuleLoadError,
        subprocess.CalledProcessError,
    ) as exc:
        print(f"error: {exc}")
        return 1

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
