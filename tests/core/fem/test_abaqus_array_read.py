"""Substrate-direct Abaqus read must be byte-identical to the object-model read."""

import pathlib

import pytest

import ada
from ada.config import Config


@pytest.fixture
def _restore_flag():
    prev = Config().meshing_array_backed
    yield
    Config().meshing_array_backed = prev


def _abaqus_inp(tmp_path):
    Config().meshing_array_backed = False
    pl = ada.Plate("pl", [(0, 0), (2, 0), (2, 1.5), (0, 1.5)], 0.02)
    p = ada.Part("p") / pl
    p.fem = pl.to_fem_obj(0.4, "shell")
    (ada.Assembly("a") / p).to_fem("m", fem_format="abaqus", scratch_dir=tmp_path)
    return next(pathlib.Path(tmp_path).rglob("*.inp"))


def _digest(path):
    a = ada.from_fem(str(path))
    fem = [p for p in a.get_all_parts_in_assembly() if p.fem is not None and len(p.fem.nodes) > 0][0].fem
    nodes = sorted((int(n.id), *[round(float(x), 6) for x in n.p]) for n in fem.nodes)
    shells = sorted((int(e.id), tuple(sorted(int(n.id) for n in e.nodes))) for e in fem.elements.shell)
    return type(fem.nodes).__name__, nodes, shells


def test_abaqus_array_read_parity(tmp_path, _restore_flag):
    inp = _abaqus_inp(tmp_path)

    Config().meshing_array_backed = False
    obj = _digest(inp)
    Config().meshing_array_backed = True
    arr = _digest(inp)

    assert obj[0] == "Nodes" and arr[0] == "ArrayNodes"
    assert obj[1] == arr[1], "node coords differ"
    assert obj[2] == arr[2], "shell connectivity differs"


def test_abaqus_read_cax4p_continuation_and_named_bc(example_files):
    """UUea.inp exercises three abaqus reader gaps that all used to abort the import:
    CAX4P (axisymmetric pore-pressure quad) element type, a connectivity line continued
    onto the next line (element 10), and a named symmetry BC (YSYMM)."""
    a = ada.from_fem(str(example_files / "fem_files/abaqus/UUea.inp"))
    fem = [p for p in a.get_all_parts_in_assembly() if p.fem is not None and len(p.fem.elements) > 0][0].fem

    # 50 CAX4P quads imported (the _PickedSet2 generate 1,50,1 range resolves).
    assert len(fem.elements) == 50
    # The continued element (10, 11, 12, 18, \n 17) parsed as a single 4-node quad.
    elem10 = fem.elements.from_id(10)
    assert sorted(n.id for n in elem10.nodes) == [11, 12, 17, 18]

    # YSYMM named restraint -> U2, UR1, UR3 fixed (dofs 2, 4, 6). Assembly-level *Boundary
    # cards land on the assembly fem.
    bcs = list(a.fem.bcs) + [b for p in a.get_all_parts_in_assembly() if p.fem for b in p.fem.bcs]
    assert bcs, "no boundary conditions parsed"
    assert any(set(d for d in bc.dofs if d) == {2, 4, 6} for bc in bcs if isinstance(bc.dofs, list))


def test_material_without_elastic_keeps_model_defaults(example_files):
    """A *Material with no *Elastic / *Density used to carry E=rho=v=None, which crashed
    every downstream writer on float(None). The reader now leaves CarbonSteel's defaults."""
    a = ada.from_fem(str(example_files / "fem_files/abaqus/UUea.inp"))
    mats = [m for p in a.get_all_parts_in_assembly(include_self=True) for m in p.materials]
    assert mats, "no materials read"
    for m in mats:
        assert m.model.E is not None and m.model.rho is not None and m.model.v is not None


def test_med_export_with_part_and_assembly_nsets(example_files, tmp_path):
    """box_rigid has both part- and assembly-level node sets; the MED writer used to create
    the 'FAM' dataset twice (name-already-exists) and choke on None-padded BC dofs."""
    a = ada.from_fem(str(example_files / "fem_files/abaqus/box_rigid.inp"))
    a.to_fem("box_rigid_med", fem_format="code_aster", scratch_dir=tmp_path, overwrite=True)
    assert next(tmp_path.rglob("*.med"), None) is not None
