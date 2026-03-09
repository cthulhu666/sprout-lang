from __future__ import annotations

from dataclasses import dataclass


KEYWORDS = {
    "fn",
    "let",
    "type",
    "match",
    "with",
    "if",
    "then",
    "else",
    "true",
    "false",
}


MULTI_CHAR = ["->", "==", "!=", "<=", ">=", "&&", "||", ">>"]
SINGLE_CHAR = set("()|=,:+-*/<>")


@dataclass
class Token:
    kind: str
    value: str
    line: int
    column: int


class TokenizeError(ValueError):
    pass


def tokenize(source: str) -> list[Token]:
    tokens: list[Token] = []
    i = 0
    line = 1
    col = 1

    def advance(n: int = 1) -> None:
        nonlocal i, line, col
        for _ in range(n):
            if i < len(source) and source[i] == "\n":
                line += 1
                col = 1
            else:
                col += 1
            i += 1

    while i < len(source):
        ch = source[i]

        if ch in " \t\r":
            advance()
            continue

        if ch == "\n":
            advance()
            continue

        if ch == "#":
            while i < len(source) and source[i] != "\n":
                advance()
            continue

        matched = None
        for op in MULTI_CHAR:
            if source.startswith(op, i):
                matched = op
                break
        if matched:
            tokens.append(Token("SYMBOL", matched, line, col))
            advance(len(matched))
            continue

        if ch in SINGLE_CHAR:
            tokens.append(Token("SYMBOL", ch, line, col))
            advance()
            continue

        if ch.isdigit():
            start_i, start_line, start_col = i, line, col
            while i < len(source) and source[i].isdigit():
                advance()
            tokens.append(Token("INT", source[start_i:i], start_line, start_col))
            continue

        if ch == '"':
            start_line, start_col = line, col
            advance()
            chars: list[str] = []
            while i < len(source) and source[i] != '"':
                if source[i] == "\\" and i + 1 < len(source):
                    nxt = source[i + 1]
                    mapping = {"n": "\n", "t": "\t", '"': '"', "\\": "\\"}
                    chars.append(mapping.get(nxt, nxt))
                    advance(2)
                else:
                    chars.append(source[i])
                    advance()
            if i >= len(source):
                raise TokenizeError(f"Unterminated string at {start_line}:{start_col}")
            advance()
            tokens.append(Token("STRING", "".join(chars), start_line, start_col))
            continue

        if ch.isalpha() or ch == "_":
            start_i, start_line, start_col = i, line, col
            while i < len(source) and (source[i].isalnum() or source[i] == "_"):
                advance()
            ident = source[start_i:i]
            if ident in KEYWORDS:
                tokens.append(Token("KEYWORD", ident, start_line, start_col))
            else:
                tokens.append(Token("IDENT", ident, start_line, start_col))
            continue

        raise TokenizeError(f"Unexpected character {ch!r} at {line}:{col}")

    tokens.append(Token("EOF", "", line, col))
    return tokens
