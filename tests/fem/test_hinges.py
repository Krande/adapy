import unittest

from ada import Assembly, Beam, Part
from ada.fem import Csys
from ada.fem.elements import HingeProp
from ada.fem.utils import convert_hinges_2_couplings


class HingeTests(unittest.TestCase):
    def test_simple_hinged_beam(self):
        bm = Beam("MyBeam", (0, 0, 0), (1, 0, 0), 'IPE400')
        bm.hinge_prop = HingeProp(bm.n1, [1, 2, 3, 4, 6], Csys("MyBeam_hinge"))
        p = Part("MyPart")
        a = Assembly() / p / [bm]
        p.fem = p.to_fem_obj(0.1)
        convert_hinges_2_couplings(p.fem)
        self.assertEqual(len(p.fem.constraints), 1)
        a.to_fem("MyHingedBeam", "abaqus", overwrite=True)


if __name__ == "__main__":
    unittest.main()
