"""Revolved beam: valid geometry, IFC round-trip, and buildingSMART import."""

import trimesh

import ada


def _solid_volume(model) -> float:
    scene = model.to_trimesh_scene()
    tris = [g for g in scene.geometry.values() if isinstance(g, trimesh.Trimesh)]
    return float(trimesh.util.concatenate(tris).volume) if tris else 0.0


def test_revolved_beam_solid_geom_is_valid():
    """A from-scratch revolved beam produces a solid of the expected volume."""
    import numpy as np

    from ada.geom.solids import RevolvedAreaSolid

    bm = ada.BeamRevolve("bm", ada.CurveRevolve((0, 0, 0), (0, 1, 0), 1.3, (0, 0, 1)), "IPE200")
    assert isinstance(bm.solid_geom().geometry, RevolvedAreaSolid)

    vol = _solid_volume(ada.Assembly() / (ada.Part("P") / bm))
    expected = bm.section.properties.Ax * (bm.curve.radius * np.deg2rad(bm.curve.angle))  # area * arc length
    assert vol == abs(vol)  # finite, positive
    assert abs(vol - expected) / expected < 0.05


def test_revolved_beam_roundtrip(tmp_path):
    bm = ada.BeamRevolve("bm", ada.CurveRevolve((0, 0, 0), (0, 1, 0), 1.3, (0, 0, 1)), "IPE200")
    a = ada.Assembly() / (ada.Part("P") / bm)
    vol0 = _solid_volume(a)

    fp = a.to_ifc(tmp_path / "rev.ifc", file_obj_only=True)

    # Valid IFC geometry.
    from ifcopenshell import validate

    logger = validate.json_logger()
    validate.validate(fp, logger)
    geom_issues = [
        s
        for s in logger.statements
        if s.get("instance") is not None
        and any(k in s["instance"].is_a() for k in ("Revolved", "Trimmed", "Axis", "Profile", "Curve"))
    ]
    assert geom_issues == []

    b = ada.from_ifc(fp)
    bm2 = next(o for o in b.get_all_physical_objects() if isinstance(o, ada.BeamRevolve))
    assert bm2.section.type == bm.section.type
    vol1 = _solid_volume(b)
    assert abs(vol1 - vol0) / vol0 < 0.02


def test_revolved_beam_occ_matches_ifc_stream():
    """The direct (OCC) tessellation and the ifcopenshell->mesh tessellation of the
    same revolved beam must agree — i.e. the geom and the exported IFC describe the
    same solid."""
    bm = ada.BeamRevolve("bm", ada.CurveRevolve((0, 0, 0), (0, 1, 0), 1.3, (0, 0, 1)), "IPE200")
    a = ada.Assembly() / (ada.Part("P") / bm)

    def _vol(**kw):
        sc = a.to_trimesh_scene(**kw)
        tris = [g for g in sc.geometry.values() if isinstance(g, trimesh.Trimesh)]
        return float(trimesh.util.concatenate(tris).volume)

    vol_occ = _vol()
    vol_stream = _vol(stream_from_ifc=True)
    assert vol_occ > 0
    assert abs(vol_stream - vol_occ) / vol_occ < 0.02


def test_import_buildingsmart_revolved_beam(example_files):
    """Import the buildingSMART beam-revolved-solid.ifc example and render it."""
    import numpy as np

    a = ada.from_ifc(example_files / "ifc_files/beams/beam-revolved-solid.ifc")
    beams = [o for o in a.get_all_physical_objects() if isinstance(o, ada.BeamRevolve)]
    assert len(beams) == 1
    bm = beams[0]
    assert bm.section.name == "IPE600"

    vol = _solid_volume(a)
    expected = bm.section.properties.Ax * (bm.curve.radius * np.deg2rad(bm.curve.angle))
    assert abs(vol - expected) / expected < 0.05


