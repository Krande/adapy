"""GeniE concept loads on the way in: load cases, explicit loads, gravity and combinations.

The GeniE XML writer (``write/write_load_case.py``, ``write/write_loads.py``) writes
``Part.concept_fem.loads`` and nothing read it back: a GeniE model with 16 load cases came in with
none, and nothing said so. This module is the inverse of that writer, into the same classes
(:mod:`ada.fem.concept.loads`), and it is also a reader of GeniE's own export, which says more
than the writer does. Everything it cannot hold is reported by name through
:mod:`ada.fem.formats.conversion_report` and left out -- never approximated.

What GeniE's XML means, measured on GeniE V9.2-01 (the probe models are the test fixtures
``genie_loads_all_kinds.xml``, ``genie_loads_reparam.xml`` and ``genie_loads_segments.xml``;
the Sesam records GeniE meshed them into are the oracle for every reading below):

* **Where things are.** Load cases are ``analyses/global/loadcases/loadcase_basic``; explicit
  loads ``global/loads/explicit_loads``; gravity ``global/loads/environmental_loads``. Load
  *combinations* live under ``analyses/analysis/loadcases/loadcase_combination`` with their
  factors in ``analysis/combinations/combination`` -- not under ``global``, where adapy's own
  writer puts them (with the factors inline as well). Both places are read. Everything is read
  from those direct paths, never with ``.//``: a meshed workspace carries a ``mirror_model``
  inside the analysis with ``<loads>`` and ``<loadcases>`` of its own.
* **Gravity.** After meshing, GeniE writes a ``gravity_load`` with ``z=-9.80665`` for *every*
  load case, with ``include_selfweight="false"`` on all but the case that asked for self-weight.
  Only ``include_selfweight="true"`` becomes a ``BGRAV`` (1 of 16 cases), so only that one is
  read as a :class:`LoadConceptAccelerationField`. Reading them all would put gravity into every
  load case. ``dummy_rotation_field`` is bookkeeping and carries nothing.
* **Coordinate systems.** Every intensity says ``intensity_system="local"
  position_system="local"``, and the loads without a ``<coordinate_system>`` child came out in
  global components (``BNLOAD``/``BELOAD1``/``BEUSLO``). "local" means the load's own coordinate
  system, which is the identity when there is none. The concept classes store no rotation, so a
  load with a non-identity ``<coordinate_system>`` is refused; the token itself is carried in
  ``system`` unchanged, as the writer emits it.
* **Beam footprints.** ``footprint_beam_point param`` and ``footprint_beam param_start/param_end``
  are fractions of the *whole* beam's length along its guide line, also on a beam of several
  segments: on a straight beam split at x=1 of 0..4, ``param=0.5`` loaded the node at x=2 and
  ``0.25..0.75`` loaded x=1..3 (four 0.5 m ``BELOAD1``). They are read as the point(s) at those
  fractions of the guide line. ``local_system="relative"`` on a line load left the intensity
  global (a vertical beam's ``fz=-1000`` stayed ``q=(0,0,-1000)``), so it is read like
  ``"global"``; on a point load it was not measured and is refused.
* **Front and back.** ``footprint_plate side="front"`` with 1000 Pa on a plate whose normal is +z
  gave Fz = -4000 on 4 m2 (a front pressure pushes into the front face), ``side="back"`` +4000.
  ``LoadConceptSurface`` carries the side as written, against the plate's own normal.

Refused by name: a non-identity ``coordinate_system``; varying pressures
(``pressure2d_3point_varying``, ``pressure2d_linear_function``); component surface loads
(``component2d_constant``); polygon pressures (``footprint_polygon`` -- GeniE smears it over whole
elements along the polygon's normal, so its own result depends on the mesh); rotation fields;
equipment loads; and every element, footprint or intensity kind not named above.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING

from ada.fem.concept.loads import (
    DesignCondition,
    LoadConceptAccelerationField,
    LoadConceptCase,
    LoadConceptCaseCombination,
    LoadConceptCaseFactored,
    LoadConceptLine,
    LoadConceptPoint,
    LoadConceptSurface,
)
from ada.fem.formats import conversion_report

if TYPE_CHECKING:
    from ada import Part

#: The ``stage`` every finding of this reader is filed under.
STAGE = "genie xml reader"

#: The intensity/position system tokens that mean "global" when no ``<coordinate_system>`` is
#: given (see the module docstring). Anything else is refused.
SYSTEMS = ("local", "global")

#: The identity rotation as GeniE writes the ``rotation_matrix`` attributes.
_IDENTITY = {"xx": 1.0, "xy": 0.0, "xz": 0.0, "yx": 0.0, "yy": 1.0, "yz": 0.0, "zx": 0.0, "zy": 0.0, "zz": 1.0}

#: Children of ``environmental_loads`` that carry no load of their own.
_BOOKKEEPING_ENV = ("dummy_rotation_field",)

#: Children of ``equipment_loads`` that only say how a load case's placed equipment is
#: represented (GeniE writes ``equipment_rep``, adapy's writer ``equipment_ref``).
_BOOKKEEPING_EQUIPMENT = ("equipment_rep", "equipment_ref")


def report():
    return conversion_report.current()


class _Refused(Exception):
    """One construct this reader cannot hold: the keyword it is reported under, and why."""

    def __init__(self, keyword: str, reason: str, **details):
        super().__init__(reason)
        self.keyword = keyword
        self.reason = reason
        self.details = details


def get_loads(xml_root: ET.Element, part: Part) -> None:
    """Read GeniE's load cases, loads and combinations into ``part.concept_fem.loads``."""
    analyses = xml_root.find("./model/analysis_domain/analyses")
    if analyses is None:
        return

    geometry = _Geometry(xml_root, part)
    global_elem = analyses.find("global")
    cases = _read_load_cases(global_elem, part) if global_elem is not None else {}
    if global_elem is not None:
        _read_global_loads(global_elem, cases, geometry)
    _read_combinations(analyses, cases, part)


