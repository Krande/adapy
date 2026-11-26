"""Convex contacts measured in process: between meshes, or between a model's members.

The public entry point to the engine (``adacpp.clash.find_convex_contacts``) for a caller that holds
meshes or members rather than a viewer GLB -- a connection generator detailing a model, the
convex-contact pass over a member model (``ada.clash.convex_contact``). It applies the same
:class:`~ada.clash.contact_criteria.ContactCriteria` a run over a GLB is judged by, at detection
time, so every contact it returns is one the checker keeps.

* :func:`find_mesh_contacts` -- one mesh per member, ``(vertices, triangles)``; contacts in the
  engine's order (``i < j``, ``near1`` on mesh ``i``, the normal from ``i`` to ``j``, the distance
  signed: negative is overlap), each with its patch polygons.
* :func:`find_member_contacts` -- beams and plates: each member tessellated, measured, and every
  contact returned as the ``_Found`` a clash pass returns -- incoming-first, roles and end resolved,
  the contact a ``ClashContact`` with its patches in ``extras["patches"]``
  (``ada.clash.convex_contact.found_from_contact``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

import numpy as np

from ada.clash._seq import as_items
from ada.clash.contact_criteria import (
    DEFAULT_KEEP_PENETRATION_DEPTH,
    DEFAULT_MIN_CONTACT_AREA,
    DEFAULT_MIN_CONTACT_WIDTH_RATIO,
    ContactCriteria,
    MemberShape,
    member_shape,
    mesh_shape,
)
from ada.clash.result import ClashContact
from ada.clash.sidecar import ContactPatch, patches_from
from ada.config import logger

__all__ = [
    "DEFAULT_LINE_CONTACT_THICKNESS",
    "DEFAULT_SECURITY_MARGIN",
    "MemberContacts",
    "MeshContact",
    "MeshContacts",
    "available",
    "criteria_options",
    "find_member_contacts",
    "find_mesh_contacts",
    "member_mesh",
]

#: [m] solids closer than this count as touching.
DEFAULT_SECURITY_MARGIN = 0.01
#: [m] the width the engine gives a LINE contact (an edge on a face), so it has an area.
DEFAULT_LINE_CONTACT_THICKNESS = 0.001

Vec3 = tuple[float, float, float]


def available() -> bool:
    """Whether the installed adacpp can measure meshes in process."""
    try:
        import adacpp.clash  # noqa: PLC0415 - probing an optional backend

        return hasattr(adacpp.clash, "find_convex_contacts")
    except Exception:  # noqa: BLE001 - an adacpp that cannot import is one that cannot answer
        return False


@dataclass(frozen=True)
class MeshContact:
    """One contact between meshes ``i`` and ``j`` (``i < j``), in the engine's order."""

    members: tuple[int, int]
    near_points: tuple[Vec3, Vec3]
    #: From mesh ``i`` towards mesh ``j``.
    normal: Vec3
    #: The engine's signed distance [m]: negative is overlap, positive a gap inside the margin.
    distance: float
    #: [m²]
    contact_area: float
    patches: tuple[ContactPatch, ...] = ()

    @property
    def centre(self) -> Vec3:
        a, b = self.near_points
        return ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0, (a[2] + b[2]) / 2.0)

    @property
    def penetration_depth(self) -> float:
        """The overlap, positive -- core's ``ClashContact`` convention."""
        return max(-self.distance, 0.0)

    def engine_dict(self) -> dict:
        """The contact as ``ada.clash.convex_contact.found_from_contact`` reads it."""
        i, j = self.members
        return {
            "i": i,
            "j": j,
            "near1": self.near_points[0],
            "near2": self.near_points[1],
            "normal": self.normal,
            "penetration_depth": self.distance,
            "contact_area": self.contact_area,
            "patches": self.patches,
        }


@dataclass(frozen=True)
class MeshContacts:
    """What one measurement found, and what it dropped."""

    contacts: tuple[MeshContact, ...]
    #: Pairs whose boxes came within the margin (the broad phase).
    candidate_pairs: int = 0
    #: Dropped by area: by the engine's floor, and then by ``min_contact_area`` with the penetration.
    below_min_area: int = 0
    #: Dropped as edge grazes (``min_contact_width_ratio``).
    below_min_width: int = 0
    #: ``(i, j, reason)`` for pairs the engine could not evaluate.
    failures: tuple[tuple[int, int, str], ...] = ()
    #: Meshes with no solid hull.
    degenerate: tuple[int, ...] = ()
    #: The criteria the contacts were judged by.
    criteria: ContactCriteria = field(default_factory=ContactCriteria)


