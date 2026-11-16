"""I-girder to I-girder joint modelling for the procedural *detail* model.

The simulation-grade model (``lod="sim"``) renders girders as bare swept
profiles that merely touch at their shared nodes. The detail model
(``lod="detail"``) upgrades every girder-girder intersection into a modelled
JOINT that carries VISIBLE connective geometry: a gusset/end plate sized from
the girder section plus fillet weld beads along the flange-contact lines.

Detection reuses ``assembly.connections.find(joint_func=detail_joint_map)`` —
the numpy clash-check path (``beam_clash_check`` / ``are_beams_connected`` /
``beam_cross_check``), which is entirely OCC-free. The emitted geometry is a
``Plate`` (via ``Connection.add_plate``) plus ``Weld`` beads (via
``Connection.add_weld``); ``Weld.solid_geom`` delegates to ``PrimExtrude`` so the
whole path tessellates in the libtess2/NGEOM stream (and wasm) without OCC.

``JointBase.get_all_physical_objects`` returns only the beams, so the joint
object itself never ships the gusset/welds. Each :class:`GirderJoint` therefore
parks its connective geometry in a ``Connection(Part)`` (``.connection``) that
the compile pass collects under a single ``ada.Part("Joints")`` added to the
assembly — that is what tessellates.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, List, Union

import numpy as np

from ada.api.connections import Connection, JointBase, JointReqChecker
from ada.config import get_logger
from ada.core.vector_utils import is_parallel, unit_vector

from ._geometry import rounded_point_key as _centre_key

if TYPE_CHECKING:
    from ada import Beam

logger = get_logger()

# Fraction of the girder section height used for the square gusset half-extent
# and the weld-bead half-length. Keeps the detail proportional to the members.
_GUSSET_HALF_FACTOR = 0.5
# Floor for weld throat / gusset thickness so thin webs still yield visible beads.
_MIN_THROAT = 6e-3
_MIN_GUSSET_T = 8e-3


# Interior fraction (of a girder's length) a crossing must sit inside BOTH spans to
# count as a mid-span "+" crossing; anything closer to an endpoint is a node/T
# junction already found by connections.find.
_INTERIOR_EPS = 1e-2
_CROSS_TOL = 0.1  # out-of-plane tolerance for two girder axes to count as crossing


def is_frame_member(beam) -> bool:
    """True for a PRIMARY frame member (girder / column) eligible for gusset,
    end-plate and base-plate detailing.

    Excludes SECONDARY members — HP bulb-flat stringers/stiffeners and other
    ANGULAR sections — which are direction-classified as ``"Girder"`` (they run
    horizontal) but must never receive connection plates or welds. Keyed on the
    section (an ``HP…`` name or an ``ANGULAR`` type), so it holds regardless of the
    member's grid role."""
    from ada.sections.categories import BaseTypes

    sec = getattr(beam, "section", None)
    if sec is None:
        return True
    if (getattr(sec, "name", "") or "").upper().startswith("HP"):
        return False
    return getattr(sec, "type", None) != BaseTypes.ANGULAR


def eval_joint_req(joint: type[JointBase], intersecting_members: List["Beam"]) -> bool:
    """True when ``intersecting_members`` satisfy ``joint``'s member-type /
    count requirements (mirrors ``ada.param_models.basic_joints.eval_joint_req``)."""
    jrc = JointReqChecker(intersecting_members, joint)
    return jrc.eval_joint_req()


def collect_girder_joints(
    assembly, *, weld_leg: float | None = None, gusset_t: float | None = None
) -> List["GirderJoint"]:
    """All I-girder joints in ``assembly``, as :class:`GirderJoint` objects (their
    ``.connection`` carries the gusset + welds).

    ``assembly.connections.find`` covers shared-node junctions (L-corners) AND
    end-at-mid-span T-junctions, but its bbox pre-filter is node-based, so it MISSES
    a pure "+" crossing where two girders pass through each other's mid-span with no
    node near either — :func:`_find_interior_crossings` adds those. Endpoints stay
    owned by ``find`` (the interior test excludes them), so nothing is double-counted.

    ``weld_leg`` (fillet throat) and ``gusset_t`` (gusset plate thickness), both in
    METRES, override the section-derived defaults so the detailing UI's knobs reach
    the emitted geometry; ``None`` keeps the historical (section-derived) sizing."""
    assembly.connections.find(joint_func=_make_joint_map(weld_leg, gusset_t))
    joints: List["GirderJoint"] = [c for c in assembly.connections.connections if isinstance(c, GirderJoint)]
    seen = {_centre_key(j.centre) for j in joints}
    joints += _find_interior_crossings(assembly, seen, weld_leg, gusset_t)
    return joints


