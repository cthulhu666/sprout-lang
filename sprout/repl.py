from __future__ import annotations

import atexit
from dataclasses import dataclass, field
import os
import re
from pathlib import Path
import sys
import tempfile

from . import ast
from .interpreter import RuntimeError, run_program
from .module_loader import ModuleLoadError, load_module_bundle, resolve_program_names
from .parser import ParseError, parse
from .surface_checks import SurfaceCheckError, validate_public_surface
from .tokenizer import TokenizeError
from .typeclass_lowering import TypeclassLoweringError, lower_typeclasses
from .typechecker import InferState, TypeCheckError, parse_type_expr, typecheck_program, unify

__all__ = [
    "cmd_repl",
    "ReplSession",
    "ReplOutcome",
    "repl_history_path",
    "repl_readline_tab_binding",
]

_REPL_COMMANDS = (
    ":help",
    ":quit",
    ":q",
    ":exit",
    ":type",
    ":t",
    ":instances",
    ":i",
)
_REPL_PRELUDE_NAMES = frozenset(
    {
        "Bool",
        "Cons",
        "Dict",
        "Err",
        "False",
        "Foldable",
        "Functor",
        "Int",
        "IO",
        "Just",
        "List",
        "Maybe",
        "Nil",
        "Nothing",
        "Ok",
        "Result",
        "Semigroup",
        "String",
        "True",
        "Unit",
        "Vec",
        "filter",
        "fmap",
        "fold",
        "foldable_to_vec",
        "list_append",
        "list_fold",
        "list_map",
        "map",
        "print",
        "split_ints",
        "vec_append",
        "vec_empty",
        "vec_fold",
        "vec_get",
        "vec_get_or",
        "vec_length",
        "vec_map",
        "vec_prepend",
        "vec_reverse",
        "vec_set",
        "vec_slice",
        "vec_sum",
        "vec_sum_by",
    }
)
_REPL_STDLIB_EXTRA_NAMES = frozenset(
    {
        "bytes",
        "collections",
        "crypto",
        "dict_empty",
        "dict_get",
        "dict_keys",
        "dict_remove",
        "dict_set",
        "dict_values",
        "http",
        "http_client",
        "math",
        "net",
        "string",
        "terminal",
    }
)
_REPL_TOKEN_RE = re.compile(r"[A-Za-z_:][A-Za-z0-9_:.]*$")
_REPL_HISTORY_LIMIT = 1000
_REPL_COMPLETER_DELIMS = " \t\n`~!@#$%^&*()-=+[{]}\\|;,'\"<>/?"
_REPL_MODULE_NAME = "app.repl"
_REPL_HELP_TEXT = "Commands: :type EXPR, :t EXPR, :instances TYPE, :i TYPE, :quit, :help, plus ordinary import lines"


def _repl_compose_source(
    imports: list[str],
    declarations: list[str],
    tail: list[str] | None = None,
) -> str:
    chunks = [f"module {_REPL_MODULE_NAME}"]
    chunks.extend(imp for imp in imports if imp.strip())
    chunks.extend(chunk for chunk in declarations + (tail or []) if chunk.strip())
    return "\n\n".join(chunks)


def _repl_parse_and_check(
    imports: list[str],
    declarations: list[str],
    tail: list[str] | None = None,
) -> tuple[object, dict[str, str]]:
    source = _repl_compose_source(imports, declarations, tail)
    with tempfile.TemporaryDirectory() as tmpdir:
        temp_path = Path(tmpdir) / "repl_session.sprout"
        temp_path.write_text(source, encoding="utf-8")
        bundle = load_module_bundle(temp_path)
        tree = parse(bundle.source)
        resolve_program_names(tree, bundle)
        validate_public_surface(tree, bundle)
        types = typecheck_program(tree)
        return tree, types


