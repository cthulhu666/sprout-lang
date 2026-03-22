from __future__ import annotations

import hashlib
import json
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


def _native_analysis_service_command() -> str:
    return os.environ.get("SPROUT_ANALYSIS_SERVICE_CMD", f"{shlex.quote(sys.executable)} -m sprout.analysis_service")


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


def _ensure_native_analysis_service_ready() -> None:
    request = json.dumps({"op": "check_source", "module_source": "module app.repl"}) + "\n"
    cmd = _native_analysis_service_command()
    try:
        run = subprocess.run(
            cmd,
            input=request,
            capture_output=True,
            text=True,
            check=False,
            shell=True,
        )
    except OSError as exc:
        raise CodegenError(
            f"native REPL startup failed while validating analysis service command `{cmd}`: {exc}. "
            "Check SPROUT_ANALYSIS_SERVICE_CMD or use `python -m sprout.cli repl`."
        ) from exc
    if run.returncode != 0:
        detail = (run.stderr or run.stdout or "").strip()
        if detail:
            detail = detail.splitlines()[0]
        else:
            detail = f"command exited with status {run.returncode}"
        raise CodegenError(
            f"native REPL startup failed while validating analysis service command `{cmd}`: {detail}. "
            "Check SPROUT_ANALYSIS_SERVICE_CMD or use `python -m sprout.cli repl`."
        )
    first_line = next((line for line in run.stdout.splitlines() if line.strip() != ""), "")
    try:
        payload = json.loads(first_line)
    except json.JSONDecodeError as exc:
        raise CodegenError(
            f"native REPL startup failed while validating analysis service command `{cmd}`: invalid JSON response ({exc.msg}). "
            "Check SPROUT_ANALYSIS_SERVICE_CMD or use `python -m sprout.cli repl`."
        ) from exc
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        message = "unexpected analysis service response"
        if isinstance(payload, dict) and isinstance(payload.get("error"), str):
            message = payload["error"]
        raise CodegenError(
            f"native REPL startup failed while validating analysis service command `{cmd}`: {message}. "
            "Check SPROUT_ANALYSIS_SERVICE_CMD or use `python -m sprout.cli repl`."
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
        _ensure_native_analysis_service_ready()
        run = subprocess.run([str(out)], check=False)
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
