"""ada.drawings: projection with hidden-line removal, plane sections, annotations, SVG output."""

from __future__ import annotations

import math
import subprocess
import sys
import xml.etree.ElementTree as ET

import numpy as np
import pytest

import ada
from ada.drawings import (
    Caption,
    Drawing,
    Leader,
    LinearDimension,
    Polyline,
    RadiusDimension,
    ScaleBar,
    SectionPlane,
    Sheet,
    TitleBlock,
    View,
)
from ada.drawings.anchors import bbox_point, beam_end
from ada.drawings.annotations import (
    AngleDimension,
    DimensionFormat,
    LayoutContext,
    TextPrim,
    text_box,
)
from ada.drawings.mesh_ops import (
    chain_segments,
    clip_mesh,
    points_in_loops,
    slice_mesh,
    weld,
)
from ada.drawings.view import Projector, pick_scale, scale_label

SVG_NS = "{http://www.w3.org/2000/svg}"


def _shoelace(loop: np.ndarray) -> float:
    x, y = loop[:, 0], loop[:, 1]
    return 0.5 * float(np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y))


def _region_area(lw) -> float:
    """Area of the cut regions (one outer loop per region, the others holes in it)."""
    total = 0.0
    for r in lw.regions:
        areas = sorted((abs(_shoelace(lp)) for lp in r.loops), reverse=True)
        total += areas[0] - sum(areas[1:])
    return total


def _length(strokes) -> float:
    return sum(float(np.sum(np.linalg.norm(np.diff(s.points, axis=0), axis=1))) for s in strokes)


def _box(name="bx"):
    return ada.PrimBox(name, (1.0, 1.0, 1.0), (2.0, 3.0, 4.0))


# ---------------------------------------------------------------------------------- views


def test_named_views_are_right_handed():
    for name in ("front", "back", "left", "right", "plan", "bottom", "iso"):
        r, u, d = View.named(name).basis()
        assert np.allclose(np.cross(r, u), -d) or np.allclose(np.cross(d, u), r)
        assert abs(r @ u) < 1e-12 and abs(u @ d) < 1e-12
    r, u, d = View.named("front").basis()
    assert np.allclose(r, (1, 0, 0)) and np.allclose(u, (0, 0, 1))
    r, u, d = View.named("plan").basis()
    assert np.allclose(r, (1, 0, 0)) and np.allclose(u, (0, 1, 0))


def test_projector_scale_and_flip():
    pr = Projector(View.named("front"), 50, "m")
    assert pr.k == pytest.approx(20.0)
    p = pr.point((1.0, 0.0, 2.0))
    assert np.allclose(p, (20.0, -40.0))  # paper y runs down


def test_pick_scale():
    assert pick_scale((10.0, 5.0), (380.0, 260.0), "m") == 50.0
    assert pick_scale((0.1, 0.1), (380.0, 260.0), "m") == 0.5  # enlarged, 2:1
    assert scale_label(50.0) == "1:50" and scale_label(0.2) == "5:1"


# ---------------------------------------------------------------------------------- mesh ops


def test_slice_and_clip_unit_cube():
    v = np.array([[x, y, z] for x in (0, 1) for y in (0, 1) for z in (0, 1)], float)
    d = Drawing("t").add(ada.PrimBox("b", (0, 0, 0), (1, 1, 1)))
    it = d.items()[0]
    v, f = weld(it.vertices, it.faces, 1e-9)
    loops = slice_mesh(v, f, (0, 0, 0.5), (0, 0, 1))
    assert len(loops) == 1 and np.allclose(loops[0][0], loops[0][-1])
    assert abs(_shoelace(loops[0][:, :2])) == pytest.approx(1.0)
    v2, f2, on = clip_mesh(v, f, (0, 0, 0.5), (0, 0, 1), 1e-9)
    assert v2[:, 2].min() == pytest.approx(0.5) and v2[:, 2].max() == pytest.approx(1.0)
    assert on.sum() == 4 or on.sum() >= 4


def test_chain_and_point_in_loops():
    sq = [((0, 0), (1, 0)), ((1, 0), (1, 1)), ((1, 1), (0, 1)), ((0, 1), (0, 0))]
    chains = chain_segments([(np.array(a, float), np.array(b, float)) for a, b in sq], 1e-9)
    assert len(chains) == 1 and len(chains[0]) == 5
    inside = points_in_loops(np.array([[0.5, 0.5], [1.5, 0.5]]), [chains[0]])
    assert inside.tolist() == [True, False]


# ---------------------------------------------------------------------------------- projection


