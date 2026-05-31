"""Unit tests for the worker @utility registry (auto-announce backbone)."""
from __future__ import annotations

import pytest

from ada.comms.rest.utility import (
    UtilityRegistry,
    run_utility,
    utility,
    viewops_key_for,
)


def test_register_and_spec_wire_shape():
    @utility(
        name="t_color_all",
        description="color everything",
        kwargs=[
            {"name": "shade", "type": "enum", "default": "red", "description": "x", "enum": ["red", "blue"]},
            {"name": "alpha", "type": "float", "default": 1.0, "description": "opacity"},
        ],
    )
    def _impl(scene_glb_path, *, storage, scope, on_progress, shade="red", alpha=1.0):
        return {"ops": [{"op": "color_elements", "elements": [{"key": "EL1", "color": "#ff0000"}]}]}

    specs = {s["name"]: s for s in UtilityRegistry.specs()}
    assert "t_color_all" in specs
    spec = specs["t_color_all"]
    assert spec["description"] == "color everything"
    assert spec["inputs"] == ["scene_glb"]
    assert spec["affects"] == ["scene.element_colors"]
    assert spec["returns"] == "viewer_ops"
    assert [k["name"] for k in spec["kwargs"]] == ["shade", "alpha"]
    assert specs["t_color_all"]["kwargs"][0]["enum"] == ["red", "blue"]


def test_run_utility_dispatch_and_version_default():
    @utility(name="t_runme", description="d")
    def _impl(scene_glb_path, *, storage, scope, on_progress, **kw):
        on_progress("working", 0.5)
        return {"ops": [{"op": "color_elements", "elements": []}], "summary": {"seen": scene_glb_path}}

    seen_stages = []
    out = run_utility(
        "t_runme",
        "/tmp/scene.glb",
        storage=object(),
        scope=object(),
        on_progress=lambda s, f: seen_stages.append((s, f)),
        kwargs={},
    )
    assert out["version"] == 1  # defaulted
    assert out["summary"]["seen"] == "/tmp/scene.glb"
    assert ("working", 0.5) in seen_stages


def test_unknown_utility_raises():
    from ada.comms.rest.utility import UnknownUtility

    with pytest.raises(UnknownUtility):
        UtilityRegistry.lookup("does_not_exist_xyz")


def test_malformed_payload_rejected():
    @utility(name="t_bad", description="d")
    def _impl(scene_glb_path, *, storage, scope, on_progress, **kw):
        return {"no_ops_key": True}

    with pytest.raises(ValueError):
        run_utility("t_bad", "/tmp/s.glb", storage=None, scope=None, kwargs={})


def test_viewops_key_for():
    assert viewops_key_for("models/wall.ifc", "diff") == "_derived/models/wall.ifc.diff.viewops.json"
    assert viewops_key_for("/a/b.glb", "x/y") == "_derived/a/b.glb.x_y.viewops.json"
