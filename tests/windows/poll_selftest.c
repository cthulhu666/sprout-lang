/* Behavioural self-test for the Windows (WSAPoll) poller backend.
 *
 * WHY THIS EXISTS. Until W5 links an executable, every Windows milestone can only be gated on
 * *compilation* — which is how W0 and W1 each ended up with an exit criterion that could not be
 * checked (docs/windows-port-v0.md §4.7). The poller is the wrong place to accept that: its logic
 * is not a mechanical substitution but real branching — an empty-socket set that must not reach
 * WSAPoll at all, a timeout clamp whose failure mode is a hang, a swap-remove during a scan. A
 * bug there would otherwise surface at W5, tangled with four other milestones' bugs.
 *
 * So this compiles the backend directly (`#include`ing the .c file, the standard trick for
 * reaching file-static state) and drives it against REAL loopback sockets on the CI runner. It
 * needs no scheduler, no GC and no runtime — only Ws2_32 — which is exactly why it can run three
 * milestones before anything else does.
 *
 * It is Windows-only by construction and lives outside tests/stdlib (those are .spr files run by
 * `just test`); the `windows` CI job builds and runs it.
 *
 *   clang --target=x86_64-pc-windows-msvc tests/windows/poll_selftest.c -o poll_selftest.exe \
 *         -lws2_32 && ./poll_selftest.exe
 */
#include <stdio.h>
#include <stdlib.h>

/* sprout_poll.c calls this on unrecoverable errors; the real one lives in sprout_runtime.c,
 * which this test deliberately does not link (see above). */
__attribute__((noreturn)) void sprout_fail(const char* msg);
void sprout_fail(const char* msg) {
  printf("FAIL sprout_fail: %s\n", msg);
  exit(1);
}

#include "../../runtime/sprout_poll.c"

static int g_failures = 0;

static void check(int cond, const char* what) {
  printf("  %-58s %s\n", what, cond ? "ok" : "FAILED");
  if (!cond) g_failures++;
}

/* The loopback pair W3 will use to replace async DNS's pipe() (§4.3). Exercised here early on
 * purpose: if the emulation is wrong, this test says so now rather than W3's DNS path saying it
 * later. Winsock has no socketpair(); libuv hand-rolls the same sequence (src/win/tcp.c:1627),
 * using AcceptEx only because IOCP needs overlapped handles — a blocking accept is enough when
 * the pair is built once on one thread, as it is here and in async_resolve. */
static int loopback_pair(SOCKET out[2]) {
  out[0] = INVALID_SOCKET;
  out[1] = INVALID_SOCKET;
  SOCKET listener = socket(AF_INET, SOCK_STREAM, 0);
  if (listener == INVALID_SOCKET) return 0;
  struct sockaddr_in addr;
  memset(&addr, 0, sizeof(addr));
  addr.sin_family = AF_INET;
  addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
  addr.sin_port = 0;                      /* let the stack choose */
  int len = (int)sizeof(addr);
  if (bind(listener, (struct sockaddr*)&addr, len) != 0) goto fail;
  if (listen(listener, 1) != 0) goto fail;
  if (getsockname(listener, (struct sockaddr*)&addr, &len) != 0) goto fail;
  out[0] = socket(AF_INET, SOCK_STREAM, 0);
  if (out[0] == INVALID_SOCKET) goto fail;
  if (connect(out[0], (struct sockaddr*)&addr, len) != 0) goto fail;
  out[1] = accept(listener, NULL, NULL);
  if (out[1] == INVALID_SOCKET) goto fail;
  closesocket(listener);
  return 1;
fail:
  closesocket(listener);
  if (out[0] != INVALID_SOCKET) closesocket(out[0]);
  return 0;
}

