"""The procedural beam-solid extruder, measured against OCC.

OCC is the reference. The extruder exists to make the viewer bake affordable
(~2.6 ms per beam through the CAD kernel, seven minutes on a large deck), not
to draw something new, so every geometric claim here is a claim that the
kernel and the extruder agree:

* a polygonal section extrudes to the SAME vertices, to the precision OCC's
  tessellator reports them in (float32);
* a curved one agrees to the sampling density, in bounding box and volume;
* the result is a closed, outward-wound solid either way;
* the profile centroid used to place eccentric beams is the same number.

The parity sweep covers the orientations and eccentricities that have actually
produced bugs before: ``up`` flipped and turned, equal and unequal end offsets,
and an offset with an axial component -- the case where the extrusion axis
stops being the element axis and the whole frame is rebuilt.
"""

from __future__ import annotations

import numpy as np
import pytest

from ada import Beam, BeamTapered, Section
from ada.api.curves import CurvePoly2d
from ada.fem.results.artefacts.beam_extrude import (
    SectionOutlineCache,
    extrude_beam,
    outline_for,
    unsupported_reason,
)
from ada.fem.results.artefacts.beam_solids import tessellate_beams_to_solid_mesh
from ada.fem.results.beam_placement import SectionCentroidCache

# the procedural extruder takes its section mesh from adacpp, so without it there is
# nothing here to assert against: every beam falls back to the kernel by design.
# The adacpp CI leg is where these run.


# Marked rather than skipped: deselected where adacpp is absent, which is the env this
# module was never meant to run in.
pytestmark = pytest.mark.adacpp

# OCC's tessellator hands back float32 positions, so "the same vertex" cannot
# be tighter than float32 epsilon on a coordinate of a few metres.
OCC_VERTEX_TOL = 2e-6


def _poly_section(name="PolyWithVoid"):
    outer = CurvePoly2d([(-0.3, -0.2), (0.3, -0.2), (0.3, 0.2), (-0.3, 0.2)], (0, 0, 0), (0, 0, 1), (1, 0, 0))
    inner = CurvePoly2d([(-0.2, -0.1), (0.2, -0.1), (0.2, 0.1), (-0.2, 0.1)], (0, 0, 0), (0, 0, 1), (1, 0, 0))
    return Section(name, "poly", outer_poly=outer, inner_poly=inner)


def _named(name: str) -> Section:
    return Section(name, from_str=name)


# Every polygonal section type the profile builders support. These extrude to
# flat faces, so OCC puts vertices only on the corners and parity is exact.
POLYGONAL_SECTIONS = {
    "HEA300": lambda: _named("HEA300"),
    "IG650x300x25x40": lambda: _named("IG650x300x25x40"),
    "TG650x300x25x40": lambda: _named("TG650x300x25x40"),
    "BG800x600x20x30": lambda: _named("BG800x600x20x30"),
    "HP180x10": lambda: _named("HP180x10"),
    "UNP180x10": lambda: _named("UNP180x10"),
    "FB100x10": lambda: _named("FB100x10"),
    "POLY": _poly_section,
}

CURVED_SECTIONS = {
    "TUB375x35": lambda: _named("TUB375x35"),
    "CIRC100": lambda: _named("CIRC100"),
}

# up, (e1, e2) — the last pair puts an axial component in one end's offset.
UPS = [(0, 0, 1), (0, 0, -1), (0, 1, 0), (0.0, 0.3, 0.95)]
ECCENTRICITIES = [
    (None, None),
    ((0, 0.1, 0.2), (0, 0.1, 0.2)),
    ((0, -0.5, 0.05), (0, 0, 0.05)),
    ((0.02, -0.1, 0.05), (0, 0.0, 0.0)),
]


def _occ_vertices(beam: Beam) -> np.ndarray:
    from ada.occ.tessellating import BatchTessellator

    ms = BatchTessellator().tessellate_geom(beam.solid_geom(), beam)
    return np.asarray(ms.position, dtype=np.float64).reshape(-1, 3)


def _unique(points: np.ndarray, decimals: int = 6) -> np.ndarray:
    return np.unique(np.round(points, decimals), axis=0)


def _hausdorff(a: np.ndarray, b: np.ndarray) -> float:
    d = np.linalg.norm(a[:, None, :] - b[None, :, :], axis=2)
    return float(max(d.min(axis=1).max(), d.min(axis=0).max()))