def _find_interior_crossings(
    assembly, seen: set, weld_leg: float | None = None, gusset_t: float | None = None
) -> List["GirderJoint"]:
    """Girder pairs that cross INSIDE both spans (a "+" crossing) — the case
    ``connections.find`` misses. ``seen`` holds the centre keys already jointed so a
    crossing coincident with an existing joint is not duplicated."""
    from ada import Beam
    from ada.core.clash_check import beam_cross_check

    girders = [
        b
        for b in assembly.get_all_physical_objects(by_type=Beam)
        if getattr(b, "member_type", None) == "Girder" and is_frame_member(b)
    ]
    out: List["GirderJoint"] = []
    for i in range(len(girders)):
        for j in range(i + 1, len(girders)):
            g1, g2 = girders[i], girders[j]
            res = beam_cross_check(g1, g2, _CROSS_TOL)
            if res is None:
                continue
            point, s, t = res
            # Strictly interior of BOTH spans — endpoints are node/T junctions that
            # find() already handled.
            if not (_INTERIOR_EPS < s < 1 - _INTERIOR_EPS and _INTERIOR_EPS < t < 1 - _INTERIOR_EPS):
                continue
            key = _centre_key(point)
            if key in seen:
                continue
            seen.add(key)
            out.append(
                GirderJoint(
                    f"GJx_{i:02d}_{j:02d}",
                    [g1, g2],
                    point,
                    parent=assembly.connections,
                    weld_leg=weld_leg,
                    gusset_t=gusset_t,
                )
            )
    return out


def _make_joint_map(weld_leg: float | None, gusset_t: float | None):
    """A ``connections.find`` ``joint_func`` closure that carries the detailing
    knobs (``weld_leg`` / ``gusset_t``) into each :class:`GirderJoint` it builds —
    ``find`` calls ``joint_func(name, members, centre, parent=...)`` with a fixed
    signature, so the sizing has to ride in via the closure."""

    def joint_map(joint_name, intersecting_members, centre, parent=None) -> Union[JointBase, None]:
        return detail_joint_map(
            joint_name, intersecting_members, centre, parent=parent, weld_leg=weld_leg, gusset_t=gusset_t
        )

    return joint_map


def detail_joint_map(
    joint_name,
    intersecting_members,
    centre,
    parent=None,
    *,
    weld_leg: float | None = None,
    gusset_t: float | None = None,
) -> Union[JointBase, None]:
    """Map a detected beam intersection to a detail joint.

    Mirrors ``ada.param_models.basic_joints.joint_map`` but only knows the
    net-new :class:`GirderJoint` (I-girder to I-girder). Any intersection whose
    member types / count do not match returns ``None`` (skipped by
    ``Connections.find``). ``weld_leg`` / ``gusset_t`` (metres) size the emitted
    gusset when supplied."""
    # Secondary members (HP stringers/stiffeners) never get a connection joint,
    # even where they meet a primary girder — skip any intersection involving one.
    if not all(is_frame_member(m) for m in intersecting_members):
        return None

    joints: list[type[JointBase]] = [GirderJoint]

    for joint in joints:
        if eval_joint_req(joint, intersecting_members):
            return joint(joint_name, intersecting_members, centre, parent=parent, weld_leg=weld_leg, gusset_t=gusset_t)

    member_types = [m.member_type for m in intersecting_members]
    logger.debug(f'No detail joint matched for member types "{member_types}"')
    return None


