"""The two CAD backends must answer the same geometry questions the same way.

The abstraction's promise is that a caller never has to know which kernel is loaded.
That only holds if the two backends agree, and "they agree" is an assertion, not a
design property — nothing in the layout stops one of them from drifting. So every verb
covered here is exercised twice on the same construction and the two answers compared:

* shape exploration    -- faces / solids / shells / wires / edges / vertex points
* boolean cut          -- DIFFERENCE, and the topology and volume it leaves behind
* geometry diagnostics -- surface area, volume, centre of mass, on solids and on faces
* face queries         -- plane, surface type, planarity
* lofting              -- ``ada.api.loft`` end to end, which composes most of the above

Two layers, because no single environment is guaranteed to carry both kernels:

* the ``backend`` fixture parametrises over whichever kernels ARE installed and checks
  invariants that hold for each on its own (a box has twelve edges, a 2x3x4 box has
  volume 24). Those run, and can fail, in a single-backend environment.
* the ``both_backends`` fixture skips unless both import, and compares the two answers
  directly. That is the only check that can catch drift, so it needs an environment
  carrying both kernels to be worth anything — see the ``test-cad-parity`` task.

``select_backend`` tries adacpp before pythonocc, so merely having adacpp installed
moves the process onto it. Every backend here is therefore pinned explicitly; nothing
trusts the default, and nothing calls ``active_backend()``.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from ada.api.loft import iter_face_poly_loops, loft_profiles
from ada.cad import (
    AdacppBackend,
    CadBackendName,
    backend_available,
    select_backend,
    to_occ_shape,
)
from ada.geom.booleans import BoolOpEnum
from ada.geom.curves import PolyLoop
from ada.geom.points import Point

BACKEND_NAMES = ("occ", "adacpp")

# The cylinder is r=0.5, h=10 rising from z=0; the box spans z=-2..2, so exactly 2 of
# the cylinder's height is inside it. What DIFFERENCE removes is therefore an analytic
# quantity, not a number read off one backend and frozen.
BOX_DX, BOX_DY, BOX_DZ = 2.0, 3.0, 4.0
CYL_RADIUS, CYL_HEIGHT = 0.5, 10.0
BOX_VOLUME = BOX_DX * BOX_DY * BOX_DZ
BOX_AREA = 2 * (BOX_DX * BOX_DY + BOX_DX * BOX_DZ + BOX_DY * BOX_DZ)
CUT_VOLUME = BOX_VOLUME - math.pi * CYL_RADIUS**2 * (BOX_DZ / 2)

LOFT_PROFILES = (
    PolyLoop(polygon=[Point(-1.0, -1.0, 0.0), Point(1.0, -1.0, 0.0), Point(1.0, 1.0, 0.0), Point(-1.0, 1.0, 0.0)]),
    PolyLoop(polygon=[Point(-1.5, -1.5, 2.0), Point(1.5, -1.5, 2.0), Point(1.5, 1.5, 2.0), Point(-1.5, 1.5, 2.0)]),
    PolyLoop(polygon=[Point(-0.8, -0.8, 4.0), Point(0.8, -0.8, 4.0), Point(0.8, 0.8, 4.0), Point(-0.8, 0.8, 4.0)]),
)


def _make_box(be):
    return be.make_box(BOX_DX, BOX_DY, BOX_DZ)


def _make_cylinder(be):
    return be.make_cylinder(CYL_RADIUS, CYL_HEIGHT)


def _make_sphere(be):
    return be.make_sphere(1.0)


def _make_cut(be):
    return be.boolean(BoolOpEnum.DIFFERENCE, _make_box(be), _make_cylinder(be))


def _make_loft(be):
    return loft_profiles(LOFT_PROFILES, ruled=True, is_solid=True, backend=be)


# A unit square in z=0, and the same outline with an interior hole: the two profiles
# behind the prisms below. A prism is the shape that separates a placement-aware edge
# identity from a placement-blind one, because MakePrism builds its top face as the
# base face instanced at another Location.
_SQUARE = [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [1.0, 1.0, 0.0], [0.0, 1.0, 0.0]]
_HOLE = [[0.25, 0.25, 0.0], [0.75, 0.25, 0.0], [0.75, 0.75, 0.0], [0.25, 0.75, 0.0]]


def _make_holed_face(be):
    cut = be.boolean(
        BoolOpEnum.DIFFERENCE,
        be.polygon_face(_SQUARE),
        be.extrude_face_along_normal(be.polygon_face(_HOLE), -1.0),
    )
    holed = [f for f in be.faces(cut) if len(be.wires(f)) == 2]
    assert len(holed) == 1, "expected the cut to leave exactly one face with a hole"
    return holed[0]


def _make_prism(be):
    return be.extrude_face_along_normal(be.polygon_face(_SQUARE), 0.5)


def _make_holed_prism(be):
    return be.extrude_face_along_normal(_make_holed_face(be), 0.5)


# Every construction is a callable rather than a shape: a shape belongs to the kernel
# that built it and cannot be handed to the other one, so each backend must build its
# own copy from the same recipe.
CONSTRUCTIONS = {
    "box": _make_box,
    "cylinder": _make_cylinder,
    "sphere": _make_sphere,
    "box_cut_by_cylinder": _make_cut,
    "loft": _make_loft,
}


@pytest.fixture(params=BACKEND_NAMES)
def backend(request):
    """One installed backend, pinned by name — never the ``select_backend`` default."""
    if not backend_available(CadBackendName(request.param)):
        pytest.skip(f"{request.param} backend not installed")
    return select_backend(prefer=request.param)


@pytest.fixture
def both_backends():
    """``(occ, adacpp)``, or a skip when this environment carries only one kernel."""
    missing = [n for n in BACKEND_NAMES if not backend_available(CadBackendName(n))]
    if missing:
        pytest.skip(f"cross-backend comparison needs both kernels; missing: {', '.join(missing)}")
    return select_backend(prefer="occ"), select_backend(prefer="adacpp")


def _topology_counts(be, shape) -> dict[str, int]:
    return {
        "solids": len(be.solids(shape)),
        "shells": len(be.shells(shape)),
        "faces": len(be.faces(shape)),
        "wires": len(be.wires(shape)),
        "edges": len(be.edges(shape)),
        "vertices": len(be.vertex_points(shape)),
    }


def _diagnostics(be, shape) -> dict:
    return {
        "area": be.area(shape),
        "volume": be.volume(shape),
        "centroid": [float(c) for c in be.center_of_mass(shape)],
        # Face order is not part of either backend's contract, so compare the multiset.
        "face_areas": sorted(round(be.area(f), 9) for f in be.faces(shape)),
        "surface_types": sorted(be.face_surface_type(f) for f in be.faces(shape)),
    }


# --------------------------------------------------------------------------------
# Single-backend invariants. These run against whichever kernels are installed, so a
# one-backend environment still exercises them for real.
# --------------------------------------------------------------------------------


def test_box_topology_is_a_box(backend):
    assert _topology_counts(backend, _make_box(backend)) == {
        "solids": 1,
        "shells": 1,
        "faces": 6,
        "wires": 6,
        "edges": 12,
        "vertices": 8,
    }


@pytest.mark.parametrize(("construction", "expected_edges"), [("box", 12), ("cylinder", 3), ("sphere", 3)])
def test_every_edge_is_reported_once(backend, construction, expected_edges):
    """Edge iteration reports each edge once, not once per face that touches it.

    A box has 12 edges and 6 faces; walking it face by face and concatenating yields
    24 entries, because every edge borders two faces. Any caller counting edges, or
    building a wire frame, doubles its answer on a backend that leaks those incidences.
    """
    assert len(backend.edges(CONSTRUCTIONS[construction](backend))) == expected_edges


@pytest.mark.parametrize(
    ("construction", "expected_edges"),
    [(_make_holed_face, 8), (_make_prism, 12), (_make_holed_prism, 24)],
    ids=["holed_face", "prism", "holed_prism"],
)
def test_a_located_copy_is_its_own_edge(backend, construction, expected_edges):
    """An extrusion's top rail is its base rail at another placement, not the same edge.

    ``BRepPrimAPI_MakePrism`` instances the base face at the extrusion height rather
    than building fresh geometry, so base and top share a TShape and differ only in
    Location. Any identity that ignores Location merges them and reports 8 edges for
    a square prism instead of 12, 16 instead of 24 with a hole -- silently, and only
    on the shapes a wire frame, an edge overlay or a boundary export is built from.
    The holed face (8, no extrusion involved) is the control: it pins the count that
    a placement-blind identity gets right, so a failure here localises to the rails.
    """
    assert len(backend.edges(construction(backend))) == expected_edges


def test_box_diagnostics_match_the_closed_form(backend):
    box = _make_box(backend)
    assert backend.volume(box) == pytest.approx(BOX_VOLUME, rel=1e-9)
    assert backend.area(box) == pytest.approx(BOX_AREA, rel=1e-9)
    # make_box is centred on the origin under both backends.
    assert [float(c) for c in backend.center_of_mass(box)] == pytest.approx([0.0, 0.0, 0.0], abs=1e-9)


def test_face_diagnostics_are_surface_not_volume(backend):
    """area/volume/centroid must be well defined on a face, not only on a solid."""
    face = min(backend.faces(_make_box(backend)), key=backend.area)
    assert backend.area(face) == pytest.approx(BOX_DX * BOX_DY, rel=1e-9)
    assert backend.volume(face) == pytest.approx(0.0, abs=1e-12)
    centroid = [float(c) for c in backend.center_of_mass(face)]
    assert centroid[:2] == pytest.approx([0.0, 0.0], abs=1e-9)
    assert abs(centroid[2]) == pytest.approx(BOX_DZ / 2, rel=1e-9)


def test_boolean_cut_removes_the_analytic_volume(backend):
    cut = _make_cut(backend)
    assert backend.volume(cut) == pytest.approx(CUT_VOLUME, rel=1e-9)
    # The bore adds a cylindrical wall and splits the face it breaks through.
    assert "cylinder" in {backend.face_surface_type(f) for f in backend.faces(cut)}
    assert len(backend.faces(cut)) > len(backend.faces(_make_box(backend)))


def test_planar_face_queries(backend):
    """Every box face is planar, with an axis-aligned normal and an on-surface origin.

    The plane comes off the face's underlying surface, whose normal carries no face
    orientation, so the six normals are three axes twice over rather than six distinct
    directions — the sign is not the invariant here. Whether the two backends pick the
    SAME sign is a separate question, and ``test_face_planes_agree`` asks it.
    """
    extents = (BOX_DX, BOX_DY, BOX_DZ)
    faces = backend.faces(_make_box(backend))
    assert all(backend.is_planar_face(f) for f in faces)

    axes = []
    for face in faces:
        origin, normal = backend.face_plane(face)
        components = [round(abs(float(c)), 9) for c in normal]
        assert sorted(components) == [0.0, 0.0, 1.0], f"expected an axis-aligned unit normal, got {components}"
        axis = components.index(1.0)
        axes.append(axis)
        # The plane is the face's own, so its origin sits on that face of the box.
        assert abs(float(origin[axis])) == pytest.approx(extents[axis] / 2, rel=1e-9)
    assert sorted(axes) == [0, 0, 1, 1, 2, 2]


def test_curved_face_is_not_reported_planar(backend):
    wall = next(f for f in backend.faces(_make_cylinder(backend)) if backend.face_surface_type(f) == "cylinder")
    assert not backend.is_planar_face(wall)
    assert backend.face_plane(wall) is None


def test_loft_is_a_closed_solid(backend):
    loft = _make_loft(backend)
    assert backend.shape_type(loft) == "solid"
    assert backend.is_valid(loft)
    # Two caps plus four ruled walls per bay, for two bays.
    assert len(backend.faces(loft)) == 10
    assert all(len(loop.polygon) == 4 for loop in iter_face_poly_loops(loft, backend=backend))


# --------------------------------------------------------------------------------
# Cross-backend agreement. The actual parity claim.
# --------------------------------------------------------------------------------


@pytest.mark.parametrize("construction", sorted(CONSTRUCTIONS))
def test_topology_counts_agree(both_backends, construction):
    occ, adacpp = both_backends
    build = CONSTRUCTIONS[construction]
    assert _topology_counts(adacpp, build(adacpp)) == _topology_counts(occ, build(occ))


@pytest.mark.parametrize("construction", sorted(CONSTRUCTIONS))
def test_diagnostics_agree(both_backends, construction):
    occ, adacpp = both_backends
    build = CONSTRUCTIONS[construction]
    expected, actual = _diagnostics(occ, build(occ)), _diagnostics(adacpp, build(adacpp))

    assert actual["surface_types"] == expected["surface_types"]
    assert actual["area"] == pytest.approx(expected["area"], rel=1e-9)
    assert actual["volume"] == pytest.approx(expected["volume"], rel=1e-9)
    assert actual["centroid"] == pytest.approx(expected["centroid"], abs=1e-9)
    assert actual["face_areas"] == pytest.approx(expected["face_areas"], rel=1e-9)


def test_boolean_cut_agrees(both_backends):
    occ, adacpp = both_backends
    for be in (occ, adacpp):
        assert be.volume(_make_cut(be)) == pytest.approx(CUT_VOLUME, rel=1e-9)
    assert _diagnostics(adacpp, _make_cut(adacpp))["face_areas"] == pytest.approx(
        _diagnostics(occ, _make_cut(occ))["face_areas"], rel=1e-9
    )


def test_face_planes_agree(both_backends):
    occ, adacpp = both_backends

    def planes(be):
        out = []
        for face in be.faces(_make_box(be)):
            origin, normal = be.face_plane(face)
            out.append(tuple(round(float(c), 9) + 0.0 for c in (*origin, *normal)))
        return sorted(out)

    assert planes(adacpp) == planes(occ)


def test_loft_face_loops_agree(both_backends):
    occ, adacpp = both_backends

    def loops(be):
        return sorted(
            tuple(sorted(tuple(round(float(c), 9) + 0.0 for c in p) for p in loop.polygon))
            for loop in iter_face_poly_loops(_make_loft(be), backend=be)
        )

    assert loops(adacpp) == loops(occ)


def test_transform_agrees(both_backends):
    occ, adacpp = both_backends
    matrix = np.array([[0.0, -1.0, 0.0, 1.0], [1.0, 0.0, 0.0, 2.0], [0.0, 0.0, 1.0, 3.0], [0.0, 0.0, 0.0, 1.0]])

    def moved(be):
        shape = be.transform(_make_box(be), matrix, True)
        return be.volume(shape), [float(c) for c in be.center_of_mass(shape)], be.bbox(shape)

    got, want = moved(adacpp), moved(occ)
    assert got[0] == pytest.approx(want[0], rel=1e-9)
    assert got[1] == pytest.approx(want[1], abs=1e-9)
    assert got[2] == pytest.approx(want[2], abs=1e-9)


# --------------------------------------------------------------------------------
# The escape hatch: handles from either kernel reaching pythonocc.
# --------------------------------------------------------------------------------


def test_to_occ_shape_leaves_occ_handles_alone():
    if not backend_available(CadBackendName.OCC):
        pytest.skip("occ backend not installed")
    occ = select_backend(prefer="occ")
    box = _make_box(occ)
    assert to_occ_shape(box, backend=occ) is box


def test_serialize_round_trips_through_deserialize():
    if not backend_available(CadBackendName.OCC):
        pytest.skip("occ backend not installed")
    occ = select_backend(prefer="occ")
    restored = occ.deserialize(occ.serialize(_make_cut(occ)))
    assert occ.volume(restored) == pytest.approx(CUT_VOLUME, rel=1e-9)


def test_deserialize_rejects_text_that_is_not_brep():
    if not backend_available(CadBackendName.OCC):
        pytest.skip("occ backend not installed")
    with pytest.raises(ValueError, match="not readable BREP"):
        select_backend(prefer="occ").deserialize("this is not a BREP file")


def test_to_occ_shape_carries_an_adacpp_handle_into_pythonocc(both_backends):
    """The pattern that previously forced the whole process onto the OCC backend.

    A shape built on adacpp is handed to raw pythonocc calls — explorer, cut, mass
    properties — and every answer must match what the adacpp verbs already give.
    """
    from OCC.Core.BRepAlgoAPI import BRepAlgoAPI_Cut
    from OCC.Core.BRepGProp import brepgprop
    from OCC.Core.GProp import GProp_GProps
    from OCC.Core.TopAbs import TopAbs_FACE
    from OCC.Core.TopExp import TopExp_Explorer
    from OCC.Core.TopoDS import TopoDS_Shape

    occ, adacpp = both_backends
    handle = _make_box(adacpp)
    shape = to_occ_shape(handle, backend=adacpp)
    assert isinstance(shape, TopoDS_Shape)

    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    occ_face_count = 0
    while explorer.More():
        occ_face_count += 1
        explorer.Next()
    assert occ_face_count == len(adacpp.faces(handle))

    props = GProp_GProps()
    brepgprop.VolumeProperties(shape, props)
    assert props.Mass() == pytest.approx(adacpp.volume(handle), rel=1e-9)

    cut = BRepAlgoAPI_Cut(shape, to_occ_shape(_make_cylinder(adacpp), backend=adacpp)).Shape()
    assert occ.volume(cut) == pytest.approx(CUT_VOLUME, rel=1e-9)


# --------------------------------------------------------------------------------
# Edge identity, without needing the native build installed.
# --------------------------------------------------------------------------------


class _StubCad:
    """Stands in for ``adacpp.cad``."""

    def __init__(self, edges):
        self._edges = edges

    def edges(self, shape):
        return list(self._edges)

    def face_id(self, edge):
        return hash(edge)


def _stub_backend(stub) -> AdacppBackend:
    be = AdacppBackend.__new__(AdacppBackend)
    be._cad = stub
    return be


def test_edges_passes_the_native_list_through_untouched():
    """The kernel owns edge identity; this side must not second-guess it.

    AdacppBackend.edges used to collapse repeats on ``face_id``, back when the native
    walk reported an edge once per incident face. ``face_id`` is a bare TShape pointer,
    which cannot tell a located copy from the sub-shape it was instanced from, so that
    de-duplication silently dropped a prism's top rails. ada-cpp 0.25.4 reports each
    edge once and keeps located copies distinct, so anything filtered here now is a
    real edge being thrown away -- including when two entries hash equal under
    ``face_id``, which is exactly the located-copy case.
    """
    be = _stub_backend(_StubCad(["e1", "e2", "e1", "e3", "e2"]))
    assert be.edges("shape") == ["e1", "e2", "e1", "e3", "e2"]


def test_edges_reports_the_missing_binding_rather_than_an_empty_list():
    be = _stub_backend(object())
    with pytest.raises(NotImplementedError, match="adacpp.cad.edges"):
        be.edges("shape")
