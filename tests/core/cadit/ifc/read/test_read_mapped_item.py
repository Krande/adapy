"""Native IfcMappedItem reader (unwrap a mapped representation + apply its transform, no OCC kernel).

IfcMappedItem is an instancing wrapper (a MappingSource representation reused via a MappingTarget
transform). It previously fell back to the IfcOpenShell OCC kernel (the single most common corpus
fallback: 75 products / 10 files). The reader now unwraps the single mapped item, reads the
underlying geometry natively, and bakes the mapped-item 4x4 — so the product reads with no kernel
and renders. A *multi-item* mapped representation whose items are all face sets (e.g. a detailed
part exported as several IfcPolygonalFaceSets — the IfcSignal bodies in linear-placement-of-signal)
merges its items into one geometry and carries the shared transform as a mesh-level instance.
Non-rigid transforms on analytic solids, non-faceted multi-item sources, and multi-item product
bodies of differing sources degrade to the kernel fallback (verified graceful).
"""

from __future__ import annotations

import numpy as np
import pytest

import ada
from ada.config import Config


@pytest.fixture(autouse=True)
def _enable_geom():
    Config().update_config_globally("ifc_import_shape_geom", True)


def _tris(objs):
    from ada.occ.tessellating import BatchTessellator

    bt = BatchTessellator()
    return sum(len(ms.indices) // 3 for o in objs for ms in bt.batch_tessellate([o]))


def test_mapped_swept_disk_solid_reads_native(example_files):
    """reinforcing-stirrup: an IfcMappedItem wrapping an IfcSweptDiskSolid reads natively (ada.geom
    SweptDiskSolid, no OCC _occ_cache) and renders."""
    import ada.geom.solids as so

    a = ada.from_ifc(example_files / "ifc_files/reinforcing-stirrup.ifc")
    objs = list(a.get_all_physical_objects())
    assert len(objs) == 1
    o = objs[0]
    assert isinstance(o.geom.geometry, so.SweptDiskSolid)  # unwrapped native geometry
    assert o._occ_cache is None  # NOT built via the IfcOpenShell kernel
    assert _tris(objs) > 100  # renders (SweptDiskSolid is in solid_geom's accepted types)


def test_mapped_csg_solid_reads_native(example_files):
    """bath-csg-solid: an IfcMappedItem wrapping an IfcCsgSolid reads natively and renders."""
    a = ada.from_ifc(example_files / "ifc_files/bath-csg-solid.ifc")
    objs = list(a.get_all_physical_objects())
    assert len(objs) == 1
    assert objs[0]._occ_cache is None
    assert _tris(objs) > 0


def test_reinforcing_assembly_matches_oracle(example_files):
    """End-to-end: reinforcing-assembly (34 mapped IfcSweptDiskSolid rebar + a concrete IfcBeam that
    falls through to the shape importer). The rebar read native via IfcMappedItem, and the beam's
    ABSOLUTE ObjectPlacement (a non-identity Y<->Z rotation, PlacementRelTo=None) is applied — so
    the beam runs along world Y, not Z. The whole assembly bbox must match the ifcopenshell oracle
    ([-0.1, 0, -0.4]..[0.1, 5, 0])."""
    a = ada.from_ifc(example_files / "ifc_files/reinforcing-assembly.ifc")
    objs = list(a.get_all_physical_objects())

    from ada.occ.tessellating import BatchTessellator

    bt = BatchTessellator()
    pts = [np.asarray(ms.position, float).reshape(-1, 3) for o in objs for ms in bt.batch_tessellate([o])]
    p = np.vstack([x for x in pts if len(x)])
    assert np.allclose(p.min(0), (-0.1, 0.0, -0.4), atol=0.02), p.min(0)
    assert np.allclose(p.max(0), (0.1, 5.0, 0.0), atol=0.02), p.max(0)


def test_transform_geometry_rigid_and_nonrigid():
    """The mapped-item geometry transform: identity is a no-op, a rigid transform moves a face set's
    coordinates, and a non-uniform/shear transform raises (so the caller keeps the kernel path)."""
    from ada.cadit.ifc.read.geom.geom_reader import _transform_geometry
    from ada.geom import surfaces as su
    from ada.geom.points import Point

    fs = su.PolygonalFaceSet(coordinates=[Point(0, 0, 0), Point(1, 0, 0), Point(0, 1, 0)], faces=[[1, 2, 3]])

    assert _transform_geometry(fs, np.eye(4)) is fs  # identity: unchanged object

    trans = np.eye(4)
    trans[:3, 3] = (10.0, 20.0, 30.0)  # rigid translation
    moved = _transform_geometry(fs, trans)
    assert np.allclose(np.asarray(moved.coordinates[0]), (10, 20, 30))

    shear = np.eye(4)
    shear[0, 1] = 0.5  # non-rigid shear
    with pytest.raises(NotImplementedError):
        _transform_geometry(fs, shear)


def test_merge_face_sets_polygonal_offsets_indices():
    """A multi-item mapped representation's face sets merge into one: coordinates concatenate and the
    1-based face indices of later items shift by the running vertex count (the core of the
    linear-placement-of-signal fix — 2 IfcSignals, each a 5-IfcPolygonalFaceSet mapped body)."""
    from ada.cadit.ifc.read.geom.geom_reader import _merge_face_sets
    from ada.geom import surfaces as su
    from ada.geom.points import Point

    a = su.PolygonalFaceSet(coordinates=[Point(0, 0, 0), Point(1, 0, 0), Point(0, 1, 0)], faces=[[1, 2, 3]])
    b = su.PolygonalFaceSet(coordinates=[Point(2, 0, 0), Point(3, 0, 0), Point(2, 1, 0)], faces=[[1, 2, 3]])

    merged = _merge_face_sets([a, b])
    assert isinstance(merged, su.PolygonalFaceSet)
    assert len(merged.coordinates) == 6
    # first item's indices unchanged; second item's shifted by 3 (a's vertex count)
    assert merged.faces == [[1, 2, 3], [4, 5, 6]]
    assert np.allclose(np.asarray(merged.coordinates[3]), (2, 0, 0))


def test_merge_face_sets_triangulated_offsets_indices():
    """Triangulated face sets merge the same way (flat 1-based CoordIndex, offset per item)."""
    from ada.cadit.ifc.read.geom.geom_reader import _merge_face_sets
    from ada.geom import surfaces as su
    from ada.geom.direction import Direction
    from ada.geom.points import Point

    n = Direction(0, 0, 1)
    a = su.TriangulatedFaceSet(
        coordinates=[Point(0, 0, 0), Point(1, 0, 0), Point(0, 1, 0)], normals=[n], indices=[1, 2, 3]
    )
    b = su.TriangulatedFaceSet(
        coordinates=[Point(2, 0, 0), Point(3, 0, 0), Point(2, 1, 0)], normals=[n], indices=[1, 2, 3]
    )

    merged = _merge_face_sets([a, b])
    assert isinstance(merged, su.TriangulatedFaceSet)
    assert len(merged.coordinates) == 6
    assert merged.indices == [1, 2, 3, 4, 5, 6]


def test_merge_face_sets_rejects_non_face_sets():
    """Mixed/non-face-set items can't merge — raises so the mapped-item caller keeps the kernel path."""
    from ada.cadit.ifc.read.geom.geom_reader import _merge_face_sets
    from ada.geom import solids as so
    from ada.geom import surfaces as su
    from ada.geom.points import Point

    fs = su.PolygonalFaceSet(coordinates=[Point(0, 0, 0), Point(1, 0, 0), Point(0, 1, 0)], faces=[[1, 2, 3]])
    box = so.Box.from_2points(Point(0, 0, 0), Point(1, 1, 1))
    with pytest.raises(NotImplementedError):
        _merge_face_sets([fs, box])
