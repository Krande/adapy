"""Tests for the memory-bounded streaming IFC writer (to_ifc(streaming=True))."""

import ada


def _model():
    p = ada.Part("MyPart")
    p.add_plate(ada.Plate("MyPlate", [(0, 0), (1, 0), (1, 1), (0, 1)], 20e-3))
    p.add_plate(ada.Plate("Tri", [(0, 0), (2, 0), (0, 2)], 10e-3))
    p.add_beam(ada.Beam("MyBeam", (0, 0, 0), (1, 0, 0), "IPE200"))
    return ada.Assembly("A") / p


def test_streaming_roundtrip(tmp_path):
    dest = tmp_path / "stream.ifc"
    ret = _model().to_ifc(dest, streaming=True)
    assert ret is None  # streaming writes to disk, holds no in-memory file
    assert dest.exists()

    a = ada.from_ifc(dest)
    pl: ada.Plate = a.get_by_name("MyPlate")
    assert isinstance(pl, ada.Plate)
    assert pl.parent.name == "MyPart"
    assert pl.t == 20e-3
    corners = list(dict.fromkeys(tuple(round(c, 6) for c in p) for p in pl.poly.points2d))
    assert len(corners) == 4

    assert isinstance(a.get_by_name("Tri"), ada.Plate)
    assert isinstance(a.get_by_name("MyBeam"), ada.Beam)


def test_streaming_matches_normal_object_count(tmp_path):
    import ifcopenshell

    n = _model().to_ifc(tmp_path / "normal.ifc")
    _model().to_ifc(tmp_path / "stream.ifc", streaming=True)

    fn = ifcopenshell.open(str(tmp_path / "normal.ifc"))
    fs = ifcopenshell.open(str(tmp_path / "stream.ifc"))
    for ifc_class in ("IfcPlate", "IfcBeam"):
        assert len(fs.by_type(ifc_class)) == len(fn.by_type(ifc_class))
    # every streamed plate carries a resolvable representation + placement
    assert all(p.Representation and p.ObjectPlacement for p in fs.by_type("IfcPlate"))
    assert n is not None  # normal path still returns the in-memory file


def test_streaming_fused_from_fem(tmp_path):
    # A part with a FEM shell mesh but no concept plates takes the fused path:
    # Part.iter_objects_from_fem builds + streams one plate per shell element.
    import ifcopenshell
    import ifcopenshell.util.element as ue

    src = ada.Plate("P", [(0, 0), (2, 0), (2, 1), (0, 1)], 0.02)
    part = ada.Part("pp")
    part.fem = src.to_fem_obj(0.5, "shell")
    n_shells = len(list(part.fem.elements.shell))
    assert n_shells > 1 and not len(part.plates)  # precondition for the fused path

    (ada.Assembly("A") / part).to_ifc(tmp_path / "fused.ifc", streaming=True)

    g = ifcopenshell.open(str(tmp_path / "fused.ifc"))
    plates = g.by_type("IfcPlate")
    assert len(plates) == n_shells  # 1:1, no coplanar merge in the streaming path
    assert all(p.Representation and p.ObjectPlacement for p in plates)
    assert ue.get_material(plates[0]) is not None
    # colour is a per-plate IfcStyledItem referencing a SHARED IfcSurfaceStyle
    assert len(g.by_type("IfcStyledItem")) >= len(plates)
    assert len(g.by_type("IfcSurfaceStyle")) < len(plates)


def test_streaming_falls_back_for_file_obj_only(tmp_path):
    # streaming needs an on-disk destination; file_obj_only must fall back, not crash
    f = _model().to_ifc(file_obj_only=True, streaming=True)
    assert f is not None
    assert len(f.by_type("IfcPlate")) == 2