class GirderJoint(JointBase):
    """An I-girder to I-girder joint that emits visible connective geometry.

    ``member_type`` is direction-derived (see ``Beam.member_type``): two
    horizontal girders both classify as ``"Girder"``, so the required combo is
    ``["Girder", "Girder"]``. Beyond the base joint bookkeeping this builds a
    plate and fillet weld beads, parked in a ``Connection(Part)`` exposed via
    :attr:`connection`:

    * where the other girder ENDS at the main one (a T or an L corner) -- an end
      plate against the main girder's flange tips, the other girder cut back to
      butt it, and fillets ON the steel: the plate to the main girder's top and
      bottom flanges, the other girder's flanges to the plate. They are placed from
      the members' meshes, so the joint point (a centreline node, or a measured
      contact) cannot bury them;
    * where the girders run through each other (a "+" crossing) -- the historical
      marker: a square plate in the main girder's web plane on the joint point and
      a bead along each axis.
    """

    mem_types = ["Girder", "Girder"]
    beamtypes = ["IG", "IG"]
    num_mem = 2

    def __init__(
        self,
        name,
        members: List["Beam"],
        centre,
        parent=None,
        *,
        weld_leg: float | None = None,
        gusset_t: float | None = None,
    ):
        super().__init__(name, members, centre, parent=parent)

        g1 = self.main_mem
        g2 = next(m for m in members if m is not g1)

        self._connection = self._build_connection(name, g1, g2, centre, weld_leg, gusset_t)

    def _build_connection(
        self, name, g1: "Beam", g2: "Beam", centre, weld_leg: float | None = None, gusset_t: float | None = None
    ) -> Connection:
        conn = Connection(f"{name}_conn")

        h = float(g1.section.h)
        # The UI knobs (gusset thickness / weld throat, in metres) win when given;
        # otherwise fall back to the section-web-derived sizing (floored).
        web_t = float(getattr(g1.section, "t_w", 0.0) or 0.0)
        t = max(float(gusset_t) if gusset_t else web_t, _MIN_GUSSET_T)
        throat = max(float(weld_leg) if weld_leg else web_t, _MIN_THROAT)

        seat = _end_seat(g1, g2)
        if seat is not None:
            gusset, welds = self._end_plate(name, g1, g2, seat, t, throat)
        else:
            gusset, welds = self._through_gusset(name, g1, g2, centre, h, t, throat)
        conn.add_plate(gusset)
        for weld in welds:
            conn.add_weld(weld)
        weld_names = [w.name for w in welds]

        # Uniform connection record (matches detailing.py's plated joints) so the
        # take-off / joints overview treats every joint type the same way.
        conn.spec_name = "adapy.girder_gusset"
        conn.metadata["connection_info"] = {
            "name": conn.name,
            "spec_name": "adapy.girder_gusset",
            "member_roles": {"members": [g1.name, g2.name]},
            "plate_names": [gusset.name],
            "weld_names": weld_names,
            "centre": [round(float(v), 4) for v in np.asarray(centre, dtype=float)],
        }
        return conn

    def _end_plate(self, name, g1: "Beam", g2: "Beam", seat: "_Seat", t: float, throat: float):
        """``g2`` ENDS at ``g1`` (a T or an L): an end plate against ``g1``'s flange tips, ``g2`` cut
        back to butt the plate, and fillets on the members' surfaces -- the plate to ``g1``'s flanges
        on its back face, ``g2``'s flanges to the plate on its front face.

        Everything is placed from the members' own geometry (their meshes, in ``g1``'s frame), not
        from the joint point: a centreline model and a clash-measured contact give the same joint.
        """
        from ada import Plate, Weld

        u1, n, z = seat.u1, seat.n, seat.z
        margin = max(3 * throat, 0.015)
        # The incoming's flange footprint along the landing: its width over the sine of the angle.
        half_w = 0.5 * max(seat.inc_width, seat.inc_bot_width) / seat.sin + margin
        # Centred where the incoming's axis meets the plate's front face (abreast of the axes'
        # crossing when the incoming is square to the landing).
        s_hit = (seat.face + t) * seat.cos / seat.sin
        lo_z, hi_z = min(seat.land_bot, seat.inc_bot) - margin, max(seat.land_top, seat.inc_top) + margin

        def at(s: float, y: float, zz: float) -> np.ndarray:
            return seat.origin + s * u1 + y * n + zz * z

        # Thickness goes against the outline's winding normal (``Plate.from_3d_points``): wound so the
        # normal is -n, the plate runs from the flange tips (y = f) outward to y = f + t.
        f = seat.face
        lo_s, hi_s = s_hit - half_w, s_hit + half_w
        pts = [at(lo_s, f, lo_z), at(lo_s, f, hi_z), at(hi_s, f, hi_z), at(hi_s, f, lo_z)]
        if float(np.cross(pts[1] - pts[0], pts[2] - pts[1]) @ n) > 0:
            pts.reverse()
        gusset = Plate.from_3d_points(f"{name}_gusset", [tuple(p) for p in pts], t)

        # The incoming butts the plate's front face: everything of it on the landing's side of that
        # face is cut away. An extrusion rather than a box, because a boolean's own placement does not
        # reach the tessellator and a box is axis-aligned -- a landing at any heading needs the cut
        # turned with it. The profile's y is z x its x, so x runs along -u1 where n is -(z x u1).
        from ada import PrimExtrude

        reach = seat.inc_length + seat.face + t + 1.0
        x_cut = u1 if float(np.cross(z, u1) @ n) > 0 else -u1
        g2.add_boolean(
            PrimExtrude(
                f"{name}_cut",
                [(-reach, -reach), (reach, -reach), (reach, f + t), (-reach, f + t)],
                h=seat.inc_top - seat.inc_bot + 2.0,
                normal=tuple(z),
                origin=tuple(seat.origin + (seat.inc_bot - 1.0) * z),
                xdir=tuple(x_cut),
            )
        )

        welds = []

        def fillet(label, a, b, members, on_normal, leg_dir):
            # The profile's +x points INTO the face the root lies on (``on_normal`` is that face's
            # outward normal); its other leg runs along that face, ``leg_dir``. The profile's y is the
            # extrusion direction x xdir, so the ends are ordered to make it ``leg_dir``.
            xdir = -on_normal
            if float(np.dot(np.cross(b - a, xdir), leg_dir)) < 0:
                a, b = b, a
            welds.append(
                Weld(f"{name}_{label}", p1=tuple(a), p2=tuple(b), xdir=tuple(xdir), throat=throat, members=members)
            )

        # The plate on the landing: along its flanges, clipped to where the landing is.
        s0, s1 = max(lo_s, seat.land_s[0]), min(hi_s, seat.land_s[1])
        fillet("weld_1", at(s0, f, seat.land_top), at(s1, f, seat.land_top), (gusset, g1), z, -n)
        if seat.land_bottom_flange:
            fillet("weld_2", at(s0, f, seat.land_bot), at(s1, f, seat.land_bot), (gusset, g1), -z, -n)
        # The incoming's flanges on the plate: across its cut end.
        for label, width, level, leg, present in (
            ("weld_3", seat.inc_width, seat.inc_top, z, True),
            ("weld_4", seat.inc_bot_width, seat.inc_bot, -z, seat.inc_bottom_flange),
        ):
            if present:
                half = 0.5 * width / seat.sin
                fillet(label, at(s_hit - half, f + t, level), at(s_hit + half, f + t, level), (g2, gusset), n, leg)
        return gusset, welds

    def _through_gusset(self, name, g1: "Beam", g2: "Beam", centre, h: float, t: float, throat: float):
        """The girders run THROUGH each other (a "+" crossing): no member ends here to seat a plate
        against, so the historical marker stays -- a square plate in ``g1``'s web plane on the joint
        point and a bead along each axis through it."""
        from ada import Plate, Weld

        c = np.asarray(centre, dtype=float)
        u1 = np.asarray(unit_vector(g1.xvec), dtype=float)
        u2 = np.asarray(unit_vector(g2.xvec), dtype=float)
        z = np.array([0.0, 0.0, 1.0])
        half = h * _GUSSET_HALF_FACTOR
        a = half * u1
        b = half * z
        pts = [c - a - b, c + a - b, c + a + b, c - a + b]
        gusset = Plate.from_3d_points(f"{name}_gusset", [tuple(p) for p in pts], t)
        welds = [
            Weld(f"{name}_weld_1", p1=tuple(c - a), p2=tuple(c + a), xdir=tuple(z), throat=throat, members=(g1, g2))
        ]
        if not is_parallel(u1, u2):
            a2 = half * u2
            welds.append(
                Weld(
                    f"{name}_weld_2", p1=tuple(c - a2), p2=tuple(c + a2), xdir=tuple(z), throat=throat, members=(g1, g2)
                )
            )
        return gusset, welds

    @property
    def connection(self) -> Connection:
        """The ``Connection(Part)`` carrying this joint's gusset plate + welds."""
        return self._connection


