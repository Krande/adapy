"""Profile-level parity between ``LoftStation.to_poly_loop`` and the sibling
loft tool's ``SectionFactory`` (``RectangleSection`` / ``CircleSection``).

The compiled loft solid matches the loft tool byte-for-byte only if the section
profiles do. These tests pin the exact point layouts (reproduced here from the
loft tool's formulas — the single source of truth) and the member-level 12-point
mixing rule, then build the floater column set and assert the column bands carry
12 side faces (3-point-per-corner outline).
"""

from __future__ import annotations

import math

import numpy as np
import pytest

import ada
from ada.topo_model import ProceduralBuilder
from ada.topology.entities import LoftStation, TopoLoftMember

_ABS = 1e-9


def _loft_curved_capable() -> bool:
    """True when the loft backend can emit ``PlateCurved`` for the ruled corner
    panels of a rounded member — which needs the ``is_planar_face`` verb.

    Capable when OCC.Core is importable (an ``OccBackend`` runs the loft) or the
    native adacpp build ships the verb (adacpp >=0.20). Where neither is present
    (pyodide/wasm) the compile falls back to flat plates and a rounded column
    bulges to ~+/-5.18 by design, so the +/-5.01 parity assertion is skipped there
    rather than failing."""
    try:
        import OCC.Core  # noqa: F401  (availability decides the loft backend)

        return True
    except ImportError:
        pass
    try:
        import adacpp.cad as _adacpp_cad

        return hasattr(_adacpp_cad, "is_planar_face")
    except ImportError:
        return False


# --- reference layouts (== loft tool section.py formulas) ------------------- #
def _ref_rounded(w_full: float, h_full: float, r: float) -> list[tuple[float, float]]:
    w, h = w_full / 2, h_full / 2
    s = math.sqrt(2) / 2
    return [
        (-w + r, -h),
        (-w + r - r * s, -h + r - r * s),
        (-w, -h + r),
        (-w, h - r),
        (-w + r - r * s, h - r + r * s),
        (-w + r, h),
        (w - r, h),
        (w - r + r * s, h - r + r * s),
        (w, h - r),
        (w, -h + r),
        (w - r + r * s, -h + r - r * s),
        (w - r, -h),
    ]


def _ref_sharp_12(w_full: float, h_full: float) -> list[tuple[float, float]]:
    w, h = w_full / 2, h_full / 2
    r = 0.05 * min(w_full, h_full)
    return [
        (-w + r, -h),
        (-w, -h),
        (-w, -h + r),
        (-w, h - r),
        (-w, h),
        (-w + r, h),
        (w - r, h),
        (w, h),
        (w, h - r),
        (w, -h + r),
        (w, -h),
        (w - r, -h),
    ]


def _ref_sharp_4(w_full: float, h_full: float) -> list[tuple[float, float]]:
    w, h = w_full / 2, h_full / 2
    return [(-w, -h), (w, -h), (w, h), (-w, h)]


def _xy(loop, x0: float = 0.0, y0: float = 0.0) -> list[tuple[float, float]]:
    return [(p.x - x0, p.y - y0) for p in loop.polygon]


# --- (a) rectangle point layouts ------------------------------------------- #
def test_rounded_rectangle_12pt_matches_loft_tool():
    st = LoftStation(TYPE="rectangle", X=3.0, Y=-4.0, Z=2.0, WIDTH=10.0, HEIGHT=8.0, CORNER_RADIUS=2.5)
    loop = st.to_poly_loop()
    got = _xy(loop, 3.0, -4.0)
    ref = _ref_rounded(10.0, 8.0, 2.5)
    assert len(got) == 12
    assert got == pytest.approx(ref, abs=_ABS)
    assert all(p.z == 2.0 for p in loop.polygon)


def test_sharp_force_12pt_rectangle_matches_loft_tool():
    st = LoftStation(TYPE="rectangle", X=0.0, Y=0.0, Z=0.0, WIDTH=10.0, HEIGHT=8.0)
    loop = st.to_poly_loop(force_12pt=True)
    got = _xy(loop)
    assert len(got) == 12
    assert got == pytest.approx(_ref_sharp_12(10.0, 8.0), abs=_ABS)


def test_sharp_rectangle_stays_4pt_by_default():
    st = LoftStation(TYPE="rectangle", X=1.0, Y=2.0, Z=0.0, WIDTH=6.0, HEIGHT=4.0)
    loop = st.to_poly_loop()
    got = _xy(loop, 1.0, 2.0)
    assert len(got) == 4
    assert got == pytest.approx(_ref_sharp_4(6.0, 4.0), abs=_ABS)


def test_corner_radius_too_large_rejected():
    # r must be < min(w, h) / 2 (loft tool RectangleSection rule).
    with pytest.raises(Exception):
        LoftStation(TYPE="rectangle", X=0, Y=0, Z=0, WIDTH=10.0, HEIGHT=8.0, CORNER_RADIUS=4.0)


