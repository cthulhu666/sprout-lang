from __future__ import annotations

from dataclasses import dataclass

from .tokenizer import Token, tokenize


INLINE_OPS = {
    "=",
    "->",
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
    in_string = False
    escaped = False
    for i, ch in enumerate(line):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch == "#":
            return line[:i], line[i:]
    return line, ""


def _format_code(code: str) -> str:
    tokens = [token for token in tokenize(code) if token.kind != "EOF"]
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
    if token.kind == "STRING":
        escaped = (
            token.value.replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("\n", "\\n")
            .replace("\r", "\\r")
            .replace("\t", "\\t")
        )
        return f'"{escaped}"'
    return token.value


def _needs_space(
    prev_prev: Token | None,
    prev: Token | None,
    curr: Token,
    next_token: Token | None,
    has_equals_ahead: bool,
) -> bool:
    if prev is None:
        return False
    if curr.value == ":":
        return False
    if prev.value == ":":
        return True
    if prev.value == "-" and _is_unary_minus(prev_prev):
        return False
    if prev.value == ",":
        return True
    if curr.value == ",":
        return False
    if curr.value in {")", "]", "}"}:
        return False
    if prev.value in {")", "]", "}"} and curr.kind in {"IDENT", "KEYWORD", "INT", "STRING"}:
        return True
    if prev.value in {"(", "[", "{"}:
        return False
    if curr.kind == "KEYWORD" and prev.value not in {"(", "[", "{", "|"}:
        return True
    if curr.value == "{":
        return True
    if curr.value == "(":
        if _is_call_like(prev_prev, prev, next_token, has_equals_ahead):
            return False
        return True
    if prev.value in INLINE_OPS or curr.value in INLINE_OPS:
        return True
    if prev.value == "|" or curr.value == "|":
        return True
    if _is_word_like(prev) and _is_word_like(curr):
        return True
    return False


def _is_word_like(token: Token) -> bool:
    return token.kind in {"IDENT", "KEYWORD", "INT", "STRING"}


def _is_call_like(
    prev_prev: Token | None,
    prev: Token,
    next_token: Token | None,
    has_equals_ahead: bool,
) -> bool:
    if prev.value in {")", "]", "}"}:
        return True
    if prev.kind != "IDENT":
        return False
    if prev_prev is None:
        return True
    if prev_prev.value == "=":
        return True
    if prev_prev.kind == "KEYWORD" and prev_prev.value in {"then", "else"}:
        return True
    if prev_prev.kind == "KEYWORD" and prev_prev.value == "where":
        return False
    if prev_prev.value == "|":
        return False
    if prev_prev.value == "->":
        if not has_equals_ahead:
            return True
        return not _looks_like_type_group(prev, next_token)
    if prev_prev.kind == "KEYWORD":
        return prev_prev.value not in {"import", "class", "instance", "type"}
    if prev_prev.kind in {"IDENT", "INT", "STRING"}:
        return False
    if prev_prev.value in {")", "]", "}"}:
        return True
    if _looks_like_type_group(prev, next_token):
        return False
    if prev_prev.value in {"|", "="} | INLINE_OPS | {",", "(", "[", "{"}:
        return True
    return False


def _is_unary_minus(prev_prev: Token | None) -> bool:
    if prev_prev is None:
        return True
    if prev_prev.value in {"(", "[", "{", ",", "|"}:
        return True
    if prev_prev.value in INLINE_OPS:
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
