"""The built-in ``adapy-default`` detailing engine.

Detailing is the fabrication-level pass that adds CONNECTION DETAILS (joints,
plates, welds, bolts) at member intersections, AFTER the topology/procedural
compile has produced the structural members::

    procedural engine -> structural members -> DETAILING engine -> members + joints -> GLB

It runs IN-PROCESS as a second stage of the same ``procedural_build`` job — on
the live ``ada.Assembly`` (Beam/Plate objects with ``section`` + ``member_type``
+ node connectivity), right where the old girder-joint pass ran, before
``to_glb()``. The GLB is triangles only; the joint detection needs the section
family + member types + node connectivity, which is why detailing operates on
the model, not the mesh.

The entrypoint is::

    detail(assembly: ada.Part, options: dict) -> ada.Part

It iterates the three starter joint collectors, parks every emitted
``Connection`` part under a single ``ada.Part("Joints")`` on the assembly
(exactly as the historical ``_apply_girder_joints`` did) and returns the
assembly so the SAME job re-tessellates once with everything in it (no double
tessellation, no overlay).

Starter joint set (the SteelStru demo scope):

1. **Girder–girder gusset** — reuses :func:`ada.topo_model.detail_joints.collect_girder_joints`
   verbatim (gusset :class:`~ada.Plate` + fillet :class:`~ada.Weld` beads).
2. **Beam–column end plate** — :class:`EndPlateJoint`: an end plate at the girder
   end sized to the girder section, a fillet weld girder-web -> end-plate, and a
   bolt group modelled metadata-first (a ``ConnectionInfo``-style record).
3. **Column base plate** — :class:`BasePlateJoint`: a rectangular base plate at
   the column's lowest node, fillet welds around the column footprint, anchor
   bolts metadata-first.

Everything is OCC-free: detection is the numpy clash path
(``beam_cross_check``), and ``Plate``/``Weld`` geometry tessellates through the
libtess2/NGEOM stream (``Weld.solid_geom`` -> ``PrimExtrude``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, List, Union

import numpy as np

from ada.api.connections import Connection, JointBase
from ada.config import get_logger
from ada.core.vector_utils import unit_vector

if TYPE_CHECKING:
    from ada import Beam, Part

logger = get_logger()

# Defaults for the per-joint-type option knobs (fed from the doc / UI in later
# phases; sensible fabrication-grade values here).
DEFAULT_WELD_LEG = 6e-3  # fillet weld leg / throat floor [m]
DEFAULT_BASE_PLATE_OVERHANG = 0.05  # base-plate overhang beyond the footprint [m]
_MIN_THROAT = 6e-3
_MIN_PLATE_T = 10e-3

# Endpoint / on-axis tolerances for the endplate + base-plate detection.
_ENDPOINT_EPS = 1e-2  # a girder param s within this of 0/1 counts as its endpoint
_CROSS_TOL = 0.1  # out-of-plane tolerance for two axes to count as crossing
_BASE_Z_TOL = 1e-3  # a column lower node this close to the base plane is a footing


def _beams_of_type(assembly: "Part", mtype: str) -> List["Beam"]:
    """All physical beams in ``assembly`` whose direction-derived ``member_type``
    is ``mtype`` (``"Girder"`` / ``"Column"`` / ``"Brace"``)."""
    from ada import Beam

    return [b for b in assembly.get_all_physical_objects(by_type=Beam) if getattr(b, "member_type", None) == mtype]


def _lower_node(beam: "Beam"):
    """The beam node with the smaller z (a column's footing end)."""
    return beam.n1 if float(beam.n1.p[2]) <= float(beam.n2.p[2]) else beam.n2


def _is_box(beam: "Beam") -> bool:
    """True for a beam with a BOX (hollow rectangular) section — the members the
    simplified box joint clash-cuts."""
    from ada.sections.categories import BaseTypes

    return getattr(beam.section, "type", None) == BaseTypes.BOX


def _box_beams(assembly: "Part") -> List["Beam"]:
    """All physical box-section beams in ``assembly``."""
    from ada import Beam

    return [b for b in assembly.get_all_physical_objects(by_type=Beam) if _is_box(b)]


# ── collectors ────────────────────────────────────────────────────────


def collect_girder_joints(assembly: "Part") -> list:
    """Girder–girder gusset joints — reuses the historical detail-joint pass
    verbatim (see :mod:`ada.topo_model.detail_joints`)."""
    from .detail_joints import collect_girder_joints as _collect

    return list(_collect(assembly))


def collect_endplate_joints(assembly: "Part", options: dict) -> List["EndPlateJoint"]:
    """Beam–column end-plate joints: a girder whose ENDPOINT is coincident with a
    column axis frames into that column with an end plate. Detection is the
    OCC-free ``beam_cross_check`` (the girder param ``s`` near 0/1 = its endpoint;
    the column param ``t`` inside ``[0, 1]`` = the point is on the column)."""
    from ada.core.clash_check import beam_cross_check

    weld_leg = float(options.get("weld_leg", DEFAULT_WELD_LEG))
    girders = _beams_of_type(assembly, "Girder")
    columns = _beams_of_type(assembly, "Column")
    out: List["EndPlateJoint"] = []
    for gi, g in enumerate(girders):
        for ci, c in enumerate(columns):
            res = beam_cross_check(g, c, _CROSS_TOL)
            if res is None:
                continue
            point, s, t = res
            at_endpoint = s < _ENDPOINT_EPS or s > 1.0 - _ENDPOINT_EPS
            on_column = -_ENDPOINT_EPS <= t <= 1.0 + _ENDPOINT_EPS
            if not (at_endpoint and on_column):
                continue
            out.append(EndPlateJoint(f"EP_{gi:02d}_{ci:02d}", [g, c], point, weld_leg=weld_leg))
    return out


def collect_box_joints(assembly: "Part", options: dict) -> List["BoxJoint"]:
    """Box-to-box clash joints: two box beams that meet, where one beam's END is
    coincident with the other beam. Deterministic incoming/landing assignment —
    the beam whose endpoint lands on the other is the *incoming* member (on a pure
    corner where both are endpoints, the lower-indexed beam is incoming). Detected
    OCC-free via ``beam_cross_check`` (see :meth:`BoxJoint.apply` for the cut)."""
    from ada.core.clash_check import beam_cross_check

    boxes = _box_beams(assembly)
    out: List["BoxJoint"] = []
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            b1, b2 = boxes[i], boxes[j]
            res = beam_cross_check(b1, b2, _CROSS_TOL)
            if res is None:
                continue
            point, s, t = res
            s_end = s < _ENDPOINT_EPS or s > 1.0 - _ENDPOINT_EPS
            t_end = t < _ENDPOINT_EPS or t > 1.0 - _ENDPOINT_EPS
            s_on = -_ENDPOINT_EPS <= s <= 1.0 + _ENDPOINT_EPS
            t_on = -_ENDPOINT_EPS <= t <= 1.0 + _ENDPOINT_EPS
            # b1's end meets b2's body (T), or b2's end meets b1's body, or a corner
            # (both ends) — the lower-indexed beam (b1) is incoming by convention.
            if s_end and t_on:
                incoming, landing = b1, b2
            elif t_end and s_on:
                incoming, landing = b2, b1
            else:
                continue
            out.append(BoxJoint(f"BX_{i:02d}_{j:02d}", [b1, b2], point, incoming=incoming, landing=landing))
    return out


def collect_base_plate_joints(assembly: "Part", options: dict) -> List["BasePlateJoint"]:
    """Column base-plate joints: every Column beam whose LOWER node sits at the
    model's base elevation (the global minimum column-foot z) gets a base plate
    with fillet welds around its footprint."""
    weld_leg = float(options.get("weld_leg", DEFAULT_WELD_LEG))
    overhang = float(options.get("base_plate_overhang", DEFAULT_BASE_PLATE_OVERHANG))
    columns = _beams_of_type(assembly, "Column")
    if not columns:
        return []
    base_z = min(float(_lower_node(c).p[2]) for c in columns)
    out: List["BasePlateJoint"] = []
    for ci, c in enumerate(columns):
        foot = _lower_node(c)
        if abs(float(foot.p[2]) - base_z) > _BASE_Z_TOL:
            continue
        out.append(BasePlateJoint(f"BP_{ci:02d}", [c], foot.p, overhang=overhang, weld_leg=weld_leg))
    return out


# ── joint types ───────────────────────────────────────────────────────


def _bolt_metadata(conn: Connection, spec_name: str, member_roles: dict, plate_names: list, weld_names: list) -> None:
    """Attach a ``ConnectionInfo``-style record (bolts modelled metadata-first in
    Phase 1) onto the connection's metadata so the inspector can surface the
    bolt group / member roles without a first-class fastener primitive."""
    conn.spec_name = spec_name
    conn.metadata["connection_info"] = {
        "name": conn.name,
        "spec_name": spec_name,
        "member_roles": member_roles,
        "plate_names": plate_names,
        "weld_names": weld_names,
    }


class EndPlateJoint(JointBase):
    """A beam-to-column end-plate connection.

    ``member_type`` is direction-derived: a horizontal girder framing into a
    vertical column gives the combo ``["Girder", "Column"]`` (the column is the
    landing member via :meth:`JointBase._get_landing_member`). Emits an end plate
    sized to the girder section at the girder end, a fillet weld along the girder
    web line, and a metadata-first bolt group — all parked in a
    ``Connection(Part)`` exposed via :attr:`connection`.
    """

    mem_types = ["Girder", "Column"]
    beamtypes = ["IG", "IG"]
    num_mem = 2

    def __init__(self, name, members: List["Beam"], centre, *, weld_leg: float = DEFAULT_WELD_LEG, parent=None):
        super().__init__(name, members, centre, parent=parent)
        col = self.main_mem
        gir = next(m for m in members if m is not col)
        self._connection = self._build_connection(name, gir, col, centre, weld_leg)

    def _build_connection(self, name, gir: "Beam", col: "Beam", centre, weld_leg: float) -> Connection:
        from ada import Plate, Weld

        conn = Connection(f"{name}_conn")
        c = np.asarray(centre, dtype=float)
        u_g = np.asarray(unit_vector(gir.xvec), dtype=float)
        z = np.array([0.0, 0.0, 1.0])
        w_dir = unit_vector(np.cross(u_g, z))

        h = float(gir.section.h)
        w = float(getattr(gir.section, "w_top", 0.0) or h)
        half_h, half_w = 0.5 * h, 0.5 * w
        t_plate = max(2.0 * float(getattr(gir.section, "t_w", 0.0) or 0.0), _MIN_PLATE_T)

        a, b = half_w * w_dir, half_h * z
        pts = [c - a - b, c + a - b, c + a + b, c - a + b]
        endplate = Plate.from_3d_points(f"{name}_endplate", [tuple(p) for p in pts], t_plate)
        conn.add_plate(endplate)

        throat = max(float(getattr(gir.section, "t_w", 0.0) or 0.0), weld_leg, _MIN_THROAT)
        weld = Weld(
            f"{name}_weld",
            p1=tuple(c - b),
            p2=tuple(c + b),
            xdir=tuple(u_g),
            throat=throat,
            members=(gir, col),
        )
        conn.add_weld(weld)

        _bolt_metadata(
            conn,
            spec_name="adapy.beam_column_endplate",
            member_roles={"incoming": [gir.name], "landing": [col.name]},
            plate_names=[endplate.name],
            weld_names=[weld.name],
        )
        return conn

    @property
    def connection(self) -> Connection:
        """The ``Connection(Part)`` carrying this joint's end plate + weld."""
        return self._connection


class BasePlateJoint(JointBase):
    """A column base-plate connection at the column's footing node.

    A single vertical Column (``mem_types=["Column"]``, ``num_mem=1``). Emits a
    rectangular base plate sized to the column footprint plus an overhang, fillet
    welds around the four footprint edges, and metadata-first anchor bolts — all
    parked in a ``Connection(Part)`` exposed via :attr:`connection`.
    """

    mem_types = ["Column"]
    beamtypes = ["IG"]
    num_mem = 1

    def __init__(
        self,
        name,
        members: List["Beam"],
        centre,
        *,
        overhang: float = DEFAULT_BASE_PLATE_OVERHANG,
        weld_leg: float = DEFAULT_WELD_LEG,
        parent=None,
    ):
        super().__init__(name, members, centre, parent=parent)
        col = self.main_mem
        self._connection = self._build_connection(name, col, centre, overhang, weld_leg)

    def _build_connection(self, name, col: "Beam", centre, overhang: float, weld_leg: float) -> Connection:
        from ada import Plate, Weld

        conn = Connection(f"{name}_conn")
        c = np.asarray(centre, dtype=float)
        x = np.array([1.0, 0.0, 0.0])
        y = np.array([0.0, 1.0, 0.0])
        z = np.array([0.0, 0.0, 1.0])

        sec = col.section
        foot = max(float(sec.h), float(getattr(sec, "w_top", 0.0) or 0.0), _MIN_PLATE_T)
        half_f = 0.5 * foot
        half_p = half_f + overhang

        pts = [c - half_p * x - half_p * y, c + half_p * x - half_p * y, c + half_p * x + half_p * y, c - half_p * x + half_p * y]
        baseplate = Plate.from_3d_points(f"{name}_baseplate", [tuple(p) for p in pts], max(2.0 * _MIN_PLATE_T, _MIN_PLATE_T))
        conn.add_plate(baseplate)

        throat = max(weld_leg, _MIN_THROAT)
        corners = [c - half_f * x - half_f * y, c + half_f * x - half_f * y, c + half_f * x + half_f * y, c - half_f * x + half_f * y]
        weld_names: list = []
        for i in range(4):
            p1, p2 = corners[i], corners[(i + 1) % 4]
            weld = Weld(f"{name}_weld_{i + 1}", p1=tuple(p1), p2=tuple(p2), xdir=tuple(z), throat=throat, members=(col,))
            conn.add_weld(weld)
            weld_names.append(weld.name)

        _bolt_metadata(
            conn,
            spec_name="adapy.column_base_plate",
            member_roles={"landing": [col.name]},
            plate_names=[baseplate.name],
            weld_names=weld_names,
        )
        return conn

    @property
    def connection(self) -> Connection:
        """The ``Connection(Part)`` carrying this joint's base plate + welds."""
        return self._connection


class BoxJoint(JointBase):
    """A simplified box-to-box clash joint.

    Unlike the plated/welded joints above, this one adds NO connective geometry —
    its detailing is deliberately minimal: a single boolean cut on the *incoming*
    box beam using the *landing* member's volume, so the two box beams no longer
    interpenetrate at the junction. This mirrors weld-gen's ``box.box_to_box``
    ``incoming.add_boolean(boolean_cut)`` step, WITHOUT the weld — the bool cut
    only — and is the minimal testbed for swapping joint definitions on the same
    demo (box sections + a box joint), later comparable to weld-gen's box specs.
    """

    mem_types = ["Girder", "Girder"]
    beamtypes = ["BG", "BG"]
    num_mem = 2

    def __init__(self, name, members: List["Beam"], centre, *, incoming: "Beam", landing: "Beam", parent=None):
        super().__init__(name, members, centre, parent=parent)
        self._incoming = incoming
        self._landing = landing

    @property
    def incoming(self) -> "Beam":
        return self._incoming

    @property
    def landing(self) -> "Beam":
        return self._landing

    def apply(self) -> None:
        """Boolean-cut the incoming beam with the landing member's volume so the
        two box beams no longer clash. Uses a ``PrimBox`` of the landing member's
        bounding box (OCC-free, mirroring :meth:`JointBase._cut_intersecting_member`);
        the incoming beam's tessellation folds the cut in at encode time."""
        from ada import PrimBox

        p1, p2 = self._landing.bbox().minmax
        self._incoming.add_boolean(PrimBox(f"{self.name}_cut", p1, p2))


# ── entrypoint ────────────────────────────────────────────────────────


def _collect(fn, assembly: "Part", *args) -> list:
    """Run a joint collector, downgrading any failure to a warning so one bad
    joint never sinks the compile (mirroring the historical girder-joint pass)."""
    try:
        return list(fn(assembly, *args))
    except Exception as exc:  # noqa: BLE001 - a joint failure must never sink the compile
        logger.warning("detailing: %s skipped: %s", getattr(fn, "__name__", fn), exc)
        return []


def detail(assembly: "Part", options: Union[dict, None] = None) -> "Part":
    """Apply the built-in ``adapy-default`` detailing engine to a compiled
    structural ``assembly``: add the enabled starter joints (girder–girder gusset,
    beam–column end plate, column base plate) as a single ``ada.Part("Joints")``
    and return the assembly.

    ``options`` carries per-joint-type toggles (``girder_gusset`` /
    ``beam_column_endplate`` / ``column_base_plate``, all default-on;
    ``box_to_box`` opt-in / default-off) and params (``weld_leg``,
    ``base_plate_overhang``). Idempotent per assembly is NOT guaranteed — call
    once per build (as the compile stage does)."""
    import ada

    options = dict(options or {})
    joints: list = []
    if options.get("girder_gusset", True):
        joints += _collect(collect_girder_joints, assembly)
    if options.get("beam_column_endplate", True):
        joints += _collect(collect_endplate_joints, assembly, options)
    if options.get("column_base_plate", True):
        joints += _collect(collect_base_plate_joints, assembly, options)

    joint_parts = [j.connection for j in joints]
    if joint_parts:
        assembly.add_part(ada.Part("Joints") / joint_parts)

    # Box joints add no connective geometry — they only bool-cut the incoming box
    # beam so the box members no longer interpenetrate (opt-in, default off).
    if options.get("box_to_box", False):
        for bj in _collect(collect_box_joints, assembly, options):
            bj.apply()
    return assembly
