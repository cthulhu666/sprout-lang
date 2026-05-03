from __future__ import annotations

from dataclasses import dataclass


KEYWORDS = {
    "export",
    "fn",
    "let",
    "type",
    "class",
    "instance",
    "where",
    "match",
    "with",
    "do",
    "if",
    "then",
    "else",
    "true",
    "false",
}


MULTI_CHAR = ["->", "<-", "==", "!=", "<=", ">=", "&&", "||", ">>", "<<", "|>", "++", ".."]
SINGLE_CHAR = set("(){}[]|=,:+-*/<>\\!")

# Template literal token kinds.
TEMPLATE_START = "TEMPLATE_START"          # opening backtick
TEMPLATE_LIT = "TEMPLATE_LIT"             # decoded literal text run inside a template
TEMPLATE_INTERP_START = "TEMPLATE_INTERP_START"  # ${ opening an interpolation slot
TEMPLATE_INTERP_END = "TEMPLATE_INTERP_END"      # } closing an interpolation slot
TEMPLATE_END = "TEMPLATE_END"             # closing backtick


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

    def scan_template() -> None:
        """Scan a backtick template literal starting at the current backtick.

        Emits TEMPLATE_START, zero or more TEMPLATE_LIT / TEMPLATE_INTERP_START /
        (recursively scanned nested template tokens) / TEMPLATE_INTERP_END tokens,
        and a final TEMPLATE_END.  ``i``, ``line``, ``col`` are updated in place via
        the outer ``advance()`` closure.
        """
        nonlocal i, line, col
        start_line, start_col = line, col
        advance()  # consume opening backtick
        tokens.append(Token(TEMPLATE_START, "", start_line, start_col))

        buf: list[str] = []
        buf_line: int = line
        buf_col: int = col

        def flush_buf() -> None:
            nonlocal buf_line, buf_col
            if buf:
                tokens.append(Token(TEMPLATE_LIT, "".join(buf), buf_line, buf_col))
                buf.clear()

        def buf_append(ch: str) -> None:
            nonlocal buf_line, buf_col
            if not buf:
                buf_line, buf_col = line, col
            buf.append(ch)

        while i < len(source):
            ch = source[i]

            if ch == "`":
                # Closing backtick — flush any buffered literal and emit TEMPLATE_END.
                flush_buf()
                end_line, end_col = line, col
                advance()  # consume closing backtick
                tokens.append(Token(TEMPLATE_END, "", end_line, end_col))
                return

            if ch == "\\" and i + 1 < len(source):
                nxt = source[i + 1]
                if nxt == "`":
                    buf_append("`")
                    advance(2)
                elif nxt == "$" and i + 2 < len(source) and source[i + 2] == "{":
                    buf_append("$")
                    buf_append("{")
                    advance(3)
                elif nxt == "n":
                    buf_append("\n")
                    advance(2)
                elif nxt == "t":
                    buf_append("\t")
                    advance(2)
                elif nxt == "\\":
                    buf_append("\\")
                    advance(2)
                else:
                    # Lenient: pass backslash + next char through literally.
                    buf_append("\\")
                    buf_append(nxt)
                    advance(2)
                continue

            if ch == "$" and i + 1 < len(source) and source[i + 1] == "{":
                # Start of an interpolation slot.
                flush_buf()
                interp_line, interp_col = line, col
                advance(2)  # consume ${
                tokens.append(Token(TEMPLATE_INTERP_START, "", interp_line, interp_col))
                # Tokenize the expression inside the slot.  We track brace depth
                # and emit normal tokens until the matching } at depth 0.
                brace_depth = 0
                while i < len(source):
                    ech = source[i]

                    if ech in " \t\r":
                        advance()
                        continue
                    if ech == "\n":
                        advance()
                        continue
                    if ech == "#":
                        while i < len(source) and source[i] != "\n":
                            advance()
                        continue

                    if ech == "`":
                        # Nested template — recurse.
                        scan_template()
                        continue

                    if ech == "{":
                        brace_depth += 1
                        tokens.append(Token("SYMBOL", "{", line, col))
                        advance()
                        continue

                    if ech == "}":
                        if brace_depth == 0:
                            # This } closes the interpolation slot.
                            close_line, close_col = line, col
                            advance()
                            tokens.append(Token(TEMPLATE_INTERP_END, "", close_line, close_col))
                            break
                        brace_depth -= 1
                        tokens.append(Token("SYMBOL", "}", line, col))
                        advance()
                        continue

                    # Normal tokenizing inside the interp slot.
                    matched_op = None
                    for op in MULTI_CHAR:
                        if source.startswith(op, i):
                            matched_op = op
                            break
                    if matched_op:
                        tokens.append(Token("SYMBOL", matched_op, line, col))
                        advance(len(matched_op))
                        continue

                    if ech in SINGLE_CHAR:
                        tokens.append(Token("SYMBOL", ech, line, col))
                        advance()
                        continue

                    if ech.isdigit():
                        start_i_inner, sl, sc = i, line, col
                        while i < len(source) and source[i].isdigit():
                            advance()
                        tokens.append(Token("INT", source[start_i_inner:i], sl, sc))
                        continue

                    if ech == "'":
                        sl, sc = line, col
                        advance()
                        chars: list[str] = []
                        while i < len(source) and source[i] != "'":
                            if source[i] == "\\" and i + 1 < len(source):
                                nxt2 = source[i + 1]
                                mapping = {"n": "\n", "r": "\r", "t": "\t", '"': '"', "'": "'", "\\": "\\"}
                                chars.append(mapping.get(nxt2, nxt2))
                                advance(2)
                            else:
                                chars.append(source[i])
                                advance()
                        if i >= len(source):
                            raise TokenizeError(f"Unterminated char literal at {sl}:{sc}")
                        advance()
                        cv = "".join(chars)
                        if len(cv) != 1:
                            raise TokenizeError(f"Char literal must contain exactly one code point at {sl}:{sc}")
                        tokens.append(Token("CHAR", cv, sl, sc))
                        continue

                    if ech == '"':
                        sl, sc = line, col
                        advance()
                        chars: list[str] = []
                        while i < len(source) and source[i] != '"':
                            if source[i] == "\\" and i + 1 < len(source):
                                nxt2 = source[i + 1]
                                mapping = {"n": "\n", "r": "\r", "t": "\t", '"': '"', "\\": "\\"}
                                chars.append(mapping.get(nxt2, nxt2))
                                advance(2)
                            else:
                                chars.append(source[i])
                                advance()
                        if i >= len(source):
                            raise TokenizeError(f"Unterminated string at {sl}:{sc}")
                        advance()
                        tokens.append(Token("STRING", "".join(chars), sl, sc))
                        continue

                    if ech.isalpha() or ech == "_":
                        start_i_inner, sl, sc = i, line, col
                        while i < len(source) and (source[i].isalnum() or source[i] == "_" or source[i] == "."):
                            advance()
                        ident = source[start_i_inner:i]
                        if ident in KEYWORDS:
                            tokens.append(Token("KEYWORD", ident, sl, sc))
                        else:
                            tokens.append(Token("IDENT", ident, sl, sc))
                        continue

                    raise TokenizeError(f"Unexpected character {ech!r} at {line}:{col}")
                # end interp-slot loop
                continue
            # end interpolation branch

            # Ordinary template character — append to buffer.
            buf_append(ch)
            advance()

        raise TokenizeError(f"Unterminated template literal at {start_line}:{start_col}")

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

        if ch == "'":
            start_line, start_col = line, col
            advance()
            chars: list[str] = []
            while i < len(source) and source[i] != "'":
                if source[i] == "\\" and i + 1 < len(source):
                    nxt = source[i + 1]
                    mapping = {"n": "\n", "r": "\r", "t": "\t", '"': '"', "'": "'", "\\": "\\"}
                    if nxt == "0":
                        raise TokenizeError(f"\\0 escape is not supported in char literals at {start_line}:{start_col}")
                    chars.append(mapping.get(nxt, nxt))
                    advance(2)
                else:
                    chars.append(source[i])
                    advance()
            if i >= len(source):
                raise TokenizeError(f"Unterminated char literal at {start_line}:{start_col}")
            advance()
            value = "".join(chars)
            if len(value) != 1:
                raise TokenizeError(f"Char literal must contain exactly one code point at {start_line}:{start_col}")
            tokens.append(Token("CHAR", value, start_line, start_col))
            continue

        if ch == '"':
            start_line, start_col = line, col
            advance()
            chars: list[str] = []
            while i < len(source) and source[i] != '"':
                if source[i] == "\\" and i + 1 < len(source):
                    nxt = source[i + 1]
                    mapping = {"n": "\n", "r": "\r", "t": "\t", '"': '"', "\\": "\\"}
                    if nxt == "0":
                        raise TokenizeError(f"\\0 escape is not supported in string literals at {start_line}:{start_col}")
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
            while i < len(source) and (source[i].isalnum() or source[i] == "_" or source[i] == "."):
                advance()
            ident = source[start_i:i]
            if ident in KEYWORDS:
                tokens.append(Token("KEYWORD", ident, start_line, start_col))
            else:
                tokens.append(Token("IDENT", ident, start_line, start_col))
            continue

        if ch == "`":
            scan_template()
            continue

        raise TokenizeError(f"Unexpected character {ch!r} at {line}:{col}")

    tokens.append(Token("EOF", "", line, col))
    return tokens
