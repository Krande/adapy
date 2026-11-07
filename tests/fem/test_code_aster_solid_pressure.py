"""A pressure on solid elements, solved by Code_Aster against closed forms.

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


def _block(nx, ny, nz, a, b, L, name="col"):
    """``(assembly, part, nodes)``: a block of HEX8 on ``[0, a] x [0, b] x [0, L]``, ``nodes[i, j, k]``."""
    mat = ada.Material("mat", CarbonSteel("S355", E=E, v=NU))
    p = ada.Part(name)
    fem = p.fem
    nodes, nid = {}, 1
    for k in range(nz + 1):
        for j in range(ny + 1):
            for i in range(nx + 1):
                nodes[i, j, k] = fem.nodes.add(ada.Node((a * i / nx, b * j / ny, L * k / nz), nid))
                nid += 1
    els, eid = [], 1
    for k in range(nz):
        for j in range(ny):
            for i in range(nx):
                hexa = [nodes[i + di, j + dj, k + dk] for dk in (0, 1) for di, dj in ((0, 0), (1, 0), (1, 1), (0, 1))]
                els.append(fem.add_elem(Elem(eid, hexa, SolidShapes.HEX8)))
                eid += 1
    fs = fem.add_set(FemSet("solid", els, "elset"))
    fem.add_section(FemSection("sec", "solid", fs, mat))
    p.add_material(mat)
    return ada.Assembly("a") / p, p, nodes


def _face_surface(p, name, face_nodes):
    fem = p.fem
    els = [el for el in fem.elements if sum(n in face_nodes for n in el.nodes) == 4]
    surf = get_surface_from_nodes_on_solid_elements(name, els, face_nodes, fem, True)
    return fem.add_surface(surf)


def _field(res, key):
    return next(np.asarray(r.values, float) for r in res.results if key in r.name)


@pytest.mark.parametrize("mesh", [(1, 1, 1), (2, 3, 4)], ids=["one_element", "2x3x4"])
def test_a_column_pressed_on_its_top_face_settles_by_pL_over_E(tmp_path, require_solver, mesh):
    require_solver("code_aster")
    nx, ny, nz = mesh
    a_, b_, L, pressure = 0.5, 0.8, 2.0, 1.0e7
    a, p, nodes = _block(nx, ny, nz, a_, b_, L)
    fem = p.fem
    bottom = [n for (i, j, k), n in nodes.items() if k == 0]
    top = [n for (i, j, k), n in nodes.items() if k == nz]
    fem.add_bc(Bc("bz", fem.add_set(FemSet("bottom", bottom, "nset")), [3]))
    fem.add_bc(Bc("bxy", fem.add_set(FemSet("c0", [nodes[0, 0, 0]], "nset")), [1, 2]))
    fem.add_bc(Bc("by", fem.add_set(FemSet("c1", [nodes[nx, 0, 0]], "nset")), [2]))
    step = a.fem.add_step(StepImplicitStatic("press"))
    step.add_load(LoadPressure("p_top", pressure, _face_surface(p, "top", top)))

    res = a.to_fem("col", "code_aster", scratch_dir=tmp_path, overwrite=True, execute=True)
    u = {int(r[0]): r[1:4] for r in _field(res, "DEPL")}
    for (i, j, k), n in nodes.items():
        x, y, z = n.p
        # the exact uniaxial field: w = -p z / E, u = nu p x / E, v = nu p y / E
        want = (NU * pressure * x / E, NU * pressure * y / E, -pressure * z / E)
        assert u[int(n.id)] == pytest.approx(want, abs=1e-12 * pressure / E * L + 1e-15), (i, j, k)
    reac = _field(res, "REAC_NODA")
    assert np.nansum(reac[:, 3]) == pytest.approx(pressure * a_ * b_, rel=1e-9)


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
