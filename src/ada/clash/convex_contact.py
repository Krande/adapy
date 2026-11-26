"""The convex-contact clash check: core's checker for members whose SOLIDS touch or overlap.

TWO CHECKS, TWO QUESTIONS. Core's own checker (``adapy``) is a CONNECTIVITY check: shared nodes, axes
that meet, plates beside beams -- which members are meant to connect. The convex-contact check answers
a different question: which members' solids actually touch or overlap, and by how much -- the contact
a connection is sized from (nearest points, normal, penetration depth, contact patch).

TWO INPUTS, ONE CHECK.

* THE VIEWER GLB, which is what the viewer's Clashes tab measures. The solids are the ones the viewer
  shows, read straight out of the GLB by adacpp (``adacpp.clash.find_glb_contacts``) -- the same bytes
  and the same C++ in a browser (wasm) and on a worker, so both find the same clashes. A run is a
  sidecar document (``ada.clash.sidecar``); ``ada.clash.contacts`` runs it and hands its clashes to a
  builder; ``/scopes/{scope}/clash-contacts`` is its REST surface.
* A MEMBER MODEL, in process: :func:`convex_contact_pass` -- the pass ``run_clash_check`` runs when
  this checker is picked -- tessellates the beams and plates and measures their convex hulls
  (``ada.clash.mesh_contacts``). The way a connection generator detailing a model in its own process
  checks it.

Both judge their contacts by the same criteria (``ada.clash.contact_criteria``), with the same
options. This module keeps the ROLE rules a contact is written by once its members are known
(:func:`found_from_contact`):

ROLES. A contact names which member is INCOMING (the one whose end is at the contact) and which is
LANDING. The contact PATCH decides first where it can (:func:`pick_incoming`): a face patch whose plane
runs along a beam's axis lies on that beam's SIDE, so where it does so for one beam and not the other,
the other is incoming -- its end is on the first one's side. Otherwise (a line contact, a beam on a
plate, two beams side by side, no patches) the member end nearest the contact point is the incoming
member's, and that end is the joint's ``end``. A plate has no ends; its centroid stands in for both, so
a plate lands unless it is nearer than either beam end. Where the roles cannot be resolved the contact
is still reported, with the roles left unset.

ORDER. The contact is written incoming-first: ``near_points[0]`` is on the incoming member and
``normal`` points from incoming towards landing. ``penetration_depth`` is the overlap, positive.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

import numpy as np

from ada.clash._seq import as_items
from ada.clash.builtin_names import CONVEX_CONTACT_CHECKER, CONVEX_CONTACT_PASS
from ada.clash.contact_criteria import (
    DEFAULT_KEEP_PENETRATION_DEPTH,
    DEFAULT_MIN_CONTACT_AREA,
    DEFAULT_MIN_CONTACT_WIDTH_RATIO,
)
from ada.clash.mesh_contacts import DEFAULT_SECURITY_MARGIN
from ada.config import logger

__all__ = [
    "DEFAULT_KEEP_PENETRATION_DEPTH",
    "DEFAULT_MIN_CONTACT_AREA",
    "DEFAULT_MIN_CONTACT_WIDTH_RATIO",
    "DEFAULT_SECURITY_MARGIN",
    "available",
    "convex_contact_pass",
    "found_from_contact",
    "patches_incoming_first",
    "pick_incoming",
    "register",
]

# DEFAULT_SECURITY_MARGIN (10 mm, ``ada.clash.mesh_contacts``),
# DEFAULT_MIN_CONTACT_AREA (200 mm²), DEFAULT_MIN_CONTACT_WIDTH_RATIO (half the thinner wall) and
# DEFAULT_KEEP_PENETRATION_DEPTH (2 mm): what counts as a contact, from ``ada.clash.contact_criteria``.


def available() -> bool:
    """Whether the installed adacpp can run the check (it measures the viewer GLB)."""
    from ada.clash.contacts import available as _available

    return _available()


def _anchors(member) -> list[tuple[np.ndarray, str | None]] | None:
    """Where a member can be met: a beam's two ends, or a plate's centroid (twice, endless)."""
    from ada import Beam, Plate

    if isinstance(member, Beam):
        return [(np.asarray(member.n1.p, dtype=float), "start"), (np.asarray(member.n2.p, dtype=float), "end")]
    if isinstance(member, Plate):
        centroid = np.asarray(member.poly.get_centroid(), dtype=float)
        return [(centroid, None), (centroid, None)]
    return None


def _roles(m1, m2, near1: np.ndarray, patches=None):
    """``(incoming, landing, end)`` -- the member whose end the contact patch is on, else the one whose
    anchor is nearest the contact point (:func:`pick_incoming`) -- or ``None`` where either member has
    no anchors. ``end`` is None for a plate."""
    picked = pick_incoming(_anchors(m1), _anchors(m2), near1, patches=patches)
    if picked is None:
        return None
    which, anchor, end = picked
    incoming, landing = (m1, m2) if which == 0 else (m2, m1)
    if incoming is landing:
        return None
    return incoming, landing, anchor, end


#: The least area a contact patch must have to say anything about the roles [m^2]: a line contact's
#: patch (two points, or a row of them) has none, and says nothing about which member's end is there.
MIN_ROLE_PATCH_AREA = 1e-5

#: A face patch whose normal is within this many degrees of square to a beam's axis lies on that beam's
#: side: its plane runs along the beam, so it cannot be the beam's end. An incoming meets its landing at
#: 20 degrees or more, so its end face's normal is never more than 70 degrees off its own axis.
SIDE_ON_DEG = 80.0


def _polygon_area(points: np.ndarray) -> float:
    if len(points) < 3:
        return 0.0
    rel = points - points.mean(axis=0)
    return float(np.linalg.norm(np.cross(rel, np.roll(rel, -1, axis=0)).sum(axis=0)) / 2.0)


def _face_patch(patches) -> tuple[np.ndarray, np.ndarray] | None:
    """``(unit normal, centroid)`` of the largest patch with area (:data:`MIN_ROLE_PATCH_AREA`), or None.

    ``patches`` are ``{"normal", "points"}`` mappings (or objects with those attributes, a sidecar's
    ``ContactPatch`` with ``points1``): only the normal's line and the polygon's area matter here, so
    which member's side of the patch the points are on does not."""
    best: tuple[float, np.ndarray, np.ndarray] | None = None
    for patch in as_items(patches):
        get = patch.get if isinstance(patch, Mapping) else lambda k, _p=patch: getattr(_p, k, None)
        normal, points = get("normal"), get("points")
        if points is None:
            points = get("points1") if get("points1") is not None else get("points_incoming")
        if normal is None or points is None:
            continue
        pts = np.asarray([[float(c) for c in list(q)[:3]] for q in as_items(points)], dtype=float).reshape(-1, 3)
        n = np.asarray([float(c) for c in list(as_items(normal))[:3]], dtype=float)
        if not np.linalg.norm(n) or len(pts) < 3:
            continue
        area = _polygon_area(pts)
        if area >= MIN_ROLE_PATCH_AREA and (best is None or area > best[0]):
            best = (area, n / np.linalg.norm(n), pts.mean(axis=0))
    return None if best is None else (best[1], best[2])


