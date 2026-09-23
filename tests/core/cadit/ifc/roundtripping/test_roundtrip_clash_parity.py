"""A model that has been through IFC must yield the SAME joints as the model it came from.

The round trip is not lossless in representation -- a beam's eccentricity comes back baked into
its axis, and a plate's outline is rebuilt from the profile curve -- and both of those used to
change the ANSWER: the same frame reported 150 joints in memory and 110 read back, because the
plate passes were asking questions that only one of the two representations could answer
(`ada/core/clash_check.py`'s `find_beams_connected_to_plate`, `IndexedPolyCurve.to_points2d`).

A count, not a geometry comparison, on purpose: this is the fact a user sees.
"""

import ada
from ada.clash.identify import identify_joints
from ada.clash.options import ClashOptions


def _frame() -> ada.Assembly:
    """A plate on four girders with an eccentric stringer under it -- the smallest model that has
    a beam-beam joint, a plate-beam joint, and an offset expressed as an eccentricity."""
    pl = ada.Plate("pl", [(0, 0), (5, 0), (5, 5), (0, 5)], 0.01)
    girders = [
        ada.Beam("g0", (0, 0, 0), (5, 0, 0), "IPE200", e1=(0, 0, 0.1), e2=(0, 0, 0.1)),
        ada.Beam("g1", (5, 0, 0), (5, 5, 0), "IPE200", e1=(0, 0, 0.1), e2=(0, 0, 0.1)),
        ada.Beam("g2", (5, 5, 0), (0, 5, 0), "IPE200", e1=(0, 0, 0.1), e2=(0, 0, 0.1)),
        ada.Beam("g3", (0, 5, 0), (0, 0, 0), "IPE200", e1=(0, 0, 0.1), e2=(0, 0, 0.1)),
    ]
    stringer = ada.Beam("s0", (0, 2.5, 0), (5, 2.5, 0), "HP140x8", e1=(0, 0, 0.01), e2=(0, 0, 0.01))
    return ada.Assembly("frame") / (ada.Part("deck") / [pl, *girders, stringer])


def _joint_counts(model) -> dict:
    outcome = identify_joints(model, ClashOptions(include_plate_joints=True))
    counts = {"total": len(outcome.joints)}
    for found in outcome.joints:
        counts[found.origin] = counts.get(found.origin, 0) + 1
    return counts


def test_joint_counts_survive_an_ifc_round_trip(tmp_path):
    original = _frame()
    before = _joint_counts(original)
    assert before["plate-beam"] > 0, "the fixture must have plate joints for this to test anything"

    ifc_file = tmp_path / "frame.ifc"
    original.to_ifc(ifc_file, validate=False)
    after = _joint_counts(ada.from_ifc(ifc_file))

    assert after == before


def test_plate_outlines_survive_an_ifc_round_trip(tmp_path):
    original = _frame()
    ifc_file = tmp_path / "frame.ifc"
    original.to_ifc(ifc_file, validate=False)
    back = ada.from_ifc(ifc_file)

    pl = next(iter(back.get_all_physical_objects(by_type=ada.Plate)))
    pts = [tuple(round(float(v), 6) for v in p) for p in pl.poly.points2d]
    assert len(pts) == 4, f"a rectangle must come back with four corners, got {pts}"
    assert len(set(pts)) == 4, f"no corner may be named twice, got {pts}"
