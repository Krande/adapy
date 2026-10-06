"""GeniE supports on the way in: support points, support curves and rigid-link supports.

This reader used to read ``<support_point>`` only, with a spring's stiffness as 0.0 and a rotated
``<local_system>`` read as if global; ``<support_curve>`` and ``<support_rigid_link>`` were dropped
and nothing said so (the user's model came in with "no supports" where GeniE has four rigid links).
It now reads all three into :mod:`ada.fem.concept.constraints`, and everything it cannot hold is
reported by name through :mod:`ada.fem.formats.conversion_report` and left out -- never
approximated.

What GeniE's XML means, measured on GeniE V8.13-02 (the probe models are the test fixtures
``genie_supports_all_kinds.xml`` and ``genie_supports_frames.xml``; the Sesam records GeniE meshed
them into -- BNBCD, BNDOF/BNTRCOS, MGSPRNG, BLDEP, BNDISPL -- are the oracle for every reading
below):

* **Frames.** The six ``<boundary_condition>`` codes are in the support's ``<local_system>``: a
  point with ``x' = (0, 1, 0)`` and dx fixed gave BNBCD 1 at dof 1 with a BNTRCOS of ``x' = y``.
  The concept classes hold global DOFs only, so a rotated support is converted when the rotation
  maps the codes exactly onto global axes (a 90 degree turn, or e.g. dz alone under a turn about
  z) and refused otherwise (a 45 degree turn holding dx' only). A prescribed DOF in a rotated
  frame is refused too: its value, in a load case, is in that frame.
* **Curves.** A curve with ``<line_orientation><constant_local_system_line_orientation/>`` is in
  its ``<local_system>`` (measured with the identity and with ``x' = y``). Without
  ``<line_orientation>`` it has GeniE's default ("guide") orientation: ``x'`` along the curve,
  ``y' = z x x'`` (``z' = y`` for a vertical curve), and GeniE writes that frame as the
  ``<local_system>`` (measured along +x, -x, +y, +z, -z and a 3-4-5 slope). A guide-oriented
  curve whose stored ``<local_system>`` is *not* the guide frame -- ``setLocalX`` on a curve
  without changing its rule -- was meshed in the guide frame, the stored one ignored, so which
  one is meant is ambiguous and it is refused. A curve's spring stiffness is per unit length
  (GeniE's ``BoundaryStiffnessPerLength``; 500000 N/m^2 became MGSPRNG 125000/250000 at the
  end/inner nodes at 0.5 m).
* **Rigid links.** The footprint box's ``lower_corner``/``upper_corner`` are global corners (the
  6 nodes GeniE linked are the ones inside them, whatever ``local_system_origin`` says: GeniE
  writes the box centre there and adapy the master point). The slave translations are linked
  unless ``slave_dx|dy|dz="free"`` (BLDEP then leaves that DOF out); the slave rotations only with
  ``slave_rx|ry|rz="dependent"`` (absent: a 9-term BLDEP, translations only; present: 12 terms).
  GeniE's importer also takes ``rotation_dependent="true"`` (adapy's writer) and exports it back
  as ``slave_r*="dependent"``. ``include_all_edges="false"`` is GeniE's
  ``IncludeOnlyNodesOfSupports``; it is carried as written.

Refused by name: a non-identity frame that does not map onto global axes; a rotated frame with a
prescribed DOF; a guide-oriented curve whose stored frame is not the guide frame; a curve that is
not one straight segment; a rigid link with a free slave translation, some but not all slave
rotations dependent, or a rotated footprint box; anything this reader has no field for.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING, Callable, get_args

import numpy as np

from ada.fem.concept.constraints import (
    ConstraintConceptCurve,
    ConstraintConceptDofType,
    ConstraintConceptPoint,
    ConstraintConceptRigidLink,
    ConstraintType,
    RigidLinkRegion,
)
from ada.fem.formats import conversion_report

if TYPE_CHECKING:
    from ada import Part

#: The ``stage`` every finding of this reader is filed under (the same as the loads reader's).
STAGE = "genie xml reader"

SUPPORT_TAGS = ("support_point", "support_curve", "support_rigid_link")

_DOFS = ("dx", "dy", "dz", "rx", "ry", "rz")

#: How far a written direction cosine may be from the value it stands for. GeniE writes them with
#: ten significant digits (0.7071067812), so a projector built from them is off by ~1e-10.
_COSINE_TOL = 1e-6


def report():
    return conversion_report.current()


class _Refused(Exception):
    """One construct this reader cannot hold: the keyword it is reported under, and why."""

    def __init__(self, keyword: str, reason: str, **details):
        super().__init__(reason)
        self.keyword = keyword
        self.reason = reason
        self.details = details


def get_boundary_conditions(
    xml_root: ET.Element, parent: Part, edge_curve_resolver: Callable[[str], object] | None = None
) -> None:
    """Read GeniE's supports into ``parent.concept_fem.constraints``.

    ``edge_curve_resolver`` gives the SAT geometry behind a support curve's ``<sat_reference>``;
    with it, a curve over a curved edge is refused rather than read as its chord.
    """
    readers = {
        "support_point": _support_point,
        "support_curve": lambda el: _support_curve(el, edge_curve_resolver),
        "support_rigid_link": _support_rigid_link,
    }
    constraints = parent.concept_fem.constraints
    add = {
        ConstraintConceptPoint: constraints.add_point_constraint,
        ConstraintConceptCurve: constraints.add_curve_constraint,
        ConstraintConceptRigidLink: constraints.add_rigid_link,
    }

    read = set()
    structures = xml_root.find("./model/structure_domain/structures")
    for el in structures.iterfind("structure/*") if structures is not None else ():
        if not el.tag.startswith("support"):
            continue
        read.add(el)
        name = el.get("name")
        try:
            reader = readers.get(el.tag)
            if reader is None:
                raise _Refused(el.tag, "no concept constraint class holds this kind of support")
            concept = reader(el)
        except _Refused as r:
            report().omitted(STAGE, r.keyword, name, r.reason, **r.details)
            continue
        add[type(concept)](concept)

    # The supports are read from their one place in the model; one anywhere else would otherwise
    # be dropped without a word.
    for el in xml_root.iter():
        if el.tag in SUPPORT_TAGS and el not in read:
            report().omitted(STAGE, el.tag, el.get("name"), "a support outside structure_domain/structures; not read")


# --- the three kinds ---------------------------------------------------------------------------


def _support_point(el: ET.Element) -> ConstraintConceptPoint:
    _check_children(el, {"local_system", "geometry", "boundary_conditions"})
    _check_attributes(el, {"name"})
    geometry = _one(el, "geometry")
    _check_children(geometry, {"position"})
    position = _xyz(_one(geometry, "position"))
    return ConstraintConceptPoint(el.get("name"), position, _global_dofs(el, _frame(el)))


def _support_curve(el: ET.Element, edge_curve_resolver) -> ConstraintConceptCurve:
    _check_children(el, {"local_system", "geometry", "line_orientation", "boundary_conditions"})
    _check_attributes(el, {"name"})
    start, end = _curve_ends(_one(el, "geometry"), edge_curve_resolver)
    frame = _frame(el)

    orientation = el.find("line_orientation")
    if orientation is not None:
        kinds = [c.tag for c in orientation]
        if kinds != ["constant_local_system_line_orientation"]:
            raise _Refused("line_orientation", "a line orientation other than a constant local system", kinds=kinds)
    else:
        guide = _guide_frame(start, end)
        if not np.allclose(frame, guide, atol=_COSINE_TOL):
            raise _Refused(
                "local_system",
                "a support curve with GeniE's default (guide) orientation whose stored local system is not the "
                "guide frame; GeniE meshes such a curve in the guide frame and ignores the stored one, so which "
                "one is meant is ambiguous; it is not read",
                local_system=frame.tolist(),
                guide_frame=guide.tolist(),
            )
    return ConstraintConceptCurve(el.get("name"), start, end, _global_dofs(el, frame))


def _support_rigid_link(el: ET.Element) -> ConstraintConceptRigidLink:
    _check_children(el, {"position", "region", "local_system", "boundary_conditions"})
    slave_attrs = {f"slave_{d}" for d in _DOFS}
    _check_attributes(el, {"name", "include_all_edges", "rotation_dependent"} | slave_attrs)

    for dof in _DOFS[:3]:
        value = el.get(f"slave_{dof}", "dependent")
        if value != "dependent":
            raise _Refused(
                "support_rigid_link",
                "a slave translation that is not linked; ConstraintConceptRigidLink links all three",
                **{f"slave_{dof}": value},
            )
    rotations = [el.get(f"slave_{dof}") for dof in _DOFS[3:]]
    if rotations == [None] * 3:
        rotation_dependent = None
    elif rotations == ["dependent"] * 3:
        rotation_dependent = True
    else:
        raise _Refused(
            "support_rigid_link",
            "slave rotations neither all linked nor all free; ConstraintConceptRigidLink links all three or none",
            slave_rotations=rotations,
        )
    if el.get("rotation_dependent") is not None:
        stated = _bool(el.get("rotation_dependent"), "rotation_dependent")
        if rotation_dependent is not None and stated != rotation_dependent:
            raise _Refused(
                "support_rigid_link",
                "rotation_dependent and slave_rx/ry/rz disagree",
                rotation_dependent=stated,
                slave_rotations=rotations,
            )
        rotation_dependent = stated
    if el.get("include_all_edges") is None:
        raise _Refused("support_rigid_link", "no include_all_edges; GeniE always writes it and no default was measured")

    region = _one(el, "region")
    _check_children(region, {"footprint_box"})
    box = _one(region, "footprint_box")
    _check_children(box, {"lower_corner", "upper_corner", "local_system", "local_system_origin"})
    box_frame = _frame(box)
    if not np.allclose(box_frame, np.eye(3), atol=_COSINE_TOL):
        raise _Refused(
            "footprint_box",
            "a rotated footprint box; RigidLinkRegion is a box along the global axes",
            local_system=box_frame.tolist(),
        )
    lower, upper = _xyz(_one(box, "lower_corner")), _xyz(_one(box, "upper_corner"))
    if any(lo > hi for lo, hi in zip(lower, upper)):
        raise _Refused("footprint_box", "a lower corner above the upper corner", lower=lower, upper=upper)

    return ConstraintConceptRigidLink(
        el.get("name"),
        _xyz(_one(el, "position")),
        RigidLinkRegion(lower, upper),
        _global_dofs(el, _frame(el)),
        rotation_dependent=bool(rotation_dependent),
        include_all_edges=_bool(el.get("include_all_edges"), "include_all_edges"),
    )


# --- geometry ----------------------------------------------------------------------------------


def _curve_ends(geometry: ET.Element, edge_curve_resolver):
    _check_children(geometry, {"wire"})
    wire = _one(geometry, "wire")
    kinds = sorted(c.tag for c in wire)
    if kinds == ["line"]:
        holder = wire.find("line")  # adapy's writer
    elif kinds in (["guide"], ["guide", "sat_reference"]):
        holder = wire.find("guide")  # GeniE's export
        _check_straight_edges(wire.find("sat_reference"), edge_curve_resolver)
    else:
        raise _Refused("support_curve", "a support curve that is not one straight segment", wire=kinds)
    ends = {p.get("end"): p for p in holder.findall("position")}
    if len(holder) != 2 or sorted(ends) != ["1", "2"]:
        raise _Refused("support_curve", "a support curve that is not one straight segment", positions=len(holder))
    start, end = _xyz(ends["1"]), _xyz(ends["2"])
    if start == end:
        raise _Refused("support_curve", "a support curve of zero length", position=start)
    return start, end


def _check_straight_edges(sat_reference: ET.Element | None, edge_curve_resolver) -> None:
    if sat_reference is None or edge_curve_resolver is None:
        return
    from ada.geom.curves import Line

    for edge in sat_reference:
        ref = edge.get("edge_ref")
        curve = edge_curve_resolver(ref) if edge.tag == "edge" and ref else None
        if not isinstance(curve, Line):
            raise _Refused(
                "support_curve",
                "a support curve over an edge that is not a straight line (or is not in the SAT); read as its "
                "two guide points it would be its chord",
                edge_ref=ref,
                curve=type(curve).__name__,
            )


def _guide_frame(start, end) -> np.ndarray:
    """GeniE's default frame along a straight curve, rows x', y', z' (see the module docstring)."""
    x = np.subtract(end, start, dtype=float)
    x /= np.linalg.norm(x)
    y = np.cross((0.0, 0.0, 1.0), x)
    if np.linalg.norm(y) < _COSINE_TOL:  # vertical: z' = global y, measured up and down
        z = np.array((0.0, 1.0, 0.0))
        return np.array([x, np.cross(z, x), z])
    y /= np.linalg.norm(y)
    return np.array([x, y, np.cross(x, y)])


# --- dofs and frames ---------------------------------------------------------------------------


def _frame(el: ET.Element) -> np.ndarray:
    """The ``<local_system>`` child of ``el`` as rows x', y', z' in global components."""
    ls = el.find("local_system")
    if ls is None:
        raise _Refused(el.tag, "no local_system, so the frame its dofs are in is not stated")
    vectors = {v.get("dir"): _xyz(v) for v in ls.findall("vector")}
    if len(ls) != 3 or sorted(vectors) != ["x", "y", "z"]:
        raise _Refused("local_system", "a local system that is not three vectors x, y and z")
    frame = np.array([vectors["x"], vectors["y"], vectors["z"]])
    if not np.allclose(frame @ frame.T, np.eye(3), atol=_COSINE_TOL) or np.linalg.det(frame) < 0:
        raise _Refused("local_system", "a local system that is not a right-handed orthonormal frame")
    return frame


