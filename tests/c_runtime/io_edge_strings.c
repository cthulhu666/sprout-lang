/* The I/O edge exits (tcp, http, env, argv, proc) hand Sprout headered Strings and well-formed
 * Bytes (docs/string-header-invariant-v0.md §7 step 3).
 *
 * Runs under SPROUT_GC_HDRCHECK=1, so every String that leaves through sprout_str_word is
 * header-checked, and str_eq / str_byte_len check each payload again on the way in.
 * argv[1] = "stress" also sets SPROUT_GC_STRESS=1: every allocation collects, so a value a
 * function fails to root across its own allocations is freed and fails the byte check.
 * None of these functions takes a String as a word, so there is no unrooted input (F) to pass.
 *
 * Covered: env_get and env_get_unboxed (set, unset), argv_get and argv_get_unboxed (in range,
 * out of range, negative), tcp_read_exact (one byte) and tcp_read_some over a socketpair (data,
 * EOF, invalid handle, bad count, a read error with its strerror text),
 * http_err_cstr and http_ok_response (headers String, binary body with an interior NUL, a headers
 * buffer with an interior NUL, which still stops at it), sprout_make_proc_result (data, empty).
 * Not covered: the EAGAIN park in tcp_read_exact (needs the scheduler), tcp_read_some's
 * TcpWouldBlock, and the http_err_cstr callers (tls_error_message, parse_http_url, chunk decode).
 */
#include <stdio.h>
#include <sys/socket.h>

#include "../../runtime/sprout_runtime.c"
#include "runtime_case.h"
#include "str_check.h"

enum { ERR = 1, OK = 2, JUST = 3, NOTHING = 4, END_OF_STREAM = 5, INVALID_HANDLE = 6,
       INVALID_ARG = 7, READ_FAILED = 8, HTTP_DECODE = 9, HTTP_RESPONSE = 10, PROC_RESULT = 11 };

static int bytes_is(long long b, const void* data, size_t n) {
  if (b == 0 || sprout_hdr_kind(sprout_hdr_of((void*)(uintptr_t)b)) != SPROUT_HEAP_BYTES) return 0;
  const BytesVal* v = (const BytesVal*)(uintptr_t)b;
  return v->len == n && (n == 0 || memcmp(v->data, data, n) == 0);
}

/* `r` is Ok(Bytes of `data`). */
static int ok_bytes(long long r, const void* data, size_t n) {
  return sprout_tag(r) == OK && bytes_is(sprout_field(r, 0), data, n);
}

/* `r` is Err(<ctor>) with no payload, or Err(<ctor>(<String>)) when `has_text`: that String is
 * `want`, or any non-empty text when `want` is NULL. */
static int err_ctor(long long r, long long ctor, int has_text, const char* want) {
  if (sprout_tag(r) != ERR) return 0;
  long long e = sprout_field(r, 0);
  if (sprout_tag(e) != ctor) return 0;
  return !has_text || is_str(sprout_field(e, 0), want);
}

static int just_str(long long r, const char* want) {
  return sprout_tag(r) == JUST && is_str(sprout_field(r, 0), want);
}

/* A SproutUnboxed2 is Just(<String>) / Nothing. */
static int unboxed_just_str(SproutUnboxed2 u, const char* want) {
  return (int64_t)(uint64_t)u == JUST && is_str((long long)(int64_t)(uint64_t)(u >> 64), want);
}
static int unboxed_nothing(SproutUnboxed2 u) { return (int64_t)(uint64_t)u == NOTHING; }

