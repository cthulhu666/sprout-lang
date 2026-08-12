# Async DNS resolution — design (v0, non-normative)

Status: **design + in-progress implementation.** Not part of the normative spec
(`docs/spec-v0.md`); this documents a runtime mechanism and its rationale.

## 1. Problem statement

The runtime is single-threaded: every green task runs cooperatively on one OS
thread, switching only at *park points* (non-blocking syscall would-block → park
→ pump runs siblings). `getaddrinfo(3)` — the only name-resolution call, used in
`tcp_connect` and the HTTP client's `http_resolve` — is opaque, monolithic
**blocking** libc code with no park point inside it. So when any green task
resolves a name, the single kernel thread is stuck inside the syscall until it
returns, and **no other green task advances and no timer fires** for the whole
duration. A slow or unreachable resolver stalls the entire process.

This is the last blocking call on the client path; connect/read/write already
park. It is a defect of the same class as the HTTP-client-blocks-the-thread and
EMFILE-hot-spin defects already fixed.

## 2. Goals and non-goals

**Goals**
- A name resolution must not freeze the scheduler: siblings and timers keep
  running while a lookup is outstanding.
- Preserve **system-resolver parity**: names resolve here exactly as they do for
  every other program on the host (`/etc/hosts`, search domains, mDNS/`.local`,
  nsswitch modules).
- Cancellation-safe: a `with_timeout`/`scope_cancel` of a task mid-resolve must
  leak neither memory nor descriptors.
- Keep the GC single-threaded in practice.

**Non-goals (this version)**
- In-process DNS caching / TTL handling (a property of Option B, deferred).
- A UDP socket surface (not added here).
- Making `getaddrinfo` itself cancellable (impossible; see §6).

## 3. Prior-art survey (verified against primary sources)

The field splits into two strategies:

**(A) Offload blocking `getaddrinfo` to a thread**, deliver the result back to
the event loop.

