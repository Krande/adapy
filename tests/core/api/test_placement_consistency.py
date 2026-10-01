"""Every consumer of a placed object must put it in the same place.

A part placement is a rigid transform: a point ``p`` given in the part's local system lands at
``origin + xdir * p.x + ydir * p.y + zdir * p.z``, and a sub-part's placement is expressed in its parent's
local system. That is also what an ``IfcLocalPlacement`` chain means, so the IFC export is checked against
it too.

The expected world bounding boxes below are computed independently of adapy's own transform code, from the
local definition of each object and the rigid transform of each part.
"""

from __future__ import annotations

import itertools

import numpy as np
import pytest

import ada
from ada.base.types import GeomRepr

OUTER_ORIGIN = np.array([10.0, 0.0, 0.0])
# Outer part: local x points along global +y, local z stays global +z (so local y is global -x)
OUTER_AXES = np.array([[0.0, 1.0, 0.0], [-1.0, 0.0, 0.0], [0.0, 0.0, 1.0]])
INNER_ORIGIN = np.array([2.0, 0.0, 0.0])  # in the local system of the outer part

# Local bounding boxes (in the local system of the part that owns the object)
LOCAL_BBOX = {
    "bm1": ((0.0, -0.075, -0.15), (1.0, 0.075, 0.15)),
    "bm2": ((0.0, -0.075, -0.15), (1.0, 0.075, 0.15)),
    "box1": ((3.0, 0.0, 4.0), (4.0, 1.0, 5.0)),
    "pl1": ((5.0, 0.0, 2.0), (6.0, 1.0, 2.01)),
}


def _outer_to_world(p: np.ndarray) -> np.ndarray:
    return OUTER_ORIGIN + p @ OUTER_AXES


def _expected_world_bbox(name: str, local_bbox=None) -> np.ndarray:
    local_bbox = LOCAL_BBOX[name] if local_bbox is None else local_bbox
    corners = np.array(list(itertools.product(*zip(*local_bbox))), dtype=float)
    if name == "bm2":
        corners = corners + INNER_ORIGIN
    world = _outer_to_world(corners)
    return np.concatenate([world.min(axis=0), world.max(axis=0)])


def _model() -> tuple[ada.Assembly, dict[str, ada.BackendGeom]]:
    bm1 = ada.Beam("bm1", (0, 0, 0), (1, 0, 0), "IPE300")
    bm2 = ada.Beam("bm2", (0, 0, 0), (1, 0, 0), "IPE300")
    box1 = ada.PrimBox("box1", (3, 0, 4), (4, 1, 5))
    pl1 = ada.Plate("pl1", [(0, 0), (1, 0), (1, 1), (0, 1)], 0.01, origin=(5, 0, 2))
    inner = ada.Part("Inner", placement=ada.Placement(origin=INNER_ORIGIN)) / bm2
    outer_place = ada.Placement(origin=OUTER_ORIGIN, xdir=OUTER_AXES[0], zdir=OUTER_AXES[2])
    outer = ada.Part("Outer", placement=outer_place) / [bm1, box1, pl1, inner]
    a = ada.Assembly("A") / outer
    return a, dict(bm1=bm1, bm2=bm2, box1=box1, pl1=pl1)


def _bbox_of(points) -> np.ndarray:
    points = np.asarray(points, dtype=float).reshape(-1, 3)
    return np.concatenate([points.min(axis=0), points.max(axis=0)])


def test_the_reference_transform_is_rigid():
    """Sanity check of the expected values: local +x of the outer part is global +y."""
    assert _outer_to_world(np.array([1.0, 0.0, 0.0])) == pytest.approx([10.0, 1.0, 0.0])
    assert _outer_to_world(np.array([0.0, 1.0, 0.0])) == pytest.approx([9.0, 0.0, 0.0])


def test_absolute_placement_is_a_rigid_composition():
    _, objs = _model()
    place = objs["bm2"].placement.get_absolute_placement(include_rotations=True)

    assert np.asarray(place.origin, dtype=float) == pytest.approx(_outer_to_world(INNER_ORIGIN))
    assert np.asarray(place.xdir, dtype=float) == pytest.approx(OUTER_AXES[0])
    assert np.asarray(place.ydir, dtype=float) == pytest.approx(OUTER_AXES[1])
    assert np.asarray(place.zdir, dtype=float) == pytest.approx(OUTER_AXES[2])


@pytest.mark.parametrize("name", LOCAL_BBOX.keys())
def test_to_global_points(name):
    from ada.api.transforms import to_global_points

    _, objs = _model()
    corners = np.array(list(itertools.product(*zip(*LOCAL_BBOX[name]))), dtype=float)

    assert _bbox_of(to_global_points(objs[name], corners)) == pytest.approx(_expected_world_bbox(name), abs=1e-6)


@pytest.mark.parametrize("name", LOCAL_BBOX.keys())
def test_solid_body_is_local(name):
    """The CAD body of an object is built in the local system of its part"""
    from ada.cad import active_backend

    _, objs = _model()
    body_bbox = np.asarray(active_backend().bbox(objs[name].solid_occ()), dtype=float)

    local = np.concatenate([np.array(LOCAL_BBOX[name][0]), np.array(LOCAL_BBOX[name][1])])
    assert body_bbox == pytest.approx(local, abs=1e-3)


@pytest.mark.parametrize("name", LOCAL_BBOX.keys())
def test_fem_mesh(name):
    _, objs = _model()
    geom_repr = GeomRepr.SHELL if name == "pl1" else GeomRepr.SOLID

    fem = objs[name].to_fem_obj(0.5, geom_repr)

    # a shell mesh of the plate lies in the plane of the plate outline, without the thickness
    local_bbox = ((5.0, 0.0, 2.0), (6.0, 1.0, 2.0)) if name == "pl1" else None
    expected = _expected_world_bbox(name, local_bbox)
    assert _bbox_of([n.p for n in fem.nodes]) == pytest.approx(expected, abs=1e-3)


def test_tessellated_scene():
    a, objs = _model()
    scene = a.to_trimesh_scene(merge_meshes=False)

    found = set()
    for node in scene.graph.nodes_geometry:
        transform, geom_name = scene.graph[node]
        name = next((n for n in objs if f"name={n}," in node), None)
        if name is None:
            continue
        v = scene.geometry[geom_name].vertices
        world = (np.c_[v, np.ones(len(v))] @ transform.T)[:, :3]
        assert _bbox_of(world) == pytest.approx(_expected_world_bbox(name), abs=1e-3), name
        found.add(name)

    assert found == set(objs)


@pytest.mark.ifcgeom
def test_ifc_world_coordinates(tmp_path):
    import ifcopenshell

    ifc_geom = pytest.importorskip("ifcopenshell.geom")

    a, objs = _model()
    ifc_file = tmp_path / "placed.ifc"
    a.to_ifc(ifc_file)

    f = ifcopenshell.open(str(ifc_file))
    settings = ifc_geom.settings()
    settings.set(settings.USE_WORLD_COORDS, True)
    found = set()
    for prod in f.by_type("IfcProduct"):
        if prod.Name not in objs or prod.Representation is None:
            continue
        shape = ifc_geom.create_shape(settings, prod)
        world_bbox = _bbox_of(shape.geometry.verts)
        assert world_bbox == pytest.approx(_expected_world_bbox(prod.Name), abs=1e-3), prod.Name
        found.add(prod.Name)

    assert found == set(objs)
