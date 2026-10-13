# HTTP keep-alive A/B — 2026-10-13

`just bench-keep-alive` (`bench/http_keep_alive/bench.sh`), defaults: 2 rounds, `-d4s`, wrk
`-t2`. One server source, `bench/http_keep_alive/server.sprout` (a fixed 15-byte JSON reply),
compiled with the same compiler against two stdlib + runtime trees:

- **before**: `973694b0`, the last commit whose responses all close;
- **after**: keep-alive (`docs/http-keep-alive-v0.md`);
- **after+close**: `after`, with wrk sending `Connection: close` on every request.

Machine: Apple M3 Pro (11 cores), macOS 15.7.5, wrk 4.2.0, nginx 1.29.1. `time_wait` counts the
`TIME_WAIT` sockets on the bench ports after a run: one per connection opened and closed. Ports are
drained before each run. Round 1 / round 2:

| scenario | variant | req/s | p50 | p99 | time_wait | RSS |
|---|---|---|---|---|---|---|
| `serve`, 40 conns | before | 10.9k / 11.2k | 1.33 / 1.32 ms | 7.8 / 14.0 ms | 18.8k / 18.5k | 218 / 234 MB |
| | **after** | **56.1k / 56.2k** | 715 / 710 µs | **0.80 / 1.02 ms** | **3 / 3** | 45 / 10 MB |
| | after+close | 10.8k / 10.9k | 1.37 / 1.32 ms | 5.6 / 7.1 ms | 18.3k / 18.1k | 202 / 218 MB |
| `serve_pooled`, 40 conns | before | 35.1k / 35.8k | 343 / 342 µs | 49.7 / 45.7 ms | 21.9k / 21.9k | 4 / 6 MB |
| | after | 46.0k / 45.9k | 355 / 357 µs | 48.1 / 29.7 ms | 21.1k / 21.0k | 4 / 5 MB |
| | after+close | 34.1k / 36.0k | 359 / 340 µs | 6.8 / 83.0 ms | 22.1k / 22.2k | 3 / 3 MB |
| `serve_pooled`, 4 conns | before | 27.5k / 31.0k | 66 / 61 µs | 71.6 / 38.6 ms | 20.2k / 21.1k | 4 / 3 MB |
| | **after** | **56.8k / 57.1k** | 60 / 60 µs | **141 / 109 µs** | **5 / 18** | 13 / 6 MB |
| | after+close | 29.2k / 28.7k | 69 / 70 µs | 84.2 / 110.5 ms | 19.5k / 19.6k | 3 / 3 MB |
| `serve` behind nginx, 40 conns | before | 11.0k / 11.0k | 2.22 / 2.20 ms | 117 / 191 ms | 17.5k / 17.5k | 223 / 185 MB |
| | **after** | **55.1k / 55.2k** | 725 / 722 µs | **1.19 / 1.29 ms** | **609 / 615** | 159 / 131 MB |

## Reading it

- **The gain is connection reuse.** `after+close` lands on `before` in every scenario: same
  server code, same stdlib, only the client's `Connection: close` differs.
- **`serve`: 5× throughput, p99 down 7–14×, memory down.** A task per *connection* instead of
  per request: 18.8k connections became 40, so ~18.8k task setups (each zeroing a 1 MiB stack)
  disappear, and with them most of the 218 MB.
- **`serve_pooled` within its reserve: 2× throughput, p99 from tens of ms to ~0.1 ms.** At 4
  clients against 8 workers every connection stays open. The `before` p99 is the client stalling on
  ephemeral ports (16,384 against ~30k new connections/s), not the server; keep-alive removes the
  cause.
- **`serve_pooled` at 40 clients mostly closes, as designed.** 40 clients against 8 workers keep a
  connection queued almost always, so most responses close (`time_wait` barely moves). Throughput
  still rises 30%, from the responses that find the queue empty. Holding idle sockets without a
  worker each needs a runtime primitive (BACKLOG, `docs/http-keep-alive-v0.md` §6).
- **Behind nginx, the #390 setup: 5× throughput, p99 from 117–191 ms to ~1.2 ms.** nginx keeps
  16 idle upstream connections; with 40 clients it opens and closes the rest as load moves, which is
  the 609 leftover connections. Before, every proxied request was a new upstream connection.
- Throughput and p50 repeat within a few percent across rounds. The p99 of the closing variants
  does not, since it is set by port recycling on the client.