def _shape_lookup(
    shapes: Sequence[MemberShape | None] | Callable[[int], MemberShape | None] | None, vertices: Sequence[Any]
) -> Callable[[int], MemberShape | None]:
    cache: dict[int, MemberShape | None] = {}

    def shape_of(k: int) -> MemberShape | None:
        if k not in cache:
            if callable(shapes):
                cache[k] = shapes(k)
            elif shapes is not None:
                cache[k] = shapes[k]
            else:
                cache[k] = mesh_shape(vertices[k])
        return cache[k]

    return shape_of


def find_mesh_contacts(
    vertices: Sequence[Any],
    triangles: Sequence[Any] | None = None,
    *,
    security_margin: float = DEFAULT_SECURITY_MARGIN,
    min_contact_area: float = DEFAULT_MIN_CONTACT_AREA,
    min_contact_width_ratio: float = DEFAULT_MIN_CONTACT_WIDTH_RATIO,
    keep_penetration_depth: float = DEFAULT_KEEP_PENETRATION_DEPTH,
    hull: bool = True,
    line_contact_thickness: float = DEFAULT_LINE_CONTACT_THICKNESS,
    shapes: Sequence[MemberShape | None] | Callable[[int], MemberShape | None] | None = None,
) -> MeshContacts:
    """Every contact among the meshes ``(vertices[k], triangles[k])`` that the checker keeps.

    ``vertices[k]`` is ``(n, 3)``. With ``hull`` (the default) each mesh's convex hull is taken from
    its vertices and ``triangles`` may be omitted; without it the meshes must be convex hulls
    already and ``triangles[k]`` ``(m, 3)`` indexes them.

    The contacts are judged by ``min_contact_area``, ``min_contact_width_ratio`` and
    ``keep_penetration_depth`` (``ada.clash.contact_criteria``). The width criterion needs each
    member's wall or plate thickness and ends: ``shapes`` gives them (a sequence by mesh index, or a
    callable asked only for meshes in a contact over the area); left out, they are read off the
    meshes (``contact_criteria.mesh_shape``). Pass ``contact_criteria.member_shape(member)`` where
    the members are known -- a beam's mesh does not tell its wall."""
    import adacpp.clash  # noqa: PLC0415 - optional backend; see available()

    criteria = ContactCriteria(
        min_contact_area=float(min_contact_area),
        min_contact_width_ratio=float(min_contact_width_ratio),
        keep_penetration_depth=float(keep_penetration_depth),
        security_margin=float(security_margin),
    )
    verts = [np.ascontiguousarray(np.asarray(v, dtype=float).reshape(-1, 3)) for v in vertices]
    tris = None if triangles is None else [np.asarray(t).reshape(-1, 3) for t in triangles]
    found = adacpp.clash.find_convex_contacts(
        verts,
        tris,
        security_margin=float(security_margin),
        min_contact_area=criteria.engine_min_contact_area,
        line_contact_thickness=float(line_contact_thickness),
        hull=bool(hull),
    )
    pairs = found["pairs"]
    pairs = pairs.tolist() if hasattr(pairs, "tolist") else list(pairs)
    measured = [
        MeshContact(
            members=(int(i), int(j)),
            near_points=(_vec3(found["near1"][q]), _vec3(found["near2"][q])),
            normal=_vec3(found["normal"][q]),
            distance=float(found["penetration_depth"][q]),
            contact_area=float(found["contact_area"][q]),
            patches=patches_from(found["patches"][q]) if "patches" in found else (),
        )
        for q, (i, j) in enumerate(pairs)
    ]
    kept, below_area, below_width = criteria.judge(measured, _shape_lookup(shapes, verts))
    failures = tuple((int(i), int(j), str(reason)) for i, j, reason in as_items(found.get("failures")))
    return MeshContacts(
        contacts=tuple(kept),
        candidate_pairs=int(found.get("candidate_pairs", 0)),
        below_min_area=int(found.get("below_min_area", 0)) + below_area,
        below_min_width=below_width,
        failures=failures,
        degenerate=tuple(int(k) for k in as_items(found.get("degenerate"))),
        criteria=criteria,
    )


