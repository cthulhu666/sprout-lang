# Live-connection cap in `stdlib.http_server` (v0)

**Status:** landed (experimental, like all of `stdlib.http_server`; not part of normative v0).
Code: `stdlib/http_server.sprout` (`ServerConfig.max_live_connections`, `Admission`, `spawn_handler`,
`serve_connection`). Tests: `tests/task_io_smoke/http_live_cap.spr`,
`http_live_cap_zero_fails.spr`, `http_live_cap_table_fails.spr`, `http_idle_clamp.spr`,
`accept_fairness.spr`.

## 1. Problem

`serve` and `serve_n` spawn one green task per accepted connection. A task costs about 1.4 MiB
(`docs/green-task-pool-v0.md` §3.1), idle keep-alive connections included. The only hard limit was
the runtime's connection table: 2047 handles (`alloc_conn_handle` in `runtime/sprout_runtime.c`),
shared with outbound `tcp_connect`. With enough inbound connections open, the application's own
outbound connects fail with "connection table full".

`serve_pooled` is already bounded by its workers and queue depth, and ignores this cap.

## 2. Deployment assumption

The HTTP server is meant to run behind a reverse proxy such as nginx. The cap is sized for that
case: a proxy absorbs slow and bursty clients, so this server sees a bounded number of fast
connections, and the cap only fires when the proxy is misconfigured or the server is also exposed
directly.

What nginx bounds, and what it does not (nginx.org, `ngx_http_proxy_module`,
`ngx_http_upstream_module`, `ngx_core_module`):

| | default | effect here |
|---|---|---|
| `proxy_request_buffering` | `on` | "the entire request body is read from the client before sending the request to a proxied server": a slow client holds nginx, not a task here |
| `proxy_read_timeout` | 60 s | how long nginx waits for a response from this server |
| `proxy_next_upstream` | `error timeout` | an upstream error is retried on another server; a non-idempotent request (POST, LOCK, PATCH) is not passed on once it was sent (1.9.13) |
| upstream `keepalive` | n/a | "does not limit the total number of connections to upstream servers that an nginx worker process can open" |
| `max_conns` (open source since 1.11.5) | 0, no limit | the only per-server bound on upstream connections; per worker process unless the group has a shared-memory `zone` |
| `worker_connections` | 512 | per worker process; counts upstream connections as well as client ones |

So with defaults nothing in nginx caps the connections it opens to this server below its own
`worker_connections` times its worker count. `max_conns` is the knob that does.

## 3. What happens at the cap

`max_live_connections` counts every connection that has a handler task in `serve` and `serve_n`:
serving or idle keep-alive. Default 512. When the count is at the cap, the accept loop accepts the
new connection and closes it at once, sending nothing.

| choice | what the client side sees | why not |
|---|---|---|
| stop accepting | the request waits unseen in the kernel accept queue; nginx answers 504 only after `proxy_read_timeout` (60 s) | the slowest failure, and no retry on another upstream |
| 503 | a clean error response | the write needs a deadline, which needs a task, which is what the cap limits |
| **accept and close** | nginx sees an upstream error at once and answers 502, or retries an idempotent request on another upstream (`proxy_next_upstream error timeout`) | chosen |

A refused connection does not count toward `serve_n`'s `max_connections`: it served nothing, as a
failed accept does not.

**Cost when exposed directly.** A real client past the cap is closed, not queued: it sees a reset or
an empty reply and has to retry. Under sustained load that is more visible than queuing would be.
A non-idempotent request that nginx had already sent is not retried and ends as a 502.

**Idle cap.** The effective idle cap is `min(max_idle_connections, max_live_connections * 3 / 4)`,
rounded down: the rule `Pooled` uses for its workers. A quarter of the places stay free for new
connections, and a live cap of 1 keeps nothing open (every response says `connection: close`).
Live 4 with idle 4 keeps three. nginx (since 1.29.7, `ngx_http_upstream_module`) keeps up to 32 idle
upstream connections per worker process by default (`keepalive 32 local;`), so with default sizing
(live 512, idle cap 256) one nginx worker's idle connections fit under the idle cap, and several
workers need `max_idle_connections` raised.

**Accept fairness.** `tcp_accept` parks on the listener after 16 calls in a row on that listener
that did not park inside `tcp_accept`, before its next `accept()`. A park elsewhere (a `chan_send` in
the caller, say) does not reset the count. The pump polls only when nothing is runnable, so an accept loop
that never parks (the refuse-at-cap loop, or `serve_loop` spawning handlers) would keep every other
task off the CPU while connections keep arriving. A queued connection makes the listener readable,
so the park returns at the next poll, after every runnable task had a turn. With an empty queue it
is the same wait `EAGAIN` causes. `tcp_accept` in `runtime/sprout_runtime.c` (`ACCEPT_FAIR_STREAK`).
The count is a workaround: it goes when the fix in the BACKLOG entry on the pump polling only when
the ready queue is empty lands.

## 4. Sizing

- Worst case task memory is `max_live_connections` times ~1.4 MiB (512: ~0.7 GiB), plus what the
  handlers allocate.
- Set nginx `max_conns` below the cap, so the cap only fires on misconfiguration. Without a `zone`
  `max_conns` is per worker process, so the total is `max_conns` times the worker count.
- The connection table is shared with outbound connects, so the cap must stay under 2047.
  `serve_with` and `serve_n_with` abort at start when `max_live_connections` is 0 or less, or 2047 or
  more, rather than start a server that cannot admit anyone or can starve its own outbound calls.
  512 leaves about 1500 handles.

## 5. Counting

`Spawned` carries a second `Ref Int` and its cap beside the idle counter. The accept loop takes a
place with `take_below` before `task_spawn`; `serve_connection` gives it back after
`handle_connection` returns, which is the one exit every close path of a connection reaches (read
errors, `release_and_close`, `close_with`, the end of the keep-alive loop). Like the idle counter it
needs no lock: the scheduler runs one task at a time and nothing parks between a read and a write
of the counter.

A place cannot leak on a path that keeps the server running. A handler that panics ends the
process. A spawned handler is dropped only by `scope_cancel`, which only the `with_scope` body that
opened the scope may call, and that body is the accept loop. Wrapping `serve` in `with_timeout`
aborts the program when the deadline fires (`stdlib/task.sprout`, `with_timeout`), so the counters
are never read after a drop.
