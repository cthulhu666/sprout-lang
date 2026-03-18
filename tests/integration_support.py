from __future__ import annotations

from contextlib import contextmanager
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
            conn, _ = listener.accept()
            with conn:
                handler(conn)

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
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        spr_path = tmp_path / filename
        bin_path = tmp_path / "prog"
        spr_path.write_text(source, encoding="utf-8")
        cmd = [sys.executable, "-m", "sprout.cli", "compile"]
        if with_http_stdlib:
            cmd.append("--with-http-stdlib")
        cmd.extend([str(spr_path), "--native", "-o", str(bin_path)])
        compile_proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
        case.assertEqual(compile_proc.returncode, 0, msg=compile_proc.stderr)
        yield bin_path
