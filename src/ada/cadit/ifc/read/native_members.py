"""Read an IFC's MEMBERS natively -- beams and plates, no ifcopenshell, no tessellation.

WHY THIS EXISTS. A clash check asks what meets what, and a quantity take-off asks how much of
what; both are questions about MEMBERS -- a beam of this section running from here to there, a
plate of this thickness in this plane -- and neither needs a triangle. Until now the only way to
get members out of an IFC was `ada.from_ifc`, which reads the file with ifcopenshell and builds
the geometry on the way past. That is the expensive half, and it is the half these two callers
throw away:

* the viewer's `.ifc -> glb` conversion is already fully native (adacpp reads the file in C++ and
  writes the GLB itself), so a clash check on that source paid for a SECOND, semantic read;
* the take-off pays for the same second read, which is why it is bounded by source size today
  (`converters/takeoff.source_is_small_enough`).

`adacpp.cad.IfcMemberScan` answers the member question from the handful of entities that state it
-- the Axis polyline, the swept area's profile and outline, the extrusion depth, the placement
chain -- in one streaming pass. This module turns that into the `Beam` and `Plate` objects the
rest of adapy already knows how to reason about, so every pass downstream (`ada.clash`, the
take-off, the connection specs) runs unchanged on top of it.

WHAT IT IS NOT. Not a replacement for `from_ifc`: it carries no geometry, no spatial hierarchy
beyond one flat part, and no products that are neither beam nor plate. It does carry the MATERIAL
each member is associated with, because mass is section x length x density and a take-off that
had to guess at the density would not be a take-off.

WHAT IT REBUILDS. A member the way the ifcopenshell reader rebuilds it from the same file: the
section from the profile's parameter bag or its curves (a fillet read as a fillet), the member in
its local frame under its object placement, its half-space cuts, and its property sets as
metadata -- so a consumer gets the same Beam or Plate whichever reader read the file.
"""

from __future__ import annotations

import pathlib
from typing import TYPE_CHECKING, Iterable, Iterator

from ada.config import logger

if TYPE_CHECKING:
    import numpy as np

__all__ = [
    "ifc_members_to_part",
    "materials_are_complete",
    "members_from_jsonl",
    "members_to_part",
    "native_members_available",
    "native_takeoff_part",
    "scan_ifc_members",
]

#: IFC classes this reader maps. Anything else is skipped -- a member reader that guessed at a
#: railing or a piece of furniture would put objects into a clash check that no spec can detail.
_BEAM_CLASSES = {"IFCBEAM", "IFCCOLUMN", "IFCMEMBER"}
_PLATE_CLASSES = {"IFCPLATE", "IFCSLAB"}

#: Set on the part by `ifc_members_to_part`: how many members the file associated with no
#: material. Read by `materials_are_complete`, which the take-off asks before trusting a mass.
_UNSTATED_MATERIALS = "native_members_unstated_materials"


def native_members_available() -> bool:
    """Whether the installed adacpp can answer the member question at all."""
    try:
        import adacpp.cad  # noqa: PLC0415 - probing an optional backend

        return hasattr(adacpp.cad, "IfcMemberScan")
    except Exception:  # noqa: BLE001 - an adacpp that cannot import is one that cannot answer
        return False


def scan_ifc_members(ifc_file: str | pathlib.Path) -> Iterator[dict]:
    """Stream one member record per product, as `IfcMemberScan` yields them.

    Exposed separately from `ifc_members_to_part` because a caller that only needs to COUNT
    products, or to look at classes and guids, should not pay for building ada objects it will
    drop -- and because the scan is a stream, which a function returning a Part cannot be.
    """
    import adacpp.cad  # noqa: PLC0415 - optional backend, probed by native_members_available

    yield from adacpp.cad.IfcMemberScan(str(ifc_file))


