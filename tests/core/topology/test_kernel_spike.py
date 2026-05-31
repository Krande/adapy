"""Phase 0 parity spike for the topology-kernel verbs on CadBackend.

Self-contained golden values: build cells from hand-defined box face-soups and
assert cell counts, free/shared-face counts,
centroids, and point containment. Runs against whatever backend
``active_backend()`` selects (set ADAPY_CAD_BACKEND=occ|adacpp), so it doubles
as the cross-backend parity gate for make_volumes_from_faces /
non_manifold_merge / free_faces / point_in_solid / center_of_mass.
"""
from __future__ import annotations

import ada
from ada.cad import Containment, active_backend


def _box_faces(p1, p2):
    be = active_backend()
    return be.faces(ada.PrimBox("b", p1, p2).solid_occ())


def _coms(cells):
    be = active_backend()
    return sorted(((round(c.x, 4), round(c.y, 4), round(c.z, 4)) for c in (be.center_of_mass(s) for s in cells)))


def test_make_volumes_two_abutting_boxes():
    # Two unit cubes sharing the x=1 plane -> 2 cells, 1 internal (shared) face,
    # 10 free (envelope) faces; centroids at the cube centres.
    be = active_backend()
    soup = _box_faces((0, 0, 0), (1, 1, 1)) + _box_faces((1, 0, 0), (2, 1, 1))
    cells = be.make_volumes_from_faces(soup, tolerance=1e-6)

    assert len(cells) == 2
    assert sorted(round(be.volume(c), 6) for c in cells) == [1.0, 1.0]
    assert len(be.free_faces(cells)) == 10  # 12 - 2*(1 shared)
    assert _coms(cells) == [(0.5, 0.5, 0.5), (1.5, 0.5, 0.5)]


def test_make_volumes_single_box():
    be = active_backend()
    cells = be.make_volumes_from_faces(_box_faces((0, 0, 0), (1, 1, 1)), tolerance=1e-6)
    assert len(cells) == 1
    assert len(be.free_faces(cells)) == 6
    assert round(be.volume(cells[0]), 6) == 1.0


def test_make_volumes_l_of_three_boxes():
    # A(0..1,0..1) | B(1..2,0..1) share x=1; B | C(1..2,1..2) share y=1.
    # 3 cells, 2 shared faces -> 18 - 2*2 = 14 free faces.
    be = active_backend()
    soup = (
        _box_faces((0, 0, 0), (1, 1, 1))
        + _box_faces((1, 0, 0), (2, 1, 1))
        + _box_faces((1, 1, 0), (2, 2, 1))
    )
    cells = be.make_volumes_from_faces(soup, tolerance=1e-6)
    assert len(cells) == 3
    assert len(be.free_faces(cells)) == 14


def test_point_in_solid():
    be = active_backend()
    cells = be.make_volumes_from_faces(
        _box_faces((0, 0, 0), (1, 1, 1)) + _box_faces((1, 0, 0), (2, 1, 1)), tolerance=1e-6
    )
    # Order cells by centroid x so the asserts are deterministic.
    cells = sorted(cells, key=lambda c: be.center_of_mass(c).x)
    a, b = cells
    assert be.point_in_solid(a, (0.5, 0.5, 0.5)) is Containment.IN
    assert be.point_in_solid(a, (1.5, 0.5, 0.5)) is Containment.OUT
    assert be.point_in_solid(b, (1.5, 0.5, 0.5)) is Containment.IN
    assert be.point_in_solid(a, (3.0, 3.0, 3.0)) is Containment.OUT


def test_non_manifold_merge_keeps_shared_face():
    # Merging two abutting box SOLIDS (the non-manifold merge path) must keep
    # the touching face shared, not dissolve the partition: 2 solids, 10 free.
    be = active_backend()
    a = ada.PrimBox("a", (0, 0, 0), (1, 1, 1)).solid_occ()
    b = ada.PrimBox("b", (1, 0, 0), (2, 1, 1)).solid_occ()
    comp = be.non_manifold_merge([a, b], tolerance=1e-6, glue=True)
    sols = be.solids(comp)
    assert len(sols) == 2
    assert len(be.free_faces(sols)) == 10
