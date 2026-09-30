"""The body, lump and shell boxes of the SAT the GeniE XML writer embeds cover the beam wires.

The box used to be the plates' extent alone, while the imprint hangs every beam whose axis lies
on no plate off the same shell as a wire. GeniE V9.2-01 treats the boxes as a bound on what is
inside: on two beams at y=0 and y=1.5 beside a plate at y=3..4 (box ``0 3 0 4 4 0``) the point
and line loads that find a beam by position wrote no Sesam records at all, while a load that
names the beam landed. Widening the three boxes to ``0 0 0 4 4 0`` by hand, and nothing else,
gave GeniE's own records and resultants; widening any one of them alone did not. The GeniE
side of that is ``tests/fem/test_genie_gxml_beam_loads.py``; this checks the SAT itself.
"""

from __future__ import annotations

import numpy as np
import pytest

import ada
from ada.cadit.sat.write.writer import part_to_sat_writer


def _records(sat: str) -> list[list[str]]:
    return [line.split() for line in sat.splitlines() if line.startswith("-") and line.rstrip().endswith("#")]


def _box(rec: list[str]) -> np.ndarray:
    # topology records end "... T xmin ymin zmin xmax ymax zmax #"
    t = len(rec) - 1 - rec[::-1].index("T")
    return np.asarray(rec[t + 1 : t + 7], dtype=float)


def _points(recs) -> np.ndarray:
    return np.asarray([r[-4:-1] for r in recs if r[1] == "point"], dtype=float)


def _beams_beside_a_plate() -> ada.Assembly:
    """The investigation's geometry: two beams on no plate, beside a plate."""
    return ada.Assembly("A") / (
        ada.Part("P")
        / (
            ada.Beam("Bm1", (0, 0, 0), (4, 0, 0), sec="IPE300"),
            ada.Beam("Bm2", (0, 1.5, 0), (4, 1.5, 0), sec="IPE300"),
            ada.Plate.from_3d_points("Pl1", [(0, 3, 0), (4, 3, 0), (4, 4, 0), (0, 4, 0)], 0.01),
        )
    )


def _two_plates_and_a_beam_above() -> ada.Assembly:
    """Two plates that touch nowhere (two lumps), and a beam above both of them."""
    return ada.Assembly("A") / (
        ada.Part("P")
        / (
            ada.Plate.from_3d_points("Pl1", [(0, 0, 0), (2, 0, 0), (2, 2, 0), (0, 2, 0)], 0.01),
            ada.Plate.from_3d_points("Pl2", [(5, 0, 0), (7, 0, 0), (7, 2, 0), (5, 2, 0)], 0.01),
            ada.Beam("Bm1", (-1, 1, 3), (8, 1, 3), sec="IPE300"),
        )
    )


@pytest.mark.parametrize("build", [_beams_beside_a_plate, _two_plates_and_a_beam_above])
def test_body_lump_and_shell_boxes_hold_every_point(build):
    recs = _records(part_to_sat_writer(build()).to_str())

    # not vacuous: the beams are in the body as wires, outside every plate
    wires = [r for r in recs if r[1] == "wire"]
    assert wires
    pts = _points(recs)
    lo, hi = pts.min(axis=0), pts.max(axis=0)

    containers = [r for r in recs if r[1] in ("body", "lump", "shell")]
    assert {r[1] for r in containers} == {"body", "lump", "shell"}
    for rec in containers:
        box = _box(rec)
        assert np.all(box[:3] <= lo) and np.all(box[3:] >= hi), (rec[0], rec[1], box.tolist(), lo, hi)


def test_investigation_box_is_the_union_of_plate_and_beams():
    """The exact numbers: what GeniE accepted when the box was widened by hand."""
    recs = _records(part_to_sat_writer(_beams_beside_a_plate()).to_str())
    boxes = {r[1]: _box(r).tolist() for r in recs if r[1] in ("body", "lump", "shell")}
    assert boxes == {k: [0, 0, 0, 4, 4, 0] for k in ("body", "lump", "shell")}


def test_a_plates_only_body_keeps_the_plate_box():
    """No wires, nothing to widen: the box stays the plates' extent."""
    a = ada.Assembly("A") / (
        ada.Part("P") / ada.Plate.from_3d_points("Pl1", [(0, 3, 0), (4, 3, 0), (4, 4, 0), (0, 4, 0)], 0.01)
    )
    recs = _records(part_to_sat_writer(a).to_str())
    assert not [r for r in recs if r[1] == "wire"]
    assert _box(next(r for r in recs if r[1] == "body")).tolist() == [0, 3, 0, 4, 4, 0]