# numpy is imported INSIDE these rather than at module scope: the slim viewer image carries this
# package (the REST clash routes reach it) and carries no numpy, so a module-level import would
# crashloop an API that never calls any of them.
def _world(placement: Iterable[float]) -> "np.ndarray":
    """The 16-float column-major matrix as a 4x4, row-vector convention."""
    import numpy as np

    return np.asarray(list(placement), dtype=float).reshape(4, 4).T


def _to_world_point(m4: "np.ndarray", p: Iterable[float]) -> "np.ndarray":
    import numpy as np

    v = np.asarray([*p, 1.0], dtype=float)
    return (m4 @ v)[:3]


def _to_world_dir(m4: "np.ndarray", d: Iterable[float]) -> "np.ndarray":
    import numpy as np

    v = np.asarray([*d, 0.0], dtype=float)
    out = (m4 @ v)[:3]
    n = float(np.linalg.norm(out))
    return out / n if n > 0 else out


def _geo_curve(curve: dict | None):
    """A scanned profile curve as the ``ada.geom`` curve the ifcopenshell reader builds from the same
    entity (``read.geom.curves``): an ``IfcPolyline`` is a ``PolyLine`` of its points, an
    ``IfcIndexedPolyCurve`` an ``IndexedPolyCurve`` of one ``Edge`` per consecutive pair of a line
    index and one ``ArcLine`` per arc index. Built the same way so the section read from it is the
    same section: the fillet recovery (``_filleted_corner_points``) works on these objects."""
    from ada.geom import curves as geo_cu
    from ada.geom.points import Point

    if not curve or not curve.get("points"):
        return None
    pts = [tuple(float(c) for c in p) for p in curve["points"]]
    if curve.get("kind") == "polyline":
        return geo_cu.PolyLine([Point(p) for p in pts])
    if curve.get("kind") != "indexed":
        return None
    segments = []
    stated = curve.get("segments")
    if stated is None:
        # No Segments: the points are one polyline.
        stated = [("line", tuple(range(1, len(pts) + 1)))]
    for kind, idx in stated:
        value = [int(i) - 1 for i in idx]
        if any(i < 0 or i >= len(pts) for i in value):
            return None
        if kind == "arc" and len(value) == 3:
            segments.append(geo_cu.ArcLine(pts[value[0]], pts[value[1]], pts[value[2]]))
        else:
            segments.extend(geo_cu.Edge(pts[a], pts[b]) for a, b in zip(value[:-1], value[1:]))
    return geo_cu.IndexedPolyCurve(segments)


def _scaled_params(params: dict, unit_scale: float) -> dict:
    """The section parameter bag in metres: its LENGTH keys are stated in the file's unit."""
    from ada.cadit.ifc.sections_props import SECTION_PARAM_KEYS

    if unit_scale == 1.0:
        return dict(params)
    return {
        k: (float(v) * unit_scale if k in SECTION_PARAM_KEYS and isinstance(v, (int, float)) else v)
        for k, v in params.items()
    }


def _section_from_curves(member: dict, name, params: dict | None):
    from ada.cadit.ifc.read.read_beam_section import _curve_to_points2d
    from ada.sections.from_geometry import section_from_polyline

    outer = _geo_curve(member.get("outer_curve"))
    if outer is None:
        return None
    try:
        outer_pts = _curve_to_points2d(outer)
        inners = [_geo_curve(c) for c in member.get("inner_curves") or ()]
        inner_pts = [_curve_to_points2d(c) for c in inners if c is not None]
    except NotImplementedError as exc:
        logger.debug(f"native members: profile curve of {member.get('name')!r} not readable ({exc})")
        return None
    return section_from_polyline(outer_pts, inner_pts[0] if inner_pts else None, name=name, params=params)


