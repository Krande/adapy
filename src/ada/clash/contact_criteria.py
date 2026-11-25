"""What core's convex-contact check counts as a contact, beyond the engine's own area cut.

THE PROBLEM. The engine (adacpp) measures the members' convex hulls and gives a LINE contact -- an
edge lying on a face, or two edges crossing -- a strip ``line_contact_thickness`` wide (1 mm), so it
has an area. Two box members that only graze along an edge then read as a contact of the edge's
length in mm²: a 170 mm edge is about 170 mm². Such grazes are not connections.

TWO CRITERIA, both options of the checker (``ada.clash.convex_contact.register``):

* ``min_contact_area`` (default 200 mm²): smaller patches are dropped by the engine itself.
* ``min_contact_width_ratio`` (default 0.5): a contact whose patch is narrower than this fraction of
  the thinner member's wall or plate thickness is dropped. The WIDTH is the patch's area over its
  longest chord (:func:`patch_width`): a face-to-face patch is as wide as the faces' overlap, a
  line contact as wide as the engine's strip, a two-point patch has no width of its own beyond it.

AND ONE EXCEPTION: a member running into another -- its END at the other member, the way a stick
model's member ends on the member it lands on -- is kept whatever its patch (:func:`runs_into`): a
member end is a connection even where the hulls only touch along an edge.

Measured on real models, the genuine contacts were all at least 5,000 mm² and 13 mm wide; the false
ones were two-point or 1 mm wide patches of 126-200 mm².

WHERE THE THICKNESS COMES FROM. A run measures the viewer GLB, which carries no sections, so the
width criterion is applied when a run is ANNOTATED (``ada.clash.contacts.annotate_applicable``),
where the members are known: from the rebuilt members where they bind (a plate's ``t``, a beam
section's thinnest wall or flange, :func:`member_shape`), else from the members' meshes (a plate-like
mesh's thinnest extent, :func:`mesh_shape`; a beam's mesh does not tell its wall). Where neither
member's thickness is known, :data:`FALLBACK_THICKNESS` stands in: with the default ratio a contact
must then be 2 mm wide -- twice the strip a line contact is given, and narrower than any plate edge.

Old runs (whose options name no ``min_contact_width_ratio``) are left as they were measured, also
when annotated again.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

import numpy as np

__all__ = [
    "DEFAULT_MIN_CONTACT_AREA",
    "DEFAULT_MIN_CONTACT_WIDTH_RATIO",
    "FALLBACK_THICKNESS",
    "MemberShape",
    "is_edge_contact",
    "member_shape",
    "mesh_shape",
    "patch_width",
    "runs_into",
]

#: The least contact patch area [m²]: 200 mm². The engine drops smaller ones.
DEFAULT_MIN_CONTACT_AREA = 2e-4
#: The least patch width, as a fraction of the thinner member's wall or plate thickness.
DEFAULT_MIN_CONTACT_WIDTH_RATIO = 0.5
#: The thickness used where neither member's is known [m]: the thinnest plate or wall a structural
#: member is expected to have.
FALLBACK_THICKNESS = 0.004
#: A mesh whose longest extent is at least this many times its second is a beam (as
#: ``ada.clash.contacts.ELONGATED``); a stubbier one is plate-like and its thinnest extent its thickness.
_ELONGATED = 3.0


@dataclass(frozen=True)
class MemberShape:
    """What the criteria need to know of one member of a contact."""

    #: Its wall or plate thickness [m], or None where it is not known.
    thickness: float | None
    #: Where its ends are (a beam's two; none for a plate).
    ends: tuple[tuple[float, float, float], ...] = ()
    #: How far its solid reaches from its axis [m]: half its cross-section's diagonal.
    reach: float = 0.0


def _positive(values) -> list[float]:
    out = []
    for v in values:
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if f > 0.0 and np.isfinite(f):
            out.append(f)
    return out


def member_shape(member: Any) -> MemberShape | None:
    """A rebuilt member's shape: a plate's thickness, a beam's thinnest wall or flange, ends and
    reach. None for anything else."""
    from ada import Beam, Plate

    if isinstance(member, Plate):
        t = _positive([getattr(member, "t", None)])
        return MemberShape(thickness=t[0] if t else None, reach=(t[0] / 2.0 if t else 0.0))
    if isinstance(member, Beam):
        sec = member.section
        walls = _positive([getattr(sec, k, None) for k in ("wt", "t_w", "t_ftop", "t_fbtn")])
        h = _positive([getattr(sec, "h", None)])
        w = _positive([getattr(sec, "w_top", None), getattr(sec, "w_btn", None)])
        r = _positive([getattr(sec, "r", None)])
        if h or w:
            reach = 0.5 * float(np.hypot(h[0] if h else 0.0, max(w) if w else 0.0))
        else:
            reach = r[0] if r else 0.0
        ends = tuple(tuple(float(c) for c in n.p) for n in (member.n1, member.n2))
        return MemberShape(thickness=min(walls) if walls else None, ends=ends, reach=reach)
    return None


def mesh_shape(vertices: Any) -> MemberShape | None:
    """A member's shape read off its mesh (``(n, 3)`` vertices): a plate-like mesh's thinnest extent
    is its thickness; an elongated mesh is a beam with its two ends on its principal axis, whose wall
    the mesh does not tell. None for fewer than two distinct points."""
    pts = np.asarray(vertices, dtype=float).reshape(-1, 3)
    if len(pts) < 2:
        return None
    centroid = pts.mean(axis=0)
    _, _, vt = np.linalg.svd(pts - centroid, full_matrices=False)
    proj = (pts - centroid) @ vt.T
    extent = np.zeros(3)
    extent[: proj.shape[1]] = proj.max(axis=0) - proj.min(axis=0)
    if extent[0] <= 0.0:
        return None
    if extent[1] > 0.0 and extent[0] / extent[1] < _ELONGATED:
        thin = float(extent[2])
        return MemberShape(thickness=thin if thin > 0.0 else None, reach=thin / 2.0)
    axis = vt[0]
    ends = tuple(tuple(float(c) for c in centroid + axis * float(x)) for x in (proj[:, 0].min(), proj[:, 0].max()))
    return MemberShape(thickness=None, ends=ends, reach=0.5 * float(np.hypot(extent[1], extent[2])))


def _patch_points(patches) -> np.ndarray:
    pts: list[list[float]] = []
    for patch in patches or ():
        get = patch.get if isinstance(patch, Mapping) else lambda k, _p=patch: getattr(_p, k, None)
        points = get("points1")
        if points is None:
            points = get("points_incoming") if get("points_incoming") is not None else get("points")
        for q in points or ():
            pts.append([float(c) for c in q[:3]])
    return np.asarray(pts, dtype=float).reshape(-1, 3)


def patch_width(contact_area: float, patches) -> float | None:
    """The contact's width: its area over the longest chord across its patch points. None where the
    patches have fewer than two distinct points (nothing to measure the width by)."""
    pts = _patch_points(patches)
    if len(pts) < 2:
        return None
    chord = float(np.max(np.linalg.norm(pts[:, None, :] - pts[None, :, :], axis=-1)))
    if chord <= 0.0:
        return None
    return float(contact_area) / chord


def runs_into(a: MemberShape | None, b: MemberShape | None, centre, margin: float = 0.0) -> bool:
    """Whether either member's END is at the contact: one runs into the other. An end is there when
    the contact's centre is within the two members' reach of it (the end face and the other member's
    side, from their axes), plus ``margin``."""
    if a is None or b is None:
        return False
    c = np.asarray(centre, dtype=float)
    for this, other in ((a, b), (b, a)):
        reach = this.reach + other.reach + max(float(margin), 0.0)
        for end in this.ends:
            if float(np.linalg.norm(c - np.asarray(end, dtype=float))) <= reach:
                return True
    return False


def is_edge_contact(
    contact_area: float,
    patches,
    centre,
    a: MemberShape | None,
    b: MemberShape | None,
    *,
    ratio: float,
    margin: float = 0.0,
) -> bool:
    """Whether a contact is a graze to drop: its patch narrower than ``ratio`` times the thinner
    member's thickness (else :data:`FALLBACK_THICKNESS`), and neither member running into the other.
    Never with ``ratio`` 0, nor for a contact whose width cannot be measured."""
    if ratio <= 0.0:
        return False
    width = patch_width(contact_area, patches)
    if width is None:
        return False
    if runs_into(a, b, centre, margin):
        return False
    known = [s.thickness for s in (a, b) if s is not None and s.thickness]
    thickness = min(known) if known else FALLBACK_THICKNESS
    return width < ratio * thickness
