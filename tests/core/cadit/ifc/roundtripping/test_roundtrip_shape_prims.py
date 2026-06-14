"""Round-trip coverage for the shape primitives (issue #16).

Shape primitives read back as a generic ada.Shape carrying the equivalent ada.geom geometry
(the exact Prim* class is not reconstructed by design) — so each test asserts the read-back is a
Shape whose geometry is the expected geo_so type.
"""

import pytest

import ada
from ada.geom import solids as so


def _roundtrip(obj, tmp_path):
    fp = (ada.Assembly() / (ada.Part("MyPart") / obj)).to_ifc(tmp_path / f"{obj.name}.ifc", file_obj_only=True)
    return ada.from_ifc(fp).get_by_name(obj.name)


@pytest.mark.parametrize(
    "obj_factory,expected_geom",
    [
        (lambda: ada.PrimBox("Box", (0, 0, 0), (1, 1, 1)), so.Box),
        (lambda: ada.PrimCyl("Cyl", (0, 0, 0), (0, 0, 1), 0.3), so.Cylinder),
        (lambda: ada.PrimCone("Cone", (0, 0, 0), (0, 0, 1), 0.3), so.Cone),
        (
            lambda: ada.PrimExtrude("Ext", [(0, 0), (1, 0), (0.5, 1)], 2, (0, 0, 1), (0, 0, 0), (1, 0, 0)),
            so.ExtrudedAreaSolid,
        ),
        (
            lambda: ada.PrimRevolve(
                "Rev",
                points=[(0, 0), (1, 0), (0.5, 1)],
                origin=(2, 2, 3),
                xdir=(0, 0, 1),
                normal=(1, 0, 0),
                rev_angle=180,
            ),
            so.RevolvedAreaSolid,
        ),
        (
            lambda: ada.PrimSweep("Swp", [(0, 0, 0), (1, 0, 0), (1, 1, 0)], [(0, 0), (0.1, 0), (0.1, 0.1), (0, 0.1)]),
            so.FixedReferenceSweptAreaSolid,
        ),
    ],
)
def test_roundtrip_shape_primitive(obj_factory, expected_geom, tmp_path):
    obj = obj_factory()
    got = _roundtrip(obj, tmp_path)

    assert isinstance(got, ada.Shape)
    assert got.parent.name == "MyPart"
    assert isinstance(got.geom.geometry, expected_geom)


def test_roundtrip_prim_sphere(tmp_path):
    # PrimSphere is the one shape primitive reconstructed as its exact class.
    sphere = ada.PrimSphere("Sph", (1, 2, 3), 0.5)
    got = _roundtrip(sphere, tmp_path)
    assert isinstance(got, ada.PrimSphere)
    assert got.radius == 0.5