def _side_on(anchors, normal: np.ndarray) -> bool | None:
    """Whether a face patch of normal ``normal`` lies on the SIDE of the member with ``anchors``: its
    plane runs along the member's axis (:data:`SIDE_ON_DEG`). None for a member with no axis (a plate,
    a stubby part's centroid): the patch says nothing about it."""
    if len(anchors) != 2 or anchors[0][1] is None or anchors[1][1] is None:
        return None
    axis = np.asarray(anchors[1][0], dtype=float) - np.asarray(anchors[0][0], dtype=float)
    if not np.linalg.norm(axis):
        return None
    cos = abs(float(np.dot(axis / np.linalg.norm(axis), normal)))
    return cos < np.cos(np.radians(SIDE_ON_DEG))


def pick_incoming(a1, a2, near1, *, patches=None) -> tuple[int, np.ndarray, str | None] | None:
    """Which of two members is INCOMING, from their anchors (``[(point, end)]``, see :func:`_anchors`):
    ``(0 | 1, anchor, end)``, or ``None`` where either has no anchors. The one rule, whether the
    anchors come from rebuilt members or from the members' meshes (``ada.clash.contacts.mesh_anchors``).

    1. The contact's ``patches``, where they decide it: the largest patch with area lies on the side of
       one beam (its plane along that beam's axis, :func:`_side_on`) and not on the other's -- the other
       beam's END is on the first one's side, so the other is incoming, at its end nearer the patch. A
       member its end is nearest to the contact point can still be the landing this way: a brace cut
       to run along a post, its end on the post's side a short way up from the post's foot.
    2. Otherwise -- no patch with area (a line contact), a member with no axis (a plate), both beams
       side-on (side by side) or neither -- the member with the anchor nearest ``near1``."""
    if a1 is None or a2 is None:
        return None
    near1 = np.asarray(near1, dtype=float)
    face = _face_patch(patches)
    if face is not None:
        normal, centre = face
        side = (_side_on(a1, normal), _side_on(a2, normal))
        if side in ((True, False), (False, True)):
            which = 0 if side[1] else 1
            anchors = a1 if which == 0 else a2
            point, end = min(anchors, key=lambda a: float(np.linalg.norm(centre - np.asarray(a[0], dtype=float))))
            return which, np.asarray(point, dtype=float), end
    candidates = [(0, p, end) for p, end in a1] + [(1, p, end) for p, end in a2]
    # First of the nearest, in (m1 start, m1 end, m2 start, m2 end) order, so a tie is reproducible.
    distances = [float(np.linalg.norm(near1 - np.asarray(p, dtype=float))) for _, p, _ in candidates]
    which, anchor, end = candidates[distances.index(min(distances))]
    return which, np.asarray(anchor, dtype=float), end


