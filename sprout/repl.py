from __future__ import annotations

from pathlib import Path

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


def cmd_repl() -> int:
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
