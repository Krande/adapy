import unittest

from ada import Assembly, Beam, Part
from ada.visualize.fem import get_edges_from_fem, get_faces_from_fem


class FemBeam(unittest.TestCase):
    def setUp(self) -> None:
        a = Assembly() / (Part("BeamFEM") / Beam("bm1", n1=[0, 0, 0], n2=[2, 0, 0], sec="IPE220", colour="red"))
        pfem = a.get_by_name("BeamFEM")
        pfem.gmsh.mesh(0.1)
        assert len(pfem.fem.elements) == 20
        self.pfem = pfem

    def test_beam_as_edges(self):
        _ = get_edges_from_fem(self.pfem.fem)

    def test_beam_as_faces(self):
        _ = get_faces_from_fem(self.pfem.fem)


if __name__ == "__main__":
    unittest.main()
