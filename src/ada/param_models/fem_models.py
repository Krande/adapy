import numpy as np

from ada import Assembly, Beam, Material, Part, PrimBox, PrimCyl, PrimExtrude, User
from ada.fem import Bc, FemSet, Load, StepImplicitStatic
from ada.fem.shapes import ElemType
from ada.fem.utils import get_beam_end_nodes
from ada.materials.metals import CarbonSteel, DnvGl16Mat


def add_random_cutouts(bm: Beam):
    h = 0.2
    r = 0.02
    normal = [0, 1, 0]
    xdir = [-1, 0, 0]

    # Polygon Extrusions
    origin = np.array([0.2, -0.1, -0.1])
    points = [(0, 0), (0.1, 0), (0.05, 0.1)]

    bm.add_boolean(PrimExtrude("Poly1", points, h, normal, origin, xdir))

    origin += np.array([0.2, 0, 0])
    points = [(0, 0, r), (0.1, 0, r), (0.05, 0.1, r)]

    bm.add_boolean(PrimExtrude("Poly2", points, h, normal, origin, xdir))

    origin += np.array([0.2, 0, 0])
    points = [(0, 0, r), (0.1, 0, r), (0.1, 0.2, r), (0.0, 0.2, r)]

    bm.add_boolean(PrimExtrude("Poly3", points, h, normal, origin, xdir))

    # Cylinder Extrude
    x = origin[0] + 0.2

    bm.add_boolean(PrimCyl("cylinder", (x, -0.1, 0), (x, 0.1, 0), 0.1))

    # Box Extrude
    x += 0.2

    bm.add_boolean(PrimBox("box", (x, -0.1, -0.1), (x + 0.2, 0.1, 0.1)))


def beam_ex1(p1=(0, 0, 0), p2=(1.5, 0, 0), profile="IPE400", geom_repr=ElemType.SHELL) -> Assembly:
    mat_grade = CarbonSteel.TYPES.S355
    bm = Beam("MyBeam", p1, p2, profile, Material("S355", mat_model=CarbonSteel(mat_grade)))
    bm.material.model.plasticity_model = DnvGl16Mat(bm.section.t_w, mat_grade)
    a = Assembly("Test", user=User("krande")) / [Part("MyPart") / bm]

    add_random_cutouts(bm)
    # Create a FEM analysis of the beam as a cantilever subjected to gravity loads
    p = a.get_part("MyPart")
    p.fem = bm.to_fem_obj(0.1, geom_repr)
    # Add a set containing ALL elements (necessary for Calculix loads).
    fs = p.fem.add_set(FemSet("Eall", [el for el in p.fem.elements], FemSet.TYPES.ELSET))

    step = a.fem.add_step(StepImplicitStatic("gravity", nl_geom=False, init_incr=100.0, total_time=100.0))
    step.add_load(Load("grav", Load.TYPES.GRAVITY, -9.81 * 800, fem_set=fs))

    fix_set = p.fem.add_set(FemSet("bc_nodes", get_beam_end_nodes(bm), FemSet.TYPES.NSET))
    a.fem.add_bc(Bc("Fixed", fix_set, [1, 2, 3]))
    return a
