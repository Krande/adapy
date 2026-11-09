"""What the GeniE XML reader does not read is named in the conversion report, never dropped without a word.

Each case edits a fixture to hold one construct no reader reads -- a beam end release, a hinge property, a corrosion
addition on a plate side, a material of a kind there is no model for, a point mass that is not one scalar, a set
member that is not a beam or plate, a concept of a kind not read -- and checks the reader still reads the rest and
names the construct (``ada.cadit.gxml.read.unread`` and the readers themselves).
"""

from __future__ import annotations

import shutil
import xml.etree.ElementTree as ET

import pytest

import ada
from ada.fem.formats import conversion_report

STAGE = "genie xml reader"


def _read(example_files, tmp_path, fixture, edit=None):
    dst = tmp_path / fixture
    shutil.copy(example_files / "fem_files" / "sesam" / fixture, dst)
    if edit is not None:
        tree = ET.parse(dst)
        edit(tree.getroot())
        tree.write(dst, encoding="utf-8", xml_declaration=True)
    with conversion_report.collect() as report:
        a = ada.from_genie_xml(dst)
    findings = {(f.kind, f.keyword): f for f in report.findings if f.stage == STAGE}
    return a, findings


def _props(root):
    return root.find("./model/structure_domain/properties")


def _beam_end(root):
    bm = next(root.iter("straight_beam"))
    ET.SubElement(bm.find("end1"), "hinge", {"hinge_ref": "H1"})


def _hinges(root):
    hinges = _props(root).find("hinges")
    if hinges is None:
        hinges = ET.SubElement(_props(root), "hinges")
    ET.SubElement(hinges, "hinge", {"name": "H1"})


def _material_kind(root):
    """A second material, of a kind with no model, used by nothing: named; the rest reads."""
    mats = next(root.iter("materials"))
    ET.SubElement(ET.SubElement(mats, "material", {"name": "Odd"}), "anisotropic_material")


def _set_member(root):
    sets = root.find("./model/structure_domain/sets")
    if sets is None:
        sets = ET.SubElement(root.find("./model/structure_domain"), "sets")
    s = ET.SubElement(sets, "set", {"name": "S_odd"})
    ET.SubElement(ET.SubElement(s, "concepts"), "concept", {"concept_ref": "no_such_concept"})


def _concept_kind(root):
    st = ET.SubElement(root.find("./model/structure_domain/structures"), "structure")
    ET.SubElement(st, "stiffened_panel", {"name": "Pn1"})


CASES = {
    "beam end": ("genie_loads_all_kinds.xml", _beam_end, ("omitted", "end1/end2")),
    # a hinge of no kind: read_hinges names it, and its beams read fully connected
    "hinge property": ("genie_loads_all_kinds.xml", _hinges, ("omitted", "hinge")),
    "material kind": ("genie_loads_all_kinds.xml", _material_kind, ("omitted", "anisotropic_material")),
    "set member": ("genie_loads_all_kinds.xml", _set_member, ("omitted", "set")),
    "concept kind": ("genie_loads_all_kinds.xml", _concept_kind, ("omitted", "stiffened_panel")),
}


@pytest.mark.parametrize("case", sorted(CASES))
def test_an_unread_construct_is_named(example_files, tmp_path, case):
    fixture, edit, key = CASES[case]
    a, findings = _read(example_files, tmp_path, fixture, edit)
    assert key in findings, sorted(findings)
    assert len(list(a.get_all_physical_objects())) > 0  # the rest is still read


def test_the_untouched_fixture_names_none_of_them(example_files, tmp_path):
    _, findings = _read(example_files, tmp_path, "genie_loads_all_kinds.xml")
    names = {kw for _, kw in findings}
    assert not names & {"end1/end2", "hinges", "set", "stiffened_panel", "anisotropic_material"}


