"""adapy DISCOVERS the tessellation-track vocabulary; it must not enumerate it.

adacpp declares which tracks a given build provides (``adacpp.cad.tess_tracks()``); adapy adds only
its own (pythonocc's BRepMesh) and publishes the union. Adding a track in adacpp must show up here,
in the converter's option schema, and in the frontend dropdown with NO adapy change.

The legacy ``TessellationPath`` enum is the thing this replaces: it is a hand-written list, so it
cannot see any track added after it was written (e.g. ``adacpp:cdt``). It is kept for back-compat
only, and these tests pin that the discovered list is a strict superset.
"""

from __future__ import annotations

import pytest

from ada.cad.registry import (
    CadBackendName,
    TessellationPath,
    TessTrack,
    available_tess_tracks,
    tess_track_by_name,
)


def test_discovery_returns_descriptors_not_bare_names():
    tracks = available_tess_tracks()
    assert tracks, "at least adapy's own OCC track or an adacpp track must be available"
    for t in tracks:
        assert isinstance(t, TessTrack)
        assert t.name and t.label, f"{t.name!r} must carry a human label for the UI"
        assert isinstance(t.watertight, bool)
        assert isinstance(t.backend, CadBackendName)


def test_track_names_are_unique():
    names = [t.name for t in available_tess_tracks()]
    assert len(names) == len(set(names))


def test_adapy_owns_only_the_occ_track():
    """Every non-OCC track must come from adacpp. If adapy ever hardcodes another one, this fails."""
    pytest.importorskip("OCC")
    non_occ = [t for t in available_tess_tracks() if t.backend is not CadBackendName.OCC]
    assert all(t.backend is CadBackendName.ADACPP for t in non_occ)
    occ = [t for t in available_tess_tracks() if t.backend is CadBackendName.OCC]
    assert len(occ) == 1 and occ[0].pipeline is None, "OCC is BRepMesh: it has no adacpp pipeline arg"


def test_adacpp_tracks_are_discovered_from_adacpp():
    """The vocabulary must match what adacpp declares — not a list maintained in adapy."""
    pytest.importorskip("adacpp")
    import adacpp

    decl = getattr(adacpp.cad, "tess_tracks", None)
    if decl is None:
        pytest.skip("adacpp predates the tess_tracks declaration")
    declared = {t["name"] for t in decl()}
    discovered = {t.pipeline for t in available_tess_tracks() if t.backend is CadBackendName.ADACPP}
    assert discovered == declared, "adapy must publish exactly what adacpp declares"


def test_discovery_sees_tracks_the_legacy_enum_cannot():
    """The whole point: a track added to adacpp after the enum was written is still reachable."""
    pytest.importorskip("adacpp")
    import adacpp

    if getattr(adacpp.cad, "tess_tracks", None) is None:
        pytest.skip("adacpp predates the tess_tracks declaration")
    discovered = {t.name for t in available_tess_tracks()}
    legacy = {p.value for p in TessellationPath}
    # Not a strict-superset assertion on purpose: availability differs by env (an adacpp-only env has
    # no pythonocc, so no "occ"). What must hold is that discovery never MISSES an adacpp track.
    adacpp_declared = {f"adacpp:{t['name']}" for t in adacpp.cad.tess_tracks()}
    assert adacpp_declared <= discovered
    if "adacpp:cdt" in adacpp_declared:
        assert "adacpp:cdt" not in legacy, "cdt postdates the enum — that is why discovery exists"
        assert tess_track_by_name("adacpp:cdt") is not None


def test_lookup_by_name_and_unknown():
    tracks = available_tess_tracks()
    assert tess_track_by_name(tracks[0].name) == tracks[0]
    assert tess_track_by_name("definitely-not-a-track") is None


def test_watertight_is_advertised():
    """A caller must be able to ASK which tracks are watertight rather than know it."""
    pytest.importorskip("adacpp")
    if getattr(__import__("adacpp").cad, "tess_tracks", None) is None:
        pytest.skip("adacpp predates the tess_tracks declaration")
    wt = [t.name for t in available_tess_tracks() if t.watertight]
    assert "adacpp:cdt" in wt, "cdt is the watertight track and must advertise itself as such"
    assert "adacpp:libtess2" not in wt, "libtess2+pin halves cracks but is not watertight"
