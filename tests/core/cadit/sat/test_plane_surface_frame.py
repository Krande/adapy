"""ACIS plane-surface / straight-curve records must parse their geometry past the entity header.

The record header ``$owner -1 -1 $attrib`` has two bare ``-1`` integers that are numeric, so a
plain float-filter swallowed them and shifted origin/normal/u_direction by two — planes came out
with a garbage frame (and the flat plate tessellated to a single triangle via the NGEOM path).
"""

from __future__ import annotations

import numpy as np

import ada


def test_plane_surface_frame_not_shifted(example_files):
    """plate_1_flat.sat: the planar face reads its true frame (origin ~(20.57,-32.53,3.9), normal
    ~(0,0,-1)), not the header-shifted garbage the old parser produced."""
    a = ada.from_acis(example_files / "sat_files/plate_1_flat.sat")
    o = list(a.get_all_physical_objects())[0]
    faces = getattr(o.geom.geometry, "cfs_faces", None) or getattr(o.geom.geometry, "faces", None)
    plane = faces[0].face_surface
    pos = plane.position
    assert np.allclose(np.asarray(pos.location), (20.569, -32.525, 3.9), atol=0.01), pos.location
    n = np.asarray(pos.axis, float)
    assert np.allclose(np.abs(n), (0, 0, 1), atol=1e-3), n  # unit normal along z (sign is sense-dependent)


def test_parse_plane_surface_helper_skips_header():
    """Unit-level: the header-skip helper drops the `$owner -1 -1 $attrib` prefix and reads the
    coordinates, not the header integers."""
    from ada.cadit.sat.parser.parser import _numeric_after_header

    parts = "$-1 -1 -1 $-1 20.5 -32.5 3.9 0 0 -1 -1 0 0 reverse_v I I I I #".split()
    got = _numeric_after_header(parts)
    assert got[0:3] == [20.5, -32.5, 3.9]  # origin, not [-1, -1, 20.5]
    assert got[3:6] == [0.0, 0.0, -1.0]  # normal