def _section_for(member: dict):
    """The member's section, in the order the ifcopenshell reader resolves one
    (``read_beam_section.import_section_from_ifc``), so the two readers agree on the same file:

    1. the adapy parameter bag on the profile (``ADA_SectionParameters``) -- exact for every type;
       a POLY section's bag holds only the scalars its curves cannot state (a hollow section's
       wall), and its shape is still read from the curves;
    2. a parametric IFC profile (I, T, U, circular hollow), from its stated dimensions;
    3. the profile's curves, arcs kept, recognised as a parametric section or kept as POLY;
    4. the profile name, through adapy's section-string parser;
    5. the sampled outline, as a POLY section -- for a profile none of the above can read.
    """
    from ada import Section
    from ada.api.curves import CurvePoly2d
    from ada.cadit.ifc.sections_props import ADA_SECTION_PSET, section_from_param_dict
    from ada.sections.categories import BaseTypes

    name = member.get("profile_name") or None
    unit_scale = float(member.get("unit_scale") or 1.0)

    bag = (member.get("profile_properties") or {}).get(ADA_SECTION_PSET)
    if bag:
        params = _scaled_params(bag, unit_scale)
        if params.get("sec_type") == BaseTypes.POLY.value:
            sec = _section_from_curves(member, name, params)
            if sec is not None:
                return sec
        else:
            try:
                return section_from_param_dict(name, params)
            except Exception as exc:  # noqa: BLE001 - a bag this adapy cannot read: use the geometry
                logger.debug(f"native members: section bag of {member.get('name')!r} unreadable ({exc})")

    ptype = (member.get("profile_type") or "").upper()
    pp = member.get("profile_params") or {}
    if ptype == "IFCISHAPEPROFILEDEF" and pp:
        return Section(
            name=name,
            sec_type=Section.TYPES.IPROFILE,
            h=pp.get("OverallDepth"),
            w_top=pp.get("OverallWidth"),
            w_btn=pp.get("OverallWidth"),
            t_w=pp.get("WebThickness"),
            t_ftop=pp.get("FlangeThickness"),
            t_fbtn=pp.get("FlangeThickness"),
            r=pp.get("FilletRadius"),
            sec_str=name,
        )
    if ptype == "IFCTSHAPEPROFILEDEF" and pp:
        return Section(
            name=name,
            sec_type=Section.TYPES.TPROFILE,
            h=pp.get("Depth"),
            w_top=pp.get("FlangeWidth"),
            w_btn=pp.get("WebThickness"),
            t_w=pp.get("WebThickness"),
            t_ftop=pp.get("FlangeThickness"),
            t_fbtn=pp.get("FlangeThickness"),
        )
    if ptype == "IFCUSHAPEPROFILEDEF" and pp:
        return Section(
            name=name,
            sec_type=Section.TYPES.CHANNEL,
            h=pp.get("Depth"),
            w_top=pp.get("FlangeWidth"),
            w_btn=pp.get("FlangeWidth"),
            t_w=pp.get("WebThickness"),
            t_ftop=pp.get("FlangeThickness"),
            t_fbtn=pp.get("FlangeThickness"),
        )
    if ptype == "IFCCIRCLEHOLLOWPROFILEDEF" and pp:
        return Section(name=name, sec_type="TUB", r=pp.get("Radius"), wt=pp.get("WallThickness"))

    sec = _section_from_curves(member, name, None)
    if sec is not None:
        return sec

    if name and name.strip():
        from ada.sections.string_to_section import interpret_section_str

        try:
            sec, _ = interpret_section_str(name.strip())
            if sec is not None:
                return sec
        except Exception as exc:  # noqa: BLE001 - an unparsable name is data, not a failure
            logger.debug(f"native members: section name {name!r} not in the catalogue ({exc}); using the outline")

    outline = [tuple(pt) for pt in member.get("outline") or ()]
    if len(outline) < 3:
        return None
    return Section(
        (name or "").strip() or f"profile_{member['id']}",
        sec_type=BaseTypes.POLY,
        outer_poly=CurvePoly2d(outline, origin=(0, 0, 0), xdir=(1, 0, 0), normal=(0, 0, 1)),
    )


