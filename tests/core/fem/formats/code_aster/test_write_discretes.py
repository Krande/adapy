"""No solver: springs, rotary inertias and non-structural masses in the Code_Aster deck (write_masses), and the MED
cells they need. Solved against closed forms in tests/fem/test_discretes_closed_form.py."""

from __future__ import annotations

import h5py
import numpy as np
import pytest

import ada
from ada.fem import Bc, FemSet, Mass, Spring, StepEigen
from ada.fem.formats import conversion_report
from ada.fem.formats.code_aster.write.write_masses import upper_by_columns
from ada.materials.metals import CarbonSteel


def _beam(name="P"):
    mat = ada.Material("S355", CarbonSteel("S355"))
    bm = ada.Beam("bm", (0, 0, 0), (2, 0, 0), "IPE300", mat)
    p = ada.Part(name) / bm
    p.fem = bm.to_fem_obj(0.5, "line")
    fem = p.fem
    (root,) = [n for n in fem.nodes if abs(n.x) < 1e-9]
    (tip,) = [n for n in fem.nodes if abs(n.x - 2) < 1e-9]
    fem.add_bc(Bc("clamp", fem.add_set(FemSet("root", [root], "nset")), [1, 2, 3, 4, 5, 6]))
    a = ada.Assembly("A") / p
    a.fem.add_step(StepEigen("eig", num_eigen_modes=3))
    return a, fem, root, tip


def _write(a, tmp_path, name="d"):
    with conversion_report.collect() as rep:
        a.to_fem(name, "code_aster", scratch_dir=tmp_path, overwrite=True)
    return (tmp_path / name / f"{name}.comm").read_text(), tmp_path / name / f"{name}.med", rep


def _med_blocks(med) -> dict[str, np.ndarray]:
    with h5py.File(med, "r") as f:
        mesh = f["ENS_MAA"][list(f["ENS_MAA"].keys())[0]]
        ts = mesh[list(mesh.keys())[0]]
        return {k: (v["NOD"][()], int(v["NOD"].attrs["NBR"])) for k, v in ts["MAI"].items()}


def test_upper_by_columns_is_code_asters_storage():
    k = np.arange(1, 10, dtype=float).reshape(3, 3)
    k = k + k.T
    assert upper_by_columns(k) == [k[0, 0], k[0, 1], k[1, 1], k[0, 2], k[1, 2], k[2, 2]]


def test_a_point_mass_and_a_grounded_spring_share_the_po1_block(tmp_path):
    """Both are POI1 cells: one MED block, which once raised "med_type PO1 is already defined"."""
    a, fem, _, tip = _beam()
    fem.add_mass(Mass("m", [tip], 10.0))
    fem.add_spring(Spring("sp", None, "SPRING1", np.diag([1e6] * 6), fem.add_set(FemSet("sp_n", [tip], "nset"))))
    comm, med, rep = _write(a, tmp_path)
    blocks = _med_blocks(med)
    assert blocks["PO1"][1] == 2
    assert "CARA='K_TR_N'" in comm and "MODELISATION='DIS_TR'" in comm
    assert not [f for f in rep.findings if f.keyword == "Spring"]


def test_a_two_node_spring_is_a_seg2_cell_beside_the_beams(tmp_path):
    """A SEG2 cell joining its two nodes, in the beams' SE2 block, with the 12 x 12 of its links in global axes."""
    a, fem, _, tip = _beam()
    other = fem.nodes.add(ada.Node((2.0, 0.0, -0.5), int(max(n.id for n in fem.nodes)) + 1))
    links = np.diag([1e6, 2e6, 3e6, 4e5, 5e5, 6e5])
    fem.add_spring(Spring("sp2", None, "SPRING2", links, fem.add_set(FemSet("sp2_n", [tip, other], "nset"))))
    comm, med, _ = _write(a, tmp_path)
    nod, n_cells = _med_blocks(med)["SE2"]
    assert n_cells == len(fem.elements.lines) + 1
    cells = nod.reshape(2, -1).T  # MED stores the connectivity column-major
    assert [int(tip.id), int(other.id)] in cells.tolist()
    (line,) = [ln for ln in comm.splitlines() if "CARA='K_TR_L'" in ln]
    assert "REPERE='GLOBAL'" in line and line.count(",") >= 78


@pytest.mark.parametrize(
    "units, kind", [("TOTAL", "TOTALE"), ("PER UNIT LENGTH", "LINEIQUE"), ("PER UNIT AREA", "SURFACIQUE")]
)
def test_a_non_structural_mass_is_spread_by_mass_rep_on_point_cells_of_its_region(tmp_path, units, kind):
    a, fem, _, _ = _beam()
    region = fem.add_set(FemSet("beams", list(fem.elements.lines), "elset"))
    fem.add_mass(Mass("ns", region, 12.5, mass_type="NONSTRUCTURAL", units=units))
    comm, med, _ = _write(a, tmp_path)
    assert f"GROUP_MA='beams', GROUP_MA_POI1='ns1', VALE=12.5, TYPE='{kind}'" in comm
    # one point cell on every node of the region, none for the mass's own pseudo-element
    assert _med_blocks(med)["PO1"][1] == len(fem.nodes)


def test_a_non_structural_mass_per_volume_is_refused_by_name(tmp_path):
    a, fem, _, _ = _beam()
    region = fem.add_set(FemSet("beams", list(fem.elements.lines), "elset"))
    fem.add_mass(Mass("ns", region, 12.5, mass_type="NONSTRUCTURAL", units="PER UNIT VOLUME"))
    comm, _, rep = _write(a, tmp_path)
    assert "MASS_REP" not in comm
    assert any(f.subject == "ns" and "no MASS_REP form" in f.reason for f in rep.findings)


def test_a_rotary_inertia_without_rotational_dofs_is_refused_by_name(tmp_path):
    """On a node of nothing that turns (here a bare node), its rotations would be free: left out and said so."""
    a, fem, _, _ = _beam()
    loose = fem.nodes.add(ada.Node((5.0, 5.0, 5.0), int(max(n.id for n in fem.nodes)) + 1))
    fem.add_mass(Mass("rot", [loose], [1.0, 2.0, 3.0, 0.0, 0.0, 0.0], mass_type="ROTARYI"))
    comm, _, rep = _write(a, tmp_path)
    assert "M_TR_N" not in comm
    assert any(f.subject == "rot" and "without rotational dofs" in f.reason for f in rep.findings)
