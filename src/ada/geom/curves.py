from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from itertools import chain
from typing import TYPE_CHECKING, Iterable, Union

import numpy as np

from ada.core.curve_utils import calc_arc_radius_center_from_3points
from ada.core.vector_utils import intersect_calc
from ada.geom.direction import Direction
from ada.geom.placement import Axis2Placement3D
from ada.geom.points import Point

if TYPE_CHECKING:
    from ada.geom.surfaces import SURFACE_GEOM_TYPES

CURVE_GEOM_TYPES = Union[
    "Line",
    "ArcLine",
    "Circle",
    "Ellipse",
    "Parabola",
    "Hyperbola",
    "BSplineCurveWithKnots",
    "RationalBSplineCurveWithKnots",
    "IndexedPolyCurve",
    "PolyLine",
    "TrimmedCurve",
    "CompositeCurve",
    "Clothoid",
    "CosineSpiral",
    "CurveSegment",
    "GradientCurve",
    "SegmentedReferenceCurve",
    "PCurve",
    "SurfaceCurve",
    "PointOnCurve",
    "OffsetCurve3D",
    "GeometricCurveSet",
]


@dataclass(slots=True)
class Line:
    """
    IFC4x3 https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3/HTML/lexical/IfcLine.htm
    STEP AP242 https://www.steptools.com/stds/stp_aim/html/t_line.html
    """

    pnt: Point | Iterable
    dir: Direction | Iterable

    def __post_init__(self):
        if not isinstance(self.pnt, Point) and isinstance(self.pnt, Iterable):
            self.pnt = Point(*self.pnt)
        if not isinstance(self.dir, Direction) and isinstance(self.dir, Iterable):
            self.dir = Direction(*self.dir)


@dataclass(slots=True)
class ArcLine:
    """
    IFC4x3 https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3/HTML/lexical/IfcArcIndex.htm

    """

    start: Point | Iterable
    midpoint: Point | Iterable
    end: Point | Iterable

    def __post_init__(self):
        if not isinstance(self.start, Point) and isinstance(self.start, Iterable):
            self.start = Point(*self.start)
        if not isinstance(self.midpoint, Point) and isinstance(self.midpoint, Iterable):
            self.midpoint = Point(*self.midpoint)
        if not isinstance(self.end, Point) and isinstance(self.end, Iterable):
            self.end = Point(*self.end)

        dim = self.start.dim
        if dim != self.end.dim or dim != self.midpoint.dim:
            raise ValueError("Start and end points must have the same dimension")

    @property
    def dim(self):
        return self.start.dim

    def __iter__(self):
        return iter((self.start, self.midpoint, self.end))


@dataclass(slots=True)
class PolyLine:
    points: list[Point]


@dataclass(slots=True)
class TrimmedCurve:
    """
    IFC4x3 (https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3/HTML/lexical/IfcTrimmedCurve.htm)
    STEP AP242 (https://www.steptools.com/stds/stp_aim/html/t_trimmed_curve.html)

    A bounded segment of an (unbounded) basis curve — line / circle / ellipse. Each trim is
    either a Cartesian ``Point`` lying on the curve or a parameter value (float);
    ``master_representation`` records which the producer considered authoritative
    ("PARAMETER", "CARTESIAN" or "UNSPECIFIED").
    """

    basis_curve: "Line | Circle | Ellipse | BSplineCurveWithKnots"
    trim1: "Point | float"
    trim2: "Point | float"
    sense_agreement: bool = True
    master_representation: str = "PARAMETER"


@dataclass(slots=True)
class CompositeCurveSegment:
    """
    IFC4x3 (https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3/HTML/lexical/IfcCompositeCurveSegment.htm)

    One segment of a CompositeCurve, wrapping a bounded parent curve.
    """

    parent_curve: "CURVE_GEOM_TYPES"
    same_sense: bool = True
    transition: str = "CONTINUOUS"


@dataclass(slots=True)
class CompositeCurve:
    """
    IFC4x3 (https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3/HTML/lexical/IfcCompositeCurve.htm)
    STEP AP242 (https://www.steptools.com/stds/stp_aim/html/t_composite_curve.html)

    A curve assembled from an ordered list of bounded parent-curve segments.
    """

    segments: list[CompositeCurveSegment]
    self_intersect: bool = False