def test_a_nonzero_corrosion_addition_on_a_plate_side_is_named(example_files, tmp_path):
    """curved_plates.xml's plates have cThk10 on their back; a zero addition (cThk00) is not named. A note, not an
    omission: GeniE's own mesh keeps the gross thickness (tests/fem/test_genie_gxml_hinges.py), as this reads it."""

    def corrode(root):
        adds = _props(root).find("corrosion_additions")
        if not any(c.get("name") == "cThk10" for c in adds):
            ET.SubElement(adds, "corrosion_addition", {"name": "cThk10", "addition": "0.001", "isStainless": "false"})
        else:
            next(c for c in adds if c.get("name") == "cThk10").set("addition", "0.001")

    _, findings = _read(example_files, tmp_path, "curved_plates.xml", corrode)
    assert ("note", "corrosion_addition_ref") in findings
    assert ("omitted", "corrosion_addition_ref") not in findings
    assert ("omitted", "corrosion_additions") not in findings


def test_a_beam_whose_material_is_not_read_is_left_out_by_name(example_files, tmp_path):
    """Its material of a kind with no model is named, and so is the beam that used it (it used to stop the read)."""

    def odd_material(root):
        mats = next(root.iter("materials"))
        ET.SubElement(ET.SubElement(mats, "material", {"name": "Odd"}), "anisotropic_material")
        seg = next(root.iter("straight_segment"))
        seg.set("material_ref", "Odd")

    a, findings = _read(example_files, tmp_path, "genie_loads_all_kinds.xml", odd_material)
    assert ("omitted", "anisotropic_material") in findings and ("omitted", "straight_segment") in findings
    assert len(list(a.get_all_physical_objects(by_type=ada.Beam))) > 0


def _aligned(**attrib):
    """Give the first straight beam an ``aligned_curve_offset`` with ``attrib`` besides its alignment."""

    def edit(root):
        bm = next(root.iter("straight_beam"))
        co = bm.find("curve_offset")
        if co is None:
            co = ET.SubElement(bm, "curve_offset")
        for child in list(co):
            co.remove(child)
        ET.SubElement(co, "aligned_curve_offset", {"alignment": "flush_top", **attrib})

    return edit


@pytest.mark.parametrize(
    "attrib", [{"constant_value": "0.05"}, {"constant_value": "0", "shiftLocalY": "0.1"}], ids=["constant", "shift"]
)
def test_an_aligned_offset_with_more_than_its_alignment_is_named(example_files, tmp_path, attrib):
    """The reader places an aligned beam by its alignment alone; a nonzero constant_value or a shiftLocalY would move
    it where the model read does not put it, so it is named rather than dropped without a word."""
    a, findings = _read(example_files, tmp_path, "genie_loads_all_kinds.xml", _aligned(**attrib))
    assert ("omitted", "aligned_curve_offset") in findings
    assert len(list(a.get_all_physical_objects(by_type=ada.Beam))) > 0


def test_an_aligned_offset_of_its_alignment_alone_is_not_named(example_files, tmp_path):
    """``constant_value="0"`` is what the writer and GeniE's own exports carry: nothing unread."""
    _, findings = _read(example_files, tmp_path, "genie_loads_all_kinds.xml", _aligned(constant_value="0"))
    assert ("omitted", "aligned_curve_offset") not in findings


def test_a_point_mass_that_is_not_one_scalar_is_named(example_files, tmp_path):
    def tensor_mass(root):
        st = ET.SubElement(root.find("./model/structure_domain/structures"), "structure")
        pm = ET.SubElement(st, "point_mass", {"name": "Pm_t"})
        ET.SubElement(ET.SubElement(pm, "geometry"), "position", {"x": "0", "y": "0", "z": "0"})
        ET.SubElement(ET.SubElement(pm, "mass"), "mass_matrix")

    _, findings = _read(example_files, tmp_path, "genie_loads_all_kinds.xml", tensor_mass)
    assert ("omitted", "point_mass") in findings
