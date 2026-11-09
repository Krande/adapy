"""The Code_Aster writer models a beam's eccentricity: offset nodes on the axis, rigid links to the mesh nodes.

Code_Aster's beams have no offset of their own (``AFFE_CARA_ELEM``/``POUTRE`` has none; ``EXCENTREMENT`` is the
shells' and grids'), so an eccentric element -- a stiffener meshed on its plate's nodes with its axis below the plate
-- is written on nodes moved by the eccentricity vector, each tied to its mesh node by ``LIAISON_GROUP`` relations
``u_axis = u_node + theta_node x e``, ``theta_axis = theta_node`` (``ada.fem.formats.code_aster.write.write_eccentricity``).
These tests pin what reaches the MED file, the command file and the sidecar; the solve is measured against Sestra
on the same mesh in ``tests/fem/test_code_aster_eccentric_beams.py``.
"""

from __future__ import annotations

import json
import re

import h5py
import numpy as np
import pytest

import ada
from ada.fem import Bc, FemSet, StepImplicitStatic
from ada.fem.formats.code_aster.write.write_eccentricity import ECC_LINKS

PL = [(0, 0), (2.0, 0), (2.0, 1.0), (0, 1.0)]


def _model(flush: bool) -> tuple[ada.Assembly, ada.Part, ada.Beam]:
    pl = ada.Plate("pl", PL, 0.006, origin=(0, 0, 0), xdir=(1, 0, 0), normal=(0, 0, 1))
    # An angle is drawn from the top of its leg, so even on "neutral axis" justification its centroid is off the
    # nodal line (and the FE eccentricity follows the geometry); the on-axis case is a symmetric section.
    bm = ada.Beam("st", (1.0, 0, 0), (1.0, 1.0, 0), "HP160x8" if flush else "IPE300", up=(0, 0, 1))
    if flush:
        bm.justification = "flush top"
    p = ada.Part("P") / [pl, bm]
    p.fem = p.to_fem_obj(0.25, bm_repr="line", pl_repr="shell")
    edge = [n for n in p.fem.nodes if abs(n.x) < 1e-9 or abs(n.x - 2.0) < 1e-9]
    p.fem.add_bc(Bc("ss", p.fem.add_set(FemSet("edges", edge, FemSet.TYPES.NSET)), [1, 2, 3]))
    a = ada.Assembly("A") / p
    a.fem.add_step(StepImplicitStatic("grav")).add_load(ada.fem.LoadGravity("g", -9.81))
    return a, p, bm


def _write(tmp_path, flush: bool):
    a, p, bm = _model(flush)
    a.to_fem("ecc", "code_aster", scratch_dir=tmp_path, overwrite=True)
    out = tmp_path / "ecc"
    return p, bm, (out / "ecc.comm").read_text(), out / "ecc.med", out / "ecc.ecc_nodes.json"


def _med_nodes_and_seg2(med_path):
    with h5py.File(med_path, "r") as f:
        mesh = f["ENS_MAA"]["ecc"]
        step = next(iter(mesh.values()))
        coo = np.asarray(step["NOE"]["COO"]).reshape(3, -1).T
        seg2 = np.asarray(step["MAI"]["SE2"]["NOD"]).reshape(2, -1).T
        # A node family is named FAM_<id>_<its set names joined by _> (write_sets._family_name).
        families = list(f["FAS"]["ecc"]["NOEUD"].keys())
    return coo, seg2, families


