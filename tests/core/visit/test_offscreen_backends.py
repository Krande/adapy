"""The offscreen renderer seam: backend choice, the embed camera preset as matrices, the poster legend.

None of this needs a GPU or adacpp's renderer extension, so it runs everywhere.
"""

import builtins
import math

import numpy as np
import pytest

from ada.visit.rendering import offscreen
from ada.visit.rendering.adacpp_offscreen import preset_camera
from ada.visit.rendering.fea_offscreen import _legend_spec


def test_auto_falls_back_to_pygfx_without_the_adacpp_extension(monkeypatch):
    real_import = builtins.__import__

    def no_render(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "adacpp" and fromlist and "_render" in fromlist:
            raise ImportError("adacpp built without the renderer")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", no_render)
    assert offscreen.resolve_backend("auto") == "pygfx"
    assert offscreen.resolve_backend("chromium") == "chromium"


def test_preset_camera_places_the_eye_as_the_embed_does():
    # A 2 x 2 x 2 box at the origin: radius sqrt(3), azimuth -135, elevation 30, fov 45, margin 1.15.
    view, proj = preset_camera((-1, -1, -1, 1, 1, 1), 1.0)
    v = view.reshape(4, 4).T  # column-major -> row-major
    eye = -v[:3, :3].T @ v[:3, 3]
    fov = math.radians(45)
    d = math.sqrt(3) / math.sin(fov / 2) * 1.15
    az, el = math.radians(-135), math.radians(30)
    expected = [d * math.cos(el) * math.sin(az), d * math.cos(el) * math.cos(az), d * math.sin(el)]
    assert np.allclose(eye, expected, rtol=1e-5)
    # Looking at the centre with world z kept up: the view's up vector has a positive z.
    assert v[1, 2] > 0
    p = proj.reshape(4, 4).T
    assert math.isclose(p[1, 1], 1 / math.tan(fov / 2), rel_tol=1e-6)
    assert p[3, 2] == -1.0  # OpenGL-convention perspective, as three.js builds it


def test_poster_legend_names_the_field_unit_and_case_and_ticks_from_the_peak_down():
    manifest = {
        "fields": [
            {
                "blob": {"url": "fea.disp.bin"},
                "group_path": ["Nodes", "DISPLACEMENT"],
                "components": ["ALL", "X", "Y", "Z"],
                "component_units": ["m", "m", "m", "m"],
                "steps": [{"i": 0, "label": "12.7558"}, {"i": 1, "label": "19.5048"}],
            }
        ]
    }
    spec = _legend_spec(manifest, "fea.disp.bin", 1, 0.3)
    assert spec["header"] == ["Nodes / DISPLACEMENT", "Magnitude [m]", "Case: 19.5048"]
    assert len(spec["ticks"]) == 7
    assert math.isclose(spec["ticks"][0], 0.3) and spec["ticks"][-1] == 0.0
    assert spec["gradient"][0] == [0.0, 0.0, 1.0] and spec["gradient"][-1] == [1.0, 0.0, 0.0]


def test_shared_adacpp_renderer_resizes_and_drops_the_previous_overlay(tmp_path):
    pytest.importorskip("adacpp._render")
    import trimesh

    from ada.visit.rendering.adacpp_offscreen import glb_to_image

    glb = tmp_path / "box.glb"
    trimesh.creation.box().export(glb)
    with_gizmo = np.asarray(glb_to_image(glb, size=(200, 150), overlay={"gizmo": True}))
    plain = np.asarray(glb_to_image(glb, size=(320, 240)))
    assert with_gizmo.shape == (150, 200, 4) and plain.shape == (240, 320, 4)
    # The gizmo sits in the bottom-right corner; the next call without an overlay must leave it empty.
    assert with_gizmo[-60:, -60:, 3].any()
    assert not plain[-60:, -60:, 3].any()