def _vec3(v: Any) -> Vec3:
    x, y, z = (float(c) for c in v)
    return (x, y, z)


def member_mesh(member: Any) -> np.ndarray | None:
    """The vertices of a member's solid (``(n, 3)``, global), or None where it cannot be tessellated
    -- logged, so one bad member does not stop a check. The engine measures its convex hull."""
    try:
        mesh = member.to_trimesh()
    except Exception as exc:  # noqa: BLE001 - one member that cannot be tessellated is skipped, said so
        logger.warning(f"clash: could not tessellate {getattr(member, 'name', member)!r}: {type(exc).__name__}: {exc}")
        return None
    verts = np.asarray(getattr(mesh, "vertices", ()), dtype=float).reshape(-1, 3)
    return verts if len(verts) else None


@dataclass
class MemberContacts:
    """The contacts between a model's members (:func:`find_member_contacts`)."""

    #: One ``_Found`` per contact, as a clash pass returns them (incoming-first).
    found: list[Any]
    #: The members that were measured; ``mesh.contacts`` index into it.
    members: list[Any]
    #: The measurement in the engine's order.
    mesh: MeshContacts
    #: Members that could not be tessellated, by name: they were not measured.
    skipped: list[str] = field(default_factory=list)


def find_member_contacts(
    members: Sequence[Any],
    *,
    security_margin: float = DEFAULT_SECURITY_MARGIN,
    min_contact_area: float = DEFAULT_MIN_CONTACT_AREA,
    min_contact_width_ratio: float = DEFAULT_MIN_CONTACT_WIDTH_RATIO,
    keep_penetration_depth: float = DEFAULT_KEEP_PENETRATION_DEPTH,
    line_contact_thickness: float = DEFAULT_LINE_CONTACT_THICKNESS,
    mesh_of: Callable[[Any], Any] | None = None,
) -> MemberContacts:
    """Every contact between ``members`` (beams and plates) that the checker keeps, each as the
    ``_Found`` a clash pass returns: incoming-first, with its roles, end, and a ``ClashContact``
    (near points, normal, positive penetration depth, contact area, security margin, incoming angle,
    and the patch polygons incoming-first in ``extras["patches"]``).

    Each member is tessellated (``mesh_of(member)``, default :func:`member_mesh`) and measured on its
    convex hull. The width criterion judges by the members' own sections and ends
    (``contact_criteria.member_shape``)."""
    from ada.clash.convex_contact import found_from_contact

    measured: list[Any] = []
    meshes: list[np.ndarray] = []
    skipped: list[str] = []
    for member in members:
        verts = (mesh_of or member_mesh)(member)
        if verts is None:
            skipped.append(str(getattr(member, "name", member)))
            continue
        measured.append(member)
        meshes.append(np.asarray(verts, dtype=float).reshape(-1, 3))

    def shape_of(k: int) -> MemberShape | None:
        return member_shape(measured[k]) or mesh_shape(meshes[k])

    result = find_mesh_contacts(
        meshes,
        security_margin=security_margin,
        min_contact_area=min_contact_area,
        min_contact_width_ratio=min_contact_width_ratio,
        keep_penetration_depth=keep_penetration_depth,
        line_contact_thickness=line_contact_thickness,
        shapes=shape_of,
    )
    for i, j, reason in result.failures:
        logger.warning(f"clash: no contact measured between {measured[i].name!r} and {measured[j].name!r}: {reason}")
    found = []
    for c in result.contacts:
        f = found_from_contact(measured, c.engine_dict(), security_margin)
        f.contact = ClashContact.from_dict(f.contact)
        found.append(f)
    return MemberContacts(found=found, members=measured, mesh=result, skipped=skipped)


def criteria_options(options: Mapping[str, Any] | None) -> dict[str, float]:
    """The keyword arguments of :func:`find_member_contacts` a checker's options ask for
    (``ClashOptions.checker_options``), each defaulted where it is not given."""
    opts = dict(options or {})
    criteria = ContactCriteria.from_options(opts)
    security_margin = opts.get("security_margin")
    try:
        margin = float(security_margin) if security_margin not in (None, "") else DEFAULT_SECURITY_MARGIN
    except (TypeError, ValueError):
        margin = DEFAULT_SECURITY_MARGIN
    return {"security_margin": margin, **criteria.to_options()}