def test_buildingsmart_revolved_beam_arc_is_smooth(example_files):
    """The revolved beam is a large-radius (7.25 m) arc: its tessellation must sample
    the sweep finely enough that the bulge apex reaches the true arc extent, not fall
    short as a coarse few-segment polygon does. Guards the tessellator's angular
    deflection: a loose one facets the arc and clips the apex (~2% short).

    ada-cpp (the worker/viewer backend) meshes with an explicit 0.2 rad angular
    deflection. The pythonocc default path (ShapeTesselator) exposes no angular
    control — only a relative mesh_quality — so it stays coarse here; skip it (its
    finer BRepMesh path is env-gated, a separate follow-up)."""
    import numpy as np
    import pytest

    from ada.cad import active_backend

    if active_backend().name != "adacpp":
        pytest.skip("angular-deflection smoothness is the ada-cpp tessellation path (worker/viewer)")

    a = ada.from_ifc(example_files / "ifc_files/beams/beam-revolved-solid.ifc")
    scene = a.to_trimesh_scene(merge_meshes=True)
    verts = np.vstack([np.asarray(g.vertices) for g in scene.geometry.values() if hasattr(g, "vertices")])

    bm = next(o for o in a.get_all_physical_objects() if isinstance(o, ada.BeamRevolve))
    # Neutral-axis sagitta of the arc = r(1 - cos(theta/2)); the outer surface apex sits
    # a further radial half-width out. The tessellated max-x must reach nearly there.
    theta = np.deg2rad(bm.curve.angle)
    sagitta = bm.curve.radius * (1.0 - np.cos(theta / 2.0))
    apex = sagitta + bm.section.w_top / 2.0  # ~2.11 m for this IPE600 beam
    assert verts[:, 0].max() > apex * 0.99, f"arc apex clipped: {verts[:, 0].max():.3f} vs ~{apex:.3f}"


def test_buildingsmart_revolved_beam_via_ngeom_stream(example_files, monkeypatch):
    """Production (worker/viewer) GLB uses the NGEOM libtess2 stream tessellator, NOT the
    OCC BatchTessellator the other tests exercise. That path had the revolve wrong three
    ways: the angle was fed in as radians not degrees (~14 turns -> profile splattered
    ±12 m around the axis); the revolve axis was left in global coords while the tessellator
    expects it position-local (axis ended up parallel to the profile normal -> the sweep
    collapsed flat); and partial revolves emitted no end caps with inside-out winding
    (negative volume -> dark shading). Assert the streamed solid sits in the right place,
    closed and outward-facing. Gated to ada-cpp (libtess2 needs it)."""
    import numpy as np
    import pytest
    import trimesh

    from ada.cad import active_backend

    if active_backend().name != "adacpp":
        pytest.skip("NGEOM libtess2 stream tessellation is the ada-cpp path")

    monkeypatch.setenv("ADA_STREAM_TESS_PIPELINE", "libtess2")
    a = ada.from_ifc(example_files / "ifc_files/beams/beam-revolved-solid.ifc")
    bm = next(o for o in a.get_all_physical_objects() if isinstance(o, ada.BeamRevolve))
    sc = a.to_trimesh_scene(merge_meshes=True)
    m = trimesh.util.concatenate([g for g in sc.geometry.values() if hasattr(g, "faces")])
    lo, hi = np.asarray(m.vertices).min(0), np.asarray(m.vertices).max(0)

    # Arc chord runs along +Y to ~10 m; profile depth keeps Z within ±0.3; bulge in +X ~2 m.
    # Splatter (angle-as-radians) blows Z/X out to ±12; flat (global axis) puts the arc on
    # the wrong plane so Y never reaches 10.
    assert hi[1] == pytest.approx(10.08, abs=0.3), f"arc doesn't sweep to p2 along Y: ymax={hi[1]:.2f}"
    assert abs(lo[2]) < 0.4 and abs(hi[2]) < 0.4, f"profile splattered out of plane in Z: {lo[2]:.2f}..{hi[2]:.2f}"
    assert 1.5 < hi[0] < 2.3, f"bulge apex X off: {hi[0]:.2f}"
    # Caps present + outward winding -> a closed, positive-volume solid near the analytic value.
    exp = bm.section.properties.Ax * (bm.curve.radius * np.deg2rad(bm.curve.angle))
    # Filleted IPE600 sits a few % above the sharp-Ax analytic (area*arc-length); band-check
    # the magnitude, keep the positive-volume winding guard.
    assert m.volume > 0, f"inside-out winding (negative volume {m.volume:.3f})"
    assert exp * 0.95 < m.volume < exp * 1.12, f"volume {m.volume:.4f} vs analytic {exp:.4f}"