def _world_placement(member: dict):
    """The product's world placement as an ``ada`` ``Placement``, in metres, or None when the file
    states none. The double-precision matrix where the scan reports one."""
    import numpy as np

    from ada.api.transforms import Placement

    raw = member.get("placement_d") or member.get("placement")
    if raw is None or len(raw) != 16:
        return None
    m4 = np.asarray(list(raw), dtype=float).reshape(4, 4).T  # column-major -> row-major
    m4[:3, 3] *= float(member.get("unit_scale") or 1.0)
    return Placement.from_4x4_matrix(m4)


def _half_spaces(member: dict, name: str) -> list:
    """The scanned half-space cuts as the ``BoolHalfSpace`` primitives the writer made them from
    (``read_beams.half_space_from_ifc``): plane location and axis, and the agreement flag as
    ``flip``. In the product's local frame, which is the frame the member is built in."""
    from ada import BoolHalfSpace

    return [
        BoolHalfSpace(
            origin=[float(c) for c in cut["origin"]],
            normal=[float(c) for c in cut["normal"]],
            flip=bool(cut["agreement"]),
            name=f"{name}_cut{i}",
        )
        for i, cut in enumerate(member.get("cuts") or ())
    ]


def _member_name(member: dict) -> str:
    return member.get("name") or member.get("guid") or f"member_{member.get('id')}"


def _beam_from(member: dict, mat=None):
    """A ``Beam`` the way the ifcopenshell reader builds one from a straight extrusion
    (``read_beams.import_straight_beam``): the node line in the product's LOCAL frame -- the
    extrusion's position, moved by the section centroid's offset -- the full object placement as
    the beam's placement, ``up`` the profile's local y. A product with no extrusion to read falls
    back to its reference line in world coordinates."""
    import numpy as np

    from ada import Beam

    name = _member_name(member)
    sec = _section_for(member)
    if sec is None:
        return None
    guid = member.get("guid") or None

    if member.get("origin") is None or not member.get("depth"):
        p1, p2 = member.get("p1"), member.get("p2")
        if p1 is None or p2 is None:
            return None
        axis = np.asarray(p2, dtype=float) - np.asarray(p1, dtype=float)
        length = float(np.linalg.norm(axis))
        if length < 1e-9:
            return None  # a zero-length member is not one; it would divide by zero downstream
        return Beam(name, p1, p2, sec, mat=mat, up=_beam_up(member, axis / length), guid=guid)

    from ada.api.beams.justification import section_centroid_offset_local
    from ada.core.vector_utils import calc_yvec, unit_vector

    extrude_dir = unit_vector(np.asarray(member["normal"], dtype=float))
    ref_dir = unit_vector(np.asarray(member["xdir"], dtype=float))
    local_y = calc_yvec(ref_dir, extrude_dir)
    p1 = np.asarray(member["origin"], dtype=float)
    shift = float(section_centroid_offset_local(sec, strict=False)[2])
    if shift != 0.0:
        p1 = p1 + shift * np.asarray(local_y, dtype=float)
    p2 = p1 + extrude_dir * float(member["depth"])

    extra = {}
    placement = _world_placement(member)
    if placement is not None:
        extra["placement"] = placement
    beam = Beam(name, p1, p2, sec=sec, mat=mat, up=local_y, guid=guid, **extra)
    for cut in _half_spaces(member, name):
        beam.add_boolean(cut)
    return beam


def _beam_up(member: dict, xvec: "np.ndarray"):
    """The beam's ``up``: the swept profile's +y, in world -- or None to keep ``Beam``'s default.

    A section's outline is stated in the profile's own frame, whose +x and +y are a beam's
    ``yvec`` and ``up``. Without this every beam took ``Beam``'s default ``up`` and so its
    section came back rotated about the axis -- a wrong answer for anything not symmetric about
    both axes, and a wrong footprint for every clash. Left at the default only when the file
    states no swept plane, or one whose profile is not square to the reference line (a profile
    not normal to the axis is not a section of it)."""
    import numpy as np

    if any(member.get(k) is None for k in ("placement", "xdir", "normal")):
        return None
    m4 = _world(member["placement"])
    up = np.cross(_to_world_dir(m4, member["normal"]), _to_world_dir(m4, member["xdir"]))
    n = float(np.linalg.norm(up))
    if n < 1e-9 or abs(float(np.dot(up / n, xvec))) > 1e-6:
        return None
    return up / n


