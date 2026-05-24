from __future__ import annotations

import hashlib
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .codegen_llvm import CodegenError

__all__ = [
    "cmd_repl",
    "default_analysis_service_bin_cmd",
]


def _native_repl_entry() -> Path:
    return Path(__file__).resolve().parent.parent / "stdlib" / "repl.sprout"


def _native_repl_cache_dir() -> Path:
    override = os.environ.get("SPROUT_NATIVE_REPL_CACHE_DIR")
    if override:
        return Path(override)
    return Path(tempfile.gettempdir()) / "sprout-native-repl-cache"


def _native_repl_cache_key() -> str:
    root = Path(__file__).resolve().parent.parent
    digest = hashlib.sha256()
    digest.update(sys.platform.encode("utf-8"))
    digest.update(b"\0")
    driver = root / "compile_driver_bin_stage1"
    if driver.exists():
        digest.update(driver.read_bytes())
        digest.update(b"\0")
    runtime_c = root / "runtime" / "sprout_runtime.c"
    if runtime_c.exists():
        digest.update(runtime_c.read_bytes())
        digest.update(b"\0")
    for path in sorted((root / "stdlib").rglob("*.sprout")):
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()[:16]


def _native_repl_binary_path() -> Path:
    return _native_repl_cache_dir() / f"repl-{_native_repl_cache_key()}"


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _stdlib_root() -> Path:
    return _project_root() / "stdlib"


def _analysis_service_bin() -> Path | None:
    """Return the path to analysis_service_bin if it exists and is executable."""
    candidate = _project_root() / "analysis_service_bin"
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return candidate
    return None


def default_analysis_service_bin_cmd(stdlib_root: Path | None = None) -> str | None:
    """Return a shell command string for the self-hosted analysis service binary.

    Returns None if analysis_service_bin is not present.  The stdlib_root
    argument is passed as the binary's first positional argument (argv[0] in
    Sprout) so that check_source / diagnostics_in_source can locate stdlib.
    """
    bin_path = _analysis_service_bin()
    if bin_path is None:
        return None
    root = _stdlib_root() if stdlib_root is None else stdlib_root
    return f"{shlex.quote(str(bin_path))} {shlex.quote(str(root))}"


def _summarize_native_repl_build_error(exc: subprocess.CalledProcessError) -> str:
    stderr = (exc.stderr or "").strip()
    stdout = (exc.stdout or "").strip()
    detail = stderr if stderr else stdout
    if not detail:
        detail = str(exc)
    first_line = detail.splitlines()[0]
    return (
        f"native REPL startup failed while building cached binary {_native_repl_binary_path()}: {first_line}. "
        "Build the stage-1 binary first: just build-stage1"
    )


def _ensure_native_repl_binary() -> Path:
    out = _native_repl_binary_path()
    if out.exists():
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    entry = _native_repl_entry()
    root = _project_root()
    driver = root / "compile_driver_bin_stage1"
    runtime_c = root / "runtime" / "sprout_runtime.c"
    if not driver.is_file() or not os.access(driver, os.X_OK):
        raise CodegenError(
            f"compile_driver_bin_stage1 not found at {driver}; build it first: just build-stage1"
        )
    if shutil.which("clang") is None:
        raise CodegenError(
            "clang not found in PATH; install Xcode Command Line Tools or LLVM"
        )
    with tempfile.TemporaryDirectory(dir=out.parent) as tmp:
        tmp_ll = Path(tmp) / "repl.ll"
        tmp_out = Path(tmp) / "sprout-repl"
        try:
            result = subprocess.run(
                [str(driver), "--emit-ir", str(_stdlib_root()), str(entry)],
                check=True, capture_output=True, text=True,
            )
        except subprocess.CalledProcessError as exc:
            raise CodegenError(_summarize_native_repl_build_error(exc)) from exc
        tmp_ll.write_text(result.stdout, encoding="utf-8")
        clang_cmd = ["clang", str(tmp_ll), str(runtime_c), "-O2", "-o", str(tmp_out)]
        if sys.platform == "darwin":
            clang_cmd += ["-framework", "Security", "-framework", "CoreFoundation"]
        try:
            subprocess.run(clang_cmd, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as exc:
            raise CodegenError(_summarize_native_repl_build_error(exc)) from exc
        os.replace(tmp_out, out)
    return out


def cmd_repl(*, native: bool = False) -> int:
    out = _ensure_native_repl_binary()
    env = os.environ.copy()
    # Wire the self-hosted analysis service binary when available.
    # SPROUT_ANALYSIS_SERVICE_CMD is a shell command string consumed by the
    # analysis-bridge C runtime (execl("/bin/sh", "sh", "-lc", cmd, NULL)).
    # analysis_service_bin requires the stdlib root as its first argument
    # (argv_get(0) in Sprout), so the command is "<binary> <stdlib_root>".
    if "SPROUT_ANALYSIS_SERVICE_CMD" not in env:
        native_cmd = default_analysis_service_bin_cmd()
        if native_cmd is not None:
            env["SPROUT_ANALYSIS_SERVICE_CMD"] = native_cmd
    # Tell the analysis service to add -framework flags when linking eval binaries.
    if sys.platform == "darwin":
        env.setdefault("SPROUT_DARWIN_FRAMEWORKS", "1")
    run = subprocess.run([str(out)], env=env, check=False)
    return run.returncode
