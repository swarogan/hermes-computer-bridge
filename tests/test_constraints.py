"""Constraints that do not need a graphical session."""

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent.parent
JS = (ROOT / "desktop" / "plugin.js").read_text(encoding="utf-8")
HELPER = ROOT / "helpers" / "portal_screencast.py"


def test_js_only_allowed_imports():
    imports = re.findall(r"^import\s+.+?\s+from\s+['\"]([^'\"]+)['\"]", JS, re.M)
    allowed = {"@hermes/plugin-sdk", "react", "react/jsx-runtime"}
    assert set(imports) <= allowed, imports


def test_js_no_hardcoded_colors():
    assert not re.search(r"#[0-9a-fA-F]{3,8}", JS)
    # Theme vars only — reject rgb() / hex, not English words in comments.
    assert "rgb(" not in JS.lower()
    assert "rgba(" not in JS.lower()


def test_js_has_resize_observer_and_canvas_attrs():
    assert "ResizeObserver" in JS
    assert "canvas.width" in JS
    assert "canvas.height" in JS


def test_js_has_socket_and_polling_fallback():
    assert ".socket(" in JS
    assert "refetchInterval" in JS
    assert "invalidateQueries" in JS


def test_js_uses_json_frame_transport_not_binary_rest():
    assert "/frame-data" in JS
    assert "data_url" in JS
    assert "new Blob" not in JS
    assert "URL.createObjectURL" not in JS
    # Large pixels are keyed by cheap /status version, not re-fetched every tick.
    assert "frame_version" in JS
    assert "staleTime: Infinity" in JS


def test_js_no_jsx_syntax():
    assert "<div" not in JS
    assert "<canvas" not in JS
    assert "jsx(" in JS


def test_helper_shebang_is_system_python():
    first = HELPER.read_text(encoding="utf-8").splitlines()[0]
    assert first == "#!/usr/bin/python3"


def test_no_novnc_or_websockify_or_cdn():
    blob = JS + (ROOT / "README.md").read_text(encoding="utf-8")
    # README may mention them as anti-patterns; plugin.js must not.
    assert "novnc" not in JS.lower()
    assert "websockify" not in JS.lower()
    assert "+esm" not in JS
    assert "cdn" not in JS.lower()


def test_restore_token_not_in_repo():
    state = ROOT / "state"
    assert not (ROOT / "screencast-restore-token").exists()
    if state.exists():
        assert not any(state.rglob("*token*"))
