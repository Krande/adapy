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


def test_read_varying_offset(example_files):
    a = ada.from_fem(example_files / "fem_files/sesam/varying_offset/varyingOffsetTypeT1.FEM")
    a.create_objects_from_fem()
    beams = list(a.get_all_physical_objects())
    assert len(beams) == 3

    ecc = ada.Direction(0, 0, -0.05)
    for bm in beams:
        assert bm.e1.is_equal(ecc)