def _plate_points(member: dict) -> list:
    """The plate outline as the ifcopenshell reader takes it (``read_plates.import_ifc_plate``):
    the profile curve's ``to_points2d`` -- corners, a filleted corner as ``(x, y, r)`` -- else the
    polyline's points, else the sampled outline."""
    curve = _geo_curve(member.get("outer_curve"))
    if curve is not None and hasattr(curve, "to_points2d"):
        try:
            return [tuple(p) for p in curve.to_points2d()]
        except Exception as exc:  # noqa: BLE001 - fall back to the plain points
            logger.debug(f"native members: plate outline of {member.get('name')!r} not read as corners ({exc})")
    if curve is not None and hasattr(curve, "points"):
        pts = [tuple(float(c) for c in p[:2]) for p in curve.points]
        if len(pts) > 1 and pts[0] == pts[-1]:
            pts = pts[:-1]
        return pts
    return [tuple(pt) for pt in member.get("outline") or ()]


def _plate_from(member: dict, mat=None):
    """A ``Plate`` the way the ifcopenshell reader builds one from an extruded arbitrary profile: its
    outline, the extrusion depth as thickness, the swept plane in the product's LOCAL frame and the
    object placement as the plate's placement."""
    from ada import Plate

    outline = _plate_points(member)
    if len(outline) < 3 or member.get("origin") is None:
        return None
    name = _member_name(member)
    extra = {}
    placement = _world_placement(member)
    if placement is not None:
        extra["placement"] = placement
    plate = Plate(
        name,
        outline,
        member["depth"],
        origin=member["origin"],
        xdir=member["xdir"],
        normal=member["normal"],
        mat=mat,
        guid=member.get("guid") or None,
        **extra,
    )
    for cut in _half_spaces(member, name):
        plate.add_boolean(cut)
    return plate


def member_metadata(member: dict) -> dict:
    """The product's property sets as the ifcopenshell reader puts them on an object's
    ``metadata`` (``reader_utils.get_ifc_property_sets``): one dict per set, keyed by the set's name
    up to any ``:``."""
    out: dict = {}
    for set_name, props in (member.get("psets") or {}).items():
        out[str(set_name).split(":")[0].strip()] = dict(props)
    return out


def member_object(member: dict, materials: dict | None = None):
    """The ``Beam`` or ``Plate`` one scanned member record maps to, with its material and property
    sets, or None for a record that is neither (or that cannot be read as one)."""
    materials = materials if materials is not None else {}
    cls = (member.get("ifc_class") or "").upper()
    if cls not in _BEAM_CLASSES and cls not in _PLATE_CLASSES:
        return None
    mat = _material_for(member, materials)
    obj = _beam_from(member, mat) if cls in _BEAM_CLASSES else _plate_from(member, mat)
    if obj is None:
        return None
    obj.metadata = member_metadata(member)
    return obj


#: How a stated property name maps onto an ada material model. The same names, and the same
#: defaults, as the ifcopenshell reader uses (`read_materials.MaterialImporter`) -- the two paths
#: describe the same file and a take-off must not depend on which one read it.
_MAT_PROPS = {
    "YoungModulus": ("E", 210000e6),
    "YieldStress": ("sig_y", 355e6),
    "MassDensity": ("rho", 7850.0),
    "PoissonRatio": ("v", 0.3),
    "ThermalExpansionCoefficient": ("alpha", 1.2e-5),
    "SpecificHeatCapacity": ("zeta", 1.15),
}

#: Which stated property carries the grade label. Exporters disagree -- adapy writes "Grade", the
#: IFC material-properties convention is "StrengthGrade" -- so both are read.
_GRADE_NAMES = ("StrengthGrade", "Grade")


