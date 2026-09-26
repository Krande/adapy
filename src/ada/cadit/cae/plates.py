"""Plates for the Abaqus/CAE writer: the ACIS body adapy already writes, and the points that
locate its faces once CAE has imported it.

Nothing here rebuilds an outline in CAE. adapy's own SAT writer
(:func:`ada.cadit.sat.write.writer.part_to_sat_writer`) produces the body Genie itself consumes —
arcs analytic, splines carried as NURBS patches, and the plates mutually split *and split along the
beam axes lying on them* — and CAE reads it: measured, a 3 x 2 m plate with a stiffener along its
middle imports as 2 faces / 7 edges / 6 vertices with the stiffener line a **single edge bounding
both faces**, and the two face areas exactly 3.0 each against adapy's own 6.0. Rebuilding the
outline would instead compound the losses the Genie reader already documents (best-fit planes,
chorded splines), so the SAT is the input and this module's whole job is to say *where* each face is.

**Why a point and not a name.** ``PartFromGeometryFile`` discards the ACIS attributes: after the
import ``part.sets.keys()`` is ``[]``, so the ``FACE00000001`` names adapy writes reach Abaqus as
nothing at all. A face is therefore located by ``findAt`` at a point on it, and the point has to be
*strictly interior*: measured, ``findAt`` at a point on the edge two faces share returns **one** of
them, arbitrarily, and ``findAt`` off the body prints a warning and returns an empty sequence rather
than raising. Both failure modes are silent, which is why the emitted script's guard is "every face
of the part is covered by exactly one shell section" rather than anything the lookups report about
themselves.

**Where the geometry comes from.** The locators are walked out of the ``SatWriter``'s own entity
graph — the body that is handed to CAE — and not recomputed from the plate outlines. That matters
because there are three ways a face gets authored (the planar imprint, the unfused one-face-per-plate
path, and a Genie topology store) and only the authored body knows which one ran. A plane face's
boundary comes from its loop's edge endpoints, which every path fills in; a spline face has no
polygon, so a curved plate is measured through the CAD backend instead and is refused when that
cannot be done.

**The refusal that matters most.** A beam whose axis lies *on* a plate is refused, and the reason is
measured rather than cautious: in Abaqus/CAE 2025 an edge shared with a face carries a section
assignment, reads it back and exports a ``*Beam Section`` keyword, and then **produces no elements
when the part is meshed**. See :data:`BEAM_ON_PLATE_REFUSAL` for the numbers. The test for it is not
geometric guesswork either: the SAT writer already reports, per beam, the edges that beam was
imprinted into (``SatWriter.edge_map``), so the body itself says which beams lie on a plate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np

from ada.config import get_logger

if TYPE_CHECKING:
    from ada import Part

logger = get_logger()


class PlateNotSupported(Exception):
    """A plate, or a beam lying on one, that this writer will not approximate."""


#: How closely the area CAE measures for a plate must match adapy's own, relative to adapy's.
#:
#: Measured, so this is a noise floor and not a hope. A flat 3 x 2 m plate split in two by a
#: stiffener came back as ``3.0 + 3.0`` against ``6.0`` -- exact. A real curved plate (a Genie
#: ``curved_shell``, rational NURBS patch, 10-edge boundary) came back as ``6.91520453146419``
#: against adapy's own OCC measurement of ``6.915204361623685``: relative ``2.5e-08``. A 2 x 1.5 m
#: plate tilted 30 degrees came back as ``2.99999999016774`` against ``3.0``: ``3.3e-09``. So 1e-06
#: is two orders above the worst of the three and every defect it exists for is far larger -- a
#: thickness on the wrong plate, a face nobody located, or the case below.
#:
#: The comparison is deliberately **absolute, scaled by the expected area**, rather than relative to
#: the built one: a spline face Abaqus considers invalid reports ``getSize() == 0.0`` rather than
#: raising (measured), and a relative form would divide by that zero instead of failing.
PLATE_AREA_REL_TOL = 1e-06

#: How closely the face normal CAE reports must match the plate's declared normal.
#:
#: Measured to be exact: a 3 x 2 m outline declared ``+z`` imports with
#: ``face.getNormal() == (0, 0, 1)`` and the same outline declared ``-z`` with ``(0, 0, -1)``, both
#: with ``face.isNormalFlipped() == False``. A tilted plate reported ``(0, -0.5, 0.866025)`` against
#: adapy's ``(0, -0.5, 0.8660254)``, which is CAE's six-figure print and not a disagreement. So the
#: sense adapy writes into the SAT survives the import and no flip is applied anywhere -- and
#: because that is a *measurement* rather than a documented guarantee, the emitted script asserts it.
PLATE_NORMAL_TOL = 1e-05

#: Grid resolution for the interior-point search, and how many times it refines around its best hit.
#:
#: The area centroid is not good enough and that is measured, not hypothetical: the C-shaped outline
#: ``(0,0) (3,0) (3,1) (1,1) (1,2) (3,2) (3,3) (0,3)`` has its centroid at ``(1.357, 1.5)``, which
#: falls in the notch, and ``findAt`` there returned 0 faces with only a warning. So the point is
#: chosen to **maximise its clearance from the boundary**, which also keeps it clear of the edge a
#: neighbouring sub-face shares -- the other silent case. 17 x 17 with two refinements resolves a
#: feature about 1/600th of the outline's own size, and the whole search is deterministic.
INTERIOR_GRID = 17
INTERIOR_REFINEMENTS = 2

#: A face whose interior clearance is below this fraction of its own diameter has no point that can
#: be trusted to land on it rather than on a neighbour. Refused by name rather than emitted.
MIN_INTERIOR_CLEARANCE_FRACTION = 1e-06

#: Why a beam lying on a plate is refused, with the measurement that condemns it.
BEAM_ON_PLATE_REFUSAL = (
    "in Abaqus/CAE 2025 an edge shared with a shell face produces NO beam elements. Measured on a "
    "3 x 2 m plate split by a stiffener along its middle, shell sections on both faces and a "
    "BeamSection on the shared edge: 'generateMesh()' gave {'S4R': 96} with no B31 at all, 0 of the "
    "13 nodes on the stiffener line was shared by a shell and a beam element, and "
    "'getUnmeshedRegions()' reported 'faces 2, edges 0' -- CAE never saw an edge to mesh. CAE's own "
    "exported INP then carries '*Element, type=S4R' beside '*Beam Section, elset=..., section=I', so "
    "the deck holds a beam section bound to an element set with nothing in it. The same holds for a "
    "beam collinear with a plate BOUNDARY edge, where the wire is absorbed without even changing the "
    "edge or vertex count. Five variants were tried (the element type assigned through a Set, the "
    "beam section assigned before the shell sections, generateMesh() re-run on the edges, "
    "seedEdgeBySize on the edge first, and letting CAE do its own imprint from an unsplit face) and "
    "all five give {'S4R': 96}; the control, the same beam moved clear of every face, gives "
    "{'S4R': 96, 'B31': 12}. getMassProperties() cannot see the defect either -- it reports 687.38 "
    "for a plate of 565.2 plus an IPE300 that the solver will never integrate -- and neither can the "
    "topology guard, because in the collinear case nothing about the counts changes. "
    "mergeType=SEPARATE is not an escape: it does give {'S4R': 96, 'B31': 12} and then leaves two "
    "coincident unmerged nodes at (1.5, 1.0, 0.0), one per element family, so the stiffener has "
    "elements and is attached to nothing. A beam meeting a plate at a POINT is fine and is built: "
    "measured, a column landing on a plate boundary splits that edge and its node comes out shared, "
    "['S4R', 'S4R', 'B31']"
)


@dataclass(frozen=True)
class FaceLocator:
    """One face of the authored body, and the point the emitted script finds it by."""

    #: The ``FACE00000001``-style name adapy gave it. Carried for traceability only: CAE
    #: discards it on import (measured), so nothing in the emitted script can look it up.
    sat_face_name: str
    #: A point strictly inside the face, in the model's own global coordinates.
    point: tuple[float, float, float]
    #: The face's own normal, as adapy authored it. Asserted against CAE's.
    normal: tuple[float, float, float]
    #: How far the point is from the nearest boundary, in length units. Reported so a
    #: marginal face is visible in the sidecar rather than only when it fails.
    clearance: float


@dataclass
class PlatePlan:
    """One adapy plate, and everything the emitted script needs to dress its faces."""

    plate_name: str
    kind: str
    cae_set_name: str
    cae_section_name: str
    thickness: float
    material_name: str
    #: adapy's own area for the whole plate -- ``poly.get_area()`` for a flat one, the CAD
    #: backend's measurement of the bare face for a curved one. The sum of the built faces'
    #: areas is checked against this, which is what makes arcs and splines honest: the
    #: boundary polygon this module walks is inscribed in an arc, adapy's area is not.
    area: float
    normal: tuple[float, float, float]
    faces: list[FaceLocator] = field(default_factory=list)


@dataclass
class PlateBody:
    """The ACIS body for one adapy ``Part``, and the plate plans that point into it."""

    sat_text: str
    plates: list[PlatePlan] = field(default_factory=list)
    #: beam name -> the SAT edge names its axis was imprinted into. Non-empty for a beam
    #: lying on a plate, which is refused; kept so the refusal can name the edges.
    beams_on_plates: dict[str, list[str]] = field(default_factory=dict)
    #: Every vertex of the authored body, sorted. These are geometric vertices CAE will hold
    #: after the import, so they belong in the bounding box the script checks and in the index
    #: a support or a load is resolved against -- a plate corner is somewhere a support can
    #: legitimately sit, and before this it was not a place the writer knew about.
    vertices: list[tuple[float, float, float]] = field(default_factory=list)

    @property
    def face_count(self) -> int:
        return sum(len(plate.faces) for plate in self.plates)


# --------------------------------------------------------------------------------------
# 2D geometry: the boundary polygon of a plane face, and a point strictly inside it
# --------------------------------------------------------------------------------------


def _point_in_polygon(point, polygon) -> bool:
    """Crossing number. Boundary membership is deliberately undefined -- the caller wants
    strictly interior points and scores them by clearance, so a boundary hit loses anyway."""
    x, y = point
    inside = False
    count = len(polygon)
    for index in range(count):
        x1, y1 = polygon[index]
        x2, y2 = polygon[(index + 1) % count]
        if (y1 > y) != (y2 > y):
            crossing = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < crossing:
                inside = not inside
    return inside


def _distance_to_segments(point, loops) -> float:
    """Shortest distance from ``point`` to any boundary segment of any loop."""
    px, py = point
    best = None
    for loop in loops:
        count = len(loop)
        for index in range(count):
            ax, ay = loop[index]
            bx, by = loop[(index + 1) % count]
            dx, dy = bx - ax, by - ay
            length2 = dx * dx + dy * dy
            if length2 <= 0.0:
                distance = ((px - ax) ** 2 + (py - ay) ** 2) ** 0.5
            else:
                t = ((px - ax) * dx + (py - ay) * dy) / length2
                t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
                distance = ((px - ax - t * dx) ** 2 + (py - ay - t * dy) ** 2) ** 0.5
            if best is None or distance < best:
                best = distance
    return 0.0 if best is None else best


def _inside(point, loops) -> bool:
    """Inside the outer loop and outside every hole."""
    if not _point_in_polygon(point, loops[0]):
        return False
    for hole in loops[1:]:
        if _point_in_polygon(point, hole):
            return False
    return True


def interior_point_2d(loops) -> tuple[tuple[float, float], float]:
    """The interior point of ``loops`` furthest from its boundary, and that distance.

    ``loops[0]`` is the outer boundary and the rest are holes. A grid scan rather than a
    centroid, because the centroid of a C-shaped outline falls outside it (measured), and
    *furthest from the boundary* rather than merely *inside*, because a point close to the
    edge two sub-faces share is a point ``findAt`` may resolve to either of them.
    """
    outer = np.asarray(loops[0], dtype=float)
    low = outer.min(axis=0)
    high = outer.max(axis=0)
    best_point = None
    best_clearance = -1.0
    lo_x, lo_y = float(low[0]), float(low[1])
    hi_x, hi_y = float(high[0]), float(high[1])
    for _ in range(INTERIOR_REFINEMENTS + 1):
        step_x = (hi_x - lo_x) / (INTERIOR_GRID + 1)
        step_y = (hi_y - lo_y) / (INTERIOR_GRID + 1)
        for i in range(1, INTERIOR_GRID + 1):
            for j in range(1, INTERIOR_GRID + 1):
                candidate = (lo_x + i * step_x, lo_y + j * step_y)
                if not _inside(candidate, loops):
                    continue
                clearance = _distance_to_segments(candidate, loops)
                if clearance > best_clearance:
                    best_clearance = clearance
                    best_point = candidate
        if best_point is None:
            break
        # Refine in a window two grid steps wide about the best hit so far, which is where a
        # thin region's own maximum has to be if the coarse pass found it at all.
        lo_x, hi_x = best_point[0] - 2 * step_x, best_point[0] + 2 * step_x
        lo_y, hi_y = best_point[1] - 2 * step_y, best_point[1] + 2 * step_y
    if best_point is None:
        raise PlateNotSupported(
            "no point inside the face could be found on a {0}x{0} grid over its own bounding box. A "
            "face that thin cannot be located by findAt at all, because the point would be within "
            "CAE's own tolerance of its boundary and could resolve to a neighbour".format(INTERIOR_GRID)
        )
    return best_point, best_clearance


def polygon_area(loops) -> float:
    """Shoelace over the outer loop less the holes. Reported, never asserted: an arc's
    boundary polygon is inscribed, so this understates such a face on purpose."""

    def one(loop) -> float:
        total = 0.0
        count = len(loop)
        for index in range(count):
            x1, y1 = loop[index]
            x2, y2 = loop[(index + 1) % count]
            total += x1 * y2 - x2 * y1
        return abs(total) / 2.0

    return one(loops[0]) - sum(one(hole) for hole in loops[1:])


# --------------------------------------------------------------------------------------
# Walking the authored SAT body
# --------------------------------------------------------------------------------------


def _loop_points(loop, face_name: str) -> list[tuple[float, float, float]]:
    """A loop's ordered boundary points, walking the coedge ring in its own direction.

    Only the edge *endpoints* are taken. An arc boundary therefore comes back as its chord,
    which is fine for both jobs this serves -- a point inside the inscribed polygon is inside
    the face, and the area that gets asserted is adapy's own rather than this polygon's.
    """
    points: list[tuple[float, float, float]] = []
    coedge = loop.coedge
    seen: set[int] = set()
    while coedge is not None and id(coedge) not in seen:
        seen.add(id(coedge))
        edge = coedge.edge
        if edge is None:
            raise PlateNotSupported(
                "face {0!r} has a coedge with no edge, so its boundary is not closed".format(face_name)
            )
        start = edge.start_pt if coedge.orientation == "forward" else edge.end_pt
        points.append(tuple(float(c) for c in list(start)[:3]))
        coedge = coedge.next_coedge
    if len(points) < 3:
        raise PlateNotSupported(
            "face {0!r} has a loop of {1} point(s); a face needs at least three".format(face_name, len(points))
        )
    return points


def _plane_frame(surface, sense: str):
    """``(origin, x, y, normal)`` for a plane face, with the face's own sense applied.

    ACIS splits a face's normal between the face record and its surface, and adapy's writer
    uses that: ``Face.sense`` is ``reversed`` on a face whose normal runs against its plane's.
    Reading the surface normal alone would report the wrong side for those.
    """
    origin = np.asarray(list(surface.centroid)[:3], dtype=float)
    normal = np.asarray(list(surface.normal)[:3], dtype=float)
    xdir = np.asarray(list(surface.xvec)[:3], dtype=float)
    normal = normal / np.linalg.norm(normal)
    xdir = xdir - normal * float(np.dot(xdir, normal))
    xdir = xdir / np.linalg.norm(xdir)
    if sense == "reversed":
        normal = -normal
    ydir = np.cross(normal, xdir)
    return origin, xdir, ydir, normal


def _plane_face_locator(face, face_name: str) -> FaceLocator:
    origin, xdir, ydir, normal = _plane_frame(face.surface, face.sense)
    loops2d = []
    loop = face.loop
    while loop is not None:
        points = np.asarray(_loop_points(loop, face_name), dtype=float) - origin
        loops2d.append([(float(np.dot(p, xdir)), float(np.dot(p, ydir))) for p in points])
        loop = loop.next_loop
    point2d, clearance = interior_point_2d(loops2d)
    diameter = float(np.linalg.norm(np.asarray(loops2d[0]).max(axis=0) - np.asarray(loops2d[0]).min(axis=0)))
    if diameter > 0.0 and clearance / diameter < MIN_INTERIOR_CLEARANCE_FRACTION:
        raise PlateNotSupported(
            "face {0!r}'s best interior point is only {1:.6g} length units clear of its boundary, which "
            "is {2:.3g} of the face's own size. findAt at such a point can resolve to a neighbouring "
            "face instead (measured: a point on a shared edge returns one of the two faces, "
            "arbitrarily), so the face is not located rather than located "
            "wrongly".format(face_name, clearance, clearance / diameter)
        )
    point = origin + point2d[0] * xdir + point2d[1] * ydir
    return FaceLocator(
        sat_face_name=face_name,
        point=tuple(float(c) for c in point),
        normal=tuple(float(c) for c in normal),
        clearance=clearance,
    )


def _curved_face_locator(plate, face_name: str) -> tuple[FaceLocator, float]:
    """Locate and measure a spline face through the CAD backend.

    A spline face has no boundary polygon to walk, so the point comes from a parametric scan of
    the plate's own ``AdvancedFace`` -- classified against the face's trimming, so it is inside
    the trimmed region and not merely inside the patch. Measured against CAE on a real Genie
    ``curved_shell``: the point ``(23.503447064906872, 22.211667940320915, 11.042436011849915)``
    resolved to exactly one face, and the areas agreed to 2.5e-08.
    """
    from ada.cad import active_backend, select_backend

    geom = getattr(plate, "geom", None)
    if geom is None or getattr(geom, "geometry", None) is None:
        raise PlateNotSupported(
            "plate {0!r} is a curved plate with no ada.geom face (it was built straight from an OCC "
            "face), so neither its area nor a point on it can be measured without the kernel that "
            "built it".format(plate.name)
        )
    backend = active_backend()
    finder = getattr(backend, "interior_point_on_face", None)
    if finder is None:
        try:
            backend = select_backend("occ")
        except Exception as exc:  # noqa: BLE001 - no pythonocc in this environment
            raise PlateNotSupported(
                "plate {0!r} is a curved plate and no CAD backend here can find a point on its face "
                "({1}). A spline face has no boundary polygon to walk, so there is nothing to locate it "
                "by in the emitted script".format(plate.name, exc)
            ) from exc
        finder = getattr(backend, "interior_point_on_face", None)
        if finder is None:
            raise PlateNotSupported(
                "plate {0!r} is a curved plate and backend {1!r} cannot find a point on a face, so the "
                "emitted script would have nothing to locate it by".format(plate.name, backend.name)
            )
    try:
        shape = backend.build(geom)
        area = float(backend.area(shape))
        point, normal, clearance = finder(shape)
    except PlateNotSupported:
        raise
    except Exception as exc:  # noqa: BLE001 - a kernel failure must name the plate
        raise PlateNotSupported(
            "plate {0!r} is a curved plate whose face the CAD backend could not measure: {1}".format(plate.name, exc)
        ) from exc
    # The bare face, not solid_occ(): with Config().geom_thicken_curved_shells on (the default)
    # solid_occ() returns a thickness-t ClosedShell whose surface area is about twice the face's.
    # Measured on the Genie fixture: 14.042782242424936 against the face's own 6.915204361623685,
    # and CAE reported 6.91520453146419. A check written against solid_occ() would have failed by
    # a factor of two and looked like a units problem.
    return (
        FaceLocator(
            sat_face_name=face_name,
            point=tuple(float(c) for c in point),
            normal=tuple(float(c) for c in normal),
            clearance=float(clearance),
        ),
        area,
    )


# --------------------------------------------------------------------------------------
# The plan
# --------------------------------------------------------------------------------------


def _plate_area(plate) -> float:
    return float(plate.poly.get_area())


def check_no_nested_plates(part: Part) -> None:
    """Refuse a part that owns plates when one of its descendants owns plates too.

    ``part_to_sat_writer`` walks ``get_all_physical_objects``, which descends into subparts, so
    the body written for a parent would carry its children's plates as well -- and this writer
    emits one CAE part per adapy Part, so those faces would be authored into two CAE parts and
    dressed twice. Refused rather than papered over, because "which part owns this plate" is a
    question about the source model and not something a writer should decide.
    """
    own = list(part.plates)
    if not own:
        return
    nested = sorted(
        (sub.name, [pl.name for pl in sorted(sub.plates, key=lambda p: p.name)])
        for sub in part.get_all_subparts(include_self=False)
        if list(sub.plates)
    )
    if nested:
        raise PlateNotSupported(
            "part {0!r} owns {1} plate(s) and so do its subpart(s) {2}. adapy's SAT body is written "
            "per Part and descends into subparts, while this writer emits one CAE part per adapy "
            "Part, so every nested plate would be authored into two CAE parts and given two shell "
            "sections. Move the plates into one part, or give the parent none.".format(
                part.name, len(own), ", ".join("{0!r} ({1})".format(n, ", ".join(p)) for n, p in nested)
            )
        )


def plate_body(part: Part) -> PlateBody | None:
    """The ACIS body for ``part``'s plates, with a locator per authored face.

    ``None`` when the part owns no plates, which is the beams-only case this writer began as.
    Every refusal names the plate it came from.
    """
    from ada.api.plates import PlateCurved
    from ada.cadit.sat.write import sat_entities as se
    from ada.cadit.sat.write.writer import part_to_sat_writer

    check_no_nested_plates(part)
    plates = list(part.plates)
    if not plates:
        return None

    # imprint=True is not the default for convenience: it is what puts the beam axes into the
    # body, and therefore what fills in edge_map -- the body's own record of which beams lie on
    # a plate, which is the refusal below.
    sat_writer = part_to_sat_writer(part, imprint=True)
    if sat_writer.is_empty:
        raise PlateNotSupported(
            "part {0!r} owns {1} plate(s) and adapy's SAT writer authored no geometry for any of them, "
            "so there would be nothing for CAE to import. The plate(s): {2}".format(
                part.name, len(plates), ", ".join(repr(pl.name) for pl in sorted(plates, key=lambda p: p.name))
            )
        )

    faces_by_name = {}
    for face in sat_writer.get_entities_by_type(se.Face):
        if face.name is None:
            raise PlateNotSupported(
                "part {0!r}: the SAT body holds a face with no name, so no plate can claim it".format(part.name)
            )
        faces_by_name[face.name.name] = face

    body = PlateBody(sat_text=sat_writer.to_str())
    claimed: dict[str, str] = {}
    for plate in sorted(plates, key=lambda p: p.name):
        names = sat_writer.face_map.get(plate.guid) or []
        if not names:
            raise PlateNotSupported(
                "plate {0!r} resolves to no face in the SAT body adapy wrote for part {1!r}. A plate "
                "consumed entirely by the imprint, or one whose geometry the SAT writer could not "
                "author, would otherwise be absent from the CAE model with nothing saying "
                "so.".format(plate.name, part.name)
            )
        curved = isinstance(plate, PlateCurved)
        locators: list[FaceLocator] = []
        area = None
        for face_name in names:
            previous = claimed.get(face_name)
            if previous is not None:
                raise PlateNotSupported(
                    "plates {0!r} and {1!r} both claim SAT face {2!r}; the face would be given two "
                    "shell sections".format(previous, plate.name, face_name)
                )
            claimed[face_name] = plate.name
            face = faces_by_name.get(face_name)
            if face is None:
                raise PlateNotSupported(
                    "plate {0!r} names SAT face {1!r}, which the body does not hold".format(plate.name, face_name)
                )
            if type(face.surface) is se.PlaneSurface:
                locators.append(_plane_face_locator(face, face_name))
                continue
            if not curved:
                raise PlateNotSupported(
                    "plate {0!r} is a flat Plate whose SAT face {1!r} was authored on a {2} rather than a "
                    "plane, so this writer cannot state the polygon that locates it".format(
                        plate.name, face_name, type(face.surface).__name__
                    )
                )
            if len(names) > 1:
                raise PlateNotSupported(
                    "plate {0!r} is a curved plate that the imprint split into {1} faces. Each sub-face "
                    "needs a point of its own and the SAT writer does not hand the sub-faces back, so "
                    "only a curved plate authored as one face is translated. Its faces: {2}".format(
                        plate.name, len(names), ", ".join(names)
                    )
                )
            locator, area = _curved_face_locator(plate, face_name)
            locators.append(locator)
        if area is None:
            if curved:
                raise PlateNotSupported(
                    "plate {0!r} is a curved plate whose area was never measured".format(plate.name)
                )
            area = _plate_area(plate)
        if not isinstance(plate, PlateCurved):
            normal = tuple(float(c) for c in plate.outline_global()[1])
        else:
            normal = locators[0].normal
        body.plates.append(
            PlatePlan(
                plate_name=plate.name,
                kind=type(plate).__name__,
                cae_set_name="",
                cae_section_name="",
                thickness=float(plate.t),
                material_name=plate.material.name,
                area=float(area),
                normal=normal,
                faces=locators,
            )
        )

    unclaimed = sorted(set(faces_by_name) - set(claimed))
    if unclaimed:
        raise PlateNotSupported(
            "the SAT body for part {0!r} holds {1} face(s) no plate claims ({2}). Every face in the "
            "imported part has to end with a shell section, so a face nobody owns is a face the "
            "emitted model would carry with no thickness at all.".format(
                part.name, len(unclaimed), ", ".join(unclaimed)
            )
        )

    body.vertices = sorted(
        tuple(float(c) for c in list(vertex.point.point)[:3]) for vertex in sat_writer.get_entities_by_type(se.Vertex)
    )

    bounding = _edges_bounding_a_face(sat_writer)
    beams_by_guid = {bm.guid: bm.name for bm in part.get_all_physical_objects(by_type=_beam_type())}
    for guid, edge_names in sorted(sat_writer.edge_map.items()):
        on_a_face = sorted(name for name in edge_names if name in bounding)
        if not on_a_face:
            continue
        name = beams_by_guid.get(guid)
        if name is not None:
            body.beams_on_plates[name] = on_a_face
    return body


def _edges_bounding_a_face(sat_writer) -> set[str]:
    """The named SAT edges that bound a face, as opposed to standing free in a wire body.

    ``edge_map`` alone is not the test, and that is the trap this exists for: adapy authors a
    beam axis with no plate under it as a **wire** body, and such an edge still has a coedge and
    still gets a name -- measured, a girder 0.3 m below a plate and a column merely touching its
    boundary both appeared in ``edge_map``, and neither lies on a plate. A coedge is owned by a
    ``Loop`` when it bounds a face and by a ``Wire`` when it does not (``se.CoEdge.loop``'s own
    type annotation says so), so the owner's type is what separates the two.
    """
    from ada.cadit.sat.write import sat_entities as se

    bounding: set[str] = set()
    for edge in sat_writer.get_entities_by_type(se.Edge):
        if edge.attrib_name is None:
            continue
        coedge = edge.coedge
        seen: set[int] = set()
        while coedge is not None and id(coedge) not in seen:
            seen.add(id(coedge))
            if type(coedge.loop) is se.Loop:
                bounding.add(edge.attrib_name.name)
                break
            coedge = coedge.partner
    return bounding


def _beam_type():
    from ada import Beam

    return Beam


def check_no_beam_on_a_plate(part_name: str, body: PlateBody) -> None:
    """Guard: refuse every beam whose axis the SAT body imprinted onto a plate face.

    The test is the body's own ``edge_map`` rather than a fresh geometric intersection, because
    the body is what CAE imports: a beam that resolved to an edge bounding a face *is* the case
    :data:`BEAM_ON_PLATE_REFUSAL` measures, whether that edge came from the planar imprint, the
    curved weld or a Genie topology store.
    """
    if not body.beams_on_plates:
        return
    listing = ", ".join(
        "{0!r} (SAT edge(s) {1})".format(name, ", ".join(body.beams_on_plates[name]))
        for name in sorted(body.beams_on_plates)
    )
    raise PlateNotSupported(
        "part {0!r}: {1} beam(s) lie on a plate, and a plate with a beam on it cannot be expressed "
        "as one CAE part at all -- {2}. {3}. Neither half can be dropped to make this work: a "
        "stiffened deck without its plate and a deck without its stiffeners are both the wrong "
        "structure. So write this model with plates=False, which leaves the beams exactly as they "
        "were before plates were translated and lists every plate as untranslated, or split the "
        "plates and the members that lie on them into models of their own.".format(
            part_name, len(body.beams_on_plates), listing, BEAM_ON_PLATE_REFUSAL
        )
    )


__all__ = [
    "BEAM_ON_PLATE_REFUSAL",
    "INTERIOR_GRID",
    "INTERIOR_REFINEMENTS",
    "MIN_INTERIOR_CLEARANCE_FRACTION",
    "PLATE_AREA_REL_TOL",
    "PLATE_NORMAL_TOL",
    "FaceLocator",
    "PlateBody",
    "PlateNotSupported",
    "PlatePlan",
    "check_no_beam_on_a_plate",
    "check_no_nested_plates",
    "interior_point_2d",
    "plate_body",
    "polygon_area",
]
