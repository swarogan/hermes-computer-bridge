"""JSON-safe frame transport shared by dashboard API and headless tests."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any, Iterable, Optional

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
JPEG_MAGIC = b"\xff\xd8\xff"


class InvalidFrame(ValueError):
    pass


def latest_frame(directory: Path) -> Optional[Path]:
    if not directory.is_dir():
        return None
    frames = sorted(
        (
            p
            for pattern in ("*.png", "*.jpg", "*.jpeg")
            for p in directory.glob(pattern)
            if p.stat().st_size > 0
        ),
        key=lambda p: p.stat().st_mtime_ns,
        reverse=True,
    )
    return frames[0] if frames else None


def frame_version(path: Optional[Path]) -> Optional[str]:
    if path is None:
        return None
    stat = path.stat()
    return f"{stat.st_mtime_ns:x}-{stat.st_size:x}"


def frame_summary(path: Optional[Path]) -> dict[str, Any]:
    if path is None:
        return {"frame_present": False, "frame_version": None}
    return {
        "frame_present": True,
        "frame_version": frame_version(path),
        "frame_bytes": path.stat().st_size,
        "frame_name": path.name,
        "frame_path": str(path),
    }


def frame_data(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    if raw.startswith(PNG_MAGIC):
        media_type = "image/png"
    elif raw.startswith(JPEG_MAGIC) and raw.endswith(b"\xff\xd9"):
        media_type = "image/jpeg"
    else:
        raise InvalidFrame(f"not a PNG/JPEG frame: {path.name}")
    return {
        **frame_summary(path),
        "media_type": media_type,
        "data_url": f"data:{media_type};base64," + base64.b64encode(raw).decode("ascii"),
    }
