"""Sesam solid-element export. Solid (continuum) elements carry no geometric cross-section,
so they emit a GELMNT1 topology record + a GELREF1 that binds only the material (geono=0);
there is no section record. Previously the section writer raised IncompatibleElements."""

import ada
from ada.fem.shapes.definitions import SolidShapes


def test_sesam_write_solid_hex_roundtrip(tmp_path):
    box = ada.PrimBox("box", (0, 0, 0), (1, 1, 1))
    fem = box.to_fem_obj(10, "solid", use_hex=True)
    assert len(fem.elements) == 1 and isinstance(next(iter(fem.elements)).type, SolidShapes)

    a = ada.Assembly("a") / (ada.Part("p", fem=fem) / box)
    a.to_fem("m", fem_format="sesam", scratch_dir=tmp_path, overwrite=True)

    fem_file = tmp_path / "m" / "mT1.FEM"
    assert fem_file.exists()
    text = fem_file.read_text()
    assert "GELMNT1" in text  # the solid element topology was written

    # read back: the HEX8 solid survives the round-trip
    b = ada.from_fem(fem_file)
    solids = [
        e
        for p in b.get_all_parts_in_assembly(True)
        if p.fem is not None
        for e in p.fem.elements
        if isinstance(e.type, SolidShapes)
    ]
    assert len(solids) == 1
