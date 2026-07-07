"""Native IfcMappedItem reader (unwrap a mapped representation + apply its transform, no OCC kernel).

IfcMappedItem is an instancing wrapper (a MappingSource representation reused via a MappingTarget
transform). It previously fell back to the IfcOpenShell OCC kernel (the single most common corpus
fallback: 75 products / 10 files). The reader now unwraps the single mapped item, reads the
underlying geometry natively, and bakes the mapped-item 4x4 — so the product reads with no kernel
and renders. Non-rigid transforms, multi-item mapped representations, and multi-item product
bodies degrade to the kernel fallback (verified graceful).
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
