from __future__ import annotations

from dataclasses import dataclass

from .tokenizer import Token, tokenize


INLINE_OPS = {
    "=",
    "->",
    "<-",
    "+",
    "-",
    "*",
    "/",
    "==",
    "!=",
    "<",
    "<=",
    ">",
    ">=",
    "&&",
    "||",
    ">>",
    "<<",
    "++",
    "|>",
}


@dataclass(frozen=True)
class LintIssue:
    line: int
    column: int
    message: str


def format_source(source: str) -> str:
    formatted_lines = [_format_line(line) for line in source.splitlines()]
    formatted = "\n".join(formatted_lines)
    return formatted + "\n"


def lint_source(source: str) -> list[LintIssue]:
    issues: list[LintIssue] = []
    lines = source.splitlines()
    for line_no, raw_line in enumerate(lines, start=1):
        if "\t" in raw_line:
            issues.append(LintIssue(line=line_no, column=raw_line.index("\t") + 1, message="tab indentation is not allowed"))
        stripped = raw_line.rstrip(" \t")
        if stripped != raw_line:
            issues.append(
                LintIssue(
                    line=line_no,
                    column=len(stripped) + 1,
                    message="trailing whitespace",
                )
            )
    if source and not source.endswith("\n"):
        last_line = len(lines) if lines else 1
        last_col = len(lines[-1]) + 1 if lines else 1
        issues.append(LintIssue(line=last_line, column=last_col, message="missing trailing newline"))
    if format_source(source) != (source if source.endswith("\n") else source + "\n"):
        issues.append(LintIssue(line=1, column=1, message="file is not formatted"))
    return issues


def _format_line(line: str) -> str:
    stripped = line.strip()
    if stripped == "":
        return ""
    indent = _leading_indent(line)
    code_part, comment_part = _split_comment(line)
    code = code_part.strip()
    comment = comment_part.rstrip()
    if code == "":
        return indent + comment.lstrip()
    formatted_code = _format_code(code)
    if not comment:
        return indent + formatted_code
    return f"{indent}{formatted_code}  {comment.lstrip()}"


def _leading_indent(line: str) -> str:
    indent_chars: list[str] = []
    for ch in line:
        if ch in {" ", "\t"}:
            indent_chars.append("  " if ch == "\t" else ch)
            continue
        break
    return "".join(indent_chars)


def _split_comment(line: str) -> tuple[str, str]:
    n = len(line)
    i = 0
    while i < n:
        ch = line[i]
        if ch == '"':
            i += 1
            while i < n:
                c = line[i]
                if c == "\\":
                    i += 2
                elif c == '"':
                    i += 1
                    break
                else:
                    i += 1
        elif ch == "'":
            # Char literal: 'x' or '\x'; skip to the closing quote.
            i += 1
            if i < n and line[i] == "\\":
                i += 3  # skip \, escape char, closing '
            elif i < n:
                i += 2  # skip char body and closing '
        elif ch == "#":
            return line[:i], line[i:]
        else:
            i += 1
    return line, ""


def _format_code(code: str) -> str:
    try:
        tokens = [token for token in tokenize(code) if token.kind != "EOF"]
    except Exception:
        return code
    # Template strings cannot be safely re-spaced without source-level reconstruction;
    # pass the line through unchanged to avoid corrupting interpolations.
    if any(t.kind.startswith("TEMPLATE") for t in tokens):
        return code
    parts: list[str] = []
    prev_prev: Token | None = None
    prev: Token | None = None
    for i, token in enumerate(tokens):
        next_token = tokens[i + 1] if i + 1 < len(tokens) else None
        has_equals_ahead = any(later.value == "=" for later in tokens[i + 1 :])
        if parts and _needs_space(prev_prev, prev, token, next_token, has_equals_ahead):
            parts.append(" ")
        parts.append(_render_token(token))
        prev_prev = prev
        prev = token
    return "".join(parts)


def _render_token(token: Token) -> str:
    if token.kind == "CHAR":
        escaped = (
            token.value.replace("\\", "\\\\")
            .replace("'", "\\'")
            .replace("\0", "\\0")
            .replace("\n", "\\n")
            .replace("\r", "\\r")
            .replace("\t", "\\t")
        )
        return f"'{escaped}'"
    if token.kind == "STRING":
        escaped = (
            token.value.replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("\0", "\\0")
            .replace("\n", "\\n")
            .replace("\r", "\\r")
            .replace("\t", "\\t")
        )
        return f'"{escaped}"'
    return token.value


