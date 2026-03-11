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
from .parser import ParseError, parse
from .stdlib import with_prelude
from .tokenizer import TokenizeError
from .typechecker import TypeCheckError, typecheck_program


def cmd_parse(path: Path) -> int:
    source = path.read_text(encoding="utf-8")
    tree = parse(source)
    print(json.dumps(to_dict(tree), indent=2))
    return 0


def cmd_check(path: Path, with_stdlib: bool = False) -> int:
    source = path.read_text(encoding="utf-8")
    tree = parse(with_prelude(source) if with_stdlib else source)
    typed = typecheck_program(tree)
    print("ok")
    for name in sorted(typed.keys()):
        print(f"{name}: {typed[name]}")
    return 0


def cmd_run(path: Path, with_stdlib: bool = False) -> int:
    source = path.read_text(encoding="utf-8")
    tree = parse(with_prelude(source) if with_stdlib else source)
    typecheck_program(tree)
    run_program(tree)
    return 0


def cmd_compile(path: Path, out: Path, with_stdlib: bool = False, native: bool = False) -> int:
    source = path.read_text(encoding="utf-8")
    tree = parse(with_prelude(source) if with_stdlib else source)
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

static ObjNode* g_objs = NULL;
static CtorMeta g_ctor_meta[2048];
static long long g_ctor_meta_len = 0;
static int g_listener_fd[2048];
static int g_listener_used[2048];
static int g_conn_fd[2048];
static int g_conn_used[2048];
static long long g_next_listener_handle = 1;
static long long g_next_conn_handle = 1;

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
    p_run = sub.add_parser("run", help="typecheck and run a Sprout file")
    p_run.add_argument("file", type=Path)
    p_run.add_argument("--with-stdlib", action="store_true", help="load stdlib prelude")
    p_compile = sub.add_parser("compile", help="typecheck and compile a Sprout file")
    p_compile.add_argument("file", type=Path)
    p_compile.add_argument("-o", "--output", type=Path, required=True, help="output file")
    p_compile.add_argument("--with-stdlib", action="store_true", help="load stdlib prelude")
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
            return cmd_check(args.file, with_stdlib=args.with_stdlib)
        if args.command == "run":
            return cmd_run(args.file, with_stdlib=args.with_stdlib)
        if args.command == "compile":
            return cmd_compile(
                args.file,
                out=args.output,
                with_stdlib=args.with_stdlib,
                native=args.native,
            )
    except (ParseError, TokenizeError, TypeCheckError, RuntimeError, CodegenError, subprocess.CalledProcessError) as exc:
        print(f"error: {exc}")
        return 1

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
