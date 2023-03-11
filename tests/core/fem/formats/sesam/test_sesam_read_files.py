import pytest

import ada


def test_read_hinged_beams_and_mass(example_files):
    a = ada.from_fem(example_files / "fem_files/sesam/beamMassT1.FEM")
    p = list(a.parts.values())[0]
    assert len(list(p.fem.elements.masses)) == 1
    assert len(list(p.fem.elements.shell)) == 4
    assert len(list(p.fem.elements.lines)) == 11

    cog = p.fem.elements.calc_cog()
    assert cog.tot_mass == pytest.approx(54093.9)
    assert cog.p[0] == pytest.approx(5.21773)
    assert cog.p[1] == pytest.approx(4.78227)
    assert cog.p[2] == pytest.approx(0.884281)

    assert len(p.fem.sections.lines) == 11
    p.fem.sections.merge_by_properties()
    assert len(p.fem.sections.lines) == 7
    assert len(p.materials) == 1


def test_sesam_xml(example_files):
    xml_file = (example_files / "fem_files/sesam/curved_plates.xml").resolve().absolute()
    _ = ada.from_genie_xml(xml_file)
