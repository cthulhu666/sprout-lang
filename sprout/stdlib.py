from __future__ import annotations

from pathlib import Path


def prelude_path() -> Path:
    return Path(__file__).resolve().parent.parent / "stdlib" / "prelude.sprout"


def http_path() -> Path:
    return Path(__file__).resolve().parent.parent / "stdlib" / "http.sprout"


def load_prelude() -> str:
    return prelude_path().read_text(encoding="utf-8")


def load_http() -> str:
    return http_path().read_text(encoding="utf-8")


def with_prelude(user_source: str) -> str:
    return f"{load_prelude()}\n\n{user_source}"


def with_http_prelude(user_source: str) -> str:
    return f"{load_http()}\n\n{user_source}"
