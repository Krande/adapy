"""RunBuilder: author a cross-section-aware run by directional moves; stay true to
the input and warn (with a fix) on geometry artifacts rather than deforming."""

from __future__ import annotations

import numpy as np

import ada
from ada.topology import RunBuilder


def _tray_section():
    return ada.Section("tray", "UNP", h=0.3, w_top=0.1, w_btn=0.1, t_w=3e-3, t_ftop=3e-3, t_fbtn=3e-3)


def test_builder_lays_points_exactly_as_authored():
    # The centreline is exactly the moves given — never simplified/snapped.
    r = RunBuilder((0, 0, 3), _tray_section()).extend((1, 0, 0), 2.0).extend((0, 1, 0), 1.5).rise(1.0)
    pts = [tuple(round(float(c), 3) for c in p) for p in r.points]
    assert pts == [(0, 0, 3), (2, 0, 3), (2, 1.5, 3), (2, 1.5, 4)]


def test_clean_run_has_no_warnings_and_emits_segments():
    r = (
        RunBuilder((0, 0, 3), _tray_section(), open_channel=True, name="Good", seg_class="IfcCableSegment")
        .extend((1, 0, 0), 2.0)
        .extend((0, 1, 0), 2.0)
        .rise(1.5)
        .extend((1, 0, 0), 2.0)
    )
    assert r.validate() == []
    runs = r.to_swept_runs()
    assert runs and all(b.name.startswith("Good_") for b in runs)
    assert all(b.metadata["segment_ifc_class"] == "IfcCableSegment" for b in runs)


def test_cramped_lead_in_warns_with_a_fix_instead_of_deforming():
    # 0.1 m of straight before a rise cannot host the tray's fitting — warn, name
    # the spot, suggest a concrete fix; the run is still laid where asked.
    r = RunBuilder((0, 0, 3), _tray_section(), name="Cramped").extend((1, 0, 0), 2.0).extend((0, 1, 0), 0.1).rise(1.5)
    ws = r.validate()
    assert ws, "a cramped lead-in must produce a warning"
    joined = "\n".join(w.message + " " + w.suggestion for w in ws)
    assert "too short" in joined and "extend" in joined.lower()
    # named at the offending vertex
    assert any(abs(w.position[1] - 0.1) < 1e-6 or w.position == (2.0, 0.0, 3.0) for w in ws)
    # input is untouched
    assert tuple(round(float(c), 3) for c in r.points[2]) == (2.0, 0.1, 3.0)


def test_bend_radius_below_section_floor_warns():
    # A radius under the section's half-diagonal would invert the cross-section.
    r = RunBuilder((0, 0, 3), _tray_section(), bend_radius=0.05, name="TooTight")
    assert any("invert the cross-section" in w.message for w in r.warnings)


def test_horizontal_segments_stay_level():
    # Every horizontal leg's swept "up" must be world +Z (a tray opens upward).
    r = (
        RunBuilder((0, 0, 3), _tray_section(), open_channel=True)
        .extend((1, 0, 0), 2.0)
        .rise(1.5)
        .extend((0, 1, 0), 2.0)
    )
    for b in r.to_swept_runs():
        origins, _dx, dy = b._frames
        o = np.asarray(origins, float)
        t = o[-1] - o[0]
        tn = np.linalg.norm(t)
        if tn < 1e-9:
            continue
        t = t / tn
        if abs(t[2]) < 0.3:  # horizontal segment
            assert abs(np.asarray(dy, float)[0][2]) > 0.9, "horizontal tray must open +Z"
