from __future__ import annotations

from typing import Any, Optional

_current: Optional[Any] = None
_agent_target: Optional[str] = None
_agent_seq: int = 0


def set_current(stream: Optional[Any]) -> None:
    global _current
    _current = stream


def get_current() -> Optional[Any]:
    return _current


def set_agent_target(target: str) -> None:
    global _agent_target, _agent_seq
    _agent_target = target
    _agent_seq += 1


def agent_activity() -> dict[str, Any]:
    return {"target": _agent_target, "seq": _agent_seq}


__all__ = ["set_current", "get_current", "set_agent_target", "agent_activity"]
