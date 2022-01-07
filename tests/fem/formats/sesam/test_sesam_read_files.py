import pytest

import ada


def test_read_beam(example_files):
    a = ada.from_fem(example_files / "fem_files/sesam/beamMassT1.FEM")
    p = a.get_part("T1")
    assert len(list(p.fem.elements.masses)) == 1
    assert len(list(p.fem.elements.shell)) == 4
    assert len(list(p.fem.elements.lines)) == 11

    cog = p.fem.elements.calc_cog()
    assert pytest.approx(cog.tot_mass, 54093.9)
    assert pytest.approx(cog.p[0], 5.21773)
    assert pytest.approx(cog.p[1], 4.78227)
    assert pytest.approx(cog.p[2], 0.884281)