class _Seat:
    """Where an incoming girder ENDS against a landing girder, in the landing's frame: ``origin`` on
    the landing's axis abreast of the incoming's axis, ``u1`` along the landing, ``n`` across it
    toward the incoming, ``z`` up. Lengths are offsets in that frame, measured on the members'
    meshes: ``face`` the landing's flange tips on the incoming's side, ``land_top`` / ``land_bot``
    its top and bottom faces, ``inc_top`` / ``inc_bot`` the incoming's; ``land_s`` the landing's
    extent along ``u1``."""

    __slots__ = (
        "origin",
        "u1",
        "n",
        "z",
        "sin",
        "cos",
        "face",
        "land_top",
        "land_bot",
        "land_s",
        "land_bottom_flange",
        "inc_top",
        "inc_bot",
        "inc_width",
        "inc_bot_width",
        "inc_bottom_flange",
        "inc_length",
    )

    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


def _mesh_vertices(beam) -> np.ndarray | None:
    try:
        mesh = beam.to_trimesh()
    except Exception:  # noqa: BLE001 - no tessellation: no seat, the through marker instead
        return None
    if hasattr(mesh, "geometry"):
        mesh = mesh.to_geometry() if hasattr(mesh, "to_geometry") else mesh.dump(concatenate=True)
    v = np.asarray(mesh.vertices, dtype=float)
    return v if len(v) else None


