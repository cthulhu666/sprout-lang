from __future__ import annotations

import atexit
from contextlib import contextmanager
import errno
import hashlib
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from collections.abc import Callable, Iterator


class BackgroundWorker:
    def __init__(self, target: Callable[[], None], *, name: str) -> None:
        self.errors: list[BaseException] = []

        def guarded() -> None:
            try:
                target()
            except BaseException as exc:  # pragma: no cover
                self.errors.append(exc)

        self._thread = threading.Thread(target=guarded, daemon=True, name=name)

    def start(self) -> None:
        self._thread.start()

    def join_ok(self, case: unittest.TestCase, *, timeout: float, alive_message: str) -> None:
        self._thread.join(timeout=timeout)
        case.assertFalse(self._thread.is_alive(), alive_message)
        case.assertFalse(self.errors, f"{self._thread.name} raised: {self.errors!r}")


def find_free_port(case: unittest.TestCase) -> int:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            return probe.getsockname()[1]
    except PermissionError:
        case.skipTest("network socket bind not permitted in this environment")
    raise AssertionError("unreachable")


_NATIVE_BINARY_CACHE_ROOT = Path(tempfile.mkdtemp(prefix="sprout-native-cache-"))
_NATIVE_BINARY_CACHE: dict[str, Path] = {}
_NATIVE_BINARY_BUILD_LOCKS: dict[str, threading.Lock] = {}
_NATIVE_BINARY_CACHE_LOCK = threading.Lock()


def _cleanup_native_binary_cache() -> None:
    import shutil

    shutil.rmtree(_NATIVE_BINARY_CACHE_ROOT, ignore_errors=True)


atexit.register(_cleanup_native_binary_cache)


def _native_binary_cache_key(source: str, *, with_http_stdlib: bool, filename: str) -> str:
    digest = hashlib.sha256()
    digest.update(filename.encode("utf-8"))
    digest.update(b"\0")
    digest.update(b"1" if with_http_stdlib else b"0")
    digest.update(b"\0")
    digest.update(source.encode("utf-8"))
    digest.update(b"\0")
    for key in sorted(name for name in os.environ if name.startswith("SPROUT_")):
        digest.update(key.encode("utf-8"))
        digest.update(b"=")
        digest.update(os.environ[key].encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def _native_binary_cache_path(key: str) -> Path:
    return _NATIVE_BINARY_CACHE_ROOT / key / "prog"


def _native_binary_build_lock(key: str) -> threading.Lock:
    with _NATIVE_BINARY_CACHE_LOCK:
        build_lock = _NATIVE_BINARY_BUILD_LOCKS.get(key)
        if build_lock is None:
            build_lock = threading.Lock()
            _NATIVE_BINARY_BUILD_LOCKS[key] = build_lock
        return build_lock


def _compiled_native_binary_path(
    case: unittest.TestCase,
    source: str,
    *,
    with_http_stdlib: bool = False,
    filename: str = "prog.sprout",
) -> Path:
    key = _native_binary_cache_key(source, with_http_stdlib=with_http_stdlib, filename=filename)
    build_lock = _native_binary_build_lock(key)
    with build_lock:
        with _NATIVE_BINARY_CACHE_LOCK:
            cached = _NATIVE_BINARY_CACHE.get(key)
            if cached is not None and cached.exists():
                return cached

        cache_dir = _NATIVE_BINARY_CACHE_ROOT / key
        cache_dir.mkdir(parents=True, exist_ok=True)
        final_bin = _native_binary_cache_path(key)
        if final_bin.exists():
            with _NATIVE_BINARY_CACHE_LOCK:
                _NATIVE_BINARY_CACHE[key] = final_bin
            return final_bin

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            spr_path = tmp_path / filename
            tmp_bin = cache_dir / f"prog.{os.getpid()}.{threading.get_ident()}"
            spr_path.write_text(source, encoding="utf-8")
            cmd = [sys.executable, "-m", "sprout.cli", "compile"]
            if with_http_stdlib:
                cmd.append("--with-http-stdlib")
            cmd.extend([str(spr_path), "--native", "-o", str(tmp_bin)])
            compile_proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
            case.assertEqual(
                compile_proc.returncode,
                0,
                msg=f"stdout:\n{compile_proc.stdout}\nstderr:\n{compile_proc.stderr}",
            )
            tmp_bin.replace(final_bin)

        with _NATIVE_BINARY_CACHE_LOCK:
            _NATIVE_BINARY_CACHE[key] = final_bin
        return final_bin


@contextmanager
def running_http_server(
    case: unittest.TestCase,
    handler_cls: type[BaseHTTPRequestHandler],
) -> Iterator[int]:
    try:
        server = HTTPServer(("127.0.0.1", 0), handler_cls)
    except PermissionError:
        case.skipTest("network socket bind not permitted in this environment")
    thread = threading.Thread(target=server.serve_forever, daemon=True, name="http-server")
    thread.start()
    try:
        yield int(server.server_address[1])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1.0)


@contextmanager
def running_tcp_fixture(
    case: unittest.TestCase,
    handler: Callable[[socket.socket], None],
) -> Iterator[int]:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        listener.bind(("127.0.0.1", 0))
    except PermissionError:
        listener.close()
        case.skipTest("network socket bind not permitted in this environment")
    listener.listen(1)
    port = int(listener.getsockname()[1])

    def serve() -> None:
        with listener:
            try:
                conn, _ = listener.accept()
                with conn:
                    handler(conn)
            except ConnectionAbortedError:
                # Native TCP tests can race fixture teardown on macOS and report an
                # aborted connection even though the client-side behavior under test
                # already completed.
                return
            except OSError as exc:
                if exc.errno == errno.ECONNABORTED:
                    return
                raise

    worker = BackgroundWorker(serve, name="tcp-fixture")
    worker.start()
    try:
        yield port
    finally:
        try:
            listener.close()
        except OSError:
            pass
        worker.join_ok(case, timeout=1.0, alive_message="tcp fixture did not exit")


def connect_with_retry(port: int, *, timeout: float = 2.0, connect_timeout: float = 0.5) -> socket.socket:
    deadline = time.time() + timeout
    last_error: OSError | None = None
    while time.time() < deadline:
        try:
            return socket.create_connection(("127.0.0.1", port), timeout=connect_timeout)
        except OSError as exc:
            last_error = exc
            time.sleep(0.02)
    raise AssertionError(f"timed out connecting to local tcp server on port {port}: {last_error}")


def tcp_roundtrip(port: int, request: bytes, *, timeout: float = 2.0, recv_size: int = 4096) -> bytes:
    deadline = time.time() + timeout
    last_error: OSError | None = None
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5) as client:
                if request:
                    client.sendall(request)
                client.shutdown(socket.SHUT_WR)
                chunks: list[bytes] = []
                while True:
                    chunk = client.recv(recv_size)
                    if not chunk:
                        return b"".join(chunks)
                    chunks.append(chunk)
        except OSError as exc:
            last_error = exc
            time.sleep(0.02)
    raise AssertionError(f"timed out connecting to local test service on port {port}: {last_error}")


@contextmanager
def compiled_native_binary(
    case: unittest.TestCase,
    source: str,
    *,
    with_http_stdlib: bool = False,
    filename: str = "prog.sprout",
) -> Iterator[Path]:
    yield _compiled_native_binary_path(
        case,
        source,
        with_http_stdlib=with_http_stdlib,
        filename=filename,
    )
