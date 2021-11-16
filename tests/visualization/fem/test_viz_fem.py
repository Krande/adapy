import pytest

from ada import Assembly, Beam, Part
from ada.fem.meshing import GmshSession
from ada.visualize.femviz import get_edges_from_fem, get_faces_from_fem


@pytest.fixture
def pfem():
    a = Assembly() / (Part("BeamFEM") / Beam("bm1", n1=[0, 0, 0], n2=[2, 0, 0], sec="IPE220", colour="red"))
    part = a.get_part("BeamFEM")
    with GmshSession(silent=True) as gs:
        gs.add_obj(a.get_by_name("bm1"), geom_repr="line")
        gs.mesh(0.1)
        part.fem = gs.get_fem()
    return part


def test_beam_as_edges(pfem):
    assert len(pfem.fem.elements) == 20
    _ = get_edges_from_fem(pfem.fem)


def test_beam_as_faces(pfem):
    _ = get_faces_from_fem(pfem.fem)
