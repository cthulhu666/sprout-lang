# HTTP keep-alive in `stdlib.http_server` (v0)

**Status:** landed (experimental, like all of `stdlib.http_server`; not part of normative v0).
Issue: #390. Code: `stdlib/http_server.sprout` (`handle_connection`, `Admission`). Tests:
`tests/task_io_smoke/http_keep_alive.spr`, `http_keep_alive_pooled.spr`. Bench:
`bench/http_keep_alive/` and `bench/results-2026-10-13-http-keep-alive.md`.

## 1. Problem

Every response carried `connection: close` and the server closed the socket after writing it: one
TCP connection per request. Behind a proxy that puts connection setup on the path of every
request, and nginx cannot keep an upstream pool against a server that closes. repbit saw it as
`upstream timed out (110) while connecting` under parallel load. Direct clients pay it too: a 4 s
run at ~30k req/s leaves more `TIME_WAIT` sockets than the client has ephemeral ports.

## 2. Goals and non-goals

Goal: HTTP/1.1 persistent connections in every `serve*`, with the request after a response served
on the same socket.

Not goals: HTTP/1.0 keep-alive (a 1.0 request closes; RFC 9112 makes 1.0 persistence opt-in),
HTTP/2, chunked transfer coding, and serving requests on one connection concurrently. Requests
sent back to back are served in order, one after another.

## 3. When a connection stays open

After a response, the connection stays open only if all of these hold:

1. The request is `HTTP/1.1` and its `Connection` options do not include `close` (RFC 9112 §9.3;
   options are a comma list, case-insensitive, RFC 9110 §7.6.1). Repeated `Connection` lines join
   with a comma (RFC 9110 §5.3), so a `close` in any of them counts.
2. The handler did not set `connection: close`.
3. The read succeeded. After a failed read (400, 408, 413, 431, 501) the framing of what follows is
   unknown, so the reply closes.
4. The response has an exact length. The 500 fallback is framed only by the close.
5. The server has capacity for one more idle connection (§4).
6. The write succeeded.

A closing response says `connection: close` (RFC 9112 §9.6), overriding any other value a handler
set. A kept response sends no `connection` header, persistence being HTTP/1.1's default.

Bytes read past the end of a request are the start of the next one and are served without
waiting. They are kept as the read buffer plus an offset, so a buffer of many pipelined requests is
not copied once per request. Otherwise the server waits up to `idle_ms` from the response for the
next request's first byte; the header budget (`header_ms`) starts at that byte. When the wait
expires, or the client closes or resets, the server closes and sends nothing: no request was made,
so none is answered.

Empty lines before a request line are skipped (RFC 9112 §2.2): some clients send a CRLF after a
body, and it must not start a request, even when its CR and LF arrive in separate reads. They do
not restart the wait, and the deadline is checked before every read, so a client sending only CRLFs
is closed at `idle_ms` like a silent one, however fast it sends them.

`max_header_bytes` is checked on every header block, including one already read with the request
before it.

HEAD replies carry no body and 204 replies carry neither body nor `content-length`. Close hid both
mistakes before; on a kept connection the stray bytes would be read as the next response. That fix
landed first, in its own change.

## 4. Capacity: `Admission`

**`serve` / `serve_n`** run a task per connection, so an idle connection costs a parked task
(~1.4 MiB, `docs/green-task-pool-v0.md` §3.1) and no worker. `max_idle_connections` (default 256)
caps the connections waiting at once; at the cap a response closes. Without it one client could
hold the 2048-slot connection table for `idle_ms` with idle sockets.

**`serve_pooled`**: a worker waiting on an idle connection serves nothing else, and Sprout cannot
wait on a socket and a channel at once. Two rules, both checked before the response is written:

- at most ¾ of the workers may wait on idle connections, so a quarter stay free to take new ones;
- while a connection is queued for a worker, a response closes and its worker returns to the queue.

The decision is never made during the idle wait. A client told `connection: close` does not reuse
the connection; one closed while idle can race a request the client is already sending, and nginx
does not retry a non-idempotent request that hit a closed connection (`proxy_next_upstream`).

