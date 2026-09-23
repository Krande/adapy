"""A beam is connected to a plate when its BODY reaches it -- not when its axis happens to be
inside the plate's few-millimetre-thick bounding box.

The two facts pinned here are the two ways the same structure gets expressed: a beam carrying its
offset as an eccentricity beside an axis in the plate's plane, and the same beam with that offset
baked into the axis (which is what a read back from IFC produces, since an extrusion's position IS
its axis). Both must find the beam, or a model answers a clash check differently depending on
which file it came from.
"""

import ada
from ada.core.clash_check import find_beams_connected_to_plate


def _plate() -> ada.Plate:
    # A 5x5 plate, 10 mm thick, its top face at z = 0.
    return ada.Plate("pl", [(0, 0), (5, 0), (5, 5), (0, 5)], 0.01)


def test_a_beam_whose_axis_lies_on_the_plate_face_is_found():
    pl = _plate()
    bm = ada.Beam("bm", (0, 2.5, 0.0), (5, 2.5, 0.0), "IPE200")
    (ada.Part("p") / [pl, bm])
    assert [b.name for b in find_beams_connected_to_plate(pl, [bm])] == ["bm"]


def test_the_same_beam_with_its_offset_baked_into_the_axis_is_also_found():
    # Half an IPE200 below the plate: the body still touches it, which is the question being
    # asked. Untolerated, this axis is 100 mm outside a 10 mm slab and the beam vanished.
    pl = _plate()
    bm = ada.Beam("bm", (0, 2.5, -0.1), (5, 2.5, -0.1), "IPE200")
    (ada.Part("p") / [pl, bm])
    assert [b.name for b in find_beams_connected_to_plate(pl, [bm])] == ["bm"]


def test_eccentricity_and_a_baked_axis_agree():
    pl = _plate()
    eccentric = ada.Beam("bm", (0, 2.5, 0.0), (5, 2.5, 0.0), "IPE200", e1=(0, 0, 0.1), e2=(0, 0, 0.1))
    baked = ada.Beam("bm", (0, 2.5, -0.1), (5, 2.5, -0.1), "IPE200")
    (ada.Part("a") / [pl, eccentric])
    assert len(find_beams_connected_to_plate(pl, [eccentric])) == len(find_beams_connected_to_plate(pl, [baked]))


def test_a_beam_a_storey_away_is_not_found():
    # The tolerance is the beam's own half-section, not a licence to reach anywhere: a beam three
    # metres above the plate is not connected to it.
    pl = _plate()
    bm = ada.Beam("bm", (0, 2.5, 3.0), (5, 2.5, 3.0), "IPE200")
    (ada.Part("p") / [pl, bm])
    assert find_beams_connected_to_plate(pl, [bm]) == []


def test_a_beam_outside_the_plates_footprint_is_not_found():
    pl = _plate()
    bm = ada.Beam("bm", (0, 9.0, 0.0), (5, 9.0, 0.0), "IPE200")
    (ada.Part("p") / [pl, bm])
    assert find_beams_connected_to_plate(pl, [bm]) == []
