# Twelve-dollar server

A JSON API over SQLite, written for the
[twelve-dollar-server-challenge](https://github.com/arjaythedev/twelve-dollar-server-challenge):
how many concurrent users can one $12 droplet (1 vCPU, 2 GB) serve behind Nginx?
Routes: `GET /health`, `GET /feed`, `GET /posts/:id`, `POST /posts`, `POST /posts/:id/like`,
the last two behind an HS256 JWT.

## Layout

| path | what |
|---|---|
| `app/main.sprout` | HTTP/1.1 keep-alive loop over `stdlib.net`, routing, handlers |
| `app/store.sprout` | the SQL; SQLite builds every JSON object with `json_object` |
| `app/jwt.sprout` | HS256 verification with `stdlib.crypto` |
| `sqlite/`, `src/sqlite_ffi.c` | the [sprout-sqlite](https://github.com/cthulhu666/sprout-sqlite) driver, copied unchanged at `3465cca` |
| `schema.sql` | the challenge's schema (MIT) |

To update the driver, copy those three files from a newer sprout-sqlite commit and change the
commit above.

## Build and run

From the repository root, after `just bootstrap-from-seed`:

```sh
EX=examples/twelve_dollar_server
./build/compile_driver_bin_stage1 --emit-ir "$PWD/stdlib" --package-root $EX $EX/app/main.sprout > /tmp/server.ll
clang /tmp/server.ll runtime/*.c $EX/src/sqlite_ffi.c -O2 -lsqlite3 -o /tmp/server
# macOS: add -framework Security -framework CoreFoundation
sqlite3 /tmp/feed.db < $EX/schema.sql
SQLITE_PATH=/tmp/feed.db JWT_SECRET=change-me PORT=3000 /tmp/server
```

`just twelve-dollar-example` does the same against a throwaway database and checks every route.

## Design

- **One OS thread, one green task per connection.** A SQLite call runs to completion without
  yielding, so requests never interleave inside a query.
- **One SQLite connection, lent through a one-slot channel.** The handle is linear, and a task
  may not capture a borrowed one; a channel moves it. Nothing yields while it is held, so it is
  never contended.
- **Behind Nginx.** An idle connection costs about 1 MB here (a green task and its stack), so the
  server cannot hold 15k clients itself. Nginx holds them and keeps up to 64 idle upstream connections open.
- **WAL, `synchronous=NORMAL`, 1 GB `mmap_size`.**

## Measured

A stand-in for the droplet: Ubuntu 24.04 container capped at 1 CPU and 2 GB, distro Nginx with
the challenge's settings, the challenge's `bench/load.js` (k6) in a second container on an Apple
M-series Mac. Five-minute holds:

| users | k6 p95 / p99 | errors | Nginx p99 |
|---|---|---|---|
| 12,000 | 23 / 257 ms | 0 | 61 ms |
| 16,000 | 15 / 390 ms | 0 | 25 ms |
| 17,000 | 101 / 535 ms | 0 | 27 ms |
| 18,000 | 163–497 ms / 0.47–1.41 s | ≤ 1 | 81–362 ms |

18,000 passed two of three runs. Past about 15,400 open connections Nginx (`worker_connections
16384`) starts closing idle keep-alive connections, and that churn shares the one CPU with the
server. Only the shape carries over to a real droplet: its vCPU is not this Mac's.

## Limits

- The driver has no parameter binding, so values are written into the SQL: ids are validated
  integers, and text is quoted in one place, `store.quote`.
