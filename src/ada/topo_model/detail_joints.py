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
_CENTRE_NDIGITS = 4


def eval_joint_req(joint: type[JointBase], intersecting_members: List["Beam"]) -> bool:
    """True when ``intersecting_members`` satisfy ``joint``'s member-type /
    count requirements (mirrors ``ada.param_models.basic_joints.eval_joint_req``)."""
    jrc = JointReqChecker(intersecting_members, joint)
    return jrc.eval_joint_req()


def _centre_key(point) -> tuple:
    return tuple(round(float(v), _CENTRE_NDIGITS) for v in point)


def collect_girder_joints(assembly) -> List["GirderJoint"]:
    """All I-girder joints in ``assembly``, as :class:`GirderJoint` objects (their
    ``.connection`` carries the gusset + welds).

    ``assembly.connections.find`` covers shared-node junctions (L-corners) AND
    end-at-mid-span T-junctions, but its bbox pre-filter is node-based, so it MISSES
    a pure "+" crossing where two girders pass through each other's mid-span with no
    node near either — :func:`_find_interior_crossings` adds those. Endpoints stay
    owned by ``find`` (the interior test excludes them), so nothing is double-counted."""
    assembly.connections.find(joint_func=detail_joint_map)
    joints: List["GirderJoint"] = [c for c in assembly.connections.connections if isinstance(c, GirderJoint)]
    seen = {_centre_key(j.centre) for j in joints}
    joints += _find_interior_crossings(assembly, seen)
    return joints


def _find_interior_crossings(assembly, seen: set) -> List["GirderJoint"]:
    """Girder pairs that cross INSIDE both spans (a "+" crossing) — the case
    ``connections.find`` misses. ``seen`` holds the centre keys already jointed so a
    crossing coincident with an existing joint is not duplicated."""
    from ada import Beam
    from ada.core.clash_check import beam_cross_check

    girders = [
        b for b in assembly.get_all_physical_objects(by_type=Beam) if getattr(b, "member_type", None) == "Girder"
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
            out.append(GirderJoint(f"GJx_{i:02d}_{j:02d}", [g1, g2], point, parent=assembly.connections))
    return out


def detail_joint_map(joint_name, intersecting_members, centre, parent=None) -> Union[JointBase, None]:
    """Map a detected beam intersection to a detail joint.

    Mirrors ``ada.param_models.basic_joints.joint_map`` but only knows the
    net-new :class:`GirderJoint` (I-girder to I-girder). Any intersection whose
    member types / count do not match returns ``None`` (skipped by
    ``Connections.find``)."""
    joints: list[type[JointBase]] = [GirderJoint]

    for joint in joints:
        if eval_joint_req(joint, intersecting_members):
            return joint(joint_name, intersecting_members, centre, parent=parent)

    member_types = [m.member_type for m in intersecting_members]
    logger.debug(f'No detail joint matched for member types "{member_types}"')
    return None


class GirderJoint(JointBase):
    """An I-girder to I-girder joint that emits visible connective geometry.

    ``member_type`` is direction-derived (see ``Beam.member_type``): two
    horizontal girders both classify as ``"Girder"``, so the required combo is
    ``["Girder", "Girder"]``. Beyond the base joint bookkeeping this builds a
    gusset plate (sized from the main girder section) and two fillet weld beads
    running through the joint along each girder axis, all parked in a
    ``Connection(Part)`` exposed via :attr:`connection`.
    """

    mem_types = ["Girder", "Girder"]
    beamtypes = ["IG", "IG"]
    num_mem = 2

    def __init__(self, name, members: List["Beam"], centre, parent=None):
        super().__init__(name, members, centre, parent=parent)

        g1 = self.main_mem
        g2 = next(m for m in members if m is not g1)

        self._connection = self._build_connection(name, g1, g2, centre)

    def _build_connection(self, name, g1: "Beam", g2: "Beam", centre) -> Connection:
        conn = Connection(f"{name}_conn")

        c = np.asarray(centre, dtype=float)
        u1 = np.asarray(unit_vector(g1.xvec), dtype=float)
        u2 = np.asarray(unit_vector(g2.xvec), dtype=float)
        z = np.array([0.0, 0.0, 1.0])

        h = float(g1.section.h)
        half = h * _GUSSET_HALF_FACTOR
        t = max(float(getattr(g1.section, "t_w", 0.0) or 0.0), _MIN_GUSSET_T)
        throat = max(float(getattr(g1.section, "t_w", 0.0) or 0.0), _MIN_THROAT)

        # Vertical gusset plate in the plane spanned by the main girder axis and
        # the global Z, centred on the joint node (square, side = section height).
        a = half * u1
        b = half * z
        pts = [c - a - b, c + a - b, c + a + b, c - a + b]
        from ada import Plate

        gusset = Plate.from_3d_points(f"{name}_gusset", [tuple(p) for p in pts], t)
        conn.add_plate(gusset)

        # Fillet weld beads running through the joint along each girder axis. The
        # xdir orients the fillet cross-section up (toward +Z); Weld -> PrimExtrude.
        from ada import Weld

        conn.add_weld(
            Weld(
                f"{name}_weld_1",
                p1=tuple(c - a),
                p2=tuple(c + a),
                xdir=tuple(z),
                throat=throat,
                members=(g1, g2),
            )
        )
        if not is_parallel(u1, u2):
            a2 = half * u2
            conn.add_weld(
                Weld(
                    f"{name}_weld_2",
                    p1=tuple(c - a2),
                    p2=tuple(c + a2),
                    xdir=tuple(z),
                    throat=throat,
                    members=(g1, g2),
                )
            )

        return conn

    @property
    def connection(self) -> Connection:
        """The ``Connection(Part)`` carrying this joint's gusset plate + welds."""
        return self._connection
