"""Small FEM models of membranes, trusses and cables, built element by element, for the writer and solver tests.

* :func:`membrane_square` -- a flat ``a x b`` square of 4-node membranes (or bending shells) in the x-y plane.
* :func:`two_bar_truss` -- two bars at 45 degrees from pinned supports at x = 0 and 2 m to an apex at (1, 0, 1).
* :func:`cable_and_truss` -- a cable from a support at x = 0 and a truss to a support at x = 2 m, meeting at a node at
  x = 1 m loaded along x: pulled towards the truss's support the cable is in tension, pushed the other way it goes
  slack and the truss carries the load alone.
"""

from __future__ import annotations

import math

import ada
from ada.fem import Bc, Elem, FemSection, FemSet, LoadPoint, StepImplicitStatic
from ada.materials.metals import CarbonSteel

E = 2.1e11
NU = 0.3
#: Rod radius of the truss and cable sections, m.
ROD_R = 0.01
ROD_AREA = math.pi * ROD_R**2


def material() -> ada.Material:
    return ada.Material("mat", CarbonSteel("S355"))


def membrane_square(nx=4, ny=4, a=1.0, b=1.0, t=0.002, theory="membrane", pretension=0.0):
    """``(assembly, part, nodes)``; ``nodes[i, j]`` is the node at ``(a i / nx, b j / ny, 0)``. The elements wind
    counter-clockwise seen from +z, so their normal is +z."""
    mat = material()
    p = ada.Part("memb")
    fem = p.fem
    nodes, nid = {}, 1
    for j in range(ny + 1):
        for i in range(nx + 1):
            nodes[i, j] = fem.nodes.add(ada.Node((a * i / nx, b * j / ny, 0.0), nid))
            nid += 1
    els, eid = [], 1
    for j in range(ny):
        for i in range(nx):
            quad = [nodes[i, j], nodes[i + 1, j], nodes[i + 1, j + 1], nodes[i, j + 1]]
            els.append(fem.add_elem(Elem(eid, quad, "QUAD")))
            eid += 1
    fs = fem.add_set(FemSet("membrane", els, "elset"))
    kw = dict(pretension=pretension) if pretension else {}
    fem.add_section(FemSection("msec", "shell", fs, mat, thickness=t, theory=theory, **kw))
    p.add_material(mat)
    return ada.Assembly("a") / p, p, nodes


def membrane_in_tension(nx=4, ny=4, force=1000.0, nl_geom=False, theory="membrane"):
    """The unit square pulled along x by ``force`` spread over its x = 1 edge, held at x = 0 along x and at the
    origin along y, every node held along z. A uniform plane-stress state: ``sigma_xx = force / (b t)``."""
    a, p, nodes = membrane_square(nx, ny, theory=theory)
    fem = p.fem
    fem.add_bc(Bc("fix_x", fem.add_set(FemSet("left", [nodes[0, j] for j in range(ny + 1)], "nset")), [1]))
    fem.add_bc(Bc("fix_y", fem.add_set(FemSet("corner", [nodes[0, 0]], "nset")), [2]))
    fem.add_bc(Bc("fix_z", fem.add_set(FemSet("all_n", list(nodes.values()), "nset")), [3]))
    step = a.fem.add_step(StepImplicitStatic("tension", nl_geom=nl_geom))
    for j in range(ny + 1):
        share = force / ny * (0.5 if j in (0, ny) else 1.0)
        step.add_load(LoadPoint(f"f{j}", share, fem.add_set(FemSet(f"r{j}", [nodes[nx, j]], "nset")), 1))
    return a, p, nodes


def rod() -> ada.Section:
    return ada.Section("rod", "CIRC", r=ROD_R)


