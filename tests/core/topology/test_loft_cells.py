"""Phase 1 spike: cell decomposition of a section loft (``from_section_loft``).

A loft threaded through ``N`` ordered section profiles is partitioned into ``N-1``
inter-station swept BAND cells. These tests build lofts directly via ``ada.api.loft``
(self-contained — no domain packages) and assert, per archetype:

  (a) cell count == Sum(stations - 1) over all members;
  (b) the decomposition is a PARTITION — the band volumes sum to the loft solid
      volume within tolerance (lossless);
  (c) each band carries the expected ``station_lo`` / ``station_hi`` metadata.
"""

from __future__ import annotations

import numpy as np

from ada.api.loft import loft_profiles
from ada.cad import active_backend
from ada.geom.curves import PolyLoop
from ada.geom.points import Point
from ada.topology.io import LoftMember, from_section_loft

_VOL_RTOL = 1e-6


def _rect_loop(z: float, half_w: float, half_h: float) -> PolyLoop:
    """A closed axis-aligned rectangle at height ``z`` (CCW seen from +Z)."""
    return PolyLoop(
        polygon=[
            Point(-half_w, -half_h, z),
            Point(half_w, -half_h, z),
            Point(half_w, half_h, z),
            Point(-half_w, half_h, z),
        ]
    )


def _loft_volume(profiles) -> float:
    return active_backend().volume(loft_profiles(profiles, ruled=True, is_solid=True))


def _assert_station_chain(cells):
    """Bands form a contiguous 0..N-1 station chain per member."""
    lo = sorted(c.metadata.get("station_lo") for c in cells)
    hi = sorted(c.metadata.get("station_hi") for c in cells)
    assert lo == list(range(len(cells)))
    assert hi == list(range(1, len(cells) + 1))
    for c in cells:
        assert c.metadata.get("station_hi") == c.metadata.get("station_lo") + 1


def test_two_station_column_one_band():
    # Equal-radius 2-station column -> a single band cell (no interior divider).
    profiles = [_rect_loop(0.0, 1.0, 1.0), _rect_loop(3.0, 1.0, 1.0)]
    cg = from_section_loft([LoftMember("column", profiles)])

    assert len(cg.cells) == 1  # (a) Sum(stations-1) = 1
    band = cg.cells[0]
    # (b) lossless: the lone band IS the loft solid.
    loft_vol = _loft_volume(profiles)
    assert abs(active_backend().volume(band.handle) - loft_vol) <= _VOL_RTOL * loft_vol
    # (c) metadata
    assert band.metadata.get("member") == "column"
    assert (band.metadata.get("station_lo"), band.metadata.get("station_hi")) == (0, 1)
    assert band.name == "column_bay0"


def test_tapered_multi_station_partition():
    # A 4-station tapered member -> 3 bands; band volumes partition the loft solid.
    profiles = [
        _rect_loop(0.0, 1.0, 1.0),
        _rect_loop(2.0, 0.9, 0.9),
        _rect_loop(4.0, 0.7, 0.7),
        _rect_loop(7.0, 0.4, 0.4),
    ]
    cg = from_section_loft([LoftMember("taper", profiles)])
    be = active_backend()

    assert len(cg.cells) == len(profiles) - 1  # (a) == 3

    band_vol = sum(be.volume(c.handle) for c in cg.cells)
    # (b) lossless partition: Sum(band vols) ~= loft solid vol.
    assert abs(band_vol - _loft_volume(profiles)) <= _VOL_RTOL * _loft_volume(profiles)

    # (c) contiguous station chain, ordered along the spine.
    _assert_station_chain(cg.cells)
    assert all(c.metadata.get("member") == "taper" for c in cg.cells)
    # bands ascend along +Z (the member spine) in station order.
    by_station = sorted(cg.cells, key=lambda c: c.metadata.get("station_lo"))
    zs = [be.center_of_mass(c.handle).z for c in by_station]
    assert zs == sorted(zs)


def test_stepped_five_station_partition():
    # A 5-station stepped (non-monotone width) member -> 4 bands, still lossless.
    profiles = [
        _rect_loop(0.0, 0.5, 0.5),
        _rect_loop(1.5, 1.0, 0.8),
        _rect_loop(3.0, 0.6, 1.2),
        _rect_loop(5.0, 1.1, 0.6),
        _rect_loop(8.0, 0.4, 0.4),
    ]
    cg = from_section_loft([LoftMember("stepped", profiles)])
    be = active_backend()

    assert len(cg.cells) == 4  # (a)
    band_vol = sum(be.volume(c.handle) for c in cg.cells)
    assert abs(band_vol - _loft_volume(profiles)) <= _VOL_RTOL * _loft_volume(profiles)  # (b)
    _assert_station_chain(cg.cells)  # (c)


def test_two_members_with_placement_union():
    # Bonus: two members unioned in one graph; the second is translated via PLACEMENT.
    member_a = LoftMember("A", [_rect_loop(0.0, 1.0, 1.0), _rect_loop(3.0, 0.6, 0.6)])

    place = np.eye(4)
    place[0, 3] = 10.0  # translate member B +10 in x
    member_b = LoftMember(
        "B",
        [_rect_loop(0.0, 1.0, 1.0), _rect_loop(2.0, 0.8, 0.8), _rect_loop(5.0, 0.5, 0.5)],
        placement=place,
    )

    cg = from_section_loft([member_a, member_b])
    be = active_backend()

    # (a) total cells == (2-1) + (3-1) == 3
    assert len(cg.cells) == 3
    a_cells = [c for c in cg.cells if c.metadata.get("member") == "A"]
    b_cells = [c for c in cg.cells if c.metadata.get("member") == "B"]
    assert len(a_cells) == 1
    assert len(b_cells) == 2

    # (b) each member's bands partition its own loft solid.
    profiles_b = [_rect_loop(0.0, 1.0, 1.0), _rect_loop(2.0, 0.8, 0.8), _rect_loop(5.0, 0.5, 0.5)]
    loft_b_vol = _loft_volume(profiles_b)
    b_band_vol = sum(be.volume(c.handle) for c in b_cells)
    assert abs(b_band_vol - loft_b_vol) <= _VOL_RTOL * loft_b_vol

    # placement moved member B's cells to x ~= 10 (built at x=0 then transformed).
    assert all(abs(be.center_of_mass(c.handle).x - 10.0) < 1e-6 for c in b_cells)
    # member A stays at the origin.
    assert abs(be.center_of_mass(a_cells[0].handle).x) < 1e-6

    # (c) station metadata per member.
    _assert_station_chain(b_cells)
    assert (a_cells[0].metadata.get("station_lo"), a_cells[0].metadata.get("station_hi")) == (0, 1)
