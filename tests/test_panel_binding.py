"""The agent follows the panel, and looks at the frame the panel already made.

Two behaviours, both driven by real failures:

* The toolset is DEFERRED, so a model that is not told a remote machine exists
  never goes looking for one. Measured: it reached for `computer_use` (which
  fails on Wayland here), then for SSH, virsh and looking-glass, and never
  touched this plugin. `_panel_context` is the per-turn line that fixes that.
* A screenshot of a VM cost a full RFB framebuffer transfer — 0.5-1.7s per
  look, several looks per task — while the panel's stream had just written the
  same pixels to disk.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def plugin(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location("cb_panel_under_test", ROOT / "__init__.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "LIVE_STREAM_FRAME", tmp_path / "live-frame.jpg")
    # Never read the machine's real bindings.json: these tests decide for
    # themselves what the human picked.
    monkeypatch.setattr(module, "_bindings_file", lambda: tmp_path / "bindings.json")
    monkeypatch.setattr(module, "_profile_name", lambda: "tom")  # overridable per test
    return module


def _stream(plugin, target, *, frame_written=True, age_s=0.0):
    """Put the registry and the frame file into a known state."""
    plugin.live_registry.set_panel_target(target)
    plugin.live_registry.set_current(object() if target else None)
    plugin._bindings_file().write_text(json.dumps({"tom": target} if target else {}))
    if frame_written:
        plugin.LIVE_STREAM_FRAME.write_bytes(b"jpeg")
    return 1_000_000.0 + age_s if frame_written else None


# --- _stream_frame_for --------------------------------------------------------


def test_uses_the_stream_frame_when_the_panel_watches_that_target(plugin):
    _stream(plugin, "vm:113")
    mtime = plugin.LIVE_STREAM_FRAME.stat().st_mtime
    found = plugin._stream_frame_for("vm:113", now=mtime + 0.1)
    assert found is not None
    path, age = found
    assert path == plugin.LIVE_STREAM_FRAME
    assert age == pytest.approx(0.1, abs=0.01)


def test_ignores_the_stream_frame_when_the_panel_watches_another_target(plugin):
    """The frame on disk shows vm:113; an agent asking about vm:107 must not get it."""
    _stream(plugin, "vm:113")
    mtime = plugin.LIVE_STREAM_FRAME.stat().st_mtime
    assert plugin._stream_frame_for("vm:107", now=mtime) is None


def test_ignores_a_stale_frame(plugin):
    """A frame older than the cap cannot answer "what is on screen now"."""
    _stream(plugin, "vm:113")
    mtime = plugin.LIVE_STREAM_FRAME.stat().st_mtime
    assert plugin._stream_frame_for("vm:113", now=mtime + 60) is None


def test_ignores_the_frame_once_the_stream_stops_writing(plugin):
    """Switching the panel off is observable only as a frame that stops ageing.

    The file is written by the process hosting the panel, so a bot's own
    process cannot ask whether that stream is alive — staleness is the signal.
    """
    _stream(plugin, "vm:113")
    mtime = plugin.LIVE_STREAM_FRAME.stat().st_mtime
    assert plugin._stream_frame_for("vm:113", now=mtime + 0.5) is not None
    assert plugin._stream_frame_for("vm:113", now=mtime + 5) is None


# --- _capture -----------------------------------------------------------------


def test_capture_serves_the_stream_frame_without_touching_the_vm(plugin, monkeypatch):
    _stream(plugin, "vm:113")

    def explode():
        raise AssertionError("must not open an RFB session when a fresh frame exists")

    monkeypatch.setattr(plugin, "_vnc", explode)
    body = json.loads(plugin._capture({"target": "vm:113"}))
    assert body["ok"] is True
    assert body["source"] == "live-stream"
    assert body["output"] == str(plugin.LIVE_STREAM_FRAME)
    assert body["view_with"] == "vision_analyze"
    assert body["frame_age_ms"] >= 0


def test_capture_falls_back_to_a_real_grab_for_an_unwatched_target(plugin, monkeypatch):
    _stream(plugin, "vm:113")
    calls = []

    class FakeVnc:
        def screenshot(self, info):
            calls.append(info["id"])
            return b"jpeg-bytes"

        def dimensions(self, info):
            return (1280, 800)

    monkeypatch.setattr(plugin, "_vnc", lambda: FakeVnc())
    monkeypatch.setattr(plugin, "EVIDENCE_DIR", plugin.LIVE_STREAM_FRAME.parent)
    body = json.loads(plugin._capture({"target": "vm:107"}))
    assert calls == ["vm:107"], "an unwatched target must be captured for real"
    assert body["source"] == "capture"
    assert body["view_with"] == "vision_analyze"


def test_an_explicit_output_path_still_writes_a_file(plugin, monkeypatch):
    """Asking for a named file means the caller wants that file on disk."""
    _stream(plugin, "vm:113")

    class FakeVnc:
        def screenshot(self, info):
            return b"jpeg-bytes"

        def dimensions(self, info):
            return (1280, 800)

    monkeypatch.setattr(plugin, "_vnc", lambda: FakeVnc())
    monkeypatch.setattr(plugin, "EVIDENCE_DIR", plugin.LIVE_STREAM_FRAME.parent)
    body = json.loads(plugin._capture({"target": "vm:113", "output": "asked-for.jpg"}))
    assert body["source"] == "capture"
    assert Path(body["output"]).name == "asked-for.jpg"
    assert Path(body["output"]).read_bytes() == b"jpeg-bytes"


# --- _panel_context -----------------------------------------------------------


def test_no_context_when_the_panel_has_selected_nothing(plugin):
    """Nothing selected means nothing to say — costing zero tokens per turn."""
    _stream(plugin, None, frame_written=False)
    assert plugin._panel_context() is None


def test_context_names_the_machine_and_the_frame_path(plugin):
    _stream(plugin, "vm:113")
    line = plugin._panel_context()
    assert "vm:113" in line
    assert str(plugin.LIVE_STREAM_FRAME) in line
    assert "vision_analyze" in line
    assert "computer_bridge_" in line


def test_context_still_names_the_machine_without_a_live_frame(plugin):
    """Knowing the machine is reachable matters even before the stream is up."""
    _stream(plugin, "vm:113", frame_written=False)
    line = plugin._panel_context()
    assert "vm:113" in line
    assert "vision_analyze" not in line, "no frame on disk, so do not promise one"


def test_context_carries_no_image_data(plugin):
    """The point is a pointer, not pixels: an inlined frame is ~200k characters."""
    _stream(plugin, "vm:113")
    line = plugin._panel_context()
    assert "base64" not in line
    assert len(line) < 500


# --- hook registration --------------------------------------------------------


def test_plugin_registers_the_pre_llm_call_hook(plugin):
    hooks = {}

    class Ctx:
        def register_hook(self, name, callback):
            hooks[name] = callback

        def __getattr__(self, item):
            return lambda *a, **k: None

    plugin.register(Ctx())
    assert "pre_llm_call" in hooks

    _stream(plugin, "vm:113")
    assert hooks["pre_llm_call"](session_id="s1")["context"] == plugin._panel_context()

    _stream(plugin, None, frame_written=False)
    assert hooks["pre_llm_call"](session_id="s1") is None


def test_manifest_declares_the_hook():
    import yaml

    manifest = yaml.safe_load((ROOT / "plugin.yaml").read_text(encoding="utf-8"))
    assert "pre_llm_call" in manifest.get("provides_hooks", [])


def test_context_uses_the_name_the_panel_already_fetched(plugin, monkeypatch):
    """The human says "omarchy"; 'vm:113' alone leaves the model to connect them.

    The label comes from the dropdown's own listing, so this costs a dict
    lookup rather than a Proxmox round trip on every turn.
    """
    _stream(plugin, "vm:113")
    monkeypatch.setattr(plugin, "label_for", lambda tid: "VM 113 (omarchy)")
    line = plugin._panel_context()
    assert "omarchy" in line
    assert "vm:113" in line, "the id must survive — it is what 'target' takes"


def test_context_falls_back_to_the_id_when_no_label_was_seen(plugin, monkeypatch):
    _stream(plugin, "vm:113")
    monkeypatch.setattr(plugin, "label_for", lambda tid: None)
    assert "vm:113" in plugin._panel_context()


def test_listing_targets_records_labels_for_later_lookup():
    from hermes_computer_bridge import targets as targets_mod

    targets_mod._LABELS.clear()
    listed = targets_mod.list_targets(
        env={},
        client_factory=lambda cfg: None,
        path=Path("/nonexistent"),
        vnc_path=Path("/nonexistent"),
    )
    assert {t["id"] for t in listed} == {"local"}
    assert targets_mod.label_for("local") == "Local desktop"


# --- the cross-process bug this file exists for -------------------------------


def test_a_bot_process_reads_the_shared_binding_not_its_empty_registry(plugin, monkeypatch):
    """The bug: `tom` fell back to `local` while the human watched vm:113.

    `live_registry` is per-process state. The panel runs against the default
    profile's backend, so in `tom`'s own `serve` process the registry is empty —
    and every tool called without an explicit target silently acted on the local
    desktop instead of the VM the human was looking at.
    """
    plugin.live_registry.set_panel_target(None)
    plugin.live_registry.set_current(None)
    plugin._bindings_file().write_text(json.dumps({"default": "vm:113", "tom": "vm:113"}))
    assert plugin._selected_target() == "vm:113"
    assert plugin._panel_context() is not None


def test_each_bot_gets_its_own_machine(plugin, monkeypatch):
    plugin.live_registry.set_panel_target(None)
    plugin._bindings_file().write_text(json.dumps({"default": "vm:113", "tom": "vm:107"}))
    monkeypatch.setattr(plugin, "_profile_name", lambda: "tom")
    assert plugin._selected_target() == "vm:107"


def test_a_bot_without_its_own_binding_uses_the_default(plugin, monkeypatch):
    plugin.live_registry.set_panel_target(None)
    plugin._bindings_file().write_text(json.dumps({"default": "vm:113"}))
    monkeypatch.setattr(plugin, "_profile_name", lambda: "riker")
    assert plugin._selected_target() == "vm:113"


def test_the_hosting_process_still_answers_from_its_registry(plugin):
    """In the process that owns the panel the registry is the fresher source."""
    plugin.live_registry.set_panel_target("vm:107")
    plugin._bindings_file().write_text(json.dumps({"tom": "vm:113"}))
    assert plugin._selected_target() == "vm:107"


def test_profile_name_comes_from_the_serve_command(monkeypatch):
    """Loaded fresh: the `plugin` fixture stubs _profile_name for other tests."""
    spec = importlib.util.spec_from_file_location("cb_profile_probe", ROOT / "__init__.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    monkeypatch.delenv("HERMES_PROFILE", raising=False)
    monkeypatch.setattr(
        module.sys, "argv", ["main.py", "--profile", "tom", "serve", "--host", "127.0.0.1"]
    )
    assert module._profile_name() == "tom"

    monkeypatch.setattr(module.sys, "argv", ["main.py", "serve"])
    assert module._profile_name() == "default"

    monkeypatch.setenv("HERMES_PROFILE", "luiza")
    assert module._profile_name() == "luiza", "an explicit env var wins"


def test_label_lookup_happens_once_even_when_it_finds_nothing(plugin, monkeypatch):
    """pre_llm_call runs every turn; a slow or dead Proxmox must not tax each one."""
    calls = []
    monkeypatch.setattr(plugin, "list_targets", lambda: calls.append(1))
    monkeypatch.setattr(plugin, "label_for", lambda tid: None)
    plugin._LABEL_LOOKUP_DONE = False

    assert plugin._label_lookup_once("vm:113") is None
    assert plugin._label_lookup_once("vm:113") is None
    assert plugin._label_lookup_once("vm:113") is None
    assert len(calls) == 1


def test_label_lookup_survives_an_unreachable_proxmox(plugin, monkeypatch):
    def boom():
        raise OSError("proxmox unreachable")

    monkeypatch.setattr(plugin, "list_targets", boom)
    plugin._LABEL_LOOKUP_DONE = False
    assert plugin._label_lookup_once("vm:113") is None

    _stream(plugin, "vm:113")
    plugin._LABEL_LOOKUP_DONE = False
    monkeypatch.setattr(plugin, "label_for", lambda tid: None)
    assert "vm:113" in plugin._panel_context(), "context must survive a failed lookup"


# --- the regression this cost us: pictures that travelled backwards -----------


def test_a_frame_older_than_the_last_input_is_never_served(plugin):
    """Reported as "input reports success but the text is missing".

    The stream frame is up to `max_age_s` old. Serving one taken before the
    keystroke shows the guest as it was BEFORE typing, so the agent concludes
    its own input failed — and the picture appears to move backwards when the
    next look falls back to a real capture.
    """
    _stream(plugin, "vm:113")
    mtime = plugin.LIVE_STREAM_FRAME.stat().st_mtime

    plugin._LAST_INPUT_AT = mtime + 0.5          # typed after this frame was taken
    assert plugin._stream_frame_for("vm:113", now=mtime + 0.6) is None

    plugin._LAST_INPUT_AT = mtime - 0.5          # frame is newer than the typing
    assert plugin._stream_frame_for("vm:113", now=mtime + 0.1) is not None


def test_capture_after_typing_takes_a_real_screenshot(plugin, monkeypatch):
    """After input the agent must see consequences, not a cached frame."""
    _stream(plugin, "vm:113")
    grabbed = []

    class FakeVnc:
        def send(self, info, cmd):
            return True

        def screenshot(self, info):
            grabbed.append(info["id"])
            return b"fresh-bytes"

        def dimensions(self, info):
            return (1280, 800)

    fake = FakeVnc()
    monkeypatch.setattr(plugin, "_vnc", lambda: fake)
    monkeypatch.setattr(plugin, "EVIDENCE_DIR", plugin.LIVE_STREAM_FRAME.parent)

    assert plugin._inject({"op": "text", "text": "hello"}, "vm:113")["ok"] is True
    body = json.loads(plugin._capture({"target": "vm:113"}))
    assert body["source"] == "capture", "a cached frame cannot show what we just typed"
    assert grabbed == ["vm:113"]


def test_input_timestamp_only_moves_on_delivery(plugin, monkeypatch):
    """A rejected command changed nothing, so it must not invalidate the stream."""
    _stream(plugin, "vm:113")

    class DeadVnc:
        def send(self, info, cmd):
            return False

    monkeypatch.setattr(plugin, "_vnc", lambda: DeadVnc())
    plugin._LAST_INPUT_AT = 0.0
    result = plugin._inject({"op": "text", "text": "hello"}, "vm:113")
    assert result["ok"] is False
    assert plugin._LAST_INPUT_AT == 0.0


# --- the standing brief in the system prompt ----------------------------------


def test_system_prompt_section_warns_off_computer_use(plugin, monkeypatch):
    """The measured failure: the model drove the LOCAL desktop instead of the VM.

    `computer_use` is always visible, its name reads like the answer to "use the
    computer", and it succeeds — against cua-driver on the human's own machine.
    Nothing else in the prompt tells the model those are different computers.
    """
    _stream(plugin, "vm:113")
    monkeypatch.setattr(plugin, "label_for", lambda tid: "VM 113 (omarchy)")
    section = plugin._system_prompt_section()
    assert "computer_use" in section
    assert "local desktop" in section.lower()
    assert "omarchy" in section
    assert "computer_bridge_paste" in section


def test_system_prompt_section_is_empty_without_a_machine(plugin):
    """No machine selected means no section — not a paragraph about nothing."""
    _stream(plugin, None, frame_written=False)
    assert plugin._system_prompt_section() == ""


def test_system_prompt_section_fits_the_host_budget(plugin, monkeypatch):
    """Hermes caps a plugin section at 4000 characters."""
    _stream(plugin, "vm:113")
    monkeypatch.setattr(plugin, "label_for", lambda tid: "VM 113 (omarchy)")
    assert len(plugin._system_prompt_section()) < 4000


def test_registration_survives_a_host_without_the_section_api(plugin):
    """An older Hermes must still get a working plugin, not an exception."""
    registered = {}

    class OldCtx:
        def register_tool(self, name, toolset, schema, handler):
            registered.setdefault("tools", []).append(name)

        def register_hook(self, name, callback):
            registered["hook"] = callback

        def register_system_prompt_section(self, *a, **k):
            raise AttributeError("not supported here")

    plugin.register(OldCtx())
    assert len(registered["tools"]) == 10
    assert registered["hook"] is not None


def test_only_the_default_profile_seeds_the_others(plugin, monkeypatch):
    """A bot must not try to install the plugin into its siblings.

    Seeding runs from the default profile because that is the one place the
    plugin is guaranteed to be loaded; a bot that lacks it cannot run this code
    anyway, and a bot that has it has no business writing into other profiles.
    """
    calls = []
    monkeypatch.setattr(
        "hermes_computer_bridge.profile_install.auto_install",
        lambda repo, log=None: calls.append(repo) or [],
    )

    monkeypatch.setattr(plugin, "_profile_name", lambda: "tom")
    plugin._seed_other_profiles()
    assert calls == [], "a bot profile must not seed anything"

    monkeypatch.setattr(plugin, "_profile_name", lambda: "default")
    plugin._seed_other_profiles()
    assert calls == [plugin.PLUGIN_DIR]


def test_seeding_failure_never_breaks_registration(plugin, monkeypatch):
    """Registration must survive a read-only or unusual ~/.hermes."""
    def boom(repo, log=None):
        raise OSError("read-only filesystem")

    monkeypatch.setattr("hermes_computer_bridge.profile_install.auto_install", boom)
    monkeypatch.setattr(plugin, "_profile_name", lambda: "default")
    plugin._seed_other_profiles()  # must not raise