# --- load cases --------------------------------------------------------------------------------


def _read_load_cases(global_elem: ET.Element, part: Part) -> dict[str, LoadConceptCase]:
    loadcases = global_elem.find("loadcases")
    if loadcases is None:
        return {}

    as_mass = {
        el.get("loadcase_ref"): _bool(el.get("mesh_loads_as_mass", "false"))
        for el in global_elem.iterfind("loads/explicit_loads/dummy_mesh_loads_as_mass")
    }
    cases = {}
    for el in loadcases:
        if el.tag == "loadcase_combination":
            continue  # adapy's writer puts combinations here; read with the others
        name = el.get("name")
        if el.tag != "loadcase_basic":
            report().omitted(
                STAGE, el.tag, name, "no concept load case holds this kind of load case; it and its loads are not read"
            )
            continue
        complex_type = el.get("complex_type", "static")
        if complex_type != "static":
            report().omitted(
                STAGE,
                "loadcase_basic",
                name,
                "LoadConceptCase holds static load cases only; this case and its loads are not read",
                complex_type=complex_type,
            )
            continue
        lc = LoadConceptCase(
            name=name,
            design_condition=_design_condition(el.get("design_condition", "operating"), "loadcase_basic", name),
            complex_type="static",
            invalidated=_bool(el.get("invalidated", "true")),
            mesh_loads_as_mass=as_mass.get(name, False),
        )
        if el.get("fem_loadcase_number") is not None:
            lc.fem_loadcase_number = int(el.get("fem_loadcase_number"))
        cases[name] = part.concept_fem.loads.add_load_case(lc)
    return cases


def _design_condition(value: str, keyword: str, subject: str) -> DesignCondition:
    # adapy's own writer up to 0.95.2 wrote the enum's repr ("DesignCondition.OPERATING"), and
    # GeniE imports that as operating, so it is read the same way.
    token = value.split(".", 1)[1] if value.startswith("DesignCondition.") else value
    try:
        return DesignCondition.from_string(token)
    except ValueError:
        report().approximated(
            STAGE,
            keyword,
            subject,
            "DesignCondition has no member for this design condition; it is read as operating",
            design_condition=value,
        )
        return DesignCondition.OPERATING