def test_box_front_projection_outline():
    d = Drawing("box", view="front", scale=10).add(_box())
    lw = d.linework()
    lo, hi = lw.bounds()
    assert np.allclose(lo, (1.0, 1.0), atol=1e-6) and np.allclose(hi, (2.0, 4.0), atol=1e-6)
    assert not lw.by_kind("hidden") and not lw.by_kind("cut")
    # the outline only: perimeter 2 * (1 + 3)
    assert _length(lw.by_kind("projection")) == pytest.approx(8.0, rel=1e-6)


def test_box_iso_hidden_lines():
    d = Drawing("box", view="iso", scale=10, show_hidden=True).add(_box())
    lw = d.linework()
    r, u, _ = d.view.basis()
    # the three edges at the hidden corner, projected
    expected = sum(
        np.linalg.norm([e @ r, e @ u]) for e in (np.array([1, 0, 0]), np.array([0, 2, 0]), np.array([0, 0, 3]))
    )
    assert _length(lw.by_kind("hidden")) == pytest.approx(expected, rel=1e-3)
    d2 = Drawing("box", view="iso", scale=10, show_hidden=False).add(_box())
    assert not d2.linework().by_kind("hidden")


def test_occluded_box_behind_box_is_hidden():
    front = ada.PrimBox("front", (0, 0, 0), (4, 1, 4))
    back = ada.PrimBox("back", (1, 3, 1), (2, 4, 2))  # entirely behind `front` in the front view
    lw = Drawing("t", view="front", scale=10, show_hidden=True).add([front, back]).linework()
    back_vis = [s for s in lw.by_kind("projection") if s.points[:, 0].min() > 0.9 and s.points[:, 0].max() < 2.1]
    assert _length(back_vis) == pytest.approx(0.0, abs=1e-9)
    assert _length(lw.by_kind("hidden")) == pytest.approx(4.0, rel=1e-3)


def test_sphere_outline_is_a_circle():
    sp = ada.PrimSphere("s", (0, 0, 0), 1.0)
    lw = Drawing("s", view="iso", scale=10).add(sp).linework()
    pts = np.vstack([s.points for s in lw.by_kind("projection")])
    rad = np.linalg.norm(pts, axis=1)
    assert rad.min() == pytest.approx(1.0, abs=1e-4) and rad.max() == pytest.approx(1.0, abs=1e-6)


def test_tube_beam_side_view():
    bm = ada.Beam("tube", (0, 0, 0), (2, 0, 0), "OD120x4")
    lw = Drawing("t", view="front", scale=5).add(bm).linework()
    lo, hi = lw.bounds()
    assert hi[0] - lo[0] == pytest.approx(2.0, abs=1e-3)
    assert hi[1] - lo[1] == pytest.approx(0.12, rel=0.01)


def test_beam_centerline_style():
    bm = ada.Beam("tube", (0, 0, 0), (2, 0, 1), "OD120x4")
    lw = Drawing("t", view="front", scale=20, beam_style="centerline").add(bm).linework()
    assert not lw.by_kind("projection")
    (cl,) = lw.by_kind("centerline")
    assert np.allclose(cl.points[[0, -1]], [[0, 0], [2, 1]]) or np.allclose(cl.points[[-1, 0]], [[0, 0], [2, 1]])


def test_wire_behind_box_is_hidden():
    box = ada.PrimBox("b", (0, 0, 0), (2, 1, 2))
    wire = Polyline([(-1, 2, 1), (3, 2, 1)], "w")  # behind the box, wider than it
    lw = Drawing("t", view="front", scale=10, show_hidden=True).add([box, wire]).linework()
    wire_vis = [s for s in lw.strokes if s.kind == "wire"]
    assert _length(wire_vis) == pytest.approx(2.0, rel=1e-2)  # the two ends that stick out
    hidden = [s for s in lw.by_kind("hidden") if s.group == "wire"]
    assert _length(hidden) == pytest.approx(2.0, rel=1e-2)


def test_placed_part_is_drawn_in_world_coordinates():
    p = ada.Part("p", placement=ada.Placement(origin=(10.0, 0.0, 0.0))) / [ada.PrimBox("b", (0, 0, 0), (1, 1, 1))]
    a = ada.Assembly("a") / p
    lo, hi = Drawing("t", view="front", scale=10).add(a).linework().bounds()
    assert lo[0] == pytest.approx(10.0, abs=1e-6) and hi[0] == pytest.approx(11.0, abs=1e-6)


# ---------------------------------------------------------------------------------- sections


@pytest.mark.parametrize("backend", ["mesh", "kernel"])
def test_box_section_area(backend):
    view = View.named("plan")
    sec = SectionPlane((0, 0, 2.5), (0, 0, -1))
    lw = Drawing("t", view=view, scale=10, section=sec, backend=backend).add(_box()).linework()
    assert _region_area(lw) == pytest.approx(2.0, rel=1e-6)
    # beyond the cut: nothing of the box's top remains, its outline is the cut itself
    assert lw.by_kind("cut")