def _end_seat(landing: "Beam", incoming: "Beam") -> "_Seat | None":
    """How ``incoming`` ends against ``landing``, or None where it does not: it runs through the
    landing, runs alongside it, or the members are not horizontal girders.

    The incoming's END is the one nearer the landing's axis; it ends AT the landing when that end is
    no further past the axis than the landing's flange tips on the far side (a centreline model
    puts it on the axis, a modelled one at the tips or short of them)."""
    z = np.array([0.0, 0.0, 1.0])
    u1 = np.asarray(unit_vector(landing.xvec), dtype=float)
    if abs(float(u1 @ z)) > 1e-3:
        return None
    a = np.asarray(landing.n1.p, dtype=float)
    across = np.cross(z, u1)
    e1, e2 = np.asarray(incoming.n1.p, dtype=float), np.asarray(incoming.n2.p, dtype=float)
    d1, d2 = float((e1 - a) @ across), float((e2 - a) @ across)
    near, far, d_near, d_far = (e1, e2, d1, d2) if abs(d1) <= abs(d2) else (e2, e1, d2, d1)
    if abs(d_far) < 1e-6:
        return None
    n = across if d_far > 0 else -across
    u2 = far - near
    length = float(np.linalg.norm(u2))
    if length < 1e-9:
        return None
    u2 = u2 / length
    sin = float(u2 @ n)
    if sin < np.sin(np.radians(10.0)) or abs(float(u2 @ z)) > 1e-3:
        return None

    v_land, v_inc = _mesh_vertices(landing), _mesh_vertices(incoming)
    if v_land is None or v_inc is None:
        return None

    # Abreast of the incoming's axis where it crosses the landing's axis (in plan).
    s_cross = float((near - a) @ u1) - float((near - a) @ n) * float(u2 @ u1) / sin
    origin = a + s_cross * u1
    local = lambda v: np.column_stack([(v - origin) @ u1, (v - origin) @ n, (v - origin) @ z])  # noqa: E731
    land, inc = local(v_land), local(v_inc)
    face = float(land[:, 1].max())
    d_end = float((near - origin) @ n)
    if d_end > face + 1e-3 or d_end < float(land[:, 1].min()) - 1e-3:
        return None  # it stops short of the landing, or runs out past its far side: not seated here
    tol = 1e-4
    land_top, land_bot = float(land[:, 2].max()), float(land[:, 2].min())
    bottom_tips = land[np.abs(land[:, 2] - land_bot) < tol, 1]
    inc_top, inc_bot = float(inc[:, 2].max()), float(inc[:, 2].min())
    # The incoming's flange widths, square to its own axis.
    side = np.cross(z, u2)
    inc_side = (v_inc - near) @ side
    top_w = np.ptp(inc_side[np.abs(inc[:, 2] - inc_top) < tol])
    bot_w = np.ptp(inc_side[np.abs(inc[:, 2] - inc_bot) < tol])
    s_land = land[:, 0]
    return _Seat(
        origin=origin,
        u1=u1,
        n=n,
        z=z,
        sin=sin,
        cos=float(u2 @ u1),
        face=face,
        land_top=land_top,
        land_bot=land_bot,
        land_s=(float(s_land.min()), float(s_land.max())),
        land_bottom_flange=bool(len(bottom_tips)) and float(bottom_tips.max()) > face - tol,
        inc_top=inc_top,
        inc_bot=inc_bot,
        inc_width=float(top_w),
        inc_bot_width=float(bot_w),
        # A flange, not a T's stem: as wide as the top one.
        inc_bottom_flange=float(bot_w) > 0.5 * float(top_w),
        inc_length=length,
    )