# --- explicit, environmental and equipment loads -----------------------------------------------


def _read_global_loads(global_elem: ET.Element, cases: dict[str, LoadConceptCase], geometry: _Geometry) -> None:
    loads = global_elem.find("loads")
    if loads is None:
        return
    for group in loads:
        if group.tag == "explicit_loads":
            for el in group:
                if el.tag == "dummy_mesh_loads_as_mass":
                    continue  # read with the load case
                _add(el, cases, lambda e: _explicit_load(e, geometry))
        elif group.tag == "environmental_loads":
            for el in group:
                if el.tag in _BOOKKEEPING_ENV:
                    continue
                if el.tag == "gravity_load" and el.get("include_selfweight") == "false":
                    continue  # GeniE writes one per load case after meshing; only "true" is gravity
                _add(el, cases, _environmental_load)
        elif group.tag == "equipment_loads":
            for el in group:
                if el.tag in _BOOKKEEPING_EQUIPMENT:
                    continue
                reason = (
                    "an equipment load has no concept load class (GeniE resolves it to line loads or masses "
                    "at meshing); it is not read"
                )
                report().omitted(STAGE, el.tag, _subject(el), reason)
        else:
            # slicer_loads and anything newer: nothing in them is read.
            for el in group:
                report().omitted(STAGE, el.tag, _subject(el), f"no concept load class holds a {group.tag} entry")


def _subject(el: ET.Element) -> str:
    name = el.get("name") or el.get("equipment_ref") or ""
    lc = el.get("loadcase_ref")
    if name and lc:
        return f"{name} in load case {lc}"
    return f"load case {lc}" if lc else name


def _add(el: ET.Element, cases: dict[str, LoadConceptCase], parse) -> None:
    lc_ref = el.get("loadcase_ref")
    try:
        lc = cases.get(lc_ref)
        if lc is None:
            raise _Refused(el.tag, f"its load case {lc_ref!r} was not read")
        load = parse(el)
    except _Refused as r:
        report().omitted(STAGE, r.keyword, _subject(el), r.reason, **r.details)
        return
    load.parent = lc
    lc.loads.append(load)
    if isinstance(load, LoadConceptAccelerationField) and load.include_self_weight:
        lc.include_self_weight = True


def _explicit_load(el: ET.Element, geometry: _Geometry):
    for child in el:
        if child.tag not in ("footprint", "intensity", "coordinate_system"):
            raise _Refused(child.tag, f"a {el.tag} child this reader has no concept field for")
    _check_coordinate_system(el)
    footprint = _only_child(el, "footprint")
    intensity = _only_child(el, "intensity")
    system = _system(intensity)
    name = el.get("name")

    if el.tag == "point_load":
        force, moment = _point_intensity(intensity)
        position = _point_footprint(footprint, geometry)
        return LoadConceptPoint(name, position, force, moment, system=system)
    if el.tag == "line_load":
        q1, q2 = _line_intensity(intensity)
        start, end = _line_footprint(footprint, geometry)
        return LoadConceptLine(name, start, end, q1, q2, system=system)
    if el.tag == "surface_load":
        pressure = _surface_intensity(intensity)
        plate, side = _surface_footprint(footprint, geometry)
        return LoadConceptSurface(name, plate_ref=plate, pressure=pressure, side=side, system=system)
    raise _Refused(el.tag, "no concept load class holds this kind of explicit load")


def _check_coordinate_system(el: ET.Element) -> None:
    cs = el.find("coordinate_system")
    if cs is None:
        return
    matrix = cs.find("cartesian_coordinate_system/transformation_matrix")
    rot = matrix.find("rotation_matrix") if matrix is not None else None
    t_vec = matrix.find("t_vec") if matrix is not None else None
    if rot is None or len(cs) != 1 or len(matrix) != (2 if t_vec is not None else 1):
        raise _Refused("coordinate_system", "a load coordinate system this reader cannot interpret")
    values = {k: float(rot.get(k, "nan")) for k in _IDENTITY}
    shift = [float(t_vec.get(k, "nan")) for k in "xyz"] if t_vec is not None else [0.0, 0.0, 0.0]
    if values != _IDENTITY or shift != [0.0, 0.0, 0.0]:
        raise _Refused(
            "coordinate_system",
            "the load is given in its own rotated coordinate system and the concept load classes store no "
            "rotation; it is not read",
            rotation=[values[k] for k in _IDENTITY],
            translation=shift,
        )