| System | Detail | Source |
|---|---|---|
| libuv (Node's loop) | `getaddrinfo` on an internal threadpool (default 4, `UV_THREADPOOL_SIZE`) | docs.libuv.org/en/v1.x/threadpool.html |
| Node `dns.lookup()` | "synchronous call to getaddrinfo(3) that runs on libuv's threadpool" | nodejs.org/api/dns.html |
| Tokio (default) | `getaddrinfo` via `spawn_blocking`; "not intended to cover all DNS use cases" | tokio `net/addr.rs` |
| libcurl (default) | threaded resolver: libc resolver in a helper thread, one per resolve | everything.curl.dev/internals/resolving.html |
| Go (cgo path) | `getaddrinfo`; "a blocked C call consumes an OS thread"; capped at 500 concurrent | pkg.go.dev/net |

**(B) Speak DNS natively over sockets**, integrated with the event loop, no
threads.

| System | Detail | Source |
|---|---|---|
| Go (default) | pure-Go resolver over `resolv.conf` servers; "consumes only a goroutine" | pkg.go.dev/net |
| Node `dns.resolve()` | c-ares; "does not use getaddrinfo(3)… does not use libuv's threadpool" | nodejs.org/api/dns.html |
| nginx `resolver` | queries nameservers directly, event-loop integrated, TTL caching | nginx.org/en/docs/http/ngx_http_core_module.html#resolver |
| c-ares / hickory-dns | DNS wire protocol over sockets, no threads for core resolution | c-ares.org |

**No standard async POSIX resolver exists.** `getaddrinfo_a` is a glibc-only
extension (`libanl`), absent on macOS/BSD (man7.org/linux/man-pages/man3/getaddrinfo_a.3.html).

Two facts drive the decision:
1. **The closest architectural analog — libuv, a single event-loop thread plus a
   small internal threadpool for syscalls with no non-blocking form — chose (A).**
   Tokio's and curl's *defaults* agree.
2. **(B) structurally sacrifices system-resolver parity.** It bypasses nsswitch,
   so mDNS/`.local`, LDAP/NIS and custom host configs stop resolving. This is
   exactly why Go *keeps* the cgo/`getaddrinfo` fallback despite preferring its
   pure resolver — a from-scratch resolver cannot see what nsswitch sees.

## 4. Decision: Option A (resolver thread)

Offload `getaddrinfo` to a short-lived OS thread; the green task parks on a pipe
until the result is ready. Chosen over (B) because it is the smaller change,
preserves parity, matches the dominant single-event-loop precedent, and its only
real weakness (uncancellable lookups, §6) is one the whole field lives with.

(B) — a DNS client written in Sprout over non-blocking UDP — remains a defensible
*later* opt-in, but it is gated behind adding a UDP runtime surface and accepting
the parity loss, so it is out of scope here.

### 4.1 Mechanism

- **Spawn-per-lookup, detached.** Each resolve spawns a detached thread that runs
  one `getaddrinfo` and exits (Go's goroutine-per-lookup model). No persistent
  thread, queue, or shutdown logic.
- **Concurrency cap (default 64) with synchronous fallback.** An atomic counter
  bounds live resolver threads; past the cap, that one lookup runs synchronously
  on the calling thread — degrading to *today's* behavior for that call only,
  never worse, and never spawning unbounded threads (cf. Go's 500 cap).
- **Wakeup via one self-pipe (`pipe2`) per in-flight request.** The green task
  parks on the read end through the existing **unowned-fd** entry point
  (`scheduler_park_on_unowned_fd`); the resolver thread writes one byte when done.
  A self-pipe is portable across epoll and kqueue; `eventfd` would be Linux-only.
  Each task waits on its *own* fd, so there is no completion-demux bookkeeping.

### 4.2 GC safety — the thread is a pure-libc island

The resolver thread touches **only** a malloc'd request struct (C strings +
`sockaddr` list). The green task copies `host`/`port` into malloc'd C strings
*before* spawning (on the main thread) and reads the result into Sprout values
*after* waking (on the main thread). The thread never allocates via the GC, never
hits a safepoint, never touches `g_current_task`. The GC's stop-the-world is
cooperative at main-thread allocation safepoints; a thread that never allocates is
invisible to it. The eventfd/pipe write→park-read handoff provides the memory
barrier. So the GC stays effectively single-threaded.

### 4.3 Lifetime

The request struct carries an atomic refcount initialized to 2 (task + thread).
- Thread: fill results → write byte (ignore `EPIPE`) → close write end → decref.
- Task (normal wake): read results → close read end → decref.
- Task (force-dropped): §6.

Last decref to reach 0 frees the struct and its strings. No path double-frees or
double-closes: the read end is closed by exactly one of {normal wake, force-drop};
the write end by the thread.

## 5. Option C (landed alongside): IP-literal fast path

Numeric hosts (`"127.0.0.1"`, `"::1"`, any literal) are recognized with
`inet_pton` and connected directly, skipping `getaddrinfo` entirely — no thread,
no lookup, no possible freeze. This removes the stall for the common internal/test
case and is orthogonal to the async-DNS mechanism.

## 6. Cancellation

`getaddrinfo` is not cancellable — no POSIX call interrupts it. The leak it would
otherwise cause is closed by the park machinery: on force-drop,
`force_drop_task` closes `park_close_fd` (the pipe read end) and runs the
registered `park_cleanup`, which drops the task's refcount (it must not allocate
or touch GC — it only decrefs / frees plain heap, satisfying the hook contract).
The detached thread runs `getaddrinfo` to completion in the background, its later
pipe write hits a closed read end (`EPIPE`, ignored), it drops its ref and frees.
**The thread finishes in the background but leaks nothing.** This matches
libuv/curl, which cannot cancel `getaddrinfo` either.

## 7. Error-message impact

None user-visible by default. A resolution failure still surfaces as
`TcpConnectFailed`/the HTTP error with `gai_strerror`. A thread-spawn failure or
over-cap falls back to synchronous resolution, so no new error variant is minted.

## 8. Tests

- **Liveness (the RED):** `tests/task_io_smoke/*` — a sibling parked on a 50 ms
  timer while the main task resolves with an injected delay
  (`SPROUT_DNS_RESOLVE_DELAY_MS`, an env-gated test seam honored by both the
  synchronous and threaded resolve paths). RED on the blocking code (timer frozen
  for the whole delay); GREEN once the delay runs on the resolver thread. Same
  one-sided pattern as `http_request_parks.spr`.
- **IP-literal fast path:** a connect to a numeric host succeeds with no lookup.
- **Cancellation:** a resolve force-dropped mid-flight leaks no descriptor
  (checked under `ulimit -n`, like `http-cancel-drop`).

## 9. Follow-ups (BACKLOG)

- Option B (thread-free native resolver + UDP surface + in-process TTL cache) as a
  later opt-in if cancellable/cacheable resolution becomes a requirement.
- Shared eventfd/pipe + completion demux to drop the per-lookup 2-fd cost.
