from __future__ import annotations

import os
from pathlib import Path
import sys

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

_REPL_INTERACTIVE_ENV = "SPROUT_REPL_INTERACTIVE"


def cmd_repl() -> int:
    reset_hosted_repl_session()
    interactive = sys.stdin.isatty() and sys.stdout.isatty()
    entry = Path(__file__).resolve().parent.parent / "stdlib" / "repl.sprout"
    bundle = load_module_bundle(entry)
    tree = parse(bundle.source)
    resolve_program_names(tree, bundle)
    validate_public_surface(tree, bundle)
    typecheck_program(tree)
    lowered = lower_typeclasses(tree)
    typecheck_program(lowered)

    old_mode = os.environ.get(_REPL_INTERACTIVE_ENV)
    try:
        if interactive:
            os.environ[_REPL_INTERACTIVE_ENV] = "1"
        else:
            os.environ.pop(_REPL_INTERACTIVE_ENV, None)
        run_program(lowered)
    finally:
        if old_mode is None:
            os.environ.pop(_REPL_INTERACTIVE_ENV, None)
        else:
            os.environ[_REPL_INTERACTIVE_ENV] = old_mode
    return 0