def _edge_use_counts(tris: np.ndarray) -> np.ndarray:
    tris = np.asarray(tris, dtype=np.int64)
    edges = np.concatenate([tris[:, [0, 1]], tris[:, [1, 2]], tris[:, [2, 0]]], axis=0)
    _, counts = np.unique(np.sort(edges, axis=1), axis=0, return_counts=True)
    return counts


def _signed_volume(verts: np.ndarray, tris: np.ndarray) -> float:
    a = verts[tris[:, 0]]
    b = verts[tris[:, 1]]
    c = verts[tris[:, 2]]
    return float(np.einsum("ij,ij->i", a, np.cross(b, c)).sum()) / 6.0


@pytest.mark.parametrize("sec_name", sorted(POLYGONAL_SECTIONS))
def test_polygonal_sections_extrude_to_the_same_vertices_as_occ(sec_name):
    """A flat-faced prism has no sampling freedom: both paths must land every
    vertex on the same corner, for every orientation and eccentricity."""

    section = POLYGONAL_SECTIONS[sec_name]()
    outline = outline_for(section)
    assert outline is not None

    for up in UPS:
        for e1, e2 in ECCENTRICITIES:
            for p1, p2 in (((0, 0, 0), (3, 0, 0)), ((1, 2, 3), (4, 6, 9))):
                bm = Beam("b", p1, p2, sec=section, up=up, e1=e1, e2=e2)
                verts, _ = extrude_beam(bm, outline)
                mine = _unique(verts)
                theirs = _unique(_occ_vertices(bm))
                label = f"{sec_name} up={up} e1={e1} e2={e2} p1={p1}"
                assert mine.shape == theirs.shape, label
                assert _hausdorff(mine, theirs) <= OCC_VERTEX_TOL, label


@pytest.mark.parametrize("sec_name", sorted(POLYGONAL_SECTIONS))
def test_polygonal_sections_match_occ_volume(sec_name):
    section = POLYGONAL_SECTIONS[sec_name]()
    bm = Beam("b", (0, 0, 0), (3, 0, 0), sec=section, up=(0, 0, 1))
    verts, tris = extrude_beam(bm, outline_for(section))

    trimesh = pytest.importorskip("trimesh")
    occ_verts = _occ_vertices(bm)
    from ada.occ.tessellating import BatchTessellator

    ms = BatchTessellator().tessellate_geom(bm.solid_geom(), bm)
    occ_tris = np.asarray(ms.indices, dtype=np.int64).reshape(-1, 3)
    occ_volume = trimesh.Trimesh(vertices=occ_verts, faces=occ_tris, process=True).volume

    mine = _signed_volume(verts, np.asarray(tris, dtype=np.int64))
    assert mine == pytest.approx(occ_volume, rel=1e-5)


@pytest.mark.parametrize("sec_name", sorted({**POLYGONAL_SECTIONS, **CURVED_SECTIONS}))
def test_extruded_solid_is_closed_and_outward_wound(sec_name):
    """Every edge used exactly twice (closed) and a positive enclosed volume
    (consistently outward). A flipped cap or a hole wound the wrong way shows
    up in one or the other."""

    factory = {**POLYGONAL_SECTIONS, **CURVED_SECTIONS}[sec_name]
    section = factory()
    bm = Beam("b", (1, 2, 3), (4, 6, 9), sec=section, up=(0, 0, 1))
    verts, tris = extrude_beam(bm, outline_for(section))
    tris = np.asarray(tris, dtype=np.int64)

    assert np.all(_edge_use_counts(tris) == 2)
    # Take the volume about the solid's own centre so the result does not
    # depend on how far from the origin the beam happens to sit.
    assert _signed_volume(verts - verts.mean(axis=0), tris) > 0


