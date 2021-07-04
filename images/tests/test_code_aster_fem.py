import unittest

import h5py
import numpy as np

from ada import Assembly, Beam, Material, Part
from ada.fem import Bc, FemSet, Step
from ada.fem.io.mesh.recipes import create_beam_mesh
from ada.fem.utils import get_beam_end_nodes
from ada.materials.metals import CarbonSteel


def fundamental_eigenfrequency(bm: Beam):
    L = bm.length
    E = bm.material.model.E
    rho = bm.material.model.rho
    I = bm.section.properties.Iy
    return (1 / (2 * np.pi)) * (3.5156 / L ** 2) * np.sqrt(E * I / rho)


def test_bm():
    bm = Beam("MyBeam", (0, 0.5, 0.5), (5, 0.5, 0.5), "IPE400", Material("S420", CarbonSteel("S420")))
    f1 = fundamental_eigenfrequency(bm)
    f2 = 6.268 * f1
    f3 = 17.456 * f1
    print(f"Fundamental Eigenfrequencies\n\n1: {f1}\n2: {f2}\n3: {f3}")
    return bm


class CodeAsterTests(unittest.TestCase):
    def test_bm_eigenfrequency_beam(self):
        bm = test_bm()

        p = Part("MyPart")
        a = Assembly("MyAssembly") / [p / bm]
        p.gmsh.mesh(0.1)

        fix_set = p.fem.add_set(FemSet("bc_nodes", get_beam_end_nodes(bm), "nset"))
        a.fem.add_bc(Bc("Fixed", fix_set, [1, 2, 3, 4, 5, 6]))

        a.fem.add_step(Step("Eigen", "eigenfrequency"))

        res = a.to_fem("Cantilever_CA_EIG_bm", "code_aster", overwrite=True, execute=True)

        f = h5py.File(res.results_file_path)
        modes = f.get("CHA/modes___DEPL")

        for mname, m in modes.items():
            mode = m.attrs["NDT"]
            freq = m.attrs["PDT"]

            print(mode, freq)

    def test_bm_eigenfrequency_shell(self):
        bm = test_bm()

        p = Part("MyPart")
        a = Assembly("MyAssembly") / [p / bm]
        create_beam_mesh(bm, p.fem, "shell")

        fix_set = p.fem.add_set(FemSet("bc_nodes", get_beam_end_nodes(bm), "nset"))
        a.fem.add_bc(Bc("Fixed", fix_set, [1, 2, 3, 4, 5, 6]))

        a.fem.add_step(Step("Eigen", "eigenfrequency"))

        res = a.to_fem("Cantilever_CA_EIG_sh", "code_aster", overwrite=True, execute=True)

        f = h5py.File(res.results_file_path)
        modes = f.get("CHA/modes___DEPL")

        for mname, m in modes.items():
            mode = m.attrs["NDT"]
            freq = m.attrs["PDT"]

            print(mode, freq)


if __name__ == "__main__":
    unittest.main()
