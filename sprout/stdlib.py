from __future__ import annotations

from pathlib import Path

from .module_loader import _extract_decl_and_export_names, parse_header


def stdlib_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "stdlib"


def prelude_path() -> Path:
    return stdlib_dir() / "prelude.sprout"

def crypto_path() -> Path:
    return stdlib_dir() / "crypto.sprout"


def load_prelude() -> str:
    path = prelude_path()
    header = parse_header(path.read_text(encoding="utf-8"), path)
    _, _, _, _, body = _extract_decl_and_export_names(
        header.body, header.body_line_numbers
    )
    return body


def load_crypto() -> str:
    path = crypto_path()
    header = parse_header(path.read_text(encoding="utf-8"), path)
    _, _, _, _, body = _extract_decl_and_export_names(
        header.body, header.body_line_numbers
    )
    return body


def with_prelude(user_source: str) -> str:
    return f"{load_prelude()}\n\n{user_source}"