@pytest.mark.parametrize("backend", ["mesh", "kernel"])
def test_tube_section_ring(backend):
    bm = ada.Beam("tube", (0, 0, 0), (2, 0, 0), "OD120x4")
    sec = SectionPlane((1.0, 0, 0), (-1, 0, 0))
    lw = Drawing("t", view="right", scale=2, section=sec, backend=backend).add(bm).linework()
    (reg,) = lw.regions
    assert len(reg.loops) == 2
    ring = math.pi * (0.06**2 - 0.056**2)
    assert _region_area(lw) == pytest.approx(ring, rel=0.03)
    # beyond the cut the tube hides behind its own cap; only its far rims show, and they
    # project onto the cut loops (the bore is open, the outer rim is the outline)
    for s in lw.by_kind("projection"):
        rad = np.linalg.norm(s.points, axis=1)
        assert np.all((np.abs(rad - 0.06) < 1e-3) | (np.abs(rad - 0.056) < 1e-3))


def test_sphere_section_through_centre():
    sp = ada.PrimSphere("s", (0, 0, 0), 2.0)
    lw = Drawing("t", view="front", scale=20, section=View.named("front").section_at((0, 0, 0))).add(sp).linework()
    assert _region_area(lw) == pytest.approx(math.pi * 4.0, rel=5e-3)


# ---------------------------------------------------------------------------------- annotations


def _ctx(scale=10.0):
    pr = Projector(View.named("front"), scale, "m")
    return LayoutContext(pr, DimensionFormat("m", 2), (np.array([0.0, -100.0]), np.array([100.0, 0.0])))


def test_linear_dimension_value_and_offset():
    ctx = _ctx()
    prims = LinearDimension((1, 0, 1), (4, 0, 1), "horizontal", offset=-8).layout(ctx)
    (txt,) = [p for p in prims if isinstance(p, TextPrim)]
    assert txt.text == "3.00"
    line = [p for p in prims if p.__class__.__name__ == "PathPrim" and p.cls == "dimension"][0]
    # 1 m at 1:10 = 100 mm; the line sits 8 mm below (paper y down) the measured points
    assert np.allclose(line.points[:, 1], -100.0 + 8.0)
    assert line.marker_start == "dim-start" and line.marker_end == "dim-end"


def test_vertical_and_aligned_dimensions():
    ctx = _ctx()
    prims = LinearDimension((0, 0, 0), (3, 0, 4), "aligned").layout(ctx)
    assert [p.text for p in prims if isinstance(p, TextPrim)] == ["5.00"]
    prims = LinearDimension((0, 0, 0), (3, 0, 4), "vertical", text="H").layout(ctx)
    assert [p.text for p in prims if isinstance(p, TextPrim)] == ["H"]


def test_dimensions_step_out_of_each_other():
    ctx = _ctx()
    a = LinearDimension((1, 0, 1), (4, 0, 1), "horizontal", offset=-8).layout(ctx)
    b = LinearDimension((1, 0, 1), (4, 0, 1), "horizontal", offset=-8).layout(ctx)
    ta = [p for p in a if isinstance(p, TextPrim)][0]
    tb = [p for p in b if isinstance(p, TextPrim)][0]
    assert tb.y > ta.y + 5.0  # moved one tier further out (down)
    lo_a, hi_a = text_box(ta)
    lo_b, hi_b = text_box(tb)
    assert lo_b[1] > hi_a[1]


def test_auto_offset_goes_away_from_centre():
    ctx = _ctx()
    # content centre at (50, -50) paper = (5, 5) model; points above it -> dimension above
    prims = LinearDimension((4, 0, 9), (6, 0, 9), "horizontal").layout(ctx)
    line = [p for p in prims if p.__class__.__name__ == "PathPrim" and p.cls == "dimension"][0]
    assert line.points[0, 1] < -90.0


def test_radius_and_leader():
    ctx = _ctx()
    prims = RadiusDimension((0, 0, 0), 0.5, "radius").layout(ctx)
    assert [p.text for p in prims if isinstance(p, TextPrim)] == ["R0.50"]
    prims = RadiusDimension((0, 0, 0), 0.5, "diameter").layout(ctx)
    assert [p.text for p in prims if isinstance(p, TextPrim)] == ["Ø1.00"]
    l1 = Leader((5, 0, 5), "first", offset=(10, 10)).layout(ctx)
    l2 = Leader((5, 0, 5), "second", offset=(10, 10)).layout(ctx)
    t1 = [p for p in l1 if isinstance(p, TextPrim)][0]
    t2 = [p for p in l2 if isinstance(p, TextPrim)][0]
    assert not ctx.collides(text_box(t2), pad=-1e3) or (t1.x, t1.y) != (t2.x, t2.y)


