"""The verbs that BUILD geometry must build the same geometry on both kernels.

``test_backend_parity`` next door asks the two backends the same MEASUREMENT questions
— area, volume, centre of mass, which faces and edges a shape has — and compares the
answers. This module asks the other half of the abstraction's promise, the half a
consumer porting off pythonocc actually leans on: ``polygon_face`` to turn an outline
into a face, ``boolean`` to punch an opening into that FACE (not the solid-solid cut
the sibling covers), ``wires`` and ``wire_points`` to read the result back as an
ordered outer loop plus one loop per hole, ``section_with_plane`` to slice a solid,
``extrude_face_along_normal`` to give a plate its thickness, ``loft_profiles`` to
thread a solid through its sections, and ``PrimBox`` for a corner-to-corner box. Those
are the verbs whose disagreement a golden-file suite reports only as "the output
changed", never as which kernel moved — so each is pinned twice here, the same two
layers the sibling module uses: against a closed-form answer under whichever kernel is
installed (the ``backend`` fixture), and against the other kernel where both are (the
``both_backends`` fixture, which skips until an environment carries both). ORDER is
part of what is compared, not only shape: a caller rebuilding an outline from
``wire_points`` gets a different polygon if the walk starts elsewhere or runs the other
way round, so the loops are compared as sequences, and the winding that separates a
boundary from a hole is checked by sign rather than assumed.

One case does NOT agree, and is carried as a strict xfail rather than a loosened
assertion: an extruded prism's edges. See ``EDGE_IDENTITY_DIVERGENCE``.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from ada.api.primitives.box import PrimBox
from ada.cad import CadBackendName, backend_available, select_backend
from ada.geom.booleans import BoolOpEnum

# --------------------------------------------------------------------------------
# Constructions, and the closed-form answers they are checked against. Every number
# below is derived from the input polygon, never read off a kernel and frozen.
# --------------------------------------------------------------------------------

# A 4x4 square in the z=0 plane, wound counter-clockwise about +Z.
SQUARE = ((-2.0, -2.0, 0.0), (2.0, -2.0, 0.0), (2.0, 2.0, 0.0), (-2.0, 2.0, 0.0))
SQUARE_AREA = 4.0 * 4.0

# An L: a 3x1 leg plus a 1x1 block on top of its left end. Concave, so it catches a
# backend that quietly convexifies an outline — the area would come out 6, not 4.
L_SHAPE = ((0.0, 0.0, 0.0), (3.0, 0.0, 0.0), (3.0, 1.0, 0.0), (1.0, 1.0, 0.0), (1.0, 2.0, 0.0), (0.0, 2.0, 0.0))
L_SHAPE_AREA = 3.0 * 1.0 + 1.0 * 1.0

# A 1 x sqrt(2) rectangle tilted 45 degrees about the y axis, so its normal is not an
# axis and "area" is not just the shadow the polygon casts on z=0.
TILTED = ((0.0, 0.0, 0.0), (1.0, 0.0, 1.0), (1.0, 1.0, 1.0), (0.0, 1.0, 0.0))
TILTED_AREA = 1.0 * math.sqrt(2.0)

POLYGONS = {
    "square": (SQUARE, SQUARE_AREA),
    "l_shape": (L_SHAPE, L_SHAPE_AREA),
    "tilted": (TILTED, TILTED_AREA),
}

# The punch: a 1x1 column tall enough to pierce the z=0 plane from either side. Centred,
# so it takes a closed hole out of the middle of SQUARE; shifted by half the square's
# half-width it straddles the +x edge instead and takes a notch out of the boundary.
PUNCH_SIDE, PUNCH_HEIGHT = 1.0, 10.0
HOLE = ((-0.5, -0.5, 0.0), (0.5, -0.5, 0.0), (0.5, 0.5, 0.0), (-0.5, 0.5, 0.0))
HOLED_AREA = SQUARE_AREA - PUNCH_SIDE * PUNCH_SIDE

NOTCH_OFFSET = 2.0  # the punch's centre lands on the square's +x edge
NOTCHED_AREA = SQUARE_AREA - (PUNCH_SIDE / 2) * PUNCH_SIDE
# The square's boundary with the punch's left half let into the +x edge.
NOTCHED_OUTLINE = (
    (-2.0, -2.0, 0.0),
    (2.0, -2.0, 0.0),
    (2.0, -0.5, 0.0),
    (1.5, -0.5, 0.0),
    (1.5, 0.5, 0.0),
    (2.0, 0.5, 0.0),
    (2.0, 2.0, 0.0),
    (-2.0, 2.0, 0.0),
)

EXTRUDE_T = 0.5

# Sectioning: a 2x3x4 box cut at z=1 gives its 2x3 footprint; cut through the origin on
# a plane normal to (1,1,0) gives the diagonal of a 2x2x2 box, sqrt(2) times as wide.
SECTION_BOX = (2.0, 3.0, 4.0)
SECTION_Z = 1.0
SECTION_AREA = SECTION_BOX[0] * SECTION_BOX[1]
SECTION_RECT = (
    (-1.0, -1.5, SECTION_Z),
    (1.0, -1.5, SECTION_Z),
    (1.0, 1.5, SECTION_Z),
    (-1.0, 1.5, SECTION_Z),
)
OBLIQUE_BOX = (2.0, 2.0, 2.0)
OBLIQUE_AREA = math.sqrt(2.0) * OBLIQUE_BOX[0] * OBLIQUE_BOX[2]
OBLIQUE_RECT = ((-1.0, 1.0, -1.0), (-1.0, 1.0, 1.0), (1.0, -1.0, 1.0), (1.0, -1.0, -1.0))

# A 2x2 square lofted to a 1x1 square one unit up: a pyramidal frustum, whose volume is
# h/3 * (A1 + A2 + sqrt(A1*A2)).
LOFT_BOTTOM = ((-1.0, -1.0, 0.0), (1.0, -1.0, 0.0), (1.0, 1.0, 0.0), (-1.0, 1.0, 0.0))
LOFT_TOP = ((-0.5, -0.5, 1.0), (0.5, -0.5, 1.0), (0.5, 0.5, 1.0), (-0.5, 0.5, 1.0))
LOFT_HEIGHT = 1.0
LOFT_A1, LOFT_A2 = 2.0 * 2.0, 1.0 * 1.0
LOFT_VOLUME = LOFT_HEIGHT / 3.0 * (LOFT_A1 + LOFT_A2 + math.sqrt(LOFT_A1 * LOFT_A2))

# Corner-to-corner box. PrimBox is the helper for this — no second one is needed beside
# make_box, which is centred on the origin and takes extents rather than corners.
BOX_P1, BOX_P2 = (1.0, 2.0, 3.0), (4.0, 6.0, 9.0)
BOX_P_VOLUME = (BOX_P2[0] - BOX_P1[0]) * (BOX_P2[1] - BOX_P1[1]) * (BOX_P2[2] - BOX_P1[2])

# An OPEN polyline, three points and two edges — the case where wire_points and
# vertex_points part company (see test_wire_points_reports_one_point_per_edge).
POLYLINE = ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0))

# A prism's edges: four round the base, four round the top, one up each corner. With a
# hole in the profile the bore contributes the same three groups again.
EXTRUDED_SQUARE_EDGES = 4 + 4 + 4
EXTRUDED_HOLED_EDGES = 2 * EXTRUDED_SQUARE_EDGES

# The one place the two kernels part company here — see the two tests below, which
# carry it as a strict xfail rather than as a loosened assertion.
EDGE_IDENTITY_DIVERGENCE = (
    "adacpp's face_id ignores a sub-shape's Location, so a prism's base and its top — "
    "one TShape sitting at two places — come back with the SAME identity. "
    "AdacppBackend.edges() de-duplicates on that identity, so every located copy is "
    "dropped: an extruded square reports 8 edges where it has 12, and an extruded face "
    "with a hole 16 where it has 24. OccBackend.face_id hashes (TShape, Location) and "
    "keeps them apart. make_box is unaffected, which is why the sibling module's "
    "twelve-edge box passes on both."
)


def _face(be, points):
    return be.polygon_face([list(p) for p in points])


def _square_face(be):
    return _face(be, SQUARE)


def _l_face(be):
    return _face(be, L_SHAPE)


def _tilted_face(be):
    return _face(be, TILTED)


def _punch(be, dx: float = 0.0):
    column = be.make_box(PUNCH_SIDE, PUNCH_SIDE, PUNCH_HEIGHT)
    if dx == 0.0:
        return column
    matrix = np.eye(4)
    matrix[0, 3] = dx
    return be.transform(column, matrix, True)


def _holed_face(be):
    return be.boolean(BoolOpEnum.DIFFERENCE, _square_face(be), _punch(be))


def _notched_face(be):
    return be.boolean(BoolOpEnum.DIFFERENCE, _square_face(be), _punch(be, NOTCH_OFFSET))


def _extruded_square(be):
    return be.extrude_face_along_normal(_square_face(be), EXTRUDE_T)


def _extruded_holed_face(be):
    return be.extrude_face_along_normal(be.faces(_holed_face(be))[0], EXTRUDE_T)


def _section(be):
    box = be.make_box(*SECTION_BOX)
    return be.section_with_plane(box, (0.0, 0.0, SECTION_Z), (0.0, 0.0, 1.0), 100.0)


def _oblique_section(be):
    box = be.make_box(*OBLIQUE_BOX)
    return be.section_with_plane(box, (0.0, 0.0, 0.0), (1.0, 1.0, 0.0), 100.0)


def _loft(be):
    return be.loft_profiles([[list(p) for p in LOFT_BOTTOM], [list(p) for p in LOFT_TOP]], True, True)


def _prim_box(be):
    # PrimBox.solid_occ() is this, plus a lookup of the process-wide active_backend().
    # Going through solid_geom() keeps the kernel pinned, which is the whole point of
    # these modules; the cache in ada.cad.shape_cache is keyed by backend name, so the
    # two routes build the same body.
    return be.build(PrimBox("parity_box", BOX_P1, BOX_P2).solid_geom())


def _polyline_wire(be):
    return be.make_wire([list(p) for p in POLYLINE])


# Every construction is a callable rather than a shape, for the reason the sibling
# module gives: a shape belongs to the kernel that built it.
CONSTRUCTIONS = {
    "polygon_face_square": _square_face,
    "polygon_face_l_shape": _l_face,
    "polygon_face_tilted": _tilted_face,
    "face_with_a_hole": _holed_face,
    "face_with_a_notch": _notched_face,
    "extruded_square": _extruded_square,
    "extruded_holed_face": _extruded_holed_face,
    "section_axis_aligned": _section,
    "section_oblique": _oblique_section,
    "loft_frustum": _loft,
    "prim_box": _prim_box,
    "open_polyline_wire": _polyline_wire,
}


# --------------------------------------------------------------------------------
# Comparison helpers. Loops are compared as SEQUENCES — that is the parity claim the
# measurement module cannot make, because a multiset of points is the same polygon
# whatever order it is in.
# --------------------------------------------------------------------------------


def _rounded(points, ndigits: int = 9) -> tuple:
    """Points as plain rounded tuples. ``+ 0.0`` folds -0.0 onto 0.0, which otherwise
    compares equal but prints differently and sorts apart."""
    return tuple(tuple(round(float(c), ndigits) + 0.0 for c in p) for p in points)


def _canonical_loop(points, ndigits: int = 9) -> tuple:
    """The same closed polygon written one way — invariant under where the walk starts
    and which way round it runs, and under nothing else. A cyclic shift or a reversal
    is the same loop; a reordering is a different polygon and must stay different."""
    pts = list(_rounded(points, ndigits))
    if len(pts) > 1 and pts[0] == pts[-1]:
        pts = pts[:-1]
    candidates = (pts, list(reversed(pts)))
    return min(tuple(seq[i:] + seq[:i]) for seq in candidates for i in range(len(seq)))


def _signed_area(points, normal) -> float:
    """Signed area of a planar loop about ``normal`` — positive counter-clockwise.

    The sign, not the magnitude, is the point: it is what distinguishes a face's outer
    boundary from the hole inside it once both are just lists of points.
    """
    pts = [np.asarray([float(c) for c in p]) for p in points]
    axis = np.asarray([float(c) for c in normal], dtype=float)
    axis = axis / np.linalg.norm(axis)
    cross_sum = np.zeros(3)
    for a, b in zip(pts, pts[1:] + pts[:1]):
        cross_sum = cross_sum + np.cross(a, b)
    return float(np.dot(cross_sum, axis)) / 2.0


def _loops(be, shape) -> list:
    """Every boundary loop of ``shape``, in order: per face, outer wire first and then
    one loop per hole. A bare wire is its own single loop."""
    faces = be.faces(shape)
    if not faces:
        return [[_rounded(be.wire_points(w)) for w in be.wires(shape)]]
    return [[_rounded(be.wire_points(w)) for w in be.wires(f)] for f in faces]


def _face_planes(be, shape) -> list:
    """Each face's ``(origin, normal)``, or ``None`` where the face is not planar.

    Face order is not part of either backend's contract, so this is a multiset — the
    loops above already carry the per-face ordering claim. Sorted by ``repr`` because
    the list mixes point tuples with ``None`` and only needs a total order, not a
    meaningful one.
    """
    planes = []
    for face in be.faces(shape):
        plane = be.face_plane(face)
        planes.append(None if plane is None else _rounded([(*plane[0], *plane[1])])[0])
    return sorted(planes, key=repr)


def _fingerprint(be, shape) -> dict:
    """Everything about a built shape that both kernels are claimed to agree on."""
    return {
        "shape_type": be.shape_type(shape),
        "valid": be.is_valid(shape),
        "counts": {
            "solids": len(be.solids(shape)),
            "faces": len(be.faces(shape)),
            "wires": len(be.wires(shape)),
            "edges": len(be.edges(shape)),
        },
        "area": round(be.area(shape), 9),
        "volume": round(be.volume(shape), 9),
        "loops": _loops(be, shape),
        "planes": _face_planes(be, shape),
    }


# --------------------------------------------------------------------------------
# Single-backend invariants: closed-form answers, checked on whichever kernel is here.
# --------------------------------------------------------------------------------


@pytest.mark.parametrize("polygon", sorted(POLYGONS))
def test_polygon_face_is_the_polygon_it_was_given(backend, polygon):
    """A face built from an outline has that outline's area and that outline's loop."""
    points, area = POLYGONS[polygon]
    face = _face(backend, points)

    assert backend.shape_type(face) == "face"
    assert backend.is_planar_face(face)
    assert backend.area(face) == pytest.approx(area, rel=1e-9)
    # A face encloses no volume, whatever it is worth as a boundary.
    assert backend.volume(face) == pytest.approx(0.0, abs=1e-12)
    assert len(backend.wires(face)) == 1
    assert _canonical_loop(backend.wire_points(face)) == _canonical_loop(points)


def test_wire_points_starts_where_the_caller_started(backend):
    """``wire_points`` is ordered, and the order is the caller's, not the kernel's.

    A consumer that rebuilds an outline from these points gets a different polygon if
    the walk starts at a different corner, so the start point and direction are part
    of the contract rather than an implementation detail to normalise away.
    """
    assert _rounded(backend.wire_points(_square_face(backend))) == _rounded(SQUARE)

    rotated = SQUARE[2:] + SQUARE[:2]
    assert _rounded(backend.wire_points(_face(backend, rotated))) == _rounded(rotated)

    reversed_square = tuple(reversed(SQUARE))
    assert _rounded(backend.wire_points(_face(backend, reversed_square))) == _rounded(reversed_square)


def test_polygon_winding_decides_which_way_the_face_looks(backend):
    """Reversing the outline flips the normal and leaves the area alone."""
    face, flipped = _square_face(backend), _face(backend, tuple(reversed(SQUARE)))

    _, normal = backend.face_plane(face)
    _, flipped_normal = backend.face_plane(flipped)
    assert [float(c) for c in normal] == pytest.approx([0.0, 0.0, 1.0], abs=1e-9)
    assert [float(c) for c in flipped_normal] == pytest.approx([0.0, 0.0, -1.0], abs=1e-9)
    assert backend.area(flipped) == pytest.approx(SQUARE_AREA, rel=1e-9)


def test_boolean_punches_a_hole_through_a_face(backend):
    """DIFFERENCE of a solid from a FACE — one face, two wires, and the area to match.

    The solid-solid cut is the sibling module's case. This is the one a plate with an
    opening goes through, and the thing that makes it usable is that the opening comes
    back as a SECOND wire on the same face rather than as a second face.
    """
    holed = _holed_face(backend)
    # Cutting a face leaves a compound of faces, not a solid: nothing here is closed.
    assert backend.shape_type(holed) == "compound"
    assert backend.solids(holed) == []

    faces = backend.faces(holed)
    assert len(faces) == 1
    assert backend.area(faces[0]) == pytest.approx(HOLED_AREA, rel=1e-9)

    wires = backend.wires(faces[0])
    assert len(wires) == 2
    outer, hole = (backend.wire_points(w) for w in wires)
    # The outer boundary comes first — ada.api.loft.iter_face_poly_loops reads the
    # loops in exactly this order and treats the rest as holes.
    assert _canonical_loop(outer) == _canonical_loop(SQUARE)
    assert _canonical_loop(hole) == _canonical_loop(HOLE)

    # And the hole runs the other way round the face normal, which is how a caller
    # holding two loops and no topology tells the opening from the boundary.
    _, normal = backend.face_plane(faces[0])
    assert _signed_area(outer, normal) == pytest.approx(SQUARE_AREA, rel=1e-9)
    assert _signed_area(hole, normal) == pytest.approx(-PUNCH_SIDE * PUNCH_SIDE, rel=1e-9)


def test_boolean_notches_a_face_at_its_edge(backend):
    """The same cut moved onto the boundary opens the outline instead of holing it.

    One wire, not two — the punch no longer has the square's edge all the way round
    it, so what it removes is a bite out of the boundary and the loop simply grows
    four points.
    """
    faces = backend.faces(_notched_face(backend))
    assert len(faces) == 1
    assert len(backend.wires(faces[0])) == 1
    assert backend.area(faces[0]) == pytest.approx(NOTCHED_AREA, rel=1e-9)
    assert _canonical_loop(backend.wire_points(faces[0])) == _canonical_loop(NOTCHED_OUTLINE)


@pytest.mark.parametrize("polygon", sorted(POLYGONS))
def test_extrude_face_along_normal_is_area_times_thickness(backend, polygon):
    """Thickening a plate: the volume is the profile's own area times the thickness.

    The tilted case is the one worth having — a backend extruding along +Z rather than
    along the face's normal gets the axis-aligned profiles right and this one wrong.
    """
    points, area = POLYGONS[polygon]
    solid = backend.extrude_face_along_normal(_face(backend, points), EXTRUDE_T)

    assert backend.shape_type(solid) == "solid"
    assert backend.is_valid(solid)
    assert backend.volume(solid) == pytest.approx(area * EXTRUDE_T, rel=1e-9)


def test_extruding_a_holed_face_keeps_the_hole(backend):
    """A plate with an opening thickens into a solid with a bore, not a solid slab."""
    solid = _extruded_holed_face(backend)
    assert backend.shape_type(solid) == "solid"
    assert backend.volume(solid) == pytest.approx(HOLED_AREA * EXTRUDE_T, rel=1e-9)
    # Two faces per side of the bore on top of the six a plain slab would have.
    assert len(backend.faces(solid)) == 6 + 4


# --------------------------------------------------------------------------------
# The divergence. Parametrised over the backend NAME rather than taking the fixture,
# so the xfail lands on the kernel that has it and the other side stays a real
# assertion — a strict xfail would otherwise be an XPASS wherever the bug is absent.
# --------------------------------------------------------------------------------

DIVERGING_BACKENDS = [
    "occ",
    pytest.param("adacpp", marks=pytest.mark.xfail(strict=True, reason=EDGE_IDENTITY_DIVERGENCE)),
]


def _pinned(name: str):
    if not backend_available(CadBackendName(name)):
        pytest.skip(f"{name} backend not installed")
    return select_backend(prefer=name)


@pytest.mark.parametrize("backend_name", DIVERGING_BACKENDS)
def test_extruded_prism_reports_every_edge(backend_name):
    """An extruded face is a prism, and a prism has a top as well as a bottom.

    The edge count is the topology answer a caller building a wire frame, an edge
    overlay or an export of the boundary depends on. The faces and the volume come
    back right on both kernels; it is only the ordered edge list that loses the top.
    """
    be = _pinned(backend_name)
    prism = be.extrude_face_along_normal(_square_face(be), EXTRUDE_T)
    holed_prism = _extruded_holed_face(be)

    # Not a symptom of a missing top face — that is there, and so are the vertices.
    assert len(be.faces(prism)) == 6
    assert len(be.vertex_points(prism)) == 8

    assert len(be.edges(prism)) == EXTRUDED_SQUARE_EDGES
    assert len(be.edges(holed_prism)) == EXTRUDED_HOLED_EDGES


@pytest.mark.parametrize("backend_name", DIVERGING_BACKENDS)
def test_face_id_separates_a_prisms_base_from_its_top(backend_name):
    """The root of it: identity that ignores Location is not identity.

    ``face_id`` is documented as an orientation-independent TOPOLOGICAL identity, which
    two sub-shapes at different places are not entitled to share. Everything that
    collapses on it — the edge de-duplication here, a cell graph matching shared faces —
    reads a located copy as the original.
    """
    be = _pinned(backend_name)
    prism = be.extrude_face_along_normal(_square_face(be), EXTRUDE_T)
    faces = be.faces(prism)

    assert len({be.face_id(f) for f in faces}) == len(faces)


def test_section_with_plane_cuts_the_footprint(backend):
    """Slicing a 2x3x4 box at z=1 gives its 2x3 footprint, as an ordered rectangle."""
    faces = backend.faces(_section(backend))
    assert len(faces) == 1
    assert backend.area(faces[0]) == pytest.approx(SECTION_AREA, rel=1e-9)
    assert _canonical_loop(backend.wire_points(faces[0])) == _canonical_loop(SECTION_RECT)

    origin, normal = backend.face_plane(faces[0])
    # The section lies in the cutting plane; the normal's sign is the face's own.
    assert [abs(float(c)) for c in normal] == pytest.approx([0.0, 0.0, 1.0], abs=1e-9)
    assert float(origin[2]) == pytest.approx(SECTION_Z, abs=1e-9)


def test_section_on_an_oblique_plane_is_the_diagonal(backend):
    """Off-axis, so the section is not simply a face of the box read back out."""
    faces = backend.faces(_oblique_section(backend))
    assert len(faces) == 1
    assert backend.area(faces[0]) == pytest.approx(OBLIQUE_AREA, rel=1e-9)
    assert _canonical_loop(backend.wire_points(faces[0])) == _canonical_loop(OBLIQUE_RECT)


def test_loft_is_the_frustum_between_its_profiles(backend):
    """Two squares, one above and smaller: the closed form is a pyramidal frustum.

    The sibling module lofts through ``ada.api.loft``; this goes straight at the
    backend verb, with a volume that follows from the two profiles rather than from
    whatever the first kernel to run happened to produce.
    """
    loft = _loft(backend)
    assert backend.shape_type(loft) == "solid"
    assert backend.is_valid(loft)
    assert backend.volume(loft) == pytest.approx(LOFT_VOLUME, rel=1e-9)
    # Two caps and one ruled wall per side.
    assert len(backend.faces(loft)) == 2 + 4


def test_prim_box_spans_the_two_corners_it_was_given(backend):
    """``PrimBox(p1, p2)`` is the corner-to-corner box; ``make_box`` takes extents."""
    solid = _prim_box(backend)
    assert backend.shape_type(solid) == "solid"
    assert backend.bbox(solid) == pytest.approx((*BOX_P1, *BOX_P2), abs=1e-9)
    assert backend.volume(solid) == pytest.approx(BOX_P_VOLUME, rel=1e-9)


def test_wire_points_reports_one_point_per_edge(backend):
    """On an OPEN wire the last vertex is not reported — one point per edge, no more.

    Both kernels do this, so it is a shared contract rather than drift: ``wire_points``
    walks edges and takes each one's start vertex, which closes a loop exactly but
    leaves an open polyline one point short. A caller rebuilding an open run has to
    reach for ``vertex_points`` instead. Pinned so that a fix landing on one kernel
    alone shows up as the divergence it would be.
    """
    wire = _polyline_wire(backend)
    assert backend.shape_type(wire) == "wire"
    assert len(backend.edges(wire)) == len(POLYLINE) - 1
    assert _rounded(backend.wire_points(wire)) == _rounded(POLYLINE[:-1])
    # Nothing is missing from the underlying wire — only from this ordered view of it.
    assert _rounded(backend.vertex_points(wire)) == _rounded(POLYLINE)
    # A closed loop has one edge per point, so it loses nothing.
    assert len(backend.wire_points(_square_face(backend))) == len(SQUARE)


# --------------------------------------------------------------------------------
# Cross-backend agreement. The actual parity claim — skipped until an environment
# carries both kernels (see the sibling module's docstring).
# --------------------------------------------------------------------------------


# The two constructions the edge-identity divergence reaches. Everything else in
# CONSTRUCTIONS agrees key for key; these two differ in the edge count alone, and the
# fingerprint says so rather than quietly leaving edges out of the comparison.
EXTRUSIONS = ("extruded_holed_face", "extruded_square")

CONSTRUCTION_PARAMS = [
    pytest.param(
        name,
        marks=[pytest.mark.xfail(strict=True, reason=EDGE_IDENTITY_DIVERGENCE)] if name in EXTRUSIONS else [],
    )
    for name in sorted(CONSTRUCTIONS)
]


@pytest.mark.parametrize("construction", CONSTRUCTION_PARAMS)
def test_constructions_agree(both_backends, construction):
    """The same recipe, built twice: same topology, same measurements, same loops."""
    occ, adacpp = both_backends
    build = CONSTRUCTIONS[construction]
    assert _fingerprint(adacpp, build(adacpp)) == _fingerprint(occ, build(occ))


@pytest.mark.parametrize("construction", sorted(CONSTRUCTIONS))
def test_loop_order_agrees(both_backends, construction):
    """Loops compared as sequences, not as sets.

    ``test_constructions_agree`` already covers this, but as one of many keys; a
    failure there says "the fingerprints differ" and leaves the reader to find where.
    This one fails on the thing a port actually breaks on — the two kernels handing
    back the same corners starting from different ones — and says so on its own.
    """
    occ, adacpp = both_backends
    build = CONSTRUCTIONS[construction]
    assert _loops(adacpp, build(adacpp)) == _loops(occ, build(occ))


def test_hole_winding_agrees(both_backends):
    """Both kernels must wind the hole against the boundary, not merely wind it."""
    occ, adacpp = both_backends

    def windings(be):
        face = be.faces(_holed_face(be))[0]
        _, normal = be.face_plane(face)
        return [round(_signed_area(be.wire_points(w), normal), 9) for w in be.wires(face)]

    assert windings(adacpp) == windings(occ)
