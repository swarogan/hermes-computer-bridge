"""hermes-computer-bridge — capability-ladder desktop capture/input."""

from hermes_computer_bridge.capture_service import (
    CaptureService,
    NotImplementedRung,
    default_providers,
    read_outputs,
)
from hermes_computer_bridge.errors import (
    BridgeError,
    CapabilityMissing,
    TransientError,
    UserCancelled,
)
from hermes_computer_bridge.geometry import (
    Output,
    frame_to_logical,
    parse_kscreen_outputs,
    resolve_origin,
)
from hermes_computer_bridge.ladder import CAPTURE_LADDER, INPUT_LADDER, run_ladder
from hermes_computer_bridge.portal_capture import PortalCapture
from hermes_computer_bridge.provider import (
    CaptureProvider,
    Frame,
    InputProvider,
    StreamInfo,
)

__all__ = [
    "BridgeError",
    "CAPTURE_LADDER",
    "CaptureProvider",
    "CaptureService",
    "CapabilityMissing",
    "Frame",
    "INPUT_LADDER",
    "InputProvider",
    "NotImplementedRung",
    "Output",
    "PortalCapture",
    "StreamInfo",
    "TransientError",
    "UserCancelled",
    "default_providers",
    "frame_to_logical",
    "parse_kscreen_outputs",
    "read_outputs",
    "resolve_origin",
    "run_ladder",
]
