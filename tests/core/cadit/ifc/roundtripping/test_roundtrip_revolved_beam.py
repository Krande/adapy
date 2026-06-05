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
