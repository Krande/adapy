"""The convex-contact clash check: core's checker for members whose SOLIDS touch or overlap.

TWO CHECKS, TWO QUESTIONS. Core's own checker (``adapy``) is a CONNECTIVITY check: shared nodes, axes
that meet, plates beside beams -- which members are meant to connect. The convex-contact check answers
a different question: which members' solids actually touch or overlap, and by how much -- the contact
a connection is sized from (nearest points, normal, penetration depth, contact patch).

ITS INPUT IS THE VIEWER GLB, NOT THE MEMBER MODEL. The solids it measures are the ones the viewer
shows, read straight out of the GLB by adacpp (``adacpp.clash.find_glb_contacts``) -- the same bytes
and the same C++ in a browser (wasm) and on a worker, so both find the same clashes. A run is a
sidecar document (``ada.clash.sidecar``); ``ada.clash.contacts`` runs it and hands its clashes to a
builder; ``/scopes/{scope}/clash-contacts`` is its REST surface. It is therefore NOT a pass over a
rebuilt member model: :func:`convex_contact_pass` refuses, by name, to run inside ``run_clash_check``.

The checker is still registered (:func:`register`) -- so the panel offers it and routing knows it as
core's -- and this module keeps the ROLE rules a detail applies to a run's clash once its members are
bound (:func:`found_from_contact`):

ROLES. A contact names which member is INCOMING (the one whose end is at the contact) and which is
LANDING: the member end nearest the contact point is the incoming member's, and that end is the
joint's ``end``. A plate has no ends; its centroid stands in for both, so a plate lands unless it is
nearer than either beam end. Where the roles cannot be resolved the contact is still reported, with
the roles left unset.

ORDER. The contact is written incoming-first: ``near_points[0]`` is on the incoming member and
``normal`` points from incoming towards landing. ``penetration_depth`` is the overlap, positive.
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

import numpy as np

from ada.clash.builtin_names import CONVEX_CONTACT_CHECKER, CONVEX_CONTACT_PASS
from ada.config import logger

__all__ = [
    "DEFAULT_MIN_CONTACT_AREA",
    "DEFAULT_SECURITY_MARGIN",
    "available",
    "convex_contact_pass",
    "found_from_contact",
    "pick_incoming",
    "register",
]

DEFAULT_SECURITY_MARGIN = 0.01
DEFAULT_MIN_CONTACT_AREA = 1e-6


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


def _roles(m1, m2, near1: np.ndarray):
    """``(incoming, landing, end)`` -- the member whose anchor is nearest the contact point lands on
    the other -- or ``None`` where either member has no anchors. ``end`` is None for a plate."""
    picked = pick_incoming(_anchors(m1), _anchors(m2), near1)
    if picked is None:
        return None
    which, anchor, end = picked
    incoming, landing = (m1, m2) if which == 0 else (m2, m1)
    if incoming is landing:
        return None
    return incoming, landing, anchor, end


def pick_incoming(a1, a2, near1) -> tuple[int, np.ndarray, str | None] | None:
    """Which of two members is INCOMING, from their anchors (``[(point, end)]``, see :func:`_anchors`):
    ``(0 | 1, anchor, end)`` -- the member with the anchor nearest ``near1`` -- or ``None`` where either
    has no anchors. The one rule, whether the anchors come from rebuilt members or from the members'
    meshes (``ada.clash.contacts.mesh_anchors``)."""
    if a1 is None or a2 is None:
        return None
    near1 = np.asarray(near1, dtype=float)
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


def found_from_contact(members: Sequence[Any], c: dict, security_margin: float):
    """One measured contact as the ``_Found`` a clash pass returns, written incoming-first.

    ``c`` is the contact in the engine's order: ``i`` / ``j`` index ``members``, ``near1`` is on member
    ``i``, ``normal`` points from ``i`` to ``j`` and ``penetration_depth`` is signed (negative overlap).
    """
    from ada.clash.identify import _Found

    m1, m2 = members[c["i"]], members[c["j"]]
    near1, near2 = np.asarray(c["near1"]), np.asarray(c["near2"])
    normal = np.asarray(c["normal"])

    resolved = _roles(m1, m2, near1)
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
    if end is not None:
        contact["extras"] = {"incoming_end": end}

    return _Found(
        members=ordered,
        centre=tuple(float(v) for v in (near1 + near2) / 2.0),
        landing=landing,
        contact=contact,
        roles={incoming.name: "incoming", landing.name: "landing"} if resolved is not None else None,
        ends={incoming.name: end} if end is not None else None,
    )


def convex_contact_pass(part, options, *, beams=None, plates=None) -> Iterable[Any]:
    """Refuses: the convex-contact check measures the viewer GLB, not a rebuilt member model."""
    raise ValueError(
        "the convex-contact check runs on the viewer GLB, not on a member model: run it through "
        "POST /scopes/{scope}/clash-contacts (or in the browser), which keeps the run as a sidecar"
    )


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
            needs_backend=False,
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
                "fabricated. Runs in the browser or on a worker; each run is kept beside its model."
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
                    "help": "Contacts with a smaller patch than this (a corner grazing a face) are dropped.",
                },
            ),
            uses_core_options=False,
            priority=10,
        )
    )
    _registered = True
    return True