def _material_for(member: dict, cache: dict):
    """The member's material, or None where the file associates it with none.

    Shared, not copied: a model states a handful of materials and uses each on thousands of
    members, so one `Material` per NAME is built and handed to every member that names it. Two
    materials sharing a name but not their properties would be a contradiction in the file; the
    first reading wins and the rest are the same object, which is also what `Part` would do with
    them on the way in.
    """
    name = (member.get("material") or "").strip()
    if not name:
        return None
    if name in cache:
        return cache[name]

    from ada import Material
    from ada.materials.metals import CarbonSteel, Metal

    stated = member.get("material_props") or {}
    props = {}
    for stated_name, (arg, default) in _MAT_PROPS.items():
        value = stated.get(stated_name)
        props[arg] = float(value) if isinstance(value, (int, float)) else default

    grade = next((str(stated[n]) for n in _GRADE_NAMES if isinstance(stated.get(n), str)), None)
    # Only a grade the catalogue KNOWS: `CarbonSteel` looks its yield and ultimate stress up by
    # name, so an unlisted one (S235, a project label) would raise on a file that is perfectly
    # valid. Its properties are stated anyway, and `Metal` carries them without the lookup.
    if grade in CarbonSteel.GRADES:
        model = CarbonSteel(grade=grade, **props)
    else:
        model = Metal(sig_u=None, **props)

    cache[name] = Material(name=name, mat_model=model)
    return cache[name]


def members_from_jsonl(jsonl: str | pathlib.Path) -> Iterator[dict]:
    """Stream member records out of a scan written as JSONL (`adacpp.ifc_members/1`).

    THE BROWSER'S ROUTE IN. embind has no cheap way to hand JS one dict per member, so the wasm
    build writes the scan to a file instead (adacpp `scanMembers`) -- and a file is also what
    crosses into pyodide, where this module runs unchanged. The header line is skipped rather
    than returned: it says what the file is, and every caller here already knows.

    Line by line, never `read()`: a plant's scan is the one artifact in this path big enough to
    matter, and holding it whole would give back exactly what streaming the scan bought.
    """
    import json

    with open(jsonl, encoding="utf-8") as fh:
        first = fh.readline()
        unit_scale = None
        if first:
            header = json.loads(first)
            # A header is how a scan says what it is. A file whose first line is a MEMBER is a
            # different format that happens to parse, so it is refused rather than half-read.
            if "schema" not in header:
                raise ValueError(f"{jsonl} is not a member scan: no schema header")
            unit_scale = header.get("unit_scale")
        for line in fh:
            line = line.strip()
            if line:
                member = json.loads(line)
                # The file's unit is stated once, in the header; a record carries it like the
                # binding's records do, so the mapping reads both the same way.
                if unit_scale is not None:
                    member.setdefault("unit_scale", unit_scale)
                yield member


def members_to_part(members: Iterable[dict], name: str = "ifc_members"):
    """One flat `Part` from member RECORDS, whatever produced them.

    Split from `ifc_members_to_part` so the mapping has exactly one implementation across the
    three ways a scan arrives: the adacpp binding on a worker, a JSONL file written by the wasm
    build in a browser, and a scan handed over from somewhere else entirely. The records are the
    same shape in all three by construction (adacpp holds its two builds to it), and a second
    mapping here would be the place that quietly stopped being true.
    """
    from ada import Part

    part = Part(name)
    skipped: dict[str, int] = {}
    materials: dict[str, object] = {}
    unstated = 0
    for member in members:
        cls = (member.get("ifc_class") or "").upper()
        obj = member_object(member, materials)
        if obj is None:
            skipped[cls or "?"] = skipped.get(cls or "?", 0) + 1
            continue
        if not (member.get("material") or "").strip():
            # Left on the object's own default rather than counted as a failure: a clash check
            # never asks what a member is made of, so a file that states no material still
            # answers every question THIS reader exists for. Only the take-off cares, and it
            # checks `materials_are_complete` before trusting the mass it computes.
            unstated += 1
        part.add_object(obj)
    if skipped:
        # Counted per class rather than logged per product: a plant has thousands of products that
        # are neither beam nor plate, and one line per product would bury the run.
        logger.info(f"native members: skipped {sum(skipped.values())} product(s) by class {skipped}")
    if unstated:
        logger.info(f"native members: {unstated} member(s) state no material; each keeps its default")
    part.metadata[_UNSTATED_MATERIALS] = unstated
    return part