@dataclass(slots=True)
class Clothoid:
    """
    IFC4x3 (https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3/HTML/lexical/IfcClothoid.htm)

    Euler spiral — curvature varies linearly with arc length. Evaluated about its 2D placement
    (``location`` + unit ``ref_direction``): with A = ``clothoid_constant`` (signed) and the
    normalized Fresnel integrals C/S, x(u) = |A|*sqrt(pi)*C(u/(|A|*sqrt(pi))) and
    y(u) = sign(A)*|A|*sqrt(pi)*S(u/(|A|*sqrt(pi))). The sign of A selects the turning sense.
    """

    location: Iterable  # 2D placement origin (the clothoid's inflection point)
    ref_direction: Iterable  # 2D unit tangent at u=0
    clothoid_constant: float


@dataclass(slots=True)
class CosineSpiral:
    """
    IFC4x3 (https://standards.buildingsmart.org/IFC/RELEASE/IFC4x3/HTML/lexical/IfcCosineSpiral.htm)

    A transition spiral whose curvature varies as a cosine of arc length. With A1 =
    ``cosine_term``, A0 = ``constant_term`` (optional) and L the length over which the cosine
    completes half a period (the containing CurveSegment's length), the heading angle is
    ``theta(s) = s/A0 + (L/(pi*A1))*sin(pi*s/L)`` and the curvature ``kappa(s) = 1/A0 +
    (1/A1)*cos(pi*s/L)``. Position is obtained by integrating ``(cos theta, sin theta)`` (no
    closed form) about the 2D placement (``location`` + unit ``ref_direction``).
    """

    location: Iterable  # 2D placement origin
    ref_direction: Iterable  # 2D unit tangent at s=0
    cosine_term: float  # A1 (required)
    constant_term: float | None = None  # A0 (optional)


@dataclass(slots=True)
class CurveSegment:
    """
    IFC4x3 (https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3/HTML/lexical/IfcCurveSegment.htm)

    A ``parent_curve`` restricted to the arc-length range [segment_start, segment_start +
    segment_length] and positioned in the containing curve by a placement (``location`` + unit
    ``ref_direction``). Distinct from CompositeCurveSegment (no SameSense; carries its own
    placement + parametric range). segment_length may be negative (parameter decreases).

    ``location``/``ref_direction`` are 2D for the planar (horizontal/vertical) curves, but 3D for
    the cant segments of an IfcSegmentedReferenceCurve (IfcAxis2Placement3D) — the extra vertical
    component of ``location`` is the superelevation offset baked onto the base curve. Planar
    consumers slice ``location[:2]``, so the 3D form is backward-compatible.
    """

    transition: str
    location: Iterable  # placement origin (2D planar, 3D for cant segments)
    ref_direction: Iterable  # unit tangent at the segment start (2D planar, 3D for cant)
    segment_start: float
    segment_length: float
    parent_curve: CURVE_GEOM_TYPES


@dataclass(slots=True)
class GradientCurve:
    """
    IFC4x3 (https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3/HTML/lexical/IfcGradientCurve.htm)

    A 3D directrix composing a horizontal ``base_curve`` (a CompositeCurve in x,y over arc length s)
    with a vertical gradient given by ``segments`` mapping s -> height z.
    """

    base_curve: "CompositeCurve"
    segments: list["CurveSegment"]
    self_intersect: bool = False


@dataclass(slots=True)
class SegmentedReferenceCurve:
    """
    IFC4x3 (https://standards.buildingsmart.org/IFC/RELEASE/IFC4x3/HTML/lexical/IfcSegmentedReferenceCurve.htm)

    A 3D curve defined in the linear parameter space of its ``base_curve`` (typically an
    IfcGradientCurve giving x,y,z along arc length). Its ``segments`` carry the cant
    (superelevation): each maps a parameter range of the base curve to a vertical offset applied
    perpendicular to the base curve axis, producing the rail reference curve. The horizontal
    (x,y) is that of the base curve unchanged; only the vertical (z) is displaced by the cant.
    """

    base_curve: "GradientCurve"
    segments: list["CurveSegment"]
    self_intersect: bool = False