def _sym(token: Token) -> str:
    """Return .value for SYMBOL tokens only; empty string for all other kinds.

    Prevents STRING/CHAR tokens whose content matches punctuation (e.g. ")" or ":")
    from triggering punctuation-specific spacing rules.
    """
    return token.value if token.kind == "SYMBOL" else ""


def _needs_space(
    prev_prev: Token | None,
    prev: Token | None,
    curr: Token,
    next_token: Token | None,
    has_equals_ahead: bool,
) -> bool:
    if prev is None:
        return False
    cs = _sym(curr)
    ps = _sym(prev)
    if cs == "!" and ps not in {"(", "[", "{", ",", "|", "!"}:
        return True
    if ps == "!":
        return cs != "{"
    if cs == ":":
        return False
    if ps == ":":
        return True
    if ps == "-" and _is_unary_minus(prev_prev):
        return False
    if ps == ",":
        return True
    if cs == ",":
        return False
    if cs in {")", "]", "}"}:
        return False
    if ps in {")", "]", "}"} and curr.kind in {"IDENT", "KEYWORD", "INT", "STRING", "CHAR"}:
        return True
    if ps in {"(", "[", "{"}:
        return False
    if curr.kind == "KEYWORD" and ps not in {"(", "[", "{", "|"}:
        return True
    if cs == "{":
        return True
    if cs == "(":
        if _is_call_like(prev_prev, prev, next_token, has_equals_ahead):
            return False
        return True
    if ps in INLINE_OPS or cs in INLINE_OPS:
        return True
    if ps == "|" or cs == "|":
        return True
    if _is_word_like(prev) and _is_word_like(curr):
        return True
    return False


def _is_word_like(token: Token) -> bool:
    return token.kind in {"IDENT", "KEYWORD", "INT", "STRING", "CHAR"}


def _is_call_like(
    prev_prev: Token | None,
    prev: Token,
    next_token: Token | None,
    has_equals_ahead: bool,
) -> bool:
    if _sym(prev) == ")":
        # Uppercase type argument after ) is a type application, not a call: Ctor(A B)(C D)
        if next_token is not None and next_token.kind == "IDENT" and next_token.value[:1].isupper():
            return False
        return True
    if _sym(prev) in {"]", "}"}:
        return True
    if prev.kind != "IDENT":
        return False
    if prev_prev is None:
        return True
    pps = _sym(prev_prev)
    if pps == "=":
        return True
    if prev_prev.kind == "KEYWORD" and prev_prev.value in {"then", "else"}:
        return True
    if prev_prev.kind == "KEYWORD" and prev_prev.value == "where":
        return False
    if pps == "|":
        return False
    if pps == "->":
        if not has_equals_ahead:
            return True
        return not _looks_like_type_group(prev, next_token)
    if prev_prev.kind == "KEYWORD":
        return prev_prev.value not in {"import", "class", "instance", "type"}
    if prev_prev.kind in {"IDENT", "INT", "STRING", "CHAR"}:
        return False
    # Check _looks_like_type_group BEFORE the ) branch so that
    # "Type( ... ) TypeArg (" is not treated as a call site.
    if _looks_like_type_group(prev, next_token):
        return False
    if pps in {")", "]", "}"}:
        return True
    if pps in {"|", "="} | INLINE_OPS | {",", "(", "[", "{"}:
        return True
    return False


def _is_unary_minus(prev_prev: Token | None) -> bool:
    if prev_prev is None:
        return True
    pps = _sym(prev_prev)
    if pps in {"(", "[", "{", ",", "|"}:
        return True
    if pps in INLINE_OPS:
        return True
    if prev_prev.kind == "KEYWORD":
        return True
    return False


def _looks_like_type_group(prev: Token, next_token: Token | None) -> bool:
    if not prev.value[:1].isupper():
        return False
    if next_token is None:
        return False
    return next_token.kind == "IDENT" and next_token.value[:1].isupper()
