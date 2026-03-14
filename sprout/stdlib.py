from __future__ import annotations

from pathlib import Path

from .module_loader import parse_header


def prelude_path() -> Path:
    return Path(__file__).resolve().parent.parent / "stdlib" / "prelude.sprout"


def http_path() -> Path:
    return Path(__file__).resolve().parent.parent / "stdlib" / "http.sprout"


def load_prelude() -> str:
    path = prelude_path()
    return parse_header(path.read_text(encoding="utf-8"), path).body


def load_http() -> str:
    path = http_path()
    return parse_header(path.read_text(encoding="utf-8"), path).body


def with_prelude(user_source: str) -> str:
    return f"{load_prelude()}\n\n{user_source}"


def with_http_prelude(user_source: str) -> str:
    return f"{load_http()}\n\n{user_source}"
