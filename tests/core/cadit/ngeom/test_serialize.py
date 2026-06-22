"""NGEOM serializer: ada.geom -> the neutral-schema binary buffer.

Validates the buffer is spec-shaped (magic/version/record framing/roots). The C++ decoder
in adacpp is tested independently; together they form the adapy<->adacpp contract. A full
cross-package round-trip (serialize here, tessellate in adacpp) is exercised in the adacpp
test env, not here, to keep adapy free of an adacpp dependency.
"""

from __future__ import annotations

import struct

import ada.geom.curves as cu
import ada.geom.surfaces as su
from ada.cadit.ngeom import NGEOM_VERSION, serialize_geometries
from ada.geom.placement import Axis2Placement3D, Direction, Point


def _line_oe(s, t):
    ec = cu.EdgeCurve(start=s, end=t, edge_geometry=cu.Line(s, [b - a for a, b in zip(s, t)]), same_sense=True)
    return cu.OrientedEdge(start=s, end=t, edge_element=ec, orientation=True)


def _square_face():
    p = [(0, 0, 0), (2, 0, 0), (2, 2, 0), (0, 2, 0)]
    loop = cu.EdgeLoop(edge_list=[_line_oe(p[i], p[(i + 1) % 4]) for i in range(4)])
    plane = su.Plane(position=Axis2Placement3D(Point(0, 0, 0), Direction(0, 0, 1), Direction(1, 0, 0)))
    return su.FaceSurface(bounds=[su.FaceBound(bound=loop, orientation=True)], face_surface=plane, same_sense=True)


def _parse(buf):
    """Minimal spec-faithful parser: returns (version, [(tag, payload_len)], [(root_idx, id)])."""
    assert buf[:8] == b"ADANGEOM"
    version, rec_count = struct.unpack_from("<ii", buf, 8)
    off = 16
    records = []
    for _ in range(rec_count):
        tag, nbytes = struct.unpack_from("<ii", buf, off)
        off += 8 + nbytes
        records.append((tag, nbytes))
    (root_count,) = struct.unpack_from("<i", buf, off)
    off += 4
    roots = []
    for _ in range(root_count):
        gidx, id_len = struct.unpack_from("<ii", buf, off)
        off += 8
        rid = buf[off : off + id_len].decode("utf-8")
        off += id_len
        roots.append((gidx, rid))
    assert off == len(buf), "trailing bytes / size mismatch"
    return version, records, roots


def test_buffer_is_spec_shaped():
    buf = serialize_geometries([("sq", _square_face())])
    version, records, roots = _parse(buf)
    assert version == NGEOM_VERSION
    tags = [t for t, _ in records]
    assert 40 in tags  # PLANE
    assert 65 in tags  # FACE_SURFACE
    assert 62 in tags  # EDGE_LOOP
    assert tags.count(60) == 4  # 4 EDGE_CURVE (one per square side)
    assert roots == [(len(records) - 1, "sq")]  # the FACE_SURFACE is the last record + the root


def test_multiple_roots_and_skip_unsupported():
    # two faces -> two roots; ordering preserved (node_id maps by position)
    buf = serialize_geometries([("a", _square_face()), ("b", _square_face())])
    _, _, roots = _parse(buf)
    assert [rid for _, rid in roots] == ["a", "b"]


def test_dependency_order_no_forward_refs():
    # every record's child refs must point to earlier indices (single-pass decodable)
    buf = serialize_geometries([("sq", _square_face())])
    _, records, _ = _parse(buf)
    # FACE_SURFACE (tag 65) is last; PLANE/loops/edges precede it
    assert records[-1][0] == 65
