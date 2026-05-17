from __future__ import annotations

import hashlib
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

from .codegen_llvm import CodegenError
from .interpreter import run_program
from .module_loader import load_module_bundle, resolve_program_names
from .parser import parse
from .repl_host import reset_hosted_repl_session
from .surface_checks import validate_public_surface
from .typeclass_lowering import lower_typeclasses
from .typechecker import typecheck_program

__all__ = [
    "cmd_repl",
    "default_analysis_service_bin_cmd",
]


def _native_repl_entry() -> Path:
    return Path(__file__).resolve().parent.parent / "examples" / "repl_hosted.sprout"


def _native_repl_cache_dir() -> Path:
    override = os.environ.get("SPROUT_NATIVE_REPL_CACHE_DIR")
    if override:
        return Path(override)
    return Path(tempfile.gettempdir()) / "sprout-native-repl-cache"


def _native_repl_cache_key() -> str:
    root = Path(__file__).resolve().parent.parent
    digest = hashlib.sha256()
    digest.update(sys.executable.encode("utf-8"))
    digest.update(b"\0")
    digest.update(sys.version.encode("utf-8"))
    digest.update(b"\0")
    digest.update(sys.platform.encode("utf-8"))
    digest.update(b"\0")
    for path in sorted((root / "sprout").glob("*.py")):
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    for path in sorted((root / "stdlib").glob("*.sprout")):
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    entry = _native_repl_entry()
    digest.update(entry.name.encode("utf-8"))
    digest.update(b"\0")
    digest.update(entry.read_bytes())
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
        "Use `python -m sprout.cli repl` for the interpreter-backed REPL."
    )


def _ensure_native_repl_binary() -> Path:
    out = _native_repl_binary_path()
    if out.exists():
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    entry = _native_repl_entry()
    with tempfile.TemporaryDirectory(dir=out.parent) as tmp:
        tmp_out = Path(tmp) / "sprout-repl"
        try:
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "sprout.cli",
                    "compile",
                    str(entry),
                    "--native",
                    "-o",
                    str(tmp_out),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            raise CodegenError(_summarize_native_repl_build_error(exc)) from exc
        os.replace(tmp_out, out)
    return out


def cmd_repl(*, native: bool = False) -> int:
    if native:
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
        run = subprocess.run([str(out)], env=env, check=False)
        return run.returncode

    reset_hosted_repl_session()
    entry = Path(__file__).resolve().parent.parent / "stdlib" / "repl.sprout"
    bundle = load_module_bundle(entry)
    tree = parse(bundle.source)
    resolve_program_names(tree, bundle)
    validate_public_surface(tree, bundle)
    typecheck_program(tree)
    lowered = lower_typeclasses(tree)
    typecheck_program(lowered)
    run_program(lowered)
    return 0