@dataclass
class ReplSession:
    imports: list[str] = field(default_factory=list)
    declarations: list[str] = field(default_factory=list)
    repl_counter: int = 0

    def _compose_source(
        self,
        tail: list[str] | None = None,
        *,
        imports: list[str] | None = None,
        declarations: list[str] | None = None,
    ) -> str:
        session_imports = self.imports if imports is None else imports
        session_declarations = self.declarations if declarations is None else declarations
        chunks = [f"module {_REPL_MODULE_NAME}"]
        chunks.extend(imp for imp in session_imports if imp.strip())
        chunks.extend(chunk for chunk in session_declarations + (tail or []) if chunk.strip())
        return "\n\n".join(chunks)

    def _parse_and_check(
        self,
        tail: list[str] | None = None,
        *,
        imports: list[str] | None = None,
        declarations: list[str] | None = None,
    ) -> tuple[object, dict[str, str]]:
        source = self._compose_source(tail, imports=imports, declarations=declarations)
        with tempfile.TemporaryDirectory() as tmpdir:
            temp_path = Path(tmpdir) / "repl_session.sprout"
            temp_path.write_text(source, encoding="utf-8")
            bundle = load_module_bundle(temp_path)
            tree = parse(bundle.source)
            resolve_program_names(tree, bundle)
            validate_public_surface(tree, bundle)
            types = typecheck_program(tree)
            return tree, types

    def parse_and_check(self, tail: list[str] | None = None) -> tuple[object, dict[str, str]]:
        return self._parse_and_check(tail)

    def _next_temp_name(self) -> str:
        self.repl_counter += 1
        return f"__repl_value_{self.repl_counter}"

    def add_import(self, source: str) -> None:
        self._parse_and_check(imports=self.imports + [source])
        self.imports.append(source)

    def add_declaration(self, source: str) -> None:
        self._parse_and_check(declarations=self.declarations + [source])
        self.declarations.append(source)

    def infer_type(self, expr: str) -> str:
        name = self._next_temp_name()
        _, types = self._parse_and_check([f"let {name} = {expr}"])
        return _repl_lookup_type(types, name)

    def _lookup_param_type(self, type_expr_source: str) -> ast.TypeExpr:
        probe_name = "__repl_instances_probe"
        tree, _ = self._parse_and_check([f"fn {probe_name}(__value: {type_expr_source}) -> Int = 0"])
        for decl in tree.declarations:
            if isinstance(decl, ast.FnDecl) and (decl.name == probe_name or decl.name.endswith(f".{probe_name}")):
                param = decl.params[0]
                if param.type_expr is None:
                    raise TypeCheckError("Internal error: REPL instance query lost its type annotation")
                return param.type_expr
        raise TypeCheckError("Internal error: REPL instance query probe was not found")

    def instances_for_type(self, type_expr_source: str) -> tuple[str, list[str]]:
        tree, _ = self.parse_and_check()
        query_type = self._lookup_param_type(type_expr_source)
        matches: list[str] = []
        for decl in tree.declarations:
            if not isinstance(decl, ast.InstanceDecl):
                continue
            if len(decl.constraint.args) != 1:
                continue
            if _type_expr_matches_query(decl.constraint.args[0], query_type):
                rendered_args = " ".join(
                    _type_expr_to_string(arg)
                    if not isinstance(arg, (ast.TypeApply, ast.TypeArrow, ast.TypeEffect))
                    else f"({_type_expr_to_string(arg)})"
                    for arg in decl.constraint.args
                )
                matches.append(f"{decl.constraint.class_name} {rendered_args}")
        matches.sort()
        return _type_expr_to_string(query_type), matches

    def completion_matches(self, text: str, line_buffer: str) -> list[str]:
        return _repl_completion_matches(text, line_buffer, self.imports, self.declarations)

    def run_expression(self, source: str) -> None:
        name = self._next_temp_name()
        _, types = self._parse_and_check([f"let {name} = {source}"])
        inferred_type = _repl_lookup_type(types, name)
        if inferred_type.endswith(" !{IO}"):
            if inferred_type != "Unit !{IO}":
                raise TypeCheckError("repl cannot auto-print effectful non-Unit expressions yet")
            main_body = name
        else:
            main_body = f"print({name})"
        tree, _ = self._parse_and_check([f"let {name} = {source}", f"fn main() -> Unit !{{IO}} = {main_body}"])
        lowered = lower_typeclasses(tree)
        typecheck_program(lowered)
        run_program(lowered)

    def submit(self, source: str) -> ReplOutcome:
        command = _repl_parse_command(source)
        if isinstance(command, str):
            if command == "quit":
                return ReplOutcome(should_exit=True)
            if command == "help":
                return ReplOutcome((_REPL_HELP_TEXT,))
            raise ValueError(f"Unknown REPL command kind {command!r}")
        return _repl_run_submission(self, command)


@dataclass(frozen=True)
class _ReplSubmission:
    kind: str
    value: str = ""


@dataclass(frozen=True)
class ReplOutcome:
    lines: tuple[str, ...] = ()
    should_exit: bool = False


def _repl_is_declaration(source: str) -> bool:
    stripped = source.strip()
    return stripped.startswith(("fn ", "let ", "type ", "class ", "instance ", "export "))


