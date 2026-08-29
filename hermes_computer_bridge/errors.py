"""Error classes for the capability ladder.

A missing capability is not a retryable failure. A transient error is not a
reason to drop to the next rung. Mixing those two is how capture stacks
become an `if desktop == ...` tree.
"""

from __future__ import annotations


class BridgeError(Exception):
    """Base error for hermes-computer-bridge."""


class CapabilityMissing(BridgeError):
    """This rung does not exist or was refused as a capability.

    Trigger fallback to the next rung. Do not retry.
    """

    def __init__(self, rung: str, detail: str = ""):
        self.rung = rung
        self.detail = detail
        super().__init__(f"capability missing on {rung}: {detail}".strip())


class TransientError(BridgeError):
    """The rung exists but this attempt failed in a way that may succeed next.

    Retry bounded times. Do not fall back until retries are exhausted.
    """

    def __init__(self, rung: str, detail: str = ""):
        self.rung = rung
        self.detail = detail
        super().__init__(f"transient error on {rung}: {detail}".strip())


class UserCancelled(BridgeError):
    """The user dismissed the portal consent dialog.

    Not missing, not transient. Surface it; do not silently fall back to
    X11 (that would surprise a Wayland user who said no).
    """

    def __init__(self, detail: str = "portal consent dismissed"):
        super().__init__(detail)


def should_fallback(exc: BaseException) -> bool:
    return isinstance(exc, CapabilityMissing)


def should_retry(exc: BaseException) -> bool:
    return isinstance(exc, TransientError)