def _global_dofs(el: ET.Element, frame: np.ndarray) -> list[ConstraintConceptDofType]:
    local = _local_dofs(el)
    if np.allclose(frame, np.eye(3), atol=_COSINE_TOL):
        return [ConstraintConceptDofType(dof, *local[dof]) for dof in _DOFS]

    if any(c == "prescribed" for c, _ in local.values()):
        raise _Refused(
            "local_system",
            "a prescribed dof in a rotated local system; its value in a load case is in that frame and the "
            "concept classes hold global dofs only",
            local_system=frame.tolist(),
        )
    converted = {}
    for dofs in (_DOFS[:3], _DOFS[3:]):
        on_axis = _map_onto_global_axes(frame, [local[d] for d in dofs])
        if on_axis is None:
            raise _Refused(
                "local_system",
                "dofs in a rotated local system that do not map onto the global axes; the concept classes hold "
                "global dofs only",
                local_system=frame.tolist(),
                local_dofs={d: local[d][0] for d in _DOFS},
            )
        converted.update(zip(dofs, on_axis))
    return [ConstraintConceptDofType(dof, *converted[dof]) for dof in _DOFS]


def _map_onto_global_axes(frame: np.ndarray, values: list[tuple]) -> list[tuple] | None:
    """The values of three local axes on the global axes, or None when the frame mixes them.

    The local axes holding one value span a subspace; it is the span of global axes exactly when
    its projector has a 0/1 diagonal, and then each global axis with a 1 takes that value. (The
    projector is then diagonal too: ``P = P^T P`` makes ``P_jj`` the sum of row j's squares.) So a
    90 degree turn moves a fixed dx' onto dy, and any turn about z keeps a lone dz.
    """
    out: list[tuple | None] = [None] * 3
    for value in sorted(set(values), key=repr):
        axes = frame[[i for i, v in enumerate(values) if v == value]]
        diagonal = np.diag(axes.T @ axes)
        if not np.allclose(diagonal, np.round(diagonal), atol=_COSINE_TOL):
            return None
        for j in np.flatnonzero(np.round(diagonal)):
            out[j] = value
    return out