static void run_env_argv(void) {
  setenv("IO_EDGE_VAR", "from the environment", 1);
  unsetenv("IO_EDGE_UNSET");
  check(just_str(env_get("IO_EDGE_VAR"), "from the environment"), "env_get set");
  check(sprout_tag(env_get("IO_EDGE_UNSET")) == NOTHING, "env_get unset");
  check(unboxed_just_str(env_get_unboxed(I("IO_EDGE_VAR")), "from the environment"),
        "env_get_unboxed set");
  check(unboxed_nothing(env_get_unboxed(I("IO_EDGE_UNSET"))), "env_get_unboxed unset");

  static char* args[] = { "prog", "first", "h\xc3\xa9llo", NULL };
  g_sprout_argc = 3;
  g_sprout_argv = args;
  check(just_str(argv_get(0), "first"), "argv_get(0)");
  check(just_str(argv_get(1), "h\xc3\xa9llo"), "argv_get(1)");
  check(sprout_tag(argv_get(2)) == NOTHING, "argv_get past the end");
  check(sprout_tag(argv_get(-1)) == NOTHING, "argv_get negative");
  check(unboxed_just_str(argv_get_unboxed(0), "first"), "argv_get_unboxed(0)");
  check(unboxed_just_str(argv_get_unboxed(1), "h\xc3\xa9llo"), "argv_get_unboxed(1)");
  check(unboxed_nothing(argv_get_unboxed(2)), "argv_get_unboxed past the end");
  check(unboxed_nothing(argv_get_unboxed(-1)), "argv_get_unboxed negative");
  g_sprout_argv = NULL;
  check(sprout_tag(argv_get(0)) == NOTHING, "argv_get without argv");
  g_sprout_argc = 0;
}

static void send_all(int fd, const char* data, size_t n) {
  check(send(fd, data, n, 0) == (ssize_t)n, "send on the socketpair");
}

static void run_tcp(void) {
  int sv[2];
  check(socketpair(AF_UNIX, SOCK_STREAM, 0, sv) == 0, "socketpair");
  g_conn_fd[1] = sv[0];
  g_conn_used[1] = 1;

  send_all(sv[1], "hello", 5);
  check(ok_bytes(tcp_read_some(1, 100), "hello", 5), "tcp_read_some");
  send_all(sv[1], "abcdef", 6);
  check(ok_bytes(tcp_read_some(1, 4), "abcd", 4), "tcp_read_some capped by max_bytes");
  check(ok_bytes(tcp_read_some(1, 100), "ef", 2), "tcp_read_some rest");
  send_all(sv[1], "x\0y", 3);
  check(ok_bytes(tcp_read_some(1, 100), "x\0y", 3), "tcp_read_some keeps an interior NUL");

  /* tcp_read_exact reads exactly one byte; any other count is an argument error. */
  send_all(sv[1], "12", 2);
  check(ok_bytes(tcp_read_exact(1, 1), "1", 1), "tcp_read_exact");
  check(ok_bytes(tcp_read_exact(1, 1), "2", 1), "tcp_read_exact again");
  send_all(sv[1], "\xff\x00", 2);
  check(ok_bytes(tcp_read_exact(1, 1), "\xff", 1), "tcp_read_exact binary");
  check(ok_bytes(tcp_read_exact(1, 1), "\x00", 1), "tcp_read_exact NUL byte");

  check(err_ctor(tcp_read_some(1, 0), INVALID_ARG, 1, NULL), "tcp_read_some max_bytes 0");
  check(err_ctor(tcp_read_exact(1, 0), INVALID_ARG, 1, "count must be 1"), "tcp_read_exact of 0");
  check(err_ctor(tcp_read_exact(1, 2), INVALID_ARG, 1, "count must be 1"), "tcp_read_exact of 2");
  check(err_ctor(tcp_read_exact(1, -1), INVALID_ARG, 1, "count must be 1"),
        "tcp_read_exact negative count");
  check(err_ctor(tcp_read_some(0, 8), INVALID_HANDLE, 0, NULL), "tcp_read_some handle 0");
  check(err_ctor(tcp_read_exact(77, 1), INVALID_HANDLE, 0, NULL), "tcp_read_exact unknown handle");

  send_all(sv[1], "ab", 2);
  close(sv[1]);
  check(ok_bytes(tcp_read_exact(1, 1), "a", 1), "tcp_read_exact before EOF");
  check(ok_bytes(tcp_read_some(1, 8), "b", 1), "tcp_read_some before EOF");
  check(err_ctor(tcp_read_exact(1, 1), END_OF_STREAM, 0, NULL), "tcp_read_exact EOF");
  check(err_ctor(tcp_read_some(1, 8), END_OF_STREAM, 0, NULL), "tcp_read_some EOF");
  tcp_close(1);

  g_conn_fd[2] = -1;   /* recv fails with EBADF; the Err carries strerror's text */
  g_conn_used[2] = 1;
  check(err_ctor(tcp_read_some(2, 8), READ_FAILED, 1, NULL), "tcp_read_some read error");
  check(err_ctor(tcp_read_exact(2, 1), READ_FAILED, 1, NULL), "tcp_read_exact read error");
  g_conn_used[2] = 0;
}