def test_tube_walls_face_out_and_the_bore_faces_in():
    """Winding per facet, not just in aggregate. A tube is the one section where
    every side facet has an unambiguous right answer: the outer wall's normal
    must point away from the axis and the bore's must point back towards it —
    a hole wound with the outer ring would fail here while still closing."""

    section = _named("TUB375x35")
    outline = outline_for(section)
    bm = Beam("b", (0, 0, 0), (3, 0, 0), sec=section, up=(0, 0, 1))
    verts, _ = extrude_beam(bm, outline)
    (o_start, o_stop), (i_start, i_stop) = outline.ring_slices
    n = outline.n_points
    tris = np.asarray(outline.triangles, dtype=np.int64)

    a, b, c = verts[tris[:, 0]], verts[tris[:, 1]], verts[tris[:, 2]]
    normals = np.cross(b - a, c - a)
    centres = (a + b + c) / 3.0
    # Radial direction from the beam axis (the x axis here) out to the facet.
    radial = centres - np.array([1.0, 0.0, 0.0]) * centres[:, 0:1]
    radial /= np.linalg.norm(radial, axis=1, keepdims=True)
    radial_component = np.einsum("ij,ij->i", normals, radial)

    point_index = np.arange(n)
    outer_vertex = (point_index >= o_start) & (point_index < o_stop)
    inner_vertex = (point_index >= i_start) & (point_index < i_stop)
    corner_ring = tris % n
    on_outer = outer_vertex[corner_ring].all(axis=1)
    on_inner = inner_vertex[corner_ring].all(axis=1)
    # The caps touch both rings, so neither mask picks them up.
    is_side = (tris < n).any(axis=1) & (tris >= n).any(axis=1)

    assert np.all(radial_component[on_outer & is_side] > 0)
    assert np.all(radial_component[on_inner & is_side] < 0)


@pytest.mark.parametrize("sec_name", sorted(CURVED_SECTIONS))
def test_curved_sections_match_occ_bounds_and_volume(sec_name):
    """A sampled circle is a polygon, so it sits just inside OCC's own sampled
    circle. Both are approximations of the same cylinder; they must agree to
    the sampling error, not exactly."""

    trimesh = pytest.importorskip("trimesh")
    section = CURVED_SECTIONS[sec_name]()
    outline = outline_for(section)
    bm = Beam("b", (0, 0, 0), (3, 0, 0), sec=section, up=(0, 0, 1))
    verts, tris = extrude_beam(bm, outline)

    occ_verts = _occ_vertices(bm)
    from ada.occ.tessellating import BatchTessellator

    ms = BatchTessellator().tessellate_geom(bm.solid_geom(), bm)
    occ_tris = np.asarray(ms.indices, dtype=np.int64).reshape(-1, 3)
    occ_volume = trimesh.Trimesh(vertices=occ_verts, faces=occ_tris, process=True).volume

    r = float(section.r)
    assert np.allclose(verts.min(axis=0), occ_verts.min(axis=0), atol=0.02 * r)
    assert np.allclose(verts.max(axis=0), occ_verts.max(axis=0), atol=0.02 * r)
    assert _signed_volume(verts, np.asarray(tris, dtype=np.int64)) == pytest.approx(occ_volume, rel=0.01)


def test_a_void_makes_the_surface_a_torus_not_a_sphere():
    """Euler characteristic is the cheap way to ask "did the hole survive?":
    0 for a tube or a box section, 2 for a solid bar."""

    def euler(section):
        bm = Beam("b", (0, 0, 0), (3, 0, 0), sec=section, up=(0, 0, 1))
        verts, tris = extrude_beam(bm, outline_for(section))
        tris = np.asarray(tris, dtype=np.int64)
        edges = np.concatenate([tris[:, [0, 1]], tris[:, [1, 2]], tris[:, [2, 0]]], axis=0)
        n_edges = int(np.unique(np.sort(edges, axis=1), axis=0).shape[0])
        return verts.shape[0] - n_edges + tris.shape[0]

    assert euler(_named("TUB375x35")) == 0
    assert euler(_named("BG800x600x20x30")) == 0
    assert euler(_poly_section()) == 0
    assert euler(_named("CIRC100")) == 2
    assert euler(_named("HEA300")) == 2