@dataclass(slots=True)
class IndexedPolyCurve:
    """
    IFC4x3 (https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3/HTML/lexical/IfcIndexedPolyCurve.htm)
    STEP (not found direct equivalent, but can be represented by using 'B_SPLINE_CURVE' and 'POLYLINE' entities)
    """

    segments: list[Edge | ArcLine]
    self_intersect: bool = False

    def get_points(self):
        points = []
        for i, p in enumerate(self.segments):
            if i == 0:
                points.append(p.start.tolist())
                points.append(p.end.tolist())
            else:
                points.append(p.end.tolist())

        return points

    def get_unique_points_and_segment_indices(self) -> tuple[np.ndarray, list[list[int]]]:
        points = list(chain.from_iterable([list(segment) for segment in self.segments]))
        points_tuple = [tuple(x) for x in chain.from_iterable([list(segment) for segment in self.segments])]
        unique_pts, pts_index = np.unique(points, axis=0, return_index=False, return_inverse=True)
        indices = [[int(pts_index[points_tuple.index(tuple(s))]) + 1 for s in segment] for segment in self.segments]

        return unique_pts, indices

    def to_points2d(self):
        local_points = []
        segments_in = self.segments
        segments = segments_in[1:]
        for i, seg in enumerate(segments):
            if i == 0:
                pseg = segments[-1]
            else:
                pseg = segments[i - 1]

            if i == len(segments) - 1:
                nseg = segments[0]
            else:
                nseg = segments[i + 1]

            if isinstance(seg, Edge):
                if i == 0:
                    local_points.append(seg.start)
                else:
                    if type(segments[i - 1]) is Edge:
                        local_points.append(seg.start)
                if i < len(segments) - 1:
                    if type(segments[i + 1]) is Edge:
                        local_points.append(seg.end)
                else:
                    local_points.append(seg.end)
            else:
                center, radius = calc_arc_radius_center_from_3points(seg.start, seg.midpoint, seg.end)
                v1_ = seg.start - pseg.start
                v2_ = nseg.end - seg.end
                # ed = np.cross(v1_, v2_)
                # if ed < 0:
                #     local_points.append(seg.start)

                s, t = intersect_calc(seg.start, nseg.end, v1_, v2_)
                ip = seg.start + s * v1_
                # ip = intersection_point(v1_, v2_)
                local_points.append((ip[0], ip[1], radius))

        return local_points


@dataclass(slots=True)
class GeometricCurveSet:
    elements: list[CURVE_GEOM_TYPES]


@dataclass(slots=True)
class Circle:
    """
    IFC4x3 https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3/HTML/lexical/IfcCircle.htm
    STEP AP242 https://www.steptools.com/stds/stp_aim/html/t_circle.html
    """

    position: Axis2Placement3D
    radius: float


@dataclass(slots=True)
class Ellipse:
    """
    IFC4x3 https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3/HTML/lexical/IfcEllipse.htm
    STEP AP242 https://www.steptools.com/stds/stp_aim/html/t_ellipse.html
    """

    position: Axis2Placement3D
    semi_axis1: float
    semi_axis2: float


@dataclass(slots=True)
class Parabola:
    """STEP AP242 https://www.steptools.com/stds/stp_aim/html/t_parabola.html

    A conic: ``focal_dist`` is the distance from vertex to focus. (No IFC equivalent.)
    """

    position: Axis2Placement3D
    focal_dist: float


@dataclass(slots=True)
class PointOnCurve:
    """STEP AP242 t_point_on_curve — a point located at ``parameter`` on a basis curve."""

    basis_curve: "CURVE_GEOM_TYPES"
    parameter: float


@dataclass(slots=True)
class OffsetCurve3D:
    """STEP AP242 t_offset_curve_3d — a curve offset from a basis curve by ``distance``."""

    basis_curve: "CURVE_GEOM_TYPES"
    distance: float
    self_intersect: bool = False
    ref_direction: "Direction | None" = None


@dataclass(slots=True)
class Hyperbola:
    """STEP AP242 https://www.steptools.com/stds/stp_aim/html/t_hyperbola.html

    A conic with real (``semi_axis``) and imaginary (``semi_imag_axis``) semi-axes.
    """

    position: Axis2Placement3D
    semi_axis: float
    semi_imag_axis: float


class BSplineCurveFormEnum(Enum):
    """
    IFC4x3 (https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3/HTML/lexical/IfcBSplineCurveForm.htm)
    STEP (https://www.steptools.com/stds/stp_aim/html/t_b_spline_curve_form.html)
    """

    POLYLINE_FORM = "POLYLINE_FORM"
    CIRCULAR_ARC = "CIRCULAR_ARC"
    ELLIPTIC_ARC = "ELLIPTIC_ARC"
    HYPERBOLIC_ARC = "HYPERBOLIC_ARC"
    PARABOLIC_ARC = "PARABOLIC_ARC"
    UNSPECIFIED = "UNSPECIFIED"


