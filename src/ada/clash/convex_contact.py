"""Convex-contact clash checking: members whose solids touch or overlap, measured.

WHAT IT ANSWERS. Core's own checker looks at axes and at surface distances, which is the right
answer for a model whose members meet exactly. A detail model -- parts modelled as fabricated, that
merely touch or overlap by millimetres -- needs the solids themselves: which members' solids come
within a security margin of each other, and the contact a connection is sized from (the nearest
point on each, the contact normal, the penetration depth and the contact-patch area).

HOW. Each member is tessellated through the active CAD backend, and adacpp (``adacpp.clash``) takes
each member's convex hull and measures every pair with coal's GJK/EPA and contact patch -- compiled
C++, the same code the browser runs through adacpp's wasm module, so a check gives the same contacts
on a worker and in a browser. Nothing here needs trimesh or scipy.

ROLES. A contact names which member is INCOMING (the one whose end is at the contact) and which is
LANDING: the member end nearest the contact point is the incoming member's, and that end is the
joint's ``end``. A plate has no ends; its centroid stands in for both, so a plate lands unless it is
nearer than either beam end. Where the roles cannot be resolved the contact is still reported, with
the roles left unset.

ORDER. The contact is written incoming-first: ``near_points[0]`` is on the incoming member and
``normal`` points from incoming towards landing. ``penetration_depth`` is the overlap, positive.

A CHECKER OF ITS OWN, and capability-free: it runs wherever core runs and adacpp carries the pass,
which is every adacpp-backed process -- including the browser's. It is registered only where adacpp
does carry it (:func:`register`), so a process without it does not offer a checker that would fail.
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
    "find_convex_contacts",
    "member_points",
    "register",
]

DEFAULT_SECURITY_MARGIN = 0.01
DEFAULT_MIN_CONTACT_AREA = 1e-6

_END_NAMES = ("start", "end")


def available() -> bool:
    """Whether the installed adacpp carries the convex-contact pass, hulls included."""
    try:
        import adacpp.clash  # noqa: PLC0415 - probing an optional backend

        return hasattr(adacpp.clash, "find_convex_contacts") and hasattr(adacpp.clash, "convex_hull")
    except Exception:  # noqa: BLE001 - an adacpp that cannot import is one that cannot answer
        return False


def member_points(member) -> np.ndarray:
    """The tessellated solid's vertices of ``member`` in world coordinates, ``(n, 3)`` float64.

    The vertices alone: adacpp takes the convex hull from them, so the triangles are not needed.
    """
    from ada.base.types import GeomRepr
    from ada.visit.tessellate import tessellate_shape

    mesh = tessellate_shape(member.shape_global(GeomRepr.SOLID))
    return np.asarray(mesh.positions, dtype=np.float64).reshape(-1, 3)


def find_convex_contacts(
    members: Sequence[Any],
    security_margin: float = DEFAULT_SECURITY_MARGIN,
    min_contact_area: float = DEFAULT_MIN_CONTACT_AREA,
) -> list[dict]:
    """Every touching / overlapping pair among ``members``, as plain dicts.

    One dict per contact: ``i`` / ``j`` (indices into ``members``, ``i < j``) and the contact as adacpp
    measured it, in ITS order -- ``near1`` on member ``i``, ``near2`` on member ``j``, ``normal`` from
    ``i`` towards ``j``, ``penetration_depth`` signed (negative is overlap) and ``contact_area``. A
    member that cannot be tessellated is logged and touches nothing.
    """
    import adacpp.clash  # noqa: PLC0415 - guarded by available()

    points = []
    for member in members:
        try:
            points.append(member_points(member))
        except Exception as exc:  # noqa: BLE001 - one bad member must not cost the whole check
            logger.warning(f"clash: could not tessellate {getattr(member, 'name', member)!r}: {exc}")
            points.append(np.zeros((0, 3)))

    r = adacpp.clash.find_convex_contacts(
        points, security_margin=float(security_margin), min_contact_area=float(min_contact_area)
    )
    for i, j, reason in r["failures"]:
        logger.warning(f"clash: no contact measured between {members[i].name!r} and {members[j].name!r}: {reason}")
    for k in r["degenerate"]:
        logger.debug(f"clash: {members[k].name!r} has no solid hull (its geometry spans no volume)")

    return [
        {
            "i": int(i),
            "j": int(j),
            "near1": tuple(float(v) for v in r["near1"][q]),
            "near2": tuple(float(v) for v in r["near2"][q]),
            "normal": tuple(float(v) for v in r["normal"][q]),
            "penetration_depth": float(r["penetration_depth"][q]),
            "contact_area": float(r["contact_area"][q]),
        }
        for q, (i, j) in enumerate(r["pairs"].tolist())
    ]


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
    a1, a2 = _anchors(m1), _anchors(m2)
    if a1 is None or a2 is None:
        return None
    candidates = [(m1, m2, p, end) for p, end in a1] + [(m2, m1, p, end) for p, end in a2]
    # First of the nearest, in (m1 start, m1 end, m2 start, m2 end) order, so a tie is reproducible.
    distances = [float(np.linalg.norm(near1 - p)) for _, _, p, _ in candidates]
    incoming, landing, anchor, end = candidates[distances.index(min(distances))]
    if incoming is landing:
        return None
    return incoming, landing, anchor, end


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
    """One measured contact as the ``_Found`` a clash pass returns, written incoming-first."""
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


def _option(options, key: str, default: float) -> float:
    raw = (getattr(options, "checker_options", None) or {}).get(key)
    if raw is None or raw == "":
        return default
    return float(raw)


def convex_contact_pass(part, options, *, beams=None, plates=None) -> Iterable[Any]:
    """The pass: one contact per pair of members whose solids touch or overlap.

    ``beams`` / ``plates`` are the members core collected from ``part``; a caller passing neither
    gets the walk.
    """
    if beams is None and plates is None:
        from ada import Beam, Plate

        beams = list(part.get_all_physical_objects(by_type=Beam))
        plates = list(part.get_all_physical_objects(by_type=Plate))
    members = [*(beams or ()), *(plates or ())]
    if len(members) < 2:
        return []
    security_margin = _option(options, "security_margin", DEFAULT_SECURITY_MARGIN)
    min_contact_area = _option(options, "min_contact_area", DEFAULT_MIN_CONTACT_AREA)
    contacts = find_convex_contacts(members, security_margin, min_contact_area)
    logger.debug(f"clash: convex-contact pass found {len(contacts)} contacts among {len(members)} members")
    return [found_from_contact(members, c, security_margin) for c in contacts]


_registered = False


def register() -> bool:
    """Register the pass and its checker -- only where adacpp carries the pass. Idempotent."""
    global _registered
    if _registered:
        return True
    if not available():
        logger.debug("clash: adacpp has no convex-contact pass here, so the convex-contact checker is not offered")
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
            # The members are tessellated through the CAD backend.
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
                "Interference between each member's convex hull: finds members whose solids touch or "
                "overlap, with the contact normal, penetration depth and contact-patch area a connection "
                "is sized from. For detail models, where parts meet as fabricated."
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
