"""What core's convex-contact check counts as a contact, beyond the engine's own area cut.

THE PROBLEM. The engine (adacpp) measures the members' convex hulls and gives a LINE contact -- an
edge lying on a face, or two edges crossing -- a strip ``line_contact_thickness`` wide (1 mm), so it
has an area. Two box members that only graze along an edge then read as a contact of the edge's
length in mm²: a 170 mm edge is about 170 mm². Such grazes are not connections.

TWO CRITERIA, both options of the checker (``ada.clash.convex_contact.register``):

* ``min_contact_area`` (default 200 mm²): smaller patches are dropped.
* ``min_contact_width_ratio`` (default 0.5): a contact whose patch is narrower than this fraction of
  the thinner member's wall or plate thickness is dropped. The WIDTH is the patch's area over its
  longest chord (:func:`patch_width`): a face-to-face patch is as wide as the faces' overlap, a
  line contact as wide as the engine's strip, a two-point patch has no width of its own beyond it.

AND TWO EXCEPTIONS, both for a member running into another:

* by its END -- the way a stick model's member ends on the member it lands on -- it is kept whatever
  its WIDTH (:func:`runs_into`): a member end is a connection even where the hulls only touch along
  an edge.
* by OVERLAPPING it deeper than ``keep_penetration_depth`` (default 2 mm) it is kept whatever its
  AREA and width (:func:`is_run_in`). A rounded 200 mm box brace driven into a column may touch it
  only along its 140 mm flat strip -- 140 mm², under the area. The member-end exemption cannot tell
  that from a graze, because grazes sit at member ends too (a stub's end on the edge of the member
  beside it); the penetration can: a graze touches, a brace driven in overlaps.

Measured on real models, the genuine contacts were all at least 5,000 mm² and 13 mm wide; the false
ones were two-point or 1 mm wide patches of 126-200 mm².

WHERE THE CRITERIA ARE APPLIED. Wherever a contact is judged, by one :class:`ContactCriteria`: the
engine pass over a viewer GLB (``ada.clash.contacts.check_glb``), a run's annotate step
(``ada.clash.contacts.annotate_applicable``) and the pass over a member model
(``ada.clash.mesh_contacts``). With the penetration rule on, the engine is asked for a floor of
:data:`ENGINE_MIN_CONTACT_AREA` and the area is judged after it, with the penetration.

WHERE THE THICKNESS COMES FROM. From the members where they are known (a plate's ``t``, a beam
section's thinnest wall or flange, :func:`member_shape`), else from the members' meshes (a
plate-like mesh's thinnest extent, :func:`mesh_shape`; a beam's mesh does not tell its wall). A run
over a GLB judges by the meshes when it is measured and again by the rebuilt members when it is
annotated. Where neither member's thickness is known, :data:`FALLBACK_THICKNESS` stands in: with the
default ratio a contact must then be 2 mm wide -- twice the strip a line contact is given, and
narrower than any plate edge.

Old runs (whose options name no ``min_contact_width_ratio`` or ``keep_penetration_depth``) are left
as they were measured, also when annotated again (``ContactCriteria.from_options(..., of_run=True)``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

import numpy as np

from ada.clash._seq import as_items

__all__ = [
    "DEFAULT_KEEP_PENETRATION_DEPTH",
    "DEFAULT_MIN_CONTACT_AREA",
    "DEFAULT_MIN_CONTACT_WIDTH_RATIO",
    "ENGINE_MIN_CONTACT_AREA",
    "FALLBACK_THICKNESS",
    "ContactCriteria",
    "MemberShape",
    "engine_min_contact_area",
    "is_below_min_area",
    "is_edge_contact",
    "is_run_in",
    "member_shape",
    "mesh_shape",
    "overlap",
    "patch_width",
    "runs_into",
]

#: The least contact patch area [m²]: 200 mm². Smaller ones are dropped, unless run in.
DEFAULT_MIN_CONTACT_AREA = 2e-4
#: How deep one member must overlap another [m] to be kept whatever its patch: 2 mm.
DEFAULT_KEEP_PENETRATION_DEPTH = 0.002
#: What the engine is asked to drop under where the area is judged after it, with the penetration [m²].
ENGINE_MIN_CONTACT_AREA = 1e-6
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
    """Every point of ``patches`` -- mappings or objects with ``points1`` (an engine result's or a run's
    patch), ``points_incoming`` (a contact's) or ``points``; each a list, tuple or numpy array."""
    pts: list[list[float]] = []
    for patch in as_items(patches):
        get = patch.get if isinstance(patch, Mapping) else lambda k, _p=patch: getattr(_p, k, None)
        points = get("points1")
        if points is None:
            points = get("points_incoming") if get("points_incoming") is not None else get("points")
        for q in as_items(points):
            pts.append([float(c) for c in list(q)[:3]])
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


def overlap(distance: float) -> float:
    """How deep two solids overlap [m], positive, from the engine's SIGNED distance (negative is
    overlap, positive a gap inside the security margin): 0 for a gap or a touch."""
    return max(-float(distance), 0.0)


def is_run_in(distance: float, keep_penetration_depth: float) -> bool:
    """Whether one member is driven into the other: overlapping by more than ``keep_penetration_depth``
    [m]. Never with ``keep_penetration_depth`` 0 (the rule off)."""
    keep = float(keep_penetration_depth or 0.0)
    return keep > 0.0 and overlap(distance) > keep


def engine_min_contact_area(min_contact_area: float, keep_penetration_depth: float) -> float:
    """The area the ENGINE is asked to drop under. With the penetration rule on, the engine cannot
    judge it (it drops by area alone), so it is asked for :data:`ENGINE_MIN_CONTACT_AREA` and
    ``min_contact_area`` is applied after it (:func:`is_below_min_area`)."""
    if float(keep_penetration_depth or 0.0) > 0.0:
        return min(float(min_contact_area), ENGINE_MIN_CONTACT_AREA)
    return float(min_contact_area)


def is_below_min_area(
    contact_area: float, distance: float, *, min_contact_area: float, keep_penetration_depth: float = 0.0
) -> bool:
    """Whether a contact is dropped by its area: under ``min_contact_area`` [m²] and not run in
    (:func:`is_run_in`)."""
    if float(contact_area) >= float(min_contact_area):
        return False
    return not is_run_in(distance, keep_penetration_depth)


def is_edge_contact(
    contact_area: float,
    patches,
    centre,
    a: MemberShape | None,
    b: MemberShape | None,
    *,
    ratio: float,
    margin: float = 0.0,
    distance: float | None = None,
    keep_penetration_depth: float = 0.0,
) -> bool:
    """Whether a contact is a graze to drop: its patch narrower than ``ratio`` times the thinner
    member's thickness (else :data:`FALLBACK_THICKNESS`), and neither member running into the other
    -- by its end (:func:`runs_into`), or, with ``distance`` (signed, negative overlap) and
    ``keep_penetration_depth``, by overlapping it deeper than that (:func:`is_run_in`). Never with
    ``ratio`` 0, nor for a contact whose width cannot be measured."""
    if ratio <= 0.0:
        return False
    if distance is not None and is_run_in(distance, keep_penetration_depth):
        return False
    width = patch_width(contact_area, patches)
    if width is None:
        return False
    if runs_into(a, b, centre, margin):
        return False
    return width < ratio * _thickness(a, b)


def _thickness(a: MemberShape | None, b: MemberShape | None) -> float:
    known = [s.thickness for s in (a, b) if s is not None and s.thickness]
    return min(known) if known else FALLBACK_THICKNESS


def _option(options: Mapping[str, Any], key: str, default: float) -> float:
    raw = options.get(key)
    if raw is None or raw == "":
        return float(default)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return float(default)
    return value if np.isfinite(value) and value >= 0.0 else float(default)


@dataclass(frozen=True)
class ContactCriteria:
    """Every rule a contact is judged by, as one value: what the checker's options ask for.

    The same rules wherever a contact is judged -- the engine pass over a GLB
    (``ada.clash.contacts.check_glb``), a run's annotate step, and the pass over a member model
    (``ada.clash.mesh_contacts``) -- so all three keep the same contacts."""

    #: [m²] smaller patches are dropped, unless run in (:func:`is_below_min_area`).
    min_contact_area: float = DEFAULT_MIN_CONTACT_AREA
    #: Narrower patches than this fraction of the thinner wall are grazes (:func:`is_edge_contact`). 0: off.
    min_contact_width_ratio: float = DEFAULT_MIN_CONTACT_WIDTH_RATIO
    #: [m] a member overlapping another deeper than this is kept whatever its patch. 0: off.
    keep_penetration_depth: float = DEFAULT_KEEP_PENETRATION_DEPTH
    #: [m] the check's security margin: how near a member end must be to count as at the contact.
    security_margin: float = 0.0

    @classmethod
    def from_options(cls, options: Mapping[str, Any] | None, *, of_run: bool = False) -> "ContactCriteria":
        """The criteria a checker's options (``ClashOptions.checker_options``, a run's ``options``) ask for.

        ``of_run``: the options a stored run was measured with. A run made before a rule existed does
        not name it and keeps what it was measured with -- that rule is off for it, not defaulted."""
        opts = dict(options or {})
        absent = 0.0 if of_run else None

        def get(key: str, default: float) -> float:
            if absent is not None and opts.get(key) is None:
                return absent
            return _option(opts, key, default)

        return cls(
            min_contact_area=get("min_contact_area", DEFAULT_MIN_CONTACT_AREA),
            min_contact_width_ratio=get("min_contact_width_ratio", DEFAULT_MIN_CONTACT_WIDTH_RATIO),
            keep_penetration_depth=get("keep_penetration_depth", DEFAULT_KEEP_PENETRATION_DEPTH),
            security_margin=_option(opts, "security_margin", 0.0),
        )

    def to_options(self) -> dict[str, float]:
        """The criteria as a run's options carry them."""
        return {
            "min_contact_area": self.min_contact_area,
            "min_contact_width_ratio": self.min_contact_width_ratio,
            "keep_penetration_depth": self.keep_penetration_depth,
        }

    @property
    def engine_min_contact_area(self) -> float:
        """What the engine itself is asked to drop under (:func:`engine_min_contact_area`)."""
        return engine_min_contact_area(self.min_contact_area, self.keep_penetration_depth)

    def below_area(self, contact_area: float, distance: float) -> str | None:
        """Why a contact is dropped by its area, or None where it is not (:func:`is_below_min_area`)."""
        if not is_below_min_area(
            contact_area,
            distance,
            min_contact_area=self.min_contact_area,
            keep_penetration_depth=self.keep_penetration_depth,
        ):
            return None
        return (
            f"a patch of {float(contact_area) * 1e6:.0f} mm², under {self.min_contact_area * 1e6:.0f} mm², "
            "and not run into the other member"
        )

    def graze(
        self,
        contact_area: float,
        distance: float,
        patches,
        centre,
        a: MemberShape | None,
        b: MemberShape | None,
    ) -> str | None:
        """Why a contact is an edge graze and dropped, or None where it is not (:func:`is_edge_contact`)."""
        if not is_edge_contact(
            contact_area,
            patches,
            centre,
            a,
            b,
            ratio=self.min_contact_width_ratio,
            margin=self.security_margin,
            distance=distance,
            keep_penetration_depth=self.keep_penetration_depth,
        ):
            return None
        width = patch_width(contact_area, patches) or 0.0
        least = self.min_contact_width_ratio * _thickness(a, b)
        return f"a patch {width * 1e3:.1f} mm wide, under {least * 1e3:.1f} mm, and no member end at it: an edge graze"

    def drop_reason(
        self,
        contact_area: float,
        distance: float,
        patches,
        centre,
        shapes: Callable[[], tuple[MemberShape | None, MemberShape | None]] | None = None,
    ) -> str | None:
        """Why a contact is dropped (by area first, then as a graze), or None where it is kept.
        ``shapes`` gives the two members' shapes; asked only when the width is judged, so a caller
        resolves its members only for contacts over the area."""
        reason = self.below_area(contact_area, distance)
        if reason is not None or self.min_contact_width_ratio <= 0.0:
            return reason
        a, b = shapes() if shapes is not None else (None, None)
        return self.graze(contact_area, distance, patches, centre, a, b)

    def judge(self, contacts, shape_of: Callable[[int], MemberShape | None]) -> tuple[list, int, int]:
        """``(kept, below_min_area, below_min_width)`` of ``contacts`` -- anything with ``members``
        (two member indices), ``contact_area``, ``distance`` (signed), ``patches`` and ``centre``, a
        run's ``ContactRecord`` or a ``MeshContact``. ``shape_of(k)`` is member ``k``'s shape, asked
        only for contacts over the area (cache it where it is costly)."""
        kept: list = []
        below_area = below_width = 0
        for c in contacts:
            if self.below_area(c.contact_area, c.distance) is not None:
                below_area += 1
                continue
            if self.min_contact_width_ratio > 0.0:
                i, j = c.members
                reason = self.drop_reason(
                    c.contact_area, c.distance, c.patches, c.centre, lambda _i=i, _j=j: (shape_of(_i), shape_of(_j))
                )
                if reason is not None:
                    below_width += 1
                    continue
            kept.append(c)
        return kept, below_area, below_width
