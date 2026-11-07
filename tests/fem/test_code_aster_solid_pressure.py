"""A pressure on solid elements, solved by Code_Aster (and Sestra, where installed) against closed forms.

Code_Aster loads a solid face with ``PRES_REP`` on face cells of the mesh, which adapy's model has none of; the writer
adds one per loaded face, oriented out of the solid (``ada.fem.formats.code_aster.write.write_skins``). Two checks with
exact answers, so a wrong face, a wrong orientation or a lost cell shows as a wrong number:

* **Uniaxial column.** An ``a x b x L`` block pressed by ``p`` on its top face, held only against rigid motion (the
  bottom face along z, one corner in x and y, the next corner in y), so it may spread sideways: the stress is
  ``-p`` along z everywhere and the top settles by ``p L / E`` and the sides spread by ``nu p a / E`` -- reproduced
  exactly by linear hexahedra, on any mesh.
* **Varying pressure on a side.** ``p = p0 + g x`` on a side face of a solid cantilever: the reactions sum to the
  pressure's integral and their moment about the root to its first moment, as for a shell.
"""

from __future__ import annotations

import numpy as np
import pytest

import ada
from ada.fem import Bc, Elem, FemSection, FemSet, LoadPressure, StepImplicitStatic
from ada.fem.shapes.definitions import SolidShapes
from ada.fem.surfaces import get_surface_from_nodes_on_solid_elements
from ada.materials.metals import CarbonSteel

E, NU = 2.1e11, 0.3


#: Each grid cell (corners 0-3 at its bottom, counter-clockwise from above, 4-7 above them) as elements of a shape,
#: corner slots of the cell. The wedges stand on the cell's two bottom triangles; the tetrahedra are its six around
#: the 0-6 diagonal (the Kuhn split, conforming from cell to cell), each turned to a positive volume in ``_block``.
_CELL_SPLIT = {
    SolidShapes.HEX8: [(0, 1, 2, 3, 4, 5, 6, 7)],
    SolidShapes.WEDGE: [(0, 1, 2, 4, 5, 6), (0, 2, 3, 4, 6, 7)],
    SolidShapes.TETRA: [(0, 1, 2, 6), (0, 2, 3, 6), (0, 3, 7, 6), (0, 7, 4, 6), (0, 4, 5, 6), (0, 5, 1, 6)],
}
#: The quadratic shapes: their linear one, and the edges (corner slot pairs) of their mid-side nodes in adapy's
#: (Abaqus's) node order.
_QUADRATIC = {
    SolidShapes.HEX20: (
        SolidShapes.HEX8,
        [(0, 1), (1, 2), (2, 3), (3, 0), (4, 5), (5, 6), (6, 7), (7, 4), (0, 4), (1, 5), (2, 6), (3, 7)],
    ),
    SolidShapes.WEDGE15: (SolidShapes.WEDGE, [(0, 1), (1, 2), (2, 0), (3, 4), (4, 5), (5, 3), (0, 3), (1, 4), (2, 5)]),
    SolidShapes.TETRA10: (SolidShapes.TETRA, [(0, 1), (1, 2), (2, 0), (0, 3), (1, 3), (2, 3)]),
}