class KnotType(Enum):
    """
    IFC4x3 (https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3/HTML/lexical/IfcKnotType.htm)
    STEP (https://www.steptools.com/stds/stp_aim/html/t_knot_type.html)
    """

    PIECEWISE_BEZIER_KNOTS = "PIECEWISE_BEZIER_KNOTS"
    QUASI_UNIFORM_KNOTS = "QUASI_UNIFORM_KNOTS"
    UNIFORM_KNOTS = "UNIFORM_KNOTS"
    UNSPECIFIED = "UNSPECIFIED"

    @staticmethod
    def from_str(value: str) -> KnotType:
        return KnotType(value)


@dataclass(slots=True)
class BSplineCurveWithKnots:
    """
    IFC4x3 (https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3/HTML/lexical/IfcBSplineCurveWithKnots.htm)
    STEP (https://www.steptools.com/stds/stp_aim/html/t_b_spline_curve_with_knots.html)
    """

    degree: int
    control_points_list: list[Point] | list[tuple]
    curve_form: BSplineCurveFormEnum
    closed_curve: bool
    self_intersect: bool
    knot_multiplicities: list[int]
    knots: list[float]
    knot_spec: KnotType


@dataclass(slots=True)
class PCurve:
    """
    IFC4x3 (https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3/HTML/lexical/IfcPcurve.htm)
    """

    basis_surface: SURFACE_GEOM_TYPES
    reference_curve: CURVE_GEOM_TYPES


@dataclass(slots=True)
class SurfaceCurve:
    """A curve that lies on one or two surfaces — a curve-on-surface.

    IFC4x3 (https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3/HTML/lexical/IfcSurfaceCurve.htm)
    STEP AP242 (https://www.steptools.com/stds/stp_aim/html/t_surface_curve.html)
    ACIS ``surfintcur`` (a surface-surface intersection curve).

    ``curve_3d`` is the exact 3D B-spline; ``associated_pcurves`` are its images in
    the parameter space of the surfaces it lies on — slot ``i`` aligns with ACIS
    pcurve index ``i + 1`` (``None`` where the surface needs none, e.g. a plane).
    The surfaces themselves are not duplicated here: they live on the faces the
    curve bounds. Carrying the pcurves with the curve is what lets a coedge whose
    SAT ``pcurve`` record is a *reference* (``±n $intcurve``) be reconstructed —
    without them a spline face's boundary has no UV image and ACIS rejects it
    ("coedge on spline surface has no PCURVE").
    """

    curve_3d: CURVE_GEOM_TYPES
    associated_pcurves: list[Pcurve2dBSpline | None] = field(default_factory=list)


@dataclass(slots=True)
class RationalBSplineCurveWithKnots(BSplineCurveWithKnots):
    """
    IFC4x3 (https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3/HTML/lexical/IfcRationalBSplineCurveWithKnots.htm)
    """

    weights_data: list[float]


@dataclass(slots=True)
class Edge:
    """
    IFC4x3 (https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3/HTML/lexical/IfcEdge.htm)
    STEP (https://www.steptools.com/stds/stp_aim/html/t_edge.html)
    """

    start: Point
    end: Point

    def __post_init__(self):
        # Already-built Points are the common case (the NGEOM hydrate passes Points
        # straight in); a Point IS Iterable, so guard on Point first to avoid
        # re-constructing millions of them through the interning cache (hot in the
        # streaming STEP→IFC/XML export of B-rep-heavy assemblies).
        if not isinstance(self.start, Point) and isinstance(self.start, Iterable):
            self.start = Point(*self.start)
        if not isinstance(self.end, Point) and isinstance(self.end, Iterable):
            self.end = Point(*self.end)

        dim = self.start.dim
        if dim != self.end.dim:
            raise ValueError("Start and end points must have the same dimension")

    def reversed(self):
        return Edge(self.end, self.start)

    @property
    def dim(self):
        return self.start.dim

    def to_line(self) -> Line:
        return Line(self.start, Direction(self.end - self.start))

    def __iter__(self):
        return iter((self.start, self.end))


