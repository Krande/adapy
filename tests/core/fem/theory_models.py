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


def icosphere(r: float, refine: int):
    """``(vertices, faces)`` of an icosphere of radius ``r``, faces wound with their normals outward."""
    import numpy as np

    t = (1 + 5**0.5) / 2
    verts = [(-1, t, 0), (1, t, 0), (-1, -t, 0), (1, -t, 0), (0, -1, t), (0, 1, t), (0, -1, -t), (0, 1, -t)]
    verts += [(t, 0, -1), (t, 0, 1), (-t, 0, -1), (-t, 0, 1)]
    verts = [np.array(v) / np.linalg.norm(v) for v in verts]
    faces = [
        (0, 11, 5), (0, 5, 1), (0, 1, 7), (0, 7, 10), (0, 10, 11), (1, 5, 9), (5, 11, 4), (11, 10, 2), (10, 7, 6),
        (7, 1, 8), (3, 9, 4), (3, 4, 2), (3, 2, 6), (3, 6, 8), (3, 8, 9), (4, 9, 5), (2, 4, 11), (6, 2, 10),
        (8, 6, 7), (9, 8, 1),
    ]  # fmt: skip
    for _ in range(refine):
        mid, new = {}, []

        def midpoint(i, j):
            key = (min(i, j), max(i, j))
            if key not in mid:
                m = verts[i] + verts[j]
                verts.append(m / np.linalg.norm(m))
                mid[key] = len(verts) - 1
            return mid[key]

        for a, b, c in faces:
            ab, bc, ca = midpoint(a, b), midpoint(b, c), midpoint(c, a)
            new += [(a, ab, ca), (b, bc, ab), (c, ca, bc), (ab, bc, ca)]
        faces = new
    return [r * v for v in verts], faces


def pressurised_sphere(r=1.0, refine=2, t=0.00045, pressure=400.0, patch_t=None, nl_geom=True):
    """``(assembly, part)``: a closed sphere of 3-node membranes under internal ``pressure``, held statically
    determinately (one node in x, y, z, one in y, z, one in z). With ``patch_t``, the triangles around the top pole
    are bending shells of that thickness, loaded by a pressure of their own."""
    from ada.fem import LoadPressure

    verts, faces = icosphere(r, refine)
    mat = material()
    p = ada.Part("sphere")
    fem = p.fem
    nodes = [fem.nodes.add(ada.Node(v, i + 1), allow_coincident=True) for i, v in enumerate(verts)]
    els = [fem.add_elem(Elem(i + 1, [nodes[k] for k in f], "TRIANGLE")) for i, f in enumerate(faces)]
    patch = [el for el in els if patch_t is not None and min(n.z for n in el.nodes) > 0.8 * r]
    skin = [el for el in els if el not in patch]
    regions = [("skin", skin, t, "membrane")]
    if patch:
        regions.append(("patch", patch, patch_t, "shell"))
    for name, members, thick, theory in regions:
        fs = fem.add_set(FemSet(name, members, "elset"))
        fem.add_section(FemSection(f"sec_{name}", "shell", fs, mat, thickness=thick, theory=theory))
    p.add_material(mat)
    a = ada.Assembly("a") / p
    # Rigid-body motion removed and nothing else held: the bottom node along x, y and z, the node furthest along x
    # along y and z (no turning about z or y), the node furthest along y along z (no turning about x).
    held = [min(nodes, key=lambda n: n.z), max(nodes, key=lambda n: n.x), max(nodes, key=lambda n: n.y)]
    for name, node, dofs in zip(("fix_a", "fix_b", "fix_c"), held, ([1, 2, 3], [2, 3], [3])):
        fem.add_bc(Bc(name, fem.add_set(FemSet(name, [node], "nset")), dofs))
    step = a.fem.add_step(StepImplicitStatic("press", nl_geom=nl_geom))
    # A positive pressure pushes into the face it names; the normals point out, so an internal pressure is negative.
    for name, *_ in regions:
        step.add_load(LoadPressure(f"p_{name}", -pressure, fem.elsets[name]))
    return a, p
