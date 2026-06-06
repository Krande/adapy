"""The substrate-direct Sesam read must be byte-identical to the object-model read.

Loads the same Sesam deck with ``meshing_array_backed`` off (object Node/Elem) and on
(MeshArrays + proxies) and compares node coords, element connectivity, and section
thickness. Also checks the array path actually produced the array-backed containers.
"""

import pytest

import ada
from ada.config import Config


@pytest.fixture
def _restore_flag():
    yield
    Config().meshing_array_backed = False


def _load_fem(path):
    a = ada.from_fem(str(path))
    parts = [p for p in a.get_all_parts_in_assembly() if p.fem is not None and len(p.fem.nodes) > 0]
    return parts[0].fem


def _digest(fem):
    nodes = sorted((n.id, *[round(float(x), 6) for x in n.p]) for n in fem.nodes)
    shells = sorted((e.id, tuple(sorted(n.id for n in e.nodes))) for e in fem.elements.shell)
    lines = sorted((e.id, tuple(sorted(n.id for n in e.nodes))) for e in fem.elements.lines)
    secs = sorted(
        (e.id, round(float(getattr(e.fem_sec, "thickness", 0) or 0), 6))
        for e in fem.elements.shell
        if e.fem_sec is not None
    )
    return nodes, shells, lines, secs


def _parity(path):
    from ada.api.mesh.containers import ArrayNodes

    Config().meshing_array_backed = False
    obj = _digest(_load_fem(path))

    Config().meshing_array_backed = True
    arr_fem = _load_fem(path)
    assert isinstance(arr_fem.nodes, ArrayNodes)
    arr = _digest(arr_fem)

    assert obj[0] == arr[0], "node coords differ"
    assert obj[1] == arr[1], "shell connectivity differs"
    assert obj[2] == arr[2], "line connectivity differs"
    assert obj[3] == arr[3], "shell section thickness differs"


def test_sesam_array_read_beam_mass(fem_files, _restore_flag):
    # beam + mass deck -> exercises line elements + the mass overflow path
    _parity(fem_files / "sesam" / "beamMassT1.FEM")


def test_sesam_array_read_shell(tmp_path, _restore_flag):
    Config().meshing_array_backed = False
    pl = ada.Plate("pl", [(0, 0), (2, 0), (2, 1.5), (0, 1.5)], 0.02)
    p = ada.Part("p") / pl
    p.fem = pl.to_fem_obj(0.4, "shell")
    a = ada.Assembly("a") / p
    a.to_fem("m", fem_format="sesam", scratch_dir=tmp_path)
    _parity(tmp_path / "m" / "mT1.FEM")