def test_eccentric_beam_is_written_on_offset_nodes_tied_by_rigid_links(tmp_path):
    p, bm, comm, med, sidecar = _write(tmp_path, flush=True)
    vec = bm.offset_helper.curve_offset_global()[0]
    assert vec[2] == pytest.approx(-(bm.section.h - bm.section.properties.Cgz))

    beam_elems = list(p.fem.elements.lines)
    beam_node_ids = sorted({int(n.id) for el in beam_elems for n in el.nodes})
    max_nid = int(p.fem.nodes.max_nid)

    coo, seg2, families = _med_nodes_and_seg2(med)
    # One offset node per mesh node of the beam, numbered after the mesh's last node, at node + vector.
    assert len(coo) == max_nid + len(beam_node_ids)
    offsets = coo[max_nid:]
    mesh_pts = np.array([p.fem.nodes.from_id(i).p for i in beam_node_ids])
    assert np.allclose(np.sort(offsets, axis=0), np.sort(mesh_pts + vec, axis=0), atol=1e-9)
    # The beam cells reference the offset nodes only; the shells keep the mesh nodes.
    assert seg2.min() > max_nid
    assert len(seg2) == len(beam_elems)
    # The link groups are in the MED file: the mesh nodes and their offset nodes.
    assert any("ecc_1_ref" in fam for fam in families) and any("ecc_1_axis" in fam for fam in families)

    # The sidecar maps every offset node to its mesh node.
    ref_of = {int(k): v for k, v in json.loads(sidecar.read_text())["ref_of"].items()}
    assert sorted(ref_of) == list(range(max_nid + 1, max_nid + 1 + len(beam_node_ids)))
    assert sorted(set(ref_of.values())) == beam_node_ids

    # The charge: six relations pairing the two groups by the vector, in the step's EXCIT.
    assert f"{ECC_LINKS} = AFFE_CHAR_MECA(" in comm
    items = re.findall(
        r"_F\(GROUP_NO_1='ecc_1_ref', GROUP_NO_2='ecc_1_axis', TRAN=\(([^)]*)\), DDL_1=\(([^)]*)\), "
        r"COEF_MULT_1=\(([^)]*)\), DDL_2=\('(\w+)',\), COEF_MULT_2=\(-1.0,\), COEF_IMPO=0.0\)",
        comm,
    )
    assert [m[3] for m in items] == ["DX", "DY", "DZ", "DRX", "DRY", "DRZ"]
    ex, ey, ez = (float(v) for v in items[0][0].split(",") if v.strip())
    assert (ex, ey, ez) == pytest.approx(tuple(vec), abs=1e-9)

    def names(s):
        return [v.strip().strip("'") for v in s.split(",") if v.strip()]

    def coefs(s):
        return [float(v) for v in s.split(",") if v.strip()]

    # u_x = u_x + ry * ez - rz * ey: ey = 0 so only DRY; u_y: rz * ex - rx * ez -> DRX; u_z: rx ey - ry ex -> none
    assert names(items[0][1]) == ["DX", "DRY"] and coefs(items[0][2]) == [1.0, ez]
    assert names(items[1][1]) == ["DY", "DRX"] and coefs(items[1][2]) == [1.0, -ez]
    assert names(items[2][1]) == ["DZ"] and coefs(items[2][2]) == [1.0]
    assert names(items[3][1]) == ["DRX"] and coefs(items[3][2]) == [1.0]
    excit = re.search(r"EXCIT=\((.*?)\)\s*\n\)", comm, re.S).group(1)
    assert f"_F(CHARGE={ECC_LINKS})" in excit and "_F(CHARGE=supports)" in excit


def test_a_point_mass_on_an_eccentric_beams_node_stays_on_the_mesh_node(tmp_path):
    """A mass is defined on the node -- where the plate and the beam meet -- not on the beam, so its POI1 cell keeps
    the mesh node while the beam's SEG2 cells move to the offset nodes (the rigid link carries it to the beam)."""
    from ada.fem import Mass

    a, p, bm = _model(flush=True)
    (mid,) = [n for n in p.fem.nodes if abs(n.x - 1.0) < 1e-9 and abs(n.y - 0.5) < 1e-9]
    p.fem.add_mass(Mass("m", p.fem.add_set(FemSet("m_set", [mid], FemSet.TYPES.NSET)), 100.0))
    a.to_fem("ecc", "code_aster", scratch_dir=tmp_path, overwrite=True)
    with h5py.File(tmp_path / "ecc" / "ecc.med", "r") as f:
        step = next(iter(f["ENS_MAA"]["ecc"].values()))
        po1 = np.asarray(step["MAI"]["PO1"]["NOD"])
        seg2 = np.asarray(step["MAI"]["SE2"]["NOD"])
    assert po1.tolist() == [int(mid.id)]
    assert int(mid.id) not in seg2.tolist() and seg2.min() > int(p.fem.nodes.max_nid)


def test_beam_on_its_axis_gets_no_links_and_no_sidecar(tmp_path):
    p, bm, comm, med, sidecar = _write(tmp_path, flush=False)
    assert ECC_LINKS not in comm
    assert not sidecar.exists()
    coo, seg2, _ = _med_nodes_and_seg2(med)
    assert len(coo) == int(p.fem.nodes.max_nid)
    assert seg2.max() <= int(p.fem.nodes.max_nid)


def test_a_stale_sidecar_is_removed_on_rewrite(tmp_path):
    _write(tmp_path, flush=True)
    sidecar = tmp_path / "ecc" / "ecc.ecc_nodes.json"
    assert sidecar.exists()
    _write(tmp_path, flush=False)
    assert not sidecar.exists()
