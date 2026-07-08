"""Geometry.transforms → mesh-level instancing.

Regression for the IfcMappedItem OCC fallbacks: a mapped item with a non-rigid (scale/shear)
transform, or a product that instances one mapping source N times, carries its placement(s) on
``Geometry.transforms`` and is baked into the tessellated mesh — one emitted MeshStore per
transform — instead of falling back to the OCC kernel.
"""

from __future__ import annotations

import numpy as np

from ada.geom import Geometry
from ada.geom.points import Point
from ada.geom.solids import Box
from ada.visit.gltf.meshes import MeshStore, MeshType


class _Obj:
    def __init__(self, transforms):
        self.geom = Geometry("g", Box.from_2points(Point(0, 0, 0), Point(1, 1, 1)), transforms=transforms)


def _unit_ms():
    # one triangle at unit scale
    pos = np.array([0, 0, 0, 1, 0, 0, 0, 1, 0], dtype=np.float32)
    idx = np.array([0, 1, 2], dtype=np.uint32)
    return MeshStore(0, None, pos, idx, None, 0, MeshType.TRIANGLES, 0)


def test_no_transforms_passthrough():
    from ada.occ.tessellating import _emit_with_geom_transforms

    ms = _unit_ms()
    out = list(_emit_with_geom_transforms(ms, _Obj(None)))
    assert len(out) == 1 and out[0] is ms


def test_nonuniform_scale_baked_into_positions():
    from ada.occ.tessellating import _emit_with_geom_transforms

    m = np.diag([2.0, 3.0, 1.0, 1.0])  # non-uniform scale
    out = list(_emit_with_geom_transforms(_unit_ms(), _Obj([m])))
    assert len(out) == 1
    p = np.asarray(out[0].position, float).reshape(-1, 3)
    assert np.allclose(p[1], [2, 0, 0]) and np.allclose(p[2], [0, 3, 0])


def test_multiple_transforms_emit_one_meshstore_each():
    from ada.occ.tessellating import _emit_with_geom_transforms

    t1 = np.eye(4)
    t2 = np.eye(4)
    t2[:3, 3] = [10, 0, 0]  # translate
    out = list(_emit_with_geom_transforms(_unit_ms(), _Obj([t1, t2])))
    assert len(out) == 2
    p2 = np.asarray(out[1].position, float).reshape(-1, 3)
    assert np.allclose(p2[0], [10, 0, 0])
