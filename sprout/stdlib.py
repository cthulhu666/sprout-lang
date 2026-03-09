from __future__ import annotations

from pathlib import Path


def prelude_path() -> Path:
    return Path(__file__).resolve().parent.parent / "stdlib" / "prelude.sprout"


def load_prelude() -> str:
    return prelude_path().read_text(encoding="utf-8")


def with_prelude(user_source: str) -> str:
    return f"{load_prelude()}\n\n{user_source}"
