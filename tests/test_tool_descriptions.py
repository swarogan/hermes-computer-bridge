"""Tool descriptions must sell the capability inside the catalog's preview.

Hermes ships this plugin as a DEFERRED toolset: the model does not get the
schemas, only a catalog listing each tool's name and the first ~60 characters
of its description. A model asked to "open a browser on the remote omarchy
system" once read `computer_bridge_targets: List the desktops this bot can view
and control: the local…` — cut exactly before "plus every running Proxmox VM" —
concluded the toolset was local-only, and went looking for SSH instead.
"""

from __future__ import annotations

import importlib.util
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CATALOG_PREVIEW = 62


def _schemas():
    spec = importlib.util.spec_from_file_location("cb_plugin_under_test", ROOT / "__init__.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    collected = []

    class Ctx:
        def register_tool(self, name, toolset, schema, handler):
            collected.append(schema)

        def __getattr__(self, item):
            return lambda *a, **k: None

    module.register(Ctx())
    return collected


def _preview(description: str) -> str:
    return textwrap.shorten(" ".join(description.split()), width=CATALOG_PREVIEW, placeholder="")


@pytest.mark.parametrize("schema", _schemas(), ids=lambda s: s["name"])
def test_preview_says_the_tool_reaches_another_machine(schema):
    """Every tool must show it works on a remote VM before the catalog cuts."""
    preview = _preview(schema["description"]).lower()
    assert "remote vm" in preview, (
        f"{schema['name']}: the catalog preview is {preview!r}, which never says "
        "the tool can drive another machine"
    )


@pytest.mark.parametrize("schema", _schemas(), ids=lambda s: s["name"])
def test_preview_is_not_spent_on_implementation_detail(schema):
    """'capability ladder', 'rung' and 'PipeWire' describe how it is built.

    They are fine later in the description; spending the preview on them is what
    made the toolset look inapplicable.
    """
    preview = _preview(schema["description"]).lower()
    for jargon in ("capability ladder", "rung", "pipewire", "screencast"):
        assert jargon not in preview, f"{schema['name']}: preview wasted on {jargon!r}"


def test_manifest_lists_every_registered_tool():
    import yaml

    manifest = yaml.safe_load((ROOT / "plugin.yaml").read_text(encoding="utf-8"))
    assert set(manifest["provides_tools"]) == {s["name"] for s in _schemas()}
