"""No solver: supports in a rotated frame (``Bc.csys``) as each writer writes them (see ``ada.fem.frames``).

The solves are ``tests/fem/test_rotated_frames.py``; these pin the records: Sesam's BNTRCOS / BNDOF and what turns
with a transformed node (BNLOAD, MGSPRNG), the frames it refuses, Code_Aster's LIAISON_OBLIQUE, and the writers that
take global axes only.
"""

from __future__ import annotations

import re

import numpy as np
import pytest

import ada
from ada.fem import Bc, FemSet, LoadPoint, Spring, StepImplicitStatic
from ada.fem.common import Csys
from ada.fem.exceptions.model_definition import UnsupportedFeature
from ada.fem.formats import conversion_report
from ada.materials.metals import CarbonSteel

L = 2.0
C30, S30 = np.cos(np.radians(30.0)), np.sin(np.radians(30.0))
TRACK = [(C30, 0.0, S30), (0.0, 1.0, 0.0)]  # x' along a 30 degree slope, z' = (-sin, 0, cos)
TURNED = [(0.0, 1.0, 0.0), (-1.0, 0.0, 0.0)]  # x' = y: global axes in another order


def _model(coords, dofs=(3,), load_at_roller=False, spring=False):
    mat = ada.Material("S355", CarbonSteel("S355"))
    bm = ada.Beam("bm", (0, 0, 0), (L, 0, 0), "IPE300", mat)
    p = ada.Part("P") / bm
    p.fem = bm.to_fem_obj(0.5, "line")
    fem = p.fem
    (left,) = [n for n in fem.nodes if abs(n.x) < 1e-9]
    (right,) = [n for n in fem.nodes if abs(n.x - L) < 1e-9]
    fem.add_bc(Bc("pin", fem.add_set(FemSet("pin", [left], "nset")), [1, 2, 3, 4]))
    roller = fem.add_set(FemSet("roller", [right], "nset"))
    fem.add_bc(Bc("roller", roller, list(dofs), csys=Csys("track", coords=coords, parent=fem)))
    a = ada.Assembly("A") / p
    step = a.fem.add_step(StepImplicitStatic("s"))
    at = (
        roller
        if load_at_roller
        else fem.add_set(FemSet("mid", [n for n in fem.nodes if abs(n.x - 1.0) < 1e-9], "nset"))
    )
    step.add_load(LoadPoint("p", -1000.0, at, 3))
    if spring:
        k = np.diag([1e3, 2e3, 3e3, 0.0, 0.0, 0.0])
        fem.add_spring(Spring("k", 900, "SPRING1", fem_set=roller, stiff=k, parent=fem))
    return a, right


def _records(path):
    """Numeric free-format records; the text ones (DATE, TDSETNAM ...) are skipped with their continuations."""

    def numbers(text):
        try:
            return [float(x) for x in text.split()]
        except ValueError:
            return None

    recs, skipping = [], False
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        if line[:8].strip():
            values = numbers(line[8:])
            skipping = values is None
            if not skipping:
                recs.append((line[:8].strip(), values))
        elif not skipping:
            values = numbers(line)
            if values is None:
                recs.pop()
                skipping = True
            else:
                recs[-1][1].extend(values)
    return recs


def _sesam(tmp_path, a):
    a.to_fem("d", "sesam", scratch_dir=tmp_path, overwrite=True)
    return _records(next((tmp_path / "d").glob("*T1.FEM")))


def test_sesam_transforms_the_roller_node_and_holds_its_local_dofs(tmp_path):
    a, right = _model(TRACK, dofs=(2, 3))
    recs = _sesam(tmp_path, a)
    univecs = [v for n, v in recs if n == "GUNIVEC"]
    (bntrcos,) = [v for n, v in recs if n == "BNTRCOS"]
    assert bntrcos[0] == len(univecs) + 1  # numbered apart from the GUNIVECs
    c = np.array(bntrcos[1:10]).reshape(3, 3, order="F")  # c11, c21, c31, ... column by column
    assert np.allclose(c[0], TRACK[0], atol=1e-8) and np.allclose(c[2], (-S30, 0.0, C30), atol=1e-8)
    (bndof,) = [v for n, v in recs if n == "BNDOF"]
    assert bndof == [right.id, bntrcos[0], bntrcos[0]]
    codes = {int(v[0]): v[2:8] for n, v in recs if n == "BNBCD"}
    assert codes[int(right.id)] == [0, 1, 1, 0, 0, 0]  # dy', dz' -- in the node's frame