def _block(nx, ny, nz, a, b, L, name="col", shape=SolidShapes.HEX8):
    """``(assembly, part, nodes)``: a block of ``shape`` elements on ``[0, a] x [0, b] x [0, L]``. ``nodes`` maps the
    grid's ``(i, j, k)`` to its corner nodes and, for a quadratic shape, ``("mid", n1.id, n2.id)`` to its mid-side
    ones."""
    mat = ada.Material("mat", CarbonSteel("S355", E=E, v=NU))
    p = ada.Part(name)
    fem = p.fem
    nodes, nid = {}, 1
    for k in range(nz + 1):
        for j in range(ny + 1):
            for i in range(nx + 1):
                nodes[i, j, k] = fem.nodes.add(ada.Node((a * i / nx, b * j / ny, L * k / nz), nid))
                nid += 1
    linear, edges = _QUADRATIC.get(shape, (shape, None))

    def mid(n1, n2):
        nonlocal nid
        key = ("mid", *sorted((n1.id, n2.id)))
        if key not in nodes:
            nodes[key] = fem.nodes.add(ada.Node(tuple((np.asarray(n1.p) + np.asarray(n2.p)) / 2), nid))
            nid += 1
        return nodes[key]

    els, eid = [], 1
    for k in range(nz):
        for j in range(ny):
            for i in range(nx):
                cell = [nodes[i + di, j + dj, k + dk] for dk in (0, 1) for di, dj in ((0, 0), (1, 0), (1, 1), (0, 1))]
                for slots in _CELL_SPLIT[linear]:
                    corners = [cell[s] for s in slots]
                    if linear == SolidShapes.TETRA:
                        c = [np.asarray(n.p, float) for n in corners]
                        if np.linalg.det(np.array([c[1] - c[0], c[2] - c[0], c[3] - c[0]])) < 0:
                            corners[1], corners[2] = corners[2], corners[1]
                    el_nodes = corners + [mid(corners[s], corners[t]) for s, t in edges or ()]
                    els.append(fem.add_elem(Elem(eid, el_nodes, shape)))
                    eid += 1
    fs = fem.add_set(FemSet("solid", els, "elset"))
    fem.add_section(FemSection("sec", "solid", fs, mat))
    p.add_material(mat)
    return ada.Assembly("a") / p, p, nodes


def _face_surface(p, name, face_nodes):
    fem = p.fem
    # every element with a whole face among face_nodes (the helper picks the face out by its corners)
    surf = get_surface_from_nodes_on_solid_elements(name, list(fem.elements), face_nodes, fem, True)
    return fem.add_surface(surf)


def _field(res, key):
    return next(np.asarray(r.values, float) for r in res.results if key in r.name)


def _case_fields(res):
    """``(displacement, reaction)`` as ``{node id: (x, y, z)}``, read the same for every solver (``FieldRole``)."""
    ((_, fields),) = res.case_fields("nodal_displacement", "nodal_reaction").items()
    out = []
    for role in ("nodal_displacement", "nodal_reaction"):
        (f,) = [f for f in fields if getattr(f.role, "value", f.role) == role]
        v = np.asarray(f.values, float)
        # the role's layout, [id, x, y, z, rx, ry, rz]; its component labels are each format's own
        out.append({int(r[0]): r[1:4] for r in v})
    return out


_COLUMNS = [
    pytest.param(SolidShapes.HEX8, (1, 1, 1), id="one_element"),
    pytest.param(SolidShapes.HEX8, (2, 3, 4), id="2x3x4"),
    # the other side tables, each on a mesh whose top face is several elements' faces
    *(pytest.param(s, (2, 2, 2), id=f"{s.value.lower()}-2x2x2") for s in _CELL_SPLIT if s != SolidShapes.HEX8),
    *(pytest.param(s, (2, 2, 2), id=f"{s.value.lower()}-2x2x2") for s in _QUADRATIC),
]


@pytest.mark.parametrize("solver", ["code_aster", "sesam"])
@pytest.mark.parametrize("shape, mesh", _COLUMNS)
def test_a_column_pressed_on_its_top_face_settles_by_pL_over_E(tmp_path, require_solver, shape, mesh, solver):
    """On Sestra this is the check of the solid side tables (solid_sides): the top face named by its Abaqus number has
    to reach Sestra as the side with those nodes, pushing into the element, or the field is wrong. The field is linear,
    so every element shape reproduces it exactly, quadratic ones only if the side's mid-side loads are consistent."""
    require_solver(solver)
    nx, ny, nz = mesh
    a_, b_, L, pressure = 0.5, 0.8, 2.0, 1.0e7
    a, p, nodes = _block(nx, ny, nz, a_, b_, L, shape=shape)
    fem = p.fem
    bottom = [n for n in fem.nodes if abs(n.p[2]) < 1e-9]
    top = [n for n in fem.nodes if abs(n.p[2] - L) < 1e-9]
    fem.add_bc(Bc("bz", fem.add_set(FemSet("bottom", bottom, "nset")), [3]))
    fem.add_bc(Bc("bxy", fem.add_set(FemSet("c0", [nodes[0, 0, 0]], "nset")), [1, 2]))
    fem.add_bc(Bc("by", fem.add_set(FemSet("c1", [nodes[nx, 0, 0]], "nset")), [2]))
    step = a.fem.add_step(StepImplicitStatic("press"))
    step.add_load(LoadPressure("p_top", pressure, _face_surface(p, "top", top)))

    res = a.to_fem("col", solver, scratch_dir=tmp_path, overwrite=True, execute=True, lazy=True)
    u, reac = _case_fields(res)
    for n in fem.nodes:
        x, y, z = n.p
        # the exact uniaxial field: w = -p z / E, u = nu p x / E, v = nu p y / E
        want = (NU * pressure * x / E, NU * pressure * y / E, -pressure * z / E)
        # Sestra's SIN holds single precision: 1e-6 of the settlement there, 1e-12 in Code_Aster's MED
        tol = (1e-6 if solver == "sesam" else 1e-12) * pressure / E * L
        assert u[int(n.id)] == pytest.approx(want, abs=tol), (int(n.id), tuple(n.p))
    rel = 1e-6 if solver == "sesam" else 1e-9
    assert sum(r[2] for r in reac.values()) == pytest.approx(pressure * a_ * b_, rel=rel)