def _incoming_angle(incoming, end: str, normal: np.ndarray) -> float | None:
    """Degrees between the incoming beam's direction at the contact end and ``normal``."""
    from ada import Direction

    p_end = np.asarray(incoming.n2.p if end == "end" else incoming.n1.p, dtype=float)
    centre = (np.asarray(incoming.n1.p, dtype=float) + np.asarray(incoming.n2.p, dtype=float)) / 2.0
    direction = p_end - centre
    if not np.linalg.norm(direction) or not np.linalg.norm(normal):
        return None
    return float(Direction(*direction).get_normalized().get_angle(Direction(*normal), as_degrees=True))


def patches_incoming_first(patches, flipped: bool) -> list[dict]:
    """A contact's patch polygons as a ``ClashContact`` carries them (``extras["patches"]``):
    ``{"normal", "points_incoming", "points_landing"}`` each, plain lists. ``patches`` are in the
    engine's order (``points1`` on member ``i``, the normal from ``i`` to ``j``) -- mappings, a run's
    ``ContactPatch``es, numpy arrays or lists; ``flipped`` where member ``j`` is the incoming one."""
    out = []
    for patch in as_items(patches):
        get = patch.get if isinstance(patch, Mapping) else lambda k, _p=patch: getattr(_p, k, None)
        normal = [float(v) for v in as_items(get("normal"))]
        p1 = [[float(v) for v in q] for q in as_items(get("points1"))]
        p2 = [[float(v) for v in q] for q in as_items(get("points2"))]
        out.append(
            {
                "normal": [-v for v in normal] if flipped else normal,
                "points_incoming": p2 if flipped else p1,
                "points_landing": p1 if flipped else p2,
            }
        )
    return out


def found_from_contact(members: Sequence[Any], c: dict, security_margin: float):
    """One measured contact as the ``_Found`` a clash pass returns, written incoming-first.

    ``c`` is the contact in the engine's order: ``i`` / ``j`` index ``members``, ``near1`` is on member
    ``i``, ``normal`` points from ``i`` to ``j`` and ``penetration_depth`` is signed (negative overlap).
    Its ``patches``, where it has them, are carried incoming-first in ``contact.extras["patches"]``
    (:func:`patches_incoming_first`).
    """
    from ada.clash.identify import _Found

    m1, m2 = members[c["i"]], members[c["j"]]
    near1, near2 = np.asarray(c["near1"]), np.asarray(c["near2"])
    normal = np.asarray(c["normal"])

    resolved = _roles(m1, m2, near1, patches=c.get("patches"))
    angle = None
    if resolved is not None:
        incoming, landing, _, end = resolved
        flipped = incoming is m2
        if end is not None:
            angle = _incoming_angle(incoming, end, normal)
            if angle is not None and flipped:
                angle = 180.0 - angle  # measured against the m1 -> m2 normal; restated incoming-first
        ordered = [incoming, landing]
    else:
        incoming = landing = end = None
        flipped = False
        ordered = [m1, m2]

    first, second = (near2, near1) if flipped else (near1, near2)
    contact: dict[str, Any] = {
        "normal": tuple(float(v) for v in (-normal if flipped else normal)),
        # Overlap as a positive depth; a gap inside the margin keeps its magnitude.
        "penetration_depth": abs(float(c["penetration_depth"])),
        "near_points": [tuple(float(v) for v in first), tuple(float(v) for v in second)],
        "contact_area": float(c["contact_area"]),
        "security_margin": float(security_margin),
    }
    if angle is not None:
        contact["incoming_angle_deg"] = angle
    extras: dict[str, Any] = {}
    if end is not None:
        extras["incoming_end"] = end
    if c.get("patches") is not None:
        extras["patches"] = patches_incoming_first(c["patches"], flipped)
    if extras:
        contact["extras"] = extras

    return _Found(
        members=ordered,
        centre=tuple(float(v) for v in (near1 + near2) / 2.0),
        landing=landing,
        contact=contact,
        roles={incoming.name: "incoming", landing.name: "landing"} if resolved is not None else None,
        ends={incoming.name: end} if end is not None else None,
    )