def _only_child(el: ET.Element, tag: str) -> ET.Element:
    holder = el.find(tag)
    if holder is None or len(holder) != 1:
        raise _Refused(el.tag, f"expected exactly one <{tag}> kind, found {0 if holder is None else len(holder)}")
    return holder[0]


def _system(intensity: ET.Element) -> str:
    system = intensity.get("intensity_system", "local")
    position_system = intensity.get("position_system", system)
    for token in (system, position_system):
        if token not in SYSTEMS:
            raise _Refused(intensity.tag, f"intensity/position system {token!r} is not a load's own (global) system")
    return system


def _xyz(el: ET.Element, keys=("x", "y", "z")) -> tuple[float, float, float]:
    return tuple(float(el.get(k)) for k in keys)


def _check_attributes(el: ET.Element, allowed: set[str], context: str) -> None:
    extra = sorted(set(el.attrib) - allowed)
    if extra:
        raise _Refused(context, f"intensity attributes the concept class has no field for: {', '.join(extra)}")


def _point_intensity(intensity: ET.Element):
    if intensity.tag != "component0d_constant":
        raise _Refused(intensity.tag, "no concept point load holds this kind of point intensity")
    values = intensity.find("intensity")
    if values is None or any(c.tag not in ("force", "moment") for c in values):
        raise _Refused(intensity.tag, "a point intensity other than one force and one moment")
    force_el, moment_el = values.find("force"), values.find("moment")
    force = _xyz(force_el, ("fx", "fy", "fz")) if force_el is not None else (0.0, 0.0, 0.0)
    moment = _xyz(moment_el, ("mx", "my", "mz")) if moment_el is not None else (0.0, 0.0, 0.0)
    return force, moment


def _line_intensity(intensity: ET.Element):
    keys = ("fx", "fy", "fz")
    if intensity.tag == "component1d_constant":
        values = intensity.findall("intensity")
        if len(values) != 1:
            raise _Refused(intensity.tag, f"expected one intensity, found {len(values)}")
        _check_attributes(values[0], set(keys), intensity.tag)
        q = _xyz(values[0], keys)
        return q, q
    if intensity.tag == "component1d_linear":
        ends = {v.get("end"): v for v in intensity.findall("intensity")}
        if sorted(ends) != ["1", "2"] or len(intensity) != 2:
            raise _Refused(intensity.tag, "expected one intensity at end 1 and one at end 2")
        for v in ends.values():
            _check_attributes(v, set(keys) | {"end"}, intensity.tag)
        return _xyz(ends["1"], keys), _xyz(ends["2"], keys)
    raise _Refused(intensity.tag, "no concept line load holds this kind of line intensity")


_SURFACE_REFUSALS = {
    "pressure2d_3point_varying": "a varying pressure; LoadConceptSurface holds a uniform pressure only",
    "pressure2d_linear_function": "a varying pressure; LoadConceptSurface holds a uniform pressure only",
    "component2d_constant": "a surface traction by components; LoadConceptSurface holds a normal pressure only",
}


def _surface_intensity(intensity: ET.Element) -> float:
    if intensity.tag != "pressure2d_constant":
        reason = _SURFACE_REFUSALS.get(intensity.tag, "no concept surface load holds this kind of surface intensity")
        raise _Refused(intensity.tag, f"{reason}; it is not read")
    values = intensity.findall("intensity")
    if len(values) != 1:
        raise _Refused(intensity.tag, f"expected one intensity, found {len(values)}")
    _check_attributes(values[0], {"pressure"}, intensity.tag)
    return float(values[0].get("pressure"))


