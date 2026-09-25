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

WHAT IT IS NOT. Not a replacement for `from_ifc`: it carries no geometry, no property sets, no
spatial hierarchy beyond one flat part, and no products that are neither beam nor plate. It does
carry the MATERIAL each member is associated with, because mass is section x length x density and
a take-off that had to guess at the density would not be a take-off. It is the cheap answer to one question, and a caller that needs a model should still
read one.
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


def _section_for(member: dict):
    """The member's section: its catalogue name where the file names one, else its OUTLINE.

    A name that adapy's section parser understands carries dimensions and a family, which is what
    a connection spec matches on. A name it does not understand (a project-specific label, an
    empty one) still has a boundary, so the section is built from that rather than guessed at --
    a section invented from a name would put a member in the wrong family, which is a wrong answer
    rather than a missing one.
    """
    from ada import Section
    from ada.api.curves import CurvePoly2d
    from ada.sections.categories import BaseTypes

    name = (member.get("profile_name") or "").strip()
    if name:
        try:
            sec = Section.from_str(name)
            return sec[0] if isinstance(sec, list) else sec
        except Exception as exc:  # noqa: BLE001 - an unparsable name is data, not a failure
            logger.debug(f"native members: section name {name!r} not in the catalogue ({exc}); using the outline")

    outline = [tuple(pt) for pt in member.get("outline") or ()]
    if len(outline) < 3:
        return None
    return Section(
        name or f"profile_{member['id']}",
        sec_type=BaseTypes.POLY,
        poly_outer=CurvePoly2d(outline),
    )


def _beam_from(member: dict):
    from ada import Beam

    p1, p2 = member.get("p1"), member.get("p2")
    if p1 is None or p2 is None:
        return None
    sec = _section_for(member)
    if sec is None:
        return None
    import numpy as np

    if float(np.linalg.norm(np.asarray(p2, dtype=float) - np.asarray(p1, dtype=float))) < 1e-9:
        return None  # a zero-length member is not one; it would divide by zero downstream
    return Beam(member["name"] or member["guid"], p1, p2, sec, guid=member["guid"] or None)


def _plate_from(member: dict):
    from ada import Plate

    outline = [tuple(pt) for pt in member.get("outline") or ()]
    if len(outline) < 3 or member.get("origin") is None:
        return None
    # The scan reports the swept plane in the product's LOCAL frame beside the world placement, so
    # the two can be taken apart; a plate wants them put together, in world, because that is the
    # frame its neighbours are in and a clash check compares them against each other.
    m4 = _world(member["placement"])
    return Plate(
        member["name"] or member["guid"],
        outline,
        member["depth"],
        origin=_to_world_point(m4, member["origin"]),
        xdir=_to_world_dir(m4, member["xdir"]),
        normal=_to_world_dir(m4, member["normal"]),
        guid=member["guid"] or None,
    )


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
        if first:
            header = json.loads(first)
            # A header is how a scan says what it is. A file whose first line is a MEMBER is a
            # different format that happens to parse, so it is refused rather than half-read.
            if "schema" not in header:
                raise ValueError(f"{jsonl} is not a member scan: no schema header")
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


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
        obj = None
        if cls in _BEAM_CLASSES:
            obj = _beam_from(member)
        elif cls in _PLATE_CLASSES:
            obj = _plate_from(member)
        if obj is None:
            skipped[cls or "?"] = skipped.get(cls or "?", 0) + 1
            continue
        mat = _material_for(member, materials)
        if mat is None:
            # Left on the object's own default rather than counted as a failure: a clash check
            # never asks what a member is made of, so a file that states no material still
            # answers every question THIS reader exists for. Only the take-off cares, and it
            # checks `materials_are_complete` before trusting the mass it computes.
            unstated += 1
        else:
            obj.material = mat
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
