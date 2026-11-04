"""The offscreen renderer seam: backend choice, the embed camera preset as matrices, the poster legend.

The tests that render need adacpp's renderer extension and a graphics adapter; the rest run everywhere.
"""

import math

import numpy as np
import pytest

from ada.visit.renderer_manager import resolve_renderer
from ada.visit.rendering import offscreen
from ada.visit.rendering.adacpp_offscreen import preset_camera
from ada.visit.rendering.fea_offscreen import _legend_spec


def _renderers(monkeypatch, *, adacpp: bool, chromium: bool):
    from ada.visit.rendering import adacpp_offscreen, chromium_offscreen_utils

    monkeypatch.setattr(adacpp_offscreen, "is_available", lambda: adacpp)
    monkeypatch.setattr(chromium_offscreen_utils, "is_available", lambda embed_bundle=None: chromium)


def test_backends_resolve_to_adacpp_and_the_removed_pygfx_one_warns(monkeypatch):
    _renderers(monkeypatch, adacpp=True, chromium=False)
    assert offscreen.resolve_backend("auto") == "adacpp"
    assert offscreen.resolve_backend("chromium") == "chromium"
    with pytest.warns(DeprecationWarning, match="pygfx"):
        assert offscreen.resolve_backend("pygfx") == "adacpp"


def test_auto_falls_back_to_chromium_without_adacpps_renderer(monkeypatch):
    _renderers(monkeypatch, adacpp=False, chromium=True)
    with pytest.warns(UserWarning, match="adacpp._render"):
        assert offscreen.resolve_backend("auto") == "chromium"


def test_no_renderer_raises_instead_of_leaving_the_image_out(monkeypatch, tmp_path):
    _renderers(monkeypatch, adacpp=False, chromium=False)
    for backend in ("auto", "adacpp"):
        with pytest.raises(offscreen.OffscreenUnavailableError, match="adacpp._render"):
            offscreen.resolve_backend(backend)
    with pytest.raises(offscreen.OffscreenUnavailableError):
        offscreen.glb_to_image(tmp_path / "unused.glb")


def test_a_model_renders_to_a_static_image(tmp_path):
    """What a report embeds for every model view: fails, not skips, when the installed adacpp has
    no renderer, since that leaves every report without its figures."""
    import ada
    from ada.visit.rendering import adacpp_offscreen

    assert adacpp_offscreen.is_available(), offscreen._adacpp_missing_message()
    part = ada.Part("part_a")
    part.add_beam(ada.Beam("bm_a", (0, 0, 0), (2, 0, 0), "IPE300"))
    part.add_beam(ada.Beam("bm_b", (0, 0, 0), (0, 0, 2), "IPE300"))
    part.add_plate(ada.Plate("pl_a", [(0, 0), (2, 0), (2, 2), (0, 2)], 0.01))
    try:
        img = (ada.Assembly("asm_a") / part).render_offscreen(size=(160, 120))
    except offscreen.OffscreenUnavailableError as exc:
        if "adapter" in str(exc):
            pytest.skip(str(exc))  # a host without a graphics adapter, not a broken install
        raise
    alpha = np.asarray(img)[..., 3]
    assert img.size == (160, 120)
    assert 0 < (alpha > 0).mean() < 1  # the model is drawn, over a transparent background


def test_the_removed_pygfx_viewer_shows_with_react():
    assert resolve_renderer("trimesh") == "trimesh"
    with pytest.warns(DeprecationWarning, match="pygfx"):
        assert resolve_renderer("pygfx") == "react"


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
    try:
        with_gizmo = np.asarray(glb_to_image(glb, size=(200, 150), overlay={"gizmo": True}))
    except offscreen.OffscreenUnavailableError as exc:
        if "adapter" in str(exc):
            pytest.skip(str(exc))  # a host without a graphics adapter, not a broken install
        raise
    plain = np.asarray(glb_to_image(glb, size=(320, 240)))
    assert with_gizmo.shape == (150, 200, 4) and plain.shape == (240, 320, 4)
    # The gizmo sits in the bottom-right corner; the next call without an overlay must leave it empty.
    assert with_gizmo[-60:, -60:, 3].any()
    assert not plain[-60:, -60:, 3].any()
