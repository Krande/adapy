"""NGEOM deserialize decodes swept/CSG solid tags 50-53 back to ada.geom.

serialize.py emits ExtrudedAreaSolid/RevolvedAreaSolid/BooleanResult/Sphere as tags 50-53 (with the
profile flattened into a planar FACE), and adacpp's C++ decoder reads them — but the Python
deserializer used to raise on those tags, so the lazy ShapeStore's pure-Python round-trip failed on
any analytic solid. It now inverts them (rebuilding the profile from the face's local-XY loops), so
serialize→deserialize→serialize is byte-identical. Only the baked-frame tag 54 stays lossy (raises).
"""

from __future__ import annotations

import math

import pytest

import ada.geom.curves as cu
import ada.geom.solids as so
import ada.geom.surfaces as su
from ada import Beam
from ada.geom import Geometry
from ada.geom.booleans import BooleanResult, BoolOpEnum
from ada.geom.direction import Direction
from ada.geom.placement import Axis1Placement, Axis2Placement3D
from ada.geom.points import Point
from ada.cadit.ngeom.deserialize import NgeomDecodeError, deserialize_geometries
from ada.cadit.ngeom.serialize import serialize_geometries


def _roundtrip(geom: Geometry):
    buf = serialize_geometries([("g", geom)])
    decoded = deserialize_geometries(buf)
    assert len(decoded) == 1
    gg = decoded[0][1]
    g2 = gg if isinstance(gg, Geometry) else Geometry("g", gg)
    # byte-identical re-encode is the strongest round-trip guarantee
    assert serialize_geometries([("g", g2)]) == buf
    return gg.geometry if isinstance(gg, Geometry) else gg


def _rect(w, h):
    p = [Point(0, 0), Point(w, 0), Point(w, h), Point(0, h)]
    segs = [cu.Edge(p[i], p[(i + 1) % 4]) for i in range(4)]
    return su.ArbitraryProfileDef(profile_type=su.ProfileType.AREA, outer_curve=cu.IndexedPolyCurve(segs))


def test_extruded_area_solid_roundtrip():
    """An IPE200 beam's extrusion (I-profile WITH fillet arcs) decodes back to an ExtrudedAreaSolid,
    fillets intact (byte-identical re-encode)."""
    g = Beam("b", (0, 0, 0), (2, 0, 0), "IPE200").solid_geom()
    out = _roundtrip(g)
    assert isinstance(out, so.ExtrudedAreaSolid)


def test_sphere_solid_roundtrip():
    out = _roundtrip(Geometry("s", so.Sphere(center=Point(0, 0, 0), radius=1.5)))
    assert isinstance(out, so.Sphere)
    assert out.radius == pytest.approx(1.5)


def test_revolved_area_solid_roundtrip():
    """Revolve round-trips through the world→local axis transform + degrees↔radians conversion."""
    rev = so.RevolvedAreaSolid(
        swept_area=_rect(1, 2),
        position=Axis2Placement3D(Point(0, 0, 0)),
        axis=Axis1Placement(Point(3, 0, 0), Direction(0, 0, 1)),
        angle=270.0,
    )
    out = _roundtrip(Geometry("r", rev))
    assert isinstance(out, so.RevolvedAreaSolid)
    assert out.angle == pytest.approx(270.0)


def test_boolean_result_roundtrip():
    box = so.Box.from_2points(Point(0, 0, 0), Point(2, 2, 2))
    sph = so.Sphere(center=Point(1, 1, 1), radius=0.8)
    out = _roundtrip(Geometry("bool", BooleanResult(first_operand=box, second_operand=sph, operator=BoolOpEnum.DIFFERENCE)))
    assert isinstance(out, BooleanResult)
    assert out.operator is BoolOpEnum.DIFFERENCE
    assert isinstance(out.first_operand, so.ExtrudedAreaSolid)  # Box serializes as an extrusion
    assert isinstance(out.second_operand, so.Sphere)


def test_fixed_reference_swept_is_lossy():
    """Tag 54 bakes the directrix into per-station frames — not invertible → a clear decode error."""
    from ada.cadit.ngeom.serialize import _EXTRUDED_AREA_SOLID  # noqa: F401 - ensure module import ok

    # Build a minimal tag-54 buffer by serializing a fixed-ref swept solid if one is constructible;
    # otherwise assert the decoder rejects the tag directly.
    from ada.cadit.ngeom import deserialize as _d

    dec = _d._Decoder([(_d._FIXED_REF_SWEPT_SOLID, memoryview(b""))])
    with pytest.raises(NgeomDecodeError, match="tag 54"):
        dec.get(0)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