def test_filleted_iprofile_tracks_the_occ_solid():
    """An I-profile with a root radius adds arcs the two paths sample
    differently. The outline still has to be the same shape: same extent, and
    a volume between the un-filleted profile and OCC's."""

    trimesh = pytest.importorskip("trimesh")
    section = Section(
        "HEA300r",
        "IG",
        h=0.29,
        w_top=0.3,
        w_btn=0.3,
        t_w=0.0085,
        t_ftop=0.014,
        t_fbtn=0.014,
        r=0.027,
    )
    bm = Beam("b", (0, 0, 0), (3, 0, 0), sec=section, up=(0, 0, 1))
    outline = outline_for(section)
    assert outline is not None
    verts, tris = extrude_beam(bm, outline)

    occ_verts = _occ_vertices(bm)
    from ada.occ.tessellating import BatchTessellator

    ms = BatchTessellator().tessellate_geom(bm.solid_geom(), bm)
    occ_tris = np.asarray(ms.indices, dtype=np.int64).reshape(-1, 3)
    occ_volume = trimesh.Trimesh(vertices=occ_verts, faces=occ_tris, process=True).volume

    assert np.allclose(verts.min(axis=0), occ_verts.min(axis=0), atol=1e-6)
    assert np.allclose(verts.max(axis=0), occ_verts.max(axis=0), atol=1e-6)
    assert np.all(_edge_use_counts(np.asarray(tris, dtype=np.int64)) == 2)

    # A BRACKET, not a tolerance. A root radius ADDS material to the corner, and
    # both paths approximate that arc with chords -- ours from discretize_curve,
    # OCC's from its own tessellator. So the answer sits between the profile with
    # no radius at all and OCC's finer approximation of the same arc, and where
    # exactly depends on how finely each samples.
    #
    # This was `approx(occ_volume, rel=1e-3)`, which is a statement about OCC's
    # sampling density rather than about our geometry: it held on OCCT 7.9.3 and
    # broke on 8.0.1 when OCC's fillet tessellation changed, with nothing wrong
    # on this side. The bracket is what the docstring above always claimed.
    square = Section("HEA300sq", "IG", h=0.29, w_top=0.3, w_btn=0.3, t_w=0.0085, t_ftop=0.014, t_fbtn=0.014)
    sq_outline = outline_for(square)
    sq_bm = Beam("sq", (0, 0, 0), (3, 0, 0), sec=square, up=(0, 0, 1))
    sq_verts, sq_tris = extrude_beam(sq_bm, sq_outline)
    v_square = _signed_volume(sq_verts, np.asarray(sq_tris, dtype=np.int64))
    v_filleted = _signed_volume(verts, np.asarray(tris, dtype=np.int64))

    assert v_square < v_filleted < occ_volume, (
        f"filleted volume {v_filleted} should sit between the un-filleted " f"{v_square} and OCC's {occ_volume}"
    )
    # And not by a lot: a chorded fillet is a small correction, not a new shape.
    assert v_filleted == pytest.approx(occ_volume, rel=2e-2)


# ---------------------------------------------------------------------------
# Outline, cache and triangulator
# ---------------------------------------------------------------------------


def test_outline_area_and_centroid_are_the_section_s_own():
    section = _named("BG800x600x20x30")
    outline = outline_for(section)
    # 0.8 x 0.6 outer, 20 mm webs and 30 mm flanges -> a 0.56 x 0.74 void.
    assert outline.area == pytest.approx(0.8 * 0.6 - 0.74 * 0.56, rel=1e-9)
    assert outline.centroid == pytest.approx((0.0, 0.0), abs=1e-12)


def test_outline_rings_are_wound_outer_ccw_and_voids_cw():
    from ada.fem.results.artefacts.beam_extrude import _signed_area

    outline = outline_for(_named("TUB375x35"))
    (o_start, o_stop), (i_start, i_stop) = outline.ring_slices
    assert _signed_area(outline.points[o_start:o_stop]) > 0
    assert _signed_area(outline.points[i_start:i_stop]) < 0


def test_outline_cache_samples_each_section_once():
    section = _named("HEA300")
    cache = SectionOutlineCache()
    first = cache.get(section)
    assert first is not None
    assert cache.get(section) is first


def test_cap_triangulation_tiles_the_profile_area():
    """Ear clipping plus hole bridging must cover the section exactly once —
    the summed triangle area is the net area, voids removed."""

    for name in ("HEA300", "UNP180x10", "BG800x600x20x30", "TUB375x35"):
        outline = outline_for(_named(name))
        p = outline.points
        n = outline.n_points
        all_tris = np.asarray(outline.triangles, dtype=np.int64)
        # The near cap is reversed (its normal is -xvec), so flip it back rather
        # than carry a separate array of the same triangles.
        t = all_tris[(all_tris < n).all(axis=1)][:, ::-1]
        a = p[t[:, 0]]
        b = p[t[:, 1]]
        c = p[t[:, 2]]
        area = 0.5 * ((b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0]))
        assert area.min() > 0, f"{name}: a clipped ear came out inverted"
        assert float(area.sum()) == pytest.approx(outline.area, rel=1e-9)