# --- (b) circle sampling --------------------------------------------------- #
def test_circle_matches_loft_tool_start_and_winding():
    st = LoftStation(TYPE="circle", X=5.0, Y=1.0, Z=3.0, RADIUS=2.0, SEGMENTS=16)
    loop = st.to_poly_loop()
    n = 16
    assert len(loop.polygon) == n
    ref = [(5.0 + 2.0 * math.cos(2 * math.pi * i / n), 1.0 + 2.0 * math.sin(2 * math.pi * i / n)) for i in range(n)]
    got = [(p.x, p.y) for p in loop.polygon]
    assert got == pytest.approx(ref, abs=_ABS)
    # first point at +X (r, 0) relative to centre, second point CCW (positive y).
    assert (loop.polygon[0].x, loop.polygon[0].y) == pytest.approx((7.0, 1.0), abs=_ABS)
    assert loop.polygon[1].y > loop.polygon[0].y


def test_circle_default_segments_is_36():
    # loft tool CircleSection default n == 36 when segments omitted.
    st = LoftStation(TYPE="circle", X=0.0, Y=0.0, Z=0.0, RADIUS=1.0)
    assert st.SEGMENTS == 36
    assert len(st.to_poly_loop().polygon) == 36


# --- (c) member-level mixing rule ------------------------------------------ #
def _rounded_st(z: float) -> LoftStation:
    return LoftStation(TYPE="rectangle", X=0, Y=0, Z=z, WIDTH=10.0, HEIGHT=10.0, CORNER_RADIUS=2.5)


def _sharp_st(z: float) -> LoftStation:
    return LoftStation(TYPE="rectangle", X=0, Y=0, Z=z, WIDTH=10.0, HEIGHT=10.0)


def test_member_mixing_forces_12pt_on_both():
    # one rounded + one sharp -> BOTH emit 12 points.
    m = TopoLoftMember(NAME="mix", STATIONS=[_sharp_st(0.0), _rounded_st(5.0)])
    assert m._force_12pt() is True
    loops = m._station_poly_loops()
    assert [len(loop.polygon) for loop in loops] == [12, 12]
    # the sharp one uses the sharp-12 layout; the rounded one the rounded-12 layout.
    assert _xy(loops[0]) == pytest.approx(_ref_sharp_12(10.0, 10.0), abs=_ABS)
    assert _xy(loops[1]) == pytest.approx(_ref_rounded(10.0, 10.0, 2.5), abs=_ABS)


def test_member_all_sharp_stays_4pt():
    m = TopoLoftMember(NAME="box", STATIONS=[_sharp_st(0.0), _sharp_st(3.0)])
    assert m._force_12pt() is False
    assert [len(loop.polygon) for loop in m._station_poly_loops()] == [4, 4]


def test_member_all_rounded_stays_12pt():
    m = TopoLoftMember(NAME="round", STATIONS=[_rounded_st(0.0), _rounded_st(3.0)])
    assert m._force_12pt() is False  # not mixing -> flag off
    assert [len(loop.polygon) for loop in m._station_poly_loops()] == [12, 12]


# --- (d) floater column set: compiles + 12 side faces per column band ------- #
def _floater_column(name: str, x: float, y: float) -> TopoLoftMember:
    """Reproduces the loft tool floater column config (5 stations, radius on the
    two middle stations), placed at ``(x, y)`` via a translation PLACEMENT."""
    col_d = 10.0
    col_h = 40.0
    pon_h = 5.0
    r = col_d / 4  # 2.5
    stations = [
        LoftStation(TYPE="rectangle", X=0, Y=0, Z=0.0, WIDTH=col_d, HEIGHT=col_d),
        LoftStation(TYPE="rectangle", X=0, Y=0, Z=pon_h, WIDTH=col_d, HEIGHT=col_d),
        LoftStation(
            TYPE="rectangle", X=0, Y=0, Z=pon_h + (col_h - pon_h) / 3, WIDTH=col_d, HEIGHT=col_d, CORNER_RADIUS=r
        ),
        LoftStation(
            TYPE="rectangle", X=0, Y=0, Z=pon_h + 2 * (col_h - pon_h) / 3, WIDTH=col_d, HEIGHT=col_d, CORNER_RADIUS=r
        ),
        LoftStation(TYPE="rectangle", X=0, Y=0, Z=col_h, WIDTH=col_d, HEIGHT=col_d),
    ]
    place = np.eye(4)
    place[0, 3] = x
    place[1, 3] = y
    return TopoLoftMember(NAME=name, STATIONS=stations, PLACEMENT=place.tolist(), SURFACE_ONLY=True)


