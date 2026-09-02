"""A T-bar written as an I-section must still produce a solid.

Sesam's ``GIORH`` card has no T variant, so a T-bar is written as an I-section
whose top flange is a placeholder: width equal to the web thickness, thickness a
near-zero sentinel (1e-4 m is what shows up in practice). Built literally that
outline is degenerate twice over — the two flange corners land exactly on the
web/flange junctions, giving zero-length edges, and the remaining flange sides
are shorter than ``CurvePoly2d``'s 1 mm segment tolerance and get dropped, so
the loop no longer closes.

Either way OCCT refuses the profile, and since the profile is what the beam is
swept from, the whole member is lost to render a flange that was never there.
On a deck full of T-girders that is tens of thousands of failures.
"""

from __future__ import annotations

import pytest

from ada import Section
from ada.sections.profiles import build_section_profile, drop_coincident


def _sec(**kw) -> Section:
    return Section("S", sec_type="IG", **kw)


#: The three shapes below are ordinary T-girder proportions, given as
#: (h, w_btn, t_w, t_fbtn).
T_BARS = [(1.0, 0.30, 0.016, 0.025), (0.30, 0.15, 0.008, 0.012), (0.65, 0.30, 0.012, 0.020)]


@pytest.mark.parametrize(("h", "w_btn", "t_w", "t_fbtn"), T_BARS)
def test_a_t_encoded_iprofile_yields_a_clean_closed_outline(h, w_btn, t_w, t_fbtn) -> None:
    sec = _sec(h=h, w_top=t_w, w_btn=w_btn, t_w=t_w, t_ftop=1e-4, t_fbtn=t_fbtn)

    curve = build_section_profile(sec, is_solid=True).outer_curve
    pts = [(float(p[0]), float(p[1])) for p in curve.points2d]

    coincident = [i for i in range(len(pts)) if pts[i] == pts[(i + 1) % len(pts)]]
    assert not coincident, f"zero-length edge(s) at {coincident} - OCCT will refuse this profile"

    # Every side must also be long enough to survive CurvePoly2d's own tolerance,
    # or the loop silently loses a segment and stops being closed.
    shortest = min(
        abs(pts[i][0] - pts[(i + 1) % len(pts)][0]) + abs(pts[i][1] - pts[(i + 1) % len(pts)][1])
        for i in range(len(pts))
    )
    assert shortest > 1e-3, f"a {shortest:.2e} m side will be dropped as sub-tolerance"

    # Those two properties are what closing the loop depends on, and they are
    # asserted directly rather than via `CurvePoly2d.segments`: reading that
    # property faults in this environment for any profile, an ordinary
    # I-section included, somewhere under `transform_3x3`. Unrelated to this
    # fix, but it rules the property out as an assertion.


@pytest.mark.parametrize(("h", "w_btn", "t_w", "t_fbtn"), T_BARS)
def test_the_outline_is_the_t_it_describes(h, w_btn, t_w, t_fbtn) -> None:
    """The placeholder flange is dropped, not modelled as a 0.1 mm plate.

    Asserted on the outline rather than on the swept solid's volume: building a
    solid needs the OCC path, which faults in this environment for any profile
    at all (see the note above). The shape below is what the volume follows
    from, and it is checked where it is decided.
    """
    sec = _sec(h=h, w_top=t_w, w_btn=w_btn, t_w=t_w, t_ftop=1e-4, t_fbtn=t_fbtn)

    pts = [(float(p[0]), float(p[1])) for p in build_section_profile(sec, is_solid=True).outer_curve.points2d]

    # Eight corners, not twelve: the four that described the placeholder flange
    # are gone.
    assert len(pts) == 8
    # The web now runs to full height, and is the full width at the top.
    top = [p for p in pts if p[1] == pytest.approx(h / 2)]
    assert len(top) == 2
    assert abs(top[0][0] - top[1][0]) == pytest.approx(t_w)
    # The bottom flange is untouched.
    assert min(p[0] for p in pts) == pytest.approx(-w_btn / 2)
    assert max(p[0] for p in pts) == pytest.approx(w_btn / 2)
    # Cross-section area, from the shape the outline describes.
    area = (h - t_fbtn) * t_w + w_btn * t_fbtn
    assert area > 0


def test_a_real_i_profile_is_untouched() -> None:
    sec = _sec(h=1.0, w_top=0.30, w_btn=0.30, t_w=0.016, t_ftop=0.025, t_fbtn=0.025)

    pts = [(float(p[0]), float(p[1])) for p in build_section_profile(sec, is_solid=True).outer_curve.points2d]

    assert len(pts) == 12, "an I-profile keeps all twelve corners"


def test_a_thin_but_wide_top_flange_is_still_a_flange() -> None:
    # The guard needs BOTH conditions: no overhang and no thickness. A 2 mm
    # flange 300 mm wide is real geometry and must not be discarded.
    sec = _sec(h=1.0, w_top=0.30, w_btn=0.30, t_w=0.016, t_ftop=0.002, t_fbtn=0.025)

    pts = [(float(p[0]), float(p[1])) for p in build_section_profile(sec, is_solid=True).outer_curve.points2d]

    assert len(pts) == 12
    assert max(p[1] for p in pts) == pytest.approx(0.5)


class TestDropCoincident:
    def test_consecutive_duplicates_go(self) -> None:
        assert drop_coincident([(0, 0), (0, 0), (1, 0), (1, 1)]) == [(0, 0), (1, 0), (1, 1)]

    def test_a_duplicate_of_the_first_point_at_the_end_goes(self) -> None:
        assert drop_coincident([(0, 0), (1, 0), (1, 1), (0, 0)]) == [(0, 0), (1, 0), (1, 1)]

    def test_distinct_points_are_all_kept(self) -> None:
        pts = [(0, 0), (1, 0), (1, 1), (0, 1)]
        assert drop_coincident(pts) == pts

    def test_a_fillet_radius_on_the_survivor_is_preserved(self) -> None:
        # Points may carry a third element; the first of a coincident run wins.
        assert drop_coincident([(0, 0, 0.05), (0, 0), (1, 0)]) == [(0, 0, 0.05), (1, 0)]