A kept connection takes its place in the idle count when the decision is made, and holds it through
the write and the idle wait until its next request starts or it closes. Counting only during the
wait let a write that parks — a large response to a slow reader — pass the same check as others, so
the caps could be exceeded by the number of writes in flight. With ¾ of 1 rounded down to 0, a
one-worker pool never keeps a connection.

The counters are `Ref Int`s shared by the accept loop and the workers. The scheduler runs one task
at a time and nothing parks between a counter's read and its write, so they need no lock. A
multi-core scheduler would need them per-scheduler or atomic.

## 5. Defaults, and prior art

`idle_ms` defaults to 65 s: nginx's upstream `keepalive_timeout` is 60 s, so a proxy in front closes
an idle connection first. Puma ships the same 65 s.

| | idle timeout | max requests per connection | fixed pool and idle connections |
|---|---|---|---|
| nginx (client side) | `keepalive_timeout 75s` | `keepalive_requests 1000` | event loop; n/a |
| nginx (upstream) | `keepalive_timeout 60s` | `keepalive_requests 1000` | caches idle connections per worker process; LRU closes past the cap |
| Apache httpd 2.4 | `KeepAliveTimeout 5` | `MaxKeepAliveRequests 100` | event MPM hands idle sockets to a listener thread, and "if all workers are busy, it will close connections in keep-alive state" |
| Go `net/http` | `IdleTimeout`, else `ReadTimeout`, else none | none | goroutine per connection; n/a |
| Tomcat 8.0 BIO | `keepAliveTimeout` = `connectionTimeout` | `maxKeepAliveRequests 100` | `disableKeepAlivePercentage` 75: keep-alive off once 75% of threads are busy |
| Puma | `persistent_timeout 65` | `max_keep_alive 999` | keeps serving on a thread only `if @thread_pool.waiting > 0`; otherwise requeues, and a reactor holds idle sockets |
| gunicorn sync | — | — | no persistent connections at all |
| Unicorn | — | — | no persistent connections: "a worker cannot serve other clients while keeping a connection alive" |

What this design takes: the 65 s default (Puma, above nginx's 60 s); Apache's and Puma's "close
when the pool is busy", applied when the response is written; Tomcat's fraction of the pool as the
idle limit. What it leaves: a per-connection request cap, which the others use to free
per-connection allocations, which this server does not keep; and the reactor, which needs a
runtime primitive (§6).

Sources: RFC 9110 §7.6.1, §8.6, §9.3.2, §15.3.5 and RFC 9112 §9.3, §9.6 (rfc-editor.org);
nginx.org `ngx_http_core_module`, `ngx_http_upstream_module` (`keepalive`),
`ngx_http_proxy_module`; httpd.apache.org/docs/2.4 `mod/core.html` and `mod/event.html`;
golang/go `src/net/http/server.go` (`Server.IdleTimeout`); tomcat.apache.org
`tomcat-8.0-doc/config/http.html`; puma/puma `lib/puma/configuration.rb`, `lib/puma/server.rb`;
gunicorn `docs/source/design.rst`; yhbt.net/unicorn `PHILOSOPHY.html`.

## 6. Limits and follow-ups

- **Pooled keep-alive turns mostly off under more clients than workers.** The fix is a task that
  holds every idle socket and hands readable ones to the workers, as Apache's event MPM and Puma's
  reactor do. It needs a runtime primitive (a wait over many handles). BACKLOG.
- **No lingering close.** A closing reply sent with request bytes still unread can be lost to a RST
  (RFC 9112 §9.6): an error reply, or any reply that closes a connection the client is pipelining
  on. Go half-closes and waits; that needs `tcp_shutdown_write`. BACKLOG.
- **Behind nginx older than 1.29.7**, upstream keep-alive needs both `keepalive N;` in the
  `upstream` block and `proxy_set_header Connection "";`, since `proxy_set_header` defaults to
  `Connection close`. nginx 1.29.7 turned upstream keepalive on by default.