def _local_dofs(el: ET.Element) -> dict[str, tuple[str, float]]:
    bcs = _one(el, "boundary_conditions")
    _check_children(bcs, {"boundary_condition"})
    out = {}
    for bc in bcs:
        dof, constraint = bc.get("dof"), bc.get("constraint")
        if dof not in _DOFS or dof in out:
            raise _Refused("boundary_condition", "a dof that is not one of dx..rz, or given twice", dof=dof)
        if constraint not in get_args(ConstraintType):
            raise _Refused(
                "boundary_condition", "a constraint kind the concept classes have no member for", **bc.attrib
            )
        if constraint == "spring":
            _check_attributes(bc, {"dof", "constraint", "stiffness"})
            if bc.get("stiffness") is None:
                raise _Refused("boundary_condition", "a spring without a stiffness", dof=dof)
            out[dof] = (constraint, float(bc.get("stiffness")))
        else:
            _check_attributes(bc, {"dof", "constraint"})
            out[dof] = (constraint, 0.0)
    if len(out) != 6:
        raise _Refused("boundary_conditions", "not all six dofs are given", dofs=sorted(out))
    return out


# --- helpers -----------------------------------------------------------------------------------


def _one(el: ET.Element, tag: str) -> ET.Element:
    found = el.findall(tag)
    if len(found) != 1:
        raise _Refused(el.tag, f"expected exactly one <{tag}>, found {len(found)}")
    return found[0]


def _check_children(el: ET.Element, allowed: set[str]) -> None:
    extra = sorted({c.tag for c in el} - allowed)
    if extra:
        raise _Refused(el.tag, f"children this reader has no concept field for: {', '.join(extra)}")


def _check_attributes(el: ET.Element, allowed: set[str]) -> None:
    extra = sorted(set(el.attrib) - allowed)
    if extra:
        raise _Refused(el.tag, f"attributes this reader has no concept field for: {', '.join(extra)}")


def _xyz(el: ET.Element) -> tuple[float, float, float]:
    try:
        return tuple(float(el.attrib[k]) for k in "xyz")
    except (KeyError, ValueError):
        raise _Refused(el.tag, "a position without numeric x, y and z", **el.attrib)


def _bool(value: str, attribute: str) -> bool:
    token = str(value).strip().lower()
    if token not in ("true", "false"):
        raise _Refused("support_rigid_link", f"{attribute}={value!r} is neither true nor false")
    return token == "true"