static void run_http(void) {
  check(err_ctor(http_err_cstr(HTTP_DECODE, strdup("invalid chunk size")), HTTP_DECODE, 1,
                 "invalid chunk size"), "http_err_cstr");

  long long r = http_ok_response(200, strdup("Content-Type: text/plain\r\nX: 1"),
                                 memcpy(malloc(5), "a\0b\xff" "c", 5), 5);
  long long resp = sprout_field(r, 0);
  check(sprout_tag(r) == OK && sprout_tag(resp) == HTTP_RESPONSE, "http_ok_response shape");
  check(sprout_field(resp, 0) == 200, "http_ok_response status");
  check(is_str(sprout_field(resp, 1), "Content-Type: text/plain\r\nX: 1"),
        "http_ok_response headers");
  check(bytes_is(sprout_field(resp, 2), "a\0b\xff" "c", 5), "http_ok_response binary body");

  /* The headers buffer is a C string: an interior NUL ends it, as before. */
  char* nul_headers = malloc(6);
  memcpy(nul_headers, "ab\0cd", 6);
  r = http_ok_response(204, nul_headers, malloc(1), 0);
  resp = sprout_field(r, 0);
  check(is_str(sprout_field(resp, 1), "ab"), "http_ok_response headers stop at a NUL");
  check(bytes_is(sprout_field(resp, 2), "", 0), "http_ok_response empty body");
}

static void run_proc(void) {
  GrowBuf out = { malloc(4), 4, 4 };
  GrowBuf err = { malloc(3), 3, 3 };
  memcpy(out.data, "o\0t\xfe", 4);
  memcpy(err.data, "e\0r", 3);
  long long r = sprout_make_proc_result(7, &out, &err);
  check(sprout_tag(r) == PROC_RESULT, "sprout_make_proc_result tag");
  check(sprout_field(r, 0) == 7, "sprout_make_proc_result exit code");
  check(bytes_is(sprout_field(r, 1), "o\0t\xfe", 4), "sprout_make_proc_result stdout");
  check(bytes_is(sprout_field(r, 2), "e\0r", 3), "sprout_make_proc_result stderr");
  check(out.data == NULL && err.data == NULL, "sprout_make_proc_result frees both buffers");

  GrowBuf none_out = { NULL, 0, 0 };
  GrowBuf none_err = { NULL, 0, 0 };
  r = sprout_make_proc_result(0, &none_out, &none_err);
  check(sprout_field(r, 0) == 0 && bytes_is(sprout_field(r, 1), "", 0) &&
        bytes_is(sprout_field(r, 2), "", 0), "sprout_make_proc_result empty");
}

int main(int argc, char** argv) {
  setenv("SPROUT_GC_HDRCHECK", "1", 1);
  setenv("SPROUT_GC_LINEAGE", "0", 1);
  if (argc == 2 && strcmp(argv[1], "stress") == 0) setenv("SPROUT_GC_STRESS", "1", 1);
  else if (argc != 1) return 99;
  sprout_register_ctor(ERR, "Err", 1, "p");
  sprout_register_ctor(OK, "Ok", 1, "p");
  sprout_register_ctor(JUST, "Just", 1, "p");
  sprout_register_ctor(NOTHING, "Nothing", 0, "");
  sprout_register_ctor(END_OF_STREAM, "stdlib.net.TcpEndOfStream", 0, "");
  sprout_register_ctor(INVALID_HANDLE, "stdlib.net.TcpInvalidHandle", 0, "");
  sprout_register_ctor(INVALID_ARG, "stdlib.net.TcpInvalidArgument", 1, "s");
  sprout_register_ctor(READ_FAILED, "stdlib.net.TcpReadFailed", 1, "s");
  sprout_register_ctor(HTTP_DECODE, "stdlib.http.HttpDecode", 1, "s");
  sprout_register_ctor(HTTP_RESPONSE, "stdlib.http.HttpResponse", 3, "isp");
  sprout_register_ctor(PROC_RESULT, "stdlib.process.ProcResult", 3, "ipp");
  run_env_argv();
  run_tcp();
  run_http();
  run_proc();
  if (g_failed) return 1;
  printf("io-edge%s\n", argc == 2 ? "-stress" : "");
  return 0;
}
