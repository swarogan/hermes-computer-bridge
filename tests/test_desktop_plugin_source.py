"""Source-level contract for desktop/plugin.js.

The renderer cannot be unit-tested without Electron, so these assert the
invariants that actually break in production: loader-rejected imports, JSX
that will not parse, canvas sizing, aspect ratio, and the socket/polling
pair. They are cheap and they catch the failures the loader reports as an
opaque toast.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = (ROOT / "desktop" / "plugin.js").read_text(encoding="utf-8")


def test_only_resolvable_imports():
    specs = re.findall(r"^import\s+.+?\s+from\s+['\"]([^'\"]+)['\"]", JS, re.M)
    assert set(specs) <= {"@hermes/plugin-sdk", "react", "react/jsx-runtime"}, specs
    assert specs, "plugin must import the SDK"


def test_no_jsx_syntax_because_file_is_not_compiled():
    assert not re.search(r"<[A-Za-z][A-Za-z0-9]*[\s/>]", JS), "JSX will not parse"
    assert "jsx(" in JS


def test_no_hardcoded_colors_only_theme_vars():
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", JS)
    assert "rgb(" not in JS.lower()
    assert "rgba(" not in JS.lower()
    assert "var(--ui-" in JS


def test_canvas_tracks_container_and_sets_attributes():
    assert "ResizeObserver" in JS
    # CSS-only sizing leaves a blurry/stale canvas — attributes are required.
    assert re.search(r"canvas\.width\s*=", JS)
    assert re.search(r"canvas\.height\s*=", JS)
    assert "devicePixelRatio" in JS


def test_aspect_ratio_preserved_no_cropping():
    """`contain`, not `cover`: cropping would hide the dead band.

    Math.min over both axis scales is the letterbox fit. Math.max on that same
    pair would crop — assert on the scale expression itself, not on the whole
    file (Math.max is legitimate elsewhere, e.g. clamping canvas size to >= 1).
    """
    scale = re.search(r"const scale = ([^\n]+)", JS)
    assert scale, "frame scale computation not found"
    assert "Math.min(" in scale.group(1)
    assert "Math.max(" not in scale.group(1)


def test_socket_preferred_with_mandatory_polling_fallback():
    assert ".socket(" in JS, "must prefer the live socket"
    assert "refetchInterval" in JS, "socket is a no-op on OAuth remotes"
    assert "useQuery" in JS, "polling must use React Query, not a hand-rolled loop"
    assert "setInterval" not in JS, "hand-rolled poll loop is forbidden by the SDK"


def test_no_forbidden_transports():
    lowered = JS.lower()
    for banned in ("novnc", "websockify", "cdn", "unpkg", "jsdelivr", "+esm"):
        assert banned not in lowered, banned


def test_input_is_wired_through_the_single_endpoint():
    assert "'/input'" in JS
    assert "op: 'move'" in JS
    assert "op: 'button'" in JS
    assert "op: 'scroll'" in JS
    for granular in ("/click", "/type", "/key", "RemoteDesktop", "NotifyPointer"):
        assert granular not in JS, f"input goes through /input, not {granular}"


def test_error_and_empty_states_are_rendered():
    assert "ErrorState" in JS
    assert "EmptyState" in JS


def test_every_referenced_sdk_identifier_is_imported():
    """A forgotten import is a ReferenceError at render, not at load."""
    line = re.search(r"^import \{([^}]+)\} from '@hermes/plugin-sdk'", JS, re.M)
    assert line
    imported = {n.strip() for n in line.group(1).split(",") if n.strip()}
    for name in ("Button", "EmptyState", "ErrorState", "useQuery", "useMutation"):
        if f"{name}," in JS or f"{name})" in JS or f"jsx({name}" in JS:
            assert name in imported, f"{name} used but not imported"