def _point_footprint(footprint: ET.Element, geometry: _Geometry):
    if footprint.tag == "footprint_point":
        point = footprint.find("point")
        if point is None or len(footprint) != 1:
            raise _Refused(footprint.tag, "expected exactly one <point>")
        return _xyz(point)
    if footprint.tag == "footprint_beam_point":
        if footprint.get("local_system", "global") != "global":
            raise _Refused(
                footprint.tag,
                f"local_system={footprint.get('local_system')!r} on a beam point load was not measured",
            )
        return geometry.beam_point(footprint.get("beam_ref"), float(footprint.get("param")), footprint.tag)
    raise _Refused(footprint.tag, "no concept point load holds this kind of point footprint")


def _line_footprint(footprint: ET.Element, geometry: _Geometry):
    if footprint.tag == "footprint_line":
        line = footprint.find("line")
        ends = {p.get("end"): p for p in line.findall("position")} if line is not None else {}
        if line is None or len(footprint) != 1 or len(line) != 2 or sorted(ends) != ["1", "2"]:
            raise _Refused(footprint.tag, "expected one <line> with a position at end 1 and one at end 2")
        return _xyz(ends["1"]), _xyz(ends["2"])
    if footprint.tag == "footprint_beam":
        if footprint.get("local_system", "global") not in ("global", "relative"):
            raise _Refused(footprint.tag, f"local_system={footprint.get('local_system')!r} was not measured")
        ref = footprint.get("beam_ref")
        t0, t1 = float(footprint.get("param_start", "0")), float(footprint.get("param_end", "1"))
        return geometry.beam_point(ref, t0, footprint.tag), geometry.beam_point(ref, t1, footprint.tag)
    raise _Refused(footprint.tag, "no concept line load holds this kind of line footprint")


def _surface_footprint(footprint: ET.Element, geometry: _Geometry):
    if footprint.tag == "footprint_plate":
        side = footprint.get("side", "front")
        if side not in ("front", "back") or len(footprint) != 0:
            raise _Refused(footprint.tag, f"a plate footprint on side {side!r} with {len(footprint)} sub-elements")
        return geometry.plate(footprint.get("plate_ref"), footprint.tag), side
    if footprint.tag == "footprint_polygon":
        raise _Refused(
            footprint.tag,
            "a polygon pressure; GeniE smears it over whole elements along the polygon's normal, so what it "
            "loads depends on the mesh; it is not read",
        )
    raise _Refused(footprint.tag, "no concept surface load holds this kind of surface footprint")


def _environmental_load(el: ET.Element):
    if el.tag == "gravity_load":
        if el.get("include_selfweight") != "true":
            raise _Refused(el.tag, f"include_selfweight={el.get('include_selfweight')!r}")
        acc = el.find("acceleration")
        if acc is None or len(el) != 1:
            raise _Refused(el.tag, "expected exactly one <acceleration>")
        return LoadConceptAccelerationField(f"{el.get('loadcase_ref')}_gravity", _xyz(acc), include_self_weight=True)
    if el.tag == "rotation_field":
        raise _Refused(el.tag, "a rotation field (angular velocity and acceleration about an axis); it is not read")
    raise _Refused(el.tag, "no concept load class holds this kind of environmental load")


# --- combinations ------------------------------------------------------------------------------


def _read_combinations(analyses: ET.Element, cases: dict[str, LoadConceptCase], part: Part) -> None:
    loads = part.concept_fem.loads
    containers = [c for c in [analyses.find("global"), *analyses.findall("analysis")] if c is not None]
    for container in containers:
        terms_by_name = {c.get("combination_ref"): c for c in container.iterfind("combinations/combination")}
        declared = set()
        for el in container.findall("loadcases/*"):
            if el.tag != "loadcase_combination":
                # under global these are the basic cases, read (or refused) by _read_load_cases
                if container.tag == "analysis":
                    report().omitted(
                        STAGE, el.tag, el.get("name"), "no concept class holds this kind of analysis load case"
                    )
                continue
            name = el.get("name")
            declared.add(name)
            terms = terms_by_name.get(name)
            term_elems = terms.findall("loadcases/loadcase") if terms is not None else el.findall("loadcase")
            try:
                lcc = _combination(el, term_elems, cases)
                if name in loads.load_case_combinations:
                    raise _Refused("loadcase_combination", "a second combination of the same name")
            except _Refused as r:
                report().omitted(STAGE, r.keyword, name, r.reason, **r.details)
                continue
            loads.add_load_case_combination(lcc)
        for name in sorted(set(terms_by_name) - declared):
            report().omitted(STAGE, "combination", name, "factors for a load case combination that is not declared")