# ---------------------------------------------------------------------------
# Section centroid
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("sec_name", sorted({**POLYGONAL_SECTIONS, **CURVED_SECTIONS}))
def test_section_centroid_matches_the_occ_measurement(sec_name):
    """``SectionCentroidCache`` decides where an eccentric beam's profile sits.
    Its failure mode is silent — every section in the model drawn off its plate
    — so the closed-form answer has to reproduce the tessellated one."""

    section = {**POLYGONAL_SECTIONS, **CURVED_SECTIONS}[sec_name]()
    procedural = SectionCentroidCache._measure_procedural(section)
    occ = SectionCentroidCache._measure_occ(section)
    assert procedural is not None
    assert occ is not None
    assert procedural == pytest.approx(occ, abs=1e-4)


def test_section_centroid_cache_prefers_the_procedural_measurement():
    section = _named("HP180x10")
    cache = SectionCentroidCache()
    assert cache.offset_local(section) == pytest.approx(SectionCentroidCache._measure_procedural(section), abs=1e-12)


# ---------------------------------------------------------------------------
# The seam with the OCC path
# ---------------------------------------------------------------------------


def _beam_tuple(beam, elem_id, p0=(0.0, 0.0, 0.0), p1=(3.0, 0.0, 0.0)):
    return (beam, elem_id, 0, 1, np.asarray(p0, dtype=float), np.asarray(p1, dtype=float))


def test_unsupported_reason_names_what_needs_the_kernel():
    section = _named("HEA300")
    assert unsupported_reason(Beam("b", (0, 0, 0), (3, 0, 0), sec=section)) is None
    tapered = BeamTapered("t", (0, 0, 0), (3, 0, 0), sec=section, tap=_named("HEA200"))
    assert unsupported_reason(tapered) == "tapered"


def test_procedural_falls_back_to_occ_per_beam_and_says_so():
    """A beam the extruder cannot take is not dropped — it goes through the
    kernel and is counted separately from a genuine skip."""

    section = _named("HEA300")
    beams = [_beam_tuple(Beam(f"b{i}", (0, i, 0), (3, i, 0), sec=section), i + 1) for i in range(3)]
    beams.append(
        _beam_tuple(
            BeamTapered("t", (0, 9, 0), (3, 9, 0), sec=section, tap=_named("HEA200")),
            99,
            (0.0, 9.0, 0.0),
            (3.0, 9.0, 0.0),
        )
    )

    mesh = tessellate_beams_to_solid_mesh(list(beams), method="procedural")
    assert mesh is not None
    assert len(mesh.element_ranges) == 4, "the fallback beam must still be in the output"
    assert mesh.skip_reasons == {"occ-fallback[tapered]": 1}


def test_occ_method_is_the_untouched_kernel_path():
    """``method="occ"`` must be exactly what the bake did before the extruder
    existed: OCC's tessellation, deduplicated, nothing else."""

    from ada.fem.results.artefacts.beam_solids import _dedup_beam_tessellation
    from ada.occ.tessellating import BatchTessellator

    section = _named("TUB375x35")
    bm = Beam("b", (0, 0, 0), (3, 0, 0), sec=section, up=(0, 0, 1))
    mesh = tessellate_beams_to_solid_mesh([_beam_tuple(bm, 7)], method="occ")

    ms = BatchTessellator().tessellate_geom(bm.solid_geom(), bm)
    raw_verts = np.asarray(ms.position, dtype=np.float64).reshape(-1, 3)
    raw_tris = np.asarray(ms.indices, dtype=np.uint32).reshape(-1, 3)
    verts, tris, _ = _dedup_beam_tessellation(raw_verts, raw_tris, np.zeros(raw_verts.shape[0], dtype=np.float32))

    assert np.array_equal(mesh.points, verts)
    assert np.array_equal(mesh.triangles, tris)
    assert mesh.skip_reasons == {}


def test_both_methods_agree_on_beam_count_and_ranges():
    section = _named("HEA300")
    beams = [_beam_tuple(Beam(f"b{i}", (0, i, 0), (3, i, 0), sec=section), i + 1) for i in range(4)]

    procedural = tessellate_beams_to_solid_mesh(list(beams), method="procedural")
    occ = tessellate_beams_to_solid_mesh(list(beams), method="occ")

    assert [r.label for r in procedural.element_ranges] == [r.label for r in occ.element_ranges]
    assert procedural.total_beams == occ.total_beams
    # Same axial parameterisation: every vertex of a beam along +X spans 0..1.
    assert procedural.vertex_t.min() == pytest.approx(0.0)
    assert procedural.vertex_t.max() == pytest.approx(1.0)


def test_unknown_method_is_rejected():
    with pytest.raises(ValueError, match="unknown beam-solid method"):
        tessellate_beams_to_solid_mesh([], method="marching-cubes")