def two_bar_truss(theory="truss", fz=1000.0, pretension=0.0):
    """``(assembly, part, (n1, n2, n3))``: pinned at n1 and n3, apex n2 held along y and loaded ``fz`` along z."""
    mat = material()
    p = ada.Part("truss")
    fem = p.fem
    n1 = fem.nodes.add(ada.Node((0, 0, 0), 1))
    n2 = fem.nodes.add(ada.Node((1, 0, 1), 2))
    n3 = fem.nodes.add(ada.Node((2, 0, 0), 3))
    e1 = fem.add_elem(Elem(1, [n1, n2], "LINE"))
    e2 = fem.add_elem(Elem(2, [n2, n3], "LINE"))
    fs = fem.add_set(FemSet("bars", [e1, e2], "elset"))
    kw = dict(pretension=pretension) if pretension else {}
    fem.add_section(FemSection("tsec", "line", fs, mat, section=rod(), theory=theory, **kw))
    p.add_material(mat)
    a = ada.Assembly("a") / p
    fem.add_bc(Bc("pins", fem.add_set(FemSet("ends", [n1, n3], "nset")), [1, 2, 3]))
    fem.add_bc(Bc("apex_y", fem.add_set(FemSet("apex", [n2], "nset")), [2]))
    step = a.fem.add_step(StepImplicitStatic("load"))
    step.add_load(LoadPoint("p", fz, fem.nsets["apex"], 3))
    return a, p, (n1, n2, n3)


def cable_and_truss(fx: float, pretension=0.0, compression_ratio=1e-4):
    """``(assembly, part)``: support -- cable -- node 2 -- truss -- support, along x, node 2 loaded ``fx``."""
    mat = material()
    p = ada.Part("slack")
    fem = p.fem
    n1 = fem.nodes.add(ada.Node((0, 0, 0), 1))
    n2 = fem.nodes.add(ada.Node((1, 0, 0), 2))
    n3 = fem.nodes.add(ada.Node((2, 0, 0), 3))
    e1 = fem.add_elem(Elem(1, [n1, n2], "LINE"))
    e2 = fem.add_elem(Elem(2, [n2, n3], "LINE"))
    cable = fem.add_set(FemSet("cable", [e1], "elset"))
    truss = fem.add_set(FemSet("truss", [e2], "elset"))
    fem.add_section(
        FemSection(
            "cab",
            "line",
            cable,
            mat,
            section=rod(),
            theory="cable",
            pretension=pretension,
            compression_ratio=compression_ratio,
        )
    )
    fem.add_section(FemSection("bar", "line", truss, mat, section=rod(), theory="truss"))
    p.add_material(mat)
    a = ada.Assembly("a") / p
    fem.add_bc(Bc("ends", fem.add_set(FemSet("ends", [n1, n3], "nset")), [1, 2, 3]))
    fem.add_bc(Bc("lateral", fem.add_set(FemSet("mid", [n2], "nset")), [2, 3]))
    step = a.fem.add_step(StepImplicitStatic("load"))
    step.add_load(LoadPoint("p", fx, fem.nsets["mid"], 1))
    return a, p


def closed_box(a=2.0, b=3.0, c=4.0, theory="membrane"):
    """``(assembly, part, elset)``: the six faces of an ``a x b x c`` box as 4-node shells, normals outward."""
    mat = material()
    p = ada.Part("box")
    fem = p.fem
    pts = [(0, 0, 0), (a, 0, 0), (a, b, 0), (0, b, 0), (0, 0, c), (a, 0, c), (a, b, c), (0, b, c)]
    nodes = [fem.nodes.add(ada.Node(q, i + 1)) for i, q in enumerate(pts)]
    faces = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    els = [fem.add_elem(Elem(i + 1, [nodes[k] for k in f], "QUAD")) for i, f in enumerate(faces)]
    fs = fem.add_set(FemSet("box", els, "elset"))
    fem.add_section(FemSection("skin", "shell", fs, mat, thickness=0.001, theory=theory))
    p.add_material(mat)
    return ada.Assembly("a") / p, p, fs
