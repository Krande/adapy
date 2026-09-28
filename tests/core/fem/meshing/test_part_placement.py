import numpy as np
import pytest

import ada
from ada.api.transforms import to_global_points, to_global_vectors
from ada.base.types import GeomRepr
from ada.cad import active_backend

PLACEMENTS = {
    "identity": ada.Placement(),
    "translated": ada.Placement(origin=(10, 0, 0)),
    "rotated": ada.Placement(origin=(10, 0, 0), xdir=(0, 1, 0), zdir=(0, 0, 1)),
}


def _beam_in_part(placement: ada.Placement, **kwargs) -> tuple[ada.Beam, ada.Part]:
    bm = ada.Beam("bm1", (0, 0, 0), (1, 0, 0), "IPE300", **kwargs)
    p = ada.Part("P1", placement=placement) / bm
    ada.Assembly() / p
    return bm, p


def test_line_part_offset():
    bm = ada.Beam("b1", (0, 0, 0), (1, 0, 0), "IPE300")
    p = ada.Part("Part", placement=ada.Placement((200, 100, 500))) / bm
    fem = p.to_fem_obj(1)
    assert len(list(fem.elements.lines)) == 1
    assert len(fem.nodes) == 2
    p1 = fem.nodes[0].p
    p2 = fem.nodes[1].p

    assert (bm.n1.p + p.placement.origin).is_equal(p1)
    assert (bm.n2.p + p.placement.origin).is_equal(p2)


@pytest.mark.parametrize("bm_repr", [GeomRepr.LINE, GeomRepr.SHELL, GeomRepr.SOLID])
@pytest.mark.parametrize("placement", PLACEMENTS.keys())
def test_all_geom_reprs_are_meshed_along_the_global_beam_axis(placement, bm_repr):
    bm, p = _beam_in_part(PLACEMENTS[placement])
    p1, p2 = to_global_points(bm, [bm.n1.p, bm.n2.p])
    xvec = to_global_vectors(bm, bm.xvec)

    fem = p.to_fem_obj(0.25, bm_repr)

    coords = np.array([n.p for n in fem.nodes], dtype=float)
    along = (coords - p1) @ xvec
    across = np.linalg.norm((coords - p1) - np.outer(along, xvec), axis=1)
    assert along.min() == pytest.approx(0.0, abs=1e-6)
    assert along.max() == pytest.approx(np.linalg.norm(p2 - p1), abs=1e-6)
    # every node lies within the IPE300 section around the beam axis
    assert across.max() <= np.hypot(0.15, 0.075) + 1e-6


@pytest.mark.parametrize("placement", PLACEMENTS.keys())
def test_solid_mesh_is_not_transformed_twice(placement):
    bm, p = _beam_in_part(PLACEMENTS[placement])

    fem = p.to_fem_obj(0.25, GeomRepr.SOLID)

    coords = np.array([n.p for n in fem.nodes], dtype=float)
    body_bbox = np.array(active_backend().bbox(bm.solid_occ()), dtype=float)
    assert coords.min(axis=0) == pytest.approx(body_bbox[:3], abs=1e-3)
    assert coords.max(axis=0) == pytest.approx(body_bbox[3:], abs=1e-3)


@pytest.mark.parametrize("placement", PLACEMENTS.keys())
def test_shell_beam_sections_are_identified_in_moved_parts(placement):
    bm, p = _beam_in_part(PLACEMENTS[placement])

    fem = p.to_fem_obj(0.25, GeomRepr.SHELL)

    thicknesses = {round(float(s.thickness), 6) for s in fem.sections.shells}
    assert thicknesses == {round(bm.section.t_fbtn, 6), round(bm.section.t_w, 6)}


@pytest.mark.parametrize("placement", PLACEMENTS.keys())
def test_line_beam_orientation_and_eccentricity_follow_part_rotation(placement):
    bm, p = _beam_in_part(PLACEMENTS[placement], e1=(0, 0, 0.1), e2=(0, 0, 0.1))

    fem = p.to_fem_obj(0.25, GeomRepr.LINE)

    local_z = to_global_vectors(bm, bm.ori[2])
    assert np.asarray(fem.sections.lines[0].local_z, dtype=float) == pytest.approx(local_z)

    e1_global = -to_global_vectors(bm, bm.e1)
    ecc_vectors = [
        np.asarray(ep.ecc_vector, dtype=float)
        for el in fem.elements.lines
        if el.eccentricity is not None
        for ep in (el.eccentricity.end1, el.eccentricity.end2)
        if ep is not None
    ]
    assert len(ecc_vectors) > 0
    assert all(v == pytest.approx(e1_global) for v in ecc_vectors)