def test_a_varying_pressure_on_a_solid_side_balances_its_force_and_first_moment(tmp_path, require_solver):
    """``p = 1e5 + 5e4 x`` on the y = 0 face of a 4 x 0.4 x 0.5 m cantilever fixed at x = 0: the y reactions sum to
    ``b_L (p0 L + g L^2 / 2)`` -- the face is 4 m long and 0.5 m high -- and their x-moment centre is the field's
    centroid, ``(p0 L^2 / 2 + g L^3 / 3) / (p0 L + g L^2 / 2)``."""
    require_solver("code_aster")
    nx, ny, nz, L, b_, h = 8, 2, 2, 4.0, 0.4, 0.5
    a, p, nodes = _block(nx, ny, nz, L, b_, h, name="cant")
    fem = p.fem
    root = [n for (i, j, k), n in nodes.items() if i == 0]
    side = [n for (i, j, k), n in nodes.items() if j == 0]
    fem.add_bc(Bc("root", fem.add_set(FemSet("root", root, "nset")), [1, 2, 3]))
    p0, g = 1.0e5, 5.0e4
    step = a.fem.add_step(StepImplicitStatic("side"))
    step.add_load(LoadPressure("p_side", p0, _face_surface(p, "side", side), gradient=(g, 0.0, 0.0)))

    res = a.to_fem("cant", "code_aster", scratch_dir=tmp_path, overwrite=True, execute=True)
    reac = _field(res, "REAC_NODA")
    total = h * (p0 * L + g * L**2 / 2)
    # the pressure pushes into the y = 0 face, i.e. along +y; the root holds it back along -y
    assert np.nansum(reac[:, 2]) == pytest.approx(-total, rel=1e-9)
    # moment about the root's z axis of the applied load, balanced by the root reactions' own moment
    xs = {int(n.id): n.p for n in fem.nodes}
    m_reac = sum(xs[int(r[0])][0] * r[2] - xs[int(r[0])][1] * r[1] for r in reac if int(r[0]) in xs)
    m_reac += np.nansum(reac[:, 6]) if reac.shape[1] > 6 else 0.0
    centroid = (p0 * L**2 / 2 + g * L**3 / 3) / (p0 * L + g * L**2 / 2)
    assert -m_reac == pytest.approx(total * centroid, rel=1e-6)


def test_a_solid_face_pressure_reaches_the_sesam_deck_as_its_side(tmp_path):
    """No solver: the top face of a HEX8 (Abaqus S2, nodes 5-8-7-6) is LHEX side 1 (5 6 7 8): SIDE 100000, four
    intensities, positive (into the element)."""
    a, p, nodes = _block(1, 1, 1, 1.0, 1.0, 1.0)
    fem = p.fem
    top = [n for (i, j, k), n in nodes.items() if k == 1]
    fem.add_bc(
        Bc("bz", fem.add_set(FemSet("bottom", [n for (i, j, k), n in nodes.items() if k == 0], "nset")), [1, 2, 3])
    )
    step = a.fem.add_step(StepImplicitStatic("press"))
    step.add_load(LoadPressure("p_top", 1000.0, _face_surface(p, "top", top)))
    a.to_fem("deck", "sesam", scratch_dir=tmp_path, overwrite=True)
    deck = next((tmp_path / "deck").glob("*T1.FEM")).read_text().splitlines()
    (i,) = [k for k, line in enumerate(deck) if line.startswith("BEUSLO")]
    elno, ndof, intno, side = (float(x) for x in deck[i + 1].split())
    assert (ndof, intno, side) == (4.0, 0.0, 100000.0)
    assert [float(x) for x in deck[i + 2].split()] == [1000.0] * 4