int main(void) {
  WSADATA wsa;
  if (WSAStartup(MAKEWORD(2, 2), &wsa) != 0) {
    printf("FAIL WSAStartup\n");
    return 1;
  }
  sprout_poll_init();

  void* toks[64];
  int   is_timer[64];
  int   n;

  /* ── 1. Timers only — the case WSAPoll cannot express ─────────────────────────
   * "The array must contain at least one structure with a valid socket"; with none valid it
   * returns WSAEINVAL. A lone task_sleep produces exactly this set, so if the backend forwarded
   * it to WSAPoll the scheduler would die on the most ordinary thing a green task can do. */
  printf("1. timers only (no sockets registered)\n");
  long long id_slow = 0, id_fast = 0;
  check(sprout_poll_add_timer(400, (void*)0xA1, &id_slow) == 1, "arm the 400ms timer");
  check(sprout_poll_add_timer(40,  (void*)0xA2, &id_fast) == 1, "arm the 40ms timer");
  n = sprout_poll_wait(toks, is_timer, 64);
  check(n == 1,                  "wait returns exactly the nearer deadline");
  check(toks[0] == (void*)0xA2,  "and it is the 40ms token, not the 400ms one");
  check(is_timer[0] == 1,        "reported as a timer");
  n = sprout_poll_wait(toks, is_timer, 64);
  check(n == 1 && toks[0] == (void*)0xA1, "the 400ms timer arrives on the next wait");

  /* ── 2. A removed timer never fires ───────────────────────────────────────────
   * §5.1's guarantee: a force-dropped sleeper must not be resumable by a stale token. */
  printf("2. timer removal\n");
  long long id_dead = 0, id_live = 0;
  sprout_poll_add_timer(30, (void*)0xB1, &id_dead);
  sprout_poll_add_timer(60, (void*)0xB2, &id_live);
  sprout_poll_remove_timer(id_dead);
  n = sprout_poll_wait(toks, is_timer, 64);
  check(n == 1 && toks[0] == (void*)0xB2, "the surviving timer fires, the removed one does not");
  sprout_poll_remove_timer(id_dead);   /* must be a silent no-op, not a crash */
  sprout_poll_remove_timer(id_live);   /* already harvested — likewise */
  check(1, "removing an already-removed / already-fired timer is a no-op");

  /* ── 3. Socket readiness ──────────────────────────────────────────────────────*/
  printf("3. socket readiness (loopback pair)\n");
  SOCKET pair[2];
  if (!loopback_pair(pair)) {
    printf("  FAILED to build a loopback pair\n");
    g_failures++;
  } else {
    sprout_poll_add((int)pair[0], SPROUT_POLL_READ, (void*)0xC1);
    send(pair[1], "x", 1, 0);
    n = sprout_poll_wait(toks, is_timer, 64);
    check(n == 1 && toks[0] == (void*)0xC1, "the readable socket wakes with its token");
    check(is_timer[0] == 0,                 "reported as an fd, not a timer");

    /* ── 4. Registration is ONE-SHOT ────────────────────────────────────────────
     * The byte is still unread, so the socket is still readable; a second wait must NOT
     * report it, because the wake dropped the registration. Getting this wrong would spin
     * the pump on a permanently-ready fd. */
    printf("4. one-shot registration\n");
    long long id_guard = 0;
    sprout_poll_add_timer(60, (void*)0xC2, &id_guard);
    n = sprout_poll_wait(toks, is_timer, 64);
    check(n == 1 && toks[0] == (void*)0xC2,
          "still-readable socket is NOT re-reported; only the timer fires");

    /* ── 5. A timer beats a socket that never becomes ready ─────────────────────
     * The with_timeout-over-a-read shape. This is where an unclamped timeout would hang:
     * a due deadline computes negative and WSAPoll reads negative as "wait forever". */
    printf("5. timer wins against an idle socket\n");
    char drain[8];
    recv(pair[0], drain, sizeof(drain), 0);        /* clear the byte so pair[0] is idle */
    sprout_poll_add((int)pair[0], SPROUT_POLL_READ, (void*)0xD1);
    long long id_deadline = 0;
    sprout_poll_add_timer(50, (void*)0xD2, &id_deadline);
    n = sprout_poll_wait(toks, is_timer, 64);
    check(n == 1 && toks[0] == (void*)0xD2 && is_timer[0] == 1,
          "the deadline fires while the socket stays silent");

    /* ── 6. A removed fd is not reported ────────────────────────────────────────
     * scope_cancel's force-drop path, and the reason sprout_poll_remove is idempotent. */
    printf("6. fd removal\n");
    sprout_poll_remove((int)pair[0], SPROUT_POLL_READ);
    sprout_poll_remove((int)pair[0], SPROUT_POLL_READ);   /* idempotent */
    send(pair[1], "y", 1, 0);
    sprout_poll_add_timer(60, (void*)0xE1, &id_guard);
    n = sprout_poll_wait(toks, is_timer, 64);
    check(n == 1 && toks[0] == (void*)0xE1,
          "the deregistered socket stays silent though data arrived");

    /* ── 7. A socket and a timer due together arrive in one batch ───────────────
     * PARK_FD_TIMER registers a task on both, and the pump is written to receive both in one
     * wait (sprout_scheduler.c:418-423) — so the backend must harvest due timers on the
     * readiness return path too, not only when WSAPoll times out. */
    printf("7. socket and timer in one batch\n");
    /* Drain case 6's byte and send a fresh one, so this case does not silently depend on
     * whatever the previous one left in the buffer — a case that passes for a reason stated
     * three cases earlier is a case that breaks when someone reorders them. */
    recv(pair[0], drain, sizeof(drain), 0);
    send(pair[1], "z", 1, 0);
    sprout_poll_add((int)pair[0], SPROUT_POLL_READ, (void*)0xF1);
    sprout_poll_add_timer(1, (void*)0xF2, &id_guard);
    Sleep(30);                                     /* let the deadline pass before we wait */
    n = sprout_poll_wait(toks, is_timer, 64);
    int saw_fd = 0, saw_timer = 0;
    for (int i = 0; i < n; i++) {
      if (toks[i] == (void*)0xF1 && !is_timer[i]) saw_fd = 1;
      if (toks[i] == (void*)0xF2 &&  is_timer[i]) saw_timer = 1;
    }
    check(n == 2 && saw_fd && saw_timer, "both the ready fd and the due timer come back");

    closesocket(pair[0]);
    closesocket(pair[1]);
  }

  printf("\n");
  if (g_failures != 0) {
    printf("==> poll_selftest FAILED (%d check%s)\n", g_failures, g_failures == 1 ? "" : "s");
    return 1;
  }
  printf("==> poll_selftest OK\n");
  return 0;
}
