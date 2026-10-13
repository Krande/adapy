"""GeniE equipment on the way in: the prism shapes, where each load case places them, and the
beam line loads GeniE resolved those placements into.

* **Shapes.** ``model/equipment_domain/equipment_concepts/equipments/prism_shape``: ``mass`` (kg),
  ``<dimensions>``, ``<cog>`` and a ``<footprint>`` of ``<polygon x1 y1 x2 y2>`` rectangles, all
  in the shape's own frame. Other equipment kinds are reported and not read.
* **Placements.** ``analyses/global/loads/equipment_loads/placed_shape``: one per (load case,
  equipment), an ``<origo>`` and a ``<local_system>`` of three ``<vector dir="x|y|z">``. The wind
  "equipment" of a GeniE model stands on its side (its z along -y), which is why the frame is
  kept rather than assumed.
* **Resolved loads.** ``analyses/global/applied_loads/applied_load`` with a
  ``load_generator_ref`` of ``<load case>.equipment(<name>)``: the beam line (and point) loads
  GeniE's ``line_load`` representation turned each placement into. They are read into
  :attr:`EquipmentConcepts.generated_loads`, never into the applied load cases (see there). The
  other applied loads are GeniE's meshed copies of explicit loads that :mod:`read_loads` already
  read, and are skipped.

Nothing here is written back: the GeniE writer takes equipment from :class:`ada.Equipment` parts.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING

import numpy as np

from ada.cadit.gxml.read.read_loads import (
    STAGE,
    _Geometry,
    _line_footprint,
    _line_intensity,
    _only_child,
    _point_footprint,
    _point_intensity,
    _Refused,
    _system,
    report,
)
from ada.fem.concept.equipment import (
    IDENTITY_AXES,
    EquipmentConceptPlacement,
    EquipmentConceptPrism,
)
from ada.fem.concept.loads import LoadConceptLine, LoadConceptPoint

if TYPE_CHECKING:
    from ada import Part

EQUIPMENTS_PATH = "./model/equipment_domain/equipment_concepts/equipments"
GLOBAL_PATH = "./model/analysis_domain/analyses/global"

_GENERATOR = re.compile(r"^(?P<case>.*)\.equipment\((?P<name>.*)\)$")


def get_equipment(xml_root: ET.Element, part: Part) -> None:
    """Read GeniE's equipment into ``part.concept_fem.equipment``."""
    concepts = part.concept_fem.equipment
    equipments = xml_root.find(EQUIPMENTS_PATH)
    if equipments is None:
        return
    for el in equipments:
        if el.tag != "prism_shape":
            report().omitted(STAGE, el.tag, el.get("name", ""), "no equipment concept holds this kind of shape")
            continue
        try:
            concepts.add_prism(_prism(el))
        except (_Refused, ValueError, TypeError) as exc:
            reason = exc.reason if isinstance(exc, _Refused) else str(exc)
            report().omitted(STAGE, el.tag, el.get("name", ""), reason)

    global_elem = xml_root.find(GLOBAL_PATH)
    if global_elem is None:
        return
    for el in global_elem.iterfind("loads/equipment_loads/placed_shape"):
        try:
            concepts.add_placement(_placement(el))
        except (_Refused, ValueError, TypeError) as exc:
            reason = exc.reason if isinstance(exc, _Refused) else str(exc)
            report().omitted(STAGE, el.tag, el.get("equipment_ref", ""), reason)

    geometry = _Geometry(xml_root, part)
    for el in global_elem.iterfind("applied_loads/applied_load"):
        match = _GENERATOR.match(el.get("load_generator_ref", ""))
        if match is None:
            continue  # a meshed copy of an explicit load, read by read_loads
        name = match.group("name")
        try:
            load = _resolved_load(el, name, geometry)
        except _Refused as r:
            report().omitted(STAGE, r.keyword, f"{name} in load case {el.get('loadcase_ref')}", r.reason, **r.details)
            continue
        concepts.add_generated_load(el.get("loadcase_ref") or match.group("case"), name, load)


def _xyz(el: ET.Element | None, default=(0.0, 0.0, 0.0)) -> tuple[float, float, float]:
    if el is None:
        return default
    return float(el.get("x", "0")), float(el.get("y", "0")), float(el.get("z", "0"))


def _prism(el: ET.Element) -> EquipmentConceptPrism:
    dims = el.find("dimensions")
    if dims is None:
        raise _Refused(el.tag, "a prism shape without dimensions")
    footprint = [tuple(float(p.get(k)) for k in ("x1", "y1", "x2", "y2")) for p in el.iterfind("footprint/polygon")]
    return EquipmentConceptPrism(
        name=el.get("name"),
        mass=float(el.get("mass", "0")),
        dimensions=_xyz(dims),
        cog=_xyz(el.find("cog")),
        footprint=footprint,
    )


def _placement(el: ET.Element) -> EquipmentConceptPlacement:
    origo = el.find("origo")
    if origo is None:
        raise _Refused(el.tag, "a placed shape without an origo")
    return EquipmentConceptPlacement(
        equipment=el.get("equipment_ref"),
        load_case=el.get("loadcase_ref", ""),
        origin=_xyz(origo),
        axes=_axes(el.find("local_system")),
        moment_equilibrium=el.get("moment_equilibrium", "true").strip().lower() == "true",
    )


def _axes(local_system: ET.Element | None):
    if local_system is None:
        return IDENTITY_AXES
    vectors = {v.get("dir"): _xyz(v) for v in local_system.iterfind("vector")}
    if sorted(vectors) != ["x", "y", "z"]:
        raise _Refused("local_system", "a placement frame without one x, one y and one z vector")
    axes = [np.asarray(vectors[k], dtype=float) for k in ("x", "y", "z")]
    norms = [float(np.linalg.norm(a)) for a in axes]
    if min(norms) < 1e-12:
        raise _Refused("local_system", "a placement frame with a zero-length axis")
    unit = [a / n for a, n in zip(axes, norms)]
    if abs(float(np.dot(np.cross(unit[0], unit[1]), unit[2])) - 1.0) > 1e-6:
        raise _Refused("local_system", "a placement frame that is not right-handed and orthogonal")
    return tuple(tuple(float(c) for c in a) for a in unit)


def _resolved_load(el: ET.Element, name: str, geometry: _Geometry):
    footprint = _only_child(el, "footprint")
    intensity = _only_child(el, "intensity")
    system = _system(intensity)
    if footprint.tag in ("footprint_beam", "footprint_line"):
        q1, q2 = _line_intensity(intensity)
        start, end = _line_footprint(footprint, geometry)
        return LoadConceptLine(name, start, end, q1, q2, system=system)
    if footprint.tag in ("footprint_beam_point", "footprint_point"):
        force, moment = _point_intensity(intensity)
        return LoadConceptPoint(name, _point_footprint(footprint, geometry), force, moment, system=system)
    raise _Refused(footprint.tag, "no concept load holds this kind of resolved equipment load")