def test_sesam_turns_a_nodal_load_and_a_grounded_spring_into_the_nodes_frame(tmp_path):
    a, right = _model(TRACK, load_at_roller=True, spring=True)
    recs = _sesam(tmp_path, a)
    (bnload,) = [v for n, v in recs if n == "BNLOAD"]
    assert int(bnload[4]) == right.id
    # -1000 along global z, in the frame: C @ (0, 0, -1000) = -1000 (x'_z, y'_z, z'_z) = -1000 (sin, 0, cos)
    assert np.allclose(bnload[6:9], [-1000.0 * S30, 0.0, -1000.0 * C30], atol=1e-4)
    (mgsprng,) = [v for n, v in recs if n == "MGSPRNG"]
    k = np.zeros((6, 6))
    lower = iter(mgsprng[2:])
    for j in range(6):
        for i in range(j, 6):
            k[i, j] = k[j, i] = next(lower)
    c = Csys("t", coords=TRACK).axes()
    assert np.allclose(k[:3, :3], c @ np.diag([1e3, 2e3, 3e3]) @ c.T, atol=1e-3)


def test_sesam_writes_a_frame_that_maps_onto_global_axes_as_a_global_support(tmp_path):
    a, right = _model(TURNED, dofs=(1,))  # dx' = dy
    recs = _sesam(tmp_path, a)
    assert not [n for n, _ in recs if n in ("BNTRCOS", "BNDOF")]
    codes = {int(v[0]): v[2:8] for n, v in recs if n == "BNBCD"}
    assert codes[int(right.id)] == [0, 1, 0, 0, 0, 0]


def test_sesam_refuses_a_frame_on_a_node_a_global_support_holds_in_part(tmp_path):
    a, right = _model(TRACK)
    fem = a.get_part("P").fem
    fem.add_bc(Bc("dx_only", fem.add_set(FemSet("again", [right], "nset")), [1]))
    with conversion_report.collect() as rep:
        recs = _sesam(tmp_path, a)
    assert not [n for n, _ in recs if n in ("BNTRCOS", "BNDOF")]
    (f,) = [f for f in rep.findings if f.subject == "roller"]
    assert f.kind == "omitted" and "global axes" in f.reason


def test_code_aster_writes_liaison_oblique_with_the_frames_angles(tmp_path):
    a, _ = _model(TRACK, dofs=(2, 3))
    a.to_fem("d", "code_aster", scratch_dir=tmp_path, overwrite=True)
    comm = (tmp_path / "d" / "d.comm").read_text()
    assert 'LIAISON_OBLIQUE=(\n        _F(GROUP_NO="roller", ANGL_NAUT=(0, -30, 0), DY=0.0, DZ=0.0),' in comm


def test_code_aster_writes_a_mapped_frame_as_ddl_impo(tmp_path):
    a, _ = _model(TURNED, dofs=(1,))
    a.to_fem("d", "code_aster", scratch_dir=tmp_path, overwrite=True)
    comm = (tmp_path / "d" / "d.comm").read_text()
    assert "LIAISON_OBLIQUE" not in comm and '_F(GROUP_NO="roller", DY=0.0),  # roller' in comm


@pytest.mark.parametrize("fmt", ["abaqus", "calculix"])
def test_global_axes_writers_map_a_turned_frame_and_refuse_a_skewed_one(tmp_path, fmt):
    a, _ = _model(TURNED, dofs=(1,))
    a.to_fem("d", fmt, scratch_dir=tmp_path, overwrite=True)
    inp = next((tmp_path / "d").glob("*.inp")).read_text().replace("P.roller", "roller")
    assert re.search(r"^ (\w+\.)?roller, 2(, 2)?$", inp, re.M)  # dx' = dy
    a, _ = _model(TRACK)
    with pytest.raises(UnsupportedFeature, match="rotated frame"):
        a.to_fem("e", fmt, scratch_dir=tmp_path, overwrite=True)
