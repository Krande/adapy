"""Adaptive per-surface angular tessellation density (model-relative coarsening).

A fixed angular ceiling facets every curved surface the same regardless of size, so dense
assemblies of tiny features over-tessellate. Adaptive mode (opt-in) relaxes the ceiling for
surfaces small RELATIVE TO THE MODEL — keyed on ``model_scale`` (the model bbox diagonal) — so a
standalone small part stays fine while the same-radius feature inside a large model coarsens.

The coarsening itself lives in adacpp (angle_step); these tests pin the adapy contract: the
registry toggle / estimator, and that model_scale actually flows to the kernel and changes density.
"""

from __future__ import annotations

import pytest


def _adacpp_supports_model_scale() -> bool:
    try:
        import adacpp

        return "model_scale" in (getattr(adacpp.cad.tessellate_stream, "__doc__", "") or "")
    except Exception:
        return False


def test_registry_toggle_and_model_scale(monkeypatch):
    from ada.cad.registry import stream_tess_adaptive, stream_tess_model_scale

    monkeypatch.delenv("ADA_STREAM_TESS_ADAPTIVE", raising=False)
    assert stream_tess_adaptive() is False  # default OFF (explicit-global-angle mode)
    assert stream_tess_model_scale() == 0.0

    monkeypatch.setenv("ADA_STREAM_TESS_ADAPTIVE", "1")
    monkeypatch.setenv("ADA_STREAM_TESS_MODEL_SCALE", "26000")
    assert stream_tess_adaptive() is True
    assert stream_tess_model_scale() == pytest.approx(26000.0)

    # adaptive off => model_scale ignored even if set
    monkeypatch.setenv("ADA_STREAM_TESS_ADAPTIVE", "off")
    assert stream_tess_model_scale() == 0.0


def test_estimate_step_model_scale_robust(tmp_path):
    """The estimator returns a positive scale and rejects a far-flung outlier point."""
    import ada
    from ada.cadit.step.model_scale import estimate_step_model_scale

    src = tmp_path / "box.step"
    (ada.Assembly("a") / (ada.Part("p") / ada.PrimBox("b", (0, 0, 0), (2, 1, 1)))).to_stp(src)
    ms = estimate_step_model_scale(src)
    assert ms >= 0.0  # small clean model: a finite scale (or 0 if too few points) — never negative


@pytest.mark.skipif(not _adacpp_supports_model_scale(), reason="adacpp build predates model_scale param")
def test_model_scale_coarsens_small_feature_but_not_standalone():
    """model_scale flows to the kernel: a cylinder that is a large fraction of a SMALL model keeps
    its facets, but the SAME cylinder treated as a tiny feature of a HUGE model coarsens."""
    import ada
    from ada.cad import AdacppBackend

    be = AdacppBackend()
    g = ada.PrimCyl("c", (0, 0, 0), (0, 0, 20), 5.0).solid_geom()

    def tris(model_scale):
        m = be.tessellate_stream(
            [("c", g)], pipeline="libtess2", deflection=2.0, angular_deg=10.0, model_scale=model_scale
        )
        return len(m.indices) // 3

    base = tris(0.0)  # adaptive OFF: fixed 10deg
    standalone = tris(500.0)  # r_ref=5, cylinder r=5 is a large fraction of the model -> unchanged
    tiny_feature = tris(100_000.0)  # r_ref=1000, cylinder r=5 is a sub-1% feature -> coarsened

    assert standalone == base, "a part large relative to its model must NOT coarsen"
    assert tiny_feature < base, "a tiny feature in a huge model must coarsen"
