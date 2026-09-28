"""A generic ``Shape`` (as minted by the IFC/native readers) holds its geometry in LOCAL representation
coordinates and its world transform on ``self.placement``, like every other object.

``solid_geom()`` is the local geometry, never mutated by a placement. The world transform is applied once by
the consumers: ``world_matrix``/``shape_global`` for CAD bodies, and the tessellator for rendering. These tests
pin that a rotated shape lands where its placement puts it (the bug was a rotated shape rendering unplaced).
"""

import numpy as np

from ada import Placement, Point, Shape
from ada.geom import Geometry
from ada.geom.solids import Box


def _box_shape(place: Placement) -> Shape:
    box = Box.from_2points(Point(0, 0, 0), Point(2, 1, 1))
    return Shape("boxshape", geom=Geometry("b", box, None), placement=place)


def _rotated_place() -> Placement:
    # 90deg about Z at origin (5, 2, 0): local +X -> world +Y
    return Placement.from_axis_angle([0, 0, 1], 90, origin=(5, 2, 0))


# local box (0,0,0)-(2,1,1) rotated 90 about Z at (5,2,0) -> x in [4,5], y in [2,4], z in [0,1]
def _check_world_bbox(mn, mx):
    assert 3.9 < mn[0] and mx[0] < 5.1, (mn, mx)
    assert 1.9 < mn[1] and mx[1] < 4.1, (mn, mx)


def test_identity_placement_has_no_world_transform():
    sh = _box_shape(Placement())
    assert np.allclose(np.asarray(sh.solid_geom().geometry.position.location), (0, 0, 0))
    assert sh.world_matrix() is None


def test_solid_geom_is_local_and_never_mutated():
    place = _rotated_place()
    sh = _box_shape(place)

    for _ in range(2):  # repeated calls never compound
        assert np.allclose(np.asarray(sh.solid_geom().geometry.position.location), (0, 0, 0))
    assert np.allclose(np.asarray(sh.geom.geometry.position.location), (0, 0, 0))


def test_world_matrix_is_the_placement():
    place = _rotated_place()
    sh = _box_shape(place)

    world = sh.world_matrix()
    assert np.allclose(world, place.get_matrix4x4())
    assert np.allclose(world[:3, :3] @ (1, 0, 0), (0, 1, 0), atol=1e-6)  # local X -> world Y


def test_global_body_is_placed():
    from ada.cad import active_backend

    sh = _box_shape(_rotated_place())
    xmin, ymin, zmin, xmax, ymax, zmax = active_backend().bbox(sh.shape_global())
    _check_world_bbox((xmin, ymin, zmin), (xmax, ymax, zmax))


def test_global_body_is_placed_on_each_kernel(backend):
    """The local solid, built by each installed kernel by name and moved by the world matrix."""
    sh = _box_shape(_rotated_place())
    body = backend.transform(backend.build(sh.solid_geom()), sh.world_matrix(), True)
    xmin, ymin, zmin, xmax, ymax, zmax = backend.bbox(body)
    _check_world_bbox((xmin, ymin, zmin), (xmax, ymax, zmax))


def test_tessellation_is_placed():
    from ada.visit.tessellate import BatchTessellator

    sh = _box_shape(_rotated_place())
    meshes = list(BatchTessellator().batch_tessellate([sh]))

    pts = np.concatenate([np.asarray(ms.position, dtype=float).reshape(-1, 3) for ms in meshes])
    _check_world_bbox(pts.min(0), pts.max(0))
