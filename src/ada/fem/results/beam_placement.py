"""Where a beam's cross-section sits relative to its element axis.

Sesam puts a beam's local origin at the section's CENTROID, and ``GECCEN`` is the
vector from the node to it (SIF 7.3.10). adapy's profile builders do not agree
with that, and do not agree with each other:

* ``profiles.angular()`` starts at ``(0, 0)`` and runs to ``-h`` — origin on the
  attachment face — and ``straight_beam_to_geom`` then shifts it by ``h - Cgz``.
* ``profiles.iprofiles()`` is symmetric about ``±h/2`` — origin at mid-height —
  and gets no correction at all.

So the raw eccentricity cannot be applied as a shift. On the deck this was found
on it moved every L section off a plate it was already flush against, and
overshot every I/T section that straddled one, by exactly the origin error
(0.1214 m = Cgz - h/2 for a TG600x300x20x32).

Rather than encode each builder's convention — which is what produced the wrong
answer the first time — this MEASURES it. One canonical beam per section is
tessellated and its exact volume centroid taken; for a straight prism that is the
cross-section's area centroid, at mid-length. Whatever the builder and the
per-type corrections did, the measurement sees the result.

Cost is one tessellation per distinct SECTION, not per beam: a deck with 162k
beams over a dozen profiles pays for a dozen.
"""

from __future__ import annotations

import numpy as np

__all__ = ["SectionCentroidCache", "eccentric_shift"]


def _volume_centroid(verts: np.ndarray, tris: np.ndarray) -> np.ndarray | None:
    """Exact centroid of a closed triangulated solid, or None if degenerate.

    Signed-tetrahedron sum against the origin. Robust to the mesh's winding as a
    whole (a consistently-inverted solid gives a negative volume and the same
    centroid), but not to an open or self-intersecting one — hence the guard.
    """
    a = verts[tris[:, 0]]
    b = verts[tris[:, 1]]
    c = verts[tris[:, 2]]
    # Six times the signed tetra volume, and four times its centroid.
    vol6 = np.einsum("ij,ij->i", a, np.cross(b, c))
    total = float(vol6.sum())
    if abs(total) < 1e-12:
        return None
    centroid = ((a + b + c) * vol6[:, None]).sum(axis=0) / (4.0 * total)
    return centroid


class SectionCentroidCache:
    """Per-section offset from the beam axis to the drawn profile's centroid.

    Stored in the beam's own ``(yvec, up)`` frame, so it re-expresses onto any
    beam with any orientation. Keyed by section id — two beams sharing a section
    share the measurement.
    """

    def __init__(self) -> None:
        self._cache: dict[int, tuple[float, float] | None] = {}

    def offset_local(self, section) -> tuple[float, float] | None:
        key = id(section)
        if key in self._cache:
            return self._cache[key]
        self._cache[key] = self._measure(section)
        return self._cache[key]

    @staticmethod
    def _measure(section) -> tuple[float, float] | None:
        from ada import Beam
        from ada.occ.tessellating import BatchTessellator

        try:
            # A canonical beam: along +X, up +Z, unit length, no eccentricity. Its
            # axis midpoint is (0.5, 0, 0), so the centroid's distance from that is
            # the profile offset we are after.
            bm = Beam("centroid_probe", (0, 0, 0), (1, 0, 0), sec=section, up=(0, 0, 1))
            ms = BatchTessellator().tessellate_geom(bm.solid_geom(), bm)
            verts = np.asarray(ms.position, dtype=np.float64).reshape(-1, 3)
            tris = np.asarray(ms.indices, dtype=np.int64).reshape(-1, 3)
        except Exception:  # noqa: BLE001 — a profile that will not build has no offset
            return None

        centroid = _volume_centroid(verts, tris)
        if centroid is None:
            return None

        xvec = np.asarray(bm.xvec, dtype=float)
        yvec = np.asarray(bm.yvec, dtype=float)
        up = np.asarray(bm.up, dtype=float)
        d = centroid - np.array([0.5, 0.0, 0.0])
        # Drop the axial part: a prism's centroid is at mid-length, and any residual
        # there is tessellation noise rather than a placement offset.
        d = d - float(np.dot(d, xvec)) * xvec
        return float(np.dot(d, yvec)), float(np.dot(d, up))


def eccentric_shift(beam, ecc: np.ndarray, cache: SectionCentroidCache) -> np.ndarray | None:
    """The offset to give ``beam`` so its section centroid lands on ``node + ecc``.

    ``ecc`` is the raw GECCEN vector in global coordinates. Returns None when the
    section's placement could not be measured — the caller then leaves the beam
    alone, which is the pre-existing behaviour rather than a guess.

    The correction is what makes this safe for both families at once: a section
    already drawn on its centroid measures an offset equal to its own
    eccentricity and comes back with a shift of zero.
    """
    local = cache.offset_local(beam.section)
    if local is None:
        return None
    dy, dz = local
    yvec = np.asarray(beam.yvec, dtype=float)
    up = np.asarray(beam.up, dtype=float)
    return np.asarray(ecc, dtype=float) - (dy * yvec + dz * up)