def convex_contact_pass(part, options, *, beams=None, plates=None) -> Iterable[Any]:
    """The pass over a member model: one ``_Found`` per contact between the beams and plates of
    ``part`` (``ada.clash.mesh_contacts.find_member_contacts``), judged by the checker's options
    (``ClashOptions.checker_options``) exactly as a run over the viewer GLB is.

    ``beams`` / ``plates`` are what core collected from ``part``; a caller that passes neither gets
    the walk."""
    from ada import Beam, Plate
    from ada.clash.mesh_contacts import criteria_options, find_member_contacts

    if beams is None and plates is None:
        beams = list(part.get_all_physical_objects(by_type=Beam))
        plates = list(part.get_all_physical_objects(by_type=Plate))
    members = [*(beams or ()), *(plates or ())]
    found = find_member_contacts(members, **criteria_options(getattr(options, "checker_options", None)))
    if found.skipped:
        logger.warning(f"clash: {len(found.skipped)} member(s) could not be tessellated and were not checked")
    return found.found


_registered = False


def register() -> bool:
    """Register the checker (and its pass name) -- only where adacpp can run the check. Idempotent."""
    global _registered
    if _registered:
        return True
    if not available():
        logger.debug("clash: adacpp cannot measure a viewer GLB here, so the convex-contact checker is not offered")
        return False
    from ada.clash.passes import (
        ClashChecker,
        ClashPass,
        register_checker,
        register_pass,
    )

    register_pass(
        ClashPass(
            name=CONVEX_CONTACT_PASS,
            label="Solids touching or overlapping: contact normal, penetration depth and contact patch",
            checker=CONVEX_CONTACT_CHECKER,
            # Over a member model it tessellates the members (a run over a GLB needs none).
            needs_backend=True,
            fn=convex_contact_pass,
            priority=30,
        )
    )
    register_checker(
        ClashChecker(
            name=CONVEX_CONTACT_CHECKER,
            label="Convex contact",
            passes=(CONVEX_CONTACT_PASS,),
            description=(
                "Interference between the members' solids as the viewer shows them: finds members that "
                "touch or overlap, with the contact normal, penetration depth, contact-patch area and "
                "patch outline a connection is sized from. For detail models, where parts meet as "
                "fabricated. Runs in the browser or on a worker; each run is kept beside its model. "
                "Also runs over a member model in process, on each member's convex hull."
            ),
            options=(
                {
                    "key": "security_margin",
                    "label": "Security margin",
                    "type": "number",
                    "default": DEFAULT_SECURITY_MARGIN,
                    "min": 0,
                    "unit": "m",
                    "help": "Solids closer than this count as touching, so members a hair apart are still found.",
                },
                {
                    "key": "min_contact_area",
                    "label": "Min contact area",
                    "type": "number",
                    "default": DEFAULT_MIN_CONTACT_AREA,
                    "min": 0,
                    "unit": "m²",
                    "help": "Contacts with a smaller patch than this (a corner or a short edge grazing a face) "
                    "are dropped. 2e-4 m² is 200 mm².",
                },
                {
                    "key": "min_contact_width_ratio",
                    "label": "Min contact width",
                    "type": "number",
                    "default": DEFAULT_MIN_CONTACT_WIDTH_RATIO,
                    "min": 0,
                    "unit": "× thickness",
                    "help": "Contacts whose patch (area over its longest chord) is narrower than this fraction of "
                    "the thinner member's wall or plate thickness -- an edge grazing a face -- are dropped, "
                    "unless one member runs into the other. 0 keeps them.",
                },
                {
                    "key": "keep_penetration_depth",
                    "label": "Keep overlaps deeper than",
                    "type": "number",
                    "default": DEFAULT_KEEP_PENETRATION_DEPTH,
                    "min": 0,
                    "unit": "m",
                    "help": "A member overlapping another deeper than this -- one driven into the other, as a "
                    "stick model's brace into its column -- is kept whatever its contact area and width. "
                    "0 turns this off.",
                },
            ),
            uses_core_options=False,
            priority=10,
        )
    )
    _registered = True
    return True
