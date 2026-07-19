# Access-log middleware overhead — 2026-07-19

What does `stdlib.http_middleware.with_logging` cost per request? Measured two
ways on the `examples/http_web_server.sprout` server (before vs after the logging
commit — only the `serve` line differs).

**Machine:** macOS arm64, 11 cores. The Sprout server is a single-core
cooperative green-thread scheduler.

## TL;DR

- **~1.9 µs/request of compute** (format + 3 clock reads), plus the sink write:
  **~0.4 µs** to `/dev/null`, **~1.4 µs** to a real file.
- At the server's ~167 µs of single-core CPU per request, that's **~1–2%** — and
  it is **not measurable** in an end-to-end load test (run-to-run noise ~25%
  swamps it).
- The cost is **string/`Vec` formatting, not the clock syscalls.** On macOS the
  clock reads are ~10–18 ns each (commpage fast path); `format_iso8601` alone is
  ~600 ns, and the line/field assembly is the rest.

## 1. End-to-end throughput — inconclusive by design

`wrk -t2 -c50 -d10s` against `/health`, median of 3 runs:

| Variant | Median req/s | Runs |
|---|---|---|
| No logging | 5,612 | 7034 / 5387 / 5612 |
| Logging → `/dev/null` | 7,016 | 7113 / 6188 / 7016 |
| Logging → file | 6,352 | 6646 / 6352 / 5289 |

The with-logging variant scoring *higher* than baseline is impossible as a real
effect — it shows the ~25% run-to-run noise dwarfs the signal. The server is
`Connection: close`, so TCP connect/accept/close dominates each request; logging's
few microseconds are below the noise floor. **Conclusion: server-level overhead
of logging is unmeasurable here, well under a few percent.**

## 2. Isolated per-request cost — the precise number

`bench/http_log_middleware/bench.sh` (1M ops/variant, warm, self-timed with the
monotonic clock), representative run:

| Path | ns/op |
|---|---|
| Bare handler (`ok("ok")`, no middleware) | ~55 |
| `with_logging`, discarding sink (compute only) | ~1,940 |
| `with_logging`, sink → `/dev/null` | ~2,300 |
| `with_logging`, sink → real file | ~3,340 |

- Logging **compute** (discarding − bare): **~1.9 µs/request**
- **write** cost: **~0.4 µs** (`/dev/null`) / **~1.4 µs** (file)

## 3. Where the compute goes

| Component (per op) | ns |
|---|---|
| `time_now_micros` (monotonic) ×2 | ~34 |
| `wall_time_micros` (realtime) ×1 | ~11 |
| `format_iso8601` (the timestamp) | ~600 |
| line + field-`Vec` assembly + log plumbing | ~1,240 |

**The clocks are a non-issue** (~45 ns for all three; macOS reads them via the
commpage). The real cost is **allocation and formatting**: `format_iso8601`
allocates ~7 small strings (`int_to_string`/`concat`/`pad`), and `all_fields`
(repeated `vec_append`) + line assembly + GC churn is the largest single chunk.

## If it ever needs optimizing (it doesn't now)

The lever is the **formatting allocations**, not the clocks or the write: a
pre-sized buffer for the ISO-8601 timestamp, or a bytes-builder for the whole
line, would take the biggest bite. Per the repo guideline (no optimizing without
a measured bottleneck), ~1–2% of a real request is not worth it today.

Reproduce: `bench/http_log_middleware/bench.sh` (needs `build/compile_driver_bin_stage1`;
run `just bootstrap-from-seed` first). The wrk A/B used the pre/post-logging
binaries built from `feat/stdlib-logging:examples/http_web_server.sprout` vs the
current example.