def _floater_pontoon(name: str, x: float, y: float) -> TopoLoftMember:
    pon_h = 5.0
    pon_b = 10.0
    pon_b2 = pon_b * 0.7
    pon_lx = 50.0
    stations = [
        LoftStation(TYPE="rectangle", X=0, Y=0, Z=0.0, WIDTH=pon_h, HEIGHT=pon_b),
        LoftStation(TYPE="rectangle", X=0, Y=0, Z=pon_lx * 0.15, WIDTH=pon_h, HEIGHT=pon_b2),
        LoftStation(TYPE="rectangle", X=0, Y=0, Z=pon_lx * 0.85, WIDTH=pon_h, HEIGHT=pon_b2),
        LoftStation(TYPE="rectangle", X=0, Y=0, Z=pon_lx, WIDTH=pon_h, HEIGHT=pon_b),
    ]
    place = np.eye(4)
    place[0, 3] = x
    place[1, 3] = y
    return TopoLoftMember(NAME=name, STATIONS=stations, PLACEMENT=place.tolist(), SURFACE_ONLY=True)


def test_floater_member_set_compiles_and_columns_have_12_side_faces():
    columns = [
        _floater_column("ne_column", 30.0, 30.0),
        _floater_column("nw_column", -30.0, 30.0),
        _floater_column("sw_column", -30.0, -30.0),
        _floater_column("se_column", 30.0, -30.0),
    ]
    pontoons = [
        _floater_pontoon("n_pontoon", 0.0, 30.0),
        _floater_pontoon("s_pontoon", 0.0, -30.0),
        _floater_pontoon("e_pontoon", 30.0, 0.0),
        _floater_pontoon("w_pontoon", -30.0, 0.0),
    ]
    pb = ProceduralBuilder(spaces=[], loft_members=columns + pontoons)
    glb = pb.compile()

    # compiles to a valid glTF.
    assert glb[:4] == b"glTF" and len(glb) > 500

    # 4 columns * (5-1) bands + 4 pontoons * (4-1) bands == 16 + 12 == 28 cells.
    cg = pb.loft_cell_graph
    assert len(cg.cells) == 28

    # Column stations are sharp(0,1) - rounded(2,3) - sharp(4): force_12pt makes
    # every rectangle a 12-point outline, so every band that touches a rounded
    # station (bays 1,2,3) carries the full 12 side faces (3 panels per corner).
    # bay0 is sharp->sharp: its 3 collinear coplanar panels per side weld to one
    # flat face (4 side faces total) — the identical result pm's single 5-station
    # ThruSections produces, so this is the parity-correct topology.
    fmap = cg.loft_face_map()
    for col in columns:
        for bay in (1, 2, 3):
            edges = {fid for fid in fmap if fid.startswith(f"{col.NAME}:bay{bay}:edge")}
            assert len(edges) == 12, f"{col.NAME} bay{bay} has {len(edges)} side faces (expected 12)"

    # the rounded-band side plates carry all 12 edge ids per bay.
    lofts = pb.assembly.parts["Lofts"]
    plate_names = {p.name for p in lofts.get_all_physical_objects(by_type=ada.Plate)}
    assert "ne_column:bay1:edge11" in plate_names
    assert "ne_column:bay2:edge0" in plate_names


@pytest.mark.skipif(
    not _loft_curved_capable(),
    reason="loft backend cannot emit curved corner plates (no OCC.Core / no adacpp "
    "is_planar_face) — rounded members intentionally fall back to flat plates",
)
def test_rounded_column_stays_within_col_half_width_not_flattened():
    """Regression: the corner-transition panels of a rounded column band are RULED
    B-spline surfaces. Emitting them as flat ``Plate``\\ s bulges the member outward
    to ~±5.18 (the flat quad over-shoots the rounded arc); keeping them as
    ``PlateCurved`` holds the rendered extent at the col half-width ±plate thickness
    (±5.01). Pins the loft-tool parity (pm renders ±5.01)."""
    trimesh = pytest.importorskip("trimesh")
    from io import BytesIO

    pb = ProceduralBuilder(spaces=[], loft_members=[_floater_column("col", 0.0, 0.0)])
    glb = pb.compile()

    with BytesIO(glb) as bio:
        scene = trimesh.load(bio, file_type="glb")
    mn, mx = scene.bounds
    # col_d = 10 -> half width 5.0; +THICKNESS/2 (0.01 default) tessellation skin.
    assert mx[0] == pytest.approx(5.01, abs=0.02), f"x-max {mx[0]:.3f} (flattened corners bulge to ~5.18)"
    assert mn[0] == pytest.approx(-5.01, abs=0.02), f"x-min {mn[0]:.3f}"
    assert mx[1] == pytest.approx(5.01, abs=0.02)
    assert mn[1] == pytest.approx(-5.01, abs=0.02)

    # the genuinely-ruled corner panels are preserved as curved plates, not flat.
    lofts = pb.assembly.parts["Lofts"]
    n_curved = len(list(lofts.get_all_physical_objects(by_type=ada.PlateCurved)))
    assert n_curved > 0, "rounded column corner transitions should be PlateCurved"
