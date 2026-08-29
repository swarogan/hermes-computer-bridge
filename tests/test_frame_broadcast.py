"""Socket fan-out contract.

The fan-out lives in the package rather than in dashboard/plugin_api.py so it
can be tested without FastAPI (which the system interpreter does not have).
Subscribers are duck-typed on `await send_json(dict)` — a real WebSocket
satisfies that, and so does the fake here.
"""

from __future__ import annotations

import asyncio

from hermes_computer_bridge.broadcast import FrameBroadcaster, frame_message, try_frame_message


class FakeSocket:
    def __init__(self, fail: bool = False) -> None:
        self.sent: list[dict] = []
        self.fail = fail

    async def send_json(self, message: dict) -> None:
        if self.fail:
            raise ConnectionError("socket went away")
        self.sent.append(message)


def test_every_subscriber_receives_the_frame_message():
    caster = FrameBroadcaster()
    a, b = FakeSocket(), FakeSocket()
    caster.add(a)
    caster.add(b)

    delivered = asyncio.run(caster.send({"type": "frame", "frame_version": "v1"}))

    assert delivered == 2
    assert a.sent == b.sent == [{"type": "frame", "frame_version": "v1"}]


def test_a_dead_socket_is_dropped_instead_of_breaking_the_others():
    caster = FrameBroadcaster()
    dead, alive = FakeSocket(fail=True), FakeSocket()
    caster.add(dead)
    caster.add(alive)

    delivered = asyncio.run(caster.send({"type": "frame"}))

    assert delivered == 1
    assert alive.sent == [{"type": "frame"}]
    assert caster.count() == 1, "a socket that raised must not be retried forever"


def test_broadcasting_with_no_subscribers_is_harmless():
    """ctx.socket is a no-op on OAuth remotes: nobody may ever subscribe."""
    assert asyncio.run(FrameBroadcaster().send({"type": "frame"})) == 0


def test_discard_is_idempotent():
    caster = FrameBroadcaster()
    socket = FakeSocket()
    caster.add(socket)

    caster.discard(socket)
    caster.discard(socket)

    assert caster.count() == 0


def test_frame_message_carries_the_pixels_not_only_a_change_notification(tmp_path):
    """The socket is the live transport, not a hint to begin two REST calls."""
    jpeg = tmp_path / "frame.jpg"
    jpeg.write_bytes(b"\xff\xd8\xffpayload\xff\xd9")

    message = frame_message(jpeg)

    assert message["type"] == "frame"
    assert message["frame_version"]
    assert message["data_url"].startswith("data:image/jpeg;base64,")


def test_transient_invalid_frame_is_skipped_without_killing_the_broadcast_loop(tmp_path):
    jpeg = tmp_path / "frame.jpg"
    jpeg.write_bytes(b"\xff\xd8\xffincomplete")
    assert try_frame_message(jpeg) is None

    jpeg.write_bytes(b"\xff\xd8\xffcomplete\xff\xd9")
    assert try_frame_message(jpeg)["type"] == "frame"
