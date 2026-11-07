"""GeniE's beam end hinges read into adapy (read_hinges) and written back as the kind they were (write_hinges).

The fixture is GeniE V9.3-00's own export of ``genie_hinges_corrosion.js``: ``Hinge(1, 1, 1, 1, 0, 0)`` on Bm1's end 2,
``FlexibleHinge(Fixed, Fixed, Fixed, Fixed, Stiffness(1e6), Stiffness(2e6))`` on Bm2's end 1, and corrosion on a plate
and a beam. GeniE meshes both forms the same (tests/fem/test_genie_gxml_hinges.py)."""

from __future__ import annotations

import shutil
import xml.etree.ElementTree as ET

import ada
from ada.fem.formats import conversion_report

FIXTURE = "genie_hinges_corrosion.xml"


def _read(example_files, tmp_path):
    dst = tmp_path / FIXTURE
    shutil.copy(example_files / "fem_files" / "sesam" / FIXTURE, dst)
    with conversion_report.collect() as rep:
        a = ada.from_genie_xml(dst)
    beams = {bm.name: bm for bm in a.get_all_physical_objects(by_type=ada.Beam)}
    return a, beams, rep


def _dofs(hinge):
    return {d.dof: (d.constraint_type, d.spring_stiffness) for d in hinge.dofs}


def test_both_kinds_of_hinge_are_read_onto_the_ends_that_name_them(example_files, tmp_path):
    _, beams, rep = _read(example_files, tmp_path)
    pin, flex = beams["Bm1"].hinge2, beams["Bm2"].hinge1
    assert beams["Bm1"].hinge1 is None and beams["Bm2"].hinge2 is None and beams["Bm3"].hinge1 is None
    assert pin.name == "H_pin" and {d: c for d, (c, _) in _dofs(pin).items()} == {
        "dx": "fixed",
        "dy": "fixed",
        "dz": "fixed",
        "rx": "fixed",
        "ry": "free",
        "rz": "free",
    }
    assert flex.name == "H_flex" and _dofs(flex)["ry"] == ("spring", 1.0e6) and _dofs(flex)["rz"] == ("spring", 2.0e6)
    assert all(_dofs(flex)[d][0] == "fixed" for d in ("dx", "dy", "dz", "rx"))
    # nothing about the hinges or the beam ends is reported left out any more
    assert not [f for f in rep.findings if f.kind == "omitted" and f.keyword in ("hinge", "hinges", "end1/end2")]


def test_corrosion_is_named_as_a_note_on_the_plate_and_the_beam(example_files, tmp_path):
    _, _, rep = _read(example_files, tmp_path)
    (note,) = [f for f in rep.findings if f.keyword == "corrosion_addition_ref"]
    assert note.kind == "note" and note.count == 2  # Pl1 (both sides) and Bm3


def test_written_back_each_hinge_is_the_kind_genie_wrote(example_files, tmp_path):
    a, _, _ = _read(example_files, tmp_path)
    out = a.to_genie_xml(tmp_path / "back.xml", embed_sat=False)
    root = ET.parse(out).getroot()
    hinges = {h.get("name"): h for h in root.iter("hinge")}
    pin, flex = hinges["H_pin"], hinges["H_flex"]
    assert [c.get("f") for c in pin.iter("connectivity")] == ["1", "1", "1", "1", "0", "0"]
    springs = {
        bc.get("dof"): bc.get("stiffness") for bc in flex.iter("boundary_condition") if bc.get("constraint") == "spring"
    }
    assert {d: float(v) for d, v in springs.items()} == {"ry": 1.0e6, "rz": 2.0e6}
    ends = {
        (bm.get("name"), end.tag): end.get("hinge_ref")
        for bm in root.iter("straight_beam")
        for end in bm
        if end.tag in ("end1", "end2")
    }
    assert ends.get(("Bm1", "end2")) == "H_pin" and ends.get(("Bm2", "end1")) == "H_flex"
