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