def _repl_parse_submission(source: str) -> _ReplSubmission:
    stripped = source.strip()
    if stripped == "":
        return _ReplSubmission("empty")
    if stripped.startswith("module "):
        return _ReplSubmission("module")
    if stripped.startswith("import "):
        return _ReplSubmission("import", source)
    if stripped.startswith(":type "):
        return _ReplSubmission("type", stripped[len(":type ") :].strip())
    if stripped.startswith(":t "):
        return _ReplSubmission("type", stripped[len(":t ") :].strip())
    if stripped.startswith(":instances "):
        return _ReplSubmission("instances", stripped[len(":instances ") :].strip())
    if stripped.startswith(":i "):
        return _ReplSubmission("instances", stripped[len(":i ") :].strip())
    if _repl_is_declaration(stripped):
        return _ReplSubmission("declaration", source)
    return _ReplSubmission("expression", source)


def _repl_parse_command(source: str) -> _ReplSubmission | str:
    stripped = source.strip()
    if stripped in {":quit", ":q", ":exit"}:
        return "quit"
    if stripped == ":help":
        return "help"
    return _repl_parse_submission(source)


def _repl_lookup_type(types: dict[str, str], name: str) -> str:
    direct = types.get(name)
    if direct is not None:
        return direct
    qualified = [typ for key, typ in types.items() if key.endswith(f".{name}")]
    if len(qualified) == 1:
        return qualified[0]
    raise KeyError(name)


def _type_expr_to_string(node: ast.TypeExpr) -> str:
    if isinstance(node, ast.TypeName):
        return node.name
    if isinstance(node, ast.TypeApply):
        base = _type_expr_to_string(node.base)
        arg = _type_expr_to_string(node.arg)
        if isinstance(node.arg, (ast.TypeApply, ast.TypeArrow, ast.TypeEffect)):
            arg = f"({arg})"
        return f"{base} {arg}"
    if isinstance(node, ast.TypeArrow):
        left = _type_expr_to_string(node.left)
        right = _type_expr_to_string(node.right)
        if isinstance(node.left, ast.TypeArrow):
            left = f"({left})"
        suffix = ""
        if node.effects:
            suffix = " !{" + ", ".join(node.effects) + "}"
        return f"{left} -> {right}{suffix}"
    if isinstance(node, ast.TypeEffect):
        return _type_expr_to_string(node.base) + " !{" + ", ".join(node.effects) + "}"
    if isinstance(node, ast.TupleType):
        return "(" + ", ".join(_type_expr_to_string(item) for item in node.items) + ")"
    raise TypeError(f"Unsupported type expression: {node!r}")


def _type_expr_outermost_base(node: ast.TypeExpr) -> ast.TypeExpr:
    current = node
    while isinstance(current, ast.TypeApply):
        current = current.base
    return current


def _type_expr_matches_query(pattern: ast.TypeExpr, query: ast.TypeExpr) -> bool:
    candidates = [query]
    query_base = _type_expr_outermost_base(query)
    if query_base is not query:
        candidates.append(query_base)
    for candidate in candidates:
        state = InferState()
        try:
            unify(
                state,
                parse_type_expr(pattern, allow_implicit_type_vars=True, state=state),
                parse_type_expr(candidate),
            )
        except TypeCheckError:
            continue
        return True
    return False


def _repl_history_path() -> Path:
    override = os.environ.get("SPROUT_REPL_HISTORY")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".sprout_repl_history"


def _repl_declared_names(declarations: list[str]) -> set[str]:
    names: set[str] = set()
    for source in declarations:
        tree = parse(source)
        for decl in tree.declarations:
            if isinstance(decl, ast.FnDecl | ast.LetDecl | ast.ClassDecl | ast.TypeDecl):
                names.add(decl.name)
            if isinstance(decl, ast.TypeDecl):
                names.update(ctor.name for ctor in decl.constructors)
            elif isinstance(decl, ast.ClassDecl):
                names.update(method.name for method in decl.methods)
            elif isinstance(decl, ast.InstanceDecl):
                names.update(method.name for method in decl.methods)
    return names


def _repl_imported_names(imports: list[str]) -> set[str]:
    names: set[str] = set()
    for source in imports:
        for line in source.splitlines():
            stripped = line.strip()
            if not stripped.startswith("import "):
                continue
            body = stripped[len("import ") :]
            if " as " in body:
                module_name, alias = body.split(" as ", 1)
                names.add(alias.strip())
                body = module_name.strip()
            if "(" in body and ")" in body:
                selected = body.split("(", 1)[1].rsplit(")", 1)[0]
                names.update(name.strip() for name in selected.split(",") if name.strip())
            else:
                names.add(body.rsplit(".", 1)[-1].strip())
    return names