@dataclass(slots=True)
class Pcurve2dBSpline:
    """A 2D B-spline curve in the parameter space (UV) of a surface — the
    p-curve attached to a coedge in ACIS / STEP / IFC. Carrying the
    pcurve as authored upstream avoids the lossy "sample 3D points,
    project back to UV" reprojection that breaks on degenerate or
    near-singular surface parameterizations.

    Control points are 2D ``[u, v]`` pairs. Optional ``weights`` makes
    the curve rational; ``None`` means non-rational.

    ``fit_tolerance`` is how closely the curve approximates the true
    curve-on-surface, as the author measured it (ACIS ``exppc`` carries
    it; SAT v4.0 ch.5 calls it "Fit tolerance"). ``0.0`` asserts the
    pcurve is exact, so it is a claim rather than a neutral default —
    keep the authored value when there is one instead of re-asserting
    exactness a reprojection cannot support.

    ``same_sense`` is the ACIS ``pcurve`` record's own forward/reversed
    flag: whether this 2D curve runs along its edge's 3D curve or
    against it. It is authored data and not derivable from the rest —
    in a Genie export it splits 13722/5184 with no correlation to the
    knots — so a reader that drops it leaves the writer defaulting to
    forward, and ACIS then rejects the face with "pcurve's range doesn't
    include coedge's range" on every one it got wrong.
    """

    degree: int
    control_points_2d: list[tuple[float, float]] | list[list[float]]
    knots: list[float]
    knot_multiplicities: list[int]
    weights: list[float] | None = None
    closed: bool = False
    fit_tolerance: float = 0.0
    same_sense: bool = True


@dataclass(slots=True)
class OrientedEdge(Edge):
    """
    IFC4x3 (https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3/HTML/lexical/IfcOrientedEdge.htm)
    STEP (https://www.steptools.com/stds/stp_aim/html/t_oriented_edge.html)

    ``pcurve`` is the optional UV-space curve on the parent face's
    surface (one per coedge in ACIS). When present, downstream OCCT
    construction skips reprojection and attaches the curve directly via
    ``BRep_Builder.UpdateEdge``.

    ``t_start`` / ``t_end`` are the underlying curve's parameter values
    at the edge's start and end vertices. SAT records them explicitly
    on every edge. Without them, ``BRepBuilderAPI_MakeEdge(curve, p1,
    p2)`` recovers parameters from 3D points — ambiguous for closed
    curves (a circle has two arcs between any two non-coincident
    points) and for self-intersecting BSplines, so OCC may pick the
    *long* arc instead of the SAT-intended short one. Threading the
    parameters lets the OCC builder use the explicit-parameter
    overload and trim correctly.
    """

    edge_element: Edge | EdgeCurve
    orientation: bool
    pcurve: Pcurve2dBSpline | None = None
    t_start: float | None = None
    t_end: float | None = None


@dataclass(slots=True)
class EdgeCurve(Edge):
    """
    IFC4x3 (https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3/HTML/lexical/IfcEdgeCurve.htm)
    STEP (https://www.steptools.com/stds/stp_aim/html/t_edge_curve.html)
    """

    edge_geometry: CURVE_GEOM_TYPES
    same_sense: bool


@dataclass(slots=True)
class PolyLoop:
    """
    IFC4x3 (https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3/HTML/lexical/IfcPolyLoop.htm)
    """

    polygon: list[Point]


@dataclass(slots=True)
class EdgeLoop:
    """
    IFC4x3 (https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3/HTML/lexical/IfcEdgeLoop.htm)
    """

    edge_list: list[OrientedEdge]


# Concrete tuple of bare-curve geometry classes (CURVE_GEOM_TYPES is a Union of forward-ref
# strings, so it can't be used with isinstance). Used to detect a Geometry that carries a curve
# rather than a surface/solid — e.g. a sectionless SAT wire body that must render as glTF line
# geometry. Includes Edge (a bare topological edge) which CURVE_GEOM_TYPES omits.
CURVE_GEOM_TUPLE = (
    Line,
    ArcLine,
    Circle,
    Ellipse,
    Parabola,
    Hyperbola,
    BSplineCurveWithKnots,
    RationalBSplineCurveWithKnots,
    IndexedPolyCurve,
    PolyLine,
    TrimmedCurve,
    CompositeCurve,
    Edge,
    # Loose curve collection (STEP GEOMETRIC_CURVE_SET wireframe bodies) — a
    # curve body like its members, so every curve-body path treats it as one.
    GeometricCurveSet,
    # NB: the analytic alignment types (Clothoid / CosineSpiral / GradientCurve /
    # SegmentedReferenceCurve) are intentionally NOT here — they are intermediate curves consumed
    # by the alignment evaluator and never stored as a Shape's geometry (the reader converts them
    # to a sampled PolyLine, which IS a curve body). Listing them would misclassify a swept
    # solid's GradientCurve directrix as a bare wire body.
)
