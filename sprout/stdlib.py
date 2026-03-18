from __future__ import annotations

from pathlib import Path

from .module_loader import _extract_decl_and_export_names, parse_header


def stdlib_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "stdlib"


def prelude_path() -> Path:
    return stdlib_dir() / "prelude.sprout"


def http_path() -> Path:
    return stdlib_dir() / "http.sprout"


def json_path() -> Path:
    return stdlib_dir() / "json.sprout"


def crypto_path() -> Path:
    return stdlib_dir() / "crypto.sprout"


def load_prelude() -> str:
    path = prelude_path()
    header = parse_header(path.read_text(encoding="utf-8"), path)
    _, _, _, body = _extract_decl_and_export_names(header.body)
    return body


def load_http() -> str:
    path = http_path()
    header = parse_header(path.read_text(encoding="utf-8"), path)
    _, _, _, body = _extract_decl_and_export_names(header.body)
    return body


def load_json() -> str:
    path = json_path()
    header = parse_header(path.read_text(encoding="utf-8"), path)
    _, _, _, body = _extract_decl_and_export_names(header.body)
    return body


def load_crypto() -> str:
    path = crypto_path()
    header = parse_header(path.read_text(encoding="utf-8"), path)
    _, _, _, body = _extract_decl_and_export_names(header.body)
    return body


def with_prelude(user_source: str) -> str:
    return f"{load_prelude()}\n\n{user_source}"


def with_http_prelude(user_source: str) -> str:
    return f"{load_prelude()}\n\n{load_json()}\n\n{load_http()}\n\n{user_source}"
