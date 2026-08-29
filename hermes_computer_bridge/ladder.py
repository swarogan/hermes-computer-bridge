"""Independent capture and input ladders.

Question is never "which desktop is this". Question is "what can you do",
validated by trying the rung you intend to use (existence != function).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Generic, Iterable, List, Optional, Sequence, TypeVar

from hermes_computer_bridge.errors import (
    BridgeError,
    CapabilityMissing,
    TransientError,
    UserCancelled,
    should_fallback,
    should_retry,
)

T = TypeVar("T")

CAPTURE_LADDER: tuple[str, ...] = (
    "portal-screencast",
    "wlr-screencopy",
    "x11-shm",
    "remote-rfb",
)

INPUT_LADDER: tuple[str, ...] = (
    "portal-remotedesktop",
    "wlr-virtual-pointer",
    "x11-xtest",
    "remote-rfb",
)

DEFAULT_TRANSIENT_RETRIES = 3


@dataclass
class Attempt:
    rung: str
    ok: bool
    error: Optional[str] = None
    kind: Optional[str] = None  # missing | transient | cancelled | other


@dataclass
class LadderResult(Generic[T]):
    value: Optional[T] = None
    used_rung: Optional[str] = None
    attempts: List[Attempt] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.value is not None and self.used_rung is not None


def run_ladder(
    rungs: Sequence[str],
    try_rung: Callable[[str], T],
    *,
    transient_retries: int = DEFAULT_TRANSIENT_RETRIES,
    skip: Optional[Iterable[str]] = None,
) -> LadderResult[T]:
    """Walk *rungs* in order.

    CapabilityMissing -> next rung.
    TransientError -> retry this rung up to *transient_retries*, then next.
    UserCancelled -> stop (do not silently pick a weaker capture).
    """
    skipped = set(skip or ())
    result = LadderResult()
    for rung in rungs:
        if rung in skipped:
            result.attempts.append(
                Attempt(rung=rung, ok=False, error="skipped", kind="missing")
            )
            continue
        last_transient: Optional[TransientError] = None
        for _ in range(max(1, transient_retries)):
            try:
                value = try_rung(rung)
            except UserCancelled as exc:
                result.attempts.append(
                    Attempt(rung=rung, ok=False, error=str(exc), kind="cancelled")
                )
                return result
            except CapabilityMissing as exc:
                result.attempts.append(
                    Attempt(rung=rung, ok=False, error=str(exc), kind="missing")
                )
                last_transient = None
                break
            except TransientError as exc:
                last_transient = exc
                result.attempts.append(
                    Attempt(rung=rung, ok=False, error=str(exc), kind="transient")
                )
                if not should_retry(exc):
                    break
                continue
            except BridgeError as exc:
                kind = "missing" if should_fallback(exc) else "other"
                result.attempts.append(
                    Attempt(rung=rung, ok=False, error=str(exc), kind=kind)
                )
                break
            result.attempts.append(Attempt(rung=rung, ok=True))
            result.value = value
            result.used_rung = rung
            return result
        if last_transient is not None:
            # Retries exhausted: treat as missing for the purpose of walking on.
            continue
    return result


__all__ = [
    "CAPTURE_LADDER",
    "INPUT_LADDER",
    "Attempt",
    "LadderResult",
    "run_ladder",
]
