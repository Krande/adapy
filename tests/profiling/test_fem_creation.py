import pytest

import ada


@pytest.mark.benchmark
def test_mesh_beam():
    bm = ada.Beam("bm1", (0, 0, 0), (1, 0, 0), "IPE300")
    p = ada.Part("MyPart") / bm
    p.fem = bm.to_fem_obj(0.01, "shell", use_quads=True)

    assert len(p.fem.elements) == 6200
