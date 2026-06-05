"""Isolated round-trip of sphere-bodied products through IFC.

GeniE-style node/support markers arrive as ``IfcBuildingElementProxy`` with an
``IfcSphere`` body. adapy imports these as :class:`PrimSphere`, which fully
round-trips (parametric centre + radius) and renders.
"""

import pytest

import ada


def test_sphere_proxy_roundtrip(tmp_path):
    sphere = ada.PrimSphere("node1", (1.0, 2.0, 3.0), 0.5)

    fp = (ada.Assembly() / (ada.Part("P") / sphere)).to_ifc(tmp_path / "sphere.ifc", file_obj_only=True)
    b = ada.from_ifc(fp)

    spheres = [o for o in b.get_all_physical_objects() if isinstance(o, ada.PrimSphere)]
    assert len(spheres) == 1
    assert spheres[0].radius == pytest.approx(0.5)
    assert tuple(float(x) for x in spheres[0].cog) == pytest.approx((1.0, 2.0, 3.0))


def test_sphere_proxy_renders(tmp_path):
    sphere = ada.PrimSphere("node1", (0.0, 0.0, 0.0), 0.5)
    a = ada.Assembly() / (ada.Part("P") / sphere)

    scene = a.to_trimesh_scene()
    faces = sum(g.faces.shape[0] for g in scene.geometry.values())
    assert faces > 0
