"""Subset streaming on the native IFC->GLB path (``include_guids``).

``adacpp.cad.stream_ifc_to_glb(include_guids=[...])`` streams only the products whose IFC GlobalId
is in the set, so a branch of a published spatial tree can be built without first slicing a subset
IFC. What is pinned here is the adapy-side contract: the kwarg reaches the binding, an empty/absent
filter still means the whole file, a filter that matches nothing raises instead of yielding a GLB of
the whole model, and an adacpp too old to take the kwarg is REFUSED rather than silently widened.

Gated on the native entry point; the subset cases additionally need an adacpp that takes the kwarg.
All fixtures are synthetic — no client data.
"""

import pytest

import ada
from ada.cadit.ifc.native_ifc_to_glb import (
    native_ifc_glb_available,
    native_ifc_subset_available,
    native_ifc_to_glb,
)

pytestmark = pytest.mark.skipif(not native_ifc_glb_available(), reason="adacpp native IFC->GLB not available")

needs_subset = pytest.mark.skipif(
    not native_ifc_subset_available(), reason="adacpp build predates stream_ifc_to_glb(include_guids=...)"
)


@pytest.fixture
def ifc_with_three_beams(tmp_path):
    """Three beams -> an IFC whose GlobalIds are the adapy guids. Returns (path, [guid, ...])."""
    beams = [ada.Beam(f"bm{i}", (0, 0, i), (1, 0, i), "IPE300") for i in range(3)]
    src = tmp_path / "three_beams.ifc"
    (ada.Assembly("m") / (ada.Part("p") / beams)).to_ifc(src, validate=False)
    return src, [bm.guid for bm in beams]


@pytest.mark.adacpp  # needs the adacpp kernel; deselected where it is absent
@needs_subset
def test_no_filter_streams_every_product(ifc_with_three_beams, tmp_path):
    src, _ = ifc_with_three_beams
    assert native_ifc_to_glb(src, tmp_path / "all.glb")["solids"] == 3


@pytest.mark.adacpp  # needs the adacpp kernel; deselected where it is absent
@needs_subset
@pytest.mark.parametrize("n_requested", [1, 2, 3])
def test_filter_streams_exactly_its_matches(ifc_with_three_beams, tmp_path, n_requested):
    src, guids = ifc_with_three_beams
    out = tmp_path / f"subset_{n_requested}.glb"
    stats = native_ifc_to_glb(src, out, include_guids=guids[:n_requested])
    assert stats["solids"] == n_requested
    assert out.stat().st_size > 0


@pytest.mark.adacpp  # needs the adacpp kernel; deselected where it is absent
@needs_subset
def test_empty_filter_is_every_product(ifc_with_three_beams, tmp_path):
    """[] must mean "no filter", not "nothing" — it is the falsy default path, not a raise."""
    src, _ = ifc_with_three_beams
    assert native_ifc_to_glb(src, tmp_path / "empty.glb", include_guids=[])["solids"] == 3


@pytest.mark.adacpp  # needs the adacpp kernel; deselected where it is absent
@needs_subset
def test_subset_is_smaller_than_the_whole(ifc_with_three_beams, tmp_path):
    src, guids = ifc_with_three_beams
    whole, part = tmp_path / "whole.glb", tmp_path / "part.glb"
    native_ifc_to_glb(src, whole)
    native_ifc_to_glb(src, part, include_guids=guids[:1])
    assert part.stat().st_size < whole.stat().st_size


@pytest.mark.adacpp  # needs the adacpp kernel; deselected where it is absent
@needs_subset
def test_filter_matching_nothing_raises(ifc_with_three_beams, tmp_path):
    """The spine disagrees with the file: raise rather than hand back a GLB of everything."""
    src, _ = ifc_with_three_beams
    out = tmp_path / "none.glb"
    with pytest.raises(RuntimeError, match="GlobalId"):
        native_ifc_to_glb(src, out, include_guids=["NOTAREALGUID0000000000"])
    assert not out.exists()


@pytest.mark.adacpp  # needs the adacpp kernel; deselected where it is absent
def test_older_binding_is_refused_not_widened(ifc_with_three_beams, tmp_path, monkeypatch):
    """An adacpp that ignores the kwarg would convert the WHOLE model and report success."""
    src, guids = ifc_with_three_beams
    monkeypatch.setattr("ada.cadit.ifc.native_ifc_to_glb.native_ifc_subset_available", lambda: False)
    with pytest.raises(RuntimeError, match="whole model"):
        native_ifc_to_glb(src, tmp_path / "widened.glb", include_guids=guids[:1])
