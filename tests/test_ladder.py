from hermes_computer_bridge.errors import (
    CapabilityMissing,
    TransientError,
    UserCancelled,
    should_fallback,
    should_retry,
)
from hermes_computer_bridge.ladder import (
    CAPTURE_LADDER,
    INPUT_LADDER,
    run_ladder,
)


def test_capture_ladder_order():
    assert CAPTURE_LADDER[0] == "portal-screencast"
    assert CAPTURE_LADDER == (
        "portal-screencast",
        "wlr-screencopy",
        "x11-shm",
        "remote-rfb",
    )


def test_input_ladder_order():
    assert INPUT_LADDER[0] == "portal-remotedesktop"
    assert INPUT_LADDER[-1] == "remote-rfb"
    assert "orgo" not in INPUT_LADDER


def test_missing_falls_back_transient_retries():
    calls = []

    def try_rung(rung: str):
        calls.append(rung)
        if rung == "portal-screencast":
            raise CapabilityMissing(rung, "no portal")
        if rung == "wlr-screencopy":
            if calls.count("wlr-screencopy") < 3:
                raise TransientError(rung, "busy")
            return b"frame"
        raise CapabilityMissing(rung, "no")

    result = run_ladder(CAPTURE_LADDER, try_rung, transient_retries=3)
    assert result.ok
    assert result.used_rung == "wlr-screencopy"
    assert result.value == b"frame"
    assert calls.count("portal-screencast") == 1
    assert calls.count("wlr-screencopy") == 3
    kinds = [a.kind for a in result.attempts if a.rung == "portal-screencast"]
    assert kinds == ["missing"]


def test_user_cancel_does_not_fall_back_to_x11():
    def try_rung(rung: str):
        if rung == "portal-screencast":
            raise UserCancelled("dismissed")
        return b"should-not-run"

    result = run_ladder(CAPTURE_LADDER, try_rung)
    assert not result.ok
    assert result.used_rung is None
    assert result.attempts[-1].kind == "cancelled"
    assert all(a.rung != "x11-shm" or not a.ok for a in result.attempts)


def test_exhausted_transient_walks_on():
    def try_rung(rung: str):
        if rung == "portal-screencast":
            raise TransientError(rung, "timeout")
        if rung == "wlr-screencopy":
            raise CapabilityMissing(rung, "no wlr")
        if rung == "x11-shm":
            return b"x11"

    result = run_ladder(CAPTURE_LADDER, try_rung, transient_retries=2)
    assert result.ok
    assert result.used_rung == "x11-shm"
    transients = [a for a in result.attempts if a.kind == "transient"]
    assert len(transients) == 2


def test_should_helpers():
    assert should_fallback(CapabilityMissing("portal"))
    assert not should_retry(CapabilityMissing("portal"))
    assert should_retry(TransientError("portal", "x"))
    assert not should_fallback(TransientError("portal", "x"))
