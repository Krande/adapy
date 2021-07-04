import unittest
import numpy as np
from ada import Assembly, Beam, Part, PrimBox, PrimCyl, PrimExtrude, Material, CarbonSteel
from ada.fem import Bc, FemSet, Load, Step
from ada.fem.io.mesh.recipes import create_beam_mesh
from ada.fem.utils import get_beam_end_nodes



class CodeAsterTests(unittest.TestCase):
    def test_eigenfrequency(self):
        bm = Beam("MyBeam", (0, 0.5, 0.5), (1.5, 0.5, 0.5), "IPE400", Material('S420', CarbonSteel("S420")))
        p = Part("MyPart")
        a = Assembly("MyAssembly") / [p / bm]
        p.gmsh.mesh(0.1)

        fix_set = p.fem.add_set(FemSet("bc_nodes", get_beam_end_nodes(bm), "nset"))
        a.fem.add_bc(Bc("Fixed", fix_set, [1, 2, 3, 4, 5, 6]))

        a.fem.add_step(Step("Eigen", "eigenfrequency"))

        a.to_fem('Cantilever_CA_EIG', 'code_aster', overwrite=True)

if __name__ == '__main__':
    unittest.main()