# ---------------------------------------------------------------------------------- svg


def test_svg_document_parses(tmp_path):
    col = ada.Beam("col", (0, 0, 0), (0, 0, 4), "HEB200")
    raf = ada.Beam("raf", (0, 0, 4), (6, 0, 4), "IPE300")
    tank = ada.PrimCyl("tank", (3, 0, 0), (3, 0, 2), 0.8)
    p = ada.Part("frame") / [col, raf, tank]
    d = Drawing("Frame & tank <test>", view="front", scale=50)
    d.add(p).style("shape", color="#336699", cut_fill="hatch")
    d.annotate(
        LinearDimension(beam_end(col, "start"), beam_end(col, "end"), "vertical"),
        Leader(bbox_point(tank, 1, 0.5, 1), "tank"),
        Caption("FRONT"),
        ScaleBar(),
        TitleBlock("Frame", number="T-1"),
    )
    path = tmp_path / "frame.svg"
    svg = d.to_svg(path)
    assert path.read_text() == svg
    root = ET.fromstring(svg.encode())
    assert root.tag == f"{SVG_NS}svg"
    assert root.get("width").endswith("mm") and root.get("data-scale") == "1:50"
    w = float(root.get("width")[:-2])
    vb = [float(x) for x in root.get("viewBox").split()]
    assert vb[2] == pytest.approx(w)
    assert root.find(f"{SVG_NS}title").text == "Frame & tank <test>"
    classes = {c for el in root.iter() for c in (el.get("class") or "").split()}
    assert {"projection", "dimension", "leader", "caption", "title-block", "scale-bar-fill"} <= classes
    texts = [el.text for el in root.iter(f"{SVG_NS}text")]
    assert "4.00" in texts and "FRONT" in texts and "SCALE 1:50" in texts
    ids = {el.get("id") for el in root.iter() if el.get("id")}
    assert {"dim-start", "dim-end", "leader-arrow", "hatch"} <= ids


def test_section_svg_has_cut_fill():
    bm = ada.Beam("tube", (0, 0, 0), (2, 0, 0), "OD120x4")
    d = Drawing("t", view="right", scale=2, section=SectionPlane((1, 0, 0), (-1, 0, 0))).add(bm, group="tube")
    root = ET.fromstring(d.to_svg().encode())
    fills = [el for el in root.iter(f"{SVG_NS}path") if "cut-fill" in (el.get("class") or "")]
    assert len(fills) == 1 and "g-tube" in fills[0].get("class")
    assert fills[0].get("d").count("M") == 2


def test_drawings_do_not_import_ifcopenshell():
    code = "import sys, ada.drawings; sys.exit(1 if 'ifcopenshell' in sys.modules else 0)"
    assert subprocess.run([sys.executable, "-c", code]).returncode == 0


def test_short_dimension_text_side():
    ctx = _ctx()
    after = LinearDimension((0, 0, 0), (0.05, 0, 0), "horizontal", offset=-8).layout(ctx)
    before = LinearDimension((0, 0, 3), (0.05, 0, 3), "horizontal", offset=-8, short_text="before").layout(ctx)
    ta = [p for p in after if isinstance(p, TextPrim)][0]
    tb = [p for p in before if isinstance(p, TextPrim)][0]
    assert ta.x > 5.0 and tb.x < 0.0  # 0.05 m = 5 mm at 1:10: text past the right / left end


def test_angle_dimension_arms():
    ctx = _ctx()
    prims = AngleDimension((0, 0, 0), (1, 0, 0), (0, 0, 1), radius=10, arms=True).layout(ctx)
    assert [p.text for p in prims if isinstance(p, TextPrim)] == ["90°"]
    assert sum(1 for p in prims if getattr(p, "cls", "") == "dimension extension") == 2


def test_sheet_composes_drawings():
    bm = ada.Beam("tube", (0, 0, 0), (2, 0, 0), "OD120x4")
    side = Drawing("side", view="front", scale=20).add(bm).annotate(Caption("SIDE"))
    sec = Drawing("cut", view="right", scale=2, section=SectionPlane((1, 0, 0), (-1, 0, 0))).add(bm)
    svg = Sheet("both", [side, sec], gap=10).to_svg()
    root = ET.fromstring(svg.encode())
    groups = [el for el in root.iter(f"{SVG_NS}g") if el.get("class") == "drawing"]
    assert len(groups) == 2
    w = float(root.get("width")[:-2])
    assert w == pytest.approx(side.sheet().width + sec.sheet().width + 10, abs=1e-2)
    assert len([el for el in root.iter(f"{SVG_NS}marker") if el.get("id") == "dim-end"]) == 1