def _revolve_curve_params(model):
    bm = next(o for o in model.get_all_physical_objects() if isinstance(o, ada.BeamRevolve))
    c = bm.curve
    return bm, {
        "radius": round(float(c.radius), 3),
        "angle": round(float(c.angle), 2),
        "rot_axis": [round(float(x), 3) for x in c.rot_axis],
        "section": bm.section.name,
    }


def _stream_bbox_vol(model):
    import numpy as np

    sc = model.to_trimesh_scene(merge_meshes=True)
    m = trimesh.util.concatenate([g for g in sc.geometry.values() if hasattr(g, "faces")])
    v = np.asarray(m.vertices)
    return v.min(0), v.max(0), float(m.volume)


def test_buildingsmart_revolved_beam_ifc_roundtrip_via_stream(example_files, tmp_path, monkeypatch):
    """The revolved beam must survive an IFC write->read round-trip: the CurveRevolve
    parameters (radius, sweep angle, revolution axis, section) preserved AND the
    production NGEOM-tessellated solid landing in the same place, closed and outward.
    Exercises the libtess2 stream path (the worker/viewer + the path that carried the
    original splatter/flat/inside-out bug). Gated to ada-cpp."""
    import numpy as np
    import pytest

    from ada.cad import active_backend

    if active_backend().name != "adacpp":
        pytest.skip("NGEOM libtess2 stream tessellation is the ada-cpp path")

    monkeypatch.setenv("ADA_STREAM_TESS_PIPELINE", "libtess2")

    a0 = ada.from_ifc(example_files / "ifc_files/beams/beam-revolved-solid.ifc")
    _, p0 = _revolve_curve_params(a0)
    lo0, hi0, vol0 = _stream_bbox_vol(a0)

    a0.to_ifc(tmp_path / "rt.ifc")
    a1 = ada.from_ifc(tmp_path / "rt.ifc")
    bm1, p1 = _revolve_curve_params(a1)
    lo1, hi1, vol1 = _stream_bbox_vol(a1)

    # Parameters preserved exactly through the round-trip.
    assert p1 == p0 == {"radius": 7.25, "angle": 87.21, "rot_axis": [0.0, 0.0, 1.0], "section": "IPE600"}

    # Geometry preserved: still the correct arc (Y->10, Z in-plane, X-bulge), not splattered
    # or flat, and still a positive-volume closed solid near the analytic value.
    assert hi1[1] == pytest.approx(10.08, abs=0.3)
    assert abs(lo1[2]) < 0.4 and abs(hi1[2]) < 0.4
    assert 1.9 < hi1[0] < 2.2  # bulge apex ~2.08 at the 10deg corpus default
    # Filleted IPE600 sits a few % above the sharp-Ax analytic (area*arc-length); band-check.
    exp = bm1.section.properties.Ax * (bm1.curve.radius * np.deg2rad(bm1.curve.angle))
    assert vol1 > 0 and exp * 0.95 < vol1 < exp * 1.12
    # Round-trip is stable, not just valid: same bbox + volume as before the write.
    assert np.allclose(lo1, lo0, atol=1e-3) and np.allclose(hi1, hi0, atol=1e-3)
    assert vol1 == pytest.approx(vol0, rel=1e-3)
