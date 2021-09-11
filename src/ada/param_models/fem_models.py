import numpy as np

from ada import Assembly, Beam, Material, Part, PrimBox, PrimCyl, PrimExtrude, User
from ada.fem import Bc, FemSet, Load, Step
from ada.fem.meshing.gmshapiv2 import GmshSession
from ada.fem.shapes import ElemType
from ada.fem.utils import get_beam_end_nodes


def add_random_cutouts(bm: Beam):
    h = 0.2
    r = 0.02
    normal = [0, 1, 0]
    xdir = [-1, 0, 0]

    # Polygon Extrusions
    origin = np.array([0.2, -0.1, -0.1])
    points = [(0, 0), (0.1, 0), (0.05, 0.1)]

    bm.add_penetration(PrimExtrude("Poly1", points, h, normal, origin, xdir))

    origin += np.array([0.2, 0, 0])
    points = [(0, 0, r), (0.1, 0, r), (0.05, 0.1, r)]

    bm.add_penetration(PrimExtrude("Poly2", points, h, normal, origin, xdir))

    origin += np.array([0.2, 0, 0])
    points = [(0, 0, r), (0.1, 0, r), (0.1, 0.2, r), (0.0, 0.2, r)]

    bm.add_penetration(PrimExtrude("Poly3", points, h, normal, origin, xdir))

    # Cylinder Extrude
    x = origin[0] + 0.2

    bm.add_penetration(PrimCyl("cylinder", (x, -0.1, 0), (x, 0.1, 0), 0.1))

    # Box Extrude
    x += 0.2

    bm.add_penetration(PrimBox("box", (x, -0.1, -0.1), (x + 0.2, 0.1, 0.1)))


def beam_ex1(p1=(0, 0, 0), p2=(1.5, 0, 0), profile="IPE400", geom_repr=ElemType.SHELL) -> Assembly:
    bm = Beam("MyBeam", p1, p2, profile, Material("S355"))
    a = Assembly("Test", user=User("krande")) / [Part("MyPart") / bm]

    add_random_cutouts(bm)
    # Create a FEM analysis of the beam as a cantilever subjected to gravity loads
    p = a.get_part("MyPart")

    with GmshSession(silent=True) as gs:
        gs.add_obj(bm, geom_repr)
        gs.mesh(0.1)
        p.fem = gs.get_fem()

    # Add a set containing ALL elements (necessary for Calculix loads).
    fs = p.fem.add_set(FemSet("Eall", [el for el in p.fem.elements], FemSet.TYPES.ELSET))

    step = a.fem.add_step(Step("gravity", Step.TYPES.STATIC, nl_geom=True, init_incr=100.0, total_time=100.0))
    step.add_load(Load("grav", Load.TYPES.GRAVITY, -9.81 * 800, fem_set=fs))

    fix_set = p.fem.add_set(FemSet("bc_nodes", get_beam_end_nodes(bm), FemSet.TYPES.NSET))
    a.fem.add_bc(Bc("Fixed", fix_set, [1, 2, 3]))
    return a
