"""Substrate-direct Code_Aster MED read must match the object-model read."""

import pathlib

import pytest

import ada
from ada.config import Config


@pytest.fixture
def _restore_flag():
    prev = Config().meshing_array_backed
    yield
    Config().meshing_array_backed = prev


def _med(tmp_path):
    Config().meshing_array_backed = False
    pl = ada.Plate("pl", [(0, 0), (2, 0), (2, 1.5), (0, 1.5)], 0.02)
    p = ada.Part("p") / pl
    p.fem = pl.to_fem_obj(0.4, "shell")
    (ada.Assembly("a") / p).to_fem("m", fem_format="code_aster", scratch_dir=tmp_path)
    return next(pathlib.Path(tmp_path).rglob("*.med"))


def _digest(path):
    a = ada.from_fem(str(path))
    fem = [p for p in a.get_all_parts_in_assembly() if p.fem is not None and len(p.fem.nodes) > 0][0].fem
    nodes = sorted((int(n.id), *[round(float(x), 6) for x in n.p]) for n in fem.nodes)
    shells = sorted((int(e.id), tuple(sorted(int(n.id) for n in e.nodes))) for e in fem.elements.shell)
    return type(fem.nodes).__name__, nodes, shells


def test_med_array_read_parity(tmp_path, _restore_flag):
    med = _med(tmp_path)

    Config().meshing_array_backed = False
    obj = _digest(med)
    Config().meshing_array_backed = True
    arr = _digest(med)

    assert obj[0] == "Nodes" and arr[0] == "ArrayNodes"
    assert obj[1] == arr[1], "node coords differ"
    assert obj[2] == arr[2], "shell connectivity differs"
