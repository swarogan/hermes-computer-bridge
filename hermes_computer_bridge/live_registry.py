from __future__ import annotations

from typing import Any, Optional

_current: Optional[Any] = None


def set_current(stream: Optional[Any]) -> None:
    global _current
    _current = stream


def get_current() -> Optional[Any]:
    return _current


__all__ = ["set_current", "get_current"]