def _combination(el: ET.Element, term_elems: list[ET.Element], cases: dict[str, LoadConceptCase]):
    name = el.get("name")
    complex_type = el.get("complex_type", "static")
    if complex_type != "static":
        raise _Refused("loadcase_combination", "LoadConceptCaseCombination holds static combinations only")
    factored = []
    for term in term_elems:
        ref = term.get("loadcase_ref")
        lc = cases.get(ref)
        if lc is None:
            raise _Refused(
                "loadcase_combination",
                "it refers to a load case that is not a basic load case read here (a combination, or a case "
                "that was refused), and a combination without that term is a different combination",
                loadcase_ref=ref,
            )
        phase = float(term.get("phase", "0"))
        factored.append(
            LoadConceptCaseFactored(lc, float(term.get("factor")), int(phase) if phase.is_integer() else phase)
        )
    equipments = el.find("equipments")
    return LoadConceptCaseCombination(
        name,
        factored,
        design_condition=_design_condition(el.get("design_condition", "operating"), "loadcase_combination", name),
        complex_type="static",
        invalidated=_bool(el.get("invalidated", "true")),
        convert_load_to_mass=_bool(el.get("convert_load_to_mass", "false")),
        global_scale_factor=float(el.get("global_scale_factor", "1")),
        equipments_type=equipments.get("representation_type") if equipments is not None else "line_load",
    )


# --- geometry the footprints refer to ----------------------------------------------------------


class _Geometry:
    """The beams' guide lines and the plates, as the footprints name them."""

    def __init__(self, xml_root: ET.Element, part: Part):
        self.part = part
        self.beams: dict[str, tuple | None] = {}
        structures = xml_root.find("./model/structure_domain/structures")
        if structures is None:
            structures = ET.Element("structures")
        for bm in structures.iterfind("structure/straight_beam"):
            segs = sorted(bm.iterfind("segments/straight_segment"), key=lambda s: int(s.get("index", "0")))
            ends = [{p.get("end"): p for p in s.findall("geometry/wire/guide/position")} for s in segs]
            if not ends or "1" not in ends[0] or "2" not in ends[-1]:
                self.beams[bm.get("name")] = None
                continue
            self.beams[bm.get("name")] = (_xyz(ends[0]["1"]), _xyz(ends[-1]["2"]))
        for bm in structures.iterfind("structure/curved_beam"):
            self.beams[bm.get("name")] = None
        self.plate_faces = {
            pl.get("name"): len(pl.findall("geometry/sheet/sat_reference/face"))
            for tag in ("flat_plate", "curved_shell")
            for pl in structures.iterfind(f"structure/{tag}")
        }

    def beam_point(self, ref: str, param: float, keyword: str):
        if ref not in self.beams:
            raise _Refused(keyword, "it names a beam that is not in the model", beam_ref=ref)
        line = self.beams[ref]
        if line is None:
            raise _Refused(keyword, "it names a curved beam; a position along its curve is not read", beam_ref=ref)
        if not 0.0 <= param <= 1.0:
            raise _Refused(keyword, "a beam parameter outside 0..1", beam_ref=ref, param=param)
        a, b = line
        return tuple(a[i] + param * (b[i] - a[i]) for i in range(3))

    def plate(self, ref: str, keyword: str):
        plate = self.part.plates.from_name(ref)
        if plate is None:
            raise _Refused(keyword, "it names a plate that was not read", plate_ref=ref)
        if self.plate_faces.get(ref, 1) > 1 and self.part.plates.from_name(f"{ref}_02") is not None:
            raise _Refused(
                keyword,
                "the plate was read as several plates and a LoadConceptSurface names one",
                plate_ref=ref,
                faces=self.plate_faces[ref],
            )
        return plate


def _bool(value: str) -> bool:
    return str(value).strip().lower() == "true"