def _repl_completion_matches(
    text: str,
    line_buffer: str,
    imports: list[str],
    declarations: list[str],
) -> list[str]:
    token_match = _REPL_TOKEN_RE.search(line_buffer)
    prefix = token_match.group(0) if token_match is not None else text
    names = set(_REPL_COMMANDS)
    names.update(_REPL_PRELUDE_NAMES)
    names.update(_REPL_STDLIB_EXTRA_NAMES)
    names.update(_repl_declared_names(declarations))
    names.update(_repl_imported_names(imports))
    return sorted(name for name in names if name.startswith(prefix))


def _repl_readline_tab_binding(readline_module: object) -> str:
    doc = getattr(readline_module, "__doc__", "") or ""
    if "libedit" in doc.lower():
        return "bind ^I rl_complete"
    return "tab: complete"


def _configure_repl_readline(
    session: ReplSession,
    history_path: Path | None = None,
) -> None:
    try:
        import readline
    except ImportError:
        return

    target = history_path if history_path is not None else _repl_history_path()
    readline.parse_and_bind(_repl_readline_tab_binding(readline))
    readline.parse_and_bind("set editing-mode emacs")
    if hasattr(readline, "set_completer_delims"):
        readline.set_completer_delims(_REPL_COMPLETER_DELIMS)
    readline.set_history_length(_REPL_HISTORY_LIMIT)

    def _complete(text: str, state: int) -> str | None:
        matches = session.completion_matches(text, readline.get_line_buffer())
        if state < len(matches):
            return matches[state]
        return None

    readline.set_completer(_complete)
    if target.exists():
        try:
            readline.read_history_file(target)
        except OSError:
            pass

    def _write_history() -> None:
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            readline.write_history_file(target)
        except OSError:
            pass

    atexit.register(_write_history)


def _repl_run_submission(session: ReplSession, submission: _ReplSubmission) -> ReplOutcome:
    match submission.kind:
        case "empty":
            return ReplOutcome()
        case "module":
            return ReplOutcome(("error: repl manages its module header automatically; use `import ...` directly",))
        case "import":
            session.add_import(submission.value)
            return ReplOutcome(("ok",))
        case "type":
            if submission.value == "":
                return ReplOutcome(("error: :type expects an expression",))
            return ReplOutcome((session.infer_type(submission.value),))
        case "instances":
            if submission.value == "":
                return ReplOutcome(("error: :instances expects a type",))
            query_type, matches = session.instances_for_type(submission.value)
            if not matches:
                return ReplOutcome((f"No instances for {query_type}",))
            return ReplOutcome((f"Instances for {query_type}:", *matches))
        case "declaration":
            session.add_declaration(submission.value)
            return ReplOutcome(("ok",))
        case "expression":
            session.run_expression(submission.value)
            return ReplOutcome()
        case _:
            raise ValueError(f"Unknown REPL submission kind {submission.kind!r}")


def cmd_repl() -> int:
    session = ReplSession()
    interactive = sys.stdin.isatty() and sys.stdout.isatty()

    def emit(text: str) -> None:
        print(text)

    def process_submission(source: str) -> bool:
        outcome = session.submit(source)
        for line in outcome.lines:
            emit(line)
        return outcome.should_exit

    if interactive:
        _configure_repl_readline(session)
        emit("Sprout REPL. Use :help for commands.")
        while True:
            try:
                line = input("sprout> ")
            except EOFError:
                emit("")
                break
            try:
                if process_submission(line):
                    break
            except EOFError:
                break
            except (
                ParseError,
                TokenizeError,
                TypeCheckError,
                RuntimeError,
                ModuleLoadError,
                SurfaceCheckError,
                TypeclassLoweringError,
            ) as exc:
                emit(f"error: {exc}")
        return 0

    for raw in sys.stdin:
        try:
            if process_submission(raw.rstrip("\n")):
                break
        except EOFError:
            break
        except (
            ParseError,
            TokenizeError,
            TypeCheckError,
            RuntimeError,
            ModuleLoadError,
            SurfaceCheckError,
            TypeclassLoweringError,
        ) as exc:
            emit(f"error: {exc}")
    return 0


repl_history_path = _repl_history_path
repl_readline_tab_binding = _repl_readline_tab_binding
