"""Isolated round-trip of curved (IfcAdvancedFace) plates through IFC.

A foreign/exported IFC may represent a curved plate as a single
``IfcAdvancedFace`` (B-spline surface) instead of an extruded profile. adapy
imports these as :class:`PlateCurved`, keeping the surface geometry (and a flat
fallback for rendering). This verifies adapy -> IFC -> adapy preserves the
curved-plate geometry instead of crashing or losing it.
"""

import os

import ada
import ada.geom.surfaces as geo_su
from ada.api.plates.base_pl import PlateCurved
from ada.cadit.sat.store import SatReaderFactory
from ada.config import Config


def _curved_plate_from_sat(sat_path) -> PlateCurved:
    sat_reader = SatReaderFactory(sat_path)
    _, adv_face = next(iter(sat_reader.iter_advanced_faces()))
    return PlateCurved("curved_plate", ada.geom.Geometry(1, adv_face, None), t=0.02)


def test_advanced_face_plate_roundtrip(example_files, tmp_path):
    """Bare-face round-trip (curved-shell thickening OFF): the body is a single
    IfcAdvancedFace, which adapy re-imports as a PlateCurved."""
    os.environ["ADA_GEOM_THICKEN_CURVED_SHELLS"] = "false"
    try:
        Config().reload_config()
        pc = _curved_plate_from_sat(example_files / "sat_files/curved_plate.sat")
        assert isinstance(pc.geom.geometry, geo_su.AdvancedFace)

        fp = (ada.Assembly() / (ada.Part("P") / pc)).to_ifc(tmp_path / "curved.ifc", file_obj_only=True)
        b = ada.from_ifc(fp)

        curved = [o for o in b.get_all_physical_objects() if isinstance(o, PlateCurved)]
        assert len(curved) == 1
        geom = curved[0].geom.geometry
        assert isinstance(geom, geo_su.AdvancedFace)
        assert isinstance(geom.face_surface, geo_su.BSplineSurfaceWithKnots)
        assert len(geom.bounds) == 1
    finally:
        os.environ.pop("ADA_GEOM_THICKEN_CURVED_SHELLS", None)
        Config().reload_config()


def test_advanced_face_plate_thick_roundtrip_keeps_geometry(example_files, tmp_path):
    """Default (thickening ON): the body is an IfcAdvancedBrep of the thickness-t
    shell. Re-import yields a generic B-rep shape (not a PlateCurved — the thickness
    parameterisation is baked into the brep), but the geometry is preserved and
    renders — no geometry left behind."""
    pc = _curved_plate_from_sat(example_files / "sat_files/curved_plate.sat")
    fp = (ada.Assembly() / (ada.Part("P") / pc)).to_ifc(tmp_path / "curved_thick.ifc", file_obj_only=True)
    assert len(fp.by_type("IfcAdvancedBrep")) == 1

    b = ada.from_ifc(fp)
    objs = list(b.get_all_physical_objects())
    assert len(objs) == 1
    scene = b.to_trimesh_scene()
    faces = sum(g.faces.shape[0] for g in scene.geometry.values())
    assert faces > 0


def test_advanced_face_plate_renders(example_files):
    """The curved plate tessellates to a non-empty mesh (not silently dropped)."""
    pc = _curved_plate_from_sat(example_files / "sat_files/curved_plate.sat")
    a = ada.Assembly() / (ada.Part("P") / pc)

    scene = a.to_trimesh_scene()
    faces = sum(g.faces.shape[0] for g in scene.geometry.values())
    assert faces > 0


def _mesh_area(model) -> float:
    import trimesh

    scene = model.to_trimesh_scene()
    tris = [g for g in scene.geometry.values() if isinstance(g, trimesh.Trimesh)]
    return float(trimesh.util.concatenate(tris).area) if tris else 0.0


def test_advanced_face_pcurve_roundtrip_preserves_area(example_files, tmp_path):
    """The UV p-curve must survive IFC export/import.

    Without it the re-imported trimmed B-spline face tessellates to a degenerate,
    near-zero-area mesh (the curved plate looks "missing" in the viewer). Exporting
    the p-curve via IfcSurfaceCurve/IfcPcurve keeps the area within tolerance.
    """
    pc = _curved_plate_from_sat(example_files / "sat_files/curved_plate.sat")
    a = ada.Assembly() / (ada.Part("P") / pc)
    area_src = _mesh_area(a)
    assert area_src > 0.1

    fp = a.to_ifc(tmp_path / "curved.ifc", file_obj_only=True)

    # The p-curve must be present in the exported IFC.
    assert len(fp.by_type("IfcPcurve")) > 0

    b = ada.from_ifc(fp)
    area_re = _mesh_area(b)
    # Must stay the same order of magnitude. Without the p-curve the face collapses
    # to <1% of its area; tessellation granularity alone varies by only a few %.
    assert area_re > 0.5 * area_src, f"area collapsed: {area_re} vs {area_src} (p-curve likely lost)"


def test_advanced_face_ifc_is_valid(example_files, tmp_path):
    """The exported curved-plate IFC (with p-curves) passes ifcopenshell validation."""
    from ifcopenshell import validate

    pc = _curved_plate_from_sat(example_files / "sat_files/curved_plate.sat")
    fp = (ada.Assembly() / (ada.Part("P") / pc)).to_ifc(tmp_path / "curved.ifc", file_obj_only=True)

    logger = validate.json_logger()
    validate.validate(fp, logger)
    geom_issues = [
        s
        for s in logger.statements
        if s.get("instance") is not None
        and any(k in s["instance"].is_a() for k in ("Curve", "Surface", "Face", "Pcurve", "Edge", "BSpline"))
    ]
    assert geom_issues == []