def test_a_quadratic_side_s_intensities_run_on_four_to_a_line(tmp_path):
    """No solver: the top of a HEX20 is IHEX side 6, eight nodes, so eight intensities -- on two lines of four. One
    line of eight is what Sestra refused ("BEUSLO card is too short for dof count given in NDOF field")."""
    a, p, nodes = _block(1, 1, 1, 1.0, 1.0, 1.0, shape=SolidShapes.HEX20)
    fem = p.fem
    fem.add_bc(Bc("bz", fem.add_set(FemSet("bottom", [n for n in fem.nodes if n.p[2] == 0.0], "nset")), [1, 2, 3]))
    step = a.fem.add_step(StepImplicitStatic("press"))
    step.add_load(LoadPressure("p_top", 1000.0, _face_surface(p, "top", [n for n in fem.nodes if n.p[2] == 1.0])))
    a.to_fem("deck", "sesam", scratch_dir=tmp_path, overwrite=True)
    deck = next((tmp_path / "deck").glob("*T1.FEM")).read_text().splitlines()
    (i,) = [k for k, line in enumerate(deck) if line.startswith("BEUSLO")]
    elno, ndof, intno, side = (float(x) for x in deck[i + 1].split())
    assert (ndof, side) == (8.0, 1.0)
    assert [float(x) for x in deck[i + 2].split()] == [1000.0] * 4
    assert [float(x) for x in deck[i + 3].split()] == [1000.0] * 4


def test_the_face_cells_written_for_a_solid_pressure_point_out_of_it(tmp_path):
    """No solver: the deck. One QUAD8 per loaded HEX20 face would need HEX20; on HEX8 one QUAD4 per top face, in a
    group of its own modelled as the solids are, loaded by PRES_REP."""
    a, p, nodes = _block(2, 2, 1, 1.0, 1.0, 1.0)
    fem = p.fem
    top = [n for (i, j, k), n in nodes.items() if k == 1]
    fem.add_bc(
        Bc("bz", fem.add_set(FemSet("bottom", [n for (i, j, k), n in nodes.items() if k == 0], "nset")), [1, 2, 3])
    )
    step = a.fem.add_step(StepImplicitStatic("press"))
    step.add_load(LoadPressure("p_top", 1000.0, _face_surface(p, "top", top)))
    a.to_fem("deck", "code_aster", scratch_dir=tmp_path, overwrite=True)
    comm = (tmp_path / "deck" / "deck.comm").read_text()
    # one group of face cells for the load, however many element sets its surface has (here one per element)
    assert "sk_sets = ('sk1',)" in comm and "GROUP_MA=sk_sets" in comm
    assert "PRES_REP=(\n        _F(GROUP_MA='sk1', PRES=1000.0)," in comm
    # the model itself is untouched: no face cell was added to it
    assert len(fem.elements) == 4 and "sk1" not in fem.elsets

    import h5py

    with h5py.File(tmp_path / "deck" / "deck.med", "r") as f:
        m = f["ENS_MAA"][list(f["ENS_MAA"].keys())[0]]
        ts = m[list(m.keys())[0]]
        xyz = ts["NOE/COO"][()].reshape(3, -1).T
        nums = ts["NOE/NUM"][()] if "NUM" in ts["NOE"] else np.arange(1, len(xyz) + 1)
        by_id = dict(zip(nums.tolist(), xyz))
        quads = ts["MAI/QU4/NOD"][()].reshape(4, -1).T
    assert len(quads) == 4
    for q in quads:
        c = np.array([by_id[int(i)] for i in q])
        normal = np.cross(c[2] - c[0], c[3] - c[1])
        assert np.allclose(c[:, 2], 1.0) and normal[2] > 0  # on the top face, pointing up: out of the block