def _has_members(part) -> bool:
    from ada import Beam, Plate

    return any(True for _ in part.get_all_physical_objects(by_type=(Beam, Plate)))


def load_members_or_model(src_path: str | pathlib.Path, ext: str, fallback):
    """The members of ``src_path``, natively where that is possible and fully where it is not.

    The one place that decides, so the four job entry points that need members for a clash check
    do not each grow their own version of the question. ``fallback`` is the full reader
    (``_load_with_ada``), used for every source that is not an IFC and whenever adacpp is too old
    to answer -- a deployment on an older backend keeps working, one release behind on speed
    rather than broken.

    A native read is preferred for IFC because the viewer's own `.ifc -> glb` conversion is
    already native: without this, checking the model a user just converted meant reading the same
    file a second time, with ifcopenshell, to recover members the first read had in its hands.
    """
    ext = (ext or "").lower()
    if ext in (".ifc", ".ifcxml") and native_members_available():
        try:
            part = ifc_members_to_part(src_path, name=pathlib.Path(src_path).stem or "ifc_members")
        except Exception as exc:  # noqa: BLE001 - a native read that fails is not a failed job
            logger.warning(f"native member read of {src_path} failed ({exc}); falling back to the full reader")
        else:
            # EMPTY IS NOT AN ANSWER HERE. A file the scan could not make sense of reads as zero
            # members, and so does a file that genuinely has none -- the two are indistinguishable
            # from this side, and the caller would be told "this source has no beams or plates",
            # which is a confident wrong answer for the first case. The full reader is the
            # authority on which it is, so an empty native read defers to it. The cost lands only
            # on sources that really carry no members, which pay one read to say so.
            if _has_members(part):
                return part
            logger.info(f"native member read of {src_path} found no members; deferring to the full reader")
    return fallback(pathlib.Path(src_path), ext)


def ifc_members_to_part(ifc_file: str | pathlib.Path, name: str = "ifc_members"):
    """One flat `Part` of the beams and plates an IFC states, read natively.

    Consumed as a STREAM: each product is turned into its object and the record dropped, so a
    plant-sized file is never held as a list of members on either side of the boundary.
    """
    return members_to_part(scan_ifc_members(ifc_file), name=name)


def materials_are_complete(part) -> bool:
    """Whether every member the native read produced carries a material the FILE stated.

    A mass is a volume times a density, and a member left on its default density still produces
    a number -- a plausible, confidently wrong one. So a take-off computed from a native read is
    only trustworthy when nothing was defaulted, and this is the question it asks.
    """
    return not part.metadata.get(_UNSTATED_MATERIALS, 0)


def native_takeoff_part(src_path: str | pathlib.Path, ext: str):
    """A part to take off natively, or None to let the caller read the source the slow way.

    The take-off's second read is the one place that must be stricter than the clash check. A
    clash check asks where things are, which this reader answers for every member; a take-off
    asks how much they weigh, which it can only answer where the file states materials and
    sections it recognises. So: no members, or any member on a defaulted material, and the
    answer is None -- the caller then falls back to the full reader under its size bound, exactly
    as before this path existed.
    """
    if (ext or "").lower() not in (".ifc", ".ifcxml") or not native_members_available():
        return None
    try:
        part = ifc_members_to_part(src_path, name=pathlib.Path(src_path).stem or "ifc_members")
    except Exception as exc:  # noqa: BLE001 - a take-off is never a reason to fail a conversion
        logger.info(f"native take-off read of {src_path} failed ({exc}); falling back")
        return None
    if not _has_members(part):
        return None
    if not materials_are_complete(part):
        logger.info(f"native take-off of {src_path} skipped: the file states no material for some members")
        return None
    return part
